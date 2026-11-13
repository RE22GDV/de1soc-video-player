# -*- coding: utf-8 -*-
"""Збіг зображення зі звуком у CPUlator: python tests/cpulator_sync.py файл.elf [секунд] [вихід.csv]

Спостерігає за Web Audio на сторінці симулятора: коли браузер запланував кожен
блок звуку. Звук ролика безперервний від першого кадру, тож те, що звучить
зараз, — це (поточний час звуку - початок першого блока). Зображення — номер
кадру на індикаторах у мить, коли він змінився, поділений на 24.
Різниця «зображення мінус звук» > 0 означає, що картинка випереджає звук.
"""
import csv
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
from devideo import cdp, check, helper   # noqa: E402

HOOK = r"""
window.__au = []; window.__ctx = null;
const start0 = AudioBufferSourceNode.prototype.start;
AudioBufferSourceNode.prototype.start = function (when) {
  window.__ctx = this.context;
  window.__au.push({when: when || this.context.currentTime, now: this.context.currentTime});
  return start0.apply(this, arguments);
};
return 1;
"""
POLL = """return {h: [...document.querySelectorAll('#devff200020 .dev_sseg')].filter(d=>d.offsetParent!==null)
  .map(d=>[...d.children].reduce((v,c,i)=>v|(c.classList.contains('dev_sseg_off')?0:1<<i),0)),
  c: (window.__ctx ? window.__ctx.currentTime : -1), f: (window.__au[0] ? window.__au[0].when : null)};"""


def measure(elf_path, seconds=40):
    size = os.path.getsize(elf_path) / 1048576
    h = helper.start(os.path.dirname(os.path.abspath(elf_path)), int(min(1024, 64 + 3 * size)))
    b = cdp.Browser(h.url_for(os.path.basename(elf_path)))
    try:
        for _ in range(240):
            if 'Total size' in (b.js(check.MSG) or ''):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError('CPUlator не завантажив файл за 2 хвилини')
        b.js(HOOK)
        c = b.rect("[...document.querySelectorAll('button')].find(x=>x.textContent.trim().startsWith('Continue'))")
        b.click(c['x'] + c['w'] / 2, c['y'] + c['h'] / 2)
        t0, prev, points = time.time(), None, []
        while time.time() - t0 < seconds:
            r = b.js(POLL)
            s = ''.join(check.SEG7.get(v & 0x7F, '?') for v in r['h']).strip()
            if s and s != prev and r['f'] is not None and all(ch in '0123456789ABCDEF' for ch in s):
                frame = int(s, 16)
                points.append((time.time() - t0, frame / 24.0, r['c'] - r['f']))
                prev = s
            time.sleep(0.005)
        return points
    finally:
        b.close()
        h.stop()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 40
    for attempt in range(3):            # сторінка симулятора інколи не довантажується
        try:
            pts = measure(sys.argv[1], seconds)
            break
        except RuntimeError as e:
            print('спроба %d: %s' % (attempt + 1, e))
    else:
        sys.exit('CPUlator так і не завантажив файл')
    off = [v - a for _, v, a in pts]
    late = [d for (t, _, _), d in zip(pts, off) if t > 8]
    print('замірів %d' % len(pts))
    if late:
        print('зображення мінус звук після 8-ї секунди: %+.2f…%+.2f с, у середньому %+.2f с'
              % (min(late), max(late), sum(late) / len(late)))
    if len(sys.argv) > 3:
        with open(sys.argv[3], 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['wall', 'video', 'sound'])
            for row in pts:
                w.writerow(['%.3f' % x for x in row])


if __name__ == '__main__':
    main()
