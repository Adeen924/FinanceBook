# FinanceBook-macos.spec  —  PyInstaller build for a personal macOS build
#
# Run through build_mac.sh (it makes the .icns, sets FB_VERSION, copies app/
# into the bundle, signs and installs). Output: dist/FinanceBook.app
#
# Same design as FinanceBook.spec (Windows): this freezes ONLY launcher.py —
# the Python runtime and every third-party library — and NOT the app's own
# source. build_mac.sh copies app/ into FinanceBook.app/Contents/Resources/app,
# and the in-app updater puts newer app/ folders in
# ~/Library/Application Support/FinanceBook/app (see launcher.py).
#
# Keep hiddenimports in step with FinanceBook.spec: the external app/ code
# isn't analysed, so every third-party library it imports must be listed.

import os
block_cipher = None

from PyInstaller.utils.hooks import collect_all

qt_datas, qt_binaries, qt_hiddenimports = collect_all("PyQt6")

_version = os.environ.get("FB_VERSION") or "0.0.0"
_icns = os.environ.get("FB_ICNS") or None

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=qt_binaries,
    datas=qt_datas,
    hiddenimports=[
        *qt_hiddenimports,
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
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FinanceBook",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,     # build_mac.sh ad-hoc signs the finished bundle
    entitlements_file=None,
    icon=_icns,
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

app = BUNDLE(
    coll,
    name="FinanceBook.app",
    icon=_icns,
    bundle_identifier="com.adeen924.financebook",
    version=_version,
    info_plist={
        "CFBundleName": "FinanceBook",
        "CFBundleDisplayName": "FinanceBook",
        "CFBundleShortVersionString": _version,
        "CFBundleVersion": _version,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "FinanceBook — personal build",
    },
)
