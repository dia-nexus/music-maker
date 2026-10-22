"""楽器の一覧と、様式のひな形。

数を変えるならここ（楽器の音域・GM の番号・定位、様式ごとの編成と和声の手持ち）。
compose.py はここを読むだけで、楽器名や番号を直接書かない。
"""

# 楽器：GM の音色番号（0始まり）、系統（代わりの音と混ぜ方の分類）、音域、定位（-1 左〜+1 右）
# @couple:instrument-families 系統を足したら synth.VOICES と mix.FAMILY と impact.rules.cjs の照合も
INSTRUMENTS = {
    'piano':        dict(program=0,  family='keys',    lo=36, hi=96, pan=-0.05, label='ピアノ'),
    'bright_piano': dict(program=1,  family='keys',    lo=36, hi=96, pan=0.0,  label='明るいピアノ'),
    'epiano':       dict(program=4,  family='epiano',  lo=40, hi=86, pan=-0.1, label='エレピ（ローズ）'),
    'celesta':      dict(program=8,  family='mallet',  lo=60, hi=100, pan=0.35, label='チェレスタ'),
    'glock':        dict(program=9,  family='mallet',  lo=72, hi=105, pan=0.3, label='鉄琴'),
    'vibes':        dict(program=11, family='mallet',  lo=53, hi=89, pan=0.2,  label='ヴィブラフォン'),
    'marimba':      dict(program=12, family='mallet',  lo=45, hi=96, pan=-0.25, label='マリンバ'),
    'nylon_guitar': dict(program=24, family='pluck',   lo=40, hi=81, pan=-0.3, label='ガットギター'),
    'steel_guitar': dict(program=25, family='pluck',   lo=40, hi=81, pan=0.35, label='アコースティックギター'),
    'clean_guitar': dict(program=27, family='pluck',   lo=40, hi=88, pan=0.4,  label='エレキギター（クリーン）'),
    'upright_bass': dict(program=32, family='bass',    lo=28, hi=55, pan=0.0,  label='ウッドベース'),
    'finger_bass':  dict(program=33, family='bass',    lo=28, hi=55, pan=0.0,  label='エレキベース'),
    'violin':       dict(program=40, family='bowed',   lo=55, hi=93, pan=-0.45, label='ヴァイオリン'),
    'viola':        dict(program=41, family='bowed',   lo=48, hi=79, pan=0.2,  label='ヴィオラ'),
    'cello':        dict(program=42, family='bowed',   lo=36, hi=72, pan=0.35, label='チェロ'),
    'contrabass':   dict(program=43, family='bowed',   lo=28, hi=55, pan=0.5,  label='コントラバス'),
    'pizz':         dict(program=45, family='pluck',   lo=36, hi=84, pan=0.15, label='弦のピチカート'),
    'harp':         dict(program=46, family='pluck',   lo=36, hi=96, pan=-0.55, label='ハープ'),
    'timpani':      dict(program=47, family='timpani', lo=38, hi=57, pan=0.1,  label='ティンパニ'),
    'strings':      dict(program=48, family='bowed',   lo=40, hi=88, pan=0.0,  label='弦合奏'),
    'slow_strings': dict(program=49, family='bowed',   lo=40, hi=88, pan=0.0,  label='弦合奏（ゆるやか）'),
    'choir':        dict(program=52, family='choir',   lo=48, hi=79, pan=0.0,  label='合唱（アー）'),
    'oohs':         dict(program=53, family='choir',   lo=48, hi=79, pan=0.0,  label='合唱（ウー）'),
    'trumpet':      dict(program=56, family='brass',   lo=55, hi=82, pan=0.15, label='トランペット'),
    'trombone':     dict(program=57, family='brass',   lo=40, hi=72, pan=0.3,  label='トロンボーン'),
    'tuba':         dict(program=58, family='brass',   lo=28, hi=55, pan=0.35, label='チューバ'),
    'horn':         dict(program=60, family='brass',   lo=41, hi=77, pan=-0.2, label='ホルン'),
    'oboe':         dict(program=68, family='reed',    lo=58, hi=91, pan=-0.1, label='オーボエ'),
    'bassoon':      dict(program=70, family='reed',    lo=34, hi=72, pan=0.1,  label='ファゴット'),
    'clarinet':     dict(program=71, family='reed',    lo=50, hi=89, pan=0.15, label='クラリネット'),
    'flute':        dict(program=73, family='flute',   lo=60, hi=96, pan=-0.2, label='フルート'),
    'whistle':      dict(program=78, family='flute',   lo=60, hi=96, pan=0.1,  label='口笛'),
    'drums':        dict(program=0,  family='drums',   lo=0, hi=127, pan=0.0,  label='ドラム', drum=True, kit=0),
    'brush_drums':  dict(program=40, family='drums',   lo=0, hi=127, pan=0.0,  label='ブラシのドラム', drum=True, kit=40),
    'orch_perc':    dict(program=48, family='drums',   lo=0, hi=127, pan=0.0,  label='オーケストラの打楽器', drum=True, kit=48),
}

