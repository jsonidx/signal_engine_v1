#!/usr/bin/env python3
"""
Refresh AI theses that haven't been updated in N days.

Usage:
    python refresh_stale_theses.py                    # dry run, 7-day cutoff
    python refresh_stale_theses.py --run              # actually re-run
    python refresh_stale_theses.py --days 14 --run    # 14-day cutoff
    python refresh_stale_theses.py --days 5 --run     # 5-day cutoff
    python refresh_stale_theses.py --llm grok-4.3 --run

Logic:
    Finds all tickers whose most recent thesis is older than --days.
    Calls ai_quant.py --tickers <list> --no-cache --force-ai for each batch.

    TRD-002: also detects catalyst-driven refresh triggers that fire BEFORE the
    age cutoff:
      1. price_above_entry_zone — price > entry_high * (1 + PRICE_ABOVE_PCT)
      2. top_rank_stale_thesis  — top-5 rank with NEUTRAL/stale thesis
      3. near_earnings_catalyst — within NEAR_EARNINGS_DAYS and evidence changed
"""

import argparse
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import yfinance as yf

from utils.db import get_connection

logger = logging.getLogger(__name__)

# ── Refresh trigger thresholds (can be overridden via strategy_config) ────────
PRICE_ABOVE_PCT       = 5.0    # % above entry_high → price_above_entry_zone
TOP_RANK_THRESHOLD    = 5      # rank ≤ N → top_rank_stale_thesis trigger
NEAR_EARNINGS_DAYS    = 14     # days-to-earnings window for near_earnings_catalyst
SAME_DAY_LOCK         = True   # prevent re-refresh on same calendar day


def _fetch_current_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch latest close prices for refresh-trigger decisions."""
    if not tickers:
        return {}
    try:
        data = yf.download(
            tickers=list(dict.fromkeys(tickers)),
            period="5d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        out: dict[str, float] = {}
        if len(tickers) == 1:
            close = data.get("Close")
            if close is not None and len(close.dropna()) > 0:
                out[tickers[0]] = float(close.dropna().iloc[-1])
            return out

        for ticker in tickers:
            try:
                close = data[ticker]["Close"].dropna()
                if len(close) > 0:
                    out[ticker] = float(close.iloc[-1])
            except Exception:
                continue
        return out
    except Exception as exc:
        logger.warning("Could not fetch current prices for refresh candidates: %s", exc)
        return {}


def should_refresh_thesis(
    ticker: str,
    current_price: float,
    entry_high: Optional[float],
    entry_low: Optional[float],
    thesis_date: str,
    days_to_earnings: Optional[int] = None,
    rank: Optional[int] = None,
    thesis_direction: str = "NEUTRAL",
    price_above_pct: float = PRICE_ABOVE_PCT,
    top_rank_threshold: int = TOP_RANK_THRESHOLD,
    near_earnings_days: int = NEAR_EARNINGS_DAYS,
) -> Tuple[bool, str]:
    """
    Determine whether a ticker's thesis should be refreshed for a catalyst reason,
    independently of the age-based staleness cutoff.

    Returns (should_refresh: bool, reason: str).

    Trigger 1 — price_above_entry_zone:
        Current price has moved more than `price_above_pct`% above entry_high,
        meaning the AI's entry frame is no longer valid.
        Motivating case: SNOW May 15 2026 — entry_high=$153, price=$168.

    Trigger 2 — top_rank_stale_thesis:
        Ticker is in the top `top_rank_threshold` daily_rankings but has a
        NEUTRAL or stale thesis.  The engine has ranked it highly but the
        AI thesis doesn't reflect that confidence.

    Trigger 3 — near_earnings_catalyst:
        Earnings are within `near_earnings_days` and the current thesis is NEUTRAL
        or more than 7 days old.  Near-earnings windows are high-volatility;
        stale neutral theses understate the binary risk.

    Same-day lock: if today == thesis_date we never re-refresh (prevents loops).
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if SAME_DAY_LOCK and thesis_date == today_str:
        return False, "same_day_lock"

    # Trigger 1: price above entry zone
    if entry_high and entry_high > 0 and current_price > 0:
        threshold_price = entry_high * (1 + price_above_pct / 100.0)
        if current_price > threshold_price:
            logger.info(
                "[%s] Refresh trigger: price_above_entry_zone "
                "(price=%.2f > entry_high=%.2f + %.1f%%)",
                ticker, current_price, entry_high, price_above_pct,
            )
            return True, "price_above_entry_zone"

    # Trigger 2: top-rank with stale/neutral thesis
    if rank is not None and rank <= top_rank_threshold:
        if thesis_direction.upper() in ("NEUTRAL", "UNKNOWN", ""):
            logger.info(
                "[%s] Refresh trigger: top_rank_stale_thesis (rank=%d, direction=%s)",
                ticker, rank, thesis_direction,
            )
            return True, "top_rank_stale_thesis"

    # Trigger 3: near earnings with neutral/stale thesis
    if days_to_earnings is not None and 0 <= days_to_earnings <= near_earnings_days:
        if thesis_direction.upper() == "NEUTRAL":
            logger.info(
                "[%s] Refresh trigger: near_earnings_catalyst "
                "(days_to_earnings=%d, direction=%s)",
                ticker, days_to_earnings, thesis_direction,
            )
            return True, "near_earnings_catalyst"

    return False, "no_trigger"


