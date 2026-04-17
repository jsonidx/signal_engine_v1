#!/usr/bin/env python3
"""
scripts/telegram_bot.py — Telegram command bot for Signal Engine.

Commands:
  /run              — trigger daily_pipeline (--skip-ai, €0.00)
  /run full         — trigger full pipeline with AI synthesis (~€0.03)
  /analyze TSLA     — fresh signals + AI thesis for one ticker
  /analyze TSLA NVDA AMD  — deep dive for multiple tickers
  /status           — show last GitHub Actions run result
  /help             — list commands

Run:
  python3 scripts/telegram_bot.py

Keep running after logout:
  nohup python3 scripts/telegram_bot.py > logs/telegram_bot.log 2>&1 &
"""

import os
import sys
import time
import logging
import threading
import requests
from datetime import datetime, timezone
from pathlib import Path

# ── Load .env from project root ───────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
# Add project root to path so ai_quant imports work
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_env_path = _ROOT / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID     = os.environ.get("TELEGRAM_CHAT_ID", "")
GH_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
GH_REPO     = os.environ.get("GITHUB_REPO", "jsonidx/signal_engine_v1")
WORKFLOW_ID          = "daily_pipeline.yml"
WORKFLOW_ID_MANUAL   = "manual_pipeline.yml"
WORKFLOW_ID_ANALYZE  = "analyze_tickers.yml"

