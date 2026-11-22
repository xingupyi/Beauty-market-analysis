"""Write reports/CHANGES.md: what changed since the previous run."""
from pathlib import Path

import pandas as pd

REPORT_PATH = Path("reports/CHANGES.md")
ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌", "parse_error": "❌"}


def _money(x):
    return "" if pd.isna(x) else f"${x:,.2f}"


def _price_table(rows, title):
    if rows.empty:
        return []
    lines = [f"### {title}", "", "| Brand | Product | Variant | Old | New | Change |", "| --- | --- | --- | --- | --- | --- |"]
    for r in rows.itertuples():
        lines.append(f"| {r.brand} | {r.product_title} | {r.variant_title} | {_money(r.price_old)} "
                     f"| {_money(r.price_new)} | {r.pct:+.0%} |")
    return lines + [""]


def write_report(day, summary, changes, problems):
    """summary: DataFrame with one row per brand. changes: today's change rows."""
    overall = "fail" if any(s in ("fail", "parse_error") for s in summary["status"]) else "ok"
    lines = [
        "# What changed since the last run",
        "",
        f"**Run date (UTC):** {day} · **Status:** {ICON[overall]} {overall}",
        "",
        "| Brand | Compared with | Rows found | New | Changed | Removed | Status |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in summary.itertuples():
        lines.append(f"| {r.brand} | {r.compared_with} | {r.rows_found} | {r.new} | {r.changed} "
                     f"| {r.removed} | {ICON.get(r.status, '')} {r.status} |")
    lines.append("")

    if problems:
        lines += ["## Problems", ""] + [f"- {p}" for p in problems] + [""]

    if not changes.empty:
        priced = changes[(changes["change"] == "changed") & changes["price_old"].notna()
                         & changes["price_new"].notna() & (changes["price_old"] != changes["price_new"])].copy()
        priced["pct"] = priced["price_new"] / priced["price_old"] - 1
        lines += ["## Price changes", ""]
        lines += _price_table(priced[priced["pct"] < 0].nsmallest(10, "pct"), "Biggest price drops")
        lines += _price_table(priced[priced["pct"] > 0].nlargest(10, "pct"), "Biggest price increases")
        if priced.empty:
            lines += ["No price changes.", ""]

        stock = changes[(changes["change"] == "changed")
                        & (changes["available_old"].astype("boolean") != changes["available_new"].astype("boolean"))]
        back = int((stock["available_new"].astype("boolean") == True).sum())  # noqa: E712
        out = int((stock["available_new"].astype("boolean") == False).sum())  # noqa: E712
        lines += ["## Stock", "", f"- Back in stock: {back}", f"- Went out of stock: {out}", ""]

        new = changes[changes["change"] == "new"]
        if not new.empty:
            lines += [f"## New variants ({len(new)})", ""]
            lines += [f"- {r.brand}: {r.product_title} ({r.variant_title})" for r in new.head(15).itertuples()]
            if len(new) > 15:
                lines.append(f"- …and {len(new) - 15} more (see data/tracker/changes.csv)")
            lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return overall