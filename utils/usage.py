"""
utils/usage.py — Anthropic API usage tracker.

Logs every Claude API call to the Supabase api_usage table.
Used for cost monitoring and per-user billing when the product launches.

Cost reference (as of 2025):
    claude-sonnet-4-6  input=$3.00/M  output=$15.00/M   (haiku-level speed, sonnet quality)
    claude-opus-4-6    input=$15.00/M output=$75.00/M
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Cost per 1M tokens (USD)
_MODEL_COSTS: dict = {
    "claude-sonnet-4-6":        {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001":{"input": 0.80,  "output": 4.00},
    "claude-opus-4-6":          {"input": 15.00, "output": 75.00},
}
_DEFAULT_COSTS = {"input": 3.00, "output": 15.00}


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost for a single API call."""
    rates = _MODEL_COSTS.get(model, _DEFAULT_COSTS)
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def log_api_usage(
    module: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    ticker: str = "",
    cache_hit: bool = False,
    user_id: Optional[str] = None,
) -> None:
    """
    Insert one row into api_usage.

    Call this after every successful (or cache-hit) Claude API call.
    Swallows all exceptions so it never breaks the caller.

    Parameters
    ----------
    module        : 'thesis' | 'transcript' | 'report_analysis'
    model         : Claude model string
    input_tokens  : actual input token count from response.usage
    output_tokens : actual output token count from response.usage
    ticker        : equity/crypto ticker (empty for portfolio-level calls)
    cache_hit     : True when result came from cache (tokens = 0)
    user_id       : Supabase auth UUID (None for CLI / single-user use)
    """
    try:
        from utils.db import get_connection
        cost = compute_cost(model, input_tokens, output_tokens)
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO api_usage
                (user_id, ticker, module, model,
                 input_tokens, output_tokens, cost_usd, cache_hit, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                ticker.upper() if ticker else None,
                module,
                model,
                input_tokens,
                output_tokens,
                cost,
                cache_hit,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        logger.debug(
            "api_usage logged: module=%s ticker=%s tokens=%d+%d cost=$%.4f cache=%s",
            module, ticker, input_tokens, output_tokens, cost, cache_hit,
        )
    except Exception as exc:
        logger.debug("log_api_usage failed (non-fatal): %s", exc)
