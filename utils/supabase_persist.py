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


def _ensure_table_security(conn, *tables: str) -> None:
    from utils.db import ensure_public_table_rls

    ensure_public_table_rls(conn, *tables)


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
        _ensure_table_security(conn, "regime_snapshots")
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
        _ensure_table_security(conn, "dark_pool_snapshots")
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
        _ensure_table_security(conn, "screener_signals")

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
        _ensure_table_security(conn, "backtest_runs")

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
    raw_composite     REAL,
    post_squeeze_guard BOOLEAN,
    squeeze_score     REAL,
    volume_score      REAL,
    vol_compress      REAL,
    options_score     REAL,
    technical_score   REAL,
    social_score      REAL,
    polymarket_score  REAL,
    dark_pool_score   REAL,
    dark_pool_signal  TEXT,
    earnings_score    REAL,
    days_to_earnings  INTEGER,
    n_flags           INTEGER,
    price             REAL,
    short_pct         REAL,
    PRIMARY KEY (date, ticker)
);
"""

# Migration DDL: add new columns to existing tables without recreating them.
# Safe to run repeatedly — ADD COLUMN IF NOT EXISTS is idempotent on Postgres.
_CATALYST_MIGRATE_DDL = """
ALTER TABLE catalyst_scores
    ADD COLUMN IF NOT EXISTS raw_composite      REAL,
    ADD COLUMN IF NOT EXISTS post_squeeze_guard BOOLEAN,
    ADD COLUMN IF NOT EXISTS earnings_score     REAL,
    ADD COLUMN IF NOT EXISTS days_to_earnings   INTEGER;
"""


def save_catalyst_scores(df: Any, run_date: str | None = None) -> None:
    """Upsert catalyst screener per-ticker scores into catalyst_scores.

    raw_composite preserves the pre-post-squeeze-guard value so that rows
    zeroed by the guard do not silently lose nonzero component evidence
    (TRD-003 regression fix).
    """
    try:
        import pandas as pd
        if df is None or (hasattr(df, "empty") and df.empty):
            return
        run_date = run_date or _today()
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_CATALYST_DDL)
        _ensure_table_security(conn, "catalyst_scores")
        # Idempotent migration for existing tables
        try:
            cur.execute(_CATALYST_MIGRATE_DDL)
        except Exception:
            pass

        def _f(row, col):
            v = row.get(col)
            return float(v) if v is not None and pd.notna(v) else None

        def _b(row, col):
            v = row.get(col)
            return bool(v) if v is not None and pd.notna(v) else None

        rows = []
        for _, row in df.iterrows():
            ticker = str(row.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            composite = _f(row, "composite")
            # raw_composite falls back to composite when not present (pre-TRD-003 rows)
            raw_composite = _f(row, "raw_composite")
            if raw_composite is None:
                raw_composite = composite
            dte = row.get("days_to_earnings")
            days_to_earnings_val = int(dte) if dte is not None and pd.notna(dte) else None
            rows.append((
                run_date, ticker,
                composite, raw_composite, _b(row, "post_squeeze_guard"),
                _f(row, "squeeze_score"), _f(row, "volume_score"),
                _f(row, "vol_compress"), _f(row, "options_score"), _f(row, "technical_score"),
                _f(row, "social_score"), _f(row, "polymarket_score"), _f(row, "dark_pool_score"),
                str(row.get("dark_pool_signal") or ""),
                _f(row, "earnings_score"), days_to_earnings_val,
                int(row["n_flags"]) if "n_flags" in row and pd.notna(row["n_flags"]) else None,
                _f(row, "price"), _f(row, "short_pct"),
            ))
        cur.executemany(
            """
            INSERT INTO catalyst_scores
                (date, ticker, composite, raw_composite, post_squeeze_guard,
                 squeeze_score, volume_score, vol_compress,
                 options_score, technical_score, social_score, polymarket_score,
                 dark_pool_score, dark_pool_signal,
                 earnings_score, days_to_earnings,
                 n_flags, price, short_pct)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (date, ticker) DO UPDATE SET
                composite=EXCLUDED.composite,
                raw_composite=EXCLUDED.raw_composite,
                post_squeeze_guard=EXCLUDED.post_squeeze_guard,
                squeeze_score=EXCLUDED.squeeze_score,
                volume_score=EXCLUDED.volume_score, vol_compress=EXCLUDED.vol_compress,
                options_score=EXCLUDED.options_score, technical_score=EXCLUDED.technical_score,
                social_score=EXCLUDED.social_score, polymarket_score=EXCLUDED.polymarket_score,
                dark_pool_score=EXCLUDED.dark_pool_score, dark_pool_signal=EXCLUDED.dark_pool_signal,
                earnings_score=EXCLUDED.earnings_score,
                days_to_earnings=EXCLUDED.days_to_earnings,
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
    date                       TEXT    NOT NULL,
    ticker                     TEXT    NOT NULL,
    final_score                REAL,
    juice_target               REAL,
    recent_squeeze             BOOLEAN,
    price                      REAL,
    short_pct_float            REAL,
    days_to_cover              REAL,
    market_cap_m               REAL,
    ev_score                   REAL,
    pct_float_short_score      REAL,
    short_pnl_score            REAL,
    days_to_cover_score        REAL,
    volume_surge_score         REAL,
    ftd_score                  REAL,
    market_cap_score           REAL,
    float_score                REAL,
    price_divergence_score     REAL,
    computed_dtc_30d           REAL,
    compression_recovery_score REAL,
    volume_confirmation_flag   BOOLEAN,
    squeeze_state              TEXT,
    explanation_summary        TEXT,
    explanation_json           JSONB,
    PRIMARY KEY (date, ticker)
);
"""

# Idempotent migrations: add columns introduced after the initial 18-column schema.
# IMPORTANT: computed_dtc_30d / compression_recovery_score / volume_confirmation_flag /
# squeeze_state are in _SQUEEZE_DDL but were missing from this list — tables created
# before those CHUNKs landed need them added here.
_SQUEEZE_MIGRATE_DDL = [
    # CHUNK-01/03/04/05: early float/squeeze columns absent from original DDL
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS computed_dtc_30d REAL;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS compression_recovery_score REAL;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS volume_confirmation_flag BOOLEAN;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS squeeze_state TEXT;",
    # CHUNK-14: explanation text/JSON
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS explanation_summary TEXT;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS explanation_json JSONB;",
    # CHUNK-10: lifecycle metadata columns
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS state_confidence TEXT;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS state_reasons JSONB;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS state_warnings JSONB;",
    # CHUNK-16: risk scoring columns
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS risk_score REAL;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS risk_level TEXT;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS risk_flags JSONB;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS risk_warnings JSONB;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS risk_components JSONB;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS dilution_risk_flag BOOLEAN;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS latest_dilution_filing_date TEXT;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS shares_offered_pct_float REAL;",
    # SI persistence direct column (was only in signal_breakdown / explanation_json)
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS si_persistence_score REAL;",
    # CHUNK-09: options/IV context columns
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS options_pressure_score REAL;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS iv_rank REAL;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS iv_rank_score REAL;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS iv_data_confidence TEXT;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS unusual_call_activity_flag BOOLEAN;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS call_put_volume_ratio REAL;",
    "ALTER TABLE squeeze_scores ADD COLUMN IF NOT EXISTS call_put_oi_ratio REAL;",
    # Schema fix: migrate date column from TEXT to DATE (idempotent)
    """
    DO $$
    BEGIN
      IF (SELECT data_type FROM information_schema.columns
          WHERE table_name = 'squeeze_scores' AND column_name = 'date') = 'text' THEN
        ALTER TABLE squeeze_scores ALTER COLUMN date TYPE date USING date::date;
      END IF;
    END $$;
    """,
]

def save_squeeze_scores(df: Any, run_date: str | None = None) -> None:
    """Upsert squeeze screener per-ticker scores into squeeze_scores."""
    try:
        import json as _json
        import pandas as pd
        if df is None or (hasattr(df, "empty") and df.empty):
            return
        run_date = run_date or _today()
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SQUEEZE_DDL)
        _ensure_table_security(conn, "squeeze_scores")
        # Idempotent migration for tables created before CHUNK-14
        for stmt in _SQUEEZE_MIGRATE_DDL:
            try:
                cur.execute(stmt)
            except Exception:
                pass  # column already exists or permission error — non-fatal

        def _f(row, col):
            v = row.get(col)
            return float(v) if v is not None and pd.notna(v) else None

        def _explanation_json(row):
            v = row.get("explanation_json")
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            if isinstance(v, dict):
                return _json.dumps(v)
            s = str(v).strip()
            return s if s not in ("", "{}") else None

        def _lifecycle_json(row, col):
            v = row.get(col)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            if isinstance(v, list):
                return _json.dumps(v)
            s = str(v).strip()
            return s if s not in ("", "[]") else None

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
                _f(row, "computed_dtc_30d"), _f(row, "compression_recovery_score"),
                bool(row["volume_confirmation_flag"]) if "volume_confirmation_flag" in row else None,
                str(row["squeeze_state"]) if "squeeze_state" in row and row["squeeze_state"] is not None else None,
                str(row["explanation_summary"]) if row.get("explanation_summary") else None,
                _explanation_json(row),
                # CHUNK-10: lifecycle metadata
                str(row["state_confidence"]) if row.get("state_confidence") else None,
                _lifecycle_json(row, "state_reasons"),
                _lifecycle_json(row, "state_warnings"),
                # CHUNK-16: risk scoring
                _f(row, "risk_score"),
                str(row["risk_level"]) if row.get("risk_level") else None,
                _lifecycle_json(row, "risk_flags"),
                _lifecycle_json(row, "risk_warnings"),
                _lifecycle_json(row, "risk_components"),
                bool(row["dilution_risk_flag"]) if "dilution_risk_flag" in row and row["dilution_risk_flag"] is not None else None,
                str(row["latest_dilution_filing_date"]) if row.get("latest_dilution_filing_date") else None,
                _f(row, "shares_offered_pct_float"),
                # CHUNK-09: options/IV context
                _f(row, "options_pressure_score"),
                _f(row, "iv_rank"),
                _f(row, "iv_rank_score"),
                str(row["iv_data_confidence"]) if row.get("iv_data_confidence") else None,
                bool(row["unusual_call_activity_flag"]) if "unusual_call_activity_flag" in row and row["unusual_call_activity_flag"] is not None else None,
                _f(row, "call_put_volume_ratio"),
                _f(row, "call_put_oi_ratio"),
                _f(row, "si_persistence_score"),
            ))
        cur.executemany(
            """
            INSERT INTO squeeze_scores
                (date, ticker, final_score, juice_target, recent_squeeze, price,
                 short_pct_float, days_to_cover, market_cap_m, ev_score,
                 pct_float_short_score, short_pnl_score, days_to_cover_score,
                 volume_surge_score, ftd_score, market_cap_score,
                 float_score, price_divergence_score,
                 computed_dtc_30d, compression_recovery_score,
                 volume_confirmation_flag, squeeze_state,
                 explanation_summary, explanation_json,
                 state_confidence, state_reasons, state_warnings,
                 risk_score, risk_level, risk_flags, risk_warnings, risk_components,
                 dilution_risk_flag, latest_dilution_filing_date, shares_offered_pct_float,
                 options_pressure_score, iv_rank, iv_rank_score, iv_data_confidence,
                 unusual_call_activity_flag, call_put_volume_ratio, call_put_oi_ratio,
                 si_persistence_score)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                price_divergence_score=EXCLUDED.price_divergence_score,
                computed_dtc_30d=EXCLUDED.computed_dtc_30d,
                compression_recovery_score=EXCLUDED.compression_recovery_score,
                volume_confirmation_flag=EXCLUDED.volume_confirmation_flag,
                squeeze_state=EXCLUDED.squeeze_state,
                explanation_summary=EXCLUDED.explanation_summary,
                explanation_json=EXCLUDED.explanation_json,
                state_confidence=EXCLUDED.state_confidence,
                state_reasons=EXCLUDED.state_reasons,
                state_warnings=EXCLUDED.state_warnings,
                risk_score=EXCLUDED.risk_score,
                risk_level=EXCLUDED.risk_level,
                risk_flags=EXCLUDED.risk_flags,
                risk_warnings=EXCLUDED.risk_warnings,
                risk_components=EXCLUDED.risk_components,
                dilution_risk_flag=EXCLUDED.dilution_risk_flag,
                latest_dilution_filing_date=EXCLUDED.latest_dilution_filing_date,
                shares_offered_pct_float=EXCLUDED.shares_offered_pct_float,
                options_pressure_score=EXCLUDED.options_pressure_score,
                iv_rank=EXCLUDED.iv_rank,
                iv_rank_score=EXCLUDED.iv_rank_score,
                iv_data_confidence=EXCLUDED.iv_data_confidence,
                unusual_call_activity_flag=EXCLUDED.unusual_call_activity_flag,
                call_put_volume_ratio=EXCLUDED.call_put_volume_ratio,
                call_put_oi_ratio=EXCLUDED.call_put_oi_ratio,
                si_persistence_score=EXCLUDED.si_persistence_score
            """,
            rows,
        )
        conn.commit()
        conn.close()
        logger.info("squeeze_scores: upserted %d rows for %s", len(rows), run_date)
    except Exception as exc:
        logger.warning("save_squeeze_scores failed (non-fatal): %s", exc)


# ==============================================================================
# 7. SHORT INTEREST HISTORY  (CHUNK-02 / CHUNK-13 Phase 2 slice)
# ==============================================================================

_SI_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS short_interest_history (
    ticker                TEXT        NOT NULL,
    publication_date      DATE        NOT NULL,
    settlement_date       DATE,
    snapshot_date         DATE        NOT NULL,
    source                TEXT        NOT NULL DEFAULT 'yfinance_snapshot',
    source_timestamp      TIMESTAMPTZ,
    shares_short          REAL,
    short_pct_float       REAL,
    float_shares          REAL,
    avg_volume_30d        REAL,
    computed_dtc_30d      REAL,
    vendor_short_ratio    REAL,
    data_confidence_score REAL        DEFAULT 0.5,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, publication_date, source)
);
"""


