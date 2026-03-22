#!/usr/bin/env python3
"""
================================================================================
PAPER TRADING TRACKER v1.0
================================================================================
Tracks your weekly signal engine output against real market performance.
Logs every signal, tracks hypothetical P&L, and builds a performance record
you can review before committing real capital.

WORKFLOW:
    1. Every Sunday evening: run signal_engine.py (generates signals)
    2. Then run: python3 paper_trader.py --record
       (snapshots current signals and prices into the trade log)
    3. Anytime: python3 paper_trader.py --report
       (shows cumulative paper trading performance)

WHAT THIS TRACKS:
    - Equity module: Top-ranked stocks, position sizes, weekly returns
    - Crypto module: BTC-only 200-day MA signal (replaces old trend module)
    - Combined portfolio P&L vs SPY benchmark
    - Running Sharpe, drawdown, hit rate

DATABASE: SQLite (paper_trades.db) — zero setup, persists across sessions.

USAGE:
    python3 paper_trader.py --record          # Log this week's signals
    python3 paper_trader.py --report          # Full performance report
    python3 paper_trader.py --report --weeks 8  # Last 8 weeks only
    python3 paper_trader.py --positions       # Show current holdings
    python3 paper_trader.py --reset           # Wipe all history (careful!)

IMPORTANT: This is NOT investment advice. Paper trading only.
================================================================================
"""

import argparse
import json
import os
import sqlite3
import sys
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from utils.db import get_connection

warnings.filterwarnings("ignore")

try:
    from config import (
        PORTFOLIO_NAV, EQUITY_ALLOCATION, CRYPTO_ALLOCATION, CASH_BUFFER,
        EQUITY_WATCHLIST, CUSTOM_WATCHLIST, RISK_PARAMS,
        TRANSACTION_COST_BPS,
    )
except ImportError:
    print("ERROR: config.py not found.")
    sys.exit(1)

# ─── Constants ────────────────────────────────────────────────────────────────
DB_PATH = "paper_trades.db"
TRADING_DAYS_YEAR = 252
ANNUALIZE = np.sqrt(TRADING_DAYS_YEAR)
BTC_TICKER = "BTC-USD"
SPY_TICKER = "SPY"
BTC_MA_PERIOD = 200  # 200-day moving average for BTC signal


# ==============================================================================
# SECTION 1: DATABASE
# ==============================================================================

