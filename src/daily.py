"""毎日動く：前日までの分析を読んで、AIが「明日の1本」を書き、動画・画像にする（PDCAのAct→Plan→Do準備）。

- 明日の分がすでにある（週まとめで作った分）なら、それを下書きとして、分析をもとに直す
- できたものは queue/<明日の日付>.json に置き、ワークフローが「確認待ち」にする
"""
import datetime as dt
import json
import re
import sys

import anthropic

from common import QUEUE, ROOT, WEEKDAYS, env, load_config, read_json, today_jst, write_json
from explainer import SCENES, VOICE_NAMES
from generate import build_media

RULES = """
【必ず守ること（オーナーからの改善指示）】
- すべて日本語。英語は使わない
- 届けたい相手は、地域のお店のオーナー。まず相手の悩みに寄り添ってから話す（売り込みから始めない）
- 1秒目（最初の場面）で、何の話かがはっきり分かる強い一言にする
- 画面に出す文字は短く（1行12文字以内が目安）。長い説明は声で話す
- 専門的な知識を入れて「詳しい会社だ」と思ってもらう。ただし数字や事実は「使ってよい事実」の中のものだけ。数字を出す場面には出典(source)を必ず入れる
- 架空の口コミ、架空のお客様の声、順位や売上の保証は書かない
- 最後は「店名をDMしてもらえれば、無料で見ます」の形でDMにつなげる
- 保存したくなる中身（すぐ使えるチェックリストやテンプレ）を入れ、「保存して見返してね」と言う
- リールは6〜7場面、全体で25〜35秒程度
"""

SCHEMA = """
【出力するJSONの形】（JSONだけを返す。説明やコードブロックは書かない）
{"type":"reel" または "image",
 "headline":"管理用の見出し（25字以内）",
 "industry":"飲食店 など",
 "improvement":"前日までの分析から、今回どこを直したか（日本語で1〜2文）",
 "caption":"投稿の説明文（改行あり、300〜500字）",
 "hashtags":["#..."（6〜8個）],
 "points":["画像のときだけ：画像に載せる3つのポイント（各25字以内）"],
 "reel_script":{"voice":14,"speed":1.17,"intonation":1.3,"scenes":[ 場面の配列 ]}  ← リールのときだけ
}

【場面(scene)の種類と項目】すべての場面に "text"（読み上げる文章）と "bg_query"（背景映像の日本語の検索語）と "bg_query_en"（英語の検索語）を入れる
- hook: 最初の場面。"big":"大きく出す文字（\\nで改行、3行まで）", "kw":[]
- reveal: 答えをドーンと見せる。"kicker":"上の小さい文字", "big":"大きい文字（\\nで改行）"
- stat: 数字を見せる。"kicker", "num":"42%", "label", "source":"※出典", "kw":[]
- check: チェックリスト。"heading", "items":[最大4つ、各14字以内], "kw":[]
- steps: 3ステップ。"heading", "steps":[["見出し","補足"],...3つ], "kw":[]
- compare: 口コミ返信あり/なしの比較（"kw":["返信なし","返信あり"]）
- template: そのまま使える文章。"label", "body":"テンプレ本文（\\nで改行）", "kw":[]
- search: スマホで検索する画面。"query":"近く 居酒屋" など, "kw":["短い言葉"]
- results: 検索結果の上位3件。"query", "shops":[["店名A","4.6","(214)","徒歩2分"],...3つ], "kw":["短い言葉"]
- action: 経路をタップしてお店へ。"name":"あなたのお店", "kw":[]
- cta: 最後の案内。"lines":["1行目","2行目"], "sub":"@rarestella.sma へDM"
"""


def tomorrow():
    return today_jst() + dt.timedelta(days=1)


def recent_headlines(n=10):
    items = []
    for p in sorted(QUEUE.glob("2*.json"))[-n:]:
        try:
            items.append(read_json(p).get("headline", ""))
        except Exception:
            pass
    return items


