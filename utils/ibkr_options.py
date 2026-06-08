"""
IBKR Option Chain Adapter  (TRD-021)
================================================================================
Fetches and normalizes per-contract option chain data for a single ticker.

Primary path  : IB Gateway / TWS via ib_insync.
Fallback path : yfinance (dev/CI without live IBKR session).

The output schema (OptionContract / OptionChainResult) is broker-agnostic so
later scoring and UI work can consume it without knowing IBKR internals.

Setup prerequisites (IBKR path):
  1. IBKR Pro / paper account with market-data subscriptions (L1 + Greeks).
  2. IB Gateway or TWS running locally (port 7497 = TWS paper, 4001 = IB Gateway).
  3. `pip install ib_insync`

Without the IBKR runtime the adapter silently falls back to yfinance, which
provides quotes and open-interest but no live Greeks (delta is approximated
from Black-Scholes using the chain's implied-vol field).
================================================================================
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, List, Optional

log = logging.getLogger(__name__)

# yfinance is a hard project dependency (already used everywhere)
import yfinance as yf


def _get_underlying_price_yf(ticker: str) -> Optional[float]:
    """
    Fetch the latest underlying stock price via yfinance.

    Used instead of IBKR snapshot requests (snapshot mode is prohibited because
    it incurs a per-snapshot entitlement cost on IBKR Pro accounts).

    Returns None when yfinance has no recent data — callers must degrade safely.
    """
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            return None
        price = float(hist["Close"].iloc[-1])
        return price if price > 0 else None
    except Exception as exc:
        log.debug("yfinance underlying price fetch failed for %s: %s", ticker, exc)
        return None


# ── Optional ib_insync import ─────────────────────────────────────────────────

try:
    from ib_insync import IB, Option, Stock  # type: ignore[import]

    _IB_AVAILABLE = True
except ImportError:
    _IB_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════════════════
# Error hierarchy
# ══════════════════════════════════════════════════════════════════════════════


class IBKRError(Exception):
    """Base adapter error — always has a human-readable message."""


class IBKRAuthError(IBKRError):
    """IB Gateway / TWS connection unavailable or not authenticated."""


class IBKREntitlementError(IBKRError):
    """Market-data subscription missing for this ticker / contract type."""


class IBKRNoDataError(IBKRError):
    """No option chain available for the requested ticker."""


class IBKRPartialDataError(IBKRError):
    """Chain returned but some fields (Greeks, OI) are missing."""


# ══════════════════════════════════════════════════════════════════════════════
# Normalized schemas
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class OptionContract:
    """
    Broker-agnostic normalized option contract.
    All price / Greek fields are Optional — None means the data was unavailable
    or unreliable for this contract at fetch time.
    """

    # Identity
    ticker: str
    expiry: str          # "YYYY-MM-DD"
    strike: float
    right: str           # "C" or "P"
    dte: int             # calendar days to expiry at fetch time

    # Quotes
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    mid: Optional[float] = None

    # Liquidity
    volume: Optional[int] = None
    open_interest: Optional[int] = None

    # Greeks (None when not subscribed or unavailable)
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    implied_vol: Optional[float] = None   # decimal, e.g. 0.40 = 40 %

    # Flags
    in_the_money: Optional[bool] = None
    underlying_price: Optional[float] = None

    # Provenance
    source: str = "unknown"   # "ibkr" | "yfinance" | "mock"
    quote_time: Optional[str] = None   # ISO-8601 UTC; per-contract from IBKR; None for yfinance

    # ── Computed helpers ──────────────────────────────────────────────────────

    @property
    def spread(self) -> Optional[float]:
        """Absolute bid/ask spread."""
        if self.bid is not None and self.ask is not None:
            return round(self.ask - self.bid, 4)
        return None

    @property
    def spread_pct(self) -> Optional[float]:
        """Spread as % of mid price (useful for liquidity screening)."""
        m = self.mid or (
            ((self.bid or 0.0) + (self.ask or 0.0)) / 2.0 if self.bid is not None and self.ask is not None else None
        )
        if m and m > 0 and self.spread is not None:
            return round(self.spread / m * 100.0, 2)
        return None


@dataclass
class OptionChainResult:
    """Output of a single chain fetch: list of contracts + metadata."""

    ticker: str
    underlying_price: Optional[float]
    fetch_time: str                         # ISO-8601 UTC
    contracts: List[OptionContract] = field(default_factory=list)
    expiries: List[str] = field(default_factory=list)
    source: str = "unknown"                 # "ibkr" | "yfinance" | "mock"
    error: Optional[str] = None            # set when fetch failed / partially failed
    partial: bool = False                   # True when chain returned but Greeks missing


# ══════════════════════════════════════════════════════════════════════════════
# IBKR adapter  (requires ib_insync + running IB Gateway / TWS)
# ══════════════════════════════════════════════════════════════════════════════


class IBKROptionsAdapter:
    """
    Connect to IB Gateway or TWS and fetch a per-ticker option chain.

    Usage::

        adapter = IBKROptionsAdapter(host="127.0.0.1", port=7497)
        adapter.connect()
        result = adapter.get_chain("AAPL")
        adapter.disconnect()
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,    # 7497 = TWS paper; 7496 = TWS live; 4001 = IB Gateway
        client_id: int = 10,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self._ib: Any = None

    # ── Session management ────────────────────────────────────────────────────

    def connect(self) -> None:
        if not _IB_AVAILABLE:
            raise IBKRAuthError(
                "ib_insync not installed — `pip install ib_insync` and ensure "
                "IB Gateway or TWS is running"
            )
        try:
            self._ib = IB()
            self._ib.connect(self.host, self.port, clientId=self.client_id, timeout=10)
            if not self._ib.isConnected():
                raise IBKRAuthError(
                    f"Cannot connect to IBKR at {self.host}:{self.port} "
                    "— is IB Gateway / TWS running?"
                )
            log.info("IBKR connected: %s:%s (clientId=%s)", self.host, self.port, self.client_id)
        except IBKRAuthError:
            raise
        except Exception as exc:
            raise IBKRAuthError(f"IBKR connection failed: {exc}") from exc

    def disconnect(self) -> None:
        if self._ib is not None and _IB_AVAILABLE:
            try:
                self._ib.disconnect()
            except Exception:
                pass
        self._ib = None

    # ── Chain fetch ───────────────────────────────────────────────────────────

    def get_chain(
        self,
        ticker: str,
        min_dte: int = 7,
        max_dte: int = 180,
        max_expiries: int = 4,
        strikes_around_atm: int = 8,
    ) -> OptionChainResult:
        """
        Fetch a normalized option chain for *ticker*.

        Returns up to *max_expiries* expiry dates in [min_dte, max_dte],
        and up to 2 × *strikes_around_atm* strikes centred on the current price.
        """
        if not _IB_AVAILABLE:
            raise IBKRAuthError("ib_insync not installed")
        if self._ib is None or not self._ib.isConnected():
            raise IBKRAuthError("Not connected — call connect() first")

        fetch_time = datetime.utcnow().isoformat()
        today = date.today()
        sym = ticker.upper()

        try:
            # ── Resolve underlying ────────────────────────────────────────────
            stock = Stock(sym, "SMART", "USD")
            resolved = self._ib.qualifyContracts(stock)
            if not resolved:
                raise IBKRNoDataError(f"Cannot resolve underlying contract for {sym}")
            stock = resolved[0]

            # ── Current price via yfinance (no IBKR snapshot) ────────────────
            # IBKR snapshot-mode requests incur a per-request entitlement cost;
            # yfinance gives an equivalent last-close price at zero cost.
            underlying_price: Optional[float] = _get_underlying_price_yf(sym)

            # ── Option chain parameters (expiries, strikes) ───────────────────
            chains = self._ib.reqSecDefOptParams(
                stock.symbol, "", stock.secType, stock.conId
            )
            if not chains:
                raise IBKRNoDataError(f"No option chain available for {sym}")

            chain_params = chains[0]   # first exchange (usually SMART or CBOE)
            all_expirations: List[str] = sorted(chain_params.expirations)
            all_strikes: List[float] = sorted(chain_params.strikes)

            # ── Filter expiries ───────────────────────────────────────────────
            target_expiries: List[str] = []
            for exp_str in all_expirations:
                exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
                dte = (exp_date - today).days
                if min_dte <= dte <= max_dte:
                    target_expiries.append(exp_str)
                if len(target_expiries) >= max_expiries:
                    break

            if not target_expiries:
                raise IBKRNoDataError(
                    f"No expiries in DTE range {min_dte}–{max_dte} for {sym}"
                )

            # ── Select strikes around ATM ─────────────────────────────────────
            if underlying_price and all_strikes:
                by_dist = sorted(all_strikes, key=lambda s: abs(s - underlying_price))
                selected_strikes = sorted(by_dist[: strikes_around_atm * 2])
            else:
                window = min(len(all_strikes), max(20, strikes_around_atm * 2))
                start = max(0, (len(all_strikes) - window) // 2)
                selected_strikes = all_strikes[start : start + window]

            # ── Build contract list ───────────────────────────────────────────
            raw_specs: List[tuple] = []
            for exp_str in target_expiries:
                exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
                dte = (exp_date - today).days
                exp_iso = exp_date.strftime("%Y-%m-%d")
                for strike in selected_strikes:
                    for right in ("C", "P"):
                        opt = Option(sym, exp_str, strike, right, "SMART")
                        raw_specs.append((opt, exp_iso, dte, strike, right))

            # ── Request market data in batches ────────────────────────────────
            batch_size = 50
            result_contracts: List[OptionContract] = []
            partial = False

            for i in range(0, len(raw_specs), batch_size):
                batch = raw_specs[i : i + batch_size]
                qualified_batch: List[tuple] = []
                for opt, exp_iso, dte, strike, right in batch:
                    try:
                        resolved = self._ib.qualifyContracts(opt)
                    except Exception:
                        resolved = []
                    if resolved:
                        qualified_batch.append((resolved[0], exp_iso, dte, strike, right))

                if not qualified_batch:
                    continue

                tickers_data = []
                for opt, _, _, _, _ in qualified_batch:
                    # tick 100 = option volume, 101 = option open interest, 106 = IV + greeks
                    td = self._ib.reqMktData(opt, "100,101,106", False, False)
                    tickers_data.append(td)

                self._ib.sleep(2.5)

                for td, (_, exp_iso, dte, strike, right) in zip(tickers_data, qualified_batch):
                    contract = self._normalize_ticker(
                        td, sym, exp_iso, dte, strike, right, underlying_price
                    )
                    if contract.delta is None:
                        partial = True
                    result_contracts.append(contract)

            expiries_iso = [
                datetime.strptime(e, "%Y%m%d").strftime("%Y-%m-%d")
                for e in target_expiries
            ]
            return OptionChainResult(
                ticker=sym,
                underlying_price=underlying_price,
                fetch_time=fetch_time,
                contracts=result_contracts,
                expiries=expiries_iso,
                source="ibkr",
                partial=partial,
            )

        except (IBKRError,):
            raise
        except Exception as exc:
            log.exception("IBKR chain fetch error for %s", sym)
            return OptionChainResult(
                ticker=sym,
                underlying_price=None,
                fetch_time=fetch_time,
                error=str(exc),
                source="ibkr",
            )

    # ── Normalization ─────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_ticker(
        td: Any,
        ticker: str,
        exp_iso: str,
        dte: int,
        strike: float,
        right: str,
        underlying_price: Optional[float],
    ) -> OptionContract:
        """
        Convert an ib_insync Ticker snapshot to an OptionContract.

        IBKR generic tick mapping (passed as "100,101,106" in reqMktData):
          100 → optVolume        option daily trading volume
          101 → optOpenInterest  option open interest
          106 → modelGreeks      implied volatility + delta/gamma/theta/vega
        """

        def safe(val: Any) -> Optional[float]:
            try:
                v = float(val)
                return v if (v == v) and abs(v) < 1e9 else None
            except (TypeError, ValueError):
                return None

        def safe_pos(val: Any) -> Optional[float]:
            v = safe(val)
            return v if v is not None and v > 0 else None

        bid = safe(td.bid)
        ask = safe_pos(td.ask)
        last = safe_pos(td.last)

        mid: Optional[float] = None
        if bid is not None and bid >= 0 and ask is not None and ask > 0:
            mid = round((bid + ask) / 2.0, 4)

        # tick 100 → optVolume  (option daily trading volume)
        # tick 101 → optOpenInterest  (option open interest — separate tick from volume)
        # td.volume is the underlying's intraday volume; not useful here.
        vol_raw = getattr(td, "optVolume", None)        # option daily volume (tick 100)
        oi_raw  = getattr(td, "optOpenInterest", None)  # option open interest (tick 101)
        vol: Optional[int] = None
        oi: Optional[int] = None
        try:
            if vol_raw is not None and float(vol_raw) == float(vol_raw):
                vol = int(float(vol_raw))
        except (TypeError, ValueError):
            pass
        try:
            if oi_raw is not None and float(oi_raw) == float(oi_raw):
                oi = int(float(oi_raw))
        except (TypeError, ValueError):
            pass

        # Greeks come from model / bid / ask greeks (tick 106 / 107)
        greeks = (
            getattr(td, "modelGreeks", None)
            or getattr(td, "bidGreeks", None)
            or getattr(td, "askGreeks", None)
        )
        delta = safe(greeks.delta) if greeks else None
        gamma = safe(greeks.gamma) if greeks else None
        theta = safe(greeks.theta) if greeks else None
        vega = safe(greeks.vega) if greeks else None
        iv = safe(greeks.impliedVol) if greeks else None
        if iv is not None and iv <= 0.001:
            iv = None

        return OptionContract(
            ticker=ticker,
            expiry=exp_iso,
            strike=strike,
            right=right,
            dte=dte,
            bid=bid,
            ask=ask,
            last=last,
            mid=mid,
            volume=vol,
            open_interest=oi,
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            implied_vol=iv,
            underlying_price=underlying_price,
            source="ibkr",
        )


