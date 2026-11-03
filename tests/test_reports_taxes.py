import unittest
from decimal import Decimal

import tests  # noqa: F401
from utils import reports as RP
from utils import taxes as TX
from utils.payroll import load_tables

T = load_tables(2026)
CATS = {
    "sales": {"id": "sales", "name": "Sales", "type": "income", "tax_line": "1", "deduct_pct": "100"},
    "meal": {"id": "meal", "name": "Meals (50%)", "type": "expense", "tax_line": "24b", "deduct_pct": "50"},
    "ads": {"id": "ads", "name": "Advertising", "type": "expense", "tax_line": "8", "deduct_pct": "100"},
    "fb": {"id": "fb", "name": "Facebook", "type": "expense", "parent_id": "ads", "tax_line": "", "deduct_pct": "100"},
    "fa": {"id": "fa", "name": "Fixed Assets", "type": "asset"},
    "misc": {"id": "misc", "name": "Misc", "type": "expense"},
}
ACCTS = [{"id": "chk", "name": "Checking", "type": "checking", "opening_balance": "1000"},
         {"id": "cap", "name": "Capital", "type": "equity", "role": "owner_capital"},
         {"id": "cc", "name": "Visa", "type": "credit card"},
         {"id": "drw", "name": "Draws", "type": "equity", "role": "owner_draws"}]


def t(i, d, a, amt, cat="", tr="0", pair="", cls=""):
    return {"id": i, "date": d, "account_id": a, "amount": str(amt), "category_id": cat,
            "is_transfer": tr, "transfer_pair_id": pair, "class_id": cls}


TXNS = [t("1", "2026-01-05", "chk", 5000, "sales", cls="X"),
        t("2", "2026-01-10", "chk", -200, "meal"),
        t("3", "2026-02-01", "cc", -300, "fb", cls="X"),
        t("4", "2026-02-15", "chk", -1200, "fa"),
        t("5", "2026-03-01", "chk", -300, tr="1", pair="p1"),
        t("6", "2026-03-01", "cc", 300, tr="1", pair="p1"),
        t("7", "2026-03-05", "cap", -2000, tr="1", pair="p2"),
        t("8", "2026-03-05", "chk", 2000, tr="1", pair="p2"),
        t("9", "2026-03-20", "chk", -500, tr="1", pair="p3"),
        t("10", "2026-03-20", "drw", 500, tr="1", pair="p3"),
        t("11", "2026-03-25", "chk", -50, "misc")]


class ReportTests(unittest.TestCase):
    def test_pivot_by_month_and_class(self):
        rows = RP.pnl_rows(TXNS, CATS)
        g = RP.pivot(rows, lambda r: r["group"], lambda r: r["month"])
        self.assertEqual(g["Sales"]["2026-01"], Decimal("5000.00"))
        self.assertEqual(g["Advertising"]["2026-02"], Decimal("-300.00"))
        self.assertEqual(g["__total__"]["__total__"], Decimal("4450.00"))
        c = RP.pivot(rows, lambda r: r["group"], lambda r: r["class_id"] or "none")
        self.assertEqual(c["__total__"]["X"], Decimal("4700.00"))

    def test_schedule_c_inherits_lines_and_halves_meals(self):
        rows = RP.pnl_rows(TXNS, CATS, [{"section": "expense", "group": "Depreciation",
                                        "amount": Decimal("-100"), "computed": "depreciation"}])
        sc = RP.schedule_c(rows, CATS)
        self.assertEqual(sc["lines"]["8"]["deductible"], Decimal("300.00"))     # child inherits 8
        self.assertEqual(sc["lines"]["24b"]["book"], Decimal("200.00"))
        self.assertEqual(sc["lines"]["24b"]["deductible"], Decimal("100.00"))
        self.assertEqual(sc["lines"]["13"]["deductible"], Decimal("100.00"))
        self.assertEqual(sc["unmapped"], Decimal("-50.00"))
        self.assertEqual(sc["net_profit"], Decimal("4500.00"))   # 5000 − 300 − 100 − 100

    def test_cash_flow_reconciles(self):
        cf = RP.cash_flow(ACCTS, TXNS, CATS, "2026-01-01", "2026-12-31")
        self.assertEqual(cf["begin"], Decimal("1000.00"))
        self.assertEqual(cf["totals"]["investing"], Decimal("-1200.00"))
        self.assertEqual(cf["sections"]["financing"]["Owner draws"], Decimal("-500.00"))
        self.assertEqual(cf["sections"]["financing"]["Owner contributions & loans"], Decimal("2000.00"))
        self.assertEqual(cf["sections"]["operating"]["Credit card payments"], Decimal("-300.00"))
        self.assertEqual(cf["end"], Decimal("5750.00"))
        self.assertEqual(cf["begin"] + cf["net_change"], cf["end"])

    def test_budget_vs_actual(self):
        rows = RP.pnl_rows(TXNS, CATS)
        out = {r["name"]: r for r in RP.budget_vs_actual(rows, {"ads": 100, "sales": 1000},
                                                         CATS, "2026-01-01", "2026-03-31")}
        self.assertEqual(out["Advertising"]["budget"], Decimal("300.00"))
        self.assertEqual(out["Advertising"]["actual"], Decimal("300.00"))
        self.assertEqual(out["Advertising"]["variance"], Decimal("0.00"))
        self.assertEqual(out["Sales"]["variance"], Decimal("2000.00"))


