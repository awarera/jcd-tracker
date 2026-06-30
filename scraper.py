#!/usr/bin/env python3
"""
Japan Car Direct - Auction Spec & Price Tracker
================================================
Builds a private dataset of every car (for chosen models) that appears on the
live auction board, with full specs + the direct lot link. Runs daily and
accumulates. After a lot's auction time passes, re-checks it to try to capture
the final sold (hammer) price - if the lot page is still alive.

Outputs (in ./data):
  lots_state.json  - every lot ever seen, with latest known values + status
  events.json      - append-only log of changes (new lot, sold price captured, gone)
  snapshots/DATE.json - daily snapshot of the live board

Run:
  export JCD_USERNAME="..."   export JCD_PASSWORD="..."
  python3 tracker.py
"""
import json, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE = "https://auc.japancardirect.com"
DATA_DIR = Path(__file__).parent / "data"
SNAP_DIR = DATA_DIR / "snapshots"
STATE_FILE = DATA_DIR / "lots_state.json"
EVENTS_FILE = DATA_DIR / "events.json"

USER = os.environ.get("JCD_USERNAME", "")
PW   = os.environ.get("JCD_PASSWORD", "")

# ----------------------------------------------------------------------------
#  MODE: "board" (default) = scrape the live auction board (the original job).
#        "prices" = visit individual lot pages of ended cars that have no sold
#                   price yet, and capture the price. Runs on its own schedule.
#  Set via env JCD_MODE. Both modes share login + parsing; they're isolated as
#  separate scheduled runs so the slower price pass can't affect the board run.
# ----------------------------------------------------------------------------
MODE = os.environ.get("JCD_MODE", "board").strip().lower()

# Price pass tuning (gentle by design — drains the backlog over several runs):
PRICE_BATCH = int(os.environ.get("JCD_PRICE_BATCH", "600"))  # max lot pages per run
PRICE_MIN_AGE_H = 3      # don't chase cars that ended <3h ago (price not settled)
PRICE_GAP_MS = 600       # polite delay between lot-page fetches

# Models to track: (maker_id, model_name_token). maker_id 3 = MAZDA.
# model_submit(maker_id, model_name, 1) drives the board.
# ============================================================================
#  WHAT TO TRACK  —  edit this section anytime, nothing else needs to change
# ============================================================================
#
#  maker_id reference:
#    1=TOYOTA  2=NISSAN  3=MAZDA  4=MITSUBISHI  5=HONDA
#    6=SUZUKI  7=SUBARU  8=ISUZU  9=DAIHATSU   23=LEXUS
#
#  Each line = one model board to scrape. To add a model, copy a line and
#  change the maker_id + model token. To remove one, delete its line.
#
#  NOTE on tokens: the site uses its own spelling. If a line returns 0 cars
#  on a run, the token is slightly off (e.g. "MAZDA2" vs older "DEMIO", or
#  "PRADO" vs "LAND CRUISER PRADO"). Just fix that one line. A wrong token
#  only affects its own line; everything else still scrapes fine.
#
#  Current shape and older shape can be SEPARATE entries on the site
#  (e.g. MAZDA2 is the current car, DEMIO is the older body — both kept here).
# ----------------------------------------------------------------------------

