"""作曲：設計図（spec.normalize の返り値）から、楽器ごとの音符の並び（Score）を作る。

順番：
  1. 和声：区切りごとに進行を選び、繰り返しには代理和音や副属和音で変化をつけ、終止と転調を入れる
  2. 旋律：曲全体で1つの動機を作り、区切りの役割に合わせて反復・模続・転回・拡大で展開する
  3. 伴奏：様式の parts を、区切りの勢い（energy）で出し入れしながら型（PATTERNS）で埋める
  4. きっかけ（cues）：拍を落とす・一撃・盛り上げなどを音符に反映し、効果音を予約する
  5. 終止：最後の小節を主和音の伸ばしにする
  6. 強弱：区切りの勢いと句の山で、打鍵の強さと表情（CC11）を描く
  7. 同じ小節の解消：全楽器の中身が同じ小節があれば、太鼓・和音の配置・旋律の飾りで変える
  8. 人間らしい揺れ：タイミングと強さを少しずつずらし、ローファイは跳ねさせる
"""
import math
import random

from . import styles
from .theory import Chord, Key, nearest_pitch, parse_roman, voice_chord


class Note:
    __slots__ = ('start', 'dur', 'pitch', 'vel', 'tag')

    def __init__(self, start, dur, pitch, vel, tag=''):
        self.start, self.dur, self.pitch, self.vel, self.tag = start, dur, int(pitch), int(vel), tag

    @property
    def end(self):
        return self.start + self.dur

    def __repr__(self):
        return f'N({self.start:.2f},{self.dur:.2f},{self.pitch},{self.vel})'


class Track:
    def __init__(self, tid, inst, fn, level=-6, part=None):
        info = styles.INSTRUMENTS[inst]
        self.id, self.inst, self.fn, self.level = tid, inst, fn, level
        self.program, self.family, self.pan = info['program'], info['family'], info['pan']
        self.lo, self.hi, self.label = info['lo'], info['hi'], info['label']
        self.drum = bool(info.get('drum'))
        self.part = part or {}
        self.notes, self.cc = [], []   # cc: (拍, 番号, 値)
        self.channel = None

    def add(self, start, dur, pitch, vel, tag=''):
        if dur <= 0:
            return
        self.notes.append(Note(start, dur, pitch, max(1, min(127, int(vel))), tag))


class ChordSpan:
    def __init__(self, start, end, chord, key, section):
        self.start, self.end, self.chord, self.key, self.section = start, end, chord, key, section


class Score:
    def __init__(self, spec):
        self.spec = spec
        self.tracks = {}
        self.chords = []
        self.sfx = []
        self.log = []
        self.melody_line = []   # (start, dur, pitch) 旋律の骨組み（重ねや対旋律が読む）

    def track(self, tid, inst, fn, level=-6, part=None):
        if tid not in self.tracks:
            self.tracks[tid] = Track(tid, inst, fn, level, part)
        return self.tracks[tid]

    def chord_at(self, beat):
        for c in self.chords:
            if c.start <= beat + 1e-6 < c.end:
                return c
        return self.chords[-1]

    def next_chord(self, span):
        i = self.chords.index(span)
        return self.chords[i + 1] if i + 1 < len(self.chords) else None


# ── 1. 和声 ──────────────────────────────────────────

SUBS = {  # 繰り返しのときの代理和音
    'I': ['I/3', 'vi', 'Iadd9', 'I/5'], 'IV': ['ii7', 'IVmaj7', 'IVadd9', 'ii'], 'V': ['Vsus4-V', 'V7', 'V/5', 'iii'],
    'vi': ['vi7', 'IV/6', 'I/3'], 'ii': ['ii7', 'IV'], 'iii': ['V/vi', 'I/3'],
    'i': ['i/3', 'i7', 'VI'], 'iv': ['iv7', 'ii°', 'VI'], 'VI': ['VImaj7', 'iv'], 'VII': ['VII/3', 'v'], 'III': ['IIImaj7', 'i/3'],
}


def _sub(tok, rng):
    base = tok.split('/')[0].rstrip('7').replace('maj', '').replace('add9', '')
    opts = SUBS.get(tok) or SUBS.get(base)
    return rng.choice(opts) if opts else tok


def choose_progression(style_name, key, tag, used, rng):
    mode = 'minor' if key.minorish else 'major'
    cands = [p for p in styles.PROGRESSIONS if p['mode'] in (mode, 'any') and (not p.get('styles') or style_name in p['styles'])]
    best, best_s = None, -1e9
    for p in cands:
        s = (3 if tag in p['tags'] else 0) + rng.random() * 1.5 - 6 * used.count(p['p'])
        if style_name == 'lofi' and p.get('styles') == ['lofi']:
            s += 2
        if s > best_s:
            best, best_s = p, s
    used.append(best['p'])
    return best['p'].split()


def colorize(chord, key, style_name):
    """ローファイ・アコースティックは三和音に音階内の7度（と9度）を足す。"""
    if style_name not in ('lofi',) or len(chord.intervals) > 3 or chord.intervals[1:3] == [5, 7]:
        return chord
    for iv in (11, 10):
        if (chord.root + iv - key.tonic) % 12 in key.scale:
            chord.intervals = chord.intervals + [iv]
            break
    if (chord.root + 14 - key.tonic) % 12 in key.scale and chord.intervals[1] == 3:
        chord.intervals = chord.intervals + [14]
    return chord


# 2回目以降も1回目と同じ和声と旋律を使う役（曲の形を A B A B のように聞かせる）
ROLE_REPEAT = ('verse', 'build', 'chorus')


def build_harmony(score, rng):
    sp = score.spec
    meter, sections, style_name = sp['meter'], sp['sections'], sp['style_name']
    style = sp['style']
    used = []
    main_prog = {}
    for si, sec in enumerate(sections):
        nbars = max(1, math.ceil(sec.length / meter.bar_beats - 1e-6))
        if sec.progression:
            prog = sec.progression
            if isinstance(prog, str):
                prog = [t for t in prog.replace('|', ' ').split()]
            toks = list(prog)
            score.log.append(f'{sec.name}：和声は設計図の指定 {" ".join(toks)}')
            base = toks
        else:
            # サビは曲の「顔」なので、2回目以降のサビは同じ進行を使う（旋律と編成で変化をつける）。
            # 平歌（verse）と盛り上げ（build）も、2回目は1回目と同じ進行にする。前は区切りごとに選び直していて、
            # 宣伝ムービーの曲の2回目の盛り上げ（I vi IV V）と平歌（I bVII IV）が、ほかの区切りと噛み合わず「急に和・民謡みたいになる」と言われた（2026-10-27 の指摘）
            if sec.role in ROLE_REPEAT and sec.role in main_prog and sec.repeat is not False:
                base = main_prog[sec.role]
            else:
                base = choose_progression(style_name, sec.key, sec.mood_tag, used, rng)
                if sec.role in ROLE_REPEAT:
                    main_prog[sec.role] = base
            score.log.append(f'{sec.name}（{sec.role}・{sec.mood_tag}）：和声 {" ".join(base)}  調 {sec.key.name()}')
        bars = []
        for b in range(nbars):
            cyc, pos = divmod(b, len(base))
            tok = base[pos]
            if cyc >= 1 and not sec.progression:
                # 2周目以降：1つを代理和音に、句の終わりは次の和音への副属和音で引っぱる
                if pos == (cyc * 2 + 1) % len(base):
                    tok = _sub(tok, rng)
                if pos == len(base) - 1 and b < nbars - 1 and '-' not in tok:
                    nxt = base[0].split('-')[0].split('/')[0]
                    if nxt not in ('I', 'i'):
                        tok = f'{tok}-V7/{nxt}'
            bars.append([(tok, sec.key)])
        # 終止
        is_last = si == len(sections) - 1
        cad = sec.cadence or (style.get('ending_cadence', 'authentic') if is_last else styles.ROLE_CADENCE.get(sec.role))
        mode = 'minor' if sec.key.minorish else 'major'
        nxt_sec = sections[si + 1] if not is_last else None
        if cad and cad in styles.CADENCES[mode]:
            seq = styles.CADENCES[mode][cad]
            if is_last and nbars >= 2:
                # 最後は終止の和音を1小節まるごと伸ばす
                bars[-1] = [(seq[-1], sec.key)]
                bars[-2] = [(t, sec.key) for t in seq[:-1]]
            elif nbars >= 2 and len(seq) > 2:
                bars[-1] = [(seq[-1], sec.key)]
                bars[-2] = [(t, sec.key) for t in seq[:-1]]
            else:
                bars[-1] = [(t, sec.key) for t in seq]
            score.log.append(f'{sec.name}：終止 {cad}（{" ".join(seq)}）')
        # 転調：次の区切りの調が違えば、最後の拍に次の調の属七を置いて橋渡し
        if nxt_sec and nxt_sec.key.tonic != sec.key.tonic and nxt_sec.modulate == 'pivot':
            last = bars[-1]
            bars[-1] = last[:1] + [('V7', nxt_sec.key)] if len(last) == 1 else last[:-1] + [('V7', nxt_sec.key)]
            score.log.append(f'{sec.name}→{nxt_sec.name}：{sec.key.name()} から {nxt_sec.key.name()} へ、属七で橋渡しして転調')
        elif nxt_sec and nxt_sec.key.tonic != sec.key.tonic:
            score.log.append(f'{sec.name}→{nxt_sec.name}：{nxt_sec.key.name()} へそのまま上げる転調')
        # 拍に並べる
        for b, bar in enumerate(bars):
            bar_start = sec.start + b * meter.bar_beats
            bar_end = min(sec.end, bar_start + meter.bar_beats)
            if bar_start >= sec.end - 1e-6:
                break
            pieces = []
            for tok, k in bar:
                pieces += [(t, k) for t in tok.split('-')]
            span = (bar_end - bar_start) / len(pieces)
            if len(pieces) == 2 and bar_end - bar_start == 4:
                span = 2
            for j, (t, k) in enumerate(pieces):
                ch = colorize(parse_roman(t, k), k, style_name)
                score.chords.append(ChordSpan(bar_start + j * span, bar_start + (j + 1) * span, ch, k, si))
    # 同じ和音が続いたらまとめる（伸ばしの楽器の打ち直しを減らす）
    merged = []
    for c in score.chords:
        if merged and merged[-1].chord.pcs == c.chord.pcs and merged[-1].chord.bass_pc == c.chord.bass_pc \
                and merged[-1].section == c.section and (c.start - sections[c.section].start) % (meter.bar_beats * 2) != 0:
            merged[-1].end = c.end
        else:
            merged.append(c)
    score.chords = merged


