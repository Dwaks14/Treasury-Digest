"""Key rates and FX from official, keyless sources: NY Fed, ECB, US Treasury, Bank of England.
FRED is supported too if a FRED_API_KEY secret is set (free at fred.stlouisfed.org)."""
from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime, timedelta
from functools import lru_cache

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; personal-treasury-digest/1.0)"}
TIMEOUT = 20

Series = list[tuple[str, float]]  # [(iso_date, value)], oldest first


def _get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


# ── New York Fed: SOFR, EFFR ────────────────────────────────────
def nyfed(rate: str) -> Series:
    data = _get(f"https://markets.newyorkfed.org/api/rates/{rate}/last/15.json").json()
    rows = [(r["effectiveDate"], float(r["percentRate"])) for r in data.get("refRates", [])]
    return sorted(rows)


# ── ECB data portal: €STR and euro reference FX rates ───────────
@lru_cache(maxsize=None)
def ecb(key: str) -> Series:
    start = (date.today() - timedelta(days=30)).isoformat()
    text = _get(f"https://data-api.ecb.europa.eu/service/data/{key}",
                params={"startPeriod": start, "format": "csvdata"}).text
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            rows.append((row["TIME_PERIOD"], float(row["OBS_VALUE"])))
        except (KeyError, ValueError):
            continue
    return sorted(rows)


def ecb_fx(num: str, den: str) -> Series:
    """Cross rate = (units of `num` per EUR) / (units of `den` per EUR); e.g. num=USD, den=EUR is EUR/USD."""
    def per_eur(ccy: str) -> dict[str, float]:
        return {} if ccy == "EUR" else dict(ecb(f"EXR/D.{ccy}.EUR.SP00.A"))
    a, b = per_eur(num), per_eur(den)
    days = sorted(set(a or b) & set(b or a))
    return [(d, round(a.get(d, 1.0) / b.get(d, 1.0), 4)) for d in days]


# ── US Treasury daily par yield curve ───────────────────────────
@lru_cache(maxsize=None)
def _ust_year(year: int) -> tuple:
    url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           f"daily-treasury-rates.csv/{year}/all")
    text = _get(url, params={"type": "daily_treasury_yield_curve",
                             "field_tdr_date_value": year, "page": "", "_format": "csv"}).text
    return tuple(csv.DictReader(io.StringIO(text)))


def ust(col: str) -> Series:
    today = date.today()
    rows = list(_ust_year(today.year))
    if today.month == 1:
        rows += list(_ust_year(today.year - 1))
    out = []
    for r in rows:
        try:
            d = datetime.strptime(r["Date"], "%m/%d/%Y").date().isoformat()
            out.append((d, float(r[col])))
        except (KeyError, ValueError):
            continue
    return sorted(out)


# ── Bank of England database: SONIA ─────────────────────────────
def boe(code: str) -> Series:
    start = (date.today() - timedelta(days=30)).strftime("%d/%b/%Y")
    text = _get("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp",
                params={"csv.x": "yes", "Datefrom": start, "Dateto": "now", "SeriesCodes": code,
                        "CSVF": "TN", "UsingCodes": "Y", "VPD": "Y", "VFD": "N"}).text
    out = []
    for row in csv.reader(io.StringIO(text)):
        try:
            out.append((datetime.strptime(row[0].strip(), "%d %b %Y").date().isoformat(), float(row[1])))
        except (IndexError, ValueError):
            continue
    return sorted(out)


# ── FRED (optional; needs FRED_API_KEY) ─────────────────────────
def fred(series_id: str) -> Series:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("skipped (no FRED_API_KEY)")
    start = (date.today() - timedelta(days=30)).isoformat()
    data = _get("https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": series_id, "api_key": key, "file_type": "json",
                        "observation_start": start}).json()
    out = []
    for o in data.get("observations", []):
        try:
            out.append((o["date"], float(o["value"])))
        except ValueError:
            continue
    return out


def _load(s: dict) -> Series:
    src = s["source"]
    if src == "nyfed":
        return nyfed(s["rate"])
    if src == "ecb":
        return ecb(s["key"])
    if src == "ecb_fx":
        return ecb_fx(s["num"], s["den"])
    if src == "ust":
        return ust(s["col"])
    if src == "boe":
        return boe(s["code"])
    if src == "fred":
        return fred(s["id"])
    raise ValueError(f"unknown source {src}")


def snapshot(cfg: dict) -> list[dict]:
    out = []
    for s in cfg.get("market_series", []):
        try:
            rows = _load(s)
        except Exception as exc:
            print(f"  ! {s['label']}: {exc}")
            continue
        if not rows:
            print(f"  ! {s['label']}: no data")
            continue
        as_of, last = rows[-1]
        prev = rows[-2][1] if len(rows) > 1 else None
        week_ago = (date.fromisoformat(as_of) - timedelta(days=7)).isoformat()
        week = next((v for d, v in reversed(rows) if d <= week_ago), None)
        is_rate = s.get("unit") == "%"

        def chg(ref):
            if ref is None:
                return None
            return round((last - ref) * 100) if is_rate else round((last / ref - 1) * 100, 2)

        out.append({"label": s["label"], "value": last, "as_of": as_of, "is_rate": is_rate,
                    "d1": chg(prev), "w1": chg(week)})  # rates: bp change; FX: % change
        print(f"  ✓ {s['label']}: {last} ({as_of})")
    return out


def as_text(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        unit = "bp" if r["is_rate"] else "%"
        lines.append(f"{r['label']}: {r['value']} (as of {r['as_of']}; 1d {r['d1']}{unit}, 1w {r['w1']}{unit})")
    return "\n".join(lines)
