# -*- coding: utf-8 -*-
"""Увесь ланцюг на справжньому відеофайлі з теки й назви кирилицею (потрібен ffmpeg)."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
from devideo import media                         # noqa: E402
from devideo.make import make                     # noqa: E402

try:
    FFMPEG = media.ffmpeg_exe()
except RuntimeError:
    FFMPEG = None


@unittest.skipUnless(FFMPEG, 'потрібен ffmpeg (його ставить run.bat)')
class Make(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = os.path.join(cls.tmp.name, 'відео кирилицею')
        os.makedirs(d)
        cls.video = os.path.join(d, 'Тестовий ролик.mp4')
        subprocess.run([FFMPEG, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '3',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', cls.video],
                       check=True, creationflags=media.NO_WINDOW)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_probe(self):
        info = media.probe(self.video)
        self.assertAlmostEqual(info['duration'], 3.0, delta=0.1)
        self.assertEqual(info['size'], (640, 360))
        self.assertTrue(info['audio'])

    def run_target(self, target):
        out = os.path.join(self.tmp.name, 'вихід')
        res = make(self.video, target, out_dir=out, log=lambda *a: None)
        self.assertTrue(res['ok'], res['lines'])
        self.assertEqual(res['nums']['frames'], 72)
        self.assertTrue(os.path.exists(res['main']))
        return res

    def test_cpulator(self):
        res = self.run_target('cpulator')
        self.assertTrue(res['main'].endswith('.elf'))

    def test_board(self):
        res = self.run_target('board')
        self.assertTrue(res['main'].endswith('.bin'))
        self.assertTrue(all(os.path.exists(f) for f in res['files']))


if __name__ == '__main__':
    unittest.main()
