#!/usr/bin/env bash
# Full, dependency-free project validation for local and CI use.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

python3 -m py_compile scripts/*.py
bash -n scripts/*.sh
python3 tests/test_detector.py
python3 scripts/benchmark_fixtures.py
git diff --check
printf 'full project checks passed\n'
