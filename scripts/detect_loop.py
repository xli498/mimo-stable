#!/usr/bin/env python3
"""Backward-compatible source-tree entry point for mimo-stable."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mimo_stable.detect_loop import main

if __name__ == "__main__":
    main()
