#!/usr/bin/env python3
"""
scripts/notify_analyze_result.py

Send deep-dive thesis results to Telegram after analyze_tickers.yml completes.
Reads fresh theses from Supabase for the requested tickers and sends one
formatted message per ticker.

Usage:
    python3 scripts/notify_analyze_result.py \
        --tickers "TSLA NVDA AMD" \
        --status success \
        --run-url "https://github.com/..." \
        --duration-min 4.2
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

# ── Load .env from project root if running locally ───────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
_env = _ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
DB_URL    = os.environ.get("DATABASE_URL", "")

TG_BASE  = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_LIMIT = 4000


# ── Telegram helpers ──────────────────────────────────────────────────────────

def tg_send(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[notify_analyze] TELEGRAM credentials not set — skipping.", file=sys.stderr)
        return False
    try:
        r = requests.post(
            f"{TG_BASE}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        if r.status_code == 200:
            return True
        print(f"[notify_analyze] Telegram HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[notify_analyze] tg_send failed: {exc}", file=sys.stderr)
        return False


def tg_send_chunked(text: str) -> None:
    if len(text) <= TG_LIMIT:
        tg_send(text)
        return
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > TG_LIMIT:
            if current:
                parts.append(current.rstrip())
            current = line
        else:
            current += line
    if current:
        parts.append(current.rstrip())
    for i, part in enumerate(parts):
        tg_send(part)
        if i < len(parts) - 1:
            time.sleep(0.5)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_connect():
    if not DB_URL:
        return None
    try:
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception as exc:
        print(f"[notify_analyze] DB connect failed: {exc}", file=sys.stderr)
        return None


def _safe_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _safe_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except Exception:
        return None


def fetch_theses(conn, tickers: list[str]) -> list[dict]:
    """Fetch today's thesis rows for the given tickers from Supabase."""
    today = date.today().isoformat()
    try:
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(tickers))
        cur.execute(f"""
            SELECT ticker, direction, conviction, time_horizon, data_quality,
                   entry_low, entry_high, stop_loss, target_1, target_2,
                   position_size_pct, thesis, key_invalidation,
                   primary_scenario, bear_scenario,
                   signal_agreement_score, bull_probability, bear_probability,
                   catalysts_json, risks_json,
                   model_used, cost_usd
            FROM   thesis_cache
            WHERE  date = %s
              AND  ticker IN ({placeholders})
            ORDER  BY conviction DESC
        """, (today, *tickers))
        return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        print(f"[notify_analyze] fetch_theses failed: {exc}", file=sys.stderr)
        return []


# ── Formatters ────────────────────────────────────────────────────────────────

def _p(v) -> str:
    f = _safe_float(v)
    return f"${f:.2f}" if f is not None else "—"


def _pct(v) -> str:
    f = _safe_float(v)
    return f"{f:.0%}" if f is not None else "—"


