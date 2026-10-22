# music-maker

動画の BGM・効果音・ジングルを、設計図（YAML）から作る道具です。区切りの長さと気分、画面の動きに合わせるきっかけ（一撃、拍を落とす、盛り上げ、風切り音）を書くと、和声・旋律・伴奏を組んで MIDI にし、SoundFont の生楽器に近い音で鳴らし、ミックスして WAV と楽器ごとの音（stems）を書き出します。

もともとは Claude Code に曲を作らせるために書きました。Claude は音を聴けないので、作ったあとに音量・音域・和音・同じ小節の繰り返し・クリップを数値と図で報告し、Claude はそれを読んで直します。人が手で使うための編集画面も付いています。

試しに聴くなら `examples/promo-60s/preview.mp3`（60秒の宣伝ムービー用、例の設計図からそのまま作ったもの）。

## Claude Code のスキルとして入れる

```bash
git clone https://github.com/dia-nexus/music-maker ~/.claude/skills/music-maker
```

これで Claude Code が「BGM を作って」「効果音を入れて」のような頼みで `SKILL.md` を読み、この道具を使います。プロジェクトだけで使うなら `.claude/skills/music-maker` に置きます。claude.ai や Claude Desktop で使うときは、このフォルダを zip にしてスキルとして読み込ませます（音を鳴らすには Python と FluidSynth が動く環境が要ります）。

最初に一度、音源と部品を入れます。

```bash
python3 ~/.claude/skills/music-maker/music.py setup
```

必要なのは Python 3.10 以上と、numpy・scipy・PyYAML・matplotlib（setup が pip で入れる）、FluidSynth（setup が apt で入れる。macOS なら `brew install fluid-synth`）、mp3 を書くなら ffmpeg。FluidSynth が無くても、合成音で鳴らせます（音は落ちます）。

## 使い方（コマンド）

```bash
python3 music.py setup                                   # セッションごとに1回。FluidSynth と GeneralUser GS を入れる
python3 music.py make examples/promo-60s.yaml -o out/promo
python3 music.py serve                                   # 人が使う編集画面 http://127.0.0.1:8765/
```

ほかのコマンドは `doctor`（どの音源で鳴るか）、`styles`（様式の一覧）、`new --style lofi --seconds 30`（設計図のひな形）、`compose`（MIDI と作曲の記録だけ、0.2 秒）。`make` には `--seed N`（別の案）、`--synth`（SoundFont を使わず合成音）、`--no-stems` が付けられる。

`out/promo/` には `master.wav`（48kHz/24bit）、`music.mid`、`stems/`、`preview.mp3`、`report.md`・`report.html`・`report.json`、`images/`（スペクトログラム、ピアノロール、音量の推移、小節どうしの似かよい）ができる。`examples/promo-60s/` に、例の設計図から作った報告と試聴用の mp3 を置いてある（WAV と stems は大きいので入れていない）。

## 様式

| 様式 | 名前 | テンポ・調の既定 |
|---|---|---|
| `bright_pop` | 明るいポップ | 120・D major |
| `acoustic` | やわらかいアコースティック | 92・G major |
| `cinematic` | 映画風オーケストラ | 100・D minor |
| `lofi` | ローファイ | 78・F major（跳ね） |
| `playful` | 遊び心（ピチカートと木管） | 126・F major |

## 音源

上から順に、見つかったもので鳴らす。

1. 環境変数 `MUSIC_SF2` の SoundFont
2. `.cache/GeneralUser-GS.sf2`（フォルダに書けなければ `~/.cache/music-maker/`、`MUSIC_MAKER_CACHE` で指定も可）（setup が raw.githubusercontent.com から取る。v2.0.3、約31MB。許諾は自作の曲への利用に制限なし、同梱・改変も可）
3. `/usr/share/sounds/sf2/FluidR3_GM.sf2`（apt の fluid-soundfont-gm、MIT）
4. どれも無ければ `mm/synth.py` の合成音（サンプルを使わない。弦は鋸歯の合奏と胴の共鳴、ギターは Karplus-Strong など）

ピアノだけは Salamander Grand Piano（CC-BY 3.0、落とすのは約300MB、展開後 1.27GB）に差し替えられる。`python3 music.py setup --extra salamander` で `.cache/` に取り、ある間はピアノの楽器がそれで鳴る。曲を外に出すときはクレジット（Salamander Grand Piano V3 by Alexander Holm, CC-BY 3.0）を添える。取得の経路と、ほかの音源の許諾の調べは `docs/sound-sources.md`。

SoundFont はこのリポジトリに入れていない。setup で取れば数秒で済み、clone が軽いままで済む。

## 構成

| ファイル | 中身 |
|---|---|
| `music.py` | 入口。setup / doctor / styles / new / compose / make / serve |
| `mm/spec.py` | 設計図を読み、秒・小節・拍の書き方をそろえ、テンポの地図（rit. を含む）を作る |
| `mm/theory.py` | 調、ローマ数字の和音、声部の進め方（平行5度・8度を避け、動きの少ない配置）、和音名の推定 |
| `mm/styles.py` | 楽器の一覧（GM の番号・音域・定位・系統）、様式のひな形、和声進行の手持ち、終止の形。数を変えるならここ |
| `mm/compose.py` | 作曲。和声→旋律（動機と展開）→伴奏の型→きっかけ→終止→強弱→同じ小節の解消→人間らしい揺れ |
| `mm/midi.py` | MIDI の書き出し（全体と楽器ごと） |
| `mm/render.py` | FluidSynth で楽器ごとに書き出す（並列）。無ければ合成音 |
| `mm/synth.py` | SoundFont が無いときの合成音 |
| `mm/sfx.py` | 楽器で出せない効果音（風切り、上昇、低い衝撃、逆再生シンバル、打音、針音） |
| `mm/mix.py` | 楽器ごとの音量合わせ・EQ・圧縮・残響（数値で作った部屋の響きを畳み込む）・広がり、全体の圧縮・ラウドネス・真のピーク |
| `mm/check.py` | 確かめの報告（数値と図） |
| `editor.html` | 人が使う編集画面。`serve` で開くと作る・聴くまでできる |

決めたことと理由、分かっている限界、次にやることは `docs/design.md`。

## 開発と検証

```bash
python3 -m unittest discover -s tests     # 作曲・設計図・理論のテスト
python3 music.py make examples/promo-60s.yaml -o out/promo
```

道具を直したら、例の設計図で `make` を流し、`examples/promo-60s/` の報告を書き直してください（報告の数字が今の道具の結果と食い違わないように）。

## 音源の許諾とクレジット

- GeneralUser GS（S. Christian Collins）：自作の曲への利用は商用を含め制限なし。setup が取得します。このリポジトリには含みません
- Salamander Grand Piano V3（Alexander Holm、CC-BY 3.0）：`setup --extra salamander` で入れたときだけ使います。曲を公開するときはクレジットを添えてください
- FluidR3_GM（MIT）：OS に入っていれば使います

## ライセンス

コードは MIT License（`LICENSE`）。作った曲は作った人のものです。
