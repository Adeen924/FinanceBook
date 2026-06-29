"""Add / Edit account dialog."""
from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                              QDoubleSpinBox, QDialogButtonBox, QVBoxLayout,
                              QLabel, QCompleter)
from PyQt6.QtCore import Qt


ACCOUNT_TYPES = ["checking", "savings", "credit card", "gift card", "loan", "investment", "cash", "other"]
CURRENCIES    = ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "CNY", "MXN"]


class AccountDialog(QDialog):
    def __init__(self, account: dict = None, institutions: list = None, parent=None):
        super().__init__(parent)
        self.account = account or {}
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
        self.type_combo.addItems([t.title() for t in ACCOUNT_TYPES])
        cur_type = self.account.get("type","checking")
        idx = ACCOUNT_TYPES.index(cur_type) if cur_type in ACCOUNT_TYPES else 0
        self.type_combo.setCurrentIndex(idx)
        form.addRow("Account Type *", self.type_combo)

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

        lay.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _accept(self):
        if not self.name_edit.text().strip():
            self.name_edit.setFocus()
            return
        self.accept()

    def get_data(self) -> dict:
        data = dict(self.account)
        data.update({
            "name": self.name_edit.text().strip(),
            "type": ACCOUNT_TYPES[self.type_combo.currentIndex()],
            "institution": self.institution_edit.text().strip(),
            "opening_balance": str(self.opening_spin.value()),
            "currency": self.currency_combo.currentText(),
        })
        return data
