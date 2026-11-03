"""People & Payroll — employees, time, payroll runs (estimates) and 1099 contractors."""
import csv
from datetime import date, timedelta

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QHeaderView, QTabWidget, QDialog, QFormLayout, QLineEdit,
                             QComboBox, QDoubleSpinBox, QDialogButtonBox, QMessageBox,
                             QFileDialog, QCheckBox, QSpinBox, QTableWidget, QTableWidgetItem,
                             QAbstractItemView)
from PyQt6.QtCore import Qt, QDate

from ui.widgets import PageTitle, DataTable, SecondaryButton, DateField, fmt_date
from ui.styles import WARNING, DANGER, MUTED, SUCCESS
from ui.pages.contacts import mask_tax_id
from utils.money import fmt, money_sum, D
from utils.payroll import PERIODS

ROW_H = 44
DISCLAIMER = ("Payroll figures are <b>estimates</b> from the 2026 IRS tables (Pub 15-T, FICA, "
              "FUTA) plus the state rates you enter — <b>not a substitute for a payroll provider "
              "or accountant</b>. You're still responsible for deposits and filings (Forms 941/940, "
              "W-2, state returns). Owner pay is a draw, not payroll.")
FILING = [("single", "Single or married filing separately"), ("mfj", "Married filing jointly"),
          ("hoh", "Head of household")]


def _money_spin(maximum=9_999_999, prefix="$", decimals=2) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(0, maximum)
    s.setDecimals(decimals)
    s.setPrefix(prefix)
    return s


