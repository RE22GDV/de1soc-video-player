# -*- coding: utf-8 -*-
"""Схеми й графіки для README та звіту (чорно-білі, Times New Roman).

    python docs/make_figures.py [файл_плати.bin файл_cpulator.elf]

Без аргументів — лише схеми й графіки синхронізації (дані з sync_*.csv у цій
теці). З аргументами — ще й графік розмірів кадрів готових роликів і аркуш
кадрів, розпакованих із файла для CPUlator (trailer_frames.png).
Потрібні Graphviz (dot), matplotlib і Pillow — лише для цих рисунків, не для застосунку.
"""
import csv
import os
import subprocess
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'app'))
from devideo import blob, verify                  # noqa: E402

import matplotlib                                 # noqa: E402
matplotlib.use('Agg')
import matplotlib.pyplot as plt                   # noqa: E402
from matplotlib.ticker import FuncFormatter       # noqa: E402

plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 12, 'axes.linewidth': 0.8,
                     'savefig.dpi': 200, 'savefig.bbox': 'tight'})
FONT = 'Times New Roman,'   # кома: інакше Pango сприймає «Roman» як стиль
SETTLE = 8.0                # перші секунди браузер лише розганяє звук — у статистику не йдуть


def num(v, fmt='%g'):
    """Число за ДСТУ: десяткова кома й справжній мінус."""
    return (fmt % v).replace('.', ',').replace('-', '−')


def comma_axes(*axes):
    for ax in axes:
        for a in (ax.xaxis, ax.yaxis):
            a.set_major_formatter(FuncFormatter(lambda v, _: num(round(v, 6))))


def dot(name, src):
    path = os.path.join(HERE, name + '.png')
    subprocess.run(['dot', '-Tpng', '-Gdpi=300', '-o', path], input=src.encode('utf-8'), check=True)
    with Image.open(path) as im:
        print('рисунок %s: %.1f x %.1f см без масштабування' % (
            name, im.width / 300 * 2.54, im.height / 300 * 2.54))


# --------------------------------------------------------------- схеми
def diagram_system():
    dot('diagram_system', r'''
digraph G {
  rankdir=TB; nodesep=0.22; ranksep=0.32; newrank=true;
  node [shape=box, fontname="%s", fontsize=13, style="rounded", margin="0.07,0.04"];
  edge [arrowsize=0.7];
  subgraph cluster_pc { label="Комп'ютер: застосунок video_player (Python)"; fontname="%s"; fontsize=13; style=dashed;
    src [label="Відеофайл\nmp4, mkv, avi, mov, webm"];
    ff  [label="ffmpeg: 320×240 з полями,\n24 кадри/с, RGB565"];
    aud [label="ffmpeg: звук 8 або\n48 кГц, стерео"];
    { rank=same; ff; aud; }
    src -> ff; src -> aud;
    vc  [label="Кадри: XOR із попереднім,\nмертва зона, RLE слів"];
    ac  [label="Звук: IMA ADPCM,\nшматок на кадр"];
    { rank=same; vc; ac; }
    ff -> vc; aud -> ac;
    blb [label="Блок «DE1V»: заголовок 48 Б,\nпокажчик кадрів, кадри, звук"];
    vc -> blb; ac -> blb;
    ver [label="Перевірка: розпакування всіх кадрів\nі звуку, контрольні суми, SNR"];
    blb -> ver;
  }
  bin [label="Плата: файл .bin, load_blob.tcl\n(JTAG у SDRAM 0xC0100000)"];
  elf [label="CPUlator: ELF із сегментом\n0xC0100000, місцевий посередник"];
  { rank=same; bin; elf; }
  ver -> bin; ver -> elf;
  subgraph cluster_board { label="Плата DE1-SoC, система ЛР6"; labelloc=b; fontname="%s"; fontsize=13; style=dashed;
    arm [label="ARM Cortex-A9: player.amp\n(Monitor Program)"];
    vga [label="VGA 320×240,\nподвійний буфер"];
    cod [label="Кодек WM8731,\n48 кГц задає темп"];
    { rank=same; vga; cod; }
    arm -> vga; arm -> cod;
  }
  sim [label="CPUlator: VGA, звук 8 кГц,\nKEY, SW, HEX, LEDR"];
  { rank=same; arm; sim; }
  bin -> arm; elf -> sim;
}''' % ((FONT,) * 3))


