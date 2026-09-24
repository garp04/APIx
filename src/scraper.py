import argparse
import asyncio
import json
import ssl
import sys
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    # certifi not installed -- fall back to the system default context.
    # If you're seeing CERTIFICATE_VERIFY_FAILED, run: pip install certifi
    SSL_CONTEXT = ssl.create_default_context()

# ---------------------------------------------------------------------------
# Config -- the parts you'll most likely need to tweak after a first live run
# ---------------------------------------------------------------------------

SOURCE_NAME = "ixigo"

# CONFIRMED against a real page dump on 2026-09-24 (results page, empty-state).
# The `from`/`to` query params ARE honoured by ixigo's frontend -- the origin
# and destination widgets rendered correctly from these. `date` and `class`
# are NOT honoured the same way (the Departure field stayed on its placeholder
# even with date= set), so we no longer rely on them and instead drive the
# real date picker below. Dropped `class=E` since it correlated with a broken
# "1 Traveller, undefined" render in the passenger widget.
SEARCH_URL_TEMPLATE = (
    "https://www.ixigo.com/search/result/flight"
    "?from={origin}&to={dest}&adults=1&children=0&infants=0"
)

# CONFIRMED data-testid attributes from the real page dump -- these are far
# more stable than the hashed CSS class names (e.g. "nsm7Bb-HzV7m-LgbsSe")
# that ixigo's build system generates, which rotate on every deploy.
ORIGIN_TESTID = "originId"
DEST_TESTID = "destinationId"
DEPARTURE_TESTID = "departureDate"
PAX_TESTID = "pax"
DEPARTURE_PLACEHOLDER_TEXT = "Departure"  # what an *unset* date field shows

# CONFIRMED: the genuine "zero results" empty state shows this heading.
# Treat it as ground truth -- "no flights" is a real answer, not a failure.
NO_RESULTS_HEADING_TEXT = "No flights found"

# UNVERIFIED -- the calendar popup was not open in the dump we have, so these
# are best-effort candidates for navigating month-by-month and clicking a day.
# If date selection fails, the run logs status="date_picker_failed" rather
# than guessing a fare; recalibrate these against debug_output/*.png.
CALENDAR_NEXT_MONTH_SELECTORS = [
    "button[aria-label*='next' i]",
    "[data-testid*='next' i]",
    "svg[data-testid='ChevronRightIcon']",
]
CALENDAR_MONTH_HEADER_SELECTORS = [
    "[class*='monthLabel']",
    "[class*='calendarHeader']",
    "[class*='month-year']",
]
SEARCH_BUTTON_NAME = "Search"

# UNVERIFIED -- no flight cards have ever actually rendered in what we've seen
# (only the empty state). Best-effort candidates; recalibrate on next run
# that returns real results.
FLIGHT_CARD_SELECTORS = [
    "div[class*='flightCard']",
    "div[class*='result-item']",
    "div[class*='ixi-flight']",
    "li[class*='flight']",
]

# Candidate selectors for "the fare/price text" *within* a card element.
PRICE_SELECTORS = [
    "[class*='price']",
    "[class*='fare']",
    "span[class*='amount']",
]

# Candidate selectors for the carrier/airline name within a card.
CARRIER_SELECTORS = [
    "[class*='airline']",
    "[class*='carrier']",
    "img[alt]",  # airline logos often carry the name in alt text
]

REQUEST_TIMEOUT_MS = 20_000
POLITE_DELAY_SECONDS = 2.0
MAX_RETRIES = 1
MAX_CALENDAR_MONTH_CLICKS = 12

DEBUG_DIR = Path("debug_output")
OUTPUT_CSV = Path("data/processed/live_scraped_quotes_real.csv")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ScrapedFare:
    scrape_timestamp: str
    source_platform: str
    origin: str
    destination: str
    sector: str
    departure_date: str
    carrier: Optional[str]
    raw_price_text: str
    parsed_fare_inr: Optional[float]
    status: str
    # status values:
    #   "ok"                 -- a real price was read from the page
    #   "parse_failed"       -- a card was found but its price text had no digits
    #   "no_flights_available" -- the site's own "No flights found!" empty
    #                            state -- a GENUINE answer, not a scraper failure
    #   "no_cards_found"     -- page rendered but none of FLIGHT_CARD_SELECTORS
    #                            matched -- needs selector recalibration
    #   "no_cards_found_but_prices_present" -- DOM scan found real rupee-amount
    #                            text but FLIGHT_CARD_SELECTORS missed its
    #                            container -- see price_candidates_*.json
    #   "date_picker_failed" -- couldn't set the departure date via the UI --
    #                            needs calendar selector recalibration
    #   "blocked_or_timeout" -- navigation/render never completed
    #   "blocked_by_robots_txt" -- robots.txt disallowed this URL
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# robots.txt compliance -- required by the problem statement
# ---------------------------------------------------------------------------

