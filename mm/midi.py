"""Score → 標準 MIDI ファイル（全体1本と、楽器ごと1本ずつ）。"""
import mido

TPB = 480


def _txt(s):
    """MIDI の文字は latin-1 しか書けないので、UTF-8 のバイト列をそのまま入れる（DAW の多くは UTF-8 で読む）。"""
    return str(s).encode('utf-8').decode('latin-1')


def _tempo_events(spec):
    out = []
    for beat, bpm in spec['tempo'].points:
        out.append((int(round(beat * TPB)), mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(bpm), time=0)))
    return out


def _track_events(tr):
    ch = tr.channel
    ev = [(0, mido.MetaMessage('track_name', name=tr.id, time=0))]
    if tr.drum:
        ev.append((0, mido.Message('program_change', channel=ch, program=tr.program, time=0)))
    else:
        ev.append((0, mido.Message('control_change', channel=ch, control=0, value=0, time=0)))
        ev.append((0, mido.Message('program_change', channel=ch, program=tr.program, time=0)))
    ev.append((0, mido.Message('control_change', channel=ch, control=7, value=100, time=0)))
    ev.append((0, mido.Message('control_change', channel=ch, control=10, value=int(max(0, min(127, 64 + tr.pan * 63))), time=0)))
    ev.append((0, mido.Message('control_change', channel=ch, control=91, value=0, time=0)))
    ev.append((0, mido.Message('control_change', channel=ch, control=93, value=0, time=0)))
    if not any(c[1] == 11 for c in tr.cc):
        ev.append((0, mido.Message('control_change', channel=ch, control=11, value=110, time=0)))
    for beat, ctl, val in tr.cc:
        ev.append((int(round(beat * TPB)), mido.Message('control_change', channel=ch, control=ctl, value=int(max(0, min(127, val))), time=0)))
    # 同じ高さの音が重なると片方が切れるので、前の音を次の音の手前で止める
    last_end = {}
    notes = sorted(tr.notes, key=lambda n: (n.start, n.pitch))
    for n in notes:
        on = int(round(n.start * TPB))
        off = int(round(n.end * TPB))
        prev = last_end.get(n.pitch)
        if prev is not None and prev[1] > on:
            prev[0].time_off = max(prev[0].time_on + 1, on - 1)
        rec = _N(on, max(on + 1, off), n.pitch, n.vel)
        last_end[n.pitch] = (rec, rec.time_off)
        ev.append(rec)
    out = []
    for item in ev:
        if isinstance(item, _N):
            out.append((item.time_on, mido.Message('note_on', channel=ch, note=item.pitch, velocity=item.vel, time=0)))
            out.append((item.time_off, 'off', item))
        else:
            out.append(item)
    fixed = []
    for x in out:
        if len(x) == 3:
            item = x[2]
            fixed.append((item.time_off, mido.Message('note_off', channel=ch, note=item.pitch, velocity=0, time=0)))
        else:
            fixed.append(x)
    return fixed


class _N:
    __slots__ = ('time_on', 'time_off', 'pitch', 'vel')

    def __init__(self, a, b, p, v):
        self.time_on, self.time_off, self.pitch, self.vel = a, b, p, v


def _to_track(events):
    # 同じ時刻なら note_off を先に
    events.sort(key=lambda e: (e[0], 0 if (hasattr(e[1], 'type') and e[1].type == 'note_off') else 1))
    t = mido.MidiTrack()
    now = 0
    for tick, msg in events:
        msg = msg.copy(time=max(0, tick - now))
        now = max(now, tick)
        t.append(msg)
    return t


def conductor(spec, score=None):
    m = spec['meter']
    ev = [(0, mido.MetaMessage('track_name', name=_txt(spec['title']), time=0)),
          (0, mido.MetaMessage('time_signature', numerator=m.num, denominator=m.den, time=0))]
    ev += _tempo_events(spec)
    for s in spec['sections']:
        ev.append((int(round(s.start * TPB)), mido.MetaMessage('marker', text=_txt(s.name), time=0)))
    for c in spec['cues']:
        ev.append((int(round(c.at * TPB)), mido.MetaMessage('cue_marker', text=_txt(f'{c.type} {c.note}'.strip()), time=0)))
    return _to_track(ev)


def write_full(score, path):
    mf = mido.MidiFile(type=1, ticks_per_beat=TPB)
    mf.tracks.append(conductor(score.spec, score))
    for tr in score.tracks.values():
        mf.tracks.append(_to_track(_track_events(tr)))
    mf.save(path)


def write_single(score, tr, path):
    mf = mido.MidiFile(type=1, ticks_per_beat=TPB)
    mf.tracks.append(conductor(score.spec, score))
    mf.tracks.append(_to_track(_track_events(tr)))
    mf.save(path)
