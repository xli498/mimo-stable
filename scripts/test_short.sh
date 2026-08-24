#!/usr/bin/env bash
# Fast, dependency-free project sanity checks.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

python3 -m py_compile scripts/*.py
python3 scripts/check_version.py
python3 tests/test_detector.py
printf 'short project checks passed\n'
