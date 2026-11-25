"""
launcher.py — Thin frozen launcher for FinanceBook.

This is the ONLY script PyInstaller freezes. It bundles the Python runtime and
every third-party dependency (PyQt6, pandas, openpyxl, ofxparse, requests, …)
inside the program, but it does NOT contain the application's own code.

The real application lives as plain files in an `app/` folder. The launcher's
only job is to find that folder, put it on sys.path and run `app/main.py`.
Because the app code is external, an update is just "replace the app/ folder" —
the program itself is never touched. See UPDATE_PLAN.md.

Where app/ is:
  Windows   next to FinanceBook.exe (unchanged).
  macOS     (1) ~/Library/Application Support/FinanceBook/app — written by the
                in-app updater — if its VERSION is newer than the bundled copy
                and it hasn't failed to start;
            (2) otherwise FinanceBook.app/Contents/Resources/app (bundled by
                build_mac.sh). Nothing is ever written inside the .app.
  Source    the app/ folder beside this file (python launcher.py).

Rollback (macOS): before running an updated app/ the launcher leaves a boot
marker; main.py removes it once its code has imported and Qt is running. If
main() raises, or a previous start died before that point, the updated app/ is
renamed app.failed and the bundled app/ runs instead, with a note telling the user.

The frozen interpreter's bundled libraries remain importable by the external
app code: PyInstaller's FrozenImporter serves them regardless of which module
issues the import.
"""

import json
import os
import runpy
import sys

APP_NAME = "FinanceBook"


def _base_dir() -> str:
    """Directory that contains this launcher (and, beside it, the app/ folder)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# ── macOS search order ───────────────────────────────────────────────────────

def mac_support_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_NAME)


def mac_bundled_app_dir() -> str:
    """FinanceBook.app/Contents/Resources/app (the executable is in Contents/MacOS)."""
    return os.path.normpath(os.path.join(_base_dir(), "..", "Resources", "app"))


def read_version(app_dir: str) -> str:
    try:
        with open(os.path.join(app_dir, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def version_newer(a: str, b: str) -> bool:
    """True if version string a is newer than b ('' is oldest)."""
    if not a:
        return False
    if not b:
        return True
    try:
        from packaging.version import Version
        return Version(a) > Version(b)
    except Exception:
        def key(v):
            return [int(p) if p.isdigit() else 0 for p in v.split("-")[0].split(".")]
        return key(a) > key(b)


def _marker_path(support: str) -> str:
    return os.path.join(support, "boot-marker.json")


def mark_failed(user_app: str) -> str:
    """Move a broken updated app/ aside (app.failed) so it isn't tried again."""
    failed = user_app + ".failed"
    import shutil
    shutil.rmtree(failed, ignore_errors=True)
    try:
        os.replace(user_app, failed)
    except OSError:
        shutil.rmtree(user_app, ignore_errors=True)
    return failed


def choose_mac_app_dir(bundled: str, support: str) -> tuple[str, str]:
    """
    Pick the app/ folder to run. Returns (app_dir, note) — note is a message
    for the user when an update had to be rolled back, else ''.
    """
    user_app = os.path.join(support, "app")
    note = ""
    marker = _marker_path(support)
    if os.path.exists(marker):
        # The last start of the updated app never reached its window.
        try:
            with open(marker, encoding="utf-8") as f:
                info = json.load(f)
        except Exception:
            info = {}
        os.remove(marker)
        if info.get("app") == user_app and os.path.isdir(user_app):
            ver = read_version(user_app)
            mark_failed(user_app)
            note = rollback_note(ver, read_version(bundled))
    if (os.path.isfile(os.path.join(user_app, "main.py"))
            and version_newer(read_version(user_app), read_version(bundled))):
        return user_app, note
    return bundled, note


def rollback_note(bad: str, good: str) -> str:
    return (f"FinanceBook {bad or 'update'} couldn't start, so this Mac went back to the "
            f"installed version {good}. Your data wasn't changed. If this keeps happening, "
            "rebuild the app with update_mac.sh.")


def _show_error(text: str) -> None:
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, APP_NAME, text)
    except Exception:
        sys.stderr.write(f"FinanceBook: {text}\n")


def _run(app_dir: str) -> None:
    sys.path.insert(0, app_dir)
    runpy.run_path(os.path.join(app_dir, "main.py"), run_name="__main__")


def _main_mac() -> None:
    support = mac_support_dir()
    bundled = mac_bundled_app_dir()
    app_dir, note = choose_mac_app_dir(bundled, support)
    if note:
        os.environ["FINANCEBOOK_LAUNCH_NOTE"] = note
    if app_dir == bundled:
        # Never write inside the signed .app (build_mac.sh precompiles it).
        sys.dont_write_bytecode = True
        if not os.path.isfile(os.path.join(app_dir, "main.py")):
            _show_error("The application files are missing from FinanceBook.app.\n\n"
                        "Rebuild it with build_mac.sh.")
            sys.exit(1)
        _run(app_dir)
        return

    # An updated app/: guard its start so a broken update rolls back.
    os.makedirs(support, exist_ok=True)
    marker = _marker_path(support)
    with open(marker, "w", encoding="utf-8") as f:
        json.dump({"app": app_dir, "version": read_version(app_dir)}, f)
    os.environ["FINANCEBOOK_BOOT_MARKER"] = marker
    try:
        _run(app_dir)
    except SystemExit:
        _clear_marker(marker)          # a normal exit (even before the window)
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        # (Re)write the marker — main.py clears it early — so restarting runs
        # the rollback in choose_mac_app_dir.
        try:
            with open(marker, "w", encoding="utf-8") as f:
                json.dump({"app": app_dir, "version": read_version(app_dir)}, f)
        except OSError:
            pass
        if getattr(sys, "frozen", False):
            os.execv(sys.executable, [sys.executable] + sys.argv[1:])
        raise
    _clear_marker(marker)


def _clear_marker(marker: str) -> None:
    try:
        os.remove(marker)
    except OSError:
        pass


def main() -> None:
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        _main_mac()
        return

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
