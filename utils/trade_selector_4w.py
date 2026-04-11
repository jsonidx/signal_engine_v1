"""
utils/trade_selector_4w.py — 4-week trade selection layer.

Sits on top of select_top_tickers() and applies three high-value filters
that the base priority-score system lacks:

  1. Hard filters  — liquidity gate, volatility cap, event-risk exclusion
  2. Clustering    — max 2 per GICS sector + pairwise correlation < 0.65
  3. Sizing        — volatility-parity weights targeting 1.5 % portfolio vol

Usage
-----
    from utils.ticker_selector import select_top_tickers
    from utils.trade_selector_4w import select_4w_trades

    candidates = select_top_tickers(..., max_tickers=15)
    df_candidates = pd.DataFrame(candidates)

    final, rejected = select_4w_trades(
        df_candidates,
        next_event_days={"AAPL": 45, "NVDA": 12},  # optional
    )

Input columns required in candidates DataFrame
-----------------------------------------------
  ticker           str
  priority_score   float   (from select_top_tickers)

These columns are fetched via yfinance if absent:
  price            float
  adv_20d          float   20-day average dollar volume
  atr_14           float   14-day Average True Range (price units)
  hist_vol_60d     float   60-day historical vol, annualised (e.g. 0.42 = 42%)
  sector           str     GICS sector name
  mkt_cap          float   market cap in USD millions

Output
------
  final_selection  DataFrame  ticker, final_score, weight, expected_atr_pct,
                              stop_level, sector, hist_vol_60d
  rejected         DataFrame  ticker, priority_score, reason
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import norm as _norm

logger = logging.getLogger(__name__)

# ── Configurable thresholds (all exposed as function parameters) ──────────────
_ADV_MIN          = 12_000_000   # minimum 20-day average dollar volume
_PRICE_MIN        = 8.0          # minimum share price
_ATR_PCT_MAX      = 0.038        # max ATR-14 / price  (3.8 % daily)
_HIST_VOL_MAX     = 0.65         # max 60-day annualised vol  (65 %)
_EVENT_DAYS_MIN   = 28           # min calendar days to next catalyst
_MAX_PER_SECTOR   = 2            # max names per GICS sector
_CORR_MAX         = 0.65         # max pairwise 60-day return correlation
_NUM_CANDIDATES   = 15           # how many to pull from priority-score list
_NUM_FINAL        = 5            # final selection size
_TARGET_VOL       = 0.015        # target portfolio vol per name  (1.5 %)
_MAX_WEIGHT       = 0.02         # hard weight cap per name  (2 %)
_VOL_FLOOR        = 0.15         # minimum vol used in sizing (prevents outlier cascade)
_STOP_PCT         = 0.10         # trailing / hard stop (10 %)
_CORR_HISTORY_DAYS = 100         # calendar days of history for correlation


# ==============================================================================
# SWING TRADE TARGET & PROBABILITY ENGINE
# ==============================================================================

def _calc_swing_targets(
    price: float,
    atr_14: float,
    hist_vol_60d: float,
    direction: str,
    agreement: float,
    confidence: float,
) -> dict:
    """
    Calculate swing trade T1/T2 targets, stop, hold days, and hit probabilities.

    Targets are ATR-based (1.5× for T1, 3× for T2, 1× for stop).
    Probability uses a log-normal GBM model blended with signal quality:
      - Raw vol probability: P(price reaches T1 within hold_days)
      - Blended with signal agreement × confidence as a quality weight

    Returns dict with keys:
      direction, t1_price, t2_price, stop_price, prob_t1, prob_t2, hold_days
    """
    empty = {
        "direction": direction, "t1_price": None, "t2_price": None,
        "stop_price": None, "prob_t1": 0.0, "prob_t2": 0.0, "hold_days": None,
    }
    if not direction or direction == "NEUTRAL":
        return empty
    if price <= 0 or atr_14 <= 0 or hist_vol_60d <= 0:
        return empty

    daily_vol = hist_vol_60d / np.sqrt(252)
    t1_dist   = 1.5 * atr_14
    t2_dist   = 3.0 * atr_14
    stop_dist = 1.0 * atr_14
    sign      = 1 if direction == "BULL" else -1

    t1    = round(price + sign * t1_dist, 2)
    t2    = round(price + sign * t2_dist, 2)
    stop  = round(price - sign * stop_dist, 2)

    # Estimated hold days: variance scales linearly → days ≈ (target_pct/daily_vol)²
    t1_pct    = t1_dist / price
    hold_days = max(3, min(30, int(round((t1_pct / daily_vol) ** 2))))

    # GBM probability: P(net log-return > log(T1/price) in hold_days days)
    log_t1 = abs(np.log(t1 / price))
    log_t2 = abs(np.log(t2 / price))
    z1 = log_t1 / (daily_vol * np.sqrt(hold_days))
    z2 = log_t2 / (daily_vol * np.sqrt(hold_days))
    raw_p1 = float(1 - _norm.cdf(z1))
    raw_p2 = float(1 - _norm.cdf(z2))

    # Signal quality weight blended in (40% weight to signal, 60% to vol model)
    sig_quality = min(1.0, agreement * (0.5 + confidence * 0.5))
    prob_t1 = raw_p1 * 0.6 + sig_quality * 0.4
    prob_t2 = raw_p2 * 0.6 + sig_quality * 0.25  # T2 is harder to hit

    # Realistic bounds
    prob_t1 = round(min(0.85, max(0.05, prob_t1)), 3)
    prob_t2 = round(min(0.65, max(0.02, prob_t2)), 3)

    return {
        "direction":  direction,
        "t1_price":   t1,
        "t2_price":   t2,
        "stop_price": stop,
        "prob_t1":    prob_t1,
        "prob_t2":    prob_t2,
        "hold_days":  hold_days,
    }


def calc_ev_t1_pct(row) -> float:
    """
    Expected Value of the swing trade to T1, expressed as % of price.

    Formula derives from fixed 1.5:1 R:R (T1=1.5×ATR, Stop=1.0×ATR):
      EV = prob_t1 × 1.5×ATR − (1−prob_t1) × 1.0×ATR
         = ATR × (2.5 × prob_t1 − 1.0)

    The 2.5 coefficient = R:R + 1 = 1.5 + 1. Breakeven at prob_t1 = 40%.

    NOTE: EV_T1 deliberately gives high-vol stocks a boost (ATR appears in
    both GBM prob_t1 and the multiplier). This is intentional: we want the
    biggest profit opportunities, not pure statistical edge.
    Limitation 1 (vol double-counting): high-ATR names are favoured twice.
    Limitation 2 (path dependency): this is a binary "at-expiry" EV, not
    first-passage probability.
    Future upgrade: replace raw_GBM_prob with first-passage probability
    (with drift) for more realistic swing-trade P(hit T1 before stop).
    """
    direction = row.get("direction", "NEUTRAL")
    prob_t1   = row.get("prob_t1", 0.0) or 0.0
    price     = row.get("price", 0.0)   or 0.0
    atr_14    = row.get("atr_14", 0.0)  or 0.0   # note: column is atr_14 not atr14

    if direction == "NEUTRAL" or prob_t1 <= 0 or price <= 0 or atr_14 <= 0:
        return -999.0   # force to bottom of ranking

    atr_pct   = atr_14 / price
    ev_t1_pct = atr_pct * (2.5 * prob_t1 - 1.0)
    return round(ev_t1_pct * 100, 1)    # return as percent with 1 decimal


# ==============================================================================
# MARKET DATA FETCH
# ==============================================================================

def _fetch_market_data(tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Fetch per-ticker fundamentals and price history via yfinance.

    Uses yf.download() for bulk OHLCV (single HTTP call, rate-limit friendly),
    then fetches sector/mkt_cap info per-ticker with a best-effort fallback.

    Returns
    -------
    meta_df      DataFrame indexed by ticker with price, adv_20d, atr_14,
                 hist_vol_60d, sector, mkt_cap
    price_hist   DataFrame of daily close prices indexed by date
    """
    end   = datetime.today()
    start = end - timedelta(days=_CORR_HISTORY_DAYS + 10)

    # ── Bulk OHLCV download (one call for all tickers) ────────────────────────
    try:
        raw = yf.download(
            tickers,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as exc:
        logger.warning("yf.download bulk fetch failed — %s", exc)
        raw = pd.DataFrame()

    # Normalise to MultiIndex even when only one ticker is requested
    if not raw.empty and not isinstance(raw.columns, pd.MultiIndex):
        raw.columns = pd.MultiIndex.from_tuples(
            [(col, tickers[0]) for col in raw.columns]
        )

    meta_rows: list[dict] = []
    close_series: dict[str, pd.Series] = {}

    for ticker in tickers:
        try:
            if raw.empty or ticker not in raw.columns.get_level_values(1):
                logger.warning("%s: no price history in bulk download — will be rejected", ticker)
                continue

            close  = raw["Close"][ticker].dropna()
            volume = raw["Volume"][ticker].dropna()
            high   = raw["High"][ticker].dropna()
            low    = raw["Low"][ticker].dropna()

            if len(close) < 15:
                logger.warning("%s: insufficient history (%d rows) — will be rejected", ticker, len(close))
                continue

            price   = float(close.iloc[-1])
            adv_20d = float((close * volume).tail(20).mean())

            # True Range → ATR-14
            tr = pd.concat(
                [
                    high - low,
                    (high - close.shift(1)).abs(),
                    (low  - close.shift(1)).abs(),
                ],
                axis=1,
            ).max(axis=1)
            atr_14 = float(tr.tail(14).mean())

            # 60-day annualised historical vol
            ret = close.pct_change().dropna()
            hist_vol_60d = float(ret.tail(60).std() * np.sqrt(252))

            close_series[ticker] = close

            meta_rows.append(
                {
                    "ticker":       ticker,
                    "price":        price,
                    "adv_20d":      adv_20d,
                    "atr_14":       atr_14,
                    "hist_vol_60d": hist_vol_60d,
                    "sector":       "Unknown",  # filled below
                    "mkt_cap":      0.0,         # filled below
                }
            )

        except Exception as exc:
            logger.warning("%s: market data computation failed — %s", ticker, exc)

    # ── Best-effort per-ticker info for sector / mkt_cap ─────────────────────
    fetched_tickers = {r["ticker"] for r in meta_rows}
    for row in meta_rows:
        ticker = row["ticker"]
        try:
            info = yf.Ticker(ticker).info or {}
            row["sector"]  = info.get("sector", "Unknown") or "Unknown"
            row["mkt_cap"] = (info.get("marketCap") or 0) / 1e6
        except Exception:
            pass  # sector defaults to "Unknown", not a hard-filter column

    logger.info(
        "_fetch_market_data: %d/%d tickers succeeded via bulk download",
        len(meta_rows), len(tickers),
    )

    meta_df    = pd.DataFrame(meta_rows).set_index("ticker") if meta_rows else pd.DataFrame()
    price_hist = pd.DataFrame(close_series) if close_series else pd.DataFrame()

    return meta_df, price_hist


# ==============================================================================
# MAIN FUNCTION
# ==============================================================================

def select_4w_trades(
    candidates: pd.DataFrame,
    next_event_days: Optional[dict[str, float]] = None,
    num_candidates: int  = _NUM_CANDIDATES,
    num_final: int       = _NUM_FINAL,
    adv_min: float       = _ADV_MIN,
    price_min: float     = _PRICE_MIN,
    atr_pct_max: float   = _ATR_PCT_MAX,
    hist_vol_max: float  = _HIST_VOL_MAX,
    event_days_min: int  = _EVENT_DAYS_MIN,
    max_per_sector: int  = _MAX_PER_SECTOR,
    max_corr: float      = _CORR_MAX,
    target_port_vol: float = _TARGET_VOL,
    max_weight: float    = _MAX_WEIGHT,
    stop_pct: float      = _STOP_PCT,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Apply 4-week trade selection filters on top of a priority-scored candidate list.

    Parameters
    ----------
    candidates : pd.DataFrame
        Output of select_top_tickers() converted to DataFrame.
        Must contain: ticker, priority_score.
        Optional (fetched via yfinance if absent): price, adv_20d, atr_14,
        hist_vol_60d, sector, mkt_cap.
    next_event_days : dict[str, float], optional
        Maps ticker → calendar days to next known catalyst (earnings, FDA,
        ex-div, etc.).  NaN / None = no known event.
        If omitted entirely, the event-risk filter is skipped with a warning.
    num_candidates : int
        How many top priority-score names to consider (default 15).
    num_final : int
        Final selection size (default 5).
    adv_min : float
        Minimum 20-day average dollar volume in USD (default $12 M).
    price_min : float
        Minimum share price (default $8).
    atr_pct_max : float
        Maximum ATR-14 / price ratio (default 3.8 %).
    hist_vol_max : float
        Maximum 60-day annualised historical vol (default 65 %).
    event_days_min : int
        Minimum days to next catalyst; names with a closer event are hard-
        excluded (default 28 calendar days).
    max_per_sector : int
        Maximum names per GICS sector in the final basket (default 2).
    max_corr : float
        Maximum allowed pairwise 60-day return correlation (default 0.65).
    target_port_vol : float
        Target portfolio volatility contribution per name (default 1.5 %).
    max_weight : float
        Hard cap on position weight (default 2 %).
    stop_pct : float
        Hard / trailing stop distance from entry (default 10 %).

    Returns
    -------
    final_selection : pd.DataFrame
        Columns: ticker, final_score, weight, expected_atr_pct, stop_level,
                 sector, hist_vol_60d
    rejected : pd.DataFrame
        Columns: ticker, priority_score, reason
    """
    if "ticker" not in candidates.columns or "priority_score" not in candidates.columns:
        raise ValueError(
            "candidates must contain 'ticker' and 'priority_score' columns."
        )

    rejected_rows: list[dict] = []

    # ── Step 1: Take top num_candidates by priority_score ────────────────────
    pool = (
        candidates
        .sort_values("priority_score", ascending=False)
        .head(num_candidates)
        .copy()
        .reset_index(drop=True)
    )
    logger.info("Step 1: %d candidates after top-%d cut", len(pool), num_candidates)

    # ── Enrich with market data if columns are missing ────────────────────────
    market_cols = {"price", "adv_20d", "atr_14", "hist_vol_60d", "sector", "mkt_cap"}
    missing_cols = market_cols - set(pool.columns)

    tickers_to_fetch = pool["ticker"].tolist()
    if missing_cols:
        logger.info("Fetching market data for %d tickers via yfinance …", len(tickers_to_fetch))
        meta_df, price_hist = _fetch_market_data(tickers_to_fetch)
    else:
        # Still need price history for correlation — fetch closes only
        _, price_hist = _fetch_market_data(tickers_to_fetch)
        meta_df = pd.DataFrame()

    # Merge fetched data into pool (only fill missing columns)
    if not meta_df.empty:
        for col in missing_cols:
            if col in meta_df.columns:
                pool = pool.join(meta_df[[col]], on="ticker")

    # Drop tickers for which yfinance returned no data
    core_cols = ["price", "adv_20d", "atr_14", "hist_vol_60d"]
    before = len(pool)
    pool = pool.dropna(subset=[c for c in core_cols if c in pool.columns])
    dropped = before - len(pool)
    if dropped:
        logger.warning("Dropped %d tickers with missing market data", dropped)

    # ── Step 2a: Liquidity filter ─────────────────────────────────────────────
    liq_mask = (pool["adv_20d"] >= adv_min) & (pool["price"] >= price_min)
    _record_rejected(pool[~liq_mask], rejected_rows,
                     f"Liquidity: adv_20d < ${adv_min/1e6:.0f}M or price < ${price_min}")
    pool = pool[liq_mask].copy()
    logger.info("Step 2a liquidity: %d remaining", len(pool))

    # ── Step 2b: Volatility / risk filter ────────────────────────────────────
    # Pass if EITHER condition holds (not both required)
    atr_pct = pool["atr_14"] / pool["price"]
    vol_mask = (atr_pct < atr_pct_max) | (pool["hist_vol_60d"] < hist_vol_max)
    _record_rejected(pool[~vol_mask], rejected_rows,
                     f"Volatility: atr_pct ≥ {atr_pct_max:.1%} AND hist_vol ≥ {hist_vol_max:.0%}")
    pool = pool[vol_mask].copy()
    logger.info("Step 2b volatility: %d remaining", len(pool))

    # ── Step 2c: Event-risk filter ────────────────────────────────────────────
    if next_event_days is None:
        logger.warning(
            "next_event_days not provided — event-risk filter skipped. "
            "Pass a dict {ticker: days_to_event} to enable."
        )
    else:
        pool["_next_event"] = pool["ticker"].map(next_event_days)
        # Reject tickers with a known event within event_days_min days
        event_mask = pool["_next_event"].isna() | (pool["_next_event"] > event_days_min)
        _record_rejected(pool[~event_mask], rejected_rows,
                         f"Event risk: catalyst in next {event_days_min} days")
        pool = pool[event_mask].drop(columns=["_next_event"])
        logger.info("Step 2c event-risk: %d remaining", len(pool))

    if pool.empty:
        logger.warning("All candidates rejected after hard filters.")
        final = pd.DataFrame(columns=["ticker","final_score","weight",
                                       "expected_atr_pct","stop_level","sector","hist_vol_60d"])
        return final, pd.DataFrame(rejected_rows)

    # ── Step 3: Sector + correlation greedy selection ─────────────────────────
    pool = pool.sort_values("priority_score", ascending=False).reset_index(drop=True)

    # Build 60-day return correlation matrix from price history
    corr_matrix = _build_correlation_matrix(price_hist, pool["ticker"].tolist())

    selected_tickers: list[str] = []
    sector_counts:    dict[str, int] = {}

    for _, row in pool.iterrows():
        ticker = row["ticker"]
        sector = str(row.get("sector", "Unknown") or "Unknown")

        # Sector cap
        if sector_counts.get(sector, 0) >= max_per_sector:
            _record_rejected_single(ticker, row["priority_score"], rejected_rows,
                                    f"Sector cap: already {max_per_sector} names in {sector}")
            continue

        # Pairwise correlation with already-selected names
        corr_breach = _max_correlation(ticker, selected_tickers, corr_matrix)
        if corr_breach is not None:
            _record_rejected_single(ticker, row["priority_score"], rejected_rows,
                                    f"Correlation: r={corr_breach:.2f} ≥ {max_corr} with selected name")
            continue

        selected_tickers.append(ticker)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

        if len(selected_tickers) >= num_final:
            break

    logger.info("Step 3 clustering: %d selected", len(selected_tickers))

    # ── Step 4: Build final selection DataFrame ───────────────────────────────
    final = pool[pool["ticker"].isin(selected_tickers)].copy()
    final = final.sort_values("priority_score", ascending=False).reset_index(drop=True)
    final = final.rename(columns={"priority_score": "final_score"})

    # ── Step 5: Volatility-parity sizing ─────────────────────────────────────
    # True vol-parity formula: w_i = target_vol / σ_i
    # Each name independently targets target_port_vol contribution to
    # portfolio vol (assuming zero correlation between names).
    #
    # Vol floor (_VOL_FLOOR) prevents a single low-vol outlier from
    # dominating the inverse-vol weights and cascading all other names
    # to the max_weight cap (e.g. a data artefact returning 4% annual vol).
    #
    # Scale-down step: if the raw basket exceeds total_alloc_max
    # (num_final × max_weight), all weights are scaled proportionally —
    # preserving relative sizing while respecting the hard budget.
    # Excess from capped names is NOT redistributed; this keeps each
    # uncapped name at its true vol-parity weight rather than over-
    # allocating to compensate for a name that hit its ceiling.
    total_alloc_max = num_final * max_weight               # hard budget (e.g. 10 %)
    vol_floored     = final["hist_vol_60d"].clip(lower=_VOL_FLOOR)
    raw_weights     = target_port_vol / vol_floored        # true vol-parity

    # Store for transparency before any scaling / capping
    final["raw_weight"] = raw_weights.round(4)

    # Scale proportionally if basket total exceeds budget
    if raw_weights.sum() > total_alloc_max:
        raw_weights = raw_weights * total_alloc_max / raw_weights.sum()

    # Hard per-name cap — no redistribution (preserves proportional ratios)
    final["weight"] = raw_weights.clip(upper=max_weight).round(4)

    # Expected daily ATR % and hard stop level
    final["expected_atr_pct"] = (final["atr_14"] / final["price"]).round(4)
    final["stop_level"]       = (final["price"] * (1 - stop_pct)).round(2)

    # Keep only output columns (+ sector, hist_vol_60d for reference)
    final["cap_hit"] = (final["raw_weight"] > final["weight"])

    keep_cols = ["ticker", "final_score", "weight", "raw_weight", "cap_hit",
                 "expected_atr_pct", "stop_level", "sector", "hist_vol_60d"]
    final = final[[c for c in keep_cols if c in final.columns]].reset_index(drop=True)

    return final, pd.DataFrame(rejected_rows)


# ==============================================================================
# DAILY TOP-20 RANKING
# ==============================================================================

def generate_daily_top20_ranking(
    candidates: pd.DataFrame,
    previous_top20: pd.DataFrame | None = None,
    next_event_days: dict[str, float] | None = None,
    adv_min: float      = _ADV_MIN,
    price_min: float    = _PRICE_MIN,
    atr_pct_max: float  = _ATR_PCT_MAX,
    hist_vol_max: float = _HIST_VOL_MAX,
    event_days_min: int = _EVENT_DAYS_MIN,
    max_per_sector: int = _MAX_PER_SECTOR,
    max_corr: float     = _CORR_MAX,
    target_port_vol: float = _TARGET_VOL,
    max_weight: float   = _MAX_WEIGHT,
    top_n: int          = 20,
) -> pd.DataFrame:
    """
    Produce a daily Top-N ranking (default 20) from the full candidate universe.

    Applies the same hard filters and sector/correlation clustering used by
    ``select_4w_trades``, but operates over the entire candidate pool rather
    than a pre-trimmed shortlist, and returns the top ``top_n`` survivors ranked
    by ``final_score`` (= ``priority_score`` after filters).

    Parameters
    ----------
    candidates : pd.DataFrame
        Full candidate universe (50+ tickers) that have already passed the
        minimum agreement threshold.  Must contain ``ticker`` and
        ``priority_score`` columns.  The following columns are fetched via
        yfinance if absent: ``price``, ``adv_20d``, ``atr_14``,
        ``hist_vol_60d``, ``sector``, ``mkt_cap``.
    previous_top20 : pd.DataFrame | None
        Yesterday's output of this function (same schema).  When provided,
        ``rank_change`` and ``rank_yesterday`` are computed against it.
        Pass ``None`` (default) to skip change tracking.
    next_event_days : dict[str, float] | None
        Maps ticker → calendar days to next known catalyst.  When omitted the
        event-risk filter is skipped with a warning.
    adv_min : float
        Minimum 20-day average dollar volume in USD (default ``_ADV_MIN``).
    price_min : float
        Minimum share price (default ``_PRICE_MIN``).
    atr_pct_max : float
        Maximum ATR-14 / price ratio (default ``_ATR_PCT_MAX``).
    hist_vol_max : float
        Maximum 60-day annualised historical vol (default ``_HIST_VOL_MAX``).
    event_days_min : int
        Minimum days to next catalyst (default ``_EVENT_DAYS_MIN``).
    max_per_sector : int
        Maximum names per GICS sector in the ranked basket (default
        ``_MAX_PER_SECTOR``).
    max_corr : float
        Maximum allowed pairwise 60-day return correlation (default
        ``_CORR_MAX``).
    target_port_vol : float
        Target portfolio vol contribution per name for sizing (default
        ``_TARGET_VOL``).
    max_weight : float
        Hard per-name weight cap (default ``_MAX_WEIGHT``).
    top_n : int
        Number of names to return (default 20).

    Returns
    -------
    pd.DataFrame
        Exactly ``top_n`` rows (or fewer if the universe is too small), sorted
        by ``rank`` ascending, with columns:

        rank            int     1 … top_n
        ticker          str
        priority_score  float   original score from the upstream screener
        final_score     float   same value (alias kept for dashboard compat)
        weight          float   vol-parity weight after cap (as decimal)
        raw_weight      float   vol-parity weight before cap
        cap_hit         bool    True when raw_weight was clipped to max_weight
        sector          str     GICS sector
        hist_vol_60d    float   60-day annualised historical vol
        adv_20d         float   20-day average dollar volume
        rank_change     str     "+3", "-2", "NEW", or "—"
        rank_yesterday  float   previous rank (NaN if not in yesterday's list)
    """
    if "ticker" not in candidates.columns or "priority_score" not in candidates.columns:
        raise ValueError(
            "candidates must contain 'ticker' and 'priority_score' columns."
        )

    # ── Work on a full copy; no top-N trim before filters ────────────────────
    pool = candidates.sort_values("priority_score", ascending=False).copy().reset_index(drop=True)
    logger.info("generate_daily_top20_ranking: %d candidates entering filters", len(pool))

    # ── Enrich with market data if columns are missing ────────────────────────
    market_cols = {"price", "adv_20d", "atr_14", "hist_vol_60d", "sector", "mkt_cap"}
    missing_cols = market_cols - set(pool.columns)
    tickers_to_fetch = pool["ticker"].tolist()

    if missing_cols:
        logger.info("Fetching market data for %d tickers via yfinance …", len(tickers_to_fetch))
        meta_df, price_hist = _fetch_market_data(tickers_to_fetch)
    else:
        _, price_hist = _fetch_market_data(tickers_to_fetch)
        meta_df = pd.DataFrame()

    if not meta_df.empty:
        for col in missing_cols:
            if col in meta_df.columns:
                pool = pool.join(meta_df[[col]], on="ticker")

    core_cols = ["price", "adv_20d", "atr_14", "hist_vol_60d"]
    before = len(pool)
    pool = pool.dropna(subset=[c for c in core_cols if c in pool.columns])
    if before - len(pool):
        logger.warning("Dropped %d tickers with missing market data", before - len(pool))

    # ── Hard filter: liquidity ────────────────────────────────────────────────
    liq_mask = (pool["adv_20d"] >= adv_min) & (pool["price"] >= price_min)
    pool = pool[liq_mask].copy()
    logger.info("After liquidity filter: %d remaining", len(pool))

    # ── Hard filter: volatility / risk ───────────────────────────────────────
    atr_pct  = pool["atr_14"] / pool["price"]
    vol_mask = (atr_pct < atr_pct_max) | (pool["hist_vol_60d"] < hist_vol_max)
    pool = pool[vol_mask].copy()
    logger.info("After volatility filter: %d remaining", len(pool))

    # ── Hard filter: event risk ───────────────────────────────────────────────
    if next_event_days is None:
        logger.warning(
            "next_event_days not provided — event-risk filter skipped. "
            "Pass a dict {ticker: days_to_event} to enable."
        )
    else:
        pool["_next_event"] = pool["ticker"].map(next_event_days)
        event_mask = pool["_next_event"].isna() | (pool["_next_event"] > event_days_min)
        pool = pool[event_mask].drop(columns=["_next_event"])
        logger.info("After event-risk filter: %d remaining", len(pool))

    if pool.empty:
        logger.warning("All candidates rejected after hard filters — returning empty ranking.")
        return pd.DataFrame(columns=[
            "rank", "ticker", "priority_score", "final_score",
            "weight", "raw_weight", "cap_hit",
            "sector", "hist_vol_60d", "adv_20d",
            "rank_change", "rank_yesterday",
        ])

    # ── Greedy clustering: sector cap + pairwise correlation ─────────────────
    pool = pool.sort_values("priority_score", ascending=False).reset_index(drop=True)
    corr_matrix = _build_correlation_matrix(price_hist, pool["ticker"].tolist())

    selected_tickers: list[str] = []
    sector_counts:    dict[str, int] = {}

    for _, row in pool.iterrows():
        ticker = str(row["ticker"])
        sector = str(row.get("sector", "Unknown") or "Unknown")

        if sector_counts.get(sector, 0) >= max_per_sector:
            continue

        corr_breach = _max_correlation(ticker, selected_tickers, corr_matrix)
        if corr_breach is not None:
            continue

        selected_tickers.append(ticker)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

        if len(selected_tickers) >= top_n:
            break

    logger.info("After clustering: %d names selected for Top-%d", len(selected_tickers), top_n)

    # ── Calculate swing targets & probabilities for all tickers in pool ───────
    target_rows: list[dict] = []
    for _, row in pool.iterrows():
        ticker     = str(row["ticker"])
        direction  = str(row.get("pre_resolved_direction") or "NEUTRAL").upper()
        agreement  = float(row.get("signal_agreement_score") or 0.0)
        confidence = float(row.get("pre_resolved_confidence") or 0.0)
        price      = float(row.get("price") or 0.0)
        atr_14     = float(row.get("atr_14") or 0.0)
        hist_vol   = float(row.get("hist_vol_60d") or 0.0)
        targets    = _calc_swing_targets(price, atr_14, hist_vol, direction, agreement, confidence)
        # Compute EV(T1) inline using the same row values
        ev         = calc_ev_t1_pct({**targets, "price": price, "atr_14": atr_14})
        target_rows.append({"ticker": ticker, **targets, "ev_t1_pct": ev})

    targets_df = pd.DataFrame(target_rows).set_index("ticker")

    # ── Build top-N cluster selection, sorted by ev_t1_pct → prob_combined → priority_score ──
    top_pool = pool[pool["ticker"].isin(selected_tickers)].copy()
    top_pool = top_pool.join(targets_df, on="ticker")

    # ev_t1_pct DESC: surfaces tickers with highest expected profit potential.
    # prob_combined DESC: tie-break on calibrated multi-factor probability.
    # priority_score DESC: final tie-break on signal quality (active on NEUTRAL days).
    top_pool = top_pool.sort_values(
        ["ev_t1_pct", "prob_combined", "priority_score"], ascending=[False, False, False]
    ).reset_index(drop=True)

    ranked = top_pool.copy()
    ranked["rank"]        = ranked.index + 1
    ranked["final_score"] = ranked["priority_score"]

    # ── Volatility-parity sizing ──────────────────────────────────────────────
    total_alloc_max = top_n * max_weight
    vol_floored     = ranked["hist_vol_60d"].clip(lower=_VOL_FLOOR)
    raw_weights     = target_port_vol / vol_floored
    ranked["raw_weight"] = raw_weights.round(4)

    if raw_weights.sum() > total_alloc_max:
        raw_weights = raw_weights * total_alloc_max / raw_weights.sum()

    ranked["weight"]  = raw_weights.clip(upper=max_weight).round(4)
    ranked["cap_hit"] = (ranked["raw_weight"] > ranked["weight"])

    # ── Append tail candidates (passed filters, excluded by clustering) ───────
    tail = pool[~pool["ticker"].isin(selected_tickers)].copy()
    tail = tail.join(targets_df, on="ticker")
    tail = tail.sort_values(
        ["ev_t1_pct", "prob_combined", "priority_score"], ascending=[False, False, False]
    ).reset_index(drop=True)
    if not tail.empty:
        tail["rank"]        = range(len(ranked) + 1, len(ranked) + 1 + len(tail))
        tail["final_score"] = tail["priority_score"]
        tail["weight"]      = 0.0
        tail["raw_weight"]  = 0.0
        tail["cap_hit"]     = False
        ranked = pd.concat([ranked, tail], ignore_index=True)

    logger.info(
        "Full ranked universe: %d rows (%d in top-%d + %d tail)",
        len(ranked), len(selected_tickers), top_n, len(tail) if not tail.empty else 0,
    )

    # ── Rank-change tracking (across full universe) ───────────────────────────
    if previous_top20 is not None and not previous_top20.empty and "rank" in previous_top20.columns:
        prev_map: dict[str, int] = dict(
            zip(previous_top20["ticker"].astype(str), previous_top20["rank"].astype(int))
        )
        rank_yesterday: list[float] = []
        rank_change:    list[str]   = []

        for _, row in ranked.iterrows():
            t          = str(row["ticker"])
            curr_rank  = int(row["rank"])
            prev_rank  = prev_map.get(t)

            if prev_rank is None:
                rank_yesterday.append(float("nan"))
                rank_change.append("NEW")
            else:
                delta = prev_rank - curr_rank   # positive = moved up (lower rank number)
                rank_yesterday.append(float(prev_rank))
                if delta == 0:
                    rank_change.append("—")
                elif delta > 0:
                    rank_change.append(f"+{delta}")
                else:
                    rank_change.append(str(delta))

        ranked["rank_yesterday"] = rank_yesterday
        ranked["rank_change"]    = rank_change
    else:
        ranked["rank_yesterday"] = float("nan")
        ranked["rank_change"]    = "—"

    # ── Return clean output columns only ─────────────────────────────────────
    output_cols = [
        "rank", "ticker", "priority_score", "final_score",
        "weight", "raw_weight", "cap_hit",
        "sector", "hist_vol_60d", "adv_20d",
        "rank_change", "rank_yesterday",
        # Swing trade columns
        "direction", "t1_price", "t2_price", "stop_price",
        "prob_t1", "prob_t2", "hold_days",
        "signal_agreement_score", "ev_t1_pct",
        "prob_combined",
    ]
    ranked = ranked[[c for c in output_cols if c in ranked.columns]].reset_index(drop=True)
    return ranked


# ==============================================================================
# DB MIGRATION
# ==============================================================================

def _migrate_daily_rankings_prob_column() -> None:
    """Add prob_combined column to daily_rankings if not already present (idempotent)."""
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'daily_rankings'
        """)
        existing = {row["column_name"] for row in cur.fetchall()}
        if "prob_combined" not in existing:
            cur.execute("ALTER TABLE daily_rankings ADD COLUMN prob_combined FLOAT")
            conn.commit()
            logger.info("_migrate_daily_rankings_prob_column: added prob_combined column")
        conn.close()
    except Exception as exc:
        logger.warning("_migrate_daily_rankings_prob_column: %s", exc)


# ==============================================================================
# DAILY TOP-20 PIPELINE  (load → rank → save)
# ==============================================================================

def run_daily_top20_pipeline(
    candidates: "list[dict] | pd.DataFrame",
    next_event_days: dict[str, float] | None = None,
    run_date: "date | None" = None,
    open_positions: "list[str] | None" = None,
) -> pd.DataFrame:
    """
    End-to-end daily Top-20 pipeline: load yesterday → rank → save to Supabase.

    Designed to be called from ``run_master.sh`` in the same way as
    ``archive_candidates``.  If Supabase is unreachable the ranking is still
    returned — the pipeline never crashes on a DB error.

    Parameters
    ----------
    candidates : list[dict] | pd.DataFrame
        Full candidate universe from ``select_top_tickers()``
        (``max_tickers=50``, ``min_agreement=0.0``).  Must contain
        ``ticker`` and ``priority_score``.
    next_event_days : dict[str, float] | None
        Maps ticker → calendar days to next catalyst.  Forwarded to
        ``generate_daily_top20_ranking``.
    run_date : date | None
        Override today's date (mainly for back-fills / tests).

    Returns
    -------
    pd.DataFrame
        The freshly generated top-20 ranking (same schema as
        ``generate_daily_top20_ranking``), whether or not the DB write
        succeeded.
    """
    from datetime import date as _date
    run_date = run_date or _date.today()

    # Ensure prob_combined column exists (idempotent — safe to call every run)
    _migrate_daily_rankings_prob_column()

    # Convert list[dict] → DataFrame so generate_daily_top20_ranking can work
    if not isinstance(candidates, pd.DataFrame):
        candidates = pd.DataFrame(candidates)

    if candidates.empty:
        logger.warning("run_daily_top20_pipeline: empty candidates — nothing to rank")
        return pd.DataFrame()

    # ── Open positions compete on equal footing — no exclusion ──────────────────
    # open_positions is used only to set the is_open_position flag on each row.
    # If an open position genuinely has the highest EV(T1) it deserves rank #1.
    open_set = {t.upper() for t in (open_positions or [])}

    # ── 1. Load yesterday's full list from Supabase ───────────────────────────
    previous_top20: pd.DataFrame | None = None
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT run_date, rank, ticker
            FROM   daily_rankings
            WHERE  run_date = (
                SELECT MAX(run_date) FROM daily_rankings WHERE run_date < %s
            )
            ORDER  BY rank ASC
            """,
            (run_date,),
        )
        rows = cur.fetchall()
        conn.close()

        if rows:
            previous_top20 = pd.DataFrame([dict(r) for r in rows])
            logger.info(
                "Loaded yesterday's top-20 (%d names) from daily_rankings",
                len(previous_top20),
            )
        else:
            logger.info(
                "No previous daily_rankings found — first run, passing None as previous_top20"
            )

    except Exception as exc:
        logger.warning(
            "Could not load yesterday's top-20 from Supabase (%s) — proceeding without it",
            exc,
        )

    # ── 2. Generate new ranking ───────────────────────────────────────────────
    top20 = generate_daily_top20_ranking(
        candidates,
        previous_top20=previous_top20,
        next_event_days=next_event_days,
    )
    logger.info("Generated new top-20 ranking (%d names)", len(top20))

    if top20.empty:
        logger.warning("run_daily_top20_pipeline: ranking returned empty — skipping DB write")
        return top20

    # ── 3. Save to Supabase daily_rankings ───────────────────────────────────
    try:
        from utils.db import managed_connection

        rows_to_write = []
        for _, row in top20.iterrows():
            rank_yesterday_val = row.get("rank_yesterday")
            # NaN → None (SQL NULL) for the nullable integer column
            if rank_yesterday_val is not None and not _is_nan(rank_yesterday_val):
                rank_yesterday_val = int(rank_yesterday_val)
            else:
                rank_yesterday_val = None

            def _safe_price(v):
                return float(v) if v is not None and not _is_nan(v) else None

            ticker_upper = str(row["ticker"]).upper()
            rows_to_write.append((
                run_date,
                int(row["rank"]),
                str(row["ticker"]),
                float(row["priority_score"]),
                float(row["final_score"]),
                float(row["weight"]),
                float(row["raw_weight"]),
                bool(row["cap_hit"]),
                str(row.get("sector") or "Unknown"),
                float(row.get("hist_vol_60d") or 0.0),
                float(row.get("adv_20d") or 0.0),
                str(row.get("rank_change") or "—"),
                rank_yesterday_val,
                # Swing trade columns
                str(row.get("direction") or "NEUTRAL"),
                _safe_price(row.get("t1_price")),
                _safe_price(row.get("t2_price")),
                _safe_price(row.get("stop_price")),
                float(row.get("prob_t1") or 0.0),
                float(row.get("prob_t2") or 0.0),
                int(row["hold_days"]) if row.get("hold_days") is not None and not _is_nan(row.get("hold_days")) else None,
                float(row.get("signal_agreement_score") or 0.0),
                float(row.get("ev_t1_pct") or -999.0),
                ticker_upper in open_set,   # is_open_position flag
                float(row["prob_combined"]) if row.get("prob_combined") is not None and not _is_nan(row.get("prob_combined")) else None,
            ))

        upsert_sql = """
            INSERT INTO daily_rankings
                (run_date, rank, ticker, priority_score, final_score,
                 weight, raw_weight, cap_hit,
                 sector, hist_vol_60d, adv_20d,
                 rank_change, rank_yesterday,
                 direction, t1_price, t2_price, stop_price,
                 prob_t1, prob_t2, hold_days, agreement_score,
                 ev_t1_pct, is_open_position, prob_combined)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_date, rank) DO UPDATE SET
                ticker           = EXCLUDED.ticker,
                priority_score   = EXCLUDED.priority_score,
                final_score      = EXCLUDED.final_score,
                weight           = EXCLUDED.weight,
                raw_weight       = EXCLUDED.raw_weight,
                cap_hit          = EXCLUDED.cap_hit,
                sector           = EXCLUDED.sector,
                hist_vol_60d     = EXCLUDED.hist_vol_60d,
                adv_20d          = EXCLUDED.adv_20d,
                rank_change      = EXCLUDED.rank_change,
                rank_yesterday   = EXCLUDED.rank_yesterday,
                direction        = EXCLUDED.direction,
                t1_price         = EXCLUDED.t1_price,
                t2_price         = EXCLUDED.t2_price,
                stop_price       = EXCLUDED.stop_price,
                prob_t1          = EXCLUDED.prob_t1,
                prob_t2          = EXCLUDED.prob_t2,
                hold_days        = EXCLUDED.hold_days,
                agreement_score  = EXCLUDED.agreement_score,
                ev_t1_pct        = EXCLUDED.ev_t1_pct,
                is_open_position = EXCLUDED.is_open_position,
                prob_combined    = EXCLUDED.prob_combined
        """

        with managed_connection() as conn:
            cur = conn.cursor()
            cur.executemany(upsert_sql, rows_to_write)

        logger.info(
            "Saved %d rows to daily_rankings for run_date=%s",
            len(rows_to_write),
            run_date,
        )

    except Exception as exc:
        logger.warning(
            "Could not save daily_rankings to Supabase (%s) — returning ranking anyway",
            exc,
        )

    return top20


def _is_nan(value: object) -> bool:
    """Return True if value is a float NaN (avoids importing math)."""
    try:
        return value != value  # NaN is the only value not equal to itself
    except Exception:
        return False


# ==============================================================================
# HELPERS
# ==============================================================================

def _build_correlation_matrix(
    price_hist: pd.DataFrame, tickers: list[str]
) -> pd.DataFrame:
    """
    Compute pairwise 60-day return correlation matrix for available tickers.
    Missing tickers return NaN correlations (treated as no constraint).
    """
    if price_hist.empty:
        return pd.DataFrame()
    available = [t for t in tickers if t in price_hist.columns]
    if len(available) < 2:
        return pd.DataFrame()
    returns = price_hist[available].pct_change().tail(60).dropna(how="all")
    return returns.corr()


def _max_correlation(
    ticker: str,
    selected: list[str],
    corr_matrix: pd.DataFrame,
) -> Optional[float]:
    """
    Return the highest correlation between ticker and any already-selected name,
    or None if it doesn't breach the threshold or data is unavailable.
    """
    if corr_matrix.empty or ticker not in corr_matrix.columns or not selected:
        return None
    for s in selected:
        if s not in corr_matrix.columns:
            continue
        r = corr_matrix.loc[ticker, s]
        if not np.isnan(r) and r >= _CORR_MAX:
            return float(r)
    return None


def _record_rejected(
    df: pd.DataFrame, rejected_rows: list[dict], reason: str
) -> None:
    for _, row in df.iterrows():
        rejected_rows.append(
            {"ticker": row["ticker"], "priority_score": row["priority_score"], "reason": reason}
        )


def _record_rejected_single(
    ticker: str, score: float, rejected_rows: list[dict], reason: str
) -> None:
    rejected_rows.append({"ticker": ticker, "priority_score": score, "reason": reason})


# ==============================================================================
# DASHBOARD QUERY HELPERS
# ==============================================================================

def get_latest_top20() -> pd.DataFrame:
    """
    Return the most recent day's full Top-20 ranking from Supabase.

    Queries ``daily_rankings`` for the single most recent ``run_date`` and
    returns all 20 rows for that date, sorted by ``rank``.

    Dashboard usage
    ---------------
    Typical call from the FastAPI layer or a React data-fetch hook::

        from utils.trade_selector_4w import get_latest_top20
        df = get_latest_top20()
        # → up to 20 rows, columns: run_date, rank, ticker, priority_score,
        #   final_score, weight, raw_weight, cap_hit, sector,
        #   hist_vol_60d, adv_20d, rank_change, rank_yesterday

    Returns
    -------
    pd.DataFrame
        Up to 20 rows sorted by ``rank`` ascending.  Returns an empty
        DataFrame (with the expected columns) if the table is empty or
        Supabase is unreachable.
    """
    _empty = pd.DataFrame(columns=[
        "run_date", "rank", "ticker", "priority_score", "final_score",
        "weight", "raw_weight", "cap_hit", "sector",
        "hist_vol_60d", "adv_20d", "rank_change", "rank_yesterday",
    ])
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT *
            FROM   daily_rankings
            WHERE  run_date = (
                SELECT MAX(run_date) FROM daily_rankings
            )
            ORDER  BY rank ASC
            """
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            logger.info("get_latest_top20: daily_rankings table is empty")
            return _empty

        return pd.DataFrame([dict(r) for r in rows])

    except Exception as exc:
        logger.warning("get_latest_top20: Supabase query failed — %s", exc)
        return _empty


def get_top20_history(
    ticker: str | None = None,
    days: int = 30,
) -> pd.DataFrame:
    """
    Return rank history from ``daily_rankings`` for the last *N* calendar days.

    Dashboard usage
    ---------------
    Fetch the full rolling table (all tickers, last 30 days)::

        from utils.trade_selector_4w import get_top20_history
        df = get_top20_history()                     # all tickers, 30 days
        df = get_top20_history(ticker="NVDA")        # single ticker, 30 days
        df = get_top20_history(ticker="NVDA", days=90)

    The result is suitable for feeding a rank-over-time line chart::

        import plotly.express as px
        fig = px.line(
            get_top20_history("NVDA"),
            x="run_date", y="rank", title="NVDA rank history",
        )
        fig.update_yaxes(autorange="reversed")   # rank 1 at the top

    Parameters
    ----------
    ticker : str | None
        When given, filters to that ticker only (case-insensitive).
        When ``None``, returns all tickers for the window.
    days : int
        Look-back window in calendar days from today (default 30).

    Returns
    -------
    pd.DataFrame
        Sorted by ``run_date DESC``, then ``rank ASC``.  Columns match the
        stored schema.  Returns an empty DataFrame if no data is found or
        Supabase is unreachable.
    """
    _empty = pd.DataFrame(columns=[
        "run_date", "rank", "ticker", "priority_score", "final_score",
        "weight", "raw_weight", "cap_hit", "sector",
        "hist_vol_60d", "adv_20d", "rank_change", "rank_yesterday",
    ])
    try:
        from datetime import date, timedelta
        from utils.db import get_connection

        cutoff = date.today() - timedelta(days=days)
        conn   = get_connection()
        cur    = conn.cursor()

        if ticker is None:
            cur.execute(
                """
                SELECT *
                FROM   daily_rankings
                WHERE  run_date >= %s
                ORDER  BY run_date DESC, rank ASC
                """,
                (cutoff,),
            )
        else:
            cur.execute(
                """
                SELECT *
                FROM   daily_rankings
                WHERE  run_date >= %s
                  AND  UPPER(ticker) = UPPER(%s)
                ORDER  BY run_date DESC, rank ASC
                """,
                (cutoff, ticker),
            )

        rows = cur.fetchall()
        conn.close()

        if not rows:
            logger.info(
                "get_top20_history: no rows found (ticker=%s, days=%d)",
                ticker, days,
            )
            return _empty

        return pd.DataFrame([dict(r) for r in rows])

    except Exception as exc:
        logger.warning("get_top20_history: Supabase query failed — %s", exc)
        return _empty
