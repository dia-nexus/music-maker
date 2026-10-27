"""曲の設計図（YAML か JSON）を読み、時刻と長さをそろえる。

時刻の書き方（cues の at、narration の from/to）：
  6 / 6.5 / "6s" / "6秒" / "0:06" / "0:06.5"     → 秒
  "bar 4" / "b4" / "4小節目" / "b4.3" / "bar 4 beat 3" / {bar: 4, beat: 3} → 小節（1始まり）と拍（1始まり）
長さの書き方（section の length、cue の length）：
  "2 bars" / "2小節" / "3 beats" / "3拍" / "1.5s" / "1.5秒" / 数字（秒）
section は length の代わりに bars / beats / seconds でも書ける。

内部の時間はすべて「拍」（4分音符=1）。秒への換算は TempoMap が持つ。
"""
import json
import math
import re

import yaml

from .theory import Key

# @couple:section-roles 役割を足したら styles.ROLE_ENERGY と SKILL.md の表と GUI の選択肢も
ROLE_ALIAS = {
    'intro': 'intro', 'つかみ': 'intro', 'イントロ': 'intro', 'hook': 'intro',
    'verse': 'verse', '紹介': 'verse', '機能紹介': 'verse', 'a': 'verse', 'aメロ': 'verse',
    'build': 'build', '盛り上げ': 'build', 'bメロ': 'build', 'pre': 'build',
    'chorus': 'chorus', 'サビ': 'chorus', '山場': 'chorus', 'climax': 'chorus',
    'break': 'break', '間': 'break', 'ブレイク': 'break',
    'bridge': 'bridge', '展開': 'bridge', 'cメロ': 'bridge',
    'outro': 'outro', '終わり': 'outro', 'エンディング': 'outro', 'ending': 'outro', 'logo': 'outro', 'ロゴ': 'outro',
}

# 気分の言葉 → 和声の性格。和声の選び方（styles.PROGRESSIONS のタグ）と対応する。
MOOD_WORDS = {
    'bright': ['明る', '楽し', 'わくわく', 'ワクワク', '元気', '爽', 'bright', 'happy', 'upbeat', '軽快', '期待'],
    'tender': ['やさし', '優し', 'やわらか', '温か', 'あたたか', '切な', 'tender', 'warm', 'nostalg', '懐か'],
    'tense': ['緊張', '不穏', 'ドキドキ', '謎', 'tense', 'dark', '問い', '悩', '困'],
    'epic': ['壮大', '高揚', '堂々', 'epic', 'heroic', '感動', '決め', '大団円', '最高潮'],
    'calm': ['穏やか', '静か', 'calm', 'chill', '落ち着', 'ゆった', '余韻'],
    'playful': ['遊び', 'かわい', '可愛', 'playful', 'cute', 'ポップ', '弾む'],
}


def mood_tag(text):
    t = str(text or '').lower()
    for tag, words in MOOD_WORDS.items():
        if any(w.lower() in t for w in words):
            return tag
    return None


class TempoMap:
    """拍 ↔ 秒。テンポは区間ごとの一定値の列（rit. は細かい区間に分けて表す）。"""

    def __init__(self, bpm):
        self.points = [(0.0, float(bpm))]  # (開始拍, bpm)

    def set(self, beat, bpm):
        self.points = [p for p in self.points if p[0] < beat - 1e-9] + [(float(beat), float(bpm))]

    def bpm_at(self, beat):
        bpm = self.points[0][1]
        for b, t in self.points:
            if b <= beat + 1e-9:
                bpm = t
        return bpm

    def sec(self, beat):
        s, pts = 0.0, self.points + [(math.inf, None)]
        for (b0, t), (b1, _) in zip(pts, pts[1:]):
            if beat <= b0:
                break
            s += (min(beat, b1) - b0) * 60.0 / t
        return s

    def beat(self, sec):
        s, pts = 0.0, self.points + [(math.inf, None)]
        for (b0, t), (b1, _) in zip(pts, pts[1:]):
            span = (b1 - b0) * 60.0 / t if b1 != math.inf else math.inf
            if sec <= s + span:
                return b0 + (sec - s) * t / 60.0
            s += span
        return 0.0


