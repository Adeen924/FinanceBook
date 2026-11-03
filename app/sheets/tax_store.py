"""
Mileage log, budgets, sales-tax summary, home-office entry and report data —
mixed into sheets.client.Database. Tax amounts are estimates; see utils.taxes.
"""
from datetime import date

TAX_SCHEMA = """
CREATE TABLE IF NOT EXISTS mileage (
    id         TEXT PRIMARY KEY,
    date       TEXT DEFAULT '',
    from_loc   TEXT DEFAULT '',
    to_loc     TEXT DEFAULT '',
    purpose    TEXT DEFAULT '',
    miles      REAL DEFAULT 0,
    rate       REAL DEFAULT 0,
    vehicle    TEXT DEFAULT '',
    treatment  TEXT DEFAULT 'contribution',
    txn_id     TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS budgets (
    category_id TEXT PRIMARY KEY,
    monthly     REAL DEFAULT 0
);
"""


class TaxMixin:
    # ── report data ──────────────────────────────────────────────────────────

    def report_rows(self, start: str, end: str, with_computed: bool = True) -> list[dict]:
        """Normalised P&L rows (see utils.reports.pnl_rows) for [start, end]."""
        from utils.reports import pnl_rows
        cats = {c["id"]: c for c in self.get_categories()}
        computed = []
        if with_computed:
            for ln in self.computed_pnl_lines(start, end):
                computed.append(dict(ln, date=end))
        return pnl_rows(self.get_transactions(start=start, end=end, include_transfers=False),
                        cats, computed)

    # ── mileage ──────────────────────────────────────────────────────────────

    def mileage_category_id(self) -> str:
        cats = self.get_categories()
        hit = next((c for c in cats if c["name"] == "Mileage Deduction"), None)
        if hit:
            return hit["id"]
        parent = next((c for c in cats if c["name"] == "Car & Truck" and not c.get("parent_id")), None)
        return self.save_category({"name": "Mileage Deduction", "type": "expense",
                                   "parent_id": parent["id"] if parent else "",
                                   "tax_line": "9", "deduct_pct": 100})["id"]

    def get_mileage(self, start: str = "", end: str = "") -> list[dict]:
        q = "SELECT * FROM mileage WHERE 1=1"
        p: list = []
        if start:
            q += " AND date>=?"; p.append(start)
        if end:
            q += " AND date<=?"; p.append(end)
        q += " ORDER BY date DESC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, p).fetchall()]

    def add_mileage(self, data: dict) -> dict:
        """
        Log a business trip in your own vehicle and book the standard-mileage
        deduction (miles × IRS rate for that date) as a transaction:
        treatment 'contribution' (Owner's Capital) or 'reimburse' (Due to Owner).
        """
        from utils.payroll import load_tables
        from utils.taxes import mileage_rate
        from utils.money import cents, D
        from sheets.client import BUSINESS_ACCOUNTS
        d = data.get("date") or date.today().isoformat()
        rate = D(data.get("rate")) or mileage_rate(load_tables(int(d[:4])), d)
        amount = cents(D(data.get("miles")) * rate)
        role = "due_to_owner" if data.get("treatment") == "reimburse" else "owner_capital"
        name, atype = BUSINESS_ACCOUNTS[role]
        acct = self.get_or_create_role_account(role, name, atype)
        desc = f"{data.get('from_loc', '')} → {data.get('to_loc', '')}".strip(" →")
        t = self.save_transaction({
            "date": d, "account_id": acct["id"], "payee": "Mileage",
            "memo": f"{float(D(data.get('miles'))):g} mi @ ${rate} — {data.get('purpose', '')} {desc}"[:200],
            "amount": str(-amount), "category_id": self.mileage_category_id(),
            "paid_by": self.owner_person_id(),
            "reimbursable": "1" if role == "due_to_owner" else "0"})
        mid = self._new_id()
        with self._conn() as c:
            c.execute("INSERT INTO mileage (id,date,from_loc,to_loc,purpose,miles,rate,vehicle,"
                      "treatment,txn_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (mid, d, data.get("from_loc", ""), data.get("to_loc", ""),
                       data.get("purpose", ""), float(D(data.get("miles"))), float(rate),
                       data.get("vehicle", ""), data.get("treatment") or "contribution",
                       t["id"], self._now()))
        return {"id": mid, "amount": amount, "rate": rate, "txn_id": t["id"]}

    def delete_mileage(self, mid: str):
        with self._conn() as c:
            row = c.execute("SELECT txn_id FROM mileage WHERE id=?", (mid,)).fetchone()
            c.execute("DELETE FROM mileage WHERE id=?", (mid,))
        if row and row[0]:
            self.delete_transaction(row[0])

    # ── budgets ──────────────────────────────────────────────────────────────

    def get_budgets(self) -> dict:
        with self._conn() as c:
            return {r[0]: float(r[1] or 0) for r in c.execute("SELECT category_id, monthly FROM budgets")}

    def set_budget(self, category_id: str, monthly):
        with self._conn() as c:
            if float(monthly or 0) == 0:
                c.execute("DELETE FROM budgets WHERE category_id=?", (category_id,))
            else:
                c.execute("INSERT OR REPLACE INTO budgets (category_id, monthly) VALUES (?,?)",
                          (category_id, float(monthly)))

    # ── sales tax ────────────────────────────────────────────────────────────

    def sales_tax_summary(self, start: str, end: str) -> dict:
        """Collected (into Sales Tax Payable) and remitted, by month, plus amount owed at end."""
        from utils.money import D, cents, ZERO
        acct = self.get_account_by_role("sales_tax_payable")
        months: dict = {}
        owed = ZERO
        if acct:
            owed = -D(self.account_balance_as_of(acct["id"], end))
            for t in self.get_transactions(account_id=acct["id"], start=start, end=end):
                m = months.setdefault(t["date"][:7], {"collected": ZERO, "remitted": ZERO})
                amt = D(t["amount"])
                if amt < 0:
                    m["collected"] += -amt
                else:
                    m["remitted"] += amt
        rows = [{"month": k, "collected": cents(v["collected"]), "remitted": cents(v["remitted"])}
                for k, v in sorted(months.items())]
        return {"rows": rows, "collected": cents(sum((r["collected"] for r in rows), ZERO)),
                "remitted": cents(sum((r["remitted"] for r in rows), ZERO)), "owed": cents(owed)}

    def remit_sales_tax(self, account_id: str, pay_date: str, amount, memo: str = "") -> dict:
        stp = self.get_or_create_role_account("sales_tax_payable", "Sales Tax Payable", "liability")
        return self.save_transfer_pair({"date": pay_date, "account_id": account_id,
                                        "payee": "State sales tax", "amount": str(amount),
                                        "memo": memo or "Sales tax payment"}, stp["id"])

    def account_balance_as_of(self, account_id: str, as_of: str) -> float:
        acct = self.get_account(account_id)
        if not acct:
            return 0.0
        with self._conn() as c:
            row = c.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE account_id=? "
                            "AND date<=?", (account_id, as_of)).fetchone()
        return float(acct.get("opening_balance") or 0) + float(row[0] or 0)

    # ── home office ──────────────────────────────────────────────────────────

    def record_home_office(self, year: int, amount, method: str) -> dict:
        """Book (or replace) the year's home-office deduction as an owner contribution."""
        key = f"home_office_txn_{year}"
        old = self.get_setting(key)
        if old and self.get_transaction(old):
            self.delete_transaction(old)
        cat = next((c["id"] for c in self.get_categories() if c["name"] == "Home Office"), "")
        if not cat:
            cat = self.save_category({"name": "Home Office", "type": "expense", "tax_line": "30"})["id"]
        cap = self.get_account_by_role("owner_capital") or self.get_or_create_role_account(
            "owner_capital", "Owner's Equity / Capital Contributions", "equity")
        t = self.save_transaction({"date": f"{year}-12-31", "account_id": cap["id"],
                                   "payee": "Home office", "memo": f"Home office deduction ({method})",
                                   "amount": str(-abs(float(amount))), "category_id": cat,
                                   "paid_by": self.owner_person_id()})
        self.set_setting(key, t["id"])
        return t
