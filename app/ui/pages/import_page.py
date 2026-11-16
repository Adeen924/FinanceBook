"""Import transactions from OFX / QFX / QBO / CSV / XLSX."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QComboBox, QLabel, QFileDialog, QMessageBox,
                              QGroupBox, QFormLayout, QLineEdit,
                              QHeaderView, QTableWidgetItem, QCheckBox)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from ui.widgets import PageTitle, DataTable
from ui.styles  import SUCCESS, WARNING

FLIP_SETTING = "import_flip|{account}|{format}"


def _money(v: float) -> str:
    return f"${abs(v):,.2f}"


class ImportPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._parsed: list[dict] = []
        self._fixes: list[dict] = []       # earlier imports whose sign gets corrected
        self._file_bytes = b""
        self._filename   = ""
        self._format_id  = ""
        self._parsed_key = None            # (account, file) the flip choice belongs to
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        lay.addWidget(PageTitle("Import Transactions"))

        # ── Step 1: Choose file + account ──
        grp1 = QGroupBox("Step 1 — Select File && Account")
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
        file_row.addStretch()
        self._banks_btn = QPushButton("Supported banks…")
        self._banks_btn.setObjectName("Secondary")
        self._banks_btn.setToolTip(
            "Bank CSV exports FinanceBook recognises and reads the right way round. "
            "Other files still import with automatic column detection.")
        self._banks_btn.clicked.connect(self._show_supported_banks)
        file_row.addWidget(self._banks_btn)
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

        self._flip = QCheckBox("Flip all signs")
        self._flip.setToolTip(
            "Money out should show in red and money in in green. If the preview has "
            "them the wrong way round (common for credit-card files that list a "
            "purchase as 42.10), tick this. FinanceBook remembers the choice for "
            "this account and bank format.")
        self._flip.toggled.connect(self._flip_toggled)
        step3_hdr.addWidget(self._flip)

        parse_btn = QPushButton("Parse File")
        parse_btn.clicked.connect(self._parse)
        step3_hdr.addWidget(parse_btn)

        self._import_btn = QPushButton("Import All")
        self._import_btn.setObjectName("Success")
        self._import_btn.clicked.connect(self._import)
        self._import_btn.setEnabled(False)
        step3_hdr.addWidget(self._import_btn)
        lay.addLayout(step3_hdr)

        summary = QHBoxLayout()
        self._format_label = QLabel("")
        self._format_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._totals_label = QLabel("")
        summary.addWidget(self._format_label)
        summary.addStretch()
        summary.addWidget(self._totals_label)
        lay.addLayout(summary)

        self._warn_label = QLabel("")
        self._warn_label.setWordWrap(True)
        self._warn_label.setStyleSheet(
            f"background:#fff6d6; color:#7a4b00; border:1px solid {WARNING}; "
            "border-radius:8px; padding:8px 12px;")
        self._warn_label.setVisible(False)
        lay.addWidget(self._warn_label)

        self._status_label = QLabel("")
        lay.addWidget(self._status_label)

        self._table = DataTable(
            ["Date", "Payee / Memo", "Amount", "Auto Account", "Duplicate?"])
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
        self._reset_preview()

    def _reset_preview(self):
        self._parsed = []
        self._fixes = []
        self._parsed_key = None
        self._table.clear_rows()
        self._import_btn.setEnabled(False)
        self._status_label.setText("")
        self._format_label.setText("")
        self._totals_label.setText("")
        self._warn_label.setVisible(False)

    def _show_supported_banks(self):
        from ui.dialogs.supported_banks_dialog import SupportedBanksDialog
        SupportedBanksDialog(self).exec()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select bank export file", "",
            "Bank Files (*.ofx *.qfx *.qbo *.csv *.xlsx *.xls);;All Files (*)")
        if not path:
            return
        with open(path, "rb") as f:
            self.set_file(f.read(), path.split("/")[-1].split("\\")[-1])

    def set_file(self, data: bytes, filename: str):
        self._file_bytes = data
        self._filename = filename
        self._file_label.setText(filename)
        ext = filename.rsplit(".", 1)[-1].lower()
        self._col_grp.setEnabled(ext in ("csv", "xlsx", "xls"))
        self._reset_preview()

    # ── parsing ───────────────────────────────────────────────────────────────

    def _flip_toggled(self, _checked):
        # Re-parse what's on screen with the new direction.
        if self._parsed_key is not None:
            self._parse(from_toggle=True)

    def _flip_key(self, acct_id: str, format_id: str) -> str:
        return FLIP_SETTING.format(account=acct_id, format=format_id)

    def _set_flip(self, on: bool):
        self._flip.blockSignals(True)
        self._flip.setChecked(on)
        self._flip.blockSignals(False)

    def _run_parser(self, acct_id, kind, ext, flip):
        """(rows, ParseResult or None) for the current file."""
        if ext in ("ofx", "qfx", "qbo"):
            from parsers.qfx import parse_ofx
            from io import BytesIO
            rows = parse_ofx(BytesIO(self._file_bytes), acct_id)
            if flip:
                for t in rows:
                    t["amount"] = str(-float(t.get("amount") or 0))
            return rows, None
        from parsers.spreadsheet import analyze_spreadsheet
        col_map = {
            "date":   self._col_date.text().strip()   or None,
            "payee":  self._col_payee.text().strip()  or None,
            "amount": self._col_amount.text().strip() or None,
            "debit":  self._col_debit.text().strip()  or None,
            "credit": self._col_credit.text().strip() or None,
        }
        col_map = {k: v for k, v in col_map.items() if v}
        res = analyze_spreadsheet(self._file_bytes, self._filename, acct_id,
                                  col_map or None, reverse_signs=flip, account_kind=kind)
        return res.rows, res

    def _parse(self, from_toggle: bool = False):
        acct_id = self._acct_combo.currentData()
        if not acct_id:
            QMessageBox.warning(self, "No Account",
                "Please select an account to import into.")
            return
        if not self._file_bytes:
            QMessageBox.warning(self, "No File",
                "Please select a file to import.")
            return

        from parsers.spreadsheet import account_kind_for
        acct = self.db.get_account(acct_id) or {}
        kind = account_kind_for(acct.get("type", ""))
        ext  = self._filename.rsplit(".", 1)[-1].lower()
        key  = (acct_id, self._filename, len(self._file_bytes))
        try:
            if from_toggle or key == self._parsed_key:
                parsed, res = self._run_parser(acct_id, kind, ext, self._flip.isChecked())
            else:
                # A new file / account: start from the flip choice remembered for
                # this account + bank format (off when there is none).
                parsed, res = self._run_parser(acct_id, kind, ext, False)
                fmt_id = res.format_id if res else "ofx"
                remembered = self.db.get_setting(self._flip_key(acct_id, fmt_id), "0") == "1"
                self._set_flip(remembered)
                if remembered:
                    parsed, res = self._run_parser(acct_id, kind, ext, True)
        except Exception as e:
            QMessageBox.critical(self, "Parse Error",
                f"Could not parse file:\n{e}")
            return
        self._parsed_key = key
        self._format_id = res.format_id if res else "ofx"
        warnings = res.warnings if res else []

        existing_hashes = self.db.get_existing_hashes()
        dups, fixes, new = [], [], []
        for t in parsed:
            if t.get("import_hash") in existing_hashes:
                dups.append(t)
            elif t.get("_legacy_hash") and t["_legacy_hash"] in existing_hashes:
                fixes.append(t)
            else:
                new.append(t)

        # Auto-apply rules to new transactions
        self.db.apply_rules(new)

        self._parsed = new
        self._fixes = fixes
        self._render_preview(new, dups, fixes)
        self._render_summary(parsed, res)

        matched   = [t for t in new if t.get("category_id") or t.get("_transfer_to")]
        unmatched = [t for t in new if not (t.get("category_id") or t.get("_transfer_to"))]

        status_parts = []
        if matched:
            status_parts.append(f"{len(matched)} auto-categorized")
        if unmatched:
            status_parts.append(f"{len(unmatched)} need an account")
        if fixes:
            status_parts.append(f"{len(fixes)} earlier imports get their sign corrected")
        if dups:
            status_parts.append(f"{len(dups)} duplicates skipped")
        if warnings:
            status_parts.append(f"{len(warnings)} parse warnings")
        if res:
            status_parts.extend(res.notes)
        self._status_label.setText("   |   ".join(status_parts) or "No transactions found")
        self._import_btn.setEnabled(bool(new or fixes))

        if warnings and not from_toggle:
            QMessageBox.warning(self, "Parse Warnings",
                "\n".join(warnings[:10]) +
                ("\n…and more" if len(warnings) > 10 else ""))

    def _render_summary(self, parsed, res):
        label = res.format_label if res else "OFX / QFX statement"
        self._format_label.setText(f"Detected format: <b>{label}</b>")
        ins  = [float(t["amount"]) for t in parsed if float(t.get("amount") or 0) > 0]
        outs = [float(t["amount"]) for t in parsed if float(t.get("amount") or 0) < 0]
        self._totals_label.setText(
            f"<span style='color:{SUCCESS}'>Money in: {len(ins)} · {_money(sum(ins))}</span>"
            f"&nbsp;&nbsp;&nbsp;"
            f"<span style='color:#c9363b'>Money out: {len(outs)} · {_money(sum(outs))}</span>")

        reasons = []
        if res is not None and res.looks_reversed:
            reasons.append(
                "These signs look reversed for a credit card: most purchases come in as "
                "money in" + (" and card payments as money out" if any(
                    "PAYMENT" in (t.get("payee") or "").upper() for t in parsed) else "")
                + ". Tick “Flip all signs” if so.")
        if res is not None and res.type_mismatches:
            reasons.append(
                f"{res.type_mismatches} row(s) disagree with the file's own type column "
                "(for example a “Debit” or “Sale” showing as money in). Check the signs — "
                "“Flip all signs” may be needed.")
        if res is not None and res.kind_mismatch:
            reasons.append(res.kind_mismatch + " Check that the right account is selected.")
        self._warn_label.setText("⚠  " + "\n⚠  ".join(reasons) if reasons else "")
        self._warn_label.setVisible(bool(reasons))

    def _render_preview(self, new_txns, dup_txns, fix_txns=()):
        cats = {c["id"]: c["name"] for c in self.db.get_categories()}
        accts = {a["id"]: a["name"] for a in self.db.get_accounts(active=None)}

        def by_date(t):
            return t.get("date", "")

        def has_account(t):
            return bool(t.get("category_id") or t.get("_transfer_to"))

        matched   = sorted([t for t in new_txns if has_account(t)],  key=by_date)
        unmatched = sorted([t for t in new_txns if not has_account(t)], key=by_date)
        dups      = sorted(dup_txns, key=by_date)
        fixes     = sorted(fix_txns, key=by_date)

        # Build sections: (header_text, transactions, tag, bg, fg)
        sections = []
        if matched:
            sections.append((
                f"ACCOUNT SET BY RULES  —  {len(matched)} transaction(s)",
                matched, "", "#e7f6ec", "#15803d"))
        if unmatched:
            sections.append((
                f"NEEDS AN ACCOUNT  —  {len(unmatched)} transaction(s)",
                unmatched, "", "#fff4e0", "#b45309"))
        if fixes:
            sections.append((
                f"IMPORTED EARLIER WITH THE WRONG SIGN (will be corrected, not added "
                f"again)  —  {len(fixes)} transaction(s)",
                fixes, "Fix sign", "#fff6d6", "#7a4b00"))
        if dups:
            sections.append((
                f"DUPLICATES (already imported, will be skipped)  —  {len(dups)} transaction(s)",
                dups, "Duplicate", "#eef2f8", "#6b7a99"))

        total_rows = sum(1 + len(txns) for _, txns, _, _, _ in sections)
        self._table.clear_rows()
        self._table.setRowCount(total_rows)

        row = 0
        for header_text, txns, tag, hdr_bg, hdr_fg in sections:
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
                if t.get("_transfer_to"):
                    cat_name = "⇄ Transfer · " + accts.get(t["_transfer_to"], "account")
                self._table.set_item(row, 0, t.get("date", ""))
                text = t.get("payee") or t.get("memo", "—")
                if t.get("payee") and t.get("memo"):
                    text = f"{t['payee']}  ·  {t['memo']}"
                self._table.set_item(row, 1, text)
                self._table.money_item(row, 2, float(t.get("amount") or 0))
                if cat_name:
                    self._table.set_item(row, 3, cat_name, color=SUCCESS)
                else:
                    self._table.set_item(row, 3, "—")
                if tag:
                    self._table.set_item(row, 4, tag, color=WARNING)
                self._table.setRowHeight(row, 28)
                row += 1

    # ── importing ─────────────────────────────────────────────────────────────

    def _import(self):
        if not (self._parsed or self._fixes):
            return
        acct_id = self._acct_combo.currentData()
        try:
            rows = []
            for t in self._parsed:
                t = dict(t)
                t.pop("_legacy_hash", None)
                rows.append(t)
            saved = self.db.bulk_save_transactions(rows)
            outcome = {"fixed": 0, "locked": 0, "skipped": 0, "missing": 0}
            for t in self._fixes:
                outcome[self.db.fix_imported_sign(acct_id, t["_legacy_hash"],
                                                  t["import_hash"])] += 1
            if acct_id and self._format_id:
                self.db.set_setting(self._flip_key(acct_id, self._format_id),
                                    "1" if self._flip.isChecked() else "0")
            msg = f"Successfully imported {len(saved)} transactions."
            if outcome["fixed"]:
                msg += (f"\n\nCorrected the sign of {outcome['fixed']} transaction(s) an "
                        "earlier import had the wrong way round.")
            left = outcome["locked"] + outcome["skipped"]
            if left:
                msg += (f"\n\n{left} earlier import(s) with the wrong sign were left alone "
                        "(in a locked period, or a transfer / split). Review them with "
                        "Settings → Check for Flipped Imported Transactions.")
            QMessageBox.information(self, "Import Complete", msg)
            self._parsed = []
            self._fixes = []
            self._parsed_key = None
            self._table.clear_rows()
            self._import_btn.setEnabled(False)
            done = f"✓ Imported {len(saved)} transactions"
            if outcome["fixed"]:
                done += f", corrected {outcome['fixed']}"
            self._status_label.setText(done)
        except Exception as e:
            QMessageBox.critical(self, "Import Failed", str(e))
