"""音楽づくりの道具のテスト。python3 -m unittest discover -s tools/music/tests"""
import math
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from mm import compose, spec, styles, theory  # noqa: E402
from mm.compose import D  # noqa: E402

EXAMPLE = os.path.join(HERE, 'examples', 'promo-60s.yaml')


def small(style='bright_pop', **kw):
    raw = dict(title='t', style=style, seed=3, sections=[
        dict(name='つかみ', bars=2, role='intro'), dict(name='紹介', bars=4, role='verse'),
        dict(name='盛り上げ', bars=4, role='build'), dict(name='山場', bars=4, role='chorus'),
        dict(name='終わり', bars=3, role='outro')])
    raw.update(kw)
    return spec.normalize(raw)


class Theory(unittest.TestCase):
    def test_key(self):
        k = theory.Key.parse('F# minor')
        self.assertEqual((k.tonic, k.mode), (6, 'minor'))
        self.assertEqual(theory.Key.parse('Bb').tonic, 10)
        with self.assertRaises(ValueError):
            theory.Key.parse('H major')

    def test_roman(self):
        C = theory.Key.parse('C major')
        self.assertEqual(theory.parse_roman('I', C).name(), 'C')
        self.assertEqual(theory.parse_roman('vi', C).name(), 'Am')
        self.assertEqual(theory.parse_roman('V7', C).name(), 'G7')
        self.assertEqual(theory.parse_roman('V/V', C).name(), 'D')
        self.assertEqual(theory.parse_roman('V7/vi', C).name(), 'E7')
        self.assertEqual(theory.parse_roman('bVII', C).name(), 'Bb')
        self.assertEqual(theory.parse_roman('IVmaj7', C).name(), 'Fmaj7')
        self.assertEqual(theory.parse_roman('I/3', C).name(), 'C/E')
        Am = theory.Key.parse('A minor')
        self.assertEqual(theory.parse_roman('i', Am).name(), 'Am')
        self.assertEqual(theory.parse_roman('V', Am).name(), 'E')     # 短調でも大文字なら長三和音
        self.assertEqual(theory.parse_roman('VI', Am).name(), 'F')

    def test_voice_leading_moves_little_and_avoids_parallels(self):
        C = theory.Key.parse('C major')
        prev = None
        moves = []
        for sym in ['I', 'vi', 'IV', 'V', 'I', 'ii', 'V7', 'I']:
            v = theory.voice_chord(theory.parse_roman(sym, C), prev, 48, 79, 4)
            self.assertEqual(v, sorted(v))
            self.assertTrue(all(48 <= p <= 79 for p in v))
            if prev:
                moves.append(sum(abs(a - b) for a, b in zip(v, prev)))
                # 外声（いちばん下と上）の平行5度・8度がない
                a0, b0, a1, b1 = prev[0], prev[-1], v[0], v[-1]
                if a0 != a1 and (a1 - a0) * (b1 - b0) > 0:
                    self.assertFalse((b0 - a0) % 12 in (0, 7) and (b1 - a1) % 12 == (b0 - a0) % 12, f'{sym} で平行')
            prev = v
        self.assertLess(sum(moves) / len(moves), 8, moves)

    def test_chord_from_pcs_uses_bass(self):
        # Fmaj7 の上の音（A C E）だけでは Am と区別できない。ベースが F なら Fmaj7
        ch, _ = theory.chord_from_pcs({9: 1, 0: 1, 4: 1, 5: 0.6}, bass=5)
        self.assertEqual(ch.root, 5)


