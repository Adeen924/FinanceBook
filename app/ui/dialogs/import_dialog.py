"""
QuickBooks import wizard (popup).

Two tabs, mirroring the two QuickBooks exports:
  • Step 1 — Accounts (IIF): chart of accounts → FinanceBook accounts + categories
  • Step 2 — Transactions (Excel): Transaction Detail by Account → transactions

Lifted out of the Settings page so the settings screen stays uncluttered; the
logic is unchanged except category import now skips anything that already exists
(no duplicates).
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QWidget, QFileDialog, QMessageBox, QHeaderView, QScrollArea, QFrame,
    QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt
from ui.widgets import DataTable
from ui.styles  import SUCCESS, DANGER


class ImportDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._parsed_accounts:   list[dict] = []
        self._parsed_categories: list[dict] = []
        self._iif_bytes  = b""
        self._iif_name   = ""
        self._qb_xl_bytes: bytes = b""
        self._qb_xl_name:  str   = ""
        self._qb_xl_txns:  list[dict] = []
        self.setWindowTitle("Import from QuickBooks")
        self.setMinimumSize(780, 640)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        tabs = QTabWidget()
        tabs.addTab(self._scroll(self._build_iif_tab()), "Step 1 · Accounts (IIF)")
        tabs.addTab(self._scroll(self._build_qb_txn_tab()), "Step 2 · Transactions (Excel)")
        lay.addWidget(tabs)

        row = QHBoxLayout()
        row.addStretch()
        done_btn = QPushButton("Done")
        done_btn.clicked.connect(self.accept)
        row.addWidget(done_btn)
        lay.addLayout(row)

    def _scroll(self, inner: QWidget) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(inner)
        return sc

    # ── Step 1: IIF accounts + categories ─────────────────────────────────────

    def _build_iif_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 8, 4, 8)
        lay.setSpacing(12)

        info = QLabel(
            "<b>Step 1 — Accounts (IIF)</b><br><br>"
            "In QuickBooks: File → Utilities → Export → <b>Lists to IIF Files</b>, "
            "check <i>Chart of Accounts</i>. This brings in your account names and "
            "your income/expense <b>categories</b>.<br><br>"
            "Transactions come from Step 2 (the Excel export)."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setStyleSheet(
            "background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px;"
            "padding:12px; font-size:12px;")
        lay.addWidget(info)

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

        self._qb_status = QLabel("")
        self._qb_status.setWordWrap(True)
        lay.addWidget(self._qb_status)

        self._acct_preview_label = QLabel("Accounts found:")
        self._acct_preview_label.setStyleSheet("font-weight:bold;")
        self._acct_preview_label.hide()
        lay.addWidget(self._acct_preview_label)

        self._acct_preview = DataTable(["Name", "Type", "Status"])
        self._acct_preview.setColumnWidth(0, 260)
        self._acct_preview.setColumnWidth(1, 120)
        self._acct_preview.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self._acct_preview.setMaximumHeight(200)
        self._acct_preview.hide()
        lay.addWidget(self._acct_preview)

        self._import_btn = QPushButton("Import Accounts & Categories into FinanceBook")
        self._import_btn.setObjectName("Success")
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._do_import)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._import_btn)
        lay.addLayout(btn_row)
        lay.addStretch()
        return page

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
        self._parsed_accounts = []
        self._parsed_categories = []
        self._import_btn.setEnabled(False)
        self._acct_preview.hide()
        self._acct_preview_label.hide()
        self._qb_status.setText("")

    def _parse_iif(self):
        if not self._iif_bytes:
            QMessageBox.warning(self, "No File", "Please select an IIF file first.")
            return

        try:
            from parsers.iif import parse_iif
            accounts, categories, warnings = parse_iif(self._iif_bytes)
        except Exception as e:
            QMessageBox.critical(self, "Parse Error", f"Could not parse IIF file:\n{e}")
            return

        existing_acct_names = {a["name"].lower() for a in self.db.get_accounts()}
        existing_cat_names  = {c["name"].lower() for c in self.db.get_categories()}

        for acct in accounts:
            acct["_exists"] = acct["name"].lower() in existing_acct_names
        for cat in categories:
            cat["_exists"] = cat["name"].lower() in existing_cat_names

        self._parsed_accounts   = accounts
        self._parsed_categories = categories

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
            acct_label += f"     New categories:  {new_cats}  (existing ones are skipped)"
        self._acct_preview_label.setText(acct_label)
        self._acct_preview_label.show()
        self._acct_preview.show()

        new_accts = sum(1 for a in accounts if not a.get("_exists"))
        new_cats  = sum(1 for c in categories if not c.get("_exists"))
        parts = []
        if new_accts: parts.append(f"{new_accts} account(s) to create")
        if new_cats:  parts.append(f"{new_cats} new categor(ies)")
        self._qb_status.setText("  ·  ".join(parts) or "Nothing new to import")
        self._qb_status.setStyleSheet(f"color: {SUCCESS}; font-weight: bold;")

        if warnings:
            QMessageBox.warning(self, "Parse Warnings",
                "\n".join(warnings[:10]) +
                ("\n…and more" if len(warnings) > 10 else ""))

        self._import_btn.setEnabled(bool(new_accts or new_cats))

    def _do_import(self):
        if not self._parsed_accounts and not self._parsed_categories:
            return

        new_accts = sum(1 for a in self._parsed_accounts if not a.get("_exists"))
        new_cats  = sum(1 for c in self._parsed_categories if not c.get("_exists"))
        reply = QMessageBox.question(
            self, "Confirm Import",
            f"This will:\n"
            f"• Create {new_accts} account(s)\n"
            f"  — Bank/credit-card accounts start at $0 (transactions set the balance)\n"
            f"  — Loans/assets/liabilities use the QB balance directly\n"
            f"• Create up to {new_cats} new categor(ies)\n\n"
            "Categories that already exist are skipped (no duplicates). "
            "Transactions are imported separately in Step 2. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            # 1. Create accounts (BANK/CCARD only)
            existing_acct_names = {a["name"].lower() for a in self.db.get_accounts()}
            for acct in self._parsed_accounts:
                if acct["name"].lower() in existing_acct_names:
                    continue
                # Bank/credit-card accounts get opening_balance=0 because their
                # transactions come from the Excel file — the IIF OBAMOUNT is the
                # *current* balance after those, so using it as opening_balance
                # would double-count. Other types keep OBAMOUNT (their only source).
                is_bank = acct["type"] in ("checking", "savings", "credit card")
                ob = "0" if is_bank else acct.get("opening_balance", "0")
                self.db.save_account({
                    "name":            acct["name"],
                    "type":            acct["type"],
                    "institution":     "",
                    "opening_balance": ob,
                    "currency":        "USD",
                })
                existing_acct_names.add(acct["name"].lower())

            # 2. Create categories (INC/EXP), parents first — skipping any that
            #    already exist (by name). The map holds both pre-existing
            #    categories AND ones created during this import, so a name that
            #    appears twice in the file is only ever created once.
            name_to_cat_id: dict[str, str] = {
                c["name"].lower(): c["id"] for c in self.db.get_categories()
            }
            cats_created = 0
            for cat in self._parsed_categories:
                key = cat["name"].lower()
                if key in name_to_cat_id:
                    continue  # already exists — do not add a duplicate
                parent_id = ""
                if cat["parent_name"]:
                    parent_short = cat["parent_name"].split(":")[-1].strip().lower()
                    parent_id    = name_to_cat_id.get(parent_short, "")
                saved_cat = self.db.save_category({
                    "name":      cat["name"],
                    "type":      cat["type"],
                    "parent_id": parent_id,
                })
                name_to_cat_id[key] = saved_cat["id"]
                cats_created += 1

            QMessageBox.information(
                self, "Import Complete",
                f"Import complete!\n\n"
                f"• {new_accts} account(s) created\n"
                f"• {cats_created} new categor(ies) created "
                f"(existing ones skipped)")

            self._parsed_accounts   = []
            self._parsed_categories = []
            self._import_btn.setEnabled(False)
            self._qb_status.setText("✓ Import complete.")
            self._acct_preview.hide();  self._acct_preview_label.hide()

        except Exception as e:
            QMessageBox.critical(self, "Import Failed", str(e))

    # ── Step 2: Excel transactions ────────────────────────────────────────────

    def _build_qb_txn_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 8, 4, 8)
        lay.setSpacing(12)

        note = QLabel(
            "<b>Step 2 — Transactions (Excel)</b><br><br>"
            "In QuickBooks: Reports → Accountant &amp; Taxes → "
            "<b>Transaction Detail by Account</b>, set the date range to <i>All</i>, "
            "then <b>Excel → Create New Worksheet</b> and save the .xlsx.<br><br>"
            "QuickBooks lists each transaction twice (double-entry). After parsing, "
            "<b>check only your real bank accounts</b>. The income/expense accounts "
            "become <i>categories</i> automatically from the Split column."
        )
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setStyleSheet(
            "background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px;"
            "padding:12px; font-size:12px;")
        lay.addWidget(note)

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

        self._xl_status = QLabel("")
        self._xl_status.setWordWrap(True)
        lay.addWidget(self._xl_status)

        self._xl_acct_label = QLabel("Select your real bank accounts to import:")
        self._xl_acct_label.setStyleSheet("font-weight:bold;")
        self._xl_acct_label.hide()
        lay.addWidget(self._xl_acct_label)

        self._xl_acct_list = QListWidget()
        self._xl_acct_list.setMaximumHeight(140)
        self._xl_acct_list.hide()
        lay.addWidget(self._xl_acct_list)

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
        self._xl_preview.setMaximumHeight(300)
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
        lay.addStretch()
        return page

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

        acct_counts: dict[str, int] = {}
        for t in new_txns:
            name = t.get("_account_name", "")
            acct_counts[name] = acct_counts.get(name, 0) + 1

        existing_acct_names = {a["name"].lower() for a in self.db.get_accounts()}

        self._xl_acct_list.clear()
        for name in sorted(acct_counts):
            item = QListWidgetItem(f"{name}  ({acct_counts[name]} transactions)")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = name.lower() in existing_acct_names
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self._xl_acct_list.addItem(item)

        self._xl_acct_label.show()
        self._xl_acct_list.show()

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
            "Check the bank accounts above you want to import. "
            "The Split column becomes the category for each transaction.")
        self._xl_status.setStyleSheet("color: #1a1a2e; font-style: italic;")
        self._xl_import_btn.setEnabled(bool(new_txns))

        if warnings:
            QMessageBox.warning(self, "Parse Warnings",
                "\n".join(warnings[:10]) +
                ("\n…and more" if len(warnings) > 10 else ""))

    def _do_import_qb_xl(self):
        if not self._qb_xl_txns:
            return

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
            name_to_acct_id: dict[str, str] = {
                a["name"].lower(): a["id"] for a in self.db.get_accounts()
            }
            name_to_cat_id: dict[str, str] = {
                c["name"].lower(): c["id"] for c in self.db.get_categories()
            }

            all_account_names_lower: set[str] = set(name_to_acct_id.keys())
            for t in to_import:
                n = t.get("_account_name", "")
                if n:
                    all_account_names_lower.add(n.lower())

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

                if split_name and split_name.lower() in all_account_names_lower:
                    t["is_transfer"]      = "1"
                    t["category_id"]      = ""
                    t["transfer_pair_id"] = ""

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

            _SKIP_PAYEES = {
                "deposit", "check", "transfer", "payment", "nsf",
                "funds transfer", "opening balance", "",
            }
            existing_rule_values = {
                r.get("value", "").lower() for r in self.db.get_rules()
            }
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
