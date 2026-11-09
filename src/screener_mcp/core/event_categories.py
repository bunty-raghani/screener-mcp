"""
Shared classification for NSE corporate announcements.

The NSE feed gives a generic `desc` and a free-text headline. Both the
per-company announcements tool and the market-wide search use the same rules
so a filing lands in the same category everywhere.

Order matters: specific, high-signal categories are checked before broad ones
("resignation of auditor" must be auditor, not resignation).
"""

from typing import Iterable

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "credit_rating": ("credit rating", "crisil", "icra", "care ratings", "india ratings", "brickwork"),
    "esg_rating": ("esg rating", "sustainability rating", "esg score"),
    "auditor": ("statutory auditor", "auditor", "audit qualification", "audit report"),
    "pledge": ("pledge", "pledged"),
    "delisting": ("delisting", "delist"),
    "resignation": ("resignation", "resigns", "cessation", "stepping down"),
    "order_win": (
        "order win", "order received", "order worth", "bags order", "bagged order", "wins order",
        "new order", "letter of award", "award of order", "receipt of order", "receiving of order",
        "receipt of orders", "receiving of orders", "bagging", "contract awarded",
        "awarded contract", "purchase order",
    ),
    "capex": (
        "capacity expansion", "capacity addition", "new plant", "capex", "commissioning",
        "greenfield", "brownfield", "expansion project", "setting up",
    ),
    "acquisition": ("acquisition", "acquire", "merger", "amalgamation", "demerger", "scheme of arrangement"),
    "buyback": ("buyback", "buy-back", "share repurchase"),
    "fund_raise": (
        "fund raising", "fundraise", "qip", "qualified institutional placement", "rights issue",
        "preferential allotment", "preferential issue", "debenture", "non-convertible", "ncd",
        "commercial paper", "term loan",
    ),
    "dividend": ("dividend",),
    "bonus_split": ("bonus", "stock split", "sub-division", "subdivision", "face value split"),
    "results": ("financial results", "unaudited results", "audited results", "quarterly results",
                "results for the quarter", "results for the year"),
    "board_meeting": ("board meeting", "board of directors meeting"),
    "agm": ("annual general meeting", "extraordinary general meeting", "agm", "egm"),
    "insider_trading": ("insider trading", "sebi (prohibition of insider trading)", "pit disclosure"),
    "general": (),
}

# Newer, more specific categories first.
_CATEGORY_ORDER = [
    "credit_rating", "esg_rating", "auditor", "pledge", "delisting", "resignation",
    "order_win", "capex", "acquisition", "buyback", "fund_raise", "dividend",
    "bonus_split", "results", "board_meeting", "agm", "insider_trading", "general",
]

MATERIALITY = {
    "credit_rating": "high",
    "auditor": "high",
    "pledge": "high",
    "delisting": "high",
    "resignation": "medium",
    "order_win": "high",
    "capex": "high",
    "acquisition": "high",
    "buyback": "high",
    "fund_raise": "medium",
    "dividend": "medium",
    "bonus_split": "medium",
    "results": "medium",
    "board_meeting": "low",
    "agm": "low",
    "insider_trading": "medium",
    "esg_rating": "low",
    "general": "low",
}


def categorize(headline: str, nse_category: str = "", extra: str = "") -> str:
    """Best category for a filing, using headline + NSE category + extra text."""
    text = f"{headline or ''} {nse_category or ''} {extra or ''}".lower()
    for category in _CATEGORY_ORDER:
        if category == "general":
            break
        if any(keyword in text for keyword in CATEGORY_KEYWORDS[category]):
            return category
    return "general"


def materiality(category: str) -> str:
    return MATERIALITY.get(category, "low")


def categories() -> Iterable[str]:
    return (c for c in _CATEGORY_ORDER if c != "general")
