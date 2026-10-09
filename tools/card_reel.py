#!/usr/bin/env python3
"""카드뉴스 → 슬라이드 릴스: pet/cards/<이름>.json 의 out/<이름>/NN.png 를 9:16 영상으로.

카드(1080×1350)를 크림 바탕 1080×1920 위에 올리고, 옆으로 넘기는 전환 + 합성 배경음(넘길 때 '뽁', 끝에 '띠링').
결과: pet/reels/<이름>-slides.mp4
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import reel_v2 as rv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CREAM = "0xFBF6EC"
Y = 200          # 카드 위쪽 여백 (아래 자막 영역 380px 피하기)
T_COVER, T_ITEM, T_CTA, XF = 2.2, 1.7, 3.0, 0.3


def main(order):
    order = Path(order).resolve()
    o = json.loads(order.read_text(encoding="utf-8"))
    src = order.parent / "out" / order.stem
    pngs = sorted(src.glob("[0-9][0-9].png"))
    if not pngs:
        raise SystemExit(f"카드 이미지 없음: {src} (먼저 card_news.py 실행)")
    durs = [T_COVER] + [T_ITEM] * (len(pngs) - 2) + [T_CTA]
    total = sum(durs) - XF * (len(pngs) - 1)
    out = ROOT / "pet" / "reels" / f"{order.stem}-slides.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # 넘길 때마다 '뽁', 마지막 장에서 '띠링'
        starts, t = [], 0.0
        for d in durs[:-1]:
            t += d - XF
            starts.append(t)
        music, sfx = rv.synth_music(total, o.get("music", "default"), [s + 0.05 for s in starts], starts[-1] + 0.4)
        rv.write_wav(str(td / "a.wav"), 0.55 * music + sfx)

        inputs, chains = [], []
        for i, (p, d) in enumerate(zip(pngs, durs)):
            inputs += ["-loop", "1", "-t", f"{d}", "-i", str(p)]
            chains.append(f"[{i}:v]scale=1080:1350,pad=1080:1920:0:{Y}:color={CREAM},setsar=1,fps=30,format=yuv420p[v{i}]")
        last, off = "v0", 0.0
        for i in range(1, len(pngs)):
            off += durs[i - 1] - XF
            chains.append(f"[{last}][v{i}]xfade=transition=slideleft:duration={XF}:offset={off:.3f}[x{i}]")
            last = f"x{i}"
        n = len(pngs)
        chains.append(f"[{n}:a]atrim=0:{total:.3f},loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100[a]")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-i", str(td / "a.wav"),
               "-filter_complex", ";".join(chains), "-map", f"[{last}]", "-map", "[a]",
               "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True)
    print(f"{out.name}: {total:.1f}초")


if __name__ == "__main__":
    for a in sys.argv[1:]:
        if a.endswith(".json") and not a.endswith("faces.json"):
            main(a)
