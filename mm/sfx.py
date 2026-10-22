"""きっかけの効果音（楽器では出せないもの）。score.sfx の予約から1本の stem を作る。

楽器で出せるもの（一撃・きらめき・シンバルの連打）は compose.apply_cues が音符で書く。
ここは雑音を形づくる音：風切り・上昇・低い衝撃・逆再生シンバル・軽い打音・レコードの針音。
"""
import math

import numpy as np

from .synth import RNG, _filt


def _sweep_noise(n, sr, f_from, f_to, q=1.2, steps=48):
    """帯域の中心が動く雑音（区切って濾し、つなぐ）。"""
    x = RNG.normal(0, 1, n)
    out = np.zeros(n)
    edges = np.linspace(0, n, steps + 1).astype(int)
    for k in range(steps):
        a, b = edges[k], edges[k + 1]
        if b <= a:
            continue
        frac = (k + 0.5) / steps
        f = f_from * (f_to / f_from) ** frac
        pad = min(a, 2048)
        seg = _filt(x[a - pad:b], 'bp', f, sr, q)[pad:]
        out[a:b] = seg
    return out


def whoosh(sr, length, strength):
    n = int(length * sr)
    t = np.linspace(0, 1, n)
    env = np.sin(math.pi * t) ** 2
    y = _sweep_noise(n, sr, 400, 3500, 1.0)
    y = y * (1 - t) + _sweep_noise(n, sr, 3500, 600, 1.0) * t
    pan = np.linspace(-0.8, 0.8, n)
    L = y * env * np.cos((pan + 1) * math.pi / 4)
    R = y * env * np.sin((pan + 1) * math.pi / 4)
    return np.stack([L, R], 1) * 0.5 * strength


def riser(sr, length, strength):
    n = int(length * sr)
    t = np.linspace(0, 1, n)
    y = _sweep_noise(n, sr, 300, 8000, 2.0) * t ** 2.2
    # 上がる和音の気配（弦の高音のような柔らかい上昇）
    f = 220 * 2 ** (2 * t)
    tone = sum(np.sin(2 * math.pi * np.cumsum(f * r) / sr) for r in (1, 1.5, 2.0)) / 3
    y = y + _filt(tone, 'lp', 3000, sr) * t ** 3 * 0.25
    y[-int(0.01 * sr):] *= np.linspace(1, 0, int(0.01 * sr))
    d = int(0.013 * sr)
    R = np.concatenate([np.zeros(d), y[:-d]])
    return np.stack([y, R], 1) * 0.45 * strength


def swell(sr, length, strength):
    """逆再生のシンバル。"""
    n = int(length * sr)
    t = np.linspace(0, 1, n)
    y = _filt(RNG.normal(0, 1, n), 'hp', 4000, sr) * np.exp((t - 1) * 5)
    y[-int(0.005 * sr):] *= np.linspace(1, 0, int(0.005 * sr))
    d = int(0.009 * sr)
    R = np.concatenate([np.zeros(d), y[:-d]])
    return np.stack([y, R], 1) * 0.35 * strength


def impact(sr, length, strength):
    n = int(length * sr)
    t = np.arange(n) / sr
    f = 32 + 48 * np.exp(-t / 0.12)
    boom = np.sin(2 * math.pi * np.cumsum(f) / sr) * np.exp(-t / 0.9)
    body = _filt(RNG.normal(0, 1, n), 'lp', 900, sr) * np.exp(-t / 0.25) * 0.5
    tail = _filt(RNG.normal(0, 1, n), 'bp', 2500, sr, 0.5) * np.exp(-t / 1.2) * 0.12
    y = boom + body + tail
    return np.stack([y, y], 1) * 0.7 * strength


def click(sr, length, strength):
    n = int(0.12 * sr)
    t = np.arange(n) / sr
    body = np.sin(2 * math.pi * (900 + 600 * np.exp(-t / 0.01)) * t) * np.exp(-t / 0.018)
    tick = _filt(RNG.normal(0, 1, n), 'bp', 4500, sr, 2) * np.exp(-t / 0.004)
    y = body * 0.5 + tick * 0.6
    return np.stack([y, y], 1) * 0.4 * strength


def vinyl(sr, n):
    """レコードの針音（ローファイ用）：細かいぷちぷちと、低いざらつき。"""
    y = _filt(RNG.normal(0, 1, n), 'bp', 1200, sr, 0.4) * 0.012
    pops = RNG.random(n) < 7 / sr
    imp = np.zeros(n)
    imp[pops] = RNG.uniform(0.2, 1.0, pops.sum()) * RNG.choice([-1, 1], pops.sum())
    y += _filt(imp, 'hp', 1500, sr) * 0.25
    return np.stack([y, np.roll(y, 37)], 1)


# @couple:cue-types きっかけの種類を足したら spec.CUE_TYPES と compose.apply_cues も
MAKERS = {'whoosh': whoosh, 'riser': riser, 'swell': swell, 'impact': impact, 'click': click}


def render(score, n, sr):
    out = np.zeros((n, 2))
    for ev in score.sfx:
        maker = MAKERS.get(ev['type'])
        if not maker:
            continue
        y = maker(sr, ev['len_sec'], ev['strength'])
        if ev['type'] in ('riser', 'swell'):
            start = int(ev['sec'] * sr)          # 終わりが at にそろう
        elif ev['type'] == 'whoosh':
            start = int((ev['sec'] - ev['len_sec'] / 2) * sr)   # 真ん中が at
        else:
            start = int(ev['sec'] * sr)
        a = max(0, start)
        y = y[a - start:]
        b = min(n, a + len(y))
        out[a:b] += y[:b - a]
    return out.astype(np.float32)
