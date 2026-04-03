"""
utils/candidate_archive.py — Persist full priority-scored candidates after each run.

Saves the output of select_top_tickers() to the candidate_snapshots Supabase
table so that future backtests can use real priority scores instead of proxies.

Called from run_master.sh Step 13 (just before or after select_4w_trades).

Usage
-----
    from utils.candidate_archive import archive_candidates

    candidates = select_top_tickers(...)
    archive_candidates(candidates, open_positions=["COIN", "GME", "SAP"])
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)


def archive_candidates(
    candidates: list[dict],
    open_positions: Optional[list[str]] = None,
    run_date: Optional[date] = None,
) -> int:
    """
    Upsert today's priority-scored candidates into candidate_snapshots.

    Parameters
    ----------
    candidates      : list of dicts from select_top_tickers()
    open_positions  : list of tickers that are currently open (flagged separately)
    run_date        : override date (defaults to today)

    Returns
    -------
    Number of rows written.
    """
    if not candidates:
        logger.warning("archive_candidates: nothing to save")
        return 0

    run_date = run_date or date.today()
    open_set = {t.upper() for t in (open_positions or [])}

    try:
        from utils.db import get_connection
        conn = get_connection()
        cur  = conn.cursor()

        # Delete today's existing snapshot so re-runs are idempotent
        cur.execute("DELETE FROM candidate_snapshots WHERE run_date = %s", (run_date,))

        rows_written = 0
        for c in candidates:
            ticker = c.get("ticker", "").upper()
            if not ticker:
                continue
            cur.execute(
                """
                INSERT INTO candidate_snapshots
                    (run_date, ticker, priority_score, signal_agreement_score,
                     pre_resolved_direction, pre_resolved_confidence,
                     equity_rank, composite_z, override_flags,
                     selection_reason, is_open_position)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    run_date,
                    ticker,
                    c.get("priority_score"),
                    c.get("signal_agreement_score"),
                    c.get("pre_resolved_direction"),
                    c.get("pre_resolved_confidence"),
                    c.get("equity_rank"),
                    c.get("composite_z"),
                    json.dumps(c.get("override_flags") or []),
                    c.get("selection_reason"),
                    ticker in open_set,
                ),
            )
            rows_written += 1

        conn.commit()
        conn.close()
        logger.info("Archived %d candidates for %s", rows_written, run_date)
        return rows_written

    except Exception as exc:
        logger.error("archive_candidates failed: %s", exc)
        return 0


def load_candidates_history(
    start_date: Optional[date] = None,
    end_date:   Optional[date] = None,
) -> "pd.DataFrame":
    """
    Load archived candidates from Supabase for backtesting.

    Returns a DataFrame with columns:
        run_date, ticker, priority_score, signal_agreement_score,
        pre_resolved_direction, pre_resolved_confidence,
        equity_rank, composite_z, override_flags, is_open_position

    Filters out dates where bear_market_circuit_breaker dominated
    (i.e. avg priority_score < 5.0 across all candidates — proxy for RISK_OFF).
    """
    import pandas as pd
    from utils.db import get_connection

    conn = get_connection()
    cur  = conn.cursor()

    query = "SELECT * FROM candidate_snapshots WHERE 1=1"
    params: list = []
    if start_date:
        query += " AND run_date >= %s"
        params.append(start_date)
    if end_date:
        query += " AND run_date <= %s"
        params.append(end_date)
    query += " ORDER BY run_date, priority_score DESC"

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])

    # Flag RISK_OFF dates: avg top-10 priority_score < 5 means circuit breaker
    # zeroed most signals — these periods are uninformative for the backtest
    avg_score = df.groupby("run_date")["priority_score"].apply(
        lambda x: x.nlargest(10).mean()
    )
    risk_off_dates = avg_score[avg_score < 5.0].index
    if len(risk_off_dates):
        before = df["run_date"].nunique()
        df = df[~df["run_date"].isin(risk_off_dates)]
        logger.info(
            "Excluded %d RISK_OFF dates from backtest (%d remaining)",
            len(risk_off_dates), df["run_date"].nunique(),
        )

    return df
