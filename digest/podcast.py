"""Two-host podcast: script instructions, free text-to-speech (Kokoro, open source), and the podcast feed.

    python -m digest.podcast synth DATE      # data/podcast/DATE.txt -> build/DATE.mp3 + data/podcast/DATE.json
    python -m digest.podcast feed            # rebuild docs/feed.xml from data/podcast/*.json
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
PODDIR = ROOT / "data" / "podcast"
MODELS = ROOT / "models"
BUILD = ROOT / "build"

HOSTS = {  # speaker label -> (Kokoro voice, language)
    "EMMA": ("bf_emma", "en-gb"),
    "MICHAEL": ("am_michael", "en-us"),
}
SPEED = 1.05

SCRIPT_INSTRUCTIONS = """You are writing today's episode of a private two-host podcast that turns the
Daily Treasury Brief into a 20 to 25 minute conversation for one listener's walk to work.

The listener:
{profile}

Their watchlist: company {company}; peers {peers}; relationship banks {banks}.

Hosts:
- EMMA: lead host. British. Former corporate treasurer: cash, liquidity, banks, FX hedging.
- MICHAEL: co-host. American. Markets and strategy: rates, macro, M&A, FP&A angles.
They are well-informed colleagues talking to a peer. Warm, sharp, never cheesy.

Structure (in this order):
1. Open (Emma): good morning, the date, and a one-sentence preview of the three biggest things.
2. Watchlist: each material item, what happened and why it matters to Carrier's treasury team. If nothing material, say so in one line and move on.
3. Rates and FX: walk through the snapshot with the numbers, what moved and what it means for funding, hedging and cash yields, currency by currency where relevant (EUR, GBP, CNY, MXN, JPY).
4. The big stories: the 3 or 4 most important stories, discussed properly, with the "so what" for an international treasury team at a US manufacturer.
5. Treasury, payments and banking: quicker round of the rest that matters.
6. Strategy corner (Michael leads): an M&A, industry, HVAC or FP&A angle from today's news.
7. Calendar: today's events, then what's coming this week, and what to watch for.
8. Close: one question worth taking into the office today, then a short sign-off.

Format rules (the script is read by a speech engine):
- Every line starts with EMMA: or MICHAEL: followed by what they say. One turn per line. Nothing else:
  no headings, no stage directions, no markdown, no sound cues, no URLs, no citations, no story numbers.
- Write for the ear: short sentences, natural back-and-forth, turns of 1 to 5 sentences. Mix longer
  explanations with quick reactions, but no filler banter.
- Say numbers as people say them: "three point nine five percent", "up two basis points",
  "euro-dollar at one point one two", "sixty-five thousand jobs". Avoid symbols (% $ € £ &) and
  abbreviations a speech engine might garble: write "euro short-term rate" not €STR, "S-O-F-R"
  is fine as "SOFR", say "the E-C-B" or "the European Central Bank", "J-P Morgan", "B-N-P Paribas".
- Use only facts in the inputs. Clearly frame implications as analysis ("that could mean…",
  "worth checking whether…"). Never invent quotes, numbers or events. Each story once only.
