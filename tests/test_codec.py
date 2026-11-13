# -*- coding: utf-8 -*-
"""Кодер кадрів і звуку: python -m unittest discover tests"""
import os
import struct
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
from devideo import codec                         # noqa: E402

MAX_RUN = codec.MAX_RUN


def reference_rle(words):
    """Покроковий кодер першої версії програвача (host/pack_video.py), дослівно."""
    out = bytearray()
    n = len(words)
    i = 0
    literal_start = 0

    def flush_literal(upto):
        nonlocal literal_start
        while literal_start < upto:
            chunk = min(MAX_RUN, upto - literal_start)
            out.extend(struct.pack('<H', 0x8000 | chunk))
            out.extend(words[literal_start:literal_start + chunk].tobytes())
            literal_start += chunk

    while i < n:
        v = words[i]
        j = i + 1
        while j < n and words[j] == v and (j - i) < MAX_RUN:
            j += 1
        run = j - i
        threshold = 2 if v == 0 else 3
        if run >= threshold:
            flush_literal(i)
            out.extend(struct.pack('<H', run))
            out.extend(struct.pack('<H', int(v)))
            literal_start = j
        i = j
    flush_literal(n)
    return bytes(out)


CASES = [
    np.zeros(76800, np.uint16),
    np.full(76800, 7, np.uint16),
    np.arange(76800, dtype=np.uint16),
    np.r_[np.zeros(MAX_RUN + 1, np.uint16), [5]].astype(np.uint16),
    np.r_[np.zeros(MAX_RUN + 2, np.uint16), [5, 5, 5]].astype(np.uint16),
    np.r_[np.full(2 * MAX_RUN + 2, 9, np.uint16), [1, 2, 1]].astype(np.uint16),
    np.r_[np.arange(MAX_RUN + 10, dtype=np.uint16), [3, 3, 3, 3]].astype(np.uint16),
    np.array([4], np.uint16),
    np.array([0, 0], np.uint16),
    np.array([1, 1], np.uint16),
]


class Rle(unittest.TestCase):
    def test_same_bytes_as_reference(self):
        rng = np.random.default_rng(20261113)
        cases = list(CASES)
        for _ in range(30):
            cases.append(rng.integers(0, 4, rng.integers(1, 5000)).astype(np.uint16))
            base = np.repeat(rng.integers(0, 65536, 300).astype(np.uint16), rng.integers(1, 40, 300))
            cases.append(base)
        for arr in cases:
            self.assertEqual(codec.rle_encode(arr), reference_rle(arr))

    def test_round_trip_key_and_delta(self):
        rng = np.random.default_rng(7)
        prev = rng.integers(0, 65536, 76800).astype(np.uint16)
        cur = prev.copy()
        cur[1000:5000] = 0x1234
        cur[rng.integers(0, 76800, 300)] ^= 0x0841
        for is_key, arr in ((True, cur), (False, np.bitwise_xor(cur, prev))):
            work = prev.copy() if not is_key else np.zeros(76800, np.uint16)
            filled = codec.rle_decode(codec.rle_encode(arr), work, is_key)
            self.assertEqual(filled, 76800)
            self.assertTrue(np.array_equal(work, cur))


class Deadzone(unittest.TestCase):
    def test_small_changes_are_dropped(self):
        prev = np.array([0x0000, 0x8410, 0xFFFF], np.uint16)
        # +1 у червоній і синій, +2 у зеленій — у межах порога 1
        cur = prev ^ np.array([0x0841, 0x0000, 0x0000], np.uint16)
        cur[2] = 0x0000
        out = codec.deadzone(cur, prev, 1)
        self.assertEqual(out[0], prev[0])
        self.assertEqual(out[2], cur[2])

    def test_zero_threshold_is_lossless(self):
        a = np.arange(100, dtype=np.uint16)
        self.assertIs(codec.deadzone(a, a[::-1].copy(), 0), a)


class Adpcm(unittest.TestCase):
    def test_round_trip_snr(self):
        spf, frames = 334, 48
        t = np.arange(spf * frames) / 8016
        tone = (9000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
        pcm = np.stack([tone, (tone // 2).astype(np.int16)])
        raw, block = codec.encode_audio(pcm, spf)
        self.assertEqual(block, 8 + spf)
        self.assertEqual(len(raw), frames * block)
        got = codec.decode_audio(raw, spf, frames).astype(np.float64)
        ref = pcm.astype(np.float64)
        snr = 10 * np.log10(np.mean(ref ** 2) / np.mean((ref - got) ** 2))
        self.assertGreater(snr, 25)


if __name__ == '__main__':
    unittest.main()
