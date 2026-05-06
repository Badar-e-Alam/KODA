#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
node --test loader.test.mjs
