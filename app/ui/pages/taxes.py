"""Taxes — estimated quarterly tax, sales tax, mileage, home office, year-end package."""
from datetime import date

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QHeaderView, QTabWidget, QFormLayout, QLineEdit, QComboBox,
                             QDoubleSpinBox, QSpinBox, QMessageBox, QFileDialog, QDialog,
                             QDialogButtonBox, QGroupBox)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices

from ui.widgets import PageTitle, DataTable, SecondaryButton, DateField, fmt_date
from ui.styles import WARNING, MUTED, SUCCESS
from utils.money import fmt, D
from utils import taxes as TX

ESTIMATE_NOTE = ("Rough <b>estimate</b> for planning: annualizes this year's profit, then adds "
                 "self-employment tax (15.3% of 92.35%) and federal income tax after the standard "
                 "deduction and 20% QBI deduction. Ignores credits, other deductions and state tax. "
                 "Estimated payments are personal — pay them as an owner draw. Confirm with your "
                 "accountant.")


def _spin(maximum=9_999_999, prefix="$", decimals=2):
    s = QDoubleSpinBox()
    s.setRange(0, maximum)
    s.setDecimals(decimals)
    s.setPrefix(prefix)
    return s


class TaxesPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build()

    def _tables(self, year=None):
        from utils.payroll import load_tables
        return load_tables(year or date.today().year)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Taxes"))
        hdr.addStretch()
        self._export_btn = QPushButton("Year-End Package…")
        self._export_btn.clicked.connect(self._year_end)
        hdr.addWidget(self._export_btn)
        lay.addLayout(hdr)
        self._tabs = QTabWidget()
        lay.addWidget(self._tabs, 1)
        self._tabs.addTab(self._build_estimates(), "Estimated Taxes")
        self._tabs.addTab(self._build_sales_tax(), "Sales Tax")
        self._tabs.addTab(self._build_mileage(), "Mileage Log")
        self._tabs.addTab(self._build_home_office(), "Home Office")

    # ── estimated taxes ──────────────────────────────────────────────────────

    def _build_estimates(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        info = QLabel(ESTIMATE_NOTE)
        info.setObjectName("InfoBox")
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(info)
        f = QFormLayout()
        self._filing = QComboBox()
        for k, label in (("single", "Single"), ("mfj", "Married filing jointly"),
                         ("hoh", "Head of household")):
            self._filing.addItem(label, k)
        f.addRow("Filing status", self._filing)
        self._other = _spin()
        f.addRow("Other household income (annual)", self._other)
        self._wages = _spin()
        f.addRow("W-2 wages (annual, you + spouse)", self._wages)
        run = QPushButton("Estimate")
        run.clicked.connect(self._estimate)
        f.addRow("", run)
        lay.addLayout(f)
        self._est = QLabel("")
        self._est.setTextFormat(Qt.TextFormat.RichText)
        self._est.setWordWrap(True)
        lay.addWidget(self._est)
        lay.addStretch()
        return w

    def _estimate(self):
        self.db.set_setting("tax_filing_status", self._filing.currentData())
        self.db.set_setting("tax_other_income", str(self._other.value()))
        self.db.set_setting("tax_w2_wages", str(self._wages.value()))
        today = date.today()
        from utils.reports import schedule_c
        cats = {c["id"]: c for c in self.db.get_categories()}
        sc = schedule_c(self.db.report_rows(f"{today.year}-01-01", today.isoformat()), cats)
        t = self._tables(today.year)
        e = TX.estimated_tax(t, sc["net_profit"], today.isoformat(), self._filing.currentData(),
                             self._other.value(), self._wages.value())
        dues = TX.next_due_dates(t, today.isoformat())
        due_txt = ", ".join(fmt_date(d) for d in dues) or "none left this year"
        stale = " (using the latest tax tables on file)" if t.get("_stale") else ""
        warn = "".join(f"<br><span style='color:{WARNING}'>⚠ {x}</span>" for x in e["warnings"])
        self._est.setText(
            f"<p style='font-size:14px'>Year-to-date net profit: <b>{fmt(sc['net_profit'])}</b> → "
            f"projected for {today.year}: <b>{fmt(e['projected_profit'])}</b>{stale}</p>"
            f"<p>Self-employment tax ≈ <b>{fmt(e['se_tax'])}</b> · Federal income tax ≈ "
            f"<b>{fmt(e['income_tax'])}</b> (after {fmt(e['standard_deduction'])} standard deduction "
            f"and {fmt(e['qbi'])} QBI deduction)</p>"
            f"<p style='font-size:15px'><b>Estimated total: {fmt(e['total'])} — about "
            f"{fmt(e['quarterly'])} per quarter</b></p>"
            f"<p>Upcoming Form 1040-ES due dates: <b>{due_txt}</b></p>{warn}")

    # ── sales tax ────────────────────────────────────────────────────────────

    def _build_sales_tax(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("From"))
        self._st_from = DateField()
        self._st_from.setDate(self._st_from.date().fromString(f"{date.today().year}-01-01", "yyyy-MM-dd"))
        row.addWidget(self._st_from)
        row.addWidget(QLabel("To"))
        self._st_to = DateField()
        row.addWidget(self._st_to)
        go = QPushButton("Run")
        go.clicked.connect(self._fill_sales_tax)
        row.addWidget(go)
        row.addStretch()
        remit = SecondaryButton("Pay Sales Tax…")
        remit.clicked.connect(self._remit)
        row.addWidget(remit)
        lay.addLayout(row)
        self._st_summary = QLabel("")
        self._st_summary.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self._st_summary)
        self._st = DataTable(["Month", "Collected", "Paid to state"])
        self._st.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._st.setColumnWidth(1, 140)
        self._st.setColumnWidth(2, 140)
        lay.addWidget(self._st)
        n = QLabel("Sales tax collected on invoices goes to the Sales Tax Payable liability (not "
                   "income). Set the default rate in Invoices & Bills → Invoice Settings. Rates and "
                   "filing frequency are set by your state.")
        n.setObjectName("Muted")
        n.setWordWrap(True)
        lay.addWidget(n)
        return w

    def _fill_sales_tax(self):
        s = self.db.sales_tax_summary(self._st_from.date().toString("yyyy-MM-dd"),
                                      self._st_to.date().toString("yyyy-MM-dd"))
        t = self._st
        t.clear_rows()
        t.setRowCount(len(s["rows"]))
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for r, row in enumerate(s["rows"]):
            t.set_item(r, 0, row["month"])
            t.set_item(r, 1, fmt(row["collected"]), align=right)
            t.set_item(r, 2, fmt(row["remitted"]), align=right)
        self._st_summary.setText(f"<b>Collected:</b> {fmt(s['collected'])} &nbsp;·&nbsp; "
                                 f"<b>Paid:</b> {fmt(s['remitted'])} &nbsp;·&nbsp; "
                                 f"<b>Owed now:</b> {fmt(s['owed'])}")
        self._st_owed = s["owed"]

    def _remit(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Pay Sales Tax")
        f = QFormLayout(dlg)
        when = DateField()
        f.addRow("Date", when)
        acct = QComboBox()
        for a in self.db.get_accounts():
            if not a.get("role"):
                acct.addItem(a["name"], a["id"])
        f.addRow("Paid from", acct)
        amt = _spin()
        amt.setValue(float(getattr(self, "_st_owed", 0) or 0))
        f.addRow("Amount", amt)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        f.addRow(btns)
        if dlg.exec() and acct.currentData() and amt.value() > 0:
            self.db.remit_sales_tax(acct.currentData(), when.date().toString("yyyy-MM-dd"), amt.value())
            self._fill_sales_tax()

    # ── mileage ──────────────────────────────────────────────────────────────

    def _build_mileage(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        g = QGroupBox("Log a trip")
        f = QHBoxLayout(g)
        self._m_date = DateField()
        f.addWidget(self._m_date)
        self._m_from = QLineEdit()
        self._m_from.setPlaceholderText("From")
        f.addWidget(self._m_from, 1)
        self._m_to = QLineEdit()
        self._m_to.setPlaceholderText("To")
        f.addWidget(self._m_to, 1)
        self._m_purpose = QLineEdit()
        self._m_purpose.setPlaceholderText("Business purpose *")
        f.addWidget(self._m_purpose, 2)
        self._m_miles = _spin(maximum=100000, prefix="", decimals=1)
        self._m_miles.setSuffix(" mi")
        f.addWidget(self._m_miles)
        self._m_treat = QComboBox()
        self._m_treat.addItem("My contribution", "contribution")
        self._m_treat.addItem("Reimburse me", "reimburse")
        f.addWidget(self._m_treat)
        add = QPushButton("Add")
        add.clicked.connect(self._add_trip)
        f.addWidget(add)
        lay.addWidget(g)
        self._m_summary = QLabel("")
        self._m_summary.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self._m_summary)
        self._m = DataTable(["Date", "From → To", "Purpose", "Miles", "Rate", "Deduction"])
        self._m.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for c, wd in ((0, 100), (1, 220), (3, 80), (4, 80), (5, 110)):
            self._m.setColumnWidth(c, wd)
        lay.addWidget(self._m)
        row = QHBoxLayout()
        n = QLabel("Uses the IRS standard mileage rate for each trip's date (from the tax-tables "
                   "file). Each trip books a Car & Truck → Mileage Deduction expense, as money you "
                   "put in or as money the company owes you. Don't also deduct actual car costs "
                   "for the same vehicle.")
        n.setObjectName("Muted")
        n.setWordWrap(True)
        row.addWidget(n, 1)
        d = SecondaryButton("Delete Selected Trip")
        d.clicked.connect(self._delete_trip)
        row.addWidget(d)
        lay.addLayout(row)
        return w

    def _fill_mileage(self):
        year = date.today().year
        trips = self.db.get_mileage()
        self._trips = trips
        t = self._m
        t.clear_rows()
        t.setRowCount(len(trips))
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        tot_mi = tot_ded = D(0)
        for r, m in enumerate(trips):
            ded = D(m["miles"]) * D(m["rate"])
            if m["date"].startswith(str(year)):
                tot_mi += D(m["miles"])
                tot_ded += ded
            t.set_item(r, 0, fmt_date(m["date"]))
            t.set_item(r, 1, f"{m['from_loc']} → {m['to_loc']}".strip(" →"))
            t.set_item(r, 2, m["purpose"])
            t.set_item(r, 3, f"{m['miles']:g}", align=right)
            t.set_item(r, 4, f"${m['rate']}", align=right)
            t.set_item(r, 5, fmt(ded), align=right, bold=True)
        rate = TX.mileage_rate(self._tables(), date.today().isoformat())
        self._m_summary.setText(f"<b>{year}:</b> {float(tot_mi):g} business miles · deduction "
                                f"{fmt(tot_ded)} &nbsp;·&nbsp; today's IRS rate ${rate}/mile")

    def _add_trip(self):
        if not self._m_purpose.text().strip() or self._m_miles.value() <= 0:
            QMessageBox.warning(self, "Trip", "Enter the miles and the business purpose "
                                "(the IRS requires a purpose for each trip).")
            return
        self.db.add_mileage({"date": self._m_date.date().toString("yyyy-MM-dd"),
                             "from_loc": self._m_from.text().strip(), "to_loc": self._m_to.text().strip(),
                             "purpose": self._m_purpose.text().strip(), "miles": self._m_miles.value(),
                             "treatment": self._m_treat.currentData()})
        for e in (self._m_from, self._m_to, self._m_purpose):
            e.clear()
        self._m_miles.setValue(0)
        self._fill_mileage()

    def _delete_trip(self):
        r = self._m.currentRow()
        if 0 <= r < len(getattr(self, "_trips", [])):
            if QMessageBox.question(self, "Delete Trip", "Delete this trip and its deduction entry?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                    ) == QMessageBox.StandardButton.Yes:
                self.db.delete_mileage(self._trips[r]["id"])
                self._fill_mileage()

    # ── home office ──────────────────────────────────────────────────────────

    def _build_home_office(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        f = QFormLayout()
        self._ho_year = QSpinBox()
        self._ho_year.setRange(2020, 2100)
        self._ho_year.setValue(date.today().year)
        f.addRow("Tax year", self._ho_year)
        self._ho_office = _spin(maximum=100000, prefix="", decimals=0)
        self._ho_office.setSuffix(" sq ft")
        f.addRow("Office area (used regularly & exclusively)", self._ho_office)
        self._ho_home = _spin(maximum=100000, prefix="", decimals=0)
        self._ho_home.setSuffix(" sq ft")
        f.addRow("Whole home area", self._ho_home)
        self._ho_exp = _spin()
        f.addRow("Home expenses for the year (rent/mortgage interest, utilities, insurance, repairs)",
                 self._ho_exp)
        calc = QPushButton("Calculate")
        calc.clicked.connect(self._calc_home_office)
        f.addRow("", calc)
        lay.addLayout(f)
        self._ho_result = QLabel("")
        self._ho_result.setTextFormat(Qt.TextFormat.RichText)
        self._ho_result.setWordWrap(True)
        lay.addWidget(self._ho_result)
        row = QHBoxLayout()
        self._ho_simple_btn = SecondaryButton("Record Simplified")
        self._ho_simple_btn.clicked.connect(lambda: self._record_ho("simplified"))
        self._ho_actual_btn = SecondaryButton("Record Actual")
        self._ho_actual_btn.clicked.connect(lambda: self._record_ho("actual"))
        row.addWidget(self._ho_simple_btn)
        row.addWidget(self._ho_actual_btn)
        row.addStretch()
        lay.addLayout(row)
        n = QLabel("Simplified: $5 per sq ft, up to 300 sq ft. Actual: your business share of home "
                   "expenses (Form 8829; depreciation of the home itself isn't included here). "
                   "Both are limited to the business's income. Recording books it as a Home Office "
                   "expense on Dec 31, paid by you. Estimate — confirm with your accountant.")
        n.setObjectName("Muted")
        n.setWordWrap(True)
        lay.addWidget(n)
        lay.addStretch()
        return w

    def _calc_home_office(self):
        y = self._ho_year.value()
        for k, s in (("ho_office_sqft", self._ho_office), ("ho_home_sqft", self._ho_home),
                     ("ho_expenses", self._ho_exp)):
            self.db.set_setting(k, str(s.value()))
        from utils.reports import schedule_c
        cats = {c["id"]: c for c in self.db.get_categories()}
        rows = [r for r in self.db.report_rows(f"{y}-01-01", f"{y}-12-31")
                if (self.db.get_category(r["category_id"]) or {}).get("name") != "Home Office"]
        limit = max(schedule_c(rows, cats)["net_profit"], D(0))
        h = TX.home_office(self._tables(y), self._ho_office.value(), self._ho_home.value(),
                           self._ho_exp.value(), limit)
        self._ho = h
        self._ho_result.setText(
            f"<p>Business use: <b>{float(h['business_pct'] * 100):.1f}%</b> of the home · income "
            f"limit {fmt(limit)}</p><p>Simplified method: <b>{fmt(h['simplified'])}</b> &nbsp;·&nbsp; "
            f"Actual-expense method: <b>{fmt(h['actual'])}</b></p>"
            f"<p style='color:{SUCCESS}'><b>The {h['better']} method gives the larger deduction.</b></p>")

    def _record_ho(self, method):
        if not getattr(self, "_ho", None):
            self._calc_home_office()
        amt = self._ho[method]
        if amt <= 0:
            QMessageBox.information(self, "Home Office", "Nothing to record — calculate first.")
            return
        self.db.record_home_office(self._ho_year.value(), amt, method)
        QMessageBox.information(self, "Recorded",
                                f"Recorded a {fmt(amt)} home-office deduction for "
                                f"{self._ho_year.value()} ({method} method). Recording again "
                                "replaces it.")

    # ── refresh / export ─────────────────────────────────────────────────────

    def refresh(self):
        self._filing.setCurrentIndex(max(self._filing.findData(
            self.db.get_setting("tax_filing_status", "single")), 0))
        self._other.setValue(float(self.db.get_setting("tax_other_income", "0") or 0))
        self._wages.setValue(float(self.db.get_setting("tax_w2_wages", "0") or 0))
        self._ho_office.setValue(float(self.db.get_setting("ho_office_sqft", "0") or 0))
        self._ho_home.setValue(float(self.db.get_setting("ho_home_sqft", "0") or 0))
        self._ho_exp.setValue(float(self.db.get_setting("ho_expenses", "0") or 0))
        self._estimate()
        self._fill_sales_tax()
        self._fill_mileage()

    def _year_end(self):
        year, ok = _ask_year(self)
        if not ok:
            return
        folder = QFileDialog.getExistingDirectory(self, "Save the year-end package in…")
        if not folder:
            return
        from reports_export import export_year_end
        try:
            files = export_year_end(self.db, year, folder)
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))
            return
        import os
        QMessageBox.information(self, "Year-End Package",
                                f"Saved {len(files)} files to:\n{os.path.dirname(files[0])}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(files[0])))


def _ask_year(parent) -> tuple[int, bool]:
    from PyQt6.QtWidgets import QInputDialog
    y, ok = QInputDialog.getInt(parent, "Year-End Package", "Tax year:", date.today().year - (
        1 if date.today().month < 4 else 0), 2000, 2100)
    return y, ok
