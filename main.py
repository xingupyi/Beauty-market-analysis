"""One command to build the dataset: uv run main.py"""
import argparse
import logging
from pathlib import Path

from scraper import clean, fetch, store


def setup_logging():
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.FileHandler("logs/scrape.log"), logging.StreamHandler()],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # hide one line per request


def latest_raw_dir(raw_root="data/raw"):
    dirs = sorted(p for p in Path(raw_root).iterdir() if p.is_dir())
    if not dirs:
        raise SystemExit("No raw snapshots yet. Run without --skip-scrape first.")
    return dirs[-1]


def main():
    parser = argparse.ArgumentParser(description="Build the beauty pricing dataset.")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="re-clean the latest raw snapshot without hitting any site")
    args = parser.parse_args()

    setup_logging()
    Path("data").mkdir(exist_ok=True)
    raw_dir = latest_raw_dir() if args.skip_scrape else fetch.run()
    df = clean.run(raw_dir)
    store.run(df, fetch.load_stores())
    df.to_csv("data/variants_latest.csv", index=False)
    logging.info("Finished: %s variants from %s brands", len(df), df["brand"].nunique())


if __name__ == "__main__":
    main()