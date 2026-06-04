#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"

if [ ! -f test_temperature.py ]; then
    echo "FAIL: test_temperature.py not found"
    exit 1
fi

# Install pytest-cov if needed (silent)
python -m pip install --quiet pytest-cov 2>/dev/null || true

# Run tests with coverage. Fail if coverage <90% or any test fails.
python -m pytest --cov=temperature --cov-report=term-missing --cov-fail-under=90 test_temperature.py -q