def is_scraping_allowed(target_url: str, user_agent: str = "*") -> bool:
    """
    Checks robots.txt for the target host before we touch the search page.
    Fails CLOSED: if robots.txt can't be fetched/parsed, we do not assume
    permission -- we refuse to scrape and say so.

    Fetches manually (instead of RobotFileParser.read(), which uses urllib's
    default SSL context) so we can hand it certifi's CA bundle -- Python venvs
    on macOS frequently lack access to the system root store and raise
    CERTIFICATE_VERIFY_FAILED on every HTTPS request otherwise.
    """
    parsed = urlparse(target_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)

    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": "APIx-Bot/0.1 (+research)"})
        with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        rp.parse(raw.splitlines())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # No robots.txt at all is conventionally treated as "allow everything".
            print(f"[robots.txt] {robots_url} -> 404, treating as allowed.")
            return True
        print(f"[robots.txt] HTTP error fetching {robots_url} ({e}). Refusing to scrape (fail closed).")
        return False
    except Exception as e:
        print(f"[robots.txt] Could not fetch {robots_url} ({e}). Refusing to scrape (fail closed).")
        return False

    allowed = rp.can_fetch(user_agent, target_url)
    print(f"[robots.txt] {robots_url} -> {'ALLOWED' if allowed else 'DISALLOWED'} for {target_url}")
    return allowed


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def parse_price_text(text: str) -> Optional[float]:
    """Turn '₹5,432' / 'INR 5432' / '5,432' into 5432.0. Returns None if no digits found."""
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


async def find_first_matching(locator_root, selectors: list[str]):
    """Try each selector in order against locator_root; return (selector_used, count)."""
    for sel in selectors:
        loc = locator_root.locator(sel)
        count = await loc.count()
        if count > 0:
            return sel, count
    return None, 0


async def get_testid_text(page, testid: str) -> Optional[str]:
    loc = page.locator(f"[data-testid='{testid}']")
    if await loc.count() == 0:
        return None
    try:
        return (await loc.first.inner_text()).strip()
    except Exception:
        return None


async def scan_for_price_candidates(page) -> list[dict]:
    """
    Class-name-agnostic fallback: walk every text node on the rendered page
    and flag anything that looks like a rupee amount. This exists because
    guessing CSS selectors is fundamentally fragile against frameworks (this
    site included) that hash class names per build -- a regex over visible
    text survives a redesign that would break any class-based selector list.

    This does NOT construct flight records by itself (we don't yet know
    which price belongs to which card/carrier) -- it surfaces raw evidence
    so a human can look at debug_output/price_candidates_*.json once and
    tell us the right grouping, instead of guessing blind over more runs.
    """
    js = r"""
    () => {
      const priceRegex = /(₹|Rs\.?|INR)\s?[0-9][0-9,]{2,}/i;
      const out = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let node;
      let seen = 0;
      while ((node = walker.nextNode()) && seen < 200) {
        const text = (node.textContent || "").trim();
        if (text && priceRegex.test(text)) {
          const el = node.parentElement;
          out.push({
            text: text.slice(0, 80),
            tag: el ? el.tagName : null,
            className: el ? String(el.className || "").slice(0, 160) : null,
            testid: el ? el.getAttribute("data-testid") : null,
            parentTestid: el && el.parentElement ? el.parentElement.getAttribute("data-testid") : null,
          });
          seen += 1;
        }
      }
      return out;
    }
    """
    try:
        return await page.evaluate(js)
    except Exception as e:
        print(f"[price-scan] Failed to run DOM scan: {e}")
        return []


async def page_shows_no_results(page) -> bool:
    """True if ixigo's own genuine 'zero flights' empty state is showing."""
    try:
        return await page.get_by_text(NO_RESULTS_HEADING_TEXT, exact=False).count() > 0
    except Exception:
        return False


