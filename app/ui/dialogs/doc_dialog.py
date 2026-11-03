"""Invoice / bill editor, payment dialog and invoice PDF export (Qt only)."""
import html
from datetime import date

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
                             QLineEdit, QComboBox, QDoubleSpinBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QDialogButtonBox, QMessageBox,
                             QTextEdit, QAbstractItemView)
from PyQt6.QtCore import Qt, QDate, QMarginsF, QSizeF
from PyQt6.QtGui import QTextDocument, QPdfWriter, QPageSize, QPageLayout

from ui.widgets import DateField, FilterComboBox, SecondaryButton
from utils.invoicing import doc_totals, due_date_for
from utils.money import D, cents, fmt

STATUSES = {"invoice": [("draft", "Draft"), ("sent", "Sent"), ("void", "Void")],
            "bill": [("open", "Open"), ("void", "Void")]}


class DocDialog(QDialog):
    def __init__(self, db, doc_type: str, doc: dict | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.doc_type = doc_type
        self.doc = dict(doc or {})
        inv = doc_type == "invoice"
        self.setWindowTitle(("Invoice" if inv else "Bill") + (" — edit" if doc else " — new"))
        self.resize(980, 640)
        lay = QVBoxLayout(self)

        top = QFormLayout()
        top.setSpacing(8)
        self.contact = FilterComboBox(placeholder="Customer" if inv else "Vendor")
        for c in db.get_contacts("customer" if inv else "vendor"):
            self.contact.add_option(c["name"], c["id"])
        self.contact.select_by_data(self.doc.get("contact_id", ""))
        self.contact.currentIndexChanged.connect(self._contact_changed)
        crow = QHBoxLayout()
        crow.addWidget(self.contact, 1)
        new_c = SecondaryButton("+ New")
        new_c.clicked.connect(self._new_contact)
        crow.addWidget(new_c)
        top.addRow("Customer *" if inv else "Vendor *", crow)
        if inv:
            num = self.doc.get("number") or db.next_invoice_number()
            self.number_lbl = QLabel(f"#{num}")
            self.number_lbl.setStyleSheet("font-weight:700;")
            top.addRow("Invoice number", self.number_lbl)
        else:
            self.ref = QLineEdit(self.doc.get("reference", ""))
            self.ref.setPlaceholderText("Vendor's bill / invoice number")
            top.addRow("Reference", self.ref)
        dates = QHBoxLayout()
        self.issue = DateField()
        self.issue.setDate(QDate.fromString(self.doc.get("issue_date" if inv else "bill_date")
                                            or date.today().isoformat(), "yyyy-MM-dd"))
        self.issue.dateChanged.connect(self._contact_changed)
        dates.addWidget(QLabel("Date"))
        dates.addWidget(self.issue)
        self.due = DateField()
        self.due.setDate(QDate.fromString(self.doc.get("due_date") or date.today().isoformat(),
                                          "yyyy-MM-dd"))
        dates.addWidget(QLabel("Due"))
        dates.addWidget(self.due)
        dates.addStretch()
        top.addRow("Dates", dates)
        self.status = QComboBox()
        for k, label in STATUSES[doc_type]:
            self.status.addItem(label, k)
        self.status.setCurrentIndex(max(self.status.findData(self.doc.get("status") or
                                                             ("draft" if inv else "open")), 0))
        top.addRow("Status", self.status)
        lay.addLayout(top)

        cols = ["Description", "Qty", "Unit price", "Tax %", "Amount", "Category"] if inv else \
            ["Description", "Qty", "Unit price", "Amount", "Category"]
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i, w in enumerate([None, 60, 96, 70, 100, 230] if inv else [None, 60, 96, 100, 230]):
            if w:
                self.table.setColumnWidth(i, w)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._recalc)
        lay.addWidget(self.table, 1)

        tools = QHBoxLayout()
        add = SecondaryButton("+ Line")
        add.clicked.connect(lambda: self._add_line({}))
        rem = SecondaryButton("Remove Line")
        rem.clicked.connect(self._remove_line)
        tools.addWidget(add)
        tools.addWidget(rem)
        tools.addStretch()
        self.totals = QLabel("")
        self.totals.setStyleSheet("font-weight:700; font-size:14px;")
        tools.addWidget(self.totals)
        lay.addLayout(tools)

        self.notes = QTextEdit(self.doc.get("notes", ""))
        self.notes.setMaximumHeight(60)
        self.notes.setPlaceholderText("Notes / payment instructions (printed on the invoice)"
                                      if inv else "Notes")
        lay.addWidget(self.notes)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        if inv:
            pdf = btns.addButton("Save && Export PDF…", QDialogButtonBox.ButtonRole.ActionRole)
            pdf.clicked.connect(lambda: self._save(export=True))
        lay.addWidget(btns)

        lines = db.get_doc_lines(doc_type, self.doc["id"]) if self.doc.get("id") else []
        for ln in lines or [{}]:
            self._add_line(ln)
        if not self.doc.get("id"):
            self._contact_changed()

    # ── lines ────────────────────────────────────────────────────────────────

    def _cat_combo(self) -> FilterComboBox:
        want = "income" if self.doc_type == "invoice" else None
        combo = FilterComboBox(placeholder="Category")
        cats = self.db.get_categories()
        for c in cats:
            if c.get("parent_id"):
                continue
            if want and c.get("type") != want:
                continue
            if not want and c.get("type") == "income":
                continue
            combo.add_option(c["name"], c["id"])
            for s in cats:
                if s.get("parent_id") == c["id"]:
                    combo.add_option(f"{c['name']} → {s['name']}", s["id"])
        return combo

    def _add_line(self, ln: dict):
        inv = self.doc_type == "invoice"
        self.table.blockSignals(True)
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(ln.get("description", "")))
        vals = [("qty", "{:g}", 1), ("unit_price", "{:.2f}", 0)]
        if inv:
            vals.append(("tax_rate", "{:g}", float(self.db.get_setting("default_sales_tax_rate", "0") or 0)))
        for i, (k, f, default) in enumerate(vals, start=1):
            it = QTableWidgetItem(f.format(float(ln.get(k, default) or 0)))
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(r, i, it)
        amt = QTableWidgetItem("0.00")
        amt.setFlags(amt.flags() & ~Qt.ItemFlag.ItemIsEditable)
        amt.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(r, len(vals) + 1, amt)
        cat = self._cat_combo()
        default_cat = ln.get("category_id") or (self.db.get_contact(
            self.contact.current_data() or "") or {}).get("default_category_id", "")
        cat.select_by_data(default_cat)
        self.table.setCellWidget(r, len(vals) + 2, cat)
        self.table.blockSignals(False)
        self._recalc()

    def _remove_line(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows or [self.table.rowCount() - 1]:
            if r >= 0:
                self.table.removeRow(r)
        self._recalc()

    def lines(self) -> list[dict]:
        inv = self.doc_type == "invoice"
        out = []
        for r in range(self.table.rowCount()):
            desc = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            qty = D(self.table.item(r, 1).text() if self.table.item(r, 1) else 1) or D(1)
            unit = D(self.table.item(r, 2).text() if self.table.item(r, 2) else 0)
            tax = D(self.table.item(r, 3).text()) if inv and self.table.item(r, 3) else D(0)
            cat = self.table.cellWidget(r, 5 if inv else 4)
            if not desc and not unit:
                continue
            out.append({"description": desc, "qty": float(qty), "unit_price": float(unit),
                        "tax_rate": float(tax), "category_id": cat.current_data() if cat else ""})
        return out

    def _recalc(self, *_):
        inv = self.doc_type == "invoice"
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            qty = D(self.table.item(r, 1).text() if self.table.item(r, 1) else 1)
            unit = D(self.table.item(r, 2).text() if self.table.item(r, 2) else 0)
            col = 4 if inv else 3
            if self.table.item(r, col):
                self.table.item(r, col).setText(f"{cents(qty * unit):,.2f}")
        self.table.blockSignals(False)
        t = doc_totals(self.lines() if inv else [dict(l, tax_rate=0) for l in self.lines()])
        txt = f"Subtotal {fmt(t['subtotal'])}"
        if inv:
            txt += f"   Tax {fmt(t['tax'])}"
        self.totals.setText(txt + f"   Total {fmt(t['total'])}")

    # ── header ───────────────────────────────────────────────────────────────

    def _contact_changed(self, *_):
        c = self.db.get_contact(self.contact.current_data() or "")
        if c is not None and not self.doc.get("id"):
            self.due.setDate(QDate.fromString(
                due_date_for(self.issue.date().toString("yyyy-MM-dd"), c.get("terms_days") or 30),
                "yyyy-MM-dd"))

    def _new_contact(self):
        from ui.pages.contacts import ContactDialog
        dlg = ContactDialog(self.db, parent=self,
                            kind="customer" if self.doc_type == "invoice" else "vendor")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            c = self.db.save_contact(dlg.get_data())
            self.contact.add_option(c["name"], c["id"])
            self.contact.select_by_data(c["id"])

    def _save(self, export: bool = False):
        if not self.contact.current_data():
            QMessageBox.warning(self, "Contact Needed", "Choose who this is for (or add them).")
            return
        lines = self.lines()
        if not lines:
            QMessageBox.warning(self, "No Lines", "Add at least one line.")
            return
        inv = self.doc_type == "invoice"
        d = dict(self.doc)
        d.update({"contact_id": self.contact.current_data(),
                  ("issue_date" if inv else "bill_date"): self.issue.date().toString("yyyy-MM-dd"),
                  "due_date": self.due.date().toString("yyyy-MM-dd"),
                  "status": self.status.currentData(),
                  "notes": self.notes.toPlainText().strip()})
        if not inv:
            d["reference"] = self.ref.text().strip()
        self.doc = self.db.save_doc(self.doc_type, d, lines)
        if export:
            export_invoice_pdf(self.db, self.doc, self)
        self.accept()


class PaymentDialog(QDialog):
    def __init__(self, db, doc_type: str, doc: dict, balance, parent=None):
        super().__init__(parent)
        inv = doc_type == "invoice"
        self.setWindowTitle("Receive Payment" if inv else "Pay Bill")
        self.setMinimumWidth(420)
        f = QFormLayout(self)
        f.setSpacing(9)
        self.date = DateField()
        f.addRow("Date", self.date)
        self.acct = QComboBox()
        for a in db.get_accounts():
            if a.get("type") not in ("equity",) and a.get("role") not in ("sales_tax_payable",):
                self.acct.addItem(a["name"], a["id"])
        pref = db.get_setting("default_funding_account")
        if self.acct.findData(pref) >= 0:
            self.acct.setCurrentIndex(self.acct.findData(pref))
        f.addRow("Deposited to" if inv else "Paid from", self.acct)
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 99_999_999)
        self.amount.setDecimals(2)
        self.amount.setPrefix("$")
        self.amount.setValue(float(balance))
        f.addRow("Amount", self.amount)
        note = QLabel(("Creates the income transaction (split by the invoice's categories); any "
                       "sales tax goes to Sales Tax Payable." if inv else
                       "Creates the expense transaction split by the bill's categories."))
        note.setObjectName("Muted")
        note.setWordWrap(True)
        f.addRow(note)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        f.addRow(btns)

    def values(self):
        return (self.date.date().toString("yyyy-MM-dd"), self.acct.currentData(),
                self.amount.value())


