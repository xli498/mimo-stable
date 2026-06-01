#!/bin/bash
# MiMo Stable — Long Test Script
# Purpose: Extended test with large file reads, checkpoints, and complex operations
# Design: Simulates a real model interaction session with multiple phases

set -euo pipefail

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

CHECKPOINTS_FILE="$TMPDIR/checkpoints.txt"
RESULTS_FILE="$TMPDIR/results.json"
echo "checkpoints:" > "$CHECKPOINTS_FILE"

log_checkpoint() {
    local msg="$1"
    local ts
    ts=$(date -Iseconds)
    echo "  - time: $ts" >> "$CHECKPOINTS_FILE"
    echo "    message: \"$msg\"" >> "$CHECKPOINTS_FILE"
    echo "[CHECKPOINT] $ts — $msg"
}

echo "========================================="
echo "  MiMo Stable — Long Test"
echo "========================================="
echo "Temp Dir: $TMPDIR"
echo "Started:  $(date -Iseconds)"
echo ""

# Phase 1: Generate a large file with varied content
log_checkpoint "Phase 1: Generating large test file"
LARGE_FILE="$TMPDIR/large_data.txt"
echo "  Generating 50KB test file..."

python3 - <<'PYEOF' "$LARGE_FILE"
import sys, random, string

with open(sys.argv[1], 'w') as f:
    # Write structured sections
    f.write("=== SECTION: Header ===\n")
    f.write("Generated: " + __import__('datetime').datetime.now().isoformat() + "\n")
    f.write("Size target: 50KB\n\n")

    f.write("=== SECTION: Random Data ===\n")
    for i in range(500):
        length = random.randint(10, 80)
        text = ''.join(random.choices(string.ascii_letters + ' ', k=length))
        f.write(f"{i:04d}: {text}\n")

    f.write("\n=== SECTION: JSON Blocks ===\n")
    for i in range(100):
        record = {
            "id": i,
            "name": f"item_{i:04d}",
            "value": random.random() * 1000,
            "tags": random.sample(["alpha","beta","gamma","delta","epsilon"], k=2),
            "nested": {"x": random.randint(0,100), "y": random.randint(0,100)}
        }
        f.write(__import__('json').dumps(record) + "\n")

    f.write("\n=== SECTION: Markdown Blocks ===\n")
    for i in range(50):
        f.write(f"## Heading {i}\n\n")
        f.write(f"This is paragraph {i} with some **bold** and *italic* text.\n")
        f.write(f"- Item A-{i}\n- Item B-{i}\n- Item C-{i}\n\n")

    f.write("\n=== SECTION: Code Blocks ===\n")
    for i in range(30):
        f.write(f"```python\n")
        f.write(f"def function_{i}(x):\n")
        f.write(f"    return x * {i} + {random.randint(1,100)}\n")
        f.write(f"\n")
        f.write(f"result_{i} = function_{i}(42)\n")
        f.write(f"print(f'Output {{result_{i}}}')\n")
        f.write(f"```\n\n")
PYEOF

FILE_SIZE=$(stat -c%s "$LARGE_FILE" 2>/dev/null || stat -f%z "$LARGE_FILE" 2>/dev/null || echo "unknown")
echo "  File size: $FILE_SIZE bytes"

# Phase 2: Read file in chunks (simulating model reading large files)
log_checkpoint "Phase 2: Reading large file in chunks"
echo "  Reading chunks..."
CHUNKS=0
LINES_TOTAL=0
while IFS= read -r line; do
    LINES_TOTAL=$((LINES_TOTAL + 1))
    if [ $((LINES_TOTAL % 200)) -eq 0 ]; then
        CHUNKS=$((CHUNKS + 1))
        echo "    Chunk $CHUNKS: line $LINES_TOTAL"
    fi
done < "$LARGE_FILE"
echo "  Total lines: $LINES_TOTAL, Chunks: $CHUNKS"

# Phase 3: Checkpoint: pattern search
log_checkpoint "Phase 3: Pattern search across file"
echo "  Searching for patterns..."

PATTERNS=("SECTION" "Heading" "function_" "item_" "alpha" "gamma" "result_")
for pattern in "${PATTERNS[@]}"; do
    count=$(grep -c "$pattern" "$LARGE_FILE" 2>/dev/null || echo 0)
    echo "    '$pattern': $count matches"
done

# Phase 4: Complex computation
log_checkpoint "Phase 4: Running complex computation"
echo "  Computing statistics..."
python3 - <<'PYEOF' "$LARGE_FILE" "$RESULTS_FILE"
import sys, json, re

with open(sys.argv[1]) as f:
    content = f.read()

results = {
    "filename": sys.argv[1],
    "total_chars": len(content),
    "total_lines": content.count('\n'),
    "sections": {},
}

# Find all sections
for match in re.finditer(r'=== SECTION: (.+?) ===', content):
    section = match.group(1)
    start = match.end()
    next_section = re.search(r'=== SECTION:', content[start:])
    end = start + next_section.start() if next_section else len(content)
    section_content = content[start:end]
    results["sections"][section] = {
        "chars": len(section_content),
        "lines": section_content.count('\n'),
    }

# Word stats
words = content.split()
results["total_words"] = len(words)
results["avg_word_len"] = sum(len(w) for w in words) / max(len(words), 1)
results["unique_words"] = len(set(w.lower() for w in words))

# Code block count
results["code_blocks"] = len(re.findall(r'```python', content))
results["json_blocks"] = len(re.findall(r'^\{"id"', content, re.MULTILINE))

with open(sys.argv[2], 'w') as f:
    json.dump(results, f, indent=2)

print(f"  Total chars: {results['total_chars']}")
print(f"  Total lines: {results['total_lines']}")
print(f"  Total words: {results['total_words']}")
print(f"  Unique words: {results['unique_words']}")
print(f"  Code blocks: {results['code_blocks']}")
print(f"  JSON blocks: {results['json_blocks']}")
for section, stats in results['sections'].items():
    print(f"  Section '{section}': {stats['lines']} lines, {stats['chars']} chars")

PYEOF

# Phase 5: File integrity verification
log_checkpoint "Phase 5: File integrity check"
echo "  Verifying file integrity..."
python3 - <<'PYEOF' "$LARGE_FILE"
import sys

with open(sys.argv[1]) as f:
    content = f.read()

# Verify structure
checks = {
    "has_header": content.startswith("=== SECTION: Header ==="),
    "has_random_data": "=== SECTION: Random Data ===" in content,
    "has_json_blocks": "=== SECTION: JSON Blocks ===" in content,
    "has_markdown": "=== SECTION: Markdown Blocks ===" in content,
    "has_code": "=== SECTION: Code Blocks ===" in content,
    "not_empty": len(content) > 0,
    "valid_utf8": True,  # Already verified by read
}

all_pass = all(checks.values())
for check, result in checks.items():
    status = "✅" if result else "❌"
    print(f"    {status} {check}")

if not all_pass:
    sys.exit(1)
PYEOF

# Phase 6: Generate summary
log_checkpoint "Phase 6: Final summary"
echo ""
echo "========================================="
echo "  Long Test Complete"
echo "========================================="
echo "  Duration: $(date -Iseconds)"
echo "  Checkpoints logged: $CHECKPOINTS_FILE"
echo "  Results saved: $RESULTS_FILE"
echo ""
cat "$CHECKPOINTS_FILE"

echo ""
echo "✅ LONG TEST PASSED"
exit 0
