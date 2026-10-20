# -*- mode: python ; coding: utf-8 -*-
# Run build_windows.bat: prepares a verified ExifTool runtime before PyInstaller.
import os
from pathlib import Path
import pyproj
from PyInstaller.building.datastruct import Tree

ROOT = Path(SPECPATH)
ENGINE = Path(os.environ.get("ARCHAEO_EXIFTOOL_BUNDLE", str(ROOT / ".build-cache" / "exiftool-runtime")))
if not (ENGINE / "exiftool.exe").is_file() or not (ENGINE / "exiftool_files").is_dir():
    raise SystemExit("ExifTool runtime is missing. Run build_windows.bat first.")

a = Analysis(
    [str(ROOT / "archaeo_launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (pyproj.datadir.get_data_dir(), "pyproj/proj_dir/share/proj"),
        (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    ],
    hiddenimports=[
        "archaeo_gps", "archaeo_gps_gui", "pyproj", "pyproj.transformer",
        "pyproj.crs", "pyproj.datadir", "tkinter", "tkinter.ttk",
        "tkinter.filedialog", "tkinter.messagebox", "tkinter.scrolledtext",
        "PIL", "PIL.Image", "PIL.ImageOps",
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["pandas", "matplotlib", "scipy", "numpy.distutils",
              "IPython", "jupyter", "notebook", "cv2", "sklearn"],
    noarchive=False,
)
# Keep the complete Perl distribution opaque: do not relocate/UPX/reclassify its DLLs.
# Preserve the exiftool.exe + exiftool_files sibling layout and original notices.
a.datas += Tree(str(ENGINE), prefix="vendor/exiftool", typecode="DATA")
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="ExifToolArchaeo", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, runtime_tmpdir=None, console=False,
    disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
