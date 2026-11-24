"""v1.19: BMO (U.S.) checking CSV (B1–B4) and the Import page's layout and
dates (I1–I3), in personal AND business books.

Before v1.19 a BMO export fell through to the generic column guessing: no
"BMO checking" detection, no check of the CREDIT/DEBIT column against the
sign, and an export with unsigned amounts would have imported every row as
money in. The import preview showed ISO dates (2026-11-10).
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.book_helper import BothBooksTestCase
from parsers import bank_formats
from parsers.spreadsheet import analyze_spreadsheet

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover
    raise unittest.SkipTest(f"PyQt6 not available: {e}")

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "bank_csv", "bmo_checking.csv")
ARTIFACTS = os.path.join(os.path.dirname(__file__), "artifacts")


def bmo_bytes() -> bytes:
    with open(FIXTURE, "rb") as f:
        return f.read()


EXPECTED = [  # date, amount, payee — every row of the fixture
    ("2026-11-10", -500.0, "ZELLE TO ALEX SAMPLE"),
    ("2026-11-10", 500.0, "PPD JORDAN SAMPLE CO PAYROLL"),
    ("2026-11-04", -550.0, "ZELLE TO ALEX SAMPLE"),
    ("2026-11-03", 550.0, "PPD JORDAN SAMPLE CO PAYROLL"),
    ("2026-11-02", -3000.0, "ZELLE TO ALEX SAMPLE"),
    ("2026-11-02", -500.0, "ZELLE TO JORDAN SAMPLE"),
    ("2026-11-02", -370.0, "ZELLE TO ALEX SAMPLE"),
    ("2026-10-30", -650.0, "ZELLE TO ALEX SAMPLE"),
    ("2026-10-30", -550.0, "ZELLE TO JORDAN SAMPLE"),
    ("2026-10-30", -300.0, "ZELLE TO ALEX SAMPLE"),
    ("2026-10-29", -250.0, "ZELLE TO CASEY EXAMPLE"),
    ("2026-10-16", 5600.0, "DDA DEPOSIT"),
    ("2026-10-13", 25.0, "PC TRANSFER CREDIT"),
]


class BmoParseTests(unittest.TestCase):
    def rows(self, data, **kw):
        res = analyze_spreadsheet(data, "BMO.csv", "ACC", account_kind="checking", **kw)
        return res, [(r["date"], float(r["amount"]), r["payee"], r["memo"]) for r in res.rows]

    def test_detected_and_every_row_right(self):
        res, got = self.rows(bmo_bytes())
        self.assertEqual(res.format.id, "bmo_checking")
        self.assertEqual(res.format.label, "BMO (U.S.) checking")
        self.assertEqual([g[:3] for g in got], EXPECTED)
        self.assertEqual({g[3] for g in got}, {""})     # memo: nothing extra
        self.assertEqual(res.type_mismatches, 0)
        self.assertEqual(res.warnings, [])
        money_in = sum(a for _d, a, _p in EXPECTED if a > 0)
        money_out = sum(-a for _d, a, _p in EXPECTED if a < 0)
        self.assertEqual((money_in, money_out), (6675.0, 6670.0))

    def test_variants_preamble_spaces_unsigned_yyyymmdd_quotes(self):
        text = bmo_bytes().decode("utf-8")
        lines = text.split("\r\n")
        head = ",".join(f" {h} " for h in lines[0].split(","))         # padded headers
        body = []
        for ln in lines[1:]:
            if not ln:
                continue
            cells = ln.split(",")
            m, d, y = cells[0].split("/")
            cells[0] = f"{y}{m}{d}"                                      # YYYYMMDD
            cells[1] = f'"{cells[1]}, INC"' if "PAYROLL" in cells[1] else cells[1]
            cells[2] = cells[2].lstrip("-")                              # unsigned
            body.append(",".join(cells))
        variant = ("Following data is valid as of 11/23/2026 08:00 AM\r\n\r\n"
                   + head + "\r\n" + "\r\n".join(body) + "\r\n").encode("utf-8")
        res, got = self.rows(variant)
        self.assertEqual(res.format.id, "bmo_checking")
        want = [(d, a, p + (", INC" if "PAYROLL" in p else "")) for d, a, p in EXPECTED]
        self.assertEqual([g[:3] for g in got], want)

    def test_same_duplicate_keys_as_a_pre_119_generic_import(self):
        data = bmo_bytes()
        new = analyze_spreadsheet(data, "BMO.csv", "ACC", account_kind="checking").rows
        with mock.patch.object(bank_formats, "detect", return_value=(None, None)):
            old = analyze_spreadsheet(data, "BMO.csv", "ACC", account_kind="checking").rows
        self.assertEqual([r["import_hash"] for r in new], [r["import_hash"] for r in old])
        self.assertEqual(len({r["import_hash"] for r in new}), len(new))

    def test_in_supported_banks_doc(self):
        from pathlib import Path
        doc = (Path(__file__).resolve().parents[1] / "docs" / "BANK_FORMATS.md").read_text(
            encoding="utf-8")
        self.assertIn("BMO (U.S.)", doc)
        self.assertIn("bmo_checking.csv", doc)


class BmoImportTests(BothBooksTestCase):
    def setUp(self):
        super().setUp()
        self.messages = []

        def box(*a, **k):
            self.messages.append(a[2] if len(a) > 2 else "")
            return QMessageBox.StandardButton.Ok
        self._patch = mock.patch.multiple(
            QMessageBox, **{n: staticmethod(box) for n in ("information", "warning",
                                                          "critical")})
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        super().tearDown()

    def page(self, db, acct):
        from ui.pages.import_page import ImportPage
        p = ImportPage(db)
        p.refresh()
        p._acct_combo.setCurrentIndex(p._acct_combo.findData(acct))
        p.set_file(bmo_bytes(), "BMO.csv")
        p._parse()
        return p

    def test_import_signs_and_no_duplicates_on_reimport(self):
        for db in self.each_book():
            acct = self.checking(db, "BMO Checking", 0)
            p = self.page(db, acct)
            self.assertIn("BMO", p._format_label.text())
            # I1: preview dates are MM/DD/YYYY.
            dates = [p._table.item(r, 0).text() for r in range(p._table.rowCount())
                     if p._table.item(r, 0) and "/" in p._table.item(r, 0).text()]
            self.assertIn("11/10/2026", dates)
            self.assertFalse(any("-" in d and d[:4].isdigit() for d in dates))
            p._import()
            txns = db.get_transactions(account_id=acct)
            self.assertEqual(len(txns), 13)
            got = sorted((t["date"], float(t["amount"]), t["payee"]) for t in txns)
            self.assertEqual(got, sorted(EXPECTED))
            self.assertEqual(round(db.account_balance(acct), 2), 5.0)
            # Re-importing the same file adds nothing.
            p2 = self.page(db, acct)
            self.assertEqual(p2._dup_count, 13)
            self.assertFalse(p2._import_btn.isEnabled())
            self.assertEqual(len(db.get_transactions(account_id=acct)), 13)


class ImportLayoutTests(BothBooksTestCase):
    """I2 / I3: Step 2 in two columns, Step 3's title and buttons on the same
    line to its right, at 1280×720 — also while Step 2 is disabled."""

    def test_layout_at_1280x720(self):
        from ui.pages.import_page import ImportPage
        for db in self.each_book():
            p = ImportPage(db)
            p.refresh()
            p.resize(1280 - 220, 720 - 40)     # the page inside the window's sidebar
            p.show()
            _app.processEvents()

            def rect(w):
                tl = w.mapTo(p, w.rect().topLeft())
                return tl.x(), tl.y(), w.width(), w.height()
            gx, gy, gw, gh = rect(p._col_grp)
            sx, sy, sw, sh = rect(p._step3_box)
            # Two columns: Debit / Credit to the right of Date / Payee / Amount.
            self.assertGreater(rect(p._col_debit)[0], rect(p._col_date)[0] + 100)
            self.assertEqual(rect(p._col_debit)[1], rect(p._col_date)[1])
            self.assertEqual(rect(p._col_credit)[1], rect(p._col_payee)[1])
            # Step 3 on the Step 2 line, to its right, fully visible.
            self.assertGreaterEqual(sx, gx + gw)
            self.assertLess(sy, gy + gh)
            self.assertLessEqual(sx + sw, p.width())
            for b in (p._flip, p._parse_btn, p._import_btn):
                self.assertTrue(b.isVisible())
                self.assertGreaterEqual(b.width(), b.sizeHint().width() - 1, b.text())
            # The preview table starts right under that line.
            tx, ty, _tw, th = rect(p._table)
            self.assertGreater(ty, gy + gh)
            self.assertLess(ty, gy + gh + 120)
            self.assertGreater(th, 200)
            # Step 2 disabled (OFX file / nothing chosen) leaves Step 3 usable.
            self.assertFalse(p._col_grp.isEnabled())
            self.assertTrue(p._parse_btn.isEnabled())
            os.makedirs(ARTIFACTS, exist_ok=True)
            p.grab().save(os.path.join(ARTIFACTS, f"import_page_1280x720_{db.book_type()}.png"))
            p.close()


if __name__ == "__main__":
    unittest.main()
