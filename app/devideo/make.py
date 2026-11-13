# -*- coding: utf-8 -*-
"""Один крок: відео -> файл для плати або CPUlator -> перевірка."""
import os
import re
import shutil
import time

import numpy as np

from . import blob as blobmod
from . import codec, elf, media, verify
from .targets import BLOB_MAX, FPS, QUALITY, TARGETS

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE = os.path.join(ROOT, 'firmware', 'cpulator', 'build', 'player_cpulator.elf')
LOADER = os.path.join(ROOT, 'host', 'load_blob.tcl')
QUARTUS_STP = r'C:\intelFPGA_lite\18.1\quartus\bin64\quartus_stp.exe'

LOAD_BAT = r"""@echo off
rem Заливка ролика в SDRAM плати DE1-SoC через JTAG (близько 1 МБ/с).
rem Спершу прошийте ПЛІС і запустіть firmware\board\player.amp у Monitor Program.
chcp 65001 >nul
cd /d "%~dp0"
set STP=quartus_stp
if exist "{stp}" set STP="{stp}"
%STP% -t load_blob.tcl "{blob}"
pause
"""


def safe_name(path):
    base = os.path.splitext(os.path.basename(path))[0]
    base = re.sub(r'[^A-Za-z0-9_-]+', '_', base).strip('_') or 'video'
    return base[:40]


def make(video, target_key, quality=None, with_audio=True, start=0.0, length=None,
         out_dir=None, log=print):
    """Повертає словник: шляхи файлів, результат перевірки, числа."""
    target = TARGETS[target_key]
    threshold = QUALITY[quality or target.quality][0]
    t0 = time.time()
    info = media.probe(video)
    total = info['duration'] - start
    if length:
        total = min(total, length)
    log('Джерело: %s, %s, %.1f с%s' % (info['video'] or '?', '%dx%d' % info['size'] if info['size'] else '?',
                                         info['duration'], ', звук %s' % info['audio'] if info['audio'] else ', без звуку'))
    log('Кадрую до 320x240 з полями, 24 кадри на секунду, мертва зона %d...' % threshold)

    packer = blobmod.Packer(threshold)
    expect = max(1, int(round(total * FPS)))
    for fr in media.frames(video, start, length):
        packer.add(fr)
        n = len(packer.payloads)
        if n % 240 == 0:
            done = sum(len(p) for p in packer.payloads)
            log('  кадрів %d з ~%d, стиснуто в %.1f МБ' % (n, expect, done / 1e6))
    st = packer.stats()
    if st['frames'] == 0:
        raise ValueError('у файлі немає жодного кадру')

    pcm, audio_blocks = None, b''
    if with_audio and info['audio']:
        log('Звук: перераховую на %d Гц і кодую IMA ADPCM по %d відліків на кадр...'
            % (target.audio_rate, target.spf))
        a = media.audio(video, target.audio_rate, start, length)
        need = st['frames'] * target.spf
        pcm = np.zeros((2, need), dtype=np.int16)
        if a is not None:
            pcm[:, :min(need, a.shape[1])] = a[:, :need]
        audio_blocks, _ = codec.encode_audio(pcm, target.spf)
    data = packer.finish(audio_blocks, target.spf, target.header_rate)
    log('  %d кадрів (%d опорних), відео %.2f МБ, звук %.2f МБ, середній кадр %d байтів'
        % (st['frames'], st['keys'], st['video_bytes'] / 1e6, len(audio_blocks) / 1e6, st['avg_frame']))
    log('  ціна мертвої зони: PSNR у середньому %.1f дБ, найгірше %.1f дБ' % (st['psnr_avg'], st['psnr_min']))
    if len(data) > BLOB_MAX:
        raise ValueError('ролик займає %.1f МіБ, а в SDRAM під нього є %.0f МіБ: візьміть коротший '
                         'фрагмент або компактнішу якість' % (len(data) / 1048576, BLOB_MAX / 1048576))

    out_dir = out_dir or os.path.join(ROOT, 'output')
    os.makedirs(out_dir, exist_ok=True)
    stem = '%s_%s' % (safe_name(video), target.key)
    files = []
    if target.board:
        main = os.path.join(out_dir, stem + '.bin')
        with open(main, 'wb') as f:
            f.write(data)
        shutil.copy2(LOADER, os.path.join(out_dir, 'load_blob.tcl'))
        bat = os.path.join(out_dir, stem + '_load.bat')
        with open(bat, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write(LOAD_BAT.format(stp=QUARTUS_STP, blob=os.path.basename(main)))
        files = [main, bat, os.path.join(out_dir, 'load_blob.tcl')]
        raw = data
    else:
        with open(TEMPLATE, 'rb') as f:
            raw = elf.build(f.read(), data)
        main = os.path.join(out_dir, stem + '.elf')
        with open(main, 'wb') as f:
            f.write(raw)
        files = [main]
    log('Записано: %s (%.2f МБ)' % (os.path.basename(main), len(raw) / 1e6))

    log('Перевіряю файл...')
    ok, lines, nums = verify.check(raw, packer.crcs, pcm, log=log)
    for ln in lines:
        log('  ' + ln)
    nums.update(st)
    log('Готово за %.1f с — %s' % (time.time() - t0, 'перевірку пройдено' if ok else 'Є ПОМИЛКИ'))
    return {'ok': ok, 'files': files, 'main': main, 'nums': nums, 'lines': lines, 'target': target,
            'threshold': threshold}
