#!/usr/bin/env python3
"""
scripts/refresh_stale_and_notify.py

1. Snapshot thesis values for all tickers older than --days
2. Re-run AI analysis for those tickers (refresh_stale_theses logic)
3. Compare old vs new: direction, conviction, entry, stop, targets
4. Send Telegram summary of what changed

Usage:
    python3 scripts/refresh_stale_and_notify.py --days 7
    python3 scripts/refresh_stale_and_notify.py --days 7 --dry-run
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_BASE   = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_LIMIT  = 4000


def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("[telegram] No credentials — printing message instead:\n")
        print(text)
        return
    # Split into chunks if needed
    chunks = [text[i:i+TG_LIMIT] for i in range(0, len(text), TG_LIMIT)]
    for chunk in chunks:
        resp = requests.post(
            f"{TG_BASE}/sendMessage",
            json={"chat_id": CHAT_ID, "text": chunk, "parse_mode": "HTML"},
            timeout=15,
        )
        if not resp.ok:
            print(f"[telegram] Warning: {resp.status_code} {resp.text}")


def fmt(val, prefix="$") -> str:
    if val is None:
        return "—"
    try:
        return f"{prefix}{float(val):.2f}"
    except (TypeError, ValueError):
        return str(val)


def get_blacklisted_tickers() -> set[str]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticker FROM blacklist WHERE expires_at IS NULL OR expires_at > NOW()"
            )
            return {r["ticker"] for r in cur.fetchall()}


def get_stale_tickers(days: int) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    blacklisted = get_blacklisted_tickers()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker FROM thesis_cache
                GROUP BY ticker
                HAVING MAX(created_at::timestamptz) < %s
                ORDER BY MAX(created_at::timestamptz) ASC
                """,
                (cutoff,),
            )
            return [r["ticker"] for r in cur.fetchall() if r["ticker"] not in blacklisted]


def snapshot_theses(tickers: list[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (ticker)
                    ticker, direction, conviction,
                    entry_low, entry_high, stop_loss, target_1, target_2,
                    created_at
                FROM thesis_cache
                WHERE ticker = ANY(%s)
                ORDER BY ticker, created_at DESC
                """,
                (tickers,),
            )
            return {r["ticker"]: dict(r) for r in cur.fetchall()}


def _parse_created_at(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def classify_refresh_results(
    tickers: list[str],
    old_snap: dict[str, dict],
    new_snap: dict[str, dict],
) -> tuple[list[str], list[str]]:
    updated: list[str] = []
    untouched: list[str] = []
    for ticker in tickers:
        old_dt = _parse_created_at((old_snap.get(ticker) or {}).get("created_at"))
        new_dt = _parse_created_at((new_snap.get(ticker) or {}).get("created_at"))
        if new_dt and (old_dt is None or new_dt > old_dt):
            updated.append(ticker)
        else:
            untouched.append(ticker)
    return updated, untouched


def run_refresh(tickers: list[str], batch_size: int = 10) -> list[tuple[list[str], int]]:
    results: list[tuple[list[str], int]] = []
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        cmd = [sys.executable, str(_ROOT / "ai_quant.py"), "--tickers"] + batch + ["--no-cache", "--force-ai"]
        print(f"Running: {' '.join(cmd)}")
        proc = subprocess.run(cmd, cwd=str(_ROOT))
        results.append((batch, proc.returncode))
    return results


def build_diff_line(field: str, old_val, new_val, prefix="$") -> str | None:
    if old_val == new_val:
        return None
    old_s = fmt(old_val, prefix)
    new_s = fmt(new_val, prefix)
    return f"    {field}: {old_s} → {new_s}"


def dir_emoji(direction: str | None) -> str:
    return {"BULL": "🟢", "BEAR": "🔴", "NEUTRAL": "⚪"}.get((direction or "").upper(), "⚪")


def build_message(
    attempted: list[str],
    updated: list[str],
    untouched: list[str],
    old_snap: dict,
    new_snap: dict,
    days: int,
) -> str:
    lines = [f"<b>🔄 Stale Thesis Refresh ({days}d+)</b>"]
    lines.append(f"<i>{len(updated)} of {len(attempted)} tickers updated</i>\n")

    changed = []
    unchanged = []

    for ticker in updated:
        old = old_snap.get(ticker, {})
        new = new_snap.get(ticker, {})
        if not new:
            unchanged.append(f"  {ticker} — no new thesis written")
            continue

        diffs = []
        # Direction change
        old_dir = (old.get("direction") or "").upper()
        new_dir = (new.get("direction") or "").upper()
        if old_dir != new_dir:
            diffs.append(f"    Direction: {dir_emoji(old_dir)}{old_dir} → {dir_emoji(new_dir)}{new_dir}")

        # Conviction change
        old_conv = old.get("conviction")
        new_conv = new.get("conviction")
        if old_conv != new_conv:
            diffs.append(f"    Conviction: {old_conv or '—'} → {new_conv or '—'}")

        for field, prefix in [
            ("entry_low", "$"), ("entry_high", "$"),
            ("stop_loss", "$"), ("target_1", "$"), ("target_2", "$"),
        ]:
            d = build_diff_line(field.replace("_", " ").title(), old.get(field), new.get(field), prefix)
            if d:
                diffs.append(d)

        age_days = 0
        if old.get("created_at"):
            try:
                old_dt = datetime.fromisoformat(str(old["created_at"])).replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - old_dt).days
            except Exception:
                pass

        if diffs:
            header = f"{dir_emoji(new_dir)} <b>{ticker}</b> <i>(was {age_days}d old)</i>"
            changed.append("\n".join([header] + diffs))
        else:
            unchanged.append(ticker)

    if changed:
        lines.append("<b>Changed:</b>")
        lines.extend(changed)

    if unchanged:
        lines.append(f"\n<b>No change:</b> {', '.join(unchanged)}")

    if untouched:
        lines.append(f"\n<b>Not updated:</b> {', '.join(untouched)}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true", help="Skip AI call, just report what would refresh")
    args = parser.parse_args()

    print(f"Checking for theses older than {args.days} days...")
    stale = get_stale_tickers(args.days)

    if not stale:
        print("All theses are fresh — nothing to do.")
        send_telegram(f"✅ <b>Stale Thesis Refresh</b>\nAll {args.days}d+ theses are up to date. Nothing refreshed.")
        return

    print(f"Found {len(stale)} stale tickers: {', '.join(stale)}")
    old_snap = snapshot_theses(stale)

    if args.dry_run:
        print("Dry run — skipping AI refresh.")
        send_telegram(f"🔍 <b>Stale Thesis Refresh (dry run)</b>\n{len(stale)} tickers would be refreshed:\n{', '.join(stale)}")
        return

    batch_results = run_refresh(stale)

    new_snap = snapshot_theses(stale)
    updated, untouched = classify_refresh_results(stale, old_snap, new_snap)
    msg = build_message(stale, updated, untouched, old_snap, new_snap, args.days)
    print("\n--- Telegram message ---")
    print(msg)
    send_telegram(msg)

    failed_batches = [batch for batch, code in batch_results if code != 0]
    if failed_batches or untouched:
        if failed_batches:
            print(f"ERROR: {len(failed_batches)} batch(es) returned non-zero exit codes.")
        if untouched:
            print(f"ERROR: {len(untouched)} ticker(s) did not get a newer thesis timestamp.")
        sys.exit(1)


if __name__ == "__main__":
    main()
