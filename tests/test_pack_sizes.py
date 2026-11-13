"""Pack sizes: units per pack on receipt lines, exact per-unit landed costs,
stock received in units, item default pack sizes, parser suggestions and the
schema-11 migration."""
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
from utils.inventory import run_costing
from utils.money import cents
from utils.receipt_booking import landed_costs, per_unit_cost, total_units

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.path.join(os.path.dirname(HERE), "_private_samples")


class SuggestionTests(unittest.TestCase):
    def test_patterns(self):
        cases = {
            "Napkins, 6 Pack": (6, False), "Pack of 6 AA batteries": (6, False),
            "USB cable 6-Pack": (6, False), "Set of 4 bowls": (4, False),
            "Paper towels 12-count": (12, False), "Wipes 80 ct": (80, False),
            "Pens, 10 pk": (10, False),
            # Wording from real Amazon titles:
            "Kindroufly 562 Pieces Black M2.5 x 4/6 /8/10 /12/16 /20mm Button Head Socket "
            "Cap Screws Nuts Washers Assortment Kit": (562, False),
            "HANGLIFE Heat-Set Threaded Inserts, M2.5 Brass Metric Knurled Nuts (100 Pieces)":
                (100, False),
            "Check Valve Core, 10pcs, DN10, Pom Plastic, Micro One-Way Valve": (10, False),
            "LED holders, 5 pc set": (5, False),
        }
        for title, (units, ambiguous) in cases.items():
            s = R.suggest_pack_size(title)
            self.assertEqual((s["units"], s["ambiguous"]), (units, ambiguous), title)

    def test_amazon_title_suggests_six(self):
        title = ("Egofine Plexiglass Sheets Acrylic Sheets 6 Pack of 8x10'' 0.04 Thick Clear "
                 "Plastic Cast Transparent Plexi Glass for Crafting Projects")
        self.assertEqual(R.suggest_pack_size(title), {"units": 6, "text": "6 Pack",
                                                      "ambiguous": False})
        with open(os.path.join(HERE, "fixtures", "amazon_order_details.txt"),
                  encoding="utf-8") as f:
            r = R.parse_receipt_text(f.read())
        self.assertEqual(r["line_items"][0]["pack_suggestion"]["units"], 6)

    @unittest.skipUnless(os.path.isfile(os.path.join(PRIVATE, "Amazon_11-2-2026.pdf")),
                         "real Amazon sample not present")
    def test_real_amazon_sample(self):
        r = R.parse_file(os.path.join(PRIVATE, "Amazon_11-2-2026.pdf"))
        self.assertEqual(r["line_items"][0]["pack_suggestion"],
                         {"units": 6, "text": "6 Pack", "ambiguous": False})

    def test_false_positives_and_ambiguity(self):
        for title in ("Qty: 2", "Widget", "1 Pack", "Monitor 27 inch 2.4GHz", "Item 6"):
            self.assertIsNone(R.suggest_pack_size(title), title)
        s = R.suggest_pack_size("3 Pack of 10 ft cables")
        self.assertEqual((s["units"], s["ambiguous"]), (3, True))
        self.assertTrue(R.suggest_pack_size("2 Pack 12 ct refills")["ambiguous"])
        self.assertFalse(R.suggest_pack_size("Pack of 2, 10 ft")["ambiguous"])
        self.assertEqual(R.suggest_pack_size("20 Sets Mini Micro JST-XH Connector")["units"], 20)
        self.assertTrue(R.suggest_pack_size("2 sets of 3 brushes")["ambiguous"])


class UnitCostTests(unittest.TestCase):
    def test_exact_cents(self):
        self.assertEqual(per_unit_cost("6.45", 6), Decimal("1.075000"))
        self.assertEqual(per_unit_cost("50.28", 12), Decimal("4.190000"))
        self.assertEqual(per_unit_cost("10.00", 3), Decimal("3.333333"))
        self.assertEqual(per_unit_cost("1", 0), Decimal(0))
        for total in ("0.01", "0.99", "6.45", "10.00", "89.78", "1234.57", "9999.99"):
            for units in (1, 2, 3, 6, 7, 12, 24, 99, 144, 1000, 9999):
                uc = per_unit_cost(total, units)
                self.assertEqual(cents(uc * units), Decimal(total), (total, units))

    def test_total_units(self):
        self.assertEqual(total_units(2, 6), Decimal(12))
        self.assertEqual(total_units(1, 1), Decimal(1))
        self.assertEqual(total_units("", ""), Decimal(1))


class ReceivingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.card = self.db.save_account({"name": "Card", "type": "credit card"})
        self.rc = self.db.add_manual_receipt()
        self.item = self.db.save_item({"name": "Acrylic sheet", "sku": "EGO-6"})

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def line(self, qty, upp, landed, item=None):
        return {"item_id": (item or self.item)["id"], "qty": qty, "units_per_pack": upp,
                "landed_total": Decimal(landed), "description": "Acrylic"}

    def moves(self, item=None):
        return self.db.get_moves((item or self.item)["id"])

    def test_pack_of_one_is_unchanged(self):
        self.db.receive_receipt_inventory(self.rc["id"], [self.line(4, 1, "50.28")],
                                          "2026-10-13", "DigiKey")
        m = self.moves()[0]
        self.assertEqual((m["qty"], m["unit_cost"]), (4, 12.57))
        self.assertIn("(4 units)", m["notes"])

    def test_one_and_two_six_packs(self):
        self.db.receive_receipt_inventory(self.rc["id"], [self.line(1, 6, "6.45")],
                                          "2026-11-02", "Amazon")
        m = self.moves()[0]
        self.assertEqual((m["qty"], m["unit_cost"]), (6, 1.075))
        other = self.db.add_manual_receipt()
        self.db.receive_receipt_inventory(other["id"], [self.line(2, 6, "12.90")],
                                          "2026-11-03", "Amazon")
        m2 = [m for m in self.moves() if m["receipt_id"] == other["id"]][0]
        self.assertEqual(m2["qty"], 12)
        self.assertIn("2 packs × 6 = 12 units", m2["notes"])
        for method in ("fifo", "avg"):
            r = run_costing(self.moves(), method)
            self.assertEqual((r["qty"], cents(r["value"])), (Decimal(18), Decimal("19.35")))

    def test_landed_share_and_costing(self):
        # One six-pack ($5.99) + one $10 expense line; tax 1.20, shipping 4.00, discount 2.00.
        items = [{"line_total": "5.99", "kind": "inventory", "business": True},
                 {"line_total": "10.00", "kind": "expense", "business": True}]
        landed = landed_costs(items, "proportional", tax="1.20", shipping="4.00",
                              discount="2.00")
        self.assertEqual(sum(landed), Decimal("19.19"))
        self.db.receive_receipt_inventory(self.rc["id"], [self.line(1, 6, landed[0])],
                                          "2026-11-02")
        m = self.moves()[0]
        self.assertEqual(m["qty"], 6)
        self.assertEqual(cents(Decimal(str(m["unit_cost"])) * 6), landed[0])
        self.db.add_move(self.item["id"], "2026-11-05", "sale", 4)
        r = run_costing(self.moves(), "fifo")
        self.assertEqual(r["qty"], Decimal(2))
        self.assertEqual(cents(r["value"]) + cents(sum(r["cogs_by_move"].values())), landed[0])

    def test_rereceive_updates_the_same_move(self):
        self.db.receive_receipt_inventory(self.rc["id"], [self.line(1, 1, "6.45")], "2026-11-02")
        first = self.moves()[0]
        self.db.receive_receipt_inventory(self.rc["id"], [self.line(1, 6, "6.45")], "2026-11-02")
        moves = self.moves()
        self.assertEqual(len(moves), 1)
        self.assertEqual((moves[0]["id"], moves[0]["qty"], moves[0]["unit_cost"]),
                         (first["id"], 6, 1.075))
        # A second item line added, then removed again.
        other = self.db.save_item({"name": "Glue"})
        self.db.receive_receipt_inventory(
            self.rc["id"], [self.line(1, 6, "6.45"), self.line(2, 1, "4.00", other)], "2026-11-02")
        self.assertEqual(len(self.moves(other)), 1)
        self.db.receive_receipt_inventory(self.rc["id"], [self.line(1, 6, "6.45")], "2026-11-02")
        self.assertEqual(self.moves(other), [])
        self.assertEqual(len(self.moves()), 1)

    def test_units_per_pack_saved_on_every_line(self):
        self.db.replace_receipt_items(self.rc["id"], [
            {"description": "Sheets", "qty": 1, "line_total": 5.99, "units_per_pack": 6,
             "kind": "inventory", "item_id": self.item["id"]},
            {"description": "Tape", "qty": 2, "line_total": 3.00, "units_per_pack": 3},
            {"description": "Personal", "qty": 1, "line_total": 1.00, "units_per_pack": 4,
             "business": False},
            {"description": "Old style", "qty": 1, "line_total": 1.00},
            {"description": "Bad", "qty": 1, "line_total": 1.00, "units_per_pack": "0"}])
        got = [i["units_per_pack"] for i in self.db.get_receipt_items(self.rc["id"])]
        self.assertEqual(got, [6, 3, 4, 1, 1])
        hit = self.db.search_receipt_items("Tape")[0]
        self.assertEqual(hit["units_per_pack"], 3)

    def test_money_is_unchanged_by_pack_size(self):
        totals = []
        for upp in (1, 6):
            rc = self.db.add_manual_receipt()
            items = [{"description": "Sheets", "qty": 1, "line_total": "5.99",
                      "kind": "inventory", "item_id": self.item["id"], "units_per_pack": upp,
                      "business": True}]
            saved = self.db.book_receipt(rc["id"], {"vendor": "Amazon", "tax": "0.46",
                                                    "total": "6.45"}, items,
                                         account_id=self.card["id"], date="2026-11-02")
            totals.append(round(sum(float(t["amount"]) for t in saved), 2))
        self.assertEqual(totals, [-6.45, -6.45])

    def test_item_default_pack_size(self):
        self.assertEqual(self.db.get_item(self.item["id"])["pack_size"], 1)
        self.db.set_item_pack_size(self.item["id"], 6)
        it = self.db.get_item(self.item["id"])
        self.assertEqual(it["pack_size"], 6)
        # A caller that predates pack sizes doesn't reset it.
        self.db.save_item({"id": it["id"], "name": "Acrylic sheet (clear)", "sku": "EGO-6"})
        self.assertEqual(self.db.get_item(it["id"])["pack_size"], 6)
        for bad in (0, -3, "1.5", "", None, "x"):
            self.db.set_item_pack_size(it["id"], bad)
            self.assertEqual(self.db.get_item(it["id"])["pack_size"], 1, bad)


