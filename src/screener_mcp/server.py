"""
Screener.in MCP Server — Indian Stock Research Assistant

Tools exposed to Claude (47 total):
  search_company              — find a company by name or symbol
  get_company_overview        — key ratios, about, price data
  get_financials              — P&L / Balance Sheet / Cash Flow / Ratios history (incl. P/E, P/B, ROE, D/E)
  get_quarterly_results       — last 8 quarters of results
  get_shareholding_pattern    — holding trends by category, promoter group and pledge assessment
  get_peer_comparison         — peer comparison table
  compare_companies           — 2-6 companies side by side (JSON + interactive dashboard)
  screen_stocks               — Screener.in query + technical clauses, AND/OR/parentheses, junk filtering
  get_52_week_low_candidates  — quality stocks near their 52-week low, in one call
  compare_to_sector           — stock's move vs its sector index and Nifty 50
  get_industry_overview       — industry medians, distribution, leaders and a stock's position
  track_screen_changes        — snapshot/diff a screen over time (local screen alerts)
  get_recent_news             — recent news headlines (Google News)
  get_analyst_targets         — consensus target price + broker targets in recent headlines
  get_relative_valuation      — P/E and ROCE vs industry median, up to 20 stocks at once
  get_valuation_bands         — current P/E, P/B, EV/EBITDA, MCap/Sales vs own 5/10-year range
  value_scenarios             — forward scenario + the growth/exit P/E today's price implies
  analyze_earnings_quality    — CFO/PAT, accruals, working capital, other-income dependence
  get_related_party_transactions — governance check: RPTs by party and year
  get_cost_structure          — material/employee/other expense mix and its drivers
  get_price_returns           — point-to-point return, CAGR and drawdown vs an index
  get_moat_signals            — revenue share / rank / HHI + ROCE, margin, promoter durability
  get_forward_outlook         — analyst estimates, order wins, capex filings, management guidance
  screen_by_theme             — pre-built thematic screens (criteria in the tool description)
  get_full_analysis           — ALL data for deep-dive reasoning
  analyze_red_flags           — rule-based red-flag checks over the full history
  get_document_list           — list annual reports, earnings call transcripts, PPTs and recordings
  ask_company_research        — semantic search over annual reports / earnings calls (all, one type, or one document)
  search_market_commentary    — semantic search for a question across multiple companies' indexed documents
  compare_management_commentary — same question across companies, side by side with citations
  get_company_announcements   — NSE corporate announcements, incl. credit and ESG rating actions
  search_announcements        — market-wide NSE announcement search by keyword/category/materiality
  get_corporate_actions       — NSE dividends, splits, bonus, rights, buybacks and ex-dates
  get_earnings_calendar       — NSE results/board-meeting/AGM calendar
  get_bulk_deals              — NSE bulk or block deals by company, by investor name, or both
  get_insider_trading         — SEBI PIT promoter/KMP/designated-person trade disclosures
  get_commodity_prices        — international benchmark price + exposed Indian companies
  notebook_ai                 — save and summarize investment research notes
  add_portfolio_stock         — add/merge a holding into your local portfolio
  update_portfolio_stock      — correct quantity/avg price on an existing holding
  remove_portfolio_stock      — remove a holding entirely
  get_portfolio               — view holdings with live P&L and weights
  get_portfolio_review        — portfolio P&L + events, results dates and price moves
  add_watchlist_stock         — add a company to the local watchlist
  remove_watchlist_stock      — remove a company from the watchlist
  get_watchlist               — list the saved watchlist
  get_watchlist_feed          — events, announcements and price moves across the watchlist

Resources:
  screener://analyst-guide    — how to use this assistant
  screener://query-syntax     — Screener query language reference
  ui://screener/stock-comparison.html — interactive dashboard (MCP App) for compare_companies

Setup:
  Set environment variables:
    SCREENER_USERNAME=your@email.com
    SCREENER_PASSWORD=yourpassword
  Without credentials, public data only (some metrics may be hidden).

  For document analysis (ask_company_research, search_market_commentary):
    pip install pdfplumber sentence-transformers chromadb

Run:
  python -m screener_mcp.server
  or via Claude Code MCP config (see README).
"""

import asyncio
import httpx
import os
import re
from pathlib import Path
from mcp.server.mcpserver import MCPServer
from .core.envelope import ToolError, error_envelope, to_envelope
from .core.nse_client import NSEError
from .tools.company_tools import (
    search_company as _search_company,
    get_company_overview as _get_overview,
    get_financials as _get_financials,
    get_quarterly_results as _get_quarterly,
    get_shareholding as _get_shareholding,
    get_peers as _get_peers,
    compare_companies as _compare,
    dashboard_rows as _dashboard_rows,
)
from .tools.screening_tools import (
    screen_stocks as _screen,
    screen_by_theme as _theme,
    theme_catalog as _theme_catalog,
)
from .tools.technical_tools import (
    get_52_week_low_candidates as _get_52w_low,
    compare_to_sector as _compare_to_sector,
)
from .tools.research_tools import (
    get_relative_valuation as _get_relative_valuation,
    get_moat_signals as _get_moat_signals,
    get_forward_outlook as _get_forward_outlook,
)
from .tools.market_tools import (
    get_recent_news as _get_recent_news,
    get_analyst_targets as _get_analyst_targets,
)
from .tools.analysis_tools import (
    get_full_analysis as _full_analysis,
    get_red_flags as _red_flags,
)
from .tools.documents import (
    get_document_list as _get_document_list,
    ask_company_research as _ask_company_research,
    search_market_commentary as _search_market_commentary,
    compare_management_commentary as _compare_management_commentary,
)
from .tools.valuation_tools import (
    get_valuation_bands as _get_valuation_bands,
    value_scenarios as _value_scenarios,
    get_price_returns as _get_price_returns,
)
from .tools.cost_tools import get_cost_structure as _get_cost_structure
from .tools.quality_tools import analyze_earnings_quality as _analyze_earnings_quality
from .tools.governance_tools import get_related_party_transactions as _get_related_party_transactions
from .tools.event_tools import (
    get_corporate_actions as _get_corporate_actions,
    get_earnings_calendar as _get_earnings_calendar,
    search_announcements as _search_announcements,
)
from .tools.industry_tools import get_industry_overview as _get_industry_overview
from .tools.watchlist import (
    add_watchlist_stock as _add_watchlist_stock,
    remove_watchlist_stock as _remove_watchlist_stock,
    get_watchlist as _get_watchlist,
    get_watchlist_feed as _get_watchlist_feed,
    get_portfolio_review as _get_portfolio_review,
)
from .tools.screen_tracker import track_screen_changes as _track_screen_changes
from .tools.announcements import get_company_announcements as _get_announcements
from .tools.shareholders import get_bulk_deals as _get_bulk_deals
from .tools.insider_trading import get_insider_trading as _get_insider_trading
from .tools.commodities import get_commodity_prices as _get_commodity_prices
from .tools.notebook import notebook_ai as _notebook_ai
from .tools.portfolio import (
    add_portfolio_stock as _add_portfolio_stock,
    update_portfolio_stock as _update_portfolio_stock,
    remove_portfolio_stock as _remove_portfolio_stock,
    get_portfolio as _get_portfolio,
)

# Hard ceiling per tool call, so a hung upstream can't stall the conversation.
_TOOL_TIMEOUT = float(os.getenv("SCREENER_TOOL_TIMEOUT", "240"))


