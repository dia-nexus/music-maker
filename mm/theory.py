"""音楽理論の部品：調、音階、ローマ数字の和音、声部の進め方。

ここは音を鳴らさない。数（MIDI の音の高さ、ピッチクラス）だけを扱う。
"""
import itertools
import re

NOTE_PC = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
PC_NAME_SHARP = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
PC_NAME_FLAT = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']

# @couple:modes 旋法を足したら編集画面の選択肢（editor.html）と SKILL.md の key の書き方も
MODES = {
    'major': [0, 2, 4, 5, 7, 9, 11],
    'minor': [0, 2, 3, 5, 7, 8, 10],
    'dorian': [0, 2, 3, 5, 7, 9, 10],
    'mixolydian': [0, 2, 4, 5, 7, 9, 10],
    'lydian': [0, 2, 4, 6, 7, 9, 11],
}
MODE_ALIAS = {'maj': 'major', 'min': 'minor', 'm': 'minor', '長調': 'major', '短調': 'minor'}


def parse_pc(name):
    name = name.strip()
    pc = NOTE_PC[name[0].upper()]
    for ch in name[1:]:
        if ch in '#♯':
            pc += 1
        elif ch in 'b♭':
            pc -= 1
    return pc % 12


class Key:
    """調。tonic はピッチクラス、mode は MODES の名前。"""

    def __init__(self, tonic, mode='major'):
        self.tonic = tonic % 12
        self.mode = mode
        self.scale = MODES[mode]

    @classmethod
    def parse(cls, text):
        m = re.match(r'^\s*([A-Ga-g][#b♯♭]?)\s*(\w+)?\s*$', str(text))
        if not m:
            raise ValueError(f'調が読めない：{text!r}（例：D major / A minor / F# dorian）')
        mode = (m.group(2) or 'major').lower()
        mode = MODE_ALIAS.get(mode, mode)
        if mode not in MODES:
            raise ValueError(f'旋法が分からない：{mode}（使えるもの：{", ".join(MODES)}）')
        return cls(parse_pc(m.group(1)), mode)

    @property
    def minorish(self):
        return self.scale[2] == 3

    def name(self):
        names = PC_NAME_FLAT if self.tonic in (5, 10, 3, 8, 1) else PC_NAME_SHARP
        return f'{names[self.tonic]} {self.mode}'

    def transpose(self, semis):
        return Key(self.tonic + semis, self.mode)

    def degree_pc(self, deg):
        """音階の度数（0始まり、負や7以上も可）→ ピッチクラス。"""
        octave, d = divmod(deg, 7)
        return (self.tonic + self.scale[d]) % 12

    def degree_pitch(self, deg, base_octave_pitch):
        """度数 → MIDI の高さ。base_octave_pitch はその調の主音の高さ（例：D4=62）。"""
        octave, d = divmod(deg, 7)
        return base_octave_pitch + 12 * octave + self.scale[d]

    def pitch_to_degree(self, pitch, base_octave_pitch):
        """MIDI の高さ → いちばん近い度数（音階外の音は下の度数に寄せる）。"""
        rel = pitch - base_octave_pitch
        octave, r = divmod(rel, 12)
        best = max(i for i, s in enumerate(self.scale) if s <= r)
        return octave * 7 + best

    def in_scale(self, pitch):
        return (pitch - self.tonic) % 12 in self.scale


# 和音の種類 → 根音からの音程
QUALITY = {
    'maj': [0, 4, 7], 'min': [0, 3, 7], 'dim': [0, 3, 6], 'aug': [0, 4, 8],
    'sus2': [0, 2, 7], 'sus4': [0, 5, 7],
}
EXT = {
    '': [], '7': [10], 'maj7': [11], 'm7': [10], '6': [9], '9': [10, 14], 'maj9': [11, 14],
    'add9': [14], 'm9': [10, 14], 'ø7': [10], 'o7': [9], '11': [10, 14, 17], 'sus4': [], 'sus2': [],
}
ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII']
ROMAN_RE = re.compile(
    r'^(?P<acc>[b#♭♯]?)(?P<num>VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)'
    r'(?P<q>°|o|ø|\+|dim|aug)?(?P<ext>maj9|maj7|add9|sus2|sus4|m9|m7|7|9|11|6)?'
    r'(?:/(?P<slash>[b#]?(?:VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)|[1357]))?$')


