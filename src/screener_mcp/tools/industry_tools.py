"""
Industry / sector overview from Screener.in's public industry pages.

`get_relative_valuation` compares a stock with the median of its industry;
this goes wider: how many listed companies the industry has, how they are
distributed on P/E and ROCE, who the leaders are, which quality names are
cheap, and where a given stock sits.

The industry pages are public (no login), 25 rows a page, sorted by market
cap. Medians use companies with market cap ≥ ₹500 Cr and a positive P/E, the
same hygiene as `get_relative_valuation`.
"""

import difflib
import re
import statistics
import time
from typing import Optional

from bs4 import BeautifulSoup

from ..client import get_client
from ..core.company_page import fetch_company_page
from ..core.envelope import ToolError, ToolResult
from ..core.industry import MEDIAN_MIN_MCAP, fetch_industry, industry_stats, parse_classification, position_in
from ..parsers.company import parse_overview

_LEVELS = ("Industry", "Broad Industry", "Sector", "Broad Sector")
_market_cache: Optional[tuple[float, list[tuple[str, str]]]] = None
_MARKET_TTL = 3600


def _median(values: list[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 2) if vals else None


async def _market_links() -> list[tuple[str, str]]:
    """[(industry name, url)] from Screener's /market/ index (cached 1 hour)."""
    global _market_cache
    if _market_cache and time.monotonic() - _market_cache[0] < _MARKET_TTL:
        return _market_cache[1]
    client = await get_client()
    html = await client.get_html("/market/")
    soup = BeautifulSoup(html, "lxml")
    links, seen = [], set()
    for a in soup.select('a[href^="/market/"]'):
        name = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
        href = a.get("href", "")
        if name and href and href not in seen:
            seen.add(href)
            links.append((name, href))
    _market_cache = (time.monotonic(), links)
    return links


def _best_link(query: str, links: list[tuple[str, str]]) -> tuple[Optional[tuple[str, str]], list[str]]:
    """(match, ambiguous_candidates). Exact and single word matches win; an
    ambiguous word match returns candidates instead of guessing."""
    q = query.strip().lower()
    if not q:
        return None, []
    exact = [x for x in links if x[0].lower() == q]
    if exact:
        return exact[0], []
    word = re.compile(rf"\b{re.escape(q)}\b", re.I)
    word_matches = [x for x in links if word.search(x[0])]
    if len(word_matches) == 1:
        return word_matches[0], []
    if len(word_matches) > 1:
        return None, [x[0] for x in word_matches[:8]]
    contains = [x for x in links if q in x[0].lower()]
    if len(contains) == 1:
        return contains[0], []
    if contains:
        return sorted(
            contains,
            key=lambda x: (len(x[0]), -difflib.SequenceMatcher(None, q, x[0].lower()).ratio()),
        )[0], []
    names = [x[0] for x in links]
    close = difflib.get_close_matches(query, names, n=3, cutoff=0.55)
    if close:
        return next(x for x in links if x[0] == close[0]), []
    return None, []


async def _resolve_industry(query: str) -> tuple[str, str, Optional[object]]:
    """(name, url, company_page_or_none) from a name, URL, or company symbol."""
    raw = (query or "").strip()
    if not raw:
        raise ToolError("Pass an industry name, a Screener industry URL, or a company symbol.", "invalid_input")
    if "/market/" in raw:
        url = raw[raw.index("/market/"):]
        return url, url, None

    links = await _market_links()
    match, ambiguous = _best_link(raw, links)
    if match:
        return match[0], match[1], None
    if ambiguous:
        raise ToolError(
            f"'{query}' matches several industries. Pick one and retry.",
            "ambiguous_industry",
            candidates=ambiguous,
        )

    # Not a leaf industry name — try it as a company symbol and use its finest
    # Screener classification (this is also how a broad industry like
    # "IT - Software" is reached, since Screener's /market/ index lists only
    # leaf industries).
    try:
        page = await fetch_company_page(raw, "consolidated")
        cls = parse_classification(page.html)
        for level in _LEVELS:
            if level in cls:
                return cls[level]["name"], cls[level]["url"], page
    except Exception:
        pass

    raise ToolError(
        f"Could not find an industry matching '{query}'. Pass a leaf industry name "
        f"(e.g. 'Specialty Chemicals', 'Pharmaceuticals', 'Cement & Cement Products'), a Screener "
        f"industry URL, or a company symbol such as 'TCS' — a symbol resolves to its finest Screener "
        f"classification (for example, TCS → 'IT - Software').",
        "not_found",
        candidates=[name for name, _ in links[:8]],
    )


def _company_row(r: dict) -> dict:
    return {
        "symbol": r.get("symbol"),
        "name": r.get("name"),
        "price": r.get("price"),
        "market_cap_cr": r.get("market_cap"),
        "pe": r.get("pe"),
        "pb": r.get("pb"),
        "roce": r.get("roce"),
        "dividend_yield": r.get("dividend_yield"),
        "sales_qtr_cr": r.get("sales_qtr"),
        "sales_growth_yoy_pct": r.get("sales_growth_yoy"),
        "net_profit_qtr_cr": r.get("net_profit_qtr"),
        "profit_growth_yoy_pct": r.get("profit_growth_yoy"),
    }


def _bands(rows: list[dict], key: str, cuts: list[float], labels: list[str]) -> dict:
    out = {label: 0 for label in labels}
    out["n/a"] = 0
    for r in rows:
        v = r.get(key)
        if v is None:
            out["n/a"] += 1
            continue
        placed = False
        for cut, label in zip(cuts, labels):
            if v < cut:
                out[label] += 1
                placed = True
                break
        if not placed:
            out[labels[-1]] += 1
    return out


def _overview_stats(rows: list[dict]) -> dict:
    sizable = [r for r in rows if (r.get("market_cap") or 0) >= MEDIAN_MIN_MCAP]
    pe_rows = [r for r in sizable if r.get("pe") and 0 < r["pe"] < 500]
    roce_rows = [r for r in sizable if r.get("roce") is not None]
    margin_rows = [r for r in rows if r.get("sales_qtr") and r["sales_qtr"] > 0
                   and r.get("net_profit_qtr") is not None]
    total_mcap = sum(r["market_cap"] for r in rows if r.get("market_cap"))
    by_mcap = sorted(rows, key=lambda r: r.get("market_cap") or 0, reverse=True)
    top5 = sum(r["market_cap"] for r in by_mcap[:5] if r.get("market_cap"))
    return {
        "companies_total": len(rows),
        "companies_in_medians": len(sizable),
        "median_pe": _median([r["pe"] for r in pe_rows]),
        "median_pb": _median([r["pb"] for r in sizable]),
        "median_roce": _median([r["roce"] for r in roce_rows]),
        "median_sales_growth_yoy": _median([r["sales_growth_yoy"] for r in sizable]),
        "median_profit_growth_yoy": _median([r["profit_growth_yoy"] for r in sizable]),
        "median_dividend_yield": _median([r["dividend_yield"] for r in sizable]),
        "median_net_margin": _median([
            r["net_profit_qtr"] / r["sales_qtr"] * 100 for r in margin_rows
        ]),
        "median_market_cap_cr": _median([r["market_cap"] for r in sizable]),
        "total_market_cap_cr": round(total_mcap, 2) if total_mcap else None,
        "top5_market_cap_share_pct": round(top5 / total_mcap * 100, 2) if total_mcap else None,
        "pe_distribution": _bands(rows, "pe", [10, 20, 30, 50, 75],
                                  ["<10", "10-20", "20-30", "30-50", "50-75", ">75"]),
        "roce_distribution": _bands(rows, "roce", [10, 15, 20, 30],
                                    ["<10%", "10-15%", "15-20%", "20-30%", ">30%"]),
        "median_basis": f"companies with market cap ≥ ₹{MEDIAN_MIN_MCAP} Cr; positive P/E only",
    }


def _pick(rows: list[dict], predicate, key, reverse: bool, n: int = 10) -> list[dict]:
    picked = [r for r in rows if (r.get("market_cap") or 0) >= MEDIAN_MIN_MCAP and predicate(r)]
    picked.sort(key=lambda r: r.get(key) if r.get(key) is not None else float("-inf"), reverse=reverse)
    return [_company_row(r) for r in picked[:n]]


async def get_industry_overview(
    industry: str = "",
    symbol: str = "",
    top_n: int = 15,
    max_pages: int = 8,
) -> ToolResult:
    """Industry map: medians, distribution, leaders and a stock's position."""
    top_n = max(5, min(int(top_n or 15), 50))
    max_pages = max(1, min(int(max_pages or 8), 12))

    warnings: list[str] = []
    page = None
    if symbol.strip():
        page = await fetch_company_page(symbol, "consolidated")
        cls = parse_classification(page.html)
        level = next((lvl for lvl in _LEVELS if lvl in cls), None)
        if not level:
            raise ToolError(f"No industry classification on {page.symbol}'s Screener page.", "no_data")
        own_name, own_url = cls[level]["name"], cls[level]["url"]
        if not industry.strip():
            industry, url = own_name, own_url
        else:
            # A broad name such as "IT - Software" is in the company's
            # classification even though Screener's /market/ index lists only
            # leaf industries, so match the classification levels first.
            same = next((v for v in cls.values() if v["name"].lower() == industry.strip().lower()), None)
            if same:
                industry, url = same["name"], same["url"]
            else:
                try:
                    _, url, _ = await _resolve_industry(industry)
                except ToolError:
                    warnings.append(
                        f"Could not resolve industry '{industry}' to a public industry page; used "
                        f"{page.symbol}'s Screener classification '{own_name}' instead."
                    )
                    industry, url = own_name, own_url
    else:
        industry, url, page = await _resolve_industry(industry)
        if page is not None:
            industry = industry

    data = await fetch_industry(url, max_pages=max_pages, by="mcap")
    industry = data.get("name") or industry
    rows = data["rows"]
    if not rows:
        raise ToolError(f"Screener's industry page returned no companies for {industry}.", "no_data")

    stats = _overview_stats(rows)
    if data["fetched_companies"] < data["total_companies"]:
        warnings.append(
            f"Fetched {data['fetched_companies']} of {data['total_companies']} companies — the tail is "
            f"below ₹{MEDIAN_MIN_MCAP} Cr or beyond the page cap. Medians are based on the fetched set."
        )
    if data["fetched_companies"] == data["total_companies"] and len(rows) < 5:
        warnings.append("This industry has fewer than 5 listed companies; medians are not very meaningful.")

    by_mcap = sorted(rows, key=lambda r: r.get("market_cap") or 0, reverse=True)
    leaders = [_company_row(r) for r in by_mcap[:top_n]]
    quality_value = _pick(
        rows,
        lambda r: r.get("pe") and 0 < r["pe"] < (stats["median_pe"] or 999)
        and r.get("roce") is not None and r["roce"] > (stats["median_roce"] or 0),
        "pe", reverse=False,
    )
    growth_leaders = _pick(
        rows,
        lambda r: r.get("profit_growth_yoy") is not None and 0 < r["profit_growth_yoy"] < 500,
        "profit_growth_yoy", reverse=True,
    )
    cheapest = _pick(rows, lambda r: r.get("pe") and 0 < r["pe"] < 500, "pe", reverse=False)
    expensive = _pick(rows, lambda r: r.get("pe") and 0 < r["pe"] < 500, "pe", reverse=True, n=5)

    out = {
        "industry": industry,
        "url": url,
        "as_of": date_today(),
        "companies_in_page": data["total_companies"],
        "companies_analyzed": data["fetched_companies"],
        "medians": {k: v for k, v in stats.items()
                    if k.startswith("median_") and k != "median_basis"},
        "distribution": {
            "pe": stats["pe_distribution"],
            "roce": stats["roce_distribution"],
            "total_market_cap_cr": stats["total_market_cap_cr"],
            "top5_market_cap_share_pct": stats["top5_market_cap_share_pct"],
        },
        "leaders_by_market_cap": leaders,
        "quality_at_a_reasonable_price": quality_value,
        "growth_leaders": growth_leaders,
        "cheapest": cheapest,
        "most_expensive": expensive,
        "method": stats["median_basis"],
        "notes": [
            "Screener's industry page mixes all listed companies, including very small or shell-like names; "
            "medians therefore exclude companies below ₹500 Cr and non-positive/ >500x P/E.",
            "This is a cross-sectional map, not a recommendation. Use the company tools to check whether a "
            "cheap P/E reflects a real discount or a deteriorating business.",
        ],
    }

    if page is not None:
        ov = parse_overview(page.html)
        row = next((r for r in rows if r.get("symbol") == page.symbol), None)
        pos = position_in(industry_stats(data), page.company_id)
        if row:
            out["symbol_position"] = {
                "symbol": page.symbol,
                "name": ov.get("name"),
                "market_cap_rank": next((i for i, r in enumerate(by_mcap, 1) if r.get("symbol") == page.symbol), None),
                "of_companies_analyzed": len(rows),
                "companies_total": data["total_companies"],
                "pe": row.get("pe"),
                "pe_vs_median_pct": (round((row["pe"] / stats["median_pe"] - 1) * 100, 1)
                                     if row.get("pe") and stats["median_pe"] else None),
                "roce": row.get("roce"),
                "roce_vs_median_pp": (round(row["roce"] - stats["median_roce"], 1)
                                      if row.get("roce") is not None and stats["median_roce"] is not None else None),
                "sales_growth_vs_median_pp": (round(row["sales_growth_yoy"] - stats["median_sales_growth_yoy"], 1)
                                              if row.get("sales_growth_yoy") is not None
                                              and stats["median_sales_growth_yoy"] is not None else None),
                "profit_growth_vs_median_pp": (round(row["profit_growth_yoy"] - stats["median_profit_growth_yoy"], 1)
                                               if row.get("profit_growth_yoy") is not None
                                               and stats["median_profit_growth_yoy"] is not None else None),
                "revenue_share_pct": (pos or {}).get("revenue_share_pct"),
                "revenue_rank": (pos or {}).get("revenue_rank"),
            }
        else:
            warnings.append(f"{page.symbol} is not in the fetched part of the {industry} industry page; "
                            "its position could not be ranked.")
    return ToolResult(data=out, warnings=warnings, meta=page.meta if page else {})


def date_today() -> str:
    from datetime import date
    return date.today().isoformat()
