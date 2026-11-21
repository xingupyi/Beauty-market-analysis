"""Load clean data into SQLite. Each run adds a new snapshot; old ones are kept."""
import logging
import sqlite3

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at  TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS brands (
    brand  TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    tier   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS variants (
    snapshot_id      INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
    brand            TEXT NOT NULL REFERENCES brands(brand),
    product_id       INTEGER NOT NULL,
    variant_id       INTEGER NOT NULL,
    product_title    TEXT,
    variant_title    TEXT,
    product_type     TEXT,
    category         TEXT,
    sku              TEXT,
    price            REAL NOT NULL,
    compare_at_price REAL,
    on_sale          INTEGER NOT NULL,
    discount_pct     REAL,
    size_value       REAL,
    size_unit        TEXT,
    size_source      TEXT,
    unit_price       REAL,
    available        INTEGER NOT NULL,
    url              TEXT,
    PRIMARY KEY (snapshot_id, brand, variant_id)
);
CREATE INDEX IF NOT EXISTS idx_variants_brand_cat ON variants(brand, category);
"""

VARIANT_COLS = [
    "brand", "product_id", "variant_id", "product_title", "variant_title", "product_type",
    "category", "sku", "price", "compare_at_price", "on_sale", "discount_pct",
    "size_value", "size_unit", "size_source", "unit_price", "available", "url",
]


def run(df, stores, db_path="data/market.db"):
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        scraped_at = df["scraped_at"].iloc[0]

        # Re-running on the same raw folder replaces that snapshot instead of duplicating it
        con.execute("DELETE FROM variants WHERE snapshot_id = "
                    "(SELECT snapshot_id FROM snapshots WHERE scraped_at = ?)", (scraped_at,))
        con.execute("INSERT OR IGNORE INTO snapshots(scraped_at) VALUES (?)", (scraped_at,))
        snapshot_id = con.execute(
            "SELECT snapshot_id FROM snapshots WHERE scraped_at = ?", (scraped_at,)
        ).fetchone()[0]

        con.executemany(
            "INSERT OR REPLACE INTO brands(brand, domain, tier) VALUES (?, ?, ?)",
            [(s["brand"], s["domain"], s["tier"]) for s in stores],
        )

        out = df[VARIANT_COLS].astype(object).where(df[VARIANT_COLS].notna(), None)
        rows = [(snapshot_id, *r) for r in out.itertuples(index=False, name=None)]
        placeholders = ", ".join(["?"] * (len(VARIANT_COLS) + 1))
        con.executemany(
            f"INSERT INTO variants(snapshot_id, {', '.join(VARIANT_COLS)}) VALUES ({placeholders})",
            rows,
        )
        con.commit()
        log.info("Saved %s variants as snapshot %s in %s", len(rows), snapshot_id, db_path)
    finally:
        con.close()