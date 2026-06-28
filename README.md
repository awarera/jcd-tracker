# JCD Auction Tracker

Private dataset + dashboard for Japan Car Direct auction listings.
Tracks chosen models (currently Mazda2 and CX-5), capturing full specs,
direct lot links, start/average prices, and the final sold (hammer) price
when it appears after a car's auction.

A sibling of the TAU tracker: Python scraper → JSON in repo → static
dashboard on GitHub Pages.

## How it works

- **scraper.py** logs into the auction site (credentials via env vars),
  walks every page of each tracked model's board, and records each car.
  Runs on a schedule via GitHub Actions and commits the data.
- **data/** holds the dataset:
  - `lots_state.json` — master record of every lot ever seen
  - `events.json` — append-only change log (new lot / sold price / off board)
  - `dashboard.json` — pre-computed view the dashboard reads
  - `snapshots/DATE.json` — daily frozen board
- **index.html** is the dashboard (GitHub Pages).

## What the price fields mean

- **Start ¥** — the auction's opening/reserve price. Fixed, known in advance.
- **Sold ¥** — the hammer price. Shows 0 until the car's auction completes,
  then the real figure (if the lot page is still reachable afterwards).
- **Avg ¥** — historical average for that model/year/condition. A benchmark,
  not this car's price.

There is no live bid that ticks up. The sold price is captured by re-reading
each lot after its auction time.

## What's tracked (and how to change it)

Open `scraper.py` and find the **WHAT TO TRACK** section near the top.
It lists priority import models grouped by maker. To change what's tracked:

- **Add a model:** copy a line, set the maker_id and model token, e.g.
  `{"maker_id": "1", "model": "PROBOX"},`
- **Remove a model:** delete its line.
- **maker_id reference:** 1=Toyota 2=Nissan 3=Mazda 4=Mitsubishi 5=Honda
  6=Suzuki 7=Subaru 8=Isuzu 9=Daihatsu 23=Lexus

The dashboard auto-discovers whatever makers/models are in the data, so you
never edit the dashboard, only this list.

**Token note:** the site uses its own spelling. If a model line returns 0 cars
in a run (check the run log), the token is slightly off — fix that one line
(e.g. current `MAZDA2` vs older `DEMIO` are separate entries; it may be
`LAND CRUISER PRADO` not `PRADO`). A wrong token only affects its own line.

### Scrape the whole site

To track **every** model on the site instead of the curated list, set:

```python
SCRAPE_ALL = True
```

This is heavy (tens of thousands of cars, ~50 makers), much slower, and more
likely to hit rate-limits. Use it deliberately. Leave it `False` for the
focused priority-model tracker (the default).

## Setup

1. Create a new GitHub repo and upload these files.
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret**
   - `JCD_USERNAME` = your auction login username
   - `JCD_PASSWORD` = your auction login password
3. Enable **Settings → Pages → Source: Deploy from branch → main / root**.
   The dashboard will be at `https://<you>.github.io/<repo>/`.
4. The scraper runs automatically (twice daily) and can be triggered manually
   from the **Actions** tab → JCD Auction Scraper → Run workflow.

## Run locally

```
pip install playwright beautifulsoup4 lxml
python -m playwright install chromium
export JCD_USERNAME="..."
export JCD_PASSWORD="..."
python scraper.py
```
Then open `index.html` (serve the folder, e.g. `python -m http.server`).

## Known unknowns (being validated)

- Whether a lot page stays reachable after its auction (decides if hammer
  prices can be captured).
- Whether logging in from GitHub's servers (not Nairobi) is accepted by the
  site. First automated run will tell.
