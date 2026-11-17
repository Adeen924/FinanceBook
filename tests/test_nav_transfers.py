"""v1.18 N1: Transfers is no longer a sidebar item (Personal) or a Transactions
hub tab (Business). Old links / saved names still land on Transactions, and
"Find Transfers…" on the Transactions page opens the potential / linked
transfers view (the same TransfersPage widgets and matching engine)."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database
from utils import nav_config as NC

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    _app = QApplication.instance() or QApplication([])
except Exception:           # pragma: no cover
    _app = None


class NavConfigTests(unittest.TestCase):
    def test_gone_from_personal_sidebar(self):
        labels = [it.label for it in NC.visible_items("personal")]
        self.assertNotIn("Transfers", labels)
        self.assertEqual(labels, ["Dashboard", "Accounts", "Transactions", "Import",
                                  "Reconcile", "Loans", "Recurring", "Rules", "Reports"])

    def test_gone_from_business_hub(self):
        items = {it.id: it for it in NC.visible_items("business")}
        self.assertEqual(items["Transactions"].pages,
                         ("Transactions", "Import", "Reconcile", "Recurring"))
        self.assertNotIn("Transfers", NC.ALL_PAGES)

    def test_alias_lands_on_transactions(self):
        self.assertEqual(NC.PAGE_ALIASES["Transfers"], "Transactions")
        for book in ("personal", "business"):
            item, page = NC.locate("Transfers", book)
            self.assertEqual(page, "Transactions")
            self.assertEqual(item.id, "Transactions")

    def test_saved_module_setting_drops_transfers(self):
        raw = json.dumps({"version": 1, "enabled": ["receipts", "Transfers"],
                          "known": ["receipts", "Transfers"]})
        self.assertEqual(NC.parse_modules(raw) & {"Transfers"}, frozenset())
        saved = json.loads(NC.dump_modules({"receipts"}, raw))
        self.assertNotIn("Transfers", saved["enabled"])
        self.assertNotIn("Transfers", saved["known"])

    def test_no_page_constructor(self):
        if _app is None:
            self.skipTest("PyQt6 not available")
        from ui.main_window import _page_constructors
        self.assertNotIn("Transfers", _page_constructors())


@unittest.skipIf(_app is None, "PyQt6 not available")
class FindTransfersTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "t.db"))
        self.chk = self.db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.sav = self.db.save_account({"name": "Savings", "type": "savings"})["id"]
        self.out = self.db.save_transaction({"date": "2026-03-01", "account_id": self.chk,
                                             "payee": "TRANSFER TO SAV", "amount": "-250"})
        self.inn = self.db.save_transaction({"date": "2026-03-02", "account_id": self.sav,
                                             "payee": "TRANSFER FROM CHK", "amount": "250"})
        from ui.pages.transactions import TransactionsPage
        self.page = TransactionsPage(self.db)
        self.page.refresh()

    def tearDown(self):
        self.page.deleteLater()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_button_opens_dialog_and_links_a_pair(self):
        from ui.pages.transfers import TransfersPage
        btn = self.page._find_transfers_btn
        self.assertEqual(btn.text(), "Find Transfers…")
        dlg = self.page.make_find_transfers_dialog()
        self.assertIsInstance(dlg.transfers, TransfersPage)
        self.assertEqual(len(dlg.transfers._potential), 1)
        self.assertIn("Potential Transfers (1)", dlg.transfers._tabs.tabText(0))
        dlg.transfers._link(0)
        for t in (self.out, self.inn):
            self.assertEqual(self.db.get_transaction(t["id"])["is_transfer"], "1")
        self.assertIn("Linked Transfers (2)", dlg.transfers._tabs.tabText(1))
        dlg.deleteLater()

    def test_clicking_runs_the_dialog_and_refreshes(self):
        seen = []

        def fake_exec(dlg):
            seen.append(dlg)
            dlg.transfers._link(0)
            return 0
        from ui.pages.transactions import FindTransfersDialog
        with mock.patch.object(FindTransfersDialog, "exec", fake_exec), \
                mock.patch.object(self.page, "_apply_filter") as reload:
            self.page._find_transfers_btn.click()
        self.assertEqual(len(seen), 1)
        reload.assert_called()


class TextTests(unittest.TestCase):
    def test_no_old_location_in_user_text(self):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for rel in ("app/ui/pages/import_page.py", "app/ui/guided_tour.py",
                    "app/ui/pages/settings.py", "app/ui/dialogs/onboarding_dialog.py",
                    "README.md"):
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                text = f.read()
            with self.subTest(rel):
                self.assertNotIn("Transfers → Linked", text)
                self.assertNotIn("Transfers → Potential", text)
                self.assertNotIn("Reconcile, Transfers and Recurring", text)


if __name__ == "__main__":
    unittest.main()
