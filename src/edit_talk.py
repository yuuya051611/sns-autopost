"""話している動画（スマホで撮ったもの）を、投稿できるリールに自動で仕上げる。

やること:
1. 縦長(1080x1920)にそろえる
2. 無言の「間」を自動でカットしてテンポよくする
3. 台本どおりの字幕を、話しているタイミングに合わせて入れる
4. 冒頭3秒に大きなタイトルを出す
5. 小さな音量でBGMを流す
6. 最後に「無料チェックはプロフィールから」の画面をつける

使い方: python src/edit_talk.py 入力.mp4 台本.json 出力.mp4
台本.json = {"title": "冒頭タイトル", "lines": ["1文目。", "2文目。", ...]}
"""
import json
import math
import pathlib
import re
import subprocess
import sys
import tempfile
import wave

import numpy as np
from PIL import Image, ImageDraw

from render import _font, _wrap

W, H, FPS = 1080, 1920, 30
BRAND = {"bg": (12, 12, 14), "accent": (215, 38, 46), "text": (255, 255, 255)}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"ffmpegでエラー: {r.stderr[-800:]}")
    return r


def duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(r.stdout.strip())


# ---------- 1. 話している区間を見つける ----------
def speech_intervals(path, total, noise="-32dB", min_silence=0.35):
    r = subprocess.run(["ffmpeg", "-i", str(path), "-af", f"silencedetect=noise={noise}:d={min_silence}",
                        "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    out, cur = [], 0.0
    for s, e in zip(starts, ends + [total] * (len(starts) - len(ends))):
        if s - cur > 0.25:
            out.append((cur, s))
        cur = e
    if total - cur > 0.25:
        out.append((cur, total))
    return out or [(0.0, total)]


# ---------- 2. 字幕の割り当て ----------
def split_chunks(line, max_len=22):
    """長い文は「、」で区切って、1枚の字幕に収まる長さにする。"""
    if len(line) <= max_len:
        return [line]
    parts, cur = [], ""
    for piece in re.split(r"(?<=、)", line):
        if cur and len(cur) + len(piece) > max_len:
            parts.append(cur)
            cur = piece
        else:
            cur += piece
    if cur:
        parts.append(cur)
    return parts


def assign_subtitles(lines, spans):
    """spans: 編集後の時間軸での話している区間。文の数と区間の数が合えば1対1、合わなければ文字数で配分。"""
    subs = []
    if len(spans) == len(lines):
        pairs = list(zip(lines, spans))
    else:
        total = sum(e - s for s, e in spans)
        start, end = spans[0][0], spans[-1][1]
        chars = sum(len(l) for l in lines)
        pairs, t = [], start
        for l in lines:
            d = (end - start) * len(l) / chars
            pairs.append((l, (t, t + d)))
            t += d
    for line, (s, e) in pairs:
        chunks = split_chunks(line)
        n = sum(len(c) for c in chunks)
        t = s
        for c in chunks:
            d = (e - s) * len(c) / n
            subs.append((c, t, t + d))
            t += d
    return subs


# ---------- 3. 画像パーツを作る ----------
def _balanced(draw, text, font, max_w):
    """行の長さをそろえて、最後の行が2〜3文字だけにならないようにする。"""
    lines = _wrap(draw, text, font, max_w)
    if len(lines) > 1 and "\n" not in text:
        width = draw.textlength(text, font=font) / len(lines)
        while width <= max_w:
            b = _wrap(draw, text, font, width)
            if len(b) == len(lines):
                return b
            width += 10
    return lines


def text_png(text, path, size=74, max_w=900, color=(255, 255, 255), stroke=9):
    font = _font(size)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines = _balanced(probe, text.rstrip("。"), font, max_w)
    lh = int(size * 1.32)
    img = Image.new("RGBA", (W, lh * len(lines) + stroke * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        tw = d.textlength(ln, font=font)
        d.text(((W - tw) / 2, stroke * 2 + i * lh), ln, font=font, fill=color,
               stroke_width=stroke, stroke_fill=(0, 0, 0))
    img.save(path)
    return img.height


def title_png(text, path):
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    want = text.count("\n") + 1
    size = 84
    while True:  # 指定した改行どおりに収まるまで文字を小さくする
        font = _font(size)
        lines = _wrap(probe, text, font, W - 200)
        if len(lines) <= want or size <= 54:
            break
        size -= 4
    lh = int(size * 1.33)
    h = 90 + lh * len(lines)
    img = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((50, 0, W - 50, h), radius=28, fill=BRAND["bg"] + (225,))
    d.rectangle((50, h - 14, W - 50, h), fill=BRAND["accent"])
    for i, ln in enumerate(lines):
        tw = d.textlength(ln, font=font)
        d.text(((W - tw) / 2, 36 + i * lh), ln, font=font, fill=BRAND["text"])
    img.save(path)


def endcard_png(path, handle, cta_lines):
    img = Image.new("RGB", (W, H), BRAND["bg"])
    d = ImageDraw.Draw(img)
    big, mid, small = _font(84), _font(56), _font(44)
    y = 640
    for ln in cta_lines:
        tw = d.textlength(ln, font=big)
        d.text(((W - tw) / 2, y), ln, font=big, fill=BRAND["text"])
        y += 120
    d.rectangle((W / 2 - 160, y + 20, W / 2 + 160, y + 30), fill=BRAND["accent"])
    t = "プロフィールのリンクから"
    d.text(((W - d.textlength(t, font=mid)) / 2, y + 80), t, font=mid, fill=BRAND["text"])
    d.text(((W - d.textlength(handle, font=small)) / 2, y + 190), handle, font=small, fill=(190, 190, 190))
    img.save(path)


# ---------- 4. BGM（著作権の心配がない、自作のやわらかい曲） ----------
def make_bgm(path, seconds, bpm=84, sr=44100):
    beat = 60 / bpm
    chords = [[60, 64, 67, 71], [57, 60, 64, 67], [53, 57, 60, 64], [55, 59, 62, 65]]  # Cmaj7 Am7 Fmaj7 G7
    n = int(seconds * sr)
    t = np.arange(n) / sr
    out = np.zeros(n)
    bar = beat * 4
    for i in range(math.ceil(seconds / bar)):
        s, e = int(i * bar * sr), min(n, int((i + 1) * bar * sr))
        seg = t[s:e] - t[s]
        env = np.minimum(1, seg / 0.4) * np.exp(-seg / (bar * 1.2))
        for note in chords[i % 4]:
            f = 440 * 2 ** ((note - 69) / 12)
            out[s:e] += env * (np.sin(2 * np.pi * f * seg) + 0.3 * np.sin(4 * np.pi * f * seg))
        for k in range(8):  # 軽いきざみ（8分音符のハイハット風）
            hs = s + int(k * beat / 2 * sr)
            he = min(e, hs + int(0.03 * sr))
            if hs < e:
                out[hs:he] += np.random.uniform(-1, 1, he - hs) * 0.35 * np.exp(-np.arange(he - hs) / (0.008 * sr))
    out = out / (np.abs(out).max() + 1e-9) * 0.6
    fade = int(sr * 1.5)
    out[-fade:] *= np.linspace(1, 0, fade)
    pcm = (out * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


# ---------- 本体 ----------
def edit(src, script, out, handle="@rarestella.sma",
         cta=("お店のGoogleマップ", "無料で現状チェック")):
    tmp = pathlib.Path(tempfile.mkdtemp())
    total = duration(src)

    # 話している区間だけ残す（前後に少し余白）
    keep = []
    for s, e in speech_intervals(src, total):
        s, e = max(0, s - 0.12), min(total, e + 0.15)
        if keep and s <= keep[-1][1]:
            keep[-1] = (keep[-1][0], e)
        else:
            keep.append((s, e))
    expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in keep)
    spans, t = [], 0.0  # 編集後の時間軸
    for s, e in keep:
        spans.append((t, t + (e - s)))
        t += e - s
    main_len = t

    subs = assign_subtitles(script["lines"], spans)
    inputs, filters = ["-i", str(src)], []
    filters.append(
        f"[0:v]select='{expr}',setpts=N/FRAME_RATE/TB,"
        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},format=yuv420p[v0]")
    filters.append(f"[0:a]aselect='{expr}',asetpts=N/SR/TB,highpass=f=80,loudnorm=I=-16:TP=-1.5[a0]")

    title_png(script["title"], tmp / "title.png")
    inputs += ["-i", str(tmp / "title.png")]
    filters.append(f"[v0][1:v]overlay=0:220:enable='lt(t,3.2)'[v1]")
    last, idx = "v1", 2
    for i, (text, s, e) in enumerate(subs):
        p = tmp / f"sub{i}.png"
        h = text_png(text, p)
        inputs += ["-i", str(p)]
        filters.append(f"[{last}][{idx}:v]overlay=0:{1330 - h // 2}:enable='between(t,{s:.3f},{e:.3f})'[v{idx}]")
        last, idx = f"v{idx}", idx + 1

    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
         "-map", f"[{last}]", "-map", "[a0]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-ar", "44100", "-ac", "2", str(tmp / "main.mp4")])

    # 最後の案内画面（2.8秒）
    endcard_png(tmp / "end.png", handle, cta)
    run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", "2.8", "-i", str(tmp / "end.png"),
         "-f", "lavfi", "-t", "2.8", "-i", "anullsrc=r=44100:cl=stereo",
         "-vf", f"fps={FPS},format=yuv420p,fade=in:0:8", "-c:v", "libx264", "-preset", "veryfast",
         "-c:a", "aac", "-shortest", str(tmp / "end.mp4")])
    (tmp / "list.txt").write_text(f"file '{tmp / 'main.mp4'}'\nfile '{tmp / 'end.mp4'}'\n")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
         "-c", "copy", str(tmp / "joined.mp4")])

    # BGMを声の下に小さく敷く（話している間はさらに下げる）
    full = main_len + 2.8
    make_bgm(tmp / "bgm.wav", full + 0.5)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp / "joined.mp4"), "-i", str(tmp / "bgm.wav"),
         "-filter_complex",
         "[1:a]volume=0.22,aformat=channel_layouts=stereo[b];"
         "[b][0:a]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[bd];"
         "[0:a][bd]amix=inputs=2:duration=first:normalize=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", str(out)])
    return out


if __name__ == "__main__":
    edit(sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8")), sys.argv[3])
