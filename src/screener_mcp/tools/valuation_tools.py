"""
Valuation tools that turn Screener.in's price and multiple history into
decisions rather than raw numbers:

  get_valuation_bands — where the current P/E, P/B, EV/EBITDA and Market
                        Cap/Sales sit in the company's own 5/10-year range
  value_scenarios     — a transparent forward scenario (sales growth, margin,
                        tax, exit P/E) plus the growth/exit multiple the
                        current price implies

Both are deterministic calculations over data the other tools already fetch;
neither downloads a new kind of data or calls an LLM.
"""

from datetime import date, datetime
from typing import Literal, Optional

from ..core import history as hist
from ..core.company_page import fetch_company_page
from ..core.envelope import ToolError, ToolResult
from ..core.indices import resolve_index
from ..core.numbers import to_number
from ..core.quality import is_financial, overview_field
from ..core.technicals import PriceHistory, fetch_price_history
from ..core.valuation_history import band_stats, fetch_multiples
from ..parsers.company import parse_overview, parse_profit_loss

FinancialType = Literal["consolidated", "standalone"]

_LABELS = {
    "pe": "P/E",
    "pb": "P/B",
    "ev_ebitda": "EV/EBITDA",
    "mcap_sales": "Market Cap/Sales",
}
_UNITS = {
    "pe": "x earnings",
    "pb": "x book",
    "ev_ebitda": "x EBITDA",
    "mcap_sales": "x sales",
}


def _band_line(metric: str, band: dict) -> str:
    label = _LABELS[metric]
    p = band["percentile"]
    med = band["median"]
    cur = band["current"]
    return (
        f"{label} {cur:.1f}{_UNITS[metric]} — {p:.0f}th percentile of its last "
        f"{band['window_years']:.0f} years (median {med:.1f}, range {band['min']:.1f}–{band['max']:.1f}); "
        f"{band['verdict']}."
    )


async def get_valuation_bands(symbol: str, years: int = 10,
                              financial_type: FinancialType = "consolidated") -> ToolResult:
    """Current valuation multiples vs their own 5/10-year distribution."""
    years = max(1, min(int(years or 10), 10))
    page = await fetch_company_page(symbol, financial_type)
    if not page.company_id:
        raise ToolError(f"Could not resolve a Screener company id for {page.symbol}.", "no_data")
    ov = parse_overview(page.html)
    name = ov.get("name") or page.symbol
    warnings = list(page.warnings)

    multiples = await fetch_multiples(page.company_id, page.financial_type == "consolidated")
    metrics: dict[str, dict] = {}
    missing: list[str] = []
    for key, series in multiples.items():
        band = band_stats(series, years)
        if band:
            metrics[key] = band
        else:
            missing.append(key)

    if not metrics:
        raise ToolError(
            f"No valuation history returned for {page.symbol}. The chart API may be unavailable, or the "
            "company has too little trading history for a percentile band.",
            "no_data",
        )
    if missing:
        warnings.append(
            "No usable history for: " + ", ".join(_LABELS[m] for m in missing)
            + " (loss-making/negative denominator, or too few observations)."
        )

    financial = is_financial(ov.get("sectors", []))
    if financial and ("ev_ebitda" in metrics or "mcap_sales" in metrics):
        warnings.append(
            "Financial company — EV/EBITDA and Market Cap/Sales are less meaningful for lenders; "
            "P/B and P/E are the primary valuation multiples."
        )

    current_price = to_number(overview_field(ov, "current_price"))
    overview_pe = to_number(overview_field(ov, "pe"))
    summary = " ".join(_band_line(k, v) for k, v in metrics.items())
    data = {
        "symbol": page.symbol,
        "name": name,
        "financial_type": page.financial_type,
        "current_price": current_price,
        "market_cap_cr": to_number(overview_field(ov, "market_cap")),
        "window_years": years,
        "metrics": metrics,
        "summary": summary,
        "method": (
            "Multiple history from Screener.in's chart API (weekly points at the 10-year range), compared "
            f"with every observation in the last {years} year(s). Percentile = share of observations at or "
            "below today's value. Values ≤ 0 and > 500x are excluded because they come from a near-zero "
            "denominator or a loss."
        ),
        "notes": [
            "A low percentile means the stock is cheap relative to its own history, not necessarily "
            "cheap in absolute terms or versus its industry — use get_relative_valuation for that.",
            "P/E here uses Screener's chart series (trailing EPS); the overview's Stock P/E can differ "
            "slightly because it uses the latest reported EPS.",
        ],
    }
    if overview_pe is not None:
        data["overview_pe"] = overview_pe
        if "pe" in metrics and abs(metrics["pe"]["current"] - overview_pe) >= 0.5:
            data["notes"].append(
                f"Overview P/E is {overview_pe:.2f} while the chart's latest P/E is "
                f"{metrics['pe']['current']:.2f}; the percentile uses the chart series for consistency."
            )
    return ToolResult(data=data, warnings=warnings, missing_fields=missing, meta=page.meta)