- Length: 3,000 to 3,800 words in total.
"""


def instructions(cfg: dict) -> str:
    r, w = cfg["reader"], cfg.get("watchlist") or {}
    names = lambda g: ", ".join(x["name"] for x in w.get(g, [])) or "none"
    return SCRIPT_INSTRUCTIONS.format(profile=r["profile"].strip(), company=names("company"),
                                      peers=names("peers"), banks=names("banks"))


def parse_script(text: str) -> list[tuple[str, str]]:
    turns = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^\**\s*([A-Z]+)\s*\**\s*:\s*(.+)$", line)
        if not m or m.group(1) not in HOSTS:
            raise ValueError(f"line not in 'EMMA: …' / 'MICHAEL: …' form: {line[:80]}")
        turns.append((m.group(1), m.group(2).strip()))
    return turns


def validate(text: str) -> tuple[list[tuple[str, str]], int]:
    turns = parse_script(text)
    words = sum(len(t.split()) for _, t in turns)
    speakers = {s for s, _ in turns}
    if len(speakers) < 2:
        raise ValueError("script needs both EMMA and MICHAEL")
    if words < 2200:
        raise ValueError(f"script is {words} words; it needs 3,000 to 3,800")
    if words > 5000:
        raise ValueError(f"script is {words} words; cut it to 3,000 to 3,800")
    return turns, words


def _speakable(text: str) -> str:
    t = re.sub(r"[*_#`\[\]]", "", text)
    for a, b in (("%", " percent"), ("€", " euro "), ("£", " pounds "), ("&", " and "), ("—", ", "), ("–", " to ")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


def _chunks(text: str, limit: int = 280) -> list[str]:
    """Split a turn into sentence-sized chunks the speech model handles comfortably."""
    out, cur = [], ""
    for s in re.split(r"(?<=[.!?;])\s+", text):
        if len(cur) + len(s) + 1 <= limit:
            cur = f"{cur} {s}".strip()
        else:
            if cur:
                out.append(cur)
            while len(s) > limit:  # very long sentence: split at a comma or space
                cut = max(s.rfind(", ", 0, limit), s.rfind(" ", 0, limit))
                cut = cut if cut > 50 else limit
                out.append(s[:cut].strip(" ,"))
                s = s[cut:].strip(" ,")
            cur = s
    if cur:
        out.append(cur)
    return out


def synth(date: str) -> Path:
    import numpy as np
    import soundfile as sf
    from kokoro_onnx import Kokoro

    script = (PODDIR / f"{date}.txt").read_text(encoding="utf-8")
    turns, words = validate(script)
    k = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
    sr, parts, prev = 24000, [], None
    for n, (who, text) in enumerate(turns, 1):
        voice, lang = HOSTS[who]
        if prev is not None:
            parts.append(np.zeros(int(sr * (0.45 if who != prev else 0.25)), dtype=np.float32))
        for chunk in _chunks(_speakable(text)):
            audio, sr = k.create(chunk, voice=voice, speed=SPEED, lang=lang)
            parts += [audio.astype(np.float32), np.zeros(int(sr * 0.12), dtype=np.float32)]
        prev = who
        if n % 20 == 0:
            print(f"  {n}/{len(turns)} turns", flush=True)
    audio = np.concatenate([np.zeros(int(sr * 0.5), dtype=np.float32), *parts, np.zeros(int(sr), dtype=np.float32)])
    BUILD.mkdir(exist_ok=True)
    wav, mp3 = BUILD / f"{date}.wav", BUILD / f"{date}.mp3"
    sf.write(wav, audio, sr)
    title = f"Morning Brief, {datetime.fromisoformat(date):%a %d %b %Y}"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-ac", "1", "-b:a", "64k",
                    "-metadata", f"title={title}", "-metadata", "artist=Daily Treasury Brief", str(mp3)], check=True)
    wav.unlink()
    seconds = int(len(audio) / sr)
    repo = os.environ.get("GITHUB_REPOSITORY", "Dwaks14/Treasury-Digest")
    meta = {"date": date, "title": title, "words": words, "seconds": seconds, "bytes": mp3.stat().st_size,
            "url": f"https://github.com/{repo}/releases/download/ep-{date}/{date}.mp3",
            "published": datetime.now(timezone.utc).isoformat()}
    (PODDIR / f"{date}.json").write_text(json.dumps(meta, indent=1))
    print(f"  ✓ {mp3.name}: {seconds // 60}m{seconds % 60:02d}s, {meta['bytes'] // 1024} KB, {words} words")
    return mp3


def feed(cfg: dict, site_url: str) -> None:
    eps = sorted((json.loads(p.read_text()) for p in PODDIR.glob("*.json")), key=lambda e: e["date"], reverse=True)
    name = cfg["reader"]["name"]
    items = []
    for e in eps[:60]:
        dur = f"{e['seconds'] // 3600:02d}:{e['seconds'] % 3600 // 60:02d}:{e['seconds'] % 60:02d}"
        items.append(f"""  <item>
    <title>{escape(e['title'])}</title>
    <description>Your two-host treasury and markets briefing for {escape(e['date'])}.</description>
    <link>{site_url}{e['date']}/index.html</link>
    <guid isPermaLink="false">treasury-brief-{e['date']}</guid>
    <pubDate>{format_datetime(datetime.fromisoformat(e['published']))}</pubDate>
    <enclosure url="{escape(e['url'])}" length="{e['bytes']}" type="audio/mpeg"/>
    <itunes:duration>{dur}</itunes:duration>
    <itunes:explicit>false</itunes:explicit>
  </item>""")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>{escape(name)}</title>
  <link>{site_url}</link>
  <language>en</language>
  <description>A private daily treasury, markets and strategy briefing.</description>
  <itunes:author>{escape(name)}</itunes:author>
  <itunes:image href="{site_url}cover.png"/>
  <itunes:category text="Business"/>
  <itunes:explicit>false</itunes:explicit>
  <itunes:block>Yes</itunes:block>
{chr(10).join(items)}
</channel>
</rss>
"""
    (ROOT / "docs" / "feed.xml").write_text(xml, encoding="utf-8")
    print(f"  ✓ feed.xml: {len(items)} episodes")


def main() -> None:
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "synth" and len(sys.argv) > 2:
        synth(sys.argv[2])
    elif cmd == "feed":
        feed(cfg, cfg["reader"].get("site_url", "https://dwaks14.github.io/Treasury-Digest/"))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