class Spec(unittest.TestCase):
    def test_time_formats_agree(self):
        sp = small(tempo=120)
        m, t = sp['meter'], sp['tempo']
        for v in [6, '6s', '6秒', '0:06', 'bar 4', 'b4', '4小節目', {'bar': 4}]:
            self.assertAlmostEqual(spec.parse_time(v, m, t), 12.0, msg=str(v))
        self.assertAlmostEqual(spec.parse_time('b4.3', m, t), 14.0)
        self.assertAlmostEqual(spec.parse_time('bar 4 beat 3', m, t), 14.0)
        self.assertAlmostEqual(spec.parse_length('2 bars', m, t, 0), 8.0)
        self.assertAlmostEqual(spec.parse_length('3拍', m, t, 0), 3.0)
        self.assertAlmostEqual(spec.parse_length('1.5s', m, t, 0), 3.0)
        with self.assertRaises(ValueError):
            spec.parse_time('そのうち', m, t)

    def test_example_sections_land_on_seconds(self):
        sp = spec.load(EXAMPLE)
        starts = [round(sp['tempo'].sec(s.start), 3) for s in sp['sections']]
        self.assertEqual(starts, [0, 6, 12, 22, 32, 46, 52])
        self.assertEqual(len(sp['sections']), 7)
        # 最後の2小節は rit. で少し遅くなるので 60 秒より少し長い
        self.assertTrue(60.0 < sp['total_sec'] < 61.5, sp['total_sec'])

    def test_tempo_map_roundtrip(self):
        sp = small(tempo=100)
        t = sp['tempo']
        for b in [0, 3.3, 20, sp['total_beats'] - 0.1]:
            self.assertAlmostEqual(t.beat(t.sec(b)), b, places=6)

    def test_bad_spec_messages(self):
        with self.assertRaises(ValueError):
            small(style='jazz_fusion')
        with self.assertRaises(ValueError):
            small(cues=[dict(at=3, type='explode')])
        with self.assertRaises(ValueError):
            small(cues=[dict(at=999, type='hit')])

    def test_section_seconds_snap_warns(self):
        sp = spec.normalize(dict(style='bright_pop', tempo=120, sections=[dict(name='a', seconds=5.3)]))
        self.assertTrue(any('そろえた' in w for w in sp['warnings']))