# ─── forward scenarios ────────────────────────────────────────────────────────

def _latest(series: dict) -> Optional[float]:
    for value in reversed(list(series.values())):
        if value is not None:
            return value
    return None


def _price_for_growth(growth: float, sales0: float, shares0: float, horizon: int,
                      model: str, opm: float, net_margin: float, tax: float,
                      other_pct: float, interest_pct: float, dep_pct: float,
                      exit_pe: float, dilution: float) -> float:
    sales_t = sales0 * ((1 + growth) ** horizon)
    shares_t = shares0 * ((1 + dilution) ** horizon)
    if model == "net_margin":
        pat = sales_t * net_margin
    else:
        ebitda = sales_t * opm
        pbt = ebitda + sales_t * other_pct - sales_t * interest_pct - sales_t * dep_pct
        pat = pbt * (1 - tax)
    eps = pat / shares_t if shares_t > 0 else 0.0
    return eps * exit_pe


def _solve_growth(target_price: float, **kwargs) -> Optional[float]:
    """Bisection: the sales growth that makes the horizon price equal target."""
    def price(g):
        return _price_for_growth(g, **kwargs)

    lo, hi = -0.5, 1.0
    p_lo, p_hi = price(lo), price(hi)
    if p_lo is None or p_hi is None or not (p_lo <= target_price <= p_hi):
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if price(mid) < target_price:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2 * 100, 2)


def _scenario_price(sales0: float, shares0: float, horizon: int, model: str,
                    growth: float, opm: float, net_margin: float, tax: float,
                    other_pct: float, interest_pct: float, dep_pct: float,
                    exit_pe: float, dilution: float) -> tuple[list[dict], float, float]:
    rows = []
    for t in range(1, horizon + 1):
        sales_t = sales0 * ((1 + growth) ** t)
        shares_t = shares0 * ((1 + dilution) ** t)
        if model == "net_margin":
            pat = sales_t * net_margin
            ebitda = pbt = None
        else:
            ebitda = sales_t * opm
            pbt = ebitda + sales_t * other_pct - sales_t * interest_pct - sales_t * dep_pct
            pat = pbt * (1 - tax)
        eps = pat / shares_t if shares_t > 0 else None
        rows.append({
            "year": t,
            "sales_cr": round(sales_t, 2),
            "ebitda_cr": round(ebitda, 2) if ebitda is not None else None,
            "pbt_cr": round(pbt, 2) if pbt is not None else None,
            "pat_cr": round(pat, 2),
            "eps": round(eps, 2) if eps is not None else None,
            "implied_price": round(eps * exit_pe, 2) if eps is not None else None,
        })
    last = rows[-1]
    return rows, last["pat_cr"], last["eps"]