# ── 2. 旋律 ──────────────────────────────────────────

RHYTHM_CELLS = {
    'pop': [[(0, 1), (1, .5), (1.5, .5), (2, 1.5), (3.5, .5)], [(0, .5), (.5, .5), (1, 1), (2, .5), (2.5, 1.5)],
            [(.5, .5), (1, .5), (1.5, 1), (2.5, .5), (3, 1)], [(0, .75), (.75, .75), (1.5, 1), (2.5, .5), (3, 1)]],
    'lyrical': [[(0, 1.5), (1.5, .5), (2, 2)], [(0, 1), (1, 1), (2, 1.5), (3.5, .5)], [(0, 2), (2, 1), (3, 1)],
                [(0, .5), (.5, .5), (1, 2), (3, 1)]],
    'lofi': [[(.5, 1), (1.5, .5), (2.5, 1.5)], [(0, .75), (.75, .75), (1.5, 1), (3, 1)], [(.5, .5), (1, .5), (2, 2)]],
    'playful': [[(0, .5), (.5, .5), (1, .5), (2, .5), (2.5, .5), (3, 1)], [(0, .5), (1, .5), (1.5, .5), (2, 1), (3, .5), (3.5, .5)],
                [(.5, .5), (1, .5), (1.5, .5), (2, .5), (3, 1)]],
}
CELL_OF_STYLE = {'bright_pop': 'pop', 'acoustic': 'lyrical', 'cinematic': 'lyrical', 'lofi': 'lofi', 'playful': 'playful'}
STEP_W = {1: 3, -1: 3, 2: 2, -2: 1.6, 3: 1, -3: .8, 4: .5, -4: .4, 0: .7, 5: .25}


def _cell_fit(cell, bar_beats):
    return [(o, min(d, bar_beats - o)) for o, d in cell if o < bar_beats - 1e-6]


