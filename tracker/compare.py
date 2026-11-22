#Daily snapshots and the comparison with the previous day
from pathlib import Path

import pandas as pd

SNAPSHOT_DIR = Path("data/tracker/snapshots")
KEY = ["brand", "variant_id"]
SNAPSHOT_COLS = [
    "brand", "product_id", "variant_id", "product_title", "variant_title", "category",
    "price", "compare_at_price", "available", "size_value", "size_unit",
]
TRACKED = ["price", "compare_at_price", "available"]  # a change in any of these = "changed"


def to_snapshot(df):
    """Keep only the columns worth tracking, in a stable order (small files, clean git diffs)."""
    return df[SNAPSHOT_COLS].sort_values(KEY).reset_index(drop=True)


def save_snapshot(snap, day):
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"{day}.csv.gz"
    # mtime=0 makes the gzip bytes identical when the content is identical
    snap.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
    return path


def load_snapshot(path):
    return pd.read_csv(path, dtype={"variant_id": "int64", "product_id": "int64"})


def previous_by_brand(brands, before_day, max_days_back=14):
    """For each brand, the most recent earlier snapshot that has data for it.

    Looking back per brand means a brand that failed yesterday is compared with
    its last good day, instead of every product looking "new".
    """
    files = sorted(p for p in SNAPSHOT_DIR.glob("*.csv.gz") if p.name[:10] < before_day)
    found = {}
    for path in reversed(files[-max_days_back:]):
        missing = [b for b in brands if b not in found]
        if not missing:
            break
        snap = load_snapshot(path)
        for brand in missing:
            rows = snap[snap["brand"] == brand]
            if not rows.empty:
                found[brand] = (path.name[:10], rows)
    return found


def _same(a, b):
    """NaN==NaN is false in pandas >> here its equal/treating two missing values as equal."""
    return (a == b) | (a.isna() & b.isna())


def compare(prev, cur, day):
    """Return one row per new, removed or changed variant between two snapshots of one brand."""
    m = prev.merge(cur, on=KEY, how="outer", suffixes=("_old", "_new"), indicator=True)
    m["change"] = m["_merge"].map({"right_only": "new", "left_only": "removed", "both": None})

    both = m["_merge"] == "both"
    differs = pd.Series(False, index=m.index)
    for col in TRACKED:
        differs |= ~_same(m[f"{col}_old"], m[f"{col}_new"])
    m.loc[both & differs, "change"] = "changed"

    out = m[m["change"].notna()].copy()
    for col in ["product_title", "variant_title"]:
        out[col] = out[f"{col}_new"].fillna(out[f"{col}_old"])
    out.insert(0, "date", day)
    cols = ["date", "brand", "variant_id", "product_title", "variant_title", "change"]
    cols += [f"{c}_{s}" for c in TRACKED for s in ("old", "new")]
    return out[cols].reset_index(drop=True)