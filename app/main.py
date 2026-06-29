"""FinanceBook — Desktop personal finance application."""
import sys
import os


def _get_data_dir() -> str:
    """
    Return the directory where finances.db should live.

    - Installed build (PyInstaller frozen):  %APPDATA%\\FinanceBook\\
      This keeps the database in a writable, user-owned location so it
      survives reinstalls and isn't blocked by UAC / Program Files rules.
    - Running from source (development):  same folder as main.py
      This preserves the OneDrive-sync behaviour you rely on for your own use.
    """
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        path = os.path.join(appdata, "FinanceBook")
        os.makedirs(path, exist_ok=True)
        return path
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _get_data_dir()
DB_PATH  = os.path.join(BASE_DIR, "finances.db")


def main():
    from PyQt6.QtWidgets import QApplication, QMessageBox, QInputDialog
    from PyQt6.QtGui import QFont
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
    app.setApplicationName("FinanceBook")
    app.setApplicationDisplayName("FinanceBook")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    from ui.styles import QSS
    app.setStyleSheet(QSS)

    try:
        from sheets.client import Database
        db = Database(DB_PATH)
    except Exception as e:
        QMessageBox.critical(None, "Database Error",
            f"Could not open finances.db:\n\n{e}")
        sys.exit(1)

    # First-run: ask the user to name this database
    if not db.get_setting("database_name"):
        name, ok = QInputDialog.getText(
            None,
            "Welcome to FinanceBook",
            "Give this database a name\n(you can create more databases later from the sidebar):",
            text="Personal Finances")
        db.set_setting("database_name",
                       name.strip() if (ok and name.strip()) else "Personal Finances")

    from ui.main_window import MainWindow
    window = MainWindow(db)
    window.show()

    # Non-blocking update check — runs on a background thread.
    # Set VERSION_URL in updater.py before shipping.
    from updater import run_update_check
    run_update_check(window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
