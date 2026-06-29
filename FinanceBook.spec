# FinanceBook.spec  —  PyInstaller build configuration
#
# Run with:  pyinstaller FinanceBook.spec --noconfirm
# Output:    dist\FinanceBook\FinanceBook.exe  (+ _internal\)
#
# This freezes ONLY launcher.py — the thin launcher. It bundles the Python
# runtime and every third-party dependency, but NOT the app's own source.
# The real app ships as plain files in app/ (copied in by build.bat / CI) so
# updates can swap app/ without touching the locked .exe. See UPDATE_PLAN.md.
#
# IMPORTANT: Because the app code is external, PyInstaller cannot see which
# libraries it imports. Every third-party dependency the app uses MUST be
# listed in hiddenimports below, or the external code will fail to import it.
# (App modules like ui/, sheets/, parsers/ are intentionally NOT listed — they
#  live outside the bundle and are loaded from app/ at runtime.)

import os
block_cipher = None

from PyInstaller.utils.hooks import collect_all

qt_datas, qt_binaries, qt_hiddenimports = collect_all("PyQt6")

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=qt_binaries,
    datas=qt_datas,
    hiddenimports=[
        *qt_hiddenimports,
        # ── Third-party libs the EXTERNAL app/ code imports at runtime.
        #    These must be frozen because the app source isn't analyzed here.
        "ofxparse",
        "pandas",
        "pandas._libs.tslibs.np_datetime",
        "pandas._libs.tslibs.nattype",
        "pandas._libs.tslibs.timedeltas",
        "openpyxl",
        "openpyxl.cell._writer",
        "dateutil",
        "dateutil.parser",
        "requests",
        "requests.adapters",
        "packaging",
        "packaging.version",
        "sqlite3",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "IPython",
        "jupyter",
        "notebook",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# The app icon ships inside app/ (so it's swappable and available at runtime);
# fall back to a repo-root assets/icon.ico if you prefer to keep it there.
if os.path.exists("app\\assets\\icon.ico"):
    _icon = "app\\assets\\icon.ico"
elif os.path.exists("assets\\icon.ico"):
    _icon = "assets\\icon.ico"
else:
    _icon = None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FinanceBook",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX can trigger false-positive antivirus warnings
    console=False,      # No black terminal window behind the GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FinanceBook",
)
