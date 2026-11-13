"""v1.14 unified Accounts: the Chart of Accounts grouping, the unified Account
picker, and transfers made by picking a balance-sheet account (linking an
already-imported other side instead of duplicating it)."""
import os
import shutil
import tempfile
import unittest

import tests  # noqa: F401
from sheets.client import Database
from utils import chart_of_accounts as COA


class GroupingTests(unittest.TestCase):
    def test_group_order_and_indentation(self):
        accounts = [{"id": "L", "name": "Car Loan", "type": "loan"},
                    {"id": "C", "name": "Checking", "type": "checking"},
                    {"id": "V", "name": "Visa", "type": "credit card"},
                    {"id": "F", "name": "Truck", "type": "fixed assets"},
                    {"id": "O", "name": "Deposit", "type": "other assets"},
                    {"id": "X", "name": "Odd", "type": "something old"}]
        cats = [{"id": "e1", "name": "Auto", "type": "expense", "parent_id": ""},
                {"id": "e2", "name": "Gas", "type": "expense", "parent_id": "e1"},
                {"id": "i1", "name": "Salary", "type": "income", "parent_id": ""},
                {"id": "oi", "name": "Interest Earned", "type": "other_income", "parent_id": ""},
                {"id": "oe", "name": "Penalties", "type": "other_expense", "parent_id": ""},
                {"id": "d1", "name": "Mortgage Principal", "type": "debt_repayment",
                 "parent_id": ""}]
        rows = COA.chart_rows(accounts, cats)
        self.assertEqual([g.label for g, _ in rows],
                         ["Bank", "Credit Card", "Fixed Assets", "Other Assets",
                          "Loan / Liability", "Income", "Other Income", "Expense",
                          "Other Expense", "Debt Repayment"])
        other_assets = dict((g.key, e) for g, e in rows)["other_assets"]
        self.assertEqual({e["data"]["id"] for e in other_assets}, {"O", "X"})
        expense = dict((g.key, e) for g, e in rows)["expense"]
        self.assertEqual([(e["data"]["id"], e["depth"]) for e in expense],
                         [("e1", 0), ("e2", 1)])

    def test_new_account_choices_route_to_tables(self):
        self.assertEqual(COA.resolve_choice("bank:savings"), ("account", "savings"))
        self.assertEqual(COA.resolve_choice("fixed_assets"), ("account", "fixed assets"))
        self.assertEqual(COA.resolve_choice("liability:loan"), ("account", "loan"))
        self.assertEqual(COA.resolve_choice("other_expense"), ("category", "other_expense"))
        self.assertEqual(COA.resolve_choice("asset"), ("category", "asset"))
        for _label, value in COA.NEW_ACCOUNT_CHOICES:
            COA.resolve_choice(value)            # every choice resolves

    def test_picker_lists_categories_then_accounts_without_own(self):
        accounts = [{"id": "C", "name": "Checking", "type": "checking"},
                    {"id": "S", "name": "Savings", "type": "savings"}]
        cats = [{"id": "e1", "name": "Food", "type": "expense", "parent_id": ""},
                {"id": "e2", "name": "Groceries", "type": "expense", "parent_id": "e1"}]
        groups = COA.picker_entries(cats, accounts, exclude_account_id="C")
        self.assertEqual([g for g, _ in groups], ["Expense", "Bank"])
        values = [v for _, opts in groups for _t, v in opts]
        self.assertEqual(values, ["e1", "e2", "acct:S"])
        self.assertIn(("Food → Groceries", "e2"), groups[0][1])
        self.assertEqual(COA.split_value("acct:S"), ("account", "S"))
        self.assertEqual(COA.split_value("e1"), ("category", "e1"))
        self.assertEqual(COA.split_value(""), ("", ""))


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "t.db"))
        self.chk = self.db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.sav = self.db.save_account({"name": "Savings", "type": "savings"})["id"]
        self.food = next(c for c in self.db.get_categories() if c["name"] == "Groceries")["id"]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def txns(self, account):
        return self.db.get_transactions(account_id=account)

    def test_signed_transfer_both_directions(self):
        out = self.db.save_transfer_pair({"date": "2026-03-01", "account_id": self.chk,
                                          "amount": "-200"}, self.sav, keep_sign=True)
        self.assertEqual(float(out["amount"]), -200)
        self.assertEqual(float(self.txns(self.sav)[0]["amount"]), 200)
        back = self.db.save_transfer_pair({"date": "2026-03-02", "account_id": self.chk,
                                           "amount": "50"}, self.sav, keep_sign=True)
        self.assertEqual(float(back["amount"]), 50)
        self.assertEqual(sorted(float(t["amount"]) for t in self.txns(self.sav)), [-50, 200])
        self.assertEqual(self.db.account_balance(self.chk) + self.db.account_balance(self.sav), 0)

    def test_links_already_imported_other_side(self):
        # The savings statement was imported first: +500 on the 3rd.
        imported = self.db.bulk_save_transactions([{
            "date": "2026-04-03", "account_id": self.sav, "payee": "XFER FROM CHK",
            "amount": "500", "import_hash": "h-sav-1"}])[0]
        src = self.db.save_transaction({"date": "2026-04-02", "account_id": self.chk,
                                        "payee": "Online transfer", "amount": "-500",
                                        "import_hash": "h-chk-1"})
        self.db.save_transfer_pair(src, self.sav, keep_sign=True, match_existing=True)
        sav = self.txns(self.sav)
        self.assertEqual(len(sav), 1)                       # linked, not duplicated
        self.assertEqual(sav[0]["id"], imported["id"])
        self.assertEqual(sav[0]["is_transfer"], "1")
        self.assertEqual(sav[0]["transfer_pair_id"], self.db.get_transaction(src["id"])["transfer_pair_id"])
        self.assertEqual(sav[0]["payee"], "XFER FROM CHK")  # the bank's own entry is kept

    def test_import_adopts_mirror_instead_of_duplicating(self):
        self.db.save_transfer_pair({"date": "2026-05-01", "account_id": self.chk,
                                    "amount": "-75"}, self.sav, keep_sign=True)
        self.db.bulk_save_transactions([{"date": "2026-05-02", "account_id": self.sav,
                                         "payee": "XFER", "amount": "75",
                                         "import_hash": "h-sav-2"}])
        sav = self.txns(self.sav)
        self.assertEqual(len(sav), 1)
        self.assertEqual(sav[0]["import_hash"], "h-sav-2")
        self.assertEqual(sav[0]["is_transfer"], "1")

    def test_recategorizing_a_transfer_detaches_other_side(self):
        made = self.db.save_transfer_pair({"date": "2026-06-01", "account_id": self.chk,
                                           "amount": "-20"}, self.sav, keep_sign=True)
        t = self.db.clear_transfer(dict(self.db.get_transaction(made["id"])))
        t["category_id"] = self.food
        self.db.save_transaction(t)
        self.assertEqual(self.txns(self.sav), [])           # FinanceBook's mirror removed
        self.assertEqual(self.db.get_transaction(made["id"])["is_transfer"], "0")
        # A bank entry on the other side is unlinked and kept, never deleted.
        bank = self.db.bulk_save_transactions([{"date": "2026-06-05", "account_id": self.sav,
                                                "amount": "30", "import_hash": "h3"}])[0]
        src = self.db.save_transfer_pair({"date": "2026-06-05", "account_id": self.chk,
                                          "amount": "-30"}, self.sav, keep_sign=True,
                                         match_existing=True)
        self.db.clear_transfer(dict(self.db.get_transaction(src["id"])))
        kept = self.db.get_transaction(bank["id"])
        self.assertEqual((kept["is_transfer"], kept["transfer_pair_id"], kept["category_id"]),
                         ("0", "", ""))

    def test_repointing_transfer_moves_mirror(self):
        visa = self.db.save_account({"name": "Visa", "type": "credit card"})["id"]
        made = self.db.save_transfer_pair({"date": "2026-07-01", "account_id": self.chk,
                                           "amount": "-60"}, self.sav, keep_sign=True)
        self.db.save_transfer_pair(self.db.get_transaction(made["id"]), visa, keep_sign=True)
        self.assertEqual(self.txns(self.sav), [])
        self.assertEqual([float(t["amount"]) for t in self.txns(visa)], [60])

    def test_split_line_to_account(self):
        base = self.db.save_transaction({"date": "2026-08-01", "account_id": self.chk,
                                         "payee": "Costco", "amount": "-100"})
        lines = self.db.split_transaction(base, [
            {"category_id": self.food, "amount": "70"},
            {"category_id": "", "amount": "30", "transfer_to": self.sav}])
        self.assertEqual(sorted(float(t["amount"]) for t in lines), [-70, -30])
        self.assertEqual([float(t["amount"]) for t in self.txns(self.sav)], [30])
        moved = next(t for t in lines if t["is_transfer"] == "1")
        self.assertEqual(self.db.get_transfer_partner(moved)["account_id"], self.sav)
        # Re-splitting replaces the mirror rather than adding a second one.
        self.db.split_transaction(lines[0], [
            {"category_id": self.food, "amount": "60"},
            {"category_id": "", "amount": "40", "transfer_to": self.sav}])
        self.assertEqual([float(t["amount"]) for t in self.txns(self.sav)], [40])
        self.db.delete_split_group(lines[0]["split_group_id"])
        self.assertEqual(self.txns(self.sav), [])
        self.assertEqual(self.txns(self.chk), [])

    def test_partner_accounts_lookup(self):
        made = self.db.save_transfer_pair({"date": "2026-09-01", "account_id": self.chk,
                                           "amount": "-10"}, self.sav, keep_sign=True)
        got = self.db.transfer_partner_accounts(self.db.get_transactions())
        self.assertEqual(got[made["id"]], self.sav)
        self.assertEqual(len(got), 2)


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication, QDialog
    _app = QApplication.instance() or QApplication([])
    _HAVE_QT = True
