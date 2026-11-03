# Daily Treasury Brief

A personal morning news digest for international treasury, cash management and corporate strategy.
Every weekday at about 05:40 Dublin time, a GitHub Action:

1. pulls headlines from about 25 sources (central banks, WSJ/FT/CNBC, CFO Dive, Payments Dive, Finextra, plus targeted Google News searches),
2. pulls rates and FX from FRED (SOFR, Fed Funds, €STR, SONIA, UST 2Y/10Y, EUR, GBP, JPY, CNY, MXN, Brent),
3. ranks the headlines against your profile in `config.yaml`,
4. publishes a mobile-friendly page to GitHub Pages, with an archive.

**Two modes**
- **Headline mode (free):** with no API key, you get a ranked list of headlines grouped by section.
- **Written brief (~$3–6/month):** add an `ANTHROPIC_API_KEY` secret and Claude writes a 700–1,100 word briefing that cites its sources: a two-minute brief, rates/FX, treasury and payments, regulation, strategy and M&A, plus a closing "worth thinking about" paragraph.

## One-time setup

1. **Create the repo:** on GitHub, click New repository, name it `treasury-digest`, make it **Public** (free GitHub Pages needs a public repo; the pages are marked `noindex`), and upload these files.
2. **Turn on Pages:** go to Settings → Pages → Source: *Deploy from a branch* → Branch `main`, folder `/docs`.
3. **Run it once:** go to Actions → *Daily digest* → **Run workflow**. Your site will be at `https://<your-username>.github.io/treasury-digest/`.
4. **Optional, for the written brief:** create a key at console.anthropic.com, then go to Settings → Secrets and variables → Actions → New secret, name it `ANTHROPIC_API_KEY`, and paste the key.
5. **On your phone:** open the site in Safari, then Share → *Add to Home Screen*.

## Customising
Everything is in `config.yaml`:
- `reader.profile`: who you are and what you care about. The AI writes to this.
- `feeds`: any RSS feed. `google_news_queries`: add a search to follow a topic, company or bank.
- `market_series`: any FRED series ID.
- `ai.model`: `claude-opus-5-5` (default) or `claude-haiku-5-5` (much cheaper).

## Run locally
```
pip install -r requirements.txt
python -m digest.build        # writes docs/ and data/
```

## Roadmap
- Phase 2: a 20–30 minute two-host podcast. Claude writes a dialogue, text-to-speech voices it, and the episodes go out on a private podcast feed for Apple Podcasts or Overcast.
