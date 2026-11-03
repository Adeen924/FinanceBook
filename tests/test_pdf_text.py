"""PDF text extraction (parsers/pdf_text.py), image-only detection and OCR."""
import os
import shutil
import tempfile
import unittest
from decimal import Decimal

import tests  # noqa: F401
from tests import pdf_fixtures as F
from parsers import pdf_text as P
from parsers import receipt as R
from parsers import ocr

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class ExtractionTests(unittest.TestCase):
    def test_cid_identity_h_with_tounicode(self):
        doc = P.read_pdf(F.skia_cid_pdf())
        lines = doc.text.splitlines()
        self.assertEqual(lines[0], "Order Details")
        self.assertIn("Order placed November 2, 2026", doc.text)
        # Labels and amounts drawn in separate runs end up on one line, with a
        # column gap between them.
        row = next(ln for ln in lines if "Item(s) Subtotal:" in ln)
        self.assertRegex(row, r"^Jordan Sample\s{3,}Item\(s\) Subtotal:\s{3,}\$12\.50$")
        # bfrange (digits) and bfchar (ligature, curly quote) both decode.
        self.assertIn("ﬁts all", doc.text)
        self.assertIn("’Blue’", doc.text)

    def test_y_flipped_ctm_keeps_reading_order(self):
        lines = P.read_pdf(F.skia_cid_pdf()).text.splitlines()
        self.assertLess(lines.index("Arriving today"), lines.index("Back to top"))

    def test_winansi_fragments_rejoin_into_words(self):
        doc = P.read_pdf(F.winansi_pdf())
        self.assertIn("Invoice # 90000001", doc.text)
        self.assertRegex(doc.text, r"Total charged to credit card\s+16\.69")
        row = next(ln for ln in doc.text.splitlines() if "PART:" in ln)
        self.assertRegex(row, r"^1\s+2\s+0\s+2\s+PART: 123-FAKE01-ND\s{3,}DESC: TEST WIDGET 5V"
                              r"\s{3,}4\.25000\s+8\.50 T$")
        self.assertEqual(doc.pages, 2)
        self.assertIn("Page 2 of 2", doc.text)

    def test_orphan_streams_and_rotated_labels_are_skipped(self):
        text = P.read_pdf(F.winansi_pdf()).text
        self.assertNotIn("ORPHAN", text)        # a content stream no page uses
        self.assertNotIn("Bill To", text)       # vertical text in the margin

    def test_object_stream_and_xref_stream(self):
        doc = P.read_pdf(F.objstm_pdf())
        self.assertEqual(doc.text.splitlines()[0], "Corner Hardware")
        r = R.parse_text(doc.text)
        self.assertEqual(r["total"], Decimal("19.99"))

    def test_image_only_detection(self):
        doc = P.read_pdf(F.image_pdf(F.TINY_JPEG, 8, 8))
        self.assertTrue(doc.image_only)
        self.assertEqual(doc.text, "")
        jpeg, w, h = doc.images[0]
        self.assertEqual((w, h), (8, 8))
        self.assertTrue(jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9"))

    def test_encrypted_and_corrupt(self):
        with self.assertRaises(P.PDFEncrypted):
            P.read_pdf(F.encrypted_pdf())
        with self.assertRaises(P.PDFError):
            P.read_pdf(b"not a pdf at all")
        # Truncated in the middle of a stream: no crash, whatever text survives.
        data = F.winansi_pdf()
        P.read_pdf(data[: len(data) // 2])
        self.assertEqual(R.pdf_to_text(F.encrypted_pdf()), "")

    def test_legacy_reader_still_used_for_pageless_files(self):
        import zlib
        content = b"BT /F1 12 Tf 72 720 Td (Office Depot) Tj T* (Total 15.00) Tj ET"
        stream = zlib.compress(content)
        pdf = (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(stream)).encode()
               + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream\nendobj\n%%EOF")
        self.assertIn("Office Depot", R.pdf_to_text(pdf))


class FileMessageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, data):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_image_pdf_without_ocr_says_its_an_image(self):
        r = R.parse_file(self.write("scan.pdf", F.image_pdf(F.TINY_JPEG, 8, 8)), use_ocr=False)
        self.assertEqual(r["method"], "manual")
        self.assertEqual(r["warnings"], [R.MSG_IMAGE])

    def test_encrypted_and_corrupt_pdfs_fail_gracefully(self):
        for name, data in (("enc.pdf", F.encrypted_pdf()), ("bad.pdf", b"%PDF-1.4\n\x00\xff garbage"),
                           ("empty.pdf", b"")):
            r = R.parse_file(self.write(name, data))
            self.assertEqual(r["method"], "manual", name)
            self.assertIn("type it in", r["warnings"][0], name)

    def test_text_without_totals_keeps_the_text(self):
        r = R.parse_file(self.write("note.txt", b"Thanks for shopping\nSee you soon\n"))
        self.assertEqual(r["method"], "manual")
        self.assertIn("Thanks for shopping", r["raw_text"])
        self.assertEqual(r["warnings"], [R.MSG_NO_TOTALS])

    def test_large_pdf_is_bounded(self):
        page = (b"BT /F1 10 Tf 72 700 Td (" + b"Line of text 1.00 " * 20 + b") Tj ET\n") * 40
        objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>",
                2: ("<< /Type /Pages /Count 200 /Kids [" +
                    " ".join(f"{10 + i} 0 R" for i in range(200)) + "] >>").encode(),
                3: F.stream(page),
                4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
        for i in range(200):
            objs[10 + i] = (b"<< /Type /Page /Parent 2 0 R /Contents 3 0 R "
                            b"/Resources << /Font << /F1 4 0 R >> >> >>")
        doc = P.read_pdf(F.build(objs, 1))
        self.assertEqual(doc.pages, 200)
        self.assertLessEqual(doc.text.count("\n\n") + 1, P.MAX_PAGES)


@unittest.skipUnless(ocr.available(), "Windows OCR is only available on Windows")
class WindowsOcrTests(unittest.TestCase):
    def test_ocr_reads_image_only_invoice_fixture(self):
        r = R.parse_file(os.path.join(FIXTURES, "jlcpcb_like_image.pdf"))
        if r["method"] == "manual":
            self.skipTest("Windows OCR is present but has no usable language: "
                          + "; ".join(r["warnings"]))
        self.assertEqual(r["method"], "ocr")
        self.assertEqual(r["vendor"], "JLCPCB")
        self.assertEqual(r["date"], "2026-10-08")
        self.assertEqual([(i["qty"], i["unit_price"], i["line_total"]) for i in r["line_items"]],
                         [(Decimal(5), Decimal("9.03"), Decimal("45.13"))])
        for k, v in (("subtotal", "45.13"), ("shipping", "35.13"), ("discount", "10.00"),
                     ("tax", "2.73"), ("duties", "16.79"), ("total", "89.78")):
            self.assertEqual(r[k], Decimal(v), k)
        self.assertEqual(r["warnings"], [])

    def test_ocr_error_on_non_image(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "x.png")
            with open(p, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"\0" * 40)
            with self.assertRaises(ocr.OCRError):
                ocr.ocr_image(p)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class OcrLayoutTests(unittest.TestCase):
    def test_words_become_lines_with_column_gaps(self):
        tsv = ("1881\t1789\t191\t25\tMerchandise\n2084\t1790\t79\t24\tTotal:\n"
               "2409\t1789\t63\t25\tUSD\n2484\t1786\t95\t31\t$45.13\n"
               "1881\t1865\t137\t31\tShipping:\n2409\t1865\t64\t25\tUSD\n2485\t1862\t94\t31\t$35.13\n")
        lines = ocr.words_to_text(tsv).splitlines()
        self.assertRegex(lines[0], r"^Merchandise Total:\s{3,}USD \$45\.13$")
        self.assertRegex(lines[1], r"^Shipping:\s{3,}USD \$35\.13$")


if __name__ == "__main__":
    unittest.main()
