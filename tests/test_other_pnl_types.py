"""v1.14 account types: Other Income / Other Expense categories (in net income,
reported in their own P&L section) and Fixed Assets / Other Assets accounts
(balance-sheet assets)."""
import os
import shutil
import tempfile
import unittest
from decimal import Decimal

import tests  # noqa: F401
from sheets.client import Database
from utils import ledger as L
from utils import reports as RP
from reports_export import pnl_csv_rows

CATS = {
    "inc": {"id": "inc", "name": "Sales", "type": "income", "parent_id": ""},
    "exp": {"id": "exp", "name": "Supplies", "type": "expense", "parent_id": ""},
    "oi":  {"id": "oi", "name": "Interest Earned", "type": "other_income", "parent_id": ""},
    "oe":  {"id": "oe", "name": "Penalties", "type": "other_expense", "parent_id": ""},
}
TXNS = [
    {"id": "1", "date": "2026-02-01", "account_id": "A", "amount": "1000", "category_id": "inc"},
    {"id": "2", "date": "2026-02-02", "account_id": "A", "amount": "-300", "category_id": "exp"},
    {"id": "3", "date": "2026-02-03", "account_id": "A", "amount": "25", "category_id": "oi"},
    {"id": "4", "date": "2026-02-04", "account_id": "A", "amount": "-40", "category_id": "oe"},
]


class PnlSectionTests(unittest.TestCase):
    def test_sections_and_buckets(self):
        got = [(L.pnl_section(t, CATS), L.pnl_bucket(t, CATS)) for t in TXNS]
        self.assertEqual(got, [("income", "income"), ("expense", "expense"),
                               ("other_income", "income"), ("other_expense", "expense")])

    def test_included_in_net_income(self):
        self.assertEqual(L.net_income(TXNS, CATS), Decimal("685.00"))

    def test_sign_helpers(self):
        self.assertEqual(L.category_sign("other_expense"), -1)
        self.assertEqual(L.category_sign("other_income"), 1)
        self.assertTrue(L.is_income_type("other_income"))
        self.assertFalse(L.is_income_type("other_expense"))

    def test_pnl_rows_flag_and_csv_order(self):
        rows = RP.pnl_rows(TXNS, CATS)
        self.assertEqual([r["other"] for r in rows], [False, False, True, True])
        self.assertEqual([RP.pnl_section_label(r) for r in rows],
                         ["Income", "Expense", "Other Income", "Other Expense"])
        csv_rows = pnl_csv_rows(rows)
        self.assertEqual(csv_rows, [
            ["Income", "Sales", "1000.00"],
            ["Expense", "Supplies", "-300.00"],
            ["", "Net operating income", "700.00"],
            ["Other Income", "Interest Earned", "25.00"],
            ["Other Expense", "Penalties", "-40.00"],
            ["", "Net income", "685.00"],
        ])

    def test_no_other_section_without_other_rows(self):
        rows = RP.pnl_rows(TXNS[:2], CATS)
        self.assertNotIn("Net operating income", [r[1] for r in pnl_csv_rows(rows)])

    def test_schedule_c_unchanged_for_mapped_line(self):
        cats = dict(CATS, oi=dict(CATS["oi"], tax_line="6"))
        sc = RP.schedule_c(RP.pnl_rows(TXNS, cats), cats)
        self.assertEqual(sc["other_income"], Decimal("25.00"))

    def test_budget_counts_other_income_as_income(self):
        rows = RP.pnl_rows(TXNS, CATS)
        out = {r["category_id"]: r for r in
               RP.budget_vs_actual(rows, {"oi": 10}, CATS, "2026-02-01", "2026-02-28")}
        self.assertTrue(out["oi"]["income"])
        self.assertFalse(out["oe"]["income"])