class Meter:
    def __init__(self, text='4/4'):
        m = re.match(r'^\s*(\d+)\s*/\s*(\d+)\s*$', str(text))
        if not m:
            raise ValueError(f'拍子が読めない：{text!r}（例：4/4, 3/4, 6/8）')
        self.num, self.den = int(m.group(1)), int(m.group(2))
        self.bar_beats = self.num * 4.0 / self.den   # 1小節の長さ（4分音符で）
        self.compound = self.den == 8 and self.num % 3 == 0
        self.pulse = 1.5 if self.compound else 4.0 / self.den  # 拍の単位（6/8 なら付点4分）
        self.pulses = int(round(self.bar_beats / self.pulse))

    def __str__(self):
        return f'{self.num}/{self.den}'


SEC_RE = re.compile(r'^\s*(-?\d+(?:\.\d+)?)\s*(s|sec|秒)?\s*$')
MMSS_RE = re.compile(r'^\s*(\d+):(\d+(?:\.\d+)?)\s*$')
BAR_RE = re.compile(r'^\s*(?:bar\s*|b|m)(\d+)(?:(?:\.|\s*beat\s*)(\d+(?:\.\d+)?))?\s*$', re.I)
BAR_JA_RE = re.compile(r'^\s*(\d+)\s*小節目?(?:\s*(\d+(?:\.\d+)?)\s*拍目?)?\s*$')
LEN_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*(bars?|小節|beats?|拍|s|sec|秒)?\s*$', re.I)


def parse_time(v, meter, tempo, where='時刻'):
    """時刻 → 拍。"""
    if isinstance(v, dict):
        bar = float(v.get('bar', 1))
        beat = float(v.get('beat', 1))
        return (bar - 1) * meter.bar_beats + (beat - 1) * meter.pulse
    if isinstance(v, (int, float)):
        return tempo.beat(float(v))
    s = str(v)
    m = SEC_RE.match(s)
    if m:
        return tempo.beat(float(m.group(1)))
    m = MMSS_RE.match(s)
    if m:
        return tempo.beat(int(m.group(1)) * 60 + float(m.group(2)))
    m = BAR_RE.match(s) or BAR_JA_RE.match(s)
    if m:
        bar = int(m.group(1))
        beat = float(m.group(2)) if m.group(2) else 1.0
        return (bar - 1) * meter.bar_beats + (beat - 1) * meter.pulse
    raise ValueError(f'{where}が読めない：{v!r}（例：6 / "6s" / "0:06" / "bar 4" / "b4.3" / "4小節目"）')


def parse_length(v, meter, tempo, at_beat, where='長さ'):
    """長さ → 拍。秒の長さは、その位置のテンポで拍に直す。"""
    if isinstance(v, (int, float)):
        return tempo.beat(tempo.sec(at_beat) + float(v)) - at_beat
    m = LEN_RE.match(str(v))
    if not m:
        raise ValueError(f'{where}が読めない：{v!r}（例："2 bars" / "2小節" / "3拍" / "1.5s"）')
    n, unit = float(m.group(1)), (m.group(2) or 's').lower()
    if unit.startswith('bar') or unit == '小節':
        return n * meter.bar_beats
    if unit.startswith('beat') or unit == '拍':
        return n * meter.pulse
    return tempo.beat(tempo.sec(at_beat) + n) - at_beat


class Section:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def energy_at(self, beat):
        """区切りの中の勢い。ramp があれば頭が低く終わりが高い（平均は energy）。"""
        frac = (beat - self.start) / max(1e-9, self.length)
        return max(0.0, min(1.0, self.energy + self.ramp * (frac - 0.5)))

    def __repr__(self):
        return f'<Section {self.name} {self.start:.0f}-{self.end:.0f}拍 {self.role} e={self.energy:.2f}>'


class Cue:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# @couple:cue-types きっかけの種類を足したら compose.apply_cues・sfx.py・SKILL.md の表・GUI の選択肢も
CUE_TYPES = {
    'hit': '全員で一撃（和音の打撃＋シンバル＋ティンパニ）',
    'impact': '低い衝撃音（hit より重い。低音のうなり＋大太鼓）',
    'break': '拍を落とす（リズム隊を length のあいだ抜く。和音の伸ばしは残す）',
    'stop': '全部止める（length のあいだ無音。残響だけ残る）',
    'drop': '溜めて戻る（直前 length を抜き、at で全員が戻る）',
    'riser': '上がっていく音（at に向かって length かけて盛り上げる。シンバルの連打＋ノイズ）',
    'swell': '和音の膨らみ（at に向かって弦や合唱をクレッシェンド＋逆再生シンバル）',
    'whoosh': '風切り音（画面の横移動に合わせる。at が真ん中）',
    'chime': 'きらめき（鉄琴の分散和音＋トライアングル）',
    'click': '軽い打音（ボタンを押す・文字が出る瞬間）',
    'accent': '強拍（その拍を強く打つ）',
}


