"""
Owner money movements for Business books.

Every action here is a transfer between a real company account (bank, card,
petty cash) and one of the built-in owner accounts, so it never touches the
P&L. See utils/ledger.py for the sign conventions.
"""

from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                             QDoubleSpinBox, QDialogButtonBox, QVBoxLayout,
                             QLabel, QCheckBox, QMessageBox)
from PyQt6.QtCore import QDate

from ui.widgets import DateField
from sheets.client import BUSINESS_ACCOUNTS
from utils.money import D, to_float


def company_accounts(db) -> list[dict]:
    """Accounts real money can come from or go to: banks, cards, petty cash."""
    return [a for a in db.get_accounts()
            if (not a.get("role") or a.get("role") == "petty_cash")
            and a.get("type") != "equity"]


def owner_account(db, role: str) -> dict:
    name, atype = BUSINESS_ACCOUNTS[role]
    return db.get_or_create_role_account(role, name, atype)


def owed_to_owner(db) -> float:
    """What the company owes the owner right now (positive)."""
    total = D(0)
    for a in db.get_accounts(active=None):
        if a.get("role") == "due_to_owner" or (
                a.get("role") == "owner_paid" and a.get("type") == "liability"):
            total += -D(db.account_balance(a["id"]))
    return to_float(total)


# mode → (title, intro, owner role, owner side is the destination?)
_MODES = {
    "reimburse": ("Reimburse Owner",
                  "Pay yourself back from a company account. This reduces "
                  "<b>Due to Owner</b> and is not an expense.",
                  "due_to_owner", True),
    "draw": ("Owner Draw",
             "Take money out of the business for personal use. Draws reduce "
             "your equity; they are not a business expense.",
             "owner_draws", True),
    "contribute": ("Capital Contribution",
                   "Put your own money into a company account (for example the "
                   "first deposit into a new business bank account).",
                   "owner_capital", False),
    "convert": ("Convert Amount Owed to Capital",
                "Turn money the company owes you into a capital contribution "
                "(you won't be repaid). This moves it from <b>Due to Owner</b> "
                "to <b>Owner's Equity</b>.",
                "owner_capital", False),
}


