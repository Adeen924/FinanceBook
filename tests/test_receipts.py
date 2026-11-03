import os
import shutil
import tempfile
import unittest
from decimal import Decimal

import tests  # noqa: F401
from sheets.client import Database
from utils.receipt_booking import plan_receipt_lines


def item(desc, total, cat="", biz=True, cls="", charged=""):
    return {"description": desc, "line_total": total, "category_id": cat,
            "business": biz, "class_id": cls, "charged_to": charged}


class PlanTests(unittest.TestCase):
    def test_groups_by_category_and_allocates_tax(self):
        biz, pers = plan_receipt_lines(
            [item("Paper", "10.00", "sup"), item("Pens", "5.00", "sup"),
             item("Coffee", "5.00", "meal")], tax="2.00", tip="0")
        self.assertEqual(pers, [])
        self.assertEqual([b["category_id"] for b in biz], ["sup", "meal"])
        self.assertEqual(biz[0]["amount"], Decimal("16.50"))
        self.assertEqual(biz[1]["amount"], Decimal("5.50"))
        self.assertEqual(sum(b["amount"] for b in biz), Decimal("22.00"))
        self.assertEqual(biz[0]["memo"], "Paper, Pens")

    def test_separate_tax_and_tip_lines(self):
        biz, pers = plan_receipt_lines(
            [item("Lunch", "30.00", "meal"), item("Gum", "10.00", "", biz=False)],
            tax="4.00", tip="6.00", mode="separate", tax_category_id="tx", tip_category_id="meal")
        total = sum(b["amount"] for b in biz) + sum(p["amount"] for p in pers)
        self.assertEqual(total, Decimal("50.00"))
        self.assertEqual(pers[0]["amount"], Decimal("12.50"))      # 10 + its 25% share of 10
        sep = {b["memo"]: b["amount"] for b in biz if b["memo"] in ("Sales tax", "Tip")}
        self.assertEqual(sep, {"Sales tax": Decimal("3.00"), "Tip": Decimal("4.50")})

    def test_odd_cents_never_lost(self):
        biz, _ = plan_receipt_lines([item("a", "0.01", "x"), item("b", "0.01", "y"),
                                     item("c", "0.01", "z")], tax="0.01")
        self.assertEqual(sum(b["amount"] for b in biz), Decimal("0.04"))


class ReceiptDbTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.cats = {c["name"]: c["id"] for c in self.db.get_categories()}
        self.src = os.path.join(self.dir, "staples.txt")
        with open(self.src, "w") as f:
            f.write("Staples\n2026-03-02\nPaper 10.00\nTotal 10.00\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_copy_and_duplicate_detection(self):
        r, dup = self.db.add_receipt_file(self.src)
        self.assertFalse(dup)
        path = self.db.receipt_abs_path(r)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(os.path.dirname(path),
                         os.path.join(self.dir, "FinanceBook Receipts", "biz"))
        r2, dup2 = self.db.add_receipt_file(self.src)
        self.assertTrue(dup2)
        self.assertEqual(r2["id"], r["id"])
        self.assertEqual(len(self.db.get_receipts()), 1)

    def test_book_split_with_personal_draw_from_company_account(self):
        bank = self.db.save_account({"name": "Biz Checking", "type": "checking"})
        r, _ = self.db.add_receipt_file(self.src)
        items = [item("Paper", "20.00", self.cats["Supplies"]),
                 item("Ads flyer", "30.00", self.cats["Print & Signage"]),
                 item("Snacks for me", "10.00", "", biz=False)]
        self.db.replace_receipt_items(r["id"], items)
        saved = self.db.book_receipt(r["id"], {"vendor": "Staples", "tax": "6.00", "tip": "0",
                                              "total": "66.00"}, items,
                                     account_id=bank["id"], date="2026-03-02")
        self.assertEqual(round(self.db.account_balance(bank["id"]), 2), -66.0)
        draws = self.db.get_account_by_role("owner_draws")
        self.assertEqual(round(self.db.account_balance(draws["id"]), 2), 11.0)
        self.assertEqual(len(saved), 3)
        self.assertEqual(self.db.get_receipt(r["id"])["status"], "booked")
        tx_ids, groups = self.db.attachment_index()
        self.assertIn(saved[0]["split_group_id"], groups)
        self.assertEqual(self.db.vendor_default("STAPLES #42")["category_id"],
                         self.cats["Print & Signage"])

    def test_book_owner_paid_excludes_personal(self):
        due = self.db.get_account_by_role("due_to_owner")
        r, _ = self.db.add_receipt_file(self.src)
        items = [item("Paper", "20.00", self.cats["Supplies"]),
                 item("Personal", "5.00", "", biz=False)]
        saved = self.db.book_receipt(r["id"], {"vendor": "Staples", "tax": "0", "tip": "0"},
                                     items, account_id=due["id"], date="2026-03-02",
                                     paid_by=self.db.owner_person_id())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["reimbursable"], "1")
        self.assertEqual(round(self.db.account_balance(due["id"]), 2), -20.0)

    def test_book_against_imported_transaction_keeps_hash(self):
        bank = self.db.save_account({"name": "Biz Checking", "type": "checking"})
        imported = self.db.save_transaction({"date": "2026-03-03", "account_id": bank["id"],
                                             "payee": "STAPLES 0042", "amount": "-30",
                                             "import_hash": "H1", "reconciled": "1"})
        r, _ = self.db.add_receipt_file(self.src)
        self.db.attach_receipt(r["id"], imported)
        items = [item("Paper", "20.00", self.cats["Supplies"]),
                 item("Stamps", "10.00", self.cats["Postage"])]
        saved = self.db.book_receipt(r["id"], {"vendor": "Staples"}, items,
                                     account_id=bank["id"], date="2026-03-02", base_txn=imported)
        self.assertEqual(round(self.db.account_balance(bank["id"]), 2), -30.0)
        self.assertEqual({t["date"] for t in saved}, {"2026-03-03"})
        self.assertIn("H1", self.db.get_existing_hashes())
        self.assertTrue(all(t["reconciled"] == "1" for t in saved))

    def test_search_items_and_suggestions(self):
        r, _ = self.db.add_receipt_file(self.src)
        self.db.save_receipt({"id": r["id"], "vendor": "Staples", "receipt_date": "2026-03-02"})
        self.db.replace_receipt_items(r["id"], [item("Printer Paper", "10", self.cats["Supplies"])])
        hits = self.db.search_receipt_items("paper")
        self.assertEqual(hits[0]["vendor"], "Staples")
        self.assertEqual(self.db.suggest_category("Other Store", "printer paper"),
                         self.cats["Supplies"])
        self.db.save_rule({"name": "AWS", "field": "payee", "operator": "contains",
                           "value": "amazon web", "category_id": self.cats["Software & Subscriptions"]})
        self.assertEqual(self.db.suggest_category("Amazon Web Services", "EC2"),
                         self.cats["Software & Subscriptions"])

    def test_delete_receipt_removes_copy_only(self):
        r, _ = self.db.add_receipt_file(self.src)
        path = self.db.receipt_abs_path(r)
        self.db.delete_receipt(r["id"])
        self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.exists(self.src))


if __name__ == "__main__":
    unittest.main()
