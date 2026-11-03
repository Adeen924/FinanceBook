"""
Employees, time, payroll runs and 1099 contractors — mixed into
sheets.client.Database. Payroll amounts are ESTIMATES (see utils.payroll).

Posting a payroll run (all inside the built-in "Payroll Liabilities" account,
so the P&L and Balance Sheet stay right without a bank account per tax):
  1. Wages expense (gross) and employer payroll taxes are recorded as
     expenses in Payroll Liabilities → the liability grows.
  2. Net pay is a transfer bank → Payroll Liabilities → the liability shrinks.
  What's left is withheld + employer taxes owed; "Pay payroll taxes" is a
  transfer bank → Payroll Liabilities that clears it.
The owner is never on payroll — owner pay is a draw (Owner Investment page).
"""
from datetime import date

PAYROLL_SCHEMA = """
CREATE TABLE IF NOT EXISTS time_entries (
    id        TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    date      TEXT DEFAULT '',
    hours     REAL DEFAULT 0,
    notes     TEXT DEFAULT '',
    run_id    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_time_person ON time_entries(person_id, date);
CREATE TABLE IF NOT EXISTS payroll_runs (
    id           TEXT PRIMARY KEY,
    period_start TEXT DEFAULT '',
    period_end   TEXT DEFAULT '',
    pay_date     TEXT DEFAULT '',
    account_id   TEXT DEFAULT '',
    status       TEXT DEFAULT 'draft',
    notes        TEXT DEFAULT '',
    txn_ids      TEXT DEFAULT '',
    created_at   TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS payroll_lines (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    person_id    TEXT NOT NULL,
    hours        REAL DEFAULT 0,
    gross        REAL DEFAULT 0,
    fit          REAL DEFAULT 0,
    ss_ee        REAL DEFAULT 0,
    med_ee       REAL DEFAULT 0,
    addl_med     REAL DEFAULT 0,
    state        REAL DEFAULT 0,
    net          REAL DEFAULT 0,
    ss_er        REAL DEFAULT 0,
    med_er       REAL DEFAULT 0,
    futa         REAL DEFAULT 0,
    suta         REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_payline_run ON payroll_lines(run_id);
"""

# People columns added for employees (v7)
PEOPLE_COLUMNS = [
    ("people", "employment_type", "TEXT DEFAULT ''"),     # owner | w2 | 1099
    ("people", "pay_type",        "TEXT DEFAULT 'hourly'"),
    ("people", "pay_rate",        "REAL DEFAULT 0"),
    ("people", "pay_frequency",   "TEXT DEFAULT 'biweekly'"),
    ("people", "hire_date",       "TEXT DEFAULT ''"),
    ("people", "tax_id",          "TEXT DEFAULT ''"),
    ("people", "address",         "TEXT DEFAULT ''"),
    ("people", "filing_status",   "TEXT DEFAULT 'single'"),
    ("people", "w4_step2",        "INTEGER DEFAULT 0"),
    ("people", "w4_pre2020",      "INTEGER DEFAULT 0"),
    ("people", "w4_dependents",   "REAL DEFAULT 0"),
    ("people", "w4_other_income", "REAL DEFAULT 0"),
    ("people", "w4_deductions",   "REAL DEFAULT 0"),
    ("people", "w4_extra",        "REAL DEFAULT 0"),
    ("people", "state_rate",      "REAL DEFAULT 0"),
    ("people", "contact_id",      "TEXT DEFAULT ''"),
]
_EMP_COLS = [c for _t, c, _d in PEOPLE_COLUMNS]
_LINE_NUM = ("hours", "gross", "fit", "ss_ee", "med_ee", "addl_med", "state", "net",
             "ss_er", "med_er", "futa", "suta")


