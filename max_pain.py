#!/usr/bin/env python3
"""
================================================================================
MAX PAIN — Options Expiration Price Target
================================================================================
Max pain is the strike price at which the total dollar value of in-the-money
options (calls + puts) is minimized — the point where option BUYERS lose the
most money at expiration.

Market makers who are net-short options profit when options expire worthless.
They hedge their books in ways that create gravitational pull toward max pain,
especially in the final 5-7 days before expiration.

WHAT IT GIVES CLAUDE:
    - Specific price gravity target for upcoming expirations
    - Direction of pull (up/down from current price)
    - Signal strength (days to expiry + total open interest)
    - Multiple expirations for short/medium-term context
    - Pin risk zone (±0.5% around max pain)

ALGORITHM:
    For each candidate strike S (every listed strike):
        call_pain(S) = Σ (S - K) × OI_calls  for all calls where K < S
        put_pain(S)  = Σ (K - S) × OI_puts   for all puts  where K > S
        total_pain(S) = call_pain(S) + put_pain(S)
    Max pain = S where total_pain is MINIMUM

SIGNAL STRENGTH:
    - HIGH   : ≤7 days to expiry AND total OI > 10,000 contracts
    - MEDIUM : ≤14 days OR high OI
    - LOW    : far from expiry or thin options market

CLI:
    python3 max_pain.py AAPL
    python3 max_pain.py GME --expirations 4
================================================================================
"""

import argparse
import sys
import warnings
from datetime import date, datetime
from typing import Optional

warnings.filterwarnings("ignore")

# ── Config ─────────────────────────────────────────────────────────────────────
DEFAULT_N_EXPIRATIONS = 3    # how many upcoming expirations to analyze
HIGH_OI_THRESHOLD     = 10_000
PIN_ZONE_PCT          = 0.5  # ± 0.5% around max pain = pin risk zone


# ── Core calculation ───────────────────────────────────────────────────────────

def _calc_max_pain_for_chain(calls_df, puts_df):
    """
    Calculate max pain strike from a single options chain.
    Returns (max_pain_strike, pain_curve_dict) or (None, {}).
    """
    try:
        call_oi = dict(zip(calls_df["strike"], calls_df["openInterest"].fillna(0)))
        put_oi  = dict(zip(puts_df["strike"],  puts_df["openInterest"].fillna(0)))
        all_strikes = sorted(set(list(call_oi.keys()) + list(put_oi.keys())))

        if len(all_strikes) < 3:
            return None, {}

        pain_at = {}
        for candidate in all_strikes:
            c_pain = sum(
                (candidate - k) * call_oi.get(k, 0)
                for k in all_strikes if k < candidate
            )
            p_pain = sum(
                (k - candidate) * put_oi.get(k, 0)
                for k in all_strikes if k > candidate
            )
            pain_at[candidate] = c_pain + p_pain

        max_pain_strike = min(pain_at, key=pain_at.get)
        return max_pain_strike, pain_at

    except Exception:
        return None, {}


def _signal_strength(days_to_expiry: int, total_oi: int) -> str:
    if days_to_expiry <= 7 and total_oi >= HIGH_OI_THRESHOLD:
        return "HIGH"
    if days_to_expiry <= 14 or total_oi >= HIGH_OI_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def _interpretation(primary: dict, current_price: float) -> str:
    dist  = primary["distance_pct"]
    days  = primary["days_to_expiry"]
    strg  = primary["signal_strength"]
    mp    = primary["max_pain"]
    direc = primary["direction"]

    if strg == "HIGH":
        urgency = f"Strong pull ({days}d to expiry, high OI)"
    elif strg == "MEDIUM":
        urgency = f"Moderate pull ({days}d to expiry)"
    else:
        urgency = f"Weak signal ({days}d to expiry, low OI)"

    if direc == "AT":
        return f"Price pinned near max pain ${mp}. {urgency}."
    elif direc == "UP":
        return (f"Max pain ${mp} is {dist:+.1f}% above — gravitational pull UP into expiry. "
                f"{urgency}.")
    else:
        return (f"Max pain ${mp} is {dist:+.1f}% below — gravitational pull DOWN into expiry. "
                f"{urgency}.")


# ── Main function ──────────────────────────────────────────────────────────────

