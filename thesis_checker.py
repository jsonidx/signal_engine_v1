"""
================================================================================
THESIS CHECKER — Claude prediction outcome tracker
================================================================================
Runs each pipeline cycle. For every Claude thesis in ai_quant_cache.db it:

  1. Fetches OHLC price history since thesis_date (via yfinance)
  2. Records price snapshots at 1d / 7d / 14d / 30d after thesis
  3. Checks if target_1, target_2, stop_loss were hit (HIGH/LOW, not just close)
  4. Calculates return % vs entry_price at each snapshot
  5. Calculates % gap between Claude's price targets and actual price
  6. Marks outcome: HIT_TARGET1 / HIT_TARGET2 / HIT_STOP / OPEN / EXPIRED (>30d)
  7. Sets claude_correct=1/0 based on direction vs 30d price
  8. Links to trade_journal.db if a trade was made within 3 days of thesis_date

USAGE:
    python3 thesis_checker.py              # update all open thesis outcomes
    python3 thesis_checker.py --report     # print accuracy summary table
    python3 thesis_checker.py --verbose    # per-ticker update detail
    python3 thesis_checker.py --days 60    # report covers last N days (default 90)

OUTCOME DEFINITIONS:
    HIT_TARGET1  — target_1 reached before stop or 30d expiry  (best case)
    HIT_TARGET2  — target_2 reached (implies target_1 also hit)
    HIT_STOP     — stop_loss triggered before any target
    EXPIRED      — 30 calendar days elapsed, neither target nor stop hit
    OPEN         — < 30 days, outcome still pending

DIRECTION LOGIC:
    BULL: target hit when daily HIGH >= target; stop hit when daily LOW <= stop
    BEAR: target hit when daily LOW  <= target; stop hit when daily HIGH >= stop
================================================================================
"""

import os
import sys
import argparse
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

import psycopg2.extras
import yfinance as yf
import pandas as pd

from utils.db import get_connection

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OUTCOME_WINDOW_DAYS = 30   # mark EXPIRED after this many calendar days
TRADE_LINK_WINDOW   = 3    # trade within N days of thesis counts as linked


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _connect():
    """Return a Supabase connection (thesis_cache and trades tables exist)."""
    return get_connection()


def _init_outcomes_table(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS thesis_outcomes (
            id                  SERIAL PRIMARY KEY,
            thesis_id           INTEGER NOT NULL,
            ticker              TEXT    NOT NULL,
            thesis_date         TEXT    NOT NULL,
            direction           TEXT,
            conviction          INTEGER,
            time_horizon        TEXT,
            -- reference prices from Claude
            entry_price         REAL,   -- closing price on thesis_date (actual market open)
            target_1            REAL,
            target_2            REAL,
            stop_loss           REAL,
            -- price snapshots (closing price N calendar days after thesis_date)
            price_1d            REAL,
            price_7d            REAL,
            price_14d           REAL,
            price_30d           REAL,
            -- return % vs entry_price at each snapshot
            return_1d           REAL,
            return_7d           REAL,
            return_14d          REAL,
            return_30d          REAL,
            -- gap between Claude target and actual price at resolution/30d
            -- negative = price fell short of bull target / overshot bear target
            vs_target_1_pct     REAL,
            vs_target_2_pct     REAL,
            vs_stop_pct         REAL,
            -- hit flags (checked using OHLC highs/lows)
            hit_target_1        BOOLEAN DEFAULT FALSE,
            hit_target_2        BOOLEAN DEFAULT FALSE,
            hit_stop            BOOLEAN DEFAULT FALSE,
            -- days from thesis_date to first hit (trading days in price history)
            days_to_target_1    INTEGER,
            days_to_target_2    INTEGER,
            days_to_stop        INTEGER,
            -- outcome
            outcome             TEXT,   -- HIT_TARGET1/HIT_TARGET2/HIT_STOP/OPEN/EXPIRED
            claude_correct      INTEGER, -- 1=direction right at 30d, 0=wrong, NULL=neutral/open
            -- trade linkage
            was_traded          BOOLEAN DEFAULT FALSE,
            trade_id            INTEGER,
            -- metadata
            last_checked        TEXT,
            resolved_at         TEXT,
            created_at          TEXT,
            UNIQUE(thesis_id)
        )
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Price helpers
# ---------------------------------------------------------------------------

def _fetch_ohlc(tickers: List[str], start_date: str) -> Dict[str, pd.DataFrame]:
    """
    Batch-download daily OHLC for all tickers from start_date to today.
    Returns {ticker: DataFrame(Open, High, Low, Close)} indexed by date.
    """
    if not tickers:
        return {}

    try:
        raw = yf.download(
            tickers,
            start=start_date,
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"  [checker] yfinance download error: {e}")
        return {}

    result: Dict[str, pd.DataFrame] = {}
    if raw.empty:
        return result

    if isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            try:
                df = raw.xs(t, axis=1, level=1)[["Open", "High", "Low", "Close"]]
                df = df.dropna(how="all")
                result[t] = df
            except KeyError:
                pass
    else:
        # Single ticker — columns are Open/High/Low/Close
        t = tickers[0]
        df = raw[["Open", "High", "Low", "Close"]].dropna(how="all")
        result[t] = df

    return result


def _price_at_offset(df: pd.DataFrame, thesis_date: str, offset_days: int) -> Optional[float]:
    """
    Return the closing price closest to (thesis_date + offset_days).
    Uses the nearest available trading day on or after the target date.
    """
    if df is None or df.empty:
        return None
    target = pd.Timestamp(thesis_date) + timedelta(days=offset_days)
    future = df[df.index >= target]
    if future.empty:
        return None
    return float(future["Close"].iloc[0])


def _entry_price(df: pd.DataFrame, thesis_date: str) -> Optional[float]:
    """Closing price on thesis_date itself (first available on/after that date)."""
    if df is None or df.empty:
        return None
    ts = pd.Timestamp(thesis_date)
    avail = df[df.index >= ts]
    if avail.empty:
        return None
    return float(avail["Close"].iloc[0])


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """(a - b) / |b| * 100, rounded to 2dp. None if either is missing/zero."""
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / abs(b) * 100, 2)


