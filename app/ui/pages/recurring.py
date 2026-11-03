"""Recurring transactions and bills (rent, subscriptions…) with reminders."""
from datetime import date

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QHeaderView, QDialog, QFormLayout, QLineEdit, QComboBox,
                             QDoubleSpinBox, QSpinBox, QCheckBox, QDialogButtonBox, QMessageBox)
from PyQt6.QtCore import Qt, QDate

from ui.widgets import PageTitle, DataTable, SecondaryButton, DateField, FilterComboBox, fmt_date
from ui.styles import WARNING, DANGER, MUTED, SUCCESS
from utils.recurring import FREQUENCIES
from utils.money import fmt

ROW_H = 44


class RecurringDialog(QDialog):
    def __init__(self, db, rec: dict | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.rec = dict(rec or {"kind": "transaction", "frequency": "monthly", "template": {}})
        t = self.rec.get("template") or {}
        self.setWindowTitle("Edit Recurring Item" if rec else "New Recurring Item")
        self.setMinimumWidth(500)
        f = QFormLayout(self)
        f.setSpacing(9)
        self.name = QLineEdit(self.rec.get("name", ""))
        self.name.setPlaceholderText("e.g. Office rent, Adobe subscription")
        f.addRow("Name *", self.name)
        self.kind = QComboBox()
        self.kind.addItem("Transaction (money already moves automatically)", "transaction")
        if db.is_business():
            self.kind.addItem("Bill to pay (shows in Invoices & Bills)", "bill")
        self.kind.setCurrentIndex(max(self.kind.findData(self.rec.get("kind")), 0))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        f.addRow("Creates a", self.kind)
        self.freq = QComboBox()
        for k, label in FREQUENCIES.items():
            self.freq.addItem(label, k)
        self.freq.setCurrentIndex(max(self.freq.findData(self.rec.get("frequency")), 0))
        f.addRow("Repeats", self.freq)
        self.next = DateField()
        if self.rec.get("next_date"):
            self.next.setDate(QDate.fromString(self.rec["next_date"], "yyyy-MM-dd"))
        f.addRow("Next date", self.next)
        self.has_end = QCheckBox("Stop after")
        self.end = DateField()
        if self.rec.get("end_date"):
            self.has_end.setChecked(True)
            self.end.setDate(QDate.fromString(self.rec["end_date"], "yyyy-MM-dd"))
        er = QHBoxLayout()
        er.addWidget(self.has_end)
        er.addWidget(self.end, 1)
        f.addRow("End", er)
        self.acct = QComboBox()
        for a in db.get_accounts():
            self.acct.addItem(a["name"], a["id"])
        self.acct.setCurrentIndex(max(self.acct.findData(t.get("account_id")), 0))
        f.addRow("Account", self.acct)
        self.payee = QLineEdit(t.get("payee", ""))
        f.addRow("Payee", self.payee)
        self.contact = FilterComboBox(placeholder="Vendor (optional)")
        for c in db.get_contacts():
            self.contact.add_option(c["name"], c["id"])
        self.contact.select_by_data(t.get("contact_id", ""))
        f.addRow("Vendor / customer", self.contact)
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 9_999_999)
        self.amount.setPrefix("$")
        self.amount.setValue(float(t.get("amount") or 0))
        f.addRow("Amount", self.amount)
        self.cat = FilterComboBox(placeholder="Category")
        cats = db.get_categories_for_book(keep_ids=[t.get("category_id", "")])
        for c in cats:
            if not c.get("parent_id"):
                self.cat.add_option(c["name"], c["id"])
                for s in cats:
                    if s.get("parent_id") == c["id"]:
                        self.cat.add_option(f"{c['name']} → {s['name']}", s["id"])
        self.cat.select_by_data(t.get("category_id", ""))
        f.addRow("Category", self.cat)
        self.memo = QLineEdit(t.get("memo", ""))
        f.addRow("Memo", self.memo)
        self.auto = QCheckBox("Add it automatically when it comes due")
        self.auto.setChecked(bool(self.rec.get("auto_post")))
        f.addRow("", self.auto)
        self.remind = QSpinBox()
        self.remind.setRange(0, 60)
        self.remind.setSuffix(" days before")
        self.remind.setValue(int(self.rec.get("remind_days") or 7))
        f.addRow("Remind me", self.remind)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        f.addRow(btns)
        self._kind_changed()

    def _kind_changed(self, *_):
        bill = self.kind.currentData() == "bill"
        for w in (self.acct, self.payee):
            w.setEnabled(not bill)

    def _ok(self):
        if not self.name.text().strip() or self.amount.value() <= 0:
            QMessageBox.warning(self, "Recurring", "Enter a name and an amount.")
            return
        self.accept()

    def get_data(self) -> dict:
        d = dict(self.rec)
        d.update({"name": self.name.text().strip(), "kind": self.kind.currentData(),
                  "frequency": self.freq.currentData(),
                  "next_date": self.next.date().toString("yyyy-MM-dd"),
                  "end_date": self.end.date().toString("yyyy-MM-dd") if self.has_end.isChecked() else "",
                  "auto_post": "1" if self.auto.isChecked() else "0",
                  "remind_days": self.remind.value(),
                  "template": {"account_id": self.acct.currentData() or "",
                               "payee": self.payee.text().strip(),
                               "contact_id": self.contact.current_data() or "",
                               "amount": self.amount.value(),
                               "category_id": self.cat.current_data() or "",
                               "memo": self.memo.text().strip()}})
        if not self.rec.get("id"):
            d["anchor_day"] = 0
        return d


class RecurringPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._rows: list[dict] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Recurring"))
        hdr.addStretch()
        self._add_btn = QPushButton("+ Recurring Item")
        self._add_btn.clicked.connect(self._add)
        hdr.addWidget(self._add_btn)
        lay.addLayout(hdr)
        self._banner = QLabel("")
        self._banner.setObjectName("InfoBox")
        self._banner.setTextFormat(Qt.TextFormat.RichText)
        self._banner.setWordWrap(True)
        lay.addWidget(self._banner)
        self._table = DataTable(["Name", "Next", "Repeats", "Amount", "Mode", "Actions"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 100), (2, 120), (3, 104), (4, 90)):
            self._table.setColumnWidth(c, w)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(5, 236)
        hh.setStretchLastSection(False)
        self._table.verticalHeader().setDefaultSectionSize(ROW_H)
        self._table.doubleClicked.connect(lambda i: self._edit(self._rows[i.row()]))
        lay.addWidget(self._table, 1)
        note = QLabel("Automatic items are added when FinanceBook opens on or after their date. "
                      "Others show a reminder here and on the Dashboard — click Post when it happens.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)

    def refresh(self):
        today = date.today().isoformat()
        self._rows = self.db.get_recurring()
        due = self.db.upcoming_recurring(today)
        self._banner.setText(
            ("<b>Coming up:</b> " + ", ".join(f"{r['name']} ({fmt_date(r['next_date'])})" for r in due))
            if due else "Nothing due in the next few days.")
        t = self._table
        t.clear_rows()
        t.setRowCount(len(self._rows))
        for r, rec in enumerate(self._rows):
            t.set_item(r, 0, rec["name"], bold=True, color=None if rec["active"] else MUTED)
            nd = rec.get("next_date", "")
            t.set_item(r, 1, fmt_date(nd) if rec["active"] else "ended",
                       color=DANGER if rec["active"] and nd < today else None)
            t.set_item(r, 2, FREQUENCIES.get(rec["frequency"], rec["frequency"]))
            t.set_item(r, 3, fmt(rec["template"].get("amount") or 0),
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.set_item(r, 4, ("Auto" if rec["auto_post"] else "Reminder") +
                       (" · bill" if rec.get("kind") == "bill" else ""), color=MUTED)
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(6, 5, 6, 5)
            cl.setSpacing(6)
            if rec["active"]:
                post = QPushButton("Post")
                post.setObjectName("CellSuccess")
                post.clicked.connect(lambda _, x=rec: self._post(x))
                cl.addWidget(post)
                skip = QPushButton("Skip")
                skip.setObjectName("CellButton")
                skip.clicked.connect(lambda _, x=rec: self._skip(x))
                cl.addWidget(skip)
            e = QPushButton("Edit")
            e.setObjectName("CellButton")
            e.clicked.connect(lambda _, x=rec: self._edit(x))
            cl.addWidget(e)
            d = QPushButton("Delete")
            d.setObjectName("CellDanger")
            d.clicked.connect(lambda _, x=rec: self._delete(x))
            cl.addWidget(d)
            t.setCellWidget(r, 5, cell)
            t.setRowHeight(r, ROW_H)

    def _add(self):
        dlg = RecurringDialog(self.db, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_recurring(dlg.get_data())
            self.refresh()

    def _edit(self, rec):
        dlg = RecurringDialog(self.db, rec, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_recurring(dlg.get_data())
            self.refresh()

    def _post(self, rec):
        self.db.post_recurring(self.db.get_recurring(rec["id"]))
        self.refresh()

    def _skip(self, rec):
        self.db.skip_recurring(rec)
        self.refresh()

    def _delete(self, rec):
        if QMessageBox.question(self, "Delete", f"Stop and delete “{rec['name']}”? Entries it "
                                "already made stay.", QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.db.delete_recurring(rec["id"])
            self.refresh()
