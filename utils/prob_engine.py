"""
utils/prob_engine.py — Calibrated probability engine for signal_engine_v1.

Computes prob_combined: a single weighted float (0.30–0.90) synthesising all
available signal scalars into a calibrated directional probability.

Replaces three disconnected probability proxies:
  1. signal_agreement_score (vote fraction, ai_quant.py)
  2. prob = (5 - rec_mean) / 4     (analyst consensus, quant_report.py)
  3. bull_probability               (uncalibrated LLM output)

CALIBRATION PATH: B — Fixed weights (domain knowledge)
  Auto-upgrade to PATH A (logistic regression) once ≥ 30 closed theses
  accumulate in thesis_outcomes.  check_calibration_readiness() reports
  current status.

WEIGHT RATIONALE:
  Technical (RSI)        0.30 — primary directional momentum signal
  Options (heat_score)   0.25 — options flow encodes informed money
  Fundamental score      0.15 — quality backdrop
  Catalyst (beat rate)   0.15 — base-rate for earnings-driven moves
  News sentiment         0.10 — short-term catalyst surface
  Agreement score        0.05 — tie-breaker / coherence bonus

None-safety: every input has a documented 0.50 (neutral) fallback.
A None anywhere never propagates to prob_combined.
"""

from __future__ import annotations

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step 1 — Normalization helpers
# ---------------------------------------------------------------------------

def normalize_signal(
    value: Optional[float],
    min_val: float,
    max_val: float,
) -> float:
    """
    Clip value to [min_val, max_val] and linearly rescale to [0.0, 1.0].

    Returns 0.50 (neutral) when:
      - value is None
      - value is NaN
      - min_val == max_val (degenerate range)

    Examples
    --------
    >>> normalize_signal(80, 0, 100)    # RSI 80
    0.8
    >>> normalize_signal(0.3, -1, 1)    # sentiment +0.3
    0.65
    >>> normalize_signal(None, 0, 100)  # missing → neutral
    0.5
    """
    if value is None:
        return 0.50
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.50
    if math.isnan(v) or math.isinf(v):
        return 0.50
    span = max_val - min_val
    if span == 0:
        return 0.50
    clipped = max(min_val, min(max_val, v))
    return (clipped - min_val) / span


# ---------------------------------------------------------------------------
# Step 2 — Data quality assessment
# ---------------------------------------------------------------------------

def _assess_data_quality(signals: dict) -> str:
    """
    Count how many of the 7 inputs are real (non-defaulted) values.

    Returns "HIGH" (6–7 real), "MEDIUM" (4–5 real), "LOW" (< 4 real).
    """
    real_count = 0

    tech = signals.get("technical") or {}
    if tech.get("rsi_14") is not None:
        real_count += 1

    opts = signals.get("options_flow") or {}
    if opts.get("heat_score") is not None:
        real_count += 1
    if opts.get("iv_rank") is not None:
        real_count += 1

    fund = signals.get("fundamentals") or {}
    if fund.get("fundamental_score_pct") is not None:
        real_count += 1

    evnt = signals.get("earnings_event") or {}
    if evnt.get("beat_rate_4q") is not None:
        real_count += 1

    news = signals.get("news_sentiment") or {}
    if news.get("source") == "marketaux" and news.get("articles_found", 0) > 0:
        real_count += 1

    agree = signals.get("signal_agreement_score")
    if agree is not None and agree > 0.0:
        real_count += 1

    if real_count >= 6:
        return "HIGH"
    elif real_count >= 4:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Step 2 (cont.) — prob_combined computation
# ---------------------------------------------------------------------------

