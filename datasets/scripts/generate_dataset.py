#!/usr/bin/env python3
"""Master entrypoint — delegates to generate_vecd.py"""
from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).with_name("generate_vecd.py")
    runpy.run_path(str(target), run_name="__main__")
