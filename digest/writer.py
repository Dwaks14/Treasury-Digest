"""Turn ranked headlines into a written briefing with Claude (or a plain list without a key)."""
from __future__ import annotations

import os
import re

SYSTEM = """You are the editor of a private, one-reader morning briefing, read on a phone on the walk to work.
Write for this reader:

{profile}

Their watchlist:
- Their company: {company}
- Peers: {peers}
- Their relationship banks: {banks}

Leave out: {avoid}.

Inputs: a market snapshot, an economic calendar (Dublin times), and numbered stories from trusted
outlets. A story tagged WATCH: names a watchlist company or bank. "also:" lists other outlets that
ran the same story. Different outlets often cover one event under different headlines. Treat those
as ONE story.

Write Markdown with exactly these sections, in this order. Target 450 to 650 words in total and never more than 700:

## Watchlist
Only material news about the watchlist: results, guidance, ratings, M&A, leadership, big contracts or
plants, litigation or fines; for banks also strategy, market exits, and transaction-banking or treasury-services
moves. Ignore stories where a bank is only quoted or forecasting (e.g. "JPMorgan analysts expect…").
One bullet per item: **Name:** what happened, then why it matters to the reader. If nothing qualifies,
write the single line: Nothing material today.

## The brief
The 4 most important other stories. One bullet each: a bold lead, then one sentence of "so what" for an
international treasury team at a US manufacturer.

## Rates & FX
2 to 4 bullets: what moved in the snapshot that matters (quote the numbers), and any high-impact calendar
event today and what it could mean for funding, hedging or cash yields.

## Also worth knowing
Up to 4 one-line bullets covering treasury and payments, regulation, or strategy, M&A and industry.

## Worth thinking about
Two or three sentences: one strategic or FP&A angle, or a question worth raising with the team.

Rules:
- Each event appears once in the whole briefing. Never repeat a story across sections.
- Cite every item with its story number(s), e.g. [12]. At most 2 citations per item; prefer the most
  authoritative outlet. Cite only numbers from the list. Never write URLs.
- Use only facts in the inputs. If a story is thin, say less rather than guess.
- Crisp, plain English. Short sentences. No filler, hype or emojis.
"""


def system_prompt(cfg: dict) -> str:
    r, w = cfg["reader"], cfg.get("watchlist") or {}
    names = lambda group: ", ".join(x["name"] for x in w.get(group, [])) or "none"
    return SYSTEM.format(profile=r["profile"].strip(), avoid=", ".join(r.get("avoid", [])),
                         company=names("company"), peers=names("peers"), banks=names("banks"))


def _items_text(items: list[dict]) -> str:
    lines = []
    for n, it in enumerate(items, 1):
        watch = f" WATCH: {', '.join(it['watch'])} |" if it.get("watch") else ""
        also = f" | also: {', '.join(it['also'][:4])}" if it.get("also") else ""
        blurb = f" | {it['summary']}" if it.get("summary") else ""
        lines.append(f"[{n}]{watch} ({it['source']}) {it['title']}{blurb}{also}")
    return "\n".join(lines)


def user_message(today: str, market_text: str, calendar_text: str, items: list[dict]) -> str:
    return (f"Date: {today}\n\nMARKET SNAPSHOT\n{market_text or '(unavailable)'}\n\n"
            f"CALENDAR\n{calendar_text or '(unavailable)'}\n\nSTORIES\n{_items_text(items)}")


def link_citations(md: str, items: list[dict]) -> str:
    """Replace [n] / [n, m] citations with real source links, so URLs can't be made up."""
    def repl(m):
        links, used = [], set()
        for num in re.findall(r"\d+", m.group(0)):
            i = int(num) - 1
            if 0 <= i < len(items) and items[i]["source"] not in used and len(links) < 2:
                used.add(items[i]["source"])
                links.append(f"[{items[i]['source']}]({items[i]['url']})")
        return f"<span class='src'>{' · '.join(links)}</span>" if links else ""
    return re.sub(r"\[(\d+(?:\s*,\s*\d+)*)\](?!\()", repl, md)


def with_claude(cfg: dict, items: list[dict], market_text: str, calendar_text: str, today: str) -> str | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic

    ai = cfg.get("ai", {})
    client = anthropic.Anthropic()
    try:
        with client.beta.messages.stream(
            model=ai.get("model", "claude-opus-5-5"),
            max_tokens=16000,
            system=system_prompt(cfg),
            messages=[{"role": "user", "content": user_message(today, market_text, calendar_text, items)}],
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
    """No-AI fallback: the top-scored headlines grouped by section, watchlist first."""
    names = {"treasury": "Treasury, cash & payments", "policy": "Banks, regulation & policy",
             "markets": "Markets", "strategy": "Strategy & M&A", "business": "Business & industry"}
    out = ["*Headline mode: today's written briefing isn't ready yet.*", ""]
    watch = [i for i in items if i.get("watch")][:10]
    if watch:
        out.append("## Watchlist")
        for i in watch:
            out.append(f"- **{', '.join(i['watch'])}:** {i['title']} <span class='src'>[{i['source']}]({i['url']})</span>")
        out.append("")
    shown = {id(i) for i in watch}
    for sec, title in names.items():
        picks = [i for i in items if i["section"] == sec and id(i) not in shown][:6]
        if not picks:
            continue
        out.append(f"## {title}")
        for i in picks:
            out.append(f"- **{i['title']}** <span class='src'>[{i['source']}]({i['url']})</span>")
        out.append("")
    return "\n".join(out)