def save_short_interest_history(records: list[dict]) -> None:
    """
    Upsert short-interest snapshots into short_interest_history.

    Each record should contain at minimum: ticker, publication_date, source.
    All other fields are optional and will be stored as NULL if missing.

    Idempotent: repeated calls with the same (ticker, publication_date, source)
    update the existing row rather than inserting duplicates. This means a daily
    yfinance run always produces exactly one row per ticker per day, preventing
    daily repeats from being misread as multiple FINRA reporting periods.
    """
    if not records:
        return
    try:
        from datetime import date as _date
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SI_HISTORY_DDL)
        _ensure_table_security(conn, "short_interest_history")

        def _v(rec, key):
            v = rec.get(key)
            return v if v is not None else None

        rows = []
        today = _date.today().isoformat()
        for rec in records:
            ticker = str(rec.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            publication_date = rec.get("publication_date") or today
            snapshot_date = rec.get("snapshot_date") or today
            source = rec.get("source") or "yfinance_snapshot"
            rows.append((
                ticker,
                str(publication_date),
                str(_v(rec, "settlement_date")) if rec.get("settlement_date") else None,
                str(snapshot_date),
                source,
                _v(rec, "source_timestamp"),
                _v(rec, "shares_short"),
                _v(rec, "short_pct_float"),
                _v(rec, "float_shares"),
                _v(rec, "avg_volume_30d"),
                _v(rec, "computed_dtc_30d"),
                _v(rec, "vendor_short_ratio"),
                float(rec["data_confidence_score"]) if rec.get("data_confidence_score") is not None else 0.5,
            ))

        cur.executemany(
            """
            INSERT INTO short_interest_history
                (ticker, publication_date, settlement_date, snapshot_date,
                 source, source_timestamp, shares_short, short_pct_float,
                 float_shares, avg_volume_30d, computed_dtc_30d,
                 vendor_short_ratio, data_confidence_score, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
            ON CONFLICT (ticker, publication_date, source) DO UPDATE SET
                settlement_date       = EXCLUDED.settlement_date,
                snapshot_date         = EXCLUDED.snapshot_date,
                source_timestamp      = EXCLUDED.source_timestamp,
                shares_short          = EXCLUDED.shares_short,
                short_pct_float       = EXCLUDED.short_pct_float,
                float_shares          = EXCLUDED.float_shares,
                avg_volume_30d        = EXCLUDED.avg_volume_30d,
                computed_dtc_30d      = EXCLUDED.computed_dtc_30d,
                vendor_short_ratio    = EXCLUDED.vendor_short_ratio,
                data_confidence_score = EXCLUDED.data_confidence_score,
                updated_at            = NOW()
            """,
            rows,
        )
        conn.commit()
        conn.close()
        logger.info("short_interest_history: upserted %d records", len(rows))
    except Exception as exc:
        logger.warning("save_short_interest_history failed (non-fatal): %s", exc)


def fetch_short_interest_history(
    ticker: str,
    as_of_date: "date | None" = None,
    limit: int = 10,
) -> list[dict]:
    """
    Fetch recent short-interest history for a ticker, point-in-time safe.

    Anti-lookahead: returns only rows where publication_date <= as_of_date.
    If as_of_date is None, uses today (safe for live scoring).
    Returns empty list on DB failure (non-fatal).

    Results are ordered newest-first so callers can easily inspect the
    most-recent SI values.
    """
    try:
        from datetime import date as _date
        cutoff = str(as_of_date or _date.today())
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SI_HISTORY_DDL)
        _ensure_table_security(conn, "short_interest_history")
        cur.execute(
            """
            SELECT ticker, publication_date, settlement_date, snapshot_date,
                   source, shares_short, short_pct_float, float_shares,
                   avg_volume_30d, computed_dtc_30d, vendor_short_ratio,
                   data_confidence_score
            FROM short_interest_history
            WHERE ticker = %s
              AND publication_date <= %s
            ORDER BY publication_date DESC
            LIMIT %s
            """,
            (ticker.upper(), cutoff, limit),
        )
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.debug("fetch_short_interest_history(%s) failed: %s", ticker, exc)
        return []


# ==============================================================================
# 8. FILING CATALYSTS  (CHUNK-07 / CHUNK-13 Phase 2B slice)
# ==============================================================================

_FILING_CATALYSTS_DDL = """
CREATE TABLE IF NOT EXISTS filing_catalysts (
    ticker                      TEXT        NOT NULL,
    filing_date                 DATE        NOT NULL,
    event_date                  DATE,
    filing_type                 TEXT        NOT NULL,
    accession_number            TEXT,
    issuer                      TEXT,
    holder_name                 TEXT,
    summary                     TEXT,
    ownership_accumulation_flag BOOLEAN     DEFAULT FALSE,
    dilution_risk_flag          BOOLEAN     DEFAULT FALSE,
    derivative_exposure_flag    BOOLEAN     DEFAULT FALSE,
    large_holder_flag           BOOLEAN     DEFAULT FALSE,
    shares_beneficially_owned   BIGINT,
    pct_class                   REAL,
    shares_offered              BIGINT,
    source_url                  TEXT,
    source                      TEXT        NOT NULL DEFAULT 'edgar_search',
    source_timestamp            TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, filing_date, filing_type, COALESCE(accession_number, ''))
);
"""

# Postgres doesn't allow COALESCE in primary key definition — use a unique index instead
_FILING_CATALYSTS_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS filing_catalysts_uq
    ON filing_catalysts (ticker, filing_date, filing_type,
                         COALESCE(accession_number, ''));
"""


def save_filing_catalysts(records: list[dict]) -> None:
    """
    Upsert SEC filing catalyst records into filing_catalysts.

    Each record should contain at minimum: ticker, filing_date, filing_type.
    All flag fields default to FALSE if not provided.
    Idempotent: same (ticker, filing_date, filing_type, accession_number)
    updates the existing row.
    """
    if not records:
        return
    try:
        from datetime import date as _date
        conn = _conn()
        cur = conn.cursor()

        # Use a simpler PK scheme compatible with Postgres — drop the COALESCE PK
        # and let the ON CONFLICT rely on the unique index instead.
        cur.execute("""
CREATE TABLE IF NOT EXISTS filing_catalysts (
    ticker                      TEXT        NOT NULL,
    filing_date                 DATE        NOT NULL,
    event_date                  DATE,
    filing_type                 TEXT        NOT NULL,
    accession_number            TEXT,
    issuer                      TEXT,
    holder_name                 TEXT,
    summary                     TEXT,
    ownership_accumulation_flag BOOLEAN     DEFAULT FALSE,
    dilution_risk_flag          BOOLEAN     DEFAULT FALSE,
    derivative_exposure_flag    BOOLEAN     DEFAULT FALSE,
    large_holder_flag           BOOLEAN     DEFAULT FALSE,
    shares_beneficially_owned   BIGINT,
    pct_class                   REAL,
    shares_offered              BIGINT,
    source_url                  TEXT,
    source                      TEXT        NOT NULL DEFAULT 'edgar_search',
    source_timestamp            TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ DEFAULT NOW()
);
""")
        cur.execute("""
CREATE UNIQUE INDEX IF NOT EXISTS filing_catalysts_uq
    ON filing_catalysts (ticker, filing_date, filing_type,
                         COALESCE(accession_number, ''));
""")
        _ensure_table_security(conn, "filing_catalysts")

        today = _date.today().isoformat()
        rows = []
        for rec in records:
            ticker = str(rec.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            rows.append((
                ticker,
                str(rec.get("filing_date") or today),
                str(rec["event_date"]) if rec.get("event_date") else None,
                str(rec.get("filing_type", "UNKNOWN")),
                rec.get("accession_number"),
                rec.get("issuer"),
                rec.get("holder_name"),
                rec.get("summary"),
                bool(rec.get("ownership_accumulation_flag", False)),
                bool(rec.get("dilution_risk_flag", False)),
                bool(rec.get("derivative_exposure_flag", False)),
                bool(rec.get("large_holder_flag", False)),
                int(rec["shares_beneficially_owned"]) if rec.get("shares_beneficially_owned") is not None else None,
                float(rec["pct_class"]) if rec.get("pct_class") is not None else None,
                int(rec["shares_offered"]) if rec.get("shares_offered") is not None else None,
                rec.get("source_url"),
                rec.get("source", "edgar_search"),
                rec.get("source_timestamp"),
            ))

        cur.executemany(
            """
            INSERT INTO filing_catalysts
                (ticker, filing_date, event_date, filing_type, accession_number,
                 issuer, holder_name, summary,
                 ownership_accumulation_flag, dilution_risk_flag,
                 derivative_exposure_flag, large_holder_flag,
                 shares_beneficially_owned, pct_class, shares_offered,
                 source_url, source, source_timestamp, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
            ON CONFLICT (ticker, filing_date, filing_type, COALESCE(accession_number, ''))
            DO UPDATE SET
                event_date                  = EXCLUDED.event_date,
                issuer                      = EXCLUDED.issuer,
                holder_name                 = EXCLUDED.holder_name,
                summary                     = EXCLUDED.summary,
                ownership_accumulation_flag = EXCLUDED.ownership_accumulation_flag,
                dilution_risk_flag          = EXCLUDED.dilution_risk_flag,
                derivative_exposure_flag    = EXCLUDED.derivative_exposure_flag,
                large_holder_flag           = EXCLUDED.large_holder_flag,
                shares_beneficially_owned   = EXCLUDED.shares_beneficially_owned,
                pct_class                   = EXCLUDED.pct_class,
                shares_offered              = EXCLUDED.shares_offered,
                source_url                  = EXCLUDED.source_url,
                source_timestamp            = EXCLUDED.source_timestamp,
                updated_at                  = NOW()
            """,
            rows,
        )
        conn.commit()
        conn.close()
        logger.info("filing_catalysts: upserted %d records", len(rows))
    except Exception as exc:
        logger.warning("save_filing_catalysts failed (non-fatal): %s", exc)


# ==============================================================================
# 9. SQUEEZE REPLAY HELPERS  (CHUNK-11)
# ==============================================================================

def fetch_squeeze_scores_for_replay(
    start_date: "date | str",
    end_date: "date | str",
    tickers: "list[str] | None" = None,
    limit: int = 5000,
) -> list[dict]:
    """
    Fetch saved squeeze_scores rows for point-in-time replay analysis.

    Returns rows with all saved columns.  Returns [] on any DB error.

    Parameters
    ----------
    start_date  : inclusive lower bound on `date` column
    end_date    : inclusive upper bound on `date` column
    tickers     : optional ticker filter; None = all tickers
    limit       : max rows returned (newest first per ticker)
    """
    try:
        _start = start_date.isoformat() if hasattr(start_date, "isoformat") else str(start_date)
        _end = end_date.isoformat() if hasattr(end_date, "isoformat") else str(end_date)
        conn = _conn()
        cur = conn.cursor()
        if tickers:
            placeholders = ",".join(["%s"] * len(tickers))
            query = f"""
            SELECT *
            FROM squeeze_scores
            WHERE date >= %s AND date <= %s
              AND ticker IN ({placeholders})
            ORDER BY ticker, date
            LIMIT %s
            """
            params = [_start, _end] + [t.upper() for t in tickers] + [limit]
        else:
            query = """
            SELECT *
            FROM squeeze_scores
            WHERE date >= %s AND date <= %s
            ORDER BY ticker, date
            LIMIT %s
            """
            params = [_start, _end, limit]
        cur.execute(query, params)
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.debug("fetch_squeeze_scores_for_replay failed: %s", exc)
        return []


def fetch_short_interest_history_for_replay(
    ticker: str,
    as_of_date: "date | str",
    limit: int = 5,
) -> list[dict]:
    """
    Fetch the most-recent SI history rows for *ticker* up to *as_of_date*.

    Used during replay to retrieve point-in-time SI context alongside a
    saved squeeze_scores snapshot.  Returns [] on any DB error.
    """
    try:
        cutoff = as_of_date.isoformat() if hasattr(as_of_date, "isoformat") else str(as_of_date)
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ticker, publication_date, settlement_date, snapshot_date,
                   shares_short, short_pct_float, float_shares,
                   avg_volume_30d, computed_dtc_30d
            FROM short_interest_history
            WHERE ticker = %s
              AND publication_date <= %s
            ORDER BY publication_date DESC
            LIMIT %s
            """,
            (ticker.upper(), cutoff, limit),
        )
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.debug("fetch_short_interest_history_for_replay(%s) failed: %s", ticker, exc)
        return []


