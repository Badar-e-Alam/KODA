#!/usr/bin/env bash
# Grader: integration tests must pass + the agent must not have modified
# anything under tests/. (We compare bytes against the canonical version
# stashed alongside the task.)
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"

# Run the failing test.
python -m pytest tests/test_integration.py -q

# Tests file untouchability check — the agent can read tests/ but not
# fix them. Hash the file vs the canonical copy to catch tampering.
EXPECTED_SHA="$(cd "$(dirname "$0")"; sha256sum repo/tests/test_integration.py | awk '{print $1}')"
ACTUAL_SHA="$(sha256sum tests/test_integration.py | awk '{print $1}')"
if [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then
    echo "FAIL: tests/test_integration.py was modified — agent must fix the source code, not the test"
    exit 1
fi

echo "PASS"
