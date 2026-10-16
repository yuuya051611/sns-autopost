"""撮影なしで作る「解説リール」。

- ナレーション: VOICEVOX（無料・商用利用可。動画の説明文に「VOICEVOX:キャラ名」の表記が必要）
- 映像: 本物の映像素材（Pixabayから自動取得）を背景に、Googleマップ風のスマホ画面などをアニメーションで重ねる
- 字幕: ナレーションの長さから計算するので、声とぴったり合う
- BGM: 自作の曲（著作権の心配なし）を、声の下に小さく敷く

台本の形式（JSON）:
{"voice": 14, "speed": 1.12, "scenes": [{"type": "hook", "text": "字幕", "speak": "読み上げ（省略時はtext）", "big": "大きく出す文字", "bg_query": "背景の映像の検索語"}, ...]}
"""
import io
import json
import math
import os
import pathlib
import subprocess
import sys
import tempfile
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from PIL import ImageFont as _IF

from edit_talk import _balanced, split_chunks
from render import _font as _font_bold

_BLACK = [p for p in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
                      "/usr/share/fonts/noto-cjk/NotoSansCJK-Black.ttc") if pathlib.Path(p).exists()]
_FCACHE = {}


def _font(size):
    """太くて読みやすい極太ゴシック（なければ太字）。"""
    if size not in _FCACHE:
        _FCACHE[size] = _IF.truetype(_BLACK[0], size, index=0) if _BLACK else _font_bold(size)
    return _FCACHE[size]


W, H, FPS = 1080, 1920, 30
BG = (255, 247, 242)
FG = (28, 28, 34)
SUB = (112, 112, 122)
CARD = (255, 255, 255)
CARD_LINE = (242, 222, 218)
RED = (215, 38, 46)
WHITE = (255, 255, 255)
INK = (32, 33, 36)
GRAY = (95, 99, 104)
STAR = (251, 188, 4)
MAPBG = (233, 236, 228)

VV_DIR = pathlib.Path(os.environ.get("VV_DIR") or pathlib.Path(__file__).resolve().parent.parent / "vv")
VOICE_NAMES = {8: "春日部つむぎ", 14: "冥鳴ひまり", 2: "四国めたん", 3: "ずんだもん", 11: "玄野武宏", 16: "九州そら"}


# ---------- ナレーション ----------
def synth_lines(texts, voice=14, speed=1.12, vv_dir=VV_DIR, intonation=1.0, pitch=0.0, tweaks=None):
    from voicevox_core.blocking import Onnxruntime, OpenJtalk, Synthesizer, VoiceModelFile
    ort_lib = sorted(vv_dir.glob("**/libvoicevox_onnxruntime.so*"))[-1]
    ort = Onnxruntime.load_once(filename=str(ort_lib))
    syn = Synthesizer(ort, OpenJtalk(str(next(vv_dir.glob("open_jtalk_dic*")))))
    for vvm in sorted((vv_dir / "vvms").glob("*.vvm")):
        with VoiceModelFile.open(vvm) as m:
            if any(s.id == voice for c in m.metas for s in c.styles):
                syn.load_voice_model(m)
    out = []
    import random as _rnd
    rnd = _rnd.Random(len("".join(texts)))
    for li, t in enumerate(texts):
        tw = (tweaks or [{}] * len(texts))[li] or {}
        q = syn.create_audio_query(t, voice)
        # 毎回まったく同じ調子だと機械っぽく聞こえるので、文ごとに少しだけ揺らす
        q.speed_scale = speed * tw.get("speed", 1.0) * rnd.uniform(0.96, 1.04)
        q.intonation_scale = intonation * tw.get("intonation", 1.0) * rnd.uniform(0.95, 1.06)
        q.pitch_scale = pitch + tw.get("pitch", 0.0) + rnd.uniform(-0.015, 0.015)
        q.volume_scale = tw.get("volume", 1.0)
        q.pre_phoneme_length = 0.08
        q.post_phoneme_length = rnd.uniform(0.28, 0.4)
        out.append(syn.synthesis(q, voice))
    return out


def wav_seconds(b):
    with wave.open(io.BytesIO(b)) as r:
        return r.getnframes() / r.getframerate()


def join_wavs(blobs, path):
    frames, rate = [], 24000
    for b in blobs:
        with wave.open(io.BytesIO(b)) as r:
            rate = r.getframerate()
            frames.append(r.readframes(r.getnframes()))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(frames))


def make_sfx(path, seconds, whooshes, pops, sr=44100):
    """効果音（今は使っていない。必要なときだけ鳴らす）。"""
    n = int(seconds * sr)
    out = np.zeros(n)
    for t0 in whooshes:
        s0, m = int(t0 * sr), int(0.35 * sr)
        if s0 + m > n:
            continue
        noise = np.random.uniform(-1, 1, m)
        tt = np.arange(m) / m
        smooth = np.convolve(noise, np.ones(44) / 44, mode="same")
        out[s0:s0 + m] += 0.5 * (noise - smooth) * np.sin(np.pi * tt) ** 2
    for t0 in pops:
        s0, m = int(t0 * sr), int(0.09 * sr)
        if s0 + m > n:
            continue
        tt = np.arange(m) / sr
        out[s0:s0 + m] += 0.35 * np.sin(2 * np.pi * (900 - 4000 * tt) * tt) * np.exp(-tt / 0.025)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(out, -1, 1) * 32767).astype(np.int16).tobytes())