def init_db():
    """Initialize SQLite database with required tables."""
    conn = get_connection(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            portfolio_nav REAL NOT NULL,
            equity_allocation REAL,
            crypto_allocation REAL,
            cash_allocation REAL,
            spy_price REAL,
            btc_price REAL,
            btc_ma200 REAL,
            btc_signal TEXT,
            notes TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS equity_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            rank INTEGER,
            composite_z REAL,
            weight_pct REAL,
            position_eur REAL,
            entry_price REAL,
            transaction_cost_eur REAL DEFAULT 0,
            FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
        )
    """)
    # Migrate: add transaction_cost_eur column if it doesn't exist yet
    try:
        c.execute("ALTER TABLE equity_positions ADD COLUMN transaction_cost_eur REAL DEFAULT 0")
    except Exception:
        pass  # Column already exists

    c.execute("""
        CREATE TABLE IF NOT EXISTS weekly_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            week_ending TEXT NOT NULL,
            portfolio_return REAL,
            benchmark_return REAL,
            equity_return REAL,
            crypto_return REAL,
            btc_return REAL,
            FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
        )
    """)

    conn.commit()
    return conn


# ==============================================================================
# SECTION 2: BTC 200-DAY MA SIGNAL (replaces broken crypto module)
# ==============================================================================

def compute_btc_signal() -> dict:
    """
    Simple BTC signal: price > 200-day MA = LONG, else CASH.

    This replaces the multi-asset crypto trend module which had a
    negative Sharpe of -0.20. Binary trend-following on BTC alone
    is more robust because:
    1. No altcoin whipsaw (altcoins trend-follow poorly)
    2. Lower turnover (signal changes ~4-6 times per year)
    3. BTC has the deepest liquidity and lowest spread
    """
    print("  Fetching BTC data for 200-day MA signal...")
    btc = yf.download(BTC_TICKER, period="2y", auto_adjust=True, progress=False)

    if btc.empty or len(btc) < BTC_MA_PERIOD:
        return {"signal": "NO_DATA", "price": 0, "ma200": 0, "pct_above": 0}

    close = btc["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    current_price = float(close.iloc[-1])
    ma200 = float(close.rolling(BTC_MA_PERIOD).mean().iloc[-1])
    pct_above = (current_price / ma200 - 1) * 100

    if current_price > ma200:
        signal = "LONG"
    else:
        signal = "CASH"

    return {
        "signal": signal,
        "price": current_price,
        "ma200": ma200,
        "pct_above": round(pct_above, 2),
    }


# ==============================================================================
# SECTION 3: RECORD SNAPSHOT
# ==============================================================================

def load_latest_signals() -> pd.DataFrame:
    """Load the most recent equity signals CSV from signals_output/."""
    output_dir = "./signals_output"
    if not os.path.exists(output_dir):
        print("  [ERROR] No signals_output/ directory. Run signal_engine.py first.")
        return pd.DataFrame()

    files = [f for f in os.listdir(output_dir)
             if f.startswith("equity_signals_") and f.endswith(".csv")]

    if not files:
        print("  [ERROR] No equity signal files found. Run signal_engine.py first.")
        return pd.DataFrame()

    latest = sorted(files)[-1]
    print(f"  Loading signals from: {latest}")
    df = pd.read_csv(os.path.join(output_dir, latest), index_col=0)
    return df


def load_latest_positions() -> pd.DataFrame:
    """Load the most recent equity positions CSV."""
    output_dir = "./signals_output"
    files = [f for f in os.listdir(output_dir)
             if f.startswith("equity_positions_") and f.endswith(".csv")]

    if not files:
        return pd.DataFrame()

    latest = sorted(files)[-1]
    return pd.read_csv(os.path.join(output_dir, latest), index_col=0)


def fetch_current_prices(tickers: list) -> dict:
    """Fetch current prices for a list of tickers."""
    prices = {}
    data = yf.download(tickers, period="5d", auto_adjust=True, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        close = data["Close"]
    else:
        close = data[["Close"]].rename(columns={"Close": tickers[0]})

    for t in tickers:
        if t in close.columns and not close[t].dropna().empty:
            prices[t] = float(close[t].dropna().iloc[-1])
    return prices


def record_snapshot(conn: sqlite3.Connection):
    """Record current signals, positions, and prices as a snapshot."""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()

    print(f"\n{'─' * 60}")
    print(f"  📸 RECORDING PAPER TRADE SNAPSHOT — {today}")
    print(f"{'─' * 60}")

    # Check if already recorded today
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM snapshots WHERE date = ?", (today,))
    if c.fetchone()[0] > 0:
        print(f"  [WARN] Snapshot already exists for {today}.")
        print(f"  To re-record, first run: python3 paper_trader.py --delete-today")
        return

    # Load equity signals and positions
    signals = load_latest_signals()
    positions = load_latest_positions()

    if signals.empty:
        print("  [ERROR] Cannot record without signals. Run signal_engine.py first.")
        return

    # BTC signal
    btc_data = compute_btc_signal()

    # Fetch benchmark + BTC prices
    spy_data = yf.download(SPY_TICKER, period="5d", auto_adjust=True, progress=False)
    if not spy_data.empty:
        close = spy_data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        spy_price = float(close.iloc[-1])
    else:
        spy_price = 0

    # Compute allocations based on signals
    equity_alloc = EQUITY_ALLOCATION
    if btc_data["signal"] == "LONG":
        crypto_alloc = CRYPTO_ALLOCATION
    else:
        crypto_alloc = 0.0  # Cash when below 200MA
    cash_alloc = 1.0 - equity_alloc - crypto_alloc

    # Insert snapshot
    c.execute("""
        INSERT INTO snapshots (date, created_at, portfolio_nav, equity_allocation,
                               crypto_allocation, cash_allocation, spy_price,
                               btc_price, btc_ma200, btc_signal)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (today, now, PORTFOLIO_NAV, equity_alloc, crypto_alloc, cash_alloc,
          spy_price, btc_data["price"], btc_data["ma200"], btc_data["signal"]))

    snapshot_id = c.lastrowid

    # Insert equity positions
    top_n = min(RISK_PARAMS["max_equity_positions"], len(signals))
    top_signals = signals.head(top_n)

    # Fetch prices for top positions
    tickers = top_signals.index.tolist()
    current_prices = fetch_current_prices(tickers)

    for ticker in tickers:
        row = top_signals.loc[ticker]
        rank = int(row.get("rank", 0))
        composite = float(row.get("composite_z", 0))

        # Position size from positions file or equal weight
        if not positions.empty and ticker in positions.index:
            weight = float(positions.loc[ticker, "weight_pct"])
            pos_eur = float(positions.loc[ticker, "position_eur"])
        else:
            weight = 100.0 / top_n
            pos_eur = PORTFOLIO_NAV * equity_alloc * (weight / 100)

        price = current_prices.get(ticker, 0)

        # Round-trip transaction cost: position enters and exits, so 2x one-way cost
        tc_eur = pos_eur * (TRANSACTION_COST_BPS / 10_000) * 2

        c.execute("""
            INSERT INTO equity_positions (snapshot_id, ticker, rank, composite_z,
                                          weight_pct, position_eur, entry_price,
                                          transaction_cost_eur)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (snapshot_id, ticker, rank, composite, weight, pos_eur, price, tc_eur))

    conn.commit()

    # Print summary
    print(f"\n  ✅ Snapshot recorded successfully!")
    print(f"\n  EQUITY ({top_n} positions):")
    for ticker in tickers[:10]:
        row = top_signals.loc[ticker]
        price = current_prices.get(ticker, 0)
        print(f"    {ticker:<10} Z={row.get('composite_z', 0):>7.3f}  ${price:>10,.2f}")

    print(f"\n  CRYPTO (BTC 200-day MA):")
    print(f"    BTC Price:  ${btc_data['price']:>12,.2f}")
    print(f"    200-day MA: ${btc_data['ma200']:>12,.2f}")
    print(f"    Signal:     {btc_data['signal']} ({btc_data['pct_above']:+.1f}% vs MA)")

    print(f"\n  ALLOCATION:")
    print(f"    Equity: {equity_alloc:.0%} | Crypto: {crypto_alloc:.0%} | Cash: {cash_alloc:.0%}")


# ==============================================================================
# SECTION 4: COMPUTE WEEKLY RETURNS
# ==============================================================================

def compute_returns(conn: sqlite3.Connection):
    """Compute returns between consecutive snapshots."""
    c = conn.cursor()
    c.execute("""
        SELECT id, date, spy_price, btc_price, btc_signal,
               equity_allocation, crypto_allocation
        FROM snapshots ORDER BY date ASC
    """)
    snapshots = c.fetchall()

    if len(snapshots) < 2:
        return

    for i in range(1, len(snapshots)):
        prev = snapshots[i - 1]
        curr = snapshots[i]
        prev_id, prev_date = prev[0], prev[1]
        curr_id, curr_date = curr[0], curr[1]

        # Check if return already computed
        c.execute("SELECT COUNT(*) FROM weekly_returns WHERE snapshot_id = ?", (curr_id,))
        if c.fetchone()[0] > 0:
            continue

        # Benchmark return (SPY)
        spy_ret = (curr[2] / prev[2] - 1) if prev[2] > 0 else 0

        # BTC return
        btc_ret = (curr[3] / prev[3] - 1) if prev[3] > 0 else 0

        # Crypto portfolio return (BTC if LONG, 0 if CASH)
        crypto_port_ret = btc_ret if prev[4] == "LONG" else 0

        # Equity return — compute from position prices
        c.execute("""
            SELECT ticker, weight_pct, entry_price FROM equity_positions
            WHERE snapshot_id = ?
        """, (prev_id,))
        prev_positions = c.fetchall()

        equity_ret = 0
        if prev_positions:
            tickers = [p[0] for p in prev_positions]
            current_prices = fetch_current_prices(tickers)

            for ticker, weight, entry_price in prev_positions:
                if entry_price > 0 and ticker in current_prices:
                    ret = current_prices[ticker] / entry_price - 1
                    equity_ret += (weight / 100) * ret

        # Combined portfolio return
        eq_alloc = prev[5] or EQUITY_ALLOCATION
        cr_alloc = prev[6] or 0
        cash_alloc = 1.0 - eq_alloc - cr_alloc

        port_ret = eq_alloc * equity_ret + cr_alloc * crypto_port_ret

        c.execute("""
            INSERT INTO weekly_returns (snapshot_id, week_ending, portfolio_return,
                                        benchmark_return, equity_return,
                                        crypto_return, btc_return)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (curr_id, curr_date, port_ret, spy_ret, equity_ret,
              crypto_port_ret, btc_ret))

    conn.commit()


