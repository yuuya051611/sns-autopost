"""来週月〜日の7投稿（リール動画と画像）を作って queue/ に置く。

文章の作り方は2通り:
- 無料: Claudeとのチャットで書いてもらった文章（inbox/posts.json）を使う
- 有料: ANTHROPIC_API_KEY があれば、毎週自動でAIに書かせる

リールに reel_script があれば、ナレーション付きの解説リール（映像背景）を作る。
"""
import datetime as dt
import json
import os
import re
import sys

import anthropic

from common import QUEUE, ROOT, WEEKDAYS, env, load_config, read_json, today_jst, write_json
from reel import make_card, make_reel, reel_cover


def next_week_dates(base=None):
    base = base or today_jst()
    monday = base + dt.timedelta(days=(7 - base.weekday()) % 7 or 7)
    return [monday + dt.timedelta(days=i) for i in range(7)]


def _lines(items):
    return "\n".join(f"- {x}" for x in (items or [])) or "- なし"


def build_prompt(cfg, dates, kinds):
    a = cfg["account"]
    plan = "\n".join(
        f"{i + 1}本目: {d.month}/{d.day}({WEEKDAYS[d.weekday()]}) {'リール動画' if k == 'reel' else '画像'}"
        for i, (d, k) in enumerate(zip(dates, kinds)))
    return f"""あなたはInstagram運用のプロです。次のアカウントの来週7本分の投稿を日本語で作ってください。

アカウント: {a['name']}
事業内容: {a['business']}
届けたい相手: {a['audience']}
投稿のゴール: {a['goal']}
最後の呼びかけ: {a['cta']}
雰囲気: {a['tone']}
今週のお知らせ: {cfg.get('news') or 'なし'}

使ってよい事実（これ以外の数字・実績は書かない）:
{_lines(a.get('facts'))}

絶対に書かないこと:
{_lines(a.get('never'))}

投稿予定:
{plan}

作り方:
- 7本はテーマを変える（相手が抱える悩みへの気づき、すぐできるチェック・豆知識、よくある間違い、運用と登録代行の違い、支援の流れ、無料チェックの案内 など）。売り込みは7本中1〜2本まで
- 一般論として広く知られている事実以外の統計や％は使わない。数字を出すときは「目安」「例えば」と分かる書き方にする
- リール動画は scenes に4〜6場面。1場面は全角40字以内で、1場面目は思わず止まって見たくなる問いかけ。最後の場面は呼びかけ
- 画像は headline（全角22字以内）と points（3つ、各全角30字以内）
- caption: 改行を使って読みやすく、200〜400字。最後に呼びかけ
- hashtags: 5〜8個（#MEO対策 #Googleマップ集客 など、店舗オーナーが見るもの）

JSONだけを返してください（説明やコードブロックは不要）:
{{"posts":[{{"type":"reel","headline":"管理用の短いタイトル","scenes":["..."],"points":[],"caption":"","hashtags":["#..."]}},{{"type":"image","headline":"","scenes":[],"points":["","",""],"caption":"","hashtags":["#..."]}}]}}
postsは投稿予定の順番どおり7件、typeも予定どおりにする。"""


def ask_claude(cfg, dates, kinds):
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model=cfg.get("model", "claude-sonnet-5-5"),
        max_tokens=8000,
        messages=[{"role": "user", "content": build_prompt(cfg, dates, kinds)}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    posts = json.loads(text)["posts"]
    if len(posts) < len(dates):
        raise SystemExit(f"AIの返答が{len(posts)}件しかありません。もう一度実行してください。")
    return posts[: len(dates)]


def build_media(cfg, d, kind, post):
    design = cfg.get("design", {})
    colors = tuple(design.get("colors", ["#13B5E0", "#0D3A9E"]))
    accent = design.get("accent", "#FFB000")
    brand = cfg["account"].get("brand_line", "")
    stem = QUEUE / "media" / d.isoformat()
    if kind == "reel" and post.get("reel_script"):
        # ナレーション＋映像背景＋動く画面の「解説リール」
        from explainer import make_explainer
        video = make_explainer(post["reel_script"], f"{stem}.mp4", brand=brand)
        reel_cover(video, f"{stem}_cover.jpg", at=0.8)
        return {"video": f"queue/media/{d.isoformat()}.mp4", "image": f"queue/media/{d.isoformat()}_cover.jpg"}
    if kind == "reel":
        scenes = [{"text": s, "big": i in (0, len(post["scenes"]) - 1)} for i, s in enumerate(post["scenes"])]
        video = make_reel(scenes, f"{stem}.mp4", brand=brand, colors=colors, accent=accent)
        reel_cover(video, f"{stem}_cover.jpg")
        return {"video": f"queue/media/{d.isoformat()}.mp4", "image": f"queue/media/{d.isoformat()}_cover.jpg"}
    make_card(post["headline"], post.get("points", []), f"{stem}.jpg", brand=brand, colors=colors, accent=accent)
    return {"image": f"queue/media/{d.isoformat()}.jpg"}


INBOX = ROOT / "inbox" / "posts.json"


def main():
    cfg = load_config()
    dates = next_week_dates()
    kinds = (cfg.get("schedule") or ["image"] * 7)[:7]
    if INBOX.exists():
        posts = read_json(INBOX)["posts"][: len(dates)]
        kinds = [p.get("type", k) for p, k in zip(posts, kinds)]
        INBOX.unlink()
        print("Claudeが書いた文章（inbox/posts.json）を使います。")
    elif os.environ.get("ANTHROPIC_API_KEY"):
        if all((QUEUE / f"{d.isoformat()}.json").exists() for d in dates):
            print("来週分はもう作成済みです。")
            return
        posts = ask_claude(cfg, dates, kinds)
    else:
        print("来週分の文章がまだありません。Claudeに「来週分作って」と送ってください。")
        return

    for d, kind, post in zip(dates, kinds, posts):
        media = build_media(cfg, d, kind, post)
        tags = " ".join(post.get("hashtags", []))
        if post.get("reel_script"):  # VOICEVOXの利用規約で必要なクレジット表記
            from explainer import VOICE_NAMES
            v = post["reel_script"].get("voice", 14)
            tags += f"\n\nVOICEVOX:{VOICE_NAMES.get(v, 'VOICEVOX')}"
        write_json(QUEUE / f"{d.isoformat()}.json", {
            "date": d.isoformat(),
            "type": kind,
            "headline": post["headline"],
            **media,
            "caption": f"{post['caption']}\n\n{tags}".strip(),
            "threads": post["caption"][:450],
            "posted": {},
        })
        print(f"{d}（{'リール' if kind == 'reel' else '画像'}）: {post['headline']}")


if __name__ == "__main__":
    sys.exit(main())