def load(path_or_dict):
    if isinstance(path_or_dict, dict):
        raw = path_or_dict
    else:
        with open(path_or_dict, encoding='utf-8') as fp:
            text = fp.read()
        raw = json.loads(text) if str(path_or_dict).endswith('.json') else yaml.safe_load(text)
    return normalize(raw)


def normalize(raw):
    from . import styles  # 循環を避ける
    warnings = []
    style_name = raw.get('style', 'bright_pop')
    if style_name not in styles.STYLES:
        raise ValueError(f'様式が分からない：{style_name}（使えるもの：{", ".join(styles.STYLES)}）')
    style = styles.STYLES[style_name]
    meter = Meter(raw.get('meter', style.get('meter', '4/4')))
    bpm = float(raw.get('tempo', style['tempo']))
    tempo = TempoMap(bpm)
    key = Key.parse(raw.get('key', style['key']))
    seed = int(raw.get('seed', 1))

    sections = []
    beat = 0.0
    raw_secs = raw.get('sections') or []
    if not raw_secs:
        raise ValueError('sections が空。区切りを1つ以上書く')
    for i, rs in enumerate(raw_secs):
        name = str(rs.get('name', f'区切り{i + 1}'))
        if 'tempo' in rs:
            tempo.set(beat, float(rs['tempo']))
        if 'bars' in rs:
            length = float(rs['bars']) * meter.bar_beats
        elif 'beats' in rs:
            length = float(rs['beats']) * meter.pulse
        elif 'seconds' in rs:
            length = parse_length(float(rs['seconds']), meter, tempo, beat)
        elif 'length' in rs:
            length = parse_length(rs['length'], meter, tempo, beat, f'{name} の length')
        elif 'to' in rs:
            length = parse_time(rs['to'], meter, tempo, f'{name} の to') - beat
        else:
            raise ValueError(f'{name}：長さ（bars / seconds / length / to のどれか）が無い')
        # 拍にそろえる（半端は伸ばし縮みの少ない方へ）
        snapped = round(length / meter.pulse) * meter.pulse
        if abs(snapped - length) > 1e-6:
            warnings.append(f'{name}：長さ {length:.2f} 拍を拍の区切り {snapped:.0f} 拍にそろえた'
                            f'（{tempo.sec(beat + snapped) - tempo.sec(beat + length):+.2f} 秒ずれる）')
        length = max(meter.pulse, snapped)
        role_raw = str(rs.get('role', name)).lower()
        role = ROLE_ALIAS.get(role_raw) or next((v for k, v in ROLE_ALIAS.items() if k in role_raw), None)
        if role is None:
            role = 'intro' if i == 0 else ('outro' if i == len(raw_secs) - 1 else 'verse')
        mood = rs.get('mood', '')
        energy = rs.get('energy')
        if energy is None:
            energy = styles.ROLE_ENERGY[role]
        sec = Section(
            index=i, name=name, role=role, mood=mood, mood_tag=mood_tag(mood) or style.get('mood', 'bright'),
            energy=float(energy), start=beat, end=beat + length, length=length,
            bars=length / meter.bar_beats,
            key=Key.parse(rs['key']) if 'key' in rs else None,
            modulate=rs.get('modulate', 'pivot'),
            progression=rs.get('progression'), cadence=rs.get('cadence'),
            instruments=rs.get('instruments'), melody=rs.get('melody', True),
            feel=rs.get('feel', style.get('feel', 'straight')), rit=bool(rs.get('rit', False)),
            density=rs.get('density'), ramp=float(rs.get('ramp', styles.ROLE_RAMP.get(role, 0.0))),
            repeat=rs.get('repeat'),   # false なら、同じ役の前の区切りの和声と旋律を繰り返さず作り直す
        )
        sections.append(sec)
        beat += length
    total = beat
    # 盛り上げ（build）の頭は、すぐ前の区切りの勢いから大きく跳ばない：前より 0.12 だけ上から始め、終わり（頂点）はそのまま。
    # 静かな入り（0.25）の直後の盛り上げが 0.53 から始まり、ベースとギターが頭で一度に入って全体が 12dB 跳ねた（2026-10-27 の指摘「7秒ぐらいからの音も気になる」）
    for a, b in zip(sections, sections[1:]):
        if b.role == 'build' and b.ramp > 0 and 'ramp' not in (raw_secs[b.index] or {}):
            top = b.energy + b.ramp / 2
            head = min(b.energy - b.ramp / 2, a.energy_at(a.end - 1e-6) + 0.12)
            b.energy, b.ramp = (top + head) / 2, top - head
    # 調：書いていない区切りは前の調を引き継ぐ
    cur = key
    for s in sections:
        s.key = s.key or cur
        cur = s.key
    # 終わりの rit.（区切りに rit: true、または様式の既定で最後の区切り）
    last = sections[-1]
    if last.rit or raw.get('ending_rit', style.get('ending_rit', True)):
        last.rit = True
        rit_beats = min(last.length, meter.bar_beats * 2)
        start = last.end - rit_beats
        base = tempo.bpm_at(start)
        steps = int(rit_beats / 0.25)
        for k in range(steps):
            b = start + k * 0.25
            tempo.set(b, base * (1 - 0.18 * ((k + 1) / steps) ** 1.6))

    cues = []
    for j, rc in enumerate(raw.get('cues') or []):
        t = rc.get('type', 'hit')
        if t not in CUE_TYPES:
            raise ValueError(f'きっかけの種類が分からない：{t}（使えるもの：{", ".join(CUE_TYPES)}）')
        at = parse_time(rc.get('at'), meter, tempo, f'cues[{j}] の at')
        default_len = {'break': meter.bar_beats, 'stop': meter.pulse * 2, 'drop': meter.pulse * 2,
                       'riser': meter.bar_beats * 2, 'swell': meter.bar_beats}.get(t, meter.pulse)
        length = parse_length(rc['length'], meter, tempo, at, f'cues[{j}] の length') if 'length' in rc else default_len
        snap = rc.get('snap', 'exact' if t in ('whoosh', 'click') else '16th')
        grid = {'beat': meter.pulse, '8th': 0.5, '16th': 0.25, 'bar': meter.bar_beats}.get(snap)
        if grid:
            snapped = round(at / grid) * grid
            if abs(tempo.sec(snapped) - tempo.sec(at)) > 0.03:
                warnings.append(f'cues[{j}] {t}：{tempo.sec(at):.2f}秒 を拍の格子にそろえて {tempo.sec(snapped):.2f}秒 にした'
                                f'（ずらしたくないときは snap: exact）')
            at = snapped
        if at < 0 or at > total + 1e-6:
            raise ValueError(f'cues[{j}]：{tempo.sec(at):.2f}秒 は曲の外（曲は {tempo.sec(total):.2f}秒）')
        cues.append(Cue(type=t, at=at, length=length, strength=float(rc.get('strength', 0.8)),
                        note=rc.get('note', ''), snap=snap, sec=tempo.sec(at)))
    cues.sort(key=lambda c: c.at)

    narration = []
    for rn in raw.get('narration') or []:
        a = parse_time(rn.get('from'), meter, tempo, 'narration の from')
        b = parse_time(rn.get('to'), meter, tempo, 'narration の to')
        narration.append({'from': tempo.sec(a), 'to': tempo.sec(b), 'duck_db': float(rn.get('duck', -7))})

    mix = {'loudness': -14.0, 'true_peak': -1.0}
    mix.update(raw.get('mix') or {})
    human = {'timing_ms': 12.0, 'velocity': 8.0}
    human.update(style.get('humanize', {}))
    human.update(raw.get('humanize') or {})
    return {
        'title': raw.get('title', '無題'), 'style_name': style_name, 'style': style,
        'meter': meter, 'tempo': tempo, 'bpm': bpm, 'key': key, 'seed': seed,
        'sections': sections, 'cues': cues, 'narration': narration,
        'mix': mix, 'humanize': human, 'total_beats': total, 'total_sec': tempo.sec(total) + 0.0,
        'sample_rate': int(raw.get('sample_rate', 48000)), 'tail_sec': float(raw.get('tail', 2.5)),
        'warnings': warnings, 'raw': raw,
    }
