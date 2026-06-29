# JCD Auction Tracker , Build Log

A record of what was built and changed, so any version can be understood or reverted.

## How to revert if something breaks

Every scrape commits data to git, so the full history is always recoverable.
To roll back the code or data to an earlier working state:

1. On GitHub, open the file (e.g. `scraper.py` or `index.html`).
2. Click **History** (top right of the file view).
3. Find the commit from the working version you want.
4. Click it, then use **Browse files** / copy the old contents back in, or
   revert the commit.

The known-good reference points are tagged below. The safest baseline is
**v1 (18 models)** , the first fully working version.

---

## v1 , 18 models (BASELINE, fully working)
Date: 2026-06-29

- Scraper logs in remotely (confirmed working from GitHub servers).
- Tracks 18 priority import models across Toyota, Nissan, Mazda, Honda, Subaru.
- Twice-daily schedule (05:00 + 14:00 UTC = 08:00 + 17:00 Nairobi).
- Sanity guard: refuses to overwrite good data if a run returns too few cars.
- Captures the site's own USD/JPY rate each run.
- Dashboard: single page, filters (status/date/maker/model/year/mileage/price/
  condition/auction house), clickable rows, USD shown under yen.
- First real scrape: 5,025 cars. After Corolla Fielder fix: 7,185 cars.

## v2 , dashboard logic fixes
Date: 2026-06-29

- Status model simplified to **Live / Ended** (was Live/Sold/No-sale).
  Reason: the board can't reliably distinguish sold vs not-sold vs
  price-not-yet-scraped in real time, so we don't claim it. A captured price
  shows SOLD; no price shows neutral "ended". Verified on real data: live +
  ended = total, zero mislabels.
- Live/ended decision uses auction date AND time in JST (was date only), so an
  auction earlier the same day correctly counts as ended.
- Lot links always use the surviving `aj-{id}.htm` page (the `st-` results page
  is dead). Confirmed: aj- pages survive after auction.
- Date filter collapsed from six buttons to a compact dropdown.

## v3 , maker expansion + multi-select filters
Date: 2026-06-29

- Japanese makers expanded to ~92 priority models (added Mitsubishi + Suzuki
  makers, and many more Toyota/Nissan/Mazda/Honda/Subaru models).
- European makers (Audi, BMW, Mercedes Benz, Volkswagen, Volvo) scraped in FULL
  via FULL_MAKERS — their site ids are resolved at runtime (resolve_maker_ids),
  since we don't hardcode ids we don't know. If one doesn't resolve on the first
  run, adjust its spelling in FULL_MAKERS to match the site label.
- Workflow timeout raised 30 -> 60 min for the larger run.
- Dashboard: Maker and Model filters are now MULTI-SELECT checkbox panels with
  search + select-all/clear. Model list depends on selected makers. Pick any
  combination across makers (e.g. Pajero + Harrier).
- Verified on real data: counts reconcile, multi-select logic correct.

### Known first-run checks for v3
- Watch the run log: any model line showing 0 lots = token needs a small fix.
- Confirm FULL_MAKERS resolved (look for "could not resolve maker id" warnings).
- Run will be much longer than v1's 13 min; if it nears 60 min, trim models.

## v4 , scraper speed-up
Date: 2026-06-29

Problem: the expanded run (97 model/maker boards) exceeded the job time limit
and was cancelled mid-run (died at the 30-min default because the 60-min
timeout file hadn't been pushed). Nothing got committed.

Fixes (scraper.py , SAME request volume, no added block risk):
- Only navigate to /aj_neo ONCE per run; subsequent models reuse the loaded
  page and just call model_submit again (was a full page reload per model).
- Replace fixed sleeps with "wait until the results container is ready, then a
  short polite gap" — removes idle waiting, not politeness.
- login() now waits for the username field to exist before filling (more
  robust; also fixes the transient "field not found" timeout seen earlier).
- Per-model errors are caught so one bad model can't abort the whole run.
Estimated full run: ~28 min -> ~11 min (about 60% faster).

Also (.github/workflows/scrape.yml):
- timeout-minutes raised 30 -> 60 (MUST be pushed; the earlier cancel at
  exactly 30m means this file had not been committed).

Tokens seen returning 0 lots in the v3 run (fix individually if wanted):
ASX, MAZDA6, ATENZA — likely need the site's exact spelling. They don't break
anything; only their own line returns empty.
