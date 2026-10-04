#!/usr/bin/env python3
"""주문서(JSON) 한 개로 9:16 릴스를 만든다.

사용: python tools/make_reel.py pet/orders/<이름>.json
환경변수: PIXABAY_API_KEY (필수), REEL_TEST=1 이면 API 없이 테스트 영상으로 대체
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
SAFE_TOP, SAFE_BOTTOM = 230, 430  # 인스타 UI에 가려지는 영역
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]
FONT_INDEX = 1  # ttc 안의 KR 글꼴
UA = {"User-Agent": "superstarkzal-promo/1.0"}
TEST = os.environ.get("REEL_TEST") == "1"


def font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, size, index=FONT_INDEX)
    raise SystemExit("한글 글꼴을 찾지 못함 (fonts-noto-cjk 설치 필요)")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("명령 실패: " + " ".join(cmd[:4]) + "\n" + r.stderr[-1500:])


def http_get(url, timeout=120):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------- Pixabay ----------

def search(query, need_seconds, video_id=None):
    """조건에 맞는 후보 목록. 세로 영상을 앞에 둔다. video_id 가 있으면 그 영상 하나만."""
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key:
        raise SystemExit("PIXABAY_API_KEY 가 비어 있음 (저장소 Secrets 확인)")
    params = {"key": key, "id": video_id} if video_id else \
        {"key": key, "q": query, "per_page": 30, "safesearch": "true", "video_type": "film"}
    url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(params)
    hits = json.loads(http_get(url, 30)).get("hits", [])
    cands = []
    for h in hits:
        if h.get("duration", 0) < need_seconds + 0.5 and not video_id:
            continue
        rend = pick_rendition(h["videos"])
        if not rend:
            continue
        cands.append({
            "id": h["id"], "page": h["pageURL"], "user": h.get("user", ""),
            "duration": h["duration"], "url": rend["url"],
            "w": rend["width"], "h": rend["height"],
            "thumb": rend.get("thumbnail") or h["videos"]["tiny"].get("thumbnail", ""),
            "vertical": rend["height"] > rend["width"],
        })
    cands.sort(key=lambda c: not c["vertical"])  # 세로 먼저, 나머지는 검색 순서 유지
    return cands


def pick_rendition(videos):
    """1080x1920 을 채울 수 있는 가장 가벼운 화질. 없으면 가장 큰 것."""
    rs = [v for v in videos.values() if v.get("url") and v.get("width") and v.get("height")]
    if not rs:
        return None
    rs.sort(key=lambda v: v["width"] * v["height"])
    for v in rs:
        scale = max(W / v["width"], H / v["height"])
        if scale <= 1.35 and v.get("size", 0) < 80_000_000:
            return v
    ok = [v for v in rs if v.get("size", 0) < 80_000_000]
    return (ok or rs)[-1]


def candidates_sheet(scenes_cands, out_path):
    """장면별 후보 썸네일 표 — 번호를 보고 주문서의 pick 을 바꾼다."""
    cols, tw, th, pad, label = 6, 240, 320, 12, 44
    rows = len(scenes_cands)
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + label + pad) + pad), "#111")
    d = ImageDraw.Draw(sheet)
    f = font(26)
    for r, (title, cands, picked) in enumerate(scenes_cands):
        y = pad + r * (th + label + pad)
        d.text((pad, y + 4), f"장면 {r + 1}: {title}", font=f, fill="#fff")
        for c, cand in enumerate(cands[:cols]):
            x = pad + c * (tw + pad)
            try:
                im = Image.open(io.BytesIO(http_get(cand["thumb"], 30))).convert("RGB")
                s = max(tw / im.width, th / im.height)
                im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1))
                l, t = (im.width - tw) // 2, (im.height - th) // 2
                im = im.crop((l, t, l + tw, t + th))
                sheet.paste(im, (x, y + label))
            except Exception:
                d.rectangle([x, y + label, x + tw, y + label + th], fill="#333")
            col = "#ffd400" if c == picked else "#000"
            d.rectangle([x, y + label, x + 56, y + label + 44], fill=col)
            d.text((x + 16, y + label + 4), str(c), font=f, fill="#000" if c == picked else "#fff")
            if c == picked:
                d.rectangle([x, y + label, x + tw - 1, y + label + th - 1], outline="#ffd400", width=5)
    sheet.save(out_path, quality=85)


# ---------- 자막 ----------

def wrap(draw, text, f, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            trial = (cur + " " + word).strip()
            if draw.textlength(trial, font=f) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def overlay_png(badge, text, sub, path):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if badge:  # 상단 고정 띠: 무슨 테스트인지 첫 장면부터 보이게
        f = font(54)
        tw = d.textlength(badge, font=f)
        bw, bh = tw + 80, 104
        x, y = (W - bw) / 2, SAFE_TOP
        d.rounded_rectangle([x, y, x + bw, y + bh], radius=52, fill=(255, 212, 0, 255))
        d.text((W / 2, y + bh / 2 - 4), badge, font=f, fill=(20, 20, 20, 255), anchor="mm")
    if text:
        f = font(92)
        lines = wrap(d, text, f, W - 140)
        lh = 122
        total = lh * len(lines)
        y = H - SAFE_BOTTOM - total - (90 if sub else 0)
        for i, ln in enumerate(lines):
            d.text((W / 2, y + i * lh), ln, font=f, fill="white", anchor="ma",
                   stroke_width=10, stroke_fill=(0, 0, 0, 255))
        if sub:
            fs = font(52)
            d.text((W / 2, y + total + 14), sub, font=fs, fill=(255, 212, 0, 255), anchor="ma",
                   stroke_width=7, stroke_fill=(0, 0, 0, 255))
    im.save(path)


# ---------- 영상 ----------

def render_scene(src, start, seconds, png, out):
    vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"fps={FPS},setsar=1,eq=saturation=1.08,tpad=stop_mode=clone:stop_duration=6[v];[v][1:v]overlay=0:0[o]")
    run(["ffmpeg", "-y", "-ss", f"{start}", "-t", f"{seconds}", "-i", src, "-loop", "1", "-t", f"{seconds}",
         "-i", png, "-filter_complex", vf, "-map", "[o]", "-t", f"{seconds}", "-an",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", out])


# ---------- 소리 ----------

VOICES = {  # 짧은 별칭 → edge-tts 목소리
    "여자": "ko-KR-SunHiNeural",
    "남자": "ko-KR-InJoonNeural",
    "남자2": "ko-KR-HyunsuMultilingualNeural",
}


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def tts(text, voice, rate, out_wav):
    """마이크로소프트 온라인 음성(edge-tts)으로 한 문장 생성 → wav."""
    mp3 = out_wav + ".mp3"
    if TEST:
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1.6", mp3])
    else:
        last = ""
        for _ in range(3):
            r = subprocess.run([sys.executable, "-m", "edge_tts", "--voice", VOICES.get(voice, voice),
                                f"--rate={rate}", "--text", text, "--write-media", mp3],
                               capture_output=True, text=True)
            if r.returncode == 0 and os.path.getsize(mp3) > 1000:
                break
            last = r.stderr[-800:]
        else:
            raise SystemExit("음성 생성 실패: " + last)
    # 앞뒤 무음 정리
    run(["ffmpeg", "-y", "-i", mp3, "-af",
         "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
         "silenceremove=start_periods=1:start_threshold=-45dB,areverse",
         "-ar", "44100", "-ac", "2", out_wav])
    return duration(out_wav)


def build_audio(scene_secs, voice_wavs, bgm, bgm_vol, out_m4a, tmp):
    """장면별 음성을 장면 시작에 맞춰 깔고, 배경음악을 아래에 섞는다."""
    total = sum(scene_secs)
    inputs, chains, labels = [], [], []
    for i, (secs, wav) in enumerate(zip(scene_secs, voice_wavs)):
        if wav:
            inputs += ["-i", wav]
            chains.append(f"[{inputs.count('-i') - 1}:a]adelay=150|150,apad,atrim=0:{secs},asetpts=N/SR/TB[s{i}]")
        else:
            inputs += ["-f", "lavfi", "-t", f"{secs}", "-i", "anullsrc=r=44100:cl=stereo"]
            chains.append(f"[{inputs.count('-i') - 1}:a]atrim=0:{secs},asetpts=N/SR/TB[s{i}]")
        labels.append(f"[s{i}]")
    chains.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1,aformat=sample_rates=44100:channel_layouts=stereo[voice]")
    if bgm:
        inputs += ["-stream_loop", "-1", "-i", bgm]
        b = inputs.count("-i") - 1
        fade = max(0.0, total - 1.2)
        chains.append(f"[{b}:a]aformat=sample_rates=44100:channel_layouts=stereo,atrim=0:{total},"
                      f"volume={bgm_vol},afade=t=in:d=0.3,afade=t=out:st={fade}:d=1.2[bg]")
        chains.append("[voice][bg]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[out]")
    else:
        chains.append("[voice]anull[out]")
    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chains), "-map", "[out]", "-t", f"{total}",
         "-c:a", "aac", "-b:a", "160k", out_m4a])


def test_clip(i, path):
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30:d=8", "-vf", f"hue=h={i * 70}",
         "-pix_fmt", "yuv420p", path])


def main(order_path):
    order_path = Path(order_path)
    order = json.loads(order_path.read_text(encoding="utf-8"))
    name = order.get("name") or order_path.stem
    out_dir = Path(order.get("out_dir", "pet/reels"))
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4 = out_dir / f"{name}.mp4"
    meta_path = out_dir / f"{name}.json"
    digest = hashlib.sha256(order_path.read_bytes()).hexdigest()[:16]
    if not TEST and mp4.exists() and meta_path.exists():
        if json.loads(meta_path.read_text()).get("order_hash") == digest:
            print(f"건너뜀(변경 없음): {name}")
            return

    badge = order.get("badge", "")
    voice = order.get("voice", "")          # "" 이면 음성 없음
    rate = order.get("rate", "+10%")
    bgm = order.get("bgm", "")              # 저장소 안 음악 파일 경로, "" 이면 없음
    if bgm and not Path(bgm).exists():
        raise SystemExit(f"배경음악 파일 없음: {bgm}")
    bgm_vol = float(order.get("bgm_volume", 0.16 if voice else 0.6))
    tmp = Path(tempfile.mkdtemp())
    parts, credits, sheet_rows, scene_secs, voice_wavs = [], [], [], [], []
    for i, sc in enumerate(order["scenes"]):
        secs = float(sc.get("seconds", 2.5))
        wav = None
        say = sc.get("say", sc.get("text", "").replace("\n", " ")) if voice else ""
        if say:
            wav = str(tmp / f"v{i}.wav")
            secs = max(secs, tts(say, sc.get("voice", voice), sc.get("rate", rate), wav) + 0.45)
        secs = round(secs, 2)
        scene_secs.append(secs)
        voice_wavs.append(wav)
        pick = int(sc.get("pick", 0))
        src = str(tmp / f"src{i}.mp4")
        if TEST:
            test_clip(i, src)
            start = 0.5
        else:
            cands = search(sc.get("query", ""), secs)
            if sc.get("id"):  # 고정된 영상: 검색 순서가 바뀌어도 같은 클립
                fixed = search("", secs, int(sc["id"]))
                if not fixed:
                    raise SystemExit(f"장면 {i + 1}: 영상 id {sc['id']} 를 찾지 못함")
                c = fixed[0]
                pick = next((k for k, x in enumerate(cands) if x["id"] == c["id"]), -1)
            else:
                if not cands:
                    raise SystemExit(f"장면 {i + 1}: '{sc['query']}' 검색 결과 없음 — 검색어를 바꿔야 함")
                pick = min(pick, len(cands) - 1)
                c = cands[pick]
            Path(src).write_bytes(http_get(c["url"], 300))
            start = float(sc.get("start", min(1.0, max(0.0, c["duration"] - secs - 0.2))))
            credits.append({"scene": i + 1, "query": sc["query"], "pick": pick, "pixabay_id": c["id"],
                            "page": c["page"], "user": c["user"], "vertical": c["vertical"]})
            sheet_rows.append((sc["query"], cands, pick))
        png = str(tmp / f"ov{i}.png")
        overlay_png(sc.get("badge", badge), sc.get("text", ""), sc.get("sub", ""), png)
        part = str(tmp / f"part{i}.mp4")
        render_scene(src, start, secs, png, part)
        parts.append(part)
        print(f"장면 {i + 1} 완료")

    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    total = sum(scene_secs)
    audio = str(tmp / "audio.m4a")
    build_audio(scene_secs, voice_wavs, bgm, bgm_vol, audio, tmp)
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", audio,
         "-map", "0:v", "-map", "1:a", "-c", "copy", "-t", f"{total}", "-movflags", "+faststart", str(mp4)])

    # 미리보기: 장면마다 한 컷씩 가로로 붙인 캡처
    n = len(parts)
    frames = []
    for i, p in enumerate(parts):
        fp = str(tmp / f"f{i}.jpg")
        run(["ffmpeg", "-y", "-ss", "0.8", "-i", p, "-frames:v", "1", "-vf", "scale=360:640", fp])
        frames.append(Image.open(fp))
    strip = Image.new("RGB", (360 * n + 8 * (n - 1), 640), "#111")
    for i, f in enumerate(frames):
        strip.paste(f, (i * 368, 0))
    strip.save(out_dir / f"{name}.preview.jpg", quality=88)
    if sheet_rows:
        candidates_sheet(sheet_rows, out_dir / f"{name}.candidates.jpg")

    meta_path.write_text(json.dumps({"order_hash": digest, "seconds": round(total, 2), "source": "Pixabay",
                                     "voice": VOICES.get(voice, voice), "bgm": bgm,
                                     "credits": credits}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"완성: {mp4} ({total:.1f}초)")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
