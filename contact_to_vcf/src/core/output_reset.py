"""Remove previous outputs of this app before a fresh conversion."""

from __future__ import annotations

import shutil
from pathlib import Path


def reset_output(output_dir: Path) -> None:
    """Delete VCF files and state created by an earlier run in this folder.

    The source file is never touched. Only names this converter owns are removed.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    state = output_dir / ".contact_to_vcf"
    if state.exists():
        shutil.rmtree(state)
    for path in output_dir.glob("contacts_*.vcf"):
        if path.is_file():
            path.unlink()
    for name in ("errors.csv", "report.txt", "conversion.log", "thu_tu_nhap.txt"):
        path = output_dir / name
        if path.is_file():
            path.unlink()
    for batch in output_dir.glob("batch_*"):
        if not batch.is_dir():
            continue
        for vcf in batch.glob("contacts_*.vcf"):
            if vcf.is_file():
                vcf.unlink()
        try:
            batch.rmdir()
        except OSError:
            continue