def fetch_filing_catalysts(
    ticker: str,
    as_of_date: "date | None" = None,
    limit: int = 20,
) -> list[dict]:
    """
    Fetch filing_catalysts rows for *ticker*, filtered to point-in-time safety.

    Parameters
    ----------
    ticker      : equity ticker (case-insensitive)
    as_of_date  : if provided, returns only rows with filing_date <= as_of_date
                  (anti-lookahead guard); defaults to today
    limit       : max rows returned (most-recent ownership records first)

    Returns empty list on any DB error — callers must not crash.
    """
    try:
        cutoff = (as_of_date or date.today()).isoformat()
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                ticker, filing_date, event_date, filing_type, accession_number,
                issuer, holder_name, summary,
                ownership_accumulation_flag, dilution_risk_flag,
                derivative_exposure_flag, large_holder_flag,
                shares_beneficially_owned, pct_class, shares_offered,
                source_url, source
            FROM filing_catalysts
            WHERE ticker = %s
              AND filing_date <= %s
            ORDER BY
                ownership_accumulation_flag DESC,
                large_holder_flag DESC,
                filing_date DESC
            LIMIT %s
            """,
            (ticker.upper(), cutoff, limit),
        )
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("fetch_filing_catalysts failed (non-fatal): %s", exc)
        return []


# ==============================================================================
# 9. RED FLAG SCORES
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
        _ensure_table_security(conn, "red_flag_scores")

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
        _ensure_table_security(conn, "fundamental_scores")

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
        _ensure_table_security(conn, "catalyst_history")
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


# ==============================================================================
# 10. IV RANK READ HELPER  (CHUNK-09)
# ==============================================================================

def fetch_latest_iv_rank(
    ticker: str,
    as_of_date: "date | str | None" = None,
    lookback_days: int = 252,
    min_history: int = 5,
) -> "dict | None":
    """
    CHUNK-09: Read-only IV rank helper for squeeze_screener integration.

    Queries iv_history for the past *lookback_days* rows (up to *as_of_date*
    for point-in-time safety), computes IV rank as
        (latest_iv30 − min_iv30) / (max_iv30 − min_iv30)
    and returns a context dict.

    Returns None when fewer than *min_history* rows exist (insufficient
    history — not an error) or on any DB error.

    Parameters
    ----------
    ticker        : equity ticker, case-insensitive
    as_of_date    : upper bound on the date column; defaults to today
    lookback_days : history window (default 252 trading days ≈ 1 year)
    min_history   : minimum rows required before returning a rank
    """
    try:
        from utils.db import managed_connection

        ticker = ticker.upper().strip()
        if as_of_date is not None:
            cutoff = as_of_date.isoformat() if hasattr(as_of_date, "isoformat") else str(as_of_date)
            query = """
                SELECT iv30, date FROM iv_history
                WHERE ticker = %s AND date <= %s
                ORDER BY date DESC
                LIMIT %s
            """
            params = (ticker, cutoff, lookback_days)
        else:
            query = """
                SELECT iv30, date FROM iv_history
                WHERE ticker = %s
                ORDER BY date DESC
                LIMIT %s
            """
            params = (ticker, lookback_days)

        with managed_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            db_rows = cur.fetchall()

        if not db_rows or len(db_rows) < min_history:
            return None

        values = [float(r["iv30"]) for r in db_rows if r.get("iv30") is not None]
        if len(values) < min_history:
            return None

        current_iv = values[0]
        min_iv = min(values)
        max_iv = max(values)

        if max_iv > min_iv:
            iv_rank = (current_iv - min_iv) / (max_iv - min_iv)
            iv_rank = max(0.0, min(1.0, iv_rank))
        else:
            iv_rank = 0.5  # degenerate: all stored IVs identical

        latest_date = db_rows[0]["date"]

        return {
            "iv_rank": round(iv_rank * 100.0, 1),  # 0–100 scale
            "iv30": round(current_iv, 4),
            "date": str(latest_date),
            "history_count": len(values),
        }

    except Exception as exc:
        logger.debug("fetch_latest_iv_rank(%s) failed: %s", ticker, exc)
        return None


# ==============================================================================
# 11. SQUEEZE ALERT HELPERS  (CHUNK-15)
# ==============================================================================


# ==============================================================================
# TRD-012: SQUEEZE TRAINING DATASET
# ==============================================================================
# Two tables: squeeze_training_snapshots (point-in-time features at signal time)
# and squeeze_training_outcomes (forward returns + taxonomy labels, filled after
# the forward window closes).
#
# Design: these tables are ML-ready exports. squeeze_scores is the source of
# truth for pipeline history; these tables extract a clean tabular view for
# model training and calibration. The key difference from squeeze_scores is:
#   - signal-time features are explicit columns (not buried in explanation_json)
#   - outcomes include binary hit flags and taxonomy labels
#   - one row per (signal_date, ticker, alert_type) — not just (date, ticker)
# ==============================================================================

_SQUEEZE_TRAINING_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS squeeze_training_snapshots (
    id                          BIGSERIAL PRIMARY KEY,
    signal_date                 DATE        NOT NULL,
    ticker                      TEXT        NOT NULL,
    alert_type                  TEXT,
    final_score                 REAL,
    short_pct_float             REAL,
    computed_dtc_30d            REAL,
    compression_recovery_score  REAL,
    volume_confirmation_flag    BOOLEAN,
    si_persistence_score        REAL,
    effective_float_score       REAL,
    effective_short_float_ratio REAL,
    large_holder_ownership_pct  REAL,
    options_pressure_score      REAL,
    iv_rank                     REAL,
    unusual_call_activity_flag  BOOLEAN,
    risk_score                  REAL,
    risk_level                  TEXT,
    dilution_risk_flag          BOOLEAN,
    explanation_tags            JSONB,
    explanation_summary         TEXT,
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (signal_date, ticker, alert_type)
);
"""

_SQUEEZE_TRAINING_OUTCOMES_DDL = """
CREATE TABLE IF NOT EXISTS squeeze_training_outcomes (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE    NOT NULL,
    ticker          TEXT    NOT NULL,
    alert_type      TEXT,
    fwd_5d          REAL,
    fwd_10d         REAL,
    fwd_20d         REAL,
    fwd_30d         REAL,
    max_fwd_return  REAL,
    hit_15pct_10d   BOOLEAN,
    hit_25pct_20d   BOOLEAN,
    outcome_label   TEXT,
    taxonomy_label  TEXT,
    labeled_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (signal_date, ticker, alert_type)
);
"""


def _norm_alert_type(val) -> "str | None":
    """
    Normalize an alert_type value to a clean string or None.

    Guards against str(None) → "None" (the common coercion bug):
    - Python None          → SQL NULL (return None)
    - "None" / "none"      → SQL NULL (return None)
    - "null" / "NULL"      → SQL NULL (return None)
    - "" / whitespace-only → SQL NULL (return None)
    - pandas/numpy NaN     → SQL NULL (return None)
    - any other value      → str(val).strip()

    Used consistently in all three training-table persistence helpers so
    unlabeled historical rows cannot pollute calibration buckets with a
    bogus "None" string grouping key.
    """
    if val is None:
        return None
    try:
        import math
        if isinstance(val, float) and math.isnan(val):
            return None
    except Exception:
        pass
    s = str(val).strip()
    if not s or s.lower() in ("none", "null", "nan"):
        return None
    return s


