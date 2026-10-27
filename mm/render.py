"""MIDI → 楽器ごとの音（stems）。

音源の選び方（上から順に、見つかったものを使う）：
  1. 環境変数 MUSIC_SF2 で指定した SoundFont
  2. tools/music/.cache/GeneralUser-GS.sf2（`python3 tools/music/music.py setup` で取得）
  3. .cache/GeneralUser-GS-1.47.sf2（GitHub に届かないとき、setup が npm の registry から取る旧版）
  4. /usr/share/sounds/sf2/FluidR3_GM.sf2（apt の fluid-soundfont-gm）
  5. pretty_midi に入っている TimGM6mb.sf2（pip だけで取れる小さな音源）
鳴らす道具は fluidsynth（コマンド）→ tinysoundfont（pip で入る。チャットのように apt が使えない所向け）の順。
どちらも無いか、SoundFont が無ければ synth.py の合成音（サンプルを使わない）。
"""
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import soundfile as sf

from . import midi, synth

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# @couple:soundfont-paths 音源の置き場所を変えたら setup の取得先・.gitignore・SKILL.md の説明も
# 音源の置き場所：MUSIC_MAKER_CACHE があればそこ。なければ道具の横の .cache。道具の置き場所に書けないとき（スキルとして読み取り専用の所に置いたとき）は ~/.cache/music-maker
def _cache_dir():
    env = os.environ.get('MUSIC_MAKER_CACHE')
    if env:
        return env
    d = os.path.join(HERE, '.cache')
    if os.path.isdir(d) or os.access(HERE, os.W_OK):
        return d
    return os.path.join(os.path.expanduser('~'), '.cache', 'music-maker')
CACHE_SF2 = os.path.join(_cache_dir(), 'GeneralUser-GS.sf2')
CACHE_SF2_OLD = os.path.join(_cache_dir(), 'GeneralUser-GS-1.47.sf2')
SYSTEM_SF2 = ['/usr/share/sounds/sf2/FluidR3_GM.sf2', '/usr/share/soundfonts/FluidR3_GM.sf2', '/usr/share/sounds/sf2/default-GM.sf2']


def find_soundfont():
    env = os.environ.get('MUSIC_SF2')
    if env and os.path.exists(env):
        return env
    for p in [CACHE_SF2, CACHE_SF2_OLD] + SYSTEM_SF2:
        if os.path.exists(p):
            return p
    # pretty_midi は import しない（--no-deps で入れると依存が足りず import で落ちる）。置き場所だけ引く
    import importlib.util
    spec = importlib.util.find_spec('pretty_midi')
    if spec and spec.submodule_search_locations:
        for d in spec.submodule_search_locations:
            p = os.path.join(d, 'TimGM6mb.sf2')
            if os.path.exists(p):
                return p
    return None


def has_tsf():
    try:
        import tinysoundfont  # noqa: F401
        return True
    except ImportError:
        return False


def piano_override():
    """ピアノだけ別の SoundFont（Salamander Grand Piano など）で鳴らす。MUSIC_PIANO_SF2 か .cache/*Salamander*.sf2。"""
    env = os.environ.get('MUSIC_PIANO_SF2')
    if env and os.path.exists(env):
        return env
    d = os.path.dirname(CACHE_SF2)
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if 'salamander' in f.lower() and f.endswith('.sf2'):
                return os.path.join(d, f)
    return None


def engine_info(force_synth=False):
    sf2 = None if force_synth else find_soundfont()
    # MUSIC_MAKER_ENGINE=tinysoundfont で、fluidsynth があっても tinysoundfont で鳴らす（チャットと同じ鳴り方を手元で確かめる）
    fs = None if os.environ.get('MUSIC_MAKER_ENGINE') == 'tinysoundfont' else shutil.which('fluidsynth')
    if sf2 and fs:
        return {'engine': 'fluidsynth', 'soundfont': sf2, 'name': os.path.basename(sf2)}
    if sf2 and has_tsf():
        return {'engine': 'tinysoundfont', 'soundfont': sf2, 'name': os.path.basename(sf2)}
    why = []
    if not fs and not has_tsf():
        why.append('fluidsynth も tinysoundfont も無い')
    if not sf2:
        why.append('SoundFont が無い')
    if force_synth:
        why = ['--synth を指定']
    return {'engine': 'synth', 'soundfont': None, 'name': '合成音（サンプルなし）', 'why': '、'.join(why)}


