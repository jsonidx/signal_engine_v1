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
# ==============================================================================

def save_option_candidate_snapshot(
    result: "Any",                    # utils.option_candidates.CandidateResult
    thesis_id: int | None = None,
    run_date: str | None = None,
    thesis_context: dict | None = None,  # extra thesis fields: thesis_date, time_horizon, signal_agreement
) -> list[int]:
    """
    Persist a CandidateResult (candidates + suppressed state) to
    option_candidate_snapshots.  Returns list of inserted row IDs.

    Writes one row per candidate (rank 1–N), plus one suppression row when
    result.suppressed is True and no candidates exist.  This preserves no-trade
    decisions for later analytics.

    thesis_context may include:
        thesis_date       — ISO date string
        time_horizon      — free text e.g. "2-4 weeks"
        signal_agreement  — float 0-1

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
                    SELECT to_regclass('public.option_candidate_snapshots')
                """)
                row = cur.fetchone()
                if row is None or row[0] is None:
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
                    return r[0] if r else None

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
                    # Chain metadata
                    "chain_source": result.chain_source,
                    "underlying_price": result.underlying_price,
                    "suppressed": result.suppressed,
                    "suppression_reason": result.suppression_reason,
                    "rejection_reasons_json": json.dumps(result.rejection_reasons) if result.rejection_reasons else None,
                }

                if result.suppressed or not result.candidates:
                    # Persist suppression / no-trade row
                    rid = _insert_row(base)
                    if rid:
                        inserted_ids.append(rid)
                else:
                    for rank, c in enumerate(result.candidates, start=1):
                        row_dict = {
                            **base,
                            "strategy_preset": c.strategy_preset,
                            "rank": rank,
                            "expiry": c.expiry,
                            "dte": c.dte,
                            "strike": c.strike,
                            "right": c.right,
                            "bid": c.bid,
                            "ask": c.ask,
                            "mid": c.mid,
                            "spread_pct": c.spread_pct,
                            "delta": c.delta,
                            # Issue #1 fix: schema column is "iv", not "implied_vol"
                            "iv": c.implied_vol,
                            "open_interest": c.open_interest,
                            "volume": c.volume,
                            "breakeven": c.breakeven,
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
                                "iv": c.implied_vol,
                                "spread_pct": c.spread_pct,
                                "dte": c.dte,
                                "oi": c.open_interest,
                                "volume": c.volume,
                            }),
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
