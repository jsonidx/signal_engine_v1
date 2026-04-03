#!/usr/bin/env bash
# Quick smoke-test for the two new rankings API endpoints.
# Usage: bash test_rankings_endpoints.sh [base_url]
# Default base_url: http://localhost:8000

BASE="${1:-http://localhost:8000}"

pass() { echo "  ✓ $1"; }
fail() { echo "  ✗ $1"; exit 1; }

echo ""
echo "Testing rankings endpoints at $BASE"
echo "────────────────────────────────────"

# ── /api/rankings/latest ──────────────────────────────────────────────────────
echo ""
echo "1. GET /api/rankings/latest"
RESP=$(curl -s -w "\n%{http_code}" "$BASE/api/rankings/latest")
CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -1)

[ "$CODE" = "200" ] && pass "HTTP 200" || fail "Expected 200, got $CODE"

DA=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data_available',''))" 2>/dev/null)
[ "$DA" = "True" ] || [ "$DA" = "true" ] \
  && pass "data_available=true" \
  || echo "  ⚠ data_available=$DA (table may be empty — run pipeline first)"

COUNT=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('count',0))" 2>/dev/null)
echo "  → count=$COUNT rows"

# ── /api/rankings/history (no ticker) ────────────────────────────────────────
echo ""
echo "2. GET /api/rankings/history?days=7"
RESP=$(curl -s -w "\n%{http_code}" "$BASE/api/rankings/history?days=7")
CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -1)

[ "$CODE" = "200" ] && pass "HTTP 200" || fail "Expected 200, got $CODE"
COUNT=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('count',0))" 2>/dev/null)
echo "  → count=$COUNT rows (last 7 days)"

# ── /api/rankings/history?ticker=AAPL ────────────────────────────────────────
echo ""
echo "3. GET /api/rankings/history?ticker=AAPL&days=30"
RESP=$(curl -s -w "\n%{http_code}" "$BASE/api/rankings/history?ticker=AAPL&days=30")
CODE=$(echo "$RESP" | tail -1)
[ "$CODE" = "200" ] && pass "HTTP 200" || fail "Expected 200, got $CODE"

# ── Validation: days out of range ────────────────────────────────────────────
echo ""
echo "4. GET /api/rankings/history?days=999 (should be 422 — days le=365)"
RESP=$(curl -s -w "\n%{http_code}" "$BASE/api/rankings/history?days=999")
CODE=$(echo "$RESP" | tail -1)
[ "$CODE" = "422" ] && pass "HTTP 422 (FastAPI validation)" || echo "  ⚠ Got $CODE (expected 422)"

echo ""
echo "────────────────────────────────────"
echo "Done."
echo ""