def get_max_pain(
    ticker: str,
    n_expirations: int = DEFAULT_N_EXPIRATIONS,
) -> dict:
    """
    Calculate max pain for the nearest N option expirations.
    Returns structured dict, or empty dict on failure.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    try:
        t = yf.Ticker(ticker)

        # Current price
        try:
            hist = t.history(period="2d")
            if hist.empty:
                return {}
            current_price = float(hist["Close"].iloc[-1])
        except Exception:
            return {}

        # Available expirations
        try:
            expirations = t.options
        except Exception:
            expirations = []

        if not expirations:
            return {}  # No options market (crypto, some small caps)

        today = date.today()
        results = []

        for exp_str in expirations[:n_expirations]:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                days_to_exp = (exp_date - today).days
                if days_to_exp < 0:
                    continue

                chain = t.option_chain(exp_str)
                calls = chain.calls[["strike", "openInterest"]].copy()
                puts  = chain.puts[["strike",  "openInterest"]].copy()

                max_pain_strike, _ = _calc_max_pain_for_chain(calls, puts)
                if max_pain_strike is None:
                    continue

                total_oi = int(calls["openInterest"].fillna(0).sum() +
                               puts["openInterest"].fillna(0).sum())

                distance_pct = round(
                    (max_pain_strike - current_price) / current_price * 100, 2
                )

                if distance_pct > 0.5:
                    direction = "UP"
                elif distance_pct < -0.5:
                    direction = "DOWN"
                else:
                    direction = "AT"

                strength = _signal_strength(days_to_exp, total_oi)

                # Pin risk zone
                pin_low  = round(max_pain_strike * (1 - PIN_ZONE_PCT / 100), 4)
                pin_high = round(max_pain_strike * (1 + PIN_ZONE_PCT / 100), 4)

                results.append({
                    "expiry":         exp_str,
                    "days_to_expiry": days_to_exp,
                    "max_pain":       max_pain_strike,
                    "distance_pct":   distance_pct,
                    "direction":      direction,
                    "total_oi":       total_oi,
                    "signal_strength": strength,
                    "pin_zone_low":   pin_low,
                    "pin_zone_high":  pin_high,
                })

            except Exception:
                continue

        if not results:
            return {}

        primary = results[0]

        return {
            "current_price":        round(current_price, 4),
            "nearest_expiry":       primary["expiry"],
            "nearest_max_pain":     primary["max_pain"],
            "nearest_distance_pct": primary["distance_pct"],
            "nearest_direction":    primary["direction"],
            "nearest_days_to_expiry": primary["days_to_expiry"],
            "nearest_total_oi":     primary["total_oi"],
            "nearest_signal_strength": primary["signal_strength"],
            "pin_zone_low":         primary["pin_zone_low"],
            "pin_zone_high":        primary["pin_zone_high"],
            "all_expirations":      results,
            "interpretation":       _interpretation(primary, current_price),
        }

    except Exception:
        return {}


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Max Pain — Options expiration price target")
    parser.add_argument("ticker", help="Ticker symbol (e.g. AAPL, GME)")
    parser.add_argument("--expirations", type=int, default=DEFAULT_N_EXPIRATIONS,
                        help=f"Number of expirations to analyze (default: {DEFAULT_N_EXPIRATIONS})")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    print(f"\nCalculating max pain for {ticker}...")
    result = get_max_pain(ticker, n_expirations=args.expirations)

    if not result:
        print("  ERROR: No options data (no listed options, or download failed).")
        sys.exit(1)

    print(f"\n  Current price:  ${result['current_price']}")
    print(f"\n  {'EXPIRY':<12}  {'MAX PAIN':>10}  {'DIST':>7}  {'DIR':>5}  {'OI':>8}  STRENGTH")
    print("  " + "-" * 62)
    for e in result["all_expirations"]:
        print(
            f"  {e['expiry']:<12}  ${e['max_pain']:>9.2f}  "
            f"{e['distance_pct']:>+6.2f}%  {e['direction']:>5}  "
            f"{e['total_oi']:>8,}  {e['signal_strength']}"
        )

    print(f"\n  Pin zone (nearest): ${result['pin_zone_low']} — ${result['pin_zone_high']}")
    print(f"\n  → {result['interpretation']}\n")


if __name__ == "__main__":
    main()
