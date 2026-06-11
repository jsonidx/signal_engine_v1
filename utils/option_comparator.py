"""
Option Target Calibration and Legacy Comparator  (TRD-044)
================================================================================
Pure-Python comparator for measuring whether v2 delta-projected option targets
are more accurate than the legacy flat-multiplier targets.

Comparison dimensions:
  legacy     — option_take_profit_1/2 (mid × 1.50 / 2.00, flat multipliers)
  v2         — projected_option_tp1/2 (delta-projected from underlying thesis)
  underlying — underlying_target_1/2 (thesis-level exits, asset-price only)

Metrics per method group:
  tp1_hit_rate     % of resolved rows where TP1 was hit
  tp2_hit_rate     % of resolved rows where TP2 was hit
  stop_hit_rate    % of resolved rows where stop was hit
  mean_return_pct  mean option_return_5d_pct across the cohort
  median_return_pct  median of the same

Cohort breakdowns:
  by_preset        strategy_preset (long_call, leaps_call, …)
  by_delta_bucket  OTM / ATM / ITM
  by_dte_bucket    ≤21d / 22–45d / 46–90d / >90d

Sparse-cohort policy:
  Any cohort with n < MIN_COHORT_SIZE gets a `sparse` flag and rates are
  returned as None rather than misleading small-sample percentages.

Input format:
  Each row in the input list is a merged dict containing snapshot + outcome
  columns:
    # from option_candidate_snapshots (snapshot columns):
    strategy_preset, delta, dte, direction,
    option_take_profit_1, option_take_profit_2, option_stop_loss,
    projected_option_tp1, projected_option_tp2, projected_option_stop,
    underlying_target_1, underlying_target_2, underlying_stop,
    target_projection_method
    # from option_candidate_outcomes (outcome columns):
    resolution_type, option_return_5d_pct,
    hit_option_tp1, hit_option_tp2, hit_option_stop,
    hit_v2_tp1, hit_v2_tp2, hit_v2_stop,
    hit_underlying_t1, hit_underlying_t2, hit_underlying_stop

This module is free of DB dependencies — it operates on dicts produced by
callers (the API endpoint, tests, etc.).  No LLM calls, no orders.
================================================================================
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import List, Optional

MIN_COHORT_SIZE = 5   # below this, percentages are suppressed to avoid noise

# V2-capable method labels (rows where v2 targets were successfully computed)
_V2_METHODS = {"delta_only", "delta_dte_adjusted"}


# ══════════════════════════════════════════════════════════════════════════════
# Output schemas
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class MethodStats:
    """Performance metrics for one target method."""
    method: str                        # "legacy" | "v2" | "underlying"
    n: int                             # sample size
    tp1_hit_rate: Optional[float]      # % (0–100); None if sparse
    tp2_hit_rate: Optional[float]
    stop_hit_rate: Optional[float]
    mean_return_pct: Optional[float]
    median_return_pct: Optional[float]
    sparse: bool = False               # True when n < MIN_COHORT_SIZE
    note: str = ""


@dataclass
class CohortComparison:
    """One cohort slice across all three methods."""
    dimension: str            # "preset" | "delta_bucket" | "dte_bucket"
    cohort_label: str         # e.g., "long_call" or "ATM (0.30-0.45)"
    n: int                    # rows in this cohort (5d resolution)
    legacy: MethodStats
    v2: MethodStats
    underlying: MethodStats
    sparse: bool = False


@dataclass
class MethodComparison:
    """Full comparator output for a batch of rows."""
    total_rows: int
    v2_eligible_rows: int      # rows with a v2 projection (method != insufficient)
    resolution_type: str       # which resolution window the rates are based on
    overall_legacy: MethodStats
    overall_v2: MethodStats
    overall_underlying: MethodStats
    by_preset: List[CohortComparison] = field(default_factory=list)
    by_delta_bucket: List[CohortComparison] = field(default_factory=list)
    by_dte_bucket: List[CohortComparison] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════


def _safe_rate(hits: list[Optional[bool]]) -> Optional[float]:
    """Return hit rate % from a list of booleans; None values are excluded."""
    valid = [h for h in hits if h is not None]
    if not valid:
        return None
    return round(sum(1 for h in valid if h) / len(valid) * 100.0, 1)


def _safe_mean(values: list[Optional[float]]) -> Optional[float]:
    valid = [v for v in values if v is not None]
    if not valid:
        return None
    return round(statistics.mean(valid), 2)


def _safe_median(values: list[Optional[float]]) -> Optional[float]:
    valid = [v for v in values if v is not None]
    if not valid:
        return None
    return round(statistics.median(valid), 2)


def _method_stats_for_rows(rows: list[dict], method: str) -> MethodStats:
    """
    Compute MethodStats for *rows* using the hit fields for *method*.

    method: "legacy" | "v2" | "underlying"
    """
    n = len(rows)
    sparse = n < MIN_COHORT_SIZE

    if method == "legacy":
        tp1  = [r.get("hit_option_tp1")  for r in rows]
        tp2  = [r.get("hit_option_tp2")  for r in rows]
        stop = [r.get("hit_option_stop") for r in rows]
    elif method == "v2":
        tp1  = [r.get("hit_v2_tp1")  for r in rows]
        tp2  = [r.get("hit_v2_tp2")  for r in rows]
        stop = [r.get("hit_v2_stop") for r in rows]
    elif method == "underlying":
        tp1  = [r.get("hit_underlying_t1")   for r in rows]
        tp2  = [r.get("hit_underlying_t2")   for r in rows]
        stop = [r.get("hit_underlying_stop") for r in rows]
    else:
        tp1 = tp2 = stop = []

    returns = [r.get("option_return_5d_pct") for r in rows]

    if sparse:
        note = f"sparse (n={n} < {MIN_COHORT_SIZE})"
        return MethodStats(
            method=method, n=n,
            tp1_hit_rate=None, tp2_hit_rate=None, stop_hit_rate=None,
            mean_return_pct=None, median_return_pct=None,
            sparse=True, note=note,
        )

    return MethodStats(
        method=method, n=n,
        tp1_hit_rate=_safe_rate(tp1),
        tp2_hit_rate=_safe_rate(tp2),
        stop_hit_rate=_safe_rate(stop),
        mean_return_pct=_safe_mean(returns),
        median_return_pct=_safe_median(returns),
        sparse=False,
    )


def _delta_bucket(delta: Optional[float]) -> str:
    if delta is None:
        return "Unknown"
    abs_d = abs(delta)
    if abs_d < 0.30:
        return "OTM (<0.30)"
    if abs_d <= 0.45:
        return "ATM (0.30-0.45)"
    return "ITM (>0.45)"


def _dte_bucket(dte: Optional[int]) -> str:
    if dte is None:
        return "Unknown"
    if dte <= 21:
        return "≤21d"
    if dte <= 45:
        return "22-45d"
    if dte <= 90:
        return "46-90d"
    return ">90d"


def _cohort_breakdown(
    rows: list[dict],
    dimension: str,
    key_fn,
) -> list[CohortComparison]:
    """Group rows by key_fn and produce one CohortComparison per group."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        k = key_fn(r) or "Unknown"
        groups.setdefault(k, []).append(r)

    result = []
    for label, group_rows in sorted(groups.items()):
        n = len(group_rows)
        v2_rows = [r for r in group_rows if r.get("target_projection_method") in _V2_METHODS]
        result.append(CohortComparison(
            dimension=dimension,
            cohort_label=label,
            n=n,
            legacy=_method_stats_for_rows(group_rows, "legacy"),
            v2=_method_stats_for_rows(v2_rows, "v2"),
            underlying=_method_stats_for_rows(group_rows, "underlying"),
            sparse=(n < MIN_COHORT_SIZE),
        ))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════


