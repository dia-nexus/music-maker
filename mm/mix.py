"""ミックス：楽器ごとの音量合わせ・EQ・圧縮・残響・広がり → 全体の圧縮・ラウドネス・ピークの天井。

stems は「この楽器の音＋その楽器の残響」まで済ませたものを書き出す。stems を全部足すと、
全体の処理（圧縮・ラウドネス合わせ・リミッター）の手前の音になる。
"""
import math

import numpy as np
import pyloudnorm as pyln
from pedalboard import Compressor, HighpassFilter, HighShelfFilter, Limiter, LowpassFilter, LowShelfFilter, PeakFilter, Pedalboard
from scipy import signal

from . import sfx
from .synth import RNG

# 系統ごとの下ごしらえ：低域を切る周波数、残響へ送る量、EQ、圧縮
# @couple:instrument-families 系統を足したら styles.INSTRUMENTS と synth.VOICES と impact.rules.cjs の照合も
FAMILY = {
    'keys':    dict(hp=90,  send=0.20, eq=[('peak', 300, -3.5, 0.9)], comp=(-20, 2.0)),
    'epiano':  dict(hp=80,  send=0.18, eq=[('peak', 300, -2.5, 0.9)], comp=(-18, 2.0)),
    'mallet':  dict(hp=180, send=0.30, eq=[], comp=None),
    'pluck':   dict(hp=100, send=0.22, eq=[('peak', 250, -3.5, 0.9), ('peak', 3500, 2.0, 0.8)], comp=(-20, 2.5)),
    'bass':    dict(hp=30,  send=0.04, eq=[('low', 90, 4.0, 0.7), ('peak', 300, -4.0, 0.8)], comp=(-18, 3.5)),
    'bowed':   dict(hp=45,  send=0.36, eq=[('peak', 350, -3.0, 0.8)], comp=None),
    'brass':   dict(hp=55,  send=0.28, eq=[], comp=(-16, 2.0)),
    'reed':    dict(hp=90,  send=0.30, eq=[], comp=None),
    'flute':   dict(hp=150, send=0.32, eq=[], comp=None),
    'choir':   dict(hp=140, send=0.42, eq=[('peak', 400, -3.5, 0.9)], comp=None),
    'timpani': dict(hp=30,  send=0.30, eq=[], comp=None),
    'drums':   dict(hp=30,  send=0.10, eq=[('peak', 400, -2.0, 1.0)], comp=(-16, 3.0)),
}
MELODY_PRESENCE = ('peak', 3000, 1.5, 0.8)


def _lufs(meter, x):
    try:
        v = meter.integrated_loudness(x.astype(np.float64))
        return v if np.isfinite(v) else -70.0
    except Exception:
        return -70.0


def make_ir(sr, size, seed=3):
    """部屋の響き（インパルス応答）を数値で作る：初期反射数本 ＋ 高域ほど早く消える残響、左右は別の雑音。"""
    rt60 = 0.9 + 2.6 * size
    n = int(rt60 * 1.2 * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)
    out = []
    for ch in range(2):
        x = rng.normal(0, 1, n)
        lo = signal.lfilter(*signal.butter(2, 1500 / (sr / 2)), x) * np.exp(-6.9 * t / rt60)
        hi = signal.lfilter(*signal.butter(2, 1500 / (sr / 2), 'high'), x) * np.exp(-6.9 * t / (rt60 * 0.45))
        y = lo + 0.6 * hi
        y *= np.minimum(1, t / 0.02)      # 残響は少し遅れて立ち上がる
        pre = int((0.012 + 0.02 * size) * sr)
        y = np.concatenate([np.zeros(pre), y])[:n]
        for k in range(7):                 # 初期反射
            d = int(rng.uniform(0.004, 0.035 + 0.03 * size) * sr)
            y[d] += rng.uniform(0.2, 0.5) * rng.choice([-1, 1])
        out.append(y)
    ir = np.stack(out, 1)
    return (ir / np.sqrt((ir ** 2).sum(0, keepdims=True))).astype(np.float32)


