"""Offline tests for the analytics/event tools added in 0.6.0.

These avoid the network: they exercise the pure calculations, parsers and
local-state diffing that the live tools sit on. Live smoke checks are run
separately against Screener.in/NSE.
"""

from datetime import date

import pytest

from screener_mcp.core.envelope import ToolError, ToolResult
from screener_mcp.core.event_categories import categorize, materiality
from screener_mcp.core.technicals import PriceHistory
from screener_mcp.core.valuation_history import band_stats
from screener_mcp.tools import cost_tools as ct
from screener_mcp.tools import documents as docs
from screener_mcp.tools import event_tools as et
from screener_mcp.tools import governance_tools as gt
from screener_mcp.tools import industry_tools as it
from screener_mcp.tools import screen_tracker as st
from screener_mcp.tools import valuation_tools as vt


def _series(values, start_year=2024):
    return [(f"{start_year}-01-{i + 1:02d}", v) for i, v in enumerate(values)]


# ─── valuation bands ──────────────────────────────────────────────────────────

def test_band_stats_percentile_median_and_verdict():
    band = band_stats(_series([10, 11, 12, 13, 14, 15, 16, 17, 18, 19]), years=1)
    assert band["current"] == 19
    assert band["median"] == 14.5
    assert band["percentile"] == 100
    assert band["verdict"] == "expensive vs its own history"
    assert band["observations"] == 10


def test_band_stats_low_percentile():
    band = band_stats(_series([19, 18, 17, 16, 15, 14, 13, 12, 11, 10]), years=1)
    assert band["percentile"] == 10
    assert band["verdict"] == "cheap vs its own history"


def test_band_stats_excludes_non_positive_and_extreme():
    band = band_stats(_series([-5, 0, 10, 20, 600]), years=1)
    assert band["observations"] == 2
    assert band["excluded_non_positive_or_extreme"] == 3
    assert band["min"] == 10 and band["max"] == 20


def test_band_stats_needs_two_points():
    assert band_stats(_series([10]), years=1) is None


# ─── scenario math ────────────────────────────────────────────────────────────

def _scenario_kwargs():
    return dict(sales0=100.0, shares0=10.0, horizon=5, model="net_margin",
                opm=0.0, net_margin=0.10, tax=0.0, other_pct=0.0,
                interest_pct=0.0, dep_pct=0.0, exit_pe=20.0, dilution=0.0)


def test_scenario_price_matches_hand_calculation():
    price = vt._price_for_growth(0.10, **_scenario_kwargs())
    # 100 × 1.1^5 × 10% = 16.1051 Cr PAT; ÷ 10 Cr shares = 1.61051 EPS; × 20 = 32.2102
    assert price == pytest.approx(32.2102, rel=1e-6)


def test_reverse_growth_solves_back_to_same_assumption():
    target = vt._price_for_growth(0.10, **_scenario_kwargs())
    solved = vt._solve_growth(target, **_scenario_kwargs())
    assert solved == pytest.approx(10.0, abs=0.02)


def test_scenario_projection_last_year_compounds():
    rows, pat, eps = vt._scenario_price(
        100.0, 10.0, 5, "net_margin", 0.10, 0.0, 0.10, 0.0, 0.0, 0.0, 0.0, 20.0, 0.0,
    )
    assert rows[-1]["sales_cr"] == pytest.approx(161.05, abs=0.01)
    assert pat == pytest.approx(16.11, abs=0.01)
    assert eps == pytest.approx(1.61, abs=0.01)
    assert rows[-1]["implied_price"] == pytest.approx(32.21, abs=0.01)


# ─── price returns ────────────────────────────────────────────────────────────

def test_return_stats_return_cagr_and_drawdown():
    stats = vt._return_stats(100, 120, [100, 130, 90, 120], date(2024, 1, 1), date(2025, 1, 1))
    assert stats["return_pct"] == 20.0
    assert stats["cagr_pct"] == pytest.approx(20.0, abs=0.1)
    assert stats["max_drawdown_pct"] == pytest.approx(-30.77, abs=0.01)
    assert stats["high"] == 130 and stats["low"] == 90
    assert stats["sessions"] == 4


