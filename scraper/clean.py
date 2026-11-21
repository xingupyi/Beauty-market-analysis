"""Turn raw Shopify JSON pages into one tidy, variant-level DataFrame."""
import csv
import json
import logging
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

FL_OZ_TO_ML = 29.5735
OZ_TO_G = 28.3495

# Matches "30 ml", "1.7 fl oz", "3.5g", "0.5 oz" and similar
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(fl\.?\s*oz|ml|g|oz)\b", re.IGNORECASE)

# First matching rule wins, so put the more specific categories first
CATEGORY_RULES = [
    ("lips", ["lip", "gloss"]),
    ("eyes", ["mascara", "eyeliner", "liner", "brow", "eyeshadow", "shadow", "lash"]),
    ("face", ["foundation", "concealer", "blush", "bronzer", "highlight", "powder",
              "primer", "setting", "tint", "contour"]),
    ("skincare", ["serum", "moistur", "cleanser", "toner", "spf", "sunscreen",
                  "cream", "mask", "exfol", "essence", "skin"]),
    ("body", ["body", "hand", "lotion", "scrub", "deodorant"]),
    ("hair", ["hair", "shampoo", "conditioner", "scalp"]),
    ("fragrance", ["perfume", "fragrance", "parfum", "eau de", "mist"]),
    ("tools", ["brush", "sponge", "tool", "applicator", "bag"]),
]
EXCLUDE_WORDS = ["gift card", "e-gift", "giftcard", "shipping protection", "route protection"]


TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(html):
    """Strip HTML tags from a product description so the size regex can read it."""
    return " ".join(TAG_RE.sub(" ", html or "").split())


def parse_size(*texts):
    """Return (size, unit) in ml or g from the first text that mentions a size.

    Labels often give both units ("0.11 fl oz / 3.2 mL"), so a metric number is
    preferred over a converted oz number when both appear in the same text.
    """
    for text in texts:
        if not text:
            continue
        matches = list(SIZE_RE.finditer(text))
        if not matches:
            continue
        metric = [m for m in matches if m.group(2).lower() in ("ml", "g")]
        m = (metric or matches)[0]
        value, unit = float(m.group(1)), m.group(2).lower().replace(".", "").replace(" ", "")
        if unit == "ml":
            return value, "ml"
        if unit == "floz":
            return round(value * FL_OZ_TO_ML, 2), "ml"
        if unit == "g":
            return value, "g"
        if unit == "oz":  # plain oz is weight on most beauty labels
            return round(value * OZ_TO_G, 2), "g"
    return None, None


def categorise(product_type, title, tags):
    text = " ".join([product_type or "", title or "", " ".join(tags or [])]).lower()
    for category, words in CATEGORY_RULES:
        if any(w in text for w in words):
            return category
    return "other"


def flatten(raw_dir, stores):
    """One row per variant, straight from the JSON with no cleaning yet."""
    rows = []
    for path in sorted(Path(raw_dir).glob("*_p*.json")):
        slug = path.stem.rsplit("_p", 1)[0]
        store = stores[slug]
        for p in json.loads(path.read_text(encoding="utf-8")):
            tags = p.get("tags") or []
            if isinstance(tags, str):  # older stores return tags as one string
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            for v in p.get("variants", []):
                rows.append({
                    "brand": store["brand"],
                    "tier": store["tier"],
                    "product_id": p["id"],
                    "product_title": p.get("title"),
                    "product_type": p.get("product_type"),
                    "tags": "|".join(tags),
                    "url": f"https://{store['domain']}/products/{p.get('handle')}",
                    "variant_id": v["id"],
                    "variant_title": v.get("title"),
                    "sku": v.get("sku"),
                    "price": v.get("price"),
                    "compare_at_price": v.get("compare_at_price"),
                    "available": v.get("available"),
                    "body_text": html_to_text(p.get("body_html")),
                    "published_at": p.get("published_at"),
                })
    return pd.DataFrame(rows)


def clean(df, scraped_at):
    """Fix types, drop junk and add the analysis columns."""
    n_start = len(df)

    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["compare_at_price"] = pd.to_numeric(df["compare_at_price"], errors="coerce")
    df["available"] = df["available"].fillna(False).astype(bool)
    df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce", utc=True)

    # Drop rows that are not real products
    text = (df["product_title"].fillna("") + " " + df["product_type"].fillna("")).str.lower()
    junk = text.str.contains("|".join(EXCLUDE_WORDS), regex=True)
    df = df[~junk & df["price"].gt(0)].copy()

    df = df.drop_duplicates(subset=["brand", "variant_id"])

    # Size and unit price
    # Look for a size in the variant title first, then the product title, then the description
    sizes, sources = [], []
    for vt, pt, body in zip(df["variant_title"], df["product_title"], df["body_text"]):
        for source, text in (("variant_title", vt), ("product_title", pt), ("description", body)):
            value, unit = parse_size(text)
            if value is not None:
                sizes.append((value, unit))
                sources.append(source)
                break
        else:
            sizes.append((None, None))
            sources.append(None)
    df["size_value"] = [s[0] for s in sizes]
    df["size_unit"] = [s[1] for s in sizes]
    df["size_source"] = sources
    df = df.drop(columns="body_text")
    df["unit_price"] = (df["price"] / df["size_value"]).round(3)  # price per ml or per g

    # Discounts: only count it as a sale when compare_at is higher than price
    on_sale = df["compare_at_price"].gt(df["price"])
    df["on_sale"] = on_sale
    df["discount_pct"] = ((1 - df["price"] / df["compare_at_price"]) * 100).where(on_sale).round(1)

    df["category"] = [
        categorise(pt, t, tg.split("|"))
        for pt, t, tg in zip(df["product_type"], df["product_title"], df["tags"])
    ]
    df["scraped_at"] = scraped_at

    log.info("Cleaned %s raw variant rows down to %s", n_start, len(df))
    log.info("Size parsed for %.0f%% of rows", df["size_value"].notna().mean() * 100)
    return df.reset_index(drop=True)


def run(raw_dir, stores_csv="stores.csv"):
    with open(stores_csv, newline="", encoding="utf-8") as f:
        stores = {row["slug"]: row for row in csv.DictReader(f)}
    scraped_at = Path(raw_dir).name
    df = flatten(raw_dir, stores)
    if df.empty:
        raise SystemExit(f"No raw pages found in {raw_dir}; did the scrape fail?")
    return clean(df, scraped_at)