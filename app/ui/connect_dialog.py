"""
Connecting an existing database, and opening any database under its lock.

choose_existing_database() — file picker + read-only checks + confirmations.
open_with_lock()           — take the database's lock (asking what to do if
                             another computer has it), then open it.
"""
import os

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QFileDialog, QMessageBox)

import db_lock
from db_connect import validate_existing_database, onedrive_dir
from sheets.client import SCHEMA_VERSION

LOCK_NOTE = ("The lock is best-effort: the other computer only sees it once OneDrive "
             "has synced it, so let OneDrive finish syncing before you open the "
             "database on your other computer.")


def confirm_existing_database(parent, path: str) -> str | None:
    """
    Run the read-only checks on path and show the user anything they need to
    know. Returns the absolute path if it should be connected, else None.
    """
    chk = validate_existing_database(path)
    if not chk.ok:
        QMessageBox.warning(parent, "Can't Connect Database", chk.error)
        return None
    for warning in chk.warnings:
        if QMessageBox.warning(
                parent, "Connect Database", warning + "\n\nConnect this file anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return None
    if chk.needs_upgrade:
        if QMessageBox.question(
                parent, "Upgrade Database",
                f"This database was last saved by an older version of FinanceBook "
                f"(format {chk.version}; this version uses {SCHEMA_VERSION}).\n\n"
                "Connecting it upgrades it to the new format. A backup is saved to the "
                "“FinanceBook Backups” folder first.\n\n"
                "After the upgrade, your other computer must also update FinanceBook "
                "before it can open this database again.\n\nUpgrade and connect?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return None
    return chk.path


def choose_existing_database(parent) -> str | None:
    """Ask for an existing .db file and check it. Returns its path or None."""
    path, _ = QFileDialog.getOpenFileName(
        parent, "Connect Existing Database", onedrive_dir(),
        "FinanceBook databases (*.db);;All files (*)")
    if not path:
        return None
    return confirm_existing_database(parent, path)


class LockConflictDialog(QDialog):
    """Another live session holds the lock: read-only, take over, or cancel."""
    READ_ONLY, TAKE_OVER, CANCEL = "read_only", "take_over", "cancel"

    def __init__(self, held: db_lock.LockHeld, db_path: str, parent=None):
        super().__init__(parent)
        self.choice = self.CANCEL
        self.setWindowTitle("Database In Use")
        self.setMinimumWidth(560)
        info = held.info
        machine = info.get("machine", "another computer")

        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.setContentsMargins(28, 24, 28, 22)
        title = QLabel("This database is already open")
        title.setObjectName("PageTitle")
        lay.addWidget(title)

        if info.get("corrupt"):
            what = "Another FinanceBook appears to be opening it right now."
        elif held.same_machine:
            what = (db_lock.describe(info) + ".\n\nThat's another FinanceBook window on "
                    "this computer — switch to it instead, or close it first.")
        else:
            what = db_lock.describe(info) + "."
        body = QLabel(f"{os.path.basename(db_path)}\n\n{what}")
        body.setWordWrap(True)
        lay.addWidget(body)

        risk = QLabel(
            "<b>Open Read-Only</b> lets you look without changing anything.<br><br>"
            f"<b>Take Over</b> only if you're sure FinanceBook isn't really running on "
            f"{machine} — for example it crashed or the laptop was shut down. If that "
            "computer has changes OneDrive hasn't uploaded yet, editing here too will "
            "create conflicting copies and one set of changes may be lost.")
        risk.setWordWrap(True)
        risk.setObjectName("InfoBox")
        lay.addWidget(risk)

        note = QLabel(LOCK_NOTE)
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Secondary")
        cancel.clicked.connect(self.reject)
        take = QPushButton("Take Over")
        take.setObjectName("Secondary")
        take.clicked.connect(lambda: self._pick(self.TAKE_OVER))
        ro = QPushButton("Open Read-Only")
        ro.clicked.connect(lambda: self._pick(self.READ_ONLY))
        ro.setDefault(True)
        for b in (cancel, take, ro):
            row.addWidget(b)
        lay.addLayout(row)

    def _pick(self, choice: str):
        self.choice = choice
        self.accept()


def open_with_lock(parent, path: str, book_type: str | None = None):
    """
    Lock and open the database at path. Returns (db, lock) — lock is None when
    opened read-only or when no lock file could be written — or None if the
    user cancelled or it couldn't be opened (they've already been told why).
    """
    from sheets.client import Database
    lock = db_lock.DbLock(path)
    read_only = False
    try:
        lock.acquire()
    except db_lock.LockHeld as held:
        dlg = LockConflictDialog(held, path, parent)
        dlg.exec()
        if dlg.choice == LockConflictDialog.READ_ONLY:
            lock, read_only = None, True
        elif dlg.choice == LockConflictDialog.TAKE_OVER:
            lock.acquire(force=True)
        else:
            return None
    except OSError as e:
        # e.g. a read-only network folder: the lock can't be written at all.
        if QMessageBox.question(
                parent, "No Lock File",
                f"FinanceBook couldn't create its lock file next to the database "
                f"({e.strerror or e}), so it can't warn you if the database is opened on "
                "another computer at the same time.\n\nOpen it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return None
        lock = None
    try:
        db = Database(path, book_type=book_type, read_only=read_only)
    except Exception as e:
        if lock is not None:
            lock.release()
        QMessageBox.critical(parent, "Database Error", f"Could not open database:\n\n{e}")
        return None
    return db, lock