except Exception:       # pragma: no cover — no Qt available
    _HAVE_QT = False


@unittest.skipUnless(_HAVE_QT, "PyQt6 not available")
class UnifiedPickerUITests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "u.db"))
        self.chk = self.db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.sav = self.db.save_account({"name": "Savings", "type": "savings"})["id"]
        self.food = next(c for c in self.db.get_categories() if c["name"] == "Groceries")["id"]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def dialog(self, txn=None):
        from ui.dialogs.transaction_dialog import TransactionDialog
        dlg = TransactionDialog(self.db, transaction=txn, account_id=self.chk)
        dlg.accept = lambda: QDialog.accept(dlg)
        return dlg

    def test_picker_excludes_own_account_and_follows_it(self):
        dlg = self.dialog()
        values = [dlg.cat_combo.itemData(i) for i in range(dlg.cat_combo.count())]
        self.assertIn("acct:" + self.sav, values)
        self.assertNotIn("acct:" + self.chk, values)
        self.assertIn(self.food, values)
        dlg.account_combo.setCurrentIndex(dlg.account_combo.findData(self.sav))
        values = [dlg.cat_combo.itemData(i) for i in range(dlg.cat_combo.count())]
        self.assertIn("acct:" + self.chk, values)
        self.assertNotIn("acct:" + self.sav, values)

    def test_picking_account_saves_transfer(self):
        dlg = self.dialog()
        dlg.amount_spin.setValue(125)
        dlg.cat_combo.select_by_data("acct:" + self.sav)
        dlg._accept()
        self.assertTrue(dlg.handled)
        chk = self.db.get_transactions(account_id=self.chk)
        sav = self.db.get_transactions(account_id=self.sav)
        self.assertEqual([float(t["amount"]) for t in chk], [-125])
        self.assertEqual([float(t["amount"]) for t in sav], [125])
        self.assertEqual(chk[0]["is_transfer"], "1")

        # Re-opened from the savings side: the picker shows Checking, the amount
        # reads negative (money came in), and saving changes nothing.
        dlg2 = self.dialog(sav[0])
        self.assertEqual(dlg2.cat_combo.current_data(), "acct:" + self.chk)
        self.assertEqual(dlg2.amount_spin.value(), -125)
        dlg2.amount_spin.setValue(-150)
        dlg2._accept()
        self.assertEqual([float(t["amount"]) for t in
                          self.db.get_transactions(account_id=self.chk)], [-150])
        self.assertEqual([float(t["amount"]) for t in
                          self.db.get_transactions(account_id=self.sav)], [150])

        # Changing it to a category makes it an ordinary expense again.
        dlg3 = self.dialog(self.db.get_transactions(account_id=self.chk)[0])
        dlg3.cat_combo.select_by_data(self.food)
        dlg3.amount_spin.setValue(150)
        dlg3._accept()
        self.assertFalse(dlg3.handled)
        self.db.save_transaction(dlg3.get_data())
        chk = self.db.get_transactions(account_id=self.chk)
        self.assertEqual((chk[0]["is_transfer"], chk[0]["category_id"], float(chk[0]["amount"])),
                         ("0", self.food, -150))
        self.assertEqual(self.db.get_transactions(account_id=self.sav), [])

    def test_accounts_page_lists_both_kinds_and_classes(self):
        from ui.pages.accounts import AccountsPage
        page = AccountsPage(self.db)
        page.refresh()
        labels = [page._tree.topLevelItem(i).text(0) for i in range(page._tree.topLevelItemCount())]
        self.assertEqual(labels[0], "BANK")
        self.assertIn("INCOME", labels)
        self.assertIn("EXPENSE", labels)
        self.assertLess(labels.index("BANK"), labels.index("INCOME"))
        self.assertTrue(any(l.strip() == "Checking" for l in labels))
        self.assertTrue(any("↳ Groceries" in l for l in labels))
        self.assertEqual(page._tabs.tabText(2), "Classes")