def compare_methods(
    rows: list[dict],
    resolution_type: str = "5d",
) -> MethodComparison:
    """
    Build a full method comparison from a list of merged snapshot+outcome dicts.

    Parameters
    ----------
    rows             : merged rows, one per resolved outcome (any resolution type)
    resolution_type  : which window the input rows represent (informational)

    Returns MethodComparison.  Never raises.
    """
    try:
        total = len(rows)
        v2_eligible = sum(
            1 for r in rows
            if r.get("target_projection_method") in _V2_METHODS
        )
        v2_rows = [r for r in rows if r.get("target_projection_method") in _V2_METHODS]

        overall_legacy     = _method_stats_for_rows(rows, "legacy")
        overall_v2         = _method_stats_for_rows(v2_rows, "v2")
        overall_underlying = _method_stats_for_rows(rows, "underlying")

        by_preset = _cohort_breakdown(
            rows, "preset",
            lambda r: r.get("strategy_preset"),
        )
        by_delta = _cohort_breakdown(
            rows, "delta_bucket",
            lambda r: _delta_bucket(r.get("delta")),
        )
        by_dte = _cohort_breakdown(
            rows, "dte_bucket",
            lambda r: _dte_bucket(r.get("dte")),
        )

        return MethodComparison(
            total_rows=total,
            v2_eligible_rows=v2_eligible,
            resolution_type=resolution_type,
            overall_legacy=overall_legacy,
            overall_v2=overall_v2,
            overall_underlying=overall_underlying,
            by_preset=by_preset,
            by_delta_bucket=by_delta,
            by_dte_bucket=by_dte,
        )

    except Exception:
        import logging
        logging.getLogger(__name__).exception("compare_methods failed")
        empty = _method_stats_for_rows([], "legacy")
        return MethodComparison(
            total_rows=0,
            v2_eligible_rows=0,
            resolution_type=resolution_type,
            overall_legacy=empty,
            overall_v2=_method_stats_for_rows([], "v2"),
            overall_underlying=_method_stats_for_rows([], "underlying"),
        )


def method_comparison_to_dict(mc: MethodComparison) -> dict:
    """Serialise MethodComparison to a JSON-safe dict."""

    def _stats(ms: MethodStats) -> dict:
        return {
            "method":             ms.method,
            "n":                  ms.n,
            "tp1_hit_rate":       ms.tp1_hit_rate,
            "tp2_hit_rate":       ms.tp2_hit_rate,
            "stop_hit_rate":      ms.stop_hit_rate,
            "mean_return_pct":    ms.mean_return_pct,
            "median_return_pct":  ms.median_return_pct,
            "sparse":             ms.sparse,
            "note":               ms.note,
        }

    def _cohort(cc: CohortComparison) -> dict:
        return {
            "dimension":    cc.dimension,
            "cohort_label": cc.cohort_label,
            "n":            cc.n,
            "sparse":       cc.sparse,
            "legacy":       _stats(cc.legacy),
            "v2":           _stats(cc.v2),
            "underlying":   _stats(cc.underlying),
        }

    return {
        "total_rows":        mc.total_rows,
        "v2_eligible_rows":  mc.v2_eligible_rows,
        "resolution_type":   mc.resolution_type,
        "overall_legacy":    _stats(mc.overall_legacy),
        "overall_v2":        _stats(mc.overall_v2),
        "overall_underlying": _stats(mc.overall_underlying),
        "by_preset":         [_cohort(c) for c in mc.by_preset],
        "by_delta_bucket":   [_cohort(c) for c in mc.by_delta_bucket],
        "by_dte_bucket":     [_cohort(c) for c in mc.by_dte_bucket],
    }
