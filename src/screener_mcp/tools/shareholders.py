"""
NSE bulk deals — by company, by investor/entity name, or both.
"""

import logging
from datetime import datetime, timedelta

from ..core.company_page import resolve_nse_symbol
from ..core.envelope import ToolError, ToolResult
from ..core.nse_client import get_nse_client

logger = logging.getLogger(__name__)


def _deal_row(d: dict, with_symbol: bool) -> dict:
    row = {
        "date": d.get("date") or None,
        "buy_sell": d.get("buySell") or None,
        "quantity": d.get("quantityTraded") or None,
        "price": d.get("tradePrice") or None,
        "client": d.get("clientName") or None,
    }
    if with_symbol:
        row = {"symbol": d.get("symbol") or None, **row}
    return row


def _coverage(days: int, days_queried: int, failed: list[str]) -> tuple[list[str], bool, str | None]:
    warnings, partial, reason = [], False, None
    if days > days_queried:
        warnings.append(
            f"NSE's bulk-deals endpoint serves one day per request, so only the most recent "
            f"{days_queried} of the requested {days} days were searched."
        )
    if failed:
        partial = True
        reason = (f"NSE requests failed for {len(failed)} of {days_queried} days "
                  f"({', '.join(failed[:5])}{' …' if len(failed) > 5 else ''}) — deals on those days are missing.")
    return warnings, partial, reason


async def get_bulk_deals(symbol: str = "", name: str = "", days: int = 30,
                         deal_type: str = "bulk") -> ToolResult:
    """
    NSE bulk or block deals, filtered by company, by investor name, or both.

    symbol: restrict to one company's deals
    name: partial client name to match ("Jhunjhunwala", "SBI Mutual Fund")
    deal_type: "bulk" (trades > 0.5% of equity) or "block" (single negotiated
               trades reported by the exchange)
    """
    symbol, name = (symbol or "").strip(), (name or "").strip()
    if not symbol and not name:
        raise ToolError("Pass a `symbol` (one company's deals), a `name` (one investor's deals), or both.",
                        "invalid_input")
    if deal_type not in ("bulk", "block"):
        raise ToolError("deal_type must be 'bulk' or 'block'.", "invalid_input")
    days = max(1, min(int(days or 30), 365))
    option_type = "block_deals" if deal_type == "block" else "bulk_deals"

    warnings, meta, nse_symbol = [], {}, None
    if symbol:
        nse_symbol, warnings, meta = await resolve_nse_symbol(symbol)

    nse = await get_nse_client()
    to_date = datetime.now().strftime("%d-%m-%Y")
    from_date = (datetime.now() - timedelta(days=days)).strftime("%d-%m-%Y")
    deals, failed, days_queried = await nse.get_bulk_deals(
        from_date, to_date, symbol=nse_symbol, option_type=option_type)

    searched = len(deals)
    if name:
        needle = name.lower()
        deals = [d for d in deals if needle in str(d.get("clientName", "")).lower()]
    cov_warnings, partial, reason = _coverage(days, days_queried, failed)
    warnings += cov_warnings
    if not deals:
        scope = " ".join(filter(None, [f"by '{name}'" if name else "",
                                       f"in {nse_symbol}" if nse_symbol else "across NSE"]))
        warnings.append(
            f"No {deal_type} deals {scope} among {searched} deal(s) on the {days_queried} day(s) searched. "
            "The NSE feed responded, so this is a real empty result — use get_shareholding_pattern for "
            "gradual accumulation, or widen `days`."
        )
    return ToolResult(
        data={
            "symbol": nse_symbol,
            "name": name or None,
            "deal_type": deal_type,
            "period": {"from": from_date, "to": to_date, "days_searched": days_queried},
            "deals_searched": searched,
            "count": len(deals),
            "deals": [_deal_row(d, with_symbol=not nse_symbol) for d in deals[:100]],
        },
        warnings=warnings,
        partial=partial,
        reason=reason,
        meta=meta,
    )