def make_bgm_bright(path, seconds, bpm=116, sr=44100):
    """明るく弾むBGM（自作・著作権フリー）。C→G→Am→F の王道進行。"""
    beat = 60 / bpm
    prog = [(48, [60, 64, 67, 72]), (43, [59, 62, 67, 71]), (45, [60, 64, 69, 72]), (41, [60, 65, 69, 72])]
    n = int(seconds * sr)
    out = np.zeros(n)

    def note(start, dur, midi, vol, decay):
        s0 = int(start * sr)
        if s0 >= n:
            return
        m = min(n - s0, int(dur * sr))
        tt = np.arange(m) / sr
        f = 440 * 2 ** ((midi - 69) / 12)
        env = np.exp(-tt / decay) * np.minimum(1, tt / 0.005)
        wave_ = np.sin(2 * np.pi * f * tt) + 0.35 * np.sin(4 * np.pi * f * tt) + 0.15 * np.sin(6 * np.pi * f * tt)
        out[s0:s0 + m] += vol * env * wave_

    bars = math.ceil(seconds / (beat * 4))
    for b in range(bars):
        root, chord = prog[b % 4]
        t0 = b * beat * 4
        for k in range(16):  # 16分のアルペジオ
            note(t0 + k * beat / 4, beat / 2, chord[k % 4] + (12 if k % 8 >= 4 else 0), 0.16, 0.12)
        for k in range(4):  # ベース
            note(t0 + k * beat, beat * 0.9, root, 0.5, 0.25)
            if k in (0, 2):  # キック
                s0 = int((t0 + k * beat) * sr)
                m = min(n - s0, int(0.18 * sr))
                if m > 0:
                    tt = np.arange(m) / sr
                    out[s0:s0 + m] += 0.9 * np.sin(2 * np.pi * (110 * np.exp(-tt * 18) + 45) * tt) * np.exp(-tt / 0.07)
            if k in (1, 3):  # クラップ
                s0 = int((t0 + k * beat) * sr)
                m = min(n - s0, int(0.12 * sr))
                if m > 0:
                    out[s0:s0 + m] += 0.35 * np.random.uniform(-1, 1, m) * np.exp(-np.arange(m) / (0.03 * sr))
    out = out / (np.abs(out).max() + 1e-9) * 0.7
    fade = int(sr * 1.2)
    out[-fade:] *= np.linspace(1, 0, fade)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((out * 32767).astype(np.int16).tobytes())


