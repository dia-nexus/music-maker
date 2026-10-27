# 音源の取り方と許諾

2026-10-22 に、このクラウドのコンテナから実際に取って確かめた。細かい調べ（候補の一覧、大きさ、取れた URL、SFZ を鳴らす道具）は `sound-sources-research.md` に残してある。

## 使っているもの

GeneralUser GS v2.0.3（S. Christian Collins）は、`python3 tools/music/music.py setup` が `https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/GeneralUser-GS.sf2`（32,319,396 バイト）から取り、`tools/music/.cache/` に置く。許諾の本文も同じ所に `GeneralUser-GS-LICENSE.txt` として落とす。要点は「自作の曲には、個人でも商用でも制限なく使ってよい。ソフトに同梱してよく、改変してもよい」。作者は、昔ほかの配布元から集めた一部のサンプルについて出どころを100%は確かめられないと書いているが、2000年の公開から苦情は一度も無いとも書いている。作った曲を動画に付ける使い方なら問題にならない。

github.com のアーカイブ（zip）と Releases はこのコンテナから 403 で取れなかったが、raw.githubusercontent.com の個別のファイルは取れた。

FluidR3_GM（Frank Wen、MIT）は apt の `fluid-soundfont-gm` で入る。setup は FluidSynth と一緒にこれも入れるので、GeneralUser GS が取れなかったときの予備になる。

Salamander Grand Piano V3（Alexander Holm、CC-BY 3.0）は `setup --extra salamander` で freepats.zenvoid.org から取る。落とすのは約300MB、展開した SoundFont は 1.27GB。ピアノ（GM の0番）の楽器だけがこれで鳴る。CC-BY なので、曲を外に出すときは「Salamander Grand Piano V3 by Alexander Holm, CC-BY 3.0」と添える。

## 取れなかったもの・使わなかったもの

合唱は、CC0 や CC-BY で実用になる音源が見つからなかった。GM の Choir Aahs のままにしている。

Hugging Face にある SoundFont の寄せ集め（soundfonts4u など）は表示が CC-BY-NC-SA（非営利限定）で、宣伝動画には使えない。apt の MuseScore General（MIT）や TimGM6mb（GPL-2）は、どれも GM の枠の中の音源で、GeneralUser GS より良くなる見込みが無いので入れていない。

## これから足せるもの

VSCO-2 Community Edition（CC0、弦・木管・金管、SFZ と WAV）は raw.githubusercontent.com から楽器単位で取れる。鳴らすには sfizz の `sfizz_render` が要り、Ubuntu の apt には無いが、openSUSE OBS の .deb を直接入れると動いた（署名鍵が期限切れのため apt のリポジトリとしては足せない）。`render.py` は楽器ごとに MIDI を分けて書き出しているので、楽器ごとに FluidSynth と sfizz_render を振り分ける形で足せる。
