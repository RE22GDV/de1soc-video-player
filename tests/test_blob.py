# -*- coding: utf-8 -*-
"""Формат блока, складання ELF і перевірка на синтетичному ролику (без ffmpeg)."""
import os
import struct
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
from devideo import blob, codec, elf, verify      # noqa: E402
from devideo.make import TEMPLATE                  # noqa: E402
from devideo.targets import BLOB_BASE              # noqa: E402

W, H = 320, 240


def synthetic(frames=30):
    """Смуга, що рухається, на градієнті: і опорні кадри, і дельти."""
    y, x = np.mgrid[0:H, 0:W]
    base = (((x >> 3) << 11) | ((y >> 2) << 5) | 8).astype(np.uint16)
    out = []
    for k in range(frames):
        f = base.copy()
        f[:, (k * 7) % W:(k * 7) % W + 12] = 0xFFFF
        out.append(f.reshape(-1))
    return out


def build(frames=30, audio=True, threshold=1):
    p = blob.Packer(threshold)
    for f in synthetic(frames):
        p.add(f)
    pcm = None
    blocks = b''
    if audio:
        t = np.arange(frames * 334) / 8016
        tone = (8000 * np.sin(2 * np.pi * 330 * t)).astype(np.int16)
        pcm = np.stack([tone, tone])
        blocks, _ = codec.encode_audio(pcm, 334)
    return p.finish(blocks, 334, 8000), p, pcm


class Blob(unittest.TestCase):
    def test_built_blob_is_consistent(self):
        data, p, pcm = build()
        self.assertEqual(blob.header_errors(data), [])
        h = blob.parse(data)
        self.assertEqual((h['frames'], h['spf'], h['audio_block']), (30, 334, 342))
        self.assertEqual(data[-4:], b'DE1V')
        ok, lines, nums = verify.check(data, p.crcs, pcm)
        self.assertTrue(ok, lines)
        self.assertGreater(nums['snr_db'], 25)

    def test_without_audio(self):
        data, p, _ = build(audio=False)
        self.assertEqual(blob.header_errors(data), [])
        self.assertEqual(blob.parse(data)['flags'], 0)
        self.assertTrue(verify.check(data, p.crcs)[0])

    def test_corruption_is_reported(self):
        data, p, pcm = build()
        cases = {
            'кінцевий підпис': data[:-4] + b'XXXX',
            'розмір у заголовку': data[:28] + struct.pack('<I', len(data) + 4) + data[32:],
            'кількість кадрів': data[:16] + struct.pack('<I', 31) + data[20:],
        }
        for name, bad in cases.items():
            self.assertTrue(blob.header_errors(bad), name)
        damaged = bytearray(data)
        off = int(blob.index(data, blob.parse(data))[5, 0])
        damaged[off + 3] ^= 0x10
        ok, lines, _ = verify.check(bytes(damaged), p.crcs, pcm)
        self.assertFalse(ok)


class Elf(unittest.TestCase):
    def test_blob_goes_to_sdram_segment(self):
        data, p, pcm = build()
        with open(TEMPLATE, 'rb') as f:
            raw = elf.build(f.read(), data)
        t = elf.read(raw)
        self.assertEqual(t['symbols']['__blob_base'], BLOB_BASE)
        seg = [s for s in t['segments'] if s['vaddr'] == BLOB_BASE]
        self.assertEqual(len(seg), 1)
        self.assertEqual(seg[0]['data'], data)
        ok, lines, _ = verify.check(raw, p.crcs, pcm)
        self.assertTrue(ok, lines)


if __name__ == '__main__':
    unittest.main()
