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

## v4.1 , login revert (fix LOGIN FAILED)
Date: 2026-06-29

The v4 login() rewrite (wait-for-field instead of fixed sleeps) caused
LOGIN FAILED on every run — filling the form too eagerly before aj_login()
finished setting it up. User confirmed they could log in fine in-browser, so
credentials were healthy; the regression was mine.
Fix: reverted login() to the EXACT working baseline (fixed 1200/600/3000ms
waits). All other v4 speed-ups (single /aj_neo navigation, smart waits in the
scrape loop) are kept — they're in scrape_model, not login, and don't affect
login success. Lesson: don't "optimize" the one function that was already
working without a live test.

Tokens seen returning 0 lots in the v3 run (fix individually if wanted):
ASX, MAZDA6, ATENZA — likely need the site's exact spelling. They don't break
anything; only their own line returns empty.

## v5 , European model names (fix "(maker)" + blank year)
Date: 2026-06-29

The whole-maker (FULL_MAKERS) boards group cars under a model header like
<font style="font-size:13px">AUDI A1</font> rather than putting the model on
each row — so European cars showed model "(maker)". (Year was actually fine;
the blank-year European rows came from the earlier cancelled/partial runs.)
Fix (scraper.py parse_board): walk the results container in document order,
track the latest model header, and tag each row with it (model_row). The
scrape loop already prefers model_row over the "(maker)" placeholder for
whole-maker scrapes. Verified on a real Audi board capture: rows now resolve
model "A1" with correct years (2021/2020/2018/2017) and prices. Per-model
Japanese scrapes are unaffected (they have no headers and the model is known).

## v5.1 , CORRECTED European model fix (v5 was wrong)
Date: 2026-06-29

v5 was tested against the WRONG board capture (a single-model A1 view), not
the real whole-maker view. On the real whole-maker board the group header is
literally "Any" (meaning all models) — so v5 produced model "Any" and blank
year for every German car. Both faults trace to ONE difference in the spec
cell (c3):
  per-model board   c3 = 'YEAR CHASSIS GRADE'            e.g. '2017 8XCHZ 1.0TFSI'
  whole-maker board c3 = 'MAKER MODEL YEAR CHASSIS GRADE' e.g. 'AUDI A1 2017 8XCHZ 1.0TFSI'
Real fix: _parse_spec_cell() detects a leading maker word, strips MAKER+MODEL
to get the model, and parses year/chassis/grade from the remainder. Year was
blank on Germans precisely because c3 started with "AUDI" not a digit.
Verified on the REAL whole-maker Audi board: model='A1', years 2017/2021/2012/
2020, chassis + prices all correct. Japanese boards (c3 starts with a digit)
parse exactly as before — confirmed unchanged on board_dump.html.
Lesson (again): verify against the actual layout, not a lookalike capture.

## v6 , price completion pass + CSV export
Date: 2026-06-30

Problem: sold prices live on each car's individual lot page, but the board
scrape only reads the board. Cars that drop off the board before their price
posts kept "ended, no price" forever — making CSV exports incomplete.

Scraper (scraper.py): added a second MODE.
- JCD_MODE=board (default) = the original live-board scrape, unchanged.
- JCD_MODE=prices = visits ended, price-less lot pages and captures the sold
  price (parse_lot_price), newest-ended first, skipping cars that ended <3h ago
  (price not settled). Capped at JCD_PRICE_BATCH (~180) per run with a polite
  ~0.9s gap, so the backlog (~6k at launch) drains over several runs, then
  just maintains. No age cutoff assumed — we have only 2 days of data and have
  NOT observed pages being removed. Instead, if a lot page genuinely returns
  "nothing found", it's marked price_gone so we don't refetch it — which lets
  the data tell us empirically if/when pages ever expire.
- Both modes share login + parsing. They run as SEPARATE scheduled jobs so the
  slower price pass can never blow the board scrape's timeout or risk live data.

Workflow (scrape.yml): board crons at 05:00 & 14:00 UTC; price crons at 09:00,
18:00, 23:00 UTC (offset, in the gaps, never concurrent with board). Manual
runs default to board; a dropdown lets you pick prices manually.

Dashboard (index.html): Export CSV button (top-right above the table). Exports
the currently filtered + sorted rows (all of them, not the 500 display cap),
with sold price in ¥ and $, chassis, grade, condition, auction, status, and
the lot URL. UTF-8 BOM so Japanese grades open correctly in Excel. CSV chosen
over xlsx as the workhorse for a large, growing dataset; filter first, export
the slice.

RISK NOTE: the price pass is the one thing that raises request volume. It is
gentle by design (cap + delays + newest-first), but the first runs work the
backlog — watch those logs for any throttling and lower JCD_PRICE_BATCH if so.
Revert point saved at jcd-baselines/v6-PRE-PRICEFETCH-EXPORT-20260630.