class AssetAccountTypeTests(unittest.TestCase):
    def test_account_class_explicit(self):
        for t in ("fixed assets", "other assets", "checking", "other"):
            self.assertEqual(L.account_class({"type": t}), "asset", t)
        self.assertEqual(L.account_class({"type": "loan"}), "liability")

    def test_balance_sheet_and_cash_flow(self):
        accounts = [{"id": "A", "name": "Checking", "type": "checking", "opening_balance": "5000"},
                    {"id": "F", "name": "Truck", "type": "fixed assets", "opening_balance": "0"},
                    {"id": "O", "name": "Deposit", "type": "other assets", "opening_balance": "0"}]
        txns = [
            {"id": "t1", "date": "2026-03-01", "account_id": "A", "amount": "-3000",
             "is_transfer": "1", "transfer_pair_id": "p1"},
            {"id": "t2", "date": "2026-03-01", "account_id": "F", "amount": "3000",
             "is_transfer": "1", "transfer_pair_id": "p1"},
            {"id": "t3", "date": "2026-03-02", "account_id": "A", "amount": "-500",
             "is_transfer": "1", "transfer_pair_id": "p2"},
            {"id": "t4", "date": "2026-03-02", "account_id": "O", "amount": "500",
             "is_transfer": "1", "transfer_pair_id": "p2"},
        ]
        bs = L.balance_sheet(accounts, txns, {}, "2026-12-31")
        assets = {n: v for n, v, _ in bs["assets"]}
        self.assertEqual(assets, {"Checking": Decimal("1500.00"), "Truck": Decimal("3000.00"),
                                  "Deposit": Decimal("500.00")})
        self.assertEqual(bs["total_assets"], Decimal("5000.00"))
        self.assertTrue(bs["balanced"])
        self.assertEqual(bs["other_adjustments"], 0)
        cf = RP.cash_flow(accounts, txns, {}, "2026-01-01", "2026-12-31")
        self.assertEqual(cf["sections"]["investing"], {"Fixed assets bought / sold": Decimal("-3000.00")})
        self.assertEqual(cf["sections"]["financing"], {"Other transfers": Decimal("-500.00")})


class FixedAssetsNotDoubleCountedTests(unittest.TestCase):
    """The Fixed Assets module books purchases to an "asset" category (money
    leaves the bank, a computed line adds the asset). A "fixed assets" account
    is separate: the computed line never includes it, and it has no effect on
    the module's numbers."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "b.db"), book_type="business")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_account_and_module_are_separate(self):
        db = self.db
        bank = db.save_account({"name": "Bank", "type": "checking", "opening_balance": "10000"})
        land = db.save_account({"name": "Land", "type": "fixed assets"})
        db.save_transfer_pair({"date": "2026-01-10", "account_id": bank["id"],
                               "amount": "-4000"}, land["id"], keep_sign=True)
        before = db.balance_sheet_extras("2026-12-31", "2026-01-01")
        self.assertEqual(before["extra_assets"], [])
        from utils.ledger import balance_sheet
        cats = {c["id"]: c for c in db.get_categories()}
        bs = balance_sheet(db.get_accounts(active=None), db.get_transactions(), cats,
                           "2026-12-31", "2026-01-01", **before)
        assets = {n: v for n, v, _ in bs["assets"]}
        self.assertEqual(assets["Land"], Decimal("4000.00"))
        self.assertEqual(bs["total_assets"], Decimal("10000.00"))
        self.assertTrue(bs["balanced"])


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    _HAVE_QT = True
except Exception:       # pragma: no cover
    _HAVE_QT = False


@unittest.skipUnless(_HAVE_QT, "PyQt6 not available")
class ReportsScreenTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "r.db"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_pnl_tree_has_other_section(self):
        from PyQt6.QtCore import QDate
        from ui.pages.reports import ReportsPage
        db = self.db
        acct = db.save_account({"name": "Checking", "type": "checking"})["id"]
        oi = db.save_category({"name": "Interest Earned", "type": "other_income"})["id"]
        oe = db.save_category({"name": "Penalties", "type": "other_expense"})["id"]
        sal = next(c for c in db.get_categories() if c["type"] == "income")["id"]
        for amt, cat in (("1000", sal), ("25", oi), ("-40", oe)):
            db.save_transaction({"date": "2026-02-01", "account_id": acct, "amount": amt,
                                 "category_id": cat})
        page = ReportsPage(db)
        page._date_from.setDate(QDate(2026, 1, 1))
        page._date_to.setDate(QDate(2026, 12, 31))
        page._tabs.setCurrentIndex(0)
        page._run_pnl()
        tops = {page._pnl_tree.topLevelItem(i).text(0): page._pnl_tree.topLevelItem(i).text(1)
                for i in range(page._pnl_tree.topLevelItemCount())}
        self.assertEqual(tops["Net Operating Income"], "$1,000.00")
        self.assertIn("Other Income / Other Expense", tops)
        self.assertEqual(tops["Net Income"], "$985.00")
        path = os.path.join(self.dir, "pnl.csv")
        page._export_pnl_csv(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Other Income", text)
        self.assertIn("Other Expense", text)