# ==============================================================================
# SECTION 5: REPORTING
# ==============================================================================

def show_report(conn: sqlite3.Connection, weeks: int = None):
    """Display paper trading performance report."""
    compute_returns(conn)

    c = conn.cursor()

    # Get snapshots
    c.execute("SELECT COUNT(*) FROM snapshots")
    n_snapshots = c.fetchone()[0]

    if n_snapshots == 0:
        print("\n  No snapshots recorded yet.")
        print("  Run: python3 paper_trader.py --record")
        return

    if n_snapshots < 2:
        print(f"\n  Only {n_snapshots} snapshot(s) recorded.")
        print("  Need at least 2 weeks of data for a performance report.")
        print("  Run signal_engine.py + paper_trader.py --record again next Sunday.")
        return

    # Fetch returns
    query = "SELECT * FROM weekly_returns ORDER BY week_ending ASC"
    returns = pd.read_sql(query, conn)

    if weeks:
        returns = returns.tail(weeks)

    if returns.empty:
        print("\n  No return data available yet.")
        return

    print(f"\n{'█' * 60}")
    print(f"  PAPER TRADING REPORT")
    print(f"  Snapshots: {n_snapshots} | Weeks with returns: {len(returns)}")
    print(f"{'█' * 60}")

    # Cumulative returns
    cum_port = (1 + returns["portfolio_return"]).cumprod()
    cum_bench = (1 + returns["benchmark_return"]).cumprod()

    total_port = cum_port.iloc[-1] - 1
    total_bench = cum_bench.iloc[-1] - 1

    # Annualized (if enough data)
    n_weeks = len(returns)
    n_years = n_weeks / 52

    if n_years > 0.1:
        cagr_port = (1 + total_port) ** (1 / n_years) - 1
        cagr_bench = (1 + total_bench) ** (1 / n_years) - 1
    else:
        cagr_port = total_port
        cagr_bench = total_bench

    # Vol & Sharpe
    vol_port = returns["portfolio_return"].std() * np.sqrt(52)
    sharpe = cagr_port / vol_port if vol_port > 0 else 0

    # Max drawdown
    cummax = cum_port.cummax()
    drawdown = (cum_port - cummax) / cummax
    max_dd = drawdown.min()

    # Hit rate
    hit_rate = (returns["portfolio_return"] > 0).mean()

    print(f"\n  CUMULATIVE PERFORMANCE:")
    print(f"  {'Portfolio Total Return:':<30} {total_port:>10.2%}")
    print(f"  {'Benchmark (SPY) Return:':<30} {total_bench:>10.2%}")
    print(f"  {'Excess Return:':<30} {total_port - total_bench:>10.2%}")

    if n_years > 0.25:
        print(f"\n  ANNUALIZED:")
        print(f"  {'Portfolio CAGR:':<30} {cagr_port:>10.2%}")
        print(f"  {'Benchmark CAGR:':<30} {cagr_bench:>10.2%}")
        print(f"  {'Sharpe Ratio:':<30} {sharpe:>10.3f}")

    print(f"\n  RISK:")
    print(f"  {'Max Drawdown:':<30} {max_dd:>10.2%}")
    print(f"  {'Weekly Hit Rate:':<30} {hit_rate:>10.1%}")

    # Weekly detail
    print(f"\n  WEEKLY RETURNS:")
    print(f"  {'Week':>12}{'Portfolio':>12}{'Benchmark':>12}{'Excess':>12}{'Cum Port':>12}")
    print(f"  {'─' * 60}")

    for i, row in returns.iterrows():
        cum = cum_port.iloc[returns.index.get_loc(i)]
        excess = row["portfolio_return"] - row["benchmark_return"]
        marker = "✓" if excess > 0 else "✗"
        print(f"  {row['week_ending']:>12}"
              f"{row['portfolio_return']:>11.2%}"
              f"{row['benchmark_return']:>11.2%}"
              f"{excess:>11.2%}"
              f"{cum - 1:>11.2%}  {marker}")

    # BTC signal history
    c.execute("SELECT date, btc_signal, btc_price, btc_ma200 FROM snapshots ORDER BY date ASC")
    btc_history = c.fetchall()

    print(f"\n  BTC 200-DAY MA SIGNAL HISTORY:")
    print(f"  {'Date':>12}{'Signal':>10}{'Price':>14}{'MA200':>14}{'Gap':>10}")
    print(f"  {'─' * 60}")
    for date, signal, price, ma200 in btc_history:
        gap = ((price / ma200) - 1) * 100 if ma200 > 0 else 0
        emoji = "🟢" if signal == "LONG" else "⚪"
        print(f"  {date:>12}{emoji} {signal:>7}"
              f"  ${price:>11,.2f}"
              f"  ${ma200:>11,.2f}"
              f"{gap:>9.1f}%")

    # Transaction costs YTD
    c.execute("""
        SELECT COALESCE(SUM(ep.transaction_cost_eur), 0)
        FROM equity_positions ep
        JOIN snapshots s ON ep.snapshot_id = s.id
        WHERE substr(s.date, 1, 4) = ?
    """, (str(datetime.now().year),))
    ytd_tc_eur = float(c.fetchone()[0] or 0)
    equity_deployed = PORTFOLIO_NAV * EQUITY_ALLOCATION
    tc_bps_drag = (ytd_tc_eur / equity_deployed * 10_000) if equity_deployed > 0 else 0

    print(f"\n  TRANSACTION COSTS:")
    print(f"  Transaction costs YTD: €{ytd_tc_eur:.2f} ({tc_bps_drag:.1f} bps drag on equity)")

    # Verdict
    print(f"\n  PAPER TRADING VERDICT:")
    if n_weeks >= 12:
        if sharpe >= 0.8:
            print(f"  🟢 Sharpe {sharpe:.2f} — Signal holds up in paper trading.")
            print(f"     Consider graduating to live trading with small size.")
        elif sharpe >= 0.3:
            print(f"  🟡 Sharpe {sharpe:.2f} — Inconclusive. Continue paper trading.")
        else:
            print(f"  🔴 Sharpe {sharpe:.2f} — Signal not performing. Do NOT go live.")
    else:
        weeks_needed = 12 - n_weeks
        print(f"  ⏳ Need {weeks_needed} more weeks of data before assessment.")
        print(f"     Continue recording every Sunday.")


