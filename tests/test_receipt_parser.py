import json
import os
import shutil
import tempfile
import unittest
import zlib
from decimal import Decimal

import tests  # noqa: F401
from parsers import receipt as R

GROCERY = """
TRADER JOE'S #123
123 Main St, Springfield IL 62701
03/14/2026  10:42 AM

BANANAS 2 @ 0.29            0.58
ORGANIC MILK                4.49
SOURDOUGH BREAD             3.99
3 x 1.50 GREEK YOGURT       4.50
SUBTOTAL                   13.56
SALES TAX 6.25%             0.85
TOTAL                      14.41
VISA ************1234      14.41
CHANGE                      0.00
"""


class TextParserTests(unittest.TestCase):
    def test_grocery_receipt(self):
        r = R.parse_text(GROCERY)
        self.assertEqual(r["vendor"], "TRADER JOE'S #123")
        self.assertEqual(r["date"], "2026-03-14")
        self.assertEqual(r["subtotal"], Decimal("13.56"))
        self.assertEqual(r["tax"], Decimal("0.85"))
        self.assertEqual(r["total"], Decimal("14.41"))
        descs = [i["description"] for i in r["line_items"]]
        self.assertEqual(len(r["line_items"]), 4, descs)
        bananas = r["line_items"][0]
        self.assertEqual(bananas["qty"], Decimal("2"))
        self.assertEqual(bananas["unit_price"], Decimal("0.29"))
        self.assertEqual(r["warnings"], [])

    def test_mismatch_is_flagged(self):
        r = R.parse_text("Cafe\n2026-01-02\nLatte 4.50\nMuffin 3.00\nSubtotal 8.00\nTotal 8.00")
        self.assertTrue(any("add up" in w for w in r["warnings"]))

    def test_tip_and_restaurant(self):
        r = R.parse_text("Joe's Diner\nJan 5, 2026\nBurger 12.00\nFries 4.00\n"
                         "Subtotal 16.00\nTax 1.20\nTip 3.00\nTotal 20.20")
        self.assertEqual(r["tip"], Decimal("3.00"))
        self.assertEqual(r["date"], "2026-01-05")
        self.assertEqual(r["warnings"], [])

    def test_dates(self):
        self.assertEqual(R.parse_date("on 5-Feb-2026 at noon"), "2026-02-05")
        self.assertEqual(R.parse_date("12/31/25"), "2025-12-31")
        self.assertEqual(R.parse_date("13/45/2026"), "")

    def test_negative_discount(self):
        r = R.parse_text("Store\nWidget 10.00\nCoupon -2.00\nTotal 8.00")
        self.assertEqual(r["line_items"][1]["line_total"], Decimal("-2.00"))
        self.assertEqual(r["warnings"], [])


class FileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, data):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return p

    def test_html(self):
        p = self.write("r.html", "<html><head><style>x{}</style></head><body>"
                       "<h1>Acme Supply</h1><p>Date: 2026-04-02</p><table>"
                       "<tr><td>Paper</td><td>$12.00</td></tr>"
                       "<tr><td>Toner</td><td>$48.00</td></tr>"
                       "<tr><td>Total</td><td>$60.00</td></tr></table></body></html>")
        r = R.parse_file(p)
        self.assertEqual(r["vendor"], "Acme Supply")
        self.assertEqual(r["total"], Decimal("60.00"))
        self.assertEqual(len(r["line_items"]), 2)

    def test_eml(self):
        eml = ("From: Orders <orders@shop.example>\r\nSubject: Your receipt\r\n"
               "Date: Tue, 3 Mar 2026 10:00:00 -0500\r\nContent-Type: text/plain\r\n\r\n"
               "Thanks for your order\r\nUSB Cable 2 x 9.99 19.98\r\nTotal 19.98\r\n")
        r = R.parse_file(self.write("r.eml", eml))
        self.assertEqual(r["vendor"], "Orders")
        self.assertEqual(r["total"], Decimal("19.98"))
        self.assertEqual(r["line_items"][0]["qty"], Decimal("2"))

    def test_text_pdf(self):
        content = (b"BT /F1 12 Tf 72 720 Td (Office Depot) Tj T* (03/01/2026) Tj T* "
                   b"(Stapler 15.00) Tj T* [(Tot) -10 (al 15.00)] TJ ET")
        stream = zlib.compress(content)
        pdf = (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(stream)).encode()
               + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream\nendobj\n%%EOF")
        r = R.parse_file(self.write("r.pdf", pdf))
        self.assertEqual(r["vendor"], "Office Depot")
        self.assertEqual(r["total"], Decimal("15.00"))

    def test_image_without_ai_is_manual(self):
        r = R.parse_file(self.write("r.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 50))
        self.assertEqual(r["method"], "manual")
        self.assertTrue(r["warnings"])


class AIResponseTests(unittest.TestCase):
    def ok(self, data):
        return {"stop_reason": "end_turn",
                "content": [{"type": "thinking", "thinking": ""},
                            {"type": "text", "text": json.dumps(data)}]}

    def test_valid(self):
        r = R.parse_ai_response(200, self.ok({
            "vendor": "Shell", "date": "2026-05-01", "currency": "usd", "subtotal": 40,
            "tax": 0, "tip": 0, "total": 40.0,
            "line_items": [{"description": "Unleaded", "qty": 10.5, "unit_price": 3.8095,
                            "line_total": 40}]}))
        self.assertEqual(r["method"], "ai")
        self.assertEqual(r["currency"], "USD")
        self.assertEqual(r["total"], Decimal("40.00"))
        self.assertEqual(r["line_items"][0]["unit_price"], Decimal("3.81"))

    def test_bad_date_and_garbage(self):
        r = R.normalize_ai_result({"vendor": None, "date": "March 3, 2026", "currency": "$$",
                                   "total": "x", "line_items": ["bad", {"qty": 0}]})
        self.assertEqual(r["date"], "2026-03-03")
        self.assertEqual(r["currency"], "USD")
        self.assertEqual(len(r["line_items"]), 1)

    def test_errors(self):
        for status, fragment in ((401, "API key"), (429, "Rate limited"), (529, "trouble"),
                                 (400, "error (400)")):
            with self.assertRaises(R.AIReceiptError) as cm:
                R.parse_ai_response(status, {"error": {"message": "nope"}}, {"retry-after": "5"})
            self.assertIn(fragment, str(cm.exception))
        with self.assertRaises(R.AIReceiptError):
            R.parse_ai_response(200, {"stop_reason": "refusal", "content": []})
        with self.assertRaises(R.AIReceiptError):
            R.parse_ai_response(200, {"stop_reason": "end_turn",
                                      "content": [{"type": "text", "text": "not json"}]})

    def test_request_body(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "r.jpg")
            with open(p, "wb") as f:
                f.write(b"\xff\xd8\xff")
            body = R.ai_request_body(p)
            self.assertEqual(body["model"], "claude-opus-5-5")
            blk = body["messages"][0]["content"][0]
            self.assertEqual(blk["source"]["media_type"], "image/jpeg")
            self.assertNotIn("temperature", body)
            self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_offline_never_crashes(self):
        class Boom:
            def post(self, *a, **k):
                import requests
                raise requests.exceptions.ConnectionError("offline")
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "r.png")
            with open(p, "wb") as f:
                f.write(b"\x89PNG")
            with self.assertRaises(R.AIReceiptError) as cm:
                R.parse_with_ai(p, "sk-test", session=Boom())
            self.assertIn("internet", str(cm.exception))
            self.assertNotIn("sk-test", str(cm.exception))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
