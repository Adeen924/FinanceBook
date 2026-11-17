"""v1.18 A1: an account or category that is in use can't be deleted — it can be
made inactive instead. Inactive income / expense accounts keep their name on
existing transactions, in reports and the register (never "Uncategorized"),
are hidden from pickers and the Chart of Accounts, and are listed on the
Inactive Accounts tab with Reactivate. Unused ones are really deleted.
Categories "deleted" by an earlier version (active=0 with transactions) come
back as inactive."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    _app = QApplication.instance() or QApplication([])
except Exception:           # pragma: no cover — no Qt available
    _app = None


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "u.db")
        self.db = Database(self.path)
        self.chk = self.db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.card = self.db.save_account({"name": "Visa", "type": "credit card"})["id"]
        self.food = self.db.save_category({"name": "Food Stuff", "type": "expense"})["id"]
        self.spare = self.db.save_category({"name": "Spare", "type": "expense"})["id"]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def txn(self, cat="", acct=None, amount=-10.0, **kw):
        return self.db.save_transaction(dict(
            {"date": "2026-03-01", "account_id": acct or self.chk, "payee": "Shop",
             "amount": str(amount), "category_id": cat}, **kw))


class UsageCountTests(_Base):
    def test_unused_counts_are_zero(self):
        self.assertEqual(self.db.usage_total(self.db.category_usage(self.spare)), 0)
        acct = self.db.save_account({"name": "Unused", "type": "savings"})["id"]
        self.assertEqual(self.db.usage_total(self.db.account_usage(acct)), 0)

    def test_category_usage_counts_every_reference(self):
        self.txn(self.food)
        self.txn(self.food, amount=-5)
        # a split line
        base = self.txn("", amount=-30)
        self.db.split_transaction(base, [{"category_id": self.food, "amount": "20"},
                                         {"category_id": self.spare, "amount": "10"}])
        self.db.save_rule({"name": "r", "field": "payee", "operator": "contains",
                           "value": "x", "category_id": self.food})
        self.db.save_recurring({"name": "Rent", "template": {"category_id": self.food,
                                                              "account_id": self.chk,
                                                              "amount": 5},
                                "next_date": "2026-04-01"})
        self.db.save_loan({"name": "Car", "original_principal": 1000, "annual_rate": 0.05,
                           "term_months": 12, "start_date": "2026-01-01",
                           "interest_category_id": self.food})
        self.db.save_contact({"name": "Acme", "default_category_id": self.food})
        self.db.set_budget(self.food, 100)
        sub = self.db.save_category({"name": "Snacks", "type": "expense",
                                     "parent_id": self.food})["id"]
        u = self.db.category_usage(self.food)
        self.assertEqual(u["transactions"], 3)
        self.assertEqual(u["rules"], 1)
        self.assertEqual(u["recurring"], 1)
        self.assertEqual(u["loans"], 1)
        self.assertEqual(u["contacts"], 1)
        self.assertEqual(u["budgets"], 1)
        self.assertEqual(u["sub_accounts"], 1)
        self.assertGreaterEqual(self.db.usage_total(u), 9)
        self.assertEqual(self.db.category_usage(sub)["transactions"], 0)
        text = self.db.describe_usage(u)
        self.assertIn("3 transactions", text)
        self.assertIn("1 bank rule", text)

    def test_category_usage_counts_inventory_and_asset_links(self):
        self.db.save_item({"name": "Widget", "income_category_id": self.food})
        self.db.save_asset({"name": "Laptop", "cost": 1000, "in_service_date": "2026-01-01",
                            "expense_category_id": self.spare})
        self.assertEqual(self.db.category_usage(self.food)["inventory"], 1)
        self.assertEqual(self.db.category_usage(self.spare)["assets"], 1)

    def test_account_usage_counts_every_reference(self):
        self.txn(self.food)
        self.db.save_transfer_pair(
            {"date": "2026-03-02", "account_id": self.card, "payee": "Pay card",
             "amount": "50"}, self.chk, keep_sign=True)
        self.db.save_rule({"name": "to card", "field": "payee", "operator": "contains",
                           "value": "card", "transfer_account_id": self.chk})
        self.db.save_rule({"name": "acct cond", "match_mode": "all", "category_id": self.food,
                           "conditions": [{"field": "account", "operator": "is",
                                           "value": self.chk}]})
        self.db.save_recurring({"name": "Rent", "template": {"category_id": self.food,
                                                              "account_id": self.chk},
                                "next_date": "2026-04-01"})
        self.db.save_loan({"name": "Car", "original_principal": 1000, "annual_rate": 0.05,
                           "term_months": 12, "start_date": "2026-01-01",
                           "account_id": self.chk})
        sub = self.db.save_account({"name": "Bills", "type": "checking",
                                    "parent_id": self.chk})["id"]
        u = self.db.account_usage(self.chk)
        self.assertEqual(u["transactions"], 2)       # the expense + the transfer side
        self.assertEqual(u["rules"], 2)
        self.assertEqual(u["recurring"], 1)
        self.assertEqual(u["loans"], 1)
        self.assertEqual(u["sub_accounts"], 1)
        self.assertEqual(self.db.account_usage(sub)["transactions"], 0)


class DeleteTests(_Base):
    def test_unused_category_is_really_deleted(self):
        self.db.delete_category(self.spare)
        self.assertIsNone(self.db.get_category(self.spare))

    def test_used_category_cannot_be_deleted(self):
        self.txn(self.food)
        with self.assertRaises(ValueError) as cm:
            self.db.delete_category(self.food)
        self.assertIn("1 transaction", str(cm.exception))
        self.assertEqual(self.db.get_category(self.food)["active"], "1")

    def test_unused_account_is_really_deleted(self):
        acct = self.db.save_account({"name": "Unused", "type": "savings"})["id"]
        self.db.delete_account(acct)
        self.assertIsNone(self.db.get_account(acct))

    def test_used_account_cannot_be_deleted(self):
        self.txn(self.food)
        with self.assertRaises(ValueError):
            self.db.delete_account(self.chk)
        self.assertEqual(self.db.get_account(self.chk)["active"], "1")

    def test_parent_with_active_subs_is_blocked(self):
        sub = self.db.save_category({"name": "Snacks", "type": "expense",
                                     "parent_id": self.food})["id"]
        with self.assertRaises(ValueError) as cm:
            self.db.set_category_active(self.food, False)
        self.assertIn("Snacks", str(cm.exception))
        with self.assertRaises(ValueError):
            self.db.delete_category(self.food)
        self.db.set_category_active(sub, False)
        self.db.set_category_active(self.food, False)       # now allowed
        self.assertEqual(self.db.get_category(self.food)["active"], "0")

    def test_account_parent_with_active_subs_is_blocked(self):
        sub = self.db.save_account({"name": "Bills", "type": "checking",
                                    "parent_id": self.chk})["id"]
        with self.assertRaises(ValueError) as cm:
            self.db.make_account_inactive(self.chk)
        self.assertIn("Bills", str(cm.exception))
        self.db.make_account_inactive(sub)
        self.db.make_account_inactive(self.chk)
        self.assertEqual(self.db.get_account(self.chk)["active"], "0")

    def test_reactivating_a_sub_reactivates_its_parent(self):
        sub = self.db.save_category({"name": "Snacks", "type": "expense",
                                     "parent_id": self.food})["id"]
        self.db.set_category_active(sub, False)
        self.db.set_category_active(self.food, False)
        self.db.set_category_active(sub, True)
        self.assertEqual(self.db.get_category(self.food)["active"], "1")


class InactiveCategoryTests(_Base):
    def setUp(self):
        super().setUp()
        self.t = self.txn(self.food)
        self.db.set_category_active(self.food, False)

    def test_hidden_from_pickers_but_resolvable(self):
        self.assertNotIn(self.food, {c["id"] for c in self.db.get_categories()})
        self.assertNotIn(self.food, {c["id"] for c in self.db.get_categories_for_book()})
        allc = {c["id"]: c for c in self.db.get_categories(include_inactive=True)}
        self.assertEqual(allc[self.food]["name"], "Food Stuff")

    def test_kept_in_its_own_transactions_picker(self):
        kept = {c["id"]: c for c in self.db.get_categories_for_book(keep_ids=[self.food])}
        self.assertIn(self.food, kept)
        self.assertIn("inactive", kept[self.food]["name"].lower())

    def test_not_uncategorized(self):
        self.assertEqual(self.db.uncategorized_count(), 0)
        res = self.db.apply_rules_to_all()
        self.assertEqual(int(res), 0)
        self.assertEqual(self.db.get_transaction(self.t["id"])["category_id"], self.food)

    def test_reports_show_its_name(self):
        rows = self.db.report_rows("2026-01-01", "2026-12-31")
        self.assertEqual([r["group"] for r in rows], ["Food Stuff"])

    def test_inactive_list(self):
        self.assertEqual([c["id"] for c in self.db.get_inactive_categories()], [self.food])


class EarlierDeletedRepairTests(unittest.TestCase):
    """A category "deleted" by v1.17 or older was only set to active=0."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "old.db")
        db = Database(self.path)
        chk = db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.used = db.save_category({"name": "Old Used", "type": "expense"})["id"]
        self.unused = db.save_category({"name": "Old Unused", "type": "expense"})["id"]
        self.tid = db.save_transaction({"date": "2026-02-01", "account_id": chk,
                                        "payee": "X", "amount": "-7",
                                        "category_id": self.used})["id"]
        with sqlite3.connect(self.path) as c:      # what v1.17's delete_category did
            c.execute("UPDATE categories SET active=0 WHERE id IN (?,?)",
                      (self.used, self.unused))
            c.execute("DELETE FROM settings WHERE key='inactive_categories_repaired'")
        c.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_repair_on_open(self):
        db = Database(self.path)
        inactive = {c["id"]: c for c in db.get_inactive_categories()}
        self.assertIn(self.used, inactive)
        self.assertEqual(inactive[self.used]["name"], "Old Used")
        self.assertNotIn(self.unused, inactive)          # nothing used it: really deleted
        self.assertIsNone(db.get_category(self.unused))
        t = db.get_transaction(self.tid)
        self.assertEqual(t["category_id"], self.used)    # no transaction changes
        self.assertEqual(t["amount"], "-7.0")
        self.assertEqual(db.uncategorized_count(), 0)
        # idempotent
        db2 = Database(self.path)
        self.assertEqual([c["id"] for c in db2.get_inactive_categories()], [self.used])