# GM の打楽器（チャンネル10）の音の番号
DRUM = dict(kick=36, side=37, snare=38, clap=39, snare2=40, tom_lo=41, hat=42, tom_mid=45, hat_pedal=44,
            hat_open=46, tom_hi=48, crash=49, ride=51, china=52, ride_bell=53, tamb=54, splash=55,
            crash2=57, ride2=59, shaker=70, triangle=81, triangle_mute=80, woodblock=76, bass_drum=35,
            cabasa=69, conga=63, conga_lo=64, bongo=60)

# 区切りの役割ごとの既定の勢い（0〜1）
# @couple:section-roles 役割を足したら spec.ROLE_ALIAS と SKILL.md の表と GUI の選択肢も
ROLE_ENERGY = {'intro': 0.35, 'verse': 0.5, 'build': 0.68, 'chorus': 0.92, 'break': 0.3, 'bridge': 0.55, 'outro': 0.62}
# 区切りの中で勢いが上がる幅（build は頭が低く、終わりが高い。楽器が小節ごとに入ってくる）
ROLE_RAMP = {'build': 0.3}
ROLE_CADENCE = {'intro': None, 'verse': None, 'build': 'half', 'chorus': None, 'break': None, 'bridge': 'half', 'outro': 'authentic'}

# 和声進行の手持ち。1語=1小節。「ii7-V7」は1小節に2つ。tags は気分、mode は major / minor / any。
# 同じ曲の中では、なるべく違う進行を区切りごとに選ぶ（compose.choose_progression）。
PROGRESSIONS = [
    # 長調
    dict(p='I V vi IV', tags=['bright', 'epic'], mode='major'),
    dict(p='vi IV I V', tags=['bright', 'epic', 'tender'], mode='major'),
    dict(p='I iii IV V', tags=['bright', 'playful'], mode='major'),
    dict(p='IV V iii vi', tags=['tender', 'bright'], mode='major'),
    dict(p='I I/3 IV V', tags=['bright', 'calm', 'playful'], mode='major'),
    dict(p='IV I/3 ii V', tags=['tender', 'calm'], mode='major'),
    dict(p='I vi ii V', tags=['playful', 'calm'], mode='major'),
    dict(p='IV V/vi vi I/5', tags=['epic', 'tender'], mode='major'),
    dict(p='I V/7 vi I/5 IV I/3 ii V', tags=['tender', 'epic'], mode='major'),
    dict(p='ii V I vi', tags=['playful', 'calm'], mode='major'),
    dict(p='IV iv I vi', tags=['tender'], mode='major'),
    dict(p='I bVII IV I', tags=['epic', 'bright'], mode='major'),
    dict(p='vi V IV V', tags=['tense', 'epic'], mode='major'),
    dict(p='IVmaj7 V7/vi vi7 I7', tags=['tender', 'calm'], mode='major', styles=['lofi', 'acoustic']),
    dict(p='ii7 V7 Imaj7 vi7', tags=['calm', 'tender', 'playful'], mode='major', styles=['lofi']),
    dict(p='IVmaj7 iii7 ii7 Imaj7', tags=['calm', 'tender'], mode='major', styles=['lofi']),
    dict(p='Imaj7 vi7 ii7 V7', tags=['calm', 'playful', 'bright'], mode='major', styles=['lofi']),
    dict(p='IVmaj7 V7 iii7 vi7', tags=['tender', 'calm'], mode='major', styles=['lofi']),
    dict(p='bVI bVII I I', tags=['epic'], mode='major'),
    dict(p='I IV vi V', tags=['bright', 'playful'], mode='major'),
    dict(p='vi ii V I', tags=['tense', 'tender'], mode='major'),
    # 短調
    dict(p='i VI III VII', tags=['epic', 'tense'], mode='minor'),
    dict(p='i iv VI V', tags=['tense', 'epic'], mode='minor'),
    dict(p='VI VII i i', tags=['epic', 'tense'], mode='minor'),
    dict(p='i VII VI VII', tags=['epic', 'tense'], mode='minor'),
    dict(p='VI III VII i', tags=['epic', 'tender'], mode='minor'),
    dict(p='i v VI iv', tags=['tender', 'calm'], mode='minor'),
    dict(p='iv VI III VII', tags=['epic', 'bright'], mode='minor'),
    dict(p='i i/3 iv V', tags=['tense'], mode='minor'),
    dict(p='VI iv i V', tags=['tense', 'epic'], mode='minor'),
    dict(p='i III VII VI', tags=['bright', 'epic'], mode='minor'),
    dict(p='i7 iv7 VIImaj7 IIImaj7', tags=['calm', 'tender'], mode='minor', styles=['lofi']),
]