MODELS = [
    # ---- TOYOTA (maker_id 1) ----
    {"maker_id": "1", "model": "AQUA"},
    {"maker_id": "1", "model": "COROLLA FIELDER"},
    {"maker_id": "1", "model": "VITZ"},
    {"maker_id": "1", "model": "HARRIER"},
    {"maker_id": "1", "model": "LAND CRUISER PRADO"},
    {"maker_id": "1", "model": "LAND CRUISER"},
    {"maker_id": "1", "model": "PROBOX"},
    {"maker_id": "1", "model": "SUCCEED"},
    {"maker_id": "1", "model": "PASSO"},
    {"maker_id": "1", "model": "PREMIO"},
    {"maker_id": "1", "model": "ALLION"},
    {"maker_id": "1", "model": "WISH"},
    {"maker_id": "1", "model": "VOXY"},
    {"maker_id": "1", "model": "NOAH"},
    {"maker_id": "1", "model": "SIENTA"},
    {"maker_id": "1", "model": "HIACE"},
    {"maker_id": "1", "model": "RAV4"},
    {"maker_id": "1", "model": "C-HR"},
    {"maker_id": "1", "model": "HILUX"},
    {"maker_id": "1", "model": "CROWN"},
    {"maker_id": "1", "model": "ALPHARD"},
    {"maker_id": "1", "model": "VANGUARD"},
    {"maker_id": "1", "model": "RACTIS"},
    {"maker_id": "1", "model": "BELTA"},
    # ---- NISSAN (maker_id 2) ----
    {"maker_id": "2", "model": "NOTE"},
    {"maker_id": "2", "model": "X-TRAIL"},
    {"maker_id": "2", "model": "SERENA"},
    {"maker_id": "2", "model": "DAYZ"},
    {"maker_id": "2", "model": "MARCH"},
    {"maker_id": "2", "model": "TIIDA"},
    {"maker_id": "2", "model": "WINGROAD"},
    {"maker_id": "2", "model": "AD"},
    {"maker_id": "2", "model": "JUKE"},
    {"maker_id": "2", "model": "DUALIS"},
    {"maker_id": "2", "model": "SYLPHY"},
    {"maker_id": "2", "model": "TEANA"},
    {"maker_id": "2", "model": "MURANO"},
    {"maker_id": "2", "model": "ELGRAND"},
    {"maker_id": "2", "model": "CARAVAN"},
    {"maker_id": "2", "model": "KICKS"},
    # ---- MAZDA (maker_id 3) ----
    {"maker_id": "3", "model": "MAZDA2"},
    {"maker_id": "3", "model": "DEMIO"},
    {"maker_id": "3", "model": "CX-5"},
    {"maker_id": "3", "model": "CX-3"},
    {"maker_id": "3", "model": "CX-8"},
    {"maker_id": "3", "model": "CX-30"},
    {"maker_id": "3", "model": "AXELA"},
    {"maker_id": "3", "model": "ATENZA"},
    {"maker_id": "3", "model": "PREMACY"},
    {"maker_id": "3", "model": "MAZDA3"},
    {"maker_id": "3", "model": "MAZDA6"},
    {"maker_id": "3", "model": "BONGO"},
    # ---- MITSUBISHI (maker_id 4) ----
    {"maker_id": "4", "model": "OUTLANDER"},
    {"maker_id": "4", "model": "RVR"},
    {"maker_id": "4", "model": "DELICA"},
    {"maker_id": "4", "model": "PAJERO"},
    {"maker_id": "4", "model": "ASX"},
    {"maker_id": "4", "model": "ECLIPSE CROSS"},
    {"maker_id": "4", "model": "MIRAGE"},
    {"maker_id": "4", "model": "LANCER"},
    # ---- HONDA (maker_id 5) ----
    {"maker_id": "5", "model": "FIT"},
    {"maker_id": "5", "model": "VEZEL"},
    {"maker_id": "5", "model": "FREED"},
    {"maker_id": "5", "model": "N BOX"},
    {"maker_id": "5", "model": "FIT SHUTTLE"},
    {"maker_id": "5", "model": "SHUTTLE"},
    {"maker_id": "5", "model": "GRACE"},
    {"maker_id": "5", "model": "INSIGHT"},
    {"maker_id": "5", "model": "CR-V"},
    {"maker_id": "5", "model": "STEPWGN"},
    {"maker_id": "5", "model": "ODYSSEY"},
    {"maker_id": "5", "model": "CIVIC"},
    {"maker_id": "5", "model": "ACCORD"},
    {"maker_id": "5", "model": "STREAM"},
    {"maker_id": "5", "model": "AIRWAVE"},
    {"maker_id": "5", "model": "VAMOS"},
    # ---- SUZUKI (maker_id 6) ----
    {"maker_id": "6", "model": "SWIFT"},
    {"maker_id": "6", "model": "SOLIO"},
    {"maker_id": "6", "model": "WAGON R"},
    {"maker_id": "6", "model": "HUSTLER"},
    {"maker_id": "6", "model": "SPACIA"},
    {"maker_id": "6", "model": "JIMNY"},
    {"maker_id": "6", "model": "ESCUDO"},
    {"maker_id": "6", "model": "BALENO"},
    {"maker_id": "6", "model": "SX4"},
    # ---- SUBARU (maker_id 7) ----
    {"maker_id": "7", "model": "FORESTER"},
    {"maker_id": "7", "model": "IMPREZA"},
    {"maker_id": "7", "model": "XV"},
    {"maker_id": "7", "model": "LEGACY"},
    {"maker_id": "7", "model": "OUTBACK"},
    {"maker_id": "7", "model": "LEVORG"},
    {"maker_id": "7", "model": "EXIGA"},
]

