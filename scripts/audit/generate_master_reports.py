#!/usr/bin/env python3
"""Regenerate master audit reports from reports/audit evidence.

Run from repo root:
  python3 scripts/audit/generate_master_reports.py
"""
from __future__ import annotations

import runpy
from pathlib import Path

# The authoritative generator lives alongside evidence collectors.
# Prefer re-running the committed inline procedure in generate_from_evidence.py
# if present; otherwise fail loudly.
ROOT = Path(__file__).resolve().parents[2]
ALT = Path(__file__).with_name("generate_from_evidence.py")
if ALT.exists():
    runpy.run_path(str(ALT), run_name="__main__")
else:
    raise SystemExit(
        "Missing scripts/audit/generate_from_evidence.py — "
        "restore from docs/audit generation commit."
    )
