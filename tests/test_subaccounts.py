"""v1.15 Chart of Accounts: move any account to a sub-account and back.

Categories (income / expense accounts) and balance-sheet accounts (bank, card,
loan, assets, equity — accounts.parent_id, schema 13) can be nested one level.
Moving an account that has its own sub-accounts brings them along under the new
parent; a sub-account takes its parent's type; nothing is ever dropped from
the list, and there are no cycles."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database, SCHEMA_VERSION
from sheets import backup
from utils import chart_of_accounts as COA


def _flat(rows):
    """[(group key, kind, name, depth), …] for chart_rows output."""
    return [(g.key, e["kind"], e["data"]["name"], e["depth"])
            for g, entries in rows for e in entries]


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "c.db"))
        self.cats = {c["name"]: c for c in self.db.get_categories()}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def cat(self, name):
        return next(c for c in self.db.get_categories() if c["name"] == name)


class ChartRowsTests(unittest.TestCase):
    def test_never_drops_a_category(self):
        cats = [{"id": "A", "name": "Auto", "type": "expense", "parent_id": ""},
                {"id": "B", "name": "Gas", "type": "expense", "parent_id": "A"},
                {"id": "C", "name": "Premium", "type": "expense", "parent_id": "B"},   # grandchild
                {"id": "X", "name": "Loop1", "type": "expense", "parent_id": "Y"},     # cycle
                {"id": "Y", "name": "Loop2", "type": "expense", "parent_id": "X"},
                {"id": "Z", "name": "Self", "type": "income", "parent_id": "Z"},       # own parent
                {"id": "O", "name": "Orphan", "type": "expense", "parent_id": "GONE"}]
        flat = _flat(COA.chart_rows([], cats))
        self.assertEqual(sorted(n for _g, _k, n, _d in flat),
                         sorted(c["name"] for c in cats))
        self.assertIn(("expense", "category", "Premium", 2), flat)
        self.assertIn(("expense", "category", "Orphan", 0), flat)
        self.assertIn(("income", "category", "Self", 0), flat)

    def test_sub_accounts_indent_under_parent_in_same_group(self):
        accts = [{"id": "C1", "name": "Checking", "type": "checking", "parent_id": ""},
                 {"id": "C2", "name": "Bills", "type": "checking", "parent_id": "C1"},
                 {"id": "S1", "name": "Savings", "type": "savings", "parent_id": "C1"},
                 {"id": "V1", "name": "Visa", "type": "credit card", "parent_id": "C1"},  # other group
                 {"id": "L1", "name": "Car", "type": "loan", "parent_id": "GONE"}]
        flat = _flat(COA.chart_rows(accts, []))
        self.assertEqual(flat[:3], [("bank", "account", "Checking", 0),
                                    ("bank", "account", "Bills", 1),
                                    ("bank", "account", "Savings", 1)])
        self.assertIn(("credit_card", "account", "Visa", 0), flat)
        self.assertIn(("liability", "account", "Car", 0), flat)
        rows = COA.chart_rows(accts, [])
        bills = next(e for _g, es in rows for e in es if e["data"]["id"] == "C2")
        self.assertEqual(bills["parent"], "C1")

    def test_picker_shows_path_for_sub_accounts(self):
        accts = [{"id": "C1", "name": "Checking", "type": "checking", "parent_id": ""},
                 {"id": "C2", "name": "Bills", "type": "checking", "parent_id": "C1"}]
        entries = dict(COA.picker_entries([], accts))
        self.assertIn(("Checking → Bills · Bank", "acct:C2"), entries["Bank"])
        self.assertIn(("Checking · Bank", "acct:C1"), entries["Bank"])


class CategoryMoveTests(_Base):
    def test_category_to_sub_and_back(self):
        gas = self.cat("Gas")
        food = self.cat("Food")
        self.db.move_category(gas["id"], food["id"])
        self.assertEqual(self.cat("Gas")["parent_id"], food["id"])
        self.db.move_category(gas["id"], "")
        self.assertEqual(self.cat("Gas")["parent_id"], "")
        flat = _flat(COA.chart_rows([], self.db.get_categories()))
        self.assertIn(("expense", "category", "Gas", 0), flat)

    def test_moving_a_parent_brings_its_children(self):
        transport = self.cat("Transportation")
        kids = {c["id"] for c in self.db.get_categories()
                if c.get("parent_id") == transport["id"]}
        self.assertTrue(kids)
        food = self.cat("Food")
        info = self.db.move_category(transport["id"], food["id"])
        self.assertEqual(info["moved_children"], len(kids))
        cats = {c["id"]: c for c in self.db.get_categories()}
        self.assertEqual(cats[transport["id"]]["parent_id"], food["id"])
        self.assertTrue(all(cats[k]["parent_id"] == food["id"] for k in kids))
        # Nothing vanished from the list.
        flat = _flat(COA.chart_rows([], self.db.get_categories()))
        self.assertEqual(len(flat), len(self.db.get_categories()))

    def test_editing_parent_through_save_category_also_brings_children(self):
        # The pre-1.15 bug: editing "Transportation" to sit under "Food" hid its subs.
        transport = self.cat("Transportation")
        food = self.cat("Food")
        self.db.save_category(dict(transport, parent_id=food["id"]))
        flat = _flat(COA.chart_rows([], self.db.get_categories()))
        self.assertEqual(len(flat), len(self.db.get_categories()))
        self.assertTrue(all(d <= 1 for *_x, d in flat))

    def test_type_follows_new_parent(self):
        gas = self.cat("Gas")
        employment = self.cat("Employment")
        info = self.db.move_category(gas["id"], employment["id"])
        self.assertTrue(info["type_changed"])
        self.assertEqual(self.cat("Gas")["type"], "income")
        # A parent's type change carries its sub-accounts with it.
        food = self.cat("Food")
        self.db.save_category(dict(food, type="other_expense"))
        self.assertTrue(all(c["type"] == "other_expense" for c in self.db.get_categories()
                            if c.get("parent_id") == food["id"]))

    def test_no_cycles(self):
        transport = self.cat("Transportation")
        gas = self.cat("Gas")
        with self.assertRaises(ValueError):
            self.db.move_category(transport["id"], gas["id"])       # under its own child
        with self.assertRaises(ValueError):
            self.db.move_category(gas["id"], gas["id"])
        food = self.cat("Food")
        groceries = self.cat("Groceries")
        with self.assertRaises(ValueError):
            self.db.move_category(gas["id"], groceries["id"])       # parents are top-level
        self.assertEqual(self.cat("Gas")["parent_id"], transport["id"])
        self.assertEqual(self.cat("Food")["parent_id"], "")
        self.assertTrue(food)


class BusinessTaxLineTests(unittest.TestCase):
    def test_tax_line_follows_parent_when_type_changes(self):
        d = tempfile.mkdtemp()
        try:
            db = Database(os.path.join(d, "b.db"), book_type="business")
            cats = db.get_categories()
            exp = next(c for c in cats if c["type"] == "expense" and c.get("tax_line")
                       and not c.get("parent_id"))
            inc = next(c for c in cats if c["type"] == "income" and not c.get("parent_id"))
            db.move_category(exp["id"], inc["id"])
            moved = db.get_category(exp["id"])
            self.assertEqual(moved["type"], "income")
            self.assertEqual(moved.get("tax_line") or "", inc.get("tax_line") or "")
            self.assertEqual(float(moved["deduct_pct"]), float(inc.get("deduct_pct") or 100))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class AccountMoveTests(_Base):
    def setUp(self):
        super().setUp()
        db = self.db
        self.chk = db.save_account({"name": "Checking", "type": "checking",
                                    "opening_balance": "100"})["id"]
        self.bills = db.save_account({"name": "Bills", "type": "checking",
                                      "opening_balance": "50"})["id"]
        self.sav = db.save_account({"name": "Savings", "type": "savings"})["id"]
        self.visa = db.save_account({"name": "Visa", "type": "credit card"})["id"]

    def test_account_to_sub_and_back(self):
        self.db.move_account(self.bills, self.chk)
        self.assertEqual(self.db.get_account(self.bills)["parent_id"], self.chk)
        flat = _flat(COA.chart_rows(self.db.get_accounts(), []))
        self.assertIn(("bank", "account", "Bills", 1), flat)
        self.db.move_account(self.bills, "")
        self.assertEqual(self.db.get_account(self.bills)["parent_id"], "")

    def test_save_account_keeps_and_validates_parent(self):
        self.db.save_account(dict(self.db.get_account(self.sav), parent_id=self.chk))
        self.assertEqual(self.db.get_account(self.sav)["parent_id"], self.chk)
        # Saving without the key leaves it alone (older callers).
        a = self.db.get_account(self.sav)
        a.pop("parent_id")
        self.db.save_account(dict(a, name="Savings 2"))
        self.assertEqual(self.db.get_account(self.sav)["parent_id"], self.chk)
        with self.assertRaises(ValueError):
            self.db.move_account(self.visa, self.chk)               # other group
        with self.assertRaises(ValueError):
            self.db.move_account(self.chk, self.sav)                # under its own child
        with self.assertRaises(ValueError):
            self.db.move_account(self.chk, self.chk)

    def test_moving_a_parent_brings_its_children(self):
        self.db.move_account(self.bills, self.chk)
        other = self.db.save_account({"name": "Joint", "type": "checking"})["id"]
        n = self.db.move_account(self.chk, other)
        self.assertEqual(n, 1)
        self.assertEqual(self.db.get_account(self.bills)["parent_id"], other)
        flat = _flat(COA.chart_rows(self.db.get_accounts(), []))
        self.assertEqual(len(flat), 5)

    def test_type_change_to_another_group_detaches(self):
        self.db.move_account(self.bills, self.chk)
        self.db.save_account(dict(self.db.get_account(self.bills), type="credit card"))
        self.assertEqual(self.db.get_account(self.bills)["parent_id"], "")

    def test_registers_transfers_and_balance_sheet_still_work(self):
        self.db.move_account(self.bills, self.chk)
        self.db.save_transfer_pair({"date": "2026-02-01", "account_id": self.chk,
                                    "payee": "Move", "amount": "30"}, self.bills)
        self.assertAlmostEqual(self.db.account_balance(self.chk), 70)
        self.assertAlmostEqual(self.db.account_balance(self.bills), 80)
        self.assertAlmostEqual(self.db.rolled_up_balance(self.chk), 150)
        self.assertEqual(len(self.db.get_transactions(account_id=self.bills)), 1)
        from utils.ledger import balance_sheet
        cats = {c["id"]: c for c in self.db.get_categories()}
        bs = balance_sheet(self.db.get_accounts(active=None), self.db.get_transactions(),
                           cats, "2026-12-31")
        names = [line[0] for line in bs["assets"]]
        self.assertIn("Checking", names)
        self.assertIn("Bills", names)            # each account is still its own line


try:
    from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox
    _app = QApplication.instance() or QApplication([])
    _HAVE_QT = True
except Exception:       # pragma: no cover
    _HAVE_QT = False


@unittest.skipUnless(_HAVE_QT, "PyQt6 not available")
class AccountsPageUITests(AccountMoveTests):
    def test_tree_rolls_up_and_moves(self):
        from ui.pages.accounts import AccountsPage, COL_NAME, COL_BALANCE
        self.db.move_account(self.bills, self.chk)
        page = AccountsPage(self.db)
        page.refresh()
        items = {page._tree.topLevelItem(i).text(COL_NAME).strip().lstrip("↳ ").strip():
                 page._tree.topLevelItem(i) for i in range(page._tree.topLevelItemCount())}
        self.assertEqual(items["Checking"].text(COL_BALANCE), "$150.00")     # 100 + 50
        self.assertEqual(items["Bills"].text(COL_BALANCE), "$50.00")
        bank_head = page._tree.topLevelItem(0)
        self.assertEqual(bank_head.text(COL_BALANCE), "$150.00")            # not double-counted

        # "Move to…" offers top level and same-group parents only.
        entry = page._entry(items["Bills"])
        opts = page.move_options(entry)
        self.assertEqual(opts[0], ("Top level", ""))
        values = [v for _t, v in opts]
        self.assertIn(self.sav, values)
        self.assertNotIn(self.visa, values)
        self.assertNotIn(self.bills, values)
        page.move_entry(entry, "")
        self.assertEqual(self.db.get_account(self.bills)["parent_id"], "")

    def test_move_category_with_children_asks_first(self):
        from ui.pages.accounts import AccountsPage
        page = AccountsPage(self.db)
        page.refresh()
        transport = self.cat("Transportation")
        food = self.cat("Food")
        entry = {"kind": "category", "data": transport, "depth": 0}
        values = [v for _t, v in page.move_options(entry)]
        self.assertIn(food["id"], values)
        self.assertNotIn(self.cat("Gas")["id"], values)                    # its own child
        with mock.patch.object(QMessageBox, "question",
                               return_value=QMessageBox.StandardButton.No) as q:
            page.move_entry(entry, food["id"])
        self.assertIn("sub-account", q.call_args[0][2])
        self.assertEqual(self.cat("Transportation")["parent_id"], "")
        with mock.patch.object(QMessageBox, "question",
                               return_value=QMessageBox.StandardButton.Yes):
            page.move_entry(entry, food["id"])
        self.assertEqual(self.cat("Transportation")["parent_id"], food["id"])

    def test_account_dialog_parent_picker(self):
        from ui.dialogs.account_dialog import AccountDialog
        accts = self.db.get_accounts()
        dlg = AccountDialog(account=self.db.get_account(self.bills), accounts=accts)
        values = [dlg.parent_combo.itemData(i) for i in range(dlg.parent_combo.count())]
        self.assertEqual(values[0], "")
        self.assertIn(self.chk, values)
        self.assertNotIn(self.visa, values)
        self.assertNotIn(self.bills, values)
        dlg.parent_combo.setCurrentIndex(dlg.parent_combo.findData(self.chk))
        self.assertEqual(dlg.get_data()["parent_id"], self.chk)
        dlg.type_combo.setCurrentIndex(dlg.type_combo.findData("credit card"))
        values = [dlg.parent_combo.itemData(i) for i in range(dlg.parent_combo.count())]
        self.assertIn(self.visa, values)
        self.assertNotIn(self.chk, values)
        self.assertEqual(dlg.get_data()["parent_id"], "")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "v12.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_v12(self):
        db = Database(self.path)
        db.save_account({"name": "Checking", "type": "checking", "opening_balance": "10"})
        db.close()
        c = sqlite3.connect(self.path)
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' "
                                 "AND tbl_name='accounts'").fetchall():
            c.execute(f"DROP TRIGGER {name}")
        c.execute("ALTER TABLE accounts DROP COLUMN parent_id")
        c.execute("PRAGMA user_version = 12")
        c.commit()
        c.close()

    def test_migrates_from_12(self):
        self.make_v12()
        db = Database(self.path)
        self.assertEqual(SCHEMA_VERSION, 13)
        self.assertEqual(backup.read_user_version(self.path), 13)
        self.assertTrue(db.last_migration_backup)
        a = db.get_accounts()[0]
        self.assertEqual((a["name"], a["parent_id"]), ("Checking", ""))
        self.assertAlmostEqual(db.account_balance(a["id"]), 10)
        sub = db.save_account({"name": "Sub", "type": "checking"})
        db.move_account(sub["id"], a["id"])
        self.assertEqual(db.get_account(sub["id"])["parent_id"], a["id"])

    def test_read_only_and_newer_guards(self):
        from sheets.client import ReadOnlyUpgradeError, NewerSchemaError
        self.make_v12()
        with self.assertRaises(ReadOnlyUpgradeError):
            Database(self.path, read_only=True)
        Database(self.path).close()
        c = sqlite3.connect(self.path)
        c.execute("PRAGMA user_version = 14")
        c.commit()
        c.close()
        with self.assertRaises(NewerSchemaError):
            Database(self.path)


if __name__ == "__main__":
    unittest.main()
