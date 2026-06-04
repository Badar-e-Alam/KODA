#!/usr/bin/env bash
# Grader for task_11_long_session.
# Pass criteria:
#   1. ./audit.md exists at the agent's workdir.
#   2. Has at least 18 of the 20 expected file bullets (allow 2 misses for
#      run-to-run variance — the task is about whether compaction kept the
#      agent productive across 20 reads, not perfect enumeration).
#   3. A handful of specific filenames must be mentioned (cheap sanity).
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"

test -f audit.md || { echo "FAIL: audit.md missing"; exit 1; }

# Count well-formed bullets ("- file_NN.py: ...").
COUNT=$(grep -cE "^- file_[0-9]+\.py:" audit.md || true)
if [ "$COUNT" -lt 18 ]; then
    echo "FAIL: audit.md has $COUNT well-formed bullets, need >=18"
    head -25 audit.md
    exit 1
fi

# Spot-check a handful of file names appear (different positions).
for f in file_01.py file_07.py file_14.py file_20.py; do
    if ! grep -q "$f" audit.md; then
        echo "FAIL: $f not mentioned in audit.md"
        exit 1
    fi
done

echo "PASS — $COUNT bullets"