async def extract_ixigo_fares(page) -> list[dict]:
    """
    CONFIRMED against a real ixigo results page dump (2026-09-25, DEL-BOM):
    each flight's displayed price sits in an <h6 data-testid="pricing">
    element. An identical <h5 data-testid="pricing"> duplicate also exists
    in the DOM for every card -- almost certainly a responsive/collapsed
    variant that's present regardless of viewport (our TreeWalker scan
    doesn't respect CSS visibility). We take only the h6 ones so each real
    flight is counted once, not twice.

    Carrier name isn't confirmed the same way -- the price-candidate scan
    didn't capture airline logos. We make a best-effort attempt to find an
    <img alt="..."> in the nearest ancestor <div> that contains one (airline
    logos are conventionally rendered this way), but if that comes back
    empty, we still keep the price record with carrier=None rather than
    guessing or dropping the fare -- the price itself is the load-bearing
    data point for the index, not the carrier label.
    """
    price_elements = page.locator("h6[data-testid='pricing']")
    count = await price_elements.count()
    records = []
    for i in range(count):
        el = price_elements.nth(i)
        try:
            raw_price_text = (await el.inner_text()).strip()
        except Exception:
            continue

        carrier_text = None
        try:
            ancestor = el.locator("xpath=ancestor::div[.//img[@alt]][1]")
            if await ancestor.count() > 0:
                img = ancestor.first.locator("img[alt]").first
                if await img.count() > 0:
                    alt_val = await img.get_attribute("alt")
                    carrier_text = alt_val.strip() if alt_val else None
        except Exception:
            pass

        records.append({"raw_price_text": raw_price_text, "carrier": carrier_text})
    return records


async def try_set_departure_date_via_ui(page, target_date: datetime) -> tuple[bool, str]:
    """
    Best-effort: click the Departure field and pick target_date from the
    calendar popup. UNVERIFIED against a live calendar (see module docstring)
    -- if this fails, it fails loudly with a reason string rather than
    silently leaving the date unset.

    Returns (success, note).
    """
    try:
        departure_field = page.locator(f"[data-testid='{DEPARTURE_TESTID}']")
        if await departure_field.count() == 0:
            return False, "departureDate field not found on page"

        await departure_field.first.click()
        await page.wait_for_timeout(800)  # let the calendar popup animate in

        target_label_variants = [
            target_date.strftime("%d %b %Y"),   # "15 Oct 2026"
            target_date.strftime("%B %Y"),       # "October 2026" (month header)
            str(target_date.day),                # bare day number, last resort
        ]

        month_header = None
        header_sel, header_count = await find_first_matching(page, CALENDAR_MONTH_HEADER_SELECTORS)
        if header_count > 0:
            month_header = page.locator(header_sel).first

        clicks = 0
        while clicks < MAX_CALENDAR_MONTH_CLICKS:
            if month_header is not None:
                current_label = (await month_header.inner_text()).strip()
                if target_date.strftime("%B %Y") in current_label:
                    break
            # Try to click the target day directly -- covers calendars that
            # render several months at once without a single reliable header.
            day_candidates = page.get_by_text(str(target_date.day), exact=True)
            if await day_candidates.count() > 0:
                # Ambiguous without a header match, but attempt the first
                # visible one; worst case this click misses and we time out
                # below rather than silently mis-picking a date.
                try:
                    await day_candidates.first.click(timeout=2000)
                    await page.wait_for_timeout(500)
                    new_departure_text = await get_testid_text(page, DEPARTURE_TESTID)
                    if new_departure_text and new_departure_text != DEPARTURE_PLACEHOLDER_TEXT:
                        return True, f"date set via day-text click -> '{new_departure_text}'"
                except Exception:
                    pass

            next_sel, next_count = await find_first_matching(page, CALENDAR_NEXT_MONTH_SELECTORS)
            if next_count == 0:
                break
            await page.locator(next_sel).first.click()
            await page.wait_for_timeout(400)
            clicks += 1

        # Final check regardless of loop path.
        new_departure_text = await get_testid_text(page, DEPARTURE_TESTID)
        if new_departure_text and new_departure_text != DEPARTURE_PLACEHOLDER_TEXT:
            return True, f"date set -> '{new_departure_text}'"

        return False, (
            "calendar interaction completed but Departure field still shows "
            "placeholder -- calendar selectors need recalibration against "
            "debug_output/*.png from this run"
        )
    except Exception as e:
        return False, f"exception during date picker interaction: {e}"


# ---------------------------------------------------------------------------
# Core scrape routine for ONE route / ONE date
# ---------------------------------------------------------------------------

