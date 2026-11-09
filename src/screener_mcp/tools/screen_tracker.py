"""
Snapshot and diff stock-screen results over time.

Screener.in can email a screen alert, but an MCP server is pull-based. This
tool stores the last run locally and tells you what entered/left the screen
since — the same monitoring workflow without a server pushing anything.

State: ~/.screener-mcp/screen_snapshots.json
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from ..core.envelope import ToolError, ToolResult
from .screening_tools import screen_by_theme, screen_stocks

_SNAPSHOT_PATH = Path.home() / ".screener-mcp" / "screen_snapshots.json"


def _load() -> dict:
    if not _SNAPSHOT_PATH.exists():
        return {}
    try:
        data = json.loads(_SNAPSHOT_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict):
    _SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SNAPSHOT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def track_screen_changes(
    label: str = "",
    query: str = "",
    theme: str = "",
    limit: int = 100,
) -> ToolResult:
    """Run a screen, diff it against the last snapshot, and save the new one.

    With no ``label``, ``query`` or ``theme``, lists the saved trackers.
    ``query`` and ``theme`` are mutually exclusive.
    """
    snapshots = _load()
    if not label and not query and not theme:
        trackers = [
            {
                "label": key,
                "kind": value.get("kind"),
                "query": value.get("query"),
                "theme": value.get("theme"),
                "captured_at": value.get("captured_at"),
                "count": len(value.get("symbols", [])),
                "total_matches": value.get("total_matches"),
            }
            for key, value in sorted(snapshots.items())
        ]
        return ToolResult(data={"trackers": trackers, "count": len(trackers)})

    label = (label or "").strip()
    if not label:
        raise ToolError("Pass a `label` to track a screen, or no arguments to list trackers.", "invalid_input")
    if bool(query.strip()) == bool(theme.strip()):
        raise ToolError("Pass exactly one of `query` or `theme` when tracking a screen.", "invalid_input")
    limit = max(10, min(int(limit or 100), 200))

    if theme.strip():
        current = await screen_by_theme(theme.strip(), limit=limit)
        kind = "theme"
    else:
        current = await screen_stocks(query.strip(), limit=limit)
        kind = "query"

    rows = current.data.get("results", [])
    symbols = sorted({str(r.get("symbol")).upper() for r in rows if r.get("symbol")})
    total_matches = current.data.get("total_matches") or current.data.get("matches_found") or len(symbols)
    previous = snapshots.get(label)
    captured_at = _now()
    entry = {
        "kind": kind,
        "query": query.strip() if kind == "query" else None,
        "theme": theme.strip() if kind == "theme" else None,
        "captured_at": captured_at,
        "symbols": symbols,
        "total_matches": total_matches,
        "limit": limit,
    }
    snapshots[label] = entry
    _save(snapshots)

    warnings = list(current.warnings)
    truncated = bool(total_matches and total_matches > len(symbols))
    if truncated:
        warnings.append(
            f"Tracked the first {len(symbols)} of {total_matches} matches (limit={limit}). "
            "Raise `limit` to track more; the diff only covers the captured set."
        )

    if not previous:
        return ToolResult(
            data={
                "label": label,
                "baseline_created": True,
                "screen": {k: entry[k] for k in ("kind", "query", "theme", "captured_at", "total_matches")},
                "symbols": symbols,
                "changes": None,
                "note": "Baseline saved. Run the same label again after the next rebalance/results season to see entries and exits.",
            },
            warnings=warnings,
            partial=truncated,
            reason="Only the first page of matches was captured." if truncated else None,
        )

    old = set(previous.get("symbols", []))
    new = set(symbols)
    added, removed = sorted(new - old), sorted(old - new)
    return ToolResult(
        data={
            "label": label,
            "baseline_created": False,
            "screen": {k: entry[k] for k in ("kind", "query", "theme", "captured_at", "total_matches")},
            "previous": {
                "captured_at": previous.get("captured_at"),
                "count": len(old),
                "total_matches": previous.get("total_matches"),
            },
            "changes": {
                "added": added,
                "removed": removed,
                "added_count": len(added),
                "removed_count": len(removed),
                "unchanged_count": len(new & old),
            },
            "symbols": symbols,
            "note": "Added = entered the captured result set since the previous run; removed = left it. A name can leave because its numbers changed, not only because the business changed.",
        },
        warnings=warnings,
        partial=truncated,
        reason="Only the first page of matches was captured." if truncated else None,
    )
