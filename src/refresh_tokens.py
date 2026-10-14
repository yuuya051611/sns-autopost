"""InstagramとThreadsのトークン（有効期限60日）を延長し、新しいトークンをファイルに書き出す。
ワークフローがそのファイルを GitHub Secrets に保存し直す。トークン自体はログに出さない。
"""
import pathlib
import sys

import requests

from common import env, load_config

OUT = pathlib.Path("/tmp/new_tokens")
JOBS = {
    "instagram": ("IG_ACCESS_TOKEN", "https://graph.instagram.com/refresh_access_token", "ig_refresh_token"),
    "threads": ("THREADS_ACCESS_TOKEN", "https://graph.threads.net/refresh_access_token", "th_refresh_token"),
}


def main():
    cfg = load_config()
    OUT.mkdir(parents=True, exist_ok=True)
    failed = False
    for name, (secret, url, grant) in JOBS.items():
        if not cfg.get("platforms", {}).get(name):
            continue
        r = requests.get(url, params={"grant_type": grant, "access_token": env(secret)}, timeout=30)
        if r.status_code >= 400 or "access_token" not in r.json():
            print(f"{name}: トークン延長に失敗しました（{r.status_code}）。手動で取り直してください。")
            failed = True
            continue
        token = r.json()["access_token"]
        print(f"::add-mask::{token}")
        (OUT / secret).write_text(token)
        print(f"{name}: トークンを延長しました")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
