import unittest
from decimal import Decimal

import tests  # noqa: F401
from utils.inventory import run_costing, cogs_between, opening_value, needs_reorder


def mv(i, d, qty, cost=None, kind=None):
    m = {"id": i, "date": d, "qty": str(qty), "kind": kind or ("purchase" if qty > 0 else "sale")}
    if cost is not None:
        m["unit_cost"] = str(cost)
    return m


MOVES = [mv("p1", "2026-01-05", 10, 2.00), mv("p2", "2026-02-05", 10, 3.00),
         mv("s1", "2026-03-01", -15), mv("p3", "2026-04-01", 5, 4.00), mv("s2", "2026-05-01", -6)]


class CostingTests(unittest.TestCase):
    def test_fifo(self):
        r = run_costing(MOVES, "fifo")
        self.assertEqual(r["cogs_by_move"]["s1"], Decimal("35.00"))   # 10×2 + 5×3
        self.assertEqual(r["cogs_by_move"]["s2"], Decimal("19.00"))   # 5×3 + 1×4
        self.assertEqual(r["qty"], Decimal("4"))
        self.assertEqual(r["value"], Decimal("16.00"))
        self.assertFalse(r["short"])

    def test_weighted_average(self):
        r = run_costing(MOVES, "avg")
        self.assertEqual(r["cogs_by_move"]["s1"], Decimal("37.50"))   # avg 2.50
        # after s1: 5 @ 2.50 = 12.50; +5 @ 4 = 32.50 / 10 = 3.25; s2 = 6 × 3.25
        self.assertEqual(r["cogs_by_move"]["s2"], Decimal("19.50"))
        self.assertEqual(r["value"], Decimal("13.00"))

    def test_value_identity(self):
        for method in ("fifo", "avg"):
            r = run_costing(MOVES, method)
            purchases = Decimal("20") + Decimal("30") + Decimal("20")
            self.assertEqual(r["value"] + sum(r["cogs_by_move"].values()), purchases, method)

    def test_as_of_and_period_cogs(self):
        r = run_costing(MOVES, "fifo", as_of="2026-03-31")
        self.assertEqual(r["qty"], Decimal("5"))
        self.assertEqual(cogs_between(MOVES, "fifo", "2026-04-01", "2026-12-31"), Decimal("19.00"))
        self.assertEqual(cogs_between(MOVES, "fifo", "", "2026-03-31"), Decimal("35.00"))

    def test_oversell_is_flagged_and_costed(self):
        r = run_costing([mv("p", "2026-01-01", 2, 5), mv("s", "2026-01-02", -3)], "fifo")
        self.assertTrue(r["short"])
        self.assertEqual(r["cogs_by_move"]["s"], Decimal("15.00"))
        self.assertEqual(r["qty"], Decimal("-1"))

    def test_customer_return_at_current_cost_and_adjustment(self):
        moves = [mv("p", "2026-01-01", 10, 3), mv("s", "2026-01-02", -4),
                 mv("r", "2026-01-03", 1, kind="return_in"), mv("a", "2026-01-04", -2, kind="adjust")]
        r = run_costing(moves, "fifo")
        self.assertEqual(r["qty"], Decimal("5"))
        self.assertEqual(r["value"], Decimal("15.00"))
        self.assertEqual(cogs_between(moves, "fifo", kinds=("adjust",)), Decimal("6.00"))

    def test_opening_and_reorder(self):
        moves = [mv("o", "2025-12-31", 10, 1.5, kind="opening")]
        self.assertEqual(opening_value(moves, "fifo"), Decimal("15.00"))
        self.assertTrue(needs_reorder(3, 5))
        self.assertFalse(needs_reorder(3, 0))


class InventoryBooksTests(unittest.TestCase):
    """Purchases, sales and counts keep the Balance Sheet balanced."""

    def setUp(self):
        import os
        import tempfile
        from sheets.client import Database
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.bank = self.db.save_account({"name": "Checking", "type": "checking",
                                          "opening_balance": "1000"})

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_full_cycle_balances(self):
        from utils.ledger import balance_sheet
        db = self.db
        mug = db.save_item({"name": "Mug", "sku": "MUG-1", "cost_method": "fifo",
                            "reorder_point": 5, "sale_price": 12})
        db.add_move(mug["id"], "2025-12-31", "opening", 4, unit_cost=3)
        db.receive_stock(mug["id"], "2026-01-10", 20, 4, account_id=self.bank["id"])
        db.sell_stock(mug["id"], "2026-02-01", 10, 12, account_id=self.bank["id"])
        db.count_stock(mug["id"], "2026-03-01", 12)          # 2 missing
        st = db.item_status(db.get_item(mug["id"]))
        self.assertEqual(st["qty"], Decimal("12"))
        self.assertEqual(st["value"], Decimal("48.00"))
        self.assertFalse(st["low"])
        cats = {c["id"]: c for c in db.get_categories()}
        for as_of in ("2026-01-31", "2026-12-31", "2027-06-30"):
            fy = as_of[:4] + "-01-01"
            bs = balance_sheet(db.get_accounts(active=None), db.get_transactions(end=as_of), cats,
                               as_of, fy, **db.balance_sheet_extras(as_of, fy))
            self.assertTrue(bs["balanced"], as_of)
            self.assertEqual(bs["other_adjustments"], Decimal("0"), as_of)
        lines = db.computed_pnl_lines("2026-01-01", "2026-12-31")
        cogs = next(l for l in lines if l["computed"] == "cogs")
        self.assertEqual(cogs["amount"], Decimal("-44.00"))   # 4×3 + 6×4 sold, 2×4 shrinkage


if __name__ == "__main__":
    unittest.main()