def fmt_thesis(t: dict) -> str:
    ticker     = t.get("ticker", "?")
    direction  = t.get("direction", "NEUTRAL")
    conviction = _safe_int(t.get("conviction")) or 0
    horizon    = t.get("time_horizon") or "?"
    dq         = t.get("data_quality") or "?"
    thesis     = t.get("thesis") or ""
    model      = (t.get("model_used") or "").split("-")[0]
    cost       = _safe_float(t.get("cost_usd"))

    dir_icon = {"BULL": "🟢", "BEAR": "🔴", "NEUTRAL": "🟡"}.get(direction, "◯")
    conv_bar = {1: "▪░░░░", 2: "▪▪░░░", 3: "▪▪▪░░", 4: "▪▪▪▪░", 5: "▪▪▪▪▪"}.get(conviction, "?????")

    lines = [
        f"{dir_icon} <b>{ticker}</b> — {direction}  [{conviction}/5 {conv_bar}]",
        f"Horizon: {horizon}  ·  Data quality: {dq}",
        "",
    ]

    entry_low  = _safe_float(t.get("entry_low"))
    entry_high = _safe_float(t.get("entry_high"))
    stop       = _safe_float(t.get("stop_loss"))
    t1         = _safe_float(t.get("target_1"))
    t2         = _safe_float(t.get("target_2"))
    pos_pct    = _safe_float(t.get("position_size_pct"))

    if entry_low and entry_high:
        lines.append(f"<b>Entry:</b>   {_p(entry_low)} – {_p(entry_high)}")
    if stop:
        lines.append(f"<b>Stop:</b>    {_p(stop)}")
    if t1:
        t2_str = f" → {_p(t2)}" if t2 else ""
        lines.append(f"<b>Target:</b>  {_p(t1)}{t2_str}")
    if pos_pct is not None:
        lines.append(f"<b>Size:</b>    {pos_pct:.0f}% of allocation slice")

    # R/R
    if entry_high and stop and t1:
        try:
            risk = entry_high - stop
            reward = t1 - entry_high
            if risk > 0:
                lines.append(f"<b>R/R:</b>     {reward/risk:.1f}x")
        except Exception:
            pass

    # Probabilities
    bull_p = _safe_float(t.get("bull_probability"))
    bear_p = _safe_float(t.get("bear_probability"))
    agree  = _safe_float(t.get("signal_agreement_score"))
    prob_parts = []
    if agree is not None:
        prob_parts.append(f"Agreement: {agree:.0%}")
    if bull_p is not None and bear_p is not None:
        prob_parts.append(f"Bull: {bull_p:.0%} / Bear: {bear_p:.0%}")
    if prob_parts:
        lines.append(" | ".join(prob_parts))

    lines.append("")

    # Scenarios
    primary = t.get("primary_scenario")
    counter = t.get("bear_scenario")
    key_inv = t.get("key_invalidation")
    if primary:
        lines.append(f"<b>Primary:</b> {primary}")
    if counter:
        lines.append(f"<b>Counter:</b> {counter}")
    if key_inv:
        lines.append(f"<b>Invalidation:</b> {key_inv}")
    if primary or counter or key_inv:
        lines.append("")

    # Thesis
    if thesis:
        lines.append(f"<b>Thesis:</b> {thesis}")
        lines.append("")

    # Catalysts
    import json as _json
    catalysts = []
    try:
        catalysts = _json.loads(t.get("catalysts_json") or "[]") or []
    except Exception:
        pass
    if catalysts:
        lines.append("<b>Catalysts:</b>")
        for c in catalysts[:3]:
            lines.append(f"  ✓ {c}")

    # Risks
    risks = []
    try:
        risks = _json.loads(t.get("risks_json") or "[]") or []
    except Exception:
        pass
    if risks:
        lines.append("<b>Risks:</b>")
        for r in risks[:3]:
            lines.append(f"  ✗ {r}")

    # Model / cost footer
    footer_parts = []
    if model:
        footer_parts.append(f"model: {model}")
    if cost is not None:
        footer_parts.append(f"cost: ${cost:.4f}")
    if footer_parts:
        lines.append(f"\n<code>{' · '.join(footer_parts)}</code>")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Send analyze_tickers results to Telegram")
    parser.add_argument("--tickers",      required=True, help="Space-separated ticker list")
    parser.add_argument("--status",       default="success")
    parser.add_argument("--run-url",      default="")
    parser.add_argument("--duration-min", type=float, default=0.0)
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers.split() if t.strip()]
    status  = args.status
    run_url = args.run_url
    dur     = args.duration_min

    status_icon = {"success": "✅", "failure": "❌", "cancelled": "🚫"}.get(status, "⚠️")
    dur_str = f" · {dur:.1f} min" if dur else ""
    ticker_str = " ".join(tickers)
    header = (
        f"{status_icon} <b>Deep Dive — {ticker_str}</b>{dur_str}\n"
        + (f'<a href="{run_url}">View on GitHub</a>' if run_url else "")
    )

    if status != "success":
        tg_send_chunked(f"{header}\n\n❌ Analysis failed — check the run logs.")
        return

    conn = _db_connect()
    if not conn:
        tg_send_chunked(f"{header}\n\n⚠️ Could not connect to database to fetch results.")
        return

    rows = fetch_theses(conn, tickers)
    conn.close()

    if not rows:
        tg_send_chunked(f"{header}\n\n⚠️ Analysis ran but no thesis found in DB for today.")
        return

    # Header first
    tg_send(header)
    time.sleep(0.3)

    # One message per ticker
    found = {r["ticker"] for r in rows}
    for row in rows:
        tg_send_chunked(fmt_thesis(row))
        time.sleep(0.3)

    # Warn about any tickers that came back empty
    missing = [t for t in tickers if t not in found]
    if missing:
        tg_send(f"⚠️ No thesis written for: {', '.join(missing)} (filtered out or below min-score)")

    print(f"[notify_analyze] Sent results for {len(rows)}/{len(tickers)} tickers.")


if __name__ == "__main__":
    main()
