# -*- coding: utf-8 -*-
"""Перевірка готового файла без плати й без симулятора.

  1. Формат: підписи на початку й у кінці, заголовок, покажчик кадрів.
  2. Незалежне розпакування всіх кадрів і звірення з тим, що закодовано
     (контрольна сума кожного кадру).
  3. Звук: розпакування всіх шматків і SNR відносно підготовленої доріжки.
  4. Розмір: чи вміщується в SDRAM, чи потрібен CPUlator із наперед виділеною пам'яттю.
"""
import zlib

import numpy as np

from . import blob as blobmod
from . import codec, elf
from .targets import BLOB_BASE, CPULATOR_PLAIN_LIMIT, HEIGHT, WIDTH


def blob_from_file(raw):
    """Блок із файла: .bin як є або сегмент ELF за адресою __blob_base."""
    if raw[:4] == b'\x7fELF':
        t = elf.read(raw)
        base = t['symbols'].get('__blob_base', BLOB_BASE)
        seg = [s for s in t['segments'] if s['vaddr'] == base]
        if not seg:
            raise ValueError('у файлі ELF немає сегмента з роликом')
        return seg[0]['data'], t
    return raw, None


def snr_db(ref, got):
    ref = ref.astype(np.float64)
    err = ref - got.astype(np.float64)
    pe, ps = float(np.mean(err ** 2)), float(np.mean(ref ** 2))
    return float('inf') if pe == 0 else (10 * np.log10(ps / pe) if ps > 0 else 0.0)


def check(raw, crcs=None, pcm=None, log=None):
    """Повертає (успіх, рядки звіту, числа)."""
    lines, nums, ok = [], {}, True

    def bad(msg):
        nonlocal ok
        ok = False
        lines.append('ПОМИЛКА: ' + msg)

    try:
        data, t = blob_from_file(raw)
    except Exception as e:
        return False, ['ПОМИЛКА: %s' % e], nums
    if t:
        code = sum(s['filesz'] for s in t['segments'] if s['vaddr'] != BLOB_BASE)
        lines.append('ELF: ARM, точка входу 0x%08X, програма %d байтів, ролик за адресою 0x%08X'
                     % (t['entry'], code, BLOB_BASE))
    problems = blobmod.header_errors(data)
    for p in problems:
        bad(p)
    if problems:
        return ok, lines, nums
    h = blobmod.parse(data)
    idx = blobmod.index(data, h)
    secs = h['frames'] / h['fps']
    lines.append('Ролик: %d кадрів %dx%d, %d:%02d, опорних %d, звук %s'
                 % (h['frames'], h['width'], h['height'], int(secs) // 60, int(secs) % 60,
                    int(np.count_nonzero(idx[:, 1] & blobmod.KEY_FLAG)),
                    '%d Гц, %d відліків на кадр' % (h['audio_rate'], h['spf']) if h['flags'] & 1 else 'немає'))
    nums.update(frames=h['frames'], seconds=secs, blob_bytes=len(data), file_bytes=len(raw))

    # усі кадри — незалежним розпаковувачем
    work = np.zeros(WIDTH * HEIGHT, dtype=np.uint16)
    bad_frames = []
    for k in range(h['frames']):
        off, size = int(idx[k, 0]), int(idx[k, 1])
        key = bool(size & blobmod.KEY_FLAG)
        size &= ~blobmod.KEY_FLAG
        if off + size > h['blob_size'] or off % 4:
            bad('кадр %d виходить за межі блока' % k)
            break
        filled = codec.rle_decode(data[off:off + size], work, key)
        if filled != len(work) or (crcs is not None and zlib.crc32(work.tobytes()) != crcs[k]):
            bad_frames.append(k)
        if log and (k + 1) % 500 == 0:
            log('  розпаковано %d / %d кадрів' % (k + 1, h['frames']))
    if bad_frames:
        bad('%d кадрів розпакувалися не так, як закодовано (перший — %d)' % (len(bad_frames), bad_frames[0]))
    else:
        lines.append('Усі %d кадрів розпаковано незалежно%s'
                     % (h['frames'], ' й збігаються із закодованими біт у біт' if crcs is not None else ''))

    if h['flags'] & 1:
        got = codec.decode_audio(data[h['audio_off']:h['audio_off'] + h['frames'] * h['audio_block']],
                                 h['spf'], h['frames'])
        if pcm is not None:
            s = snr_db(pcm[:, :got.shape[1]], got[:, :pcm.shape[1]])
            nums['snr_db'] = s
            lines.append('Звук: розпаковано %d шматків, SNR %.1f дБ відносно підготовленої доріжки'
                         % (h['frames'], s))
            if s < 15:
                bad('надто низьке відношення сигнал/шум звуку')
        else:
            lines.append('Звук: розпаковано %d шматків без помилок' % h['frames'])

    mb = len(raw) / 1e6
    nums['needs_bigmem'] = t is not None and len(raw) > CPULATOR_PLAIN_LIMIT
    if t is None:
        lines.append('Розмір блока %.1f МіБ з %.0f МіБ, що лишаються в SDRAM під ролик'
                     % (len(data) / 1048576, blobmod.BLOB_MAX / 1048576))
    elif nums['needs_bigmem']:
        lines.append('Розмір %.2f МБ: для CPUlator потрібен режим із наперед виділеною пам\'яттю' % mb)
    else:
        lines.append('Розмір %.2f МБ: вміщується у звичайний CPUlator' % mb)
    return ok, lines, nums
