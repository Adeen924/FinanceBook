import unittest
from decimal import Decimal

import tests  # noqa: F401
from utils import invoicing as inv


class InvoicingTests(unittest.TestCase):
    LINES = [{"description": "Design", "qty": 10, "unit_price": "85", "tax_rate": "0",
              "category_id": "svc"},
             {"description": "Prints", "qty": 3, "unit_price": "19.99", "tax_rate": "8.25",
              "category_id": "sales"}]

    def test_totals(self):
        t = inv.doc_totals(self.LINES)
        self.assertEqual(t["subtotal"], Decimal("909.97"))
        self.assertEqual(t["tax"], Decimal("4.95"))
        self.assertEqual(t["total"], Decimal("914.92"))

    def test_status(self):
        doc = {"status": "sent", "due_date": "2026-03-01"}
        self.assertEqual(inv.status_for(doc, 100, 0, "2026-02-01"), "sent")
        self.assertEqual(inv.status_for(doc, 100, 40, "2026-02-01"), "partial")
        self.assertEqual(inv.status_for(doc, 100, 40, "2026-03-02"), "overdue")
        self.assertEqual(inv.status_for(doc, 100, 100, "2026-03-02"), "paid")
        self.assertEqual(inv.status_for({"status": "draft"}, 100, 0, "2027-01-01"), "draft")

    def test_aging_buckets(self):
        docs = [{"contact": "Acme", "due_date": "2026-06-30", "balance": "100"},
                {"contact": "Acme", "due_date": "2026-05-15", "balance": "50"},
                {"contact": "Zed", "due_date": "2026-03-01", "balance": "25"},
                {"contact": "Zed", "due_date": "2026-07-15", "balance": "0"}]
        a = inv.aging(docs, "2026-07-01")
        self.assertEqual(a["rows"]["Acme"][1], Decimal("100.00"))   # 1 day late
        self.assertEqual(a["rows"]["Acme"][2], Decimal("50.00"))    # 47 days late
        self.assertEqual(a["rows"]["Acme"][5], Decimal("150.00"))
        self.assertEqual(a["rows"]["Zed"][4], Decimal("25.00"))
        self.assertEqual(a["totals"][5], Decimal("175.00"))
        self.assertEqual(inv.bucket_for(0), 0)
        self.assertEqual(inv.bucket_for(31), 2)

    def test_partial_payment_split_is_exact(self):
        total = inv.doc_totals(self.LINES)["total"]
        parts = inv.payment_split(self.LINES, "300.00", total)
        self.assertEqual(sum(p["amount"] for p in parts), Decimal("300.00"))
        self.assertTrue(any(p["is_tax"] for p in parts))
        full = inv.payment_split(self.LINES, total, total)
        tax = sum(p["amount"] for p in full if p["is_tax"])
        self.assertEqual(tax, Decimal("4.95"))

    def test_due_date(self):
        self.assertEqual(inv.due_date_for("2026-01-31", 30), "2026-03-02")


if __name__ == "__main__":
    unittest.main()
