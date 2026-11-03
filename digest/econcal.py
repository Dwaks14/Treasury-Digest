"""Economic calendar: central bank decisions and high-impact data releases.

Source: the public weekly calendar feed published by Forex Factory (no key needed).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

FEEDS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
         "https://nfs.faireconomy.media/ff_calendar_nextweek.json"]
DECISION_WORDS = ("rate decision", "funds rate", "refinancing rate", "deposit facility", "bank rate",
                  "policy rate", "monetary policy statement", "fomc statement", "loan prime rate", "cash rate")
CCY_NAMES = {"USD": "US", "EUR": "Euro area", "GBP": "UK", "CNY": "China", "JPY": "Japan"}


def fetch(cfg: dict, now: datetime) -> list[dict]:
    c = cfg.get("calendar") or {}
    if not c:
        return []
    tz = ZoneInfo(cfg["reader"].get("timezone", "UTC"))
    ccys, impacts = set(c.get("currencies", [])), set(c.get("impacts", ["High"]))
    start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=c.get("days_ahead", 7) + 1)

    raw = []
    for url in FEEDS:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (personal-treasury-digest)"})
            r.raise_for_status()
            raw += r.json()
        except Exception as exc:
            print(f"  ! calendar {url.rsplit('/', 1)[-1]}: {exc}")

    seen, events = set(), []
    for e in raw:
        title, ccy = e.get("title", ""), e.get("country", "")
        is_decision = any(w in title.lower() for w in DECISION_WORDS)
        if ccy not in ccys or not (e.get("impact") in impacts or is_decision):
            continue
        try:
            when = datetime.fromisoformat(e["date"]).astimezone(tz)
        except (KeyError, ValueError):
            continue
        if not (start <= when < end) or (title, e["date"]) in seen:
            continue
        seen.add((title, e["date"]))
        events.append({
            "when": when.isoformat(), "day": when.strftime("%a %d %b"),
            "time": when.strftime("%H:%M"), "today": when.date() == start.date(),
            "ccy": ccy, "region": CCY_NAMES.get(ccy, ccy), "title": title,
            "decision": is_decision, "forecast": e.get("forecast") or "", "previous": e.get("previous") or "",
        })
    events.sort(key=lambda x: x["when"])
    print(f"  ✓ calendar: {len(events)} events")
    return events


def as_text(events: list[dict]) -> str:
    lines = []
    for e in events:
        extra = ", ".join(x for x in (f"forecast {e['forecast']}" if e["forecast"] else "",
                                      f"previous {e['previous']}" if e["previous"] else "") if x)
        lines.append(f"{'TODAY ' if e['today'] else ''}{e['day']} {e['time']} Dublin | {e['region']} | "
                     f"{e['title']}{' (' + extra + ')' if extra else ''}")
    return "\n".join(lines)
