#!/usr/bin/env python3
"""
Compatibility wrapper for the merged collection pipeline.

Use `paper_collect.py` directly for new runs.
"""

from __future__ import annotations

import sys


def main() -> None:
    raise SystemExit(
        "refine_collected.py has been merged into paper_collect.py. "
        "Run paper_collect.py to produce collected-refined.json and its state file."
    )


if __name__ == "__main__":
    main()