def build_prompt(cfg, day, kind, industry, analysis, draft):
    a = cfg["account"]
    facts = "\n".join(f"- {x}" for x in a.get("facts", []))
    learn = "\n".join(f"- {x}" for x in (analysis or {}).get("learnings", [])) or "- まだ数字がありません（初回）"
    top = ""
    if analysis and analysis.get("posts"):
        rows = [p for p in analysis["posts"] if p.get("reach")][:10]
        top = "\n".join(f"- {p['date']} {p.get('headline') or p['caption'][:20]}：リーチ{p['reach']}・保存{p.get('saved')}・シェア{p.get('shares')}"
                        f"・平均視聴{p.get('avg_watch_sec') or '-'}秒" for p in rows)
    draft_txt = json.dumps(draft, ensure_ascii=False)[:3000] if draft else "なし（新しく作る）"
    return f"""あなたは店舗集客（MEO・Googleマップ）専門の会社「{a['name']}」のInstagram運用担当です。
{day.month}/{day.day}（{WEEKDAYS[day.weekday()]}）に投稿する1本を作ってください。

種類: {"リール動画" if kind == "reel" else "画像"}
今週のテーマの業種: {industry}
会社の事業: {a['business']}

使ってよい事実（数字・事実はこれだけ）:
{facts}

これまでの投稿の数字:
{top or "- まだありません"}

分析からの気づき:
{learn}

最近の見出し（同じ内容をくり返さない）:
{chr(10).join("- " + h for h in recent_headlines())}

この日の下書き（あれば、分析をもとに改善して書き直す）:
{draft_txt}
{RULES}
{SCHEMA}"""


def ask(cfg, prompt):
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    msg = client.messages.create(model=cfg.get("model", "claude-sonnet-5-5"), max_tokens=6000,
                                 messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if b.type == "text")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def valid(post, kind):
    if post.get("type") != kind or not post.get("caption") or not post.get("headline"):
        return False
    if kind == "reel":
        sc = (post.get("reel_script") or {}).get("scenes") or []
        return 4 <= len(sc) <= 9 and all(s.get("type") in SCENES and s.get("text") for s in sc)
    return len(post.get("points") or []) >= 2


def main():
    cfg = load_config()
    day = tomorrow()
    kinds = cfg.get("schedule") or ["reel"] * 7
    kind = kinds[day.weekday()]
    plan = cfg.get("industries") or ["飲食店"]
    start = cfg.get("industry_start_week", day.isocalendar()[1])
    industry = plan[(day.isocalendar()[1] - start) % len(plan)]
    analysis_path = ROOT / "analytics" / "latest.json"
    analysis = read_json(analysis_path) if analysis_path.exists() else None
    target = QUEUE / f"{day.isoformat()}.json"
    draft = read_json(target) if target.exists() else None
    if draft and draft.get("posted"):
        print("明日の分はもう投稿済みです。")
        return

    prompt = build_prompt(cfg, day, kind, industry, analysis, draft)
    post = None
    for _ in range(2):  # 形がおかしければ1回だけ作り直す
        try:
            post = ask(cfg, prompt)
        except Exception as e:
            print("AIの返答を読めませんでした:", e)
            continue
        if valid(post, kind):
            break
        post = None
    if not post:
        raise SystemExit("明日の台本を作れませんでした。")

    media = build_media(cfg, day, kind, post)
    tags = " ".join(post.get("hashtags", []))
    if post.get("reel_script"):
        v = post["reel_script"].get("voice", 14)
        tags += f"\n\nVOICEVOX:{VOICE_NAMES.get(v, 'VOICEVOX')}"
    write_json(target, {
        "date": day.isoformat(), "type": kind, "headline": post["headline"], **media,
        "caption": f"{post['caption']}\n\n{tags}".strip(), "threads": post["caption"][:450],
        "industry": post.get("industry", industry), "improvement": post.get("improvement", ""),
        "script": post, "posted": {},
    })
    print(f"{day}（{'リール' if kind == 'reel' else '画像'}）: {post['headline']}")
    print("今回の改善:", post.get("improvement", ""))


if __name__ == "__main__":
    sys.exit(main())
