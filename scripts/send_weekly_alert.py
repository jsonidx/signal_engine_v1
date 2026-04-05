#!/usr/bin/env python3
"""
scripts/send_weekly_alert.py
=============================
Sends a Monday morning summary via Telegram (or Discord as fallback).

Reads from:
  - data/regime_latest.json          → market regime badge
  - signals_output/equity_signals_*.csv  → top signals with sizes
  - data/resolved_signals.json        → signal agreement scores
  - config.py                         → PORTFOLIO_NAV, EQUITY_ALLOCATION

Configuration (.env or environment variables):
  TELEGRAM_BOT_TOKEN   — required for Telegram
  TELEGRAM_CHAT_ID     — required for Telegram (e.g. @mychannel or numeric -1001234567)
  DISCORD_WEBHOOK_URL  — alternative to Telegram

USAGE:
    python3 scripts/send_weekly_alert.py               # send now
    python3 scripts/send_weekly_alert.py --dry-run     # print message only
    python3 scripts/send_weekly_alert.py --top 7       # include top-N signals (default 7)

SCHEDULING (cron — Monday 08:00 Berlin = 06:00 UTC in CET/07:00 UTC in CEST):
    Add to crontab with: crontab -e
    0 6 * * 1 cd /path/to/signal_engine_v1 && source venv/bin/activate && python3 scripts/send_weekly_alert.py >> logs/telegram.log 2>&1
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Optional: load .env ───────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # python-dotenv not installed — use real env vars

import urllib.request
import urllib.parse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR     = PROJECT_ROOT / "data"
SIGNALS_DIR  = PROJECT_ROOT / "signals_output"

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ─────────────────────────────────────────────────────────────────────────────


def _regime_badge(regime: str) -> str:
    badges = {
        "RISK_ON":      "🟢 RISK-ON",
        "TRANSITIONAL": "🟡 TRANSITIONAL",
        "RISK_OFF":     "🔴 RISK-OFF",
    }
    return badges.get(regime.upper(), f"⚪ {regime}")


def _load_regime() -> dict:
    path = DATA_DIR / "regime_latest.json"
    if not path.exists():
        return {"regime": "UNKNOWN"}
    try:
        with open(path) as f:
            d = json.load(f)
        return d.get("market", d)
    except Exception:
        return {"regime": "UNKNOWN"}


def _load_top_signals(top_n: int) -> list:
    """Load the most recent equity_signals CSV and return top-N rows."""
    csvs = sorted(SIGNALS_DIR.glob("equity_signals_*.csv"))
    if not csvs:
        return []
    latest = csvs[-1]
    try:
        import csv
        rows = []
        with open(latest, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        # Sort by rank (ascending) or composite_z (descending)
        try:
            rows.sort(key=lambda r: int(r.get("rank", 9999)))
        except (ValueError, KeyError):
            pass
        return rows[:top_n]
    except Exception as e:
        print(f"  [warn] Could not read signals CSV: {e}")
        return []


def _load_agreement_scores() -> dict:
    """Return {ticker: agreement_score} from resolved_signals.json."""
    path = DATA_DIR / "resolved_signals.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            d = json.load(f)
        # Supports both list and dict format
        if isinstance(d, list):
            return {r.get("ticker", ""): r.get("agreement_score", 0) for r in d}
        if isinstance(d, dict):
            return {
                t: v.get("agreement_score", 0) if isinstance(v, dict) else 0
                for t, v in d.items()
            }
        return {}
    except Exception:
        return {}


def _position_size_eur(composite_z: float, nav: float, equity_alloc: float) -> float:
    """Kelly-inspired sizing: base 5% × |z| factor, capped at 8%."""
    base_pct = min(0.08, max(0.01, 0.05 * abs(composite_z) / 1.5))
    return round(nav * equity_alloc * base_pct, 0)


def _load_nav() -> tuple:
    """Return (PORTFOLIO_NAV, EQUITY_ALLOCATION) from config.py."""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        import config
        return getattr(config, "PORTFOLIO_NAV", 50_000), getattr(config, "EQUITY_ALLOCATION", 0.65)
    except Exception:
        return 50_000, 0.65


def build_message(top_n: int = 7) -> str:
    now      = datetime.now()
    regime_d = _load_regime()
    regime   = regime_d.get("regime", "UNKNOWN")
    vix      = regime_d.get("vix")
    signals  = _load_top_signals(top_n)
    agree    = _load_agreement_scores()
    nav, equity_alloc = _load_nav()

    lines = []
    lines.append(f"📊 *Signal Engine — Weekly Digest*")
    lines.append(f"_{now.strftime('%A, %d %b %Y  %H:%M')} Berlin_")
    lines.append("")

    # ── Regime ───────────────────────────────────────────────────────────────
    lines.append(f"*Regime:* {_regime_badge(regime)}")
    if vix:
        lines.append(f"*VIX:* {float(vix):.1f}")
    if regime == "RISK_OFF":
        lines.append("⚠️ *RISK-OFF — reduce size, prefer cash*")
    lines.append("")

    # ── Top signals ───────────────────────────────────────────────────────────
    if not signals:
        lines.append("_(no equity signals found)_")
    else:
        lines.append(f"*Top {len(signals)} Signals*")
        for row in signals:
            ticker  = row.get("ticker", "?")
            z       = float(row.get("composite_z", 0) or 0)
            mktrgm  = row.get("market_regime", "")
            agr     = agree.get(ticker)
            agr_str = f" agr={agr:.0%}" if agr is not None else ""
            eur     = _position_size_eur(z, nav, equity_alloc)
            dir_arrow = "🔼" if z > 0 else "🔽"
            lines.append(
                f"  {dir_arrow} *{ticker}*  z={z:+.2f}{agr_str}  ~€{eur:,.0f}"
            )
    lines.append("")

    # ── NAV summary ───────────────────────────────────────────────────────────
    equity_budget = nav * equity_alloc
    lines.append(f"*NAV:* €{nav:,.0f}  |  *Equity budget:* €{equity_budget:,.0f}")
    lines.append("")
    lines.append("_Full dashboard → http://localhost:3000_")

    return "\n".join(lines)


# ── Senders ───────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [error] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set.")
        return False
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown",
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
            if body.get("ok"):
                print("  ✅ Telegram message sent.")
                return True
            print(f"  [error] Telegram API returned ok=false: {body}")
            return False
    except Exception as e:
        print(f"  [error] Telegram send failed: {e}")
        return False


def send_discord(text: str) -> bool:
    if not DISCORD_WEBHOOK_URL:
        print("  [error] DISCORD_WEBHOOK_URL not set.")
        return False
    # Discord doesn't render Telegram Markdown; strip the asterisks/underscores lightly
    plain = text.replace("*", "**").replace("_", "_")
    payload = json.dumps({"content": plain}).encode()
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            if status in (200, 204):
                print("  ✅ Discord message sent.")
                return True
            print(f"  [error] Discord returned HTTP {status}")
            return False
    except Exception as e:
        print(f"  [error] Discord send failed: {e}")
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Send weekly Signal Engine alert")
    parser.add_argument("--dry-run", action="store_true", help="Print message only, do not send")
    parser.add_argument("--top", type=int, default=7, help="Number of top signals to include (default 7)")
    args = parser.parse_args()

    msg = build_message(top_n=args.top)

    if args.dry_run:
        print("\n" + "=" * 60)
        print("  DRY RUN — message preview:")
        print("=" * 60)
        print(msg)
        print("=" * 60 + "\n")
        return

    # Send via Telegram if configured, else try Discord
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        ok = send_telegram(msg)
    elif DISCORD_WEBHOOK_URL:
        ok = send_discord(msg)
    else:
        print(
            "  [error] No delivery channel configured.\n"
            "  Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env for Telegram,\n"
            "  or DISCORD_WEBHOOK_URL for Discord."
        )
        sys.exit(1)

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
