#!/usr/bin/env python3
"""
scripts/notify_pipeline_result.py

Send a Telegram message after every pipeline run (GitHub Actions or local).
Call at the end of both daily_pipeline.yml and manual_pipeline.yml.

Usage:
    python3 scripts/notify_pipeline_result.py \
        --status success \
        --workflow "Daily Signal Pipeline" \
        --run-url "https://github.com/..." \
        --duration-min 12.5 \
        [--skip-ai]

If --skip-ai is set the AI Thesis section is omitted (no thesis written).
Reads TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DATABASE_URL from environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone
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

# ── Project root on path so squeeze_alerts can be imported ───────────────────
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from squeeze_alerts import build_squeeze_alerts, format_alerts_section
    _SQUEEZE_ALERTS_AVAILABLE = True
except ImportError:
    _SQUEEZE_ALERTS_AVAILABLE = False

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
DB_URL    = os.environ.get("DATABASE_URL", "")

TG_BASE   = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_LIMIT  = 4000   # safe Telegram message length (hard limit 4096)


# ── Telegram send ─────────────────────────────────────────────────────────────

def tg_send(text: str, parse_mode: str = "HTML") -> bool:
    """Send text to configured chat. Returns True on success."""
    if not BOT_TOKEN or not CHAT_ID:
        print("[notify] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping.", file=sys.stderr)
        return False
    try:
        r = requests.post(
            f"{TG_BASE}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode,
                  "disable_web_page_preview": True},
            timeout=20,
        )
        if r.status_code == 200:
            return True
        print(f"[notify] Telegram HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[notify] tg_send failed: {exc}", file=sys.stderr)
        return False


def tg_send_chunked(text: str) -> None:
    """Send text, splitting at newlines if over TG_LIMIT."""
    if len(text) <= TG_LIMIT:
        tg_send(text)
        return
    # Split at paragraph boundaries
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
            time.sleep(0.5)  # avoid flood limits


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_connect():
    """Return a psycopg2 connection or None."""
    if not DB_URL:
        return None
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn
    except Exception as exc:
        print(f"[notify] DB connect failed: {exc}", file=sys.stderr)
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


# ── Data fetchers ─────────────────────────────────────────────────────────────

def fetch_top10_rankings(conn) -> list[dict]:
    """Return today's top-10 from daily_rankings (latest run_date)."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT rank, ticker, direction, prob_combined, prob_t1,
                   ev_t1_pct, t1_price, t2_price, stop_price,
                   hold_days, agreement_score, is_open_position
            FROM   daily_rankings
            WHERE  run_date = (SELECT MAX(run_date) FROM daily_rankings)
              AND  rank <= 10
            ORDER  BY rank ASC
        """)
        return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        print(f"[notify] fetch_top10 failed: {exc}", file=sys.stderr)
        return []


def fetch_top5_thesis(conn) -> list[dict]:
    """Return today's AI theses, up to 5, ordered by priority from daily_rankings."""
    try:
        today = date.today().isoformat()
        cur = conn.cursor()
        # Join with daily_rankings to get only ranked tickers in rank order
        cur.execute("""
            SELECT t.ticker, t.direction, t.conviction,
                   t.entry_low, t.entry_high, t.stop_loss,
                   t.target_1, t.target_2,
                   t.thesis, t.key_invalidation,
                   t.prob_combined, t.prob_technical, t.prob_options,
                   t.prob_catalyst, t.prob_news,
                   t.model_used, t.cost_usd,
                   dr.rank
            FROM   thesis_cache t
            JOIN   daily_rankings dr ON dr.ticker = t.ticker
                   AND dr.run_date = (SELECT MAX(run_date) FROM daily_rankings)
            WHERE  t.date = %s
            ORDER  BY dr.rank ASC
            LIMIT  5
        """, (today,))
        rows = [dict(r) for r in cur.fetchall()]
        if rows:
            return rows
        # Fallback: no rank join — just return today's theses by date
        cur.execute("""
            SELECT ticker, direction, conviction,
                   entry_low, entry_high, stop_loss,
                   target_1, target_2,
                   thesis, key_invalidation,
                   prob_combined, prob_technical, prob_options,
                   prob_catalyst, prob_news,
                   model_used, cost_usd,
                   NULL AS rank
            FROM   thesis_cache
            WHERE  date = %s
            ORDER  BY created_at DESC
            LIMIT  5
        """, (today,))
        return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        print(f"[notify] fetch_top5_thesis failed: {exc}", file=sys.stderr)
        return []


# ── Formatters ────────────────────────────────────────────────────────────────

def fmt_dir(d: str) -> str:
    return {"BULL": "▲ BULL", "BEAR": "▼ BEAR"}.get(d or "", "— NEUTRAL")


def fmt_price(v) -> str:
    f = _safe_float(v)
    return f"${f:.2f}" if f is not None else "—"