class OwnerMoneyDialog(QDialog):
    def __init__(self, db, mode: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.mode = mode
        title, intro, role, owner_is_dest = _MODES[mode]
        self._role = role
        self._owner_is_dest = owner_is_dest
        self.setWindowTitle(title)
        self.setMinimumWidth(460)

        lay = QVBoxLayout(self)
        info = QLabel(intro)
        info.setWordWrap(True)
        info.setObjectName("InfoBox")
        lay.addWidget(info)

        form = QFormLayout()
        form.setSpacing(10)
        self.date_edit = DateField()
        self.date_edit.setDate(QDate.currentDate())
        form.addRow("Date *", self.date_edit)

        self.acct_combo = QComboBox()
        if mode == "convert":
            due = owner_account(db, "due_to_owner")
            self.acct_combo.addItem(due["name"], due["id"])
        else:
            for a in company_accounts(db):
                self.acct_combo.addItem(a["name"], a["id"])
            pref = db.get_setting("default_funding_account")
            ix = self.acct_combo.findData(pref)
            if ix >= 0:
                self.acct_combo.setCurrentIndex(ix)
        label = {"reimburse": "Pay from", "draw": "Take from",
                 "contribute": "Deposit into", "convert": "Amount owed in"}[mode]
        form.addRow(label + " *", self.acct_combo)

        self.amount_spin = QDoubleSpinBox()
        self.amount_spin.setRange(0, 9_999_999)
        self.amount_spin.setDecimals(2)
        self.amount_spin.setPrefix("$")
        owed = owed_to_owner(db)
        if mode in ("reimburse", "convert") and owed > 0:
            self.amount_spin.setValue(owed)
        form.addRow("Amount *", self.amount_spin)
        if mode in ("reimburse", "convert"):
            hint = QLabel(f"The company currently owes you ${owed:,.2f}.")
            hint.setObjectName("Muted")
            form.addRow("", hint)

        self.memo_edit = QLineEdit()
        self.memo_edit.setPlaceholderText("Optional note")
        form.addRow("Memo", self.memo_edit)
        lay.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _accept(self):
        amount = self.amount_spin.value()
        acct_id = self.acct_combo.currentData()
        if not acct_id:
            QMessageBox.warning(self, "No Company Account",
                                "Add a bank account, card or Petty Cash first — "
                                "this action moves real company money.")
            return
        if amount < 0.005:
            self.amount_spin.setFocus()
            return
        if self.mode in ("reimburse", "convert"):
            owed = owed_to_owner(self.db)
            if amount > owed + 0.005 and QMessageBox.question(
                    self, "More Than Owed",
                    f"That is more than the ${owed:,.2f} the company owes you. "
                    "Record it anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return
        owner = owner_account(self.db, self._role)
        title = _MODES[self.mode][0]
        src = {
            "date": self.date_edit.date().toString("yyyy-MM-dd"),
            "payee": title,
            "memo": self.memo_edit.text().strip() or title,
            "amount": str(amount),
            "paid_by": self.db.owner_person_id(),
        }
        if self._owner_is_dest:
            src["account_id"] = acct_id
            self.db.save_transfer_pair(src, owner["id"])
        else:
            src["account_id"] = owner["id"]
            self.db.save_transfer_pair(src, acct_id)
        self.accept()


class FundingSourceDialog(QDialog):
    """Add an extra owner funding source, e.g. "Owner — personal Amex"."""
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Add Owner Funding Source")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        info = QLabel(
            "A funding source is a personal card or cash you use to pay company "
            "bills. It is not a company account — expenses recorded in it count "
            "as money you put in.")
        info.setWordWrap(True)
        info.setObjectName("InfoBox")
        lay.addWidget(info)
        form = QFormLayout()
        self.name_edit = QLineEdit("Owner-paid (personal card/cash)")
        form.addRow("Name *", self.name_edit)
        self.treat_combo = QComboBox()
        self.treat_combo.addItem("Capital contribution — not repaid", "equity")
        self.treat_combo.addItem("Company owes me — reimburse later", "liability")
        form.addRow("Treat spending as", self.treat_combo)
        lay.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _accept(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        self.db.save_account({"name": name, "type": self.treat_combo.currentData(),
                              "role": "owner_paid", "institution": "Owner (personal)"})
        self.accept()


class OpenBankAccountDialog(QDialog):
    """
    "I opened a business bank account": create it, optionally record the first
    deposit as a capital contribution, and make it the default for new entries.
    Past owner-paid entries are left exactly as they were recorded.
    """
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("I Opened a Business Bank Account")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        info = QLabel(
            "From now on, new entries default to <b>Paid by: Company account</b> "
            "using this account. Everything you paid personally so far stays "
            "recorded as it was — use <b>Reimburse Owner</b> later to pay "
            "yourself back from the new account.")
        info.setWordWrap(True)
        info.setObjectName("InfoBox")
        lay.addWidget(info)

        form = QFormLayout()
        form.setSpacing(10)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Chase Business Checking")
        form.addRow("Account name *", self.name_edit)
        self.type_combo = QComboBox()
        for t in ("checking", "savings", "credit card"):
            self.type_combo.addItem(t.title(), t)
        form.addRow("Type", self.type_combo)
        self.inst_edit = QLineEdit()
        self.inst_edit.setPlaceholderText("e.g. Chase")
        form.addRow("Institution", self.inst_edit)
        self.date_edit = DateField()
        self.date_edit.setDate(QDate.currentDate())
        form.addRow("Opened on", self.date_edit)
        self.deposit_spin = QDoubleSpinBox()
        self.deposit_spin.setRange(0, 9_999_999)
        self.deposit_spin.setDecimals(2)
        self.deposit_spin.setPrefix("$")
        form.addRow("Your opening deposit", self.deposit_spin)
        hint = QLabel("Recorded as a capital contribution from you. Leave at $0 "
                      "if the account starts empty.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        form.addRow("", hint)
        self.default_chk = QCheckBox("Use this account by default for new entries")
        self.default_chk.setChecked(True)
        form.addRow("", self.default_chk)
        lay.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _accept(self):
        name = self.name_edit.text().strip()
        if not name:
            self.name_edit.setFocus()
            return
        acct = self.db.save_account({"name": name, "type": self.type_combo.currentData(),
                                     "institution": self.inst_edit.text().strip(),
                                     "opening_balance": "0"})
        amount = self.deposit_spin.value()
        if amount >= 0.005:
            cap = owner_account(self.db, "owner_capital")
            self.db.save_transfer_pair({
                "date": self.date_edit.date().toString("yyyy-MM-dd"),
                "account_id": cap["id"], "payee": "Opening deposit",
                "memo": "Capital contribution — opening deposit",
                "amount": str(amount), "paid_by": self.db.owner_person_id(),
            }, acct["id"])
        if self.default_chk.isChecked():
            self.db.set_setting("default_funding_account", acct["id"])
        self.accept()
