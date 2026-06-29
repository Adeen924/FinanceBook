"""Settings page — database management and QuickBooks one-time migration."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QFileDialog, QMessageBox,
    QHeaderView, QScrollArea, QFrame, QLineEdit, QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt
from ui.widgets import PageTitle, DataTable, MutedLabel
from ui.styles  import SUCCESS, DANGER, WARNING
from updater    import CURRENT_VERSION


class SettingsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._parsed_accounts:    list[dict] = []
        self._parsed_categories:  list[dict] = []
        self._parsed_txns:        list[dict] = []
        self._iif_bytes  = b""
        self._iif_name   = ""
        self._qb_xl_bytes: bytes = b""
        self._qb_xl_name:  str   = ""
        self._qb_xl_txns:  list[dict] = []
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
        lay.addWidget(self._build_qb_txn_section())
        lay.addWidget(self._build_database_section())
        lay.addWidget(self._build_reset_section())
        lay.addWidget(self._build_about_section())
        lay.addStretch()

        scroll.setWidget(inner)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    # ── QuickBooks migration ──────────────────────────────────────────────────

    def _build_qb_section(self) -> QGroupBox:
        grp = QGroupBox("QuickBooks Migration  (one-time import)")
        lay = QVBoxLayout(grp)
        lay.setSpacing(12)

        # Instructions
        info = QLabel(
            "<b>QuickBooks Migration — two steps, both done here:</b><br><br>"
            "<b>Step 1 — Accounts (IIF):</b><br>"
            "&nbsp;&nbsp;File → Utilities → Export → <b>Lists to IIF Files</b><br>"
            "&nbsp;&nbsp;Check <i>Chart of Accounts</i>. Gives you account names &amp; opening balances.<br><br>"
            "<b>Step 2 — Transactions (Excel):</b><br>"
            "&nbsp;&nbsp;Reports → Accountant &amp; Taxes → <b>Transaction Detail by Account</b><br>"
            "&nbsp;&nbsp;Set date range to <i>All</i>, then click <b>Excel → Create New Worksheet</b>.<br>"
            "&nbsp;&nbsp;Save the .xlsx and load it in the <i>Transactions Excel</i> section below.<br><br>"
            "<b>Why not .QBB?</b>  QuickBooks backup files are proprietary — "
            "no outside software can read them."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setStyleSheet(
            "background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px;"
            "padding:12px; font-size:12px;")
        lay.addWidget(info)

        # File picker row
        file_row = QHBoxLayout()
        self._iif_label = QLabel("No file selected")
        self._iif_label.setObjectName("Muted")
        file_row.addWidget(self._iif_label)
        file_row.addStretch()
        browse_btn = QPushButton("Browse for .iif file…")
        browse_btn.setObjectName("Secondary")
        browse_btn.clicked.connect(self._browse_iif)
        file_row.addWidget(browse_btn)

        parse_btn = QPushButton("Parse File")
        parse_btn.clicked.connect(self._parse_iif)
        file_row.addWidget(parse_btn)
        lay.addLayout(file_row)

        # Preview status
        self._qb_status = QLabel("")
        self._qb_status.setWordWrap(True)
        lay.addWidget(self._qb_status)

        # Preview table — accounts
        self._acct_preview_label = QLabel("Accounts found:")
        self._acct_preview_label.setStyleSheet("font-weight:bold;")
        self._acct_preview_label.hide()
        lay.addWidget(self._acct_preview_label)

        self._acct_preview = DataTable(["Name", "Type", "Status"])
        self._acct_preview.setColumnWidth(0, 260)
        self._acct_preview.setColumnWidth(1, 120)
        self._acct_preview.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self._acct_preview.setMaximumHeight(160)
        self._acct_preview.hide()
        lay.addWidget(self._acct_preview)

        # Preview table — transactions
        self._txn_preview_label = QLabel("Transactions found:")
        self._txn_preview_label.setStyleSheet("font-weight:bold;")
        self._txn_preview_label.hide()
        lay.addWidget(self._txn_preview_label)

        self._txn_preview = DataTable(["Date", "Account", "Payee / Memo", "Amount", "Status"])
        self._txn_preview.setColumnWidth(0, 100)
        self._txn_preview.setColumnWidth(1, 160)
        self._txn_preview.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self._txn_preview.setColumnWidth(3, 100)
        self._txn_preview.setColumnWidth(4, 90)
        self._txn_preview.setMaximumHeight(260)
        self._txn_preview.hide()
        lay.addWidget(self._txn_preview)

        # Import button
        self._import_btn = QPushButton("Import Everything into FinanceBook")
        self._import_btn.setObjectName("Success")
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._do_import)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._import_btn)
        lay.addLayout(btn_row)

        return grp

    def _browse_iif(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select QuickBooks IIF export", "",
            "IIF Files (*.iif *.IIF);;All Files (*)")
        if not path:
            return
        with open(path, "rb") as f:
            self._iif_bytes = f.read()
        self._iif_name = path.replace("\\", "/").split("/")[-1]
        self._iif_label.setText(self._iif_name)
        # Reset preview
        self._parsed_accounts = []
        self._parsed_txns = []
        self._import_btn.setEnabled(False)
        self._acct_preview.hide()
        self._acct_preview_label.hide()
        self._txn_preview.hide()
        self._txn_preview_label.hide()
        self._qb_status.setText("")

    def _parse_iif(self):
        if not self._iif_bytes:
            QMessageBox.warning(self, "No File", "Please select an IIF file first.")
            return

        try:
            from parsers.iif import parse_iif
            accounts, categories, txns, warnings = parse_iif(self._iif_bytes)
        except Exception as e:
            QMessageBox.critical(self, "Parse Error", f"Could not parse IIF file:\n{e}")
            return

        existing_hashes     = self.db.get_existing_hashes()
        existing_acct_names = {a["name"].lower() for a in self.db.get_accounts()}
        existing_cat_names  = {c["name"].lower() for c in self.db.get_categories()}

        for acct in accounts:
            acct["_exists"] = acct["name"].lower() in existing_acct_names
        for cat in categories:
            cat["_exists"] = cat["name"].lower() in existing_cat_names

        new_txns  = [t for t in txns if t.get("import_hash") not in existing_hashes]
        dup_count = len(txns) - len(new_txns)
        self.db.apply_rules(new_txns)

        self._parsed_accounts   = accounts
        self._parsed_categories = categories
        self._parsed_txns       = new_txns

        # ── Account preview ────────────────────────────────────────────────
        self._acct_preview.clear_rows()
        self._acct_preview.setRowCount(len(accounts))
        for row, acct in enumerate(accounts):
            exists = acct.get("_exists", False)
            self._acct_preview.set_item(row, 0, acct["name"], bold=True)
            self._acct_preview.set_item(row, 1, acct["type"].title())
            self._acct_preview.set_item(
                row, 2,
                "Already exists — will skip" if exists else "Will be created",
                color="#6b7280" if exists else SUCCESS)
            self._acct_preview.setRowHeight(row, 30)

        acct_label = f"Accounts (bank/credit card only):  {len(accounts)}"
        if categories:
            new_cats = sum(1 for c in categories if not c.get("_exists"))
            acct_label += f"     Categories to create:  {new_cats} income/expense"
        self._acct_preview_label.setText(acct_label)
        self._acct_preview_label.show()
        self._acct_preview.show()

        # ── Transaction preview ────────────────────────────────────────────
        preview_txns = new_txns[:200]
        self._txn_preview.clear_rows()
        self._txn_preview.setRowCount(len(preview_txns))
        for row, t in enumerate(preview_txns):
            self._txn_preview.set_item(row, 0, t.get("date", ""))
            self._txn_preview.set_item(row, 1, t.get("_account_name", ""))
            self._txn_preview.set_item(row, 2, t.get("payee") or t.get("memo", "—"))
            self._txn_preview.money_item(row, 3, float(t.get("amount") or 0))
            cat = t.get("category_id", "")
            self._txn_preview.set_item(
                row, 4,
                "Auto-cat" if cat else "Uncategorized",
                color=SUCCESS if cat else "#6b7280")
            self._txn_preview.setRowHeight(row, 28)

        label = f"Transactions found:  {len(new_txns)} new"
        if dup_count:
            label += f"  ({dup_count} duplicates will be skipped)"
        if len(new_txns) > 200:
            label += "  — showing first 200 in preview"
        self._txn_preview_label.setText(label)
        self._txn_preview_label.show()
        self._txn_preview.show()

        new_accts = sum(1 for a in accounts if not a.get("_exists"))
        new_cats  = sum(1 for c in categories if not c.get("_exists"))
        parts = []
        if new_accts: parts.append(f"{new_accts} account(s) to create")
        if new_cats:  parts.append(f"{new_cats} categor(ies) to create")
        if new_txns:  parts.append(f"{len(new_txns)} transactions to import")
        if dup_count: parts.append(f"{dup_count} duplicates skipped")
        self._qb_status.setText("  ·  ".join(parts) or "Nothing new to import")
        self._qb_status.setStyleSheet(f"color: {SUCCESS}; font-weight: bold;")

        if warnings:
            QMessageBox.warning(self, "Parse Warnings",
                "\n".join(warnings[:10]) +
                ("\n…and more" if len(warnings) > 10 else ""))

        self._import_btn.setEnabled(bool(new_accts or new_cats or new_txns))

    def _do_import(self):
        if not self._parsed_accounts and not self._parsed_categories and not self._parsed_txns:
            return

        new_accts = sum(1 for a in self._parsed_accounts if not a.get("_exists"))
        new_cats  = sum(1 for c in self._parsed_categories if not c.get("_exists"))
        reply = QMessageBox.question(
            self, "Confirm Import",
            f"This will:\n"
            f"• Create {new_accts} account(s)\n"
            f"  — Bank/credit-card accounts start at $0 (transactions set the balance)\n"
            f"  — Loans/assets/liabilities use the QB balance directly\n"
            f"• Create {new_cats} income/expense categor(ies)\n"
            f"• Import {len(self._parsed_txns)} transaction(s)\n\n"
            "QB income/expense accounts become categories, not accounts. "
            "Duplicates are skipped. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            # 1. Create/find accounts (BANK/CCARD only)
            name_to_acct_id: dict[str, str] = {}
            for existing in self.db.get_accounts():
                name_to_acct_id[existing["name"].lower()] = existing["id"]
            for acct in self._parsed_accounts:
                if not acct.get("_exists"):
                    # Bank/credit-card accounts get opening_balance=0 because
                    # their transactions will be imported from the Excel file —
                    # the IIF OBAMOUNT is the *current* balance after all those
                    # transactions, so using it as opening_balance would
                    # double-count every transaction.
                    # All other account types (loans, assets, liabilities) keep
                    # the OBAMOUNT because no individual transactions are imported
                    # for them — it's their only balance source.
                    is_bank = acct["type"] in ("checking", "savings", "credit card")
                    ob = "0" if is_bank else acct.get("opening_balance", "0")
                    saved = self.db.save_account({
                        "name":            acct["name"],
                        "type":            acct["type"],
                        "institution":     "",
                        "opening_balance": ob,
                        "currency":        "USD",
                    })
                    name_to_acct_id[acct["name"].lower()] = saved["id"]

            # 2. Create categories (INC/EXP) — parents first, then children
            name_to_cat_id: dict[str, str] = {}
            for existing in self.db.get_categories():
                name_to_cat_id[existing["name"].lower()] = existing["id"]
            for cat in self._parsed_categories:
                if cat.get("_exists"):
                    name_to_cat_id[cat["name"].lower()] = name_to_cat_id.get(cat["name"].lower(), "")
                    continue
                parent_id = ""
                if cat["parent_name"]:
                    # Look up the parent's short name (last segment of its QB path)
                    parent_short = cat["parent_name"].split(":")[-1].strip().lower()
                    parent_id    = name_to_cat_id.get(parent_short, "")
                saved_cat = self.db.save_category({
                    "name":      cat["name"],
                    "type":      cat["type"],
                    "parent_id": parent_id,
                })
                name_to_cat_id[cat["name"].lower()] = saved_cat["id"]

            # 3. Resolve and save transactions (only those in real accounts)
            skipped = 0
            ready   = []
            for t in self._parsed_txns:
                t = dict(t)
                acct_name = t.pop("_account_name", "")
                acct_id   = name_to_acct_id.get(acct_name.lower(), "")
                if not acct_id:
                    skipped += 1
                    continue
                t["account_id"] = acct_id
                ready.append(t)

            saved_txns = self.db.bulk_save_transactions(ready)

            cats_created = sum(1 for c in self._parsed_categories if not c.get("_exists"))
            msg = (f"Import complete!\n\n"
                   f"• {new_accts} account(s) created\n"
                   f"• {cats_created} categor(ies) created\n"
                   f"• {len(saved_txns)} transaction(s) imported")
            if skipped:
                msg += f"\n• {skipped} transaction(s) skipped (not a bank/credit-card account)"
            QMessageBox.information(self, "Import Complete", msg)

            self._parsed_accounts   = []
            self._parsed_categories = []
            self._parsed_txns       = []
            self._import_btn.setEnabled(False)
            self._qb_status.setText("✓ Import complete.")
            self._acct_preview.hide();  self._acct_preview_label.hide()
            self._txn_preview.hide();   self._txn_preview_label.hide()

        except Exception as e:
            QMessageBox.critical(self, "Import Failed", str(e))

    # ── QB Transactions Excel section ─────────────────────────────────────────

    def _build_qb_txn_section(self) -> QGroupBox:
        grp = QGroupBox("QuickBooks Transactions  (Transaction Detail by Account Excel)")
        lay = QVBoxLayout(grp)
        lay.setSpacing(12)

        note = QLabel(
            "<b>How this works:</b> QuickBooks records every transaction in two accounts "
            "(double-entry), so the report lists each transaction twice — once under your "
            "bank account and once under the income/expense account. "
            "After parsing, <b>check only your real bank accounts</b> (e.g. AMRR, BofA). "
            "The income/expense accounts (Rental Income, Interest Expense, etc.) become "
            "<i>categories</i> automatically from the Split column — do not check those."
        )
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setStyleSheet(
            "background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px;"
            "padding:10px; font-size:12px;")
        lay.addWidget(note)

        # File picker
        file_row = QHBoxLayout()
        self._xl_label = QLabel("No file selected")
        self._xl_label.setObjectName("Muted")
        file_row.addWidget(self._xl_label)
        file_row.addStretch()
        browse_btn = QPushButton("Browse for .xlsx…")
        browse_btn.setObjectName("Secondary")
        browse_btn.clicked.connect(self._browse_qb_xl)
        file_row.addWidget(browse_btn)
        parse_btn = QPushButton("Parse File")
        parse_btn.clicked.connect(self._parse_qb_xl)
        file_row.addWidget(parse_btn)
        lay.addLayout(file_row)

        # Status
        self._xl_status = QLabel("")
        self._xl_status.setWordWrap(True)
        lay.addWidget(self._xl_status)

        # Account selector (shown after parse)
        self._xl_acct_label = QLabel("Select your real bank accounts to import:")
        self._xl_acct_label.setStyleSheet("font-weight:bold;")
        self._xl_acct_label.hide()
        lay.addWidget(self._xl_acct_label)

        self._xl_acct_list = QListWidget()
        self._xl_acct_list.setMaximumHeight(140)
        self._xl_acct_list.hide()
        lay.addWidget(self._xl_acct_list)

        # Preview table
        self._xl_preview_label = QLabel("")
        self._xl_preview_label.setStyleSheet("font-weight:bold;")
        self._xl_preview_label.hide()
        lay.addWidget(self._xl_preview_label)

        self._xl_preview = DataTable(["Date", "Account", "Payee / Memo", "Category (Split)", "Amount"])
        self._xl_preview.setColumnWidth(0, 100)
        self._xl_preview.setColumnWidth(1, 140)
        self._xl_preview.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._xl_preview.setColumnWidth(3, 160)
        self._xl_preview.setColumnWidth(4, 100)
        self._xl_preview.setMaximumHeight(280)
        self._xl_preview.hide()
        lay.addWidget(self._xl_preview)

        self._xl_import_btn = QPushButton("Import Selected Accounts into FinanceBook")
        self._xl_import_btn.setObjectName("Success")
        self._xl_import_btn.setEnabled(False)
        self._xl_import_btn.clicked.connect(self._do_import_qb_xl)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._xl_import_btn)
        lay.addLayout(btn_row)

        return grp

    def _browse_qb_xl(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select QuickBooks Transaction Detail Excel", "",
            "Excel Files (*.xlsx *.xls);;All Files (*)")
        if not path:
            return
        with open(path, "rb") as f:
            self._qb_xl_bytes = f.read()
        self._qb_xl_name = path.replace("\\", "/").split("/")[-1]
        self._xl_label.setText(self._qb_xl_name)
        self._qb_xl_txns = []
        self._xl_import_btn.setEnabled(False)
        self._xl_preview.hide()
        self._xl_preview_label.hide()
        self._xl_acct_list.hide()
        self._xl_acct_label.hide()
        self._xl_status.setText("")

    def _parse_qb_xl(self):
        if not self._qb_xl_bytes:
            QMessageBox.warning(self, "No File", "Please select an Excel file first.")
            return

        try:
            from parsers.spreadsheet import parse_qb_multi_account_excel
            txns, warnings = parse_qb_multi_account_excel(self._qb_xl_bytes)
        except Exception as e:
            QMessageBox.critical(self, "Parse Error", f"Could not parse file:\n{e}")
            return

        if not txns:
            self._xl_status.setText(
                "No transactions found. Make sure you exported "
                "'Transaction Detail by Account' and saved as Excel (.xlsx).")
            self._xl_status.setStyleSheet(f"color: {DANGER};")
            if warnings:
                QMessageBox.warning(self, "Parse Warnings", "\n".join(warnings[:10]))
            return

        existing_hashes = self.db.get_existing_hashes()
        new_txns = [t for t in txns if t.get("import_hash") not in existing_hashes]
        dup_count = len(txns) - len(new_txns)
        self._qb_xl_txns = new_txns

        # ── Build per-account counts ──────────────────────────────────────────
        acct_counts: dict[str, int] = {}
        for t in new_txns:
            name = t.get("_account_name", "")
            acct_counts[name] = acct_counts.get(name, 0) + 1

        # Existing FinanceBook account names (for pre-checking)
        existing_acct_names = {a["name"].lower() for a in self.db.get_accounts()}

        # ── Populate checkbox list ────────────────────────────────────────────
        self._xl_acct_list.clear()
        for name in sorted(acct_counts):
            item = QListWidgetItem(
                f"{name}  ({acct_counts[name]} transactions)")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # Pre-check only accounts already in FinanceBook
            checked = name.lower() in existing_acct_names
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self._xl_acct_list.addItem(item)

        self._xl_acct_label.show()
        self._xl_acct_list.show()

        # ── Preview — show all transactions, Split as category column ─────────
        self._xl_preview.clear_rows()
        preview = new_txns[:300]
        self._xl_preview.setRowCount(len(preview))
        for row, t in enumerate(preview):
            split = t.get("_split", "") or "—"
            self._xl_preview.set_item(row, 0, t.get("date", ""))
            self._xl_preview.set_item(row, 1, t.get("_account_name", ""))
            self._xl_preview.set_item(row, 2, t.get("payee") or t.get("memo", "—"))
            self._xl_preview.set_item(row, 3, split,
                color=SUCCESS if split != "—" else "#6b7280")
            self._xl_preview.money_item(row, 4, float(t.get("amount") or 0))
            self._xl_preview.setRowHeight(row, 28)

        label = f"All transactions parsed:  {len(new_txns)} new"
        if dup_count:
            label += f"  ({dup_count} duplicates will be skipped)"
        if len(new_txns) > 300:
            label += "  — showing first 300 in preview"
        self._xl_preview_label.setText(label)
        self._xl_preview_label.show()
        self._xl_preview.show()

        self._xl_status.setText(
            f"Check the bank accounts above you want to import. "
            f"The Split column becomes the category for each transaction.")
        self._xl_status.setStyleSheet("color: #1a1a2e; font-style: italic;")
        self._xl_import_btn.setEnabled(bool(new_txns))

        if warnings:
            QMessageBox.warning(self, "Parse Warnings",
                "\n".join(warnings[:10]) +
                ("\n…and more" if len(warnings) > 10 else ""))

    def _do_import_qb_xl(self):
        if not self._qb_xl_txns:
            return

        # Which accounts are checked?
        selected_accounts: set[str] = set()
        for i in range(self._xl_acct_list.count()):
            item = self._xl_acct_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected_accounts.add(item.data(Qt.ItemDataRole.UserRole))

        if not selected_accounts:
            QMessageBox.warning(self, "Nothing Selected",
                "Please check at least one bank account to import.")
            return

        to_import = [t for t in self._qb_xl_txns
                     if t.get("_account_name") in selected_accounts]

        reply = QMessageBox.question(
            self, "Confirm Import",
            f"Import {len(to_import)} transaction(s) from "
            f"{len(selected_accounts)} account(s)?\n\n"
            "• Split column → category (created automatically if needed).\n"
            "• Rules auto-created for any payee that always goes to the same category.\n"
            "• Duplicates are skipped.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            # ── Build lookup maps ─────────────────────────────────────────────
            name_to_acct_id: dict[str, str] = {
                a["name"].lower(): a["id"] for a in self.db.get_accounts()
            }
            name_to_cat_id: dict[str, str] = {
                c["name"].lower(): c["id"] for c in self.db.get_categories()
            }

            # Collect ALL split values that refer to accounts (existing or being
            # imported). We do this up-front so AMRR can recognise "BofA" as an
            # account even before BofA's own transactions are processed.
            all_account_names_lower: set[str] = set(name_to_acct_id.keys())
            for t in to_import:
                n = t.get("_account_name", "")
                if n:
                    all_account_names_lower.add(n.lower())

            # ── Pre-scan: determine income vs expense for each Split name ──────
            split_amounts: dict[str, list[float]] = {}
            for t in to_import:
                s = t.get("_split", "")
                if s and s != "-SPLIT-" and s.lower() not in all_account_names_lower:
                    try:
                        split_amounts.setdefault(s, []).append(
                            float(t.get("amount", 0) or 0))
                    except (ValueError, TypeError):
                        pass

            def _infer_cat_type(split_name: str) -> str:
                amounts = split_amounts.get(split_name, [])
                if not amounts:
                    return "expense"
                pos = sum(1 for a in amounts if a > 0)
                return "income" if pos > len(amounts) / 2 else "expense"

            accts_created = cats_created = skipped = 0
            ready: list[dict] = []

            for t in to_import:
                t = dict(t)
                acct_name  = t.pop("_account_name", "")
                split_name = t.pop("_split", "")

                # Resolve account — create on the fly if not yet in DB
                acct_id = name_to_acct_id.get(acct_name.lower(), "")
                if not acct_id:
                    saved = self.db.save_account({
                        "name": acct_name, "type": "other",
                        "institution": "", "opening_balance": "0", "currency": "USD",
                    })
                    acct_id = saved["id"]
                    name_to_acct_id[acct_name.lower()] = acct_id
                    all_account_names_lower.add(acct_name.lower())
                    accts_created += 1

                # ── Transfer detection ────────────────────────────────────────
                # If the Split points to another account, this is a transfer —
                # mark it and don't assign a category.
                if split_name and split_name.lower() in all_account_names_lower:
                    t["is_transfer"]      = "1"
                    t["category_id"]      = ""
                    t["transfer_pair_id"] = ""

                # ── Category resolution ───────────────────────────────────────
                elif split_name and split_name != "-SPLIT-" \
                        and not t.get("category_id", ""):
                    cat_id = name_to_cat_id.get(split_name.lower(), "")
                    if not cat_id:
                        saved_cat = self.db.save_category({
                            "name":      split_name,
                            "type":      _infer_cat_type(split_name),
                            "parent_id": "",
                        })
                        cat_id = saved_cat["id"]
                        name_to_cat_id[split_name.lower()] = cat_id
                        cats_created += 1
                    t["category_id"] = cat_id

                t["account_id"] = acct_id
                ready.append(t)

            saved_txns = self.db.bulk_save_transactions(ready)

            # ── Pair transfers ────────────────────────────────────────────────
            # After saving we have real IDs. Pair two transfer transactions that
            # share the same date and absolute amount across different accounts.
            from collections import defaultdict
            transfer_groups: dict = defaultdict(list)
            for txn in saved_txns:
                if str(txn.get("is_transfer", "0")) == "1":
                    try:
                        key = (txn["date"],
                               round(abs(float(txn.get("amount", 0) or 0)), 2))
                        transfer_groups[key].append(txn)
                    except (ValueError, TypeError):
                        pass

            transfers_linked = 0
            for group in transfer_groups.values():
                if len(group) == 2:
                    a, b = group
                    if a.get("account_id") != b.get("account_id"):
                        self.db.link_transfer(a["id"], b["id"])
                        transfers_linked += 1

            # ── Auto-create rules from consistent payee → category patterns ───
            # Generic QB memo strings that shouldn't become rule payees
            _SKIP_PAYEES = {
                "deposit", "check", "transfer", "payment", "nsf",
                "funds transfer", "opening balance", "",
            }
            existing_rule_values = {
                r.get("value", "").lower() for r in self.db.get_rules()
            }
            # payee → set of category_ids it was assigned to
            payee_cats: dict[str, set] = {}
            for t in ready:
                payee  = (t.get("payee") or "").strip()
                cat_id = t.get("category_id", "")
                if payee and cat_id and payee.lower() not in _SKIP_PAYEES:
                    payee_cats.setdefault(payee, set()).add(cat_id)

            cat_id_to_name = {v: k for k, v in name_to_cat_id.items()}
            rules_created = 0
            for payee, cat_ids in payee_cats.items():
                if len(cat_ids) == 1 and payee.lower() not in existing_rule_values:
                    cat_id   = next(iter(cat_ids))
                    cat_name = cat_id_to_name.get(cat_id, cat_id).title()
                    self.db.save_rule({
                        "name":        f"{payee}",
                        "field":       "payee",
                        "operator":    "contains",
                        "value":       payee,
                        "value2":      "",
                        "category_id": cat_id,
                        "class_id":    "",
                        "priority":    50,
                        "active":      "1",
                    })
                    rules_created += 1

            msg = f"Import complete!\n\n• {len(saved_txns)} transaction(s) imported"
            if accts_created:
                msg += f"\n• {accts_created} account(s) created"
            if cats_created:
                msg += f"\n• {cats_created} categor(ies) created from QB Split values"
            if transfers_linked:
                msg += f"\n• {transfers_linked} transfer pair(s) detected and linked (excluded from P&L)"
            if rules_created:
                msg += f"\n• {rules_created} auto-categorization rule(s) created from payee patterns"
            if skipped:
                msg += f"\n• {skipped} skipped (no account name)"
            QMessageBox.information(self, "Import Complete", msg)

            self._qb_xl_txns = []
            self._xl_import_btn.setEnabled(False)
            self._xl_status.setText("✓ Import complete.")
            self._xl_preview.hide()
            self._xl_preview_label.hide()
            self._xl_acct_list.hide()
            self._xl_acct_label.hide()

        except Exception as e:
            QMessageBox.critical(self, "Import Failed", str(e))

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
            "<b>This deletes all accounts, transactions, categories, and rules "
            "in this database.</b> Use this when you want to start over with a "
            "clean import. The database file and its name are kept — only the "
            "data inside is cleared. This cannot be undone."
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
            f"This will permanently delete ALL accounts, transactions, categories, "
            f"rules, and reconciliations in \"{db_name}\".\n\n"
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
                f"\"{db_name}\" has been cleared.\n\n"
                "You can now re-import your QuickBooks data:\n"
                "1. Settings → IIF file (accounts + categories)\n"
                "2. Settings → Excel file (transactions + rules)\n\n"
                "Restart the app or navigate away and back to refresh all pages.")
        except Exception as e:
            QMessageBox.critical(self, "Reset Failed", str(e))

    # ── About section ─────────────────────────────────────────────────────────

    def _build_about_section(self) -> QGroupBox:
        grp = QGroupBox("About")
        lay = QVBoxLayout(grp)
        lay.addWidget(QLabel(f"<b>FinanceBook</b>  v{CURRENT_VERSION}"))
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