# ---------- 描画の小道具 ----------
def ease(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def ease_back(x, k=1.9):
    """少し行きすぎて戻る、弾むような動き。"""
    x = max(0.0, min(1.0, x))
    return 1 + (k + 1) * (x - 1) ** 3 + k * (x - 1) ** 2


_BG_BIG = None

import random as _r
_r.seed(7)
BOKEH = [(_r.randint(0, W), _r.randint(0, H), _r.randint(18, 70), _r.randint(40, 120),
          _r.choice([(255, 255, 255, 70), (255, 150, 140, 60), (255, 200, 120, 60), (215, 38, 46, 34)]))
         for _ in range(18)]


def background(now=0.0):
    """映像がないときの背景。色のにじみが動き、丸い光が浮かんでいく。"""
    global _BG_BIG
    if _BG_BIG is None:
        big = Image.new("RGB", (W + 800, H + 800), BG)
        g = ImageDraw.Draw(big)
        g.ellipse((0, 200, 1200, 1500), fill=(255, 190, 180))
        g.ellipse((900, 1300, 2000, 2600), fill=(255, 215, 170))
        g.ellipse((800, -200, 1800, 800), fill=(255, 200, 215))
        g.ellipse((-200, 1800, 800, 2700), fill=(255, 228, 200))
        _BG_BIG = big.filter(ImageFilter.GaussianBlur(170))
    ox = int(400 + 330 * math.sin(now * 0.9))
    oy = int(400 + 330 * math.cos(now * 0.7))
    img = _BG_BIG.crop((ox, oy, ox + W, oy + H)).convert("RGBA")
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i, (x0, y0, r, sp, col) in enumerate(BOKEH):
        y = (y0 - now * sp) % (H + 2 * r) - r
        x = x0 + 40 * math.sin(now * 0.8 + i)
        d.ellipse((x - r, y - r, x + r, y + r), fill=col)
    img.alpha_composite(layer)
    return img.convert("RGB")


NOW = 0.0


def ctext(d, y, text, size, fill=None):
    """映像の上に置く文字。白（または指定色）に黒いふちどり。"""
    f = _font(size)
    d.text(((W - d.textlength(text, font=f)) / 2, y), text, font=f, fill=fill or WHITE,
           stroke_width=max(4, size // 12), stroke_fill=(0, 0, 0))


PHONE = (220, 200, 860, 1330)  # スマホの外枠


def phone(img, slide=1.0):
    """スマホの枠と画面を描き、画面部分のImageDrawと座標を返す。"""
    off = int((1 - ease_back(slide)) * 900 + 12 * math.sin(NOW * 2.4))
    x0, y0, x1, y1 = PHONE
    y0, y1 = y0 + off, y1 + off
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0 - 14, y0 - 14, x1 + 14, y1 + 14), radius=70, fill=(28, 28, 32))
    d.rounded_rectangle((x0, y0, x1, y1), radius=56, fill=WHITE)
    return d, (x0, y0, x1, y1)


def search_bar(d, box, text, cursor=False):
    x0, y0, x1, _ = box
    d.rounded_rectangle((x0 + 30, y0 + 60, x1 - 30, y0 + 150), radius=45, fill=WHITE, outline=(218, 220, 224), width=3)
    d.ellipse((x0 + 60, y0 + 88, x0 + 90, y0 + 118), outline=GRAY, width=5)
    d.line((x0 + 86, y0 + 114, x0 + 100, y0 + 128), fill=GRAY, width=6)
    f = _font(38)
    d.text((x0 + 120, y0 + 82), text, font=f, fill=INK)
    if cursor:
        cx = x0 + 120 + d.textlength(text, font=f) + 4
        d.rectangle((cx, y0 + 84, cx + 4, y0 + 130), fill=(26, 115, 232))


def map_area(d, box, top, bottom, pins=3):
    x0, _, x1, _ = box
    d.rectangle((x0, top, x1, bottom), fill=MAPBG)
    for a, b in [(0.1, 0.8), (0.3, 0.2), (0.7, 0.9)]:
        d.line((x0, top + (bottom - top) * a, x1, top + (bottom - top) * b), fill=WHITE, width=16)
    d.line((x0 + (x1 - x0) * 0.55, top, x0 + (x1 - x0) * 0.4, bottom), fill=WHITE, width=20)
    spots = [(0.3, 0.45), (0.62, 0.35), (0.48, 0.7)][:pins]
    for i, (px, py) in enumerate(spots):
        cx, cy = x0 + (x1 - x0) * px, top + (bottom - top) * py
        d.ellipse((cx - 26, cy - 52, cx + 26, cy), fill=(234, 67, 53))
        d.polygon([(cx - 18, cy - 14), (cx + 18, cy - 14), (cx, cy + 14)], fill=(234, 67, 53))
        d.text((cx - 8, cy - 46), str(i + 1), font=_font(26), fill=WHITE)


SHOPS = [("ヘアサロン A", "4.8", "(312)", "徒歩3分"), ("ヘアサロン B", "4.6", "(188)", "徒歩6分"),
         ("ヘアサロン C", "4.9", "(96)", "徒歩8分")]


def listing(d, box, y, name, rating, count, walk, faded=False, highlight=False, label=None):
    x0, _, x1, _ = box
    ink = (180, 182, 186) if faded else INK
    if highlight:
        d.rounded_rectangle((x0 + 16, y - 10, x1 - 16, y + 150), radius=22, outline=RED, width=7)
    d.rounded_rectangle((x0 + 36, y + 8, x0 + 156, y + 128), radius=16, fill=(200, 200, 204) if faded else (176, 190, 197))
    d.text((x0 + 180, y + 6), name, font=_font(38), fill=ink)
    sf = _font(32)
    d.text((x0 + 180, y + 58), rating, font=sf, fill=ink)
    sx = x0 + 180 + d.textlength(rating, font=sf) + 10
    for k in range(5):
        c = (sx + k * 30 + 12, y + 78)
        pts = [(c[0] + 13 * math.cos(math.pi / 2 + i * math.pi / 5) * (1 if i % 2 == 0 else 0.45),
                c[1] - 13 * math.sin(math.pi / 2 + i * math.pi / 5) * (1 if i % 2 == 0 else 0.45)) for i in range(10)]
        d.polygon(pts, fill=(220, 220, 220) if faded else STAR)
    d.text((sx + 160, y + 58), count, font=sf, fill=(180, 182, 186) if faded else GRAY)
    d.text((x0 + 180, y + 100), walk, font=_font(28), fill=(190, 192, 196) if faded else GRAY)
    if label:
        lf = _font(30)
        tw = d.textlength(label, font=lf)
        d.rounded_rectangle((x1 - 60 - tw, y + 10, x1 - 36, y + 56), radius=20, fill=RED)
        d.text((x1 - 48 - tw, y + 14), label, font=lf, fill=WHITE)


def note(d):
    f = _font(24)
    t = "※画面はイメージです"
    d.text((W - 60 - d.textlength(t, font=f), PHONE[3] + 24), t, font=f, fill=(140, 140, 146))


# ---------- 場面 ----------
def scene_hook(img, t, dur, sc):
    d = ImageDraw.Draw(img)
    lines = sc.get("big", sc["text"]).split("\n")
    p = ease(t / 0.5)
    size = max(30, int(98 * (0.4 + 0.6 * ease_back(t / 0.45))))
    y = 700 - len(lines) * 70
    for i, ln in enumerate(lines):
        ctext(d, y + i * 140, ln, size, fill=(255, 214, 0) if i == len(lines) - 1 else WHITE)
    d.rectangle((W / 2 - 120 * p, y + len(lines) * 140 + 30, W / 2 + 120 * p, y + len(lines) * 140 + 42), fill=RED)


def scene_pair(img, t, dur, sc):
    d = ImageDraw.Draw(img)
    items = sc.get("items", ["紹介", "通りがかり"])
    for i, label in enumerate(items):
        p = ease_back((t - i * 0.45) / 0.45)
        if t - i * 0.45 <= 0:
            continue
        x = 140 + i * 430
        y = 600 + int((1 - p) * 320)
        d.rounded_rectangle((x, y, x + 370, y + 420), radius=40, fill=CARD, outline=CARD_LINE, width=4)
        d.ellipse((x + 125, y + 60, x + 245, y + 180), fill=RED)
        d.line((x + 155, y + 122, x + 180, y + 148), fill=WHITE, width=14)
        d.line((x + 180, y + 148, x + 220, y + 98), fill=WHITE, width=14)
        f = _font(64)
        d.text((x + (370 - d.textlength(label, font=f)) / 2, y + 250), label, font=f, fill=INK)
    if t > 1.4:
        ctext(d, 1180, sc.get("caption", "でも、それは「たまたま」"), 56)


def scene_chart(img, t, dur, sc):
    d = ImageDraw.Draw(img)
    vals = sc.get("values", [8, 3, 9, 2, 5, 1])
    labels = ["1月", "2月", "3月", "4月", "5月", "6月"]
    x0, base = 150, 1250
    ctext(d, 420, sc.get("label", "毎月の新規客数（イメージ）"), 48)
    for i, v in enumerate(vals):
        p = ease_back((t - i * 0.15) / 0.45)
        h = max(0, int(v * 70 * p))
        x = x0 + i * 135
        col = RED if v <= 2 else (235, 235, 240)
        d.rounded_rectangle((x, base - h, x + 90, base), radius=12, fill=col)
        f = _font(34)
        d.text((x + 45 - d.textlength(labels[i], font=f) / 2, base + 20), labels[i], font=f, fill=WHITE,
               stroke_width=4, stroke_fill=(0, 0, 0))
    d.line((120, base, 960, base), fill=(235, 235, 240), width=4)


def scene_search(img, t, dur, sc):
    d, box = phone(img, t / 0.6)
    q = sc.get("query", "〇〇駅 美容室")
    n = int(max(0, (t - 0.5)) / 0.12)
    search_bar(d, box, q[:n], cursor=int(t * 3) % 2 == 0)
    map_area(d, box, box[1] + 190, box[3] - 40, pins=0)
    note(ImageDraw.Draw(img))


def scene_results(img, t, dur, sc):
    d, box = phone(img)
    search_bar(d, box, sc.get("query", "〇〇駅 美容室"))
    map_area(d, box, box[1] + 190, box[1] + 560, pins=3)
    for i, s in enumerate(sc.get("shops", SHOPS)):
        if t - 0.2 - i * 0.3 <= 0:
            continue
        p = ease_back((t - 0.2 - i * 0.3) / 0.4)
        y = box[1] + 600 + i * 190 + int((1 - p) * 260)
        listing(d, box, y, *s, highlight=t > 1.6)
    if t > 1.6:
        ctext(ImageDraw.Draw(img), 120, "ここに入ったお店が選ばれる", 52)


def scene_hidden(img, t, dur, sc):
    d, box = phone(img)
    scroll = int(ease(t / 1.0) * 420)
    top = box[1] + 600 - scroll
    for i, s in enumerate(sc.get("shops", SHOPS)):
        y = top + i * 190
        if y > box[1] + 20:
            listing(d, box, y, *s)
    y_more = top + 3 * 190 + 10
    d.text(((box[0] + box[2]) / 2 - 90, y_more), "さらに表示 ▼", font=_font(34), fill=(26, 115, 232))
    y_you = y_more + 100
    if y_you < box[3] - 150:
        listing(d, box, y_you, "あなたのお店", "-", "", "", faded=True, label="7位")
    d.rounded_rectangle((box[0], box[1], box[2], box[1] + 180), radius=56, fill=WHITE)
    search_bar(d, box, sc.get("query", "〇〇駅 美容室"))
    if t > 1.3:
        ctext(ImageDraw.Draw(img), 120, "ここまで見る人は、ほとんどいない", 52, fill=(255, 214, 0))


def scene_check(img, t, dur, sc):
    d = ImageDraw.Draw(img)
    items = sc.get("items", ["店舗情報は正しい？", "写真は足りている？", "口コミに返信している？"])
    ctext(d, 470, sc.get("heading", "今日のチェック"), 72)
    for i, it in enumerate(items):
        if t - 0.3 - i * 0.55 <= 0:
            continue
        p = ease_back((t - 0.3 - i * 0.55) / 0.4)
        y = 650 + i * 200
        x = 150 + int((1 - p) * -700)
        d.rounded_rectangle((x, y, x + 780, y + 150), radius=30, fill=CARD, outline=CARD_LINE, width=4)
        d.rounded_rectangle((x + 40, y + 45, x + 100, y + 105), radius=12, outline=GRAY, width=5)
        if t > 0.9 + i * 0.6:
            d.line((x + 52, y + 75, x + 68, y + 92), fill=RED, width=9)
            d.line((x + 68, y + 92, x + 94, y + 55), fill=RED, width=9)
        d.text((x + 140, y + 46), it, font=_font(50), fill=INK)


def scene_point(img, t, dur, sc):
    """「①②③」のポイント。大きな番号がポンと出て、見出しが下から飛び込む。"""
    d = ImageDraw.Draw(img)
    n = str(sc.get("num", 1))
    p = ease_back(t / 0.35)
    r = int(120 * p)
    cx, cy = W // 2, 560
    if r > 2:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=RED, outline=WHITE, width=8)
        f = _font(max(10, int(150 * p)))
        d.text((cx - d.textlength(n, font=f) / 2, cy - int(110 * p)), n, font=f, fill=WHITE)
    lines = sc.get("title", "").split("\n")
    for i, ln in enumerate(lines):
        q = ease_back((t - 0.2 - i * 0.12) / 0.4)
        if t - 0.2 - i * 0.12 <= 0:
            continue
        ctext(d, 760 + i * 130 + int((1 - q) * 160), ln, 96, fill=(255, 214, 0) if i == len(lines) - 1 else WHITE)


