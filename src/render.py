"""お店の写真に見出しの帯を重ねて、Instagram向けの縦長画像(1080x1350)を作る。"""
import pathlib

from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 1080, 1350
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
]


def _font(size):
    for p in FONT_CANDIDATES:
        if pathlib.Path(p).exists():
            return ImageFont.truetype(p, size, index=0)  # index 0 = 日本語(JP)
    raise SystemExit("日本語フォントが見つかりません（fonts-noto-cjk をインストールしてください）")


def _wrap(draw, text, font, max_w):
    """日本語は単語区切りがないので1文字ずつ詰めて折り返す。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def render(photo_path, headline, sub, out_path, accent="#1F3A5F", text_color="#FFFFFF"):
    img = Image.open(photo_path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = ImageOps.fit(img, (W, H), Image.LANCZOS, centering=(0.5, 0.4))

    draw = ImageDraw.Draw(img)
    pad = 64
    size = 76
    # 見出しが3行に収まるまで文字を小さくする
    while True:
        font = _font(size)
        lines = _wrap(draw, headline, font, W - pad * 2)
        if len(lines) <= 3 or size <= 48:
            break
        size -= 6
    sub_font = _font(36)
    sub_lines = _wrap(draw, sub, sub_font, W - pad * 2)[:2] if sub else []

    line_h = int(size * 1.35)
    band_h = pad + line_h * len(lines) + (24 + 50 * len(sub_lines) if sub_lines else 0) + pad
    top = H - band_h

    overlay = Image.new("RGBA", (W, band_h), accent + "E6")  # 90%不透明
    img.paste(overlay, (0, top), overlay)

    y = top + pad
    for ln in lines:
        draw.text((pad, y), ln, font=font, fill=text_color)
        y += line_h
    if sub_lines:
        y += 24
        for ln in sub_lines:
            draw.text((pad, y), ln, font=sub_font, fill=text_color)
            y += 50

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=90)  # InstagramはJPEGのみ対応
    return out_path
