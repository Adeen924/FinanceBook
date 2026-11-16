"""v1.16 Import page: shows the detected bank format and money in / out totals,
"Flip all signs" re-parses and is remembered per account + format, a yellow
warning appears when a card file looks reversed or the type column disagrees,
re-importing creates no duplicates, and a Capital One checking file imported by
v1.15 (every row money in) is corrected instead of imported twice."""
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
except Exception as e:      # pragma: no cover — no Qt available
    raise unittest.SkipTest(f"PyQt6 not available: {e}")

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "bank_csv")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


class ImportPageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "i.db"))
        self.chk = self.db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.card = self.db.save_account({"name": "Visa", "type": "credit card"})["id"]
        self.messages = []

        def box(*a, **k):
            self.messages.append(a[2] if len(a) > 2 else "")
            return QMessageBox.StandardButton.Ok
        boxes = {n: staticmethod(box) for n in ("information", "warning", "critical")}
        self._patch = mock.patch.multiple(QMessageBox, **boxes)
        self._patch.start()
        from ui.pages.import_page import ImportPage
        self.page = ImportPage(self.db)
        self.page.refresh()

    def tearDown(self):
        self._patch.stop()
        self.page.deleteLater()
        shutil.rmtree(self.dir, ignore_errors=True)

    def load(self, account_id, name, data=None):
        p = self.page
        p._acct_combo.setCurrentIndex(p._acct_combo.findData(account_id))
        p.set_file(data if data is not None else fixture(name), name)
        p._parse()

    def _last_info(self):
        return self.messages[-1] if self.messages else ""

    def amounts(self):
        return sorted(float(t["amount"]) for t in self.page._parsed)

    def test_capital_one_checking_preview_import_and_reimport(self):
        self.load(self.chk, "capitalone_checking.csv")
        p = self.page
        self.assertIn("Capital One checking", p._format_label.text())
        self.assertIn("Money in: 9 · $6,331.10", p._totals_label.text())
        self.assertIn("Money out: 14 · $4,561.72", p._totals_label.text())
        self.assertFalse(p._warn_label.isVisibleTo(p))
        self.assertTrue(p._import_btn.isEnabled())
        p._import()
        self.assertAlmostEqual(self.db.account_balance(self.chk), 6331.10 - 4561.72, places=2)
        # A withdrawal lowers the balance.
        txns = self.db.get_transactions(account_id=self.chk) \
            if "account_id" in self.db.get_transactions.__code__.co_varnames else None
        self.load(self.chk, "capitalone_checking.csv")
        self.assertEqual(p._parsed, [])
        self.assertIn("23 duplicates skipped", p._status_label.text())
        self.assertFalse(p._import_btn.isEnabled())
        self.assertAlmostEqual(self.db.account_balance(self.chk), 1769.38, places=2)

    def test_card_purchases_raise_what_is_owed(self):
        self.load(self.card, "chase_credit.csv")
        self.assertIn("Chase credit card", self.page._format_label.text())
        self.page._import()
        self.assertAlmostEqual(self.db.account_balance(self.card), -2625.54, places=2)
        self.load(self.card, "capitalone_credit.csv")
        self.page._import()
        self.assertAlmostEqual(self.db.account_balance(self.card),
                               -2625.54 - 255.24 + 230.04, places=2)

    def test_reversed_card_file_warns_and_flip_is_remembered(self):
        data = (b"Date,Description,Amount\n01/05/2026,STORE,42.10\n"
                b"01/06/2026,GAS,30\n01/07/2026,AUTOPAY PAYMENT,-500\n")
        self.load(self.card, "card.csv", data)
        p = self.page
        self.assertIn("Unknown — generic", p._format_label.text())
        self.assertTrue(p._warn_label.isVisibleTo(p))
        self.assertIn("reversed for a credit card", p._warn_label.text())
        p._flip.setChecked(True)                      # re-parses by itself
        self.assertEqual(self.amounts(), [-42.1, -30.0, 500.0])
        self.assertFalse(p._warn_label.isVisibleTo(p))
        p._import()
        self.assertEqual(self.db.get_setting(f"import_flip|{self.card}|generic"), "1")
        # Next file of the same format into the same account starts flipped …
        more = b"Date,Description,Amount\n02/05/2026,STORE,10\n02/06/2026,CAFE,5\n"
        self.load(self.card, "card2.csv", more)
        self.assertTrue(p._flip.isChecked())
        self.assertEqual(self.amounts(), [-10.0, -5.0])
        # … but not in another account.
        self.load(self.chk, "card2.csv", more)
        self.assertFalse(p._flip.isChecked())

    def test_reimporting_a_flipped_file_creates_no_duplicates(self):
        data = b"Date,Description,Amount\n01/05/2026,STORE,42.10\n01/06/2026,GAS,30\n"
        self.load(self.card, "card.csv", data)
        self.page._flip.setChecked(True)
        self.page._import()
        self.load(self.card, "card.csv", data)
        self.assertTrue(self.page._flip.isChecked())
        self.assertEqual(self.page._parsed, [])

    def test_type_column_disagreement_warns(self):
        data = (b"Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
                b"11/01/2026,11/02/2026,STORE,Shopping,Sale,30.00,\n"
                b"11/02/2026,11/02/2026,CAFE,Food,Sale,-8.00,\n")
        self.load(self.card, "chase.csv", data)
        self.assertTrue(self.page._warn_label.isVisibleTo(self.page))
        self.assertIn("1 row(s) disagree", self.page._warn_label.text())

    def test_wrong_kind_of_account_warns(self):
        self.load(self.chk, "chase_credit.csv")
        self.assertIn("credit-card export", self.page._warn_label.text())

    def test_v115_capital_one_import_is_corrected_not_duplicated(self):
        from tests.legacy_spreadsheet_v115 import parse_spreadsheet as v115
        old, _w = v115(fixture("capitalone_checking.csv"), "c.csv", self.chk)
        self.db.bulk_save_transactions(old)
        self.assertAlmostEqual(self.db.account_balance(self.chk), 6331.10 + 4561.72, places=2)
        self.load(self.chk, "capitalone_checking.csv")
        p = self.page
        self.assertEqual(p._parsed, [])
        self.assertEqual(len(p._fixes), 14)
        self.assertIn("14 earlier imports get their sign corrected", p._status_label.text())
        self.assertIn("9 duplicates skipped", p._status_label.text())
        p._import()
        self.assertAlmostEqual(self.db.account_balance(self.chk), 6331.10 - 4561.72, places=2)
        self.load(self.chk, "capitalone_checking.csv")
        self.assertEqual((p._parsed, p._fixes), ([], []))
        self.assertIn("23 duplicates skipped", p._status_label.text())

    def test_v115_rows_in_a_locked_period_are_left_alone(self):
        from tests.legacy_spreadsheet_v115 import parse_spreadsheet as v115
        old, _w = v115(fixture("capitalone_checking.csv"), "c.csv", self.chk)
        self.db.bulk_save_transactions(old)
        self.db.set_lock_date("2026-10-31")
        self.load(self.chk, "capitalone_checking.csv")
        self.page._import()
        # Only the two November withdrawals were after the lock.
        self.assertIn("left alone", self._last_info())
        self.assertAlmostEqual(self.db.account_balance(self.chk),
                               6331.10 + 4561.72 - 2 * (99.87 + 3000), places=2)

    def test_supported_banks_dialog_lists_the_registry(self):
        from parsers.bank_formats import REGISTRY
        from ui.dialogs.supported_banks_dialog import SupportedBanksDialog
        dlg = SupportedBanksDialog(self.page)
        self.assertEqual(dlg.table.rowCount(), len(REGISTRY))
        banks = {dlg.table.item(r, 0).text() for r in range(dlg.table.rowCount())}
        self.assertIn("Capital One", banks)
        verified = [dlg.table.item(r, 3).text() for r in range(dlg.table.rowCount())]
        self.assertEqual(verified.count("Yes"), sum(f.verified for f in REGISTRY))


if __name__ == "__main__":
    unittest.main()