def fmt_pct(v, decimals=1) -> str:
    f = _safe_float(v)
    if f is None:
        return "—"
    sign = "+" if f >= 0 else ""
    return f"{sign}{f:.{decimals}f}%"


def fmt_prob(v) -> str:
    f = _safe_float(v)
    return f"{f*100:.0f}%" if f is not None else "—"


def fmt_ev(v) -> str:
    f = _safe_float(v)
    if f is None or f <= -999:
        return "—"
    sign = "+" if f >= 0 else ""
    return f"{sign}{f:.1f}%"


def fmt_conviction(v) -> str:
    i = _safe_int(v)
    return f"{i}/10" if i is not None else "—"


def truncate(text: str, n: int) -> str:
    if not text:
        return ""
    return text[:n] + "…" if len(text) > n else text


# ── Message builders ──────────────────────────────────────────────────────────

def build_header(status: str, workflow: str, run_url: str, duration_min: float) -> str:
    icon = {"success": "✅", "failure": "❌", "cancelled": "🚫"}.get(status, "⚠️")
    today = date.today().strftime("%Y-%m-%d")
    dur   = f"{duration_min:.1f} min" if duration_min else ""
    lines = [
        f"{icon} <b>Signal Engine — {today}</b>",
        f"<b>{workflow}</b> · {status.upper()}{' · ' + dur if dur else ''}",
    ]
    if run_url:
        lines.append(f'<a href="{run_url}">View run on GitHub</a>')
    return "\n".join(lines)


def build_top10_section(rows: list[dict]) -> str:
    if not rows:
        return "⚠️ No ranking data for today."

    lines = ["\n<b>── TOP 10 RANKINGS ──</b>"]
    for r in rows:
        rank     = r.get("rank", "?")
        ticker   = r.get("ticker", "?")
        direction = r.get("direction", "NEUTRAL")
        ev       = fmt_ev(r.get("ev_t1_pct"))
        prob     = fmt_prob(r.get("prob_combined") or r.get("prob_t1"))
        t1       = fmt_price(r.get("t1_price"))
        stop     = fmt_price(r.get("stop_price"))
        open_flag = " 🔓" if r.get("is_open_position") else ""
        dir_icon = "▲" if direction == "BULL" else ("▼" if direction == "BEAR" else "—")

        line = (
            f"<code>#{rank:>2} {ticker:<5} {dir_icon} {direction:<4} "
            f"EV {ev:<7} P {prob:<5} T1 {t1:<8} Stop {stop}</code>"
            f"{open_flag}"
        )
        lines.append(line)

    lines.append(
        "\n<i>EV=expected value to T1 | P=prob_combined | 🔓=open position</i>"
    )
    return "\n".join(lines)


def build_thesis_section(rows: list[dict]) -> str:
    if not rows:
        return ""

    total_cost = sum(_safe_float(r.get("cost_usd")) or 0.0 for r in rows)

    lines = ["\n<b>── AI DEEP DIVE — Top 5 ──</b>"]
    for r in rows:
        rank      = r.get("rank")
        ticker    = r.get("ticker", "?")
        direction = r.get("direction", "NEUTRAL")
        conv      = fmt_conviction(r.get("conviction"))
        entry_low = fmt_price(r.get("entry_low"))
        entry_hi  = fmt_price(r.get("entry_high"))
        stop      = fmt_price(r.get("stop_loss"))
        t1        = fmt_price(r.get("target_1"))
        t2        = fmt_price(r.get("target_2"))
        thesis    = truncate(r.get("thesis") or "", 300)
        invalidation = truncate(r.get("key_invalidation") or "", 120)
        pc        = r.get("prob_combined")
        pt        = r.get("prob_technical")
        po        = r.get("prob_options")
        model     = (r.get("model_used") or "").split("-")[0]  # shorten e.g. "claude-sonnet-4-6" → "claude"

        rank_str  = f"#{rank} " if rank else ""
        dir_label = fmt_dir(direction)

        block = [
            f"\n<b>{rank_str}{ticker} — {dir_label}</b>  [Conv {conv}]",
            f"  Entry: {entry_low}–{entry_hi}  |  Stop: {stop}  |  T1: {t1}  |  T2: {t2}",
        ]

        # Probability breakdown if available
        prob_parts = []
        if pc is not None:
            prob_parts.append(f"P(combined): {fmt_prob(pc)}")
        if pt is not None:
            prob_parts.append(f"Tech {fmt_prob(pt)}")
        if po is not None:
            prob_parts.append(f"Opts {fmt_prob(po)}")
        if prob_parts:
            block.append("  " + "  ·  ".join(prob_parts))

        if thesis:
            block.append(f"  <i>{thesis}</i>")
        if invalidation:
            block.append(f"  ⚠ Invalidation: {invalidation}")
        if model:
            block.append(f"  <code>Model: {model}</code>")

        lines.extend(block)

    lines.append(f"\n<code>Total AI cost: ${total_cost:.4f}</code>")
    return "\n".join(lines)


