#!/bin/bash
# MiMo Stable — Short Test Script
# Purpose: Quick sanity check for model loop detection
# Creates 10 files, runs syntax checks, verifies output

set -euo pipefail

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo "=== MiMo Stable Short Test ==="
echo "PID: $$"
echo "Temp Dir: $TMPDIR"
echo ""

# Phase 1: Create 10 test files
echo "--- Phase 1: Creating 10 test files ---"
for i in $(seq 1 10); do
    cat > "$TMPDIR/test_$i.py" <<PYEOF
#!/usr/bin/env python3
"""Test file $i"""
import json
import os
import sys

def add(a: int, b: int) -> int:
    """Simple addition."""
    return a + b

def test_add():
    assert add(1, 2) == 3
    assert add(-1, 1) == 0
    assert add(0, 0) == 0

if __name__ == "__main__":
    test_add()
    print(f"Test $i: OK - sum={add($i, $i)}")
PYEOF
    echo "  Created: test_$i.py"
done

# Phase 2: Syntax check all files
echo ""
echo "--- Phase 2: Python syntax check ---"
PASSED=0
FAILED=0
for f in "$TMPDIR"/test_*.py; do
    if python3 -m py_compile "$f" 2>&1; then
        PASSED=$((PASSED + 1))
    else
        FAILED=$((FAILED + 1))
    fi
done
echo "  Syntax OK: $PASSED, Failed: $FAILED"

# Phase 3: Execute each file and verify output
echo ""
echo "--- Phase 3: Execution verification ---"
EXEC_OK=0
EXEC_FAIL=0
for f in "$TMPDIR"/test_*.py; do
    output=$(python3 "$f" 2>&1)
    if echo "$output" | grep -q "OK"; then
        EXEC_OK=$((EXEC_OK + 1))
        echo "  ✅ $(basename "$f"): $output"
    else
        EXEC_FAIL=$((EXEC_FAIL + 1))
        echo "  ❌ $(basename "$f"): $output"
    fi
done

# Phase 4: Summary
echo ""
echo "=== Test Summary ==="
echo "  Files created:    10"
echo "  Syntax checks:    $PASSED OK, $FAILED failed"
echo "  Execution checks: $EXEC_OK OK, $EXEC_FAIL failed"

if [ $FAILED -gt 0 ] || [ $EXEC_FAIL -gt 0 ]; then
    echo ""
    echo "❌ SHORT TEST FAILED"
    exit 1
fi

echo ""
echo "✅ SHORT TEST PASSED"
exit 0
