import os
import shutil
import tempfile
import unittest
from decimal import Decimal

import tests  # noqa: F401
from sheets.client import Database
from utils.ledger import balance_sheet


class SalesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.cats = {c["name"]: c["id"] for c in self.db.get_categories()}
        self.bank = self.db.save_account({"name": "Checking", "type": "checking"})
        self.cust = self.db.save_contact({"name": "Acme Corp", "kind": "customer", "terms_days": 15})
        self.vend = self.db.save_contact({"name": "Office Depot", "kind": "vendor",
                                          "match_text": "OFFICE DEPOT",
                                          "default_category_id": self.cats["Office Supplies"]})

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def invoice(self):
        return self.db.save_doc("invoice", {"contact_id": self.cust["id"], "issue_date": "2026-03-01",
                                            "due_date": "2026-03-16", "status": "sent"}, [
            {"description": "Consulting", "qty": 10, "unit_price": 100, "tax_rate": 0,
             "category_id": self.cats["Consulting"]},
            {"description": "Widgets", "qty": 2, "unit_price": 50, "tax_rate": 10,
             "category_id": self.cats["Product Sales"]}])

    def test_numbering(self):
        a = self.invoice()
        b = self.invoice()
        self.assertEqual(b["number"], a["number"] + 1)
        self.assertEqual(a["number"], 1001)

    def test_invoice_payment_books_income_and_sales_tax_liability(self):
        inv = self.invoice()
        s = self.db.doc_summary("invoice", inv, "2026-03-02")
        self.assertEqual(s["total"], Decimal("1110.00"))
        self.db.record_doc_payment("invoice", inv["id"], "2026-03-20", self.bank["id"], "600")
        s = self.db.doc_summary("invoice", inv, "2026-03-21")
        self.assertEqual(s["status"], "overdue")
        self.assertEqual(s["balance"], Decimal("510.00"))
        self.db.record_doc_payment("invoice", inv["id"], "2026-03-25", self.bank["id"], "510")
        s = self.db.doc_summary("invoice", inv, "2026-03-26")
        self.assertEqual(s["status"], "paid")
        self.assertEqual(round(self.db.account_balance(self.bank["id"]), 2), 1110.0)
        stp = self.db.get_account_by_role("sales_tax_payable")
        self.assertEqual(round(self.db.account_balance(stp["id"]), 2), -10.0)
        cats = {c["id"]: c for c in self.db.get_categories()}
        bs = balance_sheet(self.db.get_accounts(active=None), self.db.get_transactions(), cats,
                           "2026-12-31")
        self.assertTrue(bs["balanced"])
        self.assertEqual(bs["other_adjustments"], Decimal("0"))
        eq = {n: v for n, v, _ in bs["equity"]}
        self.assertEqual(eq["Net income (this year)"], Decimal("1100.00"))
        txns = [t for t in self.db.get_transactions() if t["account_id"] == self.bank["id"]]
        self.assertTrue(all(t["contact_id"] == self.cust["id"] for t in txns))

    def test_bill_payment_and_aging(self):
        bill = self.db.save_doc("bill", {"contact_id": self.vend["id"], "reference": "INV-9",
                                         "bill_date": "2026-01-05", "due_date": "2026-02-04",
                                         "status": "open"},
                                [{"description": "Paper", "qty": 1, "unit_price": 80,
                                  "category_id": self.cats["Office Supplies"]},
                                 {"description": "Chair", "qty": 1, "unit_price": 220,
                                  "category_id": self.cats["Office Expense"]}])
        totals = self.db.ar_ap_totals("2026-03-01")
        self.assertEqual(totals["ap"], Decimal("300.00"))
        self.assertEqual(totals["ap_overdue"], 1)
        self.db.record_doc_payment("bill", bill["id"], "2026-03-02", self.bank["id"], "300")
        self.assertEqual(round(self.db.account_balance(self.bank["id"]), 2), -300.0)
        self.assertEqual(self.db.ar_ap_totals("2026-03-03")["ap"], Decimal("0"))
        # Undo payment restores the balance and removes the transactions.
        p = self.db.get_doc_payments("bill", bill["id"])[0]
        self.db.delete_doc_payment(p["id"])
        self.assertEqual(round(self.db.account_balance(self.bank["id"]), 2), 0.0)

    def test_contact_matching_fills_vendor_and_category(self):
        txns = [{"payee": "OFFICE DEPOT #1234 CHICAGO", "amount": "-12"},
                {"payee": "Unknown", "amount": "-3"}]
        self.db.apply_rules(txns)
        self.assertEqual(txns[0]["contact_id"], self.vend["id"])
        self.assertEqual(txns[0]["category_id"], self.cats["Office Supplies"])
        self.assertNotIn("contact_id", txns[1])

    def test_rules_win_over_contact_default(self):
        self.db.save_rule({"name": "OD", "field": "payee", "operator": "contains",
                           "value": "office depot", "category_id": self.cats["Supplies"]})
        txns = [{"payee": "OFFICE DEPOT #1", "amount": "-5"}]
        self.db.apply_rules(txns)
        self.assertEqual(txns[0]["category_id"], self.cats["Supplies"])
        self.assertEqual(txns[0]["contact_id"], self.vend["id"])


if __name__ == "__main__":
    unittest.main()
