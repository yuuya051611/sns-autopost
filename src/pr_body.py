"""確認待ち（プルリクエスト）の本文を作る。画像と文章を1日ずつ並べ、スマホでそのまま確認できるようにする。

使い方: python src/pr_body.py <ブランチ名> <日付JSONのパス...>
"""
import datetime as dt
import os
import sys

from common import WEEKDAYS, read_json


def main():
    branch, files = sys.argv[1], sorted(sys.argv[2:])
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    out = [
        "## 来週の投稿の確認をお願いします",
        "",
        "問題なければ、いちばん下の **Merge pull request** → **Confirm merge** を押してください。押した分だけ毎日12時に投稿されます。",
        "直したい投稿があれば、Claudeに「◯曜日の投稿をこう直して」と伝えてください。",
        "",
    ]
    for f in files:
        item = read_json(f)
        d = dt.date.fromisoformat(item["date"])
        base = f"https://raw.githubusercontent.com/{repo}/{branch}/"
        is_reel = item.get("type") == "reel"
        out += [
            "---",
            f"### {d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）{'🎬 リール' if is_reel else '🖼 画像'}｜{item['headline']}",
            "",
            f'<img src="{base + item["image"]}" width="300">',
            "",
        ]
        if is_reel:
            out += [f"[▶ 動画を再生して確認する]({base + item['video']})", ""]
        out += [
            item["caption"].replace("\n", "  \n"),
            "",
        ]
    print("\n".join(out))


if __name__ == "__main__":
    main()