def save_squeeze_training_snapshot(record: dict) -> None:
    """
    Upsert one point-in-time training snapshot into squeeze_training_snapshots.

    All fields are sourced at signal time (no lookahead). Caller is responsible
    for providing only information known on signal_date.

    record must contain at minimum: signal_date, ticker, alert_type.
    Rows with a missing / unknown alert_type are silently skipped to prevent
    "None" string pollution in calibration grouping keys.
    """
    if not record:
        return
    _at = _norm_alert_type(record.get("alert_type"))
    if _at is None:
        logger.debug("save_squeeze_training_snapshot: skipping row with no alert_type (%s)", record.get("ticker"))
        return
    try:
        import json as _json
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SQUEEZE_TRAINING_SNAPSHOTS_DDL)
        _ensure_table_security(conn, "squeeze_training_snapshots")

        def _f(key):
            v = record.get(key)
            return float(v) if v is not None else None

        def _b(key):
            v = record.get(key)
            return bool(v) if v is not None else None

        tags = record.get("explanation_tags")
        tags_json = _json.dumps(tags) if isinstance(tags, (dict, list)) else (tags if isinstance(tags, str) else None)

        cur.execute(
            """
            INSERT INTO squeeze_training_snapshots
                (signal_date, ticker, alert_type,
                 final_score, short_pct_float, computed_dtc_30d,
                 compression_recovery_score, volume_confirmation_flag,
                 si_persistence_score, effective_float_score,
                 effective_short_float_ratio, large_holder_ownership_pct,
                 options_pressure_score, iv_rank, unusual_call_activity_flag,
                 risk_score, risk_level, dilution_risk_flag,
                 explanation_tags, explanation_summary)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (signal_date, ticker, alert_type) DO UPDATE SET
                final_score                 = EXCLUDED.final_score,
                short_pct_float             = EXCLUDED.short_pct_float,
                computed_dtc_30d            = EXCLUDED.computed_dtc_30d,
                compression_recovery_score  = EXCLUDED.compression_recovery_score,
                volume_confirmation_flag    = EXCLUDED.volume_confirmation_flag,
                si_persistence_score        = EXCLUDED.si_persistence_score,
                effective_float_score       = EXCLUDED.effective_float_score,
                effective_short_float_ratio = EXCLUDED.effective_short_float_ratio,
                large_holder_ownership_pct  = EXCLUDED.large_holder_ownership_pct,
                options_pressure_score      = EXCLUDED.options_pressure_score,
                iv_rank                     = EXCLUDED.iv_rank,
                unusual_call_activity_flag  = EXCLUDED.unusual_call_activity_flag,
                risk_score                  = EXCLUDED.risk_score,
                risk_level                  = EXCLUDED.risk_level,
                dilution_risk_flag          = EXCLUDED.dilution_risk_flag,
                explanation_tags            = EXCLUDED.explanation_tags,
                explanation_summary         = EXCLUDED.explanation_summary
            """,
            (
                str(record.get("signal_date", "")),
                str(record.get("ticker", "")).strip().upper(),
                _at,
                _f("final_score"), _f("short_pct_float"), _f("computed_dtc_30d"),
                _f("compression_recovery_score"), _b("volume_confirmation_flag"),
                _f("si_persistence_score"), _f("effective_float_score"),
                _f("effective_short_float_ratio"), _f("large_holder_ownership_pct"),
                _f("options_pressure_score"), _f("iv_rank"), _b("unusual_call_activity_flag"),
                _f("risk_score"),
                str(record.get("risk_level", "")) or None,
                _b("dilution_risk_flag"),
                tags_json,
                str(record.get("explanation_summary", "")) or None,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("save_squeeze_training_snapshot failed (non-fatal): %s", exc)


def save_squeeze_training_snapshot_backfill(record: dict) -> None:
    """
    Insert a training snapshot row from historical replay/backfill data.

    Identical to save_squeeze_training_snapshot() except this uses
    ON CONFLICT DO NOTHING — so if a richer live-pipeline snapshot already
    exists for this (signal_date, ticker, alert_type), it is preserved intact.

    Use this when materializing historical feature rows from squeeze_scores
    replay data (backfill path). Do NOT use for live pipeline writes — those
    should use save_squeeze_training_snapshot() which keeps the row fresh.

    Sourced from: squeeze_scores fields available in _build_replay_row().
    Point-in-time safe: all fields come from data already saved at signal time.
    """
    if not record:
        return
    _at = _norm_alert_type(record.get("alert_type"))
    if _at is None:
        logger.debug("save_squeeze_training_snapshot_backfill: skipping row with no alert_type (%s)", record.get("ticker"))
        return
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SQUEEZE_TRAINING_SNAPSHOTS_DDL)
        _ensure_table_security(conn, "squeeze_training_snapshots")

        def _f(key):
            v = record.get(key)
            return float(v) if v is not None else None

        def _b(key):
            v = record.get(key)
            return bool(v) if v is not None else None

        cur.execute(
            """
            INSERT INTO squeeze_training_snapshots
                (signal_date, ticker, alert_type,
                 final_score, short_pct_float, computed_dtc_30d,
                 compression_recovery_score, volume_confirmation_flag,
                 si_persistence_score, effective_float_score,
                 effective_short_float_ratio, large_holder_ownership_pct,
                 options_pressure_score, iv_rank, unusual_call_activity_flag,
                 risk_score, risk_level, dilution_risk_flag,
                 explanation_tags, explanation_summary)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (signal_date, ticker, alert_type) DO NOTHING
            """,
            (
                str(record.get("signal_date", "")),
                str(record.get("ticker", "")).strip().upper(),
                _at,
                _f("final_score"), _f("short_pct_float"), _f("computed_dtc_30d"),
                _f("compression_recovery_score"), _b("volume_confirmation_flag"),
                _f("si_persistence_score"), _f("effective_float_score"),
                _f("effective_short_float_ratio"), _f("large_holder_ownership_pct"),
                _f("options_pressure_score"), _f("iv_rank"), _b("unusual_call_activity_flag"),
                _f("risk_score"),
                str(record.get("risk_level", "")) or None,
                _b("dilution_risk_flag"),
                None,   # explanation_tags: not available in replay rows; live pipeline sets this
                None,   # explanation_summary: same
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("save_squeeze_training_snapshot_backfill failed (non-fatal): %s", exc)


def save_squeeze_training_outcome(record: dict) -> None:
    """
    Upsert a labeled outcome row into squeeze_training_outcomes.

    Only call after the relevant forward window has closed (point-in-time safety).

    record must contain at minimum: signal_date, ticker, alert_type.
    Forward return fields and labels are optional (stored as NULL when missing).
    """
    if not record:
        return
    _at = _norm_alert_type(record.get("alert_type"))
    if _at is None:
        logger.debug("save_squeeze_training_outcome: skipping row with no alert_type (%s)", record.get("ticker"))
        return
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SQUEEZE_TRAINING_OUTCOMES_DDL)
        _ensure_table_security(conn, "squeeze_training_outcomes")

        def _f(key):
            v = record.get(key)
            return float(v) if v is not None else None

        def _b(key):
            v = record.get(key)
            return bool(v) if v is not None else None

        cur.execute(
            """
            INSERT INTO squeeze_training_outcomes
                (signal_date, ticker, alert_type,
                 fwd_5d, fwd_10d, fwd_20d, fwd_30d, max_fwd_return,
                 hit_15pct_10d, hit_25pct_20d,
                 outcome_label, taxonomy_label)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (signal_date, ticker, alert_type) DO UPDATE SET
                fwd_5d          = EXCLUDED.fwd_5d,
                fwd_10d         = EXCLUDED.fwd_10d,
                fwd_20d         = EXCLUDED.fwd_20d,
                fwd_30d         = EXCLUDED.fwd_30d,
                max_fwd_return  = EXCLUDED.max_fwd_return,
                hit_15pct_10d   = EXCLUDED.hit_15pct_10d,
                hit_25pct_20d   = EXCLUDED.hit_25pct_20d,
                outcome_label   = EXCLUDED.outcome_label,
                taxonomy_label  = EXCLUDED.taxonomy_label,
                labeled_at      = NOW()
            """,
            (
                str(record.get("signal_date", "")),
                str(record.get("ticker", "")).strip().upper(),
                _at,
                _f("fwd_5d"), _f("fwd_10d"), _f("fwd_20d"), _f("fwd_30d"),
                _f("max_fwd_return"),
                _b("hit_15pct_10d"), _b("hit_25pct_20d"),
                str(record.get("outcome_label", "")) or None,
                str(record.get("taxonomy_label", "")) or None,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("save_squeeze_training_outcome failed (non-fatal): %s", exc)


def fetch_squeeze_training_outcomes(
    start_date: str | None = None,
    end_date: str | None = None,
    alert_types: "list[str] | None" = None,
    tickers: "list[str] | None" = None,
) -> "list[dict]":
    """
    Fetch labeled training outcomes for calibration and reporting.

    Returns list of dicts joining snapshots + outcomes. Only returns rows where
    outcomes have been labeled (taxonomy_label IS NOT NULL).
    """
    try:
        conn = _conn()
        cur = conn.cursor()
        wheres = ["o.taxonomy_label IS NOT NULL"]
        params: list = []
        if start_date:
            wheres.append("o.signal_date >= %s")
            params.append(start_date)
        if end_date:
            wheres.append("o.signal_date <= %s")
            params.append(end_date)
        if alert_types:
            wheres.append("o.alert_type = ANY(%s)")
            params.append(alert_types)
        if tickers:
            wheres.append("o.ticker = ANY(%s)")
            params.append([t.upper() for t in tickers])
        where_clause = " AND ".join(wheres)
        cur.execute(
            f"""
            SELECT
                o.signal_date, o.ticker, o.alert_type,
                o.fwd_5d, o.fwd_10d, o.fwd_20d, o.fwd_30d, o.max_fwd_return,
                o.hit_15pct_10d, o.hit_25pct_20d,
                o.outcome_label, o.taxonomy_label,
                s.final_score, s.short_pct_float, s.computed_dtc_30d,
                s.compression_recovery_score, s.si_persistence_score,
                s.risk_level, s.dilution_risk_flag
            FROM   squeeze_training_outcomes o
            LEFT JOIN squeeze_training_snapshots s
                   ON s.signal_date = o.signal_date
                  AND s.ticker      = o.ticker
                  AND s.alert_type  = o.alert_type
            WHERE  {where_clause}
            ORDER  BY o.signal_date ASC, o.ticker ASC
            """,
            params,
        )
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("fetch_squeeze_training_outcomes failed: %s", exc)
        return []


# ==============================================================================
# TRD-015: APPROVAL REQUESTS
# ==============================================================================

_APPROVAL_REQUESTS_DDL = """
CREATE TABLE IF NOT EXISTS approval_requests (
    request_id          TEXT        NOT NULL PRIMARY KEY,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    category            TEXT        NOT NULL,
    risk_level          TEXT        NOT NULL DEFAULT 'LOW',
    title               TEXT        NOT NULL,
    summary             TEXT,
    evidence_ref        TEXT,
    proposed_change_json JSONB,
    status              TEXT        NOT NULL DEFAULT 'PENDING',
    approved_by         TEXT,
    approved_at         TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);
"""

_VALID_APPROVAL_STATUSES = frozenset({"PENDING", "APPROVED", "REJECTED", "EXPIRED", "APPLIED"})


def save_approval_request(record: dict) -> str:
    """
    Persist a new approval request in Supabase.

    record must contain: request_id, category, title.
    Optional: risk_level, summary, evidence_ref, proposed_change_json, expires_at.

    Returns request_id on success, empty string on failure.
    """
    if not record:
        return ""
    try:
        import json as _json
        import uuid as _uuid

        request_id = str(record.get("request_id") or _uuid.uuid4())
        category   = str(record.get("category", "UNCLASSIFIED"))
        risk_level = str(record.get("risk_level", "LOW")).upper()
        title      = str(record.get("title", ""))
        summary    = str(record.get("summary", "")) or None
        evidence_ref = str(record.get("evidence_ref", "")) or None
        pch = record.get("proposed_change_json")
        pch_json = _json.dumps(pch) if isinstance(pch, (dict, list)) else (pch if isinstance(pch, str) else None)
        expires_at = record.get("expires_at")

        conn = _conn()
        cur = conn.cursor()
        cur.execute(_APPROVAL_REQUESTS_DDL)
        _ensure_table_security(conn, "approval_requests")
        cur.execute(
            """
            INSERT INTO approval_requests
                (request_id, category, risk_level, title, summary,
                 evidence_ref, proposed_change_json, status, expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'PENDING',%s)
            ON CONFLICT (request_id) DO NOTHING
            """,
            (request_id, category, risk_level, title, summary,
             evidence_ref, pch_json, expires_at),
        )
        conn.commit()
        conn.close()
        logger.info("save_approval_request: created %s (%s)", request_id, title)
        return request_id
    except Exception as exc:
        logger.warning("save_approval_request failed: %s", exc)
        return ""


def update_approval_request_status(
    request_id: str,
    new_status: str,
    approved_by: "str | None" = None,
) -> bool:
    """
    Transition an approval request to a new status.

    Transition guards (conservative, auditable):
    - APPROVED / REJECTED transitions are only allowed when the current status
      is exactly 'PENDING' AND the request is not expired. Attempting to
      approve/reject an already-decided (APPROVED, REJECTED, APPLIED) or
      expired request returns False without modifying the row.
    - APPLIED transitions are only allowed from APPROVED.
    - EXPIRED can be set from PENDING only.
    - The guard is enforced at both Python level (fast pre-check via fetch)
      and SQL level (WHERE status = 'PENDING' / 'APPROVED' as appropriate).

    Returns True only when exactly one row was updated.
    Returns False on any of: invalid status, wrong current state, expired,
      not found, or DB error.
    """
    new_status = new_status.upper()
    if new_status not in _VALID_APPROVAL_STATUSES:
        logger.warning("update_approval_request_status: invalid status %r", new_status)
        return False

    # ── Python-level pre-check (fast, avoids a silent no-op in the DB) ────────
    if new_status in ("APPROVED", "REJECTED", "EXPIRED", "APPLIED"):
        current = fetch_approval_request(request_id)
        if current is None:
            logger.warning(
                "update_approval_request_status: request %r not found", request_id
            )
            return False
        cur_status = (current.get("status") or "").upper()

        # APPROVED / REJECTED require current status = PENDING
        if new_status in ("APPROVED", "REJECTED"):
            if cur_status != "PENDING":
                logger.warning(
                    "update_approval_request_status: %s is already %s — "
                    "transition to %s rejected (only PENDING can be approved/rejected)",
                    request_id, cur_status, new_status,
                )
                return False
            # Expiry check
            expires_at = current.get("expires_at")
            if expires_at is not None:
                try:
                    from datetime import timezone as _tz
                    _now = datetime.now(_tz.utc)
                    if isinstance(expires_at, str):
                        _exp = datetime.fromisoformat(
                            expires_at.replace("Z", "+00:00")
                        )
                    else:
                        _exp = expires_at
                    if _exp.tzinfo is None:
                        _exp = _exp.replace(tzinfo=_tz.utc)
                    if _exp < _now:
                        logger.warning(
                            "update_approval_request_status: %s is expired — "
                            "transition to %s rejected",
                            request_id, new_status,
                        )
                        return False
                except Exception:
                    pass  # unparseable expiry — allow and let SQL guard handle it

        # APPLIED requires current status = APPROVED
        elif new_status == "APPLIED":
            if cur_status != "APPROVED":
                logger.warning(
                    "update_approval_request_status: %s is %s — "
                    "APPLIED transition requires APPROVED",
                    request_id, cur_status,
                )
                return False

        # EXPIRED requires current status = PENDING
        elif new_status == "EXPIRED":
            if cur_status != "PENDING":
                logger.warning(
                    "update_approval_request_status: %s is %s — "
                    "EXPIRED transition only allowed from PENDING",
                    request_id, cur_status,
                )
                return False

    # ── SQL update with status guard (belt-and-suspenders) ────────────────────
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_APPROVAL_REQUESTS_DDL)
        if new_status in ("APPROVED", "REJECTED"):
            # Belt-and-suspenders: WHERE also guards status=PENDING and expiry
            cur.execute(
                """
                UPDATE approval_requests
                SET    status = %s, approved_by = %s,
                       approved_at = NOW(), updated_at = NOW()
                WHERE  request_id = %s
                  AND  status = 'PENDING'
                  AND  (expires_at IS NULL OR expires_at > NOW())
                """,
                (new_status, approved_by, request_id),
            )
        elif new_status == "APPLIED":
            cur.execute(
                """
                UPDATE approval_requests
                SET    status = %s, updated_at = NOW()
                WHERE  request_id = %s
                  AND  status = 'APPROVED'
                """,
                (new_status, request_id),
            )
        elif new_status == "EXPIRED":
            cur.execute(
                """
                UPDATE approval_requests
                SET    status = %s, updated_at = NOW()
                WHERE  request_id = %s
                  AND  status = 'PENDING'
                """,
                (new_status, request_id),
            )
        else:
            cur.execute(
                """
                UPDATE approval_requests
                SET    status = %s, updated_at = NOW()
                WHERE  request_id = %s
                """,
                (new_status, request_id),
            )
        rows_updated = cur.rowcount
        conn.commit()
        conn.close()
        logger.info(
            "update_approval_request_status: %s → %s (rows=%d)",
            request_id, new_status, rows_updated,
        )
        return rows_updated > 0
    except Exception as exc:
        logger.warning("update_approval_request_status failed: %s", exc)
        return False


def fetch_approval_request(request_id: str) -> "dict | None":
    """Fetch a single approval request by request_id."""
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_APPROVAL_REQUESTS_DDL)
        cur.execute(
            "SELECT * FROM approval_requests WHERE request_id = %s",
            (request_id,),
        )
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("fetch_approval_request failed: %s", exc)
        return None


def fetch_pending_approval_requests() -> "list[dict]":
    """Fetch all PENDING approval requests ordered by created_at DESC."""
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_APPROVAL_REQUESTS_DDL)
        cur.execute(
            """
            SELECT * FROM approval_requests
            WHERE  status = 'PENDING'
            ORDER  BY created_at DESC
            """,
        )
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("fetch_pending_approval_requests failed: %s", exc)
        return []


def fetch_previous_squeeze_score_for_alert(
    ticker: str,
    before_date: "str | None" = None,
) -> "dict | None":
    """
    CHUNK-15: Read the most-recent squeeze_scores row for *ticker* that is
    strictly BEFORE *before_date* (the current run date).

    Used for alert deduplication — compare latest run vs the previous one.

    Returns None when:
      - No prior row exists (first time this ticker appears)
      - before_date is None and there is only one row for this ticker
      - Any DB error occurs

    Parameters
    ----------
    ticker      : equity ticker (case-insensitive)
    before_date : ISO date string (YYYY-MM-DD) or None.
                  When None, returns the second-most-recent row.
    """
    try:
        from utils.db import managed_connection

        ticker = ticker.upper().strip()
        with managed_connection() as conn:
            cur = conn.cursor()
            if before_date is not None:
                cur.execute(
                    """
                    SELECT date, ticker, final_score, squeeze_state,
                           risk_level, dilution_risk_flag,
                           options_pressure_score, unusual_call_activity_flag,
                           explanation_summary, explanation_json
                    FROM   squeeze_scores
                    WHERE  ticker = %s
                      AND  date < %s
                    ORDER  BY date DESC
                    LIMIT  1
                    """,
                    (ticker, before_date),
                )
            else:
                # No cutoff: fetch last two rows, return the older one
                cur.execute(
                    """
                    SELECT date, ticker, final_score, squeeze_state,
                           risk_level, dilution_risk_flag,
                           options_pressure_score, unusual_call_activity_flag,
                           explanation_summary, explanation_json
                    FROM   squeeze_scores
                    WHERE  ticker = %s
                    ORDER  BY date DESC
                    LIMIT  2
                    """,
                    (ticker,),
                )
                rows = cur.fetchall()
                if rows and len(rows) >= 2:
                    return dict(rows[1])
                return None

            row = cur.fetchone()
            return dict(row) if row else None

    except Exception as exc:
        logger.debug("fetch_previous_squeeze_score_for_alert(%s) failed: %s", ticker, exc)
        return None


# ==============================================================================
# TRD-026: Option Candidate Snapshot Persistence
# TRD-050: Extended with full feature-store fields
# ==============================================================================

# Version strings written into every new snapshot row.
# Bump these when the corresponding algorithm changes materially.
_ALGO_VERSION            = "2.0"
_TARGET_ENGINE_VERSION   = "2"     # "delta_only" / "delta_dte_adjusted" branches
_SCENARIO_ENGINE_VERSION = "1"     # square-root theta decay model
_RISK_FRAMEWORK_VERSION  = "1"     # Kelly-fraction PM layer (TRD-046)
_GUARDRAIL_VERSION       = "1"     # fair-value ceiling + freshness gating (TRD-049)


def save_option_candidate_snapshot(
    result: "Any",                    # utils.option_candidates.CandidateResult
    thesis_id: int | None = None,
    run_date: str | None = None,
    thesis_context: dict | None = None,  # extra thesis fields; see docstring
) -> list[int]:
    """
    Persist a CandidateResult (candidates + suppressed state) to
    option_candidate_snapshots.  Returns list of inserted row IDs.

    Writes one row per candidate (rank 1–N), plus one suppression row when
    result.suppressed is True and no candidates exist.  This preserves no-trade
    decisions for later analytics.

    thesis_context may include (TRD-050 additions shown with *):
        thesis_date         — ISO date string
        time_horizon        — free text e.g. "2-4 weeks"
        signal_agreement    — float 0-1
        *entry_low          — thesis entry zone lower bound
        *entry_high         — thesis entry zone upper bound
        *days_to_earnings   — int calendar days to next earnings
        *heat_score         — signal heat score 0-1
        *expected_move_pct  — expected move % from options market

    Column naming:
        iv — the schema column is named ``iv`` (decimal, e.g. 0.35 = 35%).
             The Python OptionCandidate field is ``implied_vol``; this
             function maps it to ``iv`` at insert time.
    """
    from utils.option_candidates import CandidateResult, OptionCandidate

    rd = run_date or _today()
    inserted_ids: list[int] = []
    tc = thesis_context or {}

    try:
        from utils.db import managed_connection
        with managed_connection() as conn:
            with conn.cursor() as cur:
                # Ensure table exists (idempotent)
                cur.execute("""
                    SELECT to_regclass('public.option_candidate_snapshots') AS tbl
                """)
                row = cur.fetchone()
                if row is None or row["tbl"] is None:
                    logger.warning(
                        "option_candidate_snapshots table missing — "
                        "run migrations/004_option_candidate_snapshots_and_outcomes.sql"
                    )
                    return []

                def _insert_row(row_dict: dict) -> int | None:
                    cols = ", ".join(row_dict.keys())
                    placeholders = ", ".join(["%s"] * len(row_dict))
                    sql = (
                        f"INSERT INTO option_candidate_snapshots ({cols}) "
                        f"VALUES ({placeholders}) RETURNING id"
                    )
                    cur.execute(sql, list(row_dict.values()))
                    r = cur.fetchone()
                    return r["id"] if r else None

                base = {
                    "run_date": rd,
                    "ticker": result.ticker,
                    "thesis_id": thesis_id,
                    # Core thesis context (Issue #3 fix)
                    "direction": getattr(result, "thesis_direction", None),
                    "conviction": getattr(result, "thesis_conviction", None),
                    "thesis_date": tc.get("thesis_date"),
                    "time_horizon": tc.get("time_horizon"),
                    "signal_agreement": tc.get("signal_agreement"),
                    # Thesis enrichment (TRD-050)
                    "thesis_entry_low":   tc.get("entry_low"),
                    "thesis_entry_high":  tc.get("entry_high"),
                    "days_to_earnings":   tc.get("days_to_earnings"),
                    "heat_score":         tc.get("heat_score"),
                    "expected_move_pct":  tc.get("expected_move_pct"),
                    # Chain metadata
                    "chain_source": result.chain_source,
                    "underlying_price": result.underlying_price,
                    "suppressed": result.suppressed,
                    "suppression_reason": result.suppression_reason,
                    "rejection_reasons_json": json.dumps(result.rejection_reasons) if result.rejection_reasons else None,
                    # Algorithm versioning (TRD-050)
                    "algo_version":            _ALGO_VERSION,
                    "target_engine_version":   _TARGET_ENGINE_VERSION,
                    "scenario_engine_version": _SCENARIO_ENGINE_VERSION,
                    "risk_framework_version":  _RISK_FRAMEWORK_VERSION,
                    "guardrail_version":       _GUARDRAIL_VERSION,
                }

                if result.suppressed or not result.candidates:
                    # Persist suppression / no-trade row
                    rid = _insert_row(base)
                    if rid:
                        inserted_ids.append(rid)
                else:
                    for rank, c in enumerate(result.candidates, start=1):
                        # Compact scenario summary: only the fields needed for cohort analytics
                        _scenarios = getattr(c, "scenarios", None) or []
                        _scenarios_compact = [
                            {
                                "id":     s.get("scenario_id"),
                                "ret":    s.get("projected_return_pct"),
                                "days":   s.get("days_to_resolution"),
                                "method": s.get("input_method"),
                                "price":  s.get("projected_option_price"),
                            }
                            for s in _scenarios
                            if s.get("input_method") != "insufficient_inputs"
                        ] or None

                        row_dict = {
                            **base,
                            "strategy_preset": c.strategy_preset,
                            "rank": rank,
                            "expiry": c.expiry,
                            "dte": c.dte,
                            "strike": c.strike,
                            "contract_right": c.right,
                            "bid": c.bid,
                            "ask": c.ask,
                            "mid": c.mid,
                            "spread_pct": c.spread_pct,
                            "delta": c.delta,
                            # Additional Greeks (TRD-050) — IBKR only; NULL for yfinance
                            "gamma": getattr(c, "gamma", None),
                            "theta": getattr(c, "theta", None),
                            "vega":  getattr(c, "vega", None),
                            # Issue #1 fix: schema column is "iv", not "implied_vol"
                            "iv": c.implied_vol,
                            "open_interest": c.open_interest,
                            "volume": c.volume,
                            "breakeven": c.breakeven,
                            # Per-contract quote time (TRD-050) — IBKR only; NULL for yfinance
                            "quote_time": getattr(c, "quote_time", None),
                            # Exit plan (TRD-026 mandatory fields)
                            "holding_window_days": c.holding_window_days,
                            "exit_by_date": c.exit_by_date,
                            "underlying_target_1": c.underlying_target_1,
                            "underlying_target_2": c.underlying_target_2,
                            "underlying_stop": c.underlying_stop,
                            "option_take_profit_1": c.option_take_profit_1,
                            "option_take_profit_2": c.option_take_profit_2,
                            "option_stop_loss": c.option_stop_loss,
                            "max_holding_rule": c.max_holding_rule,
                            "event_exit_rule": c.event_exit_rule,
                            "score": c.score,
                            "rationale": c.rationale,
                            "features_json": json.dumps({
                                "delta": c.delta,
                                "gamma": getattr(c, "gamma", None),
                                "theta": getattr(c, "theta", None),
                                "vega":  getattr(c, "vega", None),
                                "iv": c.implied_vol,
                                "spread_pct": c.spread_pct,
                                "dte": c.dte,
                                "oi": c.open_interest,
                                "volume": c.volume,
                            }),
                            # Execution guidance (TRD-031)
                            "recommended_entry_price":  getattr(c, "recommended_entry_price", None),
                            "recommended_order_type":   getattr(c, "recommended_order_type", "limit"),
                            "max_chase_price":          getattr(c, "max_chase_price", None),
                            "entry_style":              getattr(c, "entry_style", None),
                            "entry_rationale":          getattr(c, "entry_rationale", None),
                            "fill_quality_score":       getattr(c, "fill_quality_score", None),
                            "slippage_risk_label":      getattr(c, "slippage_risk_label", None),
                            "skip_if_spread_above_pct": getattr(c, "skip_if_spread_above_pct", None),
                            # V2 target engine (TRD-043)
                            "projected_option_tp1":      getattr(c, "projected_option_tp1", None),
                            "projected_option_tp2":      getattr(c, "projected_option_tp2", None),
                            "projected_option_stop":     getattr(c, "projected_option_stop", None),
                            "projected_tp1_return_pct":  getattr(c, "projected_tp1_return_pct", None),
                            "projected_tp2_return_pct":  getattr(c, "projected_tp2_return_pct", None),
                            "projected_stop_return_pct": getattr(c, "projected_stop_return_pct", None),
                            "target_projection_method":  getattr(c, "target_projection_method", None),
                            # PM/risk layer (TRD-046)
                            "risk_allowed":             getattr(c, "risk_allowed", True),
                            "risk_block_reason":        getattr(c, "risk_block_reason", None),
                            "max_premium_risk_usd":     getattr(c, "max_premium_risk_usd", None),
                            "suggested_contract_count": getattr(c, "suggested_contract_count", None),
                            "position_size_tier":       getattr(c, "position_size_tier", None),
                            "event_risk_policy":        getattr(c, "event_risk_policy", None),
                            "iv_regime_label":          getattr(c, "iv_regime_label", None),
                            "portfolio_concentration_warning": getattr(c, "portfolio_concentration_warning", None),
                            "exit_hierarchy_json":      json.dumps(getattr(c, "exit_hierarchy", [])) if getattr(c, "exit_hierarchy", None) else None,
                            "risk_nav_source":          getattr(c, "risk_nav_source", "model"),
                            # Structure archetype (TRD-048)
                            "structure_archetype":      getattr(c, "structure_archetype", None),
                            "structure_policy_reason":  getattr(c, "structure_policy_reason", None),
                            # Live-entry guardrails (TRD-049 / TRD-050)
                            "entry_action":          getattr(c, "entry_action", "enter_now"),
                            "quote_freshness_label": getattr(c, "quote_freshness_label", "unknown"),
                            "quote_age_seconds":     getattr(c, "quote_age_seconds", None),
                            "fair_value_entry_low":  getattr(c, "fair_value_entry_low", None),
                            "fair_value_entry_high": getattr(c, "fair_value_entry_high", None),
                            "entry_overpay_pct":     getattr(c, "entry_overpay_pct", None),
                            "market_quality_label":  getattr(c, "market_quality_label", "unknown"),
                            "live_guardrail_reason": getattr(c, "live_guardrail_reason", "") or None,
                            # Scenario engine compact summary (TRD-047 / TRD-050)
                            "scenarios_json": json.dumps(_scenarios_compact) if _scenarios_compact else None,
                        }
                        rid = _insert_row(row_dict)
                        if rid:
                            inserted_ids.append(rid)
    except Exception as exc:
        logger.warning("save_option_candidate_snapshot failed: %s", exc)

    return inserted_ids


def save_option_candidate_outcome(
    snapshot_id: int,
    resolution_type: str,
    outcome: dict,
) -> bool:
    """
    Persist an outcome record for a previously stored snapshot.

    Args:
        snapshot_id: FK to option_candidate_snapshots.id
        resolution_type: '1d' | '5d' | '10d' | 'expiry' | 'manual'
        outcome: dict with fields matching option_candidate_outcomes columns
    Returns True on success.
    """
    try:
        from utils.db import managed_connection
        with managed_connection() as conn:
            with conn.cursor() as cur:
                row = {
                    "candidate_snapshot_id": snapshot_id,
                    "resolution_type": resolution_type,
                    **outcome,
                }
                cols = ", ".join(row.keys())
                placeholders = ", ".join(["%s"] * len(row))
                # ON CONFLICT DO UPDATE in case resolution is re-run
                sql = (
                    f"INSERT INTO option_candidate_outcomes ({cols}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT (candidate_snapshot_id, resolution_type) "
                    f"DO UPDATE SET {', '.join(f'{k}=EXCLUDED.{k}' for k in outcome.keys())}"
                )
                cur.execute(sql, list(row.values()))
        return True
    except Exception as exc:
        logger.warning("save_option_candidate_outcome(%d, %s) failed: %s", snapshot_id, resolution_type, exc)
        return False


# ==============================================================================
# TRD-039: OPTIONS STATE HISTORY (daily chain-level snapshots for future research)
# Not used in v1 pre-breakout scoring — collection only.
# ==============================================================================

_OPTIONS_STATE_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS options_state_history (
    ticker              TEXT    NOT NULL,
    snapshot_date       DATE    NOT NULL,
    expiry              DATE    NOT NULL,
    dte                 INTEGER,
    call_volume_total   REAL,
    put_volume_total    REAL,
    call_put_volume_ratio REAL,
    call_oi_total       REAL,
    put_oi_total        REAL,
    call_put_oi_ratio   REAL,
    atm_iv              REAL,
    underlying_price    REAL,
    data_source         TEXT    NOT NULL DEFAULT 'yfinance_chain',
    data_confidence     REAL    DEFAULT 0.7,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, snapshot_date, expiry)
);
"""


def save_options_state_snapshots(records: list[dict]) -> None:
    """
    Upsert daily options-state snapshots into options_state_history.

    Each record must contain: ticker, snapshot_date, expiry.
    Call/put totals, ATM IV, and DTE are optional but encouraged.
    Idempotent on (ticker, snapshot_date, expiry).
    """
    if not records:
        return
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_OPTIONS_STATE_HISTORY_DDL)
        _ensure_table_security(conn, "options_state_history")

        rows = []
        for rec in records:
            ticker = str(rec.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            rows.append((
                ticker,
                str(rec.get("snapshot_date") or _today()),
                str(rec["expiry"]),
                rec.get("dte"),
                rec.get("call_volume_total"),
                rec.get("put_volume_total"),
                rec.get("call_put_volume_ratio"),
                rec.get("call_oi_total"),
                rec.get("put_oi_total"),
                rec.get("call_put_oi_ratio"),
                rec.get("atm_iv"),
                rec.get("underlying_price"),
                rec.get("data_source", "yfinance_chain"),
                float(rec["data_confidence"]) if rec.get("data_confidence") is not None else 0.7,
            ))

        cur.executemany(
            """
            INSERT INTO options_state_history
                (ticker, snapshot_date, expiry, dte,
                 call_volume_total, put_volume_total, call_put_volume_ratio,
                 call_oi_total, put_oi_total, call_put_oi_ratio,
                 atm_iv, underlying_price, data_source, data_confidence)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (ticker, snapshot_date, expiry) DO UPDATE SET
                dte                   = EXCLUDED.dte,
                call_volume_total     = EXCLUDED.call_volume_total,
                put_volume_total      = EXCLUDED.put_volume_total,
                call_put_volume_ratio = EXCLUDED.call_put_volume_ratio,
                call_oi_total         = EXCLUDED.call_oi_total,
                put_oi_total          = EXCLUDED.put_oi_total,
                call_put_oi_ratio     = EXCLUDED.call_put_oi_ratio,
                atm_iv                = EXCLUDED.atm_iv,
                underlying_price      = EXCLUDED.underlying_price,
                data_source           = EXCLUDED.data_source,
                data_confidence       = EXCLUDED.data_confidence
            """,
            rows,
        )
        conn.commit()
        conn.close()
        logger.info("options_state_history: upserted %d records", len(rows))
    except Exception as exc:
        logger.warning("save_options_state_snapshots failed (non-fatal): %s", exc)


def collect_options_state_for_ticker(
    ticker: str,
    snapshot_date: "date | None" = None,
    target_dte_min: int = 20,
    target_dte_max: int = 60,
) -> "dict | None":
    """
    Fetch a daily options-state snapshot for *ticker* via yfinance.

    Selects the nearest expiration within [target_dte_min, target_dte_max] trading days.
    Returns None when no options exist, no suitable expiry is found, or on errors.
    Does NOT persist — callers should batch records and call save_options_state_snapshots().

    Cadence: daily. Date accuracy > feature richness.
    """
    try:
        import yfinance as yf
        import numpy as np
        from datetime import date as _date, timedelta

        snap_date = snapshot_date or _date.today()
        t = yf.Ticker(ticker.upper())

        # Find suitable expiry
        try:
            expirations = t.options
        except Exception:
            return None
        if not expirations:
            return None

        chosen_expiry = None
        chosen_dte = None
        for exp_str in expirations:
            try:
                exp_date = _date.fromisoformat(exp_str)
            except ValueError:
                continue
            dte = (exp_date - snap_date).days
            if target_dte_min <= dte <= target_dte_max:
                chosen_expiry = exp_str
                chosen_dte = dte
                break  # take first suitable expiry (nearest)

        if chosen_expiry is None:
            return None

        try:
            chain = t.option_chain(chosen_expiry)
        except Exception:
            return None

        calls = chain.calls
        puts = chain.puts

        # Underlying price (best effort from chain strikes)
        try:
            info = t.fast_info
            underlying = float(info.last_price) if hasattr(info, "last_price") else None
        except Exception:
            underlying = None

        def _sum(df, col):
            try:
                v = df[col].fillna(0).sum()
                return float(v) if v > 0 else None
            except Exception:
                return None

        call_vol = _sum(calls, "volume")
        put_vol = _sum(puts, "volume")
        call_oi = _sum(calls, "openInterest")
        put_oi = _sum(puts, "openInterest")

        cp_vol_ratio = (call_vol / put_vol) if call_vol and put_vol and put_vol > 0 else None
        cp_oi_ratio = (call_oi / put_oi) if call_oi and put_oi and put_oi > 0 else None

        # ATM IV: IV at strike closest to underlying
        atm_iv = None
        if underlying and not calls.empty:
            try:
                atm_idx = (calls["strike"] - underlying).abs().idxmin()
                atm_iv = float(calls.loc[atm_idx, "impliedVolatility"])
                if atm_iv > 5.0:  # yfinance sometimes returns decimal fractions >1 — clamp
                    atm_iv = None
            except Exception:
                pass

        return {
            "ticker": ticker.upper(),
            "snapshot_date": snap_date.isoformat(),
            "expiry": chosen_expiry,
            "dte": chosen_dte,
            "call_volume_total": call_vol,
            "put_volume_total": put_vol,
            "call_put_volume_ratio": cp_vol_ratio,
            "call_oi_total": call_oi,
            "put_oi_total": put_oi,
            "call_put_oi_ratio": cp_oi_ratio,
            "atm_iv": atm_iv,
            "underlying_price": underlying,
            "data_source": "yfinance_chain",
            "data_confidence": 0.7,
        }
    except Exception as exc:
        logger.debug("collect_options_state_for_ticker(%s) failed: %s", ticker, exc)
        return None


def collect_short_interest_for_ticker(
    ticker: str,
    snapshot_date: "date | None" = None,
) -> "dict | None":
    """
    Fetch short-interest snapshot for *ticker* via yfinance.

    Returns a record dict compatible with save_short_interest_history().
    Returns None on missing data or errors.
    yfinance only provides the most-recent FINRA publication date, not full history.
    Date accuracy: use dateShortInterest as publication_date when available.
    """
    try:
        import yfinance as yf
        from datetime import date as _date, datetime as _dt

        snap = snapshot_date or _date.today()
        info = yf.Ticker(ticker.upper()).info

        shares_short = info.get("sharesShort")
        if shares_short is None:
            return None

        short_pct_float = info.get("shortPercentOfFloat")
        float_shares = info.get("floatShares")
        avg_vol = info.get("averageVolume")
        vendor_short_ratio = info.get("shortRatio")

        # dateShortInterest is a Unix timestamp
        pub_ts = info.get("dateShortInterest")
        if pub_ts:
            try:
                pub_date = _dt.utcfromtimestamp(int(pub_ts)).date().isoformat()
            except Exception:
                pub_date = snap.isoformat()
        else:
            pub_date = snap.isoformat()

        dtc = None
        if shares_short and avg_vol and avg_vol > 0:
            dtc = shares_short / avg_vol

        return {
            "ticker": ticker.upper(),
            "publication_date": pub_date,
            "snapshot_date": snap.isoformat(),
            "source": "yfinance_snapshot",
            "shares_short": float(shares_short) if shares_short else None,
            "short_pct_float": float(short_pct_float) if short_pct_float else None,
            "float_shares": float(float_shares) if float_shares else None,
            "avg_volume_30d": float(avg_vol) if avg_vol else None,
            "computed_dtc_30d": dtc,
            "vendor_short_ratio": float(vendor_short_ratio) if vendor_short_ratio else None,
            "data_confidence_score": 0.7,
        }
    except Exception as exc:
        logger.debug("collect_short_interest_for_ticker(%s) failed: %s", ticker, exc)
        return None


# ==============================================================================
# TRD-034: SETUP WATCHLIST  (pre-breakout pipeline persistence)
# ==============================================================================

_SETUP_WATCHLIST_DDL = """
CREATE TABLE IF NOT EXISTS setup_watchlist (
    run_date          DATE    NOT NULL,
    ticker            TEXT    NOT NULL,
    composite_score   REAL    NOT NULL DEFAULT 0.0,
    pfs_score         REAL,
    psc_score         REAL,
    erm_score         REAL,
    stage2_passed     BOOLEAN NOT NULL DEFAULT FALSE,
    archetype         TEXT,
    invalidation_condition TEXT,
    setup_grade       TEXT,
    key_risk          TEXT,
    stage3_run_at     TIMESTAMPTZ,
    pipeline_version  TEXT    NOT NULL DEFAULT 'v1',
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (run_date, ticker)
);
"""

_SETUP_WATCHLIST_MIGRATE_DDL = [
    "ALTER TABLE setup_watchlist ADD COLUMN IF NOT EXISTS archetype TEXT;",
    "ALTER TABLE setup_watchlist ADD COLUMN IF NOT EXISTS invalidation_condition TEXT;",
    "ALTER TABLE setup_watchlist ADD COLUMN IF NOT EXISTS setup_grade TEXT;",
    "ALTER TABLE setup_watchlist ADD COLUMN IF NOT EXISTS key_risk TEXT;",
    "ALTER TABLE setup_watchlist ADD COLUMN IF NOT EXISTS stage3_run_at TIMESTAMPTZ;",
]


def save_setup_watchlist_rows(rows: list[dict], run_date: "str | None" = None) -> None:
    """
    Upsert pre-breakout setup-watchlist rows.

    Each row must contain: ticker, composite_score.
    Optional: pfs_score, psc_score, erm_score, stage2_passed, pipeline_version.
    Stage 3 fields (archetype, setup_grade, etc.) are written via
    update_setup_watchlist_stage3() after Claude synthesis.
    Idempotent on (run_date, ticker).
    """
    if not rows:
        return
    rd = run_date or _today()
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SETUP_WATCHLIST_DDL)
        _ensure_table_security(conn, "setup_watchlist")
        for stmt in _SETUP_WATCHLIST_MIGRATE_DDL:
            try:
                cur.execute(stmt)
            except Exception:
                pass

        db_rows = []
        for r in rows:
            ticker = str(r.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            db_rows.append((
                rd,
                ticker,
                float(r.get("composite_score", 0.0)),
                r.get("pfs_score"),
                r.get("psc_score"),
                r.get("erm_score"),
                bool(r.get("stage2_passed", False)),
                str(r.get("pipeline_version", "v1")),
            ))

        cur.executemany(
            """
            INSERT INTO setup_watchlist
                (run_date, ticker, composite_score, pfs_score, psc_score,
                 erm_score, stage2_passed, pipeline_version)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_date, ticker) DO UPDATE SET
                composite_score  = EXCLUDED.composite_score,
                pfs_score        = EXCLUDED.pfs_score,
                psc_score        = EXCLUDED.psc_score,
                erm_score        = EXCLUDED.erm_score,
                stage2_passed    = EXCLUDED.stage2_passed,
                pipeline_version = EXCLUDED.pipeline_version
            """,
            db_rows,
        )
        conn.commit()
        conn.close()
        logger.info("setup_watchlist: upserted %d rows for %s", len(db_rows), rd)
    except Exception as exc:
        logger.warning("save_setup_watchlist_rows failed (non-fatal): %s", exc)


def update_setup_watchlist_stage3(
    run_date: str,
    ticker: str,
    stage3_fields: dict,
) -> None:
    """
    Write Stage 3 Claude synthesis fields to an existing setup_watchlist row.

    stage3_fields may contain: archetype, invalidation_condition, setup_grade, key_risk.
    This function does NOT overwrite composite_score or component scores.
    """
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SETUP_WATCHLIST_DDL)
        for stmt in _SETUP_WATCHLIST_MIGRATE_DDL:
            try:
                cur.execute(stmt)
            except Exception:
                pass
        cur.execute(
            """
            UPDATE setup_watchlist
            SET archetype              = %s,
                invalidation_condition = %s,
                setup_grade            = %s,
                key_risk               = %s,
                stage3_run_at          = NOW()
            WHERE run_date = %s AND ticker = %s
            """,
            (
                stage3_fields.get("archetype"),
                stage3_fields.get("invalidation_condition"),
                stage3_fields.get("setup_grade"),
                stage3_fields.get("key_risk"),
                str(run_date),
                ticker.upper(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("update_setup_watchlist_stage3 failed: %s", exc)


def fetch_setup_watchlist(
    run_date: "str | None" = None,
    stage2_only: bool = False,
    limit: int = 100,
) -> list[dict]:
    """
    Fetch setup-watchlist rows for a given run_date (defaults to today).
    If stage2_only=True, returns only rows where stage2_passed=TRUE.
    """
    try:
        rd = run_date or _today()
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SETUP_WATCHLIST_DDL)
        for stmt in _SETUP_WATCHLIST_MIGRATE_DDL:
            try:
                cur.execute(stmt)
            except Exception:
                pass
        where = "WHERE run_date = %s"
        params: list = [rd]
        if stage2_only:
            where += " AND stage2_passed = TRUE"
        cur.execute(
            f"SELECT * FROM setup_watchlist {where} ORDER BY composite_score DESC LIMIT %s",
            params + [limit],
        )
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("fetch_setup_watchlist failed: %s", exc)
        return []


# ==============================================================================
# TRD-040: SETUP WATCHLIST OUTCOMES (pre-breakout learning loop)
# ==============================================================================

_SETUP_OUTCOMES_DDL = """
CREATE TABLE IF NOT EXISTS setup_watchlist_outcomes (
    id                   BIGSERIAL PRIMARY KEY,
    setup_date           DATE    NOT NULL,
    ticker               TEXT    NOT NULL,
    composite_score      REAL,
    pfs_score            REAL,
    psc_score            REAL,
    erm_score            REAL,
    archetype            TEXT,
    ret_5d               REAL,
    ret_10d              REAL,
    ret_20d              REAL,
    ret_40d              REAL,
    ret_5d_excess        REAL,
    ret_10d_excess       REAL,
    ret_20d_excess       REAL,
    ret_40d_excess       REAL,
    mae_20d              REAL,
    mae_40d              REAL,
    mfe_20d              REAL,
    mfe_40d              REAL,
    success_20d          BOOLEAN,
    success_40d          BOOLEAN,
    failed_20d           BOOLEAN,
    confirmed_later      BOOLEAN,
    days_to_confirmation INTEGER,
    market_regime        TEXT,
    resolved_at          DATE,
    mature_20d           BOOLEAN NOT NULL DEFAULT FALSE,
    mature_40d           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (setup_date, ticker)
);
"""


def save_setup_outcome(record: dict) -> None:
    """
    Upsert a resolved setup-watchlist outcome row.

    record must contain: setup_date, ticker.
    All return and label fields are optional (NULL until resolved).
    Call only after the relevant forward window has closed (point-in-time safe).
    """
    if not record:
        return
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SETUP_OUTCOMES_DDL)
        _ensure_table_security(conn, "setup_watchlist_outcomes")

        def _f(key):
            v = record.get(key)
            return float(v) if v is not None else None

        def _b(key):
            v = record.get(key)
            return bool(v) if v is not None else None

        def _i(key):
            v = record.get(key)
            return int(v) if v is not None else None

        cur.execute(
            """
            INSERT INTO setup_watchlist_outcomes
                (setup_date, ticker, composite_score, pfs_score, psc_score,
                 erm_score, archetype,
                 ret_5d, ret_10d, ret_20d, ret_40d,
                 ret_5d_excess, ret_10d_excess, ret_20d_excess, ret_40d_excess,
                 mae_20d, mae_40d, mfe_20d, mfe_40d,
                 success_20d, success_40d, failed_20d,
                 confirmed_later, days_to_confirmation,
                 market_regime, resolved_at, mature_20d, mature_40d)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (setup_date, ticker) DO UPDATE SET
                composite_score      = EXCLUDED.composite_score,
                pfs_score            = EXCLUDED.pfs_score,
                psc_score            = EXCLUDED.psc_score,
                erm_score            = EXCLUDED.erm_score,
                archetype            = EXCLUDED.archetype,
                ret_5d               = EXCLUDED.ret_5d,
                ret_10d              = EXCLUDED.ret_10d,
                ret_20d              = EXCLUDED.ret_20d,
                ret_40d              = EXCLUDED.ret_40d,
                ret_5d_excess        = EXCLUDED.ret_5d_excess,
                ret_10d_excess       = EXCLUDED.ret_10d_excess,
                ret_20d_excess       = EXCLUDED.ret_20d_excess,
                ret_40d_excess       = EXCLUDED.ret_40d_excess,
                mae_20d              = EXCLUDED.mae_20d,
                mae_40d              = EXCLUDED.mae_40d,
                mfe_20d              = EXCLUDED.mfe_20d,
                mfe_40d              = EXCLUDED.mfe_40d,
                success_20d          = EXCLUDED.success_20d,
                success_40d          = EXCLUDED.success_40d,
                failed_20d           = EXCLUDED.failed_20d,
                confirmed_later      = EXCLUDED.confirmed_later,
                days_to_confirmation = EXCLUDED.days_to_confirmation,
                market_regime        = EXCLUDED.market_regime,
                resolved_at          = EXCLUDED.resolved_at,
                mature_20d           = EXCLUDED.mature_20d,
                mature_40d           = EXCLUDED.mature_40d
            """,
            (
                str(record["setup_date"]),
                str(record["ticker"]).strip().upper(),
                _f("composite_score"), _f("pfs_score"), _f("psc_score"),
                _f("erm_score"), record.get("archetype"),
                _f("ret_5d"), _f("ret_10d"), _f("ret_20d"), _f("ret_40d"),
                _f("ret_5d_excess"), _f("ret_10d_excess"),
                _f("ret_20d_excess"), _f("ret_40d_excess"),
                _f("mae_20d"), _f("mae_40d"), _f("mfe_20d"), _f("mfe_40d"),
                _b("success_20d"), _b("success_40d"), _b("failed_20d"),
                _b("confirmed_later"), _i("days_to_confirmation"),
                record.get("market_regime"),
                str(record["resolved_at"]) if record.get("resolved_at") else None,
                bool(record.get("mature_20d", False)),
                bool(record.get("mature_40d", False)),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("save_setup_outcome failed (non-fatal): %s", exc)


def fetch_unresolved_setup_watchlist_rows(
    as_of_date: "date | None" = None,
    min_days_old: int = 5,
    limit: int = 500,
) -> list[dict]:
    """
    Fetch setup_watchlist rows that are old enough to have partial forward data
    but do not yet have a mature outcome row.

    Used by the outcome resolver to find candidates for resolution.
    """
    try:
        from datetime import date as _date
        cutoff = (as_of_date or _date.today()).isoformat()
        conn = _conn()
        cur = conn.cursor()
        cur.execute(_SETUP_WATCHLIST_DDL)
        cur.execute(_SETUP_OUTCOMES_DDL)
        cur.execute(
            """
            SELECT w.*
            FROM   setup_watchlist w
            LEFT JOIN setup_watchlist_outcomes o
                   ON o.setup_date = w.run_date AND o.ticker = w.ticker
            WHERE  w.run_date <= %s::date - %s
              AND  (o.id IS NULL OR o.mature_40d = FALSE)
            ORDER  BY w.run_date ASC, w.composite_score DESC
            LIMIT  %s
            """,
            (cutoff, min_days_old, limit),
        )
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("fetch_unresolved_setup_watchlist_rows failed: %s", exc)
        return []


def fetch_unresolved_snapshots(
    resolution_type: str = "1d",
    limit: int = 200,
) -> list[dict]:
    """
    Return option_candidate_snapshots rows that do not yet have an outcome
    for the given resolution_type and are old enough to be resolved.

    Minimum age:
      1d  → created_at ≥ 1 calendar day ago
      5d  → created_at ≥ 5 calendar days ago
      10d → created_at ≥ 10 calendar days ago
    """
    min_age = {"1d": 1, "5d": 5, "10d": 10}.get(resolution_type, 1)
    try:
        from utils.db import managed_connection
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.*
                    FROM   option_candidate_snapshots s
                    LEFT JOIN option_candidate_outcomes o
                           ON o.candidate_snapshot_id = s.id
                          AND o.resolution_type = %s
                    WHERE  o.id IS NULL
                      AND  s.suppressed = FALSE
                      AND  s.mid IS NOT NULL
                      AND  s.created_at <= NOW() - INTERVAL '%s days'
                    ORDER  BY s.created_at ASC
                    LIMIT  %s
                    """,
                    (resolution_type, min_age, limit),
                )
                rows = cur.fetchall()
                return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("fetch_unresolved_snapshots(%s) failed: %s", resolution_type, exc)
        return []


