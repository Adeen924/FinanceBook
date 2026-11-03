"""Vendor parsers (Amazon, DigiKey, JLCPCB), the generic parser's shipping /
discount / duties handling, dates, check_totals and AI normalization."""
import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal

import tests  # noqa: F401
from parsers import receipt as R

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
PRIVATE = os.path.join(os.path.dirname(HERE), "_private_samples")


def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def money(r) -> dict:
    return {k: r[k] for k in R.MONEY_FIELDS}


def M(**kw) -> dict:
    out = {k: Decimal("0.00") for k in R.MONEY_FIELDS}
    out.update({k: Decimal(v) for k, v in kw.items()})
    return out


def items(r) -> list:
    return [(i["description"], i["qty"], i["unit_price"], i["line_total"], i["sku"])
            for i in r["line_items"]]


class AmazonTests(unittest.TestCase):
    def test_order_details_print(self):
        r = R.parse_receipt_text(fixture("amazon_order_details.txt"))
        self.assertEqual(r["vendor"], "Amazon")
        self.assertEqual(r["date"], "2026-11-02")          # order date, not the print date
        self.assertEqual(r["date_note"], "")
        self.assertEqual(r["order_number"], "112-0000000-0000001")
        self.assertEqual(money(r), M(subtotal="5.99", tax="0.46", total="6.45"))
        self.assertEqual(items(r), [(
            "Egofine Plexiglass Sheets Acrylic Sheets 6 Pack of 8x10'' 0.04 Thick Clear Plastic "
            "Cast Transparent Plexi Glass for Crafting Projects, Replacement Picture Frame Glass, "
            "DIY Display, Easy to Cut", Decimal(1), Decimal("5.99"), Decimal("5.99"), "")])
        self.assertEqual(r["warnings"], [])

    def test_total_before_tax_is_not_the_total(self):
        r = R.parse_receipt_text(fixture("amazon_order_details.txt"))
        self.assertNotEqual(r["total"], Decimal("5.99"))

    def test_multi_item_multi_shipment_free_shipping_coupon_gift_card(self):
        r = R.parse_receipt_text(fixture("amazon_multi_shipment.txt"))
        self.assertEqual(r["date"], "2026-10-18")
        # Shipping nets the free-shipping credit; coupon + gift card are discounts.
        self.assertEqual(money(r), M(subtotal="64.97", shipping="5.99", discount="13.00",
                                     tax="6.80", total="64.76"))
        got = items(r)
        self.assertEqual([(q, u, t) for _, q, u, t, _ in got],
                         [(Decimal(2), Decimal("9.99"), Decimal("19.98")),
                          (Decimal(1), Decimal("24.99"), Decimal("24.99")),
                          (Decimal(1), Decimal("20.00"), Decimal("20.00"))])
        self.assertEqual(got[0][0], "USB-C Cable 6ft Braided, 2 Pack")
        self.assertTrue(got[1][0].startswith("Wireless Mouse with a Very Long Product Title"))
        self.assertTrue(got[1][0].endswith("Silent Clicks, 2.4GHz, Black"))
        self.assertEqual(got[2][0], "Stainless Steel Water Bottle 32 oz")
        self.assertEqual(r["warnings"], [])

    def test_legacy_invoice_layout(self):
        r = R.parse_receipt_text(fixture("amazon_legacy_invoice.txt"))
        self.assertEqual(r["date"], "2026-03-03")
        self.assertEqual(money(r), M(subtotal="28.99", shipping="4.99", discount="1.96",
                                     tax="2.10", total="34.12"))
        self.assertEqual([(d, q, t) for d, q, _, t, _ in items(r)],
                         [("Notebook, College Ruled, 5 Pack", Decimal(1), Decimal("12.49")),
                          ("Gel Pens, Black", Decimal(3), Decimal("16.50"))])
        self.assertEqual(r["warnings"], [])

    def test_printed_price_already_line_total(self):
        text = fixture("amazon_multi_shipment.txt").replace(
            "Item(s) Subtotal:                 $64.97", "Item(s) Subtotal:                 $54.98")
        text = text.replace("Total before tax:                 $67.96",
                            "Total before tax:                 $57.97").replace(
            "Grand Total:                    $64.76", "Grand Total:                    $54.77")
        r = R.parse_receipt_text(text)
        self.assertEqual(r["line_items"][0]["line_total"], Decimal("9.99"))
        self.assertEqual(r["line_items"][0]["unit_price"], Decimal("5.00"))  # 9.99 / 2
        self.assertEqual(r["warnings"], [])

    def test_cid_pdf_end_to_end(self):
        r = R.parse_file(os.path.join(FIXTURES, "amazon_like_cid.pdf"), use_ocr=False)
        self.assertEqual((r["method"], r["vendor"], r["date"]), ("text", "Amazon", "2026-11-02"))
        self.assertEqual(money(r), M(subtotal="12.50", tax="0.97", total="13.47"))
        self.assertEqual(items(r), [("Acme Widget Holder, 2 Pack 'Blue' fits all Desk Organizer",
                                     Decimal(1), Decimal("12.50"), Decimal("12.50"), "")])


