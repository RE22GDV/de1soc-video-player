# -*- coding: utf-8 -*-
"""Стиснення кадрів і звуку для програвача DE1-SoC.

Кадр RGB565 (320x240 слів) кодується так само, як у програмі для плати:
  * «виключне або» з попереднім кадром (крім опорних);
  * мертва зона: піксель, що змінився ледь помітно, вважається незміненим;
  * кодування серій по 16-розрядних словах: токен зі старшим розрядом —
    далі стільки окремих слів, без старшого розряду — одне слово, повторити.

Кодер зроблено масивами numpy: він дає ті самі байти, що й покроковий
host/pack_video.py з першої версії програвача (tests/test_codec.py звіряє їх),
а на кадрах трейлера працює приблизно вдесятеро швидше.
Звук — IMA ADPCM шматками по одному на кадр, зі станом на початку шматка.
"""
import numpy as np

MAX_RUN = 0x7FFF

STEP = np.array([
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60,
    66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371,
    408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878,
    2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845,
    8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086,
    29794, 32767], dtype=np.int32)
INDEX = np.array([-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8], dtype=np.int32)
WARMUP = 32                      # відліків попереднього шматка для стану кодера


# ------------------------------------------------------------------ кадри
def deadzone(cur, prev, thr):
    """Піксель лишається попереднім, якщо жодна складова не змінилася більш ніж
    на thr рівнів (у зеленій, де 6 розрядів, — на 2*thr)."""
    if not thr or prev is None:
        return cur
    c = cur.astype(np.int16)
    p = prev.astype(np.int16)
    same = ((np.abs((c >> 11 & 0x1F) - (p >> 11 & 0x1F)) <= thr) &
            (np.abs((c >> 5 & 0x3F) - (p >> 5 & 0x3F)) <= thr * 2) &
            (np.abs((c & 0x1F) - (p & 0x1F)) <= thr))
    return np.where(same, prev, cur)


def _ranges(starts, counts):
    """Індекси starts[i] ... starts[i]+counts[i]-1 для всіх i, підряд."""
    total = int(counts.sum())
    if total == 0:
        return np.empty(0, dtype=np.int64)
    before = np.cumsum(counts) - counts
    return np.repeat(starts - before, counts) + np.arange(total)


def rle_encode(words):
    """Масив uint16 -> bytes токенів. Байт у байт як pack_video.rle_encode."""
    w = np.ascontiguousarray(words, dtype=np.uint16)
    n = len(w)
    if n == 0:
        return b''
    # максимальні серії однакових слів
    starts = np.flatnonzero(np.r_[True, w[1:] != w[:-1]])
    lens = np.diff(np.r_[starts, n])
    vals = w[starts]
    # довгі серії ріжуться на шматки по MAX_RUN, як у жадібному оригіналі
    full, rem = lens // MAX_RUN, lens % MAX_RUN
    nch = full + (rem > 0)
    run_of = np.repeat(np.arange(len(starts)), nch)
    k = np.arange(len(run_of)) - np.repeat(np.cumsum(nch) - nch, nch)
    clen = np.where(k < full[run_of], MAX_RUN, rem[run_of])
    cpos = starts[run_of] + k * MAX_RUN
    cval = vals[run_of]
    # серія вигідна від трьох слів, серія нулів — від двох
    qual = clen >= np.where(cval == 0, 2, 3)

    # невигідні шматки поспіль утворюють буквальні відрізки
    lit = ~qual
    first = lit & ~np.r_[False, lit[:-1]]
    gid = np.cumsum(first) - 1
    li = np.flatnonzero(lit)
    if len(li):
        g_pos = cpos[li][first[li]]
        g_len = np.bincount(gid[li], weights=clen[li]).astype(np.int64)
    else:
        g_pos = g_len = np.empty(0, dtype=np.int64)
    # відрізок довший за MAX_RUN ділиться на частини
    gp = (g_len + MAX_RUN - 1) // MAX_RUN
    g_of = np.repeat(np.arange(len(g_pos)), gp)
    gk = np.arange(len(g_of)) - np.repeat(np.cumsum(gp) - gp, gp)
    l_pos = g_pos[g_of] + gk * MAX_RUN
    l_cnt = np.minimum(MAX_RUN, g_len[g_of] - gk * MAX_RUN)

    q = np.flatnonzero(qual)
    r_pos, r_cnt, r_val = cpos[q], clen[q], cval[q]

    # усі токени в порядку позиції в кадрі
    pos = np.r_[r_pos, l_pos]
    kind = np.r_[np.zeros(len(r_pos), np.int8), np.ones(len(l_pos), np.int8)]
    cnt = np.r_[r_cnt, l_cnt].astype(np.int64)
    val = np.r_[r_val, np.zeros(len(l_pos), np.uint16)]
    order = np.argsort(pos, kind='stable')
    pos, kind, cnt, val = pos[order], kind[order], cnt[order], val[order]

    size = np.where(kind == 1, 1 + cnt, 2)
    hpos = np.cumsum(size) - size
    out = np.empty(int(size.sum()), dtype=np.uint16)
    out[hpos] = np.where(kind == 1, 0x8000 | cnt, cnt).astype(np.uint16)
    runs = kind == 0
    out[hpos[runs] + 1] = val[runs]
    lits = kind == 1
    out[_ranges(hpos[lits] + 1, cnt[lits])] = w[_ranges(pos[lits], cnt[lits])]
    return out.astype('<u2').tobytes()


