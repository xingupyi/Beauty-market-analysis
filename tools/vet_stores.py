"""Vet candidate brands before they go into stores.csv.
Reads tools/candidates.csv (brand,domain) and writes tools/vetting_results.csv.
"""
import argparse
import csv
import json
import logging
from datetime import date
from urllib.parse import quote_plus

import httpx

from scraper.fetch import USER_AGENT, get, polite_pause, robots_allows

log = logging.getLogger("vet")

# Text or headers that give away which e-commerce platform a site runs on
PLATFORM_SIGNS = {
    "shopify": ["cdn.shopify.com", "shopify.theme", "x-shopify-stage"],
    "woocommerce": ["woocommerce", "wp-content/plugins/woocommerce"],
    "salesforce_commerce": ["demandware"],
    "bigcommerce": ["bigcommerce.com"],
    "magento": ["mage/cookies", "magento"],
}

# Known public product endpoints, by platform
ENDPOINTS = {
    "shopify_feed": "/products.json?limit=1",
    "woo_store_api": "/wp-json/wc/store/v1/products?per_page=1",
}


def guess_platform(client, domain):
    """Load the homepage once and look for platform fingerprints."""
    try:
        resp = get(client, f"https://{domain}/")
    except httpx.HTTPStatusError as e:
        return f"blocked_{e.response.status_code}"
    except httpx.TransportError:
        return "unreachable"
    haystack = (resp.text[:300_000] + json.dumps(dict(resp.headers))).lower()
    found = [name for name, signs in PLATFORM_SIGNS.items() if any(s in haystack for s in signs)]
    return "+".join(found) or "unknown"


def probe(client, domain, path):
    """Return 'yes' if the endpoint answers with product JSON, else a short reason."""
    try:
        resp = get(client, f"https://{domain}{path}")
    except httpx.HTTPStatusError as e:
        return f"no_{e.response.status_code}"
    except httpx.TransportError:
        return "no_unreachable"
    try:
        data = resp.json()
    except ValueError:
        return "no_not_json"
    products = data.get("products") if isinstance(data, dict) else data
    return "yes" if isinstance(products, list) and products else "no_empty"


def ask_sephora(brand):
    url = f"https://www.sephora.com/search?keyword={quote_plus(brand)}"
    print(f"\n{brand}: open {url}")
    answer = input(" Sold at Sephora? [y/n/?] ").strip().lower()
    return url, {"y": "yes", "n": "no"}.get(answer, "unsure")


def main():
    parser = argparse.ArgumentParser(description="Vet candidate brands for stores.csv")
    parser.add_argument("--input", default="tools/candidates.csv")
    parser.add_argument("--output", default="tools/vetting_results.csv")
    parser.add_argument("--no-prompt", action="store_true", help="skip the Sephora questions")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    with open(args.input, newline="", encoding="utf-8") as f:
        candidates = list(csv.DictReader(f))

    results = []
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=20, follow_redirects=True) as client:
        for c in candidates:
            brand, domain = c["brand"].strip(), c["domain"].strip()
            row = {"brand": brand, "domain": domain, "checked_on": date.today().isoformat()}

            row["platform_guess"] = guess_platform(client, domain)
            polite_pause()
            for name, path in ENDPOINTS.items():
                row[name] = probe(client, domain, path)
                polite_pause()

            # Check robots.txt for whichever feed actually works
            feed_path = next((ENDPOINTS[n].split("?")[0] for n in ENDPOINTS if row[n] == "yes"), None)
            if feed_path is None:
                row["robots_allows_feed"] = "n/a"
            else:
                try:
                    row["robots_allows_feed"] = "yes" if robots_allows(client, domain, feed_path) else "no"
                except Exception:
                    row["robots_allows_feed"] = "unknown"
                polite_pause()

            if args.no_prompt:
                row["sephora_search"] = f"https://www.sephora.com/search?keyword={quote_plus(brand)}"
                row["sold_at_sephora"] = "not_checked"
            else:
                row["sephora_search"], row["sold_at_sephora"] = ask_sephora(brand)

            # The main scraper only reads Shopify feeds, so only those count for now
            shopify_ok = row["shopify_feed"] == "yes" and row["robots_allows_feed"] == "yes"
            row["suggested_include"] = "yes" if shopify_ok else "no"
            row["terms_note"] = "TODO: read terms yourself"
            log.info("%s: platform=%s shopify_feed=%s robots=%s", brand, row["platform_guess"],
                     row["shopify_feed"], row["robots_allows_feed"])
            results.append(row)

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"\nWrote {len(results)} rows to {args.output}. Review it, read each site's terms, "
          "then copy the brands that pass into stores.csv.")


if __name__ == "__main__":
    main()