async def scrape_one_search(origin: str, dest: str, date_str: str) -> list[ScrapedFare]:
    search_url = SEARCH_URL_TEMPLATE.format(origin=origin, dest=dest, date=date_str)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sector = f"{origin}-{dest}"

    if not is_scraping_allowed(search_url):
        return [ScrapedFare(
            scrape_timestamp=now, source_platform=SOURCE_NAME, origin=origin,
            destination=dest, sector=sector, departure_date=date_str,
            carrier=None, raw_price_text="", parsed_fare_inr=None,
            status="blocked_by_robots_txt", notes=None,
        )]

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    results: list[ScrapedFare] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )
        page = await context.new_page()

        attempt = 0
        cards_locator = None
        status = "blocked_or_timeout"
        notes = None
        target_date = datetime.strptime(date_str, "%Y-%m-%d")

        while attempt <= MAX_RETRIES:
            attempt += 1
            try:
                print(f"[{SOURCE_NAME}] Attempt {attempt}: navigating to {search_url}")
                await page.goto(search_url, timeout=REQUEST_TIMEOUT_MS, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)  # let the SPA hydrate

                # Confirm the widget actually reflects our route -- catches
                # silent param-format mismatches before we trust anything else.
                origin_text = await get_testid_text(page, ORIGIN_TESTID)
                dest_text = await get_testid_text(page, DEST_TESTID)
                print(f"[{SOURCE_NAME}] Widget shows origin='{origin_text}' destination='{dest_text}'")

                departure_text = await get_testid_text(page, DEPARTURE_TESTID)
                if departure_text == DEPARTURE_PLACEHOLDER_TEXT or not departure_text:
                    print(f"[{SOURCE_NAME}] Departure date not set by URL params -- driving the date picker UI.")
                    date_ok, date_note = await try_set_departure_date_via_ui(page, target_date)
                    notes = date_note
                    print(f"[{SOURCE_NAME}] {date_note}")
                    if not date_ok:
                        status = "date_picker_failed"
                        break

                    search_btn = page.get_by_role("button", name=SEARCH_BUTTON_NAME)
                    if await search_btn.count() > 0:
                        await search_btn.first.click()
                        await page.wait_for_timeout(4000)
                    else:
                        notes = (notes or "") + " | Search button not found via role lookup"

                # Give the results grid (or the empty-state message) time to render.
                await page.wait_for_timeout(2000)

                if await page_shows_no_results(page):
                    print(f"[{SOURCE_NAME}] Site returned a genuine 'No flights found' empty state.")
                    status = "no_flights_available"
                    break

                # CONFIRMED extraction path first (see extract_ixigo_fares docstring).
                ixigo_fares = await extract_ixigo_fares(page)
                if ixigo_fares:
                    print(f"[{SOURCE_NAME}] Extracted {len(ixigo_fares)} real fares via confirmed h6[data-testid='pricing'] selector.")
                    for rec in ixigo_fares:
                        parsed_fare = parse_price_text(rec["raw_price_text"])
                        results.append(ScrapedFare(
                            scrape_timestamp=now, source_platform=SOURCE_NAME, origin=origin,
                            destination=dest, sector=sector, departure_date=date_str,
                            carrier=rec["carrier"], raw_price_text=rec["raw_price_text"],
                            parsed_fare_inr=parsed_fare,
                            status="ok" if parsed_fare is not None else "parse_failed",
                            notes=notes,
                        ))
                    status = "ok"
                    break

                # Fallback: generic guessed selectors, kept for resilience if
                # ixigo changes markup or this code is pointed at a new source.
                selector_used, count = await find_first_matching(page, FLIGHT_CARD_SELECTORS)
                if count > 0:
                    print(f"[{SOURCE_NAME}] Found {count} candidate cards via fallback selector '{selector_used}'")
                    cards_locator = page.locator(selector_used)
                    status = "ok"
                    break
                else:
                    print(f"[{SOURCE_NAME}] No flight-card selectors matched on attempt {attempt}.")
                    price_hits = await scan_for_price_candidates(page)
                    if price_hits:
                        print(
                            f"[price-scan] Found {len(price_hits)} price-looking text nodes on a "
                            f"page that render as neither empty-state nor a card-selector match. "
                            f"The page has real data -- FLIGHT_CARD_SELECTORS just needs updating. "
                            f"See debug_output/price_candidates_*.json for exact tag/class/testid to use."
                        )
                        notes = (
                            f"{len(price_hits)} real price strings found via DOM scan but "
                            f"FLIGHT_CARD_SELECTORS didn't match their container -- see "
                            f"price_candidates_*.json"
                        )
                        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                        pc_path = DEBUG_DIR / f"price_candidates_{SOURCE_NAME}_{sector}_{date_str}_{attempt}.json"
                        pc_path.write_text(json.dumps(price_hits, indent=2), encoding="utf-8")
                        status = "no_cards_found_but_prices_present"
                    else:
                        status = "no_cards_found"

            except PWTimeout:
                print(f"[{SOURCE_NAME}] Timeout on attempt {attempt}.")
                status = "blocked_or_timeout"
            except Exception as e:
                print(f"[{SOURCE_NAME}] Unexpected error on attempt {attempt}: {e}")
                status = "blocked_or_timeout"
                notes = str(e)

            await asyncio.sleep(POLITE_DELAY_SECONDS)

        # Always save debug artifacts, success or failure -- this is how you
        # calibrate selectors without guessing.
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        html_path = DEBUG_DIR / f"{SOURCE_NAME}_{sector}_{date_str}_{stamp}.html"
        png_path = DEBUG_DIR / f"{SOURCE_NAME}_{sector}_{date_str}_{stamp}.png"
        try:
            html_content = await page.content()
            html_path.write_text(html_content, encoding="utf-8")
            await page.screenshot(path=str(png_path), full_page=True)
            print(f"[debug] Saved {html_path.name} and {png_path.name}")
        except Exception as e:
            print(f"[debug] Could not save debug artifacts: {e}")

        if status != "ok" or (cards_locator is None and not results):
            await browser.close()
            return [ScrapedFare(
                scrape_timestamp=now, source_platform=SOURCE_NAME, origin=origin,
                destination=dest, sector=sector, departure_date=date_str,
                carrier=None, raw_price_text="", parsed_fare_inr=None,
                status=status, notes=notes,
            )]

        # If the confirmed extract_ixigo_fares() path already populated
        # `results` above, there's nothing left to do -- skip the generic
        # fallback extraction entirely.
        if cards_locator is not None:
            # Extract each card's price + carrier text for real (fallback path).
            card_count = await cards_locator.count()
            card_count = min(card_count, 40)  # sane cap for a POC run
            for i in range(card_count):
                card = cards_locator.nth(i)

                price_sel, price_count = await find_first_matching(card, PRICE_SELECTORS)
                raw_price_text = ""
                if price_count > 0:
                    raw_price_text = (await card.locator(price_sel).first.inner_text()).strip()

                carrier_sel, carrier_count = await find_first_matching(card, CARRIER_SELECTORS)
                carrier_text = None
                if carrier_count > 0:
                    el = card.locator(carrier_sel).first
                    carrier_text = (await el.get_attribute("alt")) or (await el.inner_text())
                    carrier_text = carrier_text.strip() if carrier_text else None

                parsed_fare = parse_price_text(raw_price_text)
                results.append(ScrapedFare(
                    scrape_timestamp=now, source_platform=SOURCE_NAME, origin=origin,
                    destination=dest, sector=sector, departure_date=date_str,
                    carrier=carrier_text, raw_price_text=raw_price_text,
                    parsed_fare_inr=parsed_fare,
                    status="ok" if parsed_fare is not None else "parse_failed",
                    notes=notes,
                ))

        await browser.close()

    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main(origin: str, dest: str, date_str: str):
    records = await scrape_one_search(origin, dest, date_str)

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([asdict(r) for r in records])
    df.to_csv(OUTPUT_CSV, index=False)

    ok_count = (df["status"] == "ok").sum() if not df.empty else 0
    no_flights_count = (df["status"] == "no_flights_available").sum() if not df.empty else 0
    print("\n--- Run summary ---")
    print(f"Total records: {len(df)}")
    print(f"Successfully parsed fares: {ok_count}")
    print(f"Saved to: {OUTPUT_CSV}")

    if no_flights_count > 0 and ok_count == 0:
        print(
            "\nSite returned a genuine 'No flights found' result for this "
            "route/date -- this is a real, honest answer (e.g. no inventory "
            "that far out, or a route/date combo the carriers don't fly), "
            "not a scraper failure. Try a different date or route to "
            "continue calibrating FLIGHT_CARD_SELECTORS."
        )
    elif ok_count == 0:
        print(
            "\nNo real fares extracted this run. Check the 'status' and "
            "'notes' columns in the CSV, then open debug_output/*.png and "
            "*.html to see what the page actually rendered, and update the "
            "relevant selector list at the top of this file to match."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="DEL")
    parser.add_argument("--dest", default="BOM")
    parser.add_argument(
        "--date", default=None,
        help="YYYY-MM-DD. Defaults to 21 days from today.",
    )
    args = parser.parse_args()

    if args.date:
        date_str = args.date
    else:
        from datetime import timedelta
        date_str = (datetime.now() + timedelta(days=21)).strftime("%Y-%m-%d")

    try:
        asyncio.run(main(args.origin, args.dest, date_str))
    except KeyboardInterrupt:
        sys.exit(1)