class EmployeeDialog(QDialog):
    def __init__(self, db, person: dict | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.person = dict(person or {"employment_type": "w2", "role": "employee"})
        p = self.person
        self.setWindowTitle("Edit Person" if person else "Add Employee")
        self.setMinimumWidth(540)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        basic = QWidget()
        f = QFormLayout(basic)
        f.setSpacing(9)
        self.name = QLineEdit(p.get("name", ""))
        f.addRow("Name *", self.name)
        self.etype = QComboBox()
        for k, label in (("w2", "W-2 employee"), ("1099", "1099 contractor (no payroll)")):
            self.etype.addItem(label, k)
        if p.get("role") == "owner":
            self.etype.addItem("Owner (paid through draws)", "owner")
        self.etype.setCurrentIndex(max(self.etype.findData(p.get("employment_type") or
                                                           ("owner" if p.get("role") == "owner" else "w2")), 0))
        f.addRow("Type", self.etype)
        self.email = QLineEdit(p.get("email", ""))
        f.addRow("Email", self.email)
        self.phone = QLineEdit(p.get("phone", ""))
        f.addRow("Phone", self.phone)
        self.address = QLineEdit(p.get("address", ""))
        f.addRow("Address", self.address)
        self.hire = DateField()
        if p.get("hire_date"):
            self.hire.setDate(QDate.fromString(p["hire_date"], "yyyy-MM-dd"))
        f.addRow("Hire date", self.hire)
        tax_row = QHBoxLayout()
        self.tax_id = QLineEdit(p.get("tax_id", ""))
        self.tax_id.setEchoMode(QLineEdit.EchoMode.Password)
        self.tax_id.setPlaceholderText("SSN / EIN (stored in this database file)")
        tax_row.addWidget(self.tax_id, 1)
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.tax_id.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        tax_row.addWidget(show)
        f.addRow("Tax ID", tax_row)
        tabs.addTab(basic, "Person")

        pay = QWidget()
        g = QFormLayout(pay)
        g.setSpacing(9)
        self.pay_type = QComboBox()
        self.pay_type.addItem("Hourly", "hourly")
        self.pay_type.addItem("Salary (per year)", "salary")
        self.pay_type.setCurrentIndex(max(self.pay_type.findData(p.get("pay_type", "hourly")), 0))
        g.addRow("Pay type", self.pay_type)
        self.rate = _money_spin()
        self.rate.setValue(float(p.get("pay_rate") or 0))
        g.addRow("Rate", self.rate)
        self.freq = QComboBox()
        for k in PERIODS:
            if k != "annual":
                self.freq.addItem(k.title(), k)
        self.freq.setCurrentIndex(max(self.freq.findData(p.get("pay_frequency", "biweekly")), 0))
        g.addRow("Pay frequency", self.freq)
        self.filing = QComboBox()
        for k, label in FILING:
            self.filing.addItem(label, k)
        self.filing.setCurrentIndex(max(self.filing.findData(p.get("filing_status", "single")), 0))
        g.addRow("W-4 Step 1(c)", self.filing)
        self.step2 = QCheckBox("Step 2 box checked (multiple jobs)")
        self.step2.setChecked(str(p.get("w4_step2", 0)) == "1")
        g.addRow("", self.step2)
        self.pre2020 = QCheckBox("Form W-4 from 2019 or earlier")
        self.pre2020.setChecked(str(p.get("w4_pre2020", 0)) == "1")
        g.addRow("", self.pre2020)
        self.dep = _money_spin()
        self.dep.setValue(float(p.get("w4_dependents") or 0))
        g.addRow("Step 3 (dependents, annual)", self.dep)
        self.other = _money_spin()
        self.other.setValue(float(p.get("w4_other_income") or 0))
        g.addRow("Step 4(a) other income", self.other)
        self.ded = _money_spin()
        self.ded.setValue(float(p.get("w4_deductions") or 0))
        g.addRow("Step 4(b) deductions", self.ded)
        self.extra = _money_spin()
        self.extra.setValue(float(p.get("w4_extra") or 0))
        g.addRow("Step 4(c) extra per check", self.extra)
        self.state = _money_spin(prefix="", maximum=20, decimals=3)
        self.state.setSuffix(" % of gross")
        self.state.setValue(float(p.get("state_rate") or 0))
        g.addRow("State withholding", self.state)
        note = QLabel("If the Step 2 box is checked or the W-4 is pre-2020, federal withholding "
                      "isn't estimated — enter it from your payroll provider.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        g.addRow(note)
        tabs.addTab(pay, "Pay & W-4")

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
        d = dict(self.person)
        et = self.etype.currentData()
        d.update({"name": self.name.text().strip(), "employment_type": et,
                  "role": "owner" if et == "owner" else ("contractor" if et == "1099" else "employee"),
                  "email": self.email.text().strip(), "phone": self.phone.text().strip(),
                  "address": self.address.text().strip(),
                  "hire_date": self.hire.date().toString("yyyy-MM-dd"),
                  "tax_id": self.tax_id.text().strip(), "pay_type": self.pay_type.currentData(),
                  "pay_rate": self.rate.value(), "pay_frequency": self.freq.currentData(),
                  "filing_status": self.filing.currentData(),
                  "w4_step2": "1" if self.step2.isChecked() else "0",
                  "w4_pre2020": "1" if self.pre2020.isChecked() else "0",
                  "w4_dependents": self.dep.value(), "w4_other_income": self.other.value(),
                  "w4_deductions": self.ded.value(), "w4_extra": self.extra.value(),
                  "state_rate": self.state.value()})
        d.setdefault("active", "1")
        return d


class PayrollRunDialog(QDialog):
    COLS = ["Employee", "Hours", "Gross", "Fed. tax", "Soc. Sec.", "Medicare", "State", "Net pay",
            "Employer taxes"]

    def __init__(self, db, run: dict | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.run = dict(run or {})
        self.setWindowTitle("Payroll Run")
        self.resize(1040, 600)
        lay = QVBoxLayout(self)
        warn = QLabel(DISCLAIMER)
        warn.setObjectName("InfoBox")
        warn.setWordWrap(True)
        warn.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(warn)
        top = QHBoxLayout()
        today = date.today()
        self.start = DateField()
        self.end = DateField()
        self.pay = DateField()
        self.start.setDate(QDate.fromString(self.run.get("period_start") or
                                            (today - timedelta(days=13)).isoformat(), "yyyy-MM-dd"))
        self.end.setDate(QDate.fromString(self.run.get("period_end") or today.isoformat(), "yyyy-MM-dd"))
        self.pay.setDate(QDate.fromString(self.run.get("pay_date") or today.isoformat(), "yyyy-MM-dd"))
        for label, w in (("Period", self.start), ("to", self.end), ("Pay date", self.pay)):
            top.addWidget(QLabel(label))
            top.addWidget(w)
        self.acct = QComboBox()
        for a in db.get_accounts():
            if not a.get("role") or a.get("role") == "petty_cash":
                self.acct.addItem(a["name"], a["id"])
        pref = self.run.get("account_id") or db.get_setting("default_funding_account")
        if self.acct.findData(pref) >= 0:
            self.acct.setCurrentIndex(self.acct.findData(pref))
        top.addWidget(QLabel("Paid from"))
        top.addWidget(self.acct, 1)
        recalc = SecondaryButton("Load hours && recalculate")
        recalc.clicked.connect(lambda: self._calc(use_time=True))
        top.addWidget(recalc)
        lay.addLayout(top)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(self.COLS)):
            self.table.setColumnWidth(c, 92)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table, 1)
        self.totals = QLabel("")
        self.totals.setStyleSheet("font-weight:700;")
        self.totals.setWordWrap(True)
        lay.addWidget(self.totals)
        self.warnings = QLabel("")
        self.warnings.setStyleSheet(f"color:{WARNING}; font-weight:600;")
        self.warnings.setWordWrap(True)
        lay.addWidget(self.warnings)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        save = btns.addButton("Save Draft", QDialogButtonBox.ButtonRole.ActionRole)
        save.clicked.connect(lambda: self._save(post=False))
        post = btns.addButton("Post Payroll", QDialogButtonBox.ButtonRole.AcceptRole)
        post.clicked.connect(lambda: self._save(post=True))
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._lines: list[dict] = []
        if self.run.get("id"):
            people = {p["id"]: p["name"] for p in db.get_people(active_only=False)}
            self._lines = [dict(l, name=people.get(l["person_id"], ""), warnings=[])
                           for l in db.get_payroll_lines(self.run["id"])]
            self._render()
        else:
            self._calc(use_time=True)

    def _run_dict(self) -> dict:
        return dict(self.run, period_start=self.start.date().toString("yyyy-MM-dd"),
                    period_end=self.end.date().toString("yyyy-MM-dd"),
                    pay_date=self.pay.date().toString("yyyy-MM-dd"),
                    account_id=self.acct.currentData() or "")

    def _calc(self, use_time=False, overrides=None):
        if not self.db.get_employees():
            self.warnings.setText("No W-2 employees yet — add them on the Employees tab.")
        self._lines = self.db.calculate_run(self._run_dict(), overrides)
        self._render()

    def _render(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._lines))
        keys = ["name", "hours", "gross", "fit", "ss_ee", "med_ee", "state", "net", "employer_tax"]
        for r, ln in enumerate(self._lines):
            emp_tax = ln.get("employer_tax")
            if emp_tax is None:
                emp_tax = D(ln.get("ss_er")) + D(ln.get("med_er")) + D(ln.get("futa")) + D(ln.get("suta"))
            for c, k in enumerate(keys):
                v = emp_tax if k == "employer_tax" else ln.get(k, "")
                if k == "med_ee":
                    v = D(ln.get("med_ee")) + D(ln.get("addl_med"))
                text = str(v) if k == "name" else (f"{float(v or 0):g}" if k == "hours" else fmt(v or 0))
                it = QTableWidgetItem(text)
                if c:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if k not in ("hours", "gross"):
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(r, c, it)
        self.table.blockSignals(False)
        tot = {k: money_sum(l.get(k) for l in self._lines) for k in
               ("gross", "fit", "ss_ee", "med_ee", "addl_med", "state", "net")}
        emp = money_sum(D(l.get("ss_er")) + D(l.get("med_er")) + D(l.get("futa")) + D(l.get("suta"))
                        for l in self._lines)
        self.totals.setText(
            f"Gross {fmt(tot['gross'])} · withheld {fmt(tot['gross'] - tot['net'])} · net pay "
            f"{fmt(tot['net'])} · employer taxes {fmt(emp)} · total cost {fmt(tot['gross'] + emp)}")
        warns = sorted({w for l in self._lines for w in l.get("warnings", [])})
        if any(l.get("stale_tables") for l in self._lines):
            warns.append("No tax tables for this pay year — using the latest on file.")
        self.warnings.setText("<br>".join("⚠ " + w for w in warns))

    def _edited(self, item):
        r, c = item.row(), item.column()
        ov = {}
        for i, ln in enumerate(self._lines):
            entry = {"hours": D(self.table.item(i, 1).text() or 0)}
            if i == r and c == 2:
                entry["gross"] = D(self.table.item(i, 2).text().replace("$", "").replace(",", "") or 0)
            elif c == 1 or i != r:
                entry["gross"] = None if c == 1 and i == r else ln.get("gross")
            ov[ln["person_id"]] = entry
        self._calc(overrides=ov)

    def _save(self, post: bool):
        if not self._lines:
            QMessageBox.warning(self, "Nothing to Pay", "There are no paychecks in this run.")
            return
        run = self.db.save_payroll_run(self._run_dict(), self._lines)
        self.run = run
        if post:
            if QMessageBox.question(
                    self, "Post Payroll",
                    "Record these paychecks? This adds wage and payroll-tax expenses, pays net "
                    "pay from the chosen account, and leaves the taxes owed in Payroll Liabilities.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return
            try:
                self.db.finalize_payroll_run(run["id"])
            except ValueError as e:
                QMessageBox.warning(self, "Can't Post", str(e))
                return
        self.accept()


class PayrollPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("People & Payroll"))
        hdr.addStretch()
        taxes = SecondaryButton("Pay Payroll Taxes…")
        taxes.clicked.connect(self._pay_taxes)
        hdr.addWidget(taxes)
        add = SecondaryButton("+ Employee")
        add.clicked.connect(self._add_employee)
        hdr.addWidget(add)
        self._add_btn = QPushButton("+ Payroll Run")
        self._add_btn.clicked.connect(self._new_run)
        hdr.addWidget(self._add_btn)
        lay.addLayout(hdr)
        self._banner = QLabel("")
        self._banner.setObjectName("InfoBox")
        self._banner.setWordWrap(True)
        self._banner.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self._banner)

        self._tabs = QTabWidget()
        lay.addWidget(self._tabs, 1)
        self._people = DataTable(["Name", "Type", "Pay", "Frequency", "Hired", "Tax ID", "Actions"])
        self._people.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 150), (2, 120), (3, 104), (4, 96), (5, 110)):
            self._people.setColumnWidth(c, w)
        self._people.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self._people.setColumnWidth(6, 90)
        self._people.horizontalHeader().setStretchLastSection(False)
        self._people.verticalHeader().setDefaultSectionSize(ROW_H)
        self._tabs.addTab(self._wrap(self._people), "People")

        tw = QWidget()
        tl = QVBoxLayout(tw)
        tl.setContentsMargins(0, 8, 0, 0)
        trow = QHBoxLayout()
        self._t_person = QComboBox()
        trow.addWidget(self._t_person, 1)
        self._t_date = DateField()
        trow.addWidget(self._t_date)
        self._t_hours = _money_spin(maximum=24, prefix="", decimals=2)
        self._t_hours.setSuffix(" h")
        trow.addWidget(self._t_hours)
        self._t_notes = QLineEdit()
        self._t_notes.setPlaceholderText("Notes")
        trow.addWidget(self._t_notes, 1)
        addt = QPushButton("Log Hours")
        addt.clicked.connect(self._log_time)
        trow.addWidget(addt)
        delt = SecondaryButton("Delete Selected")
        delt.clicked.connect(self._delete_time)
        trow.addWidget(delt)
        tl.addLayout(trow)
        self._time = DataTable(["Date", "Employee", "Hours", "Paid in run", "Notes"])
        self._time.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for c, w in ((0, 100), (1, 180), (2, 80), (3, 110)):
            self._time.setColumnWidth(c, w)
        tl.addWidget(self._time)
        self._tabs.addTab(tw, "Time")

        self._runs = DataTable(["Pay date", "Period", "Employees", "Gross", "Net pay", "Status", "Actions"])
        self._runs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c, w in ((0, 100), (2, 90), (3, 104), (4, 104), (5, 80)):
            self._runs.setColumnWidth(c, w)
        self._runs.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self._runs.setColumnWidth(6, 170)
        self._runs.horizontalHeader().setStretchLastSection(False)
        self._runs.verticalHeader().setDefaultSectionSize(ROW_H)
        self._tabs.addTab(self._wrap(self._runs), "Payroll Runs")

        cw = QWidget()
        cl = QVBoxLayout(cw)
        cl.setContentsMargins(0, 8, 0, 0)
        crow = QHBoxLayout()
        crow.addWidget(QLabel("Tax year"))
        self._year = QSpinBox()
        self._year.setRange(2020, 2100)
        self._year.setValue(date.today().year)
        self._year.valueChanged.connect(self._fill_contractors)
        crow.addWidget(self._year)
        self._c_note = QLabel("")
        self._c_note.setObjectName("Muted")
        self._c_note.setWordWrap(True)
        crow.addWidget(self._c_note, 1)
        exp = SecondaryButton("Export 1099 Summary CSV")
        exp.clicked.connect(self._export_1099)
        crow.addWidget(exp)
        cl.addLayout(crow)
        self._contractors = DataTable(["Contractor", "Tax ID", "Paid in year", "Payments", "1099-NEC?"])
        self._contractors.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 120), (2, 120), (3, 90), (4, 200)):
            self._contractors.setColumnWidth(c, w)
        cl.addWidget(self._contractors)
        hint = QLabel("Contractors are Contacts with “may need a 1099-NEC” ticked; payments are "
                      "transactions linked to them. Card / PayPal payments are reported on 1099-K "
                      "by the processor, not by you — exclude them when filing.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        cl.addWidget(hint)
        self._tabs.addTab(cw, "Contractors (1099)")

        sw = QWidget()
        sf = QFormLayout(sw)
        self._state_code = QLineEdit()
        self._state_code.setMaxLength(2)
        self._state_code.setPlaceholderText("e.g. CA")
        sf.addRow("Payroll state", self._state_code)
        self._suta_rate = _money_spin(prefix="", maximum=20, decimals=3)
        self._suta_rate.setSuffix(" %")
        sf.addRow("SUTA (state unemployment) rate", self._suta_rate)
        self._suta_base = _money_spin()
        sf.addRow("SUTA wage base", self._suta_base)
        save = QPushButton("Save")
        save.clicked.connect(self._save_settings)
        sf.addRow("", save)
        sn = QLabel("Your state assigns your SUTA rate and wage base (check your state "
                    "unemployment notice). FUTA credit reductions for 2026 aren't final yet; "
                    "they're edited in app/data/tax_tables_2026.json.")
        sn.setObjectName("Muted")
        sn.setWordWrap(True)
        sf.addRow(sn)
        self._tabs.addTab(sw, "Payroll Settings")

    def _wrap(self, w) -> QWidget:
        p = QWidget()
        l = QVBoxLayout(p)
        l.setContentsMargins(0, 8, 0, 0)
        l.addWidget(w)
        return p

    # ── refresh ──────────────────────────────────────────────────────────────

    def refresh(self):
        owed = self.db.payroll_liability_balance()
        self._banner.setText(DISCLAIMER + (f"<br><b>Payroll taxes owed now:</b> {fmt(owed)}"
                                           if abs(owed) >= 0.005 else ""))
        people = self.db.get_people()
        t = self._people
        t.clear_rows()
        t.setRowCount(len(people))
        labels = {"w2": "W-2 employee", "1099": "1099 contractor", "owner": "Owner (draws)"}
        for r, p in enumerate(people):
            et = p.get("employment_type") or ("owner" if p.get("role") == "owner" else "")
            t.set_item(r, 0, p["name"], bold=True)
            t.set_item(r, 1, labels.get(et, p.get("role", "").title()))
            if et == "w2":
                rate = float(p.get("pay_rate") or 0)
                t.set_item(r, 2, f"{fmt(rate)}/h" if p.get("pay_type") == "hourly" else f"{fmt(rate)}/yr")
                t.set_item(r, 3, (p.get("pay_frequency") or "").title())
            else:
                t.set_item(r, 2, "—", color=MUTED)
            t.set_item(r, 4, fmt_date(p.get("hire_date", "")))
            t.set_item(r, 5, mask_tax_id(p.get("tax_id", "")) or "—", color=MUTED)
            b = QPushButton("Edit")
            b.setObjectName("CellButton")
            b.clicked.connect(lambda _, x=p: self._edit_person(x))
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(6, 5, 6, 5)
            cl.addWidget(b)
            t.setCellWidget(r, 6, cell)
            t.setRowHeight(r, ROW_H)

        emps = self.db.get_employees()
        self._t_person.clear()
        for e in emps:
            self._t_person.addItem(e["name"], e["id"])
        self._fill_time()
        self._fill_runs()
        self._fill_contractors()
        self._state_code.setText(self.db.get_setting("payroll_state", ""))
        self._suta_rate.setValue(float(self.db.get_setting("suta_rate", "0") or 0))
        self._suta_base.setValue(float(self.db.get_setting("suta_wage_base", "0") or 0))

    def _fill_time(self):
        names = {p["id"]: p["name"] for p in self.db.get_people(active_only=False)}
        rows = self.db.get_time()[:500]
        self._time_rows = rows
        t = self._time
        t.clear_rows()
        t.setRowCount(len(rows))
        for r, e in enumerate(rows):
            t.set_item(r, 0, fmt_date(e["date"]))
            t.set_item(r, 1, names.get(e["person_id"], ""))
            t.set_item(r, 2, f"{e['hours']:g}", align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.set_item(r, 3, "✓" if e.get("run_id") else "", color=SUCCESS)
            t.set_item(r, 4, e.get("notes", ""), color=MUTED)

    def _fill_runs(self):
        runs = self.db.get_payroll_runs()
        t = self._runs
        t.clear_rows()
        t.setRowCount(len(runs))
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for r, run in enumerate(runs):
            lines = self.db.get_payroll_lines(run["id"])
            t.set_item(r, 0, fmt_date(run["pay_date"]), bold=True)
            t.set_item(r, 1, f"{fmt_date(run['period_start'])} – {fmt_date(run['period_end'])}")
            t.set_item(r, 2, str(len(lines)), align=right)
            t.set_item(r, 3, fmt(money_sum(l["gross"] for l in lines)), align=right)
            t.set_item(r, 4, fmt(money_sum(l["net"] for l in lines)), align=right)
            st = run["status"]
            t.set_item(r, 5, "Posted" if st == "final" else "Draft",
                       color=SUCCESS if st == "final" else MUTED, bold=True)
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(6, 5, 6, 5)
            cl.setSpacing(6)
            if st == "final":
                v = QPushButton("Void")
                v.setObjectName("CellDanger")
                v.clicked.connect(lambda _, x=run: self._void(x))
                cl.addWidget(v)
            else:
                o = QPushButton("Open")
                o.setObjectName("CellButton")
                o.clicked.connect(lambda _, x=run: self._open_run(x))
                cl.addWidget(o)
                d = QPushButton("Delete")
                d.setObjectName("CellDanger")
                d.clicked.connect(lambda _, x=run: self._delete_run(x))
                cl.addWidget(d)
            t.setCellWidget(r, 6, cell)
            t.setRowHeight(r, ROW_H)

    def _fill_contractors(self, *_):
        year = self._year.value()
        rows = self.db.contractor_payments(year)
        self._contractor_rows = rows
        t = self._contractors
        t.clear_rows()
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            c = row["contact"]
            t.set_item(r, 0, c["name"], bold=True)
            t.set_item(r, 1, mask_tax_id(c.get("tax_id", "")) or "missing",
                       color=DANGER if row["missing_tax_id"] and row["needs_1099"] else MUTED)
            t.set_item(r, 2, fmt(row["paid"]), align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.set_item(r, 3, str(row["count"]), align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.set_item(r, 4, "Yes — file a 1099-NEC" if row["needs_1099"] else "Under threshold",
                       color=WARNING if row["needs_1099"] else MUTED, bold=row["needs_1099"])
        th = rows[0]["threshold"] if rows else None
        self._c_note.setText(f"1099-NEC threshold for payments made in {year}: "
                             f"{fmt(th) if th is not None else '—'} (from the tax-tables file)."
                             if rows else "No contacts are flagged as 1099 contractors yet.")

    # ── actions ──────────────────────────────────────────────────────────────

    def _add_employee(self):
        dlg = EmployeeDialog(self.db, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_employee(dlg.get_data())
            self.refresh()

    def _edit_person(self, p):
        dlg = EmployeeDialog(self.db, p, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_employee(dlg.get_data())
            self.refresh()

    def _log_time(self):
        pid = self._t_person.currentData()
        if not pid or self._t_hours.value() <= 0:
            return
        self.db.add_time(pid, self._t_date.date().toString("yyyy-MM-dd"), self._t_hours.value(),
                         self._t_notes.text().strip())
        self._t_notes.clear()
        self._fill_time()

    def _delete_time(self):
        r = self._time.currentRow()
        rows = getattr(self, "_time_rows", [])
        if 0 <= r < len(rows):
            if rows[r].get("run_id"):
                QMessageBox.information(self, "Already Paid", "Void that payroll run first.")
                return
            self.db.delete_time(rows[r]["id"])
            self._fill_time()

    def _new_run(self):
        if not self.db.get_employees():
            QMessageBox.information(self, "No Employees",
                                    "Add a W-2 employee first. (Owner pay is a draw, not payroll — "
                                    "use the Owner Investment page.)")
            return
        if PayrollRunDialog(self.db, parent=self).exec():
            self._tabs.setCurrentIndex(2)
            self.refresh()

    def _open_run(self, run):
        if PayrollRunDialog(self.db, run, parent=self).exec():
            self.refresh()

    def _void(self, run):
        if QMessageBox.question(self, "Void Payroll Run",
                                "Remove this run's transactions and return it to draft?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            self.db.void_payroll_run(run["id"])
            self.refresh()

    def _delete_run(self, run):
        if QMessageBox.question(self, "Delete Draft", "Delete this draft payroll run?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            self.db.delete_payroll_run(run["id"])
            self.refresh()

    def _pay_taxes(self):
        owed = self.db.payroll_liability_balance()
        dlg = QDialog(self)
        dlg.setWindowTitle("Pay Payroll Taxes")
        f = QFormLayout(dlg)
        when = DateField()
        f.addRow("Date", when)
        acct = QComboBox()
        for a in self.db.get_accounts():
            if not a.get("role"):
                acct.addItem(a["name"], a["id"])
        f.addRow("Paid from", acct)
        amt = _money_spin()
        amt.setValue(max(owed, 0))
        f.addRow("Amount", amt)
        memo = QLineEdit("Federal tax deposit (Form 941)")
        f.addRow("Memo", memo)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        f.addRow(btns)
        if dlg.exec() and acct.currentData() and amt.value() > 0:
            self.db.pay_payroll_taxes(acct.currentData(), when.date().toString("yyyy-MM-dd"),
                                      amt.value(), memo.text().strip())
            self.refresh()

    def _save_settings(self):
        self.db.set_setting("payroll_state", self._state_code.text().strip().upper())
        self.db.set_setting("suta_rate", str(self._suta_rate.value()))
        self.db.set_setting("suta_wage_base", str(self._suta_base.value()))
        QMessageBox.information(self, "Saved", "Payroll settings saved.")

    def _export_1099(self):
        year = self._year.value()
        path, _ = QFileDialog.getSaveFileName(self, "Export 1099 Summary",
                                              f"1099_summary_{year}.csv", "CSV Files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Contractor", "Company", "Tax ID (last 4)", "Address", "Email",
                        f"Paid in {year}", "Payments", "Needs 1099-NEC"])
            for row in getattr(self, "_contractor_rows", []):
                c = row["contact"]
                w.writerow([c["name"], c.get("company", ""), mask_tax_id(c.get("tax_id", "")),
                            c.get("address", "").replace("\n", ", "), c.get("email", ""),
                            f"{row['paid']:.2f}", row["count"], "yes" if row["needs_1099"] else "no"])
        QMessageBox.information(self, "Exported",
                                f"Saved to {path}\n\nTax IDs are masked; take full IDs from each "
                                "contractor's W-9 when you file.")