def compute_prob_combined(signals: dict) -> dict:
    """
    Compute a calibrated directional probability from available signal scalars.

    Parameters
    ----------
    signals : dict
        The full signals dict returned by collect_all_signals() in ai_quant.py,
        or any subset thereof.  All inputs degrade gracefully to 0.50 (neutral)
        when the expected key is absent or None.

    Returns
    -------
    dict with keys:
        prob_combined   : float, clipped to [0.30, 0.90]
        prob_technical  : float [0, 1]  — RSI normalized
        prob_options    : float [0, 1]  — heat_score / 100
        prob_catalyst   : float [0, 1]  — beat_rate_4q (0.50 default)
        prob_news       : float [0, 1]  — avg_sentiment normalized
        iv_contribution : float [0, 1]  — 1 − (iv_rank / 100), low IV = bullish
        data_quality    : str  — "HIGH" | "MEDIUM" | "LOW"
        inputs_used     : dict — raw extracted values before normalization

    Weights (PATH B — fixed domain knowledge):
        technical   0.30
        options     0.25
        fundamental 0.15
        catalyst    0.15
        news        0.10
        agreement   0.05
    """
    # ── Extract scalars with None-safe fallbacks ─────────────────────────────
    tech  = signals.get("technical")  or {}
    opts  = signals.get("options_flow") or {}
    fund  = signals.get("fundamentals") or {}
    evnt  = signals.get("earnings_event") or {}
    news  = signals.get("news_sentiment") or {}

    rsi_raw       = tech.get("rsi_14")
    heat_raw      = opts.get("heat_score")
    fund_raw      = fund.get("fundamental_score_pct")
    beat_raw      = evnt.get("beat_rate_4q")
    news_raw      = news.get("avg_sentiment", 0.0)
    agree_raw     = signals.get("signal_agreement_score")
    iv_raw        = opts.get("iv_rank")

    # ── Normalize each to [0, 1] ─────────────────────────────────────────────
    # Technical: RSI 0–100 → 0–1  (RSI 50 neutral = 0.50)
    tech_norm  = normalize_signal(rsi_raw, 0.0, 100.0)

    # Options: heat_score 0–100 → 0–1  (heat 50 neutral = 0.50)
    opts_norm  = normalize_signal(heat_raw, 0.0, 100.0) if heat_raw is not None else 0.50

    # Fundamental: 0–100 → 0–1
    fund_norm  = normalize_signal(fund_raw, 0.0, 100.0) if fund_raw is not None else 0.50

    # Catalyst: beat_rate_4q already [0, 1]; None → 0.50
    cat_norm   = float(beat_raw) if beat_raw is not None else 0.50
    cat_norm   = max(0.0, min(1.0, cat_norm))

    # News: avg_sentiment [-1, +1] → [0, 1]  (neutral 0 = 0.50)
    news_norm  = normalize_signal(news_raw, -1.0, 1.0)

    # Agreement score: already [0, 1]; None → 0.50
    agree_norm = float(agree_raw) if agree_raw is not None else 0.50
    agree_norm = max(0.0, min(1.0, agree_norm))

    # IV contribution: low IV is bullish for option-based entries → invert
    # iv_rank None (< 5 snapshots) → treat as neutral (50 → 0.50 contribution)
    iv_norm    = normalize_signal(iv_raw, 0.0, 100.0) if iv_raw is not None else 0.50
    iv_contrib = 1.0 - iv_norm  # low rank → high bullish contribution

    # ── Weighted sum ─────────────────────────────────────────────────────────
    prob_raw = (
        tech_norm  * 0.30 +
        opts_norm  * 0.25 +
        fund_norm  * 0.15 +
        cat_norm   * 0.15 +
        news_norm  * 0.10 +
        agree_norm * 0.05
    )

    # Clip to [0.30, 0.90] — no extreme values, meaningful for Kelly
    prob_combined = round(max(0.30, min(0.90, prob_raw)), 3)

    return {
        "prob_combined":   prob_combined,
        "prob_technical":  round(tech_norm, 3),
        "prob_options":    round(opts_norm, 3),
        "prob_catalyst":   round(cat_norm, 3),
        "prob_news":       round(news_norm, 3),
        "iv_contribution": round(iv_contrib, 3),
        "data_quality":    _assess_data_quality(signals),
        "inputs_used": {
            "rsi_14":               rsi_raw,
            "heat_score":           heat_raw,
            "fundamental_score_pct": fund_raw,
            "beat_rate_4q":         beat_raw,
            "avg_sentiment":        news_raw,
            "signal_agreement_score": agree_raw,
            "iv_rank":              iv_raw,
        },
    }


# ---------------------------------------------------------------------------
# Calibration readiness check
# ---------------------------------------------------------------------------