async def value_scenarios(
    symbol: str,
    horizon_years: int = 5,
    sales_growth_pct: Optional[float] = None,
    opm_pct: Optional[float] = None,
    net_margin_pct: Optional[float] = None,
    tax_rate_pct: Optional[float] = None,
    exit_pe: Optional[float] = None,
    dilution_pct: float = 0.0,
    model: Literal["auto", "operating", "net_margin"] = "auto",
    financial_type: FinancialType = "consolidated",
) -> ToolResult:
    """Forward earnings scenario + the expectations embedded in the price."""
    horizon = max(1, min(int(horizon_years or 5), 10))
    dilution = max(-0.1, min(float(dilution_pct or 0) / 100, 0.2))
    if sales_growth_pct is not None and not -50 <= sales_growth_pct <= 100:
        raise ToolError("sales_growth_pct must be between -50 and 100.", "invalid_input")
    if opm_pct is not None and not -50 <= opm_pct <= 100:
        raise ToolError("opm_pct must be between -50 and 100.", "invalid_input")
    if net_margin_pct is not None and not -50 <= net_margin_pct <= 100:
        raise ToolError("net_margin_pct must be between -50 and 100.", "invalid_input")
    if tax_rate_pct is not None and not 0 <= tax_rate_pct <= 60:
        raise ToolError("tax_rate_pct must be between 0 and 60.", "invalid_input")
    if exit_pe is not None and not 1 <= exit_pe <= 200:
        raise ToolError("exit_pe must be between 1 and 200.", "invalid_input")
    if model not in ("auto", "operating", "net_margin"):
        raise ToolError("model must be auto, operating or net_margin.", "invalid_input")
    if model == "operating" and net_margin_pct is not None:
        raise ToolError("Pass either opm_pct (operating model) or net_margin_pct (net-margin model), not both.",
                        "invalid_input")
    if model == "net_margin" and opm_pct is not None:
        raise ToolError("Pass either opm_pct (operating model) or net_margin_pct (net-margin model), not both.",
                        "invalid_input")

    page = await fetch_company_page(symbol, financial_type)
    ov = parse_overview(page.html)
    pl = parse_profit_loss(page.html)
    sales_s, profit_s = hist.series(pl, "sales"), hist.series(pl, "net profit")
    if not sales_s and hist.series(pl, "revenue"):
        sales_s = hist.series(pl, "revenue")
    if not sales_s:
        sales_s = hist.series(pl, "revenue")
    sales0, pat0 = _latest(sales_s), _latest(profit_s)
    if not sales0 or sales0 <= 0:
        raise ToolError(f"No usable sales history for {page.symbol}.", "no_data")

    warnings = list(page.warnings)
    if pat0 is not None and pat0 <= 0:
        warnings.append(
            "The latest fiscal year is loss-making; the scenario projects the same margin path forward. "
            "Treat the implied price as illustrative, not as a valuation."
        )
    financial = is_financial(ov.get("sectors", []))
    chosen_model = model
    if chosen_model == "auto":
        if net_margin_pct is not None:
            chosen_model = "net_margin"
        else:
            chosen_model = "net_margin" if financial else "operating"
    if chosen_model == "net_margin" and opm_pct is not None:
        raise ToolError("The net-margin model does not use opm_pct. Pass net_margin_pct instead, or "
                        "choose model='operating'.", "invalid_input")

    # Defaults from the latest fiscal year / trailing history.
    growth_default = hist.cagr(sales_s, 5)
    growth_source = "5-year sales CAGR"
    if growth_default is None:
        growth_default = hist.cagr(sales_s, 3)
        growth_source = "3-year sales CAGR"
    if growth_default is None:
        growth_default = 10.0
        growth_source = "10% fallback (history too short)"
    growth = (sales_growth_pct if sales_growth_pct is not None else growth_default) / 100

    opm0 = _latest(hist.series(pl, "opm"))
    if opm0 is None:
        op_s = _latest(hist.series(pl, "operating profit"))
        opm0 = (op_s / sales0 * 100) if op_s is not None else 15.0
    opm = (opm_pct if opm_pct is not None else opm0) / 100

    net_margin0 = (pat0 / sales0 * 100) if pat0 is not None else 5.0
    net_margin = (net_margin_pct if net_margin_pct is not None else net_margin0) / 100

    tax0 = _latest(hist.series(pl, "tax %"))
    tax = (tax_rate_pct if tax_rate_pct is not None else (tax0 if tax0 is not None else 25.0)) / 100

    pe_now = to_number(overview_field(ov, "pe"))
    pe_exit = exit_pe if exit_pe is not None else (pe_now if pe_now and pe_now > 0 else 15.0)

    price_now = to_number(overview_field(ov, "current_price"))
    eps0 = _latest(hist.series(pl, "eps"))
    per_share_ok = bool(pat0 and pat0 > 0 and eps0 and eps0 > 0)
    shares0 = (pat0 / eps0) if per_share_ok else None
    if shares0 is None:
        warnings.append("EPS is missing or non-positive, so per-share values and implied price are unavailable.")
        shares0 = 1.0  # avoids division; per-share fields will be None

    other_pct = (_latest(hist.series(pl, "other income")) or 0) / sales0
    interest_pct = (_latest(hist.series(pl, "interest")) or 0) / sales0
    dep_pct = (_latest(hist.series(pl, "depreciation")) or 0) / sales0

    rows, pat_t, eps_t = _scenario_price(
        sales0, shares0, horizon, chosen_model, growth, opm, net_margin, tax,
        other_pct, interest_pct, dep_pct, pe_exit, dilution,
    )
    if not per_share_ok:
        eps_t = None
        for r in rows:
            r["eps"] = None
            r["implied_price"] = None

    implied_price = rows[-1]["implied_price"]
    upside = ((implied_price / price_now - 1) * 100) if implied_price is not None and price_now else None
    cagr = (((implied_price / price_now) ** (1 / horizon) - 1) * 100
            if implied_price is not None and price_now and implied_price > 0 else None)

    # Expectations embedded in today's price.
    implied_growth = None
    implied_exit = None
    if price_now and eps_t is not None and eps_t > 0:
        implied_exit = round(price_now / eps_t, 2)
    if price_now and shares0 > 0:
        implied_growth = _solve_growth(
            price_now, sales0=sales0, shares0=shares0, horizon=horizon,
            model=chosen_model, opm=opm, net_margin=net_margin, tax=tax,
            other_pct=other_pct, interest_pct=interest_pct, dep_pct=dep_pct,
            exit_pe=pe_exit, dilution=dilution,
        )

    assumptions = {
        "model": chosen_model,
        "horizon_years": horizon,
        "sales_growth_pct": round(growth * 100, 2),
        "sales_growth_source": "input" if sales_growth_pct is not None else growth_source,
        "opm_pct": round(opm * 100, 2) if chosen_model == "operating" else None,
        "net_margin_pct": round(net_margin * 100, 2) if chosen_model == "net_margin" else None,
        "tax_rate_pct": round(tax * 100, 2),
        "exit_pe": round(pe_exit, 2),
        "exit_pe_source": "input" if exit_pe is not None else (
            "current P/E" if pe_now and pe_now > 0 else "15x fallback"),
        "dilution_pct_per_year": round(dilution * 100, 2),
        "other_income_pct_of_sales": round(other_pct * 100, 2),
        "interest_pct_of_sales": round(interest_pct * 100, 2),
        "depreciation_pct_of_sales": round(dep_pct * 100, 2),
    }
    return ToolResult(
        data={
            "symbol": page.symbol,
            "name": ov.get("name"),
            "financial_type": page.financial_type,
            "current": {
                "price": price_now,
                "pe": pe_now,
                "sales_cr": round(sales0, 2),
                "pat_cr": round(pat0, 2) if pat0 is not None else None,
                "eps": eps0,
                "shares_cr": round(shares0, 2) if shares0 and shares0 != 1.0 else None,
            },
            "assumptions": assumptions,
            "projection": rows,
            "result": {
                "implied_price": implied_price,
                "upside_pct": round(upside, 1) if upside is not None else None,
                "price_cagr_pct": round(cagr, 1) if cagr is not None else None,
                "implied_sales_growth_pct_at_current_price": implied_growth,
                "implied_exit_pe_at_current_price": implied_exit,
            },
            "caveats": [
                "This is a scenario, not a forecast or a target price. It holds margins, tax, other income, "
                "interest and depreciation at their latest shares of sales; real businesses don't.",
                "The reverse calculation answers 'what would have to be true to justify today's price' "
                "under these assumptions — not what will happen.",
                "Working capital, one-offs, acquisitions and buybacks are ignored. Use it to test assumptions, "
                "then check them against the annual report and earnings calls.",
            ],
        },
        warnings=warnings,
        meta=page.meta,
    )


