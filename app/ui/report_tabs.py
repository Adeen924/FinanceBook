"""Extra report tabs for the Reports page (grids built from utils.reports)."""
import csv

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QHeaderView, QDialog,
                             QFormLayout, QDoubleSpinBox, QDialogButtonBox, QScrollArea,
                             QPushButton)
from PyQt6.QtCore import Qt

from ui.widgets import DataTable, SecondaryButton
from ui.styles import SUCCESS, DANGER, MUTED
from utils.money import fmt, D, ZERO
from utils import reports as RP

RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class GridTab(QWidget):
    """A titled table + note; subclasses fill self.header/self.rows in build()."""
    note = ""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.summary)
        self.table = DataTable(["—"])
        lay.addWidget(self.table, 1)
        if self.note:
            n = QLabel(self.note)
            n.setObjectName("Muted")
            n.setWordWrap(True)
            lay.addWidget(n)
        self.header, self.rows = [], []

    def run(self, start, end, cls=""):
        self.header, self.rows = [], []
        self.build(start, end, cls)
        t = self.table
        t.clear()
        t.setColumnCount(len(self.header))
        t.setHorizontalHeaderLabels([h.upper() for h in self.header])
        t.setRowCount(len(self.rows))
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(self.header)):
            t.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            t.setColumnWidth(c, 104)
        for r, row in enumerate(self.rows):
            bold = str(row[0]).startswith(("Total", "Net", "Ending", "Beginning")) or \
                str(row[0]).isupper()
            for c, v in enumerate(row):
                if isinstance(v, (int, float)) or hasattr(v, "quantize"):
                    t.set_item(r, c, fmt(v) if v else "—", align=RIGHT, bold=bold,
                               color=(DANGER if v < 0 else None) if v else MUTED)
                else:
                    t.set_item(r, c, str(v), bold=bold)

    def build(self, start, end, cls):   # pragma: no cover — overridden
        pass

    def export(self, path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(self.header)
            for row in self.rows:
                w.writerow([f"{v:.2f}" if hasattr(v, "quantize") else v for v in row])

    def _rows(self, start, end, cls):
        rows = self.db.report_rows(start, end, with_computed=not cls)
        return [r for r in rows if not cls or r["class_id"] == cls]


class ByMonthTab(GridTab):
    note = "Profit & loss by month. Expenses are negative; computed lines (depreciation, COGS) " \
           "are placed in the last month of the range."

    def build(self, start, end, cls):
        rows = self._rows(start, end, cls)
        months = RP.months_between(start, end)
        g = RP.pivot(rows, lambda r: (RP.pnl_section_label(r), r["group"]), lambda r: r["month"])
        self.header = ["Account"] + [f"{MONTHS[int(m[5:]) - 1]} {m[2:4]}" for m in months] + ["Total"]
        has_other = any(k[0].startswith("Other") for k in g if k != "__total__")
        for sec in RP.PNL_SECTION_ORDER:
            keys = sorted(k for k in g if k != "__total__" and k[0] == sec)
            if sec == "Other Income" and has_other:
                op = RP.pivot([r for r in rows if not r.get("other")], lambda r: "op",
                              lambda r: r["month"]).get("op", {})
                self.rows.append(["Net operating income"] + [op.get(m, ZERO) for m in months]
                                 + [op.get("__total__", ZERO)])
            if keys:
                self.rows.append([{"Expense": "EXPENSES"}.get(sec, sec.upper())]
                                 + [""] * (len(months) + 1))
            for k in keys:
                self.rows.append([k[1]] + [g[k].get(m, ZERO) for m in months] + [g[k]["__total__"]])
        tot = g.get("__total__", {})
        self.rows.append(["Net income"] + [tot.get(m, ZERO) for m in months] + [tot.get("__total__", ZERO)])
        self.summary.setText(f"<b>Net income for the period:</b> {fmt(tot.get('__total__', 0))}")


class ByClassTab(GridTab):
    note = "Profit & loss by class / project / marketing campaign (set on each transaction or " \
           "receipt line). ROI = income ÷ spend for classes that have both."

    def build(self, start, end, cls):
        rows = self.db.report_rows(start, end, with_computed=False)
        names = {c["id"]: c["name"] for c in self.db.get_classes()}
        g = RP.pivot(rows, lambda r: r["class_id"] or "", lambda r: r["section"])
        self.header = ["Class / project / campaign", "Income", "Spend", "Net", "ROI"]
        for k in sorted((k for k in g if k != "__total__"), key=lambda k: (k == "", names.get(k, "~"))):
            inc, exp = g[k].get("income", ZERO), -g[k].get("expense", ZERO)
            roi = f"{(inc / exp):.1f}×" if inc and exp else "—"
            self.rows.append([names.get(k, "(no class)") if k else "(no class)", inc, exp,
                              g[k]["__total__"], roi])
        self.summary.setText("Tag spend with a class (e.g. a campaign name) to see what each one "
                             "costs and earns.")


class ByVendorTab(GridTab):
    note = "Money out by vendor (contact, or payee when no contact is set) and money in by customer."

    def build(self, start, end, cls):
        rows = self._rows(start, end, cls)
        names = {c["id"]: c["name"] for c in self.db.get_contacts(active_only=False)}
        key = lambda r: names.get(r["contact_id"]) or (r["payee"] or "(no payee)")  # noqa: E731
        g = RP.pivot([r for r in rows if r.get("payee") != "(computed)"], key, lambda r: r["section"])
        self.header = ["Vendor / customer", "Spent", "Received", "Net"]
        items = sorted(((k, v) for k, v in g.items() if k != "__total__"),
                       key=lambda kv: kv[1].get("expense", ZERO))
        for k, v in items:
            self.rows.append([k, -v.get("expense", ZERO), v.get("income", ZERO), v["__total__"]])
        self.summary.setText(f"{len(items)} vendors / customers in this period.")


class ByPersonTab(GridTab):
    note = "Expenses by who paid (company account vs. the owner or another person) and who they " \
           "were charged to."

    def build(self, start, end, cls):
        rows = [r for r in self._rows(start, end, cls) if r["section"] == "expense"]
        people = {p["id"]: p["name"] for p in self.db.get_people(active_only=False)}
        classes = {c["id"]: c["name"] for c in self.db.get_classes()}

        def charged(r):
            ct = r.get("charged_to") or ""
            if ct.startswith("person:"):
                return people.get(ct[7:], "Person")
            if ct.startswith("class:"):
                return "Project · " + classes.get(ct[6:], "")
            return "Company"
        g = RP.pivot(rows, lambda r: people.get(r["paid_by"], "Company account") if r["paid_by"] else
                     "Company account", charged)
        cols = sorted({c for v in g.values() for c in v if c != "__total__"})
        self.header = ["Paid by"] + [f"→ {c}" for c in cols] + ["Total"]
        for k in sorted(k for k in g if k != "__total__"):
            self.rows.append([k] + [-g[k].get(c, ZERO) for c in cols] + [-g[k]["__total__"]])


class ScheduleCTab(GridTab):
    note = "Schedule C (Form 1040) estimate from your categories' tax lines; meals at their " \
           "deductible %. Bookkeeping aid only — confirm with your accountant."

    def build(self, start, end, cls):
        from business_categories import TAX_LINES
        from reports_export import _line_sort
        cats = {c["id"]: c for c in self.db.get_categories()}
        sc = RP.schedule_c(self.db.report_rows(start, end), cats)
        self.header = ["Schedule C line", "Book amount", "Deductible"]
        for code, v in sorted(sc["lines"].items(), key=lambda kv: _line_sort(kv[0])):
            self.rows.append([TAX_LINES.get(code, f"Line {code}"), v["book"], v["deductible"]])
        self.rows.append(["Gross income (line 7)", "", sc["gross_income"]])
        self.rows.append(["Total expenses (line 28)", "", sc["expenses"]])
        self.rows.append(["Net profit (line 31)", "", sc["net_profit"]])
        warn = (f" &nbsp;·&nbsp; <span style='color:{DANGER}'>{fmt(sc['unmapped'])} is in "
                "categories with no Schedule C line — set one in Categories.</span>"
                if sc["unmapped"] else "")
        self.summary.setText(f"<b>Estimated net profit:</b> {fmt(sc['net_profit'])}{warn}")


class CashFlowTab(GridTab):
    note = "Direct method, cash accounts only (bank, cash, petty cash). Card purchases count when " \
           "the card is paid."

    def build(self, start, end, cls):
        cats = {c["id"]: c for c in self.db.get_categories()}
        cf = RP.cash_flow(self.db.get_accounts(active=None), self.db.get_transactions(), cats,
                          start, end)
        self.header = ["Cash flow", "Amount"]
        self.rows.append(["Beginning cash", cf["begin"]])
        for sec, title in (("operating", "OPERATING"), ("investing", "INVESTING"),
                           ("financing", "FINANCING")):
            self.rows.append([title, ""])
            for label, v in cf["sections"][sec].items():
                self.rows.append(["    " + label, v])
            self.rows.append([f"Net {sec}", cf["totals"][sec]])
        self.rows.append(["Ending cash", cf["end"]])
        self.summary.setText(f"<b>Net change in cash:</b> {fmt(cf['net_change'])}")


class BudgetTab(GridTab):
    note = "Monthly budgets per top-level category. Variance: positive is good (under budget for " \
           "expenses, over budget for income)."

    def __init__(self, db, parent=None):
        super().__init__(db, parent)
        edit = SecondaryButton("Edit Budgets…")
        edit.clicked.connect(self._edit)
        self.layout().insertWidget(0, edit, 0, Qt.AlignmentFlag.AlignRight)
        self._last = None

    def build(self, start, end, cls):
        self._last = (start, end, cls)
        cats = {c["id"]: c for c in self.db.get_categories()}
        out = RP.budget_vs_actual(self._rows(start, end, cls), self.db.get_budgets(), cats, start, end)
        self.header = ["Category", "Budget", "Actual", "Variance", "% used"]
        for r in out:
            pct = f"{r['pct']:.0f}%" if r["pct"] is not None else "—"
            self.rows.append([r["name"], r["budget"], r["actual"], r["variance"], pct])
        if not self.db.get_budgets():
            self.summary.setText("No budgets yet — click <b>Edit Budgets…</b>.")
        else:
            self.summary.setText("")

    def _edit(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Monthly Budgets")
        dlg.resize(460, 560)
        lay = QVBoxLayout(dlg)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        holder = QWidget()
        f = QFormLayout(holder)
        budgets = self.db.get_budgets()
        spins = {}
        for c in self.db.get_categories():
            if c.get("parent_id") or c.get("type") not in ("income", "expense",
                                                             "other_income", "other_expense"):
                continue
            s = QDoubleSpinBox()
            s.setRange(0, 9_999_999)
            s.setPrefix("$")
            s.setValue(budgets.get(c["id"], 0))
            f.addRow({"income": "Income · ", "other_income": "Other income · ",
                      "other_expense": "Other expense · "}.get(c["type"], "") + c["name"], s)
            spins[c["id"]] = s
        sa.setWidget(holder)
        lay.addWidget(sa)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        if dlg.exec():
            for cid, s in spins.items():
                self.db.set_budget(cid, s.value())
            if self._last:
                self.run(*self._last)


def extra_tabs(db) -> list[tuple[str, type]]:
    tabs = [("By Month", ByMonthTab), ("By Class / Project", ByClassTab),
            ("By Vendor", ByVendorTab)]
    if db.is_business():
        tabs += [("By Person", ByPersonTab), ("Schedule C", ScheduleCTab)]
    tabs += [("Cash Flow", CashFlowTab), ("Budget vs Actual", BudgetTab)]
    return tabs