def check_calibration_readiness() -> dict:
    """
    Query thesis_outcomes to check if logistic regression (PATH A) is viable.

    Returns
    -------
    dict:
        closed_count      : int   — theses with resolved outcome
        path_a_viable     : bool  — True if >= 30 closed theses
        theses_needed     : int   — how many more to reach PATH A threshold
        message           : str
    """
    try:
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from utils.db import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS n FROM thesis_outcomes "
                "WHERE outcome NOT IN ('OPEN', 'PENDING') AND outcome IS NOT NULL"
            )
            row = cur.fetchone()
            closed = int(row["n"]) if row else 0
        finally:
            conn.close()

        viable = closed >= 30
        needed = max(0, 30 - closed)
        msg = (
            f"PATH A ready ({closed} closed theses)."
            if viable
            else f"PATH B active — {closed}/30 closed theses. {needed} more needed for regression."
        )
        return {
            "closed_count":   closed,
            "path_a_viable":  viable,
            "theses_needed":  needed,
            "message":        msg,
        }
    except Exception as exc:
        logger.warning("check_calibration_readiness: DB unavailable — %s", exc)
        return {
            "closed_count":  0,
            "path_a_viable": False,
            "theses_needed": 30,
            "message":       f"DB unavailable: {exc}",
        }


# ---------------------------------------------------------------------------
# Empirical win rate (calibration audit, read-only)
# ---------------------------------------------------------------------------

def compute_empirical_win_rate(print_table: bool = True) -> list:
    """
    Query thesis_outcomes JOIN thesis_cache to check how well prob_combined
    predicts actual win rate across probability buckets.

    Groups by prob_combined bucket (0.30–0.40, 0.40–0.50, ...).
    Only meaningful once prob_combined is stored in thesis_cache (Step 5).
    Requires the prob_combined column to exist in thesis_cache.

    Parameters
    ----------
    print_table : bool — if True, prints the calibration table to stdout

    Returns
    -------
    list of dicts: [{bucket, n, wins, win_rate, calibration_gap}, ...]
    """
    try:
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from utils.db import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    ROUND(c.prob_combined::numeric, 1) AS bucket,
                    COUNT(*)                           AS n,
                    SUM(CASE WHEN o.claude_correct = 1 THEN 1 ELSE 0 END) AS wins
                FROM thesis_outcomes o
                JOIN thesis_cache c ON o.thesis_id = c.id
                WHERE o.outcome NOT IN ('OPEN', 'PENDING')
                  AND o.outcome IS NOT NULL
                  AND c.prob_combined IS NOT NULL
                GROUP BY ROUND(c.prob_combined::numeric, 1)
                ORDER BY bucket
            """)
            rows = cur.fetchall()
        finally:
            conn.close()

        results = []
        for row in rows:
            bucket   = float(row["bucket"])
            n        = int(row["n"])
            wins     = int(row["wins"])
            win_rate = wins / n if n > 0 else None
            cal_gap  = (win_rate - bucket) if win_rate is not None else None
            results.append({
                "bucket":          bucket,
                "n":               n,
                "wins":            wins,
                "win_rate":        round(win_rate, 3) if win_rate is not None else None,
                "calibration_gap": round(cal_gap, 3)  if cal_gap  is not None else None,
            })

        if print_table and results:
            print("\n  ── prob_combined calibration audit ──────────────────────")
            print(f"  {'Bucket':<8}  {'n':>4}  {'Win%':>6}  {'Cal gap':>8}  Status")
            print("  " + "-" * 46)
            for r in results:
                wr_str  = f"{r['win_rate']*100:.0f}%" if r["win_rate"] is not None else "n/a"
                gap_str = f"{r['calibration_gap']:+.2f}"  if r["calibration_gap"] is not None else "n/a"
                low_n   = " ⚠ low-n" if r["n"] < 10 else ""
                print(f"  {r['bucket']:.1f}–{r['bucket']+0.1:.1f}    {r['n']:>4}  {wr_str:>6}  {gap_str:>8}{low_n}")
            print()
        elif print_table:
            print("  No calibration data yet (prob_combined not yet stored or no closed theses).")

        return results

    except Exception as exc:
        logger.warning("compute_empirical_win_rate: %s", exc)
        if print_table:
            print(f"  Calibration audit unavailable: {exc}")
        return []
