#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"

python -m pytest test_analytics.py -q

OUT=$(python main.py)
echo "$OUT"

echo "$OUT" | grep -q "Total revenue: \$245.50" || { echo "FAIL: wrong total"; exit 1; }
echo "$OUT" | grep -q "Top customer: Alice (\$120.00)" || { echo "FAIL: wrong top customer"; exit 1; }
echo "PASS"
