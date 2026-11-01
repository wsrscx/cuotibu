# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH)
icon_path = ROOT / "assets" / "cuotibu.ico"
icon = str(icon_path) if icon_path.exists() else None

a = Analysis(
    ["app.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "templates"), "templates"),
        (str(ROOT / "static"), "static"),
    ],
    hiddenimports=[
        "flask_sqlalchemy",
        "sqlalchemy.dialects.sqlite",
        "bs4",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # This app is a small Flask + SQLite desktop tool. Keep unrelated
    # scientific/ML stacks from the developer machine out of the EXE.
    excludes=[
        "torch",
        "torchvision",
        "tensorflow",
        "keras",
        "onnx",
        "onnxruntime",
        "numpy",
        "pandas",
        "scipy",
        "matplotlib",
        "sympy",
        "sklearn",
        "IPython",
        "jedi",
        "Pygments",
        "PIL",
        "lxml",
        "html5lib",
        "cryptography",
        "bcrypt",
        "psutil",
        "pyarrow",
        "openpyxl",
        "fsspec",
        "win32com",
        "pythoncom",
        "pywintypes",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Cuotibu",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)
