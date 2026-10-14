"""毎日1回動く：今日の分を Instagram と Threads に投稿する。

すでに投稿済みの先には二重投稿しない（queue/日付.json の posted に記録）。
DRY_RUN=1 にすると、実際には投稿せず内容だけ表示する。
"""
import os
import sys
import time

import requests

from common import QUEUE, env, load_config, read_json, today_jst, write_json

IG_API = "https://graph.instagram.com/v23.0"
TH_API = "https://graph.threads.net/v1.0"


def image_url(rel_path):
    base = os.environ.get("IMAGE_BASE_URL", "").rstrip("/")
    if not base:
        repo = env("GITHUB_REPOSITORY")
        branch = os.environ.get("GITHUB_REF_NAME") or "main"
        base = f"https://raw.githubusercontent.com/{repo}/{branch}"
    return f"{base}/{rel_path}"


def _check(r, what):
    if r.status_code >= 400:
        # トークンはログに出さない
        raise RuntimeError(f"{what} に失敗しました（{r.status_code}）: {r.text[:500]}")
    return r.json()


def post_instagram(item, urls):
    token, uid = env("IG_ACCESS_TOKEN"), env("IG_USER_ID")
    data = {"caption": item["caption"][:2200], "access_token": token}
    if item.get("type") == "reel":
        data.update({"media_type": "REELS", "video_url": urls["video"], "share_to_feed": "true"})
    else:
        data["image_url"] = urls["image"]
    c = _check(requests.post(f"{IG_API}/{uid}/media", data=data, timeout=60), "Instagram 投稿の登録")
    cid = c["id"]
    for _ in range(40):  # 動画・画像の処理完了を待つ（最大約6分）
        s = _check(requests.get(f"{IG_API}/{cid}", params={
            "fields": "status_code", "access_token": token}, timeout=30), "Instagram 状態確認")
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            raise RuntimeError("Instagram側で画像・動画の処理に失敗しました")
        time.sleep(10)
    else:
        raise RuntimeError("Instagram側の処理が時間内に終わりませんでした")
    p = _check(requests.post(f"{IG_API}/{uid}/media_publish", data={
        "creation_id": cid, "access_token": token}, timeout=60), "Instagram 公開")
    return p["id"]


def post_threads(item, urls):
    token = env("THREADS_ACCESS_TOKEN")
    data = {"text": item["threads"][:500], "access_token": token}
    if item.get("type") == "reel":
        data.update({"media_type": "VIDEO", "video_url": urls["video"]})
    else:
        data.update({"media_type": "IMAGE", "image_url": urls["image"]})
    c = _check(requests.post(f"{TH_API}/me/threads", data=data, timeout=60), "Threads 投稿の登録")
    time.sleep(30)  # Threads公式の推奨待ち時間
    p = _check(requests.post(f"{TH_API}/me/threads_publish", data={
        "creation_id": c["id"], "access_token": token}, timeout=60), "Threads 公開")
    return p["id"]


def main():
    cfg = load_config()
    day = os.environ.get("POST_DATE") or today_jst().isoformat()
    path = QUEUE / f"{day}.json"
    if not path.exists():
        print(f"{day} の投稿は予定にありません。")
        return 0
    item = read_json(path)
    urls = {k: image_url(item[k]) for k in ("image", "video") if item.get(k)}
    dry = os.environ.get("DRY_RUN") == "1"
    targets = {"instagram": post_instagram, "threads": post_threads}
    failed = []

    for name, fn in targets.items():
        if not cfg.get("platforms", {}).get(name):
            continue
        if item["posted"].get(name):
            print(f"{name}: 投稿済みなのでスキップ")
            continue
        if dry:
            print(f"[DRY_RUN] {name} に投稿予定（{item.get('type', 'image')}）\n素材: {urls}\n{item['caption'] if name == 'instagram' else item['threads']}\n")
            continue
        try:
            item["posted"][name] = fn(item, urls)
            print(f"{name}: 投稿しました")
        except Exception as e:  # 片方が失敗してももう片方は投稿する
            print(f"{name}: {e}")
            failed.append(name)
        write_json(path, item)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
