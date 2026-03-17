#!/usr/bin/env python3
"""
================================================================================
TRADE JOURNAL v1.0
================================================================================
Logs your actual trades, tracks P&L, and generates buy/sell price zones
based on technical levels and signal strength.

FEATURES:
    1. ACTION ZONES: For each watchlist stock, computes buy range, sell
       targets, and stop-loss levels based on support/resistance + ATR
    2. TRADE LOGGING: Record when you buy or sell, at what price
    3. P&L TRACKING: Automatically checks current prices vs your entries
    4. RETURN ANALYSIS: Forward return tracking at 1/2/3/4 weeks post-entry
    5. WEEKLY REPORT: Generates the action summary for your Sunday report

USAGE:
    python3 trade_journal.py --zones                 # Show buy/sell zones
    python3 trade_journal.py --buy GME 23.50 500     # Log: bought GME at €23.50, €500
    python3 trade_journal.py --sell GME 28.00         # Log: sold GME at €28.00
    python3 trade_journal.py --status                 # Open positions + P&L
    python3 trade_journal.py --history                # All trades with outcomes
    python3 trade_journal.py --report                 # Full weekly report section

DATABASE: SQLite (trade_journal.db)

IMPORTANT: This is NOT investment advice. All zones are informational.
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

from fx_rates import (
    get_eur_rate, convert_to_eur, get_all_rates,
    get_ticker_currency, convert_ticker_price_to_eur,
)

warnings.filterwarnings("ignore")

try:
    from config import PORTFOLIO_NAV, RISK_PARAMS, OUTPUT_DIR
except ImportError:
    PORTFOLIO_NAV = 50000
    RISK_PARAMS = {"max_position_equity_pct": 0.08}
    OUTPUT_DIR = "./signals_output"

DB_PATH = "trade_journal.db"
REPORTS_DIR = "./weekly_reports"


# ==============================================================================
# SECTION 1: DATABASE
# ==============================================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            action TEXT NOT NULL,
            price REAL NOT NULL,
            size_eur REAL,
            shares REAL,
            date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            signal_composite REAL,
            signal_type TEXT,
            buy_zone_low REAL,
            buy_zone_high REAL,
            target_1 REAL,
            target_2 REAL,
            stop_loss REAL,
            notes TEXT,
            linked_buy_id INTEGER,
            status TEXT DEFAULT 'open'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS trade_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            check_date TEXT NOT NULL,
            days_held INTEGER,
            current_price REAL,
            return_pct REAL,
            unrealized_pnl_eur REAL,
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        )
    """)

    conn.commit()
    return conn


# ==============================================================================
# SECTION 2: PRICE ZONES — Buy Range, Targets, Stop Loss
# ==============================================================================

