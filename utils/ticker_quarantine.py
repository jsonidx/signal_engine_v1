"""
utils/ticker_quarantine.py
==========================
Shared per-run ticker quarantine.  When a module gets a 404/delisted signal
for a ticker it calls ``quarantine(ticker, reason)`` so that all subsequent
modules skip that ticker without re-fetching.

The quarantine is file-backed (``data/ticker_quarantine.json``) so it works
across module boundaries within the same pipeline run.  Entries are written
with an ISO-date key; if the file is from a previous calendar day it is
ignored (stale entries never block a new run).

Public API
----------
quarantine(ticker, reason)   — mark ticker as bad for this run
is_quarantined(ticker)       — True if ticker is in today's quarantine list
get_quarantined()            — dict {ticker: reason} for today
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

_QUARANTINE_PATH = Path(__file__).resolve().parents[1] / "data" / "ticker_quarantine.json"
_TODAY = date.today().isoformat()

# In-process cache so we don't hit the file system on every check
_cache: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        if _QUARANTINE_PATH.exists():
            raw = json.loads(_QUARANTINE_PATH.read_text())
            if raw.get("_date") == _TODAY:
                _cache = {k: v for k, v in raw.items() if k != "_date"}
                return _cache
    except Exception:
        pass
    _cache = {}
    return _cache


def _flush(q: dict[str, str]) -> None:
    try:
        _QUARANTINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"_date": _TODAY, **q}
        _QUARANTINE_PATH.write_text(json.dumps(payload, indent=2))
    except Exception:
        pass


def quarantine(ticker: str, reason: str) -> None:
    """Add *ticker* to the quarantine list for today.  No-op if already there."""
    q = _load()
    key = ticker.upper()
    if key not in q:
        q[key] = reason
        _flush(q)


def is_quarantined(ticker: str) -> bool:
    """Return True if *ticker* is in today's quarantine list."""
    return ticker.upper() in _load()


def get_quarantined() -> dict[str, str]:
    """Return a copy of today's quarantine dict {TICKER: reason}."""
    return dict(_load())
