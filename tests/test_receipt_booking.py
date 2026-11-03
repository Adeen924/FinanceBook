"""Shipping / discount / duties through booking, landed costs, inventory items
on receipt lines, and the schema-10 migration."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from decimal import Decimal

import tests  # noqa: F401
from parsers import receipt as R
from sheets import backup
from sheets.client import Database, SCHEMA_VERSION, NewerSchemaError
from utils.receipt_booking import plan_receipt_lines, landed_costs, receipt_total

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def item(desc, total, cat="", biz=True, kind="expense", qty=1, **kw):
    return dict({"description": desc, "line_total": total, "category_id": cat, "business": biz,
                 "class_id": "", "charged_to": "", "kind": kind, "qty": qty}, **kw)


def total_of(biz, pers=()):
    return sum(b["amount"] for b in biz) + sum(p["amount"] for p in pers)


class PlanTests(unittest.TestCase):
    AMTS = dict(tax="2.73", shipping="35.13", discount="10.00", duties="16.79")

    def test_proportional_spreads_everything(self):
        biz, pers = plan_receipt_lines([item("PCB", "45.13", "c1")], **self.AMTS)
        self.assertEqual(pers, [])
        self.assertEqual([b["amount"] for b in biz], [Decimal("89.78")])

    def test_separate_lines_and_discount_spread(self):
        biz, _ = plan_receipt_lines(
            [item("Pens", "30.00", "sup"), item("Paper", "10.00", "sup2")], mode="separate",
            tax="4.00", shipping="8.00", duties="2.00", discount="4.00",
            tax_category_id="tx", shipping_category_id="ship", duties_category_id="duty")
        by_memo = {b["memo"]: (b["category_id"], b["amount"]) for b in biz}
        self.assertEqual(by_memo["Pens"], ("sup", Decimal("27.00")))       # 30 − 3 discount
        self.assertEqual(by_memo["Paper"], ("sup2", Decimal("9.00")))
        self.assertEqual(by_memo["Sales tax"], ("tx", Decimal("4.00")))
        self.assertEqual(by_memo["Shipping"], ("ship", Decimal("8.00")))
        self.assertEqual(by_memo["Import duties / fees"], ("duty", Decimal("2.00")))
        self.assertEqual(total_of(biz), receipt_total(
            [{"line_total": "30"}, {"line_total": "10"}], tax="4", shipping="8", duties="2",
            discount="4"))

    def test_separate_mode_capitalises_inventory_and_asset_share(self):
        items = [item("Chips", "60.00", "inv", kind="inventory"),
                 item("Tape", "40.00", "sup")]
        biz, _ = plan_receipt_lines(items, mode="separate", shipping="10.00", tax="5.00",
                                    shipping_category_id="ship", tax_category_id="tx")
        by_memo = {b["memo"]: b["amount"] for b in biz}
        self.assertEqual(by_memo["Chips"], Decimal("69.00"))     # 60 + 6 shipping + 3 tax
        self.assertEqual(by_memo["Shipping"], Decimal("4.00"))   # the expense line's share
        self.assertEqual(by_memo["Sales tax"], Decimal("2.00"))
        self.assertEqual(total_of(biz), Decimal("115.00"))
        self.assertEqual(landed_costs(items, "separate", shipping="10", tax="5"),
                         [Decimal("69.00"), Decimal("40.00")])

    def test_personal_items_carry_their_share(self):
        biz, pers = plan_receipt_lines(
            [item("Work", "30.00", "c"), item("Gum", "10.00", biz=False)], mode="separate",
            shipping="4.00", discount="2.00", shipping_category_id="ship")
        self.assertEqual(pers[0]["amount"], Decimal("10.50"))    # 10 + 1 shipping − 0.50
        self.assertEqual(total_of(biz, pers), Decimal("42.00"))

    def test_rounding_never_loses_cents(self):
        items = [item(str(n), "0.01", f"c{n}") for n in range(3)]
        for mode in ("proportional", "separate"):
            biz, _ = plan_receipt_lines(items, mode=mode, tax="0.01", shipping="0.02",
                                        discount="0.01", duties="0.01")
            self.assertEqual(total_of(biz), Decimal("0.06"), mode)   # .03+.01+.02+.01−.01

    def test_no_items_only_charges(self):
        biz, _ = plan_receipt_lines([], mode="proportional", shipping="5.00",
                                    shipping_category_id="ship")
        self.assertEqual([(b["category_id"], b["amount"]) for b in biz],
                         [("ship", Decimal("5.00"))])


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.cats = {c["name"]: c["id"] for c in self.db.get_categories() if not c["parent_id"]}
        self.bank = self.db.save_account({"name": "Biz Card", "type": "credit card"})
        self.rc = self.db.add_manual_receipt()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def parsed(self, name):
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
            return R.parse_receipt_text(f.read())

    def book(self, p, items, mode):
        header = {k: p[k] for k in R.MONEY_FIELDS}
        header.update(vendor=p["vendor"], notes=f"Order # {p['order_number']}")
        return self.db.book_receipt(
            self.rc["id"], header, items, account_id=self.bank["id"], date=p["date"],
            tax_mode=mode, tax_category_id=self.cats["Taxes & Licenses"],
            shipping_category_id=self.cats["Shipping"],
            duties_category_id=self.cats["Taxes & Licenses"])

    def test_parse_check_plan_book_in_both_modes(self):
        for fixture, vendor_total in (("digikey_invoice.txt", -50.28),
                                      ("jlcpcb_invoice_ocr.txt", -89.78),
                                      ("amazon_multi_shipment.txt", -64.76)):
            for mode in ("proportional", "separate"):
                p = self.parsed(fixture)
                self.assertEqual(R.check_totals(p), [], fixture)
                items = [item(i["description"], i["line_total"], self.cats["Supplies"],
                              qty=i["qty"]) for i in p["line_items"]]
                self.rc = self.db.add_manual_receipt()
                saved = self.book(p, items, mode)
                amt = round(sum(float(t["amount"]) for t in saved), 2)
                self.assertEqual(amt, vendor_total, (fixture, mode))
                rc = self.db.get_receipt(self.rc["id"])
                for k in ("shipping", "discount", "duties", "total"):
                    self.assertEqual(Decimal(str(rc[k])), p[k], (fixture, k))
                self.assertIn(f"Order # {p['order_number']}", saved[0]["notes"])
                if mode == "separate" and p["shipping"]:
                    ship = [t for t in saved if t["category_id"] == self.cats["Shipping"]]
                    self.assertEqual(round(-float(ship[0]["amount"]), 2), float(p["shipping"]))

    def test_receipts_without_new_fields_book_as_before(self):
        items = [item("Paper", "10.00", self.cats["Supplies"])]
        saved = self.db.book_receipt(self.rc["id"], {"vendor": "Staples", "tax": "0.80"}, items,
                                     account_id=self.bank["id"], date="2026-01-01")
        self.assertEqual(round(sum(float(t["amount"]) for t in saved), 2), -10.80)
        rc = self.db.get_receipt(self.rc["id"])
        self.assertEqual((rc["shipping"], rc["discount"], rc["duties"]), (0.0, 0.0, 0.0))


class InventoryLineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.bank = self.db.save_account({"name": "Biz Card", "type": "credit card"})
        self.rc = self.db.add_manual_receipt()
        self.pico = self.db.save_item({"name": "Pico 2 W", "sku": "2648-SC1633CT-ND"})

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_item_id_and_sku_persist_with_drafts(self):
        self.db.replace_receipt_items(self.rc["id"], [item(
            "PICO", "33.68", kind="inventory", qty=4, item_id=self.pico["id"],
            sku="2648-SC1633CT-ND")])
        got = self.db.get_receipt_items(self.rc["id"])[0]
        self.assertEqual((got["item_id"], got["sku"], got["kind"]),
                         (self.pico["id"], "2648-SC1633CT-ND", "inventory"))

    def test_landed_cost_and_no_double_stock_on_rebook(self):
        items = [item("PICO", "33.68", kind="inventory", qty=4, item_id=self.pico["id"])]
        header = {"vendor": "DigiKey", "subtotal": "33.68", "shipping": "13.99", "tax": "2.61",
                  "total": "50.28"}
        for _ in range(2):           # book, then re-book ("Replace that transaction")
            self.db.book_receipt(self.rc["id"], header, items, account_id=self.bank["id"],
                                 date="2026-10-13")
            costs = landed_costs(items, "proportional", shipping="13.99", tax="2.61")
            self.assertEqual(costs, [Decimal("50.28")])
            self.db.receive_receipt_inventory(
                self.rc["id"], [{"item_id": self.pico["id"], "qty": 4,
                                 "unit_cost": float(costs[0] / 4), "description": "PICO"}],
                "2026-10-13", "DigiKey")
        moves = self.db.get_moves(self.pico["id"])
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["qty"], 4)
        self.assertAlmostEqual(moves[0]["unit_cost"], 12.57)
        st = self.db.item_status(self.pico)
        self.assertEqual((st["qty"], st["value"]), (Decimal(4), Decimal("50.28")))
        # The money was booked once, to Inventory Purchases — not again by the stock in.
        txns = [t for t in self.db.get_transactions() if t["account_id"] == self.bank["id"]]
        self.assertEqual(round(sum(float(t["amount"]) for t in txns), 2), -50.28)
        self.assertEqual(moves[0]["txn_id"], self.db.get_receipt(self.rc["id"])["transaction_id"])

    def test_stock_from_other_receipts_untouched(self):
        other = self.db.add_manual_receipt()
        self.db.receive_receipt_inventory(other["id"], [{"item_id": self.pico["id"], "qty": 2,
                                                         "unit_cost": 9}], "2026-01-01")
        self.db.receive_receipt_inventory(self.rc["id"], [{"item_id": self.pico["id"], "qty": 4,
                                                           "unit_cost": 10}], "2026-02-01")
        self.db.receive_receipt_inventory(self.rc["id"], [{"item_id": self.pico["id"], "qty": 4,
                                                           "unit_cost": 10}], "2026-02-01")
        self.assertEqual(sum(m["qty"] for m in self.db.get_moves(self.pico["id"])), 6)


def _downgrade_to_v9(path):
    """Turn a current database back into a schema-9 file (as an older app left it)."""
    conn = sqlite3.connect(path)
    try:
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            conn.execute(f"DROP TRIGGER {name}")
        for table, col in (("receipts", "shipping"), ("receipts", "discount"),
                           ("receipts", "duties"), ("receipt_items", "item_id"),
                           ("receipt_items", "sku")):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        conn.execute("PRAGMA user_version = 9")
        conn.commit()
    finally:
        conn.close()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "finances.db")
        db = Database(self.path, book_type="business")
        bank = db.save_account({"name": "Card", "type": "credit card"})
        rc = db.add_manual_receipt()
        sup = next(c["id"] for c in db.get_categories() if c["name"] == "Supplies")
        items = [item("Paper", "10.00", sup)]
        db.replace_receipt_items(rc["id"], items)
        db.book_receipt(rc["id"], {"vendor": "Staples", "tax": "0.80", "total": "10.80"}, items,
                        account_id=bank["id"], date="2026-01-05")
        self.rid, self.bank = rc["id"], bank["id"]
        db.close()
        _downgrade_to_v9(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_v9_to_v10_keeps_data_and_backs_up_first(self):
        self.assertEqual(backup.read_user_version(self.path), 9)
        db = Database(self.path)
        self.assertEqual(backup.read_user_version(self.path), SCHEMA_VERSION)
        self.assertTrue(db.last_migration_backup and os.path.exists(db.last_migration_backup))
        self.assertEqual(backup.read_user_version(db.last_migration_backup), 9)
        rc = db.get_receipt(self.rid)
        self.assertEqual((rc["vendor"], rc["total"], rc["status"]), ("Staples", 10.8, "booked"))
        self.assertEqual((rc["shipping"], rc["discount"], rc["duties"]), (0.0, 0.0, 0.0))
        it = db.get_receipt_items(self.rid)[0]
        self.assertEqual((it["description"], it["item_id"], it["sku"]), ("Paper", "", ""))
        self.assertEqual(round(db.account_balance(self.bank), 2), -10.8)    # booked total intact
        conn = sqlite3.connect(self.path)
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        finally:
            conn.close()
        self.assertIn("audit_receipts_upd", names)

    def test_newer_schema_is_refused(self):
        Database(self.path).close()
        conn = sqlite3.connect(self.path)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        with self.assertRaises(NewerSchemaError):
            Database(self.path)
        self.assertEqual(backup.read_user_version(self.path), SCHEMA_VERSION + 1)


if __name__ == "__main__":
    unittest.main()
