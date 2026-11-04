"""'Paid from' on receipts: remembered on drafts, shown for booked receipts, and
honoured when a booked receipt is saved again (offscreen Qt)."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database
from sheets.receipts_store import paid_from_key

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover — no Qt available
    raise unittest.SkipTest(f"PyQt6 not available: {e}")

from ui.dialogs import receipt_dialog as RD
from tests.test_pack_sizes_ui import amazon

YES = QMessageBox.StandardButton.Yes


class PaidFromTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.capital = self.db.get_account_by_role("owner_capital")
        self.due = self.db.get_account_by_role("due_to_owner")
        self.sup = next(c["id"] for c in self.db.get_categories() if c["name"] == "Supplies")
        self.msgs = []
        for name in ("warning", "information", "critical"):
            p = mock.patch.object(QMessageBox, name, staticmethod(
                lambda *a, n=name, **k: self.msgs.append((n, a[1])) or
                QMessageBox.StandardButton.Ok))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(QMessageBox, "question", staticmethod(
            lambda *a, **k: self.msgs.append(("question", a[1])) or YES))
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def open(self, rid, parsed=None):
        dlg = RD.ReceiptReviewDialog(self.db, self.db.get_receipt(rid), parsed=parsed)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def pick(self, dlg, acct):
        dlg.acct_combo.setCurrentIndex(dlg.acct_combo.findData(acct["id"]))

    def categorise(self, dlg):
        for r in range(dlg.table.rowCount()):
            dlg.table.cellWidget(r, RD.COL_CAT).select_by_data(self.sup)

    def txns(self, rid):
        rc = self.db.get_receipt(rid)
        first = self.db.get_transaction(rc["transaction_id"])
        if first.get("split_group_id"):
            return self.db.get_split_group(first["split_group_id"])
        return [first]

    def test_draft_remembers_paid_from(self):
        rid = self.db.add_manual_receipt()["id"]
        dlg = self.open(rid, amazon())
        self.assertEqual(dlg.acct_combo.currentData(), self.capital["id"])    # first default
        self.pick(dlg, self.due)
        dlg._save_draft()
        self.assertEqual(self.open(rid).acct_combo.currentData(), self.due["id"])
        # A brand-new receipt starts from the account used last.
        other = self.db.add_manual_receipt()["id"]
        self.assertEqual(self.open(other).acct_combo.currentData(), self.due["id"])

    def test_booked_receipt_shows_its_real_account(self):
        rid = self.db.add_manual_receipt()["id"]
        dlg = self.open(rid, amazon())
        self.categorise(dlg)
        self.pick(dlg, self.due)
        dlg._book()
        self.assertTrue(dlg.booked)
        self.assertEqual({t["account_id"] for t in self.txns(rid)}, {self.due["id"]})
        self.db.set_setting("receipt_default_paid_from", self.capital["id"])
        self.db.set_setting(paid_from_key(rid), self.capital["id"])
        again = self.open(rid)                 # shows where it's booked, not a default
        self.assertEqual(again.acct_combo.currentData(), self.due["id"])
        self.assertTrue(again.acct_combo.isEnabled())

    def test_changing_paid_from_on_a_booked_receipt_moves_it(self):
        # The reported bug: booked to Owner's Equity, changed to Due to Owner,
        # saved — and it stayed in Owner's Equity.
        for lines in (1, 2):
            rid = self.db.add_manual_receipt()["id"]
            dlg = self.open(rid, amazon())
            if lines == 2:
                dlg._add_row({"description": "Tape", "qty": 1, "unit_price": 3,
                              "line_total": 3})
                dlg.spins["subtotal"].setValue(8.99)
                dlg.spins["total"].setValue(9.45)
            self.categorise(dlg)
            if lines == 2:
                ship = next(c["id"] for c in self.db.get_categories() if c["name"] == "Postage")
                dlg.table.cellWidget(1, RD.COL_CAT).select_by_data(ship)
            self.pick(dlg, self.capital)
            dlg._book()
            total = round(sum(float(t["amount"]) for t in self.txns(rid)), 2)
            n_before = len(self.db.get_transactions())

            fix = self.open(rid)
            self.assertEqual(fix.acct_combo.currentData(), self.capital["id"])
            self.pick(fix, self.due)
            fix._book()
            self.assertTrue(fix.booked)
            moved = self.txns(rid)
            self.assertEqual({t["account_id"] for t in moved}, {self.due["id"]}, lines)
            self.assertEqual(round(sum(float(t["amount"]) for t in moved), 2), total)
            self.assertTrue(all(t["reimbursable"] == "1" for t in moved))
            self.assertEqual(len(self.db.get_transactions()), n_before)   # replaced, not added
            self.assertEqual(self.open(rid).acct_combo.currentData(), self.due["id"])
        self.assertEqual(round(self.db.account_balance(self.capital["id"]), 2), 0.0)
        self.assertEqual(round(self.db.account_balance(self.due["id"]), 2), -15.90)

    def test_imported_bank_line_keeps_its_account(self):
        bank = self.db.save_account({"name": "Biz Checking", "type": "checking"})
        imported = self.db.save_transaction({"date": "2026-11-02", "account_id": bank["id"],
                                             "payee": "AMAZON", "amount": "-6.45",
                                             "import_hash": "H1"})
        rid = self.db.add_manual_receipt()["id"]
        self.db.attach_receipt(rid, imported)
        dlg = self.open(rid, amazon())
        self.assertFalse(dlg.acct_combo.isEnabled())
        self.assertEqual(dlg.acct_combo.currentData(), bank["id"])
        self.categorise(dlg)
        dlg._book()
        self.assertEqual({t["account_id"] for t in self.txns(rid)}, {bank["id"]})
        booked = self.open(rid)                      # booked against an import: locked
        self.assertFalse(booked.acct_combo.isEnabled())
        # The data layer refuses to move it too.
        items = self.db.get_receipt_items(rid)
        self.db.book_receipt(rid, {"vendor": "Amazon", "tax": "0.46"},
                             [dict(i, business=True) for i in items],
                             account_id=self.due["id"], date="2026-11-02")
        self.assertEqual({t["account_id"] for t in self.txns(rid)}, {bank["id"]})
        self.assertIn("H1", self.db.get_existing_hashes())

    def test_delete_forgets_the_draft_choice(self):
        rid = self.db.add_manual_receipt()["id"]
        self.db.set_setting(paid_from_key(rid), self.due["id"])
        self.db.delete_receipt(rid)
        self.assertEqual(self.db.get_setting(paid_from_key(rid)), "")


if __name__ == "__main__":
    unittest.main()
