# -*- coding: utf-8 -*-
"""Автоматична перевірка ролика в CPUlator (браузер без вікна).

Завантажує файл, запускає програвач і дивиться на те саме, що побачила б
людина: чи змінюються кадри на екрані VGA і номер кадру на індикаторах, чи
йде номер кадру не повільніше за MIN_FPS на секунду (у важких сценах
симулятор не встигає показати всі 24), чи є звук у черзі без переповнення, чи
працюють пауза (KEY0) і показ із початку (KEY1).
"""
import os
import re
import time

from . import blob as blobmod
from . import cdp, helper, verify

LEDS = r"""return [...document.querySelectorAll('#devff200000 .dev_led_led')].filter(d=>d.offsetParent!==null)
  .map(d=>{const m=getComputedStyle(d).backgroundColor.match(/\d+/g); return (m && +m[0]>200 && +m[1]<120)?'1':'0';}).join('');"""
AUDIO = "const e=document.getElementById('devff203040'); return e ? e.innerText.replace(/\\s+/g,' ') : '';"
MSG = r"""return (((document.body && document.body.innerText)||'').match(/Total size is \d+ bytes/)||[''])[0];"""
STATE = "return (document.getElementById('tb_runstate')||{}).textContent||'';"
BANNER = "return (document.getElementById('de1a_banner')||{}).textContent||'';"
HEX = r"""return [...document.querySelectorAll('#devff200020 .dev_sseg')].filter(d=>d.offsetParent!==null)
  .map(d=>[...d.children].reduce((v,c,i)=>v|(c.classList.contains('dev_sseg_off')?0:1<<i),0));"""
# вибірка пікселів екрана VGA: частка не чорних і відбиток для порівняння кадрів
VGA = r"""const c=document.querySelector('#devc8000000 canvas'); if(!c) return null;
  const x=c.getContext('2d'); if(!x) return null;
  const d=x.getImageData(0,0,c.width,c.height).data; let lit=0,n=0,h=0;
  for(let i=0;i<d.length;i+=4*97){n++; const s=d[i]+d[i+1]+d[i+2]; if(s>48) lit++; h=(h*31+s)|0;}
  return {w:c.width,h:c.height,lit:lit/n,hash:h};"""
SEG7 = {0x3F: '0', 0x06: '1', 0x5B: '2', 0x4F: '3', 0x66: '4', 0x6D: '5', 0x7D: '6', 0x07: '7',
        0x7F: '8', 0x6F: '9', 0x77: 'A', 0x7C: 'B', 0x39: 'C', 0x5E: 'D', 0x79: 'E', 0x71: 'F',
        0x40: '-', 0x00: ' '}
MIN_FPS = 20          # нижче — у CPUlator показ помітно смикається


def _key(b, n):
    """Натиснути й відпустити кнопку KEYn (позначка в панелі Push buttons)."""
    expr = ("[...document.getElementById('devff200050').querySelectorAll('input[type=checkbox]')]"
            ".find(x=>x.getBoundingClientRect().width>0 && (x.parentElement.textContent||'').trim()==='%d')" % n)
    for _ in range(2):
        r = b.rect(expr)
        b.click(r['x'] + r['w'] / 2, r['y'] + r['h'] / 2)
        time.sleep(0.25)


def read_hex(b):
    return ''.join(SEG7.get(v & 0x7F, '?') for v in (b.js(HEX) or []))


def frame_no(b):
    """Номер кадру з HEX5-HEX0 (шістнадцятковий) або None."""
    s = read_hex(b).strip()
    return int(s, 16) if s and re.fullmatch(r'[0-9A-F]+', s) else None


def _out_level(text):
    m = re.search(r'Out L:\s*(\d+)', text)
    return int(m.group(1)) if m else None


def _track_header(path):
    with open(path, 'rb') as f:
        data, _ = verify.blob_from_file(f.read())
    return blobmod.parse(data)


