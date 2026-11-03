"""FinanceBook — Desktop personal finance application."""
import sys
import os


def _get_data_dir() -> str:
    """
    Return FinanceBook's own settings folder, and the suggested default
    location for a new database (the user can pick any other folder instead).

    - Installed build (PyInstaller frozen):  %APPDATA%\\FinanceBook\\
      This keeps app settings in a writable, user-owned location so they
      survive reinstalls and aren't blocked by UAC / Program Files rules.
    - Running from source (development):  same folder as main.py
    """
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        path = os.path.join(appdata, "FinanceBook")
        os.makedirs(path, exist_ok=True)
        return path
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
        else:
            msg = (f"Something went wrong:\n\n{exc_type.__name__}: {text}\n\n"
                   "Your data is safe — nothing was saved for this action. If it keeps "
                   "happening, please report it on the Issues page.")
            title = "FinanceBook"
        traceback.print_exception(exc_type, exc, tb)
        if QApplication.instance() is not None:
            QMessageBox.warning(QApplication.activeWindow(), title, msg)

    sys.excepthook = hook


def main():
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
            show_book_type=True)
        dialog.exec()
        new_db_name = dialog.database_name
        new_book_type = dialog.book_type
        location = dialog.database_location
        os.makedirs(location, exist_ok=True)
        db_path = os.path.join(location, "finances.db")
        if os.path.exists(db_path):
            i = 2
            while os.path.exists(os.path.join(location, f"finances_{i}.db")):
                i += 1
            db_path = os.path.join(location, f"finances_{i}.db")
    elif not os.path.exists(db_path):
        QMessageBox.warning(None, "Database Not Found",
            f"Could not find your database at:\n{db_path}\n\n"
            "The folder may not be available right now (e.g. a cloud drive "
            "that hasn't finished syncing). A new, empty database will be "
            "created there instead.")

    try:
        from sheets.client import Database
        db = Database(db_path, book_type=new_book_type if first_run else None)
    except Exception as e:
        QMessageBox.critical(None, "Database Error",
            f"Could not open database:\n\n{e}")
        sys.exit(1)

    if first_run:
        db.set_setting("database_name", new_db_name)
        register_database(db_path, make_active=True)
    elif not db.get_setting("database_name"):
        db.set_setting("database_name", "Personal Finances")
        register_database(db_path, make_active=True)
    db.auto_backup_if_due()
    from db_config import current_user_name
    db.set_session_user(current_user_name())
    try:
        from datetime import date as _date
        posted = db.process_recurring(_date.today().isoformat())
    except Exception:
        posted = []

    from ui.main_window import MainWindow
    window = MainWindow(db)
    window.show()
    if posted:
        QMessageBox.information(window, "Recurring Items Posted",
                                f"{len(posted)} recurring transaction(s) / bill(s) that came due "
                                "were added automatically. See the Recurring page.")

    # First-run welcome guide — shown ONCE, only for brand-new users (never on
    # app updates). `first_run` is true only when no database was registered yet;
    # the persistent flag is a second guard so it can never repeat.
    from db_config import get_flag, set_flag
    if first_run and not get_flag("onboarding_completed"):
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

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
