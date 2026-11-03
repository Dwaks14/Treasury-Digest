"""Build today's digest: python -m digest.build"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader

from . import market, sources, writer

ROOT = Path(__file__).resolve().parent.parent
DOCS, DATA = ROOT / "docs", ROOT / "data"


def render_site(cfg: dict, jenv: Environment) -> None:
    entries = sorted((json.loads(p.read_text()) for p in DATA.glob("*.json")),
                     key=lambda e: e["date"], reverse=True)
    if not entries:
        return
    day_tpl = jenv.get_template("day.html")
    for i, e in enumerate(entries):
        nav = {"newer": entries[i - 1]["date"] if i > 0 else None,
               "older": entries[i + 1]["date"] if i + 1 < len(entries) else None}
        html = day_tpl.render(cfg=cfg, e=e, body=markdown.markdown(e["markdown"]), nav=nav, prefix="../")
        out = DOCS / e["date"] / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html)
    latest = entries[0]
    (DOCS / "index.html").write_text(day_tpl.render(
        cfg=cfg, e=latest, body=markdown.markdown(latest["markdown"]),
        nav={"newer": None, "older": entries[1]["date"] if len(entries) > 1 else None}, prefix=""))
    (DOCS / "archive.html").write_text(jenv.get_template("archive.html").render(cfg=cfg, entries=entries))
    (DOCS / ".nojekyll").write_text("")


def main() -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    tz = ZoneInfo(cfg["reader"].get("timezone", "UTC"))
    now = datetime.now(timezone.utc)
    today = now.astimezone(tz).date().isoformat()

    print("Fetching news…")
    items = sources.score(sources.collect(cfg, now), cfg)
    print(f"  {len(items)} unique recent headlines")

    print("Fetching market data…")
    snap = market.snapshot(cfg)

    out = DATA / f"{today}.json"
    existing = json.loads(out.read_text()) if out.exists() else {}
    if existing.get("mode") == "ai":
        # A written briefing already exists for today; never overwrite it with headlines.
        print("Today's written briefing already exists; keeping it.")
        render(cfg)
        return

    print("Writing briefing…")
    top = items[: cfg.get("ai", {}).get("max_items_to_ai", 140)]
    md = writer.with_claude(cfg, top, market.as_text(snap), today)
    mode = "ai" if md else "headlines"
    md = md or writer.headline_list(items)

    DATA.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "date": today, "generated": now.isoformat(), "mode": mode,
        "markdown": md, "market": snap, "n_items": len(items),
        # Saved so a briefing can be written later (see digest/brief.py).
        "items": [{k: i[k] for k in ("title", "url", "source", "section", "summary")} for i in top],
    }, indent=1))

    render(cfg)
    print(f"Done: docs/{today}/ ({mode} mode)")


def render(cfg: dict) -> None:
    jenv = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=False)
    render_site(cfg, jenv)


if __name__ == "__main__":
    main()
