import datetime as dt
import json
import os
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUEUE = ROOT / "queue"
PHOTOS = ROOT / "photos"
JST = dt.timezone(dt.timedelta(hours=9))
WEEKDAYS = "月火水木金土日"


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def today_jst():
    return dt.datetime.now(JST).date()


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def env(name, required=True):
    v = os.environ.get(name, "").strip()
    if required and not v:
        raise SystemExit(f"環境変数 {name} が設定されていません（GitHubのSecretsを確認してください）")
    return v


def list_photos():
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted(p for p in PHOTOS.iterdir() if p.suffix.lower() in exts)
