#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_orders.py -q
