"""作業の進み具合を status/latest.json に書いて、GitHubに保存する（ダッシュボードとClaudeが読む）。

使い方: python src/status.py <state> <stage> [--final]
  state: running / success / failure / cancelled
  stage: 今どの段階か（例: 準備中, 動画を作成中）。"-" なら今の段階のまま
"""
import datetime
import json
import os
import subprocess
import sys

P = "status/latest.json"
STAGES = ["台本を受け取り", "準備中", "映像・声・動画を作成中", "確認待ちを作成中", "完成"]


def tail(path, n):
    if not os.path.exists(path):
        return ""
    return open(path, encoding="utf-8", errors="replace").read()[-n:]


def main():
    state, stage = sys.argv[1], sys.argv[2]
    final = "--final" in sys.argv
    now = (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
    os.makedirs("status", exist_ok=True)
    d = json.load(open(P, encoding="utf-8")) if os.path.exists(P) else {}
    if stage == STAGES[0]:
        d = {"started": now, "history": []}
    if stage == "-":  # 段階はそのまま（止まった場所を残す）
        stage = d.get("stage", STAGES[0])
    d.update({"state": state, "stage": stage, "stages": STAGES, "time": now, "run": os.environ.get("RUN_URL", "")})
    d.setdefault("history", []).append({"stage": stage, "state": state, "time": now})
    d["history"] = d["history"][-15:]
    if final:
        pr = subprocess.run(["gh", "pr", "list", "--state", "open", "--limit", "1", "--json", "url,headRefName",
                             "-q", ".[0] | [.url, .headRefName] | @tsv"], capture_output=True, text=True).stdout.strip()
        url, _, branch = pr.partition("\t")
        d.update({"pr": url, "branch": branch, "setup_log": tail("/tmp/setup.log", 800), "log": tail("/tmp/gen.log", 2500)})
    with open(P, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    subprocess.run(["git", "add", P])
    if subprocess.run(["git", "commit", "-q", "-m", f"状況: {stage} [skip ci]"]).returncode == 0:
        for _ in range(3):  # 同時に別の保存があっても、取り込んでから送り直す
            if subprocess.run(["git", "push", "-q", "origin", "HEAD:main"]).returncode == 0:
                break
            subprocess.run(["git", "pull", "-q", "--rebase", "--autostash", "origin", "main"])


if __name__ == "__main__":
    main()
