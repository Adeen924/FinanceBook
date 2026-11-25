"""FinanceBook — Desktop personal finance application."""
import sys
import os


def _get_data_dir() -> str:
    """
    Return FinanceBook's own settings folder, and the suggested default
    location for a new database (the user can pick any other folder instead).

    - Installed build (PyInstaller frozen):  %APPDATA%\\FinanceBook\\ on
      Windows, ~/Library/Application Support/FinanceBook on a Mac (see
      utils.platform_paths). A writable, user-owned location, so settings
      survive reinstalls and aren't blocked by UAC / Program Files rules.
    - Running from source (development):  same folder as main.py
    """
    if getattr(sys, "frozen", False):
        from utils.platform_paths import app_data_dir
        return app_data_dir()
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _get_data_dir()


def _install_error_handler():
    """
    Show unexpected errors in a message box instead of letting PyQt abort the
    app (its default for an exception inside a button handler). A locked
    accounting period gets a friendly explanation.
    """
    import traceback

    def hook(exc_type, exc, tb):
        from PyQt6.QtWidgets import QApplication, QMessageBox
        text = str(exc)
        if "period is locked" in text:
            msg = ("That date is in a locked period, so it can't be added, changed or deleted.\n\n"
                   "To change it, unlock the period in Settings → Period Lock.")
            title = "Period Locked"
        elif "readonly database" in text:
            msg = ("This database is open read-only because it's in use on another computer, "
                   "so that change wasn't saved.\n\nWhen it's closed there and OneDrive has "
                   "finished syncing, choose Open for Editing in the yellow banner.")
            title = "Read-Only"
        else:
            msg = (f"Something went wrong:\n\n{exc_type.__name__}: {text}\n\n"
                   "Your data is safe — nothing was saved for this action. If it keeps "
                   "happening, please report it on the Issues page.")
            title = "FinanceBook"
        traceback.print_exception(exc_type, exc, tb)
        if QApplication.instance() is not None:
            QMessageBox.warning(QApplication.activeWindow(), title, msg)

    sys.excepthook = hook


def _new_database_path(location: str) -> str:
    os.makedirs(location, exist_ok=True)
    db_path = os.path.join(location, "finances.db")
    if os.path.exists(db_path):
        i = 2
        while os.path.exists(os.path.join(location, f"finances_{i}.db")):
            i += 1
        db_path = os.path.join(location, f"finances_{i}.db")
    return db_path


def _resolve_missing_database(db_path: str):
    """
    The active database's file isn't there at startup — usually a OneDrive
    folder that hasn't synced yet. Never quietly create an empty database
    where an existing one was expected: let the user retry, pick another, or
    deliberately create a new one. Returns (path, new_name, book_type, preset) —
    the name/type/preset are set only for a new database — or None to quit.
    """
    from PyQt6.QtWidgets import QMessageBox, QDialog
    while True:
        box = QMessageBox(QMessageBox.Icon.Warning, "Database Not Found",
            f"Could not find your database at:\n{db_path}\n\n"
            "The folder may not be available right now — for example a OneDrive folder "
            "that hasn't finished syncing, or a network drive that isn't connected. "
            "Wait for it and choose Retry, or pick another database.")
        retry = box.addButton("Retry", QMessageBox.ButtonRole.AcceptRole)
        choose = box.addButton("Choose Another Database…", QMessageBox.ButtonRole.ActionRole)
        create = box.addButton("Create New…", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(retry)
        box.exec()
        clicked = box.clickedButton()
        if clicked is retry:
            if os.path.exists(db_path):
                return db_path, None, None, None
        elif clicked is choose:
            from ui.connect_dialog import choose_existing_database
            path = choose_existing_database(None)
            if path:
                return path, None, None, None
        elif clicked is create:
            from ui.database_dialog import DatabaseSetupDialog
            dialog = DatabaseSetupDialog(
                window_title="New Database", heading="Create a New Database",
                message="Give your new database a name and choose where to save it.",
                default_name="Personal Finances", default_location=BASE_DIR,
                accept_label="Create", show_book_type=True)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                return (_new_database_path(dialog.database_location),
                        dialog.database_name, dialog.book_type, dialog.preset)
        else:
            return None


def _offer_update_for_newer_database(db_path: str):
    """
    The active database was upgraded by a newer FinanceBook on another
    computer, so this one can't open it. The normal update check only runs
    once the window is open, so check now — otherwise the user could never
    reach the update that fixes this.
    """
    from PyQt6.QtWidgets import QMessageBox
    from updater import check_for_updates, _show_update_dialog
    QMessageBox.information(None, "Update Needed",
        f"Your database was updated by a newer version of FinanceBook on another "
        f"computer:\n{db_path}\n\nThis computer needs that update too before it can "
        "open it. FinanceBook will now check for the update.")
    info = check_for_updates()
    if info is not None:
        _show_update_dialog(None, info)     # exits and relaunches if they update
    else:
        QMessageBox.warning(None, "Update Needed",
            "FinanceBook couldn't find the update online. Connect to the internet and "
            "start FinanceBook again, or download the latest version from "
            "github.com/Adeen924/FinanceBook/releases.")


_SELF_TEST_CSV = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    "DEBIT,11/12/2026,\"COSTCO WHSE #1234\",-84.12,DEBIT_CARD,1915.88,,\n"
    "CREDIT,11/10/2026,\"PAYROLL ACME INC\",2000.00,ACH_CREDIT,2000.00,,\n")


