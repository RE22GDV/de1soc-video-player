# -*- coding: utf-8 -*-
"""Блок ролика DE1V — той самий формат, що розгортає програвач на платі.

    заголовок 48 байтів | покажчик кадрів (зміщення, розмір + розряд «опорний»)
    | кадри, кожен вирівняно на слово | шматки звуку по одному на кадр | «DE1V»

Кінцевий підпис потрібен на платі: ролик заливають від початку до кінця, і
програвач починає показ, лише коли бачить підпис і на початку, і в кінці.
"""
import struct
import zlib

import numpy as np

from . import codec
from .targets import BLOB_MAX, FPS, HEIGHT, KEYINT, WIDTH

MAGIC = b'DE1V'
VERSION = 2
HEADER = struct.Struct('<4sHHHHHHIIIIIIIHH')
assert HEADER.size == 48
KEY_FLAG = 0x80000000
FIELDS = ('magic', 'version', 'flags', 'width', 'height', 'fps', 'keyint', 'frames', 'index_off',
          'data_off', 'blob_size', 'audio_off', 'audio_block', 'audio_rate', 'spf', 'channels')


def psnr(a, b):
    """PSNR двох кадрів RGB565 у 8-розрядних складових, дБ."""
    from .media import to_rgb
    d = to_rgb(a).astype(np.int32) - to_rgb(b).astype(np.int32)
    mse = float(np.mean(d * d))
    return float('inf') if mse == 0 else 10 * np.log10(255 * 255 / mse)


class Packer:
    """Кодує кадри по одному, у міру надходження від ffmpeg."""

    def __init__(self, threshold, keyint=KEYINT):
        self.threshold, self.keyint = threshold, keyint
        self.payloads, self.keys, self.crcs, self.psnr = [], [], [], []
        self.prev = None

    def add(self, frame):
        k = len(self.payloads)
        cur = codec.deadzone(frame, self.prev, self.threshold)
        is_key = self.prev is None or k % self.keyint == 0
        data = codec.rle_encode(cur if is_key else np.bitwise_xor(cur, self.prev))
        if not is_key:
            alt = codec.rle_encode(cur)
            if len(alt) < len(data):
                data, is_key = alt, True
        self.payloads.append(data)
        self.keys.append(is_key)
        self.crcs.append(zlib.crc32(cur.tobytes()))
        # втрати мертвої зони рахуємо на кожному 12-му кадрі: цього досить для середнього
        if k % 12 == 0:
            self.psnr.append(psnr(frame, cur))
        self.prev = cur

    def finish(self, audio_blocks=b'', spf=0, rate=0):
        n = len(self.payloads)
        index_off = HEADER.size
        data_off = index_off + 8 * n
        index, off = bytearray(), data_off
        for data, key in zip(self.payloads, self.keys):
            index += struct.pack('<II', off, len(data) | (KEY_FLAG if key else 0))
            off += len(data) + (-len(data)) % 4
        has_audio = bool(audio_blocks)
        audio_off = off if has_audio else 0
        block = 8 + spf if has_audio else 0
        size = off + len(audio_blocks) + 4
        out = bytearray(HEADER.pack(MAGIC, VERSION, 1 if has_audio else 0, WIDTH, HEIGHT, FPS,
                                    self.keyint, n, index_off, data_off, size, audio_off, block,
                                    rate if has_audio else 0, spf if has_audio else 0,
                                    2 if has_audio else 0))
        out += index
        for data in self.payloads:
            out += data + b'\0' * ((-len(data)) % 4)
        out += audio_blocks + MAGIC
        assert len(out) == size
        return bytes(out)

    def stats(self):
        sizes = np.array([len(p) for p in self.payloads])
        worst = int(np.argmax(sizes)) if len(sizes) else 0
        finite = [p for p in self.psnr if np.isfinite(p)]
        return {'frames': len(sizes), 'keys': int(sum(self.keys)), 'video_bytes': int(sizes.sum()),
                'avg_frame': float(sizes.mean()) if len(sizes) else 0.0,
                'worst_frame': int(sizes[worst]) if len(sizes) else 0, 'worst_index': worst,
                'psnr_min': min(finite) if finite else float('inf'),
                'psnr_avg': float(np.mean(finite)) if finite else float('inf')}


def parse(blob):
    h = dict(zip(FIELDS, HEADER.unpack_from(blob, 0)))
    return h


def header_errors(blob):
    """Ті самі умови, що перевіряє програвач перед показом, і трохи суворіші."""
    if len(blob) < HEADER.size or blob[:4] != MAGIC:
        return ['немає підпису DE1V на початку']
    h = parse(blob)
    err = []
    if h['version'] != VERSION:
        err.append('версія формату %d замість %d' % (h['version'], VERSION))
    if (h['width'], h['height'], h['fps']) != (WIDTH, HEIGHT, FPS):
        err.append('кадр %dx%d при %d fps замість %dx%d при %d' % (h['width'], h['height'], h['fps'],
                                                                   WIDTH, HEIGHT, FPS))
    if h['blob_size'] != len(blob) or h['blob_size'] % 4 or h['blob_size'] > BLOB_MAX:
        err.append('розмір блока %d не узгоджений (файл %d, межа %d)' % (h['blob_size'], len(blob), BLOB_MAX))
    if blob[-4:] != MAGIC:
        err.append('немає кінцевого підпису DE1V')
    if h['frames'] == 0 or h['index_off'] != HEADER.size or h['data_off'] != HEADER.size + 8 * h['frames']:
        err.append('покажчик кадрів не узгоджений')
    if h['flags'] & 1:
        if h['spf'] % 2 or h['audio_block'] != 8 + h['spf'] or h['channels'] != 2:
            err.append('параметри звуку не узгоджені')
        elif h['audio_off'] + h['frames'] * h['audio_block'] + 4 != h['blob_size']:
            err.append('звук не займає місце до кінцевого підпису')
    return err


def index(blob, h):
    return np.frombuffer(blob, dtype='<u4', count=2 * h['frames'], offset=h['index_off']).reshape(-1, 2)
