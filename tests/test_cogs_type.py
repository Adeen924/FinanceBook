"""v1.18 A4: a "Cost of Goods Sold" account type (stored "cogs").

Spending (sign −1), its own Chart of Accounts group between Income and
Expense, its own P&L section followed by Gross Profit, in net income, on
Schedule C line 4. Business books convert the built-in Cost of Goods Sold tree
(once, only while it is still "expense") without changing net income."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database
from utils import ledger as L
from utils import reports as RP
from utils import chart_of_accounts as COA
from reports_export import pnl_csv_rows

CATS = {
    "inc": {"id": "inc", "name": "Sales", "type": "income", "parent_id": "", "tax_line": "1"},
    "cogs": {"id": "cogs", "name": "Materials", "type": "cogs", "parent_id": ""},
    "exp": {"id": "exp", "name": "Supplies", "type": "expense", "parent_id": "", "tax_line": "22"},
    "oi": {"id": "oi", "name": "Interest Earned", "type": "other_income", "parent_id": ""},
}
TXNS = [
    {"id": "1", "date": "2026-02-01", "account_id": "A", "amount": "1000", "category_id": "inc"},
    {"id": "2", "date": "2026-02-02", "account_id": "A", "amount": "-400", "category_id": "cogs"},
    {"id": "3", "date": "2026-02-03", "account_id": "A", "amount": "-100", "category_id": "exp"},
    {"id": "4", "date": "2026-02-04", "account_id": "A", "amount": "20", "category_id": "oi"},
]


class TypeTests(unittest.TestCase):
    def test_type_is_offered(self):
        from ui.pages.categories import CATEGORY_TYPES, type_label
        self.assertIn(("Cost of Goods Sold", "cogs"), CATEGORY_TYPES)
        self.assertEqual(type_label("cogs"), "Cost of Goods Sold")
        self.assertEqual(COA.resolve_choice("cogs"), ("category", "cogs"))
        self.assertIn(("Cost of Goods Sold", "cogs"), COA.NEW_ACCOUNT_CHOICES)

    def test_own_group_between_income_and_expense(self):
        keys = [g.key for g in COA.GROUPS]
        self.assertLess(keys.index("income"), keys.index("cogs"))
        self.assertLess(keys.index("cogs"), keys.index("expense"))
        rows = COA.chart_rows([], list(CATS.values()))
        self.assertEqual([g.label for g, _ in rows],
                         ["Income", "Other Income", "Cost of Goods Sold", "Expense"])

    def test_sign_and_sections(self):
        self.assertEqual(L.category_sign("cogs"), -1)
        self.assertFalse(L.is_income_type("cogs"))
        t = TXNS[1]
        self.assertEqual(L.pnl_section(t, CATS), "cogs")
        self.assertEqual(L.pnl_bucket(t, CATS), "expense")
        self.assertEqual(L.net_income(TXNS, CATS), Decimal("520.00"))

    def test_unknown_type_falls_back_to_expense(self):
        """What an older app does with "cogs": the same net income."""
        cats = dict(CATS, cogs=dict(CATS["cogs"], type="something new"))
        self.assertEqual(L.pnl_bucket(TXNS[1], cats), "expense")
        self.assertEqual(L.net_income(TXNS, cats), L.net_income(TXNS, CATS))


class ReportOrderTests(unittest.TestCase):
    def test_section_order_and_label(self):
        self.assertEqual(RP.PNL_SECTION_ORDER,
                         ("Income", "Cost of Goods Sold", "Expense", "Other Income",
                          "Other Expense"))
        rows = RP.pnl_rows(TXNS, CATS)
        self.assertEqual([RP.pnl_section_label(r) for r in rows],
                         ["Income", "Cost of Goods Sold", "Expense", "Other Income"])

    def test_csv_has_gross_profit(self):
        out = pnl_csv_rows(RP.pnl_rows(TXNS, CATS))
        self.assertEqual(out, [
            ["Income", "Sales", "1000.00"],
            ["Cost of Goods Sold", "Materials", "-400.00"],
            ["", "Gross profit", "600.00"],
            ["Expense", "Supplies", "-100.00"],
            ["", "Net operating income", "500.00"],
            ["Other Income", "Interest Earned", "20.00"],
            ["", "Net income", "520.00"],
        ])

    def test_computed_inventory_cogs_is_in_the_cogs_section(self):
        rows = RP.pnl_rows(TXNS[:1], CATS, [
            {"date": "2026-02-28", "section": "expense", "group": "Cost of Goods Sold",
             "name": "Inventory sold (computed)", "amount": Decimal("-75"),
             "computed": "cogs"}])
        self.assertEqual(RP.pnl_section_label(rows[-1]), "Cost of Goods Sold")
        self.assertIn(["", "Gross profit", "925.00"], pnl_csv_rows(rows))

    def test_schedule_c_line_4(self):
        sc = RP.schedule_c(RP.pnl_rows(TXNS, CATS), CATS)
        self.assertEqual(sc["cogs"], Decimal("400.00"))
        self.assertEqual(sc["lines"]["4"]["book"], Decimal("400.00"))

    def test_budget_vs_actual_order(self):
        rows = RP.pnl_rows(TXNS, CATS)
        names = [r["name"] for r in RP.budget_vs_actual(rows, {}, CATS, "2026-02-01",
                                                         "2026-02-28")]
        self.assertEqual(names, ["Sales", "Materials", "Supplies", "Interest Earned"])

    def test_cash_flow_line(self):
        accts = [{"id": "A", "name": "Checking", "type": "checking", "opening_balance": "0"}]
        cf = RP.cash_flow(accts, TXNS, CATS, "2026-01-01", "2026-12-31")
        op = cf["sections"]["operating"]
        self.assertEqual(op["Cash paid for cost of goods sold"], Decimal("-400.00"))
        self.assertEqual(list(op), ["Cash received from customers",
                                    "Cash paid for cost of goods sold",
                                    "Cash paid for expenses"])


class BusinessUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "biz.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def cogs_tree(self, db):
        cats = db.get_categories(include_inactive=True)
        root = next(c for c in cats if c["name"] == "Cost of Goods Sold" and not c["parent_id"])
        return root, [c for c in cats if c["parent_id"] == root["id"]]

    def make_old_book(self):
        """A business book as v1.17 left it: COGS tree typed "expense"."""
        db = Database(self.path, book_type="business")
        root, kids = self.cogs_tree(db)
        chk = db.save_account({"name": "Bank", "type": "checking"})["id"]
        sales = next(c["id"] for c in db.get_categories() if c["name"] == "Product Sales")
        mats = next(c["id"] for c in kids if c["name"] == "Materials")
        for d, amt, cat in (("2026-01-05", "2000", sales), ("2026-01-06", "-700", mats),
                            ("2026-01-07", "-50", root["id"])):
            db.save_transaction({"date": d, "account_id": chk, "payee": "x", "amount": amt,
                                 "category_id": cat})
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE categories SET type='expense' WHERE id=? OR parent_id=?",
                      (root["id"], root["id"]))
            c.execute("DELETE FROM settings WHERE key='cogs_type_converted'")
        c.close()
        return root["id"]

    def net(self, db):
        rows = db.report_rows("2026-01-01", "2026-12-31")
        return sum((r["amount"] for r in rows), Decimal(0))

    def test_new_business_book_has_cogs_type(self):
        db = Database(self.path, book_type="business")
        root, kids = self.cogs_tree(db)
        self.assertEqual(root["type"], "cogs")
        self.assertEqual({k["name"] for k in kids},
                         {"Materials", "Purchases for Resale", "Freight-in"})
        self.assertTrue(all(k["type"] == "cogs" for k in kids))

    def test_upgrade_converts_once_with_unchanged_net_income(self):
        root_id = self.make_old_book()
        with sqlite3.connect(self.path) as c:
            types = {r[0] for r in c.execute(
                "SELECT type FROM categories WHERE id=? OR parent_id=?", (root_id, root_id))}
        c.close()
        self.assertEqual(types, {"expense"})
        before_db = Database(self.path, book_type=None)     # opening runs the upgrade
        root, kids = self.cogs_tree(before_db)
        self.assertEqual(root["type"], "cogs")
        self.assertTrue(all(k["type"] == "cogs" for k in kids))
        self.assertEqual(self.net(before_db), Decimal("1250.00"))
        # Net income is the same as with the "expense" types.
        cats = before_db.category_map()
        old_cats = {k: dict(v, type="expense" if v["type"] == "cogs" else v["type"])
                    for k, v in cats.items()}
        txns = before_db.get_transactions()
        self.assertEqual(L.net_income(txns, cats), L.net_income(txns, old_cats))
        # Idempotent: a user who later changes it back keeps their choice.
        before_db.save_category(dict(root, type="expense"))
        again = Database(self.path)
        self.assertEqual(self.cogs_tree(again)[0]["type"], "expense")

    def test_not_converted_when_user_changed_the_type(self):
        root_id = self.make_old_book()
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE categories SET type='other_expense' WHERE id=? OR parent_id=?",
                      (root_id, root_id))
        c.close()
        db = Database(self.path)
        self.assertEqual(self.cogs_tree(db)[0]["type"], "other_expense")

    def test_personal_book_untouched(self):
        db = Database(self.path)
        self.assertFalse(any(c["type"] == "cogs" for c in db.get_categories()))


if __name__ == "__main__":
    unittest.main()