class DigiKeyTests(unittest.TestCase):
    def test_invoice(self):
        r = R.parse_receipt_text(fixture("digikey_invoice.txt"))
        self.assertEqual(r["vendor"], "DigiKey")
        self.assertEqual(r["date"], "2026-10-13")
        self.assertEqual(r["order_number"], "900000001")
        # Sales Amount is the items subtotal; "** Charges subtotal **" includes
        # shipping and must not be used as the subtotal.
        self.assertEqual(money(r), M(subtotal="33.68", shipping="13.99", tax="2.61",
                                     total="50.28"))
        self.assertEqual(items(r), [("RASPBERRY PI PICO 2 W RP2350", Decimal(4), Decimal("8.42"),
                                     Decimal("33.68"), "2648-SC1633CT-ND")])
        self.assertEqual(r["warnings"], [])

    def test_winansi_pdf_end_to_end(self):
        r = R.parse_file(os.path.join(FIXTURES, "digikey_like_winansi.pdf"), use_ocr=False)
        self.assertEqual((r["vendor"], r["date"], r["order_number"]),
                         ("DigiKey", "2026-02-03", "90000001"))       # Invoice Date column
        self.assertEqual(money(r), M(subtotal="8.50", shipping="6.99", tax="1.20",
                                     total="16.69"))
        self.assertEqual(items(r), [("TEST WIDGET 5V", Decimal(2), Decimal("4.25"),
                                     Decimal("8.50"), "123-FAKE01-ND")])

    def test_rows_split_across_lines_still_read(self):
        text = fixture("digikey_invoice.txt").replace(
            "1           4              0              4        PART: 2648-SC1633CT-ND"
            "                                        DESC: RASPBERRY PI PICO 2 W RP2350"
            "                           8.42000          33.68 T",
            "PART: 2648-SC1633CT-ND\nDESC: RASPBERRY PI PICO 2 W RP2350\n  1     8.42000     33.68 T")
        r = R.parse_receipt_text(text)
        self.assertEqual(items(r), [("RASPBERRY PI PICO 2 W RP2350", Decimal(4), Decimal("8.42"),
                                     Decimal("33.68"), "2648-SC1633CT-ND")])


class JlcpcbTests(unittest.TestCase):
    def test_ocr_text(self):
        r = R.parse_receipt_text(fixture("jlcpcb_invoice_ocr.txt"), "ocr")
        self.assertEqual((r["vendor"], r["method"]), ("JLCPCB", "ocr"))
        self.assertEqual(r["order_number"], "13310000A2026100200000000")
        # The bare "Subtotal 70.26" is after shipping and the discount.
        self.assertEqual(money(r), M(subtotal="45.13", shipping="35.13", discount="10.00",
                                     tax="2.73", duties="16.79", total="89.78"))
        # Ext. Price is trusted over qty × unit (5 × 9.03 = 45.15).
        self.assertEqual(items(r), [("Rigid Populated printed circuit board", Decimal(5),
                                     Decimal("9.03"), Decimal("45.13"), "")])
        self.assertEqual(r["warnings"], [])

    def test_ambiguous_date_uses_vendor_hint_visibly(self):
        r = R.parse_receipt_text(fixture("jlcpcb_invoice_ocr.txt"))
        self.assertEqual(r["date"], "2026-10-08")
        self.assertIn("08/10/2026", r["date_note"])
        self.assertIn("day/month", r["date_note"])