# ══════════════════════════════════════════════════════════════════════════════
# yfinance fallback
# ══════════════════════════════════════════════════════════════════════════════


def _yfinance_chain(
    ticker: str,
    min_dte: int = 7,
    max_dte: int = 180,
    max_expiries: int = 4,
    strikes_around_atm: int = 8,
) -> OptionChainResult:
    """
    Fetch option chain via yfinance.
    Greeks are not provided by yfinance; delta is approximated from
    Black-Scholes using each contract's implied-volatility field.
    """
    fetch_time = datetime.utcnow().isoformat()
    today = date.today()
    sym = ticker.upper()

    try:
        t = yf.Ticker(sym)
        hist = t.history(period="5d")
        if hist.empty:
            return OptionChainResult(
                ticker=sym, underlying_price=None, fetch_time=fetch_time,
                error="No price history from yfinance", source="yfinance",
            )
        underlying_price = float(hist["Close"].iloc[-1])

        expirations = t.options
        if not expirations:
            return OptionChainResult(
                ticker=sym, underlying_price=underlying_price, fetch_time=fetch_time,
                error="No option expirations available", source="yfinance",
            )

        # Filter expiries by DTE
        target_expiries: List[str] = []
        for exp_str in expirations:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            if min_dte <= dte <= max_dte:
                target_expiries.append(exp_str)
            if len(target_expiries) >= max_expiries:
                break

        if not target_expiries:
            return OptionChainResult(
                ticker=sym, underlying_price=underlying_price, fetch_time=fetch_time,
                error=f"No expiries in DTE range {min_dte}–{max_dte}d for {sym}",
                source="yfinance",
            )

        contracts: List[OptionContract] = []
        for exp_str in target_expiries:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            chain = t.option_chain(exp_str)

            for df, right in ((chain.calls, "C"), (chain.puts, "P")):
                if df is None or df.empty:
                    continue
                df = df.copy()
                df["_dist"] = abs(df["strike"] - underlying_price)
                df = df.nsmallest(strikes_around_atm * 2, "_dist")
                for _, row in df.iterrows():
                    c = _normalize_yf_row(row, sym, exp_str, dte, right, underlying_price)
                    contracts.append(c)

        return OptionChainResult(
            ticker=sym,
            underlying_price=underlying_price,
            fetch_time=fetch_time,
            contracts=contracts,
            expiries=target_expiries,
            source="yfinance",
            partial=True,   # Greeks are approximated, not broker-supplied
        )

    except Exception as exc:
        log.exception("yfinance chain error for %s", sym)
        return OptionChainResult(
            ticker=sym, underlying_price=None, fetch_time=fetch_time,
            error=str(exc), source="yfinance",
        )