class Chord:
    def __init__(self, root, intervals, symbol='', bass_interval=0, degree=None):
        self.root = root % 12
        self.intervals = intervals
        self.symbol = symbol
        self.bass_interval = bass_interval  # 0=根音、それ以外は転回（3度・5度を最低音に）
        self.degree = degree

    @property
    def pcs(self):
        return sorted({(self.root + i) % 12 for i in self.intervals})

    @property
    def triad_pcs(self):
        return [(self.root + i) % 12 for i in self.intervals[:3]]

    @property
    def bass_pc(self):
        return (self.root + self.bass_interval) % 12

    @property
    def third_pc(self):
        return (self.root + self.intervals[1]) % 12

    @property
    def seventh_pc(self):
        sev = [i for i in self.intervals if i in (9, 10, 11)]
        return (self.root + sev[0]) % 12 if sev else None

    def name(self):
        names = PC_NAME_FLAT if self.root in (10, 3, 8) else PC_NAME_SHARP
        iv = set(i % 12 for i in self.intervals)
        q = ''
        if 3 in iv and 6 in iv:
            q = 'dim'
        elif 3 in iv:
            q = 'm'
        elif 8 in iv and 4 in iv:
            q = 'aug'
        elif 5 in iv and 4 not in iv and 3 not in iv:
            q = 'sus4'
        elif 2 in iv and 4 not in iv and 3 not in iv:
            q = 'sus2'
        if 11 in iv:
            q += 'maj7'
        elif 10 in iv:
            q += '7'
        elif 9 in iv and q != 'dim':
            q += '6'
        if 2 in iv and q not in ('sus2',) and 'sus2' not in q:
            q += '(9)'
        base = names[self.root] + q
        if self.bass_interval:
            bn = names[self.bass_pc]
            base += '/' + bn
        return base

    def __repr__(self):
        return f'<{self.symbol or self.name()}>'


def parse_roman(sym, key):
    """ローマ数字（例：I, vi7, V/V, bVII, iv, ii°, IVmaj7, V/3）を Chord にする。

    根音は調の音階から取り、大文字なら長三和音、小文字なら短三和音。
    V/V のように / の後がローマ数字なら、その度数の調の和音（副属和音）。
    / の後が数字（3, 5, 7）なら転回（その音を最低音に）。
    """
    s = sym.strip()
    m = ROMAN_RE.match(s)
    if not m:
        raise ValueError(f'和音の記号が読めない：{sym!r}（例：I, vi, V7, IVmaj7, V/V, bVII, ii°）')
    acc, num, q, ext, slash = m.group('acc'), m.group('num'), m.group('q'), m.group('ext') or '', m.group('slash')
    bass_iv = 0
    k = key
    if slash and not slash.isdigit():
        target = parse_roman(slash, key)
        k = Key(target.root, 'minor' if target.intervals[1] == 3 else 'major')
    deg = ROMAN.index(num.upper())
    root = k.tonic + k.scale[deg]
    if acc in ('b', '♭'):
        root -= 1
    elif acc in ('#', '♯'):
        root += 1
    upper = num.isupper()
    if q in ('°', 'o', 'dim'):
        tri = 'dim'
    elif q in ('+', 'aug'):
        tri = 'aug'
    elif q == 'ø':
        tri = 'dim'
    elif ext in ('sus2', 'sus4'):
        tri = ext
    else:
        tri = 'maj' if upper else 'min'
    iv = list(QUALITY[tri])
    if q == 'ø':
        iv += [10]
    elif q in ('°', 'o') and ext == '7':
        iv += [9]
        ext = ''
    elif ext == '7' and upper:
        iv += [10]           # 属七
    elif ext == '7' and not upper:
        iv += [10]           # 短七
    elif ext:
        iv += EXT[ext]
    if slash and slash.isdigit():
        bass_iv = {'1': 0, '3': iv[1], '5': iv[2], '7': iv[3] if len(iv) > 3 else iv[2]}[slash]
    return Chord(root, iv, sym, bass_iv, degree=deg)


def nearest_pitch(pc, target):
    """ピッチクラス pc のうち target にいちばん近い MIDI の高さ。"""
    base = target - ((target - pc) % 12)
    return base if target - base <= 6 else base + 12