def _chain(track, is_melody):
    fam = FAMILY.get(track.family, FAMILY['keys'])
    hp = fam['hp']
    if track.inst in ('cello', 'contrabass', 'tuba', 'trombone', 'bassoon', 'timpani'):
        hp = 30
    fx = [HighpassFilter(cutoff_frequency_hz=hp)]
    for kind, f, g, q in fam['eq'] + ([MELODY_PRESENCE] if is_melody else []):
        if kind == 'low':
            fx.append(LowShelfFilter(cutoff_frequency_hz=f, gain_db=g, q=q))
        else:
            fx.append(PeakFilter(cutoff_frequency_hz=f, gain_db=g, q=q))
    if fam['comp']:
        th, ratio = fam['comp']
        fx.append(Compressor(threshold_db=th, ratio=ratio, attack_ms=12 if track.family != 'drums' else 4, release_ms=150))
    return Pedalboard(fx)


def _duck_env(kick, sr, depth_db=2.5):
    env = np.abs(kick).max(1)
    env = signal.lfilter([1 - math.exp(-1 / (0.005 * sr))], [1, -math.exp(-1 / (0.005 * sr))], env)
    rel = math.exp(-1 / (0.14 * sr))
    env = signal.lfilter([1 - rel], [1, -rel], env)
    env = env / (env.max() + 1e-9)
    return 10 ** (-depth_db * env / 20)


def true_peak_db(x, sr):
    up = signal.resample_poly(x, 4, 1, axis=0)
    return 20 * np.log10(np.abs(up).max() + 1e-12)


