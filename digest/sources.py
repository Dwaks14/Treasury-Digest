"""Fetch headlines, keep trusted sources only, tag watchlist names, merge duplicates, and rank."""
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
STOP = set("""a an the and or of to in on for at by with from as is are was were be been it its this that
these those after before over under into amid new says say said will would could may might can than more
most up down out about not no but how why what who when where which while year years week weeks day days
inc corp co ltd plc group update analysis exclusive report reports""".split())


def _clean(text: str, limit: int = 400) -> str:
    text = html.unescape(TAG_RE.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


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
        if src.get("google"):
            # Google News titles look like "Headline - Publisher"
            pub = (e.get("source") or {}).get("title") if isinstance(e.get("source"), dict) else None
            if " - " in title:
                title, tail = title.rsplit(" - ", 1)
                source = pub or tail
            elif pub:
                source = pub
        items.append({
            "title": title.strip(),
            "url": e.get("link", ""),
            "source": source.strip(),
            "section": src.get("section", "general"),
            "summary": "" if src.get("google") else _clean(e.get("summary", "")),
            "published": _entry_time(e),
            "google": bool(src.get("google")),
            "query_watch": src.get("watch"),
        })
    print(f"  ✓ {src['name']}: {len(items)}")
    return items


def google_news_url(query: str) -> str:
    return (f"https://news.google.com/rss/search?q={quote_plus(query + ' when:2d')}"
            "&hl=en-US&gl=US&ceid=US:en")


def watch_entries(cfg: dict) -> list[dict]:
    out = []
    for group, entries in (cfg.get("watchlist") or {}).items():
        for w in entries:
            out.append({**w, "group": group})
    return out


# ── trust ──────────────────────────────────────────────────────
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9&']+", " ", s.lower()).strip()


def trust_tier(source: str, cfg: dict) -> int | None:
    """1 = top tier, 2 = trusted, 3 = wire (watchlist only), None = untrusted."""
    t = cfg.get("trusted_publishers", {})
    s = _norm(source)
    for tier, key in ((1, "tier1"), (2, "tier2"), (3, "watchlist_only")):
        for name in t.get(key, []):
            n = _norm(name)
            if s in (n, n + " com", n + " co uk", "the " + n):
                return tier
    return None


# ── duplicates ─────────────────────────────────────────────────
def _tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9$€£%.]+", title.lower()) if len(w) > 2 and w not in STOP}


def _same_story(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    inter = len(a & b)
    return inter >= 3 and (inter / min(len(a), len(b)) >= 0.6 or inter / len(a | b) >= 0.4)


def dedupe(items: list[dict]) -> list[dict]:
    """Merge near-identical headlines. The best-ranked copy is kept; others are noted as `also`."""
    kept: list[dict] = []
    for it in sorted(items, key=lambda i: (i["tier"], i["google"])):
        toks = _tokens(it["title"])
        for k in kept:
            if _same_story(toks, k["_tok"]):
                if it["source"] != k["source"] and it["source"] not in k["also"]:
                    k["also"].append(it["source"])
                k["watch"] = sorted(set(k["watch"]) | set(it["watch"]))
                break
        else:
            it["_tok"], it["also"] = toks, []
            kept.append(it)
    for k in kept:
        del k["_tok"]
    return kept


# ── main entry ─────────────────────────────────────────────────
def collect(cfg: dict, now: datetime) -> list[dict]:
    sources = [{**f} for f in cfg.get("feeds", [])]
    for g in cfg.get("google_news_queries", []):
        sources.append({"name": f"Google News: {g['q'][:30]}", "url": google_news_url(g["q"]),
                        "section": g.get("section", "general"), "google": True})
    for w in watch_entries(cfg):
        sources.append({"name": f"Watchlist: {w['name']}", "url": google_news_url(w["query"]),
                        "section": "watchlist", "google": True, "watch": w["name"]})

    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = list(pool.map(_fetch_one, sources))

    hours = cfg.get("lookback_hours", 30)
    if now.weekday() == 0:  # Monday: cover the weekend
        hours = max(hours, 72)
    cutoff = now - timedelta(hours=hours)

    watch = [(w["name"], [re.compile(p) for p in w.get("match", [])]) for w in watch_entries(cfg)]
    items, dropped = [], 0
    for it in (i for b in batches for i in b):
        if not it["title"] or not it["url"]:
            continue
        if it["published"] and it["published"] < cutoff:
            continue
        # Watchlist tagging: strict patterns anywhere; a looser name check for that name's own search.
        tags = {name for name, pats in watch if any(p.search(it["title"]) for p in pats)}
        qw = it.pop("query_watch")
        if qw and re.search(rf"\b{re.escape(qw.split()[0])}", it["title"], re.I):
            tags.add(qw)
        it["watch"] = sorted(tags)
        # Trust: direct feeds are trusted; Google News results must come from a listed publisher.
        tier = trust_tier(it["source"], cfg) if it["google"] else 1
        if tier is None or (tier == 3 and not it["watch"]):
            dropped += 1
            continue
        it["tier"] = tier
        if it["section"] == "watchlist" and not it["watch"]:
            it["section"] = "business"
        items.append(it)
    print(f"  dropped {dropped} items from untrusted sources")
    before = len(items)
    items = dedupe(items)
    print(f"  merged {before - len(items)} duplicate headlines")
    return items


def score(items: list[dict], cfg: dict) -> list[dict]:
    """Relevance score used to pick what the writer sees and for the headline-only fallback."""
    kw = cfg["reader"].get("keywords", {})
    high = [k.lower() for k in kw.get("high", [])]
    med = [k.lower() for k in kw.get("medium", [])]
    section_bonus = {"treasury": 3, "policy": 2, "markets": 1.5, "strategy": 1.5, "business": 1}
    for it in items:
        text = f" {it['title']} {it['summary']} ".lower()
        s = sum(3 for k in high if k in text) + sum(1 for k in med if k in text)
        s += section_bonus.get(it["section"], 0)
        s += 12 if it["watch"] else 0
        s += {1: 2, 2: 1}.get(it["tier"], 0)
        s += min(len(it["also"]), 4)  # widely covered = likely important
        it["score"] = s
    return sorted(items, key=lambda i: i["score"], reverse=True)
