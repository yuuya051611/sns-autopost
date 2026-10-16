"""Pixabay（無料・商用利用OK・出典表示不要）から、場面に合う縦長の写真を取ってくる。"""
import hashlib
import os
import pathlib

import requests

CACHE = pathlib.Path(__file__).resolve().parent.parent / ".photo_cache"


def fetch(query, lang="ja"):
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key:
        return None
    CACHE.mkdir(exist_ok=True)
    path = CACHE / (hashlib.md5(query.encode()).hexdigest() + ".jpg")
    if path.exists():
        return path
    try:
        r = requests.get("https://pixabay.com/api/", timeout=30, params={
            "key": key, "q": query, "lang": lang, "image_type": "photo", "orientation": "vertical",
            "safesearch": "true", "per_page": 10, "order": "popular"})
        hits = r.json().get("hits", []) if r.ok else []
        if not hits and lang != "en":
            return None
        for h in hits:
            url = h.get("largeImageURL") or h.get("webformatURL")
            img = requests.get(url, timeout=60)
            if img.ok and len(img.content) > 20000:
                path.write_bytes(img.content)
                return path
    except requests.RequestException:
        pass
    return None


def fetch_video(query, lang="ja", max_sec=20):
    """Pixabayの無料動画（商用利用OK・出典表示不要）から、場面に合う短い動画を取ってくる。"""
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key or not query:
        return None
    CACHE.mkdir(exist_ok=True)
    path = CACHE / (hashlib.md5(("v:" + query).encode()).hexdigest() + ".mp4")
    if path.exists():
        return path
    try:
        r = requests.get("https://pixabay.com/api/videos/", timeout=30, params={
            "key": key, "q": query, "lang": lang, "safesearch": "true", "per_page": 10, "order": "popular"})
        for h in (r.json().get("hits", []) if r.ok else []):
            if h.get("duration", 99) > max_sec:
                continue
            v = h.get("videos", {})
            pick = v.get("medium") or v.get("small") or v.get("large")
            if pick and pick.get("url"):
                data = requests.get(pick["url"], timeout=120)
                if data.ok and len(data.content) > 50000:
                    path.write_bytes(data.content)
                    return path
    except requests.RequestException:
        pass
    return None
