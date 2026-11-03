import unittest
from decimal import Decimal

import tests  # noqa: F401
from utils import payroll as P

T = P.load_tables(2026)


class WithholdingTests(unittest.TestCase):
    def test_tables_loaded(self):
        self.assertEqual(T["year"], 2026)
        self.assertFalse(T["_stale"])
        self.assertEqual(T["social_security"]["wage_base"], 184500)

    def test_single_biweekly(self):
        # 2,000 × 26 = 52,000 − 8,600 = 43,400 → 1,240 + 12% × 23,500 = 4,060 → /26
        self.assertEqual(P.federal_withholding(2000, 26, {"filing_status": "single"}, T),
                         Decimal("156.15"))

    def test_mfj_monthly_with_credits_and_extra(self):
        # 72,000 − 12,900 = 59,100 → 2,480 + 12% × 15,000 = 4,280/yr = 356.67/mo
        self.assertEqual(P.federal_withholding(6000, 12, {"filing_status": "mfj"}, T),
                         Decimal("356.67"))
        # Step 3 $2,000 credit → −166.67; Step 4(c) +25
        self.assertEqual(P.federal_withholding(6000, 12, {"filing_status": "mfj", "step3": 2000,
                                                          "step4c": 25}, T), Decimal("215.00"))

    def test_below_threshold_is_zero(self):
        self.assertEqual(P.federal_withholding(300, 26, {"filing_status": "single"}, T), Decimal("0.00"))

    def test_top_bracket(self):
        # 800,000 annual single: 800,000 − 8,600 = 791,400 → 192,979.25 + 37% × 143,300
        w = P.federal_withholding(800000, 1, {"filing_status": "single"}, T)
        self.assertEqual(w, Decimal("192979.25") + Decimal("53021.00"))


class FicaTests(unittest.TestCase):
    def test_wage_base_cap(self):
        f = P.fica(10000, 180000, T)
        self.assertEqual(f["ss_ee"], Decimal("279.00"))        # only 4,500 left × 6.2%
        self.assertEqual(f["med_ee"], Decimal("145.00"))

    def test_additional_medicare_over_200k(self):
        f = P.fica(10000, 195000, T)
        self.assertEqual(f["addl_med"], Decimal("45.00"))      # 5,000 × 0.9%
        self.assertEqual(P.fica(1000, 250000, T)["addl_med"], Decimal("9.00"))
        self.assertEqual(P.fica(1000, 0, T)["addl_med"], Decimal("0.00"))


class PaycheckTests(unittest.TestCase):
    EMP = {"pay_type": "hourly", "pay_rate": 25, "pay_frequency": "biweekly",
           "filing_status": "single", "state_rate": 4}

    def test_paycheck(self):
        gross = P.gross_for_period(self.EMP, 80)
        self.assertEqual(gross, Decimal("2000.00"))
        p = P.compute_paycheck(self.EMP, gross, 0, T, suta_rate=2.7, suta_base=7000)
        self.assertEqual(p["fit"], Decimal("156.15"))
        self.assertEqual(p["ss_ee"], Decimal("124.00"))
        self.assertEqual(p["med_ee"], Decimal("29.00"))
        self.assertEqual(p["state"], Decimal("80.00"))
        self.assertEqual(p["net"], Decimal("1610.85"))
        self.assertEqual(p["futa"], Decimal("12.00"))
        self.assertEqual(p["suta"], Decimal("54.00"))
        self.assertEqual(p["employer_tax"], Decimal("219.00"))

    def test_futa_suta_caps(self):
        p = P.compute_paycheck(self.EMP, 2000, 6500, T, suta_rate=2.7, suta_base=7000)
        self.assertEqual(p["futa"], Decimal("3.00"))           # 500 left × 0.6%
        self.assertEqual(p["suta"], Decimal("13.50"))

    def test_unsupported_w4_is_flagged(self):
        p = P.compute_paycheck(dict(self.EMP, w4_step2="1"), 2000, 0, T)
        self.assertTrue(p["warnings"])
        self.assertEqual(p["fit"], Decimal("0.00"))

    def test_salary(self):
        self.assertEqual(P.gross_for_period({"pay_type": "salary", "pay_rate": 78000,
                                             "pay_frequency": "semimonthly"}, 0), Decimal("3250.00"))

    def test_1099_threshold_by_year(self):
        self.assertEqual(P.nec_threshold(T, 2026), Decimal("2000"))
        self.assertEqual(P.nec_threshold(T, 2025), Decimal("600"))
        self.assertEqual(P.nec_threshold(T, 2027), Decimal("2000"))