def test_slice_uses_first_close_after_start_and_last_before_end():
    hist = PriceHistory(dates=["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
                        closes=[100, 101, 102, 103])
    start_date, start_price, end_date, end_price, closes = vt._slice(
        hist, date(2024, 1, 2), date(2024, 1, 4))
    assert (start_date, start_price) == ("2024-01-02", 101)
    assert (end_date, end_price) == ("2024-01-04", 103)
    assert closes == [101, 102, 103]


def test_invalid_date_is_a_clear_input_error():
    with pytest.raises(ToolError) as exc:
        vt._parse_iso("09-11-2026", "from_date")
    assert exc.value.error_type == "invalid_input"


# ─── cost structure parsing ───────────────────────────────────────────────────

def test_cost_schedule_periods_rows_and_ttm():
    payload = {
        "Material Cost %": {
            "Mar 2024": "40%", "Mar 2025": "42%", "TTM": "41%",
            "tooltips": {"Mar 2024": "₹400 Cr", "Mar 2025": "₹500 Cr"},
        },
        "Employee Cost %": {"Mar 2024": "10%", "Mar 2025": "11%"},
    }
    assert ct._periods(payload) == ["Mar 2024", "Mar 2025"]
    row = ct._row("Material Cost %", payload["Material Cost %"], ["Mar 2024", "Mar 2025"])
    assert row["label"] == "Material Cost"
    assert row["values_pct_of_sales"] == [40, 42]
    assert row["values_cr"] == [400, 500]
    assert ct._change_pp(row, ["Mar 2024", "Mar 2025"], "yearly") == 2.0
    assert ct._ttm(payload) == {"Material Cost %": 41.0}


def test_quarterly_change_uses_same_quarter_last_year():
    row = {"values_pct_of_sales": [30, 31, 32, 33, 35]}
    assert ct._change_pp(row, ["a", "b", "c", "d", "e"], "quarterly") == 5.0


# ─── event classification ─────────────────────────────────────────────────────

def test_corporate_action_types():
    assert et._action_types("Interim Dividend - Rs 12 Per Share") == ["dividend"]
    assert et._action_types("Rights 2:25 @ Premium Rs 17/-") == ["rights"]
    assert et._action_types("Stock Split") == ["split"]
    assert et._action_types("Bonus 1:1") == ["bonus"]
    assert et._action_types("Buyback of equity shares") == ["buyback"]
    assert et._action_types("Something else") == ["other"]


def test_nse_date_parsing():
    assert et._parse_date("13-Oct-2026") == date(2026, 10, 13)
    assert et._parse_date("09-Nov-2026 18:28:35") == date(2026, 11, 9)
    assert et._parse_date("-") is None


def test_announcement_categories_are_specific_before_broad():
    assert categorize("Resignation of Statutory Auditor") == "auditor"
    assert categorize("Credit Rating Action by ICRA") == "credit_rating"
    assert categorize("Bagging/Receiving of orders/contracts") == "order_win"
    assert categorize("Board Meeting Intimation") == "board_meeting"
    assert materiality("order_win") == "high"
    assert materiality("general") == "low"


# ─── earnings-call chips ──────────────────────────────────────────────────────

def test_earnings_call_chips_keep_ppt_recording_and_ai_summary():
    html = """
    <section id="documents"><div class="concalls"><ul class="list-links">
      <li>Oct 2026
        <a href="https://x/transcript.pdf">Transcript</a>
        <button>AI Summary</button>
        <a href="https://x/ppt.pdf">PPT</a>
        <a href="https://x/rec.mp3">REC</a>
      </li>
    </ul></div></section>
    """
    calls = docs._parse_earnings_calls(html)
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://x/transcript.pdf"
    assert call["ppt_url"] == "https://x/ppt.pdf"
    assert call["recording_url"] == "https://x/rec.mp3"
    assert call["ai_summary_available"] is True


# ─── related-party table ──────────────────────────────────────────────────────

def test_related_party_table_groups_transactions_under_party():
    html = """
    <table class="data-table">
      <thead><tr><th></th><th>Mar 2024</th><th>Mar 2025</th></tr></thead>
      <tbody>
        <tr class="strong stripe">
          <td>Tata Sons Private Limited <small>Associate</small></td>
          <td colspan="2"></td>
        </tr>
        <tr><td>Dividend paid</td><td>10</td><td>12</td></tr>
        <tr><td>Revenue from operations</td><td>5</td><td>6</td></tr>
        <tr class="strong stripe">
          <td>Key Managerial Personnel <small>KMP</small></td>
          <td colspan="2"></td>
        </tr>
        <tr><td>Remuneration</td><td>2</td><td>3</td></tr>
      </tbody>
    </table>
    """
    table = gt.parse_related_party_table(html)
    assert table["years"] == ["Mar 2024", "Mar 2025"]
    assert [p["party"] for p in table["parties"]] == ["Tata Sons Private Limited", "Key Managerial Personnel"]
    assert table["parties"][0]["relation"] == "Associate"
    assert table["parties"][0]["transactions"][0] == {"label": "Dividend paid", "values": [10.0, 12.0]}
    assert table["parties"][1]["transactions"][0]["values"] == [2.0, 3.0]


# ─── cross-company commentary ─────────────────────────────────────────────────

async def test_compare_management_commentary_returns_side_by_side(monkeypatch):
    class FakePage:
        symbol = "TCS"
        html = ""

    class FakeStore:
        def list_collections(self, prefix=""):
            return [f"{prefix}2025_annual"]

    async def fake_fetch(symbol, financial_type="consolidated"):
        page = FakePage()
        page.symbol = str(symbol).upper()
        return page

    async def fake_query(collections, question, top_k=3, top_k_per_collection=3):
        return [{
            "text": "The main risk is client spending slowdown.",
            "metadata": {"doc_type": "annual_report", "label": "2025", "pages": "10-12"},
            "score": 0.9,
            "rank_score": 0.9,
            "collection": collections[0],
        }]

    monkeypatch.setattr(docs, "fetch_company_page", fake_fetch)
    monkeypatch.setattr(docs, "get_vector_store", lambda: FakeStore())
    monkeypatch.setattr(docs, "query_documents", fake_query)

    result = await docs.compare_management_commentary(["TCS", "INFY"], "What are the key risks?")
    assert [c["symbol"] for c in result.data["companies"]] == ["TCS", "INFY"]
    excerpt = result.data["companies"][0]["excerpts"][0]
    assert excerpt["document"] == "Annual Report 2025"
    assert excerpt["pages"] == "10-12"
    assert "client spending slowdown" in excerpt["text"]
    assert "## TCS" in result.data["report"] and "## INFY" in result.data["report"]


# ─── industry stats ───────────────────────────────────────────────────────────

def test_industry_overview_medians_exclude_microcaps():
    rows = [
        {"market_cap": 1000, "pe": 10, "pb": 2, "roce": 15, "sales_growth_yoy": 10,
         "profit_growth_yoy": 20, "dividend_yield": 1, "sales_qtr": 100, "net_profit_qtr": 10},
        {"market_cap": 2000, "pe": 20, "pb": 4, "roce": 25, "sales_growth_yoy": 20,
         "profit_growth_yoy": 30, "dividend_yield": 2, "sales_qtr": 200, "net_profit_qtr": 10},
        # Below ₹500 Cr: stays in the total but not in the medians.
        {"market_cap": 100, "pe": 5, "pb": 1, "roce": 5, "sales_growth_yoy": 50,
         "profit_growth_yoy": 100, "dividend_yield": 0, "sales_qtr": 10, "net_profit_qtr": 1},
    ]
    stats = it._overview_stats(rows)
    assert stats["companies_total"] == 3
    assert stats["companies_in_medians"] == 2
    assert stats["median_pe"] == 15
    assert stats["median_roce"] == 20
    assert stats["median_sales_growth_yoy"] == 15
    assert stats["median_market_cap_cr"] == 1500


# ─── screen diffing ───────────────────────────────────────────────────────────

async def test_track_screen_changes_baseline_then_diff(monkeypatch, tmp_path):
    monkeypatch.setattr(st, "_SNAPSHOT_PATH", tmp_path / "snapshots.json")

    current = {"symbols": ["AAA", "BBB"]}

    async def fake_screen(theme, limit=100):
        return ToolResult(data={"theme": theme, "results": [{"symbol": s} for s in current["symbols"]],
                                "total_matches": len(current["symbols"])})

    monkeypatch.setattr(st, "screen_by_theme", fake_screen)
    first = await st.track_screen_changes("test", theme="quality", limit=10)
    assert first.data["baseline_created"] is True
    assert first.data["symbols"] == ["AAA", "BBB"]

    current["symbols"] = ["AAA", "CCC"]
    second = await st.track_screen_changes("test", theme="quality", limit=10)
    assert second.data["changes"]["added"] == ["CCC"]
    assert second.data["changes"]["removed"] == ["BBB"]
    assert second.data["changes"]["unchanged_count"] == 1