def show_positions(conn: sqlite3.Connection):
    """Show current (latest) positions."""
    c = conn.cursor()
    c.execute("SELECT id, date FROM snapshots ORDER BY date DESC LIMIT 1")
    row = c.fetchone()

    if not row:
        print("\n  No snapshots recorded. Run: python3 paper_trader.py --record")
        return

    snapshot_id, date = row

    print(f"\n  CURRENT POSITIONS (as of {date}):")

    c.execute("""
        SELECT ticker, rank, composite_z, weight_pct, position_eur, entry_price
        FROM equity_positions WHERE snapshot_id = ?
        ORDER BY rank ASC
    """, (snapshot_id,))

    positions = c.fetchall()
    if positions:
        print(f"\n  {'Rank':<6}{'Ticker':<10}{'Z-Score':>10}{'Weight':>10}{'EUR':>12}{'Entry$':>12}")
        print(f"  {'─' * 60}")
        total_eur = 0
        for ticker, rank, z, weight, eur, price in positions:
            print(f"  {rank:<6}{ticker:<10}{z:>10.3f}{weight:>9.1f}%{eur:>11,.0f}  ${price:>10,.2f}")
            total_eur += eur
        print(f"\n  Total equity exposure: €{total_eur:,.0f}")

    # BTC
    c.execute("SELECT btc_signal, btc_price, btc_ma200 FROM snapshots WHERE id = ?",
              (snapshot_id,))
    btc = c.fetchone()
    if btc:
        signal, price, ma200 = btc
        gap = ((price / ma200) - 1) * 100 if ma200 > 0 else 0
        emoji = "🟢" if signal == "LONG" else "⚪"
        print(f"\n  BTC: {emoji} {signal} (${price:,.2f}, MA200=${ma200:,.2f}, {gap:+.1f}%)")


