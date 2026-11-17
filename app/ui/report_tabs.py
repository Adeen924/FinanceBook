"""Extra report tabs for the Reports page (grids built from utils.reports).

v1.18: By Class / Project, By Vendor and Cash Flow take the Reports page's
"Columns:" choice (supports_period); P&L-shaped grids list sub-accounts
indented under their parent. The first column stays put while the amounts
scroll sideways. The old By Month tab is now the P&L's Month columns.
"""
import csv

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QHeaderView, QDialog,
                             QFormLayout, QDoubleSpinBox, QDialogButtonBox, QScrollArea)
from PyQt6.QtCore import Qt

from ui.widgets import DataTable, SecondaryButton, FrozenColumn
from ui.styles import DANGER, MUTED
from utils.money import fmt, ZERO
from utils import reports as RP

RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
INDENT = "    "
# pnl_lines() kinds drawn in bold.
_BOLD_KINDS = ("section", "section_total", "summary", "total", "group", "parent")


class GridTab(QWidget):
    """A titled table + note; subclasses fill self.header / self.rows (and the
    row numbers to draw bold in self.bold) in build()."""
    note = ""
    supports_period = False

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
        self._frozen = FrozenColumn(self.table)
        if self.note:
            n = QLabel(self.note)
            n.setObjectName("Muted")
            n.setWordWrap(True)
            lay.addWidget(n)
        self.header, self.rows, self.bold = [], [], set()

    def run(self, start, end, cls="", period="total"):
        self.header, self.rows, self.bold = [], [], set()
        if self.supports_period:
            self.build(start, end, cls, period)
        else:
            self.build(start, end, cls)
        t = self.table
        t.clear()
        t.setColumnCount(len(self.header))
        t.setHorizontalHeaderLabels([h.upper() for h in self.header])
        t.setRowCount(len(self.rows))
        wide = len(self.header) > 6
        hdr = t.horizontalHeader()
        hdr.setStretchLastSection(not wide)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive if wide
                                 else QHeaderView.ResizeMode.Stretch)
        if wide:
            t.setColumnWidth(0, 300)
        for c in range(1, len(self.header)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            t.setColumnWidth(c, 104)
        for r, row in enumerate(self.rows):
            first = str(row[0])
            bold = r in self.bold or first.startswith(
                ("Total", "Net", "Ending", "Beginning")) or bool(first.isupper() and first.strip())
            for c, v in enumerate(row):
                if isinstance(v, (int, float)) or hasattr(v, "quantize"):
                    t.set_item(r, c, fmt(v) if v else "—", align=RIGHT, bold=bold,
                               color=(DANGER if v < 0 else None) if v else MUTED)
                else:
                    t.set_item(r, c, "" if v is None else str(v), bold=bold)
        self._frozen.sync(enabled=wide)

    def build(self, start, end, cls, period="total"):   # pragma: no cover — overridden
        pass

    def export(self, path):
        """The grid as on screen (same columns), amounts with 2 decimals."""
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(self.header)
            for row in self.rows:
                w.writerow([f"{v:.2f}" if hasattr(v, "quantize") else
                            ("" if v is None else v) for v in row])

    def _rows(self, start, end, cls, period="total"):
        rows = self.db.report_rows(start, end, with_computed=not cls, period=period)
        return [r for r in rows if not cls or r["class_id"] == cls]

    def add_pnl_lines(self, lines, columns):
        """Append pnl_lines() rows: the account indented by its level, one
        amount per column (blank for a heading)."""
        for ln in lines:
            label = INDENT * ln["depth"] + ln["label"]
            if ln["amounts"] is None:
                vals = [""] * len(columns)
            else:
                vals = [ln["amounts"].get(c, ZERO) for c in columns]
            if ln["kind"] in _BOLD_KINDS:
                self.bold.add(len(self.rows))
            self.rows.append([label] + vals)


class ByClassTab(GridTab):
    note = ("Profit & loss by class / project / marketing campaign (set on each transaction "
            "or receipt line). With Total, each class is a column; with Day … Year, each "
            "class gets its own block of period columns. Sub-accounts are indented under "
            "their parent. Expenses are shown as positive amounts.")
    supports_period = True

    def build(self, start, end, cls, period="total"):
        rows = self.db.report_rows(start, end, with_computed=False, period=period)
        names = {c["id"]: c["name"] for c in self.db.get_classes()}
        cats = self.db.category_map()
        keys = sorted({r["class_id"] or "" for r in rows},
                      key=lambda k: (k == "", names.get(k, "~").lower()))

        def name(k):
            return names.get(k, "(no class)") if k else "(no class)"
        if period == "total":
            rep = RP.pnl_report(rows, cats, keys, lambda r: r["class_id"] or "")
            self.header = ["Account"] + [name(k) for k in keys] + ["Total"]
            self.add_pnl_lines(RP.pnl_lines(rep), rep["columns"])
        else:
            buckets = RP.period_buckets(start, end, period)
            self.header = ["Account"] + [b.label for b in buckets] + ["Total"]
            col_keys = [b.key for b in buckets]
            for k in keys:
                mine = [r for r in rows if (r["class_id"] or "") == k]
                rep = RP.pnl_report(mine, cats, col_keys,
                                    lambda r: RP.period_key(r["date"], period))
                self.bold.add(len(self.rows))
                self.rows.append([name(k).upper()] + [""] * (len(col_keys) + 1))
                self.add_pnl_lines(RP.pnl_lines(rep), rep["columns"])
        g = RP.pivot(rows, lambda r: r["class_id"] or "", lambda r: r["section"])
        roi = []
        for k in keys:
            inc, exp = g.get(k, {}).get("income", ZERO), -g.get(k, {}).get("expense", ZERO)
            if inc and exp:
                roi.append(f"{name(k)}: {inc / exp:.1f}×")
        self.summary.setText(
            ("<b>ROI</b> (income ÷ spend): " + " · ".join(roi)) if roi else
            "Tag spend with a class (e.g. a campaign name) to see what each one costs and earns.")


class ByVendorTab(GridTab):
    note = "Money out by vendor (contact, or payee when no contact is set) and money in by " \
           "customer. With Day … Year columns each period shows the net (money in − out)."
    supports_period = True

    def build(self, start, end, cls, period="total"):
        rows = [r for r in self._rows(start, end, cls) if r.get("payee") != "(computed)"]
        names = {c["id"]: c["name"] for c in self.db.get_contacts(active_only=False)}
        key = lambda r: names.get(r["contact_id"]) or (r["payee"] or "(no payee)")  # noqa: E731
        g = RP.pivot(rows, key, lambda r: r["section"])
        items = sorted(((k, v) for k, v in g.items() if k != "__total__"),
                       key=lambda kv: kv[1].get("expense", ZERO))
        if period == "total":
            self.header = ["Vendor / customer", "Spent", "Received", "Net"]
            for k, v in items:
                self.rows.append([k, -v.get("expense", ZERO), v.get("income", ZERO),
                                  v["__total__"]])
        else:
            buckets = RP.period_buckets(start, end, period)
            per = RP.pivot(rows, key, lambda r: RP.period_key(r["date"], period))
            self.header = (["Vendor / customer"] + [b.label for b in buckets]
                           + ["Spent", "Received", "Net"])
            for k, v in items:
                self.rows.append([k] + [per[k].get(b.key, ZERO) for b in buckets]
                                 + [-v.get("expense", ZERO), v.get("income", ZERO),
                                    v["__total__"]])
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
           "deductible %. Cost of Goods Sold accounts go on line 4. Bookkeeping aid only — " \
           "confirm with your accountant."

    def build(self, start, end, cls):
        from business_categories import TAX_LINES
        from reports_export import _line_sort
        cats = self.db.category_map()
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
           "the card is paid. With Day … Year columns each period begins where the one before " \
           "it ended."
    supports_period = True

    def build(self, start, end, cls, period="total"):
        cats = self.db.category_map()
        accts, txns = self.db.get_accounts(active=None), self.db.get_transactions()
        tot = RP.cash_flow(accts, txns, cats, start, end)
        if period == "total":
            parts, labels = [tot], ["Amount"]
        else:
            buckets = RP.period_buckets(start, end, period)
            parts = [RP.cash_flow(accts, txns, cats, b.start, b.end) for b in buckets] + [tot]
            labels = [b.label for b in buckets] + ["Total"]
        self.header = ["Cash flow"] + labels
        self.rows.append(["Beginning cash"] + [p["begin"] for p in parts])
        order = {lbl: i for i, lbl in enumerate(RP.CASH_FLOW_LINE_ORDER)}
        for sec, title in (("operating", "OPERATING"), ("investing", "INVESTING"),
                           ("financing", "FINANCING")):
            self.rows.append([title] + [""] * len(parts))
            lines = sorted({lbl for p in parts for lbl in p["sections"][sec]},
                           key=lambda lbl: (order.get(lbl, len(order)), lbl))
            for lbl in lines:
                self.rows.append([INDENT + lbl] + [p["sections"][sec].get(lbl, ZERO)
                                                   for p in parts])
            self.rows.append([f"Net {sec}"] + [p["totals"][sec] for p in parts])
        self.rows.append(["Ending cash"] + [p["end"] for p in parts])
        self.summary.setText(f"<b>Net change in cash:</b> {fmt(tot['net_change'])}")


class BudgetTab(GridTab):
    note = "Monthly budgets per top-level account; its sub-accounts are listed under it " \
           "(their actuals roll up). Variance: positive is good (under budget for expenses, " \
           "over budget for income)."

    def __init__(self, db, parent=None):
        super().__init__(db, parent)
        edit = SecondaryButton("Edit Budgets…")
        edit.clicked.connect(self._edit)
        self.layout().insertWidget(0, edit, 0, Qt.AlignmentFlag.AlignRight)
        self._last = None

    def build(self, start, end, cls):
        self._last = (start, end, cls)
        cats = self.db.category_map()
        out = RP.budget_vs_actual(self._rows(start, end, cls), self.db.get_budgets(), cats, start, end)
        self.header = ["Category", "Budget", "Actual", "Variance", "% used"]
        nested = any(r.get("depth") for r in out)
        for r in out:
            depth = r.get("depth", 0)
            if depth == 0:
                pct = f"{r['pct']:.0f}%" if r["pct"] is not None else "—"
                if nested:
                    self.bold.add(len(self.rows))
                self.rows.append([r["name"], r["budget"], r["actual"], r["variance"], pct])
            else:
                self.rows.append([INDENT * depth + r["name"], None, r["actual"], None, ""])
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
        prefix = {"income": "Income · ", "cogs": "Cost of goods · ",
                  "other_income": "Other income · ", "other_expense": "Other expense · "}
        rank = {"income": 0, "cogs": 1, "expense": 2, "other_income": 3, "other_expense": 4}
        cats = [c for c in self.db.get_categories()
                if not c.get("parent_id") and c.get("type") in rank]
        for c in sorted(cats, key=lambda c: (rank[c["type"]], c["name"].lower())):
            s = QDoubleSpinBox()
            s.setRange(0, 9_999_999)
            s.setPrefix("$")
            s.setValue(budgets.get(c["id"], 0))
            f.addRow(prefix.get(c["type"], "") + c["name"], s)
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
    tabs = [("By Class / Project", ByClassTab), ("By Vendor", ByVendorTab)]
    if db.is_business():
        tabs += [("By Person", ByPersonTab), ("Schedule C", ScheduleCTab)]
    tabs += [("Cash Flow", CashFlowTab), ("Budget vs Actual", BudgetTab)]
    return tabs