class PayrollBooksTests(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        from sheets.client import Database
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.bank = self.db.save_account({"name": "Checking", "type": "checking",
                                          "opening_balance": "10000"})
        self.emp = self.db.save_employee({"name": "Sam", "role": "employee", "employment_type": "w2",
                                          "pay_type": "hourly", "pay_rate": 25,
                                          "pay_frequency": "biweekly", "filing_status": "single",
                                          "state_rate": 4})
        self.db.set_setting("suta_rate", "2.7")
        self.db.set_setting("suta_wage_base", "7000")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_run_posts_and_balances(self):
        from utils.ledger import balance_sheet
        db = self.db
        db.add_time(self.emp["id"], "2026-03-02", 40)
        db.add_time(self.emp["id"], "2026-03-09", 40)
        run = {"period_start": "2026-03-01", "period_end": "2026-03-14", "pay_date": "2026-03-16",
               "account_id": self.bank["id"]}
        lines = db.calculate_run(run)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["gross"], Decimal("2000.00"))
        run = db.save_payroll_run(run, lines)
        db.finalize_payroll_run(run["id"])
        self.assertEqual(round(db.account_balance(self.bank["id"]), 2), 10000 - 1610.85)
        owed = round(db.payroll_liability_balance(), 2)
        self.assertEqual(owed, round(2000 - 1610.85 + 219.00, 2))   # withheld + employer
        cats = {c["id"]: c for c in db.get_categories()}
        bs = balance_sheet(db.get_accounts(active=None), db.get_transactions(), cats, "2026-12-31")
        self.assertTrue(bs["balanced"])
        self.assertEqual(bs["other_adjustments"], Decimal("0"))
        self.assertEqual(dict((n, v) for n, v, _ in bs["equity"])["Net income (this year)"],
                         Decimal("-2219.00"))
        db.pay_payroll_taxes(self.bank["id"], "2026-04-15", owed)
        self.assertEqual(round(db.payroll_liability_balance(), 2), 0.0)
        # YTD feeds the next run's wage-base math; voiding removes everything.
        self.assertEqual(db.ytd_gross(self.emp["id"], "2026-12-31"), 2000.0)
        db.void_payroll_run(run["id"])
        self.assertEqual(round(db.account_balance(self.bank["id"]), 2), 10000 - owed)

    def test_owner_never_on_payroll(self):
        self.assertTrue(all(p.get("employment_type") == "w2" for p in self.db.get_employees()))
        self.assertNotIn(self.db.owner_person_id(), [p["id"] for p in self.db.get_employees()])

    def test_1099_summary(self):
        db = self.db
        jane = db.save_contact({"name": "Jane", "kind": "vendor", "is_1099": "1"})
        bob = db.save_contact({"name": "Bob", "kind": "vendor", "is_1099": "1", "tax_id": "12-3456789"})
        for amt, who in ((1500, jane), (700, jane), (900, bob)):
            db.save_transaction({"date": "2026-05-01", "account_id": self.bank["id"],
                                 "amount": str(-amt), "contact_id": who["id"], "payee": who["name"]})
        rows = {r["contact"]["name"]: r for r in db.contractor_payments(2026)}
        self.assertEqual(rows["Jane"]["paid"], Decimal("2200.00"))
        self.assertTrue(rows["Jane"]["needs_1099"])
        self.assertTrue(rows["Jane"]["missing_tax_id"])
        self.assertFalse(rows["Bob"]["needs_1099"])          # under $2,000 for 2026


if __name__ == "__main__":
    unittest.main()