def mix(stems, score, sr, log=print):
    spec = score.spec
    style = spec['style']
    meter = pyln.Meter(sr)
    n = len(next(iter(stems.values())))
    rv = style.get('reverb', {})
    ir = make_ir(sr, rv.get('size', 0.6))
    wet_scale = rv.get('wet', 0.2) / 0.2
    tracks = score.tracks
    processed = {}
    levels = {}
    melody_ids = {tid for tid, tr in tracks.items() if tr.fn == 'melody'}
    for tid, a in stems.items():
        tr = tracks[tid]
        x = _chain(tr, tid in melody_ids)(a.astype(np.float32), sr)
        # 音量の土台：-20 LUFS にそろえてから、その役の相対音量（styles の level）をかける
        l = _lufs(meter, x)
        g = 10 ** ((-20 - l + tr.level) / 20) if l > -60 else 1.0
        g = min(g, 10 ** (24 / 20))
        x = x * g
        levels[tid] = dict(lufs_raw=round(l, 1), gain_db=round(20 * math.log10(g + 1e-12), 1))
        processed[tid] = x
    # ベースと伸ばしの楽器を、キックに合わせて少し下げる（ポップ・ローファイ）
    kick_src = processed.get('drums')
    if kick_src is not None and spec['style_name'] in ('bright_pop', 'lofi'):
        k = _filt_low(kick_src, sr)
        dk = _duck_env(k, sr)[:, None]
        for tid, tr in tracks.items():
            if tr.fn in ('bass', 'pad'):
                processed[tid] = processed[tid] * dk
    # 残響：楽器ごとに送り、畳み込む（stem に残響も含める）
    out_stems = {}
    for tid, x in processed.items():
        tr = tracks[tid]
        send = FAMILY.get(tr.family, FAMILY['keys'])['send'] * wet_scale
        if tr.fn == 'cue':
            send *= 1.2
        if send > 0.01:
            mono = x.mean(1)
            wet = np.stack([signal.fftconvolve(mono, ir[:, 0])[:n], signal.fftconvolve(mono, ir[:, 1])[:n]], 1)
            x = x + wet * send
        # 和音を伸ばす楽器は左右を少し広げる（中央と左右の差を強める）
        if tr.fn in ('pad', 'arp') and tr.family in ('bowed', 'choir', 'pluck', 'mallet'):
            mid, side = (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2
            x = np.stack([mid + side * 1.35, mid - side * 1.35], 1)
        out_stems[tid] = x.astype(np.float32)
    # 効果音
    fx = sfx.render(score, n, sr)
    if np.abs(fx).max() > 0:
        fx = fx * 10 ** ((-20 - _lufs(meter, fx) - 6) / 20) if _lufs(meter, fx) > -60 else fx
        out_stems['sfx'] = fx
    if style.get('master', {}).get('lofi'):
        out_stems['vinyl'] = sfx.vinyl(sr, n) * 0.8
    bus = sum(out_stems.values())
    master, stats = master_chain(bus, spec, sr, meter, log)
    stats['levels'] = levels
    return master, out_stems, stats


def _filt_low(x, sr):
    b, a = signal.butter(2, 150 / (sr / 2))
    return signal.lfilter(b, a, x, axis=0)


def master_chain(bus, spec, sr, meter, log):
    st = spec['style'].get('master', {})
    x = bus.astype(np.float32)
    # 低域（120Hz 以下）は真ん中に集める
    b, a = signal.butter(2, 120 / (sr / 2))
    low = signal.lfilter(b, a, x, axis=0)
    x = x - low + low.mean(1, keepdims=True)
    x = x.astype(np.float32)
    fx = [Compressor(threshold_db=-18, ratio=st.get('glue', 2.0), attack_ms=30, release_ms=250),
          HighShelfFilter(cutoff_frequency_hz=9000, gain_db=st.get('air', 1.0)),
          LowShelfFilter(cutoff_frequency_hz=90, gain_db=0.5)]
    if st.get('lofi'):
        fx.append(LowpassFilter(cutoff_frequency_hz=9500))
    pre_l = _lufs(meter, x)
    x = x * 10 ** ((-18 - pre_l) / 20) if pre_l > -60 else x
    x = Pedalboard(fx)(x, sr)
    if st.get('lofi'):
        x = np.tanh(x * 1.6) / 1.6
        x = _wow(x, sr)
    # ナレーションの所は音楽を下げる
    for nr in spec['narration']:
        g = np.ones(len(x))
        a_, b_ = int(nr['from'] * sr), int(nr['to'] * sr)
        ramp = int(0.25 * sr)
        lvl = 10 ** (nr['duck_db'] / 20)
        g[a_:b_] = lvl
        r0 = np.linspace(1, lvl, ramp)
        g[max(0, a_ - ramp):a_] = r0[-(a_ - max(0, a_ - ramp)):] if a_ > 0 else g[0:0]
        g[b_:b_ + ramp] = np.linspace(lvl, 1, ramp)[:len(g[b_:b_ + ramp])]
        x = x * g[:, None]
    # ラウドネスを目標に、真のピークを天井の下に
    target, ceiling = spec['mix']['loudness'], spec['mix']['true_peak']
    gain_db = target - _lufs(meter, x)
    for it in range(6):
        y = Pedalboard([Limiter(threshold_db=ceiling - 0.6, release_ms=120)])((x * 10 ** (gain_db / 20)).astype(np.float32), sr)
        tp = true_peak_db(y, sr)
        if tp > ceiling:
            y = y * 10 ** ((ceiling - tp - 0.05) / 20)
        l = _lufs(meter, y)
        if abs(l - target) < 0.3:
            break
        gain_db += target - l
    # 最後の1サンプルまで天井の下に（リミッターの取りこぼし対策）
    tp = true_peak_db(y, sr)
    if tp > ceiling:
        y = y * 10 ** ((ceiling - tp - 0.05) / 20)
    # 終わりのフェード（残響の尻尾を自然に切る）
    fade = int(0.6 * sr)
    y[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 2
    stats = dict(loudness=round(float(_lufs(meter, y)), 2), true_peak=round(float(true_peak_db(y, sr)), 2), target=target, ceiling=ceiling)
    log(f'ミックス：ラウドネス {stats["loudness"]} LUFS（目標 {target}）、真のピーク {stats["true_peak"]} dBTP（天井 {ceiling}）')
    return y.astype(np.float32), stats


def _wow(x, sr):
    """テープの揺れ：ゆっくり伸び縮みする遅延。"""
    n = len(x)
    t = np.arange(n) / sr
    d = 0.0018 * (1 + np.sin(2 * math.pi * 0.55 * t)) + 0.0004 * np.sin(2 * math.pi * 3.1 * t)
    idx = np.arange(n) - d * sr
    out = np.stack([np.interp(idx, np.arange(n), x[:, c]) for c in range(2)], 1)
    return out.astype(np.float32)
