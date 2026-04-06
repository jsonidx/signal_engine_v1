#!/usr/bin/env python3
"""
verify_migration.py — Confirm the Supabase migration is applied and caching works.

Run AFTER applying the migration:
    python3 scripts/verify_migration.py

Expected output on success:
    [OK] blacklist table exists
    [OK] ticker_metadata table exists
    [OK] fundamentals table exists
    [OK] Blacklist round-trip passed
    [OK] Universe cache round-trip passed
    [OK] Fundamentals cache round-trip passed
    Migration verified — caching is active.
"""

import sys
import os

# Make sure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = "[OK]" if ok else "[FAIL]"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {status} {label}{suffix}")
    return ok


def main() -> int:
    errors = 0

    # ── 1. Table existence ────────────────────────────────────────────────────
    print("\nChecking tables exist...")
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        for table in ("blacklist", "ticker_metadata", "fundamentals"):
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s) AS exists",
                (table,),
            )
            row = cur.fetchone()
            exists = row["exists"] if row else False
            if not check(f"{table} table exists", exists):
                errors += 1
                print(f"    → Run: psql \"$DATABASE_URL\" -f migrations/001_add_blacklist_and_metadata.sql")
        conn.close()
    except Exception as exc:
        print(f"  [FAIL] Cannot connect to Supabase: {exc}")
        print("    → Check DATABASE_URL is set correctly")
        return 1

    # ── 2. Blacklist round-trip ───────────────────────────────────────────────
    print("\nChecking blacklist round-trip...")
    try:
        from db_cache import add_to_blacklist, get_active_blacklist, remove_from_blacklist
        add_to_blacklist("_VERIFY_TEST_", reason="migration_verify")
        bl = get_active_blacklist()
        found = "_VERIFY_TEST_" in bl
        if not check("Blacklist round-trip passed", found, f"{len(bl)} active entries"):
            errors += 1
        remove_from_blacklist("_VERIFY_TEST_")
    except Exception as exc:
        print(f"  [FAIL] Blacklist error: {exc}")
        errors += 1

    # ── 3. Universe cache round-trip ──────────────────────────────────────────
    print("\nChecking universe cache round-trip...")
    try:
        from db_cache import save_universe_results, get_cached_universe
        test_tickers = [f"_TEST{i:03d}_" for i in range(60)]  # 60 fake tickers (> min=50)
        save_universe_results(test_tickers)
        cached = get_cached_universe(max_age_hours=1)
        ok = cached is not None and len(cached) >= 50
        if not check("Universe cache round-trip passed", ok,
                     f"got {len(cached) if cached else 0} tickers back"):
            errors += 1
        # Cleanup
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM ticker_metadata WHERE ticker LIKE '_TEST%_'")
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"  [FAIL] Universe cache error: {exc}")
        errors += 1

    # ── 4. Fundamentals cache round-trip ─────────────────────────────────────
    print("\nChecking fundamentals cache round-trip...")
    try:
        from fundamentals_cache import save_to_cache, get_cached, clear_ticker
        test_data = {"pe_ratio": 25.0, "revenue_growth": 0.15, "_verify": True}
        save_to_cache("_VERIFY_FUND_", test_data)
        result = get_cached("_VERIFY_FUND_")
        ok = result is not None and result.get("_verify") is True
        if not check("Fundamentals cache round-trip passed", ok):
            errors += 1
        clear_ticker("_VERIFY_FUND_")
    except Exception as exc:
        print(f"  [FAIL] Fundamentals cache error: {exc}")
        errors += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if errors == 0:
        print("  Migration verified — caching is active. Next pipeline run will be warm.\n")
        return 0
    else:
        print(f"  {errors} check(s) failed. Fix the issues above before running the pipeline.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
