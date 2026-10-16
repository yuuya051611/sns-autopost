"""毎朝動く：インスタの投稿の数字を集めて、分析をまとめる（PDCAのCheck）。

出力:
- analytics/latest.json : 投稿ごとの数字・割合、平均、良かった投稿・悪かった投稿、気づき（日本語）
- analytics/history.csv : 毎日の記録（推移のグラフ用）
"""
import csv
import datetime as dt
import json
import os
import pathlib
import sys

import requests

from common import QUEUE, ROOT, env, read_json

API = "https://graph.instagram.com/v23.0"
OUT = ROOT / "analytics"
BASE_METRICS = ["reach", "views", "saved", "shares", "likes", "comments", "total_interactions"]
REEL_METRICS = ["ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]


def _get(url, params):
    r = requests.get(url, params=params, timeout=30)
    if r.status_code >= 400:
        return None, r.text[:300]
    return r.json(), None


def media_list(uid, token, limit=40):
    data, err = _get(f"{API}/{uid}/media", {
        "fields": "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count",
        "limit": limit, "access_token": token})
    if err:
        raise SystemExit(f"投稿の一覧を取れませんでした: {err}")
    return data.get("data", [])


def media_insights(mid, token, is_reel):
    """まとめて取れなければ、1つずつ取る（投稿の種類で使えない指標があるため）。"""
    metrics = BASE_METRICS + (REEL_METRICS if is_reel else [])
    out = {}
    data, err = _get(f"{API}/{mid}/insights", {"metric": ",".join(metrics), "access_token": token})
    rows = data.get("data", []) if data else []
    if err:
        for m in metrics:
            d, e = _get(f"{API}/{mid}/insights", {"metric": m, "access_token": token})
            if d:
                rows += d.get("data", [])
    for row in rows:
        vals = row.get("values") or [{}]
        out[row["name"]] = vals[0].get("value", row.get("total_value", {}).get("value"))
    return out, (err if not rows else None)


def our_posts():
    """自分たちが作った投稿（queue）の情報。キャプションの書き出しで突き合わせる。"""
    items = {}
    for p in sorted(QUEUE.glob("*.json")):
        if p.name == "photo_usage.json":
            continue
        try:
            q = read_json(p)
        except Exception:
            continue
        items[(q.get("caption") or "")[:40]] = q
    return items


def rate(a, b):
    return round(a / b, 4) if a is not None and b else None


def analyze(posts):
    done = [p for p in posts if p.get("reach")]
    avg = {}
    for k in ("reach", "views", "saved", "shares", "save_rate", "share_rate", "eng_rate", "avg_watch_sec"):
        vals = [p[k] for p in done if p.get(k) is not None]
        avg[k] = round(sum(vals) / len(vals), 4) if vals else None
    learn = []
    if done:
        best = max(done, key=lambda p: (p.get("save_rate") or 0) + (p.get("share_rate") or 0))
        worst = min(done, key=lambda p: (p.get("save_rate") or 0) + (p.get("share_rate") or 0))
        learn.append(f"一番保存・シェアされたのは「{best.get('headline') or best['caption'][:24]}」"
                     f"（保存率{(best.get('save_rate') or 0) * 100:.1f}%）。この型は続ける。")
        if worst is not best:
            learn.append(f"反応が弱かったのは「{worst.get('headline') or worst['caption'][:24]}」"
                         f"（保存率{(worst.get('save_rate') or 0) * 100:.1f}%）。つかみと中身を見直す。")
        reels = [p for p in done if p.get("is_reel")]
        imgs = [p for p in done if not p.get("is_reel")]
        if reels and imgs:
            rr = sum(p["reach"] for p in reels) / len(reels)
            ir = sum(p["reach"] for p in imgs) / len(imgs)
            learn.append(f"平均リーチはリール{rr:.0f}・画像{ir:.0f}。"
                         + ("リールの方が届いている。" if rr >= ir else "画像の方が届いている。"))
        watch = [p for p in reels if p.get("avg_watch_sec")]
        if watch:
            w = sum(p["avg_watch_sec"] for p in watch) / len(watch)
            learn.append(f"リールの平均視聴時間は{w:.1f}秒。" + ("冒頭で離れている可能性が高いので、最初の1秒をもっと強く。" if w < 8 else "最後まで見られている。"))
    return {"average": avg, "learnings": learn}


def main():
    token, uid = env("IG_ACCESS_TOKEN"), env("IG_USER_ID")
    mine = our_posts()
    posts, errors = [], []
    for m in media_list(uid, token):
        is_reel = m.get("media_product_type") == "REELS"
        ins, err = media_insights(m["id"], token, is_reel)
        if err:
            errors.append(err)
        q = mine.get((m.get("caption") or "")[:40], {})
        reach = ins.get("reach")
        watch_ms = ins.get("ig_reels_avg_watch_time")
        posts.append({
            "id": m["id"], "date": (m.get("timestamp") or "")[:10], "permalink": m.get("permalink"),
            "caption": (m.get("caption") or "")[:120], "is_reel": is_reel,
            "headline": q.get("headline"), "type": q.get("type") or ("reel" if is_reel else "image"),
            "reach": reach, "views": ins.get("views"), "saved": ins.get("saved"), "shares": ins.get("shares"),
            "likes": ins.get("likes", m.get("like_count")), "comments": ins.get("comments", m.get("comments_count")),
            "avg_watch_sec": round(watch_ms / 1000, 1) if watch_ms else None,
            "save_rate": rate(ins.get("saved"), reach), "share_rate": rate(ins.get("shares"), reach),
            "eng_rate": rate(ins.get("total_interactions"), reach),
        })
    now = (dt.datetime.utcnow() + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
    result = {"updated": now, "posts": posts, **analyze(posts)}
    if errors and not any(p.get("reach") for p in posts):
        result["warning"] = "数字を読む許可がない可能性があります（instagram_business_manage_insights）。" + errors[0]
    OUT.mkdir(exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    hist = OUT / "history.csv"
    new = not hist.exists()
    with open(hist, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["取得日", "投稿日", "投稿ID", "見出し", "種類", "リーチ", "再生・表示", "保存", "シェア", "いいね", "コメント", "平均視聴秒"])
        for p in posts:
            w.writerow([now[:10], p["date"], p["id"], p.get("headline") or "", p["type"], p.get("reach"), p.get("views"),
                        p.get("saved"), p.get("shares"), p.get("likes"), p.get("comments"), p.get("avg_watch_sec")])
    print(f"{len(posts)}件の投稿の数字を集めました。")
    for line in result["learnings"]:
        print("・" + line)


if __name__ == "__main__":
    sys.exit(main())
