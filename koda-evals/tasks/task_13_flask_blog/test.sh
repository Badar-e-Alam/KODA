#!/usr/bin/env bash
# Grader: run pytest against test_app.py.
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
test -f app.py || { echo "FAIL: app.py was not created"; exit 1; }
python -m pytest test_app.py -q
