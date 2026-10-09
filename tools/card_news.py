#!/usr/bin/env python3
"""카드뉴스(인스타 캐러셀) 만들기: pet/cards/<이름>.json → pet/cards/out/<이름>/01.png …

1080×1350(4:5) PNG. 반려동물 테스트 앱과 같은 톤(크림 바탕·잉크 테두리·손글씨 제목·결과 얼굴 그림).
주문서 형식은 pet/cards/*.json 예시 참고. HTML을 Playwright(Chromium)로 찍는다.
"""
import asyncio
import html
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "tools" / "fonts"
FACES = json.loads((ROOT / "pet" / "cards" / "faces.json").read_text(encoding="utf-8"))
W, H = 1080, 1350
INK, CREAM, HOT = "#1C1B18", "#FBF6EC", "#FF4FA3"


def esc(s):
    return html.escape(s).replace("\n", "<br>")


def css(accent):
    f = FONTS.as_uri()
    return f"""
@font-face{{font-family:Gaegu;src:url('{f}/Gaegu-Bold-korean.woff2');font-weight:700}}
@font-face{{font-family:GaeguL;src:url('{f}/Gaegu-Bold-latin.woff2');font-weight:700}}
@font-face{{font-family:Pre;src:url('{f}/Pretendard-SemiBold.otf');font-weight:600}}
@font-face{{font-family:Pre;src:url('{f}/Pretendard-Bold.otf');font-weight:700}}
@font-face{{font-family:Pre;src:url('{f}/Pretendard-ExtraBold.otf');font-weight:800}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{word-break:keep-all;width:{W}px;height:{H}px;background:{CREAM};color:{INK};font-family:Pre,sans-serif;overflow:hidden}}
.page{{position:relative;width:{W}px;height:{H}px;padding:84px 84px 96px;display:flex;flex-direction:column}}
.top{{display:flex;justify-content:space-between;align-items:center;font-weight:700;font-size:30px}}
.chip{{border:3px solid {INK};border-radius:999px;padding:10px 22px;background:#fff}}
.num{{opacity:.45}}
.hand{{font-family:GaeguL,Gaegu,sans-serif;font-weight:700;letter-spacing:-0.03em;-webkit-text-stroke:1.2px {INK};word-break:keep-all}}
.scrib{{display:block}}
.face{{width:100%;height:100%}}
.facewrap{{border:4px solid {INK};border-radius:50%;background:{accent};display:flex;align-items:center;justify-content:center}}
.facewrap svg{{width:78%;height:78%}}
.card{{border:4px solid {INK};border-radius:44px;background:#fff}}
.foot{{position:absolute;left:84px;right:84px;bottom:64px;display:flex;justify-content:space-between;font-size:28px;font-weight:700;opacity:.55}}
"""


SCRIBBLE = (f'<svg class="scrib" width="560" height="26" viewBox="0 0 190 12" fill="none">'
            f'<path d="M4 8 C 40 3, 80 11, 120 6 C 145 3, 170 9, 186 5" stroke="{HOT}" '
            f'stroke-width="4.5" stroke-linecap="round"/></svg>')

CHECKBOX = (f'<svg width="120" height="120" viewBox="0 0 60 60" fill="none" stroke="{INK}" '
            'stroke-width="4" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M8 10 C 20 8, 40 8, 50 9 C 51 22, 51 38, 50 50 C 36 51, 20 51, 9 50 C 8 36, 8 22, 8 10 Z" fill="#fff"/>'
            f'<path d="M17 30 L27 40 L56 4" stroke="{HOT}" stroke-width="6"/></svg>')


def cover(o, total):
    return f"""<div class="page">
  <div class="top"><span class="chip">{esc(o['series'])}</span><span class="num">1/{total}</span></div>
  <div style="margin-top:70px">
    <div class="hand" style="font-size:{o.get('title_size', 96)}px;line-height:1.1">{esc(o['title'])}</div>
    <div style="margin-top:18px">{SCRIBBLE}</div>
  </div>
  <div style="flex:1;display:flex;align-items:center;justify-content:center">
    <div class="facewrap" style="width:520px;height:520px">{FACES[o['face']]}</div>
  </div>
  <div style="display:flex;justify-content:space-between;align-items:center">
    <div style="font-size:40px;font-weight:700;line-height:1.4">{esc(o['sub'])}</div>
    <div class="hand" style="font-size:54px;background:{o['accent']};border:4px solid {INK};border-radius:999px;padding:12px 34px">넘겨봐 →</div>
  </div>
</div>"""


