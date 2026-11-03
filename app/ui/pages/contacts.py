"""Vendors & customers."""
from datetime import date

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QHeaderView, QComboBox, QLineEdit, QDialog, QFormLayout,
                             QSpinBox, QCheckBox, QTextEdit, QDialogButtonBox, QMessageBox)
from PyQt6.QtCore import Qt

from ui.widgets import PageTitle, DataTable, SecondaryButton, FilterComboBox
from ui.styles import MUTED
from utils.money import money_sum, fmt

ROW_H = 44
KINDS = [("vendor", "Vendor"), ("customer", "Customer"), ("both", "Vendor & customer")]


def mask_tax_id(v: str) -> str:
    digits = [c for c in (v or "") if c.isalnum()]
    return ("•••-••-" + "".join(digits[-4:])) if len(digits) >= 4 else ("•" * len(digits))


class ContactDialog(QDialog):
    def __init__(self, db, contact: dict | None = None, parent=None, kind: str = "vendor"):
        super().__init__(parent)
        self.db = db
        self.contact = dict(contact or {"kind": kind})
        self.setWindowTitle("Edit Contact" if contact else "Add Contact")
        self.setMinimumWidth(520)
        c = self.contact
        lay = QVBoxLayout(self)
        f = QFormLayout()
        f.setSpacing(9)
        self.name = QLineEdit(c.get("name", ""))
        f.addRow("Name *", self.name)
        self.kind = QComboBox()
        for k, label in KINDS:
            self.kind.addItem(label, k)
        self.kind.setCurrentIndex(max(self.kind.findData(c.get("kind", "vendor")), 0))
        f.addRow("Type", self.kind)
        self.company = QLineEdit(c.get("company", ""))
        f.addRow("Company", self.company)
        self.email = QLineEdit(c.get("email", ""))
        f.addRow("Email", self.email)
        self.phone = QLineEdit(c.get("phone", ""))
        f.addRow("Phone", self.phone)
        self.address = QTextEdit(c.get("address", ""))
        self.address.setMaximumHeight(64)
        f.addRow("Address", self.address)
        tax_row = QHBoxLayout()
        self.tax_id = QLineEdit(c.get("tax_id", ""))
        self.tax_id.setEchoMode(QLineEdit.EchoMode.Password)
        self.tax_id.setPlaceholderText("EIN or SSN (for 1099s)")
        tax_row.addWidget(self.tax_id, 1)
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.tax_id.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        tax_row.addWidget(show)
        f.addRow("Tax ID", tax_row)
        self.is_1099 = QCheckBox("Contractor — may need a 1099-NEC")
        self.is_1099.setChecked(str(c.get("is_1099", "0")) == "1")
        f.addRow("", self.is_1099)
        self.cat = FilterComboBox(placeholder="Default category (optional)")
        for cat in db.get_categories():
            parent_cat = next((p for p in db.get_categories() if p["id"] == cat.get("parent_id")), None)
            self.cat.add_option(f"{parent_cat['name']} → {cat['name']}" if parent_cat else cat["name"],
                                cat["id"])
        self.cat.select_by_data(c.get("default_category_id", ""))
        f.addRow("Default category", self.cat)
        self.terms = QSpinBox()
        self.terms.setRange(0, 365)
        self.terms.setSuffix(" days")
        self.terms.setValue(int(c.get("terms_days") or 30))
        f.addRow("Payment terms", self.terms)
        self.match = QLineEdit(c.get("match_text", ""))
        self.match.setPlaceholderText("Text that appears in bank payees, e.g. AMZN MKTP")
        f.addRow("Bank payee match", self.match)
        hint = QLabel("Imported transactions whose payee contains this text (or the name) are "
                      "linked to this contact and get its default category when uncategorized.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        f.addRow("", hint)
        self.notes = QLineEdit(c.get("notes", ""))
        f.addRow("Notes", self.notes)
        lay.addLayout(f)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _ok(self):
        if not self.name.text().strip():
            self.name.setFocus()
            return
        self.accept()

    def get_data(self) -> dict:
        d = dict(self.contact)
        d.update({"name": self.name.text().strip(), "kind": self.kind.currentData(),
                  "company": self.company.text().strip(), "email": self.email.text().strip(),
                  "phone": self.phone.text().strip(),
                  "address": self.address.toPlainText().strip(),
                  "tax_id": self.tax_id.text().strip(),
                  "is_1099": "1" if self.is_1099.isChecked() else "0",
                  "default_category_id": self.cat.current_data() or "",
                  "terms_days": self.terms.value(), "match_text": self.match.text().strip(),
                  "notes": self.notes.text().strip()})
        d.setdefault("active", "1")
        return d


class ContactsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._rows: list[dict] = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Contacts"))
        hdr.addStretch()
        self._kind = QComboBox()
        self._kind.addItem("Everyone", "")
        self._kind.addItem("Vendors", "vendor")
        self._kind.addItem("Customers", "customer")
        self._kind.currentIndexChanged.connect(self.refresh)
        hdr.addWidget(self._kind)
        apply_btn = SecondaryButton("Link Past Transactions")
        apply_btn.setToolTip("Match existing transactions' payees to your contacts")
        apply_btn.clicked.connect(self._link_existing)
        hdr.addWidget(apply_btn)
        self._add_btn = QPushButton("+ Add Contact")
        self._add_btn.clicked.connect(self._add)
        hdr.addWidget(self._add_btn)
        lay.addLayout(hdr)

        self._table = DataTable(["Name", "Type", "Email / phone", "Terms", "1099",
                                 "Paid / received this year", "Actions"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 120), (3, 74), (4, 56), (5, 180)):
            self._table.setColumnWidth(c, w)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(6, 150)
        hh.setStretchLastSection(False)
        self._table.verticalHeader().setDefaultSectionSize(ROW_H)
        self._table.doubleClicked.connect(lambda i: self._edit(self._rows[i.row()]))
        lay.addWidget(self._table, 1)
        self._summary = QLabel("")
        self._summary.setObjectName("Muted")
        lay.addWidget(self._summary)

    def refresh(self):
        self._rows = self.db.get_contacts(self._kind.currentData() or "")
        year_start = f"{date.today().year}-01-01"
        t = self._table
        t.clear_rows()
        t.setRowCount(len(self._rows))
        labels = dict(KINDS)
        for r, c in enumerate(self._rows):
            t.set_item(r, 0, c["name"], bold=True)
            t.set_item(r, 1, labels.get(c.get("kind"), ""))
            t.set_item(r, 2, " · ".join(x for x in (c.get("email"), c.get("phone")) if x) or "—",
                       color=MUTED)
            t.set_item(r, 3, f"{c.get('terms_days') or 0} d")
            t.set_item(r, 4, "✓" if str(c.get("is_1099")) == "1" else "",
                       align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            act = self.db.contact_activity(c["id"], year_start)
            total = money_sum(float(x.get("amount") or 0) for x in act
                              if str(x.get("is_transfer", 0)) != "1")
            t.money_item(r, 5, float(total))
            t.setCellWidget(r, 6, self._actions(c))
            t.setRowHeight(r, ROW_H)
        self._summary.setText(f"{len(self._rows)} contacts · amounts are this year's money in (+) "
                              "and out (−) with each contact")

    def _actions(self, c) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(6)
        e = QPushButton("Edit")
        e.setObjectName("CellButton")
        e.clicked.connect(lambda _, x=c: self._edit(x))
        lay.addWidget(e)
        d = QPushButton("Hide")
        d.setObjectName("CellDanger")
        d.setToolTip("Hide this contact (history is kept)")
        d.clicked.connect(lambda _, x=c: self._hide(x))
        lay.addWidget(d)
        return cell

    def _add(self):
        dlg = ContactDialog(self.db, parent=self, kind=self._kind.currentData() or "vendor")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_contact(dlg.get_data())
            self.refresh()

    def _edit(self, c):
        dlg = ContactDialog(self.db, contact=c, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_contact(dlg.get_data())
            self.refresh()

    def _hide(self, c):
        if QMessageBox.question(self, "Hide Contact", f"Hide “{c['name']}”? Their history stays.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            self.db.save_contact(dict(c, active="0"))
            self.refresh()

    def _link_existing(self):
        txns = [t for t in self.db.get_transactions() if not t.get("contact_id")]
        before = {t["id"]: t.get("category_id", "") for t in txns}
        self.db.apply_contacts(txns)
        n = 0
        for t in txns:
            if t.get("contact_id"):
                # Never re-categorize what the user already categorized.
                if before[t["id"]]:
                    t["category_id"] = before[t["id"]]
                self.db.save_transaction(t)
                n += 1
        QMessageBox.information(self, "Linked", f"Linked {n} transaction(s) to contacts.")
        self.refresh()
