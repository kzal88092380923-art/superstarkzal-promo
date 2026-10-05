#!/usr/bin/env python3
"""타임라인형 릴스 (주문서에 "timeline": true).

한 마리·한 상황 클립 위에 시간별 자막을 얹는다. 인스타 기본 텍스트 같은 반투명 박스 자막,
목소리(Gemini TTS) 단어 강조 자막, 코드로 합성한 배경음·효과음, 첫 0.6초 줌 움직임, 진행 막대.
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
import make_reel as mr  # noqa: E402

W, H, FPS = mr.W, mr.H, mr.FPS
TOP_Y = 300          # 위 270px 은 인스타 UI 영역
BOTTOM_LIMIT = H - 380
SIDE = 90            # 오른쪽 버튼 영역 고려한 좌우 여백
SR = 44100
TEST = mr.TEST
GEMINI = "https://generativelanguage.googleapis.com/v1beta"


# ---------------- 자막 ----------------

def text_lines(d, text, f, max_w):
    return mr.wrap(d, text, f, max_w)


def draw_box_lines(d, lines, f, y, fill=(255, 255, 255, 255), box=(0, 0, 0, 165), pad=(26, 12), gap=10,
                   highlight=None):
    """인스타 '배경 있는 텍스트'처럼 줄마다 둥근 박스. highlight=(줄, 글자시작, 글자끝)이면 그 부분 노란색."""
    asc, desc = f.getmetrics()
    lh = asc + desc
    for i, ln in enumerate(lines):
        tw = d.textlength(ln, font=f)
        x = (W - tw) / 2
        d.rounded_rectangle([x - pad[0], y - pad[1], x + tw + pad[0], y + lh + pad[1]], radius=18, fill=box)
        if highlight and highlight[0] == i:
            a, b = highlight[1], highlight[2]
            pre, mid, post = ln[:a], ln[a:b], ln[b:]
            d.text((x, y), pre, font=f, fill=fill)
            x2 = x + d.textlength(pre, font=f)
            d.text((x2, y), mid, font=f, fill=(255, 222, 60, 255))
            d.text((x2 + d.textlength(mid, font=f), y), post, font=f, fill=fill)
        else:
            d.text((x, y), ln, font=f, fill=fill)
        y += lh + pad[1] * 2 + gap
    return y


def block_height(d, lines, f, pad=12, gap=10):
    asc, desc = f.getmetrics()
    return len(lines) * (asc + desc + pad * 2 + gap) - gap


def layout(texts):
    """stack(위에서부터 쌓이는 자막)은 전부 보인다고 가정하고 자리를 미리 잡아 둔다 → 새 줄이 나와도 안 밀림."""
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    y = TOP_Y
    for t in texts:
        f = mr.font(int(t.get("size", 64)))
        t["_font"] = f
        t["_lines"] = text_lines(probe, t["text"], f, W - SIDE * 2 - 60)
        hgt = block_height(probe, t["_lines"], f)
        pos = t.get("pos", "stack")
        if pos == "stack":
            t["_y"] = y
            y += hgt + int(t.get("space_after", 22))
        elif pos == "center":
            t["_y"] = int(H * 0.47 - hgt / 2)
        elif pos == "bottom":
            t["_y"] = BOTTOM_LIMIT - hgt
        else:
            t["_y"] = int(t.get("y", TOP_Y))
    return texts


def render_state(texts, now, path, karaoke=None):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for t in texts:
        if t["from"] <= now < t["to"]:
            hl = None
            if karaoke and karaoke.get("text_id") == id(t):
                hl = karaoke["hl"]
            draw_box_lines(d, t["_lines"], t["_font"], t["_y"], highlight=hl,
                           box=tuple(t.get("box", (0, 0, 0, 165))))
    im.save(path)


def karaoke_slots(t, start, dur):
    """목소리 길이를 글자 수 비율로 나눠 단어별 강조 구간을 만든다."""
    words = []  # (줄, 시작, 끝, 가중치)
    for li, ln in enumerate(t["_lines"]):
        pos = 0
        for w in ln.split(" "):
            words.append((li, pos, pos + len(w), max(1, len(w.strip(",.?!~ㅋ")))))
            pos += len(w) + 1
    total = sum(w[3] for w in words)
    out, cur = [], start
    for li, a, b, wt in words:
        span = dur * wt / total
        out.append((cur, cur + span, (li, a, b)))
        cur += span
    return out


# ---------------- 목소리 (Gemini) ----------------

def gemini_models(key):
    req = urllib.request.Request(f"{GEMINI}/models?pageSize=200&key={key}", headers=mr.UA)
    names = [m["name"].split("/")[-1] for m in json.loads(urllib.request.urlopen(req, timeout=30).read())["models"]
             if "tts" in m["name"].lower()]

    def rank(n):
        return ("lite" in n, "pro" in n, "preview" in n, [-ord(c) for c in n])
    return sorted(names, key=rank)


def gemini_tts(text, voice, style, out_wav):
    if TEST:
        mr.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=f=330:d={0.25 + 0.09 * len(text)}",
                "-ar", str(SR), "-ac", "2", out_wav])
        return mr.duration(out_wav)
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise SystemExit("GEMINI_API_KEY 가 비어 있음 (저장소 Secrets 확인)")
    # 지시문을 길게 쓰면 지시문까지 읽어버림 → "Say in a ~ tone: 대사" 한 줄만. 그래도 길면 지시 없이.
    prompts = ([f"Say in a {style} tone: {text}"] if style else []) + [text]
    limit = 1.2 + 0.42 * len(text.replace(" ", ""))   # 대사 길이에 비해 너무 길면 지시문까지 읽은 것
    errors = []
    models = gemini_models(key)[:3]
    for prompt in prompts:
        for model in models:
            dur = _gemini_once(key, model, voice, prompt, out_wav, errors)
            if dur is None:
                continue
            if dur <= limit:
                print(f"  음성: {model} / {voice} / {dur:.2f}s / {'지시 O' if prompt != text else '지시 X'}")
                return dur
            errors.append(f"{model}: 음성이 {dur:.1f}초로 너무 김 (지시문까지 읽은 듯)")
            break
    raise SystemExit("Gemini 음성 생성 실패\n" + "\n".join(errors))


def _gemini_once(key, model, voice, prompt, out_wav, errors):
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["AUDIO"],
                             "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}},
    }).encode()
    for _ in [model]:
        for attempt in range(4):
            req = urllib.request.Request(f"{GEMINI}/models/{model}:generateContent?key={key}", data=body,
                                         headers={**mr.UA, "Content-Type": "application/json"})
            try:
                res = json.loads(urllib.request.urlopen(req, timeout=120).read())
                part = next(p for p in res["candidates"][0]["content"]["parts"] if "inlineData" in p)
                raw = base64.b64decode(part["inlineData"]["data"])
                mime = part["inlineData"].get("mimeType", "")
                rate = int(mime.split("rate=")[1].split(";")[0]) if "rate=" in mime else 24000
                pcm = out_wav + ".pcm"
                Path(pcm).write_bytes(raw)
                mr.run(["ffmpeg", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", pcm, "-af",
                        "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                        "silenceremove=start_periods=1:start_threshold=-45dB,areverse",
                        "-ar", str(SR), "-ac", "2", out_wav])
                return mr.duration(out_wav)
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors="ignore")[:300]
                if e.code == 429 and attempt < 3:
                    print("  요청 한도 → 40초 대기")
                    time.sleep(40)
                    continue
                errors.append(f"{model} {e.code}: {msg}")
                return None
            except Exception as e:  # 응답에 오디오가 없을 때 등
                errors.append(f"{model}: {e}")
                return None
    return None


# ---------------- 소리 합성 ----------------

def env(n, decay):
    return np.exp(-np.arange(n) / (SR * decay))


def tone(freq, secs, decay, harm=0.3):
    t = np.arange(int(SR * secs)) / SR
    return (np.sin(2 * np.pi * freq * t) + harm * np.sin(4 * np.pi * freq * t)) * env(len(t), decay)


def synth_music(total, mood, pops, ding):
    n = int(SR * total) + SR
    music, sfx = np.zeros(n), np.zeros(n)

    def add(buf, sig, at):
        i = int(at * SR)
        j = min(n, i + len(sig))
        if i < n:
            buf[i:j] += sig[: j - i]

    chords = [[261.63, 329.63, 392.0], [196.0, 246.94, 293.66], [220.0, 261.63, 329.63], [174.61, 220.0, 261.63]]
    if mood == "lazy":   # 느긋한 고양이: 킥 없이 낮은 플럭만
        step, octave, kick = 0.5, 0.5, False
    else:                # 기본: 1초 킥 + 반박 하이햇 + 플럭 아르페지오
        step, octave, kick = 0.25, 1.0, True
    t = 0.0
    k = 0
    while t < total:
        ch = chords[int(t // 2) % 4]
        note = [ch[0], ch[1], ch[2], ch[0] * 2][k % 4] * octave
        add(music, 0.22 * tone(note, 0.5, 0.16), t)
        if kick and abs(t - round(t)) < 1e-6:
            tt = np.arange(int(SR * 0.22)) / SR
            add(music, 0.9 * np.sin(2 * np.pi * (45 + 75 * np.exp(-tt * 30)) * tt) * env(len(tt), 0.07), t)
        if kick and abs((t - 0.5) - round(t - 0.5)) < 1e-6:
            hat = np.random.default_rng(int(t * 100)).standard_normal(int(SR * 0.05))
            add(music, 0.12 * np.diff(hat, prepend=0) * env(len(hat), 0.012), t)
        t = round(t + step, 4)
        k += 1
    for p in pops:   # 자막 뜰 때 "뽁"
        tt = np.arange(int(SR * 0.07)) / SR
        add(sfx, 0.5 * np.sin(2 * np.pi * (380 + 700 * np.exp(-tt * 60)) * tt) * env(len(tt), 0.02), p)
    if ding is not None:  # 끝 "띠링"
        add(sfx, 0.25 * tone(1318.5, 1.0, 0.3, 0.1) + 0.18 * tone(1975.5, 1.0, 0.25, 0.0), ding)
    fade = np.ones(n)
    fl = int(SR * 0.8)
    end = int(SR * total)
    fade[end - fl:end] = np.linspace(1, 0, fl)
    fade[end:] = 0
    return music * fade, sfx


def write_wav(path, mono):
    a = np.clip(mono, -1, 1)
    st = (np.stack([a, a], axis=1) * 32767).astype("<i2")
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(st.tobytes())


# ---------------- 영상 ----------------

def fetch_clip(c, tmp, i):
    src = str(tmp / f"clip{i}.mp4")
    if TEST:
        mr.test_clip(i, src)
        return src, {"pixabay_id": None}
    hit = mr.search("", 1, int(c["id"]))
    if not hit:
        raise SystemExit(f"영상 id {c['id']} 를 찾지 못함")
    Path(src).write_bytes(mr.http_get(hit[0]["url"], 300))
    return src, {"pixabay_id": hit[0]["id"], "page": hit[0]["page"], "user": hit[0]["user"]}


def base_video(clips, tmp, total):
    parts, credits = [], []
    for i, c in enumerate(clips):
        src, cred = fetch_clip(c, tmp, i)
        credits.append(cred)
        out = str(tmp / f"base{i}.mp4")
        hflip = ",hflip" if c.get("flip") else ""
        mr.run(["ffmpeg", "-y", "-ss", str(c.get("start", 0)), "-t", str(c["seconds"]), "-i", src, "-vf",
                f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}{hflip},fps={FPS},setsar=1,"
                f"tpad=stop_mode=clone:stop_duration=8,eq=saturation=1.06",
                "-t", str(c["seconds"]), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", out])
        parts.append(out)
    lst = tmp / "base.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    joined = str(tmp / "joined.mp4")
    mr.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf",
            "tpad=stop_mode=clone:stop_duration=10", "-t", f"{total}", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "16", joined])
    return joined, credits


def main(order, order_path, digest):
    name = order["name"]
    out_dir = Path(order.get("out_dir", "pet/reels"))
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(mr.tempfile.mkdtemp())
    texts = [dict(t) for t in order["texts"]]

    # 1) 목소리: 줄마다 생성하고 시간 배치
    vo = order.get("voice") or {}
    voice_items, karaoke_events = [], []
    cursor = float(vo.get("start", 0.5))
    for k, ln in enumerate(vo.get("lines", [])):
        wav = str(tmp / f"vo{k}.wav")
        dur = gemini_tts(ln["say"], ln.get("name", vo.get("name", "Kore")), ln.get("style", vo.get("style", "")), wav)
        at = float(ln.get("at", cursor))
        voice_items.append((at, wav, dur))
        cursor = at + dur + float(vo.get("gap", 0.25))
        if ln.get("caption", True):
            texts.append({"text": ln.get("text", ln["say"]), "pos": "bottom", "size": ln.get("size", 64),
                          "from": at, "to": at + dur + 0.25, "_voice": (at, dur)})
    voice_end = max([a + d for a, _, d in voice_items], default=0)

    # 2) "after_voice" 로 표시된 자막은 목소리 끝난 뒤로 시간 이동
    for t in texts:
        if t.get("after_voice") is not None and voice_items:
            length = t["to"] - t["from"]
            t["from"] = round(voice_end + float(t["after_voice"]), 2)
            t["to"] = t["from"] + length
    # "to": "end" 는 영상 끝까지
    fixed = [t["to"] for t in texts if t["to"] != "end"]
    total = round(max([float(order.get("seconds", 0)), voice_end + 0.4, *fixed]), 2)
    for t in texts:
        t["to"] = total if t["to"] == "end" else min(t["to"], total)
    layout(texts)

    # 3) 자막 상태가 바뀌는 시점마다 PNG 한 장 → concat 으로 오버레이 영상
    marks = {0.0, total}
    for t in texts:
        marks.update([round(t["from"], 3), round(t["to"], 3)])
    kslots = []
    for t in texts:
        if "_voice" in t:
            for a, b, hl in karaoke_slots(t, *t["_voice"]):
                kslots.append((a, b, id(t), hl))
                marks.update([round(a, 3), round(b, 3)])
    marks = sorted(m for m in marks if 0 <= m <= total)
    ov_list = []
    for i in range(len(marks) - 1):
        a, b = marks[i], marks[i + 1]
        if b - a < 0.01:
            continue
        mid = (a + b) / 2
        kk = next(({"text_id": tid, "hl": hl} for s, e, tid, hl in kslots if s <= mid < e), None)
        png = str(tmp / f"ov{i:03d}.png")
        render_state(texts, mid, png, kk)
        ov_list.append((png, b - a))
    ovt = tmp / "ov.txt"
    ovt.write_text("".join(f"file '{p}'\nduration {d:.3f}\n" for p, d in ov_list) + f"file '{ov_list[-1][0]}'\n")

    # 4) 영상: 클립 → 첫 0.6초 줌아웃 + 이후 아주 느린 줌인 → 자막 → 진행 막대
    base, credits = base_video(order["clips"], tmp, total)
    frames = int(total * FPS)
    z = f"if(lt(on,18),1.12-0.12*on/18,1+0.035*(on-18)/{max(frames - 18, 1)})"
    vf = (f"[0:v]scale={W * 2}:{H * 2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
          f"s={W}x{H}:fps={FPS}[bg];"
          f"[1:v]format=rgba,fps={FPS}[tx];[bg][tx]overlay=0:0:shortest=1[v1];"
          f"color=c=white@0.85:s={W}x8:r={FPS}[bar];[v1][bar]overlay=x='-W+W*t/{total}':y=0:shortest=1,"
          f"format=yuv420p[v]")
    video = str(tmp / "video.mp4")
    mr.run(["ffmpeg", "-y", "-i", base, "-f", "concat", "-safe", "0", "-i", str(ovt), "-filter_complex", vf,
            "-map", "[v]", "-t", f"{total}", "-c:v", "libx264", "-preset", "medium", "-crf", "19", video])

    # 5) 소리: 합성 배경음 + 효과음 + 목소리 → 인스타 기준 음량(-14 LUFS)
    pops = sorted({round(t["from"], 3) for t in texts if t.get("pop", True) and t["from"] > 0.05 and "_voice" not in t})
    music, sfx = synth_music(total, order.get("music", "playful"), pops,
                             total - 1.0 if order.get("ding", True) else None)
    has_voice = bool(voice_items)
    mvol = float(order.get("music_volume", 0.35 if has_voice else 1.0))
    bed = str(tmp / "bed.wav")
    write_wav(bed, music * 0.5 * mvol + sfx)
    inputs, chains, labels = ["-i", bed], [], ["[0:a]"]
    for k, (at, wav, _) in enumerate(voice_items):
        inputs += ["-i", wav]
        ms = int(at * 1000)
        chains.append(f"[{k + 1}:a]adelay={ms}|{ms},volume=1.6[v{k}]")
        labels.append(f"[v{k}]")
    chains.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0,"
                  f"atrim=0:{total},loudnorm=I=-14:TP=-1.5:LRA=11,aresample={SR}[a]")
    audio = str(tmp / "audio.m4a")
    mr.run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chains), "-map", "[a]", "-t", f"{total}",
            "-c:a", "aac", "-b:a", "160k", audio])

    mp4 = out_dir / f"{name}.mp4"
    mr.run(["ffmpeg", "-y", "-i", video, "-i", audio, "-map", "0:v", "-map", "1:a", "-c", "copy", "-t", f"{total}",
            "-movflags", "+faststart", str(mp4)])

    # 6) 자체 점검용 캡처: 0초, 0.3초, 자막 바뀌는 시점들, 마지막
    real = mr.duration(str(mp4))
    checks = sorted({0.0, 0.3, *[min(round(t["from"] + 0.15, 2), real - 0.15) for t in texts if t["from"] > 0.3],
                     real - 0.15})
    shots = []
    for i, c in enumerate(checks[:10]):
        fp = str(tmp / f"chk{i}.jpg")
        mr.run(["ffmpeg", "-y", "-ss", f"{c}", "-i", str(mp4), "-frames:v", "1", "-vf", "scale=270:480", fp])
        if os.path.exists(fp):
            shots.append((c, Image.open(fp)))
    strip = Image.new("RGB", (278 * len(shots), 520), "#111")
    dd = ImageDraw.Draw(strip)
    for i, (c, im) in enumerate(shots):
        strip.paste(im, (i * 278, 0))
        dd.text((i * 278 + 8, 486), f"{c:.1f}s", font=mr.font(24), fill="#fff")
    strip.save(out_dir / f"{name}.preview.jpg", quality=88)

    (out_dir / f"{name}.json").write_text(json.dumps({
        "order_hash": digest, "seconds": total, "source": "Pixabay", "credits": credits,
        "voice": vo.get("name", ""), "voice_lines": [round(d, 2) for _, _, d in voice_items],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"완성: {mp4} ({total:.1f}초)")
