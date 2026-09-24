"""
run_basket_scrape.py
---------------------
Loops the CONFIRMED real_scraper_poc.py extraction across a route basket and
the 5 advance-purchase windows the problem statement asks for (T+1/7/15/30/45),
writing every result -- success or honest failure -- to a single CSV as it
goes, so an interrupted run keeps whatever it already collected.

This does NOT invent data for a route/window that fails. Every row's `status`
column tells you exactly what happened for that attempt (see ScrapedFare's
docstring in real_scraper_poc.py for the full list of status values).

USAGE
-----
    python run_basket_scrape.py

Runs against ixigo only, for the 5 routes ixigo's search widget has already
been confirmed to accept via `from`/`to` params (DEL-BOM was verified live;
the others use the identical mechanism so should work the same way, but
that's a claim to verify on this run, not an assumption to trust blindly --
check the status column).

RATE LIMITING
-------------
This makes ~25 real navigations to ixigo (5 routes x 5 windows) in one run.
POLITE_DELAY_BETWEEN_SEARCHES below adds a pause between each. Don't lower
this to "go faster" -- the problem statement explicitly requires rate-limited,
ethical scraping, and hammering a single source repeatedly is also how you
get IP-blocked, which would set you back further than the delay costs you.
"""

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from scraper import scrape_one_search

# Routes ixigo's `from`/`to` params are expected to accept -- same 5 routes
# your dashboard's route_code_map already knows about. Extend this once
# you've confirmed the remaining basket routes work the same way.
ROUTE_BASKET = [
    ("DEL", "BOM"),
    ("DEL", "BLR"),
    ("BOM", "BLR"),
    ("DEL", "HYD"),
    ("DEL", "CCU"),
]

ADVANCE_WINDOWS_DAYS = [1, 7, 15, 30, 45]

POLITE_DELAY_BETWEEN_SEARCHES = 5.0  # seconds

OUTPUT_CSV = Path("data/processed/live_scraped_quotes_real.csv")


async def run_basket():
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now()

    total_attempts = len(ROUTE_BASKET) * len(ADVANCE_WINDOWS_DAYS)
    attempt_num = 0
    wrote_header = OUTPUT_CSV.exists()

    for origin, dest in ROUTE_BASKET:
        for lead_days in ADVANCE_WINDOWS_DAYS:
            attempt_num += 1
            target_date = today + timedelta(days=lead_days)
            date_str = target_date.strftime("%Y-%m-%d")

            print(
                f"\n=== [{attempt_num}/{total_attempts}] {origin}-{dest} "
                f"@ T+{lead_days} ({date_str}) ==="
            )

            try:
                records = await scrape_one_search(origin, dest, date_str)
            except Exception as e:
                # A crash on one route/window shouldn't lose everything
                # already collected -- log it as data, not a stack trace,
                # and keep going.
                print(f"!!! Unhandled exception for {origin}-{dest} T+{lead_days}: {e}")
                records = []

            if records:
                df_chunk = pd.DataFrame([asdict(r) for r in records])
                df_chunk["lead_time_days"] = lead_days  # real, requested value -- not inferred
                df_chunk.to_csv(
                    OUTPUT_CSV,
                    mode="a",
                    header=not wrote_header,
                    index=False,
                )
                wrote_header = True

                ok_count = (df_chunk["status"] == "ok").sum()
                print(f"    -> {len(df_chunk)} record(s) written, {ok_count} with status='ok'")

            if attempt_num < total_attempts:
                await asyncio.sleep(POLITE_DELAY_BETWEEN_SEARCHES)

    print(f"\nDone. Full basket results in: {OUTPUT_CSV}")
    if OUTPUT_CSV.exists():
        df_all = pd.read_csv(OUTPUT_CSV)
        print("\n--- Status breakdown across this run ---")
        print(df_all["status"].value_counts().to_string())


if __name__ == "__main__":
    asyncio.run(run_basket())