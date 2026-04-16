"""
utils/supabase_persist.py — Centralised Supabase persistence helpers.

Each function takes the data produced by a pipeline step and upserts it
into Supabase so that:
  • Every GHA run builds a full historical record.
  • The LLM can query multi-day context when generating theses.
  • The dashboard always shows the latest values regardless of where the
    pipeline ran (local vs GitHub Actions).

Tables created automatically on first call (idempotent CREATE IF NOT EXISTS):
  • regime_snapshots     — daily market + sector regime
  • dark_pool_snapshots  — per-ticker dark pool / short-flow signals
  • screener_signals     — raw multi-factor equity scores per ticker per day
  • backtest_runs        — walk-forward backtest window results
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _conn():
    from utils.db import get_connection
    return get_connection()


def _today() -> str:
    return date.today().isoformat()


# ==============================================================================
# 1. REGIME SNAPSHOTS
# ==============================================================================

_REGIME_DDL = """
CREATE TABLE IF NOT EXISTS regime_snapshots (
    date            TEXT        NOT NULL,
    regime          TEXT        NOT NULL,
    score           INTEGER,
    vix             REAL,
    spy_vs_200ma    REAL,
    yield_curve     REAL,
    components      JSONB,
    sector_regimes  JSONB,
    computed_at     TIMESTAMPTZ,
    PRIMARY KEY (date)
);
"""

def save_regime_snapshot(regime_data: dict) -> None:
    """
    Upsert one day's market + sector regime into regime_snapshots.

    regime_data is the dict written to regime_cache.json:
      {
        "market_regime": { "regime", "score", "vix", "spy_vs_200ma",
                           "yield_curve_spread", "components", "computed_at" },
        "sector_regimes": { "tech": "BULL", ... }
      }
    """
    try:
        mr = regime_data.get("market_regime", {})
        sr = regime_data.get("sector_regimes", {})
        computed_at = mr.get("computed_at") or datetime.utcnow().isoformat()
        run_date = computed_at[:10] if computed_at else _today()

        conn = _conn()
        cur = conn.cursor()
        cur.execute(_REGIME_DDL)
        cur.execute(
            """
            INSERT INTO regime_snapshots
                (date, regime, score, vix, spy_vs_200ma, yield_curve,
                 components, sector_regimes, computed_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (date) DO UPDATE SET
                regime         = EXCLUDED.regime,
                score          = EXCLUDED.score,
                vix            = EXCLUDED.vix,
                spy_vs_200ma   = EXCLUDED.spy_vs_200ma,
                yield_curve    = EXCLUDED.yield_curve,
                components     = EXCLUDED.components,
                sector_regimes = EXCLUDED.sector_regimes,
                computed_at    = EXCLUDED.computed_at
            """,
            (
                run_date,
                mr.get("regime", "UNKNOWN"),
                mr.get("score"),
                mr.get("vix"),
                mr.get("spy_vs_200ma"),
                mr.get("yield_curve_spread"),
                json.dumps(mr.get("components", {})),
                json.dumps({k: v for k, v in sr.items() if k != "computed_at"}),
                computed_at,
            ),
        )
        conn.commit()
        conn.close()
        logger.info("regime_snapshots: upserted %s", run_date)
    except Exception as exc:
        logger.warning("save_regime_snapshot failed (non-fatal): %s", exc)


# ==============================================================================
# 2. DARK POOL SNAPSHOTS
# ==============================================================================

_DARK_POOL_DDL = """
CREATE TABLE IF NOT EXISTS dark_pool_snapshots (
    date                TEXT    NOT NULL,
    ticker              TEXT    NOT NULL,
    signal              TEXT,
    short_ratio_zscore  REAL,
    short_ratio_today   REAL,
    days_of_data        INTEGER,
    PRIMARY KEY (date, ticker)
);
"""

def save_dark_pool_snapshot(results: list[dict], run_date: str | None = None) -> None:
    """
    Upsert per-ticker dark pool signals into dark_pool_snapshots.

    results is the list written to data/dark_pool_latest.json["results"]:
      [{ "ticker", "signal", "short_ratio_zscore", "short_ratio_today",
         "days_of_data" }, ...]
    """
    if not results:
        return
    run_date = run_date or _today()
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_DARK_POOL_DDL)
        rows = [
            (
                run_date,
                r["ticker"],
                r.get("signal"),
                r.get("short_ratio_zscore"),
                r.get("short_ratio_today"),
                r.get("days_of_data"),
            )
            for r in results
            if r.get("ticker")
        ]
        cur.executemany(
            """
            INSERT INTO dark_pool_snapshots
                (date, ticker, signal, short_ratio_zscore, short_ratio_today, days_of_data)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (date, ticker) DO UPDATE SET
                signal             = EXCLUDED.signal,
                short_ratio_zscore = EXCLUDED.short_ratio_zscore,
                short_ratio_today  = EXCLUDED.short_ratio_today,
                days_of_data       = EXCLUDED.days_of_data
            """,
            rows,
        )
        conn.commit()
        conn.close()
        logger.info("dark_pool_snapshots: upserted %d rows for %s", len(rows), run_date)
    except Exception as exc:
        logger.warning("save_dark_pool_snapshot failed (non-fatal): %s", exc)


# ==============================================================================
# 3. SCREENER SIGNALS (equity factor scores)
# ==============================================================================

_SCREENER_DDL = """
CREATE TABLE IF NOT EXISTS screener_signals (
    date              TEXT    NOT NULL,
    ticker            TEXT    NOT NULL,
    composite_z       REAL,
    rank              INTEGER,
    regime            TEXT,
    factors_used      TEXT,
    momentum_12_1_z   REAL,
    momentum_6_1_z    REAL,
    mean_rev_5d_z     REAL,
    vol_quality_z     REAL,
    proximity_52wk_z  REAL,
    ivol_z            REAL,
    earnings_rev_z    REAL,
    PRIMARY KEY (date, ticker)
);
"""

def save_screener_signals(signals_df: Any, run_date: str | None = None) -> None:
    """
    Upsert equity factor scores from signal_engine.py into screener_signals.

    signals_df is the pandas DataFrame written to equity_signals_*.csv.
    Columns used: ticker, composite_z, rank, market_regime, factors_used,
    and any available *_z factor columns.
    """
    try:
        import pandas as pd
        if signals_df is None or (hasattr(signals_df, "empty") and signals_df.empty):
            return
        run_date = run_date or _today()

        def _col(df, *names):
            for n in names:
                if n in df.columns:
                    return n
            return None

        regime_col = _col(signals_df, "market_regime", "regime")

        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SCREENER_DDL)

        rows = []
        for _, row in signals_df.iterrows():
            ticker = str(row.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            rows.append((
                run_date,
                ticker,
                float(row["composite_z"])            if "composite_z"       in row.index and pd.notna(row["composite_z"])       else None,
                int(row["rank"])                     if "rank"              in row.index and pd.notna(row["rank"])              else None,
                str(row[regime_col])                 if regime_col and pd.notna(row[regime_col])                               else None,
                str(row["factors_used"])             if "factors_used"      in row.index and pd.notna(row["factors_used"])      else None,
                float(row["momentum_12_1_z"])        if "momentum_12_1_z"   in row.index and pd.notna(row["momentum_12_1_z"])   else None,
                float(row["momentum_6_1_z"])         if "momentum_6_1_z"    in row.index and pd.notna(row["momentum_6_1_z"])    else None,
                float(row["mean_rev_5d_z"])          if "mean_rev_5d_z"     in row.index and pd.notna(row["mean_rev_5d_z"])     else None,
                float(row["vol_quality_z"])          if "vol_quality_z"     in row.index and pd.notna(row["vol_quality_z"])     else None,
                float(row["proximity_52wk_z"])       if "proximity_52wk_z"  in row.index and pd.notna(row["proximity_52wk_z"]) else None,
                float(row["ivol_z"])                 if "ivol_z"            in row.index and pd.notna(row["ivol_z"])            else None,
                float(row["earnings_rev_z"])         if "earnings_rev_z"    in row.index and pd.notna(row["earnings_rev_z"])    else None,
            ))

        cur.executemany(
            """
            INSERT INTO screener_signals
                (date, ticker, composite_z, rank, regime, factors_used,
                 momentum_12_1_z, momentum_6_1_z, mean_rev_5d_z,
                 vol_quality_z, proximity_52wk_z, ivol_z, earnings_rev_z)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (date, ticker) DO UPDATE SET
                composite_z      = EXCLUDED.composite_z,
                rank             = EXCLUDED.rank,
                regime           = EXCLUDED.regime,
                factors_used     = EXCLUDED.factors_used,
                momentum_12_1_z  = EXCLUDED.momentum_12_1_z,
                momentum_6_1_z   = EXCLUDED.momentum_6_1_z,
                mean_rev_5d_z    = EXCLUDED.mean_rev_5d_z,
                vol_quality_z    = EXCLUDED.vol_quality_z,
                proximity_52wk_z = EXCLUDED.proximity_52wk_z,
                ivol_z           = EXCLUDED.ivol_z,
                earnings_rev_z   = EXCLUDED.earnings_rev_z
            """,
            rows,
        )
        conn.commit()
        conn.close()
        logger.info("screener_signals: upserted %d rows for %s", len(rows), run_date)
    except Exception as exc:
        logger.warning("save_screener_signals failed (non-fatal): %s", exc)


# ==============================================================================
# 4. BACKTEST RUNS
# ==============================================================================

_BACKTEST_DDL = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    id              SERIAL PRIMARY KEY,
    run_date        TEXT    NOT NULL,
    window_start    TEXT,
    window_end      TEXT,
    sharpe          REAL,
    max_drawdown    REAL,
    hit_rate        REAL,
    turnover        REAL,
    best_factor     TEXT,
    worst_factor    TEXT,
    optimized_weights JSONB,
    train_sharpe    REAL,
    n_weeks         INTEGER,
    tickers_included INTEGER,
    factor_ic       JSONB,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS backtest_runs_run_date_idx ON backtest_runs (run_date);
"""

def save_backtest_runs(results_df: Any, factor_ic: dict | None = None,
                       run_date: str | None = None) -> None:
    """
    Append backtest window results into backtest_runs.

    results_df is the DataFrame returned by BacktestEngine.run_full_backtest().
    factor_ic is the aggregated IC dict { factor_name: [ic_values] }.
    """
    try:
        import pandas as pd
        if results_df is None or (hasattr(results_df, "empty") and results_df.empty):
            return
        run_date = run_date or _today()
        factor_ic = factor_ic or {}

        conn = _conn()
        cur = conn.cursor()
        cur.execute(_BACKTEST_DDL)

        rows = []
        for _, row in results_df.iterrows():
            weights_raw = row.get("optimized_weights", "{}")
            if isinstance(weights_raw, str):
                try:
                    weights_json = json.dumps(json.loads(weights_raw))
                except Exception:
                    weights_json = weights_raw
            else:
                weights_json = json.dumps(weights_raw)

            rows.append((
                run_date,
                str(row.get("window_start", "")),
                str(row.get("window_end", "")),
                float(row["sharpe"])         if pd.notna(row.get("sharpe"))         else None,
                float(row["max_drawdown"])   if pd.notna(row.get("max_drawdown"))   else None,
                float(row["hit_rate"])       if pd.notna(row.get("hit_rate"))       else None,
                float(row["turnover"])       if pd.notna(row.get("turnover"))       else None,
                str(row.get("best_factor",  "")),
                str(row.get("worst_factor", "")),
                weights_json,
                float(row["train_sharpe"])   if pd.notna(row.get("train_sharpe"))   else None,
                int(row["n_weeks"])          if pd.notna(row.get("n_weeks"))         else None,
                int(row["tickers_included"]) if pd.notna(row.get("tickers_included")) else None,
                json.dumps({k: float(sum(v) / len(v)) for k, v in factor_ic.items() if v}),
            ))

        cur.executemany(
            """
            INSERT INTO backtest_runs
                (run_date, window_start, window_end, sharpe, max_drawdown,
                 hit_rate, turnover, best_factor, worst_factor,
                 optimized_weights, train_sharpe, n_weeks, tickers_included, factor_ic)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            rows,
        )
        conn.commit()
        conn.close()
        logger.info("backtest_runs: inserted %d windows for %s", len(rows), run_date)
    except Exception as exc:
        logger.warning("save_backtest_runs failed (non-fatal): %s", exc)


# ==============================================================================
# 5. CATALYST SCREENER SCORES
# ==============================================================================

_CATALYST_DDL = """
CREATE TABLE IF NOT EXISTS catalyst_scores (
    date              TEXT    NOT NULL,
    ticker            TEXT    NOT NULL,
    composite         REAL,
    squeeze_score     REAL,
    volume_score      REAL,
    vol_compress      REAL,
    options_score     REAL,
    technical_score   REAL,
    social_score      REAL,
    polymarket_score  REAL,
    dark_pool_score   REAL,
    dark_pool_signal  TEXT,
    n_flags           INTEGER,
    price             REAL,
    short_pct         REAL,
    PRIMARY KEY (date, ticker)
);
"""

def save_catalyst_scores(df: Any, run_date: str | None = None) -> None:
    """Upsert catalyst screener per-ticker scores into catalyst_scores."""
    try:
        import pandas as pd
        if df is None or (hasattr(df, "empty") and df.empty):
            return
        run_date = run_date or _today()
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_CATALYST_DDL)

        def _f(row, col):
            v = row.get(col)
            return float(v) if v is not None and pd.notna(v) else None

        rows = []
        for _, row in df.iterrows():
            ticker = str(row.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            rows.append((
                run_date, ticker,
                _f(row, "composite"), _f(row, "squeeze_score"), _f(row, "volume_score"),
                _f(row, "vol_compress"), _f(row, "options_score"), _f(row, "technical_score"),
                _f(row, "social_score"), _f(row, "polymarket_score"), _f(row, "dark_pool_score"),
                str(row.get("dark_pool_signal") or ""),
                int(row["n_flags"]) if "n_flags" in row and pd.notna(row["n_flags"]) else None,
                _f(row, "price"), _f(row, "short_pct"),
            ))
        cur.executemany(
            """
            INSERT INTO catalyst_scores
                (date, ticker, composite, squeeze_score, volume_score, vol_compress,
                 options_score, technical_score, social_score, polymarket_score,
                 dark_pool_score, dark_pool_signal, n_flags, price, short_pct)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (date, ticker) DO UPDATE SET
                composite=EXCLUDED.composite, squeeze_score=EXCLUDED.squeeze_score,
                volume_score=EXCLUDED.volume_score, vol_compress=EXCLUDED.vol_compress,
                options_score=EXCLUDED.options_score, technical_score=EXCLUDED.technical_score,
                social_score=EXCLUDED.social_score, polymarket_score=EXCLUDED.polymarket_score,
                dark_pool_score=EXCLUDED.dark_pool_score, dark_pool_signal=EXCLUDED.dark_pool_signal,
                n_flags=EXCLUDED.n_flags, price=EXCLUDED.price, short_pct=EXCLUDED.short_pct
            """,
            rows,
        )
        conn.commit()
        conn.close()
        logger.info("catalyst_scores: upserted %d rows for %s", len(rows), run_date)
    except Exception as exc:
        logger.warning("save_catalyst_scores failed (non-fatal): %s", exc)


# ==============================================================================
# 6. SQUEEZE SCREENER SCORES
# ==============================================================================

_SQUEEZE_DDL = """
CREATE TABLE IF NOT EXISTS squeeze_scores (
    date                   TEXT    NOT NULL,
    ticker                 TEXT    NOT NULL,
    final_score            REAL,
    juice_target           REAL,
    recent_squeeze         BOOLEAN,
    price                  REAL,
    short_pct_float        REAL,
    days_to_cover          REAL,
    market_cap_m           REAL,
    ev_score               REAL,
    pct_float_short_score  REAL,
    short_pnl_score        REAL,
    days_to_cover_score    REAL,
    volume_surge_score     REAL,
    ftd_score              REAL,
    market_cap_score       REAL,
    float_score            REAL,
    price_divergence_score REAL,
    PRIMARY KEY (date, ticker)
);
"""

def save_squeeze_scores(df: Any, run_date: str | None = None) -> None:
    """Upsert squeeze screener per-ticker scores into squeeze_scores."""
    try:
        import pandas as pd
        if df is None or (hasattr(df, "empty") and df.empty):
            return
        run_date = run_date or _today()
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SQUEEZE_DDL)

        def _f(row, col):
            v = row.get(col)
            return float(v) if v is not None and pd.notna(v) else None

        rows = []
        for _, row in df.iterrows():
            ticker = str(row.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            rows.append((
                run_date, ticker,
                _f(row, "final_score"), _f(row, "juice_target"),
                bool(row["recent_squeeze"]) if "recent_squeeze" in row else None,
                _f(row, "price"), _f(row, "short_pct_float"), _f(row, "days_to_cover"),
                _f(row, "market_cap_m"), _f(row, "ev_score"),
                _f(row, "pct_float_short_score"), _f(row, "short_pnl_score"),
                _f(row, "days_to_cover_score"), _f(row, "volume_surge_score"),
                _f(row, "ftd_score"), _f(row, "market_cap_score"),
                _f(row, "float_score"), _f(row, "price_divergence_score"),
            ))
        cur.executemany(
            """
            INSERT INTO squeeze_scores
                (date, ticker, final_score, juice_target, recent_squeeze, price,
                 short_pct_float, days_to_cover, market_cap_m, ev_score,
                 pct_float_short_score, short_pnl_score, days_to_cover_score,
                 volume_surge_score, ftd_score, market_cap_score,
                 float_score, price_divergence_score)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (date, ticker) DO UPDATE SET
                final_score=EXCLUDED.final_score, juice_target=EXCLUDED.juice_target,
                recent_squeeze=EXCLUDED.recent_squeeze, price=EXCLUDED.price,
                short_pct_float=EXCLUDED.short_pct_float,
                days_to_cover=EXCLUDED.days_to_cover, market_cap_m=EXCLUDED.market_cap_m,
                ev_score=EXCLUDED.ev_score, pct_float_short_score=EXCLUDED.pct_float_short_score,
                short_pnl_score=EXCLUDED.short_pnl_score,
                days_to_cover_score=EXCLUDED.days_to_cover_score,
                volume_surge_score=EXCLUDED.volume_surge_score,
                ftd_score=EXCLUDED.ftd_score, market_cap_score=EXCLUDED.market_cap_score,
                float_score=EXCLUDED.float_score,
                price_divergence_score=EXCLUDED.price_divergence_score
            """,
            rows,
        )
        conn.commit()
        conn.close()
        logger.info("squeeze_scores: upserted %d rows for %s", len(rows), run_date)
    except Exception as exc:
        logger.warning("save_squeeze_scores failed (non-fatal): %s", exc)


# ==============================================================================
# 7. RED FLAG SCORES
# ==============================================================================

_RED_FLAG_DDL = """
CREATE TABLE IF NOT EXISTS red_flag_scores (
    date               TEXT    NOT NULL,
    ticker             TEXT    NOT NULL,
    red_flag_score     REAL,
    risk_level         TEXT,
    restatement_score  REAL,
    accruals_score     REAL,
    accruals_ratio     REAL,
    gaap_score         REAL,
    payout_score       REAL,
    payout_ratio_fcf   REAL,
    rev_quality_score  REAL,
    data_quality       TEXT,
    top_flag           TEXT,
    PRIMARY KEY (date, ticker)
);
"""

def save_red_flag_scores(df: Any, run_date: str | None = None) -> None:
    """Upsert red flag screener results into red_flag_scores."""
    try:
        import pandas as pd
        if df is None or (hasattr(df, "empty") and df.empty):
            return
        run_date = run_date or _today()
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_RED_FLAG_DDL)

        def _f(row, col):
            v = row.get(col)
            return float(v) if v is not None and pd.notna(v) else None

        rows = []
        for _, row in df.iterrows():
            ticker = str(row.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            rows.append((
                run_date, ticker,
                _f(row, "red_flag_score"), str(row.get("risk_level") or ""),
                _f(row, "restatement_score"), _f(row, "accruals_score"),
                _f(row, "accruals_ratio"), _f(row, "gaap_score"),
                _f(row, "payout_score"), _f(row, "payout_ratio_fcf"),
                _f(row, "rev_quality_score"), str(row.get("data_quality") or ""),
                str(row.get("top_flag") or ""),
            ))
        cur.executemany(
            """
            INSERT INTO red_flag_scores
                (date, ticker, red_flag_score, risk_level, restatement_score,
                 accruals_score, accruals_ratio, gaap_score, payout_score,
                 payout_ratio_fcf, rev_quality_score, data_quality, top_flag)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (date, ticker) DO UPDATE SET
                red_flag_score=EXCLUDED.red_flag_score, risk_level=EXCLUDED.risk_level,
                restatement_score=EXCLUDED.restatement_score,
                accruals_score=EXCLUDED.accruals_score, accruals_ratio=EXCLUDED.accruals_ratio,
                gaap_score=EXCLUDED.gaap_score, payout_score=EXCLUDED.payout_score,
                payout_ratio_fcf=EXCLUDED.payout_ratio_fcf,
                rev_quality_score=EXCLUDED.rev_quality_score,
                data_quality=EXCLUDED.data_quality, top_flag=EXCLUDED.top_flag
            """,
            rows,
        )
        conn.commit()
        conn.close()
        logger.info("red_flag_scores: upserted %d rows for %s", len(rows), run_date)
    except Exception as exc:
        logger.warning("save_red_flag_scores failed (non-fatal): %s", exc)


# ==============================================================================
# 8. FUNDAMENTAL COMPUTED SCORES
# ==============================================================================

_FUNDAMENTAL_SCORES_DDL = """
CREATE TABLE IF NOT EXISTS fundamental_scores (
    date                  TEXT    NOT NULL,
    ticker                TEXT    NOT NULL,
    composite             REAL,
    extended_composite    REAL,
    score_valuation       REAL,
    score_growth          REAL,
    score_quality         REAL,
    score_balance         REAL,
    score_earnings        REAL,
    score_analyst         REAL,
    score_dcf_valuation   REAL,
    score_peer_relative   REAL,
    score_accounting_quality REAL,
    pe_forward            REAL,
    pe_trailing           REAL,
    revenue_growth_yoy    REAL,
    earnings_growth_yoy   REAL,
    operating_margin      REAL,
    roe                   REAL,
    PRIMARY KEY (date, ticker)
);
"""

def save_fundamental_scores(df: Any, run_date: str | None = None) -> None:
    """Upsert computed fundamental scores into fundamental_scores."""
    try:
        import pandas as pd
        if df is None or (hasattr(df, "empty") and df.empty):
            return
        run_date = run_date or _today()
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_FUNDAMENTAL_SCORES_DDL)

        def _f(row, col):
            v = row.get(col)
            return float(v) if v is not None and pd.notna(v) else None

        rows = []
        for _, row in df.iterrows():
            ticker = str(row.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            rows.append((
                run_date, ticker,
                _f(row, "composite"), _f(row, "extended_composite"),
                _f(row, "score_valuation"), _f(row, "score_growth"),
                _f(row, "score_quality"), _f(row, "score_balance"),
                _f(row, "score_earnings"), _f(row, "score_analyst"),
                _f(row, "score_dcf_valuation"), _f(row, "score_peer_relative"),
                _f(row, "score_accounting_quality"),
                _f(row, "pe_forward"), _f(row, "pe_trailing"),
                _f(row, "revenue_growth_yoy"), _f(row, "earnings_growth_yoy"),
                _f(row, "operating_margin"), _f(row, "roe"),
            ))
        cur.executemany(
            """
            INSERT INTO fundamental_scores
                (date, ticker, composite, extended_composite, score_valuation,
                 score_growth, score_quality, score_balance, score_earnings,
                 score_analyst, score_dcf_valuation, score_peer_relative,
                 score_accounting_quality, pe_forward, pe_trailing,
                 revenue_growth_yoy, earnings_growth_yoy, operating_margin, roe)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (date, ticker) DO UPDATE SET
                composite=EXCLUDED.composite, extended_composite=EXCLUDED.extended_composite,
                score_valuation=EXCLUDED.score_valuation, score_growth=EXCLUDED.score_growth,
                score_quality=EXCLUDED.score_quality, score_balance=EXCLUDED.score_balance,
                score_earnings=EXCLUDED.score_earnings, score_analyst=EXCLUDED.score_analyst,
                score_dcf_valuation=EXCLUDED.score_dcf_valuation,
                score_peer_relative=EXCLUDED.score_peer_relative,
                score_accounting_quality=EXCLUDED.score_accounting_quality,
                pe_forward=EXCLUDED.pe_forward, pe_trailing=EXCLUDED.pe_trailing,
                revenue_growth_yoy=EXCLUDED.revenue_growth_yoy,
                earnings_growth_yoy=EXCLUDED.earnings_growth_yoy,
                operating_margin=EXCLUDED.operating_margin, roe=EXCLUDED.roe
            """,
            rows,
        )
        conn.commit()
        conn.close()
        logger.info("fundamental_scores: upserted %d rows for %s", len(rows), run_date)
    except Exception as exc:
        logger.warning("save_fundamental_scores failed (non-fatal): %s", exc)


# ==============================================================================
# 9. CATALYST WATCHLIST HISTORY
# ==============================================================================

_CATALYST_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS catalyst_history (
    date       TEXT    NOT NULL,
    ticker     TEXT    NOT NULL,
    composite  REAL,
    rank       INTEGER,
    delta      INTEGER,
    PRIMARY KEY (date, ticker)
);
CREATE INDEX IF NOT EXISTS catalyst_history_ticker_idx ON catalyst_history (ticker);
"""

def save_catalyst_history(history: dict, run_date: str | None = None) -> None:
    """
    Upsert watchlist_history.json content into catalyst_history.

    history is { ticker: [{ date, composite, rank, delta }, ...] }
    """
    if not history:
        return
    run_date = run_date or _today()
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_CATALYST_HISTORY_DDL)
        rows = []
        for ticker, entries in history.items():
            t = ticker.strip().upper()
            for entry in (entries if isinstance(entries, list) else [entries]):
                entry_date = entry.get("date", run_date)
                rows.append((
                    entry_date, t,
                    float(entry["composite"]) if entry.get("composite") is not None else None,
                    int(entry["rank"])         if entry.get("rank")      is not None else None,
                    int(entry["delta"])        if entry.get("delta")     is not None else None,
                ))
        if rows:
            cur.executemany(
                """
                INSERT INTO catalyst_history (date, ticker, composite, rank, delta)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (date, ticker) DO UPDATE SET
                    composite=EXCLUDED.composite,
                    rank=EXCLUDED.rank,
                    delta=EXCLUDED.delta
                """,
                rows,
            )
        conn.commit()
        conn.close()
        logger.info("catalyst_history: upserted %d rows", len(rows))
    except Exception as exc:
        logger.warning("save_catalyst_history failed (non-fatal): %s", exc)
