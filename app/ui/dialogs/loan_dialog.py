"""Create / Edit loan dialog."""
from datetime import date
from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                              QDoubleSpinBox, QSpinBox, QDialogButtonBox,
                              QVBoxLayout, QDateEdit, QLabel, QHBoxLayout)
from PyQt6.QtCore import QDate, Qt
from utils.loan import calc_monthly_payment


class LoanDialog(QDialog):
    def __init__(self, db, loan: dict = None, parent=None):
        super().__init__(parent)
        self.db   = db
        self.loan = loan or {}
        self.setWindowTitle("Edit Loan" if loan else "Add Loan")
        self.setMinimumWidth(480)
        self._build()

    def _build(self):
        lay  = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        # Name
        self.name_edit = QLineEdit(self.loan.get("name", ""))
        self.name_edit.setPlaceholderText("e.g. Mortgage – Land & Building")
        form.addRow("Loan Name *", self.name_edit)

        # Lender
        self.lender_edit = QLineEdit(self.loan.get("lender", ""))
        self.lender_edit.setPlaceholderText("e.g. Wells Fargo")
        form.addRow("Lender", self.lender_edit)

        # Original principal
        self.principal_spin = QDoubleSpinBox()
        self.principal_spin.setRange(0, 99_999_999)
        self.principal_spin.setDecimals(2)
        self.principal_spin.setPrefix("$")
        self.principal_spin.setSingleStep(1000)
        self.principal_spin.setValue(float(self.loan.get("original_principal") or 0))
        self.principal_spin.valueChanged.connect(self._refresh_payment)
        form.addRow("Original Principal *", self.principal_spin)

        # Annual interest rate
        self.rate_spin = QDoubleSpinBox()
        self.rate_spin.setRange(0, 99)
        self.rate_spin.setDecimals(4)
        self.rate_spin.setSuffix(" %")
        self.rate_spin.setSingleStep(0.125)
        self.rate_spin.setValue(float(self.loan.get("annual_rate") or 0) * 100)
        self.rate_spin.valueChanged.connect(self._refresh_payment)
        form.addRow("Annual Interest Rate *", self.rate_spin)

        # Term
        self.term_spin = QSpinBox()
        self.term_spin.setRange(1, 600)
        self.term_spin.setSuffix(" months")
        self.term_spin.setValue(int(self.loan.get("term_months") or 360))
        self.term_spin.valueChanged.connect(self._refresh_payment)
        term_row = QHBoxLayout()
        term_row.addWidget(self.term_spin)
        self._term_hint = QLabel("(360 = 30 yr, 180 = 15 yr, 84 = 7 yr, 60 = 5 yr)")
        self._term_hint.setObjectName("Muted")
        term_row.addWidget(self._term_hint)
        term_row.addStretch()
        form.addRow("Term *", term_row)

        # First payment date
        self.start_edit = QDateEdit()
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("yyyy-MM-dd")
        start_str = self.loan.get("start_date") or date.today().isoformat()
        self.start_edit.setDate(QDate.fromString(start_str, "yyyy-MM-dd"))
        form.addRow("First Payment Date *", self.start_edit)

        # Calculated / override payment
        self.payment_spin = QDoubleSpinBox()
        self.payment_spin.setRange(0, 999_999)
        self.payment_spin.setDecimals(2)
        self.payment_spin.setPrefix("$")
        self.payment_spin.setSingleStep(1)
        stored_pmt = float(self.loan.get("payment_amount") or 0)
        self.payment_spin.setValue(stored_pmt)
        pmt_note = QLabel("Leave $0.00 to auto-calculate from principal / rate / term")
        pmt_note.setObjectName("Muted")
        form.addRow("Monthly Payment", self.payment_spin)
        form.addRow("", pmt_note)

        # Calculated payment preview
        self._calc_label = QLabel("")
        self._calc_label.setObjectName("Muted")
        form.addRow("", self._calc_label)
        self._refresh_payment()

        # Linked liability account (optional)
        self.acct_combo = QComboBox()
        self.acct_combo.addItem("— None —", "")
        for a in self.db.get_accounts():
            if a.get("type") in ("loan", "other", "checking", "savings"):
                self.acct_combo.addItem(a["name"], a["id"])
        cur_acct = self.loan.get("account_id", "")
        for i in range(self.acct_combo.count()):
            if self.acct_combo.itemData(i) == cur_acct:
                self.acct_combo.setCurrentIndex(i)
                break
        form.addRow("Linked Account", self.acct_combo)

        # Interest expense category
        self.cat_combo = QComboBox()
        self.cat_combo.addItem("— None —", "")
        cats = self.db.get_categories()
        roots = [c for c in cats if not c.get("parent_id")]
        for cat in roots:
            self.cat_combo.addItem(cat["name"], cat["id"])
            for sub in cats:
                if sub.get("parent_id") == cat["id"]:
                    self.cat_combo.addItem(f"    ↳ {sub['name']}", sub["id"])
        cur_cat = self.loan.get("interest_category_id", "")
        for i in range(self.cat_combo.count()):
            if self.cat_combo.itemData(i) == cur_cat:
                self.cat_combo.setCurrentIndex(i)
                break
        form.addRow("Interest Expense Category", self.cat_combo)

        lay.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _refresh_payment(self):
        p   = self.principal_spin.value()
        r   = self.rate_spin.value() / 100
        n   = self.term_spin.value()
        pmt = calc_monthly_payment(p, r, n)
        self._calc_label.setText(f"Calculated monthly payment: ${pmt:,.2f}")

    def _accept(self):
        if not self.name_edit.text().strip():
            self.name_edit.setFocus()
            return
        if self.principal_spin.value() <= 0:
            self.principal_spin.setFocus()
            return
        self.accept()

    def get_data(self) -> dict:
        data = dict(self.loan)
        annual_rate = self.rate_spin.value() / 100
        data.update({
            "name":                 self.name_edit.text().strip(),
            "lender":               self.lender_edit.text().strip(),
            "original_principal":   str(self.principal_spin.value()),
            "annual_rate":          str(annual_rate),
            "term_months":          str(self.term_spin.value()),
            "start_date":           self.start_edit.date().toString("yyyy-MM-dd"),
            "payment_amount":       str(self.payment_spin.value()),
            "account_id":           self.acct_combo.currentData() or "",
            "interest_category_id": self.cat_combo.currentData() or "",
        })
        return data