class GenericParserTests(unittest.TestCase):
    def test_shipping_discount_duties_and_after_shipping_subtotal(self):
        r = R.parse_text("Gadget Shop\n2026-05-01\nWidget  20.00\nGizmo   10.00\n"
                         "Subtotal  30.00\nShipping & handling  8.00\nFree shipping  -3.00\n"
                         "Promo discount  -5.00\nImport duties  4.00\nSales tax  2.10\n"
                         "Grand total  36.10\nShipping address: 1 Example Rd 12.00")
        self.assertEqual(money(r), M(subtotal="30.00", shipping="5.00", discount="5.00",
                                     duties="4.00", tax="2.10", total="36.10"))
        self.assertEqual(len(r["line_items"]), 2)
        self.assertEqual(r["warnings"], [])

    def test_bare_subtotal_that_includes_shipping(self):
        r = R.parse_text("Parts Co\nShipping  12.00\nSubtotal  52.00\nTax  3.00\nTotal  55.00")
        self.assertEqual(r["subtotal"], Decimal("40.00"))
        self.assertEqual(r["warnings"], [])

    def test_shipping_larger_than_subtotal_and_no_shipping(self):
        r = R.parse_text("Tiny Parts\nResistor  0.10\nShipping  9.95\nTotal  10.05")
        self.assertEqual(money(r), M(subtotal="0.10", shipping="9.95", total="10.05"))
        self.assertEqual(r["warnings"], [])
        r = R.parse_text("Cafe\nLatte  4.50\nTotal  4.50")
        self.assertEqual(r["shipping"], Decimal("0.00"))
        self.assertEqual(r["warnings"], [])

    def test_coupon_among_items_stays_a_line(self):
        r = R.parse_text("Store\nWidget 10.00\nCoupon -2.00\nTotal 8.00")
        self.assertEqual(r["line_items"][1]["line_total"], Decimal("-2.00"))
        self.assertEqual(r["discount"], Decimal("0.00"))

    def test_numbers_that_are_not_money(self):
        r = R.parse_text("Lab Supply\nHTSUS: 8471.50.0150\nWeight 0.06 kgs\nWidget  4.00\n"
                         "Unit 8.42000\nTotal 4.00")
        self.assertEqual([i["description"] for i in r["line_items"]], ["Weight", "Widget"]
                         if r["line_items"][0]["line_total"] == Decimal("0.06") else ["Widget"])
        self.assertNotIn(Decimal("8471.50"), [i["line_total"] for i in r["line_items"]])

    def test_dates(self):
        self.assertEqual(R.parse_date_info("Date: 08/10/2026"), ("2026-08-10", "08/10/2026"))
        self.assertEqual(R.parse_date_info("Date: 08/10/2026", day_first=True), ("2026-10-08", ""))
        self.assertEqual(R.parse_date_info("Date: 13/10/2026"), ("2026-10-13", ""))
        self.assertEqual(R.parse_date_info("Date: 10/13/2026"), ("2026-10-13", ""))
        self.assertEqual(R.parse_date_info("05/05/2026"), ("2026-05-05", ""))
        r = R.parse_text("Hardware Hut\nDate: 08/10/2026\nNails  3.00\nTotal  3.00")
        self.assertEqual(r["date"], "2026-08-10")
        self.assertEqual(r["date_note"], "Date is ambiguous (08/10/2026) — check it.")

    def test_long_titles_are_capped(self):
        from parsers.receipt_vendors import _clean_desc
        self.assertEqual(len(_clean_desc("word " * 100)), 250)
        self.assertTrue(_clean_desc("word " * 100).endswith("…"))


class CheckTotalsTests(unittest.TestCase):
    def test_every_sample_balances_exactly(self):
        for name, method in (("amazon_order_details.txt", "text"),
                             ("amazon_multi_shipment.txt", "text"),
                             ("digikey_invoice.txt", "text"), ("jlcpcb_invoice_ocr.txt", "ocr")):
            r = R.parse_receipt_text(fixture(name), method)
            item_sum = sum(i["line_total"] for i in r["line_items"])
            self.assertEqual(item_sum, r["subtotal"], name)
            self.assertEqual(item_sum + r["shipping"] + r["duties"] + r["tax"] + r["tip"]
                             - r["discount"], r["total"], name)
            self.assertEqual(R.check_totals(r), [], name)

    def test_mismatch_mentions_the_charges(self):
        res = {"subtotal": Decimal("10"), "shipping": Decimal("5"), "discount": Decimal("1"),
               "tax": Decimal("1"), "total": Decimal("20"),
               "line_items": [{"description": "x", "qty": 1, "unit_price": 10, "line_total": 10}]}
        warn = R.check_totals(res)
        self.assertEqual(len(warn), 1)
        self.assertIn("Items + shipping + tax − discount = $15.00", warn[0])

    def test_unit_price_rounding_tolerance(self):
        ok = {"line_items": [{"description": "pcb", "qty": 5, "unit_price": "9.03",
                              "line_total": "45.13"}]}
        self.assertEqual(R.check_totals(ok), [])
        bad = {"line_items": [{"description": "pcb", "qty": 5, "unit_price": "9.03",
                               "line_total": "40.00"}]}
        self.assertEqual(len(R.check_totals(bad)), 1)


