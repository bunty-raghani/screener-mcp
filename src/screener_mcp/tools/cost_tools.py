"""
Expense-structure analysis from Screener.in's public schedule API.

The P&L shows total expenses; the schedule endpoint splits them into material,
employee and other costs, and each of those into sub-items (raw materials,
traded goods, inventory change, advertisement, distribution, power/fuel, …).
That split is what explains *why* an operating margin moved.

  GET /api/company/{company_id}/schedules/?parent=Expenses&section=profit-loss
  GET /api/company/{company_id}/schedules/?parent=Material%20Cost%20%25&section=profit-loss

The response is {row label: {period: "12.3%", …, "tooltips": {period: "₹1,234 Cr"}}}.
"""

import asyncio
import re
from typing import Literal, Optional

from ..client import get_client
from ..core.company_page import fetch_company_page
from ..core.envelope import ToolError, ToolResult
from ..core.numbers import to_number
from ..parsers.company import parse_overview

Period = Literal["yearly", "quarterly"]

_SECTION = {"yearly": "profit-loss", "quarterly": "quarters"}
_BASIC_ROWS = ("Material Cost %", "Employee Cost %", "Other Expenses %")
_META_KEYS = {"tooltips", "isExpandable"}
_ABS_RE = re.compile(r"([\d,]+(?:\.\d+)?)")


def _periods(payload: dict) -> list[str]:
    """Column labels, in API order, excluding TTM and metadata keys."""
    for row in payload.values():
        if isinstance(row, dict):
            cols = [k for k in row if k not in _META_KEYS]
            if cols:
                return [c for c in cols if c.upper() != "TTM"]
    return []


def _ttm(payload: dict) -> dict:
    out = {}
    for label, row in payload.items():
        if isinstance(row, dict) and row.get("TTM"):
            out[label] = to_number(row["TTM"])
    return out


def _abs_value(row: dict, period: str) -> Optional[float]:
    tooltip = (row.get("tooltips") or {}).get(period, "")
    m = _ABS_RE.search(tooltip or "")
    return to_number(m.group(1)) if m else None


def _row(label: str, row: dict, periods: list[str], parent: str = "", level: int = 1) -> dict:
    pct = [to_number(row.get(p)) if row.get(p) not in (None, "") else None for p in periods]
    abs_cr = [_abs_value(row, p) for p in periods]
    return {
        "label": label.removesuffix(" %"),
        "parent": parent or None,
        "level": level,
        "values_pct_of_sales": pct,
        "values_cr": abs_cr,
    }


def _change_pp(row: dict, periods: list[str], period_type: Period) -> Optional[float]:
    vals = row["values_pct_of_sales"]
    if len(vals) < 2:
        return None
    latest = vals[-1]
    # Quarterly: compare with the same quarter a year earlier when possible.
    prior_idx = -5 if period_type == "quarterly" and len(vals) >= 5 else -2
    prior = vals[prior_idx]
    if latest is None or prior is None:
        return None
    return round(latest - prior, 2)


async def get_cost_structure(
    symbol: str,
    period: Period = "yearly",
    periods: int = 5,
    include_subitems: bool = True,
) -> ToolResult:
    """Material / employee / other expense mix, with sub-items and changes."""
    if period not in _SECTION:
        raise ToolError("period must be 'yearly' or 'quarterly'.", "invalid_input")
    periods = max(1, min(int(periods or 5), 12))

    page = await fetch_company_page(symbol, "consolidated")
    if not page.company_id:
        raise ToolError(f"Could not resolve a Screener company id for {page.symbol}.", "no_data")
    client = await get_client()
    section = _SECTION[period]

    async def fetch(parent: str):
        try:
            payload = await client.get_json(
                f"/api/company/{page.company_id}/schedules/",
                params={"parent": parent, "section": section},
            )
            return parent, payload if isinstance(payload, dict) else {}, None
        except Exception as e:
            return parent, {}, f"{parent}: {type(e).__name__}"

    parents = list(_BASIC_ROWS) if include_subitems else []
    fetched = await asyncio.gather(fetch("Expenses"), *[fetch(p) for p in parents])
    _, base, base_err = fetched[0]
    if not base:
        raise ToolError(
            f"Screener.in returned no expense schedule for {page.symbol} [{period}]. "
            "The company may report a different format, or the page may be temporarily unavailable.",
            "no_data",
        )
    all_periods = _periods(base)
    if not all_periods:
        raise ToolError(f"The expense schedule for {page.symbol} had no period columns.", "no_data")
    shown = all_periods[-periods:]
    ttm = _ttm(base)

    warnings = list(page.warnings) + ([base_err] if base_err else [])
    rows = [_row(label, base[label], shown) for label in _BASIC_ROWS if label in base]
    if not rows:
        raise ToolError(f"The expense schedule for {page.symbol} had no material/employee/other rows.",
                        "no_data")

    for parent, payload, err in fetched[1:]:
        if err:
            warnings.append(f"Sub-items for {parent} unavailable ({err}).")
            continue
        for label, row in payload.items():
            if label in _META_KEYS or not isinstance(row, dict):
                continue
            rows.append(_row(label, row, shown, parent=parent.removesuffix(" %"), level=2))

    basic = [r for r in rows if r["level"] == 1]
    latest_mix = {r["label"]: r["values_pct_of_sales"][-1] for r in basic}
    total = sum(v for v in latest_mix.values() if v is not None) if latest_mix else None
    changes = {
        r["label"]: _change_pp(r, shown, period)
        for r in basic if _change_pp(r, shown, period) is not None
    }
    largest_change = (max(changes.items(), key=lambda kv: abs(kv[1])) if changes else (None, None))

    summary_bits = []
    for label, value in latest_mix.items():
        if value is not None:
            summary_bits.append(f"{label} {value:.1f}% of sales")
    summary = f"Latest mix: {', '.join(summary_bits)}."
    if largest_change[0]:
        summary += (f" Biggest change: {largest_change[0]} {largest_change[1]:+.1f} pp "
                    f"vs {'the same quarter last year' if period == 'quarterly' else 'the prior year'}.")

    return ToolResult(
        data={
            "symbol": page.symbol,
            "name": parse_overview(page.html).get("name"),
            "period": period,
            "periods": shown,
            "rows": rows,
            "latest_mix_pct_of_sales": latest_mix,
            "latest_expense_ratio_total_pct": round(total, 2) if total is not None else None,
            "changes_pp": changes,
            "summary": summary,
            "ttm_pct_of_sales": ttm or None,
            "source": f"Screener.in expense schedule ({section})",
            "notes": [
                "Percentages are of sales, as Screener reports them. Sub-items can overlap or leave an "
                "'other' residual, so they need not add up to the parent.",
                "'Other Expenses' is the catch-all; check its sub-items before attributing a margin move "
                "to operating leverage.",
            ],
        },
        warnings=warnings,
        meta=page.meta,
    )
