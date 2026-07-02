# archaeo_gps.spec
# PyInstaller 빌드 스펙 파일
# 사용법: pyinstaller archaeo_gps.spec

import os
import sys
from pathlib import Path
import pyproj

block_cipher = None

# pyproj PROJ 데이터 경로
PROJ_DATA = pyproj.datadir.get_data_dir()

a = Analysis(
    ["archaeo_gps_gui.py"],
    pathex=["."],
    binaries=[],
    datas=[
        # PROJ 좌표 변환 데이터 (필수)
        (PROJ_DATA, "pyproj/proj_dir/share/proj"),
        # 메인 로직 모듈
        ("archaeo_gps.py", "."),
    ],
    hiddenimports=[
        "pyproj",
        "pyproj.transformer",
        "pyproj.crs",
        "pyproj.datadir",
        "pandas",
        "pandas.core",
        "pandas.io.formats.excel",
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "tkinter.scrolledtext",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "scipy", "numpy.distutils",
        "IPython", "jupyter", "notebook",
        "PIL", "cv2", "sklearn",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ExifToolArchaeo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI 모드: 콘솔 창 숨김
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="archaeo.ico",   # 아이콘 파일 있으면 주석 해제
)
