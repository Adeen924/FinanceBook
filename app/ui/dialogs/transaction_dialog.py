"""Add / Edit transaction dialog."""
from datetime import date
from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                              QDoubleSpinBox, QDialogButtonBox, QVBoxLayout,
                              QDateEdit, QTextEdit, QLabel, QCheckBox, QFrame,
                              QPushButton, QMessageBox)
from PyQt6.QtCore import QDate


class TransactionDialog(QDialog):
    def __init__(self, db, transaction: dict = None, account_id: str = "", parent=None):
        super().__init__(parent)
        self.db = db
        self.transaction = transaction or {}
        # Set True when the user splits from inside this dialog: the split has
        # already been written, so the caller must NOT re-save get_data() over it.
        self.did_split = False
        self.setWindowTitle("Edit Transaction" if transaction else "Add Transaction")
        self.setMinimumWidth(500)
        self._build(account_id)

    def _build(self, account_id):
        lay  = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        # Date
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        txn_date = self.transaction.get("date") or date.today().isoformat()
        self.date_edit.setDate(QDate.fromString(txn_date, "yyyy-MM-dd"))
        self.date_edit.dateChanged.connect(self._update_loan_preview)
        form.addRow("Date *", self.date_edit)

        # Account
        self.account_combo = QComboBox()
        self._accounts = self.db.get_accounts()
        for a in self._accounts:
            self.account_combo.addItem(a["name"], a["id"])
        cur_acct = self.transaction.get("account_id") or account_id
        for i, a in enumerate(self._accounts):
            if a["id"] == cur_acct:
                self.account_combo.setCurrentIndex(i)
                break
        form.addRow("Account *", self.account_combo)

        # Payee
        self.payee_edit = QLineEdit(self.transaction.get("payee",""))
        self.payee_edit.setPlaceholderText("Who paid or was paid")
        form.addRow("Payee", self.payee_edit)

        # Memo
        self.memo_edit = QLineEdit(self.transaction.get("memo",""))
        self.memo_edit.setPlaceholderText("Optional description")
        form.addRow("Memo", self.memo_edit)

        # Amount
        self.amount_spin = QDoubleSpinBox()
        self.amount_spin.setRange(-9_999_999, 9_999_999)
        self.amount_spin.setDecimals(2)
        self.amount_spin.setPrefix("$")
        self.amount_spin.setSingleStep(1.0)
        self.amount_spin.setValue(float(self.transaction.get("amount") or 0))
        self.amount_spin.valueChanged.connect(self._update_loan_preview)
        form.addRow("Amount *", self.amount_spin)

        note = QLabel("Negative = expense/payment out,  Positive = income/deposit in")
        note.setObjectName("Muted")
        form.addRow("", note)

        # Category
        self.cat_combo = QComboBox()
        self.cat_combo.addItem("— Uncategorized —", "")
        cats = self.db.get_categories()
        self._cats = cats
        roots = [c for c in cats if not c.get("parent_id")]
        for cat in roots:
            self.cat_combo.addItem(cat["name"], cat["id"])
            for sub in cats:
                if sub.get("parent_id") == cat["id"]:
                    self.cat_combo.addItem(f"    ↳ {sub['name']}", sub["id"])
        cur_cat = self.transaction.get("category_id","")
        for i in range(self.cat_combo.count()):
            if self.cat_combo.itemData(i) == cur_cat:
                self.cat_combo.setCurrentIndex(i)
                break
        form.addRow("Category", self.cat_combo)

        # ── Loan Payment section ───────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("Muted")
        form.addRow(sep)

        self._loan_chk = QCheckBox("This is a loan payment")
        self._loan_chk.setChecked(bool(self.transaction.get("loan_id", "")))
        self._loan_chk.toggled.connect(self._on_loan_toggled)
        form.addRow("Loan Payment", self._loan_chk)

        self._loan_combo = QComboBox()
        self._loans = self.db.get_loans()
        for ln in self._loans:
            self._loan_combo.addItem(ln["name"], ln["id"])
        cur_loan = self.transaction.get("loan_id", "")
        for i, ln in enumerate(self._loans):
            if ln["id"] == cur_loan:
                self._loan_combo.setCurrentIndex(i)
                break
        self._loan_combo.currentIndexChanged.connect(self._update_loan_preview)
        form.addRow("Select Loan", self._loan_combo)

        self._loan_preview = QLabel("")
        self._loan_preview.setObjectName("Muted")
        self._loan_preview.setWordWrap(True)
        form.addRow("", self._loan_preview)

        # Show/hide loan controls
        self._loan_combo.setVisible(self._loan_chk.isChecked())
        self._loan_preview.setVisible(self._loan_chk.isChecked())

        if self._loan_chk.isChecked():
            self._update_loan_preview()

        # ─────────────────────────────────────────────────────────────────────

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setObjectName("Muted")
        form.addRow(sep2)

        # Class
        self.class_combo = QComboBox()
        self.class_combo.addItem("— No Class —", "")
        for cls in self.db.get_classes():
            self.class_combo.addItem(cls["name"], cls["id"])
        cur_cls = self.transaction.get("class_id","")
        for i in range(self.class_combo.count()):
            if self.class_combo.itemData(i) == cur_cls:
                self.class_combo.setCurrentIndex(i)
                break
        form.addRow("Class", self.class_combo)

        # Notes
        self.notes_edit = QTextEdit(self.transaction.get("notes",""))
        self.notes_edit.setMaximumHeight(70)
        self.notes_edit.setPlaceholderText("Optional notes…")
        form.addRow("Notes", self.notes_edit)

        lay.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)

        # Split: only for an already-saved transaction that isn't a transfer or
        # loan payment (those track their own breakdown).
        can_split = (bool(self.transaction.get("id"))
                     and str(self.transaction.get("is_transfer", "0")) != "1"
                     and not self.transaction.get("loan_id"))
        if can_split:
            split_btn = QPushButton(
                "Edit Split…" if self.transaction.get("split_group_id")
                else "Split into categories…")
            split_btn.setObjectName("Secondary")
            split_btn.clicked.connect(self._open_split)
            btns.addButton(split_btn, QDialogButtonBox.ButtonRole.ActionRole)

        lay.addWidget(btns)

    # ── split ───────────────────────────────────────────────────────────────────

    def _open_split(self):
        total = self.amount_spin.value()
        if abs(total) < 0.005:
            QMessageBox.warning(self, "Amount Needed",
                                "Enter the transaction amount before splitting.")
            return
        # Carry any edits made in this dialog into the split's shared fields.
        base = dict(self.transaction)
        base.update({
            "date":       self.date_edit.date().toString("yyyy-MM-dd"),
            "account_id": self.account_combo.currentData(),
            "payee":      self.payee_edit.text().strip(),
            "memo":       self.memo_edit.text().strip(),
            "amount":     str(total),
            "class_id":   self.class_combo.currentData() or "",
            "notes":      self.notes_edit.toPlainText().strip(),
        })

        from ui.dialogs.split_dialog import SplitDialog
        dlg = SplitDialog(self.db, base, total_amount=total, parent=self)
        if dlg.exec() == SplitDialog.DialogCode.Accepted:
            self.db.split_transaction(base, dlg.get_splits())
            self.did_split = True
            self.accept()

    # ── loan helpers ──────────────────────────────────────────────────────────

    def _on_loan_toggled(self, checked: bool):
        self._loan_combo.setVisible(checked)
        self._loan_preview.setVisible(checked)
        if checked:
            self._update_loan_preview()
            # Auto-fill interest category from loan
            self._apply_loan_category()
        else:
            self._loan_preview.setText("")

    def _current_loan(self) -> dict | None:
        if not self._loan_chk.isChecked() or not self._loans:
            return None
        idx = self._loan_combo.currentIndex()
        if idx < 0 or idx >= len(self._loans):
            return None
        return self._loans[idx]

    def _update_loan_preview(self):
        if not self._loan_chk.isChecked():
            return
        loan = self._current_loan()
        if not loan:
            self._loan_preview.setText("No loans found — add one on the Loans page.")
            return

        from utils.loan import split_for_date
        txn_date   = self.date_edit.date().toString("yyyy-MM-dd")
        raw_amount = self.amount_spin.value()
        amount     = abs(raw_amount)

        if amount == 0:
            self._loan_preview.setText("Enter the payment amount above to see the split.")
            return

        split = split_for_date(loan, txn_date, amount)

        lines = [
            f"<b>Payment #{split['payment_number']}</b> "
            f"(scheduled: ${split['scheduled_payment']:,.2f})",
            f"  Principal: <b>${split['principal']:,.2f}</b>",
            f"  Interest:  <b>${split['interest']:,.2f}</b>",
            f"  Balance before: ${split['balance_before']:,.2f}  →  "
            f"after: ${split['balance_after']:,.2f}",
        ]
        if amount > split["scheduled_payment"] + 0.005:
            extra = round(split["principal"] - (split["scheduled_payment"] - split["interest"]), 2)
            lines.append(f"  Extra principal: +${extra:,.2f}")

        self._loan_preview.setText("<br>".join(lines))

    def _apply_loan_category(self):
        loan = self._current_loan()
        if not loan:
            return
        cat_id = loan.get("interest_category_id", "")
        if not cat_id:
            return
        for i in range(self.cat_combo.count()):
            if self.cat_combo.itemData(i) == cat_id:
                self.cat_combo.setCurrentIndex(i)
                break

    # ── accept ────────────────────────────────────────────────────────────────

    def _accept(self):
        if not self.account_combo.currentData():
            self.account_combo.setFocus()
            return
        if self._loan_chk.isChecked() and not self._loans:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "No Loans",
                                "No loans exist yet. Add one on the Loans page first.")
            return
        self.accept()

    def get_data(self) -> dict:
        data = dict(self.transaction)
        data.update({
            "date":       self.date_edit.date().toString("yyyy-MM-dd"),
            "account_id": self.account_combo.currentData(),
            "payee":      self.payee_edit.text().strip(),
            "memo":       self.memo_edit.text().strip(),
            "amount":     str(self.amount_spin.value()),
            "category_id": self.cat_combo.currentData() or "",
            "class_id":   self.class_combo.currentData() or "",
            "notes":      self.notes_edit.toPlainText().strip(),
        })

        # Loan split
        if self._loan_chk.isChecked():
            loan = self._current_loan()
            if loan:
                from utils.loan import split_for_date
                txn_date = data["date"]
                amount   = abs(float(data["amount"]))
                split    = split_for_date(loan, txn_date, amount)
                data["loan_id"]          = loan["id"]
                data["principal_amount"] = str(split["principal"])
                data["interest_amount"]  = str(split["interest"])
                # Auto-set interest category if not manually overridden
                if not data["category_id"] and loan.get("interest_category_id"):
                    data["category_id"] = loan["interest_category_id"]
            else:
                data["loan_id"]          = ""
                data["principal_amount"] = "0"
                data["interest_amount"]  = "0"
        else:
            data["loan_id"]          = ""
            data["principal_amount"] = "0"
            data["interest_amount"]  = "0"

        return data