def _normalize_yf_row(
    row: Any,
    ticker: str,
    expiry: str,
    dte: int,
    right: str,
    underlying_price: float,
) -> OptionContract:
    """Normalize one yfinance option chain row into an OptionContract."""

    def _safe(val: Any) -> Optional[float]:
        try:
            v = float(val)
            return v if (v == v) and abs(v) < 1e9 else None
        except (TypeError, ValueError):
            return None

    def _safe_int(val: Any) -> Optional[int]:
        try:
            v = float(val)
            if v != v:
                return None
            i = int(v)
            return i if i >= 0 else None
        except (TypeError, ValueError):
            return None

    bid = _safe(row.get("bid"))
    ask = _safe(row.get("ask"))
    last = _safe(row.get("lastPrice"))

    # Treat zero bid as valid (contract might just have no buyers)
    if bid is not None and bid < 0:
        bid = None

    mid: Optional[float] = None
    if bid is not None and ask is not None and ask > 0:
        mid = round((bid + ask) / 2.0, 4)
    elif ask is not None and ask > 0:
        mid = ask  # best effort when bid = 0

    iv_raw = _safe(row.get("impliedVolatility"))
    iv = iv_raw if iv_raw is not None and iv_raw > 0.01 else None

    strike = float(row["strike"])
    oi = _safe_int(row.get("openInterest"))
    vol = _safe_int(row.get("volume"))

    in_the_money: Optional[bool] = None
    try:
        itm = row.get("inTheMoney")
        if itm is not None:
            in_the_money = bool(itm)
    except Exception:
        pass

    delta = _approx_delta(underlying_price, strike, iv, dte, right) if iv else None

    return OptionContract(
        ticker=ticker,
        expiry=expiry,
        strike=strike,
        right=right,
        dte=dte,
        bid=bid,
        ask=ask,
        last=last,
        mid=mid,
        volume=vol,
        open_interest=oi,
        delta=delta,
        implied_vol=iv,
        in_the_money=in_the_money,
        underlying_price=underlying_price,
        source="yfinance",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Black-Scholes delta approximation (no dividends, r = 0)
# ══════════════════════════════════════════════════════════════════════════════


def _approx_delta(
    S: float, K: float, iv: float, dte: int, right: str
) -> Optional[float]:
    """
    Simplified Black-Scholes delta (r = 0, no dividends).
    Used as a fallback when broker Greeks are not available.
    """
    try:
        T = dte / 365.0
        if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
            return None
        d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
        cdf = _norm_cdf(d1)
        return round(cdf if right == "C" else cdf - 1.0, 4)
    except Exception:
        return None


def _norm_cdf(x: float) -> float:
    """Abramowitz & Stegun approximation of Φ(x)."""
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    poly = t * (
        0.319381530
        + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429)))
    )
    phi = math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
    p = 1.0 - phi * poly
    return p if x >= 0 else 1.0 - p


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════