def rle_decode(data, work, is_key):
    """Незалежний розпаковувач (як decode_frame() програми на платі).
    Змінює work на місці. Повертає кількість заповнених слів."""
    src = np.frombuffer(data, dtype='<u2')
    n = len(work)
    i = p = 0
    while i < n and p < len(src):
        t = int(src[p])
        p += 1
        if t & 0x8000:
            c = t & 0x7FFF
            chunk = src[p:p + c]
            p += c
            if is_key:
                work[i:i + c] = chunk
            else:
                work[i:i + c] ^= chunk
            i += c
        else:
            v = int(src[p])
            p += 1
            if is_key:
                work[i:i + t] = v
            elif v:
                work[i:i + t] ^= v
            i += t
    return i


# ------------------------------------------------------------------ звук
def _encode_step(s, pred, index):
    step = STEP[index]
    diff = s - pred
    code = np.where(diff < 0, 8, 0)
    diff = np.abs(diff)
    delta = step >> 3
    for bit, sh in ((4, 0), (2, 1), (1, 2)):
        st = step >> sh
        hit = diff >= st
        code = code | np.where(hit, bit, 0)
        diff = np.where(hit, diff - st, diff)
        delta = delta + np.where(hit, st, 0)
    pred = np.clip(np.where(code & 8, pred - delta, pred + delta), -32768, 32767)
    index = np.clip(index + INDEX[code], 0, 88)
    return code, pred, index


def adpcm_encode_channel(x, block):
    """Один канал, довжина кратна block. Шматки кодуються всі разом: стан на
    початку шматка береться після розгону на останніх відліках попереднього.
    Повертає (стани [nb, 4] uint8, напівбайти [nb, block // 2] uint8)."""
    x = x.astype(np.int32).reshape(-1, block)
    nb = x.shape[0]
    pred = np.zeros(nb, dtype=np.int32)
    index = np.zeros(nb, dtype=np.int32)
    if nb > 1 and block > WARMUP:
        warm = x[:-1, -WARMUP:]
        p, ix = pred[1:].copy(), index[1:].copy()
        p[:] = warm[:, 0]
        for i in range(1, WARMUP):
            _, p, ix = _encode_step(warm[:, i], p, ix)
        pred[1:], index[1:] = p, ix
    head = np.zeros((nb, 4), dtype=np.uint8)
    pv = pred.astype(np.int16).view(np.uint16)
    head[:, 0], head[:, 1], head[:, 2] = pv & 0xFF, pv >> 8, index
    codes = np.empty((nb, block), dtype=np.uint8)
    for i in range(block):
        c, pred, index = _encode_step(x[:, i], pred, index)
        codes[:, i] = c
    nib = (codes[:, 0::2] | (codes[:, 1::2] << 4)).astype(np.uint8)
    return head, nib


def adpcm_decode_channel(head, nib, block):
    """Незалежний декодер (для перевірки). Повертає int16 [nb * block]."""
    nb = head.shape[0]
    pred = (head[:, 0].astype(np.int32) | (head[:, 1].astype(np.int32) << 8)).astype(np.int16).astype(np.int32)
    index = np.minimum(head[:, 2].astype(np.int32), 88)
    codes = np.empty((nb, block), dtype=np.int32)
    codes[:, 0::2] = nib & 0x0F
    codes[:, 1::2] = nib >> 4
    out = np.empty((nb, block), dtype=np.int16)
    for i in range(block):
        c = codes[:, i]
        step = STEP[index]
        diff = step >> 3
        diff = diff + np.where(c & 4, step, 0) + np.where(c & 2, step >> 1, 0) + np.where(c & 1, step >> 2, 0)
        pred = np.clip(np.where(c & 8, pred - diff, pred + diff), -32768, 32767)
        index = np.clip(index + INDEX[c], 0, 88)
        out[:, i] = pred
    return out.reshape(-1)


def encode_audio(pcm, spf):
    """pcm: int16 [2, frames * spf]. Повертає bytes шматків: стан L, стан R,
    напівбайти L, напівбайти R — 8 + spf байтів на кадр."""
    hl, nl = adpcm_encode_channel(pcm[0], spf)
    hr, nr = adpcm_encode_channel(pcm[1], spf)
    blocks = np.concatenate([hl, hr, nl, nr], axis=1)
    return np.ascontiguousarray(blocks).tobytes(), blocks.shape[1]


def decode_audio(raw, spf, frames):
    a = np.frombuffer(raw, dtype=np.uint8, count=frames * (8 + spf)).reshape(frames, 8 + spf)
    left = adpcm_decode_channel(a[:, 0:4], a[:, 8:8 + spf // 2], spf)
    right = adpcm_decode_channel(a[:, 4:8], a[:, 8 + spf // 2:8 + spf], spf)
    return np.stack([left, right])