POLL_INTERVAL   = 3    # seconds between Telegram polls
GH_POLL_WAIT    = 20   # seconds between GitHub run status checks
GH_TIMEOUT      = 1800 # max seconds to wait for a pipeline run (30 min)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Telegram helpers ──────────────────────────────────────────────────────────
TG_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def tg_send(text: str, chat_id: str = CHAT_ID) -> None:
    """Send a message to the configured chat. Retries once on failure."""
    for attempt in range(2):
        try:
            r = requests.post(
                f"{TG_BASE}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=15,
            )
            if r.status_code == 200:
                return
            log.warning("tg_send HTTP %d: %s", r.status_code, r.text[:200])
        except Exception as exc:
            log.warning("tg_send attempt %d failed: %s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(3)


def tg_get_updates(offset: int) -> list:
    """Long-poll for new messages."""
    try:
        r = requests.get(
            f"{TG_BASE}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=40,
        )
        r.raise_for_status()
        return r.json().get("result", [])
    except Exception as exc:
        log.warning("getUpdates failed: %s", exc)
        return []

# ── GitHub Actions helpers ────────────────────────────────────────────────────
GH_HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
GH_API = f"https://api.github.com/repos/{GH_REPO}"


def gh_trigger(skip_ai: bool = True, manual_ai: bool = False) -> bool:
    """Dispatch a workflow. Returns True on success."""
    if manual_ai:
        url = f"{GH_API}/actions/workflows/{WORKFLOW_ID_MANUAL}/dispatches"
        payload = {"ref": "main", "inputs": {"confirm": "true"}}
    else:
        url = f"{GH_API}/actions/workflows/{WORKFLOW_ID}/dispatches"
        payload = {"ref": "main", "inputs": {"skip_ai": "true" if skip_ai else "false"}}
    try:
        r = requests.post(url, json=payload, headers=GH_HEADERS, timeout=15)
        if r.status_code == 204:
            log.info("gh_trigger OK — workflow dispatched (skip_ai=%s)", skip_ai)
            return True
        log.error("gh_trigger HTTP %d: %s", r.status_code, r.text)
        return False
    except Exception as exc:
        log.error("gh_trigger failed: %s", exc)
        return False


def gh_latest_run(after_ts: float, workflow: str = WORKFLOW_ID) -> dict | None:
    """Return the most recent workflow_dispatch run created after after_ts."""
    url = f"{GH_API}/actions/workflows/{workflow}/runs"
    try:
        r = requests.get(
            url,
            params={"event": "workflow_dispatch", "per_page": 5},
            headers=GH_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        for run in r.json().get("workflow_runs", []):
            created = datetime.fromisoformat(
                run["created_at"].replace("Z", "+00:00")
            ).timestamp()
            if created >= after_ts:
                return run
    except Exception as exc:
        log.warning("gh_latest_run failed: %s", exc)
    return None


def gh_run_status(run_id: int) -> dict:
    """Return status/conclusion for a specific run."""
    try:
        r = requests.get(
            f"{GH_API}/actions/runs/{run_id}",
            headers=GH_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.warning("gh_run_status failed: %s", exc)
        return {}


def gh_run_jobs_summary(run_id: int) -> str:
    """Return a short text summary of job steps."""
    try:
        r = requests.get(
            f"{GH_API}/actions/runs/{run_id}/jobs",
            headers=GH_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
        lines = []
        for job in jobs:
            for step in job.get("steps", []):
                name = step["name"]
                conclusion = step.get("conclusion") or step.get("status", "")
                icon = {"success": "✅", "failure": "❌", "skipped": "⏭", "in_progress": "⏳"}.get(conclusion, "•")
                lines.append(f"{icon} {name}")
        return "\n".join(lines) if lines else "No step details available."
    except Exception as exc:
        return f"Could not fetch step details: {exc}"


def wait_for_run(skip_ai: bool, manual_ai: bool = False) -> None:
    """
    Trigger, wait for completion, send Telegram updates.
    Runs in the main thread (blocking); call from a thread in production.
    """
    if manual_ai:
        mode = "full AI run via xAI/Grok (~€0.03–0.05)"
    elif skip_ai:
        mode = "data-only (--skip-ai, €0.00)"
    else:
        mode = "full run with AI (~€0.03)"
    tg_send(f"🚀 <b>Pipeline triggered</b> — {mode}\nWaiting for GitHub Actions to start…")

    trigger_ts = time.time() - 5  # slight buffer for clock skew
    if not gh_trigger(skip_ai, manual_ai=manual_ai):
        tg_send("❌ Failed to trigger workflow. Check GITHUB_TOKEN permissions.")
        return

    # Wait for the run to appear
    workflow = WORKFLOW_ID_MANUAL if manual_ai else WORKFLOW_ID
    run = None
    for _ in range(30):
        time.sleep(5)
        run = gh_latest_run(trigger_ts, workflow)
        if run:
            break

    if not run:
        tg_send("⚠️ Run triggered but couldn't find it in GitHub — check Actions tab.")
        return

    run_id  = run["id"]
    run_url = run["html_url"]
    tg_send(f"⚙️ <b>Run started</b> — <a href='{run_url}'>View on GitHub</a>")

    # Poll until complete or timeout
    waited = 0
    while waited < GH_TIMEOUT:
        time.sleep(GH_POLL_WAIT)
        waited += GH_POLL_WAIT
        data = gh_run_status(run_id)
        status     = data.get("status", "unknown")
        conclusion = data.get("conclusion")

        if status == "completed":
            icon = {"success": "✅", "failure": "❌", "cancelled": "🚫"}.get(conclusion, "⚠️")
            elapsed = round((time.time() - trigger_ts) / 60, 1)
            summary = gh_run_jobs_summary(run_id)
            tg_send(
                f"{icon} <b>Pipeline {conclusion.upper()}</b> — {elapsed} min\n\n"
                f"<b>Steps:</b>\n{summary}\n\n"
                f"<a href='{run_url}'>Full logs on GitHub</a>"
            )
            return

    tg_send(f"⏰ Timed out waiting for run after {GH_TIMEOUT//60} min.\n{run_url}")


# ── Analyze helpers ───────────────────────────────────────────────────────────

def gh_trigger_analyze(tickers: list[str]) -> bool:
    """Dispatch analyze_tickers.yml with the given tickers. Returns True on success."""
    ticker_str = " ".join(tickers)
    try:
        r = requests.post(
            f"{GH_API}/actions/workflows/{WORKFLOW_ID_ANALYZE}/dispatches",
            json={"ref": "main", "inputs": {"tickers": ticker_str}},
            headers=GH_HEADERS,
            timeout=15,
        )
        if r.status_code == 204:
            log.info("gh_trigger_analyze OK — tickers=%s", ticker_str)
            return True
        log.error("gh_trigger_analyze HTTP %d: %s", r.status_code, r.text)
        return False
    except Exception as exc:
        log.error("gh_trigger_analyze failed: %s", exc)
        return False


# ── Command router ────────────────────────────────────────────────────────────
HELP_TEXT = (
    "📡 <b>Signal Engine Bot</b>\n\n"
    "/run — trigger pipeline (data only, €0.00)\n"
    "/run full — trigger daily pipeline with AI (~€0.03)\n"
    "/run ai — trigger manual pipeline, full xAI/Grok run (~€0.03–0.05)\n"
    "/analyze TSLA — deep dive: fresh signals + AI thesis for one ticker\n"
    "/analyze TSLA NVDA AMD — deep dive for multiple tickers\n"
    "/status — last pipeline run result\n"
    "/help — show this message"
)


def handle_command(text: str, chat_id: str) -> None:
    text = text.strip()
    lower = text.lower()

    if lower.startswith("/help"):
        tg_send(HELP_TEXT, chat_id)

    elif lower.startswith("/analyze"):
        parts = text.split()[1:]  # everything after /analyze
        tickers = [p.upper() for p in parts if p.isalpha()]
        if not tickers:
            tg_send("Usage: /analyze TSLA\n       /analyze TSLA NVDA AMD", chat_id)
            return
        ticker_str = " ".join(tickers)
        if gh_trigger_analyze(tickers):
            tg_send(
                f"🔍 <b>Deep dive triggered</b> — {ticker_str}\n"
                f"Running on GitHub Actions (~2–4 min per ticker).\n"
                f"Results will arrive here when done.",
                chat_id,
            )
        else:
            tg_send("❌ Failed to trigger analyze workflow. Check GITHUB_TOKEN permissions.", chat_id)

    elif lower.startswith("/run"):
        if "ai" in lower.split():
            t = threading.Thread(target=wait_for_run, args=(False,), kwargs={"manual_ai": True}, daemon=True)
        else:
            full = "full" in lower
            t = threading.Thread(target=wait_for_run, args=(not full,), daemon=True)
        t.start()

    elif lower.startswith("/status"):
        try:
            r = requests.get(
                f"{GH_API}/actions/workflows/{WORKFLOW_ID}/runs",
                params={"per_page": 1},
                headers=GH_HEADERS,
                timeout=15,
            )
            runs = r.json().get("workflow_runs", [])
            if not runs:
                tg_send("No runs found.", chat_id)
                return
            run = runs[0]
            status     = run.get("status", "?")
            conclusion = run.get("conclusion") or status
            icon = {"success": "✅", "failure": "❌", "cancelled": "🚫", "in_progress": "⏳"}.get(conclusion, "•")
            created = run["created_at"][:16].replace("T", " ")
            tg_send(
                f"{icon} <b>Last run:</b> {conclusion.upper()}\n"
                f"Started: {created} UTC\n"
                f"<a href='{run['html_url']}'>View on GitHub</a>",
                chat_id,
            )
        except Exception as exc:
            tg_send(f"❌ Could not fetch status: {exc}", chat_id)

    else:
        tg_send(f"Unknown command. Type /help for the list.", chat_id)


# ── Main loop ─────────────────────────────────────────────────────────────────
def main() -> None:
    if not BOT_TOKEN or not CHAT_ID or not GH_TOKEN:
        log.error("Missing TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, or GITHUB_TOKEN in .env")
        sys.exit(1)

    log.info("Signal Engine Telegram bot started. Listening for commands…")
    tg_send("🤖 <b>Signal Engine Bot online</b>\nType /help for commands.")

    offset = 0
    while True:
        updates = tg_get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message") or update.get("edited_message")
            if not msg:
                continue
            text    = msg.get("text", "")
            chat_id = str(msg["chat"]["id"])
            # Only respond to the configured chat
            if chat_id != str(CHAT_ID):
                log.info("Ignored message from unknown chat %s", chat_id)
                continue
            if text.startswith("/"):
                log.info("Command from %s: %s", chat_id, text)
                handle_command(text, chat_id)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