# ==============================================================================
# RESEARCH-LANE CANDIDATE PERSISTENCE (TRD-057)
# ==============================================================================

_RESEARCH_LANE_DDL = """
CREATE TABLE IF NOT EXISTS research_lane_candidates (
    id                BIGSERIAL PRIMARY KEY,
    date              TEXT        NOT NULL,
    ticker            TEXT        NOT NULL,
    rank              INTEGER,
    total             INTEGER,
    lane              TEXT,
    status            TEXT,
    force_tags        TEXT[],
    score             REAL,
    advanced_to_ai    BOOLEAN DEFAULT FALSE,
    sources           JSONB,
    broad_source_only BOOLEAN,
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (date, ticker)
);
"""


def persist_research_lane_candidates(
    ranked_universe: dict,
    max_candidates: int = None,
) -> int:
    """
    Persist the full prescreened research-lane cohort (from ranked_universe.json)
    before AI selection narrows the funnel.

    ranked_universe: dict of {ticker: {rank, total, status, force_tags, lane,
                                       sources, broad_source_only}}
    Returns the number of rows upserted (0 on failure).

    Idempotent: uses ON CONFLICT(date, ticker) DO UPDATE.
    Degrades safely on DB unavailability — logs warning and returns 0.
    """
    if not ranked_universe:
        return 0

    if max_candidates is None:
        try:
            from config import RESEARCH_LANE_MAX_CANDIDATES
            max_candidates = RESEARCH_LANE_MAX_CANDIDATES
        except ImportError:
            max_candidates = 100

    today = date.today().isoformat()

    # Cap to max_candidates — ranked_universe is already sorted by rank
    items = sorted(ranked_universe.items(), key=lambda kv: kv[1].get("rank", 9999))
    items = items[:max_candidates]

    try:
        from utils.db import managed_connection
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_RESEARCH_LANE_DDL)
                import json as _json
                rows = 0
                for ticker, meta in items:
                    force_tags = meta.get("force_tags") or []
                    raw_sources = meta.get("sources") or []
                    sources_json = _json.dumps(raw_sources) if raw_sources else None
                    broad_only = meta.get("broad_source_only")
                    cur.execute(
                        """
                        INSERT INTO research_lane_candidates
                            (date, ticker, rank, total, lane, status, force_tags, score,
                             sources, broad_source_only)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (date, ticker) DO UPDATE SET
                            rank              = EXCLUDED.rank,
                            total             = EXCLUDED.total,
                            lane              = EXCLUDED.lane,
                            status            = EXCLUDED.status,
                            force_tags        = EXCLUDED.force_tags,
                            score             = EXCLUDED.score,
                            sources           = EXCLUDED.sources,
                            broad_source_only = EXCLUDED.broad_source_only
                        """,
                        (
                            today,
                            ticker.upper(),
                            meta.get("rank"),
                            meta.get("total"),
                            meta.get("lane", "research_broad"),
                            meta.get("status"),
                            force_tags if force_tags else None,
                            meta.get("score"),
                            sources_json,
                            broad_only,
                        ),
                    )
                    rows += 1
                conn.commit()
        return rows
    except Exception as exc:
        logger.warning("persist_research_lane_candidates failed: %s", exc)
        return 0


