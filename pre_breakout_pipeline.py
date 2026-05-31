"""
pre_breakout_pipeline.py — Pre-Breakout Setup Watchlist Pipeline  (TRD-034)

Standalone pipeline, separate from the confirmation pipeline (run_master.sh).
Scores the universe with PFS + PSC signals, persists results to setup_watchlist,
and optionally runs Stage 3 Claude synthesis.

Usage
-----
    # Data-only (no Claude) — requires watchlist.txt or live DB
    python3 pre_breakout_pipeline.py

    # With Stage 3 Claude synthesis
    python3 pre_breakout_pipeline.py --stage3

    # Dry-run (no DB writes) — requires universe source
    python3 pre_breakout_pipeline.py --dry-run

    # Offline dry-run with explicit tickers — no DB or watchlist.txt needed
    python3 pre_breakout_pipeline.py --dry-run --tickers AAPL MSFT GOOGL NVDA META

    # Custom date (for backfill)
    python3 pre_breakout_pipeline.py --date 2026-05-01

Dependencies
------------
- Universe loading (--tickers override removes DB dependency)
- psycopg2 + DATABASE_URL only required when actually persisting (not --dry-run)
- yfinance required for price fetching in all modes

Pipeline stages
---------------
Stage 1 — Deterministic scoring: PFS + PSC per ticker in universe
Stage 2 — Threshold gate: composite_score ≥ STAGE2_THRESHOLD AND pfs_score > PFS_MIN
Stage 3 — Optional bounded Claude synthesis on Stage 2 shortlist (≤10 names/day)

Constraints
-----------
- This pipeline DOES NOT modify daily_rankings, candidate_snapshots, or thesis_cache.
- No options, dark-pool, or narrative signals.
- No LLM in Stage 1 or Stage 2.
- ERM score placeholder is always None until TRD-037 gate passes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Stage 2 thresholds ────────────────────────────────────────────────────────
STAGE2_THRESHOLD = 0.40     # minimum composite_score to pass Stage 2
PFS_MIN = 0.05              # PSC-only guard: pfs_score must exceed this to pass
PFS_WEIGHT = 0.60
PSC_WEIGHT = 0.40
PIPELINE_VERSION = "v1"

# ── Universe settings ─────────────────────────────────────────────────────────
WATCHLIST_PATH = Path(__file__).parent / "watchlist.txt"
MAX_UNIVERSE = 500          # cap to avoid runaway API calls
PRICE_LOOKBACK_DAYS = 80    # trading days of history for signal computation


def load_universe() -> list[str]:
    """Load ticker universe from watchlist.txt, falling back to daily_rankings."""
    if WATCHLIST_PATH.exists():
        tickers = [
            line.strip().upper()
            for line in WATCHLIST_PATH.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if tickers:
            logger.info("Universe: %d tickers from watchlist.txt", len(tickers))
            return tickers[:MAX_UNIVERSE]

    # Fallback: recent daily_rankings tickers
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT ticker FROM daily_rankings
            WHERE run_date >= CURRENT_DATE - 7
            ORDER BY ticker
            LIMIT %s
            """,
            (MAX_UNIVERSE,),
        )
        tickers = [r["ticker"] for r in cur.fetchall()]
        conn.close()
        logger.info("Universe: %d tickers from daily_rankings fallback", len(tickers))
        return tickers
    except Exception as exc:
        logger.error("Failed to load universe: %s", exc)
        return []


def load_sector_map(tickers: list[str]) -> dict[str, str]:
    """Load sector mapping from daily_rankings (most recent entry per ticker)."""
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(tickers))
        cur.execute(
            f"""
            SELECT DISTINCT ON (ticker) ticker, sector
            FROM daily_rankings
            WHERE ticker IN ({placeholders})
            ORDER BY ticker, run_date DESC
            """,
            tickers,
        )
        mapping = {r["ticker"]: r["sector"] or "Unknown" for r in cur.fetchall()}
        conn.close()
        return mapping
    except Exception as exc:
        logger.warning("load_sector_map failed: %s", exc)
        return {}