def scene_reveal(img, t, dur, sc):
    """答えをドーンと見せる。大きな文字が揺れながら飛び込んでくる。"""
    d = ImageDraw.Draw(img)
    if sc.get("kicker"):
        ctext(d, 520, sc["kicker"], 60)
    lines = sc.get("big", "").split("\n")
    shake = int(14 * math.sin(t * 40) * max(0, 1 - t / 0.5))
    for i, ln in enumerate(lines):
        q = ease_back((t - 0.15 - i * 0.12) / 0.35)
        if t - 0.15 - i * 0.12 <= 0:
            continue
        size = max(20, int(124 * (0.6 + 0.4 * q)))
        f = _font(size)
        while d.textlength(ln, font=f) > W - 100 and size > 40:  # はみ出さないよう自動で縮める
            size -= 6
            f = _font(size)
        x = (W - d.textlength(ln, font=f)) / 2 + shake
        d.text((x, 680 + i * 160), ln, font=f, fill=(255, 214, 0) if i == len(lines) - 1 else WHITE,
               stroke_width=10, stroke_fill=(0, 0, 0))


def _review_card(d, box, name, stars, text, reply=None, dim=False):
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, radius=28, fill=(245, 245, 247) if dim else WHITE)
    ink = (150, 150, 156) if dim else INK
    d.ellipse((x0 + 28, y0 + 28, x0 + 92, y0 + 92), fill=(200, 200, 206))
    d.text((x0 + 110, y0 + 30), name, font=_font(36), fill=ink)
    d.text((x0 + 110, y0 + 74), "★" * stars + "☆" * (5 - stars), font=_font(30), fill=(200, 200, 200) if dim else STAR)
    d.text((x0 + 32, y0 + 120), text, font=_font(34), fill=ink)
    if reply:
        d.rounded_rectangle((x0 + 32, y0 + 180, x1 - 32, y1 - 24), radius=18, fill=(255, 238, 236))
        d.text((x0 + 56, y0 + 196), "オーナーより", font=_font(28), fill=RED)
        d.text((x0 + 56, y0 + 236), reply, font=_font(32), fill=INK)
    else:
        d.text((x0 + 32, y1 - 64), "（返信なし）", font=_font(30), fill=(170, 170, 176))