# ---------------------------------------------------------------------------
# Hit detection
# ---------------------------------------------------------------------------

def _find_hit(
    df: pd.DataFrame,
    thesis_date: str,
    level: float,
    direction: str,
    use_high: bool,
) -> Optional[int]:
    """
    Return the number of rows (trading days) from thesis_date until
    the price level is hit. None if never hit within the df range.

    use_high=True  → check High column (target for BULL, stop for BEAR)
    use_high=False → check Low  column (target for BEAR, stop for BULL)
    """
    if df is None or df.empty or level is None or level <= 0:
        return None

    ts = pd.Timestamp(thesis_date)
    future = df[df.index > ts]   # strictly after thesis (need at least 1 day)
    if future.empty:
        return None

    col = "High" if use_high else "Low"
    if use_high:
        hits = future[future[col] >= level]
    else:
        hits = future[future[col] <= level]

    if hits.empty:
        return None

    first_hit = hits.index[0]
    days = int((future.index <= first_hit).sum())
    return days


# ---------------------------------------------------------------------------
# Core outcome computation
# ---------------------------------------------------------------------------

def _compute_outcome(thesis, df: Optional[pd.DataFrame]) -> dict:
    """
    Compute all outcome fields for one thesis row. Returns a dict ready for
    INSERT/UPDATE into thesis_outcomes.
    """
    ticker      = thesis["ticker"]
    thesis_date = thesis["date"]
    direction   = (thesis["direction"] or "NEUTRAL").upper()
    target_1    = thesis["target_1"]
    target_2    = thesis["target_2"]
    stop_loss   = thesis["stop_loss"]
    now         = datetime.now()

    out: dict = {
        "thesis_id":    thesis["id"],
        "ticker":       ticker,
        "thesis_date":  thesis_date,
        "direction":    direction,
        "conviction":   thesis["conviction"],
        "time_horizon": thesis["time_horizon"],
        "target_1":     target_1,
        "target_2":     target_2,
        "stop_loss":    stop_loss,
        "last_checked": now.isoformat(),
        "created_at":   now.isoformat(),
    }

    if df is None or df.empty:
        out["outcome"] = "OPEN"
        return out

    # ── Reference price ──────────────────────────────────────────────────────
    ep = _entry_price(df, thesis_date)
    out["entry_price"] = ep

    # ── Price snapshots ───────────────────────────────────────────────────────
    p1  = _price_at_offset(df, thesis_date, 1)
    p7  = _price_at_offset(df, thesis_date, 7)
    p14 = _price_at_offset(df, thesis_date, 14)
    p30 = _price_at_offset(df, thesis_date, 30)

    out["price_1d"]  = p1
    out["price_7d"]  = p7
    out["price_14d"] = p14
    out["price_30d"] = p30

    out["return_1d"]  = _pct(p1,  ep)
    out["return_7d"]  = _pct(p7,  ep)
    out["return_14d"] = _pct(p14, ep)
    out["return_30d"] = _pct(p30, ep)

    # ── Hit detection ────────────────────────────────────────────────────────
    # BULL: target reached when HIGH >= target; stop when LOW <= stop
    # BEAR: target reached when LOW  <= target; stop when HIGH >= stop
    is_bull = direction == "BULL"
    is_bear = direction == "BEAR"

    days_to_t1   = None
    days_to_t2   = None
    days_to_stop = None

    if is_bull:
        days_to_t1   = _find_hit(df, thesis_date, target_1,  direction, use_high=True)
        days_to_t2   = _find_hit(df, thesis_date, target_2,  direction, use_high=True)
        days_to_stop = _find_hit(df, thesis_date, stop_loss, direction, use_high=False)
    elif is_bear:
        days_to_t1   = _find_hit(df, thesis_date, target_1,  direction, use_high=False)
        days_to_t2   = _find_hit(df, thesis_date, target_2,  direction, use_high=False)
        days_to_stop = _find_hit(df, thesis_date, stop_loss, direction, use_high=True)

    hit_t1   = days_to_t1   is not None
    hit_t2   = days_to_t2   is not None
    hit_stop = days_to_stop is not None

    out["hit_target_1"]    = int(hit_t1)
    out["hit_target_2"]    = int(hit_t2)
    out["hit_stop"]        = int(hit_stop)
    out["days_to_target_1"] = days_to_t1
    out["days_to_target_2"] = days_to_t2
    out["days_to_stop"]     = days_to_stop

    # ── vs-target % gap ───────────────────────────────────────────────────────
    # Use the latest available price as the "actual" for the gap calculation
    latest_price = p30 or p14 or p7 or p1
    out["vs_target_1_pct"] = _pct(latest_price, target_1)  if target_1  else None
    out["vs_target_2_pct"] = _pct(latest_price, target_2)  if target_2  else None
    out["vs_stop_pct"]     = _pct(latest_price, stop_loss) if stop_loss else None

    # ── Outcome classification ────────────────────────────────────────────────
    days_elapsed = (now - datetime.strptime(thesis_date, "%Y-%m-%d")).days

    # Determine which event happened first (stop vs target)
    outcome = "OPEN"
    resolved_at = None

    if hit_t2 and hit_t1:
        # Both targets hit — check which came first vs stop
        first_target = min(d for d in [days_to_t1, days_to_t2] if d)
        if hit_stop and days_to_stop < first_target:
            outcome = "HIT_STOP"
        else:
            outcome = "HIT_TARGET2"
    elif hit_t1:
        if hit_stop and days_to_stop < days_to_t1:
            outcome = "HIT_STOP"
        else:
            outcome = "HIT_TARGET1"
    elif hit_stop:
        outcome = "HIT_STOP"
    elif days_elapsed >= OUTCOME_WINDOW_DAYS:
        outcome = "EXPIRED"
    else:
        outcome = "OPEN"

    if outcome != "OPEN":
        resolved_at = now.isoformat()

    out["outcome"]     = outcome
    out["resolved_at"] = resolved_at

    # ── Claude correct? ───────────────────────────────────────────────────────
    ref = p30 or latest_price
    if ref is not None and ep is not None and direction in ("BULL", "BEAR"):
        if direction == "BULL":
            out["claude_correct"] = 1 if ref > ep else 0
        else:
            out["claude_correct"] = 1 if ref < ep else 0
    else:
        out["claude_correct"] = None

    return out