def reset_db(conn: sqlite3.Connection):
    """Reset all paper trading history."""
    c = conn.cursor()
    c.execute("DELETE FROM weekly_returns")
    c.execute("DELETE FROM equity_positions")
    c.execute("DELETE FROM snapshots")
    conn.commit()
    print("  ⚠️  All paper trading history has been deleted.")


# ==============================================================================
# SECTION 6: MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Paper Trading Tracker v1.0")
    parser.add_argument("--record", action="store_true", help="Record today's signals")
    parser.add_argument("--report", action="store_true", help="Show performance report")
    parser.add_argument("--positions", action="store_true", help="Show current positions")
    parser.add_argument("--reset", action="store_true", help="Delete all history")
    parser.add_argument("--weeks", type=int, help="Limit report to N weeks")
    args = parser.parse_args()

    conn = init_db()

    if args.reset:
        confirm = input("  Are you sure you want to delete all history? (yes/no): ")
        if confirm.lower() == "yes":
            reset_db(conn)
        else:
            print("  Cancelled.")
    elif args.record:
        record_snapshot(conn)
    elif args.report:
        show_report(conn, weeks=args.weeks)
    elif args.positions:
        show_positions(conn)
    else:
        # Default: show current status
        show_positions(conn)
        print("\n  USAGE:")
        print("  python3 paper_trader.py --record     # Log this week's signals")
        print("  python3 paper_trader.py --report     # Performance report")
        print("  python3 paper_trader.py --positions  # Current holdings")

    conn.close()


if __name__ == "__main__":
    main()
