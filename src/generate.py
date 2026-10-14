"""毎週1回動く：来週月〜日の7投稿を作って queue/ に置く。

写真を実際にAIに見せて、その写真に合った文章を書かせる。
写真は「使った回数が少ない順」に選ぶので、同じ写真ばかりにならない。
"""
import base64
import datetime as dt
import io
import json
import re
import sys

import anthropic
from PIL import Image, ImageOps

from common import QUEUE, WEEKDAYS, env, list_photos, load_config, read_json, today_jst, write_json
from render import render

USAGE_FILE = QUEUE / "photo_usage.json"


def next_week_dates(base=None):
    base = base or today_jst()
    monday = base + dt.timedelta(days=(7 - base.weekday()) % 7 or 7)
    return [monday + dt.timedelta(days=i) for i in range(7)]


def pick_photos(n):
    photos = list_photos()
    if not photos:
        raise SystemExit("photos/ フォルダに写真がありません。お店の写真を入れてください。")
    usage = read_json(USAGE_FILE) if USAGE_FILE.exists() else {}
    ranked = sorted(photos, key=lambda p: (usage.get(p.name, 0), p.name))
    chosen = [ranked[i % len(ranked)] for i in range(n)]
    for p in chosen:
        usage[p.name] = usage.get(p.name, 0) + 1
    return chosen, usage


def thumb_b64(path):
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((640, 640))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def build_prompt(cfg, dates):
    s = cfg["shop"]
    days = "、".join(f"{i + 1}枚目={d.month}/{d.day}({WEEKDAYS[d.weekday()]})" for i, d in enumerate(dates))
    return f"""あなたは地域の小さなお店のSNS運用担当です。添付の写真7枚それぞれに1投稿ずつ、InstagramとThreadsに自動投稿する文章を日本語で作ってください。

店名: {s.get('name')}
業種: {s.get('kind')}
地域: {s.get('area')}
来てほしいお客さん: {s.get('target')}
強み・推したいもの: {s.get('strength')}
雰囲気: {s.get('tone')}
定休日: {s.get('closed_days')}
Webサイト・予約: {s.get('url') or 'なし'}
今週のお知らせ: {cfg.get('news') or 'なし'}

投稿日: {days}

ルール:
- 必ず写真に実際に写っているものに沿って書く。写っていないものを写っているかのように書かない
- 7本はテーマを変える（商品紹介、こだわり・裏側、お客さんの悩みに答える豆知識、人柄、季節や地域の話題、お知らせ など）
- 価格・効果・実績など、上の情報にない事実は作らない。架空のお客様の声や口コミは書かない
- 誇大表現、他店との比較、医療・効果の断定はしない
- 定休日の投稿でも問題ない内容にする（「本日営業中」のような日付依存の表現は避ける）
- headline: 画像に大きく載せる見出し。全角20字以内
- sub: 画像に小さく載せる一言。全角25字以内（店名でもよい）
- caption: Instagram用。改行を使って読みやすく、150〜300字。最後に来店・予約につながる一言
- hashtags: 地域名・業種を含む5〜8個
- threads: Threads用の短い文。120字以内、ハッシュタグは1個まで

JSONだけを返してください（説明やコードブロックは不要）:
{{"posts":[{{"headline":"","sub":"","caption":"","hashtags":["#..."],"threads":""}}]}}
postsは写真の順番どおり7件。"""


def ask_claude(cfg, photos, dates):
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    content = []
    for p in photos:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": thumb_b64(p)}})
    content.append({"type": "text", "text": build_prompt(cfg, dates)})
    msg = client.messages.create(
        model=cfg.get("model", "claude-sonnet-5-5"),
        max_tokens=6000,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    data = json.loads(text)
    posts = data["posts"]
    if len(posts) < len(photos):
        raise SystemExit(f"AIの返答が{len(posts)}件しかありません。もう一度実行してください。")
    return posts[: len(photos)]


def main():
    cfg = load_config()
    dates = next_week_dates()
    if all((QUEUE / f"{d.isoformat()}.json").exists() for d in dates):
        print("来週分はもう作成済みです。")
        return
    photos, usage = pick_photos(len(dates))
    posts = ask_claude(cfg, photos, dates)
    design = cfg.get("design", {})

    for d, photo, post in zip(dates, photos, posts):
        img_path = QUEUE / "images" / f"{d.isoformat()}.jpg"
        render(photo, post["headline"], post.get("sub", ""), img_path,
               accent=design.get("accent", "#1F3A5F"), text_color=design.get("text", "#FFFFFF"))
        tags = " ".join(post.get("hashtags", []))
        write_json(QUEUE / f"{d.isoformat()}.json", {
            "date": d.isoformat(),
            "photo": photo.name,
            "image": f"queue/images/{d.isoformat()}.jpg",
            "headline": post["headline"],
            "caption": f"{post['caption']}\n\n{tags}".strip(),
            "threads": post.get("threads") or post["caption"][:450],
            "posted": {},
        })
        print(f"{d} を作成: {post['headline']}")
    write_json(USAGE_FILE, usage)


if __name__ == "__main__":
    sys.exit(main())