def compute_action_zones(ticker: str) -> dict:
    """
    Compute buy zone, sell targets, and stop loss for a stock.

    Buy Zone: Based on recent support levels + ATR cushion
    Target 1: Next resistance level (conservative)
    Target 2: Measured move target (aggressive)
    Stop Loss: Below key support — ATR-based to avoid noise

    Returns dict with all levels and reasoning.
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="6mo")
        if hist.empty or len(hist) < 50:
            return None

        close = hist["Close"]
        high = hist["High"]
        low = hist["Low"]
        volume = hist["Volume"]
        current = float(close.iloc[-1])

        # ATR (14-day) for volatility-based levels
        tr = pd.concat([
            high - low,
            abs(high - close.shift(1)),
            abs(low - close.shift(1))
        ], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        atr_pct = atr / current

        # ── Support Levels ──
        # Recent swing lows (last 60 days)
        lows_20d = float(low.iloc[-20:].min())
        lows_40d = float(low.iloc[-40:].min())

        # Volume-weighted price (VWAP proxy) as support
        vwap_20d = float((close.iloc[-20:] * volume.iloc[-20:]).sum() / volume.iloc[-20:].sum())

        # Key support = highest of recent lows (strongest floor)
        key_support = max(lows_20d, vwap_20d * 0.98)

        # ── Resistance Levels ──
        highs_20d = float(high.iloc[-20:].max())
        highs_60d = float(high.iloc[-60:].max())

        # EMAs as dynamic resistance/support
        ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])

        # ── Buy Zone ──
        # Ideal entry: between key support and current price minus 0.5 ATR
        buy_zone_low = round(key_support, 2)
        buy_zone_high = round(current - 0.3 * atr, 2)

        # If current price is already at support, buy zone = current ± 0.5 ATR
        if buy_zone_high <= buy_zone_low:
            buy_zone_low = round(current - 0.5 * atr, 2)
            buy_zone_high = round(current + 0.2 * atr, 2)

        # ── Targets ──
        # Target 1: Next resistance (conservative — recent 20d high)
        target_1 = round(highs_20d, 2)

        # Target 2: Measured move (aggressive — 60d high or 2x ATR from entry)
        target_2 = round(max(highs_60d, current + 3 * atr), 2)

        # Ensure targets are above current
        if target_1 <= current * 1.02:
            target_1 = round(current * 1.08, 2)  # Min 8% target
        if target_2 <= target_1:
            target_2 = round(target_1 * 1.15, 2)

        # ── Stop Loss ──
        # Below key support by 1 ATR (room for noise)
        stop_loss = round(key_support - atr, 2)

        # Risk/Reward ratio
        risk = current - stop_loss
        reward_1 = target_1 - current
        reward_2 = target_2 - current
        rr_1 = reward_1 / risk if risk > 0 else 0
        rr_2 = reward_2 / risk if risk > 0 else 0

        # Position sizing (max 2% of NAV for catalyst, risk-based)
        max_eur = PORTFOLIO_NAV * 0.02  # 2% max for speculative
        risk_per_share = current - stop_loss
        if risk_per_share > 0:
            shares_by_risk = max_eur * 0.02 / risk_per_share  # Risk 2% of position
            position_eur = min(shares_by_risk * current, max_eur)
        else:
            position_eur = max_eur

        # RSI for timing
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(com=13, min_periods=14).mean()
        avg_loss = loss.ewm(com=13, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = float((100 - (100 / (1 + rs))).iloc[-1])

        # Timing assessment
        if rsi > 70:
            timing = "WAIT — overbought (RSI {:.0f}), let it pull back to buy zone".format(rsi)
        elif rsi > 60:
            timing = "CAUTION — getting warm (RSI {:.0f}), watch for dip".format(rsi)
        elif 40 <= rsi <= 60:
            timing = "GOOD ENTRY WINDOW — RSI {:.0f} in sweet spot".format(rsi)
        elif rsi < 30:
            timing = "OVERSOLD — RSI {:.0f}, potential bounce but confirm catalyst first".format(rsi)
        else:
            timing = "ACCEPTABLE — RSI {:.0f}, approaching sweet spot".format(rsi)

        return {
            "ticker": ticker,
            "current_price": current,
            "atr": atr,
            "atr_pct": atr_pct,
            "buy_zone_low": buy_zone_low,
            "buy_zone_high": buy_zone_high,
            "target_1": target_1,
            "target_2": target_2,
            "stop_loss": stop_loss,
            "risk_reward_t1": rr_1,
            "risk_reward_t2": rr_2,
            "suggested_size_eur": round(position_eur, 0),
            "key_support": key_support,
            "resistance_20d": highs_20d,
            "resistance_60d": highs_60d,
            "ema21": ema21,
            "ema50": ema50,
            "rsi": rsi,
            "timing": timing,
        }

    except Exception as e:
        return None


def show_zones(tickers: list = None):
    """Display buy/sell zones for watchlist stocks."""
    if not tickers:
        watchlist_path = "./watchlist.txt"
        if os.path.exists(watchlist_path):
            with open(watchlist_path) as f:
                tickers = [l.strip().upper() for l in f
                           if l.strip() and not l.startswith("#")]
        else:
            print("  No watchlist.txt found. Pass --ticker or create watchlist.txt")
            return

    print(f"\n{'█' * 60}")
    print(f"  ACTION ZONES — {datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'█' * 60}")

    # Fetch FX rates
    print(f"  Loading exchange rates...")
    fx_rates = get_all_rates(verbose=True)

    all_zones = []

    for ticker in tickers:
        if ticker.endswith("-USD"):
            continue

        zones = compute_action_zones(ticker)
        if not zones:
            print(f"\n  {ticker}: No data available")
            continue

        all_zones.append(zones)
        z = zones

        # Convert to EUR
        ticker_ccy = get_ticker_currency(ticker)
        fx_rate = get_eur_rate(ticker_ccy) if ticker_ccy != "EUR" else 1.0
        fx_div = fx_rate if fx_rate > 0 else 1.0

        price = z['current_price'] / fx_div
        atr = z['atr'] / fx_div
        stop = z['stop_loss'] / fx_div
        buy_lo = z['buy_zone_low'] / fx_div
        buy_hi = z['buy_zone_high'] / fx_div
        t1 = z['target_1'] / fx_div
        t2 = z['target_2'] / fx_div

        print(f"\n  {'─' * 56}")
        ccy_note = "" if ticker_ccy == "EUR" else f"  (from {ticker_ccy}, rate {fx_rate:.4f})"
        print(f"  {ticker}  |  Current: €{price:.2f}  |  ATR: €{atr:.2f} ({z['atr_pct']:.1%}){ccy_note}")
        print(f"  {'─' * 56}")
        print(f"  📉 STOP LOSS:    €{stop:.2f}  ({(stop/price-1)*100:+.1f}%)")
        print(f"  🟢 BUY ZONE:     €{buy_lo:.2f} — €{buy_hi:.2f}")
        print(f"  ⚡ CURRENT:      €{price:.2f}")
        print(f"  🎯 TARGET 1:     €{t1:.2f}  ({(t1/price-1)*100:+.1f}%)  R:R {z['risk_reward_t1']:.1f}")
        print(f"  🎯 TARGET 2:     €{t2:.2f}  ({(t2/price-1)*100:+.1f}%)  R:R {z['risk_reward_t2']:.1f}")
        print(f"  💰 SIZE:         €{z['suggested_size_eur']:.0f}  (2% of NAV)")
        print(f"  ⏱️  TIMING:       {z['timing']}")

        if buy_lo <= price <= buy_hi:
            print(f"  ➡️  ACTION:       IN BUY ZONE — consider entry if catalyst confirms")
        elif price < buy_lo:
            print(f"  ➡️  ACTION:       BELOW BUY ZONE — wait for stabilization")
        elif price > t1:
            print(f"  ➡️  ACTION:       ABOVE TARGET 1 — take partial profits if holding")
        else:
            print(f"  ➡️  ACTION:       BETWEEN zones — wait for pullback to buy zone")

    return all_zones


# ==============================================================================
# SECTION 3: TRADE LOGGING
# ==============================================================================

def log_buy(conn, ticker: str, price: float, size_eur: float, notes: str = ""):
    """Log a buy trade."""
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()
    shares = size_eur / price if price > 0 else 0

    # Get action zones for context
    zones = compute_action_zones(ticker)

    c.execute("""
        INSERT INTO trades (ticker, action, price, size_eur, shares, date,
                            created_at, buy_zone_low, buy_zone_high,
                            target_1, target_2, stop_loss, notes, status)
        VALUES (?, 'BUY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
    """, (
        ticker.upper(), price, size_eur, shares, today, now,
        zones["buy_zone_low"] if zones else None,
        zones["buy_zone_high"] if zones else None,
        zones["target_1"] if zones else None,
        zones["target_2"] if zones else None,
        zones["stop_loss"] if zones else None,
        notes,
    ))
    conn.commit()

    trade_id = c.lastrowid
    print(f"\n  ✅ BUY logged: {ticker} @ €{price:.2f} | €{size_eur:.0f} | {shares:.2f} shares")
    if zones:
        print(f"     Stop: €{zones['stop_loss']:.2f} | T1: €{zones['target_1']:.2f} | T2: €{zones['target_2']:.2f}")
    print(f"     Trade ID: {trade_id}")
    return trade_id


def log_sell(conn, ticker: str, price: float, notes: str = ""):
    """Log a sell trade and close the open buy."""
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()

    # Find open buy for this ticker
    c.execute("""
        SELECT id, price, size_eur, shares, date FROM trades
        WHERE ticker = ? AND action = 'BUY' AND status = 'open'
        ORDER BY date DESC LIMIT 1
    """, (ticker.upper(),))
    buy = c.fetchone()

    if not buy:
        print(f"  [ERROR] No open BUY position found for {ticker}")
        return

    buy_id, buy_price, buy_size, buy_shares, buy_date = buy
    pnl_pct = (price / buy_price - 1)
    pnl_eur = buy_size * pnl_pct
    days_held = (datetime.strptime(today, "%Y-%m-%d") -
                 datetime.strptime(buy_date, "%Y-%m-%d")).days

    # Log sell
    c.execute("""
        INSERT INTO trades (ticker, action, price, size_eur, shares, date,
                            created_at, notes, linked_buy_id, status)
        VALUES (?, 'SELL', ?, ?, ?, ?, ?, ?, ?, 'closed')
    """, (ticker.upper(), price, buy_size + pnl_eur, buy_shares,
          today, now, notes, buy_id))

    # Close the buy
    c.execute("UPDATE trades SET status = 'closed' WHERE id = ?", (buy_id,))
    conn.commit()

    emoji = "🟢" if pnl_eur >= 0 else "🔴"
    print(f"\n  {emoji} SELL logged: {ticker} @ €{price:.2f}")
    print(f"     Entry: €{buy_price:.2f} → Exit: €{price:.2f}")
    print(f"     Return: {pnl_pct:+.1%} | P&L: €{pnl_eur:+.2f} | Held: {days_held} days")


# ==============================================================================
# SECTION 4: POSITION STATUS & P&L
# ==============================================================================

def show_status(conn):
    """Show all open positions consolidated by ticker with current P&L."""
    c = conn.cursor()
    c.execute("""
        SELECT ticker, price, size_eur, shares, date
        FROM trades WHERE action = 'BUY' AND status = 'open'
        ORDER BY date ASC
    """)
    raw_positions = c.fetchall()

    if not raw_positions:
        print("\n  No open positions.")
        return

    print(f"\n{'█' * 60}")
    print(f"  OPEN POSITIONS — {datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'█' * 60}")

    # Consolidate by ticker
    consolidated = {}
    for ticker, price, size, shares, date in raw_positions:
        if ticker not in consolidated:
            consolidated[ticker] = {
                "total_shares": 0,
                "total_cost": 0,
                "first_date": date,
                "last_date": date,
                "n_buys": 0,
            }
        consolidated[ticker]["total_shares"] += shares
        consolidated[ticker]["total_cost"] += size
        consolidated[ticker]["last_date"] = date
        consolidated[ticker]["n_buys"] += 1

    # Fetch FX rates
    print(f"  Loading exchange rates...")
    fx_rates = get_all_rates(verbose=True)

    # Fetch current prices and convert to EUR
    unique_tickers = list(consolidated.keys())
    current_prices = {}

    for t in unique_tickers:
        try:
            data = yf.download(t, period="2d", auto_adjust=True, progress=False)
            close = data["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            raw_price = float(close.iloc[-1])
            current_prices[t] = convert_ticker_price_to_eur(raw_price, t)
        except Exception:
            pass

    # Get action zones for stop/target levels
    zones_cache = {}
    for t in unique_tickers:
        zones_cache[t] = compute_action_zones(t)

    total_invested = 0
    total_value = 0
    total_pnl = 0

    # Header
    header = (f"  {'Ticker':<8}{'Shares':>9}{'Avg Cost':>11}{'Current':>11}"
              f"{'Value':>13}{'P&L €':>12}{'P&L %':>9}{'Days':>6}  Status")
    print(f"\n{header}")
    print(f"  {'─' * len(header)}")

    for ticker in unique_tickers:
        pos = consolidated[ticker]
        shares = pos["total_shares"]
        cost = pos["total_cost"]
        avg_cost = cost / shares if shares > 0 else 0
        current = current_prices.get(ticker, 0)
        value = shares * current
        pnl_eur = value - cost
        pnl_pct = (current / avg_cost - 1) if avg_cost > 0 and current > 0 else 0
        days = (datetime.now() - datetime.strptime(pos["first_date"], "%Y-%m-%d")).days

        total_invested += cost
        total_value += value
        total_pnl += pnl_eur

        # Status from zones (convert zones to EUR)
        zones = zones_cache.get(ticker)
        ticker_ccy = get_ticker_currency(ticker)
        fx_rate = get_eur_rate(ticker_ccy) if ticker_ccy != "EUR" else 1.0
        fx_div = fx_rate if fx_rate > 0 else 1.0

        if current > 0 and zones:
            stop_eur = zones["stop_loss"] / fx_div if zones["stop_loss"] else None
            t1_eur = zones["target_1"] / fx_div if zones["target_1"] else None
            t2_eur = zones["target_2"] / fx_div if zones["target_2"] else None

            if stop_eur and current <= stop_eur:
                status = "STOP HIT"
            elif t2_eur and current >= t2_eur:
                status = "T2 HIT"
            elif t1_eur and current >= t1_eur:
                status = "T1 HIT"
            else:
                status = "Holding"
        else:
            status = "Holding"
            stop_eur = t1_eur = t2_eur = None

        pnl_sign = "+" if pnl_eur >= 0 else ""
        pnl_pct_str = f"{pnl_pct:+.1%}"

        # Format shares — show decimals only for fractional
        shares_str = f"{shares:.2f}" if shares != int(shares) else f"{shares:.0f}"

        print(f"  {ticker:<8}"
              f"{shares_str:>9}"
              f"   €{avg_cost:>8.2f}"
              f"   €{current:>8.2f}"
              f"   €{value:>10,.2f}"
              f"   €{pnl_eur:>+10,.2f}"
              f"  {pnl_pct_str:>7}"
              f"  {days:>4}"
              f"  {status}")

        # Show zone levels in EUR
        if stop_eur and t1_eur and t2_eur:
            print(f"  {'':>8}{'':>9}"
                  f"   Stop: €{stop_eur:.2f}"
                  f"  |  T1: €{t1_eur:.2f} ({(t1_eur/current-1)*100:+.1f}%)"
                  f"  |  T2: €{t2_eur:.2f} ({(t2_eur/current-1)*100:+.1f}%)")

        # Log return checkpoint
        c.execute("""
            SELECT id FROM trades WHERE ticker = ? AND action = 'BUY' AND status = 'open'
            ORDER BY date ASC LIMIT 1
        """, (ticker,))
        first_buy = c.fetchone()
        if first_buy:
            c.execute("""
                INSERT OR IGNORE INTO trade_returns
                (trade_id, check_date, days_held, current_price, return_pct, unrealized_pnl_eur)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (first_buy[0], datetime.now().strftime("%Y-%m-%d"), days, current, pnl_pct, pnl_eur))

    conn.commit()

    # Compute realized P&L from closed trades
    c.execute("""
        SELECT COALESCE(SUM(size_eur), 0) FROM trades
        WHERE action = 'SELL'
    """)
    total_cash_in = c.fetchone()[0]

    c.execute("""
        SELECT COALESCE(SUM(size_eur), 0) FROM trades
        WHERE action = 'BUY'
    """)
    total_cash_out = c.fetchone()[0]

    net_deployed = total_cash_out - total_cash_in
    realized_pnl = total_invested - net_deployed  # cost basis - net deployed = realized gains funding positions

    # Portfolio totals
    total_pnl_pct = total_pnl / total_invested if total_invested > 0 else 0
    total_pnl_pct_str = f"{total_pnl_pct:+.1%}"

    print(f"\n  {'─' * 90}")
    print(f"  {'TOTAL':<8}{'':>9}{'':>11}{'':>11}"
          f"   €{total_value:>10,.2f}"
          f"   €{total_pnl:>+10,.2f}"
          f"  {total_pnl_pct_str:>7}")

    print(f"\n  Cost basis (open):    €{total_invested:,.2f}")
    print(f"  Net capital deployed: €{net_deployed:,.2f}")
    print(f"  Realized P&L:         €{realized_pnl:+,.2f}")
    print(f"  Current value:        €{total_value:,.2f}")
    print(f"  Unrealized P&L:       €{total_pnl:+,.2f} ({total_pnl_pct:+.1%})")
    print(f"  Total P&L (real+unr): €{realized_pnl + total_pnl:+,.2f}")
    print(f"  Open tickers:         {len(consolidated)}")

    # Concentration warning
    for ticker in unique_tickers:
        pos = consolidated[ticker]
        current = current_prices.get(ticker, 0)
        value = pos["total_shares"] * current
        pct_of_nav = value / PORTFOLIO_NAV * 100 if PORTFOLIO_NAV > 0 else 0
        if pct_of_nav > 10:
            print(f"\n  ⚠️  CONCENTRATION WARNING: {ticker} is {pct_of_nav:.1f}% of NAV (€{value:,.0f})")
            print(f"     Max recommended: 8% for core, 2% for catalyst positions")


