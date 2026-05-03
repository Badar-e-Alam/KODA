#!/usr/bin/env bash
# Pass criteria:
#   1. All tests pass
#   2. _write_csv helper exists in report.py
#   3. Module is meaningfully shorter than the original (duplication actually reduced)
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"

python -m pytest test_report.py -q

if ! grep -q "_write_csv" report.py; then
    echo "FAIL: expected a _write_csv helper in report.py"
    exit 1
fi

LINES=$(grep -c -v '^\s*$' report.py || true)
if [ "$LINES" -gt 25 ]; then
    echo "FAIL: report.py is still $LINES non-blank lines — duplication not reduced"
    exit 1
fi

echo "PASS"
