"""Import transactions from OFX / QFX / QBO / CSV / XLSX."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QComboBox, QLabel, QFileDialog, QMessageBox,
                              QGroupBox, QFormLayout, QLineEdit,
                              QHeaderView, QTableWidgetItem)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from ui.widgets import PageTitle, DataTable, SecondaryButton
from ui.styles  import SUCCESS, DANGER, WARNING


class ImportPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._parsed: list[dict] = []
        self._file_bytes = b""
        self._filename   = ""
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        lay.addWidget(PageTitle("Import Transactions"))

        # ── Step 1: Choose file + account ──
        grp1 = QGroupBox("Step 1 — Select File & Account")
        grp1_lay = QFormLayout(grp1)
        grp1_lay.setSpacing(10)

        self._acct_combo = QComboBox()
        self._acct_combo.setMinimumWidth(220)
        grp1_lay.addRow("Import into account:", self._acct_combo)

        file_row = QHBoxLayout()
        self._file_label = QLabel("No file selected")
        self._file_label.setObjectName("Muted")
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("Secondary")
        browse_btn.clicked.connect(self._browse)
        file_row.addWidget(self._file_label)
        file_row.addWidget(browse_btn)
        grp1_lay.addRow("File:", file_row)
        lay.addWidget(grp1)

        # ── Step 2: Column mapping (CSV/XLSX only) ──
        self._col_grp = QGroupBox(
            "Step 2 — Column Mapping  (CSV/XLSX only, leave blank to auto-detect)")
        col_lay = QFormLayout(self._col_grp)
        col_lay.setSpacing(8)
        self._col_date   = QLineEdit(); self._col_date.setPlaceholderText("e.g. Date")
        self._col_payee  = QLineEdit(); self._col_payee.setPlaceholderText("e.g. Description")
        self._col_amount = QLineEdit(); self._col_amount.setPlaceholderText(
            "e.g. Amount  (or leave blank and use Debit/Credit below)")
        self._col_debit  = QLineEdit(); self._col_debit.setPlaceholderText("e.g. Debit / Withdrawal")
        self._col_credit = QLineEdit(); self._col_credit.setPlaceholderText("e.g. Credit / Deposit")
        col_lay.addRow("Date column:",   self._col_date)
        col_lay.addRow("Payee column:",  self._col_payee)
        col_lay.addRow("Amount column:", self._col_amount)
        col_lay.addRow("Debit column:",  self._col_debit)
        col_lay.addRow("Credit column:", self._col_credit)
        self._col_grp.setEnabled(False)
        lay.addWidget(self._col_grp)

        # ── Step 3: Preview & Import ──
        step3_hdr = QHBoxLayout()
        self._preview_label = QLabel("Step 3 — Preview & Import")
        self._preview_label.setStyleSheet("font-weight:bold; font-size:14px;")
        step3_hdr.addWidget(self._preview_label)
        step3_hdr.addStretch()

        parse_btn = QPushButton("Parse File")
        parse_btn.clicked.connect(self._parse)
        step3_hdr.addWidget(parse_btn)

        self._import_btn = QPushButton("Import All")
        self._import_btn.setObjectName("Success")
        self._import_btn.clicked.connect(self._import)
        self._import_btn.setEnabled(False)
        step3_hdr.addWidget(self._import_btn)
        lay.addLayout(step3_hdr)

        self._status_label = QLabel("")
        lay.addWidget(self._status_label)

        self._table = DataTable(
            ["Date", "Payee / Memo", "Amount", "Auto Category", "Duplicate?"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 110)
        self._table.setColumnWidth(2, 110)
        self._table.setColumnWidth(3, 160)
        self._table.setColumnWidth(4, 100)
        lay.addWidget(self._table)

    def refresh(self):
        accounts = self.db.get_accounts()
        self._acct_combo.clear()
        self._acct_combo.addItem("— Select account —", "")
        for a in accounts:
            self._acct_combo.addItem(a["name"], a["id"])
        self._parsed = []
        self._table.clear_rows()
        self._import_btn.setEnabled(False)
        self._status_label.setText("")

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select bank export file", "",
            "Bank Files (*.ofx *.qfx *.qbo *.csv *.xlsx *.xls);;All Files (*)")
        if not path:
            return
        with open(path, "rb") as f:
            self._file_bytes = f.read()
        self._filename = path.split("/")[-1].split("\\")[-1]
        self._file_label.setText(self._filename)
        ext = self._filename.rsplit(".", 1)[-1].lower()
        self._col_grp.setEnabled(ext in ("csv", "xlsx", "xls"))

    def _parse(self):
        acct_id = self._acct_combo.currentData()
        if not acct_id:
            QMessageBox.warning(self, "No Account",
                "Please select an account to import into.")
            return
        if not self._file_bytes:
            QMessageBox.warning(self, "No File",
                "Please select a file to import.")
            return

        ext      = self._filename.rsplit(".", 1)[-1].lower()
        warnings = []
        try:
            if ext in ("ofx", "qfx", "qbo"):
                from parsers.qfx import parse_ofx
                from io import BytesIO
                parsed = parse_ofx(BytesIO(self._file_bytes), acct_id)
            else:
                from parsers.spreadsheet import parse_spreadsheet
                col_map = {
                    "date":   self._col_date.text().strip()   or None,
                    "payee":  self._col_payee.text().strip()  or None,
                    "amount": self._col_amount.text().strip() or None,
                    "debit":  self._col_debit.text().strip()  or None,
                    "credit": self._col_credit.text().strip() or None,
                }
                col_map = {k: v for k, v in col_map.items() if v}
                parsed, warnings = parse_spreadsheet(
                    self._file_bytes, self._filename, acct_id,
                    col_map if col_map else None)
        except Exception as e:
            QMessageBox.critical(self, "Parse Error",
                f"Could not parse file:\n{e}")
            return

        existing_hashes = self.db.get_existing_hashes()
        dups = [t for t in parsed if t.get("import_hash") in existing_hashes]
        new  = [t for t in parsed if t.get("import_hash") not in existing_hashes]

        # Auto-apply rules to new transactions
        self.db.apply_rules(new)

        self._parsed = new
        self._render_preview(new, dups)

        matched   = [t for t in new if t.get("category_id")]
        unmatched = [t for t in new if not t.get("category_id")]

        status_parts = []
        if matched:
            status_parts.append(f"{len(matched)} auto-categorized")
        if unmatched:
            status_parts.append(f"{len(unmatched)} need a category")
        if dups:
            status_parts.append(f"{len(dups)} duplicates skipped")
        if warnings:
            status_parts.append(f"{len(warnings)} parse warnings")
        self._status_label.setText("   |   ".join(status_parts) or "No transactions found")
        self._import_btn.setEnabled(bool(new))

        if warnings:
            QMessageBox.warning(self, "Parse Warnings",
                "\n".join(warnings[:10]) +
                ("\n…and more" if len(warnings) > 10 else ""))

    def _render_preview(self, new_txns, dup_txns):
        cats = {c["id"]: c["name"] for c in self.db.get_categories()}

        def by_date(t):
            return t.get("date", "")

        matched   = sorted([t for t in new_txns if t.get("category_id")],  key=by_date)
        unmatched = sorted([t for t in new_txns if not t.get("category_id")], key=by_date)
        dups      = sorted(dup_txns, key=by_date)

        # Build sections: (header_text, transactions, is_dup)
        sections = []
        if matched:
            sections.append((
                f"AUTO-CATEGORIZED BY RULES  —  {len(matched)} transaction(s)",
                matched, False, "#e8f5e9", "#2e7d32"))
        if unmatched:
            sections.append((
                f"NEEDS A CATEGORY  —  {len(unmatched)} transaction(s)",
                unmatched, False, "#fff8e1", "#b45309"))
        if dups:
            sections.append((
                f"DUPLICATES (already imported, will be skipped)  —  {len(dups)} transaction(s)",
                dups, True, "#f3f4f6", "#6b7280"))

        total_rows = sum(1 + len(txns) for _, txns, _, _, _ in sections)
        self._table.clear_rows()
        self._table.setRowCount(total_rows)

        row = 0
        for header_text, txns, is_dup, hdr_bg, hdr_fg in sections:
            # Section header row — spans all columns
            self._table.setSpan(row, 0, 1, 5)
            hdr_item = QTableWidgetItem(f"  {header_text}")
            hdr_item.setBackground(QColor(hdr_bg))
            hdr_item.setForeground(QColor(hdr_fg))
            f = QFont()
            f.setBold(True)
            f.setPointSize(9)
            hdr_item.setFont(f)
            hdr_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not selectable
            self._table.setItem(row, 0, hdr_item)
            self._table.setRowHeight(row, 26)
            row += 1

            for t in txns:
                cat_name = cats.get(t.get("category_id", ""), "")
                self._table.set_item(row, 0, t.get("date", ""))
                self._table.set_item(row, 1, t.get("payee") or t.get("memo", "—"))
                self._table.money_item(row, 2, float(t.get("amount") or 0))
                if cat_name:
                    self._table.set_item(row, 3, cat_name, color=SUCCESS)
                else:
                    self._table.set_item(row, 3, "—")
                if is_dup:
                    self._table.set_item(row, 4, "Duplicate", color=WARNING)
                self._table.setRowHeight(row, 28)
                row += 1

    def _import(self):
        if not self._parsed:
            return
        try:
            saved = self.db.bulk_save_transactions(self._parsed)
            QMessageBox.information(self, "Import Complete",
                f"Successfully imported {len(saved)} transactions.")
            self._parsed = []
            self._table.clear_rows()
            self._import_btn.setEnabled(False)
            self._status_label.setText(f"✓ Imported {len(saved)} transactions")
        except Exception as e:
            QMessageBox.critical(self, "Import Failed", str(e))
