# -*- coding: utf-8 -*-
"""Читання відео й звуку через ffmpeg (його привозить пакет imageio-ffmpeg).

Кадри: 320x240, RGB565, рівно 24 на секунду. Кадр вписується з чорними полями,
а не розтягується. hqdn3d приглушує зерно, інакше нерухомі ділянки ворушаться
від кадру до кадру й стиснення падає. Впорядковане розмиття (bayer) прибирає
сходинки в темних сценах і так само нерухоме від кадру до кадру.
"""
import re
import shutil
import subprocess
import sys

import numpy as np

from .targets import FPS, HEIGHT, WIDTH

VF = ('hqdn3d=4:4:9:9,'
      'scale=%d:%d:flags=lanczos+accurate_rnd+full_chroma_int:force_original_aspect_ratio=decrease,'
      'pad=%d:%d:(ow-iw)/2:(oh-ih)/2:black,fps=%d' % (WIDTH, HEIGHT, WIDTH, HEIGHT, FPS))
FRAME_BYTES = WIDTH * HEIGHT * 2
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = shutil.which('ffmpeg')
        if exe:
            return exe
    raise RuntimeError('Не знайдено ffmpeg: запустіть run.bat, він поставить пакет imageio-ffmpeg.')


def _span(start, length):
    args = ['-ss', '%.3f' % start] if start else []
    return args, (['-t', '%.3f' % length] if length else [])


def probe(path):
    """Тривалість і наявність доріжок. ffmpeg -i завжди завершується помилкою
    «немає вихідного файла», тому дивимося лише на його опис входу."""
    r = subprocess.run([ffmpeg_exe(), '-hide_banner', '-i', path], capture_output=True,
                       creationflags=NO_WINDOW)
    text = r.stderr.decode('utf-8', 'replace')
    m = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', text)
    if not m:
        raise ValueError('ffmpeg не розпізнав файл як відео:\n' + text.strip()[-400:])
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    v = re.search(r'Stream #\S+.*Video: (\w+)[^\n]*?(\d{2,5})x(\d{2,5})', text)
    a = re.search(r'Stream #\S+.*Audio: (\w+)', text)
    return {'duration': dur, 'video': v.group(1) if v else None,
            'size': (int(v.group(2)), int(v.group(3))) if v else None,
            'audio': a.group(1) if a else None}


def frames(path, start=0.0, length=None):
    """Генератор кадрів: uint16 [WIDTH * HEIGHT] у RGB565."""
    pre, post = _span(start, length)
    cmd = ([ffmpeg_exe(), '-hide_banner', '-loglevel', 'error'] + pre + ['-i', path] + post +
           ['-an', '-vf', VF, '-sws_flags', 'lanczos+accurate_rnd+full_chroma_int',
            '-sws_dither', 'bayer', '-pix_fmt', 'rgb565le', '-f', 'rawvideo', '-'])
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    try:
        while True:
            raw = p.stdout.read(FRAME_BYTES)
            if len(raw) < FRAME_BYTES:
                break
            yield np.frombuffer(raw, dtype='<u2').copy()
    finally:
        p.stdout.close()
        err = p.stderr.read().decode('utf-8', 'replace')
        p.stderr.close()
        p.wait()
        if p.returncode not in (0, None) and sys.exc_info()[0] is None:
            raise RuntimeError('ffmpeg не зміг розкодувати відео:\n' + err.strip()[-400:])


def audio(path, rate, start=0.0, length=None):
    """Доріжка як int16 [2, відліки] на частоті rate або None, якщо звуку немає."""
    pre, post = _span(start, length)
    cmd = ([ffmpeg_exe(), '-hide_banner', '-loglevel', 'error'] + pre + ['-i', path] + post +
           ['-vn', '-ac', '2', '-ar', str(rate), '-f', 's16le', '-'])
    r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError('ffmpeg не зміг прочитати звук:\n' + r.stderr.decode('utf-8', 'replace')[-400:])
    a = np.frombuffer(r.stdout, dtype='<i2')
    a = a[:len(a) // 2 * 2]
    return a.reshape(-1, 2).T.copy() if len(a) else None


def to_rgb(frame):
    """RGB565 -> uint8 [HEIGHT, WIDTH, 3] (для знімків і оцінки якості)."""
    f = frame.astype(np.uint32).reshape(HEIGHT, WIDTH)
    r = (f >> 11) & 0x1F
    g = (f >> 5) & 0x3F
    b = f & 0x1F
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], axis=2).astype(np.uint8)
