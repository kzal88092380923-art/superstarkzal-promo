#!/usr/bin/env python3
"""영상 후보 찾기: pet/scout/<이름>.json 의 검색어마다 Pixabay 후보 12개를 썸네일 시트로 만든다.

시트 각 칸에 Pixabay 영상 id·길이·세로 여부를 적어 두어, 주문서에 id 로 바로 고정할 수 있다.
"""
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
import make_reel as mr  # noqa: E402

COLS, TW, TH, PAD, HEAD = 6, 240, 320, 10, 46


def sheet(rows, out):
    total_rows = sum((len(c) + COLS - 1) // COLS for _, c in rows)
    hgt = len(rows) * HEAD + total_rows * (TH + 40 + PAD) + PAD
    im = Image.new("RGB", (COLS * (TW + PAD) + PAD, hgt), "#111")
    d = ImageDraw.Draw(im)
    f, fs = mr.font(28), mr.font(22)
    y = PAD
    for q, cands in rows:
        d.text((PAD, y + 6), q, font=f, fill="#ffd400")
        y += HEAD
        for k, c in enumerate(cands):
            x = PAD + (k % COLS) * (TW + PAD)
            if k and k % COLS == 0:
                y += TH + 40 + PAD
            try:
                t = Image.open(io.BytesIO(mr.http_get(c["thumb"], 30))).convert("RGB")
                s = max(TW / t.width, TH / t.height)
                t = t.resize((int(t.width * s) + 1, int(t.height * s) + 1))
                l, tp = (t.width - TW) // 2, (t.height - TH) // 2
                im.paste(t.crop((l, tp, l + TW, tp + TH)), (x, y))
            except Exception:
                d.rectangle([x, y, x + TW, y + TH], fill="#333")
            tag = f"{c['id']}  {c['duration']}s{' 세로' if c['vertical'] else ''}"
            d.text((x, y + TH + 6), tag, font=fs, fill="#fff")
        y += TH + 40 + PAD
    im.save(out, quality=82)


def main(path):
    path = Path(path)
    spec = json.loads(path.read_text(encoding="utf-8"))
    rows, listing = [], {}
    for q in spec["queries"]:
        cands = mr.search(q, float(spec.get("min_seconds", 6)))
        if spec.get("vertical_only"):
            cands = [c for c in cands if c["vertical"]]
        cands = cands[:12]
        rows.append((q, cands))
        listing[q] = [{k: c[k] for k in ("id", "duration", "vertical", "page")} for c in cands]
        print(f"{q}: {len(cands)}개")
    sheet(rows, path.with_suffix(".jpg"))
    path.with_suffix(".result.json").write_text(json.dumps(listing, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        if not p.endswith(".result.json"):
            main(p)
