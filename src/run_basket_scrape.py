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