def run(elf_path, bigmem=False, log=print, shot=None):
    """Повертає (успіх, рядки звіту, числа)."""
    lines, nums, ok = [], {}, True

    def res(good, text):
        nonlocal ok
        ok = ok and good
        lines.append(('OK  ' if good else 'НІ  ') + text)
        log(lines[-1])

    hdr = _track_header(elf_path)
    size = os.path.getsize(elf_path) / 1048576
    h = helper.start(os.path.dirname(os.path.abspath(elf_path)), int(min(1024, 64 + 3 * size)) if bigmem else 0)
    b = None
    try:
        log('Запускаю CPUlator без вікна%s...' % (' з наперед виділеною пам\'яттю' if bigmem else ''))
        b = cdp.Browser(h.url_for(os.path.basename(elf_path)))
        msg = ''
        for attempt in range(2):                             # другий шанс — перезавантажити сторінку
            for _ in range(180):
                try:
                    msg = b.js(MSG) or ''
                except RuntimeError:
                    msg = ''
                if 'Total size' in msg:
                    break
                time.sleep(0.5)
            if 'Total size' in msg:
                break
            log('  файл не з\u2019явився (%s), перезавантажую сторінку...' % (b.js(BANNER) or 'без банера'))
            b.call('Page.reload', ignoreCache=True)
            time.sleep(2)
        res('Total size' in msg, 'файл завантажено: ' + (msg.strip()[-60:] or 'немає відповіді'))
        if 'Total size' not in msg:
            return False, lines, nums
        c = b.rect("[...document.querySelectorAll('button')].find(x=>x.textContent.trim().startsWith('Continue'))")
        b.click(c['x'] + c['w'] / 2, c['y'] + c['h'] / 2)
        time.sleep(2.0)
        res('Running' in (b.js(STATE) or ''), 'програвач запущено')

        # темп: номер кадру на HEX за 6 с
        f0, t0 = frame_no(b), time.time()
        vga, levels, flags = [], [], set()
        while time.time() - t0 < 6:
            vga.append(b.js(VGA))
            a = b.js(AUDIO) or ''
            lv = _out_level(a)
            if lv is not None:
                levels.append(lv)
            flags |= set(re.findall(r'Overflowed: yes', a))
            time.sleep(0.2)
        f1, t1 = frame_no(b), time.time()
        fps = (f1 - f0) / (t1 - t0) if f0 is not None and f1 is not None else 0.0
        nums.update(fps=fps, frame_start=f0, frame_end=f1)
        res(fps >= MIN_FPS, 'кадри йдуть: %s -> %s за %.1f с, %.1f кадру на секунду'
            % (f0, f1, t1 - t0, fps))
        shots = [v for v in vga if v]
        lit = max((v['lit'] for v in shots), default=0.0)
        changes = len(set(v['hash'] for v in shots))
        nums.update(vga_lit=lit, vga_changes=changes)
        res(lit > 0.03 and changes >= 5, 'зображення на VGA: %d різних знімків із %d, світлих пікселів до %.0f %%'
            % (changes, len(shots), lit * 100))
        if hdr['flags'] & 1:
            leds = b.js(LEDS) or ''
            res(len(leds) == 10 and leds[-3] == '1' and bool(levels) and min(levels) > 0,
                'звук: LEDR2 світиться, черга звуку %d…%d відліків'
                % (min(levels, default=0), max(levels, default=0)))
        res(not flags, 'запис звуку без переповнення черги (немає ознаки Overflowed)')
        if shot:
            b.shot(shot)

        _key(b, 0)                                           # пауза
        time.sleep(0.8)
        p0 = frame_no(b)
        time.sleep(1.5)
        p1 = frame_no(b)
        leds = b.js(LEDS) or ''
        res(p0 is not None and p0 == p1 and len(leds) == 10 and leds[-2] == '1',
            'KEY0 — пауза: кадр %s не змінюється, світиться LEDR1' % p0)
        _key(b, 0)                                           # продовження
        time.sleep(1.5)
        r1 = frame_no(b)
        res(r1 is not None and p1 is not None and r1 > p1, 'KEY0 ще раз — показ продовжився: кадр %s' % r1)
        _key(b, 1)                                           # з початку
        time.sleep(0.6)
        s1 = frame_no(b)
        res(s1 is not None and r1 is not None and s1 < min(r1, 3 * 24), 'KEY1 — показ із початку: кадр %s' % s1)
        leds = b.js(LEDS) or ''
        nums['deadline_led'] = len(leds) == 10 and leds[0] == '1'
        return ok, lines, nums
    except Exception as e:
        res(False, 'перевірка зупинилася: %s' % e)
        return False, lines, nums
    finally:
        if b:
            b.close()
        h.stop()
