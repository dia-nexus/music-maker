"""SoundFont が無いときの代わりの音。サンプルを使わず、楽器の鳴り方の仕組みを真似て合成する。

電子音っぽさ（ぴこぴこ）の原因は、①倍音が時間で変わらない ②音程が揺れない ③1音が1つの発音体だけ
④胴や部屋の響きが無い、の4つ。ここではそれぞれに手当てをする：
  ピアノ：倍音ごとに減衰の速さが違い、高い倍音ほど早く消える。弦の不協和（インハーモニシティ）と、
          2本の弦のわずかなずれによるうなり、ハンマーの打撃音。
  撥弦（ギター・ハープ・ピチカート）：Karplus-Strong（雑音を遅延で帰還させて弦の振動を作る）＋胴の共鳴。
  擦弦（弦楽）：帯域を制限した鋸歯を、合奏の人数ぶん少しずつ音程と揺れをずらして重ね、胴の共鳴で濾す。
  金管：強く吹くほど明るくなる（暗い音と明るい音を音量の包絡で混ぜる）。立ち上がりで少し下から入る。
  木管・合唱：息の雑音と、母音の共鳴（フォルマント）。
  打楽器：音程の下がるサイン、帯域を絞った雑音、金属の不協和な部分音。
"""
import math

import numpy as np
from scipy import signal

from . import styles

RNG = np.random.default_rng(7)


def mtof(p):
    return 440.0 * 2 ** ((p - 69) / 12)


def _env_adsr(n, sr, a, d, s, r, hold_n):
    """立ち上がり a 秒、減衰 d 秒、持続 s（0〜1）、余韻 r 秒。hold_n は鍵を離すまでのサンプル数。"""
    env = np.zeros(n, dtype=np.float32)
    an, dn, rn = max(1, int(a * sr)), max(1, int(d * sr)), max(1, int(r * sr))
    hold_n = min(hold_n, n)
    t = np.arange(n)
    env[:an] = np.linspace(0, 1, an)[:n] if an <= n else np.linspace(0, 1, an)[:n]
    if an < n:
        seg = np.arange(an, min(an + dn, n)) - an
        env[an:an + len(seg)] = 1 - (1 - s) * (seg / dn)
        env[an + dn:] = s
    level = env[hold_n - 1] if hold_n > 0 else 0
    if hold_n < n:
        rel = np.arange(n - hold_n)
        env[hold_n:] = level * np.exp(-5.0 * rel / rn)
    return env


def _polyblep_saw(phase_cycles, dt):
    """位相（周期単位の累積）から、折り返しを抑えた鋸歯。"""
    t = phase_cycles % 1.0
    y = 2 * t - 1
    dt = np.clip(dt, 1e-6, 0.5)
    m = t < dt
    x = t[m] / dt[m]
    y[m] -= x + x - x * x - 1
    m = t > 1 - dt
    x = (t[m] - 1) / dt[m]
    y[m] -= x * x + x + x + 1
    return y


def _polyblep_square(phase_cycles, dt, duty=0.5):
    return 0.5 * (_polyblep_saw(phase_cycles, dt) - _polyblep_saw(phase_cycles + duty, dt))


