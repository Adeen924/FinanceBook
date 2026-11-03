"""Business books list business categories in pickers; personal books unchanged."""
import os
import shutil
import tempfile
import unittest

import tests  # noqa: F401
from sheets.client import Database


def names(cats):
    return {c["name"] for c in cats}


class CategoryFilterTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def db(self, name, book_type=None):
        return Database(os.path.join(self.dir, name), book_type=book_type)

    def test_personal_book_unchanged(self):
        db = self.db("p.db")
        self.assertEqual(db.get_categories_for_book(), db.get_categories())

    def test_new_business_book_has_only_business_tree(self):
        db = self.db("b.db", "business")
        self.assertEqual(db.personal_default_category_ids(), set())
        self.assertEqual(db.get_categories_for_book(), db.get_categories())

    def test_converted_book_hides_personal_defaults(self):
        db = self.db("c.db")
        before = db.get_categories()
        db.set_book_type("business")
        allc = db.get_categories()
        self.assertTrue(names(before) <= names(allc))           # nothing removed
        shown = names(db.get_categories_for_book())
        fresh = names(self.db("fresh.db", "business").get_categories())
        self.assertEqual(shown, fresh)
        for personal in ("Groceries", "Restaurants", "Video Games", "Food"):
            self.assertNotIn(personal, shown)
        self.assertIn("Utilities", shown)     # shared name: taken over by Schedule C line 25
        self.assertIn("Account Transfers", shown)

    def test_used_kept_and_toggle_shows_all(self):
        db = self.db("c.db")
        groceries = next(c for c in db.get_categories() if c["name"] == "Groceries")
        food = groceries["parent_id"]
        acct = db.save_account({"name": "Checking", "type": "checking"})
        db.save_transaction({"date": "2026-01-01", "account_id": acct["id"], "payee": "Market",
                             "amount": "-10", "category_id": groceries["id"]})
        db.set_book_type("business")
        shown = {c["id"] for c in db.get_categories_for_book()}
        self.assertIn(groceries["id"], shown)              # used by a transaction
        self.assertIn(food, shown)                          # its parent, so trees still build
        restaurants = next(c for c in db.get_categories() if c["name"] == "Restaurants")
        self.assertNotIn(restaurants["id"], shown)
        self.assertIn(restaurants["id"], {c["id"] for c in
                                          db.get_categories_for_book(keep_ids=[restaurants["id"]])})
        self.assertEqual(len(db.get_categories_for_book(include_personal=True)),
                         len(db.get_categories()))
        db.set_setting("show_personal_categories", "1")
        self.assertEqual(len(db.get_categories_for_book()), len(db.get_categories()))

    def test_round_trip_and_reports_keep_everything(self):
        db = self.db("c.db")
        n_personal = len(db.get_categories())
        db.set_book_type("business")
        n_all = len(db.get_categories())
        self.assertGreater(n_all, n_personal)
        db.set_book_type("personal")
        self.assertEqual(len(db.get_categories_for_book()), n_all)   # personal: everything
        db.set_book_type("business")
        self.assertEqual(len(db.get_categories()), n_all)           # nothing seeded twice
        # Reports look categories up through get_categories(), which is unfiltered.
        acct = db.save_account({"name": "Checking", "type": "checking"})
        pharmacy = next(c for c in db.get_categories() if c["name"] == "Pharmacy")
        db.save_transaction({"date": "2026-02-01", "account_id": acct["id"], "payee": "CVS",
                             "amount": "-12", "category_id": pharmacy["id"]})
        from utils.reports import pnl_rows
        cats = {c["id"]: c for c in db.get_categories()}
        rows = pnl_rows(db.get_transactions(), cats)
        row = next(r for r in rows if r["category_id"] == pharmacy["id"])
        self.assertEqual((row["section"], row["group"], float(row["amount"])),
                         ("expense", "Healthcare", -12.0))


if __name__ == "__main__":
    unittest.main()
