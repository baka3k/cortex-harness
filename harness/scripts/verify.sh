#!/usr/bin/env bash
set -euo pipefail

# Verification gate with conditional critical checks.

CRITICAL_TEST_CMD="${CRITICAL_TEST_CMD:-pytest -q}"
CRITICAL_LINT_CMD="${CRITICAL_LINT_CMD:-}"
CRITICAL_TYPE_CMD="${CRITICAL_TYPE_CMD:-}"
SUPPLEMENTAL_CMDS="${SUPPLEMENTAL_CMDS:-}"

critical_failed=0

run_check() {
  local label="$1"
  local cmd="$2"
  local critical="$3"

  if [[ -z "${cmd// }" ]]; then
    echo "[verify][skip] $label"
    return 0
  fi

  echo "[verify][run] $label: $cmd"
  if bash -lc "$cmd"; then
    echo "[verify][pass] $label"
  else
    if [[ "$critical" == "1" ]]; then
      echo "[verify][fail][critical] $label"
      critical_failed=1
    else
      echo "[verify][warn][supplemental] $label"
    fi
  fi
}

run_check "critical-test" "$CRITICAL_TEST_CMD" "1"
run_check "critical-lint" "$CRITICAL_LINT_CMD" "1"
run_check "critical-type" "$CRITICAL_TYPE_CMD" "1"

if [[ -n "${SUPPLEMENTAL_CMDS// }" ]]; then
  IFS=';' read -r -a extras <<< "$SUPPLEMENTAL_CMDS"
  idx=1
  for c in "${extras[@]}"; do
    run_check "supplemental-$idx" "$c" "0"
    idx=$((idx + 1))
  done
fi

if [[ "$critical_failed" -eq 1 ]]; then
  echo "[verify] Result: FAIL (critical checks failed)"
  exit 1
fi

echo "[verify] Result: PASS (all critical checks passed)"
