#!/usr/bin/env python3
"""音楽づくりの道具（Claude 用）。設計図（YAML/JSON）から、作曲 → MIDI → 生楽器に近い音 → ミックス → 確かめの報告、まで。

  python3 music.py setup                       必要なもの（pip・FluidSynth・GeneralUser GS）を入れる
  python3 music.py doctor                      いま使える音源と道具を見る
  python3 music.py styles                      様式のひな形の一覧
  python3 music.py new --style cinematic --seconds 30 > spec.yaml   設計図のひな形を作る
  python3 music.py make spec.yaml -o out/x     曲を作り、WAV・MIDI・stems・報告を書き出す
  python3 music.py compose spec.yaml -o out/x  MIDI と作曲の記録だけ（数秒で終わる。和声や構成の下書きに）
  python3 music.py serve                       人が使う編集画面（http://127.0.0.1:8765/）

make の主なオプション：--seed N（別の案）、--synth（SoundFont を使わず合成音）、--no-stems、--wav-only
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

GU_URL = 'https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/GeneralUser-GS.sf2'
GU_LICENSE_URL = 'https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/documentation/LICENSE.txt'
GU_SIZE = 32319396
SALAMANDER_URL = 'https://freepats.zenvoid.org/Piano/SalamanderGrandPiano/SalamanderGrandPiano-SF2-V3+20200602.tar.xz'
PIP_PKGS = ['numpy', 'scipy', 'pyyaml', 'mido', 'soundfile', 'pyloudnorm', 'pedalboard', 'matplotlib']


def log(msg):
    print(msg, flush=True)


def cmd_setup(a):
    log('1/3 Python の部品を入れる')
    missing = []
    for mod, pkg in [('numpy', 'numpy'), ('scipy', 'scipy'), ('yaml', 'pyyaml'), ('mido', 'mido'), ('soundfile', 'soundfile'),
                     ('pyloudnorm', 'pyloudnorm'), ('pedalboard', 'pedalboard'), ('matplotlib', 'matplotlib')]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q'] + missing, check=False)
    log('2/3 FluidSynth（MIDI を SoundFont で鳴らす道具）')
    if not shutil.which('fluidsynth'):
        if shutil.which('apt-get'):
            subprocess.run('apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq fluidsynth fluid-soundfont-gm >/dev/null 2>&1', shell=True)
        elif shutil.which('brew'):
            subprocess.run(['brew', 'install', 'fluid-synth'])
    log('   ' + ('入っている' if shutil.which('fluidsynth') else '入れられなかった（合成音で鳴らす）'))
    log('3/3 GeneralUser GS（GM の SoundFont、約31MB）')
    from mm import render
    # @couple:soundfont-paths 取得先や置き場所を変えたら render.find_soundfont の探す順と README の音源の節も
    dst = render.CACHE_SF2
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst) and os.path.getsize(dst) == GU_SIZE:
        log('   取得済み：' + dst)
    else:
        tmp = dst + '.part'
        r = subprocess.run(['curl', '-fsSL', '--retry', '3', '-o', tmp, GU_URL])
        if r.returncode == 0 and os.path.getsize(tmp) > 1_000_000:
            with open(tmp, 'rb') as fp:
                head = fp.read(12)
            if head[:4] == b'RIFF' and head[8:12] == b'sfbk':
                os.replace(tmp, dst)
                subprocess.run(['curl', '-fsSL', '-o', os.path.join(os.path.dirname(dst), 'GeneralUser-GS-LICENSE.txt'), GU_LICENSE_URL])
                log('   取得した：' + dst)
            else:
                os.remove(tmp)
                log('   取れたファイルが SoundFont ではなかった（プロキシの拒否画面など）')
        else:
            log('   取れなかった。FluidR3_GM（apt）か合成音で鳴らす')
    if a.extra == 'salamander':
        log('おまけ：Salamander Grand Piano V3（ピアノだけ差し替え。CC-BY 3.0 Alexander Holm、約300MB）')
        sal = os.path.join(os.path.dirname(dst), 'SalamanderGrandPiano-V3.sf2')
        if os.path.exists(sal):
            log('   取得済み：' + sal)
        else:
            tmp = os.path.join(os.path.dirname(dst), 'salamander.tar.xz')
            r = subprocess.run(['curl', '-fsSL', '--retry', '3', '-o', tmp, SALAMANDER_URL])
            if r.returncode == 0:
                import tarfile
                with tarfile.open(tmp) as tf:
                    for m in tf.getmembers():
                        if m.name.endswith('.sf2'):
                            with tf.extractfile(m) as src, open(sal, 'wb') as out:
                                shutil.copyfileobj(src, out)
                        elif m.name.endswith('readme.txt'):
                            with tf.extractfile(m) as src, open(os.path.join(os.path.dirname(dst), 'Salamander-readme.txt'), 'wb') as out:
                                shutil.copyfileobj(src, out)
                os.remove(tmp)
                log('   取得した：' + sal)
            else:
                log('   取れなかった（freepats.zenvoid.org に届かない）。ピアノは GeneralUser GS のまま')
    cmd_doctor(a)


def cmd_doctor(a):
    from mm import render
    info = render.engine_info()
    log(f'音源：{info["name"]}（{info["engine"]}）' + (f'　理由：{info["why"]}' if info.get('why') else ''))
    if info.get('soundfont'):
        log(f'  SoundFont：{info["soundfont"]}')
    for tool in ['fluidsynth', 'ffmpeg']:
        log(f'  {tool}：{shutil.which(tool) or "無い"}')
    extra = render.piano_override()
    if extra:
        log(f'  ピアノの差し替え：{extra}')


def cmd_styles(a):
    from mm import styles
    for k, s in styles.STYLES.items():
        log(f'{k:12s} {s["label"]}　テンポ {s["tempo"]}　調 {s["key"]}')
        log(f'             {s["description"]}')
        log('             楽器：' + '、'.join(f'{p["id"]}={styles.INSTRUMENTS[p["inst"]]["label"]}({p["fn"]}, 勢い{p["e"][0]:.2f}〜)' for p in s['parts']))


def cmd_new(a):
    from mm import styles
    st = styles.STYLES[a.style]
    bpm = a.tempo or st['tempo']
    secs = a.seconds
    bar = 60 / bpm * 4
    plan = [('つかみ', 'intro', '期待'), ('紹介', 'verse', '軽快'), ('盛り上げ', 'build', '盛り上げていく'),
            ('山場', 'chorus', '高揚'), ('終わり', 'outro', '堂々と締める')]
    n = len(plan)
    bars_total = max(n, round(secs / bar))
    weights = [0.12, 0.25, 0.18, 0.3, 0.15]
    bars = [max(1, round(bars_total * w)) for w in weights]
    bars[3] += bars_total - sum(bars)
    out = [f'title: 新しい曲', f'style: {a.style}', f'tempo: {bpm:g}', 'meter: 4/4', f'key: {st["key"]}', 'seed: 1', '', 'sections:']
    for (name, role, mood), b in zip(plan, bars):
        out += [f'  - name: {name}', f'    bars: {b}            # {b * bar:.1f} 秒', f'    role: {role}', f'    mood: {mood}']
    out += ['', 'cues: []', '# cues:', '#   - { at: 12, type: hit }            # 12秒で一撃',
            '#   - { at: "bar 9", type: riser, length: 2 bars }   # 9小節目の頭に向けて盛り上げる',
            '', 'mix: { loudness: -14, true_peak: -1 }']
    print('\n'.join(out))


def _load(a):
    from mm import spec as spec_mod
    sp = spec_mod.load(a.spec)
    if a.seed is not None:
        sp['seed'] = a.seed
    for w in sp['warnings']:
        log('注意：' + w)
    return sp


def _outdir(a):
    out = a.out or os.path.join('out', os.path.splitext(os.path.basename(a.spec))[0])
    os.makedirs(out, exist_ok=True)
    return out


def cmd_compose(a):
    from mm import compose, midi
    sp = _load(a)
    out = _outdir(a)
    t0 = time.time()
    score = compose.compose(sp)
    midi.write_full(score, os.path.join(out, 'music.mid'))
    with open(os.path.join(out, 'compose-log.md'), 'w', encoding='utf-8') as fp:
        fp.write('# 作曲の記録\n\n' + '\n'.join('- ' + x for x in score.log) + '\n')
    log(f'作曲：{len(score.tracks)} 本の楽器、{sum(len(t.notes) for t in score.tracks.values())} 音（{time.time() - t0:.1f} 秒）')
    for x in score.log:
        log('  ' + x)
    log(f'書き出し：{out}/music.mid')
    return sp, score, out


def cmd_make(a):
    import numpy as np
    import soundfile as sf
    from mm import check, mix, render
    sp, score, out = cmd_compose(a)
    sr = sp['sample_rate']
    t0 = time.time()
    stems, info = render.render_stems(score, sr, force_synth=a.synth, log=log)
    log(f'  音にした（{time.time() - t0:.1f} 秒）')
    t0 = time.time()
    master, out_stems, stats = mix.mix(stems, score, sr, log=log)
    log(f'  ミックスした（{time.time() - t0:.1f} 秒）')
    sf.write(os.path.join(out, 'master.wav'), master, sr, subtype='PCM_24')
    if not a.no_stems:
        sd = os.path.join(out, 'stems')
        os.makedirs(sd, exist_ok=True)
        for k, v in out_stems.items():
            sf.write(os.path.join(sd, f'{k}.wav'), v, sr, subtype='PCM_24')
    if shutil.which('ffmpeg') and not a.wav_only:
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', os.path.join(out, 'master.wav'), '-b:a', '192k',
                        os.path.join(out, 'preview.mp3')])
    t0 = time.time()
    rep = check.analyze(master, out_stems, score, sr, out, info, stats)
    html_report(out, rep)
    log(f'  確かめた（{time.time() - t0:.1f} 秒）')
    log(f'書き出し：{out}/master.wav（48kHz/24bit）、music.mid、' + ('stems/、' if not a.no_stems else '') + 'report.md、report.html、images/')
    if rep['flags']:
        log('直したほうがよさそうな所：')
        for f in rep['flags']:
            log('  - ' + f)
    else:
        log('確かめで気になる所は見つからなかった')


def html_report(out, rep):
    """人が聴いて見るためのページ（音を再生しながら図と表を読む）。"""
    import html
    md = open(os.path.join(out, 'report.md'), encoding='utf-8').read().split('\n')
    body, in_table = [], False
    for line in md:
        if line.startswith('|'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if set(''.join(cells)) <= set('-'):
                continue
            if not in_table:
                body.append('<table>')
                in_table = True
                body.append('<tr>' + ''.join(f'<th>{html.escape(c)}</th>' for c in cells) + '</tr>')
            else:
                body.append('<tr>' + ''.join(f'<td>{html.escape(c)}</td>' for c in cells) + '</tr>')
            continue
        if in_table:
            body.append('</table>')
            in_table = False
        if line.startswith('# '):
            body.append(f'<h1>{html.escape(line[2:])}</h1>')
            audio = 'preview.mp3' if os.path.exists(os.path.join(out, 'preview.mp3')) else 'master.wav'
            body.append(f'<audio controls src="{audio}" style="width:100%"></audio>')
        elif line.startswith('## '):
            body.append(f'<h2>{html.escape(line[3:])}</h2>')
        elif line.startswith('![') and '](' in line:
            src = line[line.index('](') + 2:-1]
            body.append(f'<img src="{html.escape(src)}" alt="">')
        elif line.startswith('- '):
            body.append(f'<li>{html.escape(line[2:])}</li>')
        elif line.strip():
            body.append(f'<p>{html.escape(line)}</p>')
    page = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(rep["title"])}</title><style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:16px;line-height:1.6}}'
            'table{border-collapse:collapse;font-size:13px;display:block;overflow-x:auto}td,th{border:1px solid #ccc;padding:3px 6px}'
            'img{max-width:100%}li{margin-left:1em}</style>' + '\n'.join(body))
    with open(os.path.join(out, 'report.html'), 'w', encoding='utf-8') as fp:
        fp.write(page)


def cmd_serve(a):
    """人が使う画面：設計図を編集し、ボタンで曲を作って、聴いて、報告を見る。"""
    import http.server
    import socketserver
    import urllib.parse
    root = os.path.abspath(a.out or 'out')
    os.makedirs(root, exist_ok=True)

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.unquote(self.path.split('?')[0])
            if path in ('/', '/index.html'):
                f = os.path.join(HERE, 'editor.html')
            elif path.startswith('/out/'):
                f = os.path.abspath(os.path.join(root, path[5:]))
                if not f.startswith(root + os.sep):
                    return self.send_error(403)
            else:
                return self.send_error(404)
            if not os.path.isfile(f):
                return self.send_error(404)
            ctype = {'.html': 'text/html; charset=utf-8', '.png': 'image/png', '.mp3': 'audio/mpeg', '.wav': 'audio/wav',
                     '.mid': 'audio/midi', '.md': 'text/markdown; charset=utf-8', '.json': 'application/json'}.get(os.path.splitext(f)[1], 'application/octet-stream')
            data = open(f, 'rb').read()
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != '/api/make':
                return self.send_error(404)
            raw = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            name = time.strftime('%Y%m%d-%H%M%S')
            out = os.path.join(root, 'editor', name)
            os.makedirs(out, exist_ok=True)
            spec_path = os.path.join(out, 'spec.json')
            with open(spec_path, 'w', encoding='utf-8') as fp:
                json.dump(raw, fp, ensure_ascii=False, indent=1)
            r = subprocess.run([sys.executable, os.path.join(HERE, 'music.py'), 'make', spec_path, '-o', out, '--no-stems'],
                               capture_output=True, text=True)
            if r.returncode != 0:
                msg = (r.stderr.strip().splitlines() or ['作れませんでした'])[-1]
                return self._json(400, {'error': msg})
            rep = json.load(open(os.path.join(out, 'report.json'), encoding='utf-8'))
            base = f'out/editor/{name}'
            audio = 'preview.mp3' if os.path.exists(os.path.join(out, 'preview.mp3')) else 'master.wav'
            self._json(200, {'audio': f'{base}/{audio}', 'wav': f'{base}/master.wav', 'midi': f'{base}/music.mid',
                             'report': f'{base}/report.html', 'images': f'{base}/images', 'flags': rep.get('flags', [])})

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('127.0.0.1', a.port), H) as srv:
        log(f'編集画面：http://127.0.0.1:{a.port}/ （止めるときは Ctrl+C）。作った曲は {root}/editor/ に残る')
        srv.serve_forever()


def main():
    from mm import styles
    p = argparse.ArgumentParser(description='Claude 用の音楽づくりの道具', formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('setup', help='必要なものを入れる')
    s.add_argument('--extra', choices=['salamander'], help='ピアノを Salamander Grand Piano に差し替える（約300MB）')
    sub.add_parser('doctor', help='使える音源と道具を見る')
    sub.add_parser('styles', help='様式の一覧')
    s = sub.add_parser('new', help='設計図のひな形')
    s.add_argument('--style', default='bright_pop', choices=list(styles.STYLES))
    s.add_argument('--seconds', type=float, default=60)
    s.add_argument('--tempo', type=float)
    for name in ('make', 'compose'):
        s = sub.add_parser(name)
        s.add_argument('spec')
        s.add_argument('-o', '--out')
        s.add_argument('--seed', type=int)
        s.add_argument('--synth', action='store_true', help='SoundFont を使わず合成音で鳴らす')
        s.add_argument('--no-stems', action='store_true')
        s.add_argument('--wav-only', action='store_true', help='mp3 の試聴用を作らない')
    s = sub.add_parser('serve', help='人が使う編集画面を開く（設計図の編集・作る・聴く・報告）')
    s.add_argument('--port', type=int, default=8765)
    s.add_argument('-o', '--out', help='作った曲を置く場所（既定は out/）')
    a = p.parse_args()
    a.extra = getattr(a, 'extra', None)
    {'setup': cmd_setup, 'doctor': cmd_doctor, 'styles': cmd_styles, 'new': cmd_new, 'make': cmd_make, 'compose': cmd_compose, 'serve': cmd_serve}[a.cmd](a)


if __name__ == '__main__':
    main()