def show_history(conn):
    """Show all closed trades with outcomes."""
    c = conn.cursor()
    c.execute("""
        SELECT b.ticker, b.price as entry, b.size_eur, b.date as buy_date,
               s.price as exit_price, s.date as sell_date
        FROM trades b
        JOIN trades s ON s.linked_buy_id = b.id
        WHERE b.action = 'BUY' AND s.action = 'SELL'
        ORDER BY b.date DESC
    """)
    trades = c.fetchall()

    if not trades:
        print("\n  No closed trades yet.")
        return

    print(f"\n{'█' * 60}")
    print(f"  TRADE HISTORY")
    print(f"{'█' * 60}")

    print(f"\n  {'Ticker':<8}{'Entry':>10}{'Exit':>10}{'P&L %':>9}{'P&L €':>10}{'Days':>6}{'Buy Date':>12}{'Sell Date':>12}")
    print(f"  {'─' * 78}")

    total_pnl = 0
    wins = 0
    losses = 0

    for t in trades:
        ticker, entry, size, buy_date, exit_price, sell_date = t
        pnl_pct = (exit_price / entry - 1)
        pnl_eur = size * pnl_pct
        days = (datetime.strptime(sell_date, "%Y-%m-%d") -
                datetime.strptime(buy_date, "%Y-%m-%d")).days

        total_pnl += pnl_eur
        if pnl_eur >= 0:
            wins += 1
        else:
            losses += 1

        emoji = "🟢" if pnl_eur >= 0 else "🔴"
        print(f"  {ticker:<8}"
              f"€{entry:>9.2f}"
              f"€{exit_price:>9.2f}"
              f"{emoji}{pnl_pct:>7.1%}"
              f"{'€':>2}{pnl_eur:>7.2f}"
              f"{days:>6}"
              f"  {buy_date}"
              f"  {sell_date}")

    total = wins + losses
    print(f"\n  Total trades: {total} | Wins: {wins} | Losses: {losses} | "
          f"Hit rate: {wins/max(total,1):.0%}")
    print(f"  Total realized P&L: €{total_pnl:+,.2f}")