# 終止の形（区切りの最後の1〜2小節に上書きする）
CADENCES = {
    'major': {'authentic': ['ii7', 'V7', 'I'], 'half': ['IV', 'V'], 'plagal': ['IV', 'I'], 'deceptive': ['V', 'vi'],
              'epic': ['bVI', 'bVII', 'I']},
    'minor': {'authentic': ['iv', 'V7', 'i'], 'half': ['iv', 'V'], 'plagal': ['iv', 'i'], 'deceptive': ['V', 'VI'],
              'epic': ['VI', 'VII', 'I']},
}

# ── 様式のひな形 ─────────────────────────────────
# parts：楽器の役。e=(入る勢い, 抜ける勢い)。区切りの energy がこの間にあるとき鳴る。
#   fn：melody / double（旋律の重ね）/ counter（対旋律）/ pad / comp / arp / bass / ostinato / drums / timp / perc
#   pattern：fn ごとの型（compose.py の PATTERNS）。oct：旋律の重ねのオクターブ。level：混ぜるときの相対音量 dB。
# @couple:style-list 様式を足したら SKILL.md の様式の表と README と GUI の選択肢も
STYLES = {
    'bright_pop': dict(
        label='明るいポップ', tempo=120, key='D major', mood='bright', feel='straight',
        description='ピアノとアコギが刻み、ベースとドラムが前へ押す。サビで弦と鉄琴、合唱が足される。',
        reverb=dict(size=0.55, wet=0.16), master=dict(air=1.5, glue=2.0),
        humanize=dict(timing_ms=9, velocity=8),
        parts=[
            dict(id='lead', inst='piano', fn='melody', e=(0.0, 0.72), level=-1),
            dict(id='lead_hi', inst='clean_guitar', fn='melody', e=(0.72, 1.01), level=-3),
            dict(id='glock', inst='glock', fn='double', oct=1, e=(0.62, 1.01), level=-10),
            dict(id='keys', inst='piano', fn='comp', pattern='pop_syncop', e=(0.2, 1.01), level=-8),
            dict(id='guitar', inst='steel_guitar', fn='comp', pattern='strum', e=(0.42, 1.01), level=-7),
            dict(id='strings', inst='strings', fn='pad', e=(0.55, 1.01), level=-8),
            dict(id='choir', inst='oohs', fn='pad', e=(0.85, 1.01), level=-12, register=-12),
            dict(id='vln', inst='violin', fn='counter', e=(0.8, 1.01), level=-9),
            dict(id='bass', inst='finger_bass', fn='bass', pattern='pop_eighths', e=(0.3, 1.01), level=-1),
            dict(id='drums', inst='drums', fn='drums', pattern='pop', e=(0.36, 1.01), level=-3),
            dict(id='shaker', inst='drums', fn='perc', pattern='shaker', e=(0.2, 0.75), level=-14),
        ]),
    'acoustic': dict(
        label='やわらかいアコースティック', tempo=92, key='G major', mood='tender', feel='straight',
        description='ガットギターの指弾きが中心。チェロが寄り添い、ブラシのドラムとウッドベースがそっと支える。',
        reverb=dict(size=0.6, wet=0.2), master=dict(air=1.0, glue=1.5),
        humanize=dict(timing_ms=14, velocity=10),
        parts=[
            dict(id='lead', inst='piano', fn='melody', e=(0.0, 0.62), level=-2),
            dict(id='flute', inst='flute', fn='melody', e=(0.62, 1.01), level=-4),
            dict(id='guitar', inst='nylon_guitar', fn='arp', pattern='travis', e=(0.0, 1.01), level=-4),
            dict(id='cello', inst='cello', fn='counter', e=(0.45, 1.01), level=-8),
            dict(id='strings', inst='slow_strings', fn='pad', e=(0.62, 1.01), level=-11),
            dict(id='glock', inst='glock', fn='arp', pattern='sparkle', e=(0.75, 1.01), level=-15),
            dict(id='bass', inst='upright_bass', fn='bass', pattern='two_feel', e=(0.35, 1.01), level=-5),
            dict(id='drums', inst='brush_drums', fn='drums', pattern='brush', e=(0.45, 1.01), level=-8),
            dict(id='shaker', inst='brush_drums', fn='perc', pattern='shaker', e=(0.3, 1.01), level=-16),
        ]),
    'cinematic': dict(
        label='映画風オーケストラ', tempo=100, key='D minor', mood='epic', feel='straight', ending_cadence='epic',
        description='弦の刻みとハープで始まり、ホルンと合唱、ティンパニで山場を作る。静かな所は木管が歌う。',
        reverb=dict(size=0.85, wet=0.3), master=dict(air=1.0, glue=2.0),
        humanize=dict(timing_ms=16, velocity=9),
        parts=[
            dict(id='oboe', inst='oboe', fn='melody', e=(0.0, 0.5), level=-3),
            dict(id='vln_lead', inst='violin', fn='melody', e=(0.5, 0.8), level=-2),
            dict(id='horns_lead', inst='horn', fn='melody', e=(0.8, 1.01), level=-2),
            dict(id='tpt', inst='trumpet', fn='double', oct=0, e=(0.9, 1.01), level=-8),
            dict(id='harp', inst='harp', fn='arp', pattern='harp', e=(0.0, 0.75), level=-8),
            dict(id='celesta', inst='celesta', fn='arp', pattern='sparkle', e=(0.0, 0.45), level=-14),
            dict(id='ostinato', inst='strings', fn='ostinato', pattern='spiccato', e=(0.45, 1.01), level=-7),
            dict(id='strings', inst='slow_strings', fn='pad', e=(0.0, 1.01), level=-8),
            dict(id='cello', inst='cello', fn='counter', e=(0.35, 1.01), level=-7),
            dict(id='low_brass', inst='trombone', fn='pad', e=(0.75, 1.01), level=-10, register=-12),
            dict(id='choir', inst='choir', fn='pad', e=(0.7, 1.01), level=-9),
            dict(id='bass', inst='contrabass', fn='bass', pattern='sustain', e=(0.0, 1.01), level=-6),
            dict(id='timp', inst='timpani', fn='timp', e=(0.5, 1.01), level=-6),
            dict(id='perc', inst='orch_perc', fn='drums', pattern='epic', e=(0.7, 1.01), level=-7),
        ]),
    'lofi': dict(
        label='ローファイ', tempo=78, key='F major', mood='calm', feel='swing', ending_rit=False,
        description='ローズの7th和音と跳ねたドラム、ウッドベース。テープの揺れとレコードの針音をかける。',
        reverb=dict(size=0.45, wet=0.15), master=dict(air=-2.5, glue=2.5, lofi=True),
        humanize=dict(timing_ms=18, velocity=12, swing=0.6),
        parts=[
            dict(id='lead', inst='vibes', fn='melody', e=(0.0, 1.01), level=-4),
            dict(id='keys', inst='epiano', fn='comp', pattern='lofi_stabs', e=(0.0, 1.01), level=-3),
            dict(id='guitar', inst='clean_guitar', fn='arp', pattern='lofi_pick', e=(0.6, 1.01), level=-12),
            dict(id='pad', inst='slow_strings', fn='pad', e=(0.7, 1.01), level=-15, register=-12),
            dict(id='bass', inst='upright_bass', fn='bass', pattern='lofi', e=(0.25, 1.01), level=-4),
            dict(id='drums', inst='drums', fn='drums', pattern='lofi', e=(0.3, 1.01), level=-5),
        ]),
    'playful': dict(
        label='遊び心（ピチカートと木管）', tempo=126, key='F major', mood='playful', feel='straight',
        description='弦のピチカートとファゴットが跳ね、クラリネットとマリンバが歌う。アプリの操作紹介に合う軽さ。',
        reverb=dict(size=0.5, wet=0.17), master=dict(air=1.5, glue=1.5),
        humanize=dict(timing_ms=10, velocity=9),
        parts=[
            dict(id='lead', inst='clarinet', fn='melody', e=(0.0, 0.7), level=-2, articulation='staccato'),
            dict(id='lead_hi', inst='flute', fn='melody', e=(0.7, 1.01), level=-2, articulation='staccato'),
            dict(id='marimba', inst='marimba', fn='double', oct=0, e=(0.55, 1.01), level=-9),
            dict(id='pizz', inst='pizz', fn='comp', pattern='oompah', e=(0.0, 1.01), level=-4),
            dict(id='bassoon', inst='bassoon', fn='bass', pattern='oompah_bass', e=(0.3, 1.01), level=-6),
            dict(id='glock', inst='glock', fn='arp', pattern='sparkle', e=(0.6, 1.01), level=-14),
            dict(id='strings', inst='strings', fn='pad', e=(0.8, 1.01), level=-12),
            dict(id='perc', inst='drums', fn='perc', pattern='playful', e=(0.4, 1.01), level=-10),
        ]),
}
