"""文字が動くリール動画（1080x1920・30fps・MP4）と、同じデザインの画像投稿を無料で作る。

scenes は [{"text": "...", "big": true/false}] のリスト。1シーンごとに文字が下からふわっと出てくる。
"""
import json
import pathlib
import subprocess
import sys

from PIL import Image, ImageDraw

from render import _font, _wrap

W, H, FPS = 1080, 1920, 30
SCENE_SEC = 3.2
IN_SEC = 0.45


def _hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _background(c1, c2):
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    a, b = _hex(c1), _hex(c2)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3)))
    return bg


def _ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _layout(draw, text, big):
    size = 92 if big else 68
    while True:
        font = _font(size)
        lines = _wrap(draw, text, font, W - 220 - size)  # 句読点のぶら下がり分を空ける
        if len(lines) <= (4 if big else 6) or size <= 44:
            return font, lines, int(size * 1.45)
        size -= 6


def make_reel(scenes, out_path, brand="", colors=("#13B5E0", "#0D3A9E"), accent="#FFB000"):
    bg = _background(*colors)
    probe = ImageDraw.Draw(bg)
    laid = [_layout(probe, s["text"], s.get("big", False)) for s in scenes]
    brand_font = _font(40)
    total = int(len(scenes) * SCENE_SEC * FPS)

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ff = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",  # 無音トラック（Instagramの仕様上つけておく）
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out_path),
    ], stdin=subprocess.PIPE)

    for f in range(total):
        sec = f / FPS
        idx = min(int(sec // SCENE_SEC), len(scenes) - 1)
        local = sec - idx * SCENE_SEC
        frame = bg.copy()
        d = ImageDraw.Draw(frame)
        font, lines, lh = laid[idx]

        p = _ease(local / IN_SEC)
        block_h = lh * len(lines)
        y0 = (H - block_h) // 2 + int((1 - p) * 60)
        # 文字の後ろに半透明の白い板
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.rounded_rectangle((60, y0 - 70, W - 60, y0 + block_h + 50), radius=36,
                             fill=(255, 255, 255, int(235 * p)))
        ld.rectangle((60, y0 - 70, 72, y0 + block_h + 50), fill=_hex(accent) + (int(255 * p),))
        y = y0
        for ln in lines:
            ld.text((110, y), ln, font=font, fill=(16, 35, 80, int(255 * p)))
            y += lh
        frame.paste(layer, (0, 0), layer)

        # 下の進み具合バーとブランド名
        d.rectangle((0, H - 12, int(W * (f + 1) / total), H), fill=_hex(accent))
        if brand:
            d.text((60, H - 140), brand, font=brand_font, fill=(255, 255, 255))
        ff.stdin.write(frame.tobytes())

    ff.stdin.close()
    if ff.wait() != 0:
        raise SystemExit("動画の書き出しに失敗しました")
    return out_path


def make_card(headline, points, out_path, brand="", colors=("#13B5E0", "#0D3A9E"), accent="#FFB000"):
    """リールと同じデザインの画像投稿（1080x1350）。見出し＋ポイント最大3つ。"""
    cw, ch = 1080, 1350
    a, b = _hex(colors[0]), _hex(colors[1])
    img = Image.new("RGB", (cw, ch))
    d = ImageDraw.Draw(img)
    for y in range(ch):
        t = y / ch
        d.line([(0, y), (cw, y)], fill=tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3)))

    # 見出し（白文字）
    size = 84
    while True:
        hf = _font(size)
        hl = _wrap(d, headline, hf, cw - 160 - size)
        if len(hl) <= 3 or size <= 52:
            break
        size -= 6
    y = 120
    d.rectangle((80, y, 92, y + int(size * 1.4) * len(hl) - 20), fill=_hex(accent))
    for ln in hl:
        d.text((120, y), ln, font=hf, fill=(255, 255, 255))
        y += int(size * 1.4)

    # ポイント（白い板）
    pf, nf = _font(46), _font(46)
    y += 60
    for i, pt in enumerate(points[:3], 1):
        pl = _wrap(d, pt, pf, cw - 380)
        box_h = 64 * len(pl) + 70
        d.rounded_rectangle((80, y, cw - 80, y + box_h), radius=28, fill=(255, 255, 255))
        d.ellipse((115, y + 35, 185, y + 105), fill=_hex(accent))
        d.text((150, y + 70), str(i), font=nf, fill=(16, 35, 80), anchor="mm")
        ty = y + 35
        for ln in pl:
            d.text((215, ty), ln, font=pf, fill=(16, 35, 80))
            ty += 64
        y += box_h + 30
        if y > ch - 220:
            break

    if brand:
        d.text((80, ch - 110), brand, font=_font(38), fill=(255, 255, 255))
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=92)
    return out_path


def reel_cover(video_path, out_path, at=1.2):
    """確認画面用に、リールの1コマを画像で切り出す。"""
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(at), "-i", str(video_path),
                    "-frames:v", "1", "-q:v", "3", str(out_path)], check=True)
    return out_path


if __name__ == "__main__":
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    make_reel(data["scenes"], sys.argv[2], brand=data.get("brand", ""))
