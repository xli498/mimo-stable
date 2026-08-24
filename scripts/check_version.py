#!/usr/bin/env python3
"""Verify that the package version is represented in the changelog."""

from __future__ import annotations

import re
import sys
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    try:
        import tomli as tomllib
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Python 3.10 requires the optional 'tomli' package to run check_version.py"
        ) from exc
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## \[{re.escape(version)}\]", changelog, re.MULTILINE):
        print(f"CHANGELOG.md has no release heading for version {version}", file=sys.stderr)
        return 1
    print(f"version consistency passed: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
