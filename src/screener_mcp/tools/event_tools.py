"""
Corporate events from NSE's public feeds:

  get_corporate_actions   — dividends, splits, bonus, rights, buybacks, demergers
  get_earnings_calendar   — upcoming board meetings / results / AGMs
  search_announcements    — market-wide announcement search by keyword/category

Screener.in gates its market-wide announcements, corporate actions and
shareholder-search pages behind a premium account; NSE serves the underlying
events publicly, so these tools use NSE and keep working on a free Screener
login.
"""

from datetime import date, datetime, timedelta
from typing import Optional

from ..core.company_page import resolve_nse_symbol
from ..core.envelope import ToolError, ToolResult
from ..core.event_categories import categories as announcement_categories
from ..core.event_categories import categorize, materiality
from ..core.nse_client import get_nse_client

_DATE_FORMATS = ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y")

_ACTION_TYPES = {
    "dividend": ("dividend",),
    "split": ("split", "sub-division", "subdivision", "face value split"),
    "bonus": ("bonus",),
    "rights": ("rights",),
    "buyback": ("buyback", "buy-back", "repurchase"),
    "demerger": ("demerger", "amalgamation", "scheme of arrangement"),
}

_MATERIALITY_RANK = {"low": 1, "medium": 2, "high": 3}


def _parse_date(raw: str) -> Optional[date]:
    text = (raw or "").strip()
    if not text or text == "-":
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _action_types(subject: str) -> list[str]:
    text = (subject or "").lower()
    found = [name for name, keys in _ACTION_TYPES.items() if any(k in text for k in keys)]
    return found or ["other"]


def _action_row(item: dict) -> dict:
    subject = item.get("subject", "")
    ex = _parse_date(item.get("exDate", ""))
    rec = _parse_date(item.get("recDate", ""))
    event_date = ex or rec
    return {
        "symbol": item.get("symbol"),
        "company": item.get("comp"),
        "types": _action_types(subject),
        "subject": subject,
        "ex_date": ex.isoformat() if ex else None,
        "record_date": rec.isoformat() if rec else None,
        "book_closure_start": item.get("bcStartDate") if item.get("bcStartDate") != "-" else None,
        "book_closure_end": item.get("bcEndDate") if item.get("bcEndDate") != "-" else None,
        "series": item.get("series"),
        "face_value": item.get("faceVal"),
        "_date": event_date,
    }


def _in_window(d: Optional[date], start: date, end: date) -> bool:
    return d is not None and start <= d <= end


async def get_corporate_actions(
    symbol: str = "",
    days: int = 365,
    action_type: str = "all",
    symbols: str = "",
) -> ToolResult:
    """Dividends, splits, bonus, rights, buybacks and demergers from NSE."""
    days = max(1, min(int(days or 365), 730))
    valid = {"all", *_ACTION_TYPES, "other"}
    if action_type not in valid:
        raise ToolError(f"action_type must be one of: {', '.join(sorted(valid))}.", "invalid_input")

    today = date.today()
    start = today - timedelta(days=days)
    end = today + timedelta(days=45)  # include announced upcoming ex-dates
    nse = await get_nse_client()
    raw = await nse.get_corporate_actions(start.strftime("%d-%m-%Y"), end.strftime("%d-%m-%Y"))

    warnings: list[str] = []
    meta: dict = {}
    nse_symbol = ""
    wanted: set[str] = set()
    if symbol.strip():
        nse_symbol, warnings, meta = await resolve_nse_symbol(symbol)
        wanted = {nse_symbol}
    elif symbols.strip():
        requested = [s.strip() for s in symbols.split(",") if s.strip()]
        if len(requested) > 30:
            raise ToolError("Pass at most 30 symbols to get_corporate_actions.", "invalid_input")
        available = {(item.get("symbol") or "").upper() for item in raw}
        wanted, unresolved = await _resolve_symbol_set(requested, available)
        if unresolved:
            warnings.append(f"Could not resolve: {', '.join(unresolved)}.")

    rows = []
    for item in raw:
        if wanted and (item.get("symbol") or "").upper() not in wanted:
            continue
        row = _action_row(item)
        if not _in_window(row["_date"], start, end):
            continue
        if action_type != "all" and action_type not in row["types"]:
            continue
        rows.append(row)

    rows.sort(key=lambda r: (0 if r["_date"] >= today else 1,
                             r["_date"].toordinal() if r["_date"] >= today else -r["_date"].toordinal()))
    upcoming = [r for r in rows if r["_date"] >= today]
    counts: dict[str, int] = {}
    for r in rows:
        for t in r["types"]:
            counts[t] = counts.get(t, 0) + 1
    if not rows:
        scope = f"for {', '.join(sorted(wanted))}" if wanted else "across NSE"
        warnings.append(
            f"No corporate actions {scope} in the window {start} → {end}. NSE returned {len(raw)} actions "
            "market-wide, so this is a real empty result for that filter."
        )
    limit = 100 if not wanted else 200
    if len(rows) > limit:
        warnings.append(f"Showing the first {limit} of {len(rows)} actions — narrow with `action_type` or `days`.")
    for r in rows:
        r.pop("_date", None)
        r["upcoming"] = r["ex_date"] is not None and date.fromisoformat(r["ex_date"]) >= today
    return ToolResult(
        data={
            "symbol": nse_symbol or None,
            "symbols": sorted(wanted) if wanted and not nse_symbol else None,
            "window": {"from": start.isoformat(), "to": end.isoformat(), "lookback_days": days,
                       "includes_upcoming_days": 45},
            "action_type": action_type,
            "count": len(rows),
            "counts_by_type": counts,
            "next_upcoming": upcoming[:5],
            "actions": rows[:limit],
            "source": "NSE corporate actions (equities)",
        },
        warnings=warnings,
        meta=meta,
    )