def _safe(result):
    """Wrap a tool implementation so it always returns the standard envelope
    (see core/envelope.py) and network/auth errors become structured errors."""
    import functools
    async def wrapper(*args, **kwargs):
        try:
            return to_envelope(await asyncio.wait_for(result(*args, **kwargs), timeout=_TOOL_TIMEOUT))
        except asyncio.TimeoutError:
            return error_envelope(
                f"Gave up after {_TOOL_TIMEOUT:.0f}s — the data source is slow or rate-limiting. Retry, or "
                "narrow the request (smaller universe / max_candidates, include_earnings_call=False).",
                "timeout",
            )
        except ToolError as e:
            return error_envelope(e.message, e.error_type, **e.details)
        except NSEError as e:
            return error_envelope(
                f"{e}. This is an NSE-side failure, not an empty result — retry shortly.",
                "upstream_unavailable",
                source="nse",
            )
        except PermissionError as e:
            return error_envelope(
                f"Login required. {e} Set SCREENER_USERNAME and SCREENER_PASSWORD env vars, then restart the server.",
                "login_required",
            )
        except httpx.TimeoutException:
            return error_envelope("Request timed out — Screener.in is taking too long to respond. Try again in a moment.", "timeout")
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code == 404:
                return error_envelope("Not found. Check the symbol, or use search_company() to find the NSE/BSE code.", "not_found")
            if code == 429:
                return error_envelope("Screener.in is rate-limiting requests (HTTP 429). Wait a minute and retry, or narrow the request.", "rate_limited")
            return error_envelope(f"Screener.in returned HTTP {code}. The site may be down — try again shortly.", "upstream_error", http_status=code)
        except httpx.TransportError as e:
            return error_envelope(f"Cannot reach the data source ({type(e).__name__}). Check your connection and try again.", "network_error")
        except ImportError as e:
            return error_envelope(str(e), "missing_dependency")
        except Exception as e:
            return error_envelope(
                f"Unexpected error: {type(e).__name__}: {e}. If this persists, please open an issue at "
                "https://github.com/LogeshR15/screener-mcp/issues",
                "unexpected_error",
            )
    functools.update_wrapper(wrapper, result)
    return wrapper


_UI_DIR = Path(__file__).parent / "ui"
STOCK_COMPARISON_WIDGET_URI = "ui://screener/stock-comparison.html"
MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"


mcp = MCPServer(
    "Screener Stock Research",
    instructions="""
You are an expert Indian equity analyst with deep knowledge of Indian stock markets,
Screener.in data, and long-term investing principles.

When users ask about stocks, companies, or investment themes:
1. Use the appropriate tools to fetch real Screener.in data
2. Reason over the data like a seasoned analyst
3. Present findings clearly — use tables, bullet points, and plain language
4. Always contextualize numbers (e.g., "ROCE of 25% is excellent — above industry average")
5. Flag important caveats (data is point-in-time, past performance ≠ future results)
6. For beginners, translate jargon into everyday language
7. For advanced users, go deeper into trends and red flags

You have access to all financial statements, ratios, shareholding data, and screening capabilities.
The data comes from Screener.in and covers BSE/NSE listed Indian companies.

Always be honest about limitations: Screener data may lag by a quarter, sector classifications
are broad, and you cannot predict stock prices.
""",
)


# ─── Search & Discovery ────────────────────────────────────────────────────────

@mcp.tool(annotations={"title": "Search Company", "readOnlyHint": True, "openWorldHint": True})
async def search_company(query: str) -> dict:
    """
    Search for an Indian stock/company by name or NSE/BSE symbol.

    Examples:
      search_company("Tata Consultancy")
      search_company("INFY")
      search_company("Jyothy Labs")
      search_company("ITC")
    """
    return await _safe(_search_company)(query)


@mcp.tool(annotations={"title": "Screen Stocks", "readOnlyHint": True, "openWorldHint": True})
async def screen_stocks(
    query: str,
    limit: int = 25,
    sort_by: str = "",
    order: str = "desc",
    universe: str = "",
    max_candidates: int = 150,
    min_market_cap: float = 100,
    exclude_flagged: bool = True,
    peer_relative: bool = False,
    page: int = 1,
) -> dict:
    """
    Run a stock screen: Screener.in fundamental fields and/or technical
    (price-action) clauses, combined with AND, OR and parentheses.

    Fundamental fields (Screener.in syntax, exact spelling matters):
      Market Capitalization, Current Price, Price to Earning, Price to book value,
      Return on capital employed, Return on equity, Debt to equity,
      Sales growth 5Years, Sales growth 3Years, Sales growth last year,
      Profit growth 5Years, Profit growth 3Years, Profit growth last year,
      Dividend yield, Pledged percentage, Net cash flow last year,
      Average return on equity 5Years, Average return on capital employed 5Years,
      Current ratio, EV / EBITDA, PEG Ratio

    Technical clauses (evaluated from daily price history):
      52 week low distance < 10        — % above the 52-week low
      52 week high distance > 30       — % below the 52-week high
      RSI < 30                         — 14-day RSI
      Price above 200 DMA              — also: below, 20/50/200 DMA, "50 DMA above 200 DMA"
      Price vs 50 DMA < -5             — % above(+)/below(−) a DMA
      Volume vs 20 day average > 2     — today's volume ÷ 20-day average (spike detection)

    Operators: > < >= <= =, combined with AND / OR and parentheses, e.g.
      "(Return on capital employed > 20 OR Return on equity > 25) AND Debt to equity < 0.5"
      "Return on capital employed > 15 AND (RSI < 30 OR 52 week low distance < 5)"
    Purely fundamental OR logic runs natively on Screener; when technical
    clauses sit under an OR, each alternative runs as its own screen (up to 6)
    and results are merged — each result lists matched_groups.

    Result hygiene (on by default):
      min_market_cap: ₹ Cr floor added to the Screener query unless the query
                      already has a Market Capitalization clause (0 = off)
      exclude_flagged: drop rows with implausible numbers (P/E < 1, >500%
                      profit spike on a small base, profit > sales, negligible
                      sales, sub-₹1 price) — they're listed in
                      excluded_for_data_quality. False = keep them, flagged.

    peer_relative: add each result's P/E and ROCE vs its industry median
                   (first 20 results; slower — one industry fetch per industry).

    How it runs: fundamental clauses go to Screener.in (login required);
    technical clauses are then checked against up to `max_candidates` of those
    matches. With only technical clauses, candidates come from a public NSE
    index — `universe` (default "nifty500"; also nifty50, midcap100,
    smallcap100, smallcap250, bank, it, auto, pharma, fmcg, metal, realty,
    energy, defence, chemicals, ...) — so no login is needed.

    sort_by: a technical metric (rsi14, pct_above_52w_low, volume_vs_20d_avg, ...)
             or a result column name. Default: the first technical filter.

    page: 1-based page of results, `limit` per page (page=2 → results 26-50
          with the default limit).

    Returns partial=true (with a reason) if only some candidates could be
    checked — e.g. more fundamental matches than max_candidates.
    """
    return await _safe(_screen)(
        query, sort_by=sort_by, order=order, limit=limit, universe=universe,
        max_candidates=max_candidates, min_market_cap=min_market_cap,
        exclude_flagged=exclude_flagged, peer_relative=peer_relative, page=page,
    )


@mcp.tool(annotations={"title": "Relative Valuation", "readOnlyHint": True, "openWorldHint": True})
async def get_relative_valuation(symbols: list[str] | str) -> dict:
    """
    Is this P/E cheap *for its industry*? Compares each stock's P/E and ROCE
    with the median of every listed company in its Screener industry, for up
    to 20 stocks in one call (stocks in the same industry share one fetch, so
    this scales across a screen's results).

    Returns per stock: industry, P/E vs industry median (%), ROCE vs median
    (pp), and a one-line assessment (e.g. "at a premium to its industry, but
    the premium comes with clearly higher ROCE", or a possible value trap).
    Sorted cheapest-vs-industry first.

    symbols: list (["HBLENGINE", "BSE", "TCS"]) or comma-separated string

    Tip: screen_stocks(..., peer_relative=True) attaches the same data to
    screen results directly.
    """
    return await _safe(_get_relative_valuation)(symbols)


@mcp.tool(annotations={"title": "Moat Signals", "readOnlyHint": True, "openWorldHint": True})
async def get_moat_signals(symbol: str) -> dict:
    """
    Quantitative moat proxies for a company — market position plus how
    durable its economics have been.

    Market position (from every listed company in its Screener industry):
      revenue share and rank by quarterly sales, industry concentration (HHI
      and CR4 — fragmented / moderately / highly concentrated), market-leader
      and top-3 flags.
    Durability (from up to ~12 years of statements):
      ROCE consistency (years ≥ 15%, median, min), operating-margin stability
      (standard deviation), sales CAGR, promoter-holding stability.
    Plus a signals summary with the thresholds used.

    These are proxies — listed-company revenue share misses unlisted and
    imported competitors, and brand / switching costs / licences still need
    judgement (check the annual report and earnings calls).

    symbol: NSE/BSE symbol or company name
    """
    return await _safe(_get_moat_signals)(symbol)