def invoice_html(db, doc: dict) -> str:
    s = db.doc_summary("invoice", doc)
    c = db.get_contact(doc.get("contact_id", "")) or {}
    biz = html.escape(db.get_setting("company_name") or db.get_setting("database_name", ""))
    addr = html.escape(db.get_setting("company_address", "")).replace("\n", "<br>")
    email = html.escape(db.get_setting("company_email", ""))
    rows = "".join(
        f"<tr><td>{html.escape(l['description'])}</td><td align='right'>{l['qty']:g}</td>"
        f"<td align='right'>{fmt(l['unit_price'])}</td>"
        f"<td align='right'>{l['tax_rate']:g}%</td>"
        f"<td align='right'>{fmt(cents(D(l['qty']) * D(l['unit_price'])))}</td></tr>"
        for l in s["lines"])
    paid_row = (f"<tr><td colspan='4' align='right'>Paid</td><td align='right'>({fmt(s['paid'])})</td></tr>"
                if s["paid"] else "")
    return f"""
    <html><body style="font-family:'Segoe UI',Arial; font-size:10pt; color:#0f1b3d;">
    <table width="100%"><tr>
      <td><span style="font-size:20pt; font-weight:700;">{biz}</span><br>{addr}<br>{email}</td>
      <td align="right"><span style="font-size:22pt; color:#2f6bff; font-weight:700;">INVOICE</span><br>
          <b>#{doc.get('number')}</b><br>Date: {doc.get('issue_date', '')}<br>
          Due: {doc.get('due_date', '')}</td>
    </tr></table>
    <p style="margin-top:18px;"><b>Bill to</b><br>{html.escape(c.get('name', ''))}<br>
       {html.escape(c.get('company', ''))}<br>
       {html.escape(c.get('address', '')).replace(chr(10), '<br>')}<br>
       {html.escape(c.get('email', ''))}</p>
    <table width="100%" cellspacing="0" cellpadding="6" style="margin-top:12px; border-collapse:collapse;">
      <tr style="background:#eef4ff;"><th align="left">Description</th><th align="right">Qty</th>
      <th align="right">Unit price</th><th align="right">Tax</th><th align="right">Amount</th></tr>
      {rows}
      <tr><td colspan="4" align="right">Subtotal</td><td align="right">{fmt(s['subtotal'])}</td></tr>
      <tr><td colspan="4" align="right">Tax</td><td align="right">{fmt(s['tax'])}</td></tr>
      <tr><td colspan="4" align="right"><b>Total</b></td><td align="right"><b>{fmt(s['total'])}</b></td></tr>
      {paid_row}
      <tr><td colspan="4" align="right"><b>Balance due</b></td>
          <td align="right"><b>{fmt(s['balance'])}</b></td></tr>
    </table>
    <p style="margin-top:24px;">{html.escape(doc.get('notes', '')).replace(chr(10), '<br>')}</p>
    </body></html>"""


def export_invoice_pdf(db, doc: dict, parent=None, path: str = "") -> str:
    """Render an invoice to PDF with QTextDocument + QPdfWriter (no extra deps)."""
    if not path:
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(parent, "Save Invoice PDF",
                                              f"Invoice-{doc.get('number')}.pdf", "PDF (*.pdf)")
        if not path:
            return ""
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.Letter))
    writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)
    writer.setResolution(96)
    writer.setTitle(f"Invoice {doc.get('number')}")
    td = QTextDocument()
    td.setHtml(invoice_html(db, doc))
    td.setPageSize(QSizeF(writer.width(), writer.height()))
    td.print(writer)
    return path