class TaxHelperTests(unittest.TestCase):
    def test_mileage_rate_changes_mid_2026(self):
        self.assertEqual(TX.mileage_rate(T, "2026-03-01"), Decimal("0.725"))
        self.assertEqual(TX.mileage_rate(T, "2026-08-01"), Decimal("0.76"))

    def test_home_office(self):
        h = TX.home_office(T, 400, 2000, 24000)
        self.assertEqual(h["simplified"], Decimal("1500.00"))   # capped at 300 sq ft
        self.assertEqual(h["actual"], Decimal("4800.00"))       # 20% of 24,000
        self.assertEqual(h["better"], "actual")
        self.assertEqual(TX.home_office(T, 100, gross_income_limit=200)["simplified"], Decimal("200.00"))

    def test_estimated_tax(self):
        e = TX.estimated_tax(T, 30000, "2026-07-01", "single")   # 6 months → 60,000
        self.assertEqual(e["projected_profit"], Decimal("60000.00"))
        self.assertEqual(e["se_tax"], Decimal("8477.73"))        # 55,410 × 15.3%
        self.assertEqual(e["qbi"], Decimal("11152.23"))           # 20% × (60,000 − 4,238.87)
        self.assertGreater(e["income_tax"], Decimal("0"))
        self.assertEqual(e["quarterly"], (e["total"] / 4).quantize(Decimal("0.01")))
        self.assertEqual(TX.next_due_dates(T, "2026-09-16"), ["2027-01-15"])


class StoreTests(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        from sheets.client import Database
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_mileage_books_deduction_at_date_rate(self):
        r = self.db.add_mileage({"date": "2026-08-03", "miles": 100, "purpose": "Client visit",
                                 "treatment": "reimburse"})
        self.assertEqual(r["amount"], Decimal("76.00"))
        due = self.db.get_account_by_role("due_to_owner")
        self.assertEqual(round(self.db.account_balance(due["id"]), 2), -76.0)
        self.db.delete_mileage(r["id"])
        self.assertEqual(round(self.db.account_balance(due["id"]), 2), 0.0)

    def test_year_end_package_writes_files(self):
        import os
        from reports_export import export_year_end
        bank = self.db.save_account({"name": "Chk", "type": "checking", "opening_balance": "100"})
        cats = {c["name"]: c["id"] for c in self.db.get_categories()}
        self.db.save_transaction({"date": "2026-02-01", "account_id": bank["id"], "amount": "500",
                                  "category_id": cats["Consulting"]})
        self.db.record_home_office(2026, 750, "simplified")
        files = export_year_end(self.db, 2026, self.dir)
        names = {os.path.basename(f) for f in files}
        self.assertIn("schedule_c_summary_2026.csv", names)
        self.assertIn("balance_sheet_2026-12-31.csv", names)
        with open(next(f for f in files if f.endswith("schedule_c_summary_2026.csv")),
                  encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Line 30", text)
        self.assertIn("Net profit (estimate),,-250.00", text)


if __name__ == "__main__":
    unittest.main()