def item(o, i, it, total):
    return f"""<div class="page">
  <div class="top"><span class="chip">{esc(o['series'])}</span><span class="num">{i + 2}/{total}</span></div>
  <div class="card" style="flex:1;margin-top:48px;padding:72px 64px;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;gap:28px">
      {CHECKBOX}
      <div class="hand" style="font-size:64px;opacity:.35">{i + 1:02d}</div>
    </div>
    <div class="hand" style="margin-top:56px;font-size:{it.get('size', 92)}px;line-height:1.15">{esc(it['text'])}</div>
    <div style="margin-top:40px;font-size:38px;font-weight:600;line-height:1.55;opacity:.7">{esc(it.get('note', ''))}</div>
    <div style="flex:1"></div>
    <div style="display:flex;justify-content:flex-end">
      <div class="facewrap" style="width:220px;height:220px">{FACES[o['face']]}</div>
    </div>
  </div>
  <div style="height:40px"></div>
</div>"""


def cta(o, total):
    return f"""<div class="page" style="background:{o['accent']}">
  <div class="top"><span class="chip">{esc(o['series'])}</span><span class="num">{total}/{total}</span></div>
  <div style="margin-top:80px">
    <div class="hand" style="font-size:84px;line-height:1.12">{esc(o['cta_title'])}</div>
    <div style="margin-top:36px;font-size:42px;font-weight:700;line-height:1.5">{esc(o['cta_sub'])}</div>
  </div>
  <div style="flex:1;display:flex;align-items:center;justify-content:center">
    <div class="facewrap" style="width:300px;height:300px;background:#fff">{FACES[o['face']]}</div>
  </div>
  <div class="card" style="padding:44px 48px;text-align:center">
    <div style="font-size:40px;font-weight:700">12문항 · 1분 · 8가지 타입</div>
    <div style="margin-top:18px;font-size:56px;font-weight:800;letter-spacing:-0.01em">pet.superstarkzal.com</div>
    <div style="margin-top:16px;font-size:34px;font-weight:600;opacity:.6">프로필 링크에서 바로 해볼 수 있어</div>
  </div>
</div>"""


async def render(path):
    path = Path(path).resolve()
    o = json.loads(path.read_text(encoding="utf-8"))
    total = len(o["items"]) + 2
    pages = [cover(o, total)] + [item(o, i, it, total) for i, it in enumerate(o["items"])] + [cta(o, total)]
    out = path.parent / "out" / path.stem
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": W, "height": H})
        for n, body in enumerate(pages, 1):
            tmp = out / "_page.html"
            tmp.write_text(f"<html><head><meta charset='utf-8'><style>{css(o['accent'])}</style></head><body>{body}</body></html>", encoding="utf-8")
            await pg.goto(tmp.as_uri())
            await pg.evaluate("document.fonts.ready")
            ok = await pg.evaluate("document.fonts.check('700 64px Gaegu')")
            if not ok:
                raise SystemExit("Gaegu 폰트 로드 실패")
            await pg.wait_for_timeout(150)
            await pg.screenshot(path=str(out / f"{n:02d}.png"))
        await b.close()
    (out / "_page.html").unlink(missing_ok=True)
    # 한눈에 보기용 시트
    from PIL import Image
    ims = [Image.open(out / f"{n:02d}.png") for n in range(1, total + 1)]
    tw, th = 270, 338
    sheet = Image.new("RGB", (tw * min(total, 4) + 10 * (min(total, 4) + 1), (th + 10) * ((total + 3) // 4) + 10), "#222")
    for k, im in enumerate(ims):
        sheet.paste(im.resize((tw, th)), (10 + (k % 4) * (tw + 10), 10 + (k // 4) * (th + 10)))
    sheet.save(out / "sheet.jpg", quality=85)
    print(f"{path.stem}: {total}장 → {out}")


if __name__ == "__main__":
    for a in sys.argv[1:]:
        if a.endswith(".json") and not a.endswith("faces.json"):
            asyncio.run(render(a))