# ----------------------------------------------------------------------------
#  FULL MAKERS , scraped whole (every model), by maker NAME.
#  Used for lower-volume makers where we want the entire catalogue. The maker's
#  numeric id is resolved at runtime from the board page (we don't hardcode ids
#  we don't know). Names must match the site's maker labels (uppercase).
# ----------------------------------------------------------------------------
FULL_MAKERS = ["AUDI", "BMW", "MERCEDES BENZ", "VOLKSWAGEN", "VOLVO"]

# ----------------------------------------------------------------------------
#  EXPAND TO FULL SITE  (set True to scrape EVERY model of EVERY maker)
#  Heavy and rate-limit-prone. Leave False for the curated list above.
# ----------------------------------------------------------------------------
SCRAPE_ALL = False



def now_iso():
    return datetime.now(timezone.utc).isoformat()

def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default

def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------------------------------------------------------------------------
# Maker words that can prefix the spec cell on whole-maker boards, longest
# first so "MERCEDES BENZ" matches before "MERCEDES". Used to split
# "AUDI A1 2017 8XCHZ 1.0TFSI" into model + year/chassis/grade.
_MAKER_WORDS = ["MERCEDES BENZ", "VOLKSWAGEN", "MITSUBISHI", "DAIHATSU",
                "TOYOTA", "NISSAN", "SUBARU", "SUZUKI", "HONDA", "MAZDA",
                "LEXUS", "ISUZU", "AUDI", "BMW", "VOLVO"]

def _parse_spec_cell(c3):
    """Parse the spec cell, which has two layouts:
       per-model board:   'YEAR CHASSIS GRADE'          e.g. '2017 8XCHZ 1.0TFSI'
       whole-maker board: 'MAKER MODEL YEAR CHASSIS GRADE' e.g. 'AUDI A1 2017 8XCHZ 1.0TFSI'
    Returns (model_row, year, chassis, grade). model_row is None on per-model
    boards (the model is already known from config there).
    """
    s = c3 or ""
    up = s.upper()
    for mk in _MAKER_WORDS:
        if up.startswith(mk + " "):
            rest = s[len(mk):].strip()
            mm = re.match(r"(.*?)\s+(\d{4})\s+(\S+)\s*(.*)", rest)
            if mm:
                return (mm.group(1).strip() or None, mm.group(2),
                        mm.group(3), (mm.group(4).strip() or None))
            break
    m = re.match(r"(\d{4})\s+(\S+)\s*(.*)", s)
    if m:
        return None, m.group(1), m.group(2), (m.group(3).strip() or None)
    return None, None, None, None

def parse_board(html):
    soup = BeautifulSoup(html, "lxml")
    out = soup.find(id="aj_out_poisk")
    if not out:
        return []
    rows = out.find_all("tr", id=re.compile(r"^aj_view\d+"))
    lots = []
    for r in rows:
        link = r.find("a", href=re.compile(r"aj-"))
        if not link:
            continue
        href = link.get("href")
        lot_uid = re.sub(r"^aj-|\.htm$", "", href)
        cells = r.find_all("td", recursive=False)
        def ct(i):
            return " ".join(cells[i].get_text(" ", strip=True).split()) if i < len(cells) else ""
        c2 = ct(2)
        m_date = re.search(r"(\d{2}\.\d{2}\.\d{4})", c2)
        m_time = re.search(r"\[(\d{2}:\d{2})\]", c2)
        auction = c2
        if m_date:
            auction = c2.split(m_date.group(1))[-1]
        auction = re.sub(r"^\s*\[\d{2}:\d{2}\]\s*", "", auction).strip()
        auction = re.sub(r"\s+", " ", auction)
        # the lot number sometimes leads the cell; strip it if duplicated
        lotnum = link.get_text(strip=True)
        auction = re.sub(r"^" + re.escape(lotnum) + r"\s*", "", auction).strip()
        c3 = ct(3)
        # spec cell: handles both per-model and whole-maker layouts
        model_row, year, chassis, grade = _parse_spec_cell(c3)
        c4 = ct(4)
        m_cc = re.search(r"(\d+)\s*cc", c4)
        c5 = ct(5)
        m_km = re.search(r"(\d+)\s*km", c5)
        m_cond = re.search(r"km\s+(\S+)\s+(\S+)\s*$", c5)
        c7 = ct(7)
        m_avg = re.search(r"yen\|(\d+)\|(\d+)\|(\d+)", c7)
        start_yen = sold_yen = avg_yen = None
        if m_avg:
            start_yen, sold_yen, avg_yen = m_avg.group(1), m_avg.group(2), m_avg.group(3)
        lots.append({
            "lot_uid": lot_uid,
            "lot_url": BASE + "/" + href,
            "lot_number": link.get_text(strip=True),
            "auction": auction,
            "auction_date": m_date.group(1) if m_date else None,
            "auction_time": m_time.group(1) if m_time else None,
            "year": year,
            "chassis": chassis,
            "grade": grade,
            "engine_cc": m_cc.group(1) if m_cc else None,
            "spec_raw": c4,
            "mileage_km": m_km.group(1) if m_km else None,
            "condition": m_cond.group(2) if m_cond else None,
            "start_yen": start_yen,
            "sold_yen": sold_yen,
            "avg_yen": avg_yen,
            "model_row": model_row,
        })
    return lots