async def _resolve_symbol_set(requested: list[str], available: set[str]) -> tuple[set[str], list[str]]:
    """Resolve names to NSE symbols, avoiding network work when the input is
    already a symbol present in the feed."""
    out, unresolved = set(), []
    for raw in requested:
        value = (raw or "").strip()
        if not value:
            continue
        upper = value.upper()
        if upper in available:
            out.add(upper)
            continue
        try:
            nse_symbol, _, _ = await resolve_nse_symbol(value)
            out.add(nse_symbol)
        except Exception:
            out.add(upper)
            unresolved.append(value)
    return out, unresolved


async def get_earnings_calendar(
    symbols: str = "",
    days: int = 30,
    purpose: str = "results",
) -> ToolResult:
    """Upcoming board meetings, results, AGMs and dividend dates from NSE."""
    days = max(1, min(int(days or 30), 180))
    purpose = (purpose or "results").strip().lower()
    if purpose not in ("all", "results", "board_meeting", "agm", "dividend"):
        raise ToolError("purpose must be all, results, board_meeting, agm or dividend.", "invalid_input")

    requested = [s.strip() for s in (symbols or "").split(",") if s.strip()]
    if len(requested) > 30:
        raise ToolError("Pass at most 30 symbols to get_earnings_calendar.", "invalid_input")

    nse = await get_nse_client()
    raw = await nse.get_event_calendar()
    today = date.today()
    end = today + timedelta(days=days)
    available = {(item.get("symbol") or "").upper() for item in raw}

    warnings: list[str] = []
    wanted: set[str] = set()
    if requested:
        wanted, unresolved = await _resolve_symbol_set(requested, available)
        if unresolved:
            warnings.append(f"Could not resolve: {', '.join(unresolved)}.")

    rows = []
    for item in raw:
        d = _parse_date(item.get("date", ""))
        if not _in_window(d, today, end):
            continue
        sym = (item.get("symbol") or "").upper()
        if wanted and sym not in wanted:
            continue
        p = (item.get("purpose") or "").lower()
        if purpose != "all":
            if purpose == "results" and not ("result" in p or "financial" in p):
                continue
            if purpose == "board_meeting" and "board" not in p:
                continue
            if purpose == "agm" and not ("annual general" in p or "agm" in p or "extraordinary" in p):
                continue
            if purpose == "dividend" and "dividend" not in p:
                continue
        rows.append({
            "date": d.isoformat(),
            "days_until": (d - today).days,
            "symbol": sym,
            "company": item.get("company"),
            "purpose": item.get("purpose"),
            "description": item.get("bm_desc"),
        })

    rows.sort(key=lambda r: (r["date"], r["symbol"]))
    if not rows:
        scope = f"for {', '.join(sorted(wanted))}" if wanted else "across NSE"
        warnings.append(
            f"No {purpose} events {scope} in the next {days} days. NSE's calendar has {len(raw)} upcoming "
            "events market-wide, so this is a real empty result for that filter."
        )
    if len(rows) > 100:
        warnings.append(f"Showing the first 100 of {len(rows)} events — narrow with `symbols`, `days` or `purpose`.")
    return ToolResult(
        data={
            "window": {"from": today.isoformat(), "to": end.isoformat(), "days": days},
            "purpose": purpose,
            "symbols": sorted(wanted) if wanted else None,
            "count": len(rows),
            "events": rows[:100],
            "source": "NSE event calendar (equities)",
        },
        warnings=warnings,
    )


