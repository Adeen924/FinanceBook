"""
launcher.py — Thin frozen launcher for FinanceBook.

This is the ONLY script PyInstaller freezes. It bundles the Python runtime and
every third-party dependency (PyQt6, pandas, openpyxl, ofxparse, requests, …)
inside FinanceBook.exe, but it does NOT contain the application's own code.

The real application lives as plain files in the `app/` folder sitting next to
FinanceBook.exe. The launcher's only job is to put `app/` on sys.path and run
`app/main.py`. Because the app code is external, an update is just "replace the
app/ folder" — the locked .exe is never touched. See UPDATE_PLAN.md.

The frozen interpreter's bundled libraries remain importable by the external
app code: PyInstaller's FrozenImporter serves them regardless of which module
issues the import.
"""

import os
import runpy
import sys


def _base_dir() -> str:
    """Directory that contains this launcher (and, beside it, the app/ folder)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    app_dir = os.path.join(_base_dir(), "app")
    main_py = os.path.join(app_dir, "main.py")

    if not os.path.isfile(main_py):
        # Surface a clear error instead of a silent exit if app/ is missing.
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            app = QApplication(sys.argv)
            QMessageBox.critical(
                None, "FinanceBook",
                "The application files (app folder) could not be found "
                f"next to the program.\n\nExpected: {main_py}")
        except Exception:
            sys.stderr.write(f"FinanceBook: app/main.py not found at {main_py}\n")
        sys.exit(1)

    # Make the external app importable, then run it as if it were the main script.
    sys.path.insert(0, app_dir)
    runpy.run_path(main_py, run_name="__main__")


if __name__ == "__main__":
    main()
