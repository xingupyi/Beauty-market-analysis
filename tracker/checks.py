"""Health checks: turn "the site changed" into a loud, red, failed run."""

MAX_DROP = 0.5            # fail if a brand returns under half of its previous row count
MAX_MISSING_PRICE = 0.01  # fail if more than 1% of a brand's rows have no price
WARN_CHANGED = 0.5        # warn (don't fail) if over half of a brand's rows changed


def check_brand(brand, cur_rows, prev_rows, n_changed):
    """Return (status, message). status is 'ok', 'warn' or 'fail'."""
    n = len(cur_rows)
    if n == 0:
        return "fail", f"{brand}: 0 rows. Feed empty, blocked or removed."
    missing_price = cur_rows["price"].isna().mean()
    if missing_price > MAX_MISSING_PRICE:
        return "fail", f"{brand}: {missing_price:.0%} of rows have no price. Data format may have changed."
    if prev_rows is not None and n < MAX_DROP * len(prev_rows):
        return "fail", f"{brand}: {n} rows, down from {len(prev_rows)}. Possible partial scrape or site change."
    if prev_rows is not None and n and n_changed / n > WARN_CHANGED:
        return "warn", f"{brand}: {n_changed / n:.0%} of rows changed. Likely a sale; check the report."
    return "ok", ""