def scene_compare(img, t, dur, sc):
    """口コミに返信していないお店と、ていねいに返信しているお店を並べて比べる。"""
    d = ImageDraw.Draw(img)
    p1 = ease_back(t / 0.4)
    p2 = ease_back((t - 0.6) / 0.4)
    y_a = 300 + int((1 - p1) * -500)
    _review_card(d, (90, y_a, W - 90, y_a + 330), "Aさん", 2, "待ち時間が長かったです…", dim=True)
    if t > 0.25:
        ctext(d, y_a + 345, "返信なし → 印象が悪いまま", 44, fill=(200, 200, 205))
    if t > 0.6:
        y_b = 900 + int((1 - p2) * 500)
        _review_card(d, (90, y_b, W - 90, y_b + 330), "Bさん", 2, "待ち時間が長かったです…",
                     reply="ご不便をおかけしました。予約枠を増やしました！")
        if t > 1.1:
            ctext(d, y_b + 345, "返信あり → 「ちゃんとしたお店」", 44, fill=(255, 214, 0))


def scene_steps(img, t, dur, sc):
    """相談の流れを3ステップで見せる（DMを送る → お店を見る → 改善案が届く）。"""
    d = ImageDraw.Draw(img)
    ctext(d, 330, sc.get("heading", "やることは、これだけ"), 72)
    steps = sc.get("steps", [])
    for i, (title, sub) in enumerate(steps):
        if t - 0.15 - i * 0.3 <= 0:
            continue
        q = ease_back((t - 0.15 - i * 0.3) / 0.3)
        y = 500 + i * 250
        x = 90 + int((1 - q) * 900)
        d.rounded_rectangle((x, y, x + W - 180, y + 200), radius=32, fill=WHITE)
        d.ellipse((x + 36, y + 50, x + 136, y + 150), fill=RED)
        f = _font(60)
        d.text((x + 86 - d.textlength(str(i + 1), font=f) / 2, y + 58), str(i + 1), font=f, fill=WHITE)
        d.text((x + 170, y + 40), title, font=_font(54), fill=INK)
        d.text((x + 170, y + 116), sub, font=_font(34), fill=GRAY)
        if i < len(steps) - 1 and t - 0.15 - (i + 1) * 0.3 > 0:
            d.polygon([(W // 2 - 26, y + 212), (W // 2 + 26, y + 212), (W // 2, y + 240)], fill=(255, 214, 0))


def scene_stat(img, t, dur, sc):
    """調査の数字を大きく見せる（出典も小さく必ず入れる）。"""
    d = ImageDraw.Draw(img)
    if sc.get("kicker"):
        ctext(d, 470, sc["kicker"], 56)
    q = ease_back(t / 0.45)
    f = _font(max(20, int(200 * (0.5 + 0.5 * q))))
    num = sc.get("num", "")
    d.text(((W - d.textlength(num, font=f)) / 2, 600), num, font=f, fill=(255, 214, 0),
           stroke_width=12, stroke_fill=(0, 0, 0))
    for i, ln in enumerate(sc.get("label", "").split("\n")):
        if t > 0.35 + i * 0.15:
            ctext(d, 880 + i * 100, ln, 64)
    if sc.get("source"):
        ctext(d, 1130, sc["source"], 30, fill=(225, 225, 230))


def scene_template(img, t, dur, sc):
    """「そのまま使える」テンプレートを、1文字ずつ打ち込むように見せる（保存したくなる場面）。"""
    d = ImageDraw.Draw(img)
    p = ease_back(t / 0.35)
    y0 = 330 + int((1 - p) * 300)
    label = sc.get("label", "コピペOK！返信テンプレ")
    f = _font(46)
    tw_ = d.textlength(label, font=f)
    d.rounded_rectangle(((W - tw_) / 2 - 30, y0 - 110, (W + tw_) / 2 + 30, y0 - 30), radius=26, fill=(255, 214, 0))
    d.text(((W - tw_) / 2, y0 - 100), label, font=f, fill=INK)
    d.rounded_rectangle((70, y0, W - 70, y0 + 760), radius=36, fill=WHITE)
    body = sc.get("body", "")
    shown = body[: int(max(0, t - 0.3) * 28)]
    bf = _font(46)
    y = y0 + 50
    for para in shown.split("\n"):
        for ln in _balanced(d, para, bf, W - 240) if para else [""]:
            d.text((120, y), ln, font=bf, fill=INK)
            y += 72
    if len(shown) < len(body) and int(t * 3) % 2 == 0:
        d.rectangle((120, y - 66, 126, y - 14), fill=RED)


def scene_action(img, t, dur, sc):
    """お店のページで「経路」をタップ → 地図に道順が引かれて、お店に向かう。"""
    d, box = phone(img)
    x0, y0, x1, y1 = box
    name = sc.get("name", "あなたのお店")
    map_area(d, box, y0 + 40, y0 + 520, pins=0)
    route_p = ease((t - 1.2) / 1.2)
    sx, sy = x0 + 120, y0 + 470
    ex, ey = x1 - 140, y0 + 130
    if route_p > 0:
        pts = [(sx, sy), (sx, sy - 160), (ex - 120, sy - 160), (ex - 120, ey), (ex, ey)]
        segs = list(zip(pts, pts[1:]))
        total_len = sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in segs)
        left = total_len * route_p
        for a, b in segs:
            L = abs(a[0] - b[0]) + abs(a[1] - b[1])
            if left <= 0:
                break
            f = min(1, left / L)
            d.line((a, (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)), fill=(26, 115, 232), width=16)
            left -= L
        d.ellipse((sx - 18, sy - 18, sx + 18, sy + 18), fill=(26, 115, 232), outline=WHITE, width=5)
    d.ellipse((ex - 26, ey - 52, ex + 26, ey), fill=(234, 67, 53))
    d.polygon([(ex - 18, ey - 14), (ex + 18, ey - 14), (ex, ey + 14)], fill=(234, 67, 53))
    d.rectangle((x0, y0 + 520, x1, y1), fill=WHITE)
    d.text((x0 + 40, y0 + 550), name, font=_font(46), fill=INK)
    d.text((x0 + 40, y0 + 615), sc.get("rating", "4.6 ★★★★★ (128)"), font=_font(32), fill=GRAY)
    for i, lb in enumerate(["電話", "経路", "予約"]):
        col = (26, 115, 232)
        cx = x0 + 120 + i * 200
        cy = y0 + 760
        on = i == 1 and t > 0.6
        d.ellipse((cx - 62, cy - 62, cx + 62, cy + 62), fill=col if on else (232, 240, 254))
        f = _font(34)
        d.text((cx - d.textlength(lb, font=f) / 2, cy - 22), lb, font=f, fill=WHITE if on else col)
        if i == 1 and 0.6 < t < 1.3:  # タップの波紋
            r = int(60 + 90 * ((t - 0.6) / 0.7))
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 214, 0), width=8)
    if t > 2.2:
        ctext(ImageDraw.Draw(img), 120, "そのまま、お店へ", 56, fill=(255, 214, 0))


