#!/usr/bin/env python3
"""
================================================================================
MIDWEEK SCAN v1.0
================================================================================
Lightweight Wednesday evening scan focused on:
1. Catalyst screening with fresh weekday social data (peak Reddit activity)
2. Quick market pulse (SPY, VIX, BTC trend status)
3. Watchlist alerts for any stocks that moved significantly since Sunday

Run this on Wednesday evening to catch weekday social momentum while it's
fresh (1.0x recency weight) rather than waiting for Sunday when it decays.

USAGE:
    python3 midweek_scan.py                # Full midweek scan
    python3 midweek_scan.py --watchlist     # Only check Sunday's picks
    python3 midweek_scan.py --pulse         # Market pulse only

SCHEDULE IN COWORK:
    /schedule → Wednesday 8:00 PM → run midweek_scan.py

IMPORTANT: This is NOT investment advice. For research/educational purposes only.
================================================================================
"""

import argparse
import json
import os
import sys
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

try:
    from config import (
        PORTFOLIO_NAV, EQUITY_ALLOCATION, CRYPTO_ALLOCATION,
        RISK_PARAMS, OUTPUT_DIR,
    )
except ImportError:
    OUTPUT_DIR = "./signals_output"
    PORTFOLIO_NAV = 50_000

REPORT_DIR = "./weekly_reports"


# ==============================================================================
# SECTION 1: MARKET PULSE
# ==============================================================================

