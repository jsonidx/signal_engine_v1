"""
Favorites — persistent user-pinned tickers stored in Supabase.
==============================================================
Provides a single source of truth for "always force-include" tickers:
  • load_favorites()    → list of symbols
  • add_favorite(sym)   → bool (True = success)
  • remove_favorite(sym) → bool
  • sync_to_watchlist() → rewrites MANUALLY ADDED block in watchlist.txt

Falls back to the "MANUALLY ADDED" block in watchlist.txt when Supabase
is unavailable, so the CLI always has something to work with.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_WATCHLIST_PATH = Path(__file__).parent / "watchlist.txt"
_MANUAL_HEADER  = "# ── MANUALLY ADDED / NOT YET SCREENED ──────────────────────"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _supabase_conn():
    """Return a psycopg2 connection or None if unavailable."""
    try:
        from utils.db import get_connection
        return get_connection()
    except Exception as exc:
        logger.debug("Supabase unavailable: %s", exc)
        return None


def _ensure_table(conn) -> None:
    """Create user_favorites if it doesn't exist (idempotent)."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_favorites (
                id        SERIAL PRIMARY KEY,
                symbol    TEXT NOT NULL UNIQUE,
                added_at  TIMESTAMPTZ DEFAULT NOW(),
                notes     TEXT DEFAULT ''
            )
        """)
    conn.commit()


def _load_from_watchlist() -> list[str]:
    """Parse the MANUALLY ADDED section of watchlist.txt as fallback."""
    if not _WATCHLIST_PATH.exists():
        return []
    tickers: list[str] = []
    in_block = False
    try:
        for line in _WATCHLIST_PATH.read_text().splitlines():
            stripped = line.strip()
            if "MANUALLY ADDED" in stripped:
                in_block = True
                continue
            if in_block:
                # Another section header ends the block
                if stripped.startswith("#") and "──" in stripped and "MANUALLY" not in stripped:
                    break
                tok = stripped.split("#")[0].strip().upper()
                if tok and not tok.startswith("-") and "." not in tok:
                    tickers.append(tok)
    except Exception as exc:
        logger.warning("_load_from_watchlist error: %s", exc)
    return tickers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_favorites() -> list[str]:
    """
    Return list of favorite ticker symbols.

    Priority:
      1. Supabase user_favorites table
      2. watchlist.txt MANUALLY ADDED block (offline fallback)
    """
    conn = _supabase_conn()
    if conn is not None:
        try:
            _ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT symbol FROM user_favorites ORDER BY added_at")
                rows = cur.fetchall()
            conn.close()
            favs = [r["symbol"].upper() for r in rows]
            logger.debug("Loaded %d favorites from Supabase", len(favs))
            return favs
        except Exception as exc:
            logger.warning("load_favorites Supabase error: %s — falling back to watchlist.txt", exc)
            try:
                conn.close()
            except Exception:
                pass

    return _load_from_watchlist()


def add_favorite(symbol: str, notes: str = "") -> bool:
    """
    Add a ticker to user_favorites.
    Returns True on success, False on failure (e.g. Supabase down).
    Syncs watchlist.txt on success.
    """
    symbol = symbol.upper().strip()
    conn = _supabase_conn()
    if conn is None:
        logger.warning("add_favorite: Supabase unavailable — cannot persist %s", symbol)
        return False
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_favorites (symbol, notes) VALUES (%s, %s) "
                "ON CONFLICT (symbol) DO UPDATE SET notes = EXCLUDED.notes",
                (symbol, notes or ""),
            )
        conn.commit()
        conn.close()
        sync_to_watchlist()
        logger.info("Added favorite: %s", symbol)
        return True
    except Exception as exc:
        logger.error("add_favorite error for %s: %s", symbol, exc)
        try:
            conn.close()
        except Exception:
            pass
        return False


def remove_favorite(symbol: str) -> bool:
    """
    Remove a ticker from user_favorites.
    Returns True on success, False on failure.
    Syncs watchlist.txt on success.
    """
    symbol = symbol.upper().strip()
    conn = _supabase_conn()
    if conn is None:
        logger.warning("remove_favorite: Supabase unavailable")
        return False
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_favorites WHERE symbol = %s", (symbol,))
        conn.commit()
        conn.close()
        sync_to_watchlist()
        logger.info("Removed favorite: %s", symbol)
        return True
    except Exception as exc:
        logger.error("remove_favorite error for %s: %s", symbol, exc)
        try:
            conn.close()
        except Exception:
            pass
        return False


def sync_to_watchlist(path: Optional[Path] = None) -> None:
    """
    Rewrite the MANUALLY ADDED block in watchlist.txt to match
    the current Supabase user_favorites list.
    """
    favs = load_favorites()
    target = path or _WATCHLIST_PATH
    if not target.exists():
        return

    try:
        lines = target.read_text().splitlines()

        # Strip existing MANUALLY ADDED block
        out: list[str] = []
        skip = False
        for line in lines:
            stripped = line.strip()
            if "MANUALLY ADDED" in stripped:
                skip = True
                continue
            if skip:
                if stripped.startswith("#") and "──" in stripped and "MANUALLY" not in stripped:
                    skip = False
                    out.append(line)
                continue
            out.append(line)

        # Remove trailing blank lines before we re-add the block
        while out and not out[-1].strip():
            out.pop()

        # Append fresh MANUALLY ADDED block
        out.append("")
        out.append(_MANUAL_HEADER)
        for sym in sorted(favs):
            out.append(sym)
        out.append("")

        target.write_text("\n".join(out) + "\n")
        logger.debug("sync_to_watchlist: wrote %d favorites to %s", len(favs), target)
    except Exception as exc:
        logger.warning("sync_to_watchlist error: %s", exc)
