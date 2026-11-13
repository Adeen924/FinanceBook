"""v1.14 Transactions screen: DateField fills the year for month/day entries,
and the register stays on the transaction you just edited."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database

try:
    from PyQt6.QtWidgets import QApplication, QDialog
    from PyQt6.QtCore import QDate
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover — no Qt available
    raise unittest.SkipTest(f"PyQt6 not available: {e}")

from ui.widgets import DateField, parse_date_text


class DateFieldTests(unittest.TestCase):
    def test_parse(self):
        today = QDate(2026, 11, 13)
        cases = {"11/5": "2026-11-05", "11-5": "2026-11-05", "1/2": "2026-01-02",
                 "11/5/26": "2026-11-05", "3-4-07": "2007-03-04",
                 "11/05/2026": "2026-11-05", "2025-12-31": "2025-12-31"}
        for text, want in cases.items():
            self.assertEqual(parse_date_text(text, today=today).toString("yyyy-MM-dd"), want, text)
        for bad in ("13/5", "2/30", "11/5/2", "11/5/202", "abc", ""):
            self.assertFalse(parse_date_text(bad, today=today).isValid(), bad)

    def test_typing_month_day_then_leaving_fills_current_year(self):
        f = DateField()
        f.setDate(QDate(2020, 1, 1))
        seen = []
        f.dateChanged.connect(seen.append)
        f._edit.setText("11/5")
        f._edit.textEdited.emit("11/5")
        f._edit.editingFinished.emit()           # Tab / leaving the field
        year = QDate.currentDate().year()
        self.assertEqual(f._edit.text(), f"11/05/{year}")
        self.assertEqual(f.date(), QDate(year, 11, 5))
        self.assertEqual(seen[-1], QDate(year, 11, 5))
        f._edit.setText("2-3-24")
        f._edit.editingFinished.emit()
        self.assertEqual(f._edit.text(), "02/03/2024")


class KeepSelectionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "k.db"))
        self.chk = self.db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.sav = self.db.save_account({"name": "Savings", "type": "savings"})["id"]
        self.food = next(c for c in self.db.get_categories() if c["name"] == "Groceries")["id"]
        year = QDate.currentDate().year()
        self.ids = []
        for i in range(60):
            t = self.db.save_transaction({
                "date": f"{year}-01-{(i % 28) + 1:02d}", "account_id": self.chk,
                "payee": f"Shop {i:02d}", "amount": str(-(i + 1))})
            self.ids.append(t["id"])
        from ui.pages.transactions import TransactionsPage
        self.page = TransactionsPage(self.db)
        self.page.resize(1200, 500)
        self.page.refresh()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def selected(self):
        row = self.page._table.currentRow()
        return self.page._txns[row] if 0 <= row < len(self.page._txns) else None

    def row_of(self, txn_id):
        return next(i for i, t in enumerate(self.page._txns) if t["id"] == txn_id)

    def test_edit_keeps_row_selected(self):
        from ui.dialogs import transaction_dialog as TD
        target = self.db.get_transaction(self.ids[3])

        def fake_exec(dlg):
            dlg.memo_edit.setText("edited")
            dlg.cat_combo.select_by_data(self.food)
            return QDialog.DialogCode.Accepted
        with mock.patch.object(TD.TransactionDialog, "exec", fake_exec):
            self.page._edit_txn(target)
        sel = self.selected()
        self.assertEqual(sel["id"], target["id"])
        self.assertEqual(sel["memo"], "edited")
        self.assertNotEqual(self.page._table.currentRow(), len(self.page._txns) - 1)

    def test_recategorize_to_transfer_and_split_keep_row(self):
        target = self.db.get_transaction(self.ids[5])
        self.page._set_category(target, "acct:" + self.sav)
        self.assertEqual(self.selected()["id"], target["id"])
        self.assertEqual(self.selected()["is_transfer"], "1")

        other = self.db.get_transaction(self.ids[7])
        from ui.dialogs import split_dialog as SD
        with mock.patch.object(SD.SplitDialog, "exec", lambda d: QDialog.DialogCode.Accepted), \
                mock.patch.object(SD.SplitDialog, "get_splits", lambda d: [
                    {"category_id": self.food, "amount": "3"},
                    {"category_id": self.food, "amount": "5"}]):
            self.page._split_txn(other)
        sel = self.selected()
        self.assertTrue(sel["split_group_id"])
        self.assertEqual(sel["payee"], other["payee"])

    def test_no_remembered_row_scrolls_to_bottom(self):
        self.page._apply_filter()
        self.assertEqual(self.page._table.currentRow(), -1)