# ── Squeeze lifecycle alerts (CHUNK-15) ──────────────────────────────────────

def _fetch_two_latest_squeeze_dates(conn) -> tuple[str | None, str | None]:
    """Return (latest_date, previous_date) strings from squeeze_scores, or (None, None)."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT date FROM squeeze_scores
            ORDER BY date DESC
            LIMIT 2
        """)
        rows = cur.fetchall()
        dates = [str(r["date"]) for r in rows]
        latest   = dates[0] if len(dates) >= 1 else None
        previous = dates[1] if len(dates) >= 2 else None
        return latest, previous
    except Exception as exc:
        print(f"[notify] _fetch_two_latest_squeeze_dates failed: {exc}", file=sys.stderr)
        return None, None


def _fetch_squeeze_scores_for_date(conn, run_date: str) -> list[dict]:
    """Fetch all squeeze_scores rows for *run_date*."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT date, ticker, final_score, squeeze_state,
                   risk_level, dilution_risk_flag,
                   options_pressure_score, unusual_call_activity_flag,
                   explanation_summary, explanation_json
            FROM   squeeze_scores
            WHERE  date = %s
            ORDER  BY final_score DESC NULLS LAST
        """, (run_date,))
        return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        print(f"[notify] _fetch_squeeze_scores_for_date failed: {exc}", file=sys.stderr)
        return []


def build_squeeze_alerts_section(conn) -> str:
    """
    CHUNK-15: Fetch latest + previous squeeze_scores, build lifecycle alerts,
    and return a formatted Telegram section string (empty if no alerts or
    if squeeze_alerts module is unavailable).
    """
    if not _SQUEEZE_ALERTS_AVAILABLE:
        return ""
    try:
        latest_date, previous_date = _fetch_two_latest_squeeze_dates(conn)
        if not latest_date:
            return ""

        current_rows  = _fetch_squeeze_scores_for_date(conn, latest_date)
        previous_rows = (
            _fetch_squeeze_scores_for_date(conn, previous_date)
            if previous_date else []
        )

        # Build a lookup: ticker → previous row
        prev_by_ticker = {r["ticker"]: r for r in previous_rows}

        all_alerts: list[dict] = []
        for row in current_rows:
            ticker = str(row.get("ticker") or "").upper()
            if not ticker:
                continue
            prev_row = prev_by_ticker.get(ticker)
            ticker_alerts = build_squeeze_alerts(row, prev_row)
            all_alerts.extend(ticker_alerts)

        return format_alerts_section(all_alerts)

    except Exception as exc:
        print(f"[notify] build_squeeze_alerts_section failed: {exc}", file=sys.stderr)
        return ""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Send pipeline result to Telegram")
    parser.add_argument("--status",       default="success",
                        help="success | failure | cancelled")
    parser.add_argument("--workflow",     default="Signal Pipeline",
                        help="Workflow display name")
    parser.add_argument("--run-url",      default="",
                        help="GitHub Actions run URL")
    parser.add_argument("--duration-min", type=float, default=0.0,
                        help="Elapsed minutes")
    parser.add_argument("--skip-ai",      action="store_true",
                        help="Omit AI thesis section (no AI run)")
    args = parser.parse_args()

    conn = _db_connect()

    # ── Build message ─────────────────────────────────────────────────────────
    header = build_header(
        status=args.status,
        workflow=args.workflow,
        run_url=args.run_url,
        duration_min=args.duration_min,
    )

    rankings_section = ""
    thesis_section   = ""
    squeeze_alerts_section = ""

    if conn:
        top10 = fetch_top10_rankings(conn)
        rankings_section = build_top10_section(top10)

        if not args.skip_ai:
            top5_thesis = fetch_top5_thesis(conn)
            if top5_thesis:
                thesis_section = build_thesis_section(top5_thesis)
            else:
                thesis_section = "\n<i>No AI theses written today (AI skipped or no qualifying tickers).</i>"
        else:
            thesis_section = "\n<i>AI synthesis skipped (--skip-ai run).</i>"

        # CHUNK-15: squeeze lifecycle alerts
        squeeze_alerts_section = build_squeeze_alerts_section(conn)

        conn.close()
    else:
        rankings_section = "\n⚠️ Could not connect to database — no ranking data."
        thesis_section   = ""

    full_message = "\n".join(filter(None, [
        header, rankings_section, thesis_section, squeeze_alerts_section,
    ]))

    # ── Send ──────────────────────────────────────────────────────────────────
    tg_send_chunked(full_message)
    print(f"[notify] Telegram notification sent ({len(full_message)} chars).")


if __name__ == "__main__":
    main()
