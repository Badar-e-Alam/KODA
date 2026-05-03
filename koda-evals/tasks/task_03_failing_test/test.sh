#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Verify the agent didn't cheat by editing the tests
if ! diff -q "$SCRIPT_DIR/repo/test_strutils.py" "$WORKDIR/test_strutils.py" > /dev/null; then
    echo "FAIL: agent modified test_strutils.py (not allowed)"
    exit 1
fi

cd "$WORKDIR"
python -m pytest test_strutils.py -q