@mcp.tool(annotations={"title": "Forward Outlook", "readOnlyHint": True, "openWorldHint": True})
async def get_forward_outlook(symbol: str, days: int = 180, include_earnings_call: bool = True) -> dict:
    """
    Forward-looking inputs in one call, instead of extrapolating trailing data:

      1. Analyst estimates — current and next fiscal-year EPS and revenue
         consensus (low/high, growth, analyst count) and forward P/E.
      2. Order wins — NSE filings in the last `days` announcing orders,
         contracts, LoAs, with rupee values stated in the headline summed.
      3. Capex & expansion — filings about new capacity, plants, commissioning.
      4. Management guidance — the most relevant passages from the latest
         earnings call on guidance, order book / pipeline, and capex
         (needs the [ai] extra; otherwise returns an install hint).

    Each part is independent — if one source is down the result is partial.

    symbol: NSE/BSE symbol or company name
    days: filing look-back (30-730, default 180)
    include_earnings_call: set False to skip the transcript step (faster)
    """
    return await _safe(_get_forward_outlook)(symbol, days, include_earnings_call)


@mcp.tool(annotations={"title": "52-Week-Low Candidates", "readOnlyHint": True, "openWorldHint": True})
async def get_52_week_low_candidates(
    min_roce: float = 15,
    max_debt_to_equity: float = 0.5,
    max_pct_above_52w_low: float = 10,
    min_market_cap: float = 1000,
    universe: str = "",
    limit: int = 20,
) -> dict:
    """
    Quality stocks trading near their 52-week low — in one call.

    Combines quality filters (ROCE, debt-to-equity, market cap in ₹ Cr) with
    proximity to the 52-week low, and returns for each match: current price,
    52W high/low, % above the low, RSI/DMA context, and the same clean fields
    as get_company_overview (P/E, ROCE, ROE, book value, dividend yield,
    debt-to-equity).

    With a Screener login and no `universe`, scans the whole market via a
    Screener query. Otherwise scans an index's constituents (default Nifty
    500) and computes debt-to-equity from each match's latest balance sheet.

    Examples:
      get_52_week_low_candidates()
      get_52_week_low_candidates(min_roce=20, max_debt_to_equity=0.2, max_pct_above_52w_low=5)
      get_52_week_low_candidates(universe="midcap100", min_market_cap=5000)
    """
    return await _safe(_get_52w_low)(
        min_roce, max_debt_to_equity, max_pct_above_52w_low, min_market_cap, universe, limit,
    )


@mcp.tool(annotations={"title": "Compare To Sector", "readOnlyHint": True, "openWorldHint": True})
async def compare_to_sector(symbol: str, days: int = 30, benchmark: str = "") -> dict:
    """
    Has this stock fallen (or risen) more or less than its sector and the
    market? Separates a market/sector pullback from a company-specific move.

    Compares the stock's close-to-close return against its sector index
    (auto-picked from its Screener sector, e.g. Nifty Auto, Nifty IT, Nifty
    India Defence) and the Nifty 50, over `days` calendar days plus 7/30/90-day
    context windows. Also returns beta and correlation vs the sector, drawdown
    from the window high, and a plain-language verdict.

    symbol: NSE/BSE symbol
    days: window in calendar days (5-365, default 30)
    benchmark: optional index override — nifty50, nifty500, bank, it, auto,
               pharma, fmcg, metal, realty, energy, defence, chemicals, ...

    Examples:
      compare_to_sector("MSUMI", days=60)
      compare_to_sector("HDFCBANK", benchmark="private_bank")
    """
    return await _safe(_compare_to_sector)(symbol, days, benchmark)


@mcp.tool(annotations={"title": "Industry Overview", "readOnlyHint": True, "openWorldHint": True})
async def get_industry_overview(industry: str = "", symbol: str = "",
                                top_n: int = 15, max_pages: int = 8) -> dict:
    """
    The whole industry in one view: how many listed companies, median P/E,
    P/B, ROCE, growth, margin and dividend yield, the P/E and ROCE
    distribution, the market-cap leaders, quality-at-a-reasonable-price names,
    growth leaders, the cheapest/most expensive names, and where a given stock
    sits (market-cap rank, P/E and ROCE vs median, revenue share).

    `industry` accepts a leaf Screener industry name ("Specialty Chemicals",
    "Pharmaceuticals", "Cement & Cement Products"), a Screener industry URL,
    or a company symbol. Broad names such as "IT - Software" are not in
    Screener's public /market/ index — pass a representative `symbol` (e.g.
    "TCS") instead, and the company's broad-industry classification is used.

    Medians exclude companies below ₹500 Cr and non-positive/ >500x P/E.
    `max_pages` caps how much of a large industry is fetched (25 rows a page).

    Examples:
      get_industry_overview(symbol="TCS")
      get_industry_overview("Specialty Chemicals")
      get_industry_overview("Cement & Cement Products", top_n=20)
    """
    return await _safe(_get_industry_overview)(industry, symbol, top_n, max_pages)


@mcp.tool(annotations={"title": "Track Screen Changes", "readOnlyHint": False, "openWorldHint": True})
async def track_screen_changes(label: str = "", query: str = "", theme: str = "",
                               limit: int = 100) -> dict:
    """
    Snapshot a stock screen and diff it against the last run — the local
    equivalent of a Screener email alert. Added names entered the captured
    result set; removed names left it.

    Run once to create a baseline, then again after results season / a
    rebalance to see the churn. State lives at
    ~/.screener-mcp/screen_snapshots.json.

    label: a name for this tracker ("quality_compounder")
    query: a Screener query (mutually exclusive with theme)
    theme: one of the pre-built themes (mutually exclusive with query)
    limit: how many results to capture (10-200, default 100)

    With no arguments, lists all saved trackers.

    Examples:
      track_screen_changes("quality", theme="high_roce_low_debt", limit=150)
      track_screen_changes("garp", query="Price to Earning < 25 AND Profit growth 5Years > 15")
      track_screen_changes()
    """
    return await _safe(_track_screen_changes)(label, query, theme, limit)


@mcp.tool(annotations={"title": "Get Recent News", "readOnlyHint": True, "openWorldHint": True})
async def get_recent_news(symbol: str, days: int = 14, limit: int = 20) -> dict:
    """
    Recent news headlines about a company (Google News, Indian edition).

    Returns publisher, publish time (UTC) and link for each headline, newest
    first, de-duplicated. Use it for the "what's happened lately" context that
    financial statements can't show — results reactions, brokerage calls,
    management moves, price hikes, regulatory news. For the company's own
    exchange filings use get_company_announcements instead.

    symbol: NSE/BSE symbol or company name
    days: look-back window (1-90, default 14)
    limit: max headlines (default 20)

    Examples:
      get_recent_news("TMPV")
      get_recent_news("Garden Reach", days=30)
    """
    return await _safe(_get_recent_news)(symbol, days, limit)


@mcp.tool(annotations={"title": "Get Analyst Targets", "readOnlyHint": True, "openWorldHint": True})
async def get_analyst_targets(symbol: str) -> dict:
    """
    Analyst price targets for a stock, from two independent sources:

    1. Consensus (Yahoo Finance): mean / median / high / low target, number
       of analysts, implied upside vs the current price, and the
       strong-buy/buy/hold/sell/strong-sell split.
    2. Broker targets mentioned in the last 60 days of news headlines
       (e.g. "ICICI Securities target ₹370"), with links to each article.

    The two come from different broker sets and dates, so they rarely match;
    both are labelled with their source. Returns partial=true if one source is
    unavailable, or if the stock has no analyst coverage.

    symbol: NSE/BSE symbol or company name

    Examples:
      get_analyst_targets("TMPV")
      get_analyst_targets("HDFCBANK")
    """
    return await _safe(_get_analyst_targets)(symbol)


