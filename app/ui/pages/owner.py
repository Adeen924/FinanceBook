"""Owner Investment — what you've put into the business, taken out, and are owed."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QGridLayout, QMenu, QFileDialog, QMessageBox,
                             QHeaderView, QScrollArea, QFrame)
from PyQt6.QtCore import Qt, QDate

from ui.widgets import PageTitle, DataTable, SecondaryButton, DateField, fmt_date
from ui.pages.dashboard import KpiCard
from ui.styles import WARNING
from utils.ledger import owner_investment
from utils.money import fmt


class OwnerInvestmentPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._items: list[dict] = []
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        content = QWidget()
        content.setObjectName("ScrollContent")
        scroll.setWidget(content)

        lay = QVBoxLayout(content)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)

        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Owner Investment"))
        hdr.addStretch()
        self._expense_btn = QPushButton("+ Expense I Paid")
        self._expense_btn.setToolTip("Record a company expense you paid with your own money")
        self._expense_btn.clicked.connect(self._add_owner_expense)
        reimb_btn = SecondaryButton("Reimburse Owner")
        reimb_btn.clicked.connect(lambda: self._move("reimburse"))
        more_btn = SecondaryButton("More")
        more_btn.setProperty("hasMenu", True)
        menu = QMenu(more_btn)
        for label, slot in (
                ("Record owner draw…", lambda: self._move("draw")),
                ("Record capital contribution (cash in)…", lambda: self._move("contribute")),
                ("Convert amount owed to capital…", lambda: self._move("convert")),
                (None, None),
                ("Add owner funding source (personal card/cash)…", self._add_funding_source),
                ("I opened a business bank account…", self._open_bank_account)):
            if label is None:
                menu.addSeparator()
            else:
                menu.addAction(label, slot)
        more_btn.setMenu(menu)
        for b in (more_btn, reimb_btn, self._expense_btn):
            hdr.addWidget(b)
        lay.addLayout(hdr)

        info = QLabel(
            "No business bank account yet? Record company expenses you pay "
            "personally with <b>Paid by: me</b>. Choose <b>Capital contribution</b> "
            "(an investment you won't be repaid) or <b>Company owes me</b> "
            "(shows as <i>Due to Owner</i> until you use <b>Reimburse Owner</b>).")
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setObjectName("InfoBox")
        lay.addWidget(info)

        filt = QHBoxLayout()
        filt.setSpacing(8)
        filt.addWidget(QLabel("From:"))
        self._from = DateField()
        self._from.setDate(QDate(QDate.currentDate().year(), 1, 1))
        filt.addWidget(self._from)
        filt.addWidget(QLabel("To:"))
        self._to = DateField()
        self._to.setDate(QDate.currentDate())
        filt.addWidget(self._to)
        run_btn = QPushButton("Run")
        run_btn.clicked.connect(self.refresh)
        filt.addWidget(run_btn)
        export_btn = SecondaryButton("Export CSV")
        export_btn.clicked.connect(self._export_csv)
        filt.addWidget(export_btn)
        filt.addStretch()
        lay.addLayout(filt)

        grid = QGridLayout()
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)
        self._kpi_net = KpiCard("Net invested", "owner", accent=True)
        self._kpi_contrib = KpiCard("Contributed", "trend_up")
        self._kpi_owed = KpiCard("Owed to you", "card")
        self._kpi_drawn = KpiCard("Drawn", "wallet")
        for i, card in enumerate((self._kpi_net, self._kpi_contrib, self._kpi_owed,
                                  self._kpi_drawn)):
            grid.addWidget(card, 0, i)
            grid.setColumnStretch(i, 1)
        lay.addLayout(grid)

        self._table = DataTable(["Date", "Payee / Memo", "Category", "Recorded as",
                                 "Account", "Amount"])
        hh = self._table.horizontalHeader()
        self._table.setColumnWidth(0, 95)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(2, 180)
        self._table.setColumnWidth(3, 160)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(5, 110)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setMinimumHeight(300)
        self._table.doubleClicked.connect(self._edit_selected)
        lay.addWidget(self._table, 1)

        self._summary = QLabel("")
        self._summary.setObjectName("Muted")
        lay.addWidget(self._summary)

    # ── data ─────────────────────────────────────────────────────────────────

    def _range(self):
        return (self._from.date().toString("yyyy-MM-dd"),
                self._to.date().toString("yyyy-MM-dd"))

    def _report(self):
        start, end = self._range()
        return owner_investment(self.db.get_accounts(active=None),
                                self.db.get_transactions(end=end), start, end,
                                owner_id=self.db.owner_person_id())

    def refresh(self):
        start, end = self._range()
        r = self._report()
        self._kpi_net.set_values(fmt(r["net_investment"]), "to date", None,
                                 "lifetime")
        self._kpi_contrib.set_values(fmt(r["contributed"]), "in range", None, "capital put in")
        owed = r["owed_now"]
        self._kpi_owed.set_values(fmt(owed), f"{fmt(r['reimbursed'])} repaid", None,
                                  "in range")
        self._kpi_drawn.set_values(fmt(r["drawn"]), "in range", None, "taken out")

        self._items = r["items"]
        cats = {c["id"]: c for c in self.db.get_categories()}
        accts = {a["id"]: a for a in self.db.get_accounts(active=None)}
        self._table.clear_rows()
        self._table.setRowCount(len(self._items))
        for row, t in enumerate(self._items):
            cat = cats.get(t.get("category_id", ""), {})
            parent = cats.get(cat.get("parent_id", ""), {})
            cname = f"{parent['name']} → {cat['name']}" if parent else cat.get("name", "")
            if str(t.get("is_transfer", "0")) == "1":
                cname = "— (transfer)"
            self._table.set_item(row, 0, fmt_date(t.get("date", "")))
            self._table.set_item(row, 1, t.get("payee") or t.get("memo") or "—")
            self._table.set_item(row, 2, cname or "⚠ Uncategorized",
                                 color=None if cname else WARNING)
            self._table.set_item(row, 3, t.get("owner_kind", ""))
            self._table.set_item(row, 4, accts.get(t.get("account_id", ""), {}).get("name", ""))
            self._table.money_item(row, 5, float(t.get("amount") or 0))
        self._summary.setText(
            f"{len(self._items)} owner-funded entries from {fmt_date(start)} to "
            f"{fmt_date(end)}  ·  You paid for the company: {fmt(r['contributed'] + r['lent'])}"
            f"  ·  Double-click a row to edit it.")

    # ── actions ──────────────────────────────────────────────────────────────

    def _add_owner_expense(self):
        from ui.dialogs.transaction_dialog import TransactionDialog
        dlg = TransactionDialog(self.db, parent=self,
                                preset={"paid_by": self.db.owner_person_id()})
        if dlg.exec() == TransactionDialog.DialogCode.Accepted:
            if not getattr(dlg, "handled", False):
                self.db.save_transaction(dlg.get_data())
            self.refresh()

    def _edit_selected(self):
        row = self._table.currentRow()
        if not 0 <= row < len(self._items):
            return
        txn = self.db.get_transaction(self._items[row]["id"])
        if not txn:
            return
        from ui.dialogs.transaction_dialog import TransactionDialog
        dlg = TransactionDialog(self.db, transaction=txn, parent=self)
        if dlg.exec() == TransactionDialog.DialogCode.Accepted:
            if not getattr(dlg, "handled", False):
                self.db.save_transaction(dlg.get_data())
            self.refresh()

    def _move(self, mode: str):
        from ui.dialogs.owner_dialogs import OwnerMoneyDialog
        if OwnerMoneyDialog(self.db, mode, parent=self).exec():
            self.refresh()

    def _add_funding_source(self):
        from ui.dialogs.owner_dialogs import FundingSourceDialog
        if FundingSourceDialog(self.db, parent=self).exec():
            self.refresh()

    def _open_bank_account(self):
        from ui.dialogs.owner_dialogs import OpenBankAccountDialog
        if OpenBankAccountDialog(self.db, parent=self).exec():
            self.refresh()
            QMessageBox.information(
                self, "Account Added",
                "Your business account was added and is now the default for new "
                "entries. When you're ready, use Reimburse Owner to pay back what "
                "the company owes you.")

    def _export_csv(self):
        import csv
        start, end = self._range()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Owner Investment", f"owner_investment_{start}_{end}.csv",
            "CSV Files (*.csv)")
        if not path:
            return
        r = self._report()
        cats = {c["id"]: c for c in self.db.get_categories()}
        accts = {a["id"]: a for a in self.db.get_accounts(active=None)}
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Owner Investment", f"{start} to {end}"])
                for label, key in (("Contributed", "contributed"),
                                   ("Paid for company (owed to owner)", "lent"),
                                   ("Reimbursed", "reimbursed"), ("Drawn", "drawn"),
                                   ("Owed to owner (end date)", "owed_now"),
                                   ("Net invested (lifetime)", "net_investment")):
                    w.writerow([label, f"{r[key]:.2f}"])
                w.writerow([])
                w.writerow(["Date", "Payee", "Memo", "Category", "Recorded as",
                            "Account", "Amount", "Reimbursable"])
                for t in r["items"]:
                    w.writerow([t.get("date", ""), t.get("payee", ""), t.get("memo", ""),
                                cats.get(t.get("category_id", ""), {}).get("name", ""),
                                t.get("owner_kind", ""),
                                accts.get(t.get("account_id", ""), {}).get("name", ""),
                                f"{float(t.get('amount') or 0):.2f}",
                                "yes" if str(t.get("reimbursable", "0")) == "1" else "no"])
            QMessageBox.information(self, "Exported", f"Saved to {path}")
        except OSError as e:
            QMessageBox.critical(self, "Export Failed", str(e))
