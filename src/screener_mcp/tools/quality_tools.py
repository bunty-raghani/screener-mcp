"""
Earnings-quality analysis: is reported profit backed by cash?

Everything is computed from the P&L, balance sheet, cash-flow and ratios
already on a Screener.in company page — no new data source. The checks are
deliberately transparent: every flag carries the numbers that triggered it.

Key measures
  CFO/PAT        cash from operations ÷ net profit (100% = profit fully in cash)
  CFO/EBITDA     cash conversion before tax, interest and depreciation
  Accrual ratio  (net profit − CFO) ÷ total assets
  CCC            cash conversion cycle (debtor + inventory − payable days)
  Other income   share of pre-tax profit that is not from operations
"""

import statistics
from typing import Optional

from ..core import history as hist
from ..core.company_page import fetch_company_page
from ..core.envelope import ToolError, ToolResult
from ..core.quality import is_financial
from ..parsers.company import (
    parse_balance_sheet,
    parse_cash_flow,
    parse_overview,
    parse_profit_loss,
    parse_ratios,
)


def _pct(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None or den == 0:
        return None
    return round(num / den * 100, 1)


def _avg(values: list[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(statistics.mean(vals), 1) if vals else None


def _flag(name: str, severity: str, finding: str, **evidence) -> dict:
    return {"flag": name, "severity": severity, "finding": finding, "evidence": evidence}


def _score(flags: list[dict], financial: bool) -> tuple[Optional[int], Optional[str]]:
    if financial:
        return None, None
    score = 100
    weights = {"high": 20, "medium": 10, "low": 5}
    for f in flags:
        score -= weights.get(f["severity"], 5)
    score = max(0, min(100, score))
    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "E"
    return score, grade


async def analyze_earnings_quality(symbol: str, years: int = 10) -> ToolResult:
    """Cash conversion, accruals, working capital and other-income dependence."""
    years = max(3, min(int(years or 10), 12))
    page = await fetch_company_page(symbol, "consolidated")
    ov = parse_overview(page.html)
    pl, bs, cf, ratios = (parse_profit_loss(page.html), parse_balance_sheet(page.html),
                          parse_cash_flow(page.html), parse_ratios(page.html))
    financial = is_financial(ov.get("sectors", []))

    sales = hist.series(pl, "sales") or hist.series(pl, "revenue")
    ebitda = hist.series(pl, "operating profit")
    pat = hist.series(pl, "net profit")
    pbt = hist.series(pl, "profit before tax")
    other_income = hist.series(pl, "other income")
    tax = hist.series(pl, "tax %")
    cfo = hist.series(cf, "cash from operating")
    fcf = hist.series(cf, "free cash flow")
    assets = hist.series(bs, "total assets")
    debtor_days = hist.series(ratios, "debtor days")
    inventory_days = hist.series(ratios, "inventory days")
    payable_days = hist.series(ratios, "days payable")
    ccc = hist.series(ratios, "cash conversion cycle")

    fiscal = [y for y in hist.fiscal_years(pl) if pat.get(y) is not None]
    shown = fiscal[-years:]
    if not shown:
        raise ToolError(f"No usable profit history for {page.symbol}.", "no_data")

    warnings = list(page.warnings)
    missing: list[str] = []
    if not cfo:
        missing.append("cash_flow")
        warnings.append("No cash-flow history on the page — cash-conversion checks are unavailable.")
    if not fcf:
        missing.append("free_cash_flow")
    if not ccc:
        missing.append("cash_conversion_cycle")

    rows = []
    for y in shown:
        p = pat.get(y)
        c = cfo.get(y)
        e = ebitda.get(y)
        a = assets.get(y)
        oi = other_income.get(y)
        pb = pbt.get(y)
        rows.append({
            "year": y,
            "sales_cr": sales.get(y),
            "ebitda_cr": e,
            "pat_cr": p,
            "cfo_cr": c,
            "fcf_cr": fcf.get(y),
            "cfo_pat_pct": _pct(c, p) if p and p > 0 else None,
            "cfo_ebitda_pct": _pct(c, e) if not financial and e and e > 0 else None,
            "accrual_ratio_pct": (round((p - c) / a * 100, 2)
                                 if not financial and p is not None and c is not None and a and a > 0 else None),
            "other_income_pct_of_pbt": _pct(oi, pb) if pb and pb > 0 else None,
            "tax_rate_pct": tax.get(y),
            "debtor_days": debtor_days.get(y),
            "inventory_days": inventory_days.get(y),
            "payable_days": payable_days.get(y),
            "cash_conversion_cycle_days": ccc.get(y),
        })

    recent = rows[-3:]
    cfo_pat_avg = _avg([r["cfo_pat_pct"] for r in recent])
    cfo_ebitda_avg = _avg([r["cfo_ebitda_pct"] for r in recent])
    accrual_avg = _avg([r["accrual_ratio_pct"] for r in recent])
    latest = rows[-1]
    prior = rows[-2] if len(rows) > 1 else None
    ccc_change = (
        round(latest["cash_conversion_cycle_days"] - rows[-4]["cash_conversion_cycle_days"], 1)
        if latest["cash_conversion_cycle_days"] is not None and len(rows) >= 4
        and rows[-4]["cash_conversion_cycle_days"] is not None else None
    )

    flags: list[dict] = []
    if not financial:
        if cfo_pat_avg is not None and cfo_pat_avg < 70:
            flags.append(_flag(
                "weak_cash_conversion", "high" if cfo_pat_avg < 50 else "medium",
                f"Three-year average CFO/PAT is {cfo_pat_avg:.0f}% — reported profit is running ahead of cash.",
                cfo_pat_avg_pct=cfo_pat_avg,
            ))
        if cfo_ebitda_avg is not None and cfo_ebitda_avg < 70:
            flags.append(_flag(
                "weak_ebitda_cash_conversion", "high" if cfo_ebitda_avg < 50 else "medium",
                f"Three-year average CFO/EBITDA is {cfo_ebitda_avg:.0f}% — operations are converting "
                "less than 70% of operating profit into cash.",
                cfo_ebitda_avg_pct=cfo_ebitda_avg,
            ))
        if accrual_avg is not None and accrual_avg > 5:
            flags.append(_flag(
                "high_accruals", "high" if accrual_avg > 10 else "medium",
                f"Three-year average accrual ratio is {accrual_avg:.1f}% of total assets — a meaningful "
                "part of profit is not yet cash.",
                accrual_ratio_pct=accrual_avg,
            ))
        if latest["pat_cr"] and latest["pat_cr"] > 0 and latest["cfo_cr"] is not None and latest["cfo_cr"] < 0:
            flags.append(_flag(
                "profit_without_cash", "high",
                "Latest year reports a profit but negative operating cash flow.",
                pat_cr=latest["pat_cr"], cfo_cr=latest["cfo_cr"],
            ))
        if len(rows) >= 2 and all(r["fcf_cr"] is not None and r["fcf_cr"] < 0 for r in rows[-2:]):
            flags.append(_flag(
                "negative_free_cash_flow", "medium",
                "Free cash flow was negative in each of the last two years.",
                fcf_cr=[r["fcf_cr"] for r in rows[-2:]],
            ))
        if ccc_change is not None and ccc_change > 30 and (latest["cash_conversion_cycle_days"] or 0) > 90:
            flags.append(_flag(
                "working_capital_stretch", "medium",
                f"The cash conversion cycle lengthened by {ccc_change:.0f} days over three years "
                f"(now {latest['cash_conversion_cycle_days']:.0f} days).",
                ccc_change_days=ccc_change,
            ))

    # For a bank/NBFC, "other income" is core fee/treasury income, not a
    # non-operating windfall, so the dependence flag only applies elsewhere.
    if (not financial and latest["other_income_pct_of_pbt"] is not None
            and latest["other_income_pct_of_pbt"] > 25):
        flags.append(_flag(
            "other_income_dependence", "high" if latest["other_income_pct_of_pbt"] > 50 else "medium",
            f"Other income is {latest['other_income_pct_of_pbt']:.0f}% of pre-tax profit — a large part of "
            "earnings is not from the operating business.",
            other_income_pct_of_pbt=latest["other_income_pct_of_pbt"],
        ))
    if (latest["tax_rate_pct"] is not None and prior and prior["tax_rate_pct"] is not None
            and latest["tax_rate_pct"] < 15 and prior["tax_rate_pct"] < 15):
        flags.append(_flag(
            "low_tax_rate", "low",
            f"The tax rate has been below 15% for two years ({prior['tax_rate_pct']:.0f}% → "
            f"{latest['tax_rate_pct']:.0f}%) — check for a genuine exemption rather than a one-off.",
            tax_rate_pct=[prior["tax_rate_pct"], latest["tax_rate_pct"]],
        ))

    score, grade = _score(flags, financial)
    summary = _summary(rows, cfo_pat_avg, cfo_ebitda_avg, ccc_change, flags, financial)
    return ToolResult(
        data={
            "symbol": page.symbol,
            "name": ov.get("name"),
            "sectors": ov.get("sectors", []),
            "financial_sector": financial,
            "years": shown,
            "yearly": rows,
            "three_year_averages": {
                "cfo_pat_pct": cfo_pat_avg,
                "cfo_ebitda_pct": cfo_ebitda_avg,
                "accrual_ratio_pct": accrual_avg,
            },
            "cash_conversion_cycle_change_3y_days": ccc_change,
            "flags": flags,
            "score": score,
            "grade": grade,
            "score_scale": "A ≥85, B ≥70, C ≥55, D ≥40, E <40; financials are not scored",
            "summary": summary,
            "method": (
                "CFO/PAT = cash from operating activity ÷ net profit. CFO/EBITDA = CFO ÷ Screener's "
                "operating profit. Accrual ratio = (net profit − CFO) ÷ total assets. CCC = debtor + "
                "inventory − payable days."
            ),
            "notes": [
                "A single year can be distorted by timing; the flags use three-year averages where possible.",
                "Banks/NBFCs are not scored: loan growth and deposit flows make CFO and working-capital days "
                "non-comparable. Their earnings quality needs GNPA, provisioning and NIM data.",
            ],
        },
        warnings=warnings,
        missing_fields=missing,
        meta=page.meta,
    )


def _summary(rows: list[dict], cfo_pat, cfo_ebitda, ccc_change, flags, financial: bool) -> str:
    latest = rows[-1]
    if financial:
        bits = ["Financial company — cash-conversion and working-capital checks are not scored."]
        if latest.get("other_income_pct_of_pbt") is not None:
            bits.append(f"Other income is {latest['other_income_pct_of_pbt']:.0f}% of pre-tax profit.")
        return " ".join(bits)
    bits = []
    if cfo_pat is not None:
        bits.append(f"3-year average CFO/PAT {cfo_pat:.0f}%")
    if cfo_ebitda is not None:
        bits.append(f"CFO/EBITDA {cfo_ebitda:.0f}%")
    if latest.get("cash_conversion_cycle_days") is not None:
        bits.append(f"cash conversion cycle {latest['cash_conversion_cycle_days']:.0f} days")
        if ccc_change is not None:
            bits.append(f"({ccc_change:+.0f} days over three years)")
    if flags:
        bits.append(f"{len(flags)} quality flag(s): " + ", ".join(f["flag"] for f in flags))
    else:
        bits.append("no material cash-quality flags")
    return ". ".join(bits) + "."