async def screen_by_theme(theme: str, limit: int = 20, page: int = 1) -> dict:
    return await _safe(_theme)(theme, limit=limit, page=page)


screen_by_theme.__doc__ = f"""
    Run a pre-built thematic stock screen.

    Query themes run a Screener query across the whole market. Sector themes
    (defense, ev_theme, chemicals, railways, renewable_energy) start from the
    companies actually in the sector — NSE index constituents, Screener
    industry pages or a curated list — then apply the filters; the response's
    data.universe says exactly which.

    Themes and criteria:
{_theme_catalog()}

    limit: results per page (default 20)
    page: 1-based page of results

    Examples:
      screen_by_theme("hidden_gems")
      screen_by_theme("defense")
      screen_by_theme("compounders", page=2)
    """
screen_by_theme = mcp.tool(annotations={"title": "Screen By Theme", "readOnlyHint": True, "openWorldHint": True})(
    screen_by_theme)


# ─── Company Deep Dive ─────────────────────────────────────────────────────────

@mcp.tool(annotations={"title": "Get Company Overview", "readOnlyHint": True, "openWorldHint": True})
async def get_company_overview(symbol: str, financial_type: str = "consolidated") -> dict:
    """
    Get a company's key ratios, current price, 52-week range, and about section.

    data.price_freshness says when the price is from (price_as_of), whether
    it's an intraday print or the last close, the previous close and the
    day's change — so you can tell how current "current_price" is.

    symbol: NSE/BSE symbol (e.g., "TCS", "INFY", "RELIANCE", "HDFCBANK")
    financial_type: "consolidated" (default) or "standalone"

    This is the best starting point for any company analysis.
    """
    return await _safe(_get_overview)(symbol, financial_type)


@mcp.tool(annotations={"title": "Get Financials", "readOnlyHint": True, "openWorldHint": True})
async def get_financials(
    symbol: str,
    statement: str = "profit_loss",
    financial_type: str = "consolidated",
    years: int = 5,
) -> dict:
    """
    Get financial statements for a company, as numbers (₹ Cr unless the
    label says % or days).

    symbol: NSE/BSE symbol
    statement options:
      "profit_loss"    — Revenue, expenses, operating profit, PAT (default).
                         The trailing-twelve-months column comes back as
                         data.ttm, separate from the fiscal years.
      "balance_sheet"  — Assets, liabilities, equity, debt
      "cash_flow"      — Operating, investing, financing cash flows
      "ratios"         — Screener's ratios table (debtor/inventory/payable
                         days, cash conversion cycle, working-capital days,
                         ROCE) plus ROE %, debt-to-equity, and year-end P/E and
                         P/B. data.row_sources says where each row comes from.
    financial_type: "consolidated" or "standalone"
    years: number of fiscal years (default 5, max 12) — TTM is never counted
    """
    valid = {"profit_loss", "balance_sheet", "cash_flow", "ratios"}
    if statement not in valid:
        return error_envelope(f"Invalid statement type '{statement}'. Choose from: {', '.join(sorted(valid))}", "invalid_input")
    return await _safe(_get_financials)(symbol, statement, financial_type, years)


@mcp.tool(annotations={"title": "Get Quarterly Results", "readOnlyHint": True, "openWorldHint": True})
async def get_quarterly_results(symbol: str, financial_type: str = "consolidated") -> dict:
    """
    Get the last 8 quarters of results for a company.

    Shows: Revenue, Expenses, Operating Profit, OPM%, Net Profit, EPS
    Useful for identifying quarter-on-quarter growth trends and seasonality.

    symbol: NSE/BSE symbol (e.g., "TCS", "WIPRO", "MARUTI")
    """
    return await _safe(_get_quarterly)(symbol, financial_type)


@mcp.tool(annotations={"title": "Get Shareholding Pattern", "readOnlyHint": True, "openWorldHint": True})
async def get_shareholding_pattern(symbol: str) -> dict:
    """
    Shareholding pattern for the last 8 quarters, with the analysis:

      - rows: Promoter / FII / DII / Government / Public %, shareholder count
      - trends: per category — latest %, change over the window and over the
        last quarter (pp), direction
      - promoter_group: present or not. Companies like ITC have no promoter;
        promoter-trend and pledge checks then don't apply, and the response
        says so instead of implying "no pledge".
      - pledge: % of promoter holding pledged, from Screener's analysis, with
        severity (>50% high, 20-50% moderate, >0-20% low); not_flagged (none
        flagged — small pledges may not be); or not_applicable (no promoter)

    symbol: NSE/BSE symbol

    Examples:
      get_shareholding_pattern("ZEEL")
      get_shareholding_pattern("ITC")
    """
    return await _safe(_get_shareholding)(symbol)


@mcp.tool(annotations={"title": "Get Peer Comparison", "readOnlyHint": True, "openWorldHint": True})
async def get_peer_comparison(symbol: str, financial_type: str = "consolidated") -> dict:
    """
    Get peer comparison table as shown on Screener.in for a company.

    Shows the company alongside its sector peers with key metrics
    like Market Cap, Sales, Net Profit, PE, ROCE, etc.

    symbol: NSE/BSE symbol
    """
    return await _safe(_get_peers)(symbol, financial_type)


@mcp.tool(
    annotations={"title": "Compare Companies", "readOnlyHint": True, "openWorldHint": True},
    # "ui/resourceUri" is the older flat key some hosts still read
    meta={"ui": {"resourceUri": STOCK_COMPARISON_WIDGET_URI}, "ui/resourceUri": STOCK_COMPARISON_WIDGET_URI},
)
async def compare_companies(symbols: list[str] | str, financial_type: str = "consolidated") -> dict:
    """
    Side-by-side comparison of 2 to 6 companies: price, market cap, P/E,
    book value, ROCE, ROE, debt-to-equity, dividend yield, 52-week range,
    plus point-in-time red-flag checks per company.

    Hosts that support MCP Apps (e.g. Claude Desktop) also render an
    interactive dashboard with the best value in each row highlighted; every
    host gets the same data as JSON in data.companies.

    symbols: list (["ITC", "HINDUNILVR", "NESTLEIND"]) or comma-separated
             string ("TCS,INFY,WIPRO"). Company names and near-miss symbols
             are resolved like everywhere else.
    financial_type: "consolidated" or "standalone"

    Examples:
      compare_companies(["ITC", "HINDUNILVR"])
      compare_companies("TCS,INFY,WIPRO,HCLTECH")
    """
    env = await _safe(_compare)(symbols, financial_type)
    # The dashboard reads `stocks` / `count` at the top level of the result.
    data = env.get("data") or {}
    stocks = _dashboard_rows(data) if data else []
    return {**env, "stocks": stocks, "count": len(data.get("companies", []))}


# ─── Analysis Tools ────────────────────────────────────────────────────────────

@mcp.tool(annotations={"title": "Get Full Analysis", "readOnlyHint": True, "openWorldHint": True})
async def get_full_analysis(symbol: str, financial_type: str = "consolidated") -> dict:
    """
    Fetch ALL financial data for a company in a single call, as one report.

    Uses the same windows as the standalone tools, so numbers always agree:
      - Key ratios, about, NSE/BSE codes
      - 10 fiscal years of P&L (+ TTM), Balance Sheet, Cash Flow
      - Ratios history incl. ROE, debt-to-equity, year-end P/E and P/B
      - 8 quarters of results
      - 8 quarters of shareholding
      - Peer comparison table

    Use this for a thorough analysis, to explain a company (including to a
    beginner), or to answer complex multi-part questions.

    symbol: NSE/BSE symbol
    """
    return await _safe(_full_analysis)(symbol, financial_type)


@mcp.tool(annotations={"title": "Analyze Red Flags", "readOnlyHint": True, "openWorldHint": True})
async def analyze_red_flags(symbol: str, financial_type: str = "consolidated") -> dict:
    """
    Rule-based red-flag checks over a company's full history — computed
    here, each flag with its severity and the numbers behind it:

      - Promoter holding falling, promoter shares pledged
      - Leverage (debt-to-equity) and borrowings outgrowing sales
      - ROCE falling or below 10%
      - Operating cash flow lagging net profit (and negative CFO)
      - Sales growing much faster than profit
      - Debtor / inventory days rising
      - Profit leaning on other income (one-offs)
      - Equity dilution

    Returns flags (sorted by severity), clean_checks, skipped_checks (e.g.
    leverage for banks, promoter checks for a company with no promoter), the
    thresholds used, and not_checked items that need the annual report.

    symbol: NSE/BSE symbol
    """
    return await _safe(_red_flags)(symbol, financial_type)


