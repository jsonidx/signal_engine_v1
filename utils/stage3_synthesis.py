"""
utils/stage3_synthesis.py — Stage 3 Claude Synthesis for Setup Watchlist  (TRD-038)

Bounded LLM enrichment after deterministic Stage 1/2 filtering.

Contract
--------
- Input:  up to MAX_NAMES_PER_DAY Stage 2–cleared setup rows (structured dict)
- Output: structured JSON with exactly: archetype, invalidation_condition,
          setup_grade, key_risk — nothing else.
- Claude CANNOT rank, override scores, or produce price targets.
- Failure-safe: if the model call fails or returns malformed output,
  the deterministic scores are retained intact and Stage 3 fields are NULL.

Allowed output schema (per ticker)
-----------------------------------
{
  "ticker": str,
  "archetype": str,                  # e.g. "coiling_sector_laggard", "base_breakout"
  "invalidation_condition": str,     # e.g. "close below 20d low"
  "setup_grade": "A" | "B" | "C",   # quality classification
  "key_risk": str                    # e.g. "earnings in 5 days"
}

Usage
-----
    from utils.stage3_synthesis import run_stage3_synthesis
    results = run_stage3_synthesis(shortlist_rows)
    # results: list of dicts with Stage 3 fields; failed rows have nulls
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

MAX_NAMES_PER_DAY = 10
ALLOWED_GRADES = {"A", "B", "C"}

_SYSTEM_PROMPT = """
You are a quantitative research assistant analyzing pre-breakout equity setups.
Your ONLY job is to classify each setup into a structured schema.

Rules:
- Do NOT rank setups against each other.
- Do NOT produce price targets, stop levels, or position sizes.
- Do NOT recommend buying or selling anything.
- Return ONLY valid JSON — no prose, no markdown, no explanation outside the JSON.
- For each ticker, output exactly the four fields: archetype, invalidation_condition, setup_grade, key_risk.
- setup_grade must be exactly one of: A, B, C
- Keep all text fields under 120 characters.
""".strip()

_USER_PROMPT_TEMPLATE = """
Classify the following pre-breakout setup candidates. Each has deterministic scores already computed.
Your job is only to add the four structured classification fields.

Setup rows:
{setup_json}

Return a JSON array with one object per ticker:
[
  {{
    "ticker": "<TICKER>",
    "archetype": "<short label describing the setup type>",
    "invalidation_condition": "<one-line condition that would negate this setup>",
    "setup_grade": "<A|B|C>",
    "key_risk": "<primary risk factor in one short phrase>"
  }},
  ...
]
"""

_ALLOWED_OUTPUT_KEYS = {"ticker", "archetype", "invalidation_condition", "setup_grade", "key_risk"}


def _sanitize_row(row: dict) -> dict:
    """Strip Stage 3 fields and sensitive info before sending to Claude."""
    return {
        "ticker": str(row.get("ticker", "")).upper(),
        "composite_score": round(float(row.get("composite_score", 0)), 3),
        "pfs_score": round(float(row.get("pfs_score") or 0), 3),
        "psc_score": round(float(row.get("psc_score") or 0), 3),
        "stage2_passed": bool(row.get("stage2_passed", False)),
    }


def _parse_stage3_response(response_text: str, expected_tickers: list[str]) -> list[dict]:
    """
    Parse Claude's JSON response into validated Stage 3 field dicts.

    On any parse error, returns a list of null-field dicts for all expected tickers.
    """
    null_result = [
        {"ticker": t, "archetype": None, "invalidation_condition": None,
         "setup_grade": None, "key_risk": None}
        for t in expected_tickers
    ]
    try:
        # Strip markdown code fences if present
        text = response_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        parsed = json.loads(text)
        if not isinstance(parsed, list):
            logger.warning("stage3: expected JSON array, got %s", type(parsed))
            return null_result

        results = []
        ticker_set = {t.upper() for t in expected_tickers}
        seen = set()
        for item in parsed:
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker", "")).upper()
            if ticker not in ticker_set or ticker in seen:
                continue
            seen.add(ticker)

            grade = str(item.get("setup_grade", "")).upper()
            if grade not in ALLOWED_GRADES:
                grade = "C"  # safe default

            results.append({
                "ticker": ticker,
                "archetype": str(item.get("archetype", ""))[:120] or None,
                "invalidation_condition": str(item.get("invalidation_condition", ""))[:120] or None,
                "setup_grade": grade,
                "key_risk": str(item.get("key_risk", ""))[:120] or None,
            })

        # Fill in missing tickers with nulls
        found = {r["ticker"] for r in results}
        for t in expected_tickers:
            if t.upper() not in found:
                results.append({
                    "ticker": t.upper(), "archetype": None,
                    "invalidation_condition": None, "setup_grade": None, "key_risk": None,
                })

        return results

    except Exception as exc:
        logger.warning("stage3: JSON parse failed (%s) — returning nulls", exc)
        return null_result


def run_stage3_synthesis(
    shortlist: list[dict],
    model: str = "claude-sonnet-4-6",
    dry_run: bool = False,
) -> list[dict]:
    """
    Run Stage 3 Claude synthesis on the setup-watchlist shortlist.

    Parameters
    ----------
    shortlist : list of setup_watchlist row dicts; must have cleared stage2_passed=True
    model     : Anthropic model ID
    dry_run   : if True, skip the API call and return null-field dicts (for testing)

    Returns list of dicts with Stage 3 fields: ticker, archetype, invalidation_condition,
    setup_grade, key_risk. One dict per input row. Failed/null rows have None values.
    """
    if not shortlist:
        return []

    # Enforce daily cap
    if len(shortlist) > MAX_NAMES_PER_DAY:
        logger.warning(
            "stage3: shortlist has %d names, capping at %d",
            len(shortlist), MAX_NAMES_PER_DAY,
        )
        shortlist = sorted(shortlist, key=lambda r: float(r.get("composite_score", 0)), reverse=True)
        shortlist = shortlist[:MAX_NAMES_PER_DAY]

    expected_tickers = [str(r.get("ticker", "")).upper() for r in shortlist]
    sanitized = [_sanitize_row(r) for r in shortlist]

    null_results = [
        {"ticker": t, "archetype": None, "invalidation_condition": None,
         "setup_grade": None, "key_risk": None}
        for t in expected_tickers
    ]

    if dry_run:
        return null_results

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("stage3: ANTHROPIC_API_KEY not set — skipping synthesis")
        return null_results

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        setup_json = json.dumps(sanitized, indent=2)
        user_msg = _USER_PROMPT_TEMPLATE.format(setup_json=setup_json)

        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        response_text = response.content[0].text if response.content else ""
        return _parse_stage3_response(response_text, expected_tickers)

    except Exception as exc:
        logger.warning("stage3: API call failed (%s) — returning nulls", exc)
        return null_results
