"""
Related-party transactions from Screener.in.

Screener's company page loads the RPT table from

  GET /results/rpt/{company_id}/{consolidated|standalone}/

with the same "party → transaction → yearly values" shape as the financial
tables. This is a governance check: large or growing transactions with
promoter-group entities deserve a closer read of the annual report.
"""

from typing import Literal

from bs4 import BeautifulSoup

from ..client import get_client
from ..core.company_page import fetch_company_page
from ..core.envelope import ToolError, ToolResult
from ..core.numbers import to_number
from ..parsers.company import parse_overview

FinancialType = Literal["consolidated", "standalone"]


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def parse_related_party_table(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="data-table") or soup.find("table")
    if not table:
        return {"years": [], "parties": []}
    thead = table.find("thead")
    years = [_clean(th.get_text(" ", strip=True)) for th in thead.find_all("th")][1:] if thead else []

    parties: list[dict] = []
    current = None
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        label = _clean(cells[0].get_text(" ", strip=True))
        strong = "strong" in (tr.get("class") or [])
        if strong and len(cells) >= 2 and (cells[1].get("colspan") or "").strip():
            # Party header row: name + <small>relation</small>.
            small = cells[0].find("small")
            relation = _clean(small.get_text(" ", strip=True)) if small else ""
            party = _clean(cells[0].get_text(" ", strip=True))
            if relation and party.endswith(relation):
                party = party[: -len(relation)].strip()
            current = {"party": party, "relation": relation or None, "transactions": []}
            parties.append(current)
            continue
        if current is None:
            continue
        values = [to_number(c.get_text(" ", strip=True)) for c in cells[1:]]
        if any(v is not None for v in values):
            current["transactions"].append({"label": label, "values": values})

    return {"years": years, "parties": parties}


async def get_related_party_transactions(
    symbol: str,
    years: int = 5,
    financial_type: FinancialType = "consolidated",
) -> ToolResult:
    """Related-party transactions by party and year, from Screener.in."""
    years = max(1, min(int(years or 5), 12))
    page = await fetch_company_page(symbol, financial_type)
    if not page.company_id:
        raise ToolError(f"Could not resolve a Screener company id for {page.symbol}.", "no_data")
    client = await get_client()
    html = await client.get_html(f"/results/rpt/{page.company_id}/{page.financial_type}/")
    table = parse_related_party_table(html)
    all_years = table["years"]
    shown = all_years[-years:]
    parties = []
    for p in table["parties"]:
        txs = [
            {"label": t["label"], "values": t["values"][-len(shown):] if shown else t["values"]}
            for t in p["transactions"]
        ]
        parties.append({"party": p["party"], "relation": p["relation"], "transactions": txs})

    if not parties:
        raise ToolError(
            f"Screener.in returned no related-party table for {page.symbol}. It may not report RPTs in "
            "this format, or the page is temporarily unavailable.",
            "no_data",
        )
    warnings = list(page.warnings)
    latest_idx = None
    for i in range(len(shown) - 1, -1, -1):
        if any(len(t["values"]) > i and t["values"][i] is not None
               for p in parties for t in p["transactions"]):
            latest_idx = i
            break
    latest = shown[latest_idx] if latest_idx is not None else None
    latest_by_party = []
    if latest_idx is not None:
        for p in parties:
            vals = [t["values"][latest_idx] for t in p["transactions"] if len(t["values"]) > latest_idx]
            vals = [v for v in vals if v is not None]
            if vals:
                latest_by_party.append({"party": p["party"], "relation": p["relation"],
                                        "latest_year": latest, "reported_items": len(vals)})
    return ToolResult(
        data={
            "symbol": page.symbol,
            "name": parse_overview(page.html).get("name"),
            "financial_type": page.financial_type,
            "years": shown,
            "units": "₹ Cr",
            "party_count": len(parties),
            "parties": parties,
            "latest_year_activity": latest_by_party,
            "source": "Screener.in related-party transactions",
            "notes": [
                "The table mixes income, expense, receivable, payable and financing lines, so the rows are "
                "not additive. Read the transaction labels, not a total.",
                "Large or rapidly growing transactions with promoter-group entities are a governance flag; "
                "confirm the arm's-length pricing and disclosures in the annual report.",
            ],
        },
        warnings=warnings,
        meta=page.meta,
    )