# ─── Valuation & Earnings Quality ─────────────────────────────────────────────

@mcp.tool(annotations={"title": "Valuation Bands", "readOnlyHint": True, "openWorldHint": True})
async def get_valuation_bands(symbol: str, years: int = 10,
                              financial_type: str = "consolidated") -> dict:
    """
    Is the stock cheap or expensive **relative to its own history**?

    Returns the current P/E, P/B, EV/EBITDA and Market Cap/Sales, each with:
      percentile within the last `years` of daily observations, median, 25th
      and 75th percentile, min/max, premium/discount to median, and a plain
      verdict ("cheap vs its own history" … "expensive vs its own history").

    This complements get_relative_valuation: that tool compares a stock with
    its industry *today*, this one compares it with itself over 5-10 years.
    A low percentile is not a buy signal — check why the multiple is low.

    symbol: NSE/BSE symbol or company name
    years: 1-10 (default 10; Screener's chart API holds ~10 years)
    financial_type: consolidated (default) or standalone
    """
    return await _safe(_get_valuation_bands)(symbol, years, financial_type)


@mcp.tool(annotations={"title": "Value Scenarios", "readOnlyHint": True, "openWorldHint": True})
async def value_scenarios(
    symbol: str,
    horizon_years: int = 5,
    sales_growth_pct: float = None,
    opm_pct: float = None,
    net_margin_pct: float = None,
    tax_rate_pct: float = None,
    exit_pe: float = None,
    dilution_pct: float = 0.0,
    model: str = "auto",
    financial_type: str = "consolidated",
) -> dict:
    """
    Test a valuation scenario, and see what the current price already implies.

    Forward: projects sales, EBITDA/PBT/PAT and EPS over the horizon from your
    assumptions, then values the final EPS at `exit_pe` — returning implied
    price, upside and price CAGR.

    Reverse: solves the sales growth (bisection) and the exit P/E that would
    justify today's price under the same assumptions. That is the more useful
    question: "what has to be true?"

    Defaults (when omitted): 5-year sales CAGR (or 3-year), latest OPM or net
    margin, latest tax rate, current P/E. Financial companies automatically
    use the net-margin model; pass model="operating" to override.

    Inputs are percentages (15 means 15%), not fractions. This is a scenario,
    not a forecast or target price — the assumptions are printed with it.
    """
    return await _safe(_value_scenarios)(
        symbol, horizon_years, sales_growth_pct, opm_pct, net_margin_pct,
        tax_rate_pct, exit_pe, dilution_pct, model, financial_type,
    )


@mcp.tool(annotations={"title": "Earnings Quality", "readOnlyHint": True, "openWorldHint": True})
async def analyze_earnings_quality(symbol: str, years: int = 10) -> dict:
    """
    Is reported profit backed by cash? A transparent quality check over the
    full history, not a black-box score.

    Computes per year: CFO/PAT, CFO/EBITDA, free cash flow/PAT, accrual ratio
    (profit − CFO ÷ total assets), other income as % of PBT, tax rate, debtor/
    inventory/payable days and the cash conversion cycle. Flags weak cash
    conversion, high accruals, negative FCF, working-capital stretch,
    other-income dependence and persistently low tax rates, with the evidence.

    Returns a 0-100 score and A-E grade for non-financial companies. Banks and
    NBFCs are not scored — loan/deposit flows make CFO and working-capital days
    non-comparable; the raw numbers are still shown.

    symbol: NSE/BSE symbol or company name
    years: 3-12 (default 10)
    """
    return await _safe(_analyze_earnings_quality)(symbol, years)


@mcp.tool(annotations={"title": "Related Party Transactions", "readOnlyHint": True, "openWorldHint": True})
async def get_related_party_transactions(symbol: str, years: int = 5,
                                         financial_type: str = "consolidated") -> dict:
    """
    Related-party transactions by party and year, from Screener.in.

    Use it as a governance check: large or growing transactions with
    promoter-group entities (associates, subsidiaries, key managerial
    personnel) deserve a closer read of the annual report and the arm's-length
    pricing disclosure.

    The table mixes income, expense, receivable, payable and financing lines,
    so rows are not additive. Read the labels, not a total.

    symbol: NSE/BSE symbol or company name
    years: 1-12 (default 5)
    financial_type: consolidated (default) or standalone
    """
    return await _safe(_get_related_party_transactions)(symbol, years, financial_type)


@mcp.tool(annotations={"title": "Cost Structure", "readOnlyHint": True, "openWorldHint": True})
async def get_cost_structure(symbol: str, period: str = "yearly",
                             periods: int = 5, include_subitems: bool = True) -> dict:
    """
    Why did the operating margin move? Splits expenses into the drivers.

    Returns Material, Employee and Other expenses as % of sales, with
    sub-items where Screener reports them — raw materials, traded goods,
    inventory change, advertisement, distribution, packing, power/fuel,
    travel, etc. — plus YoY percentage-point changes and the biggest mover.

    Percentages are of sales. Sub-items can overlap and need not add up to the
    parent. 'Other Expenses' is the catch-all, so check its sub-items before
    attributing a margin move to operating leverage.

    symbol: NSE/BSE symbol or company name
    period: "yearly" (default) or "quarterly"
    periods: number of columns, 1-12 (default 5)
    include_subitems: also fetch the breakdown (default True)
    """
    return await _safe(_get_cost_structure)(symbol, period, periods, include_subitems)


@mcp.tool(annotations={"title": "Price Returns", "readOnlyHint": True, "openWorldHint": True})
async def get_price_returns(symbol: str, from_date: str, to_date: str = "",
                            benchmark: str = "nifty50") -> dict:
    """
    Point-to-point return, CAGR and maximum drawdown for a stock, versus an index.

    Use it to answer "how did this stock do from X to Y?" instead of stringing
    together 1-week/1-month technical fields. `benchmark` accepts a key such as
    nifty50, nifty500, midcap100, smallcap100, bank, it, auto, pharma, fmcg,
    metal, realty, energy, defence, chemicals (or "" to skip).

    Long windows use weekly closes from Screener's chart API (daily up to 400
    days). Prices are not dividend-adjusted. Dates are YYYY-MM-DD.
    """
    return await _safe(_get_price_returns)(symbol, from_date, to_date, benchmark)


# ─── Document Analysis ────────────────────────────────────────────────────────

@mcp.tool(annotations={"title": "Get Document List", "readOnlyHint": True, "openWorldHint": True})
async def get_document_list(symbol: str) -> dict:
    """
    List all available annual reports and earnings call transcripts for a company.

    Fetches from Screener.in company page and NSE API.

    symbol: NSE/BSE symbol (e.g., "TCS", "INFY")

    Use this to see which years/quarters ask_company_research can search.
    Annual reports are de-duplicated per year (a PDF is preferred over a .zip).
    """
    return await _safe(_get_document_list)(symbol)


