#!/usr/bin/env python3
"""
scripts/notify_hot_entry.py

Check all active AI theses for tickers where the current price sits inside
BOTH the AI entry zone (thesis_cache.entry_low/high) AND the live technical
buy zone (compute_action_zones). Send a Telegram alert for each match.

Called daily by the refresh_stale_theses.yml GitHub workflow.
Skips blacklisted tickers.

Usage:
    python3 scripts/notify_hot_entry.py
    python3 scripts/notify_hot_entry.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
_env = _ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.db import get_connection
from trade_journal import compute_action_zones

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_BASE   = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("[telegram] No credentials — printing instead:\n")
        print(text)
        return
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        resp = requests.post(
            f"{TG_BASE}/sendMessage",
            json={"chat_id": CHAT_ID, "text": chunk, "parse_mode": "HTML"},
            timeout=15,
        )
        if not resp.ok:
            print(f"[telegram] Warning: {resp.status_code} {resp.text}")


def get_blacklisted() -> set[str]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM blacklist WHERE expires_at IS NULL OR expires_at > NOW()")
            return {r["ticker"] for r in cur.fetchall()}


def get_active_theses() -> list[dict]:
    """Latest thesis per ticker that has entry zones defined."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (ticker)
                    ticker, direction, conviction, entry_low, entry_high,
                    target_1, target_2, stop_loss, thesis, created_at
                FROM thesis_cache
                WHERE entry_low IS NOT NULL AND entry_high IS NOT NULL
                ORDER BY ticker, created_at DESC
            """)
            return [dict(r) for r in cur.fetchall()]


def check_ticker(thesis: dict) -> dict | None:
    """Return hot-entry data if current price is in both AI and live zone, else None."""
    ticker     = thesis["ticker"]
    ai_low     = float(thesis["entry_low"])
    ai_high    = float(thesis["entry_high"])

    try:
        zones = compute_action_zones(ticker)
    except Exception as e:
        print(f"  [{ticker}] zone error: {e}")
        return None

    if not zones:
        return None

    current   = float(zones["current_price"])
    live_low  = float(zones["buy_zone_low"])
    live_high = float(zones["buy_zone_high"])

    in_ai   = ai_low   <= current <= ai_high
    in_live = live_low <= current <= live_high

    if in_ai and in_live:
        return {
            "ticker":     ticker,
            "direction":  thesis.get("direction") or "NEUTRAL",
            "conviction": thesis.get("conviction") or 0,
            "current":    current,
            "ai_low":     ai_low,
            "ai_high":    ai_high,
            "live_low":   live_low,
            "live_high":  live_high,
            "target_1":   thesis.get("target_1"),
            "target_2":   thesis.get("target_2"),
            "stop_loss":  thesis.get("stop_loss"),
            "thesis":     (thesis.get("thesis") or "")[:200],
        }
    return None


def dir_emoji(direction: str) -> str:
    return {"BULL": "🟢", "BEAR": "🔴", "NEUTRAL": "⚪"}.get(direction.upper(), "⚪")


def conviction_dots(n: int) -> str:
    n = max(0, min(5, n or 0))
    return "●" * n + "○" * (5 - n)


def fmt(val, prefix="$") -> str:
    if val is None:
        return "—"
    try:
        return f"{prefix}{float(val):.2f}"
    except (TypeError, ValueError):
        return str(val)


def build_message(hits: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"🔥 <b>Hot Entry Alert</b> — {len(hits)} ticker{'s' if len(hits) != 1 else ''}",
        f"<i>Price inside AI entry zone AND live buy zone · {now}</i>",
    ]

    for h in hits:
        dir_e = dir_emoji(h["direction"])
        rr = None
        if h["target_1"] and h["stop_loss"]:
            entry_mid = (h["ai_low"] + h["ai_high"]) / 2
            risk = entry_mid - h["stop_loss"]
            if risk > 0:
                rr = (h["target_1"] - entry_mid) / risk

        lines.append("")
        lines.append(f"{dir_e} <b>{h['ticker']}</b>  {conviction_dots(h['conviction'])}  {h['direction']}")
        lines.append(f"  Price: <b>{fmt(h['current'])}</b>")
        lines.append(f"  AI entry:   {fmt(h['ai_low'])} – {fmt(h['ai_high'])}")
        lines.append(f"  Live zone:  {fmt(h['live_low'])} – {fmt(h['live_high'])}")
        lines.append(f"  T1: {fmt(h['target_1'])}  T2: {fmt(h['target_2'])}  Stop: {fmt(h['stop_loss'])}" +
                     (f"  R:R {rr:.1f}" if rr else ""))
        if h["thesis"]:
            lines.append(f"  <i>{h['thesis']}{'…' if len(h['thesis']) == 200 else ''}</i>")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print message, don't send")
    parser.add_argument("--workers", type=int, default=6, help="Parallel zone fetches (default: 6)")
    args = parser.parse_args()

    blacklisted = get_blacklisted()
    theses = [t for t in get_active_theses() if t["ticker"] not in blacklisted]

    print(f"Checking {len(theses)} tickers for hot entry (blacklisted: {len(blacklisted)} skipped)...")

    hits: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(check_ticker, t): t["ticker"] for t in theses}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                result = future.result()
                if result:
                    print(f"  🔥 {ticker} — HOT ENTRY")
                    hits.append(result)
                else:
                    print(f"  ·  {ticker}")
            except Exception as e:
                print(f"  [{ticker}] error: {e}")

    # Sort by conviction desc
    hits.sort(key=lambda h: h["conviction"], reverse=True)

    if not hits:
        msg = "✅ <b>Hot Entry Check</b>\nNo tickers in hot entry zone right now."
        print("\nNo hot entries found.")
    else:
        msg = build_message(hits)
        print(f"\n{len(hits)} hot entr{'y' if len(hits) == 1 else 'ies'} found.")

    print("\n--- Telegram message ---")
    print(msg)

    if not args.dry_run:
        send_telegram(msg)
    else:
        print("\n[dry-run] Telegram not sent.")


if __name__ == "__main__":
    main()
