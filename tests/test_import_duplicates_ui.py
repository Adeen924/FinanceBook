"""v1.18 I1: rows already imported (same import_hash) are announced in a popup
with a "Show details" list (date, payee, amount, the account they're in). When
every row is a duplicate it says there's nothing new and Import stays off. The
Import Complete message counts the duplicates too. The popup can't be turned
off but appears once per parse, not again when the preview re-renders."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover
    raise unittest.SkipTest(f"PyQt6 not available: {e}")

FIRST = (b"Date,Description,Amount\r\n"
         b"03/02/2026,COFFEE SHOP,-4.50\r\n"
         b"03/03/2026,PAYROLL ACME,1500.00\r\n")
SECOND = FIRST + b"03/04/2026,GROCERY MART,-62.10\r\n"


class DuplicatePopupTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "d.db"))
        self.chk = self.db.save_account({"name": "Everyday Checking", "type": "checking"})["id"]
        self.messages = []

        def box(*a, **k):
            self.messages.append(a[2] if len(a) > 2 else "")
            return QMessageBox.StandardButton.Ok
        self._patch = mock.patch.multiple(
            QMessageBox, **{n: staticmethod(box) for n in ("information", "warning", "critical")})
        self._patch.start()
        from ui.pages.import_page import ImportPage
        self.page = ImportPage(self.db)
        self.page.refresh()

    def tearDown(self):
        self._patch.stop()
        self.page.deleteLater()
        shutil.rmtree(self.dir, ignore_errors=True)

    def load(self, data, name="bank.csv"):
        p = self.page
        p._acct_combo.setCurrentIndex(p._acct_combo.findData(self.chk))
        p.set_file(data, name)
        p._parse()

    def test_no_popup_when_nothing_is_a_duplicate(self):
        self.load(FIRST)
        self.assertEqual(self.page.dup_popups_shown, 0)
        self.assertIsNone(self.page._dup_box)

    def test_some_duplicates(self):
        self.load(FIRST)
        self.page._import()
        self.load(SECOND)
        p = self.page
        self.assertEqual(p.dup_popups_shown, 1)
        box = p._dup_box
        self.assertEqual(box.text(),
                         "2 duplicate transaction(s) were found and won't be imported.")
        details = box.detailedText().splitlines()
        self.assertEqual(len(details), 2)
        self.assertIn("COFFEE SHOP", details[0])
        self.assertIn("$4.50", details[0])
        self.assertIn("03/02/2026", details[0])
        self.assertIn("Everyday Checking", details[0])
        self.assertIn("PAYROLL ACME", details[1])
        self.assertEqual([b.text() for b in box.buttons()
                          if box.buttonRole(b) != QMessageBox.ButtonRole.ActionRole],
                         ["OK"])
        self.assertTrue(p._import_btn.isEnabled())
        # Re-rendering (Flip all signs re-parses the same file) doesn't pop up again.
        p._flip.setChecked(True)
        p._flip.setChecked(False)
        self.assertEqual(p.dup_popups_shown, 1)
        p._import()
        self.assertIn("2 duplicates skipped (already imported)", self.messages[-1])
        self.assertIn("Successfully imported 1 transactions", self.messages[-1])

    def test_all_duplicates(self):
        self.load(FIRST)
        self.page._import()
        self.load(FIRST)
        p = self.page
        self.assertEqual(p._dup_box.text(),
                         "Nothing new to import — all 2 transactions were already imported.")
        self.assertEqual(len(p._dup_box.detailedText().splitlines()), 2)
        self.assertFalse(p._import_btn.isEnabled())

    def test_parse_again_shows_it_again(self):
        self.load(FIRST)
        self.page._import()
        self.load(FIRST)
        self.page._parse()          # the user clicks Parse File again
        self.assertEqual(self.page.dup_popups_shown, 2)


if __name__ == "__main__":
    unittest.main()
