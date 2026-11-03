"""Write a briefing for an edition that GitHub Actions already collected.

Used by the scheduled Claude routine (no API key needed):

    python -m digest.brief show              # print instructions + numbered headlines for today
    python -m digest.brief publish FILE.md   # save the briefing, link citations, rebuild the site
    python -m digest.brief podcast-show      # print podcast script instructions + today's material
    python -m digest.brief podcast-publish FILE.txt   # check and save the script (GitHub then voices it)

Both accept --date YYYY-MM-DD (default: today in the configured timezone).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yaml

import re

from . import econcal, market, podcast, writer
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
    print(f"STATUS: {e['mode']}  (mode 'ai' means a briefing was already written)\n")
    print("=== INSTRUCTIONS ===")
    print(writer.system_prompt(cfg))
    print("=== INPUT ===")
    print(writer.user_message(date, market.as_text(e.get("market", [])),
                              econcal.as_text(e.get("calendar", [])), e["items"]))


def publish(md_file: str, date: str | None) -> None:
    cfg, e, date = _load(date)
    md = open(md_file, encoding="utf-8").read().strip()
    if len(md) < 400 or "##" not in md:
        sys.exit("REJECTED: briefing looks too short or has no sections")
    words = len(md.split())
    if words > 800:
        sys.exit(f"REJECTED: briefing is {words} words; cut it to 650 or fewer")
    e["markdown"] = writer.link_citations(md, e["items"])
    e["mode"] = "ai"
    e["briefed"] = datetime.now(timezone.utc).isoformat()
    (DATA / f"{date}.json").write_text(json.dumps(e, indent=1))
    render(cfg)
    print(f"PUBLISHED: docs/{date}/index.html")


def podcast_show(date: str | None) -> None:
    cfg, e, date = _load(date)
    done = (podcast.PODDIR / f"{date}.txt").exists()
    print(f"PODCAST_STATUS: {'exists' if done else 'missing'}  (exists means today's script was already written)\n")
    print("=== INSTRUCTIONS ===")
    print(podcast.instructions(cfg))
    print("=== INPUT ===")
    if e.get("mode") == "ai":
        brief = re.sub(r"<span class='src'>.*?</span>", "", e["markdown"])
        print(f"TODAY'S WRITTEN BRIEFING (your backbone; go deeper using the stories below)\n{brief}\n")
    print(writer.user_message(date, market.as_text(e.get("market", [])),
                              econcal.as_text(e.get("calendar", [])), e["items"]))


def podcast_publish(txt_file: str, date: str | None) -> None:
    cfg, e, date = _load(date)
    text = open(txt_file, encoding="utf-8").read().strip()
    try:
        turns, words = podcast.validate(text)
    except ValueError as exc:
        sys.exit(f"REJECTED: {exc}")
    podcast.PODDIR.mkdir(parents=True, exist_ok=True)
    (podcast.PODDIR / f"{date}.txt").write_text("\n".join(f"{w}: {t}" for w, t in turns) + "\n", encoding="utf-8")
    print(f"SAVED: data/podcast/{date}.txt ({words} words, {len(turns)} turns, ~{round(words / 155)} min). "
          "Commit and push it; GitHub voices it automatically.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["show", "publish", "podcast-show", "podcast-publish"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--date")
    a = ap.parse_args()
    if a.cmd == "show":
        show(a.date)
    elif a.cmd == "podcast-show":
        podcast_show(a.date)
    elif a.cmd == "podcast-publish":
        if not a.file:
            sys.exit("usage: python -m digest.brief podcast-publish FILE.txt")
        podcast_publish(a.file, a.date)
    else:
        if not a.file:
            sys.exit("usage: python -m digest.brief publish FILE.md")
        publish(a.file, a.date)


if __name__ == "__main__":
    main()
