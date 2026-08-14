#!/usr/bin/env bash
# Deterministic milestone gate.
#
# Why this exists: the /goal evaluator cannot run commands or read files. It only judges what
# Claude has surfaced in the conversation. So the completion condition must be a single
# unambiguous line that Claude produces by running this script. That makes "done" a fact rather
# than an opinion, and stops an autonomous session from declaring victory early.
#
# Usage:  ./scripts/verify_milestone.sh <1-9>
# Output: exactly one summary line, "MILESTONE <n>: PASS" or "MILESTONE <n>: FAIL".

set -uo pipefail
M="${1:-}"
[[ -z "$M" ]] && { echo "usage: $0 <milestone 1-9>"; exit 2; }

FAILURES=()
check() {  # check <label> <command...>
  local label="$1"; shift
  if "$@" >/tmp/_vm.log 2>&1; then
    echo "  ok    $label"
  else
    echo "  FAIL  $label"
    sed 's/^/          /' /tmp/_vm.log | tail -15
    FAILURES+=("$label")
  fi
}

echo "=== Verifying milestone $M ==="

# ── Universal gates: these must hold at every milestone, no exceptions ──
check "ruff clean"                 uv run ruff check src tests
check "mypy strict on src"         uv run mypy --strict src
check "tests pass"                 uv run pytest -q
check "tests are offline"          uv run pytest -q -p no:cacheprovider --disable-socket
check "no silent imputation"       bash -c '! grep -rnE "fillna\(0|except: *pass|or 0\.0" src/'
check "config parses"              uv run python -c "import yaml,glob;[yaml.safe_load(open(f)) for f in glob.glob(\"config/*.yaml\")]"
check "no secrets committed"       bash -c '! git ls-files | grep -qE "^\.env$|secrets/"'

# ── Per-milestone acceptance criteria (CLAUDE.md §5) ──
case "$M" in
  1) check "fetch 4 markets"       uv run analyze fetch AAPL 7203.T ASML.AS 1299.HK --offline-fixtures
     check "provenance enforced"   uv run pytest -q tests/test_provenance.py
     check "currency normalised"   uv run pytest -q tests/test_normalize.py
     check "quality report exists" uv run pytest -q tests/test_quality.py ;;
  2) check "factor registry"       uv run analyze factors AAPL --offline-fixtures
     check "factor signs asserted" uv run pytest -q tests/test_factor_signs.py
     check "sector-relative z"     uv run pytest -q tests/test_zscore_buckets.py ;;
  3) check "reverse DCF"           uv run pytest -q tests/test_reverse_dcf.py
     check "scenarios weighted"    uv run pytest -q tests/test_scenarios.py ;;
  4) check "attribution"           uv run analyze why AAPL --window 30d --offline-fixtures
     check "unexplained reported"  uv run pytest -q tests/test_attribution.py ;;
  5) check "decision engine"       uv run analyze decide AAPL --offline-fixtures
     check "gates veto"            uv run pytest -q tests/test_gates.py
     check "hysteresis holds"      uv run pytest -q tests/test_hysteresis.py ;;
  6) check "report builds"         uv run analyze report AAPL --offline-fixtures
     check "payload complete"      uv run pytest -q tests/test_payload.py ;;
  7) check "prediction contract"   uv run pytest -q tests/test_prediction_log.py
     check "scorer resolves"       uv run pytest -q tests/test_prediction_score.py ;;
  8) check "no look-ahead"         uv run pytest -q tests/test_no_lookahead.py
     check "walk-forward only"     uv run pytest -q tests/test_walkforward.py
     check "costs modelled"        uv run pytest -q tests/test_costs.py
     check "deflated sharpe"       uv run pytest -q tests/test_deflated_sharpe.py ;;
  9) check "universe screen"       uv run analyze screen --universe config/universe.yaml --offline-fixtures ;;
  *) echo "unknown milestone: $M"; exit 2 ;;
esac

echo
if [[ ${#FAILURES[@]} -eq 0 ]]; then
  echo "MILESTONE $M: PASS"
  exit 0
else
  echo "Unmet criteria: ${FAILURES[*]}"
  echo "MILESTONE $M: FAIL"
  exit 1
fi