def mark_research_candidate_advanced(ticker: str, run_date: str = None) -> None:
    """
    Mark a research-lane candidate as advanced to AI synthesis.
    Called from ai_quant after a ticker is selected for AI synthesis.
    Degrades safely on DB unavailability.
    """
    today = run_date or date.today().isoformat()
    try:
        from utils.db import managed_connection
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE research_lane_candidates SET advanced_to_ai=TRUE "
                    "WHERE date=%s AND ticker=%s",
                    (today, ticker.upper()),
                )
            conn.commit()
    except Exception as exc:
        logger.debug("mark_research_candidate_advanced failed for %s: %s", ticker, exc)


# ==============================================================================
# TRD-059: FUNNEL METRICS (daily universe → AI funnel snapshot)
# ==============================================================================

_FUNNEL_METRICS_DDL = """
CREATE TABLE IF NOT EXISTS funnel_metrics (
    run_date                   DATE        NOT NULL PRIMARY KEY,
    raw_universe_count         INTEGER,
    hard_excluded_count        INTEGER,
    lane_excluded_count        INTEGER,
    execution_core_count       INTEGER,
    execution_high_beta_count  INTEGER,
    research_broad_count       INTEGER,
    prescreened_count          INTEGER,
    agreement_eligible_count   INTEGER,
    ai_selected_count          INTEGER,
    active_thesis_count        INTEGER,
    watch_only_count           INTEGER,
    suppressed_count           INTEGER,
    no_trade_count             INTEGER,
    bull_count                 INTEGER,
    bear_count                 INTEGER,
    neutral_count              INTEGER,
    excluded_by_source         JSONB,
    suppression_reasons        JSONB,
    candidates_by_lane         JSONB,
    candidates_by_source       JSONB,
    broad_source_only_candidates INTEGER,
    ai_selected_by_lane        JSONB,
    ai_selected_by_source      JSONB,
    broad_source_only_ai_selected INTEGER,
    broad_source_health        JSONB,
    created_at                 TIMESTAMPTZ DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ DEFAULT NOW()
);
"""