def get_option_chain(
    ticker: str,
    min_dte: int = 7,
    max_dte: int = 180,
    max_expiries: int = 4,
    strikes_around_atm: int = 8,
    ibkr_host: str = "127.0.0.1",
    ibkr_port: int = 7497,
    ibkr_client_id: int = 10,
    force_yfinance: bool = False,
) -> OptionChainResult:
    """
    Fetch a normalized option chain for *ticker*.

    Tries IBKR first (if ib_insync is installed and a session is reachable),
    falls back to yfinance when IBKR is unavailable.

    Args:
        force_yfinance: Skip IBKR entirely, useful in tests.
    """
    if not force_yfinance and _IB_AVAILABLE:
        adapter = IBKROptionsAdapter(ibkr_host, ibkr_port, ibkr_client_id)
        try:
            adapter.connect()
            return adapter.get_chain(
                ticker,
                min_dte=min_dte,
                max_dte=max_dte,
                max_expiries=max_expiries,
                strikes_around_atm=strikes_around_atm,
            )
        except IBKRAuthError as exc:
            log.warning("IBKR unavailable (%s) — falling back to yfinance", exc)
        except IBKRNoDataError:
            raise
        except Exception as exc:
            log.warning("IBKR error (%s) — falling back to yfinance", exc)
        finally:
            adapter.disconnect()

    return _yfinance_chain(
        ticker,
        min_dte=min_dte,
        max_dte=max_dte,
        max_expiries=max_expiries,
        strikes_around_atm=strikes_around_atm,
    )