def self_test() -> int:
    """
    `FinanceBook --self-test`: a smoke test of the installed program. Uses a
    temporary database and settings folder (the real ones are never touched),
    opens the main window offscreen, visits every page, imports a small bank
    CSV, runs the Profit & Loss report and exits 0 — or prints the failure and
    exits 1. FINANCEBOOK_SELFTEST_SHOTS=<folder> also saves a picture of each page.
    """
    import tempfile
    import traceback
    tmp = tempfile.mkdtemp(prefix="FinanceBook-selftest-")
    os.environ["FINANCEBOOK_SETTINGS_DIR"] = os.path.join(tmp, "settings")
    shots = os.environ.get("FINANCEBOOK_SELFTEST_SHOTS", "")
    if not shots:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    step = "start"
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv[:1])
        app.setApplicationName("FinanceBook")
        # Never block on a dialog: every message box answers with its default.
        for name in ("information", "warning", "critical", "question"):
            setattr(QMessageBox, name, staticmethod(
                lambda *a, **k: QMessageBox.StandardButton.Yes))
        QMessageBox.exec = lambda self: int(QMessageBox.StandardButton.Yes)

        step = "theme"
        from ui.styles import apply_theme
        apply_theme(app)

        step = "database"
        from sheets.client import Database
        from utils import nav_config
        db = Database(os.path.join(tmp, "selftest.db"), book_type="business")
        db.set_setting("database_name", "Self-test")
        nav_config.save_modules(db, nav_config.ALL_MODULES)

        step = "import CSV"
        from parsers.spreadsheet import parse_spreadsheet
        acct = db.save_account({"name": "Checking", "type": "checking"})["id"]
        rows, _warn = parse_spreadsheet(_SELF_TEST_CSV.encode("utf-8"),
                                        "chase_checking.csv", acct)
        if len(rows) != 2:
            raise RuntimeError(f"CSV import parsed {len(rows)} rows, expected 2")
        db.import_transactions(rows)
        if len(db.get_transactions()) != 2:
            raise RuntimeError("imported transactions were not saved")

        step = "main window"
        from ui.main_window import MainWindow, _page_constructors
        window = MainWindow(db, None)
        window.show()
        app.processEvents()
        if shots:
            os.makedirs(shots, exist_ok=True)
        for name in _page_constructors():
            step = f"page {name}"
            window._nav_to(name)
            app.processEvents()
            if shots:
                window.grab().save(os.path.join(shots, name.replace(" ", "_")
                                                .replace("&", "and") + ".png"))
            print(f"  ok  page {name}")

        step = "report"
        rp = window.page("Reports")
        rp.refresh()
        rp._run()
        app.processEvents()
        print("  ok  Profit & Loss report")

        step = "close"
        window.close()
        app.processEvents()
        from updater import read_local_version
        print(f"  ok  version {read_local_version()} from {os.path.dirname(os.path.abspath(__file__))}")
        print("SELF-TEST OK")
        return 0
    except Exception:
        print(f"SELF-TEST FAILED at {step}:")
        traceback.print_exc()
        return 1
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from PyQt6.QtGui import QIcon
    from PyQt6.QtCore import Qt

    # On fractional Windows display scaling (125% / 150% / 175%) the default
    # rounding policy mismatches font metrics against widget geometry, which
    # clips text inside date fields, inputs and table cells. PassThrough uses
    # the exact scale factor and keeps text from being cut off.
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass

    app = QApplication(sys.argv)
    _install_error_handler()
    app.setApplicationName("FinanceBook")
    app.setApplicationDisplayName("FinanceBook")
    app.setStyle("Fusion")

    # App icon (taskbar + window). Drop an .ico at app/assets/icon.ico to set it;
    # absent is fine — Qt falls back to the default. The icon ships inside app/,
    # so it lives next to this file whether running frozen or from source.
    _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "assets", "icon.ico")
    if os.path.exists(_icon_path):
        app.setWindowIcon(QIcon(_icon_path))

    from ui.styles import apply_theme
    apply_theme(app)

    # macOS launcher: an updated app/ that got this far (its code imports and
    # Qt runs) has started; from here a crash in main() is reported to the
    # launcher by the exception itself. Clearing it now means quitting at the
    # welcome or "database not found" dialog isn't mistaken for a failure.
    marker = os.environ.pop("FINANCEBOOK_BOOT_MARKER", "")
    if marker:
        try:
            os.remove(marker)
        except OSError:
            pass

    from db_config import get_active_db_path, register_database, DEFAULT_DB_PATH

    db_path = get_active_db_path()
    first_run = db_path is None

    if first_run and os.path.exists(DEFAULT_DB_PATH):
        # Upgrading from a version that always stored the db at the default
        # location — treat it as already set up rather than re-prompting.
        db_path = DEFAULT_DB_PATH
        register_database(db_path, make_active=True)
        first_run = False

    new_db_name = None
    new_book_type = None
    new_preset = None
    connected = False
    if first_run:
        from ui.database_dialog import DatabaseSetupDialog
        dialog = DatabaseSetupDialog(
            window_title="Welcome to FinanceBook",
            heading="Thank you for using FinanceBook!",
            message=(
                "We're glad to have you here. FinanceBook keeps all your data "
                "in a single database file that lives entirely on your "
                "computer (or wherever you choose to store it) — nothing is "
                "sent to a server.\n\n"
                "Give your first database a name and pick a folder to save "
                "it in. Choosing a Google Drive, OneDrive, or network/server "
                "folder will keep it backed up and synced across your "
                "computers. You'll be able to create more databases later "
                "from the sidebar."),
            default_name="Personal Finances",
            default_location=BASE_DIR,
            accept_label="Get Started",
            show_book_type=True,
            show_connect=True)
        dialog.exec()
        if dialog.connected_path:
            db_path = dialog.connected_path
            connected = True
        else:
            new_db_name = dialog.database_name
            new_book_type = dialog.book_type
            new_preset = dialog.preset
            db_path = _new_database_path(dialog.database_location)
    elif not os.path.exists(db_path):
        resolved = _resolve_missing_database(db_path)
        if resolved is None:
            sys.exit(0)
        db_path, new_db_name, new_book_type, new_preset = resolved
        connected = new_db_name is None

    from sheets.backup import read_user_version
    from sheets.client import SCHEMA_VERSION
    try:
        newer = os.path.getsize(db_path) > 0 and read_user_version(db_path) > SCHEMA_VERSION
    except Exception:
        newer = False
    if newer:
        _offer_update_for_newer_database(db_path)
        sys.exit(1)

    # Take the database's lock (asking if another computer has it), then open
    # it — read-only if the user chose that. Errors are shown by open_with_lock.
    from ui.connect_dialog import open_with_lock
    opened = open_with_lock(None, db_path, book_type=new_book_type)
    if opened is None:
        sys.exit(1)
    db, lock = opened

    if new_db_name is not None:
        db.set_setting("database_name", new_db_name)
        if new_book_type == "business" and new_preset:
            from utils import nav_config
            nav_config.save_modules(db, nav_config.preset_modules(new_preset))
        register_database(db_path, make_active=True)
    elif connected:
        register_database(db_path, make_active=True)
    elif not db.read_only and not db.get_setting("database_name"):
        db.set_setting("database_name", "Personal Finances")
        register_database(db_path, make_active=True)
    # auto-backup, session user and recurring posting are all no-ops read-only.
    db.auto_backup_if_due()
    from db_config import current_user_name
    db.set_session_user(current_user_name())
    try:
        from datetime import date as _date
        posted = db.process_recurring(_date.today().isoformat())
    except Exception:
        posted = []

    from ui.main_window import MainWindow
    window = MainWindow(db, lock)
    window.show()
    # macOS launcher: a rolled-back update is explained here (see launcher.py).
    note = os.environ.pop("FINANCEBOOK_LAUNCH_NOTE", "")
    if note:
        QMessageBox.warning(window, "Update Rolled Back", note)
    if posted:
        QMessageBox.information(window, "Recurring Items Posted",
                                f"{len(posted)} recurring transaction(s) / bill(s) that came due "
                                "were added automatically. See the Recurring page.")

    # First-run welcome guide — shown ONCE, only for brand-new users (never on
    # app updates, nor when connecting a database another computer already
    # uses). `first_run` is true only when no database was registered yet; the
    # persistent flag is a second guard so it can never repeat.
    from db_config import get_flag, set_flag
    if first_run and not connected and not get_flag("onboarding_completed"):
        try:
            window.start_tour()   # interactive coach-mark walkthrough
        except Exception:
            try:                  # fall back to the simple slide guide
                from ui.dialogs.onboarding_dialog import OnboardingDialog
                OnboardingDialog(parent=window).exec()
            except Exception:
                pass  # never let the guide block the app from opening
        set_flag("onboarding_completed", True)

    # Non-blocking update check — runs on a background thread.
    # Reads the latest GitHub release and compares it to app/VERSION.
    from updater import run_update_check
    run_update_check(window)

    code = app.exec()
    window.release_database()
    sys.exit(code)


if __name__ == "__main__":
    main()
