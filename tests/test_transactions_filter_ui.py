"""v1.15 Transactions screen: every filter control re-filters by itself (no
"Filter" button), the search box is debounced, refresh() repopulating the combos
doesn't re-filter on each change, and "⚠ Uncategorized only" includes entries
whose account was deleted."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database

try:
    from PyQt6.QtWidgets import QApplication, QPushButton
    from PyQt6.QtCore import QDate
    from PyQt6.QtTest import QTest
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover — no Qt available
    raise unittest.SkipTest(f"PyQt6 not available: {e}")


class TransactionsFilterTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "f.db"))
        db = self.db
        self.chk = db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.card = db.save_account({"name": "Visa", "type": "credit card"})["id"]
        self.sav = db.save_account({"name": "Savings", "type": "savings"})["id"]
        cats = {c["name"]: c["id"] for c in db.get_categories()}
        self.gas, self.food = cats["Gas"], cats["Groceries"]
        gone = db.save_category({"name": "Old", "type": "expense"})["id"]

        def add(acct, date, payee, amount, cat=""):
            db.save_transaction({"date": date, "account_id": acct, "payee": payee,
                                 "amount": amount, "category_id": cat})
        add(self.chk, "2026-01-10", "SHELL", "-40", self.gas)
        add(self.chk, "2026-02-10", "KROGER", "-80", self.food)
        add(self.chk, "2026-03-10", "MYSTERY", "-5")                  # uncategorized
        add(self.card, "2026-03-11", "SHELL", "-30", self.gas)
        add(self.card, "2026-03-12", "OLD THING", "-9", gone)          # orphaned below
        db.delete_category(gone)
        db.save_transfer_pair({"date": "2026-03-15", "account_id": self.chk,
                               "payee": "TO SAVINGS", "amount": "100"}, self.sav)

        from ui.pages.transactions import TransactionsPage
        self.page = TransactionsPage(db)
        self.page.refresh()
        self.page._date_from.setDate(QDate(2026, 1, 1))
        self.page._date_to.setDate(QDate(2026, 12, 31))
        QTest.qWait(400)

    def tearDown(self):
        self.page.deleteLater()
        shutil.rmtree(self.dir, ignore_errors=True)

    def rows(self):
        return self.page._table.rowCount()

    def pick(self, combo, data):
        combo.setCurrentIndex(combo.findData(data))

    def test_every_control_filters_without_a_button(self):
        p = self.page
        self.assertEqual(self.rows(), 7)                  # 5 + both transfer sides
        self.pick(p._acct_filter, self.card)
        self.assertEqual(self.rows(), 2)
        self.pick(p._acct_filter, "")
        self.pick(p._cat_filter, self.gas)
        self.assertEqual(self.rows(), 2)
        self.pick(p._cat_filter, "")
        self.assertEqual(self.rows(), 7)

        p._show_transfers.setChecked(False)
        self.assertEqual(self.rows(), 5)
        p._show_transfers.setChecked(True)

        p._date_from.setDate(QDate(2026, 3, 1))
        QTest.qWait(400)
        self.assertEqual(self.rows(), 5)
        p._date_to.setDate(QDate(2026, 3, 11))
        QTest.qWait(400)
        self.assertEqual(self.rows(), 2)
        p._date_from.setDate(QDate(2026, 1, 1))
        p._date_to.setDate(QDate(2026, 12, 31))
        QTest.qWait(400)

        p._search_edit.setText("shel")
        self.assertEqual(self.rows(), 7)                  # debounced: not yet
        QTest.qWait(400)
        self.assertEqual(self.rows(), 2)
        p._search_edit.clear()
        QTest.qWait(400)
        self.assertEqual(self.rows(), 7)

    def test_uncategorized_only_includes_orphans(self):
        p = self.page
        self.pick(p._cat_filter, p.UNCATEGORIZED)
        payees = sorted(p._txns[i]["payee"] for i in range(self.rows()))
        self.assertEqual(payees, ["MYSTERY", "OLD THING"])
        p.show_uncategorized()
        self.assertEqual(self.rows(), 2)

    def test_no_filter_button_and_clear_resets(self):
        p = self.page
        texts = [b.text() for b in p.findChildren(QPushButton)]
        self.assertNotIn("Filter", texts)
        self.pick(p._acct_filter, self.card)
        p._search_edit.setText("shell")
        QTest.qWait(400)
        self.assertEqual(self.rows(), 1)
        with mock.patch.object(self.db, "get_transactions",
                               wraps=self.db.get_transactions) as gt:
            p._clear_filter()
            self.assertEqual(gt.call_count, 1)            # one reload, not one per control
        p._date_from.setDate(QDate(2026, 1, 1))
        QTest.qWait(400)
        self.assertEqual(self.rows(), 7)

    def test_refresh_reloads_once(self):
        self.pick(self.page._cat_filter, self.gas)
        with mock.patch.object(self.db, "get_transactions",
                               wraps=self.db.get_transactions) as gt:
            self.page.refresh()
            QTest.qWait(400)
            self.assertEqual(gt.call_count, 1)
        self.assertEqual(self.page._cat_filter.currentData(), self.gas)
        self.assertEqual(self.rows(), 2)

    def test_edit_keeps_the_row(self):
        p = self.page
        kroger = next(t for t in p._txns if t["payee"] == "KROGER")
        p._apply_filter(keep=kroger)
        self.assertEqual(p._txns[p._table.currentRow()]["id"], kroger["id"])


if __name__ == "__main__":
    unittest.main()
