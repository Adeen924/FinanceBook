"""Add / Edit transaction dialog."""
from datetime import date
from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                              QDoubleSpinBox, QDialogButtonBox, QVBoxLayout,
                              QTextEdit, QLabel, QCheckBox, QFrame,
                              QPushButton, QMessageBox, QWidget, QHBoxLayout)
from PyQt6.QtCore import QDate, Qt
from ui.widgets import FilterComboBox, DateField, AccountPicker
from utils.chart_of_accounts import account_value
from utils.ledger import category_sign


# Owner-paid treatments (business books): which built-in account the expense
# is recorded in.
OWNER_TREATMENTS = [
    ("capital",   "Capital contribution — I'm investing it (not repaid)", "owner_capital"),
    ("reimburse", "Company owes me — reimburse me later", "due_to_owner"),
]


class TransactionDialog(QDialog):
    def __init__(self, db, transaction: dict = None, account_id: str = "", parent=None,
                 preset: dict = None):
        super().__init__(parent)
        self.db = db
        self.transaction = transaction or {}
        # preset: initial values for a NEW transaction (e.g. paid_by=owner from
        # the Owner Investment page). Ignored when editing.
        self._preset = {} if transaction else dict(preset or {})
        self._business = db.is_business()
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
        # Loaded once up front so _category_sign() works before the category
        # combo is built (the amount field needs it to decide its display sign).
        self._cats = self.db.get_categories()

        # Date
        self.date_edit = DateField()
        txn_date = self.transaction.get("date") or date.today().isoformat()
        self.date_edit.setDate(QDate.fromString(txn_date, "yyyy-MM-dd"))
        self.date_edit.dateChanged.connect(self._update_loan_preview)
        form.addRow("Date *", self.date_edit)

        # Account
        self.account_combo = QComboBox()
        self._accounts = self.db.get_accounts()
        for a in self._accounts:
            self.account_combo.addItem(a["name"], a["id"])
        cur_acct = (self.transaction.get("account_id") or account_id
                    or self._default_business_account())
        for i, a in enumerate(self._accounts):
            if a["id"] == cur_acct:
                self.account_combo.setCurrentIndex(i)
                break

        if self._business:
            self._build_paid_by_rows(form)
        self.account_combo.setToolTip("The account this entry appears in (bank, card, cash…)")
        form.addRow("Bank / card account *", self.account_combo)
        if self._business:
            self.account_combo.currentIndexChanged.connect(self._sync_paid_by_from_account)
            self._init_paid_by()

        # Payee
        self.payee_edit = QLineEdit(self.transaction.get("payee",""))
        self.payee_edit.setPlaceholderText("Who paid or was paid")
        form.addRow("Payee", self.payee_edit)

        # Memo
        self.memo_edit = QLineEdit(self.transaction.get("memo",""))
        self.memo_edit.setPlaceholderText("Optional description")
        form.addRow("Memo", self.memo_edit)

        # Amount: a positive number plus an explicit direction. Stored amounts
        # are signed (negative = money out of this account); an existing entry
        # opens with the direction of its stored sign and keeps it — picking or
        # changing the account never flips it (an imported withdrawal stays a
        # withdrawal). A new entry follows the picked account's usual direction
        # until the user chooses one. A negative amount still means "the other
        # way" (e.g. a refund) and is tidied into Money in / out on leaving.
        stored = float(self.transaction.get("amount") or 0)
        self.amount_spin = QDoubleSpinBox()
        self.amount_spin.setRange(-9_999_999, 9_999_999)
        self.amount_spin.setDecimals(2)
        self.amount_spin.setPrefix("$")
        self.amount_spin.setSingleStep(1.0)
        # Done before connecting valueChanged so it triggers no side effects.
        self.amount_spin.setValue(abs(stored))
        self.amount_spin.valueChanged.connect(self._update_loan_preview)
        self.amount_spin.editingFinished.connect(self._normalize_amount)

        self.direction_combo = QComboBox()
        self.direction_combo.addItem("Money out", "out")
        self.direction_combo.addItem("Money in", "in")
        self.direction_combo.setItemData(0, "A payment, purchase or card charge",
                                         Qt.ItemDataRole.ToolTipRole)
        self.direction_combo.setItemData(1, "A deposit, refund or card payment",
                                         Qt.ItemDataRole.ToolTipRole)
        self.direction_combo.setToolTip(
            "Whether this entry takes money out of the account above or puts money in.")
        # True once the direction is decided by the stored entry or by the user;
        # until then (a new entry) it follows the picked account.
        self._direction_fixed = abs(stored) >= 0.005
        if self._direction_fixed:
            initial = "in" if stored > 0 else "out"
        else:
            initial = self._natural_direction(self.transaction.get("category_id", "")
                                              or self._preset.get("category_id", ""))
        self._set_direction(initial)
        self.direction_combo.activated.connect(self._on_direction_picked)
        amt_row = QWidget()
        ar = QHBoxLayout(amt_row)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.setSpacing(8)
        ar.addWidget(self.amount_spin, 1)
        ar.addWidget(self.direction_combo, 1)
        form.addRow("Amount *", amt_row)

        self._amount_note = QLabel("")
        self._amount_note.setObjectName("Muted")
        self._amount_note.setWordWrap(True)
        form.addRow("", self._amount_note)

        # Account — one type-to-filter list of income/expense accounts
        # (categories) and balance-sheet accounts. Picking a balance-sheet
        # account makes the entry a transfer to/from it.
        self._partner = None
        if (self.transaction.get("id") and str(self.transaction.get("is_transfer", "0")) == "1"
                and not self.transaction.get("split_group_id")):
            self._partner = self.db.get_transfer_partner(self.transaction)
        self.cat_combo = AccountPicker(
            self.db, exclude_account_id=self.account_combo.currentData() or "",
            keep_ids=[self.transaction.get("category_id", "")])
        self.cat_combo.setToolTip(
            "Where the money goes or comes from: an income or expense account, or one "
            "of your own accounts (bank, card, loan…) to record a transfer.")
        if self._partner:
            self.cat_combo.select_by_data(account_value(self._partner.get("account_id", "")))
        else:
            self.cat_combo.select_by_data(self.transaction.get("category_id", ""))
        form.addRow("Account", self.cat_combo)
        self.cat_combo.currentIndexChanged.connect(self._on_target_changed)
        self.cat_combo.lineEdit().editingFinished.connect(self._on_target_changed)
        self.account_combo.currentIndexChanged.connect(
            lambda _: self.cat_combo.set_exclude_account(self.account_combo.currentData() or ""))

        if self._business:
            self.charged_combo = FilterComboBox(placeholder="Company (general business)")
            self.charged_combo.add_option("Company (general business)", "")
            for p in self.db.get_people():
                self.charged_combo.add_option(f"Person · {p['name']}", f"person:{p['id']}")
            for c in self.db.get_classes():
                self.charged_combo.add_option(f"Project / Class · {c['name']}", f"class:{c['id']}")
            self.charged_combo.select_by_data(
                self.transaction.get("charged_to") or self._preset.get("charged_to", "")
                or "")
            if not self.charged_combo.currentText():
                self.charged_combo.setCurrentIndex(0)
            form.addRow("Charged to", self.charged_combo)

            self.contact_combo = FilterComboBox(placeholder="Vendor / customer (optional)")
            for c in self.db.get_contacts():
                self.contact_combo.add_option(c["name"], c["id"])
            self.contact_combo.select_by_data(self.transaction.get("contact_id", ""))
            form.addRow("Vendor / customer", self.contact_combo)
            self.payee_edit.editingFinished.connect(self._suggest_contact)

        # ── Split section ──────────────────────────────────────────────────────
        sep0 = QFrame(); sep0.setFrameShape(QFrame.Shape.HLine); sep0.setObjectName("Muted")
        form.addRow(sep0)
        self._split_chk = QCheckBox("Split this transaction across multiple accounts")
        self._split_chk.toggled.connect(self._on_split_toggled)
        form.addRow("Split", self._split_chk)

        self._split_box = QWidget()
        _sb = QHBoxLayout(self._split_box)
        _sb.setContentsMargins(0, 0, 0, 0)
        _sb.setSpacing(8)
        self._split_btn = QPushButton("Choose accounts && amounts…")
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

        self._set_row_visible(self._loan_combo, self._loan_chk.isChecked())
        self._loan_preview.setVisible(self._loan_chk.isChecked())
        if self._loan_chk.isChecked():
            self._update_loan_preview()

        # Notes
        self.notes_edit = QTextEdit(self.transaction.get("notes",""))
        self.notes_edit.setMaximumHeight(70)
        self.notes_edit.setPlaceholderText("Optional notes…")
        form.addRow("Notes", self.notes_edit)

        # Attachments (existing transactions only — a new one has no id yet).
        if self.transaction.get("id"):
            att_row = QWidget()
            al = QHBoxLayout(att_row)
            al.setContentsMargins(0, 0, 0, 0)
            self._att_label = QLabel("")
            self._att_label.setObjectName("Muted")
            al.addWidget(self._att_label, 1)
            att_btn = QPushButton("Attach…")
            att_btn.setObjectName("Secondary")
            att_btn.clicked.connect(self._attach)
            al.addWidget(att_btn)
            self._att_open = QPushButton("Open")
            self._att_open.setObjectName("Secondary")
            self._att_open.clicked.connect(self._open_attachment)
            al.addWidget(self._att_open)
            form.addRow("📎 Receipts", att_row)
            self._refresh_attachments()

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

        # An existing transfer opens from this row's side: the other account is in
        # the Account picker and the direction reads Money out / in for this
        # account, so re-saving keeps both sides exactly as they were.
        self._on_target_changed()
        self._update_loan_lock()

    # ── attachments ────────────────────────────────────────────────────────────

    def _refresh_attachments(self):
        self._attached = self.db.get_transaction_receipts(self.transaction)
        n = len(self._attached)
        self._att_label.setText(", ".join(r.get("original_name") or r.get("vendor") or "receipt"
                                          for r in self._attached) if n else "No files attached")
        self._att_open.setEnabled(bool(n))

    def _attach(self):
        import os
        from PyQt6.QtWidgets import QFileDialog
        from ui.pages.receipts import FILE_FILTER
        path, _ = QFileDialog.getOpenFileName(self, "Attach Receipt or File",
                                              os.path.expanduser("~"), FILE_FILTER)
        if not path:
            return
        try:
            rc, _dup = self.db.add_receipt_file(path)
        except OSError as e:
            QMessageBox.warning(self, "Attach Failed", str(e))
            return
        self.db.attach_receipt(rc["id"], self.transaction)
        self._refresh_attachments()

    def _open_attachment(self):
        import os
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        for rc in self._attached:
            path = self.db.receipt_abs_path(rc)
            if path and os.path.exists(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                return

    # ── business: who paid ─────────────────────────────────────────────────────

    def _default_business_account(self) -> str:
        """New business entries default to the company's chosen funding account
        (set when a business bank account is opened), if it still exists."""
        if not self._business or self.transaction:
            return ""
        acct_id = self.db.get_setting("default_funding_account")
        if acct_id and any(a["id"] == acct_id for a in self.db.get_accounts()):
            return acct_id
        return ""

    def _build_paid_by_rows(self, form):
        self._owner_id = self.db.owner_person_id()
        self.paid_by_combo = QComboBox()
        self.paid_by_combo.addItem("Company account (bank, card, petty cash)", "")
        for p in self.db.get_people():
            owner_label = "Me" if p["name"].strip().lower() == "owner" else p["name"]
            label = (f"{owner_label} (owner) — my own money" if p["id"] == self._owner_id
                     else f"{p['name']} — paid personally")
            self.paid_by_combo.addItem(label, p["id"])
        form.addRow("Paid by", self.paid_by_combo)

        self.treatment_combo = QComboBox()
        for key, label, _role in OWNER_TREATMENTS:
            self.treatment_combo.addItem(label, key)
        form.addRow("Treat as", self.treatment_combo)
        self._treatment_hint = QLabel(
            "Recorded in a built-in owner account, so no business bank account is "
            "needed. Capital is your investment; “Company owes me” shows as a "
            "liability until you use Reimburse Owner.")
        self._treatment_hint.setObjectName("Muted")
        self._treatment_hint.setWordWrap(True)
        form.addRow("", self._treatment_hint)
        self.paid_by_combo.currentIndexChanged.connect(self._on_paid_by_changed)
        self.treatment_combo.currentIndexChanged.connect(self._on_paid_by_changed)

    def _role_of(self, acct_id: str) -> str:
        a = next((a for a in self._accounts if a["id"] == acct_id), None)
        return (a or {}).get("role", "") or ""

    def _select_account_by_role(self, role: str, name: str, atype: str):
        acct = self.db.get_or_create_role_account(role, name, atype)
        if not any(a["id"] == acct["id"] for a in self._accounts):
            self._accounts.append(acct)
            self.account_combo.addItem(acct["name"], acct["id"])
        ix = self.account_combo.findData(acct["id"])
        if ix >= 0:
            self.account_combo.blockSignals(True)
            self.account_combo.setCurrentIndex(ix)
            self.account_combo.blockSignals(False)

    def _init_paid_by(self):
        t = self.transaction
        paid_by = t.get("paid_by") if t else self._preset.get("paid_by")
        role = self._role_of(self.account_combo.currentData() or "")
        if not t and paid_by is None and not self._default_business_account():
            # No company account chosen yet → most likely the owner paid.
            has_bank = any(not a.get("role") and a.get("type") not in ("equity", "liability")
                           for a in self._accounts)
            paid_by = "" if has_bank else self._owner_id
        if role in ("owner_capital", "due_to_owner", "owner_paid"):
            paid_by = self._owner_id
        treatment = ("reimburse" if role == "due_to_owner"
                     else "capital" if role in ("owner_capital", "owner_paid")
                     else self._preset.get("treatment")
                     or self.db.get_setting("owner_paid_default_treatment", "capital"))
        self.paid_by_combo.blockSignals(True)
        self.treatment_combo.blockSignals(True)
        ix = self.paid_by_combo.findData(paid_by or "")
        self.paid_by_combo.setCurrentIndex(max(ix, 0))
        self.treatment_combo.setCurrentIndex(max(self.treatment_combo.findData(treatment), 0))
        self.paid_by_combo.blockSignals(False)
        self.treatment_combo.blockSignals(False)
        if not t:
            self._on_paid_by_changed()
        else:
            # Editing: show how it was recorded, never move it to another account.
            self._update_paid_by_visibility()

    def _update_paid_by_visibility(self):
        is_owner = self.paid_by_combo.currentData() == self._owner_id
        self._set_row_visible(self.treatment_combo, is_owner)
        self._treatment_hint.setVisible(is_owner)

    def _on_paid_by_changed(self, *_):
        who = self.paid_by_combo.currentData() or ""
        self._update_paid_by_visibility()
        if who == self._owner_id:
            key = self.treatment_combo.currentData()
            role = next(r for k, _l, r in OWNER_TREATMENTS if k == key)
            from sheets.client import BUSINESS_ACCOUNTS
            name, atype = BUSINESS_ACCOUNTS[role]
            if self._role_of(self.account_combo.currentData() or "") != "owner_paid" \
                    or key != "capital":
                self._select_account_by_role(role, name, atype)
        elif who:
            self._select_account_by_role("reimb_payable", "Reimbursements Payable", "liability")
        elif self._role_of(self.account_combo.currentData() or ""):
            # Back to a company account: pick the default funding / first real one.
            target = self.db.get_setting("default_funding_account") or next(
                (a["id"] for a in self._accounts if not a.get("role")), "")
            ix = self.account_combo.findData(target)
            if ix >= 0:
                self.account_combo.blockSignals(True)
                self.account_combo.setCurrentIndex(ix)
                self.account_combo.blockSignals(False)

    def _sync_paid_by_from_account(self, *_):
        """Picking an owner account by hand updates Paid by to match."""
        role = self._role_of(self.account_combo.currentData() or "")
        self.paid_by_combo.blockSignals(True)
        self.treatment_combo.blockSignals(True)
        if role in ("owner_capital", "due_to_owner", "owner_paid"):
            self.paid_by_combo.setCurrentIndex(max(self.paid_by_combo.findData(self._owner_id), 0))
            self.treatment_combo.setCurrentIndex(
                self.treatment_combo.findData("reimburse" if role == "due_to_owner" else "capital"))
        elif not role:
            self.paid_by_combo.setCurrentIndex(0)
        self.paid_by_combo.blockSignals(False)
        self.treatment_combo.blockSignals(False)
        self._update_paid_by_visibility()

    def _suggest_contact(self):
        if self.contact_combo.current_data():
            return
        c = self.db.match_contact(self.payee_edit.text())
        if c:
            self.contact_combo.select_by_data(c["id"])
            if not self.cat_combo.currentText().strip() and c.get("default_category_id"):
                self.cat_combo.select_by_data(c["default_category_id"])

    def _business_fields(self) -> dict:
        if not self._business:
            return {}
        who = self.paid_by_combo.currentData() or ""
        role = self._role_of(self.account_combo.currentData() or "")
        reimb = role in ("due_to_owner", "reimb_payable")
        if who == self._owner_id and role == "owner_paid":
            acct = next((a for a in self._accounts if a["id"] == self.account_combo.currentData()), {})
            reimb = acct.get("type") == "liability"
        out = {"paid_by": who, "reimbursable": "1" if reimb else "0",
               "charged_to": self.charged_combo.current_data() or "",
               "contact_id": self.contact_combo.current_data() or ""}
        if out["charged_to"].startswith("class:"):
            out["class_id"] = out["charged_to"][6:]
        return out

    # ── combo builders ──────────────────────────────────────────────────────────

    def _category_sign(self, cat_id: str) -> int:
        """-1 for spending categories (expense, other expense, debt repayment —
        entered positive but stored negative = money out) and for a transfer to
        another account (positive = money leaving this one); +1 otherwise. A
        negative entry therefore becomes a refund (stored positive)."""
        if (cat_id or "").startswith("acct:"):
            return -1
        cat = next((c for c in getattr(self, "_cats", []) if c.get("id") == cat_id), None)
        return category_sign(cat.get("type")) if cat else 1

    def _natural_direction(self, value: str) -> str:
        """The usual direction for a picker value: 'out' for spending accounts
        and transfers to another account (and for no account yet — most new
        entries are payments), 'in' for income."""
        if not value:
            return "out"
        return "out" if self._category_sign(value) < 0 else "in"

    def _set_direction(self, direction: str):
        self.direction_combo.blockSignals(True)
        self.direction_combo.setCurrentIndex(max(self.direction_combo.findData(direction), 0))
        self.direction_combo.blockSignals(False)

    def _on_direction_picked(self, *_):
        self._direction_fixed = True
        self._on_target_changed()       # refresh the note under the amount

    def _direction_sign(self) -> int:
        return -1 if self.direction_combo.currentData() == "out" else 1

    def _normalize_amount(self):
        """A negative amount means the other direction: show it as a positive
        amount with the direction switched (e.g. -20 on Groceries → $20 Money in)."""
        v = self.amount_spin.value()
        if v < 0:
            self._set_direction("in" if self.direction_combo.currentData() == "out" else "out")
            self._direction_fixed = True
            self.amount_spin.setValue(-v)

    def _target(self) -> tuple:
        """('category', id) | ('account', id) | ('', '') from the Account picker."""
        return self.cat_combo.selection()

    def _is_transfer(self) -> bool:
        return self._target()[0] == "account"

    # ── mode handling ────────────────────────────────────────────────────────────
    # Transfer + Split + Category can all be used together. Only a loan payment
    # is exclusive (its principal/interest split conflicts with the others).

    def _force_off(self, *checkboxes):
        for c in checkboxes:
            c.blockSignals(True)
            c.setChecked(False)
            c.blockSignals(False)

    def _update_loan_lock(self):
        busy = self._is_transfer() or self._split_chk.isChecked()
        self._loan_chk.setEnabled(not busy)
        loan_on = self._loan_chk.isChecked()
        self._split_chk.setEnabled(not loan_on)

    def _set_row_visible(self, field_widget, visible: bool):
        """Hide/show a form row, including its label."""
        lbl = self._form.labelForField(field_widget)
        if lbl is not None:
            lbl.setVisible(visible)
        field_widget.setVisible(visible)

    def _on_target_changed(self, *_):
        """The Account picker changed: a balance-sheet account makes this a
        transfer (no Paid by / Charged to; filed under "Account Transfers" and
        left out of P&L)."""
        if not hasattr(self, "_loan_chk"):
            return          # still building the dialog
        on = self._is_transfer()
        if not self._direction_fixed:
            self._set_direction(self._natural_direction(self.cat_combo.current_data()))
        if self._business:
            self._set_row_visible(self.paid_by_combo, not on)
            self._set_row_visible(self.charged_combo, not on)
            if on:
                self._set_row_visible(self.treatment_combo, False)
                self._treatment_hint.setVisible(False)
            else:
                self._update_paid_by_visibility()
        self._amount_note.setText(
            "Transfer: Money out moves it from this account into the one picked "
            "above; Money in moves it from there into this account. The other "
            "account's entry is created (or the matching imported one linked) "
            "automatically."
            if on else
            ("This entry keeps its direction when you change the account — switch "
             "Money out / Money in only if the amount really went the other way."
             if self._direction_fixed else
             "Enter the amount; the account fills in its usual direction (Expense = "
             "money out, Income = money in). Pick Money in for a refund."))
        if on and self._loan_chk.isChecked():
            self._force_off(self._loan_chk)
            self._set_row_visible(self._loan_combo, False)
            self._loan_preview.setVisible(False)
        self._update_loan_lock()

    def _on_split_toggled(self, on: bool):
        self._split_box.setVisible(on)
        if on and self._loan_chk.isChecked():
            self._force_off(self._loan_chk)
            self._set_row_visible(self._loan_combo, False)
            self._loan_preview.setVisible(False)
        self._update_loan_lock()

    def _define_split(self):
        # Split lines are entered as positive magnitudes; their per-line category
        # decides each line's sign (see db.split_transaction), so hand the dialog
        # the magnitude of the total to allocate.
        total = abs(self.amount_spin.value())
        if total < 0.005:
            QMessageBox.warning(self, "Amount Needed",
                                "Enter the transaction amount first, then set the split.")
            return
        base = dict(self.transaction)
        base["amount"] = str(total)
        base["account_id"] = self.account_combo.currentData() or base.get("account_id", "")
        kind, target = self._target()
        base["category_id"] = target if kind == "category" else ""
        from ui.dialogs.split_dialog import SplitDialog
        dlg = SplitDialog(self.db, base, total_amount=total, parent=self,
                          first_target=self.cat_combo.current_data())
        if dlg.exec() == SplitDialog.DialogCode.Accepted:
            self._pending_splits = dlg.get_splits()
            self._update_split_summary()

    def _update_split_summary(self):
        if not self._pending_splits:
            self._split_summary.setText("No split defined yet.")
            return
        cats = {c["id"]: c for c in self.db.get_categories()}
        accts = {a["id"]: a for a in self.db.get_accounts(active=None)}
        parts = []
        for s in self._pending_splits:
            if s.get("transfer_to"):
                nm = "→ " + accts.get(s["transfer_to"], {}).get("name", "account")
            else:
                nm = cats.get(s.get("category_id", ""), {}).get("name", "Uncategorized")
            parts.append(f"{nm}: ${abs(float(s.get('amount') or 0)):,.2f}")
        self._split_summary.setText("  ·  ".join(parts))

    # ── loan helpers ──────────────────────────────────────────────────────────

    def _on_loan_toggled(self, checked: bool):
        if checked:
            self._force_off(self._split_chk)
            self._split_box.setVisible(False)
        self._set_row_visible(self._loan_combo, checked)
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
        # Guard: this can fire from amount/date changes during _build, before the
        # loan widgets exist.
        if not getattr(self, "_loan_chk", None) or not self._loan_chk.isChecked():
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

        split_on = self._split_chk.isChecked()
        kind, dest = self._target()

        if split_on and (not self._pending_splits or len(self._pending_splits) < 2):
            QMessageBox.warning(self, "Define the Split",
                "Click “Choose accounts & amounts…” and set at least two "
                "lines that add up to the total.")
            return

        if not split_on and self.cat_combo.currentText().strip() and not kind:
            QMessageBox.warning(self, "Pick an Account",
                f"“{self.cat_combo.currentText().strip()}” isn't in the list. "
                "Pick an account from the list, or clear the field to leave it "
                "uncategorized.")
            self.cat_combo.setFocus()
            return

        transfer_on = kind == "account" and not split_on
        if transfer_on:
            if dest == self.account_combo.currentData():
                QMessageBox.warning(self, "Same Account",
                    "The other account must be different from this one.")
                return
            if abs(self.amount_spin.value()) < 0.005:
                QMessageBox.warning(self, "Amount Needed", "Enter the amount.")
                return

        # Split (lines may move parts to other accounts)
        if split_on:
            data = self.get_data()
            if self._partner:
                self.db.detach_transfer_partner(self.transaction)
                data["is_transfer"], data["transfer_pair_id"] = "0", ""
            self.db.split_transaction(data, self._pending_splits)
            self.handled = True
            self.accept()
            return

        # Transfer to / from another of the user's accounts
        if transfer_on:
            self.db.save_transfer_pair(self.get_data(), dest, keep_sign=True,
                                       match_existing=True)
            self.handled = True
            self.accept()
            return

        # No longer a transfer: undo the other side (an imported bank entry is
        # unlinked and left uncategorized, a mirror FinanceBook made is removed).
        if self._partner:
            self.db.detach_transfer_partner(self.transaction)

        if self._loan_chk.isChecked() and not self._loans:
            QMessageBox.warning(self, "No Loans",
                                "No loans exist yet. Add one on the Loans page first.")
            return
        if self._business and self._maybe_record_as_draw():
            return
        if self._business and self.paid_by_combo.currentData() == self._owner_id:
            self.db.set_setting("owner_paid_default_treatment",
                                self.treatment_combo.currentData() or "capital")
        self.accept()

    def _maybe_record_as_draw(self) -> bool:
        """Company money spent on the owner personally isn't a business expense —
        offer to book it as an Owner's Draw. Returns True if handled."""
        if (self.paid_by_combo.currentData() or "") != "" or \
                self.charged_combo.current_data() != f"person:{self._owner_id}":
            return False
        if self._role_of(self.account_combo.currentData() or ""):
            return False
        reply = QMessageBox.question(
            self, "Personal Expense",
            "This was paid with company money but charged to you personally.\n\n"
            "Record it as an Owner's Draw (money you took out) instead of a "
            "business expense? Draws are not deductible and stay off the P&L.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
            QMessageBox.StandardButton.Cancel)
        if reply == QMessageBox.StandardButton.Cancel:
            return True
        if reply == QMessageBox.StandardButton.No:
            return False
        from sheets.client import BUSINESS_ACCOUNTS
        name, atype = BUSINESS_ACCOUNTS["owner_draws"]
        draws = self.db.get_or_create_role_account("owner_draws", name, atype)
        data = self.get_data()
        data["amount"] = str(abs(self.amount_spin.value()))
        if self.transaction.get("id"):
            self.db.delete_transaction_full(self.transaction)
            data.pop("id", None)
        self.db.save_transfer_pair(data, draws["id"])
        self.handled = True
        self.accept()
        return True

    def get_data(self) -> dict:
        data = dict(self.transaction)
        kind, target = self._target()
        cat_id = target if kind == "category" else ""
        entered = self.amount_spin.value()
        # The amount is typed as a positive number and Money out / Money in sets
        # the stored sign (out → negative). A negative entry means the opposite
        # direction (a refund). For a transfer, money out leaves this account.
        amount = entered * self._direction_sign() or 0.0     # never "-0.0"
        if kind != "account" and (str(data.get("is_transfer", "0")) == "1"
                                  and not data.get("split_group_id")):
            # Was a transfer, now categorized: it becomes an ordinary entry.
            data["is_transfer"] = "0"
            data["transfer_pair_id"] = ""
        data.update({
            "date":       self.date_edit.date().toString("yyyy-MM-dd"),
            "account_id": self.account_combo.currentData(),
            "payee":      self.payee_edit.text().strip(),
            "memo":       self.memo_edit.text().strip(),
            "amount":     str(amount),
            "category_id": cat_id,
            "notes":      self.notes_edit.toPlainText().strip(),
        })
        data.update(self._business_fields())

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