def _vibrato_phase(f, n, sr, depth=0.004, rate=5.5, delay=0.25, jitter=0.0015):
    t = np.arange(n) / sr
    ramp = np.clip((t - delay) / 0.4, 0, 1)
    r = rate * (1 + RNG.uniform(-0.06, 0.06))
    drift = np.cumsum(RNG.normal(0, 1, n // 256 + 2))
    drift = np.interp(np.arange(n), np.arange(len(drift)) * 256, drift) * jitter / max(1, math.sqrt(n / 256))
    inst = f * (1 + depth * ramp * np.sin(2 * math.pi * r * t + RNG.uniform(0, 6.28)) + drift)
    return np.cumsum(inst) / sr, inst / sr


def _biquad(kind, f0, sr, q=0.707, gain_db=0.0):
    w0 = 2 * math.pi * min(f0, sr * 0.45) / sr
    alpha = math.sin(w0) / (2 * q)
    A = 10 ** (gain_db / 40)
    c = math.cos(w0)
    if kind == 'lp':
        b = [(1 - c) / 2, 1 - c, (1 - c) / 2]; a = [1 + alpha, -2 * c, 1 - alpha]
    elif kind == 'hp':
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]; a = [1 + alpha, -2 * c, 1 - alpha]
    elif kind == 'bp':
        b = [alpha, 0, -alpha]; a = [1 + alpha, -2 * c, 1 - alpha]
    else:  # peak
        b = [1 + alpha * A, -2 * c, 1 - alpha * A]; a = [1 + alpha / A, -2 * c, 1 - alpha / A]
    return np.array(b) / a[0], np.array(a) / a[0]


def _filt(x, kind, f0, sr, q=0.707, gain_db=0.0):
    b, a = _biquad(kind, f0, sr, q, gain_db)
    return signal.lfilter(b, a, x).astype(np.float32)


# ── 楽器ごとの1音 ────────────────────────────────────

def v_keys(p, vel, hold, sr, tail=1.6):
    f = mtof(p)
    dur = hold + tail
    n = int(dur * sr)
    t = np.arange(n) / sr
    B = 0.00015 * (f / 261.6) ** 1.2
    bright = 0.35 + 0.65 * vel
    out = np.zeros(n, dtype=np.float64)
    for k in range(1, 26):
        fk = k * f * math.sqrt(1 + B * k * k)
        if fk > 14000:
            break
        amp = (1 / k ** 1.15) * math.exp(-(k - 1) * (1.15 - bright) * 0.45)
        if k % 7 == 0:
            amp *= 0.3   # ハンマーの当たる位置で消える倍音
        rate = (0.35 + 0.09 * k) * (f / 261.6) ** 0.6
        env = 0.72 * np.exp(-rate * 2.2 * t) + 0.28 * np.exp(-rate * 0.35 * t)
        det = 1 + RNG.uniform(-0.0006, 0.0006)
        ph = RNG.uniform(0, 6.28)
        out += amp * env * (np.sin(2 * math.pi * fk * t + ph) + 0.8 * np.sin(2 * math.pi * fk * det * t + ph + 0.5))
    # 鍵を離したらダンパーで止める
    hn = int(hold * sr)
    if hn < n:
        out[hn:] *= np.exp(-np.arange(n - hn) / (0.09 * sr))
    hammer = _filt(RNG.normal(0, 1, min(n, int(0.012 * sr))), 'bp', min(4000, f * 6), sr, 0.8) * 0.25 * vel
    out[:len(hammer)] += hammer * np.linspace(1, 0, len(hammer))
    a = np.minimum(1, t / 0.002)
    return (out * a * 0.16).astype(np.float32)


def v_epiano(p, vel, hold, sr):
    f = mtof(p)
    n = int((hold + 1.2) * sr)
    t = np.arange(n) / sr
    idx = (0.6 + 2.2 * vel) * np.exp(-t * 2.5) + 0.15
    mod = np.sin(2 * math.pi * f * t)
    car = np.sin(2 * math.pi * f * t + idx * mod)
    bell = np.sin(2 * math.pi * f * 7.0 * t) * np.exp(-t * 18) * 0.25 * vel
    env = np.exp(-t * (0.9 + f / 900))
    hn = int(hold * sr)
    if hn < n:
        env[hn:] *= np.exp(-np.arange(n - hn) / (0.08 * sr))
    return ((car + bell) * env * 0.3 * np.minimum(1, t / 0.003)).astype(np.float32)


MALLET_MODES = {
    'glock': ([1, 2.76, 5.40, 8.93], [1, .35, .15, .06], 1.4),
    'celesta': ([1, 2.0, 3.0, 4.1], [1, .25, .08, .03], 1.8),
    'vibes': ([1, 3.98, 9.9], [1, .2, .05], 2.4),
    'marimba': ([1, 3.93, 9.2], [1, .12, .03], 0.55),
}


def v_mallet(p, vel, hold, sr, kind='glock'):
    f = mtof(p)
    ratios, amps, t60 = MALLET_MODES.get(kind, MALLET_MODES['glock'])
    n = int((min(hold, t60) + t60) * sr)
    t = np.arange(n) / sr
    out = np.zeros(n)
    for r, a in zip(ratios, amps):
        if f * r > 16000:
            continue
        out += a * np.sin(2 * math.pi * f * r * t) * np.exp(-t * (6.9 / t60) * (1 + 0.8 * (r - 1)))
    if kind == 'vibes':
        out *= 1 - 0.25 * (0.5 + 0.5 * np.sin(2 * math.pi * 5.2 * t))
    click = _filt(RNG.normal(0, 1, int(0.004 * sr)), 'hp', 3000, sr) * 0.15
    out[:len(click)] += click
    return (out * 0.22 * (0.4 + 0.6 * vel)).astype(np.float32)


def v_pluck(p, vel, hold, sr, kind='guitar'):
    """Karplus-Strong：雑音の初期値を、平均化しながら遅延で帰還させる（弦の振動）。"""
    f = mtof(p)
    ring = {'guitar': 2.2, 'harp': 3.0, 'pizz': 0.45, 'bass': 1.6}.get(kind, 2.0)
    n = int((min(hold + 0.25, ring) + 0.3) * sr) if kind != 'harp' else int((ring + 0.2) * sr)
    N = max(2, int(round(sr / f - 0.5)))
    burst = RNG.uniform(-1, 1, N)
    bright = 0.25 + 0.7 * vel
    burst = _filt(burst, 'lp', 800 + 7000 * bright * (1 if kind != 'bass' else 0.4), sr)
    x = np.zeros(n)
    x[:N] = burst
    g = 0.5 * (10 ** (-3 / (ring * f)))   # 1周ごとの減衰（ring 秒で -60dB）
    a = np.zeros(N + 2)
    a[0] = 1
    a[N] -= g
    a[N + 1] -= g
    y = signal.lfilter([1.0], a, x)
    hn = int((hold + 0.08) * sr) if kind in ('guitar', 'bass') else n
    if hn < n:
        y[hn:] *= np.exp(-np.arange(n - hn) / (0.06 * sr))
    if kind == 'bass':
        t = np.arange(n) / sr
        y = y * 0.7 + 0.5 * np.sin(2 * math.pi * f * t) * np.exp(-t * 1.2) * (y != 0)
    return (y * 0.5).astype(np.float32)


def v_bowed(p, vel, hold, sr, voices=3, attack=None):
    f = mtof(p)
    rel = 0.35
    n = int((hold + rel) * sr)
    out = np.zeros(n, dtype=np.float64)
    for v in range(voices):
        det = f * 2 ** (RNG.uniform(-9, 9) / 1200) if voices > 1 else f
        ph, dt = _vibrato_phase(det, n, sr, depth=0.0045 if voices == 1 else 0.003, delay=0.2 + RNG.uniform(0, .2))
        ph += RNG.uniform(0, 1)
        out += _polyblep_saw(ph, dt)
    att = attack if attack is not None else (0.22 - 0.15 * vel)
    env = _env_adsr(n, sr, att, 0.2, 0.85, rel, int(hold * sr))
    bow = _filt(RNG.normal(0, 1, n), 'bp', min(f * 3, 6000), sr, 1.5) * 0.04
    return ((out / voices * 0.22 + bow) * env).astype(np.float32)


def v_brass(p, vel, hold, sr, dark=False):
    f = mtof(p)
    n = int((hold + 0.25) * sr)
    t = np.arange(n) / sr
    scoop = 1 - 0.012 * np.exp(-t / 0.04)
    ph, dt = _vibrato_phase(f, n, sr, depth=0.002, delay=0.4)
    ph = np.cumsum(f * scoop * (dt * sr / f)) / sr
    saw = _polyblep_saw(ph, dt)
    env = _env_adsr(n, sr, 0.04 + 0.05 * (1 - vel), 0.15, 0.8, 0.18, int(hold * sr))
    dark_sig = _filt(saw, 'lp', 700 if dark else 900, sr)
    bright_sig = _filt(saw, 'lp', 2600 if dark else 4200, sr)
    w = np.clip(env ** 1.5 * (0.4 + 0.6 * vel), 0, 1)
    return ((dark_sig * (1 - w) + bright_sig * w) * env * 0.35).astype(np.float32)


def v_reed(p, vel, hold, sr, kind='clarinet'):
    f = mtof(p)
    n = int((hold + 0.15) * sr)
    ph, dt = _vibrato_phase(f, n, sr, depth=0.003 if kind != 'clarinet' else 0.0012, delay=0.3)
    if kind == 'clarinet':
        x = _polyblep_square(ph, dt)
        x = _filt(x, 'lp', 2200, sr)
    else:
        x = _polyblep_square(ph, dt, duty=0.22)
        x = _filt(x, 'peak', 1150, sr, 2, 9) if kind == 'oboe' else _filt(x, 'peak', 500, sr, 1.5, 6)
        x = _filt(x, 'lp', 3500, sr)
    breath = _filt(RNG.normal(0, 1, n), 'bp', min(f * 2.5, 5000), sr, 1.2) * 0.05
    env = _env_adsr(n, sr, 0.035, 0.1, 0.9, 0.1, int(hold * sr))
    return ((x * 0.4 + breath) * env).astype(np.float32)


def v_flute(p, vel, hold, sr):
    f = mtof(p)
    n = int((hold + 0.2) * sr)
    ph, dt = _vibrato_phase(f, n, sr, depth=0.004, delay=0.25)
    x = np.sin(2 * math.pi * ph) + 0.18 * np.sin(4 * math.pi * ph) + 0.06 * np.sin(6 * math.pi * ph)
    breath = _filt(RNG.normal(0, 1, n), 'bp', min(f * 1.5, 6000), sr, 0.9) * 0.12
    env = _env_adsr(n, sr, 0.06, 0.1, 0.9, 0.12, int(hold * sr))
    chiff = np.zeros(n)
    cn = min(n, int(0.04 * sr))
    chiff[:cn] = _filt(RNG.normal(0, 1, cn), 'hp', 2500, sr) * np.linspace(0.3, 0, cn)
    return ((x * 0.3 + breath) * env + chiff * 0.2).astype(np.float32)


def v_choir(p, vel, hold, sr):
    return v_bowed(p, vel, hold, sr, voices=4, attack=0.25)  # 母音は track 全体のフォルマントで付ける


def v_timpani(p, vel, hold, sr):
    f = mtof(p)
    n = int(2.2 * sr)
    t = np.arange(n) / sr
    bend = 1 + 0.03 * np.exp(-t / 0.05)
    out = np.zeros(n)
    for r, a, d in [(1, 1, 1.2), (1.5, .5, 1.6), (1.98, .3, 2.2), (2.44, .15, 3)]:
        out += a * np.sin(2 * math.pi * np.cumsum(f * r * bend) / sr) * np.exp(-t * d)
    thump = _filt(RNG.normal(0, 1, int(0.03 * sr)), 'lp', 300, sr)
    out[:len(thump)] += thump * 0.6 * np.linspace(1, 0, len(thump))
    return (out * 0.45 * (0.3 + 0.7 * vel)).astype(np.float32)


def _noise(n, sr, kind, f0, q=0.7):
    return _filt(RNG.normal(0, 1, n), kind, f0, sr, q)


def _metal(n, sr, base, decay):
    t = np.arange(n) / sr
    out = np.zeros(n)
    for r in [1, 1.342, 1.783, 2.135, 2.567, 3.1, 3.46, 4.2]:
        out += np.sin(2 * math.pi * base * r * t + RNG.uniform(0, 6)) * np.exp(-t / (decay * (1 - 0.06 * r)))
    return out


def v_drum(p, vel, hold, sr, brush=False):
    D = styles.DRUM
    g = 0.25 + 0.75 * vel
    if p in (D['kick'], D['bass_drum']):
        n = int(0.6 * sr if p == D['kick'] else 1.6 * sr)
        t = np.arange(n) / sr
        f = 45 + 75 * np.exp(-t / 0.035) if p == D['kick'] else 38 + 30 * np.exp(-t / 0.08)
        y = np.sin(2 * math.pi * np.cumsum(f) / sr) * np.exp(-t / (0.28 if p == D['kick'] else 0.7))
        y[:int(0.003 * sr)] += _noise(int(0.003 * sr), sr, 'hp', 2000) * 0.4
        return (y * g * 0.9).astype(np.float32)
    if p in (D['snare'], D['snare2'], D['clap'], D['side']):
        n = int(0.35 * sr)
        t = np.arange(n) / sr
        if brush:
            y = _noise(n, sr, 'bp', 3500, 0.5) * np.exp(-t / 0.09) * np.minimum(1, t / 0.012) * 0.4
            return (y * g).astype(np.float32)
        if p == D['side']:
            y = _noise(int(0.05 * sr), sr, 'bp', 1800, 3) * np.exp(-np.arange(int(0.05 * sr)) / sr / 0.01) * 1.5
            return (y * g).astype(np.float32)
        if p == D['clap']:
            y = np.zeros(n)
            for k, o in enumerate([0, 0.011, 0.023]):
                s = int(o * sr)
                m = n - s
                y[s:] += _noise(m, sr, 'bp', 1300, 1.2) * np.exp(-np.arange(m) / sr / (0.012 if k < 2 else 0.12))
            return (y * g * 0.7).astype(np.float32)
        tone = np.sin(2 * math.pi * 185 * t) * np.exp(-t / 0.05) + 0.5 * np.sin(2 * math.pi * 330 * t) * np.exp(-t / 0.03)
        nz = _noise(n, sr, 'bp', 4500, 0.6) * np.exp(-t / 0.13)
        return ((tone * 0.6 + nz * 0.8) * g * 0.7).astype(np.float32)
    if p in (D['hat'], D['hat_pedal'], D['hat_open'], D['shaker'], D['cabasa'], D['tamb']):
        dec = {D['hat']: 0.045, D['hat_pedal']: 0.03, D['hat_open']: 0.35, D['shaker']: 0.06, D['cabasa']: 0.06, D['tamb']: 0.2}[p]
        n = int((dec * 4 + 0.02) * sr)
        t = np.arange(n) / sr
        y = _noise(n, sr, 'hp', 7000 if p != D['shaker'] else 5000) * np.exp(-t / dec)
        if p in (D['hat'], D['hat_open'], D['hat_pedal']):
            y += 0.3 * _filt(_metal(n, sr, 3200, dec), 'hp', 6000, sr)
        if p == D['shaker']:
            y *= np.minimum(1, t / 0.015)
        return (y * g * 0.35).astype(np.float32)
    if p in (D['crash'], D['crash2'], D['china'], D['splash'], D['ride'], D['ride2'], D['ride_bell']):
        ride = p in (D['ride'], D['ride2'], D['ride_bell'])
        dec = 0.9 if ride else 1.6
        n = int(dec * 3 * sr)
        t = np.arange(n) / sr
        y = _filt(_metal(n, sr, 420 if ride else 340, dec), 'hp', 3000, sr) * (0.5 if ride else 0.35)
        y += _noise(n, sr, 'hp', 5000) * np.exp(-t / (dec * (0.3 if ride else 0.6))) * (0.3 if ride else 0.8)
        return (y * g * 0.3).astype(np.float32)
    if p in (D['tom_lo'], D['tom_mid'], D['tom_hi']):
        base = {D['tom_lo']: 90, D['tom_mid']: 130, D['tom_hi']: 180}[p]
        n = int(0.6 * sr)
        t = np.arange(n) / sr
        f = base * (1 + 0.4 * np.exp(-t / 0.04))
        y = np.sin(2 * math.pi * np.cumsum(f) / sr) * np.exp(-t / 0.25) + _noise(n, sr, 'bp', 1500, 0.8) * np.exp(-t / 0.03) * 0.3
        return (y * g * 0.7).astype(np.float32)
    if p in (D['woodblock'],):
        n = int(0.15 * sr)
        t = np.arange(n) / sr
        y = (np.sin(2 * math.pi * 1100 * t) + 0.4 * np.sin(2 * math.pi * 2700 * t)) * np.exp(-t / 0.025)
        return (y * g * 0.4).astype(np.float32)
    if p in (D['triangle'], D['triangle_mute']):
        dec = 1.5 if p == D['triangle'] else 0.1
        n = int(dec * 2.5 * sr)
        t = np.arange(n) / sr
        y = sum(np.sin(2 * math.pi * 4200 * r * t) * a for r, a in [(1, 1), (2.03, .5), (3.1, .3), (4.4, .2)]) * np.exp(-t / dec)
        return (y * g * 0.12).astype(np.float32)
    # そのほか（コンガ・ボンゴなど）：短い太鼓
    n = int(0.3 * sr)
    t = np.arange(n) / sr
    y = np.sin(2 * math.pi * (200 + 4 * (p - 60)) * t) * np.exp(-t / 0.08)
    return (y * g * 0.5).astype(np.float32)


# @couple:instrument-families 系統を足したら styles.INSTRUMENTS と mix.FAMILY と impact.rules.cjs の照合も
VOICES = {
    'keys': lambda p, v, h, sr, inst: v_keys(p, v, h, sr),
    'epiano': lambda p, v, h, sr, inst: v_epiano(p, v, h, sr),
    'mallet': lambda p, v, h, sr, inst: v_mallet(p, v, h, sr, inst),
    'pluck': lambda p, v, h, sr, inst: v_pluck(p, v, h, sr, {'harp': 'harp', 'pizz': 'pizz'}.get(inst, 'guitar')),
    'bass': lambda p, v, h, sr, inst: v_pluck(p, v, h, sr, 'bass'),
    'bowed': lambda p, v, h, sr, inst: v_bowed(p, v, h, sr, voices=1 if inst in ('violin', 'viola', 'cello') else (2 if inst == 'contrabass' else 4)),
    'brass': lambda p, v, h, sr, inst: v_brass(p, v, h, sr, dark=inst in ('horn', 'tuba', 'trombone')),
    'reed': lambda p, v, h, sr, inst: v_reed(p, v, h, sr, 'clarinet' if inst == 'clarinet' else ('oboe' if inst == 'oboe' else 'bassoon')),
    'flute': lambda p, v, h, sr, inst: v_flute(p, v, h, sr),
    'choir': lambda p, v, h, sr, inst: v_choir(p, v, h, sr),
    'timpani': lambda p, v, h, sr, inst: v_timpani(p, v, h, sr),
    'drums': lambda p, v, h, sr, inst: v_drum(p, v, h, sr, brush=inst == 'brush_drums'),
}

VOWELS = {'choir': [(800, 1.0, 6), (1150, 0.6, 7), (2900, 0.25, 9)], 'oohs': [(350, 1.0, 6), (650, 0.4, 7), (2400, 0.12, 9)]}


def _post(track, y, sr):
    fam = track.family
    if fam == 'bowed':
        y = _filt(y, 'peak', 280, sr, 1.2, 4)
        y = _filt(y, 'peak', 1100, sr, 1.5, 3)
        y = _filt(y, 'peak', 2600, sr, 2, 2.5)
        y = _filt(y, 'lp', 7500, sr)
        y = _filt(y, 'hp', 60, sr)
    elif fam == 'choir':
        z = np.zeros_like(y)
        for f0, g, q in VOWELS.get(track.inst, VOWELS['choir']):
            z += _filt(y, 'bp', f0, sr, q) * g * 3
        y = z
    elif fam == 'pluck':
        y = _filt(y, 'peak', 110, sr, 1.5, 4) if track.inst in ('steel_guitar', 'nylon_guitar') else y
        y = _filt(y, 'peak', 220, sr, 1.2, 2)
    return y


def render_track(track, spec, sr):
    tempo = spec['tempo']
    end = max((n.end for n in track.notes), default=0)
    total = tempo.sec(end) + 4
    n = int(total * sr)
    buf = np.zeros(n, dtype=np.float64)
    # ペダル：踏んでいる間は鍵を離しても止めない
    pedal = sorted((tempo.sec(b), v) for b, c, v in track.cc if c == 64)

    def pedal_release(tsec):
        down = False
        for ts, v in pedal:
            if ts > tsec:
                if down and v < 64:
                    return ts
                down = v >= 64 if ts <= tsec else down
            else:
                down = v >= 64
        return tsec if not down else tsec + 1.5

    fn = VOICES.get(track.family, VOICES['keys'])
    for note in track.notes:
        s = tempo.sec(note.start)
        e = tempo.sec(note.end)
        hold = max(0.03, e - s)
        if track.family in ('keys', 'epiano') and pedal:
            hold = max(hold, pedal_release(e) - s)
        y = fn(note.pitch, note.vel / 127.0, hold, sr, track.inst).astype(np.float64)
        i = int(s * sr)
        j = min(n, i + len(y))
        buf[i:j] += y[:j - i] * (note.vel / 127.0) ** 0.6
    # 表情（CC11）
    expr = sorted((tempo.sec(b), v) for b, c, v in track.cc if c == 11)
    if expr:
        ts = np.array([e[0] for e in expr])
        vs = np.array([e[1] for e in expr]) / 127.0
        curve = np.interp(np.arange(n) / sr, ts, vs)
        buf *= curve ** 1.6
    y = _post(track, buf.astype(np.float32), sr)
    # 定位（等パワー）＋ 合奏や和音楽器はわずかに左右をずらして広げる
    pan = (track.pan + 1) / 2 * (math.pi / 2)
    L, R = y * math.cos(pan), y * math.sin(pan)
    if track.family in ('bowed', 'choir', 'pluck') and track.inst in ('strings', 'slow_strings', 'choir', 'oohs', 'harp'):
        d = int(0.011 * sr)
        R = np.concatenate([np.zeros(d, dtype=np.float32), R[:-d]]) * 0.85 + R * 0.15
    return np.stack([L, R], axis=1).astype(np.float32) * 0.9