class Motif:
    def __init__(self, rhythm, contour):
        self.rhythm, self.contour = rhythm, contour

    @classmethod
    def make(cls, rng, cells, bar_beats):
        cell = _cell_fit(rng.choice(cells), bar_beats)
        contour, d, last = [0], 0, 0
        for i in range(1, len(cell)):
            if abs(last) >= 3:
                step = -int(math.copysign(1, last)) * rng.choice([1, 1, 2])   # 跳躍の後は反対へ戻る
            else:
                step = rng.choices(list(STEP_W), list(STEP_W.values()))[0]
            d += step
            d = max(-4, min(6, d))
            contour.append(d)
            last = step
        if max(contour) - min(contour) < 3:   # 平坦すぎる動機は1か所跳ばす
            contour[len(contour) // 2] += 3
        return cls(cell, contour)

    def inverted(self):
        return Motif(self.rhythm, [-c for c in self.contour])

    def retro_rhythm(self, bar_beats):
        return Motif([(bar_beats - o - d, d) for o, d in reversed(self.rhythm)], self.contour)


# 旋律の中心の高さ（伴奏より上に出す）
MELODY_CENTER = {'piano': 76, 'bright_piano': 76, 'clean_guitar': 79, 'violin': 79, 'flute': 81, 'oboe': 76, 'clarinet': 74,
                 'horn': 67, 'trumpet': 74, 'vibes': 76, 'whistle': 81, 'marimba': 76}


def compose_melody(score, rng, parts_by_sec):
    sp = score.spec
    meter, bb = sp['meter'], sp['meter'].bar_beats
    cells = RHYTHM_CELLS[CELL_OF_STYLE.get(sp['style_name'], 'pop')]
    A = Motif.make(rng, cells, bb)
    B = Motif.make(rng, cells, bb)
    B.contour = [-c for c in A.contour][:len(B.rhythm)] + [0] * max(0, len(B.rhythm) - len(A.contour))
    score.log.append(f'動機A：リズム {[o for o, _ in A.rhythm]} 音の動き {A.contour}')
    score.motif = A
    prev_deg = 2
    line = []
    sung = {}   # 役 → (小節の数, [(区切りの頭からの拍, 長さ, 度数, 句の中の位置, 最後の小節か)])
    for sec in sp['sections']:
        mparts = [p for p in parts_by_sec[sec.index] if p['fn'] == 'melody']
        if not mparts or sec.melody is False or sec.role == 'break':
            continue
        part = mparts[0]
        info = styles.INSTRUMENTS[part['inst']]
        # 主音の高さ：楽器の音域の中ほどより少し上
        center = max(info['lo'] + 12, min(info['hi'] - 10, MELODY_CENTER.get(part['inst'], 74)))
        base = nearest_pitch(sec.key.tonic, center - 2)
        shift = {'intro': 0, 'verse': -1, 'build': 0, 'chorus': 2, 'bridge': 1, 'outro': 0}.get(sec.role, 0)
        nbars = max(1, math.ceil(sec.length / bb - 1e-6))
        motif = B if sec.role == 'bridge' else A
        # 同じ役の2回目：1回目の旋律をそのまま写す（小節の数が同じとき）。主音からの距離で写すので、転調していればその調で同じ旋律になる
        prior = sung.get(sec.role) if sec.role in ROLE_REPEAT and sec.repeat is not False else None
        if prior and prior[0] == nbars:
            out = [(sec.start + o, min(d, sec.end - sec.start - o), base + off, sec, pos, lastbar)
                   for (o, d, off, pos, lastbar) in prior[1] if sec.start + o < sec.end - 1e-6]
            if out and max(x[2] for x in out) > info['hi'] - 2:
                out = [(x[0], x[1], x[2] - 12) + x[3:] for x in out]
            elif out and min(x[2] for x in out) < info['lo'] + 3:
                out = [(x[0], x[1], x[2] + 12) + x[3:] for x in out]
            score.log.append(f'{sec.name}（{sec.role}）：旋律は前の {sec.role} を繰り返す')
            line += out
            continue
        # 区切りの頭の高さ（度数）。山場は高く、紹介は低めから。前の区切りの最後の音とは近い方の8度を選ぶ
        want = {'intro': 2, 'verse': 0, 'build': 0, 'chorus': 2, 'bridge': 2, 'outro': 2}.get(sec.role, 2)
        prev_deg = want
        out = []
        prev_bar = None
        for b in range(nbars):
            bar_start = sec.start + b * bb
            if bar_start >= sec.end - 1e-6:
                break
            pos, ph = b % 4, b // 4
            last_bar = b == nbars - 1
            phrase_last = pos == 3 or last_bar
            m = motif
            if ph % 2 == 1 and pos == 0:
                m = Motif(motif.rhythm, motif.contour[:-1] + [motif.contour[-1] + rng.choice([-2, 2])])
            if pos == 1:
                # 応答：音の動きはそのままで、リズムをもう一つの動機のものに替える
                rh = B.rhythm if motif is A else A.rhythm
                m = Motif(rh, (m.contour + [m.contour[-1] - 1] * len(rh))[:len(rh)])
            if pos == 2:
                # 展開：動機の頭を半分に切って2回、2回目は1度上へ（模続）
                half = [(o, d) for o, d in m.rhythm if o < bb / 2] or m.rhythm[:2]
                rh = half + [(o + bb / 2, d) for o, d in half if o + bb / 2 < bb]
                ct = m.contour[:len(half)] + [c + 1 for c in m.contour[:len(half)]]
                m = Motif(rh[:len(ct)], ct[:len(rh)])
                if sec.role == 'bridge':
                    m = m.inverted()
            if sec.role == 'intro' and pos in (1, 3) and not last_bar:
                continue
            if sec.role == 'outro' and b >= 1 and pos % 2 == 1 and not last_bar:
                continue
            if phrase_last:
                # 句の終わり：頭の1〜2音のあと、和音の音で伸ばす
                rh = [(0, .5), (.5, .5), (1, bb - 1)] if rng.random() < .6 else [(0, 1), (1, bb - 1)]
                ct = [0, 1, 2][:len(rh)] if rng.random() < .5 else [0, -1, -2][:len(rh)]
                m = Motif(rh, ct)
            if sec.role == 'build' and last_bar:
                # 盛り上げのせり上がりは、次の区切りへ渡す最後の小節だけ。前は小節ごとに1度ずつ上げていて、区切りの途中で旋律が持ち上がり、山場より高くなった（2026-10-27）
                m = Motif(m.rhythm, [c + 1 for c in m.contour])
            chord = score.chord_at(bar_start)
            # 小節の最初の音：前の音から近い和音の音（度数で）
            target = prev_deg + (1 if sec.role == 'build' and last_bar else 0)
            start_deg = _snap_to_chord(target, chord, sec.key, base, shift)
            for (o, d), c in zip(m.rhythm, m.contour):
                t = bar_start + o
                if t >= sec.end - 1e-6:
                    break
                ch = score.chord_at(t)
                deg = start_deg + c
                strong = (o % (meter.pulse * 2) == 0) or d >= 1.5
                if strong:
                    deg = _snap_to_chord(deg, ch, sec.key, base, shift)
                if phrase_last and o >= 1 - 1e-6:
                    # 終わりの長い音：最後の句なら主音、そうでなければ和音の3度か5度
                    want = [0, 7, -7] if (last_bar and (sec.role == 'outro' or sec.cadence == 'authentic')) else None
                    deg = _snap_to_chord(deg, ch, sec.key, base, shift, prefer=want)
                pitch = sec.key.degree_pitch(deg + shift, base)
                if phrase_last and o >= 1 - 1e-6 and out and out[-1][2] == pitch and out[-1][0] >= bar_start:
                    # 句の終わりの伸ばしが直前と同じ音なら、隣の和音の音へ
                    deg = _snap_to_chord(deg + 2, ch, sec.key, base, shift)
                    pitch = sec.key.degree_pitch(deg + shift, base)
                out.append((t, min(d, sec.end - t), pitch, sec, pos, b == nbars - 1))
                prev_deg = deg
            # 音域に収める：小節まるごとオクターブで動かす（1音ずつ動かすと動機の形が崩れる）
            k0 = next((k for k, x in enumerate(out) if x[0] >= bar_start), len(out))
            if k0 < len(out):
                hi_p, lo_p = max(x[2] for x in out[k0:]), min(x[2] for x in out[k0:])
                mv = -12 if hi_p > info['hi'] - 2 and lo_p - 12 >= info['lo'] else (12 if lo_p < info['lo'] + 3 and hi_p + 12 <= info['hi'] else 0)
                if mv:
                    out[k0:] = [(x[0], x[1], x[2] + mv) + x[3:] for x in out[k0:]]
                    prev_deg += 7 * mv // 12
            this_bar = [(round(x[0] - bar_start, 3), x[2]) for x in out if bar_start <= x[0] < bar_start + bb]
            if this_bar and this_bar == prev_bar:
                # 前の小節と同じ旋律：1度上へずらす（模続）
                k0 = len(out) - len(this_bar)
                for k in range(k0, len(out)):
                    t0, d0, p0, s0, po, lb = out[k]
                    up = p0 + (2 if sec.key.in_scale(p0 + 2) else 1)
                    out[k] = (t0, d0, up if up <= info['hi'] - 2 else p0 - 1, s0, po, lb)
                this_bar = [(round(x[0] - bar_start, 3), x[2]) for x in out[k0:]]
            prev_bar = this_bar
        # 山場は曲でいちばん高く聞こえるように：前の区切りの最高音より低ければ、音域に収まるかぎり1オクターブ上げる
        if sec.role == 'chorus' and out and line and max(x[2] for x in out) < max(x[2] for x in line) and max(x[2] for x in out) + 12 <= info['hi'] - 2:
            out = [(x[0], x[1], x[2] + 12) + x[3:] for x in out]
            base += 12
        if sec.role in ROLE_REPEAT and sec.role not in sung and out:
            # 主音（base）からの半音の距離で覚える（音域に収めたオクターブの移動や模続も含めて写せる）
            sung[sec.role] = (nbars, [(x[0] - sec.start, x[1], x[2] - base, x[4], x[5]) for x in out])
        line += out
    fixed = line
    score.melody_line = fixed
    return fixed


def _snap_to_chord(deg, chord, key, base, shift, prefer=None):
    """度数 deg に近い、和音の構成音の度数。"""
    best, bd = deg, 99
    for dd in range(deg - 3, deg + 4):
        pc = key.degree_pc(dd + shift)
        if pc in chord.chord.triad_pcs or (pc in chord.chord.pcs and len(chord.chord.pcs) > 3):
            score = abs(dd - deg)
            if prefer is not None and ((dd + shift) % 7) == 0:
                score -= 2
            if score < bd:
                best, bd = dd, score
    if best == deg and key.degree_pc(deg + shift) not in chord.chord.pcs:
        # 音階の度数で和音の音に届かない（借用和音など）：一番近い半音へ
        return deg
    return best


# ── 3. 伴奏の型 ───────────────────────────────────────

def chord_spans_in(score, start, end):
    return [c for c in score.chords if c.end > start + 1e-6 and c.start < end - 1e-6]


def bass_pitch(chord, lo=33, hi=47, prev=None):
    center = (lo + hi) // 2 - 2
    target = (prev + center) / 2 if prev is not None else center   # 前の音に近く、でも真ん中へ引き戻す
    p = nearest_pitch(chord.bass_pc, target)
    while p < lo:
        p += 12
    while p > hi:
        p -= 12
    return p


def voicing(score, track, span, lo, hi, n=4, key='v'):
    prev = getattr(track, '_prev_' + key, None)
    v = voice_chord(span.chord, prev, lo, hi, n)
    setattr(track, '_prev_' + key, v)
    return v


def _vel(sec, base=0, var=0, rng=None):
    v = 48 + 58 * sec.energy + base
    if rng:
        v += rng.uniform(-var, var)
    return v


def pattern_comp(score, tr, part, sec, rng, bars):
    pat, bb = part.get('pattern', 'block'), score.spec['meter'].bar_beats
    lo = tr.lo if tr.inst not in ('piano', 'bright_piano', 'epiano') else 50
    hi = min(tr.hi, 70 if tr.family in ('keys', 'epiano') else 76)
    for bi, (b0, b1) in enumerate(bars):
        for span in chord_spans_in(score, b0, b1):
            s0, s1 = max(span.start, b0), min(span.end, b1)
            v = voicing(score, tr, span, lo, hi, 4 if pat != 'lofi_stabs' else 3)
            if pat == 'lofi_stabs':
                third, sev = span.chord.third_pc, span.chord.seventh_pc
                pcs = [p for p in [third, sev, (span.chord.root + 14) % 12, (span.chord.root + 7) % 12] if p is not None][:3]
                v = sorted(nearest_pitch(pc, 62) for pc in pcs)
            hits = COMP_RHYTHMS[pat](s1 - s0, bi + int(s0), sec.energy, rng)
            for o, d, acc in hits:
                t = s0 + o
                if t >= s1 - 1e-6:
                    continue
                vv = _vel(sec, -8 + acc * 10, 4, rng)
                if pat == 'strum':
                    gv = guitar_voicing(span.chord)
                    down = acc >= 0
                    strings = gv if down else gv[-4:][::-1]
                    for k, p in enumerate(strings):
                        tr.add(t + k * 0.022, min(d, s1 - t), p, vv - (0 if down else 12) - k * 1.5)
                elif pat == 'oompah':
                    if acc >= 0:
                        for p in v[1:]:
                            tr.add(t, min(d, s1 - t), p, vv)
                else:
                    for k, p in enumerate(v):
                        tr.add(t + (k * 0.012 if tr.family == 'keys' and d >= 1 else 0), min(d, s1 - t), p, vv - (k == 0) * 6)
                # ピアノの左手：ベースがいない区切りでは根音を足す
                if tr.family in ('keys',) and o == 0 and not part.get('_bass_active'):
                    tr.add(t, min(2, s1 - t), bass_pitch(span.chord, 36, 50), vv - 6)


def _r(*xs):
    return [(o, d, a) for o, d, a in xs]


def comp_pop_syncop(L, i, e, rng):
    if e < 0.45:
        return _r((0, L, 1)) if L <= 2 else _r((0, 2, 1), (2, L - 2, 0))
    v = [_r((0, .75, 1), (.75, .75, 0), (1.5, 1, 0), (2.5, .5, -1), (3, 1, 0)),
         _r((0, 1.5, 1), (1.5, 1, 0), (2.5, 1.5, 0)),
         _r((0, .5, 1), (.5, 1, 0), (1.5, .5, -1), (2, .75, 1), (2.75, 1.25, 0)),
         _r((0, 1, 1), (1, .5, -1), (1.5, 1, 0), (3, .5, 0), (3.5, .5, -1))]
    return [(o, d, a) for o, d, a in v[i % len(v)] if o < L]


def comp_strum(L, i, e, rng):
    base = [(0, 1, 1), (1, .5, 0), (1.5, .5, -1), (2.5, .5, -1), (3, .5, 0), (3.5, .5, -1)]
    if e < 0.6:
        base = [(0, 1.5, 1), (1.5, .5, -1), (2, 1, 0), (3, 1, -1)]
    if i % 4 == 3:
        base = base[:-1] + [(3.5, .25, -1), (3.75, .25, -1)]
    return [x for x in base if x[0] < L]


def comp_lofi(L, i, e, rng):
    v = [_r((0, 1.25, 1), (1.75, .5, -1), (2.5, 1.5, 0)), _r((-0.25 + 0.25, .75, 0), (1.5, 1, 1), (3.5, .5, -1)),
         _r((0, 2, 1), (2.75, 1.25, 0)), _r((0, .5, 1), (1, 1, 0), (2.5, .5, -1), (3, 1, 0))]
    return [x for x in v[i % len(v)] if x[0] < L]


def comp_oompah(L, i, e, rng):
    return [(o, .3, 0 if o % 2 else -1) for o in [1, 3] if o < L] + ([(2.5, .25, 0)] if i % 2 and L > 2.5 else [])


def comp_block(L, i, e, rng):
    return [(0, L, 1)]


COMP_RHYTHMS = {'pop_syncop': comp_pop_syncop, 'strum': comp_strum, 'lofi_stabs': comp_lofi, 'oompah': comp_oompah, 'block': comp_block}


def guitar_voicing(chord):
    """ギターの6弦の押さえ方に近い並び（低い根音から5〜6音）。"""
    root = nearest_pitch(chord.bass_pc, 45)
    while root < 40:
        root += 12
    out, p = [root], root
    tones = [(chord.root + i) % 12 for i in chord.intervals[:3]]
    order = [tones[2], tones[0], tones[1], tones[0], tones[2]] if len(tones) == 3 else tones
    for pc in order:
        p += 1
        while p % 12 != pc:
            p += 1
        if p > 71:
            break
        out.append(p)
    return out


def pattern_arp(score, tr, part, sec, rng, bars):
    pat, bb = part.get('pattern'), score.spec['meter'].bar_beats
    for bi, (b0, b1) in enumerate(bars):
        for span in chord_spans_in(score, b0, b1):
            s0, s1 = max(span.start, b0), min(span.end, b1)
            ch = span.chord
            if pat == 'travis':
                v = guitar_voicing(ch)
                bass = [v[0], v[2] if len(v) > 2 else v[0]]
                top = v[3:] or v[1:]
                t, k = s0, 0
                while t < s1 - 1e-6:
                    o = t - b0
                    if abs(o - round(o)) < 1e-6:
                        tr.add(t, min(1.5, s1 - t), bass[int(round(o)) % 2], _vel(sec, -6, 3, rng))
                    if abs((o * 2) % 2 - 1) < 1e-6 or (sec.energy > .55 and abs(o - round(o)) < 1e-6 and int(round(o)) % 2 == 0):
                        tr.add(t, min(1.0, s1 - t), top[k % len(top)], _vel(sec, -14, 5, rng))
                        k += 1
                    t += 0.5
            elif pat == 'harp':
                tones = sorted({nearest_pitch(pc, 60) for pc in ch.pcs})
                seq = [p - 12 for p in tones] + tones + [p + 12 for p in tones]
                seq = [p for p in seq if tr.lo <= p <= tr.hi]
                step = 0.25 if sec.energy > 0.4 else 0.5
                updown = seq + seq[-2:0:-1]
                t, k = s0, (bi * 3) % len(updown)
                while t < s1 - 1e-6:
                    tr.add(t, 2.0, updown[k % len(updown)], _vel(sec, -10 + (k % 4 == 0) * 6, 4, rng))
                    t += step
                    k += 1
            elif pat == 'sparkle':
                tones = sorted({nearest_pitch(pc, 84) for pc in ch.pcs})
                if (bi + int(s0)) % 2 == 1 or sec.energy > .7:
                    st = s1 - 1.0 if s1 - s0 >= 2 else s0
                    for k, p in enumerate(tones[:4] if bi % 2 else tones[::-1][:4]):
                        if st + k * 0.25 < s1:
                            tr.add(st + k * .25, .6, max(tr.lo, min(tr.hi, p)), _vel(sec, -20, 4, rng))
            elif pat == 'lofi_pick':
                tones = sorted({nearest_pitch(pc, 67) for pc in ch.pcs})
                for o in ([1.5, 3] if bi % 2 else [0.5, 2.5, 3.5]):
                    if s0 + o < s1:
                        tr.add(s0 + o, .75, rng.choice(tones), _vel(sec, -18, 5, rng))


def pattern_bass(score, tr, part, sec, rng, bars):
    pat = part.get('pattern', 'root')
    lo, hi = max(tr.lo, 28), min(tr.hi, 50)
    prev = None
    for bi, (b0, b1) in enumerate(bars):
        spans = chord_spans_in(score, b0, b1)
        for span in spans:
            s0, s1 = max(span.start, b0), min(span.end, b1)
            L = s1 - s0
            root = bass_pitch(span.chord, lo, hi, prev)
            prev = root
            nxt = score.next_chord(span)
            nroot = bass_pitch(nxt.chord, lo, hi, root) if nxt else root
            # 5度は、上（根音+7）と下（根音-5）のうち次の根音に近いほう。上ばかり選ぶと B→F#（上へ7度）→G（下へ10度）のように跳ねた（2026-10-27）
            fifth = min([f for f in (root + 7, root - 5) if lo - 2 <= f <= hi + 2] or [root + 7], key=lambda f: abs(f - nroot))
            approach = nroot - 1 if nroot > root else nroot + 1
            if pat == 'pop_eighths':
                # 8分の連打は山場（0.75 以上）だけ。盛り上げや平歌で根音を8分で叩き続けると「ボンボンボン」と鳴る（2026-10-27 の指摘）
                if sec.energy_at(s0) < 0.75:
                    seq = [(0, 1.5, root), (1.5, .5, root), (2, 1.5, fifth), (3.5, .5, approach)]
                else:
                    seq = [(o * .5, .45, root if o not in (3, 7) else (root + 12 if o == 3 else approach)) for o in range(8)]
                    if bi % 2:
                        seq[5] = (2.5, .45, fifth)
            elif pat == 'two_feel':
                seq = [(0, 2, root), (2, 1.5, fifth), (3.5, .5, approach)] if bi % 2 == 0 else [(0, 1.5, root), (1.5, .5, root + 12 if root + 12 <= hi + 5 else root), (2, 2, fifth)]
            elif pat == 'sustain':
                seq = [(0, L, root)]
                if sec.energy > .75:
                    seq = [(0, L, root), (0, L, root - 12 if root - 12 >= 28 else root)]
            elif pat == 'lofi':
                seq = [(0, 1.25, root), (1.5, .5, root), (2, 1, fifth), (3.5, .5, approach)] if bi % 2 == 0 else \
                      [(0, 1.5, root), (1.75, .25, root + 12 if root + 12 <= 55 else root), (2.5, 1, fifth), (3.5, .5, approach)]
            elif pat == 'oompah_bass':
                seq = [(0, .4, root), (2, .4, fifth)] + ([(3.5, .3, approach)] if bi % 2 else [])
            else:
                seq = [(0, L, root)]
            for o, d, p in seq:
                while p > tr.hi:
                    p -= 12
                while p < tr.lo:
                    p += 12
                if o < L - 1e-6:
                    tr.add(s0 + o, min(d, L - o), p, _vel(sec, -2 + (o == 0) * 6, 4, rng))


def pattern_ostinato(score, tr, part, sec, rng, bars):
    """弦の刻み。型は今のところ 'spiccato'（8分で根音・5度・8度を行き来し、3+3+2 で強める）だけ。"""
    if part.get('pattern', 'spiccato') != 'spiccato':
        raise ValueError(f"刻みの型 {part.get('pattern')} は無い（使えるのは 'spiccato'）")
    for bi, (b0, b1) in enumerate(bars):
        for span in chord_spans_in(score, b0, b1):
            s0, s1 = max(span.start, b0), min(span.end, b1)
            r = nearest_pitch(span.chord.root, 52)
            f = r + 7 if r + 7 <= 64 else r - 5
            third = nearest_pitch(span.chord.third_pc, r + 4)
            cells = [[r, r, f, r + 12, r, f, r, f], [r, r + 12, f, r, third, f, r, r + 12], [r, f, r + 12, f, r, f, third, f]]
            cell = cells[bi % len(cells)]
            step = 0.25 if sec.energy > 0.85 else 0.5
            t, k = s0, 0
            while t < s1 - 1e-6:
                o = (t - b0)
                acc = 1 if o % 1.5 < 1e-6 or abs(o - 3) < 1e-6 else 0
                tr.add(t, step * 0.6, cell[k % len(cell)], _vel(sec, -12 + acc * 14, 3, rng))
                t += step
                k += 1


def pattern_pad(score, tr, part, sec, rng, bars):
    reg = part.get('register', 0)
    lo, hi = max(tr.lo, 48 + reg), min(tr.hi, 79 + reg)
    for r0, r1 in _runs(bars):
        for span in chord_spans_in(score, r0, r1):
            s0, s1 = max(span.start, r0), min(span.end, r1)
            v = voicing(score, tr, span, lo, hi, 4)
            for k, p in enumerate(v):
                tr.add(s0, s1 - s0 + 0.04, p, _vel(sec, -14 + (k == 3) * 4, 3, rng))


def pattern_counter(score, tr, part, sec, rng, bars):
    """対旋律：和音の3度か7度（導く音）を選んで、旋律が伸ばす所で動く。"""
    lo, hi = tr.lo + 5, min(tr.hi, tr.lo + 24)
    prev = (lo + hi) // 2
    mel = [n for n in score.melody_line if sec.start <= n[0] < sec.end]
    spans = [(span, r0, r1) for r0, r1 in _runs(bars) for span in chord_spans_in(score, r0, r1)]
    for span, r0, r1 in spans:
        s0, s1 = max(span.start, r0), min(span.end, r1)
        cands = [span.chord.third_pc] + ([span.chord.seventh_pc] if span.chord.seventh_pc is not None else []) + [(span.chord.root + 7) % 12]
        p = min((nearest_pitch(pc, prev) for pc in cands), key=lambda x: abs(x - prev))
        while p > hi:
            p -= 12
        while p < lo:
            p += 12
        mel_busy = sum(1 for n in mel if s0 <= n[0] < s1)
        if mel_busy <= 2 and s1 - s0 >= 2:
            # 旋律が休むので、対旋律が経過音で動く
            nxt = score.next_chord(span)
            target = nearest_pitch(nxt.chord.third_pc, p) if nxt else p
            mid = p + (1 if target > p else -1) * (2 if abs(target - p) > 2 else 1)
            tr.add(s0, (s1 - s0) / 2, p, _vel(sec, -10, 3, rng))
            tr.add(s0 + (s1 - s0) / 2, (s1 - s0) / 2, mid if span.key.in_scale(mid) else p, _vel(sec, -12, 3, rng))
        else:
            tr.add(s0, s1 - s0, p, _vel(sec, -12, 3, rng))
        prev = p


def pattern_timp(score, tr, part, sec, rng, bars):
    for bi, (b0, b1) in enumerate(bars):
        span = score.chord_at(b0)
        r = nearest_pitch(span.chord.root, 45)
        while r > tr.hi:
            r -= 12
        while r < tr.lo:
            r += 12
        if bi % 2 == 0 or sec.energy > .85:
            tr.add(b0, 1, r, _vel(sec, 0, 4, rng))
        if sec.energy > .85 and b1 - b0 >= 4:
            tr.add(b0 + 2.5, .5, r, _vel(sec, -10, 4, rng))
            tr.add(b0 + 3, 1, r + 7 if r + 7 <= tr.hi else r - 5, _vel(sec, -4, 4, rng))


D = styles.DRUM


def drum_bar(pat, e, i, L, rng, fill=False, first=False):
    """1小節ぶんの打楽器：[(拍, 音, 強さの増減)]"""
    out = []
    if pat == 'pop':
        if e < 0.55:
            out += [(0, D['kick'], 0), (2, D['side'], -10)] + [(q, D['hat'], -18) for q in range(4)]
            if i % 2:
                out += [(2.5, D['kick'], -12)]
        else:
            # 大太鼓は2小節で一回りする型。前は4通りを小節ごとに回していて、3小節の区切り（6秒の場面）では毎回2小節めに 2.75 拍の跳ねと強いスネアが来て、
            # 区切りの途中で「上がる」ように聞こえた（2026-10-27 の指摘「21秒の音が上がる感じが、曲全体の周期性やリズムの観点からよくわからん」）。跳ねは区切りの終わりのフィル（fill）に任せる
            kicks = [[0, 2.5], [0, 1.5, 2.5]][i % 2]
            out += [(k, D['kick'], 0) for k in kicks] + [(1, D['snare'], 4), (3, D['snare'], 4)]
            step = .25 if e > 0.85 else .5
            n = int(4 / step)
            out += [(k * step, D['hat'], -10 - (k % 2) * 10 + (k % (2 if step == .5 else 4) == 0) * 4) for k in range(n)]
            if i % 2:
                out = [x for x in out if not (x[1] == D['hat'] and abs(x[0] - 3.5) < 1e-6)] + [(3.5, D['hat_open'], -10)]
            if e > .8:
                out += [(1, D['clap'], -6), (3, D['clap'], -6)]
            if rng.random() < .35:
                out += [(rng.choice([1.75, 2.25, 3.75]), D['snare'], -30)]  # ゴースト
    elif pat == 'brush':
        out += [(0, D['kick'], -10), (2, D['kick'], -16)] + [(k * .5, D['snare'], -34 + (k % 2) * 4) for k in range(8)]
        out += [(1, D['snare'], -12), (3, D['snare'], -12), (1, D['hat_pedal'], -18), (3, D['hat_pedal'], -18)]
        if e > .7:
            out += [(k, D['ride'], -18) for k in range(4)] + [(k + .66, D['ride'], -26) for k in (1, 3)]
    elif pat == 'epic':
        out += [(0, D['bass_drum'], 6), (1.5, D['tom_lo'], -6), (2.5, D['tom_lo'], -10), (3, D['tom_mid'], -4)]
        if e > .85:
            out += [(.75, D['tom_mid'], -12), (2, D['bass_drum'], 0), (3.5, D['tom_hi'], -10), (3.75, D['tom_hi'], -12)]
        if i % 2:
            out += [(2, D['snare'], -6)]
    elif pat == 'lofi':
        variants = [[0, 1.75, 2.5], [0, 2.5], [0, .75, 2.5, 3.25], [0, 2.25, 2.5]]
        out += [(k, D['kick'], -2) for k in variants[i % 4]] + [(1, D['snare'], -4), (3, D['snare'], -2)]
        out += [(k * .5, D['hat'], -18 - (k % 2) * 8 + rng.randint(-4, 4)) for k in range(8)]
        if rng.random() < .4:
            out += [(rng.choice([1.5, 3.5, 3.75]), D['side'], -24)]
    elif pat == 'shaker':
        out += [(k * .25, D['shaker'], -28 + (k % 4 == 2) * 8 + (k % 2 == 0) * 3) for k in range(16)]
    elif pat == 'playful':
        out += [(.5 + k, D['woodblock'], -16 + (k % 2) * 6) for k in range(4)]
        if e > .6:
            out += [(1, D['clap'], -10), (3, D['clap'], -10)]
        if i % 2 == 0:
            out += [(0, D['triangle'], -14)]
    if fill and pat in ('pop', 'lofi', 'brush'):
        out = [x for x in out if x[0] < 3]
        tom = [D['snare'], D['snare'], D['tom_hi'], D['tom_mid'], D['tom_mid'], D['tom_lo'], D['tom_lo'], D['snare']] if pat == 'pop' else [D['snare']] * 4
        st = .5 if pat != 'pop' else .125
        for k in range(int(1 / st)):
            out.append((3 + k * st, tom[k % len(tom)], -14 + k * 3))
    if fill and pat == 'epic':
        out += [(3 + k * .125, D['snare'], -20 + k * 3) for k in range(8)]
    if first and pat in ('pop', 'epic', 'lofi') and e > 0.5:
        out += [(0, D['crash'], 0)]
    return [x for x in out if x[0] < L - 1e-6]


def pattern_drums(score, tr, part, sec, rng, bars, next_energy):
    pat = part.get('pattern', 'pop')
    for bi, (b0, b1) in enumerate(bars):
        last = bi == len(bars) - 1
        fill = last and next_energy is not None and next_energy >= sec.energy - 0.05 and pat != 'shaker'
        for o, note, dv in drum_bar(pat, sec.energy, bi, b1 - b0, rng, fill=fill, first=bi == 0):
            tr.add(b0 + o, .25, note, _vel(sec, dv, 3, rng))


# ── 4. きっかけ ─────────────────────────────────────

RHYTHM_FNS = ('drums', 'perc', 'bass', 'comp', 'arp', 'ostinato', 'timp')


def _cut(tr, a, b, cut_sustain=True):
    keep = []
    for n in tr.notes:
        if a - 1e-6 <= n.start < b - 1e-6:
            continue
        if cut_sustain and n.start < a and n.end > a:
            n.dur = max(0.05, a - n.start)
        keep.append(n)
    tr.notes = keep


def apply_cues(score, rng):
    sp = score.spec
    tempo = sp['tempo']
    for cue in sp['cues']:
        sec = next((s for s in sp['sections'] if s.start <= cue.at < s.end), sp['sections'][-1])
        span = score.chord_at(min(cue.at, sp['total_beats'] - 0.01))
        st = cue.strength
        len_sec = tempo.sec(cue.at + cue.length) - tempo.sec(cue.at)
        if cue.type == 'break':
            for tr in score.tracks.values():
                if tr.fn in RHYTHM_FNS:
                    _cut(tr, cue.at, cue.at + cue.length)
        elif cue.type == 'stop':
            for tr in score.tracks.values():
                _cut(tr, cue.at, cue.at + cue.length)
        elif cue.type == 'drop':
            for tr in score.tracks.values():
                if tr.fn in RHYTHM_FNS or tr.fn == 'melody':
                    _cut(tr, cue.at - cue.length, cue.at)
            _hit(score, cue.at, span, sec, st, heavy=False)
        elif cue.type in ('hit', 'impact'):
            _hit(score, cue.at, span, sec, st, heavy=cue.type == 'impact')
            if cue.type == 'impact':
                score.sfx.append(dict(type='impact', sec=cue.sec, len_sec=2.5, strength=st))
        elif cue.type == 'riser':
            a = cue.at - cue.length
            perc = cue_perc(score)
            n = int(cue.length / 0.125)
            for k in range(n):
                frac = k / max(1, n - 1)
                perc.add(a + k * 0.125, .125, D['ride'] if k % 2 else D['crash2'], 20 + 80 * frac ** 1.5 * st, 'cue')
                if frac > .5:
                    perc.add(a + k * 0.125, .125, D['snare'], 15 + 70 * frac ** 2 * st, 'cue')
            score.sfx.append(dict(type='riser', sec=tempo.sec(a), len_sec=len_sec if len_sec > 0 else tempo.sec(cue.at) - tempo.sec(a),
                                  strength=st, end_sec=cue.sec))
        elif cue.type == 'swell':
            a = cue.at - cue.length
            for tr in score.tracks.values():
                if tr.fn in ('pad', 'counter') or tr.family in ('bowed', 'choir', 'brass'):
                    steps = int(cue.length / 0.25)
                    for k in range(steps + 1):
                        tr.cc.append((a + k * .25, 11, int(40 + 87 * (k / max(1, steps)) ** 1.3)))
            score.sfx.append(dict(type='swell', sec=tempo.sec(a), len_sec=tempo.sec(cue.at) - tempo.sec(a), strength=st, end_sec=cue.sec))
        elif cue.type == 'chime':
            ch = score.track('cue_chime', 'glock', 'cue', -11)
            tones = sorted({nearest_pitch(pc, 84) for pc in span.chord.pcs})
            tones = tones + [tones[0] + 12]
            for k, p in enumerate(tones):
                ch.add(cue.at + k * 0.125, 1.5, min(105, p), 60 + 40 * st - k * 3, 'cue')
            cue_perc(score).add(cue.at, 1, D['triangle'], 50 + 40 * st, 'cue')
        elif cue.type == 'whoosh':
            score.sfx.append(dict(type='whoosh', sec=cue.sec, len_sec=max(0.5, len_sec), strength=st))
        elif cue.type == 'click':
            score.sfx.append(dict(type='click', sec=cue.sec, len_sec=0.15, strength=st))
        elif cue.type == 'accent':
            for tr in score.tracks.values():
                for n in tr.notes:
                    if abs(n.start - cue.at) < 0.06:
                        n.vel = min(127, int(n.vel + 25 * st))
                        n.tag = 'cue'
        score.log.append(f'きっかけ {cue.type} @ {cue.sec:.2f}秒（{sec.name}）{cue.note}')


def cue_perc(score):
    """きっかけ用の打楽器。様式のドラムと同じキットを使う（MIDI の10チャンネルは音色が1つだけ）。"""
    kit = next((p['inst'] for p in score.spec['style']['parts'] if styles.INSTRUMENTS[p['inst']].get('drum')), 'orch_perc')
    return score.track('cue_perc', kit, 'cue', -9)


def _hit(score, at, span, sec, st, heavy):
    hit = score.track('cue_hit', 'strings' if score.spec['style_name'] != 'lofi' else 'epiano', 'cue', -7)
    v = voice_chord(span.chord, None, 48, 76, 4)
    b = nearest_pitch(span.chord.root, 38)
    lows = [p for p in (b, b - 12) if p >= hit.lo] or [nearest_pitch(span.chord.root, hit.lo + 6)]
    for p in v + lows:
        hit.add(at, 0.6 if not heavy else 1.2, p, 80 + 45 * st, 'cue')
    perc = cue_perc(score)
    perc.add(at, 1, D['crash'], 70 + 50 * st, 'cue')
    perc.add(at, 1, D['bass_drum'], 80 + 45 * st, 'cue')
    if heavy:
        perc.add(at, 1, D['kick'], 90 + 35 * st, 'cue')
    timp = score.tracks.get('timp') or score.track('cue_timp', 'timpani', 'cue', -8)
    r = nearest_pitch(span.chord.root, 43)
    while r < 38:
        r += 12
    timp.add(at, 1.5, r, 85 + 40 * st, 'cue')


# ── 5〜8. 終止・強弱・同じ小節の解消・揺れ ─────────────────────

def apply_ending(score):
    sp = score.spec
    bb = sp['meter'].bar_beats
    last = sp['sections'][-1]
    fb = max(last.start, sp['total_beats'] - bb)
    ext = bb * 0.75   # 最後の和音を少し伸ばす（フェルマータ）
    span = score.chord_at(sp['total_beats'] - 0.01)
    for tr in score.tracks.values():
        if not tr.notes or tr.fn == 'cue':
            continue
        if tr.drum:
            tr.notes = [n for n in tr.notes if n.start < fb - 1e-6 or abs(n.start - fb) < 1e-3]
            if tr.fn == 'drums' and any(abs(n.start - fb) < 1e-3 for n in tr.notes):
                tr.add(fb, 1, D['crash'], 100)
            continue
        if tr.fn == 'melody' or tr.fn == 'double':
            for n in tr.notes:
                if n.end > fb + bb - 1.01:
                    n.dur = sp['total_beats'] + ext - n.start
            continue
        before = [n for n in tr.notes if n.start < fb - 1e-6]
        at_fb = [n for n in tr.notes if abs(n.start - fb) < 0.05]
        tr.notes = before
        if not before and not at_fb:
            continue
        if tr.fn in ('bass',):
            tr.add(fb, bb + ext, bass_pitch(span.chord, 31, 45), 92)
        elif tr.fn in ('timp',):
            r = nearest_pitch(span.chord.root, 45)
            tr.add(fb, bb + ext, r if r <= tr.hi else r - 12, 96)
        else:
            lo = max(tr.lo, 48)
            v = voice_chord(span.chord, getattr(tr, '_prev_v', None), lo, min(tr.hi, lo + 26), 4)
            roll = 0.05 if tr.family in ('pluck', 'keys') else 0
            for k, p in enumerate(v):
                tr.add(fb + k * roll, sp['total_beats'] + ext - fb - k * roll, p, 78)
            if tr.family == 'keys':
                tr.cc.append((fb + .05, 64, 127))
    score.end_beats = sp['total_beats'] + ext


def apply_dynamics(score):
    """持続する楽器（弦・金管・木管・合唱）の表情 CC11 を、区切りの勢いと句の山で描く。"""
    sp = score.spec
    secs = sp['sections']
    for tr in score.tracks.values():
        if tr.family not in ('bowed', 'brass', 'reed', 'flute', 'choir') or tr.fn == 'cue':
            continue
        has_swell = any(c[1] == 11 for c in tr.cc)
        pts = []
        for i, s in enumerate(secs):
            nxt = secs[i + 1].energy_at(secs[i + 1].start) if i + 1 < len(secs) else s.energy
            t = s.start
            while t < s.end - 1e-6:
                base = 62 + 55 * s.energy_at(t)
                ph = ((t - s.start) % 8) / 8   # 2小節の句の山
                v = base + 6 * math.sin(math.pi * ph)
                # 次の区切りが強ければ、最後の1小節でクレッシェンド
                left = s.end - t
                if nxt > s.energy + 0.1 and left <= 4:
                    v += (1 - left / 4) * max(0, 62 + 55 * nxt - base)
                if i == 0 and t - s.start < 2:
                    v *= 0.75 + 0.25 * (t - s.start) / 2
                pts.append((t, 11, int(max(30, min(127, v)))))
                t += 0.5
        if has_swell:
            sw = [c for c in tr.cc if c[1] == 11]
            ranges = []
            sw.sort()
            for c in sw:
                pts = [p for p in pts if abs(p[0] - c[0]) > 0.3]
            pts += sw
            tr.cc = [c for c in tr.cc if c[1] != 11]
        tr.cc += pts


def bar_signature(score, b0, b1):
    sig = []
    for tid, tr in score.tracks.items():
        for n in tr.notes:
            if b0 - 1e-6 <= n.start < b1 - 1e-6:
                sig.append((tid, n.pitch, round((n.start - b0) * 8), round(n.dur * 4)))
    return tuple(sorted(sig))


def dedupe_bars(score, rng):
    sp = score.spec
    bb = sp['meter'].bar_beats
    nb = int(math.ceil(sp['total_beats'] / bb - 1e-6))
    seen, changed, left = {}, 0, []
    for b in range(nb):
        b0, b1 = b * bb, min((b + 1) * bb, sp['total_beats'])
        for attempt in range(6):
            sig = bar_signature(score, b0, b1)
            if not sig or sig not in seen:
                break
            _vary_bar(score, b0, b1, attempt, rng)
            changed += 1
        else:
            left.append(b + 1)
        sig = bar_signature(score, b0, b1)
        if sig:
            seen.setdefault(sig, b + 1)
    score.log.append(f'同じ小節の解消：{changed} か所を変えた' + (f'。変えきれなかった小節 {left}' if left else ''))


def _vary_bar(score, b0, b1, attempt, rng):
    order = ['drums', 'perc', 'comp', 'arp', 'melody', 'pad', 'bass']
    fn = order[attempt % len(order)]
    for tr in score.tracks.values():
        if tr.fn != fn:
            continue
        ns = [n for n in tr.notes if b0 <= n.start < b1]
        if not ns:
            continue
        if tr.drum:
            t = b0 + rng.choice([0.75, 1.75, 2.25, 3.25, 3.75]) if b1 - b0 >= 4 else b0 + 0.75
            tr.add(t, .25, rng.choice([D['snare'], D['hat_open'], D['side'], D['tom_hi']]), 34)
        elif fn in ('comp', 'pad', 'arp'):
            top = max(ns, key=lambda n: n.pitch)
            if top.pitch - 12 >= tr.lo:
                top.pitch -= 12
            else:
                low = min(ns, key=lambda n: n.pitch)
                if low.pitch + 12 <= tr.hi:
                    low.pitch += 12
        elif fn == 'melody':
            longest = max(ns, key=lambda n: n.dur)
            if longest.dur >= 1:
                half = longest.dur / 2
                longest.dur = half
                tr.add(longest.start + half, half, longest.pitch + rng.choice([-2, 2, 1]), longest.vel - 8)
        elif fn == 'bass':
            ns[-1].pitch += 12 if ns[-1].pitch + 12 <= 55 else -12
        return


# ── 弾き方（演奏の表情）──────────────────────────────
# 2026-10-27 の指摘「音に柔らかさや連続感がない。エレクトーンをボンボン叩いてるんだろうなってかんじ。弾き方が下手」。
# 譜面（何をいつ鳴らすか）は変えずに、人が弾くときにやっていることを足す：
#   1. 旋律・ベースは次の音までつなぐ（レガート）。弦・管・声は少し重ねて、音の切れ目を作らない
#   2. ピアノ（鍵盤）は和音の替わり目でペダルを踏み替える（替わった直後に上げて、すぐ踏む）。前は替わり目の手前で上げていて、毎回音が途切れた
#   3. 打鍵の強さに、拍の重み（小節の頭が強く、裏が弱い）と、句の山（句の中ほどが強く、句の終わりは抜く）を付け、隣どうしの強さをならす
#   4. 伴奏は旋律より弱く、強さの幅も狭める（伴奏が叩きつけない）。和音は少しずらして鳴らす（ピアノの指が同時には落ちない）
#   5. ギターの刻みは次の刻みまで鳴らしっぱなし、弦の刻み（spiccato）も長めに
SUSTAIN_FAM = ('bowed', 'brass', 'reed', 'flute', 'choir')
LINE_FN = ('melody', 'double', 'counter', 'bass')
ACCOMP_FN = ('comp', 'arp', 'ostinato', 'pad')


def _metric_weight(start, bb):
    pos = start % bb
    if abs(pos) < 0.02 or abs(pos - bb) < 0.02:
        return 5
    if bb == 4 and abs(pos - 2) < 0.02:
        return 2
    if abs(pos - round(pos)) < 0.02:
        return 0
    if abs(pos * 2 - round(pos * 2)) < 0.04:
        return -4
    return -6


def _phrases(notes, gap=0.75, longest=8.0):
    """休み（gap 拍以上）か、長さ longest 拍で句に分ける。"""
    out, cur = [], []
    for n in notes:
        if cur and (n.start - cur[-1].end >= gap or n.start - cur[0].start >= longest):
            out.append(cur)
            cur = []
        cur.append(n)
    if cur:
        out.append(cur)
    return out


def perform(score, rng):
    bb = score.spec['meter'].bar_beats
    for tr in score.tracks.values():
        if tr.drum or tr.fn in ('cue', 'timp', 'drums', 'perc'):
            continue
        notes = sorted([n for n in tr.notes if n.tag != 'cue'], key=lambda n: (n.start, n.pitch))
        if not notes:
            continue
        # 3a. 拍の重み
        k = 0.5 if tr.fn == 'pad' else 1.0
        for n in notes:
            n.vel += int(round(_metric_weight(n.start, bb) * k))
        # 1. つなぐ（同じ時刻に重なる和音は1つの塊として、次の塊までつなぐ）
        # スタッカートと書いた旋律（playful のクラリネットなど）は切ったまま。つなぐと弾き方の指定が消える
        if tr.fn in LINE_FN and (tr.part or {}).get('articulation') != 'staccato':
            starts = sorted({round(n.start, 3) for n in notes})
            nxt = {a: b for a, b in zip(starts, starts[1:])}
            for n in notes:
                b = nxt.get(round(n.start, 3))
                if b is None:
                    continue
                ioi, gap = b - n.start, b - n.end
                if gap > max(0.5, ioi * 0.5):
                    continue   # 書かれた休み（句の切れ目）はそのまま
                if tr.fn == 'bass':
                    n.dur = max(n.dur, ioi - min(0.06, ioi * 0.1))
                elif tr.family in SUSTAIN_FAM:
                    n.dur = ioi + 0.08
                else:
                    n.dur = ioi + 0.03
            # 3b. 句の山：句の中ほどを強く、句の終わりの音は抜く。いちばん高い音は少し強く
            if tr.fn != 'bass':
                mono = [n for n in notes if n is max((m for m in notes if abs(m.start - n.start) < 1e-3), key=lambda m: m.pitch)]
                for ph in _phrases(mono):
                    L = len(ph)
                    top = max(m.pitch for m in ph)
                    for i, n in enumerate(ph):
                        x = (i + 0.5) / L
                        n.vel += int(round(8 * math.sin(math.pi * x ** 0.85) - 3))
                        if n.pitch == top and L > 2:
                            n.vel += 3
                    if L > 2:
                        ph[-1].vel -= 6
        # 4. 伴奏は旋律より弱く、幅も狭める。旋律をなぞる重ね（鉄琴など）とベースも旋律より前に出さない
        if tr.fn == 'double':
            tr.vel_cap = 78
            for n in notes:
                n.vel = int(min(78, 30 + (n.vel - 30) * 0.7))
        if tr.fn == 'melody' and tr.family in ('keys', 'epiano', 'pluck', 'mallet'):
            # 打って鳴らす楽器の旋律は 100 まで（それより強いと音源の硬い音が鳴る）
            tr.vel_cap = 100
            for n in notes:
                n.vel = int(min(100, n.vel if n.vel <= 90 else 90 + (n.vel - 90) * 0.5))
        if tr.fn == 'bass':
            tr.vel_cap = 92
            for n in notes:
                n.vel = int(min(92, 36 + (n.vel - 36) * 0.8))
        if tr.fn in ACCOMP_FN:
            cap = 88 if tr.family in ('keys', 'epiano', 'pluck') else 96
            tr.vel_cap = cap   # 揺らし（humanize）のあとにも効かせる
            for n in notes:
                n.vel = int(min(cap, 34 + (n.vel - 34) * 0.78))
        # 4c. 打つ楽器（鍵盤・撥弦・鉄琴）の旋律も強く叩きすぎない。強い打鍵は音源の硬い層が鳴り、角が立つ
        if tr.fn in ('melody', 'double') and tr.family in ('keys', 'epiano', 'pluck', 'mallet'):
            for n in notes:
                n.vel = int(min(100, 36 + (n.vel - 36) * 0.85))
        # 5. 刻みの長さ
        if tr.fn == 'comp' and tr.family == 'pluck':
            starts = sorted({round(n.start, 2) for n in notes})
            nxt = {a: b for a, b in zip(starts, starts[1:])}
            for n in notes:
                b = nxt.get(round(n.start, 2))
                span_end = score.chord_at(n.start).end
                n.dur = max(n.dur, min((b if b is not None else n.end) + 0.02, span_end + 0.05) - n.start)
        if tr.fn == 'ostinato':
            for n in notes:
                n.dur = n.dur / 0.6 * 0.85   # 刻みの間の 6 割 → 8.5 割
        # 4b. 和音を少しずらす（鍵盤・撥弦の伴奏。低い音から 8〜18ms ずつ）
        if tr.fn in ('comp',) and tr.family in ('keys', 'epiano'):
            tempo = score.spec['tempo']
            groups = {}
            for n in notes:
                groups.setdefault(round(n.start, 3), []).append(n)
            for t, g in groups.items():
                if len(g) < 3:
                    continue
                spb = 60.0 / tempo.bpm_at(t)
                for j, n in enumerate(sorted(g, key=lambda m: m.pitch)):
                    n.start += j * rng.uniform(0.008, 0.018) / spb
        # 3c. 隣どうしの強さをならす（同じ時刻の和音は1つとして数える）
        prev = None
        for n in notes:
            n.vel = int(max(1, min(127, n.vel if prev is None else round(0.7 * n.vel + 0.3 * prev))))
            prev = n.vel
        # 2. 鍵盤のペダル：和音の替わり目の直後に上げて、すぐ踏み直す（レガート・ペダル）
        if tr.family in ('keys', 'epiano'):
            tr.cc = [c for c in tr.cc if c[1] != 64]
            a0, a1 = notes[0].start, max(n.end for n in notes)
            for span in chord_spans_in(score, a0, a1):
                t = max(span.start, a0)
                tr.cc.append((t + 0.02, 64, 0))
                tr.cc.append((t + 0.12, 64, 127))
            tr.cc.append((a1 + 0.5, 64, 0))


def humanize(score, rng):
    sp = score.spec
    h = sp['humanize']
    tempo = sp['tempo']
    swing = float(h.get('swing', 0.5)) if any(s.feel == 'swing' for s in sp['sections']) else 0.5
    bias = {'drums': 0, 'perc': 2, 'bass': 3, 'comp': 4, 'arp': 2, 'pad': 18, 'counter': 10, 'melody': 6, 'double': 8,
            'ostinato': 2, 'timp': 2, 'cue': 0}
    for tr in score.tracks.values():
        for n in tr.notes:
            if n.tag == 'cue':
                continue
            sec_per_beat = 60.0 / tempo.bpm_at(n.start)
            # 跳ね：8分の裏を遅らせる
            if swing > 0.5:
                frac = (n.start % 1)
                if abs(frac - 0.5) < 0.03:
                    n.start += (swing - 0.5)
                elif abs(frac - 0.25) < 0.03 or abs(frac - 0.75) < 0.03:
                    n.start += (swing - 0.5) * 0.5
            sd = h['timing_ms'] * (0.4 if tr.drum else 0.6)
            ms = max(-2.5 * sd, min(2.5 * sd, rng.gauss(bias.get(tr.fn, 0) * (h['timing_ms'] / 12), sd)))
            if sp['style_name'] == 'lofi' and tr.drum and n.pitch in (D['snare'], D['side']):
                ms += 22   # ローファイの後ろに置くスネア
            n.start = max(0.0, n.start + ms / 1000.0 / sec_per_beat)
            n.vel = int(max(1, min(getattr(tr, 'vel_cap', 127), n.vel + rng.gauss(0, h['velocity'] * 0.5))))


# ── 全体 ──────────────────────────────────────────

FN_ORDER = ['melody', 'double', 'counter', 'pad', 'comp', 'arp', 'ostinato', 'bass', 'timp', 'drums', 'perc']


def parts_for(sec, style):
    lo_e, hi_e = sec.energy_at(sec.start), sec.energy_at(sec.end - 1e-6)
    parts = [p for p in style['parts'] if (p['e'][0] <= sec.energy < p['e'][1]) if p['fn'] == 'melody'] + \
            [p for p in style['parts'] if p['fn'] != 'melody' and p['e'][0] <= hi_e and lo_e < p['e'][1]]
    ins = sec.instruments
    if ins:
        if isinstance(ins, str):
            ins = ins.replace(',', ' ').split()
        explicit = [x for x in ins if not x.startswith(('+', '-'))]
        if explicit:
            # 名前はまず役の id で探し、合う id が無いときだけ楽器名で探す（strings が刻みの弦まで拾わないように）
            parts = []
            for x in explicit:
                parts += [p for p in style['parts'] if p['id'] == x] or [p for p in style['parts'] if p['inst'] == x]
            unknown = [x for x in explicit if not any(x in (p['id'], p['inst']) for p in style['parts'])]
            for x in unknown:
                if x not in styles.INSTRUMENTS:
                    raise ValueError(f'{sec.name}：楽器 {x} はこの様式にも楽器の一覧にも無い')
                parts.append(dict(id=x, inst=x, fn='pad', e=(0, 1.01), level=-10))
        for x in ins:
            if x.startswith('+'):
                name = x[1:]
                p = next((p for p in style['parts'] if name in (p['id'], p['inst'])), None)
                if p is None:
                    if name not in styles.INSTRUMENTS:
                        raise ValueError(f'{sec.name}：楽器 {name} が無い')
                    p = dict(id=name, inst=name, fn='pad', e=(0, 1.01), level=-10)
                if p not in parts:
                    parts.append(p)
            elif x.startswith('-'):
                parts = [p for p in parts if x[1:] not in (p['id'], p['inst'], p['fn'])]
    # 間（break）は、楽器の指定が無ければ打楽器を休ませる（静かな所で小物が刻み続けると耳につく）
    if sec.role == 'break' and not ins:
        parts = [p for p in parts if p['fn'] not in ('drums', 'perc')]
    # 旋律は1つの区切りに1つ
    mel = [p for p in parts if p['fn'] == 'melody']
    if len(mel) > 1:
        parts = [p for p in parts if p['fn'] != 'melody' or p is mel[-1]]
    return sorted(parts, key=lambda p: FN_ORDER.index(p['fn']))


def sec_fixed(sec, part):
    """区切りに楽器の名前をそのまま並べた指定（[piano, harp]）があれば、勢いで楽器を出し入れしない。
    足し引きだけ（[-drums]・[+choir]）のときは、盛り上げ（build）で楽器が小節ごとに入る流れを残し、+ で足した楽器だけ頭から鳴らす。
    前は足し引きでも出し入れを止めていて、宣伝ムービーの曲の「つかみ」（[-drums]）で、ベース・ギター・弦・鉄琴が6秒で一度に入った（2026-10-27 の指摘「7秒ぐらいからの音も気になる」）"""
    ins = sec.instruments
    if not ins:
        return False
    if isinstance(ins, str):
        ins = ins.replace(',', ' ').split()
    if any(not x.startswith(('+', '-')) for x in ins):
        return True
    return any(x.startswith('+') and x[1:] in (part['id'], part['inst']) for x in ins)


def _runs(bars):
    out = []
    for b0, b1 in bars:
        if out and abs(out[-1][1] - b0) < 1e-6:
            out[-1] = (out[-1][0], b1)
        else:
            out.append((b0, b1))
    return out


def compose(spec):
    rng = random.Random(spec['seed'])
    score = Score(spec)
    style = spec['style']
    build_harmony(score, rng)
    parts_by_sec = {s.index: parts_for(s, style) for s in spec['sections']}
    for s in spec['sections']:
        score.log.append(f'{s.name}：編成 ' + '、'.join(styles.INSTRUMENTS[p['inst']]['label'] + f'（{p["fn"]}）' for p in parts_by_sec[s.index]))
    melody = compose_melody(score, rng, parts_by_sec)
    bb = spec['meter'].bar_beats
    secs = spec['sections']
    for s in secs:
        bars = []
        t = s.start
        while t < s.end - 1e-6:
            bars.append((t, min(t + bb, s.end)))
            t += bb
        nxt = secs[s.index + 1].energy if s.index + 1 < len(secs) else None
        parts = parts_by_sec[s.index]
        bass_active = any(p['fn'] == 'bass' for p in parts)
        for p in parts:
            fn = p['fn']
            if fn != 'melody' and not sec_fixed(s, p):
                # 勢いが区切りの中で変わるとき（build）：その小節の勢いで鳴る楽器だけ
                pbars = [b for b in bars if p['e'][0] <= s.energy_at(b[0]) < p['e'][1]]
            else:
                pbars = bars
            if not pbars:
                continue
            tr = score.track(p['id'], p['inst'], p['fn'], p.get('level', -6), p)
            p = dict(p, _bass_active=bass_active)
            bars_all, bars = bars, pbars
            if fn == 'melody':
                art = p.get('articulation', 'legato' if tr.family in ('bowed', 'reed', 'flute', 'brass', 'choir') else 'normal')
                for (t0, d, pitch, sec, pos, lastbar) in melody:
                    if sec is not s:
                        continue
                    dur = d * (0.55 if art == 'staccato' and d <= 1 else (1.0 if art == 'legato' else 0.95))
                    tr.add(t0, dur, pitch, _vel(s, 2 + (pos == 2) * 6, 4, rng))
            elif fn == 'double':
                for (t0, d, pitch, sec, pos, lastbar) in melody:
                    if sec is not s or not any(b0 - 1e-6 <= t0 < b1 for b0, b1 in bars):
                        continue
                    pp = pitch + 12 * p.get('oct', 1)
                    while pp > tr.hi:
                        pp -= 12
                    while pp < tr.lo:
                        pp += 12
                    tr.add(t0, d * 0.95, pp, _vel(s, -6, 4, rng))
            elif fn == 'comp':
                pattern_comp(score, tr, p, s, rng, bars)
            elif fn == 'arp':
                pattern_arp(score, tr, p, s, rng, bars)
            elif fn == 'bass':
                pattern_bass(score, tr, p, s, rng, bars)
            elif fn == 'pad':
                pattern_pad(score, tr, p, s, rng, bars)
            elif fn == 'counter':
                pattern_counter(score, tr, p, s, rng, bars)
            elif fn == 'ostinato':
                pattern_ostinato(score, tr, p, s, rng, bars)
            elif fn == 'timp':
                pattern_timp(score, tr, p, s, rng, bars)
            elif fn in ('drums', 'perc'):
                pattern_drums(score, tr, p, s, rng, bars, nxt if fn == 'drums' else None)
            bars = bars_all
        if s.ramp:
            # 区切りの中のクレッシェンド（打鍵の強さ）
            for tr in score.tracks.values():
                for n in tr.notes:
                    if s.start <= n.start < s.end:
                        n.vel = int(max(1, min(127, n.vel * (1 + 0.9 * (s.energy_at(n.start) - s.energy)))))
    # ティンパニのロール：次の区切りが山場なら最後の拍で
    for s in secs[:-1]:
        nx = secs[s.index + 1]
        if nx.energy >= 0.8 and s.energy < nx.energy and 'timp' in score.tracks:
            tr = score.tracks['timp']
            r = nearest_pitch(score.chord_at(nx.start - 0.01).chord.root, 45)
            r = r if r <= tr.hi else r - 12
            for k in range(8):
                tr.add(nx.start - 1 + k * 0.125, .125, r, 40 + k * 9)
    apply_cues(score, rng)
    apply_ending(score)
    apply_dynamics(score)
    dedupe_bars(score, rng)
    perform(score, rng)
    humanize(score, rng)
    for tr in score.tracks.values():
        tr.notes.sort(key=lambda n: (n.start, n.pitch))
        tr.cc.sort()
    # MIDI のチャンネル：打楽器は 10（9）、ほかは順に。16 を超えたら同じ音色どうしで共有
    ch, used = 0, {}
    for tr in score.tracks.values():
        if tr.drum:
            tr.channel = 9
            continue
        if tr.program in used and ch >= 15:
            tr.channel = used[tr.program]
            continue
        if ch == 9:
            ch += 1
        tr.channel = min(ch, 15)
        used.setdefault(tr.program, tr.channel)
        ch += 1
    score.tracks = {k: v for k, v in score.tracks.items() if v.notes}
    return score
