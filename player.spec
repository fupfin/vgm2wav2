# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

is_windows = sys.platform == "win32"
vgm2wav2_exe = "vgm2wav2.exe" if is_windows else "vgm2wav2"
vgm2wav2_path = Path("build") / vgm2wav2_exe

if not vgm2wav2_path.exists():
    raise SystemExit(f"ERROR: {vgm2wav2_path} not found. Build vgm2wav2 first.")

extra_binaries = [(str(vgm2wav2_path), ".")]
if is_windows:
    for dll in ["libgme.dll", "libwinpthread-1.dll", "zlib1.dll",
                "libstdc++-6.dll", "libgcc_s_seh-1.dll"]:
        dll_path = Path("build") / dll
        if dll_path.exists():
            extra_binaries.append((str(dll_path), "."))

a = Analysis(
    ["player.py"],
    pathex=[],
    binaries=extra_binaries,
    datas=[],
    hiddenimports=[
        "textual.widgets._button",
        "textual.widgets._checkbox",
        "textual.widgets._collapsible",
        "textual.widgets._content_switcher",
        "textual.widgets._data_table",
        "textual.widgets._digits",
        "textual.widgets._directory_tree",
        "textual.widgets._footer",
        "textual.widgets._header",
        "textual.widgets._help_panel",
        "textual.widgets._input",
        "textual.widgets._key_panel",
        "textual.widgets._label",
        "textual.widgets._link",
        "textual.widgets._list_item",
        "textual.widgets._list_view",
        "textual.widgets._loading_indicator",
        "textual.widgets._log",
        "textual.widgets._markdown",
        "textual.widgets._markdown_viewer",
        "textual.widgets._masked_input",
        "textual.widgets._option_list",
        "textual.widgets._placeholder",
        "textual.widgets._pretty",
        "textual.widgets._progress_bar",
        "textual.widgets._radio_button",
        "textual.widgets._radio_set",
        "textual.widgets._rich_log",
        "textual.widgets._rule",
        "textual.widgets._select",
        "textual.widgets._selection_list",
        "textual.widgets._sparkline",
        "textual.widgets._static",
        "textual.widgets._switch",
        "textual.widgets._tab",
        "textual.widgets._tab_pane",
        "textual.widgets._tabbed_content",
        "textual.widgets._tabs",
        "textual.widgets._text_area",
        "textual.widgets._toast",
        "textual.widgets._toggle_button",
        "textual.widgets._tooltip",
        "textual.widgets._tree",
        "textual.widgets._welcome",
    ],
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