_FUNNEL_ALL_COLUMNS = (
    "raw_universe_count", "hard_excluded_count", "lane_excluded_count",
    "execution_core_count", "execution_high_beta_count", "research_broad_count",
    "prescreened_count", "agreement_eligible_count", "ai_selected_count",
    "active_thesis_count", "watch_only_count", "suppressed_count",
    "no_trade_count", "bull_count", "bear_count", "neutral_count",
    "excluded_by_source", "suppression_reasons",
    # Source/lane attribution (TRD-075)
    "candidates_by_lane", "candidates_by_source", "broad_source_only_candidates",
    "ai_selected_by_lane", "ai_selected_by_source", "broad_source_only_ai_selected",
    # Broad-source health metadata (TRD-056 hardening)
    "broad_source_health",
)

_FUNNEL_JSONB_COLUMNS = frozenset({
    "excluded_by_source", "suppression_reasons",
    "candidates_by_lane", "candidates_by_source",
    "ai_selected_by_lane", "ai_selected_by_source",
    "broad_source_health",
})


def persist_funnel_metrics(metrics: dict, run_date: str = None) -> None:
    """
    Upsert daily funnel metrics row.

    Each call may provide a partial dict — columns not present in the dict
    are COALESCEd from the existing DB row (so two calls, one from
    universe_builder and one from ai_quant, build up the same row without
    clobbering each other).

    Degrades safely on DB unavailability.
    """
    if not metrics:
        return
    rd = run_date or date.today().isoformat()
    try:
        import json as _json
        from utils.db import managed_connection

        # Only carry the known columns — ignore unknown keys
        known = {k: metrics[k] for k in _FUNNEL_ALL_COLUMNS if k in metrics}
        if not known:
            return

        # Serialise JSONB fields
        for jsonb_col in _FUNNEL_JSONB_COLUMNS:
            if jsonb_col in known and isinstance(known[jsonb_col], dict):
                known[jsonb_col] = _json.dumps(known[jsonb_col])

        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_FUNNEL_METRICS_DDL)
                _ensure_table_security(conn, "funnel_metrics")

                col_list  = ", ".join(known.keys())
                val_list  = ", ".join(["%s"] * len(known))
                # COALESCE: preserve existing values for cols not in this call
                update_set = ", ".join(
                    f"{c} = COALESCE(EXCLUDED.{c}, funnel_metrics.{c})"
                    for c in known
                )
                sql = (
                    f"INSERT INTO funnel_metrics (run_date, {col_list}, updated_at) "
                    f"VALUES (%s, {val_list}, NOW()) "
                    f"ON CONFLICT (run_date) DO UPDATE SET "
                    f"{update_set}, updated_at = NOW()"
                )
                cur.execute(sql, [rd] + list(known.values()))
            conn.commit()
        logger.info("funnel_metrics: upserted for %s (%s)", rd, list(known.keys()))
    except Exception as exc:
        logger.warning("persist_funnel_metrics failed (non-fatal): %s", exc)


def fetch_funnel_metrics(
    run_date: str = None,
    history_days: int = 1,
) -> list[dict]:
    """
    Fetch funnel_metrics rows.

    run_date=None → today's row only (history_days ignored).
    history_days > 1 → last N days ordered newest-first.
    Returns [] on any DB error.
    """
    try:
        from utils.db import managed_connection

        rd = run_date or date.today().isoformat()
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_FUNNEL_METRICS_DDL)
                if history_days <= 1:
                    cur.execute(
                        "SELECT * FROM funnel_metrics WHERE run_date = %s",
                        (rd,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM funnel_metrics "
                        "WHERE run_date <= %s "
                        "ORDER BY run_date DESC "
                        "LIMIT %s",
                        (rd, history_days),
                    )
                rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("fetch_funnel_metrics failed: %s", exc)
        return []


def fetch_outcome_attribution(days: int = 90) -> dict:
    """
    Aggregate thesis directional-accuracy metrics by source, lane, and broad_source_only status.

    Metric semantics: `directional_accuracy` is based on `claude_correct` (1 = thesis direction
    matched subsequent price action), NOT on trade P&L or target-hit rate. Only rows where
    `claude_correct IS NOT NULL` are counted — OPEN/pending rows with no verdict are excluded
    from the denominator.

    Joins thesis_outcomes with thesis_cache (for attribution columns added in
    migration 016) and falls back to research_lane_candidates for legacy rows
    that predate the migration.

    Returns dict with:
      by_source            — list of {label, resolved, correct_count, directional_accuracy, avg_return_30d}
      by_lane              — list of same
      broad_source_only_summary — {broad, non_broad} with same metrics
      by_direction         — list of same
      by_governance_state  — list of same; label is A_LIST/STANDARD/PROBATION/QUARANTINE or "unknown"
      days                 — int (query window)
      total_resolved       — int (rows where claude_correct IS NOT NULL)
    """
    empty = {
        "by_source": [], "by_lane": [], "broad_source_only_summary": {},
        "by_direction": [], "by_governance_state": [], "days": days, "total_resolved": 0,
    }
    try:
        import json as _json
        from utils.db import managed_connection

        _SQL = """
            WITH latest_cache AS (
                SELECT DISTINCT ON (ticker, date)
                    ticker, date, candidate_lane, sources, broad_source_only, governance_state
                FROM thesis_cache
                ORDER BY ticker, date, created_at DESC NULLS LAST
            ),
            attributed AS (
                SELECT
                    o.ticker,
                    o.thesis_date,
                    o.direction,
                    o.outcome,
                    o.return_30d,
                    o.claude_correct,
                    COALESCE(tc.candidate_lane, rlc.lane)                 AS candidate_lane,
                    COALESCE(tc.sources,        rlc.sources)               AS sources,
                    COALESCE(tc.broad_source_only, rlc.broad_source_only)  AS broad_source_only,
                    tc.governance_state                                     AS governance_state
                FROM thesis_outcomes o
                LEFT JOIN latest_cache tc
                       ON tc.ticker = o.ticker AND tc.date = o.thesis_date
                LEFT JOIN research_lane_candidates rlc
                       ON rlc.ticker = o.ticker AND rlc.date = o.thesis_date
                WHERE o.thesis_date::date >= CURRENT_DATE - (%s * INTERVAL '1 day')
                  AND o.claude_correct IS NOT NULL
            )
            SELECT * FROM attributed
        """

        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_SQL, (days,))
                rows = [dict(r) for r in cur.fetchall()]

        if not rows:
            return {**empty, "days": days}

        # ── Python-side aggregation ────────────────────────────────────────────
        from collections import defaultdict

        def _make_bucket():
            return {"resolved": 0, "correct": 0, "returns": []}

        by_source:     dict = defaultdict(_make_bucket)
        by_lane:       dict = defaultdict(_make_bucket)
        by_direction:  dict = defaultdict(_make_bucket)
        by_governance: dict = defaultdict(_make_bucket)
        broad_bucket   = {"broad": _make_bucket(), "non_broad": _make_bucket()}

        for r in rows:
            direction    = (r.get("direction") or "UNKNOWN").upper()
            claude_right = (r.get("claude_correct") or 0) == 1
            ret30        = r.get("return_30d")
            sources_raw  = r.get("sources")
            lane         = r.get("candidate_lane") or "unknown"
            is_broad     = r.get("broad_source_only") or False
            gov_state    = r.get("governance_state") or "unknown"

            # Sources: JSONB comes back as list; handle legacy string fallback
            if isinstance(sources_raw, str):
                try:
                    sources_raw = _json.loads(sources_raw)
                except Exception:
                    sources_raw = []
            sources_list = sources_raw or []

            def _acc(bucket):
                bucket["resolved"] += 1
                if claude_right:
                    bucket["correct"] += 1
                if ret30 is not None:
                    bucket["returns"].append(float(ret30))

            for src in (sources_list or ["unknown"]):
                _acc(by_source[src])
            _acc(by_lane[lane])
            _acc(by_direction[direction])
            _acc(by_governance[gov_state])
            _acc(broad_bucket["broad" if is_broad else "non_broad"])

        def _finalise(d: dict) -> list:
            out = []
            for key, b in sorted(d.items(), key=lambda kv: -kv[1]["resolved"]):
                res = b["resolved"]
                acc = round(b["correct"] / res, 4) if res else None
                avg = round(sum(b["returns"]) / len(b["returns"]), 4) if b["returns"] else None
                out.append({
                    "label":                key,
                    "resolved":             res,
                    "correct_count":        b["correct"],
                    "directional_accuracy": acc,
                    "avg_return_30d":       avg,
                })
            return out

        def _finalise_one(b: dict, label: str) -> dict:
            res = b["resolved"]
            acc = round(b["correct"] / res, 4) if res else None
            avg = round(sum(b["returns"]) / len(b["returns"]), 4) if b["returns"] else None
            return {
                "label":                label,
                "resolved":             res,
                "correct_count":        b["correct"],
                "directional_accuracy": acc,
                "avg_return_30d":       avg,
            }

        bso_summary = {
            "broad":     _finalise_one(broad_bucket["broad"],     "broad_source_only"),
            "non_broad": _finalise_one(broad_bucket["non_broad"], "quality_index"),
        }

        return {
            "by_source":                _finalise(by_source),
            "by_lane":                  _finalise(by_lane),
            "by_direction":             _finalise(by_direction),
            "by_governance_state":      _finalise(by_governance),
            "broad_source_only_summary": bso_summary,
            "days":                     days,
            "total_resolved":           len(rows),
        }

    except Exception as exc:
        logger.warning("fetch_outcome_attribution failed: %s", exc)
        return {**empty, "days": days}


