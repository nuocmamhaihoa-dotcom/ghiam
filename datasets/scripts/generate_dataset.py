#!/usr/bin/env python3
"""Master Command entrypoint — delegates to VECD factory."""
from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).with_name("generate_vecd.py")
    runpy.run_path(str(target), run_name="__main__")