def fetch_ohlcv(
    tickers: list[str],
    start: date,
    end: date,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Fetch adjusted close, high, low, volume for all tickers.
    Returns (prices, highs, lows, volumes).
    """
    symbols = " ".join(tickers)
    try:
        raw = yf.download(
            symbols,
            start=start,
            end=end + timedelta(days=1),
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as exc:
        logger.error("OHLCV download failed: %s", exc)
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    if raw.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    def _extract(field: str) -> pd.DataFrame:
        if isinstance(raw.columns, pd.MultiIndex):
            if field in raw.columns.get_level_values(0):
                df = raw[field].copy()
            else:
                df = pd.DataFrame()
        else:
            df = raw[["Close"]].rename(columns={"Close": tickers[0]}) if field == "Close" else pd.DataFrame()
        df.index = pd.to_datetime(df.index).date
        return df

    return _extract("Close"), _extract("High"), _extract("Low"), _extract("Volume")


def run_pipeline(
    run_date: date | None = None,
    stage3: bool = False,
    dry_run: bool = False,
    tickers_override: list[str] | None = None,
) -> dict:
    """
    Execute one full pre-breakout pipeline run.

    Parameters
    ----------
    tickers_override : if provided, use this list as the universe instead of
                       watchlist.txt or daily_rankings. Enables offline/test runs
                       without DB access.

    Returns summary dict with run metadata.
    """
    from utils.pfs_signal import score_pfs
    from utils.psc_signal import score_psc

    today = run_date or date.today()
    logger.info("Pre-breakout pipeline starting: %s (stage3=%s, dry_run=%s)",
                today, stage3, dry_run)

    # ── 1. Universe + sector map ───────────────────────────────────────────────
    if tickers_override is not None:
        # Explicit override — no DB calls at all (tickers_override=[]=error path)
        universe = [t.upper().strip() for t in tickers_override if t.strip()]
        logger.info("Universe: %d tickers from --tickers override", len(universe))
        sector_map: dict[str, str] = {}   # unknown sectors; PFS groups by "Unknown"
    else:
        universe = load_universe()
        sector_map = load_sector_map(universe)
    if not universe:
        logger.error("Empty universe — aborting")
        return {"status": "error", "reason": "empty_universe"}

    # ── 2. Fetch OHLCV ────────────────────────────────────────────────────────
    # Use business-day lookback from today
    bdays = pd.bdate_range(end=today, periods=PRICE_LOOKBACK_DAYS + 5)
    fetch_start = bdays[0].date()

    logger.info("Fetching OHLCV for %d tickers (%s → %s)...", len(universe), fetch_start, today)
    prices, highs, lows, volumes = fetch_ohlcv(universe, fetch_start, today)

    if prices.empty:
        logger.error("No price data returned — aborting")
        return {"status": "error", "reason": "no_prices"}

    logger.info("Price matrix: %d days × %d tickers", prices.shape[0], prices.shape[1])

    available = [t for t in universe if t in prices.columns]
    logger.info("Scoreable tickers: %d of %d", len(available), len(universe))

    # ── 3. Stage 1: PFS scoring ────────────────────────────────────────────────
    logger.info("Running PFS scoring...")
    pfs_results = score_pfs(
        universe=available,
        sector_map=sector_map,
        prices=prices,
        volumes=volumes,
        as_of_date=today,
    )
    pfs_map = {r.ticker: r for r in pfs_results}

    # ── 4. Stage 1: PSC scoring ────────────────────────────────────────────────
    logger.info("Running PSC scoring...")
    psc_results = score_psc(
        universe=available,
        prices=prices,
        highs=highs,
        lows=lows,
        volumes=volumes,
        as_of_date=today,
    )
    psc_map = {r.ticker: r for r in psc_results}

    # ── 5. Stage 2: composite score + gate ────────────────────────────────────
    rows = []
    for ticker in available:
        pfs_r = pfs_map.get(ticker)
        psc_r = psc_map.get(ticker)

        pfs_score = pfs_r.pfs_score if pfs_r else 0.0
        psc_score = psc_r.psc_score if psc_r else 0.0

        composite = PFS_WEIGHT * pfs_score + PSC_WEIGHT * psc_score

        # PSC-only guard: must have meaningful PFS to pass Stage 2
        stage2_passed = (composite >= STAGE2_THRESHOLD) and (pfs_score > PFS_MIN)

        rows.append({
            "ticker": ticker,
            "composite_score": composite,
            "pfs_score": pfs_score,
            "psc_score": psc_score,
            "erm_score": None,   # blocked until TRD-037 gate passes
            "stage2_passed": stage2_passed,
            "pipeline_version": PIPELINE_VERSION,
        })

    stage2_count = sum(1 for r in rows if r["stage2_passed"])
    logger.info(
        "Stage 2: %d of %d tickers pass (composite ≥ %.2f AND pfs > %.2f)",
        stage2_count, len(rows), STAGE2_THRESHOLD, PFS_MIN,
    )

    # ── 6. Stage 3: bounded Claude synthesis ──────────────────────────────────
    if stage3 and stage2_count > 0:
        from utils.stage3_synthesis import run_stage3_synthesis
        shortlist = [r for r in rows if r["stage2_passed"]]
        logger.info("Running Stage 3 synthesis on %d names...", len(shortlist))
        stage3_results = run_stage3_synthesis(shortlist, dry_run=dry_run)
        s3_map = {r["ticker"]: r for r in stage3_results}
        for row in rows:
            s3 = s3_map.get(row["ticker"])
            if s3:
                row["archetype"] = s3.get("archetype")
                row["invalidation_condition"] = s3.get("invalidation_condition")
                row["setup_grade"] = s3.get("setup_grade")
                row["key_risk"] = s3.get("key_risk")

    # ── 7. Persist ─────────────────────────────────────────────────────────────
    if not dry_run:
        from utils.supabase_persist import save_setup_watchlist_rows, update_setup_watchlist_stage3
        save_setup_watchlist_rows(rows, run_date=today.isoformat())
        if stage3:
            for row in rows:
                if row.get("archetype") is not None or row.get("setup_grade") is not None:
                    update_setup_watchlist_stage3(today.isoformat(), row["ticker"], row)
        logger.info("Persisted %d rows to setup_watchlist", len(rows))
    else:
        logger.info("[dry-run] Would write %d rows to setup_watchlist", len(rows))

    summary = {
        "status": "ok",
        "run_date": today.isoformat(),
        "universe_size": len(universe),
        "scored": len(available),
        "stage2_passed": stage2_count,
        "stage3_run": stage3 and not dry_run,
        "pipeline_version": PIPELINE_VERSION,
    }
    logger.info("Pipeline complete: %s", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-breakout setup watchlist pipeline")
    parser.add_argument("--date", default=None, help="Run date YYYY-MM-DD (default: today)")
    parser.add_argument("--stage3", action="store_true", help="Run Stage 3 Claude synthesis")
    parser.add_argument("--dry-run", action="store_true", help="Skip DB writes")
    parser.add_argument(
        "--tickers", nargs="+", default=None, metavar="TICKER",
        help="Explicit universe override; removes dependency on watchlist.txt or DB",
    )
    args = parser.parse_args()

    run_date = date.fromisoformat(args.date) if args.date else None
    result = run_pipeline(
        run_date=run_date,
        stage3=args.stage3,
        dry_run=args.dry_run,
        tickers_override=args.tickers,
    )
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