@mcp.tool(annotations={"title": "Ask Company Research", "readOnlyHint": True, "openWorldHint": True})
async def ask_company_research(
    symbol: str,
    question: str = "",
    doc_type: str = "all",
    year: int = 0,
    quarter: str = "",
    pdf_url: str = "",
    max_annual_reports: int = 3,
    max_earnings_calls: int = 4,
    force_reindex: bool = False,
) -> dict:
    """
    Ask a question over a company's annual reports and earnings-call
    transcripts using semantic search — across all recent documents at once,
    one document type, or a single document.

      ask_company_research("TCS", "How has capex strategy evolved?")      # 3 ARs + 4 calls
      ask_company_research("ITC", "Key regulatory risks", doc_type="annual_report")
      ask_company_research("TCS", "Key risks", year=2024)                 # one annual report
      ask_company_research("HDFCBANK", "NIM guidance", quarter="Q3FY25")  # one earnings call

    Downloads and indexes the PDFs locally on first use (cached after that),
    then returns the most relevant excerpts (each capped to ~900 characters,
    with document and page) — BRSR/sustainability boilerplate is ranked down
    unless the question is about ESG.

    doc_type: "all" | "annual_report" | "earnings_call"
    year: one annual report (e.g. 2024) — implies doc_type="annual_report"
    quarter: one earnings call (e.g. "Q1FY26") — implies doc_type="earnings_call"
    pdf_url: search this PDF directly (label it with year or quarter)
    max_annual_reports / max_earnings_calls: how many recent documents to
        include when no year/quarter is given
    force_reindex: re-download and rebuild the index (see each document's
        freshness.source_changed)

    Requires the [ai] extra: pip install 'screener-mcp[ai]'
    """
    return await _safe(_ask_company_research)(
        symbol, question, doc_type, year, quarter, pdf_url, max_annual_reports, max_earnings_calls, force_reindex,
    )


@mcp.tool(annotations={"title": "Search Market Commentary", "readOnlyHint": True, "openWorldHint": True})
async def search_market_commentary(
    question: str,
    symbols: list[str],
    top_k_per_symbol: int = 3,
) -> dict:
    """
    Semantic search for a question across MULTIPLE companies' already-indexed
    documents at once — e.g. "which of these companies mentioned raw material
    cost pressure in their recent earnings calls?"

    Only searches documents already indexed via ask_company_research for
    each symbol — it does NOT download new documents, so cost/latency stays bounded no matter how
    many symbols are passed. Run ask_company_research(symbol, ...) first for
    any symbol you want included that hasn't been indexed yet.

    Requires: pip install pdfplumber sentence-transformers chromadb

    Examples:
      search_market_commentary("raw material cost pressure", ["TATASTEEL", "JSWSTEEL", "SAIL"])
      search_market_commentary("management outlook on margins", ["ITC", "HINDUNILVR", "NESTLEIND"])
    """
    return await _safe(_search_market_commentary)(question, symbols, top_k_per_symbol)


@mcp.tool(annotations={"title": "Compare Management Commentary", "readOnlyHint": True, "openWorldHint": True})
async def compare_management_commentary(
    symbols: list[str] | str,
    question: str,
    doc_type: str = "all",
    top_k: int = 3,
    index_missing: bool = False,
) -> dict:
    """
    Ask the same question of several companies' annual reports/earnings calls
    and return their answers side by side, with document and page citations.

    This searches documents already indexed by ask_company_research — run that
    once per symbol first (recommended), or set index_missing=True to index the
    latest annual report / earnings call on demand (slower: local embeddings).

    symbols: 2-6 symbols (list or comma-separated)
    question: the common question ("How is AI demand changing?", "What are the
              key risks?", "What did management say about capex?")
    doc_type: all | annual_report | earnings_call
    top_k: excerpts per company
    index_missing: index the latest document for symbols with no cache

    Requires: pip install pdfplumber sentence-transformers chromadb
    """
    return await _safe(_compare_management_commentary)(
        symbols, question, doc_type, top_k, index_missing,
    )


# ─── Corporate Actions & Events ────────────────────────────────────────────────

@mcp.tool(annotations={"title": "Get Company Announcements", "readOnlyHint": True, "openWorldHint": True})
async def get_company_announcements(
    symbol: str,
    category: str = "all",
    days: int = 0,
) -> dict:
    """
    Fetch recent company announcements (exchange filings) from NSE.

    symbol: NSE trading symbol (e.g., "TCS", "RELIANCE")
    category: filter by type — "all" | "results" | "board_meeting" | "dividend"
              | "insider_trading" | "agm" | "acquisition" | "buyback" | "fund_raise"
              | "credit_rating" (CRISIL/ICRA/CARE/India Ratings actions — a
                debt-quality check) | "esg_rating" (ESG scores, which NSE files
                under "Credit Rating" too)
    days: how many days to look back (default 30; 730 for the rating
          categories, since rating actions come once or twice a year; max 3650)

    An NSE failure is returned as an error (upstream_unavailable), never as
    an empty list — an empty list means NSE really has no such filings.

    Examples:
      get_company_announcements("INFY", "results", 90)
      get_company_announcements("RELIANCE", "credit_rating")
      get_company_announcements("HDFCBANK", "dividend")
    """
    return await _safe(_get_announcements)(symbol, category, days)


@mcp.tool(annotations={"title": "Get Bulk Deals", "readOnlyHint": True, "openWorldHint": True})
async def get_bulk_deals(symbol: str = "", name: str = "", days: int = 30,
                         deal_type: str = "bulk") -> dict:
    """
    NSE bulk or block deals — for one company, for one investor across the
    market, or both.

    symbol: restrict to one company ("YESBANK")
    name: partial investor/entity name ("Jhunjhunwala", "SBI Mutual Fund",
          "Nalanda") — matched against the deal's client name
    days: look-back (default 30, max 365; NSE serves one day per request, so
          only the most recent 90 days are searched)
    deal_type: "bulk" (default; single trades > 0.5% of equity) or "block"
          (single negotiated trades reported by the exchange)

    Pass at least one of symbol / name. Bulk deals miss gradual accumulation —
    for aggregate FII/DII/promoter holdings use get_shareholding_pattern, for
    promoter/KMP trades of any size use get_insider_trading.

    Examples:
      get_bulk_deals("ADANIENT", days=60)
      get_bulk_deals(name="Mutual Fund")
      get_bulk_deals("TCS", name="SBI Mutual Fund")
    """
    return await _safe(_get_bulk_deals)(symbol, name, days, deal_type)


@mcp.tool(annotations={"title": "Get Insider Trading", "readOnlyHint": True, "openWorldHint": True})
async def get_insider_trading(symbol: str, days: int = 365) -> dict:
    """
    Recent insider trading disclosures (SEBI PIT Regulation 7(2)) for a company.

    Shows promoter/KMP/designated-person trades — buy or sell, quantity,
    value, and holding before/after — with no minimum trade size. This is
    different from `get_bulk_deals`, which only catches single trades over
    0.5% of equity and so misses most insider activity. Merges NSE's two PIT
    feeds (each lists filings the other doesn't).

    symbol: NSE trading symbol (e.g., "RELIANCE", "INFY")
    days: look-back window (default 365)

    Examples:
      get_insider_trading("RELIANCE")
      get_insider_trading("ADANIENT")
    """
    return await _safe(_get_insider_trading)(symbol, days)


@mcp.tool(annotations={"title": "Corporate Actions", "readOnlyHint": True, "openWorldHint": True})
async def get_corporate_actions(symbol: str = "", days: int = 365,
                                action_type: str = "all", symbols: str = "") -> dict:
    """
    Dividends, splits, bonus, rights, buybacks and demergers from NSE, with
    ex-dates and record dates.

    symbol: one company ("TCS"); or pass symbols="TCS,INFY,WIPRO" for several
    days: look-back (default 365; upcoming ex-dates in the next 45 days are
            always included)
    action_type: all | dividend | split | bonus | rights | buyback | demerger | other

    Use this for ex-date timing, dividend history and split/bonus adjustments.
    The company page's own corporate-actions modal is premium-gated on
    Screener; NSE's feed is public.

    Examples:
      get_corporate_actions("TCS")
      get_corporate_actions(action_type="dividend", days=90)
      get_corporate_actions(symbols="TCS,INFY,HDFCBANK")
    """
    return await _safe(_get_corporate_actions)(symbol, days, action_type, symbols)


@mcp.tool(annotations={"title": "Earnings Calendar", "readOnlyHint": True, "openWorldHint": True})
async def get_earnings_calendar(symbols: str = "", days: int = 30,
                                purpose: str = "results") -> dict:
    """
    Upcoming board meetings, results dates, AGMs and dividend events from NSE.

    symbols: optional comma-separated list ("TCS,INFY"); omit for the whole
             market (first 100 events returned)
    days: look-ahead window, 1-180 (default 30)
    purpose: all | results (default) | board_meeting | agm | dividend

    Use it with the portfolio/watchlist tools to know when results drop.

    Examples:
      get_earnings_calendar("TCS,INFY,HDFCBANK")
      get_earnings_calendar(days=15, purpose="all")
    """
    return await _safe(_get_earnings_calendar)(symbols, days, purpose)


