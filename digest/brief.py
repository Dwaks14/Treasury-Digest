"""Write a briefing for an edition that GitHub Actions already collected.

Used by the scheduled Claude routine (no API key needed):

    python -m digest.brief show              # print instructions + numbered headlines for today
    python -m digest.brief publish FILE.md   # save the briefing, link citations, rebuild the site

Both accept --date YYYY-MM-DD (default: today in the configured timezone).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yaml

from . import market, writer
from .build import DATA, ROOT, render


def _load(date: str | None) -> tuple[dict, dict, str]:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    if not date:
        tz = ZoneInfo(cfg["reader"].get("timezone", "UTC"))
        date = datetime.now(timezone.utc).astimezone(tz).date().isoformat()
    path = DATA / f"{date}.json"
    if not path.exists():
        sys.exit(f"NO_EDITION: {path.name} not found (has the GitHub Action run yet?)")
    entry = json.loads(path.read_text())
    if not entry.get("items"):
        sys.exit(f"NO_ITEMS: {path.name} has no saved headlines")
    return cfg, entry, date


def show(date: str | None) -> None:
    cfg, e, date = _load(date)
    r = cfg["reader"]
    print(f"STATUS: {e['mode']}  (mode 'ai' means a briefing was already written)\n")
    print("=== INSTRUCTIONS ===")
    print(writer.SYSTEM.format(profile=r["profile"].strip(), avoid=", ".join(r.get("avoid", []))))
    print(f"=== DATE ===\n{date}\n")
    print(f"=== MARKET SNAPSHOT ===\n{market.as_text(e.get('market', [])) or '(unavailable)'}\n")
    print("=== HEADLINES ===")
    print(writer._items_text(e["items"]))


def publish(md_file: str, date: str | None) -> None:
    cfg, e, date = _load(date)
    md = open(md_file, encoding="utf-8").read().strip()
    if len(md) < 400 or "##" not in md:
        sys.exit("REJECTED: briefing looks too short or has no sections")
    e["markdown"] = writer.link_citations(md, e["items"])
    e["mode"] = "ai"
    e["briefed"] = datetime.now(timezone.utc).isoformat()
    (DATA / f"{date}.json").write_text(json.dumps(e, indent=1))
    render(cfg)
    print(f"PUBLISHED: docs/{date}/index.html")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["show", "publish"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--date")
    a = ap.parse_args()
    if a.cmd == "show":
        show(a.date)
    else:
        if not a.file:
            sys.exit("usage: python -m digest.brief publish FILE.md")
        publish(a.file, a.date)


if __name__ == "__main__":
    main()
