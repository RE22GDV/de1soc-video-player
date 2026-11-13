# -*- coding: utf-8 -*-
"""Командний рядок (без вікна):

    python app/cli.py make  відео.mp4 --target cpulator|board [--quality best|good|compact]
                            [--no-audio] [--start С] [--length С]
    python app/cli.py check файл.elf|файл.bin [--cpulator [--bigmem]]
    python app/cli.py cpulator файл.elf [--bigmem]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devideo import make as mk, verify          # noqa: E402
from devideo.targets import QUALITY, TARGETS   # noqa: E402


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='DE1-SoC video player')
    sub = ap.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('make', help='відео -> файл для плати або CPUlator')
    m.add_argument('video')
    m.add_argument('--target', choices=sorted(TARGETS), default='cpulator')
    m.add_argument('--quality', choices=sorted(QUALITY), default=None)
    m.add_argument('--no-audio', action='store_true')
    m.add_argument('--start', type=float, default=0.0)
    m.add_argument('--length', type=float, default=None)
    m.add_argument('--out', default=None)
    c = sub.add_parser('check', help='перевірити готовий файл')
    c.add_argument('file')
    c.add_argument('--cpulator', action='store_true', help='також перевірити в CPUlator (браузер без вікна)')
    c.add_argument('--bigmem', action='store_true', help='CPUlator із наперед виділеною пам\'яттю')
    r = sub.add_parser('cpulator', help='відкрити CPUlator із завантаженим файлом')
    r.add_argument('elf')
    r.add_argument('--bigmem', action='store_true')
    a = ap.parse_args()

    if a.cmd == 'make':
        res = mk.make(a.video, a.target, a.quality, not a.no_audio, a.start, a.length, a.out)
        sys.exit(0 if res['ok'] else 1)
    if a.cmd == 'check':
        with open(a.file, 'rb') as f:
            ok, lines, _ = verify.check(f.read())
        print('\n'.join(lines))
        if ok and a.cpulator:
            from devideo import check
            ok = check.run(a.file, a.bigmem)[0]
        sys.exit(0 if ok else 1)
    if a.cmd == 'cpulator':
        from devideo import helper
        size = os.path.getsize(a.elf) / 1048576
        h = helper.start(os.path.dirname(os.path.abspath(a.elf)), int(min(1024, 64 + 3 * size)) if a.bigmem else 0)
        url = h.url_for(os.path.basename(a.elf))
        print('CPUlator:', url)
        import webbrowser
        webbrowser.open(url)
        print('Не закривайте це вікно, поки працюєте. Зупинка — Ctrl+C.')
        try:
            h.thread.join()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