def get_catalyst_refresh_candidates(
    daily_rankings: Optional[list] = None,
    price_above_pct: float = PRICE_ABOVE_PCT,
    top_rank_threshold: int = TOP_RANK_THRESHOLD,
    near_earnings_days: int = NEAR_EARNINGS_DAYS,
) -> list[dict]:
    """
    Query Supabase for tickers that should be refreshed due to catalyst triggers
    (TRD-002).  Returns a list of dicts with keys:
        ticker, reason, current_price, entry_high, thesis_date, thesis_direction

    Requires live DB connection.  Returns [] on any error.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ticker FROM blacklist WHERE expires_at IS NULL OR expires_at > NOW()")
                blacklisted = {r["ticker"] for r in cur.fetchall()}

                # Get the most recent thesis per ticker including entry_high
                cur.execute(
                    """
                    SELECT DISTINCT ON (ticker)
                        ticker, direction, entry_high, entry_low,
                        created_at::date::text AS thesis_date
                    FROM thesis_cache
                    ORDER BY ticker, created_at DESC
                    """
                )
                theses = {r["ticker"]: dict(r) for r in cur.fetchall()
                          if r["ticker"] not in blacklisted}

                # Get latest daily_rankings for rank context. Current price is
                # fetched separately; t1_price/t2_price are targets, not live price.
                cur.execute(
                    """
                    SELECT DISTINCT ON (ticker)
                        ticker, rank
                    FROM daily_rankings
                    WHERE run_date = (SELECT MAX(run_date) FROM daily_rankings)
                    ORDER BY ticker, created_at DESC
                    """
                )
                rankings = {r["ticker"]: dict(r) for r in cur.fetchall()}

                # Latest persisted catalyst context. This lets the production
                # refresh path use near_earnings_catalyst without an extra live
                # earnings-calendar call.
                cur.execute(
                    """
                    SELECT DISTINCT ON (ticker)
                        ticker, days_to_earnings
                    FROM catalyst_scores
                    WHERE date = (SELECT MAX(date) FROM catalyst_scores)
                    ORDER BY ticker, date DESC
                    """
                )
                catalysts = {r["ticker"]: dict(r) for r in cur.fetchall()}

        # Only fetch prices for tickers that have both a thesis and a ranking.
        # Fetching all rankings tickers would waste yfinance calls for tickers
        # we can't check against a thesis anyway.
        needed = [t for t in theses if t in rankings]
        candidates = []
        current_prices = _fetch_current_prices(needed)

        for ticker, thesis in theses.items():
            if ticker not in rankings:
                continue
            rank_row = rankings[ticker]
            current_price = current_prices.get(ticker, 0.0)
            should, reason = should_refresh_thesis(
                ticker=ticker,
                current_price=float(current_price or 0),
                entry_high=float(thesis.get("entry_high") or 0),
                entry_low=float(thesis.get("entry_low") or 0),
                thesis_date=thesis.get("thesis_date", ""),
                days_to_earnings=(
                    catalysts.get(ticker, {}).get("days_to_earnings")
                    if ticker in catalysts else None
                ),
                rank=rank_row.get("rank"),
                thesis_direction=thesis.get("direction", "NEUTRAL"),
                price_above_pct=price_above_pct,
                top_rank_threshold=top_rank_threshold,
                near_earnings_days=near_earnings_days,
            )
            if should:
                candidates.append({
                    "ticker": ticker,
                    "reason": reason,
                    "current_price": current_price,
                    "entry_high": thesis.get("entry_high"),
                    "thesis_date": thesis.get("thesis_date"),
                    "thesis_direction": thesis.get("direction"),
                    "days_to_earnings": (
                        catalysts.get(ticker, {}).get("days_to_earnings")
                        if ticker in catalysts else None
                    ),
                })

        return candidates
    except Exception as exc:
        logger.warning("get_catalyst_refresh_candidates failed: %s", exc)
        return []


def get_stale_tickers(days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM blacklist WHERE expires_at IS NULL OR expires_at > NOW()")
            blacklisted = {r["ticker"] for r in cur.fetchall()}
            cur.execute(
                """
                SELECT ticker, MAX(created_at) AS latest_thesis
                FROM thesis_cache
                GROUP BY ticker
                HAVING MAX(created_at::timestamptz) < %s
                ORDER BY latest_thesis ASC
                """,
                (cutoff,),
            )
            return [r for r in cur.fetchall() if r["ticker"] not in blacklisted]


def main():
    parser = argparse.ArgumentParser(description="Refresh stale AI theses")
    parser.add_argument("--days", type=int, default=7, help="Stale threshold in days (default: 7)")
    parser.add_argument("--run", action="store_true", help="Actually execute re-runs (default: dry run)")
    parser.add_argument("--llm", type=str, default=None, help="LLM backend to use (e.g. grok-4.3)")
    parser.add_argument("--batch-size", type=int, default=10, help="Tickers per ai_quant.py call (default: 10)")
    parser.add_argument(
        "--catalyst", action="store_true",
        help="Also include catalyst-trigger refreshes (price_above_entry, top_rank, near_earnings)",
    )
    args = parser.parse_args()

    # Catalyst-driven refresh candidates (TRD-002)
    catalyst_tickers: set[str] = set()
    if args.catalyst:
        cat_candidates = get_catalyst_refresh_candidates()
        if cat_candidates:
            print(f"\nCatalyst refresh triggers ({len(cat_candidates)} tickers):")
            for c in cat_candidates:
                print(f"  {c['ticker']:<8} reason={c['reason']:<30} "
                      f"price={c.get('current_price') or 'N/A'}  "
                      f"entry_high={c.get('entry_high') or 'N/A'}  "
                      f"thesis={c.get('thesis_date') or 'N/A'}")
                catalyst_tickers.add(c["ticker"])

    rows = get_stale_tickers(args.days)

    stale_tickers = [r["ticker"] for r in rows]
    # Merge catalyst triggers with age-based stale list (deduped)
    all_tickers = list(dict.fromkeys(list(catalyst_tickers) + stale_tickers))

    if not all_tickers:
        print(f"No stale or catalyst-triggered theses found.")
        return

    tickers = all_tickers
    cutoff_str = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")

    print(f"\nFound {len(tickers)} tickers to refresh "
          f"({len(rows)} age-stale, {len(catalyst_tickers)} catalyst-triggered):")
    for r in rows:
        latest = datetime.fromisoformat(r["latest_thesis"]).replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - latest).days
        print(f"  {r['ticker']:<8} last thesis: {latest.strftime('%Y-%m-%d')}  ({age}d ago)")

    if not args.run:
        print(f"\nDry run — pass --run to execute.")
        print(f"Command that would run:")
        for i in range(0, len(tickers), args.batch_size):
            batch = tickers[i : i + args.batch_size]
            cmd = build_cmd(batch, args.llm)
            print(f"  {' '.join(cmd)}")
        return

    print(f"\nRunning in batches of {args.batch_size}...")
    for i in range(0, len(tickers), args.batch_size):
        batch = tickers[i : i + args.batch_size]
        cmd = build_cmd(batch, args.llm)
        print(f"\n--- Batch {i // args.batch_size + 1}: {' '.join(batch)} ---")
        print(f"CMD: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd="/Users/jason/signal_engine_v1")
        if result.returncode != 0:
            print(f"WARNING: batch exited with code {result.returncode}")


def build_cmd(tickers: list[str], llm: str | None) -> list[str]:
    cmd = [sys.executable, "ai_quant.py", "--tickers"] + tickers + ["--no-cache", "--force-ai"]
    if llm:
        cmd += ["--llm", llm]
    return cmd


if __name__ == "__main__":
    main()
