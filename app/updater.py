"""
updater.py — One-click in-app updater for FinanceBook.

Wired into main.py after window.show():
    from updater import run_update_check
    run_update_check(window)

Design (see UPDATE_PLAN.md):
  - The shipped product is a THIN frozen launcher (FinanceBook.exe) sitting next
    to a plain-files `app/` folder. This module lives inside `app/`.
  - Version source of truth is GitHub Releases, NOT a hand-maintained version.json.
    We read the latest release tag and compare it to the local `app/VERSION`.
  - A "code update" ships an `app-<version>.zip` asset. The update is just
    "replace the app/ folder", which is plain file copying — the locked .exe is
    never touched, so Windows' self-overwrite lock problem disappears.
  - A "runtime update" (rare: changed deps / launcher) ships no app payload; we
    fall back to opening the release page for a full re-download.

The check runs on a daemon thread so it never blocks the GUI. Any failure
(no internet, rate limit, bad data) is swallowed and the app continues normally.
"""

import os
import sys
import threading
import webbrowser

# ── Config ──────────────────────────────────────────────────────────────────────

GITHUB_REPO = "Adeen924/FinanceBook"
_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# The release asset carrying the swappable app/ payload is named like
# "app-1.0.3.zip". Its presence in a release means a one-click code update is
# possible; its absence means the release is a runtime-only change (full download).
_APP_ASSET_PREFIX = "app-"

_REQUEST_TIMEOUT = 8  # seconds before giving up on the GitHub API
_USER_AGENT = "FinanceBook-Updater"


# ── Paths / local version ────────────────────────────────────────────────────────

def _app_dir() -> str:
    """Absolute path of the app/ folder (the directory this module lives in)."""
    return os.path.dirname(os.path.abspath(__file__))


