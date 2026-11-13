"""Settings page — database management and QuickBooks one-time migration."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QMessageBox,
    QScrollArea, QFrame, QLineEdit, QCheckBox, QSpinBox, QFileDialog, QComboBox,
)
from PyQt6.QtCore import Qt, QTimer
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
        self._scroll = scroll

        inner = QWidget()
        lay   = QVBoxLayout(inner)
        lay.setContentsMargins(28, 24, 28, 32)
        lay.setSpacing(24)

        lay.addWidget(PageTitle("Settings"))

        lay.addWidget(self._build_qb_section())
        lay.addWidget(self._build_database_section())
        lay.addWidget(self._build_book_type_section())
        lay.addWidget(self._build_features_section())
        lay.addWidget(self._build_backup_section())
        lay.addWidget(self._build_receipt_files_section())
        lay.addWidget(self._build_ai_section())
        lay.addWidget(self._build_control_section())
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
        info.setObjectName("InfoBox")
        lay.addWidget(info)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._wizard_btn = QPushButton("Open Import Wizard…")
        self._wizard_btn.setObjectName("Success")
        self._wizard_btn.clicked.connect(self._open_import_wizard)
        btn_row.addWidget(self._wizard_btn)
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

        connect_btn = QPushButton("Connect Existing Database…")
        connect_btn.setObjectName("Secondary")
        connect_btn.clicked.connect(self._connect_existing)
        lay.addRow("", connect_btn)

        from ui.connect_dialog import LOCK_NOTE
        sync_note = QLabel(
            "Using the same database on two computers (for example in a OneDrive folder)? "
            "Use <b>Connect Existing Database…</b> on the second computer. While a database "
            "is open, FinanceBook keeps a small “.lock” file next to it so the other "
            "computer can open it read-only instead of editing it at the same time. "
            + LOCK_NOTE)
        sync_note.setObjectName("Muted")
        sync_note.setWordWrap(True)
        lay.addRow("", sync_note)

        return grp

    def _connect_existing(self):
        mw = getattr(self, "_main_window", None)
        if mw is not None and hasattr(mw, "connect_existing_database"):
            mw.connect_existing_database()

    def _save_db_name(self):
        name = self._db_name_edit.text().strip()
        if not name:
            return
        self.db.set_setting("database_name", name)
        QMessageBox.information(self, "Saved",
            f"Database renamed to \"{name}\".\n"
            "The new name will appear in the sidebar dropdown.")

    # ── Book type ─────────────────────────────────────────────────────────────

    def _build_book_type_section(self) -> QGroupBox:
        grp = QGroupBox("Book Type")
        lay = QVBoxLayout(grp)
        lay.setSpacing(10)
        self._book_info = QLabel("")
        self._book_info.setWordWrap(True)
        self._book_info.setTextFormat(Qt.TextFormat.RichText)
        self._book_info.setObjectName("InfoBox")
        lay.addWidget(self._book_info)
        row = QHBoxLayout()
        row.addStretch()
        self._book_btn = QPushButton("")
        self._book_btn.setObjectName("Secondary")
        self._book_btn.clicked.connect(self._toggle_book_type)
        row.addWidget(self._book_btn)
        lay.addLayout(row)
        return grp

    def _refresh_book_type(self):
        if self.db.is_business():
            self._book_info.setText(
                "This is a <b>Business</b> book: Schedule C categories, owner "
                "investment tracking and the Business pages are turned on.")
            self._book_btn.setText("Switch to Personal…")
        else:
            self._book_info.setText(
                "This is a <b>Personal</b> book. Switch to <b>Business</b> to "
                "track a company: it adds Schedule C categories, built-in owner "
                "accounts (Capital, Draws, Due to Owner, Petty Cash) and the "
                "Business pages. Nothing you already have is changed or removed.")
            self._book_btn.setText("Switch to Business…")

    def _toggle_book_type(self):
        to_business = not self.db.is_business()
        if to_business:
            msg = ("Turn this database into a Business book?\n\n"
                   "FinanceBook will back it up first, then ADD business categories, "
                   "owner equity accounts and an 'Owner' person. Your existing "
                   "accounts, transactions and categories are not changed.")
        else:
            msg = ("Switch this database back to a Personal book?\n\n"
                   "Business pages will be hidden. Nothing is deleted — the business "
                   "categories and owner accounts stay, and switching back to "
                   "Business shows everything again.")
        if QMessageBox.question(self, "Change Book Type", msg,
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            if to_business:
                self.db.backup_now("manual")
            self.db.set_book_type("business" if to_business else "personal")
        except Exception as e:
            QMessageBox.critical(self, "Could Not Switch", str(e))
            return
        self._refresh_book_type()
        mw = getattr(self, "_main_window", None)
        if mw is not None and hasattr(mw, "refresh_navigation"):
            mw.refresh_navigation()

    # ── Sidebar & Features (Business books) ───────────────────────────────────

    def _build_features_section(self) -> QGroupBox:
        from utils import nav_config as NC
        grp = QGroupBox("Sidebar && Features")
        self._features_grp = grp
        lay = QVBoxLayout(grp)
        lay.setSpacing(10)
        info = QLabel(
            "Choose which business features appear in the sidebar. Hiding one never "
            "deletes anything: its data stays in your books and in every report (balance "
            "sheet, profit &amp; loss, cost of goods sold, depreciation, payroll and sales "
            "tax owed). The choice is saved in this database, so it follows the database "
            "to your other computer (for example through OneDrive).")
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setObjectName("InfoBox")
        lay.addWidget(info)

        row = QHBoxLayout()
        row.addWidget(QLabel("Business size:"))
        self._preset_combo = QComboBox()
        for p in NC.PRESETS:
            self._preset_combo.addItem(p.label, p.key)
            self._preset_combo.setItemData(self._preset_combo.count() - 1, p.description,
                                           Qt.ItemDataRole.ToolTipRole)
        self._preset_combo.addItem("Custom", "custom")
        self._preset_combo.activated.connect(self._on_preset)
        row.addWidget(self._preset_combo)
        self._preset_desc = QLabel("")
        self._preset_desc.setObjectName("Muted")
        self._preset_desc.setWordWrap(True)
        row.addWidget(self._preset_desc, 1)
        lay.addLayout(row)

        self._module_checks = {}
        self._module_notes = {}
        for m in NC.MODULES:
            chk = QCheckBox(m.label.replace("&", "&&"))
            chk.toggled.connect(lambda on, k=m.key: self._on_module_toggled(k, on))
            lay.addWidget(chk)
            note = QLabel(m.description)
            note.setObjectName("Muted")
            note.setWordWrap(True)
            note.setTextFormat(Qt.TextFormat.RichText)
            note.setContentsMargins(28, 0, 0, 4)
            lay.addWidget(note)
            self._module_checks[m.key] = chk
            self._module_notes[m.key] = note

        locked = QLabel(
            "🔒 Always shown: Dashboard, Accounts (your chart of accounts, including "
            "income and expense accounts), Transactions (with Import, Reconcile, "
            "Transfers and Recurring tabs), Contacts, Reports, Rules and Settings.")
        locked.setObjectName("Muted")
        locked.setWordWrap(True)
        locked.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(locked)
        self._features_ro_note = QLabel(
            "This database is open read-only, so features can't be changed here.")
        self._features_ro_note.setObjectName("Muted")
        self._features_ro_note.setWordWrap(True)
        lay.addWidget(self._features_ro_note)
        return grp

    @staticmethod
    def _plural(n: int, one: str, many: str) -> str:
        return f"{n} {one if n == 1 else many}"

    def _module_data(self) -> dict:
        """How much each module already holds, as text ('' when empty)."""
        db, P = self.db, self._plural
        counts = {
            "receipts": lambda: P(len(db.get_receipts()), "receipt", "receipts"),
            "invoices": lambda: P(len(db.get_invoices()) + len(db.get_bills()),
                                  "invoice or bill", "invoices and bills"),
            "inventory": lambda: P(len(db.get_items(active_only=False)), "item", "items"),
            "assets": lambda: P(len(db.get_assets()), "asset", "assets"),
            "payroll": lambda: P(len(db.get_employees(active_only=False)),
                                 "person", "people"),
            "taxes": lambda: P(len(db.get_mileage()), "mileage trip", "mileage trips"),
            "loans": lambda: P(len(db.get_loans()), "loan", "loans"),
        }
        out = {}
        for key, fn in counts.items():
            try:
                text = fn()
            except Exception:
                text = ""
            out[key] = "" if text.startswith("0 ") else text
        return out

    def _refresh_features(self):
        from utils import nav_config as NC
        business = self.db.is_business()
        self._features_grp.setVisible(business)
        if not business:
            return
        enabled = NC.enabled_modules(self.db)
        data = self._module_data()
        ro = self.db.read_only
        tip = ("This database is open read-only (it's in use on another computer), so "
               "features can't be changed here." if ro else "")
        preset = NC.preset_for(enabled)
        self._preset_combo.blockSignals(True)
        self._preset_combo.setCurrentIndex(max(self._preset_combo.findData(preset), 0))
        self._preset_combo.blockSignals(False)
        self._preset_combo.setEnabled(not ro)
        self._preset_combo.setToolTip(tip)
        self._preset_desc.setText(
            NC.PRESETS_BY_KEY[preset].description if preset in NC.PRESETS_BY_KEY
            else "Your own mix of features.")
        for m in NC.MODULES:
            on = m.key in enabled
            chk = self._module_checks[m.key]
            chk.blockSignals(True)
            chk.setChecked(on)
            chk.blockSignals(False)
            chk.setEnabled(not ro)
            chk.setToolTip(tip)
            has = data.get(m.key, "")
            if has and not on:
                extra = (f"<br><span style=\"color:{WARNING};\">{m.label.replace('&', '&amp;')} "
                         f"is hidden but has {has} — hiding doesn't delete anything, and "
                         "reports still include them.</span>")
            elif has:
                extra = f" &nbsp;<i>Has {has}.</i>"
            else:
                extra = ""
            self._module_notes[m.key].setText(m.description.replace("&", "&amp;") + extra)
        self._features_ro_note.setVisible(ro)

    def _apply_modules(self, enabled):
        from utils import nav_config as NC
        if self.db.read_only:
            self._refresh_features()
            return
        mw = getattr(self, "_main_window", None)
        if mw is not None and hasattr(mw, "set_modules"):
            mw.set_modules(enabled)     # rebuilds the sidebar, then refreshes this page
        else:
            NC.save_modules(self.db, enabled)
        self._refresh_features()

    def _on_module_toggled(self, key: str, on: bool):
        from utils import nav_config as NC
        enabled = set(NC.enabled_modules(self.db))
        enabled = enabled | {key} if on else enabled - {key}
        self._apply_modules(enabled)

    def _on_preset(self, _index: int = 0):
        from utils import nav_config as NC
        key = self._preset_combo.currentData()
        if key not in NC.PRESETS_BY_KEY:
            self._refresh_features()       # "Custom" only names a mix; nothing to apply
            return
        self._apply_modules(NC.preset_modules(key))

    def show_features_section(self):
        """Scroll Sidebar & Features into view (from the sidebar's Add features link)."""
        def scroll():
            try:
                self._scroll.verticalScrollBar().setValue(max(self._features_grp.y() - 12, 0))
            except RuntimeError:
                pass        # the page was closed before the deferred call ran
        scroll()
        QTimer.singleShot(0, scroll)        # again once a just-built page has its layout

    # ── Backups ───────────────────────────────────────────────────────────────

    def _build_backup_section(self) -> QGroupBox:
        grp = QGroupBox("Backups")
        lay = QVBoxLayout(grp)
        lay.setSpacing(10)
        info = QLabel(
            "Backups are saved in a <b>FinanceBook Backups</b> folder next to this "
            "database, so a database in OneDrive / Google Drive has its backups "
            "synced too. FinanceBook also backs up automatically before every "
            "upgrade that changes the database.")
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setObjectName("Muted")
        lay.addWidget(info)

        auto_row = QHBoxLayout()
        self._auto_chk = QCheckBox("Back up automatically once a day, keeping the last")
        self._auto_chk.toggled.connect(self._save_backup_prefs)
        auto_row.addWidget(self._auto_chk)
        self._keep_spin = QSpinBox()
        self._keep_spin.setRange(1, 100)
        self._keep_spin.valueChanged.connect(self._save_backup_prefs)
        auto_row.addWidget(self._keep_spin)
        auto_row.addWidget(QLabel("backups"))
        auto_row.addStretch()
        lay.addLayout(auto_row)

        self._backup_status = QLabel("")
        self._backup_status.setObjectName("Muted")
        self._backup_status.setWordWrap(True)
        lay.addWidget(self._backup_status)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        open_btn = QPushButton("Open Backups Folder")
        open_btn.setObjectName("Secondary")
        open_btn.clicked.connect(self._open_backup_folder)
        btn_row.addWidget(open_btn)
        restore_btn = QPushButton("Restore from Backup…")
        restore_btn.setObjectName("Secondary")
        restore_btn.clicked.connect(self._restore_backup)
        btn_row.addWidget(restore_btn)
        backup_btn = QPushButton("Back Up Now")
        backup_btn.clicked.connect(self._backup_now)
        btn_row.addWidget(backup_btn)
        lay.addLayout(btn_row)
        return grp

    # ── Receipt files ─────────────────────────────────────────────────────────

    def _build_receipt_files_section(self) -> QGroupBox:
        grp = QGroupBox("Receipt Files")
        lay = QVBoxLayout(grp)
        lay.setSpacing(10)
        info = QLabel(
            "When you add a receipt, FinanceBook saves its own copy in a <b>FinanceBook "
            "Receipts</b> folder next to this database (one subfolder per database), so it "
            "never mixes with receipts you keep yourself.")
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setObjectName("Muted")
        lay.addWidget(info)
        self._legacy_lbl = QLabel("")
        self._legacy_lbl.setWordWrap(True)
        lay.addWidget(self._legacy_lbl)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._move_receipts_btn = QPushButton("Move Older Copies")
        self._move_receipts_btn.setObjectName("Secondary")
        self._move_receipts_btn.clicked.connect(self._move_legacy_receipts)
        btn_row.addWidget(self._move_receipts_btn)
        open_btn = QPushButton("Open Receipts Folder")
        open_btn.setObjectName("Secondary")
        open_btn.clicked.connect(self._open_receipts_folder)
        btn_row.addWidget(open_btn)
        lay.addLayout(btn_row)
        self._refresh_receipt_files()
        return grp

    def _refresh_receipt_files(self):
        n = len(self.db.legacy_receipt_copies())
        self._legacy_lbl.setVisible(bool(n))
        self._move_receipts_btn.setVisible(bool(n))
        self._move_receipts_btn.setEnabled(not self.db.read_only)
        if n:
            self._legacy_lbl.setText(
                f"{n} receipt cop{'ies' if n != 1 else 'y'} from earlier versions "
                f"{'are' if n != 1 else 'is'} still in the shared <b>receipts</b> folder. "
                "<b>Move Older Copies</b> moves only those files into FinanceBook Receipts — "
                "anything else in that folder is left alone.")

    def _open_receipts_folder(self):
        import os
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        folder = self.db.receipts_dir()
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _move_legacy_receipts(self):
        n = len(self.db.legacy_receipt_copies())
        if not n or QMessageBox.question(
                self, "Move Receipt Copies",
                f"Move FinanceBook's {n} receipt cop{'ies' if n != 1 else 'y'} from the "
                f"“receipts” folder into “FinanceBook Receipts”?\n\nOnly files FinanceBook "
                "copied there itself are moved. Your own files stay where they are.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        res = self.db.move_legacy_receipts()
        self._refresh_receipt_files()
        msg = f"Moved {res['moved']} receipt cop{'ies' if res['moved'] != 1 else 'y'}."
        if res["missing"]:
            msg += f"\n{res['missing']} file(s) were already missing and were skipped."
        if res["failed"]:
            msg += (f"\n{res['failed']} couldn't be moved (in use or still syncing?) and were "
                    "left where they were — try again later.")
        QMessageBox.information(self, "Receipt Copies", msg)

    def _refresh_backups(self):
        from sheets.backup import list_backups
        self._auto_chk.blockSignals(True)
        self._keep_spin.blockSignals(True)
        self._auto_chk.setChecked(self.db.get_setting("auto_backup_enabled", "1") == "1")
        self._keep_spin.setValue(int(self.db.get_setting("auto_backup_keep", "10") or 10))
        self._auto_chk.blockSignals(False)
        self._keep_spin.blockSignals(False)
        backups = list_backups(self.db.db_path)
        if backups:
            import os
            from datetime import datetime
            newest = backups[0]
            when = datetime.fromtimestamp(os.path.getmtime(newest)).strftime("%m/%d/%Y %I:%M %p")
            self._backup_status.setText(
                f"{len(backups)} backup{'s' if len(backups) != 1 else ''} on file. "
                f"Newest: {os.path.basename(newest)} ({when}).")
        else:
            self._backup_status.setText("No backups yet.")

    def _save_backup_prefs(self, *_):
        self.db.set_setting("auto_backup_enabled", "1" if self._auto_chk.isChecked() else "0")
        self.db.set_setting("auto_backup_keep", str(self._keep_spin.value()))

    def _backup_now(self):
        try:
            path = self.db.backup_now("manual")
        except Exception as e:
            QMessageBox.critical(self, "Backup Failed", str(e))
            return
        self._refresh_backups()
        QMessageBox.information(self, "Backed Up", f"Saved a backup to:\n{path}")

    def _open_backup_folder(self):
        import os
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        from sheets.backup import backup_dir
        folder = backup_dir(self.db.db_path)
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _restore_backup(self):
        import os
        from sheets.backup import backup_dir, restore_database
        if self.db.read_only:
            QMessageBox.information(
                self, "Restore", "This database is open read-only because it's in use "
                "elsewhere, so a backup can't be restored into it from here.")
            return
        start = backup_dir(self.db.db_path)
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a Backup to Restore",
            start if os.path.isdir(start) else os.path.dirname(self.db.db_path),
            "FinanceBook databases (*.db)")
        if not path:
            return
        if os.path.abspath(path) == os.path.abspath(self.db.db_path):
            QMessageBox.warning(self, "Restore", "That is the database that's open now.")
            return
        db_name = self.db.get_setting("database_name", "this database")
        if QMessageBox.warning(
                self, "Restore Backup",
                f"Replace everything in \"{db_name}\" with the contents of:\n\n"
                f"{os.path.basename(path)}\n\nThe current data is backed up first, "
                "so you can undo this by restoring that backup.",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Ok:
            return
        try:
            safety = restore_database(path, self.db.db_path)
        except Exception as e:
            QMessageBox.critical(self, "Restore Failed", str(e))
            return
        QMessageBox.information(
            self, "Restored",
            "Backup restored. Your previous data was saved as:\n"
            f"{os.path.basename(safety)}")
        mw = getattr(self, "_main_window", None)
        if mw is not None and hasattr(mw, "reopen_current"):
            mw.reopen_current()     # same file, same lock — just re-read it

    # ── AI receipt reading (opt-in) ──────────────────────────────────────────

    def _build_ai_section(self) -> QGroupBox:
        grp = QGroupBox("AI Receipt Reading  (optional)")
        lay = QVBoxLayout(grp)
        lay.setSpacing(10)
        info = QLabel(
            "Text receipts (emails, web pages, text PDFs) are read on your computer. For "
            "photos and scanned PDFs you can let <b>Claude</b> read them: the receipt file "
            "is sent to Anthropic's API using your own API key. Nothing else from your "
            "books is sent. The key is stored only on this computer (encrypted for your "
            "Windows account) — never in the database file.")
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setObjectName("Muted")
        lay.addWidget(info)
        self._ai_chk = QCheckBox("Use AI to read photos and scanned PDFs")
        self._ai_chk.toggled.connect(self._toggle_ai)
        lay.addWidget(self._ai_chk)
        form = QFormLayout()
        self._ai_key = QLineEdit()
        self._ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ai_key.setPlaceholderText("Paste your Anthropic API key (sk-ant-…)")
        form.addRow("API key:", self._ai_key)
        self._ai_model = QLineEdit()
        self._ai_model.setPlaceholderText("claude-opus-5-5")
        form.addRow("Model:", self._ai_model)
        lay.addLayout(form)
        row = QHBoxLayout()
        self._ai_status = QLabel("")
        self._ai_status.setObjectName("Muted")
        row.addWidget(self._ai_status, 1)
        clear_btn = QPushButton("Remove Key")
        clear_btn.setObjectName("Secondary")
        clear_btn.clicked.connect(self._clear_ai_key)
        row.addWidget(clear_btn)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_ai)
        row.addWidget(save_btn)
        lay.addLayout(row)
        return grp

    def _refresh_ai(self):
        from db_config import ai_settings
        from utils.secret_store import mask
        s = ai_settings()
        self._ai_chk.blockSignals(True)
        self._ai_chk.setChecked(s["enabled"])
        self._ai_chk.blockSignals(False)
        self._ai_key.clear()
        self._ai_model.setText(s["model"])
        self._ai_status.setText(f"Key saved: {mask(s['api_key'])}" if s["api_key"]
                                else "No key saved.")

    def _toggle_ai(self, on: bool):
        from db_config import set_flag, get_flag
        if on and not get_flag("ai_receipts_notice_ack"):
            if QMessageBox.question(
                    self, "Send Receipts to Anthropic?",
                    "When you click “Read with AI” on a receipt, that receipt file is "
                    "uploaded to Anthropic's API so Claude can read it. Usage is billed to "
                    "your Anthropic account.\n\nTurn on AI receipt reading?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                self._ai_chk.blockSignals(True)
                self._ai_chk.setChecked(False)
                self._ai_chk.blockSignals(False)
                return
            set_flag("ai_receipts_notice_ack", True)
        set_flag("ai_receipts_enabled", on)

    def _save_ai(self):
        from db_config import set_ai_api_key, set_value
        key = self._ai_key.text().strip()
        if key:
            if not key.startswith("sk-"):
                QMessageBox.warning(self, "API Key", "That doesn't look like an Anthropic "
                                    "API key (they start with “sk-”).")
                return
            set_ai_api_key(key)
        set_value("ai_receipts_model", self._ai_model.text().strip() or "claude-opus-5-5")
        self._refresh_ai()

    def _clear_ai_key(self):
        from db_config import set_ai_api_key
        set_ai_api_key("")
        self._refresh_ai()

    # ── Period lock & audit trail ────────────────────────────────────────────

    def _build_control_section(self) -> QGroupBox:
        from ui.widgets import DateField
        grp = QGroupBox("Period Lock && Audit Trail")
        lay = QVBoxLayout(grp)
        lay.setSpacing(10)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Your name (recorded with every change):"))
        self._user_name = QLineEdit()
        name_row.addWidget(self._user_name, 1)
        save_name = QPushButton("Save")
        save_name.setObjectName("Secondary")
        save_name.clicked.connect(self._save_user_name)
        name_row.addWidget(save_name)
        lay.addLayout(name_row)

        self._lock_lbl = QLabel("")
        self._lock_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._lock_lbl.setWordWrap(True)
        lay.addWidget(self._lock_lbl)
        lock_row = QHBoxLayout()
        lock_row.addWidget(QLabel("Lock everything dated on or before"))
        self._lock_date = DateField()
        lock_row.addWidget(self._lock_date)
        lock_btn = QPushButton("Lock")
        lock_btn.clicked.connect(self._set_lock)
        lock_row.addWidget(lock_btn)
        unlock_btn = QPushButton("Unlock")
        unlock_btn.setObjectName("Secondary")
        unlock_btn.clicked.connect(self._clear_lock)
        lock_row.addWidget(unlock_btn)
        lock_row.addStretch()
        audit_btn = QPushButton("View Audit Log…")
        audit_btn.setObjectName("Secondary")
        audit_btn.clicked.connect(self._show_audit)
        lock_row.addWidget(audit_btn)
        lay.addLayout(lock_row)
        hint = QLabel("Close a month or year after you've reconciled or filed it, so nothing in it "
                      "can be changed by accident. Every add, edit and delete is logged with who "
                      "made it and when.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return grp

    def _refresh_control(self):
        from db_config import current_user_name
        from ui.widgets import fmt_date
        from PyQt6.QtCore import QDate
        self._user_name.setText(current_user_name())
        ld = self.db.lock_date()
        self._lock_lbl.setText(f"<b>Books locked through {fmt_date(ld)}.</b>" if ld
                               else "No period is locked.")
        if ld:
            self._lock_date.setDate(QDate.fromString(ld, "yyyy-MM-dd"))

    def _save_user_name(self):
        from db_config import set_value, current_user_name
        set_value("user_name", self._user_name.text().strip())
        self.db.set_session_user(current_user_name())
        QMessageBox.information(self, "Saved", "Your name will be recorded with your changes.")

    def _set_lock(self):
        from ui.widgets import fmt_date
        d = self._lock_date.date().toString("yyyy-MM-dd")
        if QMessageBox.question(
                self, "Lock Period",
                f"Lock all transactions dated on or before {fmt_date(d)}? They can't be added, "
                "edited or deleted until you unlock.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            self.db.set_lock_date(d)
            self._refresh_control()

    def _clear_lock(self):
        self.db.set_lock_date("")
        self._refresh_control()

    def _show_audit(self):
        from ui.dialogs.audit_dialog import AuditDialog
        AuditDialog(self.db, self).exec()

    # ── Reset section ─────────────────────────────────────────────────────────

    def _build_reset_section(self) -> QGroupBox:
        grp = QGroupBox("Clear or Delete")
        grp.setObjectName("DangerZone")
        lay = QVBoxLayout(grp)
        lay.setSpacing(10)

        warn = QLabel(
            "<b>Clear All Data</b> deletes all accounts, transactions, rules, and "
            "reconciliations in this database but keeps the database itself, its "
            "name and categories — use it to start over with a clean import.<br>"
            "<b>Delete This Database</b> removes the whole database from FinanceBook "
            "and moves its file to the Recycle Bin."
        )
        warn.setWordWrap(True)
        warn.setTextFormat(Qt.TextFormat.RichText)
        warn.setStyleSheet("color: #8f1d22; font-size:12px; font-weight:400;")
        lay.addWidget(warn)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        reset_btn = QPushButton("Clear All Data…")
        reset_btn.setObjectName("Danger")
        reset_btn.clicked.connect(self._do_reset)
        btn_row.addWidget(reset_btn)
        self._delete_db_btn = QPushButton("Delete This Database…")
        self._delete_db_btn.setObjectName("Danger")
        self._delete_db_btn.clicked.connect(self._delete_database)
        btn_row.addWidget(self._delete_db_btn)
        lay.addLayout(btn_row)
        return grp

    def _delete_database(self):
        mw = getattr(self, "_main_window", None)
        if mw is not None and hasattr(mw, "delete_current_database"):
            mw.delete_current_database()

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

        guide_row = QHBoxLayout()
        guide_btn = QPushButton("Show Welcome Guide")
        guide_btn.setObjectName("Secondary")
        guide_btn.clicked.connect(self._show_welcome_guide)
        guide_row.addWidget(guide_btn)
        guide_row.addStretch()
        lay.addLayout(guide_row)
        return grp

    def _show_welcome_guide(self):
        mw = getattr(self, "_main_window", None)
        if mw is not None and hasattr(mw, "start_tour"):
            mw.start_tour()
            return
        from ui.dialogs.onboarding_dialog import OnboardingDialog
        OnboardingDialog(parent=self).exec()

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        self._db_name_edit.setText(
            self.db.get_setting("database_name", ""))
        self._refresh_book_type()
        self._refresh_features()
        self._refresh_backups()
        self._refresh_receipt_files()
        self._refresh_ai()
        self._refresh_control()