def diagram_format():
    dot('diagram_format', r'''
digraph F {
  rankdir=TB; nodesep=0.2; ranksep=0.32;
  node [shape=record, fontname="%s", fontsize=13];
  edge [fontname="%s", fontsize=12, arrowsize=0.7];
  blk [label="{Блок «DE1V» (у SDRAM з адреси 0xC0100000)|{заголовок\n48 Б|покажчик кадрів\nN × 8 Б|кадри RLE,\nвирівняні на 4 Б|звук: N шматків\nпо 8 + spf байтів|підпис\n«DE1V»}}"];
  hdr [label="{Заголовок|{сигнатура,\nверсія 2|прапорці\n(біт 0 – звук)|ширина, висота,\nкадрів/с|період опорних\nкадрів, кадрів N}|{зміщення покажчика,\nданих, звуку|розмір блока|байтів у шматку\nзвуку|частота, spf,\nканали}}"];
  idx [label="{Елемент покажчика, 8 Б|{зміщення кадру|розмір, старший біт – опорний кадр}}"];
  snd [label="{Шматок звуку кадру|{стан L, 4 Б|стан R, 4 Б|L: spf напівбайтів|R: spf напівбайтів}}"];
  blk -> hdr [label="  48 Б"];
  hdr -> idx [label="  index_off"];
  idx -> snd [label="  audio_off + k · (8 + spf)"];
}''' % (FONT, FONT))


def diagram_tempo():
    dot('diagram_tempo', r'''
digraph T {
  rankdir=TB; nodesep=0.3; ranksep=0.2; newrank=true;
  node [shape=box, fontname="%s", fontsize=13, margin="0.08,0.03"];
  edge [arrowsize=0.65, fontname="%s", fontsize=13];
  s [style=rounded, label="Старт: seek_to(0)"];
  k [label="read_keys(): KEY0 – пауза або продовження,\nKEY1 – з початку (через seek_to)"];
  f [label="audio_fill(): долити чергу звуку\n(після кінця ролика – тишею)"];
  c [label="update_clock(): clk за таймером у межах\nвід w − LATENCY − MAX_LAG до w − LATENCY"];
  p [shape=diamond, margin="0,0", label="пауза або\nкінець?"];
  w [label="want = (clk + cost · rate / TIMER_HZ) / spf"];
  e [shape=diamond, margin="0,0", height=0.5, label="want ≥ N?"];
  x [label="SW0 = 1: seek_to(0),\nінакше КІНЕЦЬ"];
  q [shape=diamond, margin="0,0", label="want >\nпоказаного?"];
  d [label="розпакувати кадри до want, показати\nостанній, cost = (3 · cost + тривалість) / 4"];
  a1 [shape=circle, label="А", margin=0, width=0.34, height=0.34];
  a2 [shape=circle, label="А", margin=0, width=0.34, height=0.34];
  a3 [shape=circle, label="А", margin=0, width=0.34, height=0.34];
  a4 [shape=circle, label="А", margin=0, width=0.34, height=0.34];
  a5 [shape=circle, label="А", margin=0, width=0.34, height=0.34];
  { rank=same; a1; k; }
  { rank=same; p; a2; }
  { rank=same; e; x; a5; }
  { rank=same; q; a3; }
  { rank=same; d; a4; }
  s -> k; a1 -> k; k -> f -> c -> p;
  p -> a2 [label=" так "]; p -> w [label=" ні"];
  w -> e; e -> x [label=" так "]; x -> a5; e -> q [label=" ні"];
  q -> a3 [label=" ні "]; q -> d [label=" так"];
  d -> a4;
}''' % (FONT, FONT))


