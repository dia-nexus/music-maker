"""確かめ（耳の代わり）：出来上がりを数値と図にして、Claude が読んで直せるようにする。

出すもの：
  区切りごとの音量・密度・明るさ・広がり・楽器、と隣の区切りとの違い
  楽器ごとの音域（最低・最高・中央）と、楽器の無理のない音域からのはみ出し
  和音の判定（設計した和音と、MIDI・実際の音から推定した和音の一致率）
  同じ小節の繰り返し（MIDI の完全一致と、音の似かよい）
  クリップと真のピーク
  機械っぽさの目安（強さのばらつき、拍からのずれ、音色の系統の数、左右の広がり、残響）
  きっかけが効いているか（その時刻に音の立ち上がりがあるか）
  図：スペクトログラム、ピアノロール、音量の推移、小節どうしの似かよい
"""
import json
import math
import os

import numpy as np
import pyloudnorm as pyln
from scipy import signal

from . import styles
from .theory import PC_NAME_SHARP, chord_from_pcs

FONT_CANDIDATES = ['Noto Sans CJK JP', 'IPAexGothic', 'IPAGothic', 'TakaoGothic', 'WenQuanYi Zen Hei', 'DejaVu Sans']


def _plt():
    import logging
    import matplotlib
    matplotlib.use('Agg')
    logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    have = {f.name for f in font_manager.fontManager.ttflist}
    fams = [f for f in FONT_CANDIDATES if f in have] or ['DejaVu Sans']
    plt.rcParams['font.family'] = fams
    plt.rcParams['axes.unicode_minus'] = False
    return plt


def pname(p):
    return f'{PC_NAME_SHARP[p % 12]}{p // 12 - 1}'


def _db(x):
    return 20 * math.log10(max(1e-9, x))


def _lufs(meter, x):
    if len(x) < meter.rate * 0.45:
        return -70.0
    try:
        v = meter.integrated_loudness(x.astype(np.float64))
        return round(v, 1) if np.isfinite(v) else -70.0
    except Exception:
        return -70.0


def _stft(mono, sr, nfft=4096, hop=1024):
    f, t, Z = signal.stft(mono, sr, nperseg=nfft, noverlap=nfft - hop, boundary=None, padded=False)
    return f, t, np.abs(Z)


def _chroma(f, S):
    pcs = np.zeros((12, S.shape[1]))
    ok = (f > 55) & (f < 4200)
    midi = 69 + 12 * np.log2(f[ok] / 440.0)
    pc = np.mod(np.round(midi), 12).astype(int)
    w = 1 / (1 + np.maximum(0, midi - 72) / 24)   # 高い倍音を少し弱める
    E = (S[ok] ** 2) * w[:, None]
    for k in range(12):
        pcs[k] = E[pc == k].sum(0)
    return pcs


def _peaks_pc(seg, sr, fmin, fmax, nper):
    """区間の平均スペクトル（長い窓）から山だけを拾い、ピッチクラスごとの重みにする。"""
    if len(seg) < 2048:
        return {}
    nper = min(nper, len(seg))
    ff, P = signal.welch(seg, sr, nperseg=nper)
    amp = np.sqrt(P)
    band = (ff > fmin) & (ff < fmax)
    if not band.any():
        return {}
    idx, _ = signal.find_peaks(amp * band, prominence=amp[band].max() * 0.04)
    w = {}
    for i in idx:
        midi = 69 + 12 * math.log2(ff[i] / 440.0)
        if abs(midi - round(midi)) > 0.35:
            continue
        pc = int(round(midi)) % 12
        w[pc] = w.get(pc, 0) + amp[i] / (1 + max(0, midi - 72) / 12)
    return w


def _audio_chord(mono, sr, a, b):
    seg = mono[int((a + 0.03) * sr):int((b - 0.03) * sr)]
    w = _peaks_pc(seg, sr, 70, 2000, 16384)
    if not w:
        return None
    mx = max(w.values())
    w = {k: v / mx for k, v in w.items() if v / mx > 0.2}
    head = mono[int((a + 0.02) * sr):int((a + max(0.35, (b - a) * 0.4)) * sr)]
    bw = _peaks_pc(head, sr, 30, 200, 32768)
    bass = max(bw, key=bw.get) if bw else None
    ch, _ = chord_from_pcs(w, bass)
    return ch