def read_local_version() -> str:
    """Read the current version from app/VERSION. Falls back to '0.0.0'."""
    try:
        with open(os.path.join(_app_dir(), "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


def _is_frozen() -> bool:
    """True when running from the packaged thin launcher (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


# ── Core check ────────────────────────────────────────────────────────────────────

def check_for_updates() -> dict | None:
    """
    Query the GitHub 'latest release' API and compare its tag to app/VERSION.

    Returns a normalized dict if a newer version is available, else None.
    Returns None (silently) on any error: no internet, rate limit, bad JSON, etc.

    Returned dict shape:
        {
            "latest_version": "1.0.3",
            "tag":            "v1.0.3",
            "notes":          "What changed...",
            "html_url":       "https://github.com/.../releases/tag/v1.0.3",
            "app_asset":      {"name": "app-1.0.3.zip",
                               "url": "<browser_download_url>",
                               "size": 123456} | None,
        }
    """
    try:
        import requests
        from packaging.version import Version

        resp = requests.get(
            _API_URL,
            timeout=_REQUEST_TIMEOUT,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()

        tag = (data.get("tag_name") or "").strip()
        latest_str = tag.lstrip("vV")
        if not latest_str:
            return None

        if Version(latest_str) <= Version(read_local_version()):
            return None  # already up to date

        # Find the swappable app payload asset, if this release has one.
        app_asset = None
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.startswith(_APP_ASSET_PREFIX) and name.endswith(".zip"):
                app_asset = {
                    "name": name,
                    "url": asset.get("browser_download_url", ""),
                    "size": int(asset.get("size", 0)),
                }
                break

        return {
            "latest_version": latest_str,
            "tag": tag,
            "notes": (data.get("body") or "").strip() or "No release notes provided.",
            "html_url": data.get("html_url", ""),
            "app_asset": app_asset,
        }

    except Exception:
        # No internet, DNS failure, timeout, HTTP/rate-limit error, bad JSON,
        # missing 'packaging', unparseable version, etc. — fail silent.
        return None


# ── Update dialog ──────────────────────────────────────────────────────────────────

def _show_update_dialog(parent, info: dict) -> None:
    """Show the 'Update available' dialog on the main thread."""
    from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QHBoxLayout,
                                 QPushButton)
    from PyQt6.QtCore import Qt

    latest = info.get("latest_version", "Unknown")
    notes = info.get("notes", "")
    can_one_click = bool(info.get("app_asset")) and _is_frozen()

    dlg = QDialog(parent)
    dlg.setWindowTitle("Update Available")
    dlg.setMinimumWidth(420)
    dlg.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

    lay = QVBoxLayout(dlg)
    lay.setSpacing(12)
    lay.setContentsMargins(24, 20, 24, 20)

    title = QLabel(f"FinanceBook {latest} is available")
    title.setStyleSheet("font-size: 15px; font-weight: bold;")
    lay.addWidget(title)

    subtitle = QLabel(f"You are running version {read_local_version()}.")
    subtitle.setObjectName("Muted")
    lay.addWidget(subtitle)

    notes_label = QLabel(notes)
    notes_label.setWordWrap(True)
    notes_label.setStyleSheet(
        "background: #f5f7fa; border: 1px solid #e5e7eb; border-radius: 5px;"
        "padding: 10px; font-size: 12px;")
    lay.addWidget(notes_label)

    if not can_one_click:
        hint = QLabel(
            "This is a major update and must be downloaded manually.")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        lay.addWidget(hint)

    btn_row = QHBoxLayout()
    btn_row.addStretch()

    later_btn = QPushButton("Later")
    later_btn.setObjectName("Secondary")
    later_btn.clicked.connect(dlg.reject)
    btn_row.addWidget(later_btn)

    if can_one_click:
        action_btn = QPushButton("Install Update")
        action_btn.clicked.connect(
            lambda: (dlg.accept(), _perform_update(parent, info)))
    else:
        action_btn = QPushButton("Open Download Page")
        action_btn.clicked.connect(
            lambda: (webbrowser.open(info.get("html_url", "")), dlg.accept()))
    btn_row.addWidget(action_btn)

    lay.addLayout(btn_row)
    dlg.exec()


# ── One-click update execution ──────────────────────────────────────────────────────

def _perform_update(parent, info: dict) -> None:
    """
    Download the app payload with a progress dialog, verify it, then hand off to
    a helper that swaps app/ and relaunches once this process exits.
    """
    from PyQt6.QtWidgets import QProgressDialog, QMessageBox
    from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal

    asset = info["app_asset"]
    staging_root = os.path.join(_temp_dir(), "FinanceBook_update_files")
    zip_path = os.path.join(staging_root, asset["name"])
    new_app_dir = os.path.join(staging_root, "app_new")

    progress = QProgressDialog("Downloading update…", "Cancel", 0, 100, parent)
    progress.setWindowTitle("Updating FinanceBook")
    progress.setMinimumWidth(380)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.setWindowModality(Qt.WindowModality.WindowModal)

    class _Worker(QObject):
        progressed = pyqtSignal(int)
        done = pyqtSignal()
        failed = pyqtSignal(str)

        def run(self):
            try:
                _download_and_prepare(
                    asset, staging_root, zip_path, new_app_dir,
                    on_progress=self.progressed.emit,
                    should_cancel=lambda: progress.wasCanceled())
                self.done.emit()
            except _Cancelled:
                self.failed.emit("")  # empty == user cancelled, stay silent
            except Exception as e:
                self.failed.emit(str(e))

    thread = QThread(parent)
    worker = _Worker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    def _on_progress(pct):
        progress.setValue(pct)

    def _cleanup():
        thread.quit()
        thread.wait()
        progress.close()

    def _on_done():
        _cleanup()
        try:
            _launch_helper_and_exit(parent, new_app_dir)
        except Exception as e:
            QMessageBox.warning(parent, "Update Failed",
                                f"Could not finish the update:\n\n{e}")

    def _on_failed(msg):
        _cleanup()
        _safe_rmtree(staging_root)
        if msg:  # blank means the user cancelled — no need to nag
            QMessageBox.warning(
                parent, "Update Failed",
                f"The update could not be downloaded:\n\n{msg}\n\n"
                "You can try again later from the Help menu.")

    worker.progressed.connect(_on_progress)
    worker.done.connect(_on_done)
    worker.failed.connect(_on_failed)
    # Keep references alive for the lifetime of the thread.
    thread._worker = worker  # type: ignore[attr-defined]
    thread.start()
    progress.exec()


class _Cancelled(Exception):
    """Raised internally when the user cancels the download."""


def _download_and_prepare(asset, staging_root, zip_path, new_app_dir,
                          on_progress, should_cancel) -> None:
    """
    Download the payload zip, verify it fully, and extract to new_app_dir.
    Raises on any failure; raises _Cancelled if the user cancels.
    Never leaves a partially-applied state — extraction happens only after the
    download is verified complete and the zip passes an integrity test.
    """
    import requests
    import zipfile

    _safe_rmtree(staging_root)
    os.makedirs(staging_root, exist_ok=True)

    expected_size = asset.get("size", 0)
    with requests.get(asset["url"], stream=True, timeout=_REQUEST_TIMEOUT,
                      headers={"User-Agent": _USER_AGENT}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or expected_size or 0)
        written = 0
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if should_cancel():
                    raise _Cancelled()
                if chunk:
                    f.write(chunk)
                    written += len(chunk)
                    if total:
                        on_progress(min(99, int(written * 100 / total)))

    # Verify: full size (guards against a truncated/partial download) + zip integrity.
    if expected_size and os.path.getsize(zip_path) != expected_size:
        raise RuntimeError("Downloaded file size did not match the release.")
    if not zipfile.is_zipfile(zip_path):
        raise RuntimeError("Downloaded file is not a valid zip archive.")
    with zipfile.ZipFile(zip_path) as zf:
        if zf.testzip() is not None:
            raise RuntimeError("Downloaded update archive is corrupt.")
        os.makedirs(new_app_dir, exist_ok=True)
        zf.extractall(new_app_dir)

    # Sanity check: the payload must look like an app/ folder.
    if not os.path.exists(os.path.join(new_app_dir, "main.py")):
        raise RuntimeError("Update payload is missing main.py — aborting.")

    on_progress(100)


def _launch_helper_and_exit(parent, new_app_dir: str) -> None:
    """
    Write the swap/relaunch helper batch, launch it detached, and quit the app so
    the files unlock. The helper waits for our PID to exit before touching app/.
    """
    import subprocess
    from PyQt6.QtWidgets import QApplication, QMessageBox

    exe_path = sys.executable                 # the frozen FinanceBook.exe
    app_dir = _app_dir()                      # current app/ to be replaced
    backup_dir = app_dir + "_backup"          # rollback copy
    helper_path = os.path.join(_temp_dir(), "FinanceBook_update.bat")
    pid = os.getpid()

    _write_helper_bat(helper_path, pid, exe_path, app_dir, backup_dir,
                      new_app_dir)

    # DETACHED so the helper outlives this process; no console window.
    DETACHED = 0x00000008
    NO_WINDOW = 0x08000000
    subprocess.Popen(["cmd", "/c", helper_path],
                     creationflags=DETACHED | NO_WINDOW,
                     close_fds=True)

    QMessageBox.information(
        parent, "Restarting to Finish Update",
        "FinanceBook will now close and reopen on the new version.\n"
        "Your data is safe — it is stored separately from the app.")

    # Quit cleanly so the .py files unlock for the helper.
    app = QApplication.instance()
    if app is not None:
        app.quit()
    os._exit(0)


def _write_helper_bat(path, pid, exe_path, app_dir, backup_dir,
                      new_app_dir) -> None:
    """Write the batch helper that performs the swap after the app exits."""
    staging_root = os.path.dirname(new_app_dir)
    script = f"""@echo off
setlocal
set "PID={pid}"
set "EXE={exe_path}"
set "APPDIR={app_dir}"
set "BACKUP={backup_dir}"
set "NEWDIR={new_app_dir}"
set "STAGING={staging_root}"

REM ── Wait for FinanceBook.exe to fully exit so app/ unlocks ──────────────
:waitloop
tasklist /FI "PID eq %PID%" 2>NUL | find "%PID%" >NUL
if not errorlevel 1 (
    timeout /t 1 /nobreak >NUL
    goto waitloop
)

REM ── Back up the current app/ for rollback ──────────────────────────────
if exist "%BACKUP%" rmdir /S /Q "%BACKUP%"
move "%APPDIR%" "%BACKUP%" >NUL

REM ── Install the new app/ (robocopy handles cross-volume temp dirs) ──────
mkdir "%APPDIR%"
robocopy "%NEWDIR%" "%APPDIR%" /E /NFL /NDL /NJH /NJS /NP >NUL
if %ERRORLEVEL% GEQ 8 (
    REM Install failed — roll back to the backup.
    rmdir /S /Q "%APPDIR%" 2>NUL
    move "%BACKUP%" "%APPDIR%" >NUL
)

REM ── Clean up staged download and relaunch ──────────────────────────────
rmdir /S /Q "%STAGING%" 2>NUL
start "" "%EXE%"

REM ── Self-delete this helper ────────────────────────────────────────────
(goto) 2>NUL & del "%~f0"
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)


# ── Small helpers ────────────────────────────────────────────────────────────────

def _temp_dir() -> str:
    import tempfile
    return tempfile.gettempdir()


def _safe_rmtree(path: str) -> None:
    import shutil
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except Exception:
        pass


# ── Background runner (entry point) ────────────────────────────────────────────────

def run_update_check(parent_window) -> None:
    """
    Run the update check on a background daemon thread; never blocks the GUI.
    Call once from main.py after window.show().
    """
    def _worker():
        info = check_for_updates()
        if info is None:
            return
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: _show_update_dialog(parent_window, info))

    threading.Thread(target=_worker, daemon=True, name="update-check").start()