# --------------------------------------------------------------- графіки
def read_sync(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    v = np.array([float(r['video']) for r in rows])
    s = np.array([float(r['sound']) for r in rows])
    w = np.array([float(r['wall']) for r in rows])
    return w, v, v - s


def sync_stats(w, v, d):
    m = v >= SETTLE
    gaps = np.diff(w[m])
    return {'min': float(d[m].min()), 'max': float(d[m].max()), 'mean': float(d[m].mean()),
            'fps': float(m.sum() / (w[m][-1] - w[m][0])), 'gap': float(gaps.max()),
            'reached': float(v.max())}


def plot_sync():
    """Зображення мінус звук: стара прошивка (темп за таймером) і нова (годинник за звуком).
    sync_*.csv пише tests/cpulator_sync.py (спостереження за Web Audio в CPUlator)."""
    series = [('sync_timer.csv', 'темп за таймером', 'black', ':'),
              ('sync_clock.csv', 'годинник за чергою звуку', 'black', '-')]
    fig, ax = plt.subplots(figsize=(6.5, 2.9))
    ax.axhline(0, color='0.6', linewidth=0.7, linestyle=':')
    out = {}
    for name, label, color, ls in series:
        w, v, d = read_sync(name)
        out[name] = sync_stats(w, v, d)
        ax.plot(v, d, color=color, linewidth=0.8, linestyle=ls, label=label)
    ax.axvspan(0, SETTLE, color='0.92', linewidth=0)
    ax.set_xlabel('Час зображення від початку фрагмента, с')
    ax.set_ylabel('Зображення мінус звук, с')
    ax.legend(loc='lower right', frameon=False, fontsize=11)
    comma_axes(ax)
    fig.savefig(os.path.join(HERE, 'plot_sync.png'))
    plt.close(fig)
    for k, st in out.items():
        print('%s: %s…%s с, середнє %s с, %s кадру/с, найбільша пауза %s с, дійшло до %s с' % (
            k, num(st['min'], '%+.2f'), num(st['max'], '%+.2f'), num(st['mean'], '%+.2f'),
            num(st['fps'], '%.1f'), num(st['gap'], '%.3f'), num(st['reached'], '%.1f')))
    return out


def plot_sync_full():
    """Весь ролик з новою прошивкою (sync_full.csv)."""
    path = os.path.join(HERE, 'sync_full.csv')
    if not os.path.exists(path):
        return None
    w, v, d = read_sync('sync_full.csv')
    st = sync_stats(w, v, d)
    fig, ax = plt.subplots(figsize=(6.5, 2.6))
    ax.axhline(0, color='0.6', linewidth=0.7, linestyle=':')
    ax.axvspan(0, SETTLE, color='0.92', linewidth=0)
    ax.plot(v, d, color='black', linewidth=0.7)
    ax.set_xlabel('Час зображення, с')
    ax.set_ylabel('Зображення мінус звук, с')
    comma_axes(ax)
    fig.savefig(os.path.join(HERE, 'plot_sync_full.png'))
    plt.close(fig)
    print('sync_full: %s…%s с, середнє %s с, %s кадру/с, найбільша пауза %s с, дійшло до %s с' % (
        num(st['min'], '%+.2f'), num(st['max'], '%+.2f'), num(st['mean'], '%+.2f'),
        num(st['fps'], '%.1f'), num(st['gap'], '%.3f'), num(st['reached'], '%.1f')))
    return st


def frame_sizes(path):
    with open(path, 'rb') as f:
        data, _ = verify.blob_from_file(f.read())
    h = blob.parse(data)
    idx = blob.index(data, h)
    raw = idx[:, 1].astype(np.int64)
    size = raw & (blob.KEY_FLAG - 1)
    key = (raw & blob.KEY_FLAG) != 0
    return h, size, key


def plot_frames(board_path, cpulator_path):
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(6.5, 4.8), gridspec_kw={'height_ratios': [1.15, 1]})
    out = {}
    for path, label, ls in ((board_path, 'плата, мертва зона 4', '-'),
                            (cpulator_path, 'CPUlator, мертва зона 2', '--')):
        h, size, key = frame_sizes(path)
        fps = h['fps']
        n = len(size) // fps
        per_sec = size[:n * fps].reshape(n, fps).sum(axis=1) / 1024
        a2.plot(np.arange(n) + 0.5, per_sec, color='black', linewidth=0.8, linestyle=ls, label=label)
        out[label] = {'avg': float(size.mean()), 'key_avg': float(size[key].mean()),
                      'delta_avg': float(size[~key].mean()), 'max_sec': float(per_sec.max()),
                      'frames': int(len(size))}
        if path == board_path:
            t = np.arange(len(size)) / fps
            a1.plot(t[~key], size[~key] / 1024, color='black', linewidth=0.35, label='проміжні кадри')
            a1.plot(t[key], size[key] / 1024, 'o', color='0.4', markersize=2.2, label='опорні кадри')
    a1.set_ylabel('Кадр, КіБ')
    a1.legend(loc='lower center', bbox_to_anchor=(0.5, 1.0), frameon=False, fontsize=11, ncol=2)
    a2.set_ylabel('Відео за секунду, КіБ')
    a2.set_xlabel('t, с')
    a2.legend(loc='lower center', bbox_to_anchor=(0.5, 1.0), frameon=False, fontsize=11, ncol=2)
    fig.subplots_adjust(hspace=0.42)
    comma_axes(a1, a2)
    fig.savefig(os.path.join(HERE, 'plot_frames.png'))
    plt.close(fig)
    for k, st in out.items():
        print('%s: кадрів %d, середній %d Б, опорний %d Б, проміжний %d Б, найбільше за секунду %d КіБ' % (
            k, st['frames'], st['avg'], st['key_avg'], st['delta_avg'], st['max_sec']))
    return out