def analyze(master, stems, score, sr, outdir, info=None, mix_stats=None):
    spec = score.spec
    tempo, meter_ = spec['tempo'], spec['meter']
    os.makedirs(os.path.join(outdir, 'images'), exist_ok=True)
    lm = pyln.Meter(sr)
    mono = master.mean(1)
    rep = {'title': spec['title'], 'style': spec['style_name'], 'engine': info or {}, 'mix': mix_stats or {},
           'warnings': list(spec['warnings'])}
    f, t, S = _stft(mono, sr)

    # ── 区切りごと ──
    secs = []
    for s in spec['sections']:
        a, b = tempo.sec(s.start), tempo.sec(s.end)
        seg = master[int(a * sr):int(b * sr)]
        # 揺れで数十ms 前にずれた次の区切りの頭を数えないよう、境目に 50ms の余裕を持たせる
        tol = 0.05 * tempo.bpm_at(s.start) / 60
        notes = [(tid, n) for tid, tr in score.tracks.items() for n in tr.notes if s.start - tol <= n.start < s.end - tol and tr.fn != 'cue']
        active = sorted({tid for tid, _ in notes})
        frames = (t >= a) & (t < b)
        spec_seg = S[:, frames]
        cen = float((f[:, None] * spec_seg).sum() / (spec_seg.sum() + 1e-9)) if frames.any() else 0
        L, R = seg[:, 0], seg[:, 1]
        corr = float(np.corrcoef(L, R)[0, 1]) if len(L) > 10 and L.std() > 0 and R.std() > 0 else 1.0
        secs.append(dict(name=s.name, role=s.role, start=round(a, 2), end=round(b, 2), energy=s.energy,
                         lufs=_lufs(lm, seg), rms_db=round(_db(np.sqrt((seg ** 2).mean()) + 1e-12), 1),
                         peak_db=round(_db(np.abs(seg).max()), 1), notes_per_sec=round(len(notes) / max(0.1, b - a), 1),
                         centroid_hz=int(cen), width=round(1 - corr, 3), tracks=active, key=s.key.name(),
                         families=sorted({score.tracks[x].family for x in active})))
    flags = []
    for i in range(1, len(secs)):
        p, c = secs[i - 1], secs[i]
        d_l = abs(c['lufs'] - p['lufs'])
        d_n = abs(c['notes_per_sec'] - p['notes_per_sec']) / max(1, p['notes_per_sec'])
        same_set = set(c['tracks']) == set(p['tracks'])
        c['change'] = dict(d_lufs=round(c['lufs'] - p['lufs'], 1), d_density=round(d_n * 100), same_instruments=same_set,
                           added=sorted(set(c['tracks']) - set(p['tracks'])), removed=sorted(set(p['tracks']) - set(c['tracks'])))
        if d_l < 1.0 and d_n < 0.15 and same_set:
            flags.append(f'「{p["name"]}」→「{c["name"]}」の変化が小さい（音量差 {d_l:.1f}dB、密度差 {d_n * 100:.0f}%、楽器は同じ）')
    rep['sections'] = secs

    # ── 帯域のバランス（全体） ──
    P = (S ** 2).sum(1)
    bands = [('低音 〜120Hz', 0, 120), ('中低 120〜500', 120, 500), ('中 500〜2k', 500, 2000), ('中高 2k〜6k', 2000, 6000), ('高 6k〜', 6000, 24000)]
    tot = P[(f > 20)].sum() + 1e-12
    bal = {name: round(float(P[(f >= lo) & (f < hi)].sum() / tot * 100), 1) for name, lo, hi in bands}
    rep['balance'] = bal
    if bal['低音 〜120Hz'] > 55:
        flags.append(f'低音が出すぎ（120Hz 以下が全体の {bal["低音 〜120Hz"]}%。目安 25〜50%）')
    if bal['低音 〜120Hz'] < 15:
        flags.append(f'低音が足りない（120Hz 以下が {bal["低音 〜120Hz"]}%）')
    if bal['中低 120〜500'] > 45:
        flags.append(f'中低域がこもり気味（120〜500Hz が {bal["中低 120〜500"]}%。和音の楽器の 250〜400Hz を削るか、伴奏の音域を散らす）')
    if bal['高 6k〜'] > 12:
        flags.append(f'高音が強すぎ（6kHz 以上が {bal["高 6k〜"]}%、耳に痛い）')

    # ── 音域 ──
    ranges = []
    for tid, tr in score.tracks.items():
        if tr.drum or not tr.notes:
            continue
        ps = [n.pitch for n in tr.notes]
        out_of = [p for p in ps if p < tr.lo or p > tr.hi]
        ranges.append(dict(track=tid, inst=tr.label, fn=tr.fn, lo=pname(min(ps)), hi=pname(max(ps)), median=pname(int(np.median(ps))),
                           n=len(ps), out_of_range=len(out_of), lo_midi=min(ps), hi_midi=max(ps)))
        if out_of:
            flags.append(f'{tr.label}（{tid}）：音域の外の音が {len(out_of)} 個（{pname(min(out_of))}〜{pname(max(out_of))}）')
    rep['ranges'] = ranges

    # ── 和音 ──
    chord_rows, midi_ok, audio_ok, total = [], 0, 0, 0
    chroma = _chroma(f, S)
    for c in score.chords:
        a, b = tempo.sec(c.start), tempo.sec(c.end)
        w, lows = {}, {}
        for tid, tr in score.tracks.items():
            if tr.drum or tr.fn in ('melody', 'double', 'cue'):
                continue
            for n in tr.notes:
                ov = min(n.end, c.end) - max(n.start, c.start)
                if ov > 0:
                    w[n.pitch % 12] = w.get(n.pitch % 12, 0) + ov * (1.5 if n.pitch < 52 else 1)
                    if n.pitch < 55 and n.start <= c.start + 0.3 and n.end > c.start + 0.05:
                        lows[n.pitch] = 1
        mbass = min(lows) % 12 if lows else None   # 和音の頭で鳴っている最低音
        mc, mconf = chord_from_pcs(w, mbass)
        ac = _audio_chord(mono, sr, a, b)
        want = set(c.chord.triad_pcs)
        m_match = mc is not None and set(mc.triad_pcs) == want
        a_match = ac is not None and (set(ac.triad_pcs) == want or (ac.root == c.chord.root))
        midi_ok += m_match
        audio_ok += a_match
        total += 1
        chord_rows.append(dict(time=round(a, 2), section=spec['sections'][c.section].name, planned=c.chord.name(), roman=c.chord.symbol,
                               midi=mc.name() if mc else '-', audio=ac.name() if ac else '-', midi_match=m_match, audio_match=a_match))
    rep['chords'] = dict(rows=chord_rows, midi_match=round(midi_ok / max(1, total), 3), audio_match=round(audio_ok / max(1, total), 3),
                         unique=len({r['planned'] for r in chord_rows}))
    if rep['chords']['audio_match'] < 0.6:
        flags.append(f'実際の音から推定した和音の一致率が低い（{rep["chords"]["audio_match"] * 100:.0f}%）。和音の楽器が小さすぎるか、旋律や打楽器に埋もれている')

    # ── 同じ小節 ──
    from .compose import bar_signature
    bb = meter_.bar_beats
    nb = int(math.ceil(spec['total_beats'] / bb - 1e-6))
    sigs = [bar_signature(score, k * bb, min((k + 1) * bb, spec['total_beats'])) for k in range(nb)]
    first, dup = {}, []
    for k, sg in enumerate(sigs):
        if not sg:
            continue
        if sg in first:
            dup.append((first[sg] + 1, k + 1))
        else:
            first[sg] = k
    consec = [(a, b) for a, b in dup if b == a + 1]
    # 音の特徴（和音の色＋音量＋明るさ）での似かよい
    feats = []
    for k in range(nb):
        a, b = tempo.sec(k * bb), tempo.sec(min((k + 1) * bb, spec['total_beats']))
        fr = (t >= a) & (t < b)
        if not fr.any():
            feats.append(np.zeros(14))
            continue
        ch = chroma[:, fr].mean(1)
        ch = ch / (np.linalg.norm(ch) + 1e-9)
        e = S[:, fr].mean(1)
        cen = (f * e).sum() / (e.sum() + 1e-9) / 4000
        loud = _db(np.sqrt((mono[int(a * sr):int(b * sr)] ** 2).mean()) + 1e-9) / 60 + 1
        feats.append(np.concatenate([ch, [cen, loud]]))
    F = np.array(feats)
    Fn = F / (np.linalg.norm(F, axis=1, keepdims=True) + 1e-9)
    sim = Fn @ Fn.T
    near = [(i + 1, j + 1, round(float(sim[i, j]), 4)) for i in range(nb) for j in range(i + 1, nb) if sim[i, j] > 0.998]
    rep['repetition'] = dict(bars=nb, exact_duplicates=dup, consecutive_duplicates=consec, unique_bars=len(first),
                             near_audio=near[:20], near_audio_count=len(near))
    if consec:
        flags.append(f'連続して同じ小節：{consec}')
    if dup:
        flags.append(f'全楽器の中身が同じ小節の組：{len(dup)} 組（{dup[:6]}）')

    # ── クリップ ──
    clip = int((np.abs(master) >= 0.999).sum())
    tp = mix_stats.get('true_peak') if mix_stats else None
    rep['clipping'] = dict(samples_at_full_scale=clip, true_peak_db=tp, sample_peak_db=round(_db(np.abs(master).max()), 2),
                           stems_over_0db=[k for k, v in stems.items() if np.abs(v).max() > 1.0])
    if clip:
        flags.append(f'クリップ：{clip} サンプルが最大値に張りついている')

    # ── 機械っぽさの目安 ──
    vel_sd, timing = {}, {}
    for tid, tr in score.tracks.items():
        if tr.fn == 'cue' or len(tr.notes) < 8:
            continue
        vs = np.array([n.vel for n in tr.notes])
        vel_sd[tid] = round(float(vs.std()), 1)
        devs = []
        for n in tr.notes:
            g = round(n.start * 4) / 4
            devs.append((n.start - g) * 60 / tempo.bpm_at(n.start) * 1000)
        timing[tid] = round(float(np.std(devs)), 1)
    fams = sorted({tr.family for tr in score.tracks.values() if tr.fn != 'cue'})
    tail_start = tempo.sec(getattr(score, 'end_beats', spec['total_beats']))
    tail = master[int(tail_start * sr):]
    body = master[int((tail_start - 2) * sr):int(tail_start * sr)]
    tail_db = round(_db(np.sqrt((tail ** 2).mean()) + 1e-12) - _db(np.sqrt((body ** 2).mean()) + 1e-12), 1) if len(tail) and len(body) else None
    LR = np.corrcoef(master[:, 0], master[:, 1])[0, 1]
    # スペクトルの時間変化（同じ音色が鳴りっぱなしだと小さい）
    flux = np.sqrt((np.diff(np.log(S[(f > 200) & (f < 8000)] + 1e-6), axis=1) ** 2).mean(0))
    mech = dict(velocity_sd=vel_sd, timing_sd_ms=timing, families=fams, n_families=len(fams), stereo_correlation=round(float(LR), 3),
                reverb_tail_db=tail_db, spectral_flux=round(float(np.median(flux)), 3),
                engine=info.get('engine') if info else None)
    verdict = []
    if mech['n_families'] < 4:
        verdict.append(f'音色の系統が {mech["n_families"]} 種類しかない')
    if vel_sd and np.median(list(vel_sd.values())) < 4:
        verdict.append('打鍵の強さがほとんど揃っている（機械的）')
    if timing and np.median(list(timing.values())) < 2:
        verdict.append('タイミングが拍にぴったり（機械的）')
    if LR > 0.97:
        verdict.append('左右がほぼ同じ（モノラルに近い）')
    if tail_db is not None and tail_db < -40:
        verdict.append('最後の音のあとに響きが残っていない（部屋の無い音）')
    if info and info.get('engine') == 'synth':
        verdict.append('SoundFont が無く合成音で鳴らした（setup で GeneralUser GS を取ると生楽器に近くなる）')
    mech['verdict'] = verdict
    rep['mechanical'] = mech
    flags += verdict

    # ── 弾き方の目安（2026-10-27「エレクトーンをボンボン叩いている」「曲が少し立ってる」から）──
    # 楽譜の側：旋律がつながっているか、鍵盤がペダルを踏んでいるか、打鍵が強すぎないか、ベースが同じ音を連打していないか
    # 音の側：耳に刺さる高域（2.5kHz 以上）の割合
    play = {'tracks': {}}
    pflags = []
    for tid, tr in score.tracks.items():
        if tr.fn in ('cue', 'drums', 'perc', 'timp') or tr.drum or not tr.notes:
            continue
        ns = sorted(tr.notes, key=lambda n: (n.start, n.pitch))
        row = dict(fn=tr.fn, vel_max=max(n.vel for n in ns), vel_mean=round(float(np.mean([n.vel for n in ns])), 1))
        if tr.fn in ('melody', 'counter', 'double') and (tr.part or {}).get('articulation') != 'staccato':
            starts = sorted({round(n.start, 3) for n in ns})
            ends = {}
            for n in ns:
                ends[round(n.start, 3)] = max(ends.get(round(n.start, 3), 0), n.end)
            pairs = [(a, b) for a, b in zip(starts, starts[1:]) if b - a <= 1.0 + 1e-6]
            gaps = [b - ends[a] for a, b in pairs]
            row['gap_pct'] = round(100 * sum(g > 0.05 for g in gaps) / max(1, len(gaps)))
            if len(gaps) >= 8 and row['gap_pct'] > 20:
                pflags.append(f'{tid}：1拍以内に続く音の {row["gap_pct"]}% で、音と音のあいだが空いている（つながらず、ぶつぶつ聞こえる）')
        if tr.family in ('keys', 'epiano'):
            row['pedal'] = sum(1 for c in tr.cc if c[1] == 64 and c[2] >= 64)
            if row['pedal'] == 0 and len(ns) >= 8:
                pflags.append(f'{tid}：ピアノなのにペダルを踏んでいない（音が切れて硬く聞こえる）')
        cap = 100 if tr.fn == 'melody' else 95
        if tr.family in ('keys', 'epiano', 'pluck', 'mallet') and row['vel_max'] > cap:
            pflags.append(f'{tid}：打鍵の強さが最大 {row["vel_max"]}（{cap} を超えると音源の硬い音が鳴る）')
        if tr.fn == 'bass':
            run = best = 1
            for a, b in zip(ns, ns[1:]):
                run = run + 1 if (b.pitch == a.pitch and b.start - a.start <= 0.55) else 1
                best = max(best, run)
            row['same_note_run'] = best
            if best >= 6:
                pflags.append(f'{tid}：同じ音を8分で {best} 回続けて弾いている（「ボンボンボン」と聞こえる）')
        play['tracks'][tid] = row
    P = S ** 2   # 振幅の2乗（エネルギー）で数える
    hi_share = round(float(P[f >= 2500].sum() / (P.sum() + 1e-12) * 100), 1)
    play['high_2500_pct'] = hi_share
    if hi_share > 6:
        pflags.append(f'2.5kHz 以上の割合が {hi_share}%（耳に刺さって「立って」聞こえやすい。目安 5% 以下）')
    play['verdict'] = pflags
    rep['play'] = play
    flags += pflags

    # ── きっかけ ──
    onset = np.concatenate([[0], np.maximum(0, np.diff(np.log(S[(f > 40) & (f < 12000)].sum(0) + 1e-9)))])
    cue_rows = []
    for c in spec['cues']:
        fr = (t > c.sec - 0.12) & (t < c.sec + 0.12)
        local = (t > c.sec - 2) & (t < c.sec + 2)
        if not fr.any():
            continue
        k = np.argmax(onset * fr)
        # 周り±2秒の立ち上がり（拍の頭など）の上位1割と比べる。1 以上ならその近くで一番目立つ部類
        ref = np.percentile(onset[local & ~fr], 90) if (local & ~fr).any() else 1e-6
        strength = float(onset[k] / (ref + 1e-6))
        if c.type in ('break', 'stop'):
            a, b = tempo.sec(c.at), tempo.sec(c.at + c.length)
            pre = mono[int(max(0, a - (b - a)) * sr):int(a * sr)]
            inn = mono[int(a * sr):int(b * sr)]
            drop = _db(np.sqrt((inn ** 2).mean()) + 1e-9) - _db(np.sqrt((pre ** 2).mean()) + 1e-9)
            cue_rows.append(dict(time=round(c.sec, 2), type=c.type, note=c.note, effect=f'{drop:+.1f} dB', ok=drop < -2))
        elif c.type in ('riser', 'swell'):
            a = tempo.sec(c.at - c.length)
            # 上昇は高域（3kHz 以上）の力の増え方で見る（全体の音量は曲の盛り上がりに埋もれやすい）
            hb = f > 3000
            e0 = S[hb][:, (t >= a) & (t < a + 0.4)].sum(0).mean() if ((t >= a) & (t < a + 0.4)).any() else 1e-9
            e1 = S[hb][:, (t >= c.sec - 0.4) & (t < c.sec)].sum(0).mean() if ((t >= c.sec - 0.4) & (t < c.sec)).any() else 1e-9
            rise = 20 * math.log10((e1 + 1e-9) / (e0 + 1e-9))
            cue_rows.append(dict(time=round(c.sec, 2), type=c.type, note=c.note, effect=f'高域 {rise:+.1f} dB の上昇', ok=rise > 3))
        else:
            off = (t[k] - c.sec) * 1000
            cue_rows.append(dict(time=round(c.sec, 2), type=c.type, note=c.note, effect=f'立ち上がり {strength:.2f}（周りの拍頭=1、ずれ {off:+.0f}ms）',
                                 ok=strength > 0.9))
    rep['cues'] = cue_rows
    for r in cue_rows:
        if not r['ok']:
            flags.append(f'きっかけ {r["type"]} @{r["time"]}秒 が弱い（{r["effect"]}）')
    rep['flags'] = flags

    _images(master, score, sr, outdir, f, t, S, sim, rep)
    with open(os.path.join(outdir, 'report.json'), 'w', encoding='utf-8') as fp:
        json.dump(rep, fp, ensure_ascii=False, indent=1, default=str)
    with open(os.path.join(outdir, 'report.md'), 'w', encoding='utf-8') as fp:
        fp.write(markdown(rep, score))
    return rep


