"""Invoices (money owed to you) and bills (money you owe), with aging reports."""
import csv
from datetime import date

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QHeaderView, QTabWidget, QMessageBox, QMenu, QFileDialog,
                             QDialog, QFormLayout, QLineEdit, QTextEdit, QDoubleSpinBox,
                             QDialogButtonBox)
from PyQt6.QtCore import Qt

from ui.widgets import PageTitle, DataTable, SecondaryButton, fmt_date
from ui.styles import SUCCESS, WARNING, DANGER, MUTED, PRIMARY
from utils.invoicing import AGING_BUCKETS, aging
from utils.money import fmt

ROW_H = 44
_STATUS_COLORS = {"paid": SUCCESS, "overdue": DANGER, "partial": WARNING, "draft": MUTED,
                  "void": MUTED, "sent": PRIMARY, "open": PRIMARY}


class CompanyDialog(QDialog):
    """Name/address/email printed on invoices, plus the default sales-tax rate."""
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Invoice Settings")
        self.setMinimumWidth(460)
        f = QFormLayout(self)
        self.name = QLineEdit(db.get_setting("company_name") or db.get_setting("database_name", ""))
        f.addRow("Business name", self.name)
        self.addr = QTextEdit(db.get_setting("company_address", ""))
        self.addr.setMaximumHeight(70)
        f.addRow("Address", self.addr)
        self.email = QLineEdit(db.get_setting("company_email", ""))
        f.addRow("Email / phone", self.email)
        self.tax = QDoubleSpinBox()
        self.tax.setRange(0, 25)
        self.tax.setDecimals(3)
        self.tax.setSuffix(" %")
        self.tax.setValue(float(db.get_setting("default_sales_tax_rate", "0") or 0))
        f.addRow("Default sales tax", self.tax)
        self.start = QLineEdit(db.get_setting("invoice_start_number", "1001"))
        f.addRow("First invoice #", self.start)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        f.addRow(btns)

    def _ok(self):
        self.db.set_setting("company_name", self.name.text().strip())
        self.db.set_setting("company_address", self.addr.toPlainText().strip())
        self.db.set_setting("company_email", self.email.text().strip())
        self.db.set_setting("default_sales_tax_rate", str(self.tax.value()))
        if self.start.text().strip().isdigit():
            self.db.set_setting("invoice_start_number", self.start.text().strip())
        self.accept()


class InvoicesPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._invoices: list[dict] = []
        self._bills: list[dict] = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Invoices & Bills"))
        hdr.addStretch()
        settings_btn = SecondaryButton("Invoice Settings…")
        settings_btn.clicked.connect(lambda: CompanyDialog(self.db, self).exec())
        hdr.addWidget(settings_btn)
        bill_btn = SecondaryButton("+ Bill")
        bill_btn.clicked.connect(lambda: self._new("bill"))
        hdr.addWidget(bill_btn)
        self._add_btn = QPushButton("+ Invoice")
        self._add_btn.clicked.connect(lambda: self._new("invoice"))
        hdr.addWidget(self._add_btn)
        lay.addLayout(hdr)

        self._summary = QLabel("")
        self._summary.setObjectName("InfoBox")
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self._summary)

        self._tabs = QTabWidget()
        lay.addWidget(self._tabs, 1)
        self._inv_table = self._doc_table(["#", "Customer", "Due", "Total", "Balance",
                                           "Status", "Actions"])
        self._tabs.addTab(self._wrap(self._inv_table), "Invoices")
        self._bill_table = self._doc_table(["Ref", "Vendor", "Due", "Total", "Balance",
                                            "Status", "Actions"])
        self._tabs.addTab(self._wrap(self._bill_table), "Bills")
        self._ar_table, ar_page = self._aging_tab("invoice")
        self._tabs.addTab(ar_page, "A/R Aging")
        self._ap_table, ap_page = self._aging_tab("bill")
        self._tabs.addTab(ap_page, "A/P Aging")
        note = QLabel("Cash basis: invoices and bills reach the P&L when they're paid. Unpaid "
                      "amounts show here and as memo lines on the Balance Sheet.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)

    def _wrap(self, w) -> QWidget:
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 8, 0, 0)
        l.addWidget(w)
        return page

    def _doc_table(self, cols) -> DataTable:
        t = DataTable(cols)
        hh = t.horizontalHeader()
        t.setColumnWidth(0, 76)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c, w in ((2, 92), (3, 96), (4, 96), (5, 80)):
            t.setColumnWidth(c, w)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(6, 206)
        hh.setStretchLastSection(False)
        t.verticalHeader().setDefaultSectionSize(ROW_H)
        return t

    def _aging_tab(self, doc_type):
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 8, 0, 0)
        t = DataTable(["Contact"] + AGING_BUCKETS + ["Total"])
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 7):
            t.setColumnWidth(c, 104)
        l.addWidget(t)
        row = QHBoxLayout()
        row.addStretch()
        b = SecondaryButton("Export CSV")
        b.clicked.connect(lambda: self._export_aging(doc_type))
        row.addWidget(b)
        l.addLayout(row)
        return t, page

    # ── data ─────────────────────────────────────────────────────────────────

    def refresh(self):
        today = date.today().isoformat()
        contacts = {c["id"]: c["name"] for c in self.db.get_contacts(active_only=False)}
        self._invoices = self.db.get_invoices()
        self._bills = self.db.get_bills()
        self._fill(self._inv_table, "invoice", self._invoices, contacts, today)
        self._fill(self._bill_table, "bill", self._bills, contacts, today)
        self._fill_aging(self._ar_table, "invoice", today)
        self._fill_aging(self._ap_table, "bill", today)
        t = self.db.ar_ap_totals(today)
        self._summary.setText(
            f"<b>Owed to you:</b> {fmt(t['ar'])} "
            f"({t['ar_overdue']} overdue, {fmt(t['ar_overdue_amt'])}) &nbsp;&nbsp;·&nbsp;&nbsp; "
            f"<b>You owe:</b> {fmt(t['ap'])} ({t['ap_overdue']} overdue, "
            f"{fmt(t['ap_overdue_amt'])})")

    def _fill(self, t, doc_type, docs, contacts, today):
        t.clear_rows()
        t.setRowCount(len(docs))
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for r, d in enumerate(docs):
            s = self.db.doc_summary(doc_type, d, today)
            t.set_item(r, 0, f"#{d['number']}" if doc_type == "invoice" else (d.get("reference") or "—"),
                       bold=True)
            who = t.set_item(r, 1, contacts.get(d.get("contact_id"), "—"))
            issued = d.get("issue_date") if doc_type == "invoice" else d.get("bill_date", "")
            who.setToolTip(f"Dated {fmt_date(issued or '')}")
            t.set_item(r, 2, fmt_date(d.get("due_date", "")))
            t.set_item(r, 3, fmt(s["total"]), align=right)
            t.set_item(r, 4, fmt(s["balance"]), align=right, bold=s["balance"] > 0)
            t.set_item(r, 5, s["status"].title(), color=_STATUS_COLORS.get(s["status"]), bold=True)
            t.setCellWidget(r, 6, self._actions(doc_type, d, s))
            t.setRowHeight(r, ROW_H)

    def _actions(self, doc_type, d, s) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(6)
        e = QPushButton("Open")
        e.setObjectName("CellButton")
        e.setMinimumWidth(56)
        e.clicked.connect(lambda _, x=d: self._edit(doc_type, x))
        lay.addWidget(e)
        if s["balance"] > 0 and s["status"] not in ("void",):
            p = QPushButton("Receive" if doc_type == "invoice" else "Pay")
            p.setObjectName("CellSuccess")
            p.setMinimumWidth(74)
            p.clicked.connect(lambda _, x=d, b=s["balance"]: self._pay(doc_type, x, b))
            lay.addWidget(p)
        more = QPushButton("⋯")
        more.setObjectName("CellButton")
        more.setFixedWidth(32)
        menu = QMenu(more)
        if doc_type == "invoice":
            menu.addAction("Export PDF…", lambda x=d: self._pdf(x))
        menu.addAction("Undo last payment", lambda x=d: self._undo_payment(doc_type, x))
        menu.addSeparator()
        menu.addAction("Delete…", lambda x=d: self._delete(doc_type, x))
        more.clicked.connect(lambda _, m=menu, b=more: m.exec(b.mapToGlobal(b.rect().bottomLeft())))
        lay.addWidget(more)
        return cell

    def _fill_aging(self, t, doc_type, today):
        rep = aging([{"contact": d["contact"], "due_date": d.get("due_date", ""),
                      "balance": d["balance"]} for d in self.db.open_documents(doc_type, today)],
                    today)
        rows = sorted(rep["rows"].items())
        t.clear_rows()
        t.setRowCount(len(rows) + 1)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for r, (name, vals) in enumerate(rows + [("Total", rep["totals"])]):
            bold = name == "Total" and r == len(rows)
            t.set_item(r, 0, name, bold=bold)
            for c, v in enumerate(vals, start=1):
                t.set_item(r, c, fmt(v) if v else "—", align=right, bold=bold or c == 6,
                           color=DANGER if 2 <= c <= 5 and v else None)

    # ── actions ──────────────────────────────────────────────────────────────

    def _new(self, doc_type):
        from ui.dialogs.doc_dialog import DocDialog
        if DocDialog(self.db, doc_type, parent=self).exec():
            self._tabs.setCurrentIndex(0 if doc_type == "invoice" else 1)
            self.refresh()

    def _edit(self, doc_type, d):
        from ui.dialogs.doc_dialog import DocDialog
        if DocDialog(self.db, doc_type, d, parent=self).exec():
            self.refresh()

    def _pay(self, doc_type, d, balance):
        from ui.dialogs.doc_dialog import PaymentDialog
        dlg = PaymentDialog(self.db, doc_type, d, balance, parent=self)
        if dlg.exec():
            when, acct, amt = dlg.values()
            if not acct:
                QMessageBox.warning(self, "No Account", "Add a bank account first.")
                return
            try:
                self.db.record_doc_payment(doc_type, d["id"], when, acct, amt)
            except ValueError as e:
                QMessageBox.warning(self, "Payment", str(e))
            self.refresh()

    def _undo_payment(self, doc_type, d):
        pays = self.db.get_doc_payments(doc_type, d["id"])
        if not pays:
            QMessageBox.information(self, "No Payments", "Nothing has been paid on this yet.")
            return
        last = pays[-1]
        if QMessageBox.question(
                self, "Undo Payment",
                f"Remove the {fmt(last['amount'])} payment on {fmt_date(last['date'])} and the "
                "transactions it created?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            self.db.delete_doc_payment(last["id"])
            self.refresh()

    def _delete(self, doc_type, d):
        n = len(self.db.get_doc_payments(doc_type, d["id"]))
        extra = f"\n\nIts {n} payment(s) and their transactions will be removed too." if n else ""
        if QMessageBox.question(self, "Delete", f"Delete this {doc_type}?{extra}",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            self.db.delete_doc(doc_type, d["id"])
            self.refresh()

    def _pdf(self, d):
        from ui.dialogs.doc_dialog import export_invoice_pdf
        path = export_invoice_pdf(self.db, d, self)
        if path:
            QMessageBox.information(self, "Saved", f"Invoice saved to:\n{path}")

    def _export_aging(self, doc_type):
        today = date.today().isoformat()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Aging", f"{'ar' if doc_type == 'invoice' else 'ap'}_aging_{today}.csv",
            "CSV Files (*.csv)")
        if not path:
            return
        rep = aging([{"contact": d["contact"], "due_date": d.get("due_date", ""),
                      "balance": d["balance"]} for d in self.db.open_documents(doc_type, today)],
                    today)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Contact"] + AGING_BUCKETS + ["Total"])
            for name, vals in sorted(rep["rows"].items()):
                w.writerow([name] + [f"{v:.2f}" for v in vals])
            w.writerow(["Total"] + [f"{v:.2f}" for v in rep["totals"]])