# ==============================================================================
# TRD-068: TICKER GOVERNANCE
# ==============================================================================

_GOVERNANCE_VALID_STATES = frozenset({"A_LIST", "STANDARD", "PROBATION", "QUARANTINE"})

_GOVERNANCE_DDL = """
CREATE TABLE IF NOT EXISTS ticker_governance (
    ticker           TEXT        NOT NULL PRIMARY KEY,
    governance_state TEXT        NOT NULL DEFAULT 'STANDARD',
    reason           TEXT,
    notes            TEXT,
    set_by           TEXT        DEFAULT 'pm',
    set_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);
"""


def fetch_ticker_governance(tickers: list[str] = None) -> dict[str, str]:
    """
    Return {TICKER: governance_state} for the given tickers (or all non-STANDARD).

    tickers=None → fetch all rows (for bulk loading).
    Missing tickers default to 'STANDARD'.
    Returns {} on any DB error.
    """
    try:
        from utils.db import managed_connection
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_GOVERNANCE_DDL)
                if tickers:
                    upper_tickers = [t.upper() for t in tickers]
                    placeholders = ",".join(["%s"] * len(upper_tickers))
                    cur.execute(
                        f"SELECT ticker, governance_state FROM ticker_governance "
                        f"WHERE ticker IN ({placeholders})",
                        upper_tickers,
                    )
                else:
                    cur.execute(
                        "SELECT ticker, governance_state FROM ticker_governance "
                        "WHERE governance_state != 'STANDARD'"
                    )
                rows = cur.fetchall()
        return {r["ticker"]: r["governance_state"] for r in rows}
    except Exception as exc:
        logger.debug("fetch_ticker_governance failed: %s", exc)
        return {}


def fetch_ticker_governance_full() -> list[dict]:
    """Return all governance rows with full metadata for PM review."""
    try:
        from utils.db import managed_connection
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_GOVERNANCE_DDL)
                cur.execute(
                    "SELECT ticker, governance_state, reason, notes, set_by, set_at, updated_at "
                    "FROM ticker_governance "
                    "ORDER BY governance_state, ticker"
                )
                rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("fetch_ticker_governance_full failed: %s", exc)
        return []


def set_ticker_governance(
    ticker: str,
    state: str,
    reason: str = None,
    notes: str = None,
    set_by: str = "pm",
) -> bool:
    """
    Upsert a governance entry for ticker.

    Returns True on success, False on invalid state or DB error.
    """
    state = state.upper()
    if state not in _GOVERNANCE_VALID_STATES:
        logger.warning("set_ticker_governance: invalid state %r for %s", state, ticker)
        return False
    try:
        from utils.db import managed_connection
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_GOVERNANCE_DDL)
                _ensure_table_security(conn, "ticker_governance")
                cur.execute(
                    """
                    INSERT INTO ticker_governance
                        (ticker, governance_state, reason, notes, set_by, set_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (ticker) DO UPDATE SET
                        governance_state = EXCLUDED.governance_state,
                        reason           = EXCLUDED.reason,
                        notes            = EXCLUDED.notes,
                        set_by           = EXCLUDED.set_by,
                        set_at           = EXCLUDED.set_at,
                        updated_at       = NOW()
                    """,
                    (ticker.upper(), state, reason, notes, set_by),
                )
            conn.commit()
        logger.info("set_ticker_governance: %s → %s (by %s)", ticker.upper(), state, set_by)
        return True
    except Exception as exc:
        logger.warning("set_ticker_governance failed: %s", exc)
        return False


def remove_ticker_governance(ticker: str) -> bool:
    """Remove a governance entry (ticker reverts to STANDARD default). Returns True on success."""
    try:
        from utils.db import managed_connection
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_GOVERNANCE_DDL)
                cur.execute(
                    "DELETE FROM ticker_governance WHERE ticker = %s",
                    (ticker.upper(),),
                )
            conn.commit()
        logger.info("remove_ticker_governance: %s removed", ticker.upper())
        return True
    except Exception as exc:
        logger.warning("remove_ticker_governance failed: %s", exc)
        return False


# ==============================================================================
# TRD-078: GOVERNANCE RECOMMENDATION / CALIBRATION LAYER
# ==============================================================================

# Advisory-only: these thresholds drive recommendation text but never auto-write
# governance state. PM retains full override authority.
_GOV_REC_THRESHOLDS = {
    "min_sample":            5,     # rows below this → insufficient_sample
    "promote_min_sample":    8,     # stronger evidence required for promotion
    "promote_min_accuracy":  0.70,  # >= 70% directional accuracy → eligible for A_LIST
    "promote_min_return":    0.03,  # avg_return_30d >= 3% required if return data exists
    "probation_max_accuracy":0.45,  # < 45% → move_to_probation
    "quarantine_max_accuracy":0.35, # < 35% → consider_quarantine
}

# Aggregate per-ticker outcomes over the lookback window.
# Joined with ticker_governance for current live state.
_GOV_REC_SQL = """
    SELECT
        o.ticker,
        COUNT(*)                                                     AS resolved,
        COUNT(*) FILTER (WHERE o.claude_correct = 1)                AS correct_count,
        ROUND(
            AVG(o.return_30d) FILTER (WHERE o.return_30d IS NOT NULL)::NUMERIC,
            4
        )::FLOAT                                                     AS avg_return_30d,
        COALESCE(tg.governance_state, 'STANDARD')                   AS current_state
    FROM thesis_outcomes o
    LEFT JOIN ticker_governance tg ON tg.ticker = o.ticker
    WHERE o.thesis_date::date >= CURRENT_DATE - (%s * INTERVAL '1 day')
      AND o.claude_correct IS NOT NULL
    GROUP BY o.ticker, tg.governance_state
    ORDER BY resolved DESC
"""


def _governance_rec_classify(
    resolved: int,
    correct_count: int,
    directional_accuracy: float,
    avg_return_30d,
    current_state: str,
    T: dict,
) -> tuple[str, str]:
    """
    Return (recommendation, reason_summary) for a single ticker.

    Recommendations:
      promote_to_a_list    — strong positive evidence, currently below A_LIST
      move_to_probation    — weak negative evidence, currently A_LIST or STANDARD
      consider_quarantine  — strong negative evidence
      keep_current_state   — evidence consistent with current classification
      insufficient_sample  — not enough resolved theses to make a call
    """
    da = directional_accuracy
    ret_str = f", avg return {avg_return_30d:+.1%}" if avg_return_30d is not None else ""

    if resolved < T["min_sample"]:
        return "insufficient_sample", f"{resolved} resolved (need ≥{T['min_sample']})"

    # Strong negative — quarantine territory
    if da < T["quarantine_max_accuracy"]:
        if current_state == "QUARANTINE":
            return "keep_current_state", (
                f"{resolved} resolved, {da:.0%} accuracy{ret_str} — quarantine justified"
            )
        return "consider_quarantine", (
            f"{resolved} resolved, {da:.0%} accuracy{ret_str} "
            f"(threshold: <{T['quarantine_max_accuracy']:.0%})"
        )

    # Moderate negative — probation territory
    if da < T["probation_max_accuracy"]:
        if current_state in ("PROBATION", "QUARANTINE"):
            return "keep_current_state", (
                f"{resolved} resolved, {da:.0%} accuracy{ret_str} — restriction appropriate"
            )
        return "move_to_probation", (
            f"{resolved} resolved, {da:.0%} accuracy{ret_str} "
            f"(threshold: <{T['probation_max_accuracy']:.0%})"
        )

    # Strong positive — promotion territory
    if da >= T["promote_min_accuracy"] and resolved >= T["promote_min_sample"]:
        return_disqualifies = (
            avg_return_30d is not None and avg_return_30d < T["promote_min_return"]
        )
        if return_disqualifies:
            return "keep_current_state", (
                f"{resolved} resolved, {da:.0%} accuracy but avg return "
                f"{avg_return_30d:+.1%} below {T['promote_min_return']:.0%} threshold"
            )
        if current_state == "A_LIST":
            return "keep_current_state", (
                f"{resolved} resolved, {da:.0%} accuracy{ret_str} — A_LIST justified"
            )
        if current_state in ("STANDARD", "PROBATION"):
            return "promote_to_a_list", (
                f"{resolved} resolved, {da:.0%} accuracy{ret_str}"
            )
        # QUARANTINE — positive signal but manual review required; never auto-promote
        return "keep_current_state", (
            f"{resolved} resolved, {da:.0%} accuracy{ret_str} — "
            "improving but quarantine requires manual PM review"
        )

    # Neutral zone
    return "keep_current_state", (
        f"{resolved} resolved, {da:.0%} accuracy{ret_str} — no change warranted"
    )


def fetch_governance_recommendations(days: int = 90) -> dict:
    """
    Generate evidence-based governance review suggestions from historical thesis outcomes.

    Advisory only — does not write to ticker_governance.
    Uses issuance-time claude_correct verdicts (NOT trade P&L win rate).

    Returns:
        {
            "promote_candidates":    list of ticker dicts,
            "probation_candidates":  list of ticker dicts,
            "quarantine_candidates": list of ticker dicts,
            "keep_current_state":    list of ticker dicts,
            "insufficient_sample":   list of ticker dicts,
            "summary":               {total_tickers, by_recommendation},
            "thresholds_used":       _GOV_REC_THRESHOLDS copy,
            "days":                  int,
        }
    """
    T = _GOV_REC_THRESHOLDS
    empty = {
        "promote_candidates":   [],
        "probation_candidates": [],
        "quarantine_candidates":[],
        "keep_current_state":   [],
        "insufficient_sample":  [],
        "summary": {"total_tickers": 0, "by_recommendation": {}},
        "thresholds_used": dict(T),
        "days": days,
    }
    try:
        from utils.db import managed_connection
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_GOVERNANCE_DDL)  # ensure table exists
                cur.execute(_GOV_REC_SQL, (days,))
                rows = cur.fetchall()

        buckets: dict[str, list] = {
            "promote_to_a_list":    [],
            "move_to_probation":    [],
            "consider_quarantine":  [],
            "keep_current_state":   [],
            "insufficient_sample":  [],
        }

        for r in rows:
            resolved  = int(r["resolved"])
            correct   = int(r["correct_count"])
            da        = round(correct / resolved, 4) if resolved > 0 else 0.0
            avg_ret   = r.get("avg_return_30d")
            current   = r.get("current_state") or "STANDARD"

            rec, reason = _governance_rec_classify(
                resolved, correct, da, avg_ret, current, T
            )
            entry = {
                "ticker":                r["ticker"],
                "current_state":         current,
                "recommendation":        rec,
                "reason_summary":        reason,
                "resolved":              resolved,
                "correct_count":         correct,
                "directional_accuracy":  da,
                "avg_return_30d":        avg_ret,
                "days":                  days,
            }
            buckets[rec].append(entry)

        # Sort each actionable bucket: most resolved first (strongest evidence)
        for key in buckets:
            buckets[key].sort(key=lambda x: x["resolved"], reverse=True)

        by_rec = {k: len(v) for k, v in buckets.items()}
        return {
            "promote_candidates":   buckets["promote_to_a_list"],
            "probation_candidates": buckets["move_to_probation"],
            "quarantine_candidates":buckets["consider_quarantine"],
            "keep_current_state":   buckets["keep_current_state"],
            "insufficient_sample":  buckets["insufficient_sample"],
            "summary": {
                "total_tickers":    sum(by_rec.values()),
                "by_recommendation": by_rec,
            },
            "thresholds_used": dict(T),
            "days": days,
        }

    except Exception as exc:
        logger.warning("fetch_governance_recommendations failed: %s", exc)
        return {**empty, "days": days}