def scene_cta(img, t, dur, sc):
    d = ImageDraw.Draw(img)
    p = ease(t / 0.6)
    l1, l2 = sc.get("lines", ["お店のGoogleマップ", "無料で現状チェック"])
    ctext(d, 660 - int((1 - ease_back(t / 0.5)) * 120), l1, 84)
    ctext(d, 790, l2, 84, fill=(255, 214, 0))
    d.rectangle((W / 2 - 170 * p, 935, W / 2 + 170 * p, 947), fill=RED)
    ctext(d, 1000, sc.get("sub", "プロフィールのリンクから"), 56)
    handle = sc.get("handle", "@rarestella.sma")
    if handle not in sc.get("sub", ""):
        ctext(d, 1110, handle, 46)


def scene_shop(img, t, dur, sc):
    """写真も映像も使えないときの代わり：お店のイラストが弾んで出てきて、人が集まってくる。"""
    d = ImageDraw.Draw(img)
    p = ease_back(t / 0.5)
    cx, base = W // 2, 1180
    w, h = 720, 560
    top = base - int(h * p)
    x0, x1 = cx - w // 2, cx + w // 2
    d.rounded_rectangle((x0, top, x1, base), radius=24, fill=CARD, outline=CARD_LINE, width=6)
    for k in range(8):  # しましまの屋根
        col = RED if k % 2 == 0 else WHITE
        d.rectangle((x0 - 30 + k * (w + 60) // 8, top - 70, x0 - 30 + (k + 1) * (w + 60) // 8, top + 30), fill=col)
    d.rounded_rectangle((x0 - 30, top - 70, x1 + 30, top + 30), radius=16, outline=RED, width=6)
    d.rounded_rectangle((cx - 90, base - 300, cx + 90, base), radius=14, fill=(255, 226, 196))
    d.ellipse((cx + 50, base - 160, cx + 70, base - 140), fill=RED)
    for wx in (x0 + 60, x1 - 240):
        d.rounded_rectangle((wx, top + 90, wx + 180, top + 250), radius=14, fill=(214, 236, 248))
    sign = sc.get("caption") or "OPEN"
    f = _font(46)
    tw = d.textlength(sign, font=f)
    d.rounded_rectangle((cx - tw / 2 - 30, top + 50, cx + tw / 2 + 30, top + 120), radius=20, fill=FG)
    d.text((cx - tw / 2, top + 58), sign, font=f, fill=WHITE)
    for i, (px, col) in enumerate([(-470, (255, 160, 140)), (450, (120, 170, 240)), (-330, (250, 200, 90)), (320, (150, 210, 160))]):
        if t - 0.5 - i * 0.25 <= 0:
            continue
        q = ease_back((t - 0.5 - i * 0.25) / 0.4)
        x = cx + int(px * (1.6 - 0.6 * q))
        y = base + 40
        d.ellipse((x - 34, y - 230, x + 34, y - 162), fill=col)
        d.rounded_rectangle((x - 50, y - 150, x + 50, y), radius=40, fill=col)
    d.rounded_rectangle((120, base + 40, W - 120, base + 52), radius=6, fill=CARD_LINE)


_PHOTO_CACHE = {}
_VIDEO_CACHE = {}


def _load_clip(path, seconds):
    """動画を縦長(1080x1920)・30fpsのコマに変換して読み込む。"""
    if path in _VIDEO_CACHE:
        return _VIDEO_CACHE[path]
    for k in [k for k in _VIDEO_CACHE if k != "_shade"]:  # メモリ節約のため前の動画は捨てる
        del _VIDEO_CACHE[k]
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-t", f"{seconds + 0.5:.2f}",
                        "-vf", f"scale={int(W * 1.15)}:{int(H * 1.15)}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    n = len(r.stdout) // (W * H * 3)
    frames = [r.stdout[i * W * H * 3:(i + 1) * W * H * 3] for i in range(n)]
    _VIDEO_CACHE[path] = frames
    return frames


_SMALL = {}

CUT = 999  # 場面の途中で映像を切り替えない（カチカチさせない）

VARIANTS = [  # (横の位置 0〜1, 左右反転, 開始位置の割合, 速さ)
    (0.5, False, 0.0, 1.0), (0.0, True, 0.4, 0.75), (1.0, False, 0.6, 1.0),
    (0.3, True, 0.2, 0.8), (0.8, False, 0.8, 1.0), (0.5, True, 0.5, 0.7),
]


def _clip_len(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 10.0


def footage_bg(path, t, dur, tint=150, blur=0, variant=0):
    """どの場面も、本物の映像を背景にする（上に文字や画面を置けるよう暗くする）。"""
    sw, sh = (W // 4, H // 4) if blur else (W // 2, H // 2)  # 小さく読み込んで拡大（メモリ節約）
    key = (path, sw, variant)
    if key not in _SMALL:
        _SMALL.clear()
        xpos, flip, start, speed = VARIANTS[variant % len(VARIANTS)]
        ss = max(0.0, (_clip_len(path) - dur * speed - 0.3) * start)
        vf = (f"setpts=PTS/{speed},scale={int(sw * 1.35)}:{int(sh * 1.35)}:force_original_aspect_ratio=increase,"
              f"crop={sw}:{sh}:(in_w-{sw})*{xpos}:(in_h-{sh})/2" + (",hflip" if flip else "") + f",fps={FPS}")
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{ss:.2f}", "-i", str(path),
                            "-vf", vf, "-t", f"{dur + 0.5:.2f}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           capture_output=True, check=True)
        n = len(r.stdout) // (sw * sh * 3)
        _SMALL[key] = [r.stdout[i * sw * sh * 3:(i + 1) * sw * sh * 3] for i in range(n)]
    frames = _SMALL[key]
    if not frames:
        return None
    frame = Image.frombytes("RGB", (sw, sh), frames[int(t * FPS) % len(frames)])
    if (sw, sh) != (W, H):
        frame = frame.resize((W, H), Image.BILINEAR)
    # ずっと寄り続け＋横に流れる動き（止まって見えないように）
    z = 1.04 + 0.10 * min(1, t / max(dur, 0.1))
    zw, zh = int(W * z), int(H * z)
    dx = int((zw - W) * (0.5 + 0.45 * math.sin(variant * 1.7 + t * 0.9)))
    frame = frame.resize((zw, zh), Image.BILINEAR).crop((dx, (zh - H) // 2, dx + W, (zh - H) // 2 + H))
    return Image.blend(frame, Image.new("RGB", (W, H), (8, 8, 12)), tint / 255)


def _caption_pill(img, t, text):
    d = ImageDraw.Draw(img)
    f = _font(64)
    tw = d.textlength(text, font=f)
    q = ease_back(t / 0.4)
    y0 = 220 - int((1 - q) * 200)
    d.rounded_rectangle(((W - tw) / 2 - 40, y0, (W + tw) / 2 + 40, y0 + 110), radius=30, fill=RED)
    d.text(((W - tw) / 2, y0 + 18), text, font=f, fill=WHITE)


def _bottom_shade():
    if "_shade" not in _VIDEO_CACHE:
        shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shade)
        for i in range(700):
            sd.line((0, H - 700 + i, W, H - 700 + i), fill=(0, 0, 0, int(170 * i / 700)))
        _VIDEO_CACHE["_shade"] = shade
    return _VIDEO_CACHE["_shade"]


def scene_video(img, t, dur, sc):
    """本物の動画素材を画面いっぱいに流す。下を暗くして字幕を読みやすくする。"""
    frames = _load_clip(sc["video"], dur)
    if not frames:
        return scene_shop(img, t, dur, sc)
    k = int(t * FPS) % len(frames)
    frame = Image.frombytes("RGB", (W, H), frames[k]).convert("RGBA")
    frame.alpha_composite(_bottom_shade())
    img.paste(frame.convert("RGB"))
    if sc.get("caption"):
        _caption_pill(img, t, sc["caption"])


def scene_photo(img, t, dur, sc):
    """写真を画面いっぱいに。ゆっくり寄りながら横に流れる（ケン・バーンズ）。"""
    path = sc["photo"]
    if path not in _PHOTO_CACHE:
        from PIL import ImageOps
        ph = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        _PHOTO_CACHE[path] = ImageOps.fit(ph, (int(W * 1.25), int(H * 1.25)), Image.LANCZOS)
    big = _PHOTO_CACHE[path]
    p = min(1, t / max(dur, 0.1))
    z = 1.25 - 0.15 * p
    cw, ch = int(W * z), int(H * z)
    x = int((big.width - cw) * (0.2 + 0.6 * p))
    y = int((big.height - ch) / 2)
    frame = big.crop((x, y, x + cw, y + ch)).resize((W, H), Image.BILINEAR).convert("RGBA")
    frame.alpha_composite(_bottom_shade())
    img.paste(frame.convert("RGB"))
    if sc.get("caption"):
        _caption_pill(img, t, sc["caption"])


SCENES = {"photo": scene_photo, "shop": scene_shop, "video": scene_video, "point": scene_point,
          "reveal": scene_reveal, "compare": scene_compare, "steps": scene_steps, "stat": scene_stat,
          "template": scene_template, "action": scene_action, "hook": scene_hook, "pair": scene_pair,
          "chart": scene_chart, "search": scene_search, "results": scene_results, "hidden": scene_hidden,
          "check": scene_check, "cta": scene_cta}


def subtitle_layer(text):
    """赤いステッカー風の字幕（画像と貼る位置を返す）。"""
    f = _font(64)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines = _balanced(probe, text.rstrip("。"), f, 860)
    lw = max(probe.textlength(ln, font=f) for ln in lines)
    bw, bh = int(lw + 80), 84 * len(lines) + 44
    st = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(st)
    d.rounded_rectangle((0, 0, bw - 1, bh - 1), radius=30, fill=RED + (245,))
    for i, ln in enumerate(lines):
        d.text(((bw - d.textlength(ln, font=f)) / 2, 18 + i * 84), ln, font=f, fill=WHITE)
    cy = 1470  # Instagramの下のボタンや説明文に隠れない高さ
    return st, ((W - bw) // 2, cy - bh // 2)


def _proxy(src, dst):
    """大きな素材（4Kなど）を、軽い縦長サイズへ変換しておく（処理を速くするため）。"""
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-an", "-t", "30",
                    "-vf", "scale=-2:1240,fps=30", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", str(dst)],
                   check=True)
    return str(dst)


# ---------- 本体 ----------
def make_explainer(script, out_path, brand="RARE STELLA ｜ MEO・SEO運用", vv_dir=VV_DIR):
    from photos import fetch, fetch_video
    tmp = pathlib.Path(tempfile.mkdtemp())
    scenes = [dict(s) for s in script["scenes"]]
    # 映像・写真の場面: Pixabayから自動で取ってくる。取れなければイラストの場面に置きかえる
    for sc in scenes:
        if sc["type"] in ("video", "photo") and not sc.get("video") and not sc.get("photo"):
            clip = fetch_video(sc.get("query", ""))
            if clip:
                sc["type"], sc["video"] = "video", str(clip)
                continue
            got = fetch(sc.get("query", ""))
            if got:
                sc["type"], sc["photo"] = "photo", str(got)
            else:
                sc["type"] = "shop"
    blobs = synth_lines([s.get("speak", s["text"]) for s in scenes], script.get("voice", 14),
                        script.get("speed", 1.12), vv_dir, script.get("intonation", 1.0), script.get("pitch", 0.0),
                        [s.get("voice_tweak") for s in scenes])
    durs = [wav_seconds(b) for b in blobs]
    bg_clips = [sc["video"] for sc in scenes if sc["type"] == "video" and sc.get("video")] + list(script.get("bg_videos", []))
    bg_clips = [_proxy(c, tmp / f"bg{k}.mp4") for k, c in enumerate(bg_clips)]
    for k, sc in enumerate(scenes):  # 場面ごとに合う背景映像を自動ダウンロード（Pixabay）
        q = sc.get("bg_query")
        if not q:
            continue
        clip = fetch_video(q) or fetch_video(sc.get("bg_query_en", ""), lang="en")
        if clip:
            sc["_bg"] = _proxy(clip, tmp / f"scene{k}.mp4")
    if not bg_clips:
        bg_clips = [sc["_bg"] for sc in scenes if sc.get("_bg")]
    join_wavs(blobs, tmp / "voice.wav")
    total = sum(durs)

    # 字幕の時間割
    subs, t0 = [], 0.0
    for sc, dur in zip(scenes, durs):
        if sc.get("kw") is not None:  # 文字を減らす：決めたキーワードだけを出す
            chunks = sc["kw"]
        elif script.get("subs") == "off":
            chunks = []
        else:
            chunks = split_chunks(sc["text"], script.get("sub_len", 20)) if sc["type"] not in ("cta", "hook", "reveal") else []
        if chunks:
            n = sum(max(len(c), 4) for c in chunks)
            t = t0
            for c in chunks:
                dd = (dur - 0.2) * max(len(c), 4) / n
                subs.append((t, t + dd, subtitle_layer(c)))
                t += dd
        t0 += dur

    global NOW
    bf = _font(30)
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                           "-crf", "19", "-pix_fmt", "yuv420p", str(tmp / "video.mp4")], stdin=subprocess.PIPE)
    frames = int(total * FPS)
    starts = [sum(durs[:i]) for i in range(len(durs))]
    for f in range(frames):
        now = f / FPS
        i = max(k for k, s in enumerate(starts) if s <= now)
        NOW = now
        local = now - starts[i]
        sc_i = scenes[i]
        if sc_i["type"] != "video" and (bg_clips or sc_i.get("_bg")):
            # 説明の場面も、映像を暗くした背景の上に出す
            sharp = sc_i["type"] in ("hook", "point", "cta", "reveal", "stat")
            cut = int(local / CUT)
            clip = sc_i.get("_bg") or bg_clips[(i + cut) % len(bg_clips)]
            img = footage_bg(clip, local - cut * CUT, min(CUT, durs[i]) + 0.2, tint=130 if sharp else 150,
                             blur=0 if sharp else 1, variant=i * 3 + cut) or background(now)
        else:
            img = background(now)
        SCENES[sc_i["type"]](img, local, durs[i], sc_i)
        # 場面の切り替わりで、ゆるやかに寄る（カメラの動き）
        zoom = 1 + 0.04 * (1 - ease(local / 0.6)) + 0.03 * min(1, local / max(durs[i], 0.1))
        if zoom > 1.001:
            zw, zh = int(W * zoom), int(H * zoom)
            img = img.resize((zw, zh), Image.BILINEAR).crop(((zw - W) // 2, (zh - H) // 2, (zw - W) // 2 + W, (zh - H) // 2 + H))
        for s_, e_, (st, pos) in subs:
            if s_ <= now < e_:
                k = 1 + 0.25 * (1 - ease_back((now - s_) / 0.18))  # 字幕がポンと出る
                if abs(k - 1) > 0.01:
                    sw, sh = int(st.width * k), int(st.height * k)
                    st2 = st.resize((max(1, sw), max(1, sh)), Image.BILINEAR)
                    pos = (pos[0] - (sw - st.width) // 2, pos[1] - (sh - st.height) // 2)
                    img.paste(st2, pos, st2)
                else:
                    img.paste(st, pos, st)
                break
        d = ImageDraw.Draw(img)
        d.text((60, 56), brand, font=bf, fill=WHITE, stroke_width=3, stroke_fill=(0, 0, 0))
        d.rectangle((0, H - 10, int(W * (f + 1) / frames), H), fill=RED)
        ff.stdin.write(img.tobytes())
    ff.stdin.close()
    ff.wait()

    make_bgm_bright(tmp / "bgm.wav", total + 0.5)
    make_sfx(tmp / "sfx.wav", total + 0.5, [], [] if script.get("no_sfx", True) else [s_ for s_, _, _ in subs])
    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp / "video.mp4"), "-i", str(tmp / "voice.wav"),
        "-i", str(tmp / "bgm.wav"), "-i", str(tmp / "sfx.wav"), "-filter_complex",
        "[1:a]aresample=44100,aformat=channel_layouts=stereo,loudnorm=I=-15:TP=-1.5,asplit=2[voice1][voice2];"
        "[2:a]aresample=44100,volume=0.26,aformat=channel_layouts=stereo[music];"
        "[music][voice1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[ducked];"
        "[3:a]aresample=44100,volume=0.5,aformat=channel_layouts=stereo[fx];"
        "[voice2][ducked][fx]amix=inputs=3:duration=first:normalize=0[mix]",
        "-map", "0:v", "-map", "[mix]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", str(out_path)], check=True)
    return out_path


if __name__ == "__main__":
    make_explainer(json.load(open(sys.argv[1], encoding="utf-8")), sys.argv[2])