async def search_announcements(
    query: str = "",
    category: str = "all",
    days: int = 7,
    symbols: str = "",
    min_materiality: str = "low",
    limit: int = 50,
) -> ToolResult:
    """Market-wide NSE announcement search — order wins, capex, ratings, risks."""
    days = max(1, min(int(days or 7), 90))
    limit = max(1, min(int(limit or 50), 200))
    valid_categories = {"all", *announcement_categories()}
    if category not in valid_categories:
        raise ToolError(f"category must be one of: {', '.join(sorted(valid_categories))}.", "invalid_input")
    if min_materiality not in _MATERIALITY_RANK:
        raise ToolError("min_materiality must be low, medium or high.", "invalid_input")

    today = date.today()
    start = today - timedelta(days=days)
    nse = await get_nse_client()
    raw = await nse.get_market_announcements(start.strftime("%d-%m-%Y"), today.strftime("%d-%m-%Y"))

    requested = [s.strip() for s in (symbols or "").split(",") if s.strip()]
    if len(requested) > 30:
        raise ToolError("Pass at most 30 symbols to search_announcements.", "invalid_input")
    available = {(item.get("symbol") or "").upper() for item in raw}
    wanted, unresolved = await _resolve_symbol_set(requested, available) if requested else (set(), [])

    warnings: list[str] = []
    if unresolved:
        warnings.append(f"Could not resolve: {', '.join(unresolved)}.")
    if not query and category == "all" and not requested:
        warnings.append(
            "No query, category or symbols given — returning the latest material disclosures across the "
            "whole market. Add a keyword or category to focus the search."
        )

    needle = (query or "").strip().lower()
    min_rank = _MATERIALITY_RANK[min_materiality]
    matched = []
    seen = set()
    for item in raw:
        sym = (item.get("symbol") or "").upper()
        if wanted and sym not in wanted:
            continue
        headline = item.get("headline") or ""
        nse_category = item.get("nse_category") or ""
        cat = categorize(headline, nse_category, item.get("company") or "")
        if category != "all" and cat != category:
            continue
        if _MATERIALITY_RANK.get(materiality(cat), 1) < min_rank:
            continue
        if needle and needle not in f"{headline} {nse_category} {item.get('company', '')}".lower():
            continue
        key = item.get("seq_id") or f"{sym}|{item.get('date')}|{headline}"
        if key in seen:
            continue
        seen.add(key)
        matched.append({
            "date": item.get("date"),
            "symbol": sym or None,
            "company": item.get("company"),
            "industry": item.get("industry") or None,
            "category": cat,
            "materiality": materiality(cat),
            "nse_category": nse_category,
            "headline": headline,
            "url": item.get("url"),
            "_sort": item.get("sort_date") or item.get("date") or "",
        })

    matched.sort(key=lambda r: (r["_sort"], r["symbol"] or ""), reverse=True)
    for r in matched:
        r.pop("_sort", None)
    counts: dict[str, int] = {}
    for r in matched:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    if not matched:
        warnings.append(
            f"No announcements matched in the last {days} days. NSE returned {len(raw)} filings market-wide, "
            "so the filters are the reason — broaden the keyword/category or increase `days`."
        )
    if len(matched) > limit:
        warnings.append(f"Showing the latest {limit} of {len(matched)} matches — narrow the query or raise `limit`.")
    return ToolResult(
        data={
            "query": query or None,
            "category": category,
            "min_materiality": min_materiality,
            "window": {"from": start.isoformat(), "to": today.isoformat(), "days": days},
            "symbols": sorted(wanted) if wanted else None,
            "filings_scanned": len(raw),
            "matched": len(matched),
            "counts_by_category": counts,
            "announcements": matched[:limit],
            "source": "NSE corporate announcements (equities)",
        },
        warnings=warnings,
    )