class AINormalizationTests(unittest.TestCase):
    def test_old_shape_without_new_fields(self):
        r = R.normalize_ai_result({"vendor": "Shop", "date": "2026-01-02", "currency": "USD",
                                   "subtotal": 10, "tax": 1, "tip": 0, "total": 11,
                                   "line_items": [{"description": "A", "qty": 1,
                                                   "unit_price": 10, "line_total": 10}]})
        self.assertEqual(money(r), M(subtotal="10", tax="1", total="11"))
        self.assertEqual(r["line_items"][0]["sku"], "")
        self.assertEqual(r["warnings"], [])

    def test_new_shape(self):
        data = {"vendor": "JLCPCB", "date": "2026-10-08", "currency": "USD",
                "order_number": "W1", "subtotal": 45.13, "shipping": 35.13, "discount": -10,
                "duties": 16.79, "tax": 2.73, "tip": 0, "total": 89.78,
                "line_items": [{"description": "PCB", "sku": "SMT1", "qty": 5,
                                "unit_price": 9.03, "line_total": 45.13}]}
        payload = {"stop_reason": "end_turn", "content": [{"type": "text",
                                                            "text": json.dumps(data)}]}
        r = R.parse_ai_response(200, payload)
        self.assertEqual(money(r), M(subtotal="45.13", shipping="35.13", discount="10.00",
                                     duties="16.79", tax="2.73", total="89.78"))
        self.assertEqual((r["order_number"], r["line_items"][0]["sku"]), ("W1", "SMT1"))
        self.assertEqual(r["warnings"], [])

    def test_ambiguous_ai_date(self):
        r = R.normalize_ai_result({"date": "08/10/2026"})
        self.assertEqual(r["date_note"], "Date is ambiguous (08/10/2026) — check it.")

    def test_schema_and_pdf_request(self):
        req = R.RECEIPT_SCHEMA["required"]
        for k in ("shipping", "discount", "duties", "order_number"):
            self.assertIn(k, req)
        self.assertIn("sku", R.RECEIPT_SCHEMA["properties"]["line_items"]["items"]["required"])
        d = tempfile.mkdtemp()
        try:
            from tests import pdf_fixtures as F
            p = os.path.join(d, "scan.pdf")
            with open(p, "wb") as f:
                f.write(F.image_pdf(F.TINY_JPEG, 8, 8))
            blk = R.ai_request_body(p)["messages"][0]["content"][0]
            self.assertEqual((blk["type"], blk["source"]["media_type"]),
                             ("document", "application/pdf"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(os.path.isdir(PRIVATE), "real receipts in _private_samples/ not present")
class RealSampleTests(unittest.TestCase):
    """Runs only on a machine that has the owner's real receipts (never committed)."""

    def parse(self, name):
        path = os.path.join(PRIVATE, name)
        if not os.path.exists(path):
            self.skipTest(f"{name} not present")
        return R.parse_file(path)

    def test_amazon(self):
        r = self.parse("Amazon_11-2-2026.pdf")
        self.assertEqual((r["vendor"], r["date"], r["method"]), ("Amazon", "2026-11-02", "text"))
        self.assertTrue(r["order_number"])
        self.assertEqual(money(r), M(subtotal="5.99", tax="0.46", total="6.45"))
        self.assertEqual(len(r["line_items"]), 1)
        it = r["line_items"][0]
        self.assertTrue(it["description"].startswith(
            "Egofine Plexiglass Sheets Acrylic Sheets 6 Pack of 8x10'' 0.04 Thick Clear Plastic"))
        self.assertNotIn("Sold by", it["description"])
        self.assertEqual((it["qty"], it["line_total"]), (Decimal(1), Decimal("5.99")))
        self.assertEqual(r["warnings"], [])

    def test_digikey(self):
        r = self.parse("Digikey_10-13-2026.pdf")
        self.assertEqual((r["vendor"], r["date"], r["order_number"]),
                         ("DigiKey", "2026-10-13", "134434395"))
        self.assertEqual(money(r), M(subtotal="33.68", shipping="13.99", tax="2.61",
                                     total="50.28"))
        self.assertEqual(items(r), [("RASPBERRY PI PICO 2 W RP2350", Decimal(4), Decimal("8.42"),
                                     Decimal("33.68"), "2648-SC1633CT-ND")])
        self.assertEqual(r["warnings"], [])

    def test_jlcpcb(self):
        from parsers import ocr
        if not ocr.available():
            self.skipTest("needs Windows OCR")
        r = self.parse("JLCPCB_8-10-2026.pdf")
        self.assertEqual((r["vendor"], r["date"], r["method"]), ("JLCPCB", "2026-10-08", "ocr"))
        self.assertTrue(r["date_note"])
        self.assertEqual(money(r), M(subtotal="45.13", shipping="35.13", discount="10.00",
                                     tax="2.73", duties="16.79", total="89.78"))
        self.assertEqual(items(r), [("Rigid Populated printed circuit board", Decimal(5),
                                     Decimal("9.03"), Decimal("45.13"), "")])
        self.assertEqual(r["warnings"], [])


if __name__ == "__main__":
    unittest.main()
