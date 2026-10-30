# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec. Build on Windows: pyinstaller --noconfirm contact_to_vcf/ContactToVCF.spec"""

from pathlib import Path

spec_dir = Path(SPECPATH).resolve()
entry = spec_dir / "src" / "main.py"
src = spec_dir / "src"

a = Analysis(
    [str(entry)],
    pathex=[str(src)],
    binaries=[],
    datas=[],
    hiddenimports=["openpyxl", "et_xmlfile"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ContactToVCF",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
