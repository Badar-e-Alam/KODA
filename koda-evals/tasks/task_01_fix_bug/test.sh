#!/usr/bin/env bash
# Grader for task_01_fix_bug
# Usage: bash test.sh <path_to_agent_workdir>
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_pagination.py -q
