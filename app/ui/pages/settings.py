"""Settings page — database management and QuickBooks one-time migration."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QMessageBox,
    QScrollArea, QFrame, QLineEdit,
)
from PyQt6.QtCore import Qt
from ui.widgets import PageTitle
from ui.styles  import SUCCESS, DANGER, WARNING
from updater    import read_local_version


class SettingsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build()

    def _build(self):
        # Outer scroll area so it works at any window height
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        lay   = QVBoxLayout(inner)
        lay.setContentsMargins(28, 24, 28, 32)
        lay.setSpacing(24)

        lay.addWidget(PageTitle("Settings"))

        lay.addWidget(self._build_qb_section())
        lay.addWidget(self._build_database_section())
        lay.addWidget(self._build_reset_section())
        lay.addWidget(self._build_about_section())
        lay.addStretch()

        scroll.setWidget(inner)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    # ── QuickBooks migration (opens a popup wizard) ────────────────────────────

    def _build_qb_section(self) -> QGroupBox:
        grp = QGroupBox("QuickBooks Migration  (one-time import)")
        lay = QVBoxLayout(grp)
        lay.setSpacing(12)

        info = QLabel(
            "Bring your data over from QuickBooks in two steps — accounts and "
            "categories from an <b>IIF</b> export, then transactions from an "
            "<b>Excel</b> export. The import opens in its own window to keep "
            "this page tidy."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setStyleSheet(
            "background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px;"
            "padding:12px; font-size:12px;")
        lay.addWidget(info)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        open_btn = QPushButton("Open Import Wizard…")
        open_btn.setObjectName("Success")
        open_btn.clicked.connect(self._open_import_wizard)
        btn_row.addWidget(open_btn)
        lay.addLayout(btn_row)
        return grp

    def _open_import_wizard(self):
        from ui.dialogs.import_dialog import ImportDialog
        ImportDialog(self.db, parent=self).exec()

    # ── Database section ──────────────────────────────────────────────────────

    def _build_database_section(self) -> QGroupBox:
        grp = QGroupBox("Database")
        lay = QFormLayout(grp)
        lay.setSpacing(10)

        self._db_name_edit = QLineEdit()
        lay.addRow("Database name:", self._db_name_edit)

        save_btn = QPushButton("Save Name")
        save_btn.setObjectName("Secondary")
        save_btn.clicked.connect(self._save_db_name)
        lay.addRow("", save_btn)

        path_label = QLabel()
        path_label.setObjectName("Muted")
        path_label.setWordWrap(True)
        path_label.setText(f"File: {self.db.db_path}")
        lay.addRow("Location:", path_label)

        return grp

    def _save_db_name(self):
        name = self._db_name_edit.text().strip()
        if not name:
            return
        self.db.set_setting("database_name", name)
        QMessageBox.information(self, "Saved",
            f"Database renamed to \"{name}\".\n"
            "The new name will appear in the sidebar dropdown.")

    # ── Reset section ─────────────────────────────────────────────────────────

    def _build_reset_section(self) -> QGroupBox:
        grp = QGroupBox("Reset Database")
        grp.setStyleSheet("QGroupBox { border: 1px solid #ef4444; border-radius:6px; "
                          "margin-top:8px; font-weight:bold; color:#ef4444; } "
                          "QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }")
        lay = QVBoxLayout(grp)
        lay.setSpacing(10)

        warn = QLabel(
            "<b>This deletes all accounts, transactions, rules, and "
            "reconciliations in this database.</b> Use this when you want to "
            "start over with a clean import. Your categories, the database file, "
            "and its name are kept — only the data inside is cleared. "
            "This cannot be undone."
        )
        warn.setWordWrap(True)
        warn.setTextFormat(Qt.TextFormat.RichText)
        warn.setStyleSheet("color: #7f1d1d; font-size:12px;")
        lay.addWidget(warn)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        reset_btn = QPushButton("Clear All Data…")
        reset_btn.setStyleSheet(
            "QPushButton { background:#ef4444; color:white; font-weight:bold; "
            "padding:6px 18px; border-radius:5px; border:none; } "
            "QPushButton:hover { background:#dc2626; }")
        reset_btn.clicked.connect(self._do_reset)
        btn_row.addWidget(reset_btn)
        lay.addLayout(btn_row)
        return grp

    def _do_reset(self):
        db_name = self.db.get_setting("database_name", "this database")
        reply = QMessageBox.warning(
            self, "Clear All Data",
            f"This will permanently delete ALL accounts, transactions, "
            f"rules, and reconciliations in \"{db_name}\".\n\n"
            f"Your categories will be kept.\n\n"
            f"Type  DELETE  in the box below to confirm.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if reply != QMessageBox.StandardButton.Ok:
            return

        # Second confirmation — require typing "DELETE"
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "Confirm Reset", "Type DELETE to confirm:")
        if not ok or text.strip().upper() != "DELETE":
            QMessageBox.information(self, "Cancelled", "Reset cancelled.")
            return

        try:
            self.db.clear_all_data()
            QMessageBox.information(
                self, "Done",
                f"\"{db_name}\" has been cleared. Your categories were kept.\n\n"
                "You can now re-import your QuickBooks data:\n"
                "1. Settings → IIF file (accounts; existing categories are kept)\n"
                "2. Settings → Excel file (transactions + rules)\n\n"
                "Restart the app or navigate away and back to refresh all pages.")
        except Exception as e:
            QMessageBox.critical(self, "Reset Failed", str(e))

    # ── About section ─────────────────────────────────────────────────────────

    def _build_about_section(self) -> QGroupBox:
        grp = QGroupBox("About")
        lay = QVBoxLayout(grp)
        lay.addWidget(QLabel(f"<b>FinanceBook</b>  v{read_local_version()}"))
        muted = QLabel("Local-first personal and business finance — data stored in SQLite, "
                       "synced via OneDrive / Google Drive.")
        muted.setObjectName("Muted")
        muted.setWordWrap(True)
        lay.addWidget(muted)
        return grp

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        self._db_name_edit.setText(
            self.db.get_setting("database_name", ""))