def voice_chord(chord, prev, lo, hi, n=4):
    """和音を n 声に配る。前の配置 prev から動きが少なく、平行5度・8度と声の交差を避ける。

    返り値は低い順の MIDI の高さのリスト。いちばん下の声は lo〜hi の範囲の下寄りに置く。
    """
    pcs = chord.pcs
    if prev is None:
        # 最初は真ん中あたりに密集配置
        center = (lo + hi) // 2
        start = nearest_pitch(pcs[0], center - 5)
        out, p = [], start
        tones = [(chord.root + i) % 12 for i in chord.intervals]
        while len(out) < n:
            pc = tones[len(out) % len(tones)]
            p = p if not out else out[-1] + 1
            while p % 12 != pc:
                p += 1
            out.append(p)
        return out
    # 各声の候補：和音の構成音で、前の高さから ±7 半音以内
    cands = []
    for pv in prev:
        cs = [p for p in range(max(lo, pv - 7), min(hi, pv + 7) + 1) if p % 12 in pcs]
        if not cs:   # 前の音が音域の外：音域の中で一番近い構成音
            cs = sorted((p for p in range(lo, hi + 1) if p % 12 in pcs), key=lambda p: abs(p - pv))[:2]
        cands.append(cs or [nearest_pitch(pcs[0], pv)])
    best, best_cost = None, 1e9
    essential = {chord.root % 12, chord.third_pc}
    if chord.seventh_pc is not None:
        essential.add(chord.seventh_pc)
    for combo in itertools.product(*cands):
        if any(combo[i] >= combo[i + 1] for i in range(len(combo) - 1)):
            continue  # 交差・同音は避ける
        used = {p % 12 for p in combo}
        if not essential <= used:
            continue
        cost = sum(abs(a - b) for a, b in zip(combo, prev))
        # 平行5度・8度（同じ向きに動いて、同じ完全音程が続く）
        for i in range(len(combo)):
            for j in range(i + 1, len(combo)):
                a0, b0, a1, b1 = prev[i], prev[j], combo[i], combo[j]
                if a0 == a1:
                    continue
                if (b0 - a0) % 12 in (0, 7) and (b1 - a1) % 12 == (b0 - a0) % 12 and (a1 - a0) * (b1 - b0) > 0:
                    cost += 8
        # 上の声どうしが1オクターブ以上離れない
        for i in range(1, len(combo) - 1):
            if combo[i + 1] - combo[i] > 12:
                cost += 4
        # 導音（主和音の半音下）の重複を避ける
        if [p % 12 for p in combo].count(chord.third_pc) > 1:
            cost += 2
        if cost < best_cost:
            best, best_cost = list(combo), cost
    if best is None:
        return voice_chord(chord, None, lo, hi, n)
    return best


def chord_from_pcs(pcs, bass=None):
    """ピッチクラスの集まり（重みつき dict か list）から和音名を推定する。確かめ（check）で使う。

    bass（最低音のピッチクラス）があれば根音の手がかりにする。上の4音だけでは Fmaj7 と Am7 が同じになるため。
    """
    if isinstance(pcs, dict):
        weights = pcs
    else:
        weights = {}
        for p in pcs:
            weights[p % 12] = weights.get(p % 12, 0) + 1
    if not weights:
        return None, 0.0
    total = sum(weights.values())
    best, best_score = None, -1
    templates = [('maj', [0, 4, 7]), ('min', [0, 3, 7]), ('dim', [0, 3, 6]), ('sus4', [0, 5, 7]),
                 ('7', [0, 4, 7, 10]), ('maj7', [0, 4, 7, 11]), ('m7', [0, 3, 7, 10])]
    for root in range(12):
        for qname, iv in templates:
            tones = {(root + i) % 12 for i in iv}
            hit = sum(w for p, w in weights.items() if p in tones)
            miss = sum(w for p, w in weights.items() if p not in tones)
            covered = sum(1 for t in tones if weights.get(t, 0) > 0) / len(tones)
            score = hit - 0.6 * miss + 0.3 * covered * total - (0.05 * total if len(iv) == 4 else 0)
            if bass is not None and root == bass % 12:
                score += 0.35 * total
            if score > best_score:
                best_score = score
                ivs = iv
                best = Chord(root, ivs)
    conf = max(0.0, min(1.0, best_score / total)) if total else 0
    return best, conf