class Compose(unittest.TestCase):
    def test_every_style_composes_cleanly(self):
        for name in styles.STYLES:
            sc = compose.compose(small(name))
            self.assertGreater(len(sc.tracks), 3, name)
            for tid, tr in sc.tracks.items():
                if tr.drum:
                    continue
                bad = [n.pitch for n in tr.notes if not (tr.lo <= n.pitch <= tr.hi)]
                self.assertEqual(bad, [], f'{name}/{tid} 音域の外')
            self._no_consecutive_duplicate_bars(sc, name)

    def _no_consecutive_duplicate_bars(self, sc, name):
        sp = sc.spec
        bb = sp['meter'].bar_beats
        nb = int(math.ceil(sp['total_beats'] / bb))
        sigs = [compose.bar_signature(sc, k * bb, (k + 1) * bb) for k in range(nb)]
        for k in range(1, nb):
            self.assertNotEqual(sigs[k], sigs[k - 1], f'{name}：{k}小節目と{k + 1}小節目が同じ')

    def test_deterministic_by_seed(self):
        a = compose.compose(small(seed=5))
        b = compose.compose(small(seed=5))
        c = compose.compose(small(seed=6))
        sig = lambda s: [(t, n.start, n.pitch) for t, tr in s.tracks.items() for n in tr.notes]
        self.assertEqual(sig(a), sig(b))
        self.assertNotEqual(sig(a), sig(c))

    def test_harmony_cadences_and_modulation(self):
        sp = spec.load(EXAMPLE)
        sc = compose.compose(sp)
        secs = sp['sections']
        build = secs[3]
        # 盛り上げ（build）は半終止：最後の和音が属和音（D major の V = A）…ただし次が転調するので橋渡しの属七
        last_build = sc.chord_at(build.end - 0.01)
        self.assertEqual(last_build.chord.root, theory.parse_pc('B'))   # E major の V7 = B7
        # 最後は主和音（E major）
        self.assertEqual(sc.chord_at(sp['total_beats'] - 0.01).chord.name(), 'E')
        # 区切りごとに進行が違う（サビ以外）
        progs = [tuple(c.chord.name() for c in sc.chords if c.section == s.index) for s in secs]
        self.assertEqual(len(set(progs)), len(progs))

    def test_melody_ends_on_tonic_and_chorus_is_highest(self):
        sp = spec.load(EXAMPLE)
        sc = compose.compose(sp)
        last = sc.melody_line[-1]
        self.assertEqual(last[2] % 12, sp['sections'][-1].key.tonic)
        top = {}
        for t, d, p, sec, pos, lb in sc.melody_line:
            top[sec.role] = max(top.get(sec.role, 0), p)
        self.assertGreaterEqual(top['chorus'], top['build'])
        self.assertGreaterEqual(top['chorus'], top['verse'])

    def test_break_cue_removes_rhythm(self):
        sp = small(cues=[dict(at='bar 8', type='break', length='1 bar')])
        sc = compose.compose(sp)
        a, b = 7 * 4, 8 * 4
        for tr in sc.tracks.values():
            if tr.fn in compose.RHYTHM_FNS:
                inside = [n for n in tr.notes if a + 0.1 <= n.start < b - 0.1]
                self.assertEqual(inside, [], tr.id)

    def test_hit_and_impact_add_sounds(self):
        sp = small(cues=[dict(at=12, type='hit'), dict(at=20, type='impact'), dict(at=5, type='whoosh')])
        sc = compose.compose(sp)
        self.assertIn('cue_hit', sc.tracks)
        self.assertTrue(any(n.pitch == D['crash'] for n in sc.tracks['cue_perc'].notes))
        self.assertEqual(sorted(e['type'] for e in sc.sfx), ['impact', 'whoosh'])

    def test_build_ramps_up(self):
        sc = compose.compose(small())
        b = sc.spec['sections'][2]
        notes = [n for tr in sc.tracks.values() if tr.fn != 'cue' for n in tr.notes if b.start <= n.start < b.end]
        first = [n for n in notes if n.start < b.start + 4]
        last = [n for n in notes if n.start >= b.end - 8]
        tracks_in = lambda a, z: {tid for tid, tr in sc.tracks.items() if tr.fn != 'cue' and any(a <= n.start < z for n in tr.notes)}
        self.assertGreater(len(tracks_in(b.end - 4, b.end)), len(tracks_in(b.start, b.start + 4)))
        self.assertGreater(sum(n.vel for n in last) / len(last), sum(n.vel for n in first) / len(first))

    def test_performance_is_connected_and_soft(self):
        """弾き方：旋律はつながり、鍵盤は和音の替わり目の直後でペダルを踏み替え、伴奏は旋律より弱い（2026-10-27 の指摘「ボンボン叩いている」）。"""
        sc = compose.compose(small())
        lead, keys = sc.tracks['lead'], sc.tracks['keys']
        ns = sorted(lead.notes, key=lambda n: n.start)
        pairs = [(a, b) for a, b in zip(ns, ns[1:]) if 0 < b.start - a.start <= 1.0]
        gaps = [b.start - a.end for a, b in pairs]
        self.assertLess(sum(g > 0.05 for g in gaps) / max(1, len(gaps)), 0.1, '1拍以内に続く旋律の音のあいだが空いている')
        ped = sorted(c for c in keys.cc if c[1] == 64)
        self.assertTrue(ped, '伴奏のピアノがペダルを踏んでいない')
        for t, _, v in ped:
            if v == 0 and t < max(n.end for n in keys.notes):
                span = sc.chord_at(t)
                self.assertGreaterEqual(t - span.start, 0, 'ペダルを和音の替わり目より前に上げている（音が途切れる）')
                self.assertLess(t - span.start, 0.1)
        self.assertLessEqual(max(n.vel for n in keys.notes), 95, '伴奏の打鍵が強すぎる')
        self.assertLessEqual(max(n.vel for n in lead.notes), 105)
        self.assertTrue(any(c[1] == 64 for c in lead.cc), '旋律のピアノにペダルがない')

    def test_staccato_melody_stays_short_and_repeat_can_be_turned_off(self):
        """スタッカートの旋律はつながない。repeat: false の区切りは、前の同じ役の旋律を写さない（2026-10-27）。"""
        sc = compose.compose(small('playful'))
        lead = sc.tracks['lead']
        ns = sorted(lead.notes, key=lambda n: n.start)
        short = [a for a, b in zip(ns, ns[1:]) if 0 < b.start - a.start <= 1 and a.dur < 0.8 * (b.start - a.start)]
        self.assertTrue(short, 'スタッカートの旋律が全部つながっている')
        secs = [dict(name='A', bars=2, role='verse'), dict(name='B', bars=2, role='build'), dict(name='A2', bars=2, role='verse'), dict(name='終わり', bars=2, role='outro')]
        rel = lambda sc, name: [(round(t - s.start, 2), p) for t, d, p, s, pos, lb in sc.melody_line if s.name == name]
        same = compose.compose(small(sections=secs))
        self.assertEqual(rel(same, 'A'), rel(same, 'A2'), '同じ役の2回目が1回目と違う')
        secs[2]['repeat'] = False
        diff = compose.compose(small(sections=secs))
        self.assertTrue(any('繰り返す' in l for l in same.log))
        self.assertFalse(any('A2' in l and '繰り返す' in l for l in diff.log))

    def test_instruments_override(self):
        raw = dict(style='cinematic', sections=[dict(name='a', bars=2, instruments=['harp', 'strings']),
                                                dict(name='b', bars=2, instruments=['+choir', '-timp'], energy=0.9)])
        sc = compose.compose(spec.normalize(raw))
        a_ids = {tid for tid, tr in sc.tracks.items() if any(n.start < 7.5 for n in tr.notes)}
        self.assertLessEqual(a_ids, {'harp', 'strings'})
        b_ids = {tid for tid, tr in sc.tracks.items() if any(n.start >= 8 for n in tr.notes)}
        self.assertIn('choir', b_ids)
        self.assertNotIn('timp', b_ids)


