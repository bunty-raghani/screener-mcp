"""
Local watchlist + an on-demand event feed.

The portfolio tools answer "what do I own and what is it worth"; this answers
"what changed across the companies I follow". State is a single local JSON
file at ~/.screener-mcp/watchlist.json — no account and no external service.

The feed combines four NSE/Screener sources that are otherwise separate calls:
  * upcoming results / board meetings
  * corporate actions (dividends, splits, bonus, buybacks)
  * material announcements (order wins, capex, ratings, auditor changes)
  * a price snapshot with 1-day/1-week/1-month moves
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.company_page import fetch_company_page
from ..core.envelope import ToolError, ToolResult
from ..core.technicals import fetch_price_history, pct_return
from ..parsers.company import parse_overview
from .event_tools import get_corporate_actions, get_earnings_calendar, search_announcements
from .portfolio import get_portfolio

logger = logging.getLogger(__name__)

_WATCHLIST_PATH = Path.home() / ".screener-mcp" / "watchlist.json"
_MAX_FEED_SYMBOLS = 20


def _load() -> dict:
    if not _WATCHLIST_PATH.exists():
        return {"symbols": []}
    try:
        data = json.loads(_WATCHLIST_PATH.read_text())
        data.setdefault("symbols", [])
        return data
    except Exception:
        return {"symbols": []}


def _save(data: dict):
    _WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _WATCHLIST_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _entries(data: dict) -> list[dict]:
    """Normalise old (list of strings) and new (list of objects) formats."""
    out = []
    for item in data.get("symbols", []):
        if isinstance(item, str):
            out.append({"symbol": item.upper(), "name": None, "company_id": None})
        elif isinstance(item, dict) and item.get("symbol"):
            out.append(item)
    return out


async def add_watchlist_stock(symbol: str) -> str:
    """Add a company to the local watchlist (idempotent)."""
    raw = (symbol or "").strip()
    if not raw:
        raise ToolError("Pass a company symbol or name.", "invalid_input")
    page = await fetch_company_page(raw, "consolidated")
    ov = parse_overview(page.html)
    data = _load()
    entries = _entries(data)
    if any(e["symbol"] == page.symbol for e in entries):
        return f"**{page.symbol} is already on the watchlist.**"
    entries.append({
        "symbol": page.symbol,
        "name": ov.get("name"),
        "company_id": page.company_id,
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    data["symbols"] = entries
    _save(data)
    return f"**Added {page.symbol} — {ov.get('name') or ''} to the watchlist.** ({len(entries)} symbol(s))"


async def remove_watchlist_stock(symbol: str) -> str:
    """Remove a company from the local watchlist."""
    raw = (symbol or "").strip().upper()
    data = _load()
    entries = _entries(data)
    kept = [e for e in entries if e["symbol"] != raw]
    if len(kept) == len(entries):
        raise ToolError(f"{raw} is not on the watchlist.", "not_found")
    data["symbols"] = kept
    _save(data)
    return f"**Removed {raw} from the watchlist.** ({len(kept)} symbol(s) left)"


async def get_watchlist() -> ToolResult:
    """The saved watchlist with names, newest first."""
    entries = _entries(_load())
    if not entries:
        return ToolResult(
            data={"count": 0, "symbols": []},
            warnings=["Watchlist is empty. Add one with `add_watchlist_stock('TCS')`."],
        )
    return ToolResult(data={"count": len(entries), "symbols": entries})


async def _price_snapshot(symbol: str, company_id: Optional[str], name: Optional[str]) -> dict:
    try:
        hist = await fetch_price_history(company_id, 400) if company_id else None
        if not hist or not hist.closes:
            return {"symbol": symbol, "name": name, "price": None, "returns": None}
        closes = hist.closes
        return {
            "symbol": symbol,
            "name": name,
            "price": round(closes[-1], 2),
            "as_of": hist.dates[-1],
            "returns": {
                "1d_pct": round(pct_return(closes, 1), 2) if pct_return(closes, 1) is not None else None,
                "1w_pct": round(pct_return(closes, 5), 2) if pct_return(closes, 5) is not None else None,
                "1m_pct": round(pct_return(closes, 21), 2) if pct_return(closes, 21) is not None else None,
                "3m_pct": round(pct_return(closes, 63), 2) if pct_return(closes, 63) is not None else None,
            },
        }
    except Exception as e:
        logger.warning("price snapshot failed for %s: %s", symbol, e)
        return {"symbol": symbol, "name": name, "price": None, "returns": None}


async def _resolve_entries(symbols: str = "") -> list[dict]:
    if symbols.strip():
        raw = [s.strip() for s in symbols.split(",") if s.strip()]
        if len(raw) > _MAX_FEED_SYMBOLS:
            raise ToolError(f"Pass at most {_MAX_FEED_SYMBOLS} symbols to the feed.", "invalid_input")
        entries = []
        for s in raw:
            page = await fetch_company_page(s, "consolidated")
            ov = parse_overview(page.html)
            entries.append({"symbol": page.symbol, "name": ov.get("name"), "company_id": page.company_id})
        return entries
    return _entries(_load())[:_MAX_FEED_SYMBOLS]


async def get_watchlist_feed(symbols: str = "", days: int = 7) -> ToolResult:
    """Everything material that changed across the watchlist in one call."""
    days = max(1, min(int(days or 7), 90))
    entries = await _resolve_entries(symbols)
    if not entries:
        return ToolResult(
            data={"symbols": [], "count": 0},
            warnings=["Watchlist is empty. Add one with `add_watchlist_stock('TCS')`, or pass `symbols`."],
        )
    names = {e["symbol"].upper(): e for e in entries}
    symbols_arg = ",".join(names)

    warnings: list[str] = []
    source_errors: list[str] = []

    async def source(coro, name: str):
        try:
            return await coro
        except Exception as e:
            source_errors.append(name)
            warnings.append(f"{name} unavailable ({type(e).__name__}) — that section is missing from the feed.")
            return None

    announcements, calendar, actions = await asyncio.gather(
        source(search_announcements(symbols=symbols_arg, days=days, min_materiality="low", limit=100),
               "announcements"),
        source(get_earnings_calendar(symbols=symbols_arg, days=max(days, 30), purpose="all"),
               "earnings calendar"),
        source(get_corporate_actions(symbols=symbols_arg, days=max(days, 90), action_type="all"),
               "corporate actions"),
    )

    sem = asyncio.Semaphore(3)

    async def snap(e):
        async with sem:
            return await _price_snapshot(e["symbol"], e.get("company_id"), e.get("name"))

    snapshots = await asyncio.gather(*[snap(e) for e in entries])

    for result in (announcements, calendar, actions):
        if result is not None:
            warnings.extend(result.warnings)
    feed_announcements = announcements.data.get("announcements", []) if announcements else []
    feed_events = calendar.data.get("events", []) if calendar else []
    feed_actions = actions.data.get("actions", []) if actions else []
    summary = {
        "symbols": len(entries),
        "high_materiality_announcements": sum(1 for a in feed_announcements if a.get("materiality") == "high"),
        "announcements": len(feed_announcements),
        "upcoming_events": len(feed_events),
        "corporate_actions": len(feed_actions),
        "price_snapshots": sum(1 for s in snapshots if s.get("price") is not None),
    }
    return ToolResult(
        data={
            "symbols": [e["symbol"] for e in entries],
            "window": {"announcements_days": days, "events_days": max(days, 30),
                       "corporate_actions_days": max(days, 90)},
            "price_snapshot": snapshots,
            "upcoming_events": feed_events,
            "corporate_actions": feed_actions,
            "announcements": feed_announcements,
            "summary": summary,
            "note": (
                "Price returns are from closing prices; corporate actions are NSE's ex/record dates. "
                "A high-materiality announcement is a lead for research, not a conclusion — read the filing."
            ),
        },
        warnings=warnings,
        partial=bool(source_errors),
        reason=(f"Event source(s) unavailable: {', '.join(source_errors)}." if source_errors else None),
    )


async def get_portfolio_review(days: int = 7) -> ToolResult:
    """Portfolio P&L plus the same event feed for every holding."""
    portfolio = await get_portfolio()
    if not isinstance(portfolio, ToolResult) or not portfolio.data.get("holdings"):
        return portfolio if isinstance(portfolio, ToolResult) else ToolResult(
            data={"portfolio": portfolio},
        )
    symbols = [h["symbol"] for h in portfolio.data["holdings"]]
    feed = await get_watchlist_feed(symbols=",".join(symbols), days=days)
    return ToolResult(
        data={
            "portfolio": portfolio.data,
            "events": {
                "upcoming_events": feed.data.get("upcoming_events", []),
                "corporate_actions": feed.data.get("corporate_actions", []),
                "announcements": feed.data.get("announcements", []),
                "price_snapshot": feed.data.get("price_snapshot", []),
                "summary": feed.data.get("summary", {}),
            },
        },
        warnings=list(portfolio.warnings) + list(feed.warnings),
        missing_fields=portfolio.missing_fields,
        partial=bool(portfolio.partial or feed.partial),
        reason=portfolio.reason or feed.reason,
    )
