"""Latest values for key rates and FX from FRED (public CSV endpoint, no API key)."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

import requests

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={start}"


def _series(series_id: str) -> list[tuple[str, float]]:
    start = (date.today() - timedelta(days=45)).isoformat()
    resp = requests.get(FRED_CSV.format(id=series_id, start=start), timeout=20,
                        headers={"User-Agent": "personal-treasury-digest/1.0"})
    resp.raise_for_status()
    rows = []
    for row in csv.reader(io.StringIO(resp.text)):
        if len(row) < 2 or row[0].lower().startswith(("date", "observation")):
            continue
        try:
            rows.append((row[0], float(row[1])))
        except ValueError:  # FRED uses "." for missing days
            continue
    return rows


def snapshot(cfg: dict) -> list[dict]:
    out = []
    for s in cfg.get("market_series", []):
        try:
            rows = _series(s["id"])
        except Exception as exc:
            print(f"  ! FRED {s['id']}: {exc}")
            continue
        if not rows:
            continue
        as_of, last = rows[-1]
        prev = rows[-2][1] if len(rows) > 1 else None
        week = next((v for d, v in reversed(rows) if d <= (date.fromisoformat(as_of) - timedelta(days=7)).isoformat()), None)
        is_rate = s.get("unit") == "%"
        def chg(ref):
            if ref is None:
                return None
            return round((last - ref) * 100) if is_rate else round((last / ref - 1) * 100, 2)
        out.append({
            "label": s["label"], "value": last, "as_of": as_of, "is_rate": is_rate,
            "d1": chg(prev), "w1": chg(week),  # rates: bp change; FX/commodities: % change
        })
    return out


def as_text(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        unit = "bp" if r["is_rate"] else "%"
        lines.append(f"{r['label']}: {r['value']} (as of {r['as_of']}; 1d {r['d1']}{unit}, 1w {r['w1']}{unit})")
    return "\n".join(lines)
