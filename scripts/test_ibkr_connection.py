#!/usr/bin/env python3
"""Minimal local IBKR Gateway / TWS connectivity test for this repo."""

from __future__ import annotations

import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.ibkr_options import IBKRAuthError, IBKROptionsAdapter


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got: {value!r}") from exc


def main() -> int:
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = _env_int("IBKR_PORT", 4002)
    client_id = _env_int("IBKR_CLIENT_ID", 10)
    ticker = os.getenv("IBKR_TEST_TICKER", "AAPL").upper()

    print(f"Connecting to IBKR at {host}:{port} with clientId={client_id}")

    adapter = IBKROptionsAdapter(host=host, port=port, client_id=client_id)
    try:
        adapter.connect()
        result = adapter.get_chain(
            ticker,
            min_dte=7,
            max_dte=90,
            max_expiries=2,
            strikes_around_atm=3,
        )
    except IBKRAuthError as exc:
        print(f"IBKR auth/connect error: {exc}")
        return 2
    except Exception as exc:
        print(f"IBKR chain fetch failed: {exc}")
        return 3
    finally:
        adapter.disconnect()

    print(f"source={result.source}")
    print(f"ticker={result.ticker}")
    print(f"underlying_price={result.underlying_price}")
    print(f"expiries={result.expiries[:5]}")
    print(f"contracts={len(result.contracts)}")
    print(f"partial={result.partial}")
    print(f"error={result.error}")

    if result.contracts:
        c = result.contracts[0]
        print(
            "sample_contract="
            f"{c.right} {c.strike} {c.expiry} "
            f"bid={c.bid} ask={c.ask} mid={c.mid} "
            f"delta={c.delta} oi={c.open_interest} vol={c.volume}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