@mcp.tool(annotations={"title": "Search Announcements", "readOnlyHint": True, "openWorldHint": True})
async def search_announcements(query: str = "", category: str = "all", days: int = 7,
                               symbols: str = "", min_materiality: str = "low",
                               limit: int = 50) -> dict:
    """
    Search NSE corporate announcements **across the whole market** — order
    wins, capacity expansion, credit-rating actions, auditor changes, promoter
    pledges, fund raises, results, insider filings and more.

    The per-company get_company_announcements tool answers "what did this
    company file?"; this one answers "who in the market just announced X?" —
    the idea-generation and early-warning workflow. NSE's feed is public;
    Screener's equivalent announcements page is premium-gated.

    query: keyword in the headline/company name ("order", "capex", "rating")
    category: all | order_win | capex | credit_rating | auditor | pledge |
              resignation | acquisition | buyback | fund_raise | dividend |
              bonus_split | results | board_meeting | agm | insider_trading |
              esg_rating | delisting | general
    days: look-back, 1-90 (default 7)
    symbols: optional comma-separated restriction (max 30)
    min_materiality: low | medium | high
    limit: max filings returned, 1-200 (default 50)

    Examples:
      search_announcements(category="order_win", days=7)
      search_announcements("auditor", category="auditor", days=30)
      search_announcements(category="credit_rating", days=30, min_materiality="high")
    """
    return await _safe(_search_announcements)(
        query, category, days, symbols, min_materiality, limit,
    )


# ─── Commodity Analysis ────────────────────────────────────────────────────────

@mcp.tool(annotations={"title": "Get Commodity Prices", "readOnlyHint": True, "openWorldHint": True})
async def get_commodity_prices(commodity: str, years: int = 5) -> dict:
    """
    Commodity price context and its impact on Indian listed companies.

    Returns the international benchmark price the MCX/NCDEX contract tracks
    (COMEX gold/silver/copper, Brent, Henry Hub, CBOT wheat, etc.), with
    4-week, 52-week and `years`-period moves and range, USD/INR, and an
    approximate INR price (a pure FX conversion — excludes import duty/GST, so
    below MCX). Also lists which companies benefit or suffer from price moves
    and their symbols (data.watch_symbols) to pass to compare_companies.

    commodity: gold | silver | crude_oil | copper | aluminium | zinc | nickel
               | cotton | natural_gas | steel | wheat | tobacco | wood_pulp
               (nickel, tobacco, wood_pulp: no free price feed → partial,
               exposure analysis only)
    years: history period for the moves and range (1–10, default 5)

    Examples:
      get_commodity_prices("crude_oil")
      get_commodity_prices("copper", years=3)
      get_commodity_prices("gold")
    """
    return await _safe(_get_commodity_prices)(commodity, years)


# ─── Research Notebook ────────────────────────────────────────────────────────

@mcp.tool(annotations={"title": "Notebook AI (Research Notes)", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False})
async def notebook_ai(
    action: str,
    symbol: str = "",
    content: str = "",
    note_id: str = "",
) -> dict:
    """
    Save, read, and AI-summarize your investment research notes locally.

    Notes are stored in ~/.screener-mcp/notebooks/ — persists across sessions.

    action:
      "create"    — new note (requires symbol + content)
      "append"    — add to existing note (requires note_id + content)
      "read"      — read a note (requires note_id)
      "list"      — list all notes (optional symbol filter)
      "summarize" — AI-structured summary of all notes for a symbol
      "delete"    — delete a note (requires note_id)

    Examples:
      notebook_ai("create", symbol="TCS", content="Q3 results strong — revenue beat...")
      notebook_ai("append", note_id="a1b2c3d4", content="Met management — bullish on BFSI...")
      notebook_ai("list", symbol="TCS")
      notebook_ai("summarize", symbol="TCS")
      notebook_ai("read", note_id="a1b2c3d4")
    """
    return await _safe(_notebook_ai)(action, symbol or None, content or None, note_id or None)


# ─── Portfolio ──────────────────────────────────────────────────────────────────

@mcp.tool(annotations={"title": "Add Portfolio Stock", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False})
async def add_portfolio_stock(symbol: str, quantity: float, avg_price: float) -> dict:
    """
    Add a holding to your local portfolio (~/.screener-mcp/portfolio.json).

    If you already hold this symbol, the new lot merges into the existing
    position with a quantity-weighted average price — like a real broker
    ledger, buying more at a different price updates your average cost
    rather than overwriting it.

    symbol: NSE/BSE symbol (e.g., "RELIANCE")
    quantity: number of shares in this lot
    avg_price: price per share for this lot

    Examples:
      add_portfolio_stock("RELIANCE", quantity=10, avg_price=1350)
      add_portfolio_stock("TCS", quantity=5, avg_price=3800)
    """
    return await _safe(_add_portfolio_stock)(symbol, quantity, avg_price)


