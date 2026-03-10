# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

is_windows = sys.platform == "win32"
vgm2wav2_exe = "vgm2wav2.exe" if is_windows else "vgm2wav2"
vgm2wav2_path = Path("build") / vgm2wav2_exe

if not vgm2wav2_path.exists():
    raise SystemExit(f"ERROR: {vgm2wav2_path} not found. Build vgm2wav2 first.")

a = Analysis(
    ["player.py"],
    pathex=[],
    binaries=[(str(vgm2wav2_path), ".")],
    datas=[],
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name="vgm-player",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="vgm-player",
)