def get_page_count(html):
    """How many result pages exist. Read the pagination control specifically:
    it uses navi(this,N) onclick calls. Fall back to total-count / 20."""
    soup = BeautifulSoup(html, "lxml")
    out = soup.find(id="aj_out_poisk")
    if not out:
        return 1
    navi_nums = [int(n) for n in re.findall(r"navi\(this,(\d+)\)", str(out))]
    if navi_nums:
        return max(navi_nums)
    span = out.find("span")
    if span and span.get_text(strip=True).isdigit():
        total = int(span.get_text(strip=True))
        return max(1, -(-total // 20))  # ceil division
    return 1


def get_usd_per_yen(html):
    """The site publishes its own rate table: tpl_curr={"yen":[{"usd":"160.9",...}]}
    That number is yen-per-usd. Return usd-per-yen (1/that). Fallback None."""
    m = re.search(r'"yen"\s*:\s*\[\s*\{[^}]*"usd"\s*:\s*"([\d.]+)"', html)
    if m:
        try:
            yen_per_usd = float(m.group(1))
            if yen_per_usd > 0:
                return round(1.0 / yen_per_usd, 6)
        except ValueError:
            pass
    return None

# ---------------------------------------------------------------------------
def login(page):
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    try: page.evaluate("aj_login()")
    except Exception: pass
    page.wait_for_timeout(600)
    page.fill("input[name=username]", USER)
    page.fill("input[name=password]", PW)
    try: page.evaluate("doLoad_login()")
    except Exception: pass
    page.wait_for_timeout(3000)
    return USER.lower() in page.content().lower()

def resolve_maker_ids(page):
    """Map maker NAME -> site maker id by reading the maker links on the board.

    The board page lists makers as links whose handlers carry the maker id
    (e.g. model_submit('12','',1) or a maker_id in the onclick/href). We read
    the visible label and the id together. Returns {NAME_UPPER: id}.

    NOTE: selector confirmed on first live run. If a FULL_MAKERS entry doesn't
    resolve, its name likely differs from the site label (e.g. 'MERCEDES BENZ'
    vs 'MERCEDES-BENZ') — adjust the FULL_MAKERS spelling to match the site.
    """
    try:
        page.goto(BASE + "/aj_neo", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        pairs = page.evaluate(
            r"""() => {
                const out = {};
                // maker links typically call a submit with the maker id as first arg
                const els = Array.from(document.querySelectorAll("a[onclick], a[href]"));
                for (const el of els) {
                    const label = (el.textContent || "").trim().toUpperCase();
                    if (!label) continue;
                    const oc = el.getAttribute("onclick") || el.getAttribute("href") || "";
                    // look for a numeric id argument
                    const m = oc.match(/(?:maker[_a-z]*|submit)\s*\(?\s*['"]?(\d+)['"]?/i)
                            || oc.match(/['"](\d+)['"]\s*,\s*['"]/);
                    if (m) out[label] = m[1];
                }
                return out;
            }"""
        )
        return pairs or {}
    except Exception as e:
        print(f"  ! resolve_maker_ids failed: {e}", file=sys.stderr)
        return {}


def discover_all_models(page):
    """For SCRAPE_ALL: enumerate every (maker_id, model) board on the site.

    The board page exposes makers and, once a maker is chosen, that maker's
    model list. We read those selectors to build the full work list.

    NOTE: this path drives the site's own maker/model controls. The exact
    selector names should be confirmed on the first live full run — if it
    returns an empty or tiny list, the selector below needs adjusting to match
    the live DOM. The curated MODELS list does not depend on this function.
    """
    work = []
    for maker_id, maker_name in MAKER_NAME.items():
        try:
            # ask the site for this maker's model list via its own JS
            models = page.evaluate(
                """(mk) => {
                    // the board exposes a model dropdown per maker; collect its options
                    const sel = document.querySelector('select[name="model"], #model, #aj_model');
                    if(!sel) return [];
                    return Array.from(sel.options)
                        .map(o => o.value || o.textContent.trim())
                        .filter(v => v && v.toLowerCase() !== 'all');
                }""", maker_id)
            for mdl in (models or []):
                work.append({"maker_id": maker_id, "model": mdl})
        except Exception as e:
            print(f"  ! could not list models for {maker_name}: {e}", file=sys.stderr)
    return work


def scrape_model(page, maker_id, model, fresh_nav=True):
    """Load a model board, walk all result pages, return all lots.

    Speed notes (same request volume, just less idle waiting):
    - We only navigate to /aj_neo once per maker-session; subsequent models
      reuse the loaded page and just call model_submit again (fresh_nav=False).
    - We wait for the results container to be present rather than sleeping a
      fixed time, then a short polite gap.
    """
    if fresh_nav:
        page.goto(BASE + "/aj_neo", wait_until="domcontentloaded")
        try:
            page.wait_for_selector("#aj_out_poisk", timeout=8000)
        except Exception:
            page.wait_for_timeout(1200)
    page.evaluate(f"model_submit('{maker_id}','{model}',1)")
    # wait for the board to render results (or settle) instead of fixed 3s
    try:
        page.wait_for_function(
            "() => { const o=document.getElementById('aj_out_poisk');"
            "return o && o.innerHTML && o.innerHTML.length > 50; }",
            timeout=8000)
    except Exception:
        page.wait_for_timeout(1500)
    page.wait_for_timeout(400)  # small polite settle
    html = page.content()
    all_lots = parse_board(html)
    total_pages = get_page_count(html)

    current = 1
    guard = 0
    while current < total_pages and guard < 100:
        guard += 1
        target = current + 1
        try:
            link = page.locator(
                f"#aj_out_poisk a[onclick*='navi(this,{target})']"
            ).first
            if link.count() == 0:
                print(f"   page {target}: link not present, stopping", file=sys.stderr)
                break
            link.click(timeout=5000)
            # wait for the page-number link we clicked to no longer be the
            # 'next' target (i.e. content advanced), then a short settle
            try:
                page.wait_for_function(
                    "(t) => { const o=document.getElementById('aj_out_poisk');"
                    "return o && o.innerHTML && o.innerHTML.length > 50; }",
                    arg=str(target), timeout=6000)
            except Exception:
                page.wait_for_timeout(1200)
            page.wait_for_timeout(300)  # small polite gap between page turns
            page_lots = parse_board(page.content())
            if page_lots:
                all_lots += page_lots
                current = target
            else:
                print(f"   page {target}: no rows parsed, stopping", file=sys.stderr)
                break
        except Exception as e:
            print(f"   page {target} failed: {str(e)[:120]}", file=sys.stderr)
            break

    # de-dupe by lot_uid
    seen, uniq = set(), []
    for lot in all_lots:
        if lot["lot_uid"] not in seen:
            seen.add(lot["lot_uid"]); uniq.append(lot)
    return uniq

# ---------------------------------------------------------------------------
def parse_lot_price(html):
    """From an individual lot page (aj-XXXX.htm), extract the sold price in yen.
    Returns (sold_yen:str|None, page_gone:bool).

    We don't have a confirmed selector yet, so we parse defensively from the
    visible text: the lot page shows 'Sold for' with a yen figure, and an ended
    page shows 'nothing found' / 'This auction has ended'. Both are handled.
    If the layout differs from what we expect, we return (None, False) so the
    car is simply retried next run rather than wrongly marked gone.
    """
    low = html.lower()
    if "nothing found" in low or "auction has ended" in low and "sold" not in low:
        # page exists but lists nothing and no sold figure — treat as gone only
        # if there is genuinely no price anywhere on the page
        pass
    # Look for the board-style packed value first if present
    m = re.search(r"yen\|(\d+)\|(\d+)\|(\d+)", html)
    if m and m.group(2) != "0":
        return m.group(2), False
    # Look for a 'sold' figure: patterns like '1 590 000 ¥ sold' or 'Sold for ... ¥'
    # Normalise spaces in numbers (the site writes '1 590 000')
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    # find a yen amount immediately followed by 'sold'
    m2 = re.search(r"([\d][\d \u00a0,]{2,})\s*¥\s*sold", text, re.I)
    if m2:
        digits = re.sub(r"[^\d]", "", m2.group(1))
        if digits and int(digits) > 0:
            return digits, False
    # explicit 'nothing found' with no price → page is gone/empty
    if "nothing found" in low:
        return None, True
    return None, False


def fetch_lot_price(page, lot_url):
    """Navigate to a lot page and return (sold_yen, page_gone)."""
    try:
        page.goto(lot_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            page.wait_for_timeout(800)
        return parse_lot_price(page.content())
    except Exception as e:
        print(f"   ! fetch failed {lot_url}: {str(e)[:80]}", file=sys.stderr)
        return None, False


def run_price_pass():
    """Visit ended, price-less lot pages and capture sold prices.
    Newest-ended first, skipping cars that ended too recently (price not
    settled). Capped per run. Learns empirically: if a page truly returns
    'nothing found', mark it so we don't refetch it."""
    if not USER or not PW:
        print("ERROR: set JCD_USERNAME and JCD_PASSWORD", file=sys.stderr); sys.exit(1)
    state = load_json(STATE_FILE, {})
    events = load_json(EVENTS_FILE, [])
    ts = now_iso()
    now_ms = datetime.now(timezone.utc).timestamp()

    def ended_moment(rec):
        d = rec.get("auction_date"); t = rec.get("auction_time") or "23:59"
        if not d: return None
        try:
            dd, mm, yy = d.split("."); hh, mi = t.split(":")
            # JST = UTC+9
            return datetime(int(yy), int(mm), int(dd), int(hh), int(mi),
                            tzinfo=timezone.utc).timestamp() - 9*3600
        except Exception:
            return None

    # candidates: have a lot_url, no sold price, auction moment has passed by
    # at least PRICE_MIN_AGE_H, and not already marked page_gone
    cands = []
    for uid, rec in state.items():
        if rec.get("price_gone"):
            continue
        if parse_int_safe(rec.get("sold_yen")):
            continue
        em = ended_moment(rec)
        if em is None:
            continue
        age_h = (now_ms - em) / 3600.0
        if age_h < PRICE_MIN_AGE_H:
            continue  # ended too recently; price may not be posted
        cands.append((em, uid, rec))

    # newest-ended first
    cands.sort(key=lambda x: x[0], reverse=True)
    batch = cands[:PRICE_BATCH]
    print(f"Price pass: {len(cands)} price-less ended cars; fetching {len(batch)} this run.")

    captured = gone = 0
    start_t = time.time()
    MAX_RUN_S = 45 * 60   # stop gracefully well before the 60-min job timeout
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        print("Logging in...")
        if not login(page):
            print("LOGIN FAILED", file=sys.stderr); browser.close(); sys.exit(1)
        print("Login OK.")
        for i, (em, uid, rec) in enumerate(batch, 1):
            if time.time() - start_t > MAX_RUN_S:
                print(f"  time guard: stopping at {i-1}/{len(batch)} to commit safely before timeout")
                break
            url = rec.get("lot_url")
            if not url:
                continue
            sold, page_gone = fetch_lot_price(page, url)
            if sold and parse_int_safe(sold):
                rec["sold_yen"] = sold
                rec["status"] = "sold"
                rec["price_captured_at"] = ts
                events.append({"ts": ts, "type": "sold_price", "lot_uid": uid,
                               "lot_number": rec.get("lot_number"), "sold_yen": sold})
                captured += 1
            elif page_gone:
                rec["price_gone"] = True
                rec["price_gone_at"] = ts
                gone += 1
            if i % 50 == 0:
                print(f"  ...{i}/{len(batch)} (captured {captured}, gone {gone})")
            page.wait_for_timeout(PRICE_GAP_MS)
        browser.close()

    save_json(STATE_FILE, state)
    save_json(EVENTS_FILE, events)
    # refresh dashboard.json so the site reflects the new prices
    rebuild_dashboard(state, events)
    save_json(DATA_DIR / "last_run_status.json", {
        "ts": ts, "ok": True, "mode": "prices",
        "fetched": len(batch), "captured": captured, "page_gone": gone,
        "remaining_priceless": len(cands) - captured - gone})
    print(f"\nPrice pass done. Captured {captured} prices, {gone} pages gone, "
          f"~{len(cands)-captured-gone} price-less ended cars remain.")


def parse_int_safe(v):
    try:
        return int(v) if v not in (None, "", "0") else 0
    except (TypeError, ValueError):
        return 0


def rebuild_dashboard(state, events):
    """Recompute dashboard.json from current state (shared by both modes)."""
    lots_list = list(state.values())
    sold_lots = [l for l in lots_list if parse_int_safe(l.get("sold_yen"))]
    by_model, by_maker = {}, {}
    for l in lots_list:
        by_model[l.get("model_tracked", "?")] = by_model.get(l.get("model_tracked", "?"), 0) + 1
        by_maker[l.get("maker", "?")] = by_maker.get(l.get("maker", "?"), 0) + 1
    usd = None
    # reuse last known rate stored on any lot/run if present
    for l in lots_list:
        if l.get("usd_per_yen"):
            usd = l["usd_per_yen"]; break
    dashboard = {
        "generated": now_iso(),
        "usd_per_yen": usd,
        "totals": {
            "tracked": len(lots_list),
            "live": sum(1 for l in lots_list if l.get("status") == "live"),
            "sold_captured": len(sold_lots),
            "off_board": sum(1 for l in lots_list if l.get("status") == "off_board"),
            "by_model": by_model, "by_maker": by_maker,
        },
        "lots": lots_list,
        "recent_events": events[-200:],
    }
    save_json(DATA_DIR / "dashboard.json", dashboard)


def run():
    if not USER or not PW:
        print("ERROR: set JCD_USERNAME and JCD_PASSWORD", file=sys.stderr); sys.exit(1)
    state = load_json(STATE_FILE, {})       # lot_uid -> record
    events = load_json(EVENTS_FILE, [])
    today = datetime.now().strftime("%Y-%m-%d")
    ts = now_iso()
    snapshot = []
    usd_per_yen = None
    # maker name lookup from MODELS config
    MAKER_NAME = {"1":"TOYOTA","2":"NISSAN","3":"MAZDA","4":"MITSUBISHI","5":"HONDA",
                  "6":"SUZUKI","7":"SUBARU","8":"ISUZU","9":"DAIHATSU","23":"LEXUS"}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        print("Logging in...")
        if not login(page):
            print("LOGIN FAILED", file=sys.stderr); browser.close(); sys.exit(1)
        print("Login OK.")

        # Build the work list: either the curated MODELS, or every model on the
        # site (discovered per maker) when SCRAPE_ALL is on.
        if SCRAPE_ALL:
            print("SCRAPE_ALL on — discovering all models per maker...")
            work = discover_all_models(page)
            print(f"  discovered {len(work)} model boards across all makers")
        else:
            work = list(MODELS)
            # Resolve FULL_MAKERS (European brands) to their site maker ids and
            # add a whole-maker entry (model="" => no model filter, all models).
            if FULL_MAKERS:
                name_to_id = resolve_maker_ids(page)
                for mk in FULL_MAKERS:
                    mid = name_to_id.get(mk.upper())
                    if mid:
                        work.append({"maker_id": mid, "model": "", "maker_name": mk.upper()})
                        MAKER_NAME[mid] = mk.upper()
                    else:
                        print(f"  ! could not resolve maker id for '{mk}' — skipping", file=sys.stderr)

        for idx, m in enumerate(work):
            label = m["model"] or f"(all {m.get('maker_name', m['maker_id'])})"
            print(f"Scraping {label} (maker {m['maker_id']})...")
            try:
                lots = scrape_model(page, m["maker_id"], m["model"], fresh_nav=(idx == 0))
            except Exception as e:
                print(f"  ! error on {label}: {e}", file=sys.stderr)
                lots = []
            print(f"  {len(lots)} lots")
            if usd_per_yen is None:
                usd_per_yen = get_usd_per_yen(page.content())
            for lot in lots:
                # for whole-maker scrapes, model isn't pre-known; keep the model
                # parsed from the row if present, else mark by maker
                lot["model_tracked"] = m["model"] or lot.get("model_tracked") or lot.get("model_row") or "(maker)"
                lot["maker"] = MAKER_NAME.get(m["maker_id"], m["maker_id"])
                lot["last_seen"] = ts
                if usd_per_yen:
                    lot["usd_per_yen"] = usd_per_yen
                snapshot.append(lot)
        browser.close()

    # ---- SANITY GUARD ----
    # Protect the dataset: if this run came back with far fewer cars than the
    # previous live count, something went wrong (HTML change, rate-limit,
    # partial/blocked scrape). Do NOT overwrite good data with junk.
    prev_live = sum(1 for r in state.values() if r.get("status") == "live")
    this_count = len(snapshot)
    MIN_OK = 50            # absolute floor: a real run of these models returns hundreds
    DROP_RATIO = 0.5       # flag if we got under half of last run's live count
    if state and prev_live > 0:
        if this_count < MIN_OK or this_count < prev_live * DROP_RATIO:
            print(f"SANITY GUARD TRIPPED: scraped {this_count} lots vs {prev_live} "
                  f"previously live. Refusing to overwrite data. "
                  f"Likely a site change, rate-limit, or partial scrape.",
                  file=sys.stderr)
            # write a marker so the run is visibly flagged, but leave data intact
            save_json(DATA_DIR / "last_run_status.json", {
                "ts": ts, "ok": False, "scraped": this_count,
                "prev_live": prev_live, "reason": "low_count_guard"})
            sys.exit(2)
    else:
        # first ever run: just require a non-trivial count
        if this_count < MIN_OK:
            print(f"SANITY GUARD: first run returned only {this_count} lots "
                  f"(expected hundreds). Not saving. Check login/scrape.",
                  file=sys.stderr)
            save_json(DATA_DIR / "last_run_status.json", {
                "ts": ts, "ok": False, "scraped": this_count,
                "reason": "first_run_too_small"})
            sys.exit(2)
    print(f"Sanity OK: {this_count} lots scraped (prev live {prev_live}).")

    # Merge into state + log events
    seen_today = set()
    for lot in snapshot:
        uid = lot["lot_uid"]; seen_today.add(uid)
        if uid not in state:
            lot["first_seen"] = ts; lot["status"] = "live"
            state[uid] = lot
            events.append({"ts": ts, "type": "new_lot", "lot_uid": uid,
                           "lot_number": lot["lot_number"], "model": lot["model_tracked"],
                           "auction_date": lot["auction_date"], "auction_time": lot["auction_time"]})
        else:
            prev = state[uid]
            # capture a newly appearing sold price
            if (prev.get("sold_yen") in (None, "0")) and lot.get("sold_yen") not in (None, "0"):
                events.append({"ts": ts, "type": "sold_price", "lot_uid": uid,
                               "lot_number": lot["lot_number"], "sold_yen": lot["sold_yen"]})
                prev["status"] = "sold"
            prev.update({k: lot[k] for k in lot if k != "first_seen"})
            prev["last_seen"] = ts

    # mark lots no longer on the board
    for uid, rec in state.items():
        if uid not in seen_today and rec.get("status") == "live":
            rec["status"] = "off_board"
            rec["off_board_since"] = ts
            events.append({"ts": ts, "type": "off_board", "lot_uid": uid,
                           "lot_number": rec.get("lot_number")})

    save_json(STATE_FILE, state)
    save_json(EVENTS_FILE, events)
    save_json(SNAP_DIR / f"{today}.json", snapshot)

    # ---- dashboard.json: pre-computed view the static page reads ----
    lots_list = list(state.values())
    def to_int(v):
        try: return int(v)
        except (TypeError, ValueError): return None
    sold_lots = [l for l in lots_list if to_int(l.get("sold_yen"))]
    by_model = {}
    for l in lots_list:
        by_model.setdefault(l.get("model_tracked", "?"), 0)
        by_model[l.get("model_tracked", "?")] += 1
    by_maker = {}
    for l in lots_list:
        by_maker.setdefault(l.get("maker", "?"), 0)
        by_maker[l.get("maker", "?")] += 1
    dashboard = {
        "generated": ts,
        "usd_per_yen": usd_per_yen,
        "totals": {
            "tracked": len(lots_list),
            "live": sum(1 for l in lots_list if l.get("status") == "live"),
            "sold_captured": len(sold_lots),
            "off_board": sum(1 for l in lots_list if l.get("status") == "off_board"),
            "by_model": by_model,
            "by_maker": by_maker,
        },
        "lots": lots_list,
        "recent_events": events[-200:],
    }
    save_json(DATA_DIR / "dashboard.json", dashboard)
    save_json(DATA_DIR / "last_run_status.json", {
        "ts": ts, "ok": True, "scraped": len(snapshot),
        "total_tracked": len(state)})

    live = sum(1 for r in state.values() if r.get("status") == "live")
    print(f"\nDone. {len(snapshot)} lots on board today | {len(state)} total tracked | {live} live")
    print(f"Saved: {STATE_FILE.name}, {EVENTS_FILE.name}, dashboard.json, snapshots/{today}.json")

if __name__ == "__main__":
    if MODE == "prices":
        print("=== MODE: prices (lot-page price completion) ===")
        run_price_pass()
    else:
        print("=== MODE: board (live auction board scrape) ===")
        run()