class PayrollMixin:
    # ── employees ────────────────────────────────────────────────────────────

    def save_employee(self, data: dict) -> dict:
        """save_person() plus the employment/W-4 fields."""
        data = self.save_person(data)
        vals = []
        for k in _EMP_COLS:
            v = data.get(k)
            if k in ("pay_rate", "w4_dependents", "w4_other_income", "w4_deductions", "w4_extra",
                     "state_rate"):
                v = float(v or 0)
            elif k in ("w4_step2", "w4_pre2020"):
                v = 1 if str(v or 0) in ("1", "True") else 0
            else:
                v = v or ""
            vals.append(v)
        with self._conn() as c:
            c.execute(f"UPDATE people SET {','.join(k + '=?' for k in _EMP_COLS)} WHERE id=?",
                      vals + [data["id"]])
        return data

    def get_employees(self, active_only: bool = True) -> list[dict]:
        return [p for p in self.get_people(active_only) if p.get("employment_type") == "w2"]

    # ── time ─────────────────────────────────────────────────────────────────

    def add_time(self, person_id: str, day: str, hours, notes: str = "") -> str:
        tid = self._new_id()
        with self._conn() as c:
            c.execute("INSERT INTO time_entries (id,person_id,date,hours,notes) VALUES (?,?,?,?,?)",
                      (tid, person_id, day, float(hours or 0), notes))
        return tid

    def get_time(self, person_id: str = "", start: str = "", end: str = "") -> list[dict]:
        q = "SELECT * FROM time_entries WHERE 1=1"
        p: list = []
        if person_id:
            q += " AND person_id=?"; p.append(person_id)
        if start:
            q += " AND date>=?"; p.append(start)
        if end:
            q += " AND date<=?"; p.append(end)
        q += " ORDER BY date DESC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, p).fetchall()]

    def delete_time(self, tid: str):
        with self._conn() as c:
            c.execute("DELETE FROM time_entries WHERE id=?", (tid,))

    # ── payroll runs ─────────────────────────────────────────────────────────

    def get_payroll_runs(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM payroll_runs ORDER BY pay_date DESC, created_at DESC").fetchall()]

    def get_payroll_lines(self, run_id: str) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM payroll_lines WHERE run_id=?", (run_id,)).fetchall()]

    def ytd_gross(self, person_id: str, before: str) -> float:
        """Finalized gross pay this calendar year before `before` (for wage-base caps)."""
        year = before[:4]
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(l.gross),0) FROM payroll_lines l JOIN payroll_runs r "
                "ON r.id=l.run_id WHERE l.person_id=? AND r.status='final' AND r.pay_date>=? "
                "AND r.pay_date<?", (person_id, f"{year}-01-01", before)).fetchone()
        return float(row[0] or 0)

    def calculate_run(self, run: dict, overrides: dict | None = None) -> list[dict]:
        """
        Paychecks for every active W-2 employee for this run. overrides:
        {person_id: {"hours": .., "gross": ..}}. Hours default to logged time.
        """
        from utils.payroll import load_tables, compute_paycheck, gross_for_period
        tables = load_tables(int((run.get("pay_date") or date.today().isoformat())[:4]))
        suta_rate = float(self.get_setting("suta_rate", "0") or 0)
        suta_base = float(self.get_setting("suta_wage_base", "0") or 0)
        state = self.get_setting("payroll_state", "")
        out = []
        for emp in self.get_employees():
            ov = (overrides or {}).get(emp["id"], {})
            if "hours" in ov:
                hours = float(ov["hours"] or 0)
            else:
                hours = sum(float(t["hours"] or 0) for t in self.get_time(
                    emp["id"], run.get("period_start", ""), run.get("period_end", "")))
            gross = ov.get("gross")
            if gross in (None, ""):
                gross = gross_for_period(emp, hours)
            ytd = self.ytd_gross(emp["id"], run.get("pay_date") or date.today().isoformat())
            pc = compute_paycheck(emp, gross, ytd, tables, suta_rate, suta_base, state)
            out.append(dict(pc, person_id=emp["id"], name=emp["name"], hours=hours,
                            stale_tables=tables.get("_stale", False)))
        return out

    def save_payroll_run(self, run: dict, lines: list[dict]) -> dict:
        cols = ("period_start", "period_end", "pay_date", "account_id", "status", "notes")
        with self._conn() as c:
            if run.get("id"):
                c.execute(f"UPDATE payroll_runs SET {','.join(k + '=?' for k in cols)} WHERE id=?",
                          [run.get(k, "") for k in cols] + [run["id"]])
            else:
                run["id"] = self._new_id()
                run.setdefault("status", "draft")
                c.execute(f"INSERT INTO payroll_runs (id,{','.join(cols)},created_at) VALUES "
                          f"(?,{','.join('?' * len(cols))},?)",
                          [run["id"]] + [run.get(k, "") for k in cols] + [self._now()])
            c.execute("DELETE FROM payroll_lines WHERE run_id=?", (run["id"],))
            for ln in lines:
                c.execute(f"INSERT INTO payroll_lines (id,run_id,person_id,{','.join(_LINE_NUM)}) "
                          f"VALUES (?,?,?,{','.join('?' * len(_LINE_NUM))})",
                          [self._new_id(), run["id"], ln["person_id"]] +
                          [float(ln.get(k) or 0) for k in _LINE_NUM])
        return run

    def _payroll_category(self, name: str) -> str:
        for c in self.get_categories():
            if c["name"] == name:
                return c["id"]
        return self.save_category({"name": name, "type": "expense",
                                   "tax_line": "26" if name == "Wages" else "23"})["id"]

    def finalize_payroll_run(self, run_id: str) -> list[str]:
        """Post a run's transactions (see module docstring). Returns txn ids."""
        from utils.money import money_sum
        with self._conn() as c:
            run = dict(c.execute("SELECT * FROM payroll_runs WHERE id=?", (run_id,)).fetchone())
        if run["status"] == "final":
            raise ValueError("This payroll run is already posted.")
        if not run.get("account_id"):
            raise ValueError("Choose the account paychecks are paid from.")
        lines = self.get_payroll_lines(run_id)
        if not lines:
            raise ValueError("No paychecks in this run.")
        liab = self.get_or_create_role_account("payroll_liabilities", "Payroll Liabilities",
                                               "liability")
        people = {p["id"]: p for p in self.get_people(active_only=False)}
        wages_cat = self._payroll_category("Wages")
        tax_cat = self._payroll_category("Payroll Taxes (Employer)")
        d = run["pay_date"]
        label = f"Payroll {run['period_start']} – {run['period_end']}"
        ids = []
        for ln in lines:
            name = people.get(ln["person_id"], {}).get("name", "Employee")
            t = self.save_transaction({"date": d, "account_id": liab["id"], "payee": name,
                                       "memo": f"{label}: gross wages", "amount": str(-ln["gross"]),
                                       "category_id": wages_cat, "charged_to": f"person:{ln['person_id']}"})
            ids.append(t["id"])
            if ln["net"]:
                src = self.save_transfer_pair({"date": d, "account_id": run["account_id"],
                                               "payee": name, "memo": f"{label}: net pay",
                                               "amount": str(ln["net"])}, liab["id"])
                ids.append(src["id"])
        employer = money_sum(ln["ss_er"] + ln["med_er"] + ln["futa"] + ln["suta"] for ln in lines)
        if employer:
            t = self.save_transaction({"date": d, "account_id": liab["id"], "payee": "Payroll taxes",
                                       "memo": f"{label}: employer taxes",
                                       "amount": str(-float(employer)), "category_id": tax_cat})
            ids.append(t["id"])
        with self._conn() as c:
            c.execute("UPDATE payroll_runs SET status='final', txn_ids=? WHERE id=?",
                      (",".join(ids), run_id))
            c.execute("UPDATE time_entries SET run_id=? WHERE run_id='' AND date>=? AND date<=? "
                      "AND person_id IN (SELECT person_id FROM payroll_lines WHERE run_id=?)",
                      (run_id, run["period_start"], run["period_end"], run_id))
        return ids

    def void_payroll_run(self, run_id: str):
        """Undo a posted run's transactions and return it to draft."""
        with self._conn() as c:
            run = dict(c.execute("SELECT * FROM payroll_runs WHERE id=?", (run_id,)).fetchone())
        for tid in (run.get("txn_ids") or "").split(","):
            t = self.get_transaction(tid) if tid else None
            if t:
                self.delete_transaction_full(t)
        with self._conn() as c:
            c.execute("UPDATE payroll_runs SET status='draft', txn_ids='' WHERE id=?", (run_id,))
            c.execute("UPDATE time_entries SET run_id='' WHERE run_id=?", (run_id,))

    def delete_payroll_run(self, run_id: str):
        self.void_payroll_run(run_id)
        with self._conn() as c:
            c.execute("DELETE FROM payroll_lines WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM payroll_runs WHERE id=?", (run_id,))

    def payroll_liability_balance(self) -> float:
        acct = self.get_account_by_role("payroll_liabilities")
        return -self.account_balance(acct["id"]) if acct else 0.0

    def pay_payroll_taxes(self, account_id: str, pay_date: str, amount, memo: str = "") -> dict:
        liab = self.get_or_create_role_account("payroll_liabilities", "Payroll Liabilities",
                                               "liability")
        return self.save_transfer_pair({"date": pay_date, "account_id": account_id,
                                        "payee": "IRS / state", "amount": str(amount),
                                        "memo": memo or "Payroll tax deposit"}, liab["id"])

    # ── 1099 contractors ─────────────────────────────────────────────────────

    def contractor_payments(self, year: int) -> list[dict]:
        """
        Money paid to each 1099-flagged contact in a calendar year (non-transfer
        outflows linked to the contact), with the 1099-NEC threshold check.
        """
        from utils.payroll import load_tables, nec_threshold
        from utils.money import money_sum, D
        threshold = nec_threshold(load_tables(year), year)
        out = []
        for c in self.get_contacts(active_only=False):
            if str(c.get("is_1099") or "0") != "1":
                continue
            txns = self.contact_activity(c["id"], f"{year}-01-01", f"{year}-12-31")
            paid = -money_sum(t["amount"] for t in txns
                              if str(t.get("is_transfer", 0)) != "1" and float(t["amount"] or 0) < 0)
            refunds = money_sum(t["amount"] for t in txns
                                if str(t.get("is_transfer", 0)) != "1" and float(t["amount"] or 0) > 0)
            total = paid - refunds
            out.append({"contact": c, "paid": total, "count": len(txns),
                        "needs_1099": total >= threshold, "threshold": threshold,
                        "missing_tax_id": not (c.get("tax_id") or "").strip()})
        out.sort(key=lambda r: -r["paid"])
        return out
