#!/usr/bin/env python3
"""Repo-root wrapper for VECD generate_dataset.py"""
from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "datasets" / "scripts" / "generate_dataset.py"
    runpy.run_path(str(target), run_name="__main__")