# ==============================================================================
# SECTION 5: WEEKLY REPORT SECTION
# ==============================================================================

def generate_report_section(conn) -> str:
    """Generate the action zones + trade journal section for the weekly report."""
    lines = [
        "",
        "## Action Zones & Trade Journal",
        "",
    ]

    # Load watchlist
    watchlist_path = "./watchlist.txt"
    tickers = []
    if os.path.exists(watchlist_path):
        with open(watchlist_path) as f:
            tickers = [l.strip().upper() for l in f
                       if l.strip() and not l.startswith("#") and not l.endswith("-USD")]

    if tickers:
        lines.append("### Buy/Sell Zones (Catalyst Watchlist)")
        lines.append("")
        lines.append("| Ticker | Price | Buy Zone | Stop Loss | Target 1 | Target 2 | R:R | Timing |")
        lines.append("|--------|-------|----------|-----------|----------|----------|-----|--------|")

        for ticker in tickers:
            zones = compute_action_zones(ticker)
            if not zones:
                continue
            z = zones
            in_zone = "✅ IN ZONE" if z["buy_zone_low"] <= z["current_price"] <= z["buy_zone_high"] else ""
            lines.append(
                f"| {z['ticker']} | €{z['current_price']:.2f} | "
                f"€{z['buy_zone_low']:.2f}-€{z['buy_zone_high']:.2f} {in_zone} | "
                f"€{z['stop_loss']:.2f} ({(z['stop_loss']/z['current_price']-1)*100:+.1f}%) | "
                f"€{z['target_1']:.2f} ({(z['target_1']/z['current_price']-1)*100:+.1f}%) | "
                f"€{z['target_2']:.2f} ({(z['target_2']/z['current_price']-1)*100:+.1f}%) | "
                f"{z['risk_reward_t1']:.1f} | "
                f"RSI {z['rsi']:.0f} |"
            )
        lines.append("")

    # Open positions
    c = conn.cursor()
    c.execute("""
        SELECT ticker, price, size_eur, date, target_1, target_2, stop_loss
        FROM trades WHERE action = 'BUY' AND status = 'open'
        ORDER BY date DESC
    """)
    open_pos = c.fetchall()

    if open_pos:
        lines.append("### Open Positions")
        lines.append("")
        lines.append("| Ticker | Entry | Size | Date | Current | P&L | Stop | T1 | T2 |")
        lines.append("|--------|-------|------|------|---------|-----|------|----|----|")

        for pos in open_pos:
            ticker, entry, size, date, t1, t2, stop = pos
            try:
                data = yf.download(ticker, period="2d", auto_adjust=True, progress=False)
                close_col = data["Close"]
                if isinstance(close_col, pd.DataFrame):
                    close_col = close_col.iloc[:, 0]
                current = float(close_col.iloc[-1])
                pnl = (current / entry - 1) * 100
                emoji = "🟢" if pnl >= 0 else "🔴"
                lines.append(
                    f"| {ticker} | €{entry:.2f} | €{size:.0f} | {date} | "
                    f"€{current:.2f} | {emoji} {pnl:+.1f}% | "
                    f"€{stop:.2f} | " if stop else "— | "
                    f"€{t1:.2f} | " if t1 else "— | "
                    f"€{t2:.2f} |" if t2 else "— |"
                )
            except Exception:
                lines.append(f"| {ticker} | €{entry:.2f} | €{size:.0f} | {date} | N/A | — | — | — | — |")
        lines.append("")

    # Closed trades summary
    c.execute("""
        SELECT COUNT(*), SUM(CASE WHEN s.price > b.price THEN 1 ELSE 0 END),
               SUM(b.size_eur * (s.price / b.price - 1))
        FROM trades b
        JOIN trades s ON s.linked_buy_id = b.id
        WHERE b.action = 'BUY' AND s.action = 'SELL'
    """)
    row = c.fetchone()
    if row and row[0] and row[0] > 0:
        total, wins, total_pnl = row
        lines.append(f"### Closed Trades: {total} trades | {wins}/{total} wins ({wins/total*100:.0f}%) | Total P&L: €{total_pnl:+,.2f}")
        lines.append("")

    lines.append("---")
    lines.append("*Max 2% of NAV per catalyst position. Stop losses are non-negotiable.*")

    return "\n".join(lines)