def market_pulse() -> dict:
    """Quick check on broad market conditions."""
    print("\n  Fetching market pulse...")
    pulse = {}

    # SPY
    try:
        spy = yf.Ticker("SPY")
        hist = spy.history(period="1mo")
        if not hist.empty:
            current = float(hist["Close"].iloc[-1])
            week_ago = float(hist["Close"].iloc[-5]) if len(hist) >= 5 else current
            month_ago = float(hist["Close"].iloc[0])
            sma20 = float(hist["Close"].rolling(20).mean().iloc[-1]) if len(hist) >= 20 else current
            pulse["spy"] = {
                "price": current,
                "week_change": (current / week_ago - 1),
                "month_change": (current / month_ago - 1),
                "vs_sma20": (current / sma20 - 1),
                "trend": "BULLISH" if current > sma20 else "BEARISH",
            }
    except Exception:
        pulse["spy"] = {"price": 0, "trend": "UNKNOWN"}

    # VIX
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="5d")
        if not hist.empty:
            current_vix = float(hist["Close"].iloc[-1])
            if current_vix > 30:
                regime = "EXTREME FEAR"
            elif current_vix > 20:
                regime = "ELEVATED"
            elif current_vix > 15:
                regime = "NORMAL"
            else:
                regime = "COMPLACENT"
            pulse["vix"] = {"level": current_vix, "regime": regime}
    except Exception:
        pulse["vix"] = {"level": 0, "regime": "UNKNOWN"}

    # BTC
    try:
        btc = yf.download("BTC-USD", period="1y", auto_adjust=True, progress=False)
        if not btc.empty:
            close = btc["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            current_btc = float(close.iloc[-1])
            ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else current_btc
            week_ago_btc = float(close.iloc[-5]) if len(close) >= 5 else current_btc
            pulse["btc"] = {
                "price": current_btc, "ma200": ma200,
                "vs_ma200": (current_btc / ma200 - 1),
                "week_change": (current_btc / week_ago_btc - 1),
                "signal": "LONG" if current_btc > ma200 else "CASH",
            }
    except Exception:
        pulse["btc"] = {"price": 0, "signal": "UNKNOWN"}

    return pulse


def print_pulse(pulse: dict):
    """Print market pulse summary."""
    print(f"\n{'─' * 60}")
    print(f"  MARKET PULSE — {datetime.now().strftime('%A %Y-%m-%d %H:%M')}")
    print(f"{'─' * 60}")

    spy = pulse.get("spy", {})
    if spy.get("price"):
        trend_icon = "🟢" if spy["trend"] == "BULLISH" else "🔴"
        print(f"\n  SPY:  ${spy['price']:,.2f}  {trend_icon} {spy['trend']}")
        print(f"        Week: {spy['week_change']:+.2%}  |  Month: {spy['month_change']:+.2%}  |  vs 20-SMA: {spy['vs_sma20']:+.2%}")

    vix = pulse.get("vix", {})
    if vix.get("level"):
        regime_icon = {"EXTREME FEAR": "🔴", "ELEVATED": "🟠", "NORMAL": "🟡", "COMPLACENT": "🟢"}.get(vix["regime"], "⚪")
        print(f"\n  VIX:  {vix['level']:.1f}  {regime_icon} {vix['regime']}")
        if vix["regime"] == "EXTREME FEAR":
            print(f"        ⚠️  High VIX = squeeze setups more dangerous, reduce size")
        elif vix["regime"] == "COMPLACENT":
            print(f"        Low VIX = breakouts more likely to sustain")

    btc = pulse.get("btc", {})
    if btc.get("price"):
        btc_icon = "🟢" if btc["signal"] == "LONG" else "⚪"
        print(f"\n  BTC:  ${btc['price']:,.2f}  {btc_icon} {btc['signal']}")
        print(f"        MA200: ${btc['ma200']:,.2f} ({btc['vs_ma200']:+.1%})  |  Week: {btc['week_change']:+.2%}")


# ==============================================================================
# SECTION 2: WATCHLIST CHECK
# ==============================================================================

def check_sunday_positions() -> list:
    """Load Sunday's positions and check midweek movement."""
    alerts = []
    try:
        from utils.db import get_connection
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT id, date FROM snapshots ORDER BY date DESC LIMIT 1")
        row = c.fetchone()
        if not row:
            conn.close()
            return alerts

        snapshot_id, snap_date = row['id'], row['date']
        c.execute("""
            SELECT ticker, entry_price, composite_z, rank
            FROM equity_positions WHERE snapshot_id = %s
            ORDER BY rank ASC
        """, (snapshot_id,))
        positions = c.fetchall()
        conn.close()

        if not positions:
            return alerts

        tickers = [p['ticker'] for p in positions]
        data = yf.download(tickers, period="5d", auto_adjust=True, progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            close = data["Close"]
        else:
            close = data[["Close"]].rename(columns={"Close": tickers[0]})

        for pos in positions:
            ticker, entry_price, z_score, rank = pos['ticker'], pos['entry_price'], pos['composite_z'], pos['rank']
            if ticker in close.columns and entry_price > 0:
                current = close[ticker].dropna()
                if not current.empty:
                    current_price = float(current.iloc[-1])
                    change = (current_price / entry_price - 1)
                    flag = ""
                    if abs(change) > 0.05:
                        flag = "BIG MOVE" if change > 0 else "BIG DROP"
                    elif abs(change) > 0.03:
                        flag = "MOVING" if change > 0 else "DIPPING"

                    alerts.append({
                        "ticker": ticker, "entry_price": entry_price,
                        "current_price": current_price, "change": change,
                        "rank": rank, "z_score": z_score, "flag": flag,
                    })
    except Exception as e:
        print(f"  [WARN] Could not check positions: {e}")

    return alerts


def print_watchlist_alerts(alerts: list):
    """Print position movement alerts."""
    if not alerts:
        print(f"\n  No positions recorded yet — run paper_trader.py --record first.")
        return

    print(f"\n{'─' * 60}")
    print(f"  POSITION MOVEMENT SINCE LAST SNAPSHOT")
    print(f"{'─' * 60}")
    print(f"\n  {'Rank':<6}{'Ticker':<10}{'Entry$':>10}{'Now$':>10}{'Change':>10}{'Alert':>14}")
    print(f"  {'─' * 60}")

    big_moves = []
    for a in sorted(alerts, key=lambda x: abs(x["change"]), reverse=True):
        flag_str = a["flag"]
        if "BIG" in flag_str:
            icon = "🔴" if "DROP" in flag_str else "🟢"
            flag_str = f"{icon} {flag_str}"
            big_moves.append(a)
        elif "DIPPING" in flag_str:
            flag_str = f"🟡 {flag_str}"
        elif "MOVING" in flag_str:
            flag_str = f"🟢 {flag_str}"

        print(f"  {a['rank']:<6}{a['ticker']:<10}"
              f"${a['entry_price']:>9.2f}"
              f"${a['current_price']:>9.2f}"
              f"{a['change']:>+9.2%}"
              f"  {flag_str}")

    if big_moves:
        print(f"\n  ⚠️  {len(big_moves)} position(s) moved >5% — review before Sunday rebalance")


# ==============================================================================
# SECTION 3: CATALYST SCAN
# ==============================================================================

def run_catalyst_scan(include_social: bool = True):
    """Import and run the catalyst screener with social data."""
    try:
        from catalyst_screener import screen_universe, print_results
        from catalyst_screener import MEME_UNIVERSE, SMALL_CAP_UNIVERSE, LARGE_CAP_WATCH

        universe = list(set(SMALL_CAP_UNIVERSE + MEME_UNIVERSE + LARGE_CAP_WATCH))
        results = screen_universe(universe, include_social=include_social)

        if not results.empty:
            print_results(results, top_n=10)

            os.makedirs(OUTPUT_DIR, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d")
            path = os.path.join(OUTPUT_DIR, f"midweek_catalyst_{date_str}.csv")
            export = results.drop(columns=["flags"])
            export.to_csv(path, index=False)
            print(f"\n  📁 Exported to: {path}")

            return results

    except ImportError:
        print("  [ERROR] catalyst_screener.py not found in the same directory.")
        return pd.DataFrame()


# ==============================================================================
# SECTION 4: SAVE REPORT
# ==============================================================================

def save_midweek_report(pulse: dict, alerts: list, catalyst_results):
    """Save a markdown summary of the midweek scan."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    path = os.path.join(REPORT_DIR, f"midweek_{date_str}.md")

    lines = []
    lines.append(f"# Midweek Scan — {datetime.now().strftime('%A %B %d, %Y')}\n")

    lines.append("## Market Pulse\n")
    spy = pulse.get("spy", {})
    vix = pulse.get("vix", {})
    btc = pulse.get("btc", {})
    if spy.get("price"):
        lines.append(f"- SPY: ${spy['price']:,.2f} ({spy['trend']}) — Week: {spy.get('week_change',0):+.2%}")
    if vix.get("level"):
        lines.append(f"- VIX: {vix['level']:.1f} ({vix['regime']})")
    if btc.get("price"):
        lines.append(f"- BTC: ${btc['price']:,.2f} ({btc['signal']}) — vs MA200: {btc.get('vs_ma200',0):+.1%}")
    lines.append("")

    if alerts:
        lines.append("## Position Movement Since Sunday\n")
        big = [a for a in alerts if abs(a["change"]) > 0.03]
        if big:
            for a in sorted(big, key=lambda x: abs(x["change"]), reverse=True):
                direction = "up" if a["change"] > 0 else "down"
                lines.append(f"- {a['ticker']}: {a['change']:+.2%} ({direction} from ${a['entry_price']:.2f} to ${a['current_price']:.2f})")
        else:
            lines.append("- No significant moves (all within +/- 3%)")
        lines.append("")

    if catalyst_results is not None and not catalyst_results.empty:
        lines.append("## Top Catalyst Candidates\n")
        for _, row in catalyst_results.head(5).iterrows():
            lines.append(f"- {row['ticker']}: Composite {row['composite']:.0f}% — "
                         f"Short {row['short_pct']:.0%}, Vol {row['vol_ratio']:.1f}x, "
                         f"Social {row['social_score']}/5")
        lines.append("")

    lines.append("---\n")
    lines.append("*This is not investment advice. For research/educational purposes only.*\n")

    with open(path, "w") as f:
        f.write("\n".join(lines))

    print(f"\n  📄 Midweek report saved: {path}")


# ==============================================================================
# SECTION 5: MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Midweek Scan v1.0")
    parser.add_argument("--watchlist", action="store_true",
                        help="Only check Sunday's positions for movement")
    parser.add_argument("--pulse", action="store_true",
                        help="Market pulse only (no catalyst scan)")
    parser.add_argument("--no-social", action="store_true",
                        help="Skip Reddit scan (faster)")
    args = parser.parse_args()

    print(f"\n{'█' * 60}")
    print(f"  MIDWEEK SCAN v1.0 — {datetime.now().strftime('%A %Y-%m-%d %H:%M')}")
    print(f"  Peak weekday social data capture")
    print(f"{'█' * 60}")

    pulse = market_pulse()
    print_pulse(pulse)

    alerts = check_sunday_positions()
    print_watchlist_alerts(alerts)

    catalyst_results = None
    if not args.watchlist and not args.pulse:
        catalyst_results = run_catalyst_scan(include_social=not args.no_social)

    save_midweek_report(pulse, alerts, catalyst_results)

    print(f"\n{'█' * 60}")
    print(f"  MIDWEEK SCAN COMPLETE")
    print(f"  This is awareness, not a trade trigger.")
    print(f"  Full signal run happens Sunday evening.")
    print(f"  THIS IS NOT INVESTMENT ADVICE.")
    print(f"{'█' * 60}\n")


if __name__ == "__main__":
    main()
