#!/usr/bin/env bash
# One command to verify the grader: runs every ground-truth + robustness test and reports
# pass/fail. Exit 0 only if all pass. Usage:  bash scripts/run_tests.sh
set -uo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] && source .venv/bin/activate 2>/dev/null || true

tests=(test_centering test_defects test_quality test_robustness test_pricing)
fail=0
for t in "${tests[@]}"; do
  if python3 "scripts/$t.py" >/tmp/vtest_$t.log 2>&1; then
    echo "  PASS  $t"
  else
    echo "  FAIL  $t   (see /tmp/vtest_$t.log)"
    tail -3 "/tmp/vtest_$t.log" | sed 's/^/        /'
    fail=1
  fi
done

echo
if [ "$fail" -eq 0 ]; then
  echo "All tests passed. (Accuracy vs labeled cards: python3 scripts/validate_labeled.py)"
else
  echo "Some tests FAILED."
fi
exit "$fail"
