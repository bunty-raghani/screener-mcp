"""
Valuation multiples (P/E, P/B, EV/EBITDA, Market Cap/Sales) from Screener.in's
chart API.

Screener's "Ratios" table on a company page only carries working-capital days
and ROCE — no valuation multiples. The chart API behind each valuation chart
has a series going back ~10 years (weekly points at that range, daily for short
windows):

  GET /api/company/{company_id}/chart/?q=Price to Earning-Median PE-EPS&days=3650
  GET /api/company/{company_id}/chart/?q=Price to book value-Median PBV-Book value&days=3650
  GET /api/company/{company_id}/chart/?q=EV Multiple-Median EV Multiple-EBITDA&days=3650
  GET /api/company/{company_id}/chart/?q=Market Cap to Sales-Median Market Cap to Sales-Sales&days=3650

so a fiscal year's multiple is the last point on or before its year-end date,
and the whole daily series can be used for a valuation-percentile band.
"""

import asyncio
import calendar
import time
from datetime import date, datetime, timedelta
from typing import Optional

from ..client import get_client

_QUERIES = {
    "pe": ("Price to Earning-Median PE-EPS", "Price to Earning"),
    "pb": ("Price to book value-Median PBV-Book value", "Price to book value"),
    "ev_ebitda": ("EV Multiple-Median EV Multiple-EBITDA", "EV Multiple"),
    "mcap_sales": ("Market Cap to Sales-Median Market Cap to Sales-Sales", "Market Cap to Sales"),
}
_DAYS = 3650
_CACHE_TTL = 6 * 3600
_cache: dict[tuple[str, bool], tuple[float, dict]] = {}

# Multiples are only meaningful when positive. Very large values usually mean
# a near-zero denominator (a tiny profit or EBITDA), so they are left out of
# the distribution rather than allowed to dominate the percentile.
MAX_MULTIPLE = 500.0


def fiscal_year_end(label: str) -> Optional[date]:
    """'Mar 2024' → 2024-03-31. None for 'TTM' or anything unparseable."""
    try:
        d = datetime.strptime(label.strip(), "%b %Y")
    except (ValueError, AttributeError):
        return None
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _series(payload: dict, metric: str) -> list[tuple[str, float]]:
    for ds in (payload or {}).get("datasets", []):
        if ds.get("metric") == metric:
            out = []
            for point in ds.get("values", []):
                try:
                    out.append((point[0], float(point[1])))
                except (TypeError, ValueError, IndexError):
                    continue
            return sorted(out)
    return []


async def fetch_multiples(company_id: str, consolidated: bool) -> dict[str, list[tuple[str, float]]]:
    """{"pe": [(iso date, value), ...], "pb": [...]} — oldest first."""
    key = (company_id, consolidated)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    client = await get_client()

    async def one(q: str) -> dict:
        params = {"q": q, "days": str(_DAYS)}
        if consolidated:
            params["consolidated"] = "true"
        payload = await client.get_json(f"/api/company/{company_id}/chart/", params=params)
        return payload if isinstance(payload, dict) else {}

    payloads = await asyncio.gather(*[one(q) for q, _ in _QUERIES.values()])
    result = {k: _series(p, metric) for (k, (_, metric)), p in zip(_QUERIES.items(), payloads)}
    _cache[key] = (time.monotonic(), result)
    return result


def _quantile(sorted_values: list[float], q: float) -> Optional[float]:
    """Linear-interpolation quantile on a sorted list (q in 0..1)."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _verdict(percentile: Optional[float]) -> Optional[str]:
    if percentile is None:
        return None
    if percentile <= 20:
        return "cheap vs its own history"
    if percentile <= 40:
        return "below its usual range"
    if percentile < 60:
        return "in line with its own history"
    if percentile < 80:
        return "above its usual range"
    return "expensive vs its own history"


def band_stats(series: list[tuple[str, float]], years: int = 10) -> Optional[dict]:
    """Percentile band for one multiple series over the last ``years``.

    Current is the latest observation in the window; the percentile is the
    share of observations at or below it. Values ≤ 0 (loss-making or negative
    EBITDA) and > MAX_MULTIPLE are excluded, since they distort a multiple
    distribution.
    """
    if not series:
        return None
    try:
        last_date = datetime.strptime(series[-1][0], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    cutoff = (last_date - timedelta(days=round(years * 365.25))).strftime("%Y-%m-%d")
    window = [(d, float(v)) for d, v in series
              if d >= cutoff and 0 < float(v) <= MAX_MULTIPLE]
    if len(window) < 2:
        return None
    values = sorted(v for _, v in window)
    current = window[-1][1]
    below = sum(1 for v in values if v <= current)
    percentile = round(below / len(values) * 100, 1)
    median = _quantile(values, 0.5)
    return {
        "current": round(current, 2),
        "current_date": window[-1][0],
        "percentile": percentile,
        "median": round(median, 2) if median is not None else None,
        "p25": round(_quantile(values, 0.25), 2),
        "p75": round(_quantile(values, 0.75), 2),
        "min": round(values[0], 2),
        "max": round(values[-1], 2),
        "premium_discount_vs_median_pct": (
            round((current / median - 1) * 100, 1) if median else None
        ),
        "verdict": _verdict(percentile),
        "observations": len(values),
        "window_start": window[0][0],
        "window_end": window[-1][0],
        "window_years": years,
        "excluded_non_positive_or_extreme": len(series) - len(window),
    }


def value_at_year_end(series: list[tuple[str, float]], label: str) -> Optional[float]:
    """Last value on or before the fiscal year-end; None if the series starts
    after it (so years older than the chart window stay blank, not guessed)."""
    end = fiscal_year_end(label)
    if not end or not series:
        return None
    cutoff = end.isoformat()
    if series[0][0] > cutoff:
        return None
    best = None
    for d, v in series:
        if d > cutoff:
            break
        best = v
    return best
