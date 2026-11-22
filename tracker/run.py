"""Daily tracker: scrape, snapshot, compare with the last run, report, and fail loudly.

Usage:
    uv run python -m tracker.run                      # normal daily run
    uv run python -m tracker.run --force              # run again even if today already succeeded
    uv run python -m tracker.run --raw-dir data/raw/X --date 2026-11-22   # replay saved JSON (testing)
"""
import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scraper import clean, fetch
from tracker.checks import check_brand
from tracker.compare import compare, previous_by_brand, save_snapshot, to_snapshot
from tracker.report import write_report

TRACKER_DIR = Path("data/tracker")
CHANGES_CSV = TRACKER_DIR / "changes.csv"
RUNS_CSV = TRACKER_DIR / "runs.csv"
log = logging.getLogger("tracker")


def append_for_day(path, rows, day):
    """Append today's rows, first removing any rows from an earlier run on the same day."""
    old = pd.read_csv(path) if path.exists() else pd.DataFrame()
    if not old.empty:
        old = old[old.iloc[:, 0].astype(str) != day]  # first column is always the date
    pd.concat([old, rows], ignore_index=True).to_csv(path, index=False)


def already_ok(day):
    if not RUNS_CSV.exists():
        return False
    runs = pd.read_csv(RUNS_CSV)
    today = runs[runs["date"].astype(str) == day]
    return not today.empty and (today["status"].isin(["ok", "warn"])).all()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--raw-dir", help="use saved raw JSON instead of scraping")
    parser.add_argument("--date", help="override the run date (YYYY-MM-DD), for testing")
    args = parser.parse_args()

    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        handlers=[logging.FileHandler("logs/tracker.log"), logging.StreamHandler()])
    logging.getLogger("httpx").setLevel(logging.WARNING)

    day = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if already_ok(day) and not args.force:
        log.info("Already ran successfully for %s; nothing to do.", day)
        return 0

    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    brands = [s["brand"] for s in fetch.load_stores()]
    raw_dir = Path(args.raw_dir) if args.raw_dir else fetch.run()

    # A cleaning crash usually means the JSON format changed: record it and fail loudly
    try:
        snap = to_snapshot(clean.run(raw_dir))
    except (Exception, SystemExit) as e:
        log.exception("Could not parse the scraped data")
        summary = pd.DataFrame([{"date": day, "brand": b, "compared_with": "", "rows_found": 0, "new": 0,
                                 "changed": 0, "removed": 0, "status": "parse_error"} for b in brands])
        append_for_day(RUNS_CSV, summary, day)
        write_report(day, summary, pd.DataFrame(), [f"Cleaning failed: {e!r}. The site's data format may have changed."])
        return 1

    prev = previous_by_brand(brands, before_day=day)

    rows, all_changes, problems = [], [], []
    for brand in brands:
        cur = snap[snap["brand"] == brand]
        prev_day, prev_rows = prev.get(brand, ("", None))
        changes = compare(prev_rows, cur, day) if prev_rows is not None and not cur.empty else pd.DataFrame()
        counts = changes["change"].value_counts() if not changes.empty else {}
        n_changed = int(counts.get("changed", 0))
        status, message = check_brand(brand, cur, prev_rows, n_changed)
        if status == "fail":
            changes = pd.DataFrame()  # don't log a broken scrape as thousands of "removed" rows
            counts = {}
        if message:
            problems.append(message)
            (log.error if status == "fail" else log.warning)(message)
        all_changes.append(changes)
        rows.append({"date": day, "brand": brand, "compared_with": prev_day or "first run",
                     "rows_found": len(cur), "new": int(counts.get("new", 0)), "changed": int(counts.get("changed", 0)),
                     "removed": int(counts.get("removed", 0)), "status": status})
        log.info("%s: %s rows, new=%s changed=%s removed=%s [%s]", brand, len(cur), rows[-1]["new"],
                 rows[-1]["changed"], rows[-1]["removed"], status)

    summary = pd.DataFrame(rows)
    # Only data that passed its checks goes into the snapshot, so tomorrow's run
    # compares a failed brand with its last good day instead of a broken scrape
    failed = summary.loc[summary["status"] == "fail", "brand"]
    save_snapshot(snap[~snap["brand"].isin(failed)], day)
    changes = pd.concat([c for c in all_changes if not c.empty] or [pd.DataFrame()], ignore_index=True)
    append_for_day(RUNS_CSV, summary, day)
    if not changes.empty or CHANGES_CSV.exists():
        append_for_day(CHANGES_CSV, changes, day)
    overall = write_report(day, summary, changes, problems)

    log.info("Run %s finished: %s rows, %s changes, status=%s", day, len(snap), len(changes), overall)
    return 1 if overall == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())