class Render(unittest.TestCase):
    def test_synth_fallback_and_mix(self):
        from mm import check, mix, render
        sp = spec.normalize(dict(style='cinematic', tempo=120, sections=[dict(name='a', bars=2, energy=0.95), dict(name='b', bars=1, role='outro')],
                                 cues=[dict(at=2, type='hit')]))
        sc = compose.compose(sp)
        stems, info = render.render_stems(sc, 24000, force_synth=True, log=lambda *_: None)
        self.assertEqual(info['engine'], 'synth')
        for k, v in stems.items():
            self.assertFalse(bool((v != v).any()), k)
        master, out, stats = mix.mix(stems, sc, 24000, log=lambda *_: None)
        self.assertLess(stats['true_peak'], -0.9)
        self.assertLess(abs(stats['loudness'] - (-14)), 1.0)
        with tempfile.TemporaryDirectory() as d:
            rep = check.analyze(master, out, sc, 24000, d, info, stats)
            self.assertTrue(os.path.exists(os.path.join(d, 'report.md')))
            self.assertTrue(os.path.exists(os.path.join(d, 'images', 'spectrogram.png')))
            self.assertEqual(rep['clipping']['samples_at_full_scale'], 0)

    def test_tinysoundfont_without_fluidsynth(self):
        # チャットのように fluidsynth（apt）が無い所では、pip の tinysoundfont で SoundFont を鳴らす
        from mm import render
        if not render.has_tsf() or not render.find_soundfont():
            self.skipTest('tinysoundfont か SoundFont が無い')
        sp = spec.normalize(dict(style='acoustic', tempo=120, sections=[dict(name='a', bars=2), dict(name='b', bars=1, role='outro')]))
        sc = compose.compose(sp)
        old = os.environ.get('MUSIC_MAKER_ENGINE')
        os.environ['MUSIC_MAKER_ENGINE'] = 'tinysoundfont'
        try:
            stems, info = render.render_stems(sc, 24000, log=lambda *_: None)
        finally:
            if old is None:
                os.environ.pop('MUSIC_MAKER_ENGINE')
            else:
                os.environ['MUSIC_MAKER_ENGINE'] = old
        self.assertEqual(info['engine'], 'tinysoundfont')
        loud = [k for k, v in stems.items() if float(abs(v).max()) > 1e-3]
        self.assertGreaterEqual(len(loud), len(stems) - 1, '鳴っていない楽器が多い')


if __name__ == '__main__':
    unittest.main()