TRAILER_PICKS = (576, 864, 1056, 1248, 1536, 2208)   # 24, 36, 44, 52, 64 і 92 с трейлера
GAP = 4


def trailer_frames(path, picks=TRAILER_PICKS, cols=3):
    """Кадри саме такими, якими їх покаже програвач: розпаковані незалежним
    декодером із готового файла (320x240, RGB565, після мертвої зони)."""
    from devideo import codec, media
    with open(path, 'rb') as f:
        data, _ = verify.blob_from_file(f.read())
    h = blob.parse(data)
    idx = blob.index(data, h)
    work = np.zeros(320 * 240, dtype=np.uint16)
    tiles = {}
    for k in range(max(picks) + 1):               # дельти розгортаються лише підряд
        off, size = int(idx[k, 0]), int(idx[k, 1])
        key = bool(size & blob.KEY_FLAG)
        codec.rle_decode(data[off:off + (size & (blob.KEY_FLAG - 1))], work, key)
        if k in picks:
            tiles[k] = Image.fromarray(media.to_rgb(work.copy()))
    rows = (len(picks) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * 320 + (cols - 1) * GAP, rows * 240 + (rows - 1) * GAP), 'white')
    for i, k in enumerate(picks):
        sheet.paste(tiles[k], ((i % cols) * (320 + GAP), (i // cols) * (240 + GAP)))
    sheet.save(os.path.join(HERE, 'trailer_frames.png'))
    print('рисунок trailer_frames.png: кадри', ', '.join('%d (%d с)' % (k, k // 24) for k in picks))


if __name__ == '__main__':
    diagram_system()
    diagram_format()
    diagram_tempo()
    plot_sync()
    plot_sync_full()
    if len(sys.argv) >= 3:
        plot_frames(sys.argv[1], sys.argv[2])
        trailer_frames(sys.argv[2])
