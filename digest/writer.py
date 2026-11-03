"""Turn ranked headlines into a written briefing with Claude (or a plain list without a key)."""
from __future__ import annotations

import os
import re

SYSTEM = """You are the editor of a private, one-reader morning briefing.
Write for this reader:

{profile}

Things they do NOT want: {avoid}.

You'll get today's market snapshot and a numbered list of candidate headlines
(title, source, short blurb). Pick what genuinely matters to this reader, skip
the rest, and write the briefing in Markdown using exactly these sections:

## The two-minute brief
4 to 6 bullets: the most important things today, each with a one-line "so what" for a corporate treasurer.

## Rates, FX & liquidity
What moved in the snapshot and why it matters (funding costs, hedging, cash investment yields). Mention the actual numbers. Short paragraphs or bullets.

## Treasury, cash & payments
## Banks, regulation & policy
## Strategy, M&A & the industrial world
For each of these three sections, cover 2 to 5 stories. Each is a bold one-line headline in your own words, then 1 to 3 sentences on what happened and why it matters to an international treasury team at a US manufacturer. Leave a section out if nothing qualifies.

## Worth thinking about
One short paragraph connecting dots across today's news: a strategic or FP&A angle, a question to raise with the team, or a risk to watch.

Rules:
- Cite every story with its number in square brackets, e.g. [12]. Cite only numbers from the list. Never write URLs.
- Use only facts present in the headlines, blurbs and snapshot. If a blurb is thin, say less rather than guess.
- Plain, confident, analyst-grade English. No hype, no filler, no emojis.
- Total length 700 to 1100 words.
"""


def _items_text(items: list[dict]) -> str:
    lines = []
    for n, it in enumerate(items, 1):
        blurb = f" | {it['summary']}" if it["summary"] else ""
        lines.append(f"[{n}] ({it['source']}) {it['title']}{blurb}")
    return "\n".join(lines)


def link_citations(md: str, items: list[dict]) -> str:
    """Replace [n] / [n, m] citations with real source links, so URLs can't be made up."""
    def repl(m):
        links = []
        for num in re.findall(r"\d+", m.group(0)):
            i = int(num) - 1
            if 0 <= i < len(items):
                links.append(f"[{items[i]['source']}]({items[i]['url']})")
        return f"<span class='src'>{' · '.join(links)}</span>" if links else ""
    return re.sub(r"\[(\d+(?:\s*,\s*\d+)*)\](?!\()", repl, md)


def with_claude(cfg: dict, items: list[dict], market_text: str, today: str) -> str | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic

    ai = cfg.get("ai", {})
    items = items[: ai.get("max_items_to_ai", 140)]
    reader = cfg["reader"]
    client = anthropic.Anthropic()
    try:
        with client.beta.messages.stream(
            model=ai.get("model", "claude-opus-5-5"),
            max_tokens=16000,
            system=SYSTEM.format(profile=reader["profile"].strip(), avoid=", ".join(reader.get("avoid", []))),
            messages=[{"role": "user", "content":
                       f"Date: {today}\n\nMARKET SNAPSHOT\n{market_text or '(unavailable)'}\n\n"
                       f"HEADLINES\n{_items_text(items)}"}],
            output_config={"effort": ai.get("effort", "medium")},
            # If the model declines, the API automatically retries on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.APIStatusError as exc:
        print(f"  ! Claude API error {exc.status_code}: {exc.message}. Falling back to headline list.")
        return None
    except anthropic.APIConnectionError as exc:
        print(f"  ! Could not reach Claude API: {exc}. Falling back to headline list.")
        return None

    if msg.stop_reason == "refusal":
        print("  ! Model declined; falling back to headline list.")
        return None
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    u = msg.usage
    print(f"  ✓ Claude ({msg.model}): {u.input_tokens} in / {u.output_tokens} out tokens")
    return link_citations(text, items) if text else None


def headline_list(items: list[dict]) -> str:
    """No-AI fallback: the top-scored headlines grouped by section."""
    names = {"treasury": "Treasury, cash & payments", "policy": "Banks, regulation & policy",
             "markets": "Markets", "strategy": "Strategy & M&A", "business": "Business & industry"}
    out = ["*Headline mode: add an `ANTHROPIC_API_KEY` to get a written briefing.*", ""]
    for sec, title in names.items():
        picks = [i for i in items if i["section"] == sec][:8]
        if not picks:
            continue
        out.append(f"## {title}")
        for i in picks:
            out.append(f"- **{i['title']}** <span class='src'>[{i['source']}]({i['url']})</span>")
        out.append("")
    return "\n".join(out)
