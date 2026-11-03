"""Fetch headlines from RSS feeds and Google News searches, then dedupe and score them."""
from __future__ import annotations

import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import requests

UA = "Mozilla/5.0 (compatible; personal-treasury-digest/1.0)"
TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str, limit: int = 400) -> str:
    text = html.unescape(TAG_RE.sub(" ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _entry_time(e) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:
            return datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
    return None


def _fetch_one(src: dict) -> list[dict]:
    try:
        resp = requests.get(src["url"], headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as exc:  # one bad feed should never break the digest
        print(f"  ! {src['name']}: {exc}")
        return []
    items = []
    for e in parsed.entries[:60]:
        title = _clean(e.get("title", ""), 250)
        source = src["name"]
        # Google News titles look like "Headline - Publisher"
        if src.get("google") and " - " in title:
            title, source = title.rsplit(" - ", 1)
        items.append({
            "title": title,
            "url": e.get("link", ""),
            "source": source,
            "section": src.get("section", "general"),
            "summary": _clean(e.get("summary", "")) if not src.get("google") else "",
            "published": _entry_time(e),
        })
    print(f"  ✓ {src['name']}: {len(items)}")
    return items


def google_news_url(query: str) -> str:
    return (f"https://news.google.com/rss/search?q={quote_plus(query + ' when:2d')}"
            "&hl=en-US&gl=US&ceid=US:en")


def collect(cfg: dict, now: datetime) -> list[dict]:
    sources = list(cfg.get("feeds", []))
    for g in cfg.get("google_news_queries", []):
        sources.append({"name": f"Google News: {g['q'][:30]}", "url": google_news_url(g["q"]),
                        "section": g.get("section", "general"), "google": True})

    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = list(pool.map(_fetch_one, sources))

    hours = cfg.get("lookback_hours", 30)
    if now.weekday() == 0:  # Monday: cover the weekend
        hours = max(hours, 72)
    cutoff = now - timedelta(hours=hours)

    seen, items = set(), []
    for item in (i for b in batches for i in b):
        if not item["title"] or not item["url"]:
            continue
        if item["published"] and item["published"] < cutoff:
            continue
        key = re.sub(r"[^a-z0-9]", "", item["title"].lower())[:70]
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    return items


def score(items: list[dict], cfg: dict) -> list[dict]:
    """Simple keyword relevance score, used to pre-filter and as the no-AI fallback."""
    kw = cfg["reader"].get("keywords", {})
    high = [k.lower() for k in kw.get("high", [])]
    med = [k.lower() for k in kw.get("medium", [])]
    section_bonus = {"treasury": 3, "policy": 2, "markets": 1.5, "strategy": 1.5, "business": 1}
    for it in items:
        text = f" {it['title']} {it['summary']} ".lower()
        s = sum(3 for k in high if k in text) + sum(1 for k in med if k in text)
        s += section_bonus.get(it["section"], 0)
        it["score"] = s
    return sorted(items, key=lambda i: i["score"], reverse=True)