# ---------------------------------------------------------------------------
# Trade linkage
# ---------------------------------------------------------------------------

def _find_linked_trade(ticker: str, thesis_date: str) -> Tuple[int, Optional[int]]:
    """
    Check trade_journal.db for a BUY trade on ticker within TRADE_LINK_WINDOW
    days of thesis_date. Returns (was_traded, trade_id).
    """
    conn = _connect()
    try:
        d = datetime.strptime(thesis_date, "%Y-%m-%d")
        lo = (d - timedelta(days=TRADE_LINK_WINDOW)).strftime("%Y-%m-%d")
        hi = (d + timedelta(days=TRADE_LINK_WINDOW)).strftime("%Y-%m-%d")
        cur = conn.cursor()
        cur.execute(
            """SELECT id FROM trades
               WHERE ticker=%s AND action='BUY' AND date BETWEEN %s AND %s
               ORDER BY ABS(date::date - %s::date) LIMIT 1""",
            (ticker, lo, hi, thesis_date),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return 1, row["id"]
        return 0, None
    except Exception:
        return 0, None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main checker
# ---------------------------------------------------------------------------

def run_checker(verbose: bool = False) -> int:
    """
    Update thesis_outcomes for all theses. Returns count of records updated.
    """
    conn = _connect()
    _init_outcomes_table(conn)

    # Load all theses
    cur = conn.cursor()
    cur.execute("SELECT * FROM thesis_cache ORDER BY date DESC")
    theses = cur.fetchall()

    if not theses:
        print("  [checker] No theses in cache yet.")
        conn.close()
        return 0

    # Separate: already-resolved outcomes don't need re-checking
    existing = {
        row["thesis_id"]: row["outcome"]
        for row in cur.execute("SELECT thesis_id, outcome FROM thesis_outcomes").fetchall()
    }
    resolved_states = {"HIT_TARGET1", "HIT_TARGET2", "HIT_STOP", "EXPIRED"}

    to_check = [
        t for t in theses
        if existing.get(t["id"]) not in resolved_states
    ]

    if not to_check:
        print("  [checker] All theses already resolved — nothing to update.")
        conn.close()
        return 0

    print(f"  [checker] Checking {len(to_check)} open thesis(es)...")

    # Batch price download — find earliest thesis_date
    tickers      = list({t["ticker"] for t in to_check})
    oldest_date  = min(t["date"] for t in to_check)
    # Fetch from 2 days before oldest thesis (to get entry_price on thesis_date)
    fetch_start  = (datetime.strptime(oldest_date, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")

    if verbose:
        print(f"  [checker] Fetching OHLC for {tickers} from {fetch_start}...")

    ohlc = _fetch_ohlc(tickers, fetch_start)

    updated = 0
    for thesis in to_check:
        ticker = thesis["ticker"]
        df     = ohlc.get(ticker)
        out    = _compute_outcome(thesis, df)

        # Trade linkage
        was_traded, trade_id = _find_linked_trade(ticker, thesis["date"])
        out["was_traded"] = was_traded
        out["trade_id"]   = trade_id

        # Upsert
        cols = ", ".join(out.keys())
        placeholders = ", ".join("%s" for _ in out)
        updates = ", ".join(
            f"{k}=excluded.{k}"
            for k in out
            if k not in ("thesis_id", "created_at")
        )
        cur = conn.cursor()
        cur.execute(
            f"""INSERT INTO thesis_outcomes ({cols}) VALUES ({placeholders})
                ON CONFLICT(thesis_id) DO UPDATE SET {updates}""",
            list(out.values()),
        )

        if verbose:
            outcome = out.get("outcome", "?")
            ret30   = out.get("return_30d")
            ret_str = f"{ret30:+.1f}%" if ret30 is not None else "n/a"
            correct = out.get("claude_correct")
            correct_str = {1: "✓", 0: "✗", None: "—"}.get(correct, "—")
            print(
                f"    {ticker:<6} {thesis['date']}  "
                f"{thesis['direction']:<7} conv={thesis['conviction']}  "
                f"outcome={outcome:<12} 30d={ret_str:>7}  correct={correct_str}"
            )

        updated += 1

    conn.commit()
    conn.close()
    print(f"  [checker] Updated {updated} thesis outcome(s).")
    return updated


# ---------------------------------------------------------------------------
# Accuracy report
# ---------------------------------------------------------------------------

def print_accuracy_report(days: int = 90) -> None:
    """Print a summary of Claude's prediction accuracy over the last N days."""
    conn = _connect()
    cur = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        cur.execute(
            """SELECT o.*, c.bull_probability, c.signal_agreement_score
               FROM thesis_outcomes o
               JOIN thesis_cache c ON o.thesis_id = c.id
               WHERE o.thesis_date >= %s
               ORDER BY o.thesis_date DESC""",
            (cutoff,),
        )
        rows = cur.fetchall()
    except Exception:
        print("No thesis_outcomes table yet — run thesis_checker.py first.")
        conn.close()
        return
    conn.close()

    if not rows:
        print(f"No thesis outcomes in the last {days} days.")
        return

    total      = len(rows)
    resolved   = [r for r in rows if r["outcome"] not in ("OPEN", None)]
    correct    = [r for r in rows if r["claude_correct"] == 1]
    wrong      = [r for r in rows if r["claude_correct"] == 0]
    hit_t1     = [r for r in rows if r["outcome"] in ("HIT_TARGET1", "HIT_TARGET2")]
    hit_stop   = [r for r in rows if r["outcome"] == "HIT_STOP"]
    traded     = [r for r in rows if r["was_traded"] == 1]

    def avg(vals):
        v = [x for x in vals if x is not None]
        return sum(v) / len(v) if v else None

    returns_30d   = [r["return_30d"]   for r in rows if r["return_30d"]   is not None]
    vs_t1         = [r["vs_target_1_pct"] for r in rows if r["vs_target_1_pct"] is not None]
    days_to_t1    = [r["days_to_target_1"] for r in rows if r["days_to_target_1"] is not None]

    print()
    print("=" * 62)
    print(f"  CLAUDE THESIS ACCURACY REPORT — last {days} days")
    print("=" * 62)
    print(f"  Total theses       : {total}")
    print(f"  Resolved           : {len(resolved)}  /  Open: {total - len(resolved)}")
    print(f"  Traded             : {len(traded)}")
    print()
    print(f"  Direction correct  : {len(correct)} / {len(correct)+len(wrong)}"
          + (f"  ({len(correct)/(len(correct)+len(wrong))*100:.0f}%)" if (len(correct)+len(wrong)) > 0 else ""))
    print(f"  Hit target 1+      : {len(hit_t1)}")
    print(f"  Hit stop           : {len(hit_stop)}")
    print()
    if returns_30d:
        print(f"  Avg 30d return     : {avg(returns_30d):+.1f}%")
    if vs_t1:
        print(f"  Avg vs target_1    : {avg(vs_t1):+.1f}%  "
              f"(negative = price fell short of bull target)")
    if days_to_t1:
        print(f"  Avg days to t1     : {avg(days_to_t1):.1f} trading days")

    print()
    print(f"  {'TICKER':<6}  {'DATE':<10}  {'DIR':<6}  {'CONV':>4}  "
          f"{'OUTCOME':<12}  {'30d':>7}  {'vs T1':>7}  {'OK':>3}  TRADED")
    print("  " + "-" * 58)
    for r in rows:
        ret30  = f"{r['return_30d']:+.1f}%" if r["return_30d"] is not None else "  n/a"
        vt1    = f"{r['vs_target_1_pct']:+.1f}%" if r["vs_target_1_pct"] is not None else "  n/a"
        ok     = {1: "✓", 0: "✗", None: "—"}.get(r["claude_correct"], "—")
        traded = "Y" if r["was_traded"] else "N"
        print(
            f"  {r['ticker']:<6}  {r['thesis_date']:<10}  {(r['direction'] or '?'):<6}  "
            f"{(r['conviction'] or 0):>4}  {(r['outcome'] or 'OPEN'):<12}  "
            f"{ret30:>7}  {vt1:>7}  {ok:>3}  {traded}"
        )
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Thesis Checker — tracks Claude prediction outcomes"
    )
    parser.add_argument("--report",  action="store_true", help="Print accuracy summary and exit")
    parser.add_argument("--verbose", action="store_true", help="Show per-ticker update detail")
    parser.add_argument("--days",    type=int, default=90, help="Days to cover in --report (default 90)")
    args = parser.parse_args()

    if args.report:
        print_accuracy_report(days=args.days)
        return

    run_checker(verbose=args.verbose)


if __name__ == "__main__":
    main()