def _marks(ax, spec, ymax=None, cues=True):
    tempo = spec['tempo']
    for s in spec['sections']:
        ax.axvline(tempo.sec(s.start), color='#888', lw=0.8, ls='--')
        ax.text(tempo.sec(s.start) + 0.2, 1.0, s.name, transform=ax.get_xaxis_transform(), fontsize=8, va='top', color='#333')
    if cues:
        for c in spec['cues']:
            ax.axvline(c.sec, color='#d62728', lw=0.8, alpha=0.6)
            ax.text(c.sec, 0.02, c.type, transform=ax.get_xaxis_transform(), fontsize=7, color='#d62728', rotation=90, va='bottom')


def _images(master, score, sr, outdir, f, t, S, sim, rep):
    plt = _plt()
    spec = score.spec
    tempo = spec['tempo']
    img = os.path.join(outdir, 'images')
    # スペクトログラム（対数の周波数）
    fig, ax = plt.subplots(figsize=(14, 5))
    ok = (f > 30) & (f < 16000)
    ax.pcolormesh(t, f[ok], 20 * np.log10(S[ok] + 1e-7), shading='auto', cmap='magma', vmin=-90, vmax=-10)
    ax.set_yscale('log')
    ax.set_ylim(30, 16000)
    ax.set_xlabel('秒')
    ax.set_ylabel('Hz')
    ax.set_title(f'スペクトログラム：{spec["title"]}')
    _marks(ax, spec)
    fig.tight_layout()
    fig.savefig(os.path.join(img, 'spectrogram.png'), dpi=90)
    plt.close(fig)
    # ピアノロール
    tids = [k for k, tr in score.tracks.items()]
    fig, ax = plt.subplots(figsize=(14, 7))
    cmap = plt.get_cmap('tab20')
    for i, tid in enumerate(tids):
        tr = score.tracks[tid]
        col = cmap(i % 20)
        for n in tr.notes:
            y = n.pitch if not tr.drum else 10 + (n.pitch - 35) * 0.5
            ax.add_patch(plt.Rectangle((tempo.sec(n.start), y - 0.4), max(0.03, tempo.sec(n.end) - tempo.sec(n.start)), 0.8,
                                       color=col, alpha=0.35 + 0.6 * n.vel / 127, lw=0))
        ax.plot([], [], color=col, lw=6, label=f'{tid}（{tr.label}）')
    ax.set_xlim(0, tempo.sec(getattr(score, 'end_beats', spec['total_beats'])) + 1)
    ax.set_ylim(5, 110)
    ax.set_xlabel('秒')
    ax.set_ylabel('MIDI の高さ（下の帯は打楽器）')
    for p in range(24, 109, 12):
        ax.axhline(p, color='#ddd', lw=0.5)
        ax.text(0, p, pname(p), fontsize=7, color='#999')
    _marks(ax, spec)
    ax.legend(fontsize=7, ncol=2, loc='upper right', framealpha=0.8)
    ax.set_title('ピアノロール（色＝楽器、濃さ＝打鍵の強さ）')
    fig.tight_layout()
    fig.savefig(os.path.join(img, 'pianoroll.png'), dpi=90)
    plt.close(fig)
    # 音量の推移（短区間ラウドネス 3秒窓）
    lm = pyln.Meter(sr, block_size=0.4)
    times, vals = [], []
    T = len(master) / sr
    x = 0.0
    while x + 3 <= T:
        seg = master[int(x * sr):int((x + 3) * sr)]
        try:
            v = lm.integrated_loudness(seg.astype(np.float64))
        except Exception:
            v = -70
        times.append(x + 1.5)
        vals.append(v if np.isfinite(v) else -70)
        x += 0.5
    fig, ax = plt.subplots(figsize=(14, 3.5))
    ax.plot(times, vals, color='#1f77b4')
    ax.axhline(spec['mix']['loudness'], color='#2ca02c', ls=':', lw=1)
    for s in rep['sections']:
        ax.hlines(s['lufs'], s['start'], s['end'], color='#ff7f0e', lw=2)
    for nr in spec['narration']:
        ax.axvspan(nr['from'], nr['to'], color='#9467bd', alpha=0.1)
    ax.set_ylim(-40, -5)
    ax.set_xlabel('秒')
    ax.set_ylabel('LUFS')
    ax.set_title('音量の推移（青＝3秒窓、橙＝区切りの平均、緑の点線＝目標、紫＝ナレーション）')
    _marks(ax, spec)
    fig.tight_layout()
    fig.savefig(os.path.join(img, 'loudness.png'), dpi=90)
    plt.close(fig)
    # 小節どうしの似かよい
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    im = ax.imshow(sim, cmap='viridis', vmin=0.9, vmax=1.0, origin='lower', extent=[0.5, len(sim) + 0.5, 0.5, len(sim) + 0.5])
    bb = spec['meter'].bar_beats
    for s in spec['sections']:
        b = s.start / bb + 0.5
        ax.axvline(b, color='w', lw=0.6)
        ax.axhline(b, color='w', lw=0.6)
    ax.set_xlabel('小節')
    ax.set_ylabel('小節')
    ax.set_title('小節どうしの音の似かよい（明るいほど似ている）')
    fig.colorbar(im, ax=ax, fraction=0.046)
    fig.tight_layout()
    fig.savefig(os.path.join(img, 'bar_similarity.png'), dpi=90)
    plt.close(fig)


