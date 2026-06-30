"""Add / Edit transaction dialog."""
from datetime import date
from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                              QDoubleSpinBox, QDialogButtonBox, QVBoxLayout,
                              QDateEdit, QTextEdit, QLabel, QCheckBox, QFrame,
                              QPushButton, QMessageBox, QWidget, QHBoxLayout)
from PyQt6.QtCore import QDate
from ui.widgets import FilterComboBox


class TransactionDialog(QDialog):
    def __init__(self, db, transaction: dict = None, account_id: str = "", parent=None):
        super().__init__(parent)
        self.db = db
        self.transaction = transaction or {}
        # When True, this dialog already persisted the result (a split and/or a
        # transfer), so the caller must NOT re-save get_data() on top of it.
        self.handled = False
        self._pending_splits: list[dict] | None = None
        self.setWindowTitle("Edit Transaction" if transaction else "Add Transaction")
        self.setMinimumWidth(500)
        self._build(account_id)

    def _build(self, account_id):
        lay  = QVBoxLayout(self)
        form = QFormLayout()
        self._form = form
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

        self._amount_note = QLabel("Negative = expense/payment out,  Positive = income/deposit in")
        self._amount_note.setObjectName("Muted")
        form.addRow("", self._amount_note)

        # Category — type-to-filter, starts blank with a placeholder
        self.cat_combo = self._build_category_combo()
        self.cat_combo.select_by_data(self.transaction.get("category_id",""))
        form.addRow("Category", self.cat_combo)

        # ── Transfer section ───────────────────────────────────────────────────
        sep0 = QFrame(); sep0.setFrameShape(QFrame.Shape.HLine); sep0.setObjectName("Muted")
        form.addRow(sep0)

        self._transfer_chk = QCheckBox("This is a transfer between my accounts")
        self._transfer_chk.toggled.connect(self._on_transfer_toggled)
        form.addRow("Transfer", self._transfer_chk)

        self._transfer_box = QWidget()
        _tb = QVBoxLayout(self._transfer_box)
        _tb.setContentsMargins(0, 0, 0, 0)
        _tb.setSpacing(4)
        self._transfer_acct_combo = QComboBox()
        for a in self._accounts:
            self._transfer_acct_combo.addItem(a["name"], a["id"])
        _tb.addWidget(self._transfer_acct_combo)
        _hint = QLabel("Money moves into this account. A matching entry is created "
                       "there automatically, and transfers are left out of P&L.")
        _hint.setObjectName("Muted"); _hint.setWordWrap(True)
        _tb.addWidget(_hint)
        form.addRow("Goes to account", self._transfer_box)
        self._transfer_box.setVisible(False)

        # ── Split section ──────────────────────────────────────────────────────
        self._split_chk = QCheckBox("Split this transaction across multiple categories")
        self._split_chk.toggled.connect(self._on_split_toggled)
        form.addRow("Split", self._split_chk)

        self._split_box = QWidget()
        _sb = QHBoxLayout(self._split_box)
        _sb.setContentsMargins(0, 0, 0, 0)
        _sb.setSpacing(8)
        self._split_btn = QPushButton("Choose categories & amounts…")
        self._split_btn.setObjectName("Secondary")
        self._split_btn.clicked.connect(self._define_split)
        _sb.addWidget(self._split_btn)
        self._split_summary = QLabel("No split defined yet.")
        self._split_summary.setObjectName("Muted")
        self._split_summary.setWordWrap(True)
        _sb.addWidget(self._split_summary, 1)
        form.addRow("", self._split_box)
        self._split_box.setVisible(False)

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

        self._loan_combo.setVisible(self._loan_chk.isChecked())
        self._loan_preview.setVisible(self._loan_chk.isChecked())
        if self._loan_chk.isChecked():
            self._update_loan_preview()

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
        # Delete button — only when editing an existing transaction.
        if self.transaction.get("id"):
            self.deleted = False
            del_btn = btns.addButton("Delete", QDialogButtonBox.ButtonRole.DestructiveRole)
            del_btn.setObjectName("Danger")
            del_btn.clicked.connect(self._delete_self)
        lay.addWidget(btns)

        # Pre-fill an existing transfer so it can be reviewed / kept in sync.
        # Always present the outflow (negative) side as the source "Account" and
        # the inflow (positive) side as "Goes to account", no matter which row the
        # user opened, so re-saving never flips the transfer's direction.
        if self.transaction.get("id") and str(self.transaction.get("is_transfer","0")) == "1":
            partner = self.db.get_transfer_partner(self.transaction)
            if partner:
                self._transfer_chk.setChecked(True)
                this_amt = float(self.transaction.get("amount") or 0)
                if this_amt <= 0:
                    from_acct = self.transaction.get("account_id")
                    to_acct   = partner.get("account_id")
                else:
                    from_acct = partner.get("account_id")
                    to_acct   = self.transaction.get("account_id")
                for i in range(self.account_combo.count()):
                    if self.account_combo.itemData(i) == from_acct:
                        self.account_combo.setCurrentIndex(i)
                        break
                for i in range(self._transfer_acct_combo.count()):
                    if self._transfer_acct_combo.itemData(i) == to_acct:
                        self._transfer_acct_combo.setCurrentIndex(i)
                        break
                self.amount_spin.setValue(abs(this_amt))

        self._update_loan_lock()

    # ── combo builders ──────────────────────────────────────────────────────────

    def _build_category_combo(self) -> FilterComboBox:
        """Filterable category combo: roots as 'Name', subs as 'Parent → Child'."""
        combo = FilterComboBox(placeholder="Type to search categories…")
        cats = self.db.get_categories()
        self._cats = cats
        roots = [c for c in cats if not c.get("parent_id")]
        for cat in roots:
            combo.add_option(cat["name"], cat["id"])
            for sub in cats:
                if sub.get("parent_id") == cat["id"]:
                    combo.add_option(f"{cat['name']} → {sub['name']}", sub["id"])
        return combo

    # ── mode handling ────────────────────────────────────────────────────────────
    # Transfer + Split + Category can all be used together. Only a loan payment
    # is exclusive (its principal/interest split conflicts with the others).

    def _force_off(self, *checkboxes):
        for c in checkboxes:
            c.blockSignals(True)
            c.setChecked(False)
            c.blockSignals(False)

    def _update_loan_lock(self):
        busy = self._transfer_chk.isChecked() or self._split_chk.isChecked()
        self._loan_chk.setEnabled(not busy)
        loan_on = self._loan_chk.isChecked()
        self._transfer_chk.setEnabled(not loan_on)
        self._split_chk.setEnabled(not loan_on)

    def _set_row_visible(self, field_widget, visible: bool):
        """Hide/show a form row, including its label."""
        lbl = self._form.labelForField(field_widget)
        if lbl is not None:
            lbl.setVisible(visible)
        field_widget.setVisible(visible)

    def _on_transfer_toggled(self, on: bool):
        self._transfer_box.setVisible(on)
        # Transfers need no payee or category — they're filed automatically under
        # "Account Transfers" and excluded from P&L. Hide those rows for clarity.
        self._set_row_visible(self.payee_edit, not on)
        self._set_row_visible(self.cat_combo, not on)
        self._amount_note.setVisible(not on)
        if on and self._loan_chk.isChecked():
            self._force_off(self._loan_chk)
            self._loan_combo.setVisible(False)
            self._loan_preview.setVisible(False)
        self._update_loan_lock()

    def _on_split_toggled(self, on: bool):
        self._split_box.setVisible(on)
        if on and self._loan_chk.isChecked():
            self._force_off(self._loan_chk)
            self._loan_combo.setVisible(False)
            self._loan_preview.setVisible(False)
        self._update_loan_lock()

    def _define_split(self):
        total = self.amount_spin.value()
        if abs(total) < 0.005:
            QMessageBox.warning(self, "Amount Needed",
                                "Enter the transaction amount first, then set the split.")
            return
        base = dict(self.transaction)
        base["amount"] = str(total)
        from ui.dialogs.split_dialog import SplitDialog
        dlg = SplitDialog(self.db, base, total_amount=total, parent=self)
        if dlg.exec() == SplitDialog.DialogCode.Accepted:
            self._pending_splits = dlg.get_splits()
            self._update_split_summary()

    def _update_split_summary(self):
        if not self._pending_splits:
            self._split_summary.setText("No split defined yet.")
            return
        cats = {c["id"]: c for c in self.db.get_categories()}
        parts = []
        for s in self._pending_splits:
            nm = cats.get(s.get("category_id",""), {}).get("name", "Uncategorized")
            parts.append(f"{nm}: ${abs(float(s.get('amount') or 0)):,.2f}")
        self._split_summary.setText("  ·  ".join(parts))

    # ── loan helpers ──────────────────────────────────────────────────────────

    def _on_loan_toggled(self, checked: bool):
        if checked:
            self._force_off(self._transfer_chk, self._split_chk)
            self._transfer_box.setVisible(False)
            self._split_box.setVisible(False)
            # Transfer was just force-disabled (signals blocked), so restore the
            # Payee/Category rows it had hidden.
            self._set_row_visible(self.payee_edit, True)
            self._set_row_visible(self.cat_combo, True)
            self._amount_note.setVisible(True)
        self._loan_combo.setVisible(checked)
        self._loan_preview.setVisible(checked)
        self._update_loan_lock()
        if checked:
            self._update_loan_preview()
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
        if cat_id:
            self.cat_combo.select_by_data(cat_id)

    # ── delete ──────────────────────────────────────────────────────────────

    def _delete_self(self):
        """Delete this transaction. For a transfer, both sides are removed; for a
        split, the whole group is removed."""
        is_transfer = str(self.transaction.get("is_transfer", "0")) == "1" \
            and self.transaction.get("transfer_pair_id")
        members = self.db.get_split_group(self.transaction.get("split_group_id", ""))
        if is_transfer:
            extra = "\n\nThis is a transfer — the matching entry in the other " \
                    "account will be deleted too."
        elif len(members) > 1:
            extra = f"\n\nThis is a split with {len(members)} lines — all of " \
                    "them will be deleted."
        else:
            extra = ""
        reply = QMessageBox.question(
            self, "Delete Transaction",
            f"Delete this transaction on {self.transaction.get('date','')} for "
            f"${abs(float(self.transaction.get('amount',0) or 0)):,.2f}?{extra}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_transaction_full(self.transaction)
        self.deleted = True
        # Mark handled so the caller doesn't try to re-save the deleted row.
        self.handled = True
        self.accept()

    # ── accept ────────────────────────────────────────────────────────────────

    def _accept(self):
        if not self.account_combo.currentData():
            self.account_combo.setFocus()
            return

        split_on    = self._split_chk.isChecked()
        transfer_on = self._transfer_chk.isChecked()
        dest = self._transfer_acct_combo.currentData() if transfer_on else ""

        if split_on and (not self._pending_splits or len(self._pending_splits) < 2):
            QMessageBox.warning(self, "Define the Split",
                "Click “Choose categories & amounts…” and set at least two "
                "lines that add up to the total.")
            return

        if transfer_on:
            if not dest:
                QMessageBox.warning(self, "Choose an Account",
                                    "Select the account the money is going into.")
                return
            if dest == self.account_combo.currentData():
                QMessageBox.warning(self, "Same Account",
                    "The destination account must be different from the source.")
                return
            if abs(self.amount_spin.value()) < 0.005:
                QMessageBox.warning(self, "Amount Needed", "Enter the amount.")
                return

        # Split (optionally also a transfer)
        if split_on:
            self.db.split_transaction(self.get_data(), self._pending_splits,
                                      transfer_to=dest)
            self.handled = True
            self.accept()
            return

        # Transfer only
        if transfer_on:
            self.db.save_transfer_pair(self.get_data(), dest)
            self.handled = True
            self.accept()
            return

        if self._loan_chk.isChecked() and not self._loans:
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
            "category_id": self.cat_combo.current_data() or "",
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