def show_report(conn):
    """Print the weekly report section."""
    report = generate_report_section(conn)
    print(report)

    # Also save to file
    os.makedirs(REPORTS_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    path = os.path.join(REPORTS_DIR, f"trade_journal_{date_str}.md")
    with open(path, "w") as f:
        f.write(report)
    print(f"\n  📁 Saved: {path}")


# ==============================================================================
# SECTION 6: MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Trade Journal v1.0")
    parser.add_argument("--zones", action="store_true", help="Show buy/sell zones")
    parser.add_argument("--buy", nargs=3, metavar=("TICKER", "PRICE", "SIZE_EUR"),
                        help="Log a buy: --buy GME 23.50 500")
    parser.add_argument("--sell", nargs=2, metavar=("TICKER", "PRICE"),
                        help="Log a sell: --sell GME 28.00")
    parser.add_argument("--status", action="store_true", help="Open positions + P&L")
    parser.add_argument("--history", action="store_true", help="Closed trade history")
    parser.add_argument("--report", action="store_true", help="Weekly report section")
    parser.add_argument("--notes", type=str, default="", help="Trade notes")
    args = parser.parse_args()

    conn = init_db()

    if args.zones:
        show_zones()
    elif args.buy:
        ticker, price, size = args.buy
        log_buy(conn, ticker.upper(), float(price), float(size), args.notes)
    elif args.sell:
        ticker, price = args.sell
        log_sell(conn, ticker.upper(), float(price), args.notes)
    elif args.status:
        show_status(conn)
    elif args.history:
        show_history(conn)
    elif args.report:
        show_report(conn)
    else:
        show_zones()
        print()
        show_status(conn)

    conn.close()


if __name__ == "__main__":
    main()