def markdown(rep, score):
    spec = score.spec
    L = [f'# 確かめの報告：{rep["title"]}', '']
    e = rep['engine'] or {}
    L.append(f'- 様式：{spec["style"]["label"]}（{rep["style"]}）　テンポ {spec["bpm"]:g}　拍子 {spec["meter"]}　調 {spec["key"].name()}　長さ {spec["total_sec"]:.1f} 秒')
    L.append(f'- 音源：{e.get("name", "?")}（{e.get("engine", "?")}）' + (f'、ピアノは {e["piano"]}' if e.get('piano') else ''))
    m = rep['mix']
    if m:
        L.append(f'- ラウドネス {m.get("loudness")} LUFS（目標 {m.get("target")}）／真のピーク {m.get("true_peak")} dBTP（天井 {m.get("ceiling")}）')
    L.append('')
    L.append('## 直したほうがよさそうな所')
    L.append('')
    L += [f'- {x}' for x in rep['flags']] or ['- なし']
    L.append('')
    L.append('## 区切りごと')
    L.append('')
    L.append('| 区切り | 秒 | 勢い | LUFS | 音/秒 | 明るさ(Hz) | 広がり | 調 | 足した楽器 | 抜いた楽器 |')
    L.append('|---|---|---|---|---|---|---|---|---|---|')
    for s in rep['sections']:
        ch = s.get('change', {})
        L.append(f'| {s["name"]}（{s["role"]}） | {s["start"]:.1f}–{s["end"]:.1f} | {s["energy"]:.2f} | {s["lufs"]} | {s["notes_per_sec"]} | '
                 f'{s["centroid_hz"]} | {s["width"]} | {s["key"]} | {", ".join(ch.get("added", [])) or "-"} | {", ".join(ch.get("removed", [])) or "-"} |')
    L.append('')
    if rep.get('balance'):
        L.append('帯域のバランス（エネルギーの割合）：' + '、'.join(f'{k} {v}%' for k, v in rep['balance'].items()))
        L.append('')
    L.append('![音量の推移](images/loudness.png)')
    L.append('')
    L.append('![スペクトログラム](images/spectrogram.png)')
    L.append('')
    L.append('![ピアノロール](images/pianoroll.png)')
    L.append('')
    L.append('## 音域')
    L.append('')
    L.append('| 楽器 | 役 | 最低 | 中央 | 最高 | 音の数 | 音域の外 |')
    L.append('|---|---|---|---|---|---|---|')
    for r in rep['ranges']:
        L.append(f'| {r["inst"]}（{r["track"]}） | {r["fn"]} | {r["lo"]} | {r["median"]} | {r["hi"]} | {r["n"]} | {r["out_of_range"]} |')
    L.append('')
    c = rep['chords']
    L.append('## 和音')
    L.append('')
    L.append(f'設計した和音の種類 {c["unique"]}。MIDI から推定した和音との一致 {c["midi_match"] * 100:.0f}%、'
             f'書き出した音から推定した和音との一致 {c["audio_match"] * 100:.0f}%（音からの推定は根音が合えば一致とみなす）。')
    L.append('')
    L.append('| 秒 | 区切り | 設計 | 記号 | MIDI | 音 |')
    L.append('|---|---|---|---|---|---|')
    for r in c['rows']:
        L.append(f'| {r["time"]:.1f} | {r["section"]} | {r["planned"]} | {r["roman"]} | {r["midi"]}{"" if r["midi_match"] else " ✗"} | '
                 f'{r["audio"]}{"" if r["audio_match"] else " ✗"} |')
    L.append('')
    r = rep['repetition']
    L.append('## 同じ小節の繰り返し')
    L.append('')
    L.append(f'全 {r["bars"]} 小節のうち、中身の違う小節 {r["unique_bars"]}。全楽器が同じ小節の組 {len(r["exact_duplicates"])}、'
             f'そのうち連続 {len(r["consecutive_duplicates"])}。音の特徴がほぼ同じ（0.998 超）の組 {r["near_audio_count"]}。')
    L.append('')
    L.append('![小節どうしの似かよい](images/bar_similarity.png)')
    L.append('')
    k = rep['clipping']
    L.append('## クリップ')
    L.append('')
    L.append(f'最大値に張りついたサンプル {k["samples_at_full_scale"]}、サンプルのピーク {k["sample_peak_db"]} dBFS、真のピーク {k["true_peak_db"]} dBTP。')
    L.append('')
    mm = rep['mechanical']
    L.append('## 機械っぽさの目安')
    L.append('')
    L.append(f'- 音色の系統 {mm["n_families"]} 種類：{", ".join(mm["families"])}')
    L.append(f'- 打鍵の強さのばらつき（標準偏差）：' + '、'.join(f'{k} {v}' for k, v in mm['velocity_sd'].items()))
    L.append(f'- 拍の格子からのずれ（標準偏差 ms）：' + '、'.join(f'{k} {v}' for k, v in mm['timing_sd_ms'].items()))
    L.append(f'- 左右の相関 {mm["stereo_correlation"]}（1 に近いほどモノラル）、最後の音のあとの響き {mm["reverb_tail_db"]} dB、スペクトルの時間変化 {mm["spectral_flux"]}')
    L.append(f'- 判定：' + ('、'.join(mm['verdict']) if mm['verdict'] else '目安の範囲では機械的な所は見つからない'))
    L.append('')
    pl = rep.get('play') or {}
    if pl:
        L.append('## 弾き方の目安')
        L.append('')
        L.append('| 楽器 | 役 | 強さ 平均／最大 | 音のあいだが空く割合 | ペダル | ベースの同音連打 |')
        L.append('|---|---|---|---|---|---|')
        for tid, r in pl['tracks'].items():
            L.append(f'| {tid} | {r["fn"]} | {r["vel_mean"]}／{r["vel_max"]} | {str(r["gap_pct"]) + "%" if "gap_pct" in r else "-"} | '
                     f'{r["pedal"] if "pedal" in r else "-"} | {r.get("same_note_run", "-")} |')
        L.append('')
        L.append(f'2.5kHz 以上の割合 {pl["high_2500_pct"]}%（目安 5% 以下）。判定：' + ('、'.join(pl['verdict']) if pl['verdict'] else '目安の範囲'))
        L.append('')
    L.append('## きっかけ')
    L.append('')
    L.append('| 秒 | 種類 | 効き | メモ |')
    L.append('|---|---|---|---|')
    for c in rep['cues']:
        L.append(f'| {c["time"]:.2f} | {c["type"]} | {c["effect"]}{"" if c["ok"] else " ✗"} | {c["note"]} |')
    L.append('')
    L.append('## 作曲の判断の記録')
    L.append('')
    L += [f'- {x}' for x in score.log]
    if rep['warnings']:
        L.append('')
        L.append('## 設計図の読み込みで直したこと')
        L.append('')
        L += [f'- {x}' for x in rep['warnings']]
    L.append('')
    return '\n'.join(L)