# ─── period returns ───────────────────────────────────────────────────────────

def _parse_iso(value: str, field: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ToolError(f"{field} must be YYYY-MM-DD, got '{value}'.", "invalid_input")


def _slice(hist: PriceHistory, start: date, end: date) -> tuple[Optional[str], Optional[float],
                                                                Optional[str], Optional[float], list[float]]:
    """First close on/after start, last close on/before end, and the closes between."""
    if not hist.dates:
        return None, None, None, None, []
    start_iso, end_iso = start.isoformat(), end.isoformat()
    pairs = [(d, c) for d, c in zip(hist.dates, hist.closes) if start_iso <= d <= end_iso]
    if not pairs:
        return None, None, None, None, []
    return pairs[0][0], pairs[0][1], pairs[-1][0], pairs[-1][1], [c for _, c in pairs]


def _return_stats(start_price: float, end_price: float, closes: list[float],
                  start: date, end: date) -> dict:
    years = max((end - start).days / 365.25, 0)
    ret = (end_price / start_price - 1) * 100 if start_price else None
    cagr = ((end_price / start_price) ** (1 / years) - 1) * 100 if start_price and years >= 1 else None
    peak, max_dd = None, None
    for c in closes:
        peak = c if peak is None else max(peak, c)
        dd = (c / peak - 1) * 100 if peak else None
        max_dd = dd if max_dd is None or (dd is not None and dd < max_dd) else max_dd
    return {
        "return_pct": round(ret, 2) if ret is not None else None,
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "max_drawdown_pct": round(max_dd, 2) if max_dd is not None else None,
        "high": round(max(closes), 2) if closes else None,
        "low": round(min(closes), 2) if closes else None,
        "sessions": len(closes),
        "calendar_years": round(years, 2),
    }


async def get_price_returns(
    symbol: str,
    from_date: str,
    to_date: str = "",
    benchmark: str = "nifty50",
) -> ToolResult:
    """Point-to-point return, CAGR and drawdown for a stock vs an index."""
    start = _parse_iso(from_date, "from_date")
    end = _parse_iso(to_date, "to_date") if to_date.strip() else date.today()
    if start >= end:
        raise ToolError("from_date must be before to_date.", "invalid_input")
    span = (end - start).days
    if span > 3650:
        raise ToolError("The maximum window is 10 years (Screener's chart history).", "invalid_input")

    page = await fetch_company_page(symbol, "consolidated")
    if not page.company_id:
        raise ToolError(f"Could not resolve a Screener company id for {page.symbol}.", "no_data")
    days = min(3660, max(30, span + 15))
    hist = await fetch_price_history(page.company_id, days)
    s_date, s_price, e_date, e_price, closes = _slice(hist, start, end)
    warnings = list(page.warnings)
    if s_date is None or e_date is None:
        raise ToolError(
            f"No price history for {page.symbol} between {start} and {end}. Screener's chart API may not "
            "cover that far back for this company.",
            "no_data",
        )
    if s_date != start.isoformat():
        warnings.append(f"{start} was not a trading day (or predates the data); used the first close on {s_date}.")
    if e_date != end.isoformat():
        warnings.append(f"{end} was not a trading day (or is in the future); used the last close on {e_date}.")

    stock = _return_stats(s_price, e_price, closes, date.fromisoformat(s_date), date.fromisoformat(e_date))
    out = {
        "symbol": page.symbol,
        "name": parse_overview(page.html).get("name"),
        "requested": {"from": start.isoformat(), "to": end.isoformat()},
        "actual": {"from": s_date, "to": e_date},
        "stock": {"start_price": round(s_price, 2), "end_price": round(e_price, 2), **stock},
        "price_basis": "daily closes" if days <= 400 else "weekly closes (Screener down-samples long windows)",
        "method": "Closing prices from Screener.in's chart API; not adjusted for dividends.",
    }

    bench_key = (benchmark or "").strip().lower()
    if bench_key and bench_key not in ("none", "off", "no"):
        idx = resolve_index(benchmark)
        if not idx:
            warnings.append(f"Unknown benchmark '{benchmark}' — benchmark comparison skipped.")
        else:
            _, _, idx_id, display = idx
            idx_hist = await fetch_price_history(idx_id, days)
            b_s_date, b_s_price, b_e_date, b_e_price, b_closes = _slice(idx_hist, start, end)
            if b_s_date is None or b_e_date is None:
                warnings.append(f"No price history for benchmark {display} in this window.")
            else:
                bench = _return_stats(b_s_price, b_e_price, b_closes,
                                      date.fromisoformat(b_s_date), date.fromisoformat(b_e_date))
                out["benchmark"] = {"name": display, "symbol": idx[1], **bench}
                if stock["return_pct"] is not None and bench["return_pct"] is not None:
                    out["excess_return_pp"] = round(stock["return_pct"] - bench["return_pct"], 2)
    return ToolResult(data=out, warnings=warnings, meta=page.meta)
