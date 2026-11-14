"""Add / Edit account dialog."""
from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                              QDoubleSpinBox, QDialogButtonBox, QVBoxLayout,
                              QLabel, QCompleter, QCheckBox)
from PyQt6.QtCore import Qt


ACCOUNT_TYPES = ["checking", "savings", "credit card", "gift card", "loan", "investment",
                 "cash", "fixed assets", "other assets", "liability", "equity", "other"]

_FIXED_ASSETS_HINT = (
    "For assets you track by hand (no depreciation): move money in with a transfer. "
    "Equipment added in the Fixed Assets module is already on the balance sheet — "
    "don't also transfer it here, or it is counted twice.")
CURRENCIES    = ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "CNY", "MXN"]


class AccountDialog(QDialog):
    def __init__(self, account: dict = None, institutions: list = None,
                 balance: float = 0.0, parent=None, account_type: str = "",
                 accounts: list = None):
        """accounts: the active accounts, offered (filtered to the same Chart of
        Accounts group) as the parent in "Sub-account of". None hides the row."""
        super().__init__(parent)
        self.account = account or {}
        self._all_accounts = accounts
        if account_type and not self.account.get("id"):
            self.account = dict(self.account, type=account_type)
        self.balance = float(balance or 0)
        self.setWindowTitle("Edit Account" if account else "Add Account")
        self.setMinimumWidth(420)
        self._build(institutions or [])

    def _build(self, institutions: list):
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(form.labelAlignment() | form.labelAlignment().AlignRight)

        self.name_edit = QLineEdit(self.account.get("name",""))
        self.name_edit.setPlaceholderText("e.g. Chase Checking")
        form.addRow("Account Name *", self.name_edit)

        self.type_combo = QComboBox()
        for t in ACCOUNT_TYPES:
            self.type_combo.addItem(t.title(), t)
        cur_type = self.account.get("type") or "checking"
        if cur_type not in ACCOUNT_TYPES:
            # A type this list doesn't offer (e.g. "credit" from an import) is
            # kept as it is rather than silently becoming "checking".
            self.type_combo.addItem(cur_type.title(), cur_type)
        self.type_combo.setCurrentIndex(max(self.type_combo.findData(cur_type), 0))
        form.addRow("Account Type *", self.type_combo)
        self._type_hint = QLabel(_FIXED_ASSETS_HINT)
        self._type_hint.setObjectName("Muted")
        self._type_hint.setWordWrap(True)
        form.addRow("", self._type_hint)
        self.type_combo.currentIndexChanged.connect(self._sync_type_hint)
        self._sync_type_hint()

        # Sub-account of: a top-level account in the same group (a bank account
        # under a bank account, a card under a card…).
        self.parent_combo = None
        if self._all_accounts is not None:
            self.parent_combo = QComboBox()
            self.parent_combo.setToolTip(
                "Nest this account under another one in the Accounts list. Its register, "
                "reconciliation and reports stay its own; the parent shows the total.")
            form.addRow("Sub-account of", self.parent_combo)
            self._fill_parents(self.account.get("parent_id") or "")
            self.type_combo.currentIndexChanged.connect(
                lambda _: self._fill_parents(self.parent_combo.currentData() or ""))

        self.institution_edit = QLineEdit(self.account.get("institution",""))
        self.institution_edit.setPlaceholderText("e.g. Bank of America")
        if institutions:
            completer = QCompleter(institutions)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            self.institution_edit.setCompleter(completer)
        form.addRow("Institution", self.institution_edit)

        self.opening_spin = QDoubleSpinBox()
        self.opening_spin.setRange(-9_999_999, 9_999_999)
        self.opening_spin.setDecimals(2)
        self.opening_spin.setPrefix("$")
        self.opening_spin.setSingleStep(1.0)
        self.opening_spin.setValue(float(self.account.get("opening_balance") or 0))
        form.addRow("Opening Balance", self.opening_spin)

        note = QLabel("Balance on the day you started tracking in FinanceBook")
        note.setObjectName("Muted")
        form.addRow("", note)

        self.currency_combo = QComboBox()
        self.currency_combo.addItems(CURRENCIES)
        cur_cur = self.account.get("currency","USD")
        cidx = CURRENCIES.index(cur_cur) if cur_cur in CURRENCIES else 0
        self.currency_combo.setCurrentIndex(cidx)
        form.addRow("Currency", self.currency_combo)

        # ── Inactive toggle (only when editing an existing account) ────────────
        self._inactive_chk = None
        if self.account.get("id"):
            already_inactive = str(self.account.get("active", "1")) == "0"
            has_balance = abs(self.balance) >= 0.005

            self._inactive_chk = QCheckBox("Make account Inactive")
            self._inactive_chk.setChecked(already_inactive)
            form.addRow("Status", self._inactive_chk)

            # Rule: only a $0-balance account can be made inactive. If it already
            # is inactive, keep the box usable so it can be reactivated.
            if has_balance and not already_inactive:
                self._inactive_chk.setEnabled(False)
                hint = QLabel(
                    f"This account has a balance of ${self.balance:,.2f}. "
                    "Only accounts with a $0 balance can be made inactive.")
                hint.setObjectName("Muted")
                hint.setWordWrap(True)
                form.addRow("", hint)

        lay.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _fill_parents(self, want: str):
        from utils.chart_of_accounts import group_for_account
        grp = group_for_account({"type": self.type_combo.currentData() or ""}).key
        me = self.account.get("id") or ""
        self.parent_combo.blockSignals(True)
        self.parent_combo.clear()
        self.parent_combo.addItem("— None (top-level account) —", "")
        for a in sorted(self._all_accounts, key=lambda a: (a.get("name") or "").lower()):
            if a.get("id") != me and not a.get("parent_id") \
                    and group_for_account(a).key == grp:
                self.parent_combo.addItem(a.get("name", ""), a["id"])
        self.parent_combo.setCurrentIndex(max(self.parent_combo.findData(want), 0))
        self.parent_combo.blockSignals(False)

    def _sync_type_hint(self, *_):
        self._type_hint.setVisible(self.type_combo.currentData() == "fixed assets")

    def _accept(self):
        if not self.name_edit.text().strip():
            self.name_edit.setFocus()
            return
        # Safety net for the $0-balance rule (the checkbox is also disabled).
        if (self._inactive_chk is not None and self._inactive_chk.isChecked()
                and str(self.account.get("active", "1")) != "0"
                and abs(self.balance) >= 0.005):
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Cannot Make Inactive",
                f"This account has a balance of ${self.balance:,.2f}.\n\n"
                "Only accounts with a $0 balance can be made inactive.")
            return
        self.accept()

    def get_data(self) -> dict:
        data = dict(self.account)
        data.update({
            "name": self.name_edit.text().strip(),
            "type": self.type_combo.currentData() or "checking",
            "institution": self.institution_edit.text().strip(),
            "opening_balance": str(self.opening_spin.value()),
            "currency": self.currency_combo.currentText(),
        })
        if self._inactive_chk is not None:
            data["active"] = "0" if self._inactive_chk.isChecked() else "1"
        if self.parent_combo is not None:
            data["parent_id"] = self.parent_combo.currentData() or ""
        return data