def _downgrade_to_v10(path):
    conn = sqlite3.connect(path)
    try:
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute("ALTER TABLE receipt_items DROP COLUMN units_per_pack")
        conn.execute("ALTER TABLE items DROP COLUMN pack_size")
        conn.execute("PRAGMA user_version = 10")
        conn.commit()
    finally:
        conn.close()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "finances.db")
        db = Database(self.path, book_type="business")
        card = db.save_account({"name": "Card", "type": "credit card"})
        item = db.save_item({"name": "Pico", "sku": "P1"})
        rc = db.add_manual_receipt()
        items = [{"description": "PICO", "qty": 4, "line_total": "33.68", "kind": "inventory",
                  "item_id": item["id"], "business": True}]
        db.replace_receipt_items(rc["id"], items)
        db.book_receipt(rc["id"], {"vendor": "DigiKey", "shipping": "13.99", "tax": "2.61",
                                   "total": "50.28"}, items, account_id=card["id"],
                        date="2026-10-13")
        db.receive_receipt_inventory(rc["id"], [{"item_id": item["id"], "qty": 4,
                                                 "landed_total": "50.28", "description": "PICO"}],
                                     "2026-10-13", "DigiKey")
        self.ids = (rc["id"], item["id"], card["id"])
        db.close()
        _downgrade_to_v10(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_v10_to_v11(self):
        rid, iid, card = self.ids
        self.assertEqual(backup.read_user_version(self.path), 10)
        db = Database(self.path)
        try:
            self.assertGreaterEqual(SCHEMA_VERSION, 11)
            self.assertEqual(backup.read_user_version(self.path), SCHEMA_VERSION)
            self.assertTrue(os.path.exists(db.last_migration_backup))
            self.assertEqual(backup.read_user_version(db.last_migration_backup), 10)
            line = db.get_receipt_items(rid)[0]
            self.assertEqual((line["description"], line["qty"], line["units_per_pack"]),
                             ("PICO", 4.0, 1))
            item = db.get_item(iid)
            self.assertEqual((item["name"], item["pack_size"]), ("Pico", 1))
            st = db.item_status(item)
            self.assertEqual((st["qty"], st["value"]), (Decimal(4), Decimal("50.28")))
            self.assertEqual(round(db.account_balance(card), 2), -50.28)
        finally:
            db.close()

    def test_newer_schema_still_refused(self):
        Database(self.path).close()
        conn = sqlite3.connect(self.path)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        with self.assertRaises(NewerSchemaError):
            Database(self.path)


if __name__ == "__main__":
    unittest.main()
