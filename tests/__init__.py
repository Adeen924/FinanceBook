"""
FinanceBook unit tests (stdlib unittest — no extra dependencies).

Run from the repository root:

    python -m unittest discover -s tests -v
"""
import os
import sys

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Qt's "offscreen" platform (used by the UI tests) finds no system fonts on
# Windows by itself, so text is drawn as boxes and measured wrongly. Point it at
# the Windows fonts folder so widths and the screenshots in tests/artifacts/ are real.
_WIN_FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
if sys.platform == "win32" and os.path.isdir(_WIN_FONTS):
    os.environ.setdefault("QT_QPA_FONTDIR", _WIN_FONTS)
