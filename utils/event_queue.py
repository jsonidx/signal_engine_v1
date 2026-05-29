"""
utils/event_queue.py
=====================
Bounded event-driven Deep Dive candidate queue.

Stores tickers that triggered early catalyst momentum gates
(EARLY_MOMENTUM_BREAKOUT, CATALYST_PRICE_EXPANSION) so they can be routed
into AI thesis generation even when absent from watchlist.txt /
resolved_signals.json.

Schema per entry:
  ticker       : str        — uppercase ticker symbol
  reason       : str        — tag(s) that triggered queuing, e.g.
                              "EARLY_MOMENTUM_BREAKOUT,CATALYST_PRICE_EXPANSION"
  score        : float      — pre-screen score at queue time (0–1)
  source_fields: dict       — arbitrary key/value metadata (force_tags, price, etc.)
  queued_at    : str        — ISO 8601 UTC timestamp

Constraints:
  - Daily cap: default 10 fresh candidates per calendar day (configurable)
  - De-duplication: same ticker on same UTC date → second enqueue is a no-op
  - Persistence: JSON file at data/event_queue.json; auto-created if absent
  - Stale cleanup: entries older than keep_days are pruned on read (default 3)

Public API:
  enqueue(ticker, reason, score, source_fields, ...)  -> bool
  get_queue_for_date(run_date=None, ...)               -> list[dict]
  get_all_pending(max_age_days=1, ...)                 -> list[dict]
  clear_stale_entries(keep_days=3, ...)                -> int
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from config import EVENT_QUEUE_DAILY_CAP
except ImportError:
    EVENT_QUEUE_DAILY_CAP = 10

_DEFAULT_DAILY_CAP = EVENT_QUEUE_DAILY_CAP
_QUEUE_PATH = Path(__file__).parent.parent / "data" / "event_queue.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load(queue_path: Path) -> list:
    if not queue_path.exists():
        return []
    try:
        with open(queue_path) as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("event_queue: failed to load %s: %s", queue_path, exc)
        return []


def _save(entries: list, queue_path: Path) -> None:
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(queue_path, "w") as fh:
            json.dump(entries, fh, indent=2)
    except Exception as exc:
        logger.warning("event_queue: failed to save %s: %s", queue_path, exc)


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enqueue(
    ticker: str,
    reason: str,
    score: float = 0.0,
    source_fields: Optional[dict] = None,
    queue_path: Optional[Path] = None,
    daily_cap: Optional[int] = None,
) -> bool:
    """
    Add *ticker* to the event queue for today.

    Returns True if the entry was added, False if the daily cap was already
    reached or the ticker was already queued today (de-duplication).
    """
    ticker = ticker.upper().strip()
    if not ticker:
        return False

    path = queue_path or _QUEUE_PATH
    cap  = daily_cap if daily_cap is not None else _DEFAULT_DAILY_CAP
    today = _today_utc()

    entries = _load(path)
    today_entries = [e for e in entries if e.get("queued_at", "")[:10] == today]

    # De-duplication: same ticker same day → no-op
    if any(e["ticker"] == ticker for e in today_entries):
        logger.debug("event_queue: %s already queued today — skipping", ticker)
        return False

    # Daily cap check
    if len(today_entries) >= cap:
        logger.info(
            "event_queue: daily cap (%d) reached — not queuing %s (%s)",
            cap, ticker, reason,
        )
        return False

    entry = {
        "ticker":       ticker,
        "reason":       reason,
        "score":        round(float(score), 6),
        "source_fields": source_fields or {},
        "queued_at":    datetime.now(timezone.utc).isoformat(),
    }
    entries.append(entry)
    _save(entries, path)
    logger.info("event_queue: queued %s (%s, score=%.4f)", ticker, reason, score)
    return True


def _today_local() -> str:
    """Return today's date as ISO string in local time (used as fallback only)."""
    return date.today().isoformat()


def get_queue_for_date(
    run_date: Optional[date] = None,
    queue_path: Optional[Path] = None,
) -> list:
    """
    Return all queue entries for *run_date*.

    Defaults to today in UTC (matching how enqueue() stamps entries).
    Pass an explicit date to query a specific day.
    """
    path = queue_path or _QUEUE_PATH
    if run_date is None:
        date_str = _today_utc()   # UTC — matches enqueue() stamp
    else:
        date_str = run_date.isoformat()
    entries = _load(path)
    return [e for e in entries if e.get("queued_at", "")[:10] == date_str]


def get_all_pending(
    max_age_days: int = 1,
    queue_path: Optional[Path] = None,
) -> list:
    """
    Return all queue entries from the last *max_age_days* calendar days (UTC).

    Preferred for production callers because it tolerates:
      - Pipeline runs that span UTC midnight
      - Berlin (UTC+2) local time being one day ahead of the UTC stamp

    With max_age_days=2 no same-day entry can be missed regardless of timezone.
    """
    from datetime import timedelta
    path    = queue_path or _QUEUE_PATH
    # Cutoff is UTC-anchored so it matches the enqueue() stamps
    utc_today   = datetime.now(timezone.utc).date()
    cutoff_date = utc_today - timedelta(days=max_age_days - 1)
    cutoff      = cutoff_date.isoformat()
    entries     = _load(path)
    return [e for e in entries if e.get("queued_at", "")[:10] >= cutoff]


def clear_stale_entries(
    keep_days: int = 3,
    queue_path: Optional[Path] = None,
) -> int:
    """
    Remove entries whose UTC date is older than *keep_days* calendar days.
    Returns the number of entries removed.
    """
    from datetime import timedelta
    path        = queue_path or _QUEUE_PATH
    utc_today   = datetime.now(timezone.utc).date()
    cutoff_date = utc_today - timedelta(days=keep_days)
    cutoff      = cutoff_date.isoformat()
    entries     = _load(path)
    fresh       = [e for e in entries if e.get("queued_at", "")[:10] >= cutoff]
    removed     = len(entries) - len(fresh)
    if removed:
        _save(fresh, path)
        logger.info("event_queue: pruned %d stale entries (keep_days=%d)", removed, keep_days)
    return removed