def _fluid(sf2, mid, wav, sr):
    cmd = ['fluidsynth', '-ni', '-q', '-r', str(sr), '-g', '0.5', '-O', 'float', '-T', 'wav',
           '-o', 'synth.reverb.active=0', '-o', 'synth.chorus.active=0', '-o', 'synth.polyphony=512',
           '-o', f'synth.sample-rate={sr}', '-F', wav, sf2, mid]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _tsf(sf2, mid, wav, sr):
    """fluidsynth の代わり。pip だけで入る tinysoundfont で同じ MIDI を鳴らす（残響・コーラスは付けない。後のミックスで掛ける）。"""
    import tinysoundfont
    synth = tinysoundfont.Synth(gain=-6, samplerate=sr)  # fluidsynth の -g 0.5 に合わせて半分
    sfid = synth.sfload(sf2, max_voices=512)
    for ch in range(16):
        try:
            synth.program_select(ch, sfid, 128 if ch == 9 else 0, 0, ch == 9)
        except Exception:
            pass
    seq = tinysoundfont.Sequencer(synth)
    seq.midi_load(mid)
    end = max((e.t for e in seq.events), default=0.0) + 4.0
    n = int(end * sr)
    out = np.empty((n, 2), dtype=np.float32)
    step = 4096
    for i in range(0, n, step):
        k = min(step, n - i)
        out[i:i + k] = np.frombuffer(synth.generate(k), dtype=np.float32).reshape(-1, 2)
    sf.write(wav, out, sr, subtype='FLOAT')


def render_stems(score, sr=48000, force_synth=False, workdir=None, log=print):
    info = engine_info(force_synth)
    spec = score.spec
    total_sec = spec['tempo'].sec(getattr(score, 'end_beats', spec['total_beats'])) + spec['tail_sec']
    n = int(total_sec * sr)
    stems = {}
    if info['engine'] in ('fluidsynth', 'tinysoundfont'):
        tmp = workdir or tempfile.mkdtemp(prefix='mm-')
        jobs = []
        for tid, tr in score.tracks.items():
            mid = os.path.join(tmp, f'{tid}.mid')
            wav = os.path.join(tmp, f'{tid}.wav')
            midi.write_single(score, tr, mid)
            jobs.append((tid, mid, wav))
        piano = piano_override()
        # 差し替えは GM の 0 番（アコースティックピアノ）だけ。Salamander には 0 番しか無い
        sf_of = {tid: (piano if piano and score.tracks[tid].program == 0 and not score.tracks[tid].drum else info['soundfont']) for tid, _, _ in jobs}
        run = _fluid if info['engine'] == 'fluidsynth' else _tsf
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as ex:
            list(ex.map(lambda j: run(sf_of[j[0]], j[1], j[2], sr), jobs))
        if piano:
            info['piano'] = os.path.basename(piano)
        for tid, mid, wav in jobs:
            a, r = sf.read(wav, dtype='float32', always_2d=True)
            if a.shape[1] == 1:
                a = np.repeat(a, 2, axis=1)
            stems[tid] = _fit(a, n)
        log(f'音源：{"FluidSynth" if info["engine"] == "fluidsynth" else "tinysoundfont"} ＋ {info["name"]}（{len(jobs)} 本の楽器を並べて書き出し）')
        if not workdir:
            # 一時フォルダは読み終えたら消す（楽器ごとの 32bit の wav で、1回 300MB を超える。残すと書き出すたびに溜まり、ディスクが埋まった。2026-10-27）
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        log(f'音源：{info["name"]}（{info.get("why", "")}）')
        for tid, tr in score.tracks.items():
            stems[tid] = _fit(synth.render_track(tr, spec, sr), n)
    return stems, info


def _fit(a, n):
    if len(a) >= n:
        return a[:n]
    return np.concatenate([a, np.zeros((n - len(a), a.shape[1]), dtype=a.dtype)])