@mcp.tool(annotations={"title": "Update Portfolio Stock", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def update_portfolio_stock(
    symbol: str,
    quantity: float = 0.0,
    avg_price: float = 0.0,
) -> dict:
    """
    Overwrite quantity and/or average price for an existing holding — e.g.
    after a partial sell (set the new remaining quantity) or to correct
    your recorded cost basis.

    Use `add_portfolio_stock` instead for a new buy lot — it recalculates
    the average price for you rather than replacing it.

    symbol: NSE/BSE symbol already in your portfolio
    quantity: new total share count (0 = leave unchanged)
    avg_price: new average price (0 = leave unchanged)

    Examples:
      update_portfolio_stock("RELIANCE", quantity=5)   # sold half
      update_portfolio_stock("TCS", avg_price=3750)     # correct cost basis
    """
    return await _safe(_update_portfolio_stock)(
        symbol,
        quantity if quantity else None,
        avg_price if avg_price else None,
    )


@mcp.tool(annotations={"title": "Remove Portfolio Stock", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False})
async def remove_portfolio_stock(symbol: str) -> dict:
    """
    Remove a holding entirely from the portfolio (full exit).

    Examples:
      remove_portfolio_stock("YESBANK")
    """
    return await _safe(_remove_portfolio_stock)(symbol)


@mcp.tool(annotations={"title": "Get Portfolio", "readOnlyHint": True, "openWorldHint": True})
async def get_portfolio() -> dict:
    """
    View your portfolio with live prices, P&L, and per-holding weight.

    Fetches the current price for each holding from Screener.in and computes
    invested value, current value, gain/loss (₹ and %), and each position's
    weight in the total portfolio.

    Examples:
      get_portfolio()
    """
    return await _safe(_get_portfolio)()


@mcp.tool(annotations={"title": "Add Watchlist Stock", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
async def add_watchlist_stock(symbol: str) -> dict:
    """
    Add a company to the local watchlist (idempotent). State lives at
    ~/.screener-mcp/watchlist.json. Use get_watchlist_feed to see what changed.

    symbol: NSE/BSE symbol or company name
    """
    return await _safe(_add_watchlist_stock)(symbol)


@mcp.tool(annotations={"title": "Remove Watchlist Stock", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False})
async def remove_watchlist_stock(symbol: str) -> dict:
    """Remove a company from the local watchlist."""
    return await _safe(_remove_watchlist_stock)(symbol)


@mcp.tool(annotations={"title": "Get Watchlist", "readOnlyHint": True, "openWorldHint": False})
async def get_watchlist() -> dict:
    """The saved watchlist with names and when each company was added."""
    return await _safe(_get_watchlist)()


@mcp.tool(annotations={"title": "Watchlist Feed", "readOnlyHint": True, "openWorldHint": True})
async def get_watchlist_feed(symbols: str = "", days: int = 7) -> dict:
    """
    Everything material that changed across the watchlist in one call:
      upcoming results/board meetings, corporate actions, material
      announcements (order wins, capex, ratings, auditor changes) and a price
      snapshot with 1-day/1-week/1-month moves.

    symbols: optional comma-separated override (max 20); default = the saved
             watchlist
    days: announcement look-back (1-90, default 7); events look 30 days ahead,
          corporate actions 90 days back + 45 days ahead

    Use it for a weekly review instead of calling five tools per company.
    """
    return await _safe(_get_watchlist_feed)(symbols, days)


@mcp.tool(annotations={"title": "Portfolio Review", "readOnlyHint": True, "openWorldHint": True})
async def get_portfolio_review(days: int = 7) -> dict:
    """
    Your portfolio's live P&L plus the same event feed as get_watchlist_feed
    for every holding: upcoming results, corporate actions, material
    announcements and 1d/1w/1m price moves. One call for a portfolio review.

    days: announcement look-back (default 7)
    """
    return await _safe(_get_portfolio_review)(days)


# ─── Resources ────────────────────────────────────────────────────────────────

@mcp.resource("screener://analyst-guide")
def analyst_guide() -> str:
    """How to use the Screener Stock Research assistant."""
    return """
# Screener Stock Research — Analyst Guide

## What I can do

I'm your Indian stock research copilot powered by Screener.in data.

### Ask me naturally:
- "Find chemical stocks with low debt and strong growth"
- "Compare ITC and HUL"
- "Explain Jyothy Labs like I'm a beginner"
- "What are the red flags in Asian Paints?"
- "Find hidden gems below ₹5000 crore market cap"
- "Which companies benefit from EV adoption?"
- "Give me businesses with improving ROCE for the last 5 years"
- "Find turnaround stories in mid-cap space"
- "What is Titan's promoter holding trend?"
- "Show me the last 4 quarters for HDFC Bank"
- "Quality stocks within 10% of their 52-week low"
- "Has MSUMI fallen more than the auto sector over the last 60 days?"
- "Which of these stocks trade below their industry P/E?"
- "Is MSUMI a market leader with durable returns?"
- "What's BEL's order pipeline, guidance and analyst estimates?"
- "Analyst targets and recent news for TMPV"

### What I fetch from Screener.in:
- 10+ years of financials (P&L, Balance Sheet, Cash Flow)
- Historical key ratios (PE, ROCE, ROE, Debt/Equity, etc.)
- Quarterly results (last 8 quarters)
- Shareholding patterns (promoter, FII, DII)
- Peer comparison tables
- Stock screening by custom filters (AND / OR / parentheses), technical
  clauses, or pre-built themes, with junk rows filtered out
- Industry pages: medians, revenue share and concentration for every industry

### Beyond Screener.in:
- NSE filings: announcements, credit/ESG ratings, order wins, capex, bulk deals, insider trades
- Yahoo Finance: analyst consensus targets, EPS/revenue estimates
- Google News: recent headlines and broker target mentions

Every tool returns {status, partial, warnings, data, ...}. Treat status
"partial" or "error" as real signals, and never read null as zero.

### Setup for full data:
Set in your environment:
  SCREENER_USERNAME=your@email.com
  SCREENER_PASSWORD=yourpassword

Without login, some data fields may be restricted (Screener.in requires login for full data).

## Limitations
- Financial statements come from Screener.in and may lag by 1 quarter; check
  price_freshness on the overview for how current the price is
- I cannot predict stock prices or guarantee returns
- Always verify critical data directly on Screener.in
- Past financial performance does not guarantee future results
"""


@mcp.resource("screener://query-syntax")
def query_syntax_guide() -> str:
    """Screener.in query language reference for stock screening."""
    return """
# Screener.in Query Language Reference

## Syntax
  FIELD OPERATOR VALUE [AND FIELD OPERATOR VALUE ...]

## Operators
  >  <  >=  <=  =       comparisons
  AND  OR  ( )          combine conditions, e.g.
      (Return on capital employed > 20 OR Return on equity > 25) AND Debt to equity < 0.5

## Technical clauses (evaluated from daily prices; mix freely with the above)
  52 week low distance < 10        % above the 52-week low
  52 week high distance > 30       % below the 52-week high
  RSI < 30                         14-day RSI
  Price above 200 DMA              also below / 20, 50, 200 DMA / "50 DMA above 200 DMA"
  Price vs 50 DMA < -5             % above(+)/below(−) a moving average
  Volume vs 20 day average > 2     volume spike

## Common Fields (exact spelling)
  Market Capitalization          (₹ Crore)
  Current Price
  Price to Earning               (PE ratio)
  Price to book value            (PB ratio)
  EV / EBITDA
  PEG Ratio
  Dividend yield                 (%)
  Return on capital employed     (%)
  Return on equity               (%)
  Debt to equity
  Current ratio
  Pledged percentage             (%)
  Sales growth 5Years            (%)
  Sales growth 3Years            (%)
  Sales growth last year         (%)
  Profit growth 5Years           (%)
  Profit growth 3Years           (%)
  Profit growth last year        (%)
  Average return on equity 5Years
  Average return on capital employed 5Years
  Net cash flow last year

## Example Queries

# Classic GARP (Growth at Reasonable Price)
Price to Earning < 25 AND Profit growth 5Years > 15 AND Return on equity > 15

# Deep Value Small Cap
Market Capitalization < 2000 AND Price to Earning < 15 AND Debt to equity < 0.5

# Quality Compounder
Sales growth 5Years > 15 AND Profit growth 5Years > 15 AND Return on capital employed > 20 AND Debt to equity < 0.3

# High Dividend + Quality
Dividend yield > 3 AND Return on equity > 15 AND Debt to equity < 0.5

# Turnaround Candidate
Profit growth last year > 30 AND Profit growth 3Years > 20 AND Debt to equity < 1

# Momentum + Quality
Profit growth 5Years > 20 AND Sales growth 5Years > 20 AND Return on capital employed > 20
"""


# ─── MCP App UI Resource ──────────────────────────────────────────────────────

# The widget runs in the host's sandboxed iframe, whose CSP blocks CDN script
# loads, so the MCP Apps runtime (vendored in ui/) is inlined into the HTML.
# Its trailing ES `export {...}` is rewritten to a `globalThis.ExtApps` global
# because the inlined code runs as a classic block inside the module script.
def _inline_ext_apps(html: str) -> str:
    bundle = (_UI_DIR / "ext-apps-app-with-deps.js").read_text()

    def to_global(m: re.Match) -> str:
        pairs = []
        for part in m.group(1).split(","):
            local, _, exported = (x.strip() for x in part.partition(" as "))
            pairs.append(f"{exported or local}:{local}")
        return "globalThis.ExtApps={" + ",".join(pairs) + "};"

    bundle = re.sub(r"export\{([^}]+)\};?\s*$", to_global, bundle)
    return html.replace("/*__EXT_APPS_BUNDLE__*/", bundle)


_stock_comparison_html = _inline_ext_apps((_UI_DIR / "stock_comparison.html").read_text())


@mcp.resource(
    STOCK_COMPARISON_WIDGET_URI,
    name="Stock Comparison Dashboard",
    description="Interactive dashboard rendered for compare_companies results.",
    mime_type=MCP_APP_MIME_TYPE,
)
def stock_comparison_ui() -> str:
    """Stock Comparison Dashboard — interactive UI for comparing multiple stocks."""
    return _stock_comparison_html


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    """Plain HTTP health check for platforms that poll a non-MCP endpoint
    to confirm the instance is up."""
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok"})


# ─── Entry point ──────────────────────────────────────────────────────────────

def http_bind() -> tuple[str, int]:
    """Host/port for the HTTP transports (SDK v2 moved these off the constructor).

    The default is loopback: an unauthenticated server holding Screener
    credentials must not be exposed to the network by accident. Docker opts in
    to 0.0.0.0 via MCP_HOST.
    """
    return (
        os.getenv("MCP_HOST", "127.0.0.1"),
        int(os.getenv("PORT") or os.getenv("MCP_PORT") or "8000"),
    )


def main():
    # MCP_TRANSPORT=streamable-http to run as a network server (for remote
    # MCP clients that need an HTTP server URL) instead of the default stdio
    # mode used by claude mcp add / local desktop clients.
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    host, port = http_bind()
    mcp.run(transport=transport, host=host, port=port)


if __name__ == "__main__":
    main()