@unittest.skipIf(_app is None, "PyQt6 not available")
class AccountsPageDeleteTests(_Base):
    def setUp(self):
        super().setUp()
        self.asked = []
        from ui.pages.accounts import AccountsPage
        self.page = AccountsPage(self.db)
        self.page.refresh()

    def tearDown(self):
        self.page.deleteLater()
        super().tearDown()

    def entry(self, kind, ident):
        for i in range(self.page._tree.topLevelItemCount()):
            e = self.page._entry(self.page._tree.topLevelItem(i))
            if e and e["kind"] == kind and e["data"]["id"] == ident:
                return e
        raise AssertionError(f"{kind} {ident} not in the chart")

    def answer(self, choice):
        def ask(title, text, buttons):
            self.asked.append((title, text, buttons))
            return choice
        return mock.patch.object(self.page, "_ask", side_effect=ask)

    def test_used_expense_offers_make_inactive(self):
        self.txn(self.food)
        self.txn(self.food, amount=-3)
        with self.answer("Make Inactive"):
            self.page._delete_entry(self.entry("category", self.food))
        title, text, buttons = self.asked[-1]
        self.assertIn("2 transactions", text)
        self.assertEqual(buttons, ["Make Inactive", "Cancel"])
        self.assertEqual(self.db.get_category(self.food)["active"], "0")
        self.assertIn("Inactive Accounts (1)", self.page._tabs.tabText(1))

    def test_used_bank_cancel_changes_nothing(self):
        self.txn(self.food)
        with self.answer("Cancel"):
            self.page._delete_entry(self.entry("account", self.chk))
        self.assertIn("1 transaction", self.asked[-1][1])
        self.assertEqual(self.db.get_account(self.chk)["active"], "1")

    def test_unused_confirms_then_deletes(self):
        with self.answer("Delete"):
            self.page._delete_entry(self.entry("category", self.spare))
        self.assertEqual(self.asked[-1][2], ["Delete", "Cancel"])
        self.assertIsNone(self.db.get_category(self.spare))

    def test_parent_with_subs_is_blocked_with_names(self):
        self.db.save_category({"name": "Snacks", "type": "expense", "parent_id": self.food})
        self.page.refresh()
        with self.answer("OK"):
            self.page._delete_entry(self.entry("category", self.food))
        self.assertIn("Snacks", self.asked[-1][1])
        self.assertEqual(self.asked[-1][2], ["OK"])
        self.assertEqual(self.db.get_category(self.food)["active"], "1")

    def test_inactive_tab_lists_category_and_reactivates(self):
        self.txn(self.food)
        self.db.set_category_active(self.food, False)
        self.page.refresh()
        rows = self.page.inactive_entries()
        self.assertIn(("category", self.food), [(e["kind"], e["data"]["id"]) for e in rows])
        e = next(e for e in rows if e["data"]["id"] == self.food)
        self.page._reactivate_entry(e)
        self.assertEqual(self.db.get_category(self.food)["active"], "1")
        self.assertEqual(self.page.inactive_entries(), [])

    def test_inactive_category_hidden_from_chart(self):
        self.txn(self.food)
        self.db.set_category_active(self.food, False)
        self.page.refresh()
        with self.assertRaises(AssertionError):
            self.entry("category", self.food)


@unittest.skipIf(_app is None, "PyQt6 not available")
class RegisterShowsInactiveNameTests(_Base):
    def test_register_account_column(self):
        self.txn(self.food)
        self.db.set_category_active(self.food, False)
        from ui.pages.transactions import TransactionsPage
        from PyQt6.QtCore import QDate
        page = TransactionsPage(self.db)
        page.refresh()
        page._date_from.setDate(QDate(2026, 1, 1))
        page._date_to.setDate(QDate(2026, 12, 31))
        page._apply_filter()
        texts = [page._table.item(r, 3).text() for r in range(page._table.rowCount())
                 if page._table.item(r, 3)]
        self.assertTrue(any("Food Stuff" in t for t in texts), texts)
        self.assertFalse(any("Uncategorized" in t for t in texts), texts)
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
