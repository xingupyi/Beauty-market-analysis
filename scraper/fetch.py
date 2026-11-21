"""Fetch public Shopify catalogs (/products.json) for each brand in stores.csv."""
import csv
import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.robotparser import RobotFileParser

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

USER_AGENT = (
    "beauty-market-snapshot/1.0 (student portfolio project; "
    "contact: your.email@example.com)"
)
PAGE_SIZE = 250          # Shopify's maximum per page
MAX_PAGES = 40           # safety cap: 40 x 250 = 10,000 products per brand
DELAY_RANGE = (1.0, 3.0)  # seconds to wait between requests


def load_stores(path="stores.csv"):
    #Read the vetted brand list. Only rows with include=yes are scraped
    with open(path, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row["include"].strip().lower() == "yes"]


def polite_pause():
    time.sleep(random.uniform(*DELAY_RANGE))


def is_retryable(exc):
    #Retry on network errors, 429 (rate limited) and 5xx (server errors) only
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


@retry(
    retry=retry_if_exception(is_retryable),
    wait=wait_exponential(multiplier=2, min=2, max=60),  #if server slows down give exponential backoff
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
def get(client, url, params=None):
    resp = client.get(url, params=params)
    resp.raise_for_status()
    return resp

#check path is allowed from robots.txt
def robots_allows(client, domain, path="/products.json"):
    url = f"https://{domain}/robots.txt"
    try:
        resp = get(client, url)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            log.info("%s has no robots.txt; treating as allowed", domain)
            return True
        raise
    rp = RobotFileParser()
    rp.parse(resp.text.splitlines())
    return rp.can_fetch(USER_AGENT, f"https://{domain}{path}")


def scrape_store(client, store, out_dir):
    #Page through one brand's catalog and save each page's raw JSON
    brand, domain = store["brand"], store["domain"]

    if not robots_allows(client, domain):
        log.warning("SKIP %s: robots.txt disallows /products.json", brand)
        return 0
    polite_pause()

    total = 0
    for page in range(1, MAX_PAGES + 1):
        url = f"https://{domain}/products.json"
        try:
            resp = get(client, url, params={"limit": PAGE_SIZE, "page": page})
        except httpx.HTTPStatusError as e:
            log.warning("SKIP %s: HTTP %s on page %s", brand, e.response.status_code, page)
            break

        try:
            products = resp.json().get("products", [])
        except ValueError:
            log.warning("SKIP %s: page %s was not JSON (feed probably disabled)", brand, page)
            break

        if not products:  # an empty page means we have reached the end
            break

        out_file = out_dir / f"{store['slug']}_p{page:03d}.json"
        out_file.write_text(json.dumps(products), encoding="utf-8")
        total += len(products)
        log.info("%s page %s: %s products", brand, page, len(products))
        polite_pause()

    log.info("DONE %s: %s products", brand, total)
    return total


def run(stores_csv="stores.csv", raw_root="data/raw"):
    #Scrape every included store into data/raw/<UTC timestamp>/. Returns that folder.
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out_dir = Path(raw_root) / scraped_at
    out_dir.mkdir(parents=True, exist_ok=True)

    stores = load_stores(stores_csv)
    log.info("Scraping %s stores into %s", len(stores), out_dir)

    summary = {}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(headers=headers, timeout=30, follow_redirects=True) as client:
        for store in stores:
            try:
                summary[store["brand"]] = scrape_store(client, store, out_dir)
            except Exception:  # one broken store should never kill the whole run
                log.exception("FAILED %s", store["brand"])
                summary[store["brand"]] = 0

    (out_dir / "_summary.json").write_text(json.dumps(summary, indent=2))
    return out_dir