#!/usr/bin/env bash
# Grader: node --test runs all node:test cases. Exit 0 = all pass.
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
node --test cart.test.mjs
