"""v1.19 Transaction screen (T1–T4) and Accounts links (A1), in personal AND
business books.

Before v1.19: typing a name that isn't an account only warned on Save
("isn't in the list"); a new split line started at $0; "+ Add Transaction"
ignored the account picked in the filter; Date To was today (future-dated
entries were hidden) and could not be blank; account names were underlined.
"""
import os
import unittest
from datetime import date, timedelta
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.book_helper import BothBooksTestCase

try:
    from PyQt6.QtWidgets import QApplication, QDialog
    from PyQt6.QtCore import QDate
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover
    raise unittest.SkipTest(f"PyQt6 not available: {e}")

from ui import widgets as W
from ui.pages.categories import CategoryDialog


def accept_category_dialog(dlg):
    return QDialog.DialogCode.Accepted


class NewAccountNameTests(BothBooksTestCase):
    """T1: a typed name that matches no account → Add it / Discard."""

    def picker(self, db, direction="out"):
        p = W.AccountPicker(db)
        p.money_direction = lambda: direction
        return p

    def test_add_creates_account_with_guessed_type_and_selects_it(self):
        for db in self.each_book():
            for direction, want in (("out", "expense"), ("in", "income")):
                p = self.picker(db, direction)
                name = f"Pet Food {direction}"
                p.setEditText(name)
                self.assertEqual(p.unknown_text(), name)
                with mock.patch.object(W, "ask_new_account", return_value="add"), \
                        mock.patch.object(CategoryDialog, "exec", accept_category_dialog):
                    self.assertTrue(p.confirm_text())
                cat = db.get_category(p.current_data())
                self.assertEqual((cat["name"], cat["type"]), (name, want))
                self.assertEqual(p.unknown_text(), "")

    def test_discard_restores_previous_value(self):
        for db in self.each_book():
            food = self.category(db, "Groceries")
            p = self.picker(db)
            p.select_by_data(food)
            p.setEditText("Nonsense Account")
            with mock.patch.object(W, "ask_new_account", return_value="discard"):
                self.assertFalse(p.confirm_text())
            self.assertEqual(p.current_data(), food)

    def test_enter_or_focus_out_asks(self):
        for db in self.each_book():
            p = self.picker(db)
            p.setEditText("Brand New")
            with mock.patch.object(W, "ask_new_account", return_value="discard") as ask:
                p.lineEdit().editingFinished.emit()
                _app.processEvents()
            ask.assert_called_once()
            self.assertEqual(p.currentText(), "")

    def test_save_asks_in_transaction_dialog(self):
        from ui.dialogs.transaction_dialog import TransactionDialog
        for db in self.each_book():
            chk = self.checking(db)
            dlg = TransactionDialog(db, account_id=chk)
            dlg.amount_spin.setValue(9)
            dlg.cat_combo.setEditText("Hobby Supplies")
            with mock.patch.object(W, "ask_new_account", return_value="add"), \
                    mock.patch.object(CategoryDialog, "exec", accept_category_dialog):
                dlg._accept()
            t = db.save_transaction(dlg.get_data())
            self.assertEqual(db.get_category(t["category_id"])["name"], "Hobby Supplies")
            self.assertEqual(float(t["amount"]), -9.0)


class SplitRemainderTests(BothBooksTestCase):
    """T2: new line = remaining; last line follows the remainder until typed."""

    def test_remainder_rules(self):
        from ui.dialogs.split_lines import SplitLinesEditor
        for db in self.each_book():
            ed = SplitLinesEditor(db)
            ed.set_total(100)
            a = ed.add_line(amount=100, typed=True)
            b = ed.add_line()
            self.assertEqual(b["amount"].value(), 0.0)
            a["amount"].setValue(70)
            self.assertEqual(b["amount"].value(), 30.0)
            c = ed.add_line()                          # nothing left: starts at 0
            self.assertEqual(c["amount"].value(), 0.0)
            b["amount"].setValue(20)                   # b typed; c (last) follows
            self.assertEqual(c["amount"].value(), 10.0)
            c["amount"].setValue(5)                    # c typed: no more following
            a["amount"].setValue(60)
            self.assertEqual(c["amount"].value(), 5.0)
            self.assertEqual(ed.remaining(), 15.0)
            ed.fill_remaining()
            self.assertEqual(c["amount"].value(), 20.0)
            self.assertTrue(ed.is_balanced())


class RegisterDefaultsTests(BothBooksTestCase):
    def page(self, db):
        from ui.pages.transactions import TransactionsPage
        p = TransactionsPage(db)
        p.resize(1200, 600)
        p.refresh()
        return p

    def test_add_defaults_to_filtered_account(self):
        """T3."""
        from ui.dialogs import transaction_dialog as TD
        for db in self.each_book():
            self.checking(db, "First")
            second = self.checking(db, "Second")
            p = self.page(db)
            p.show_account(second)
            seen = {}

            def fake_exec(dlg):
                seen["acct"] = dlg.account_combo.currentData()
                return QDialog.DialogCode.Rejected
            with mock.patch.object(TD.TransactionDialog, "exec", fake_exec):
                p._add()
                p._add_btn.click()
            self.assertEqual(seen["acct"], second)
            p._clear_filter()
            with mock.patch.object(TD.TransactionDialog, "exec", fake_exec):
                p._add()
            self.assertNotEqual(seen.get("acct"), None)

    def test_date_to_blank_shows_future_entries(self):
        """T4."""
        for db in self.each_book():
            chk = self.checking(db)
            future = (date.today() + timedelta(days=40)).isoformat()
            db.save_transaction({"date": future, "account_id": chk, "payee": "Later",
                                 "amount": "-1"})
            db.save_transaction({"date": date.today().isoformat(), "account_id": chk,
                                 "payee": "Now", "amount": "-1"})
            p = self.page(db)
            self.assertTrue(p._date_to.is_empty())
            self.assertEqual(p._date_to._edit.placeholderText(), "MM/DD/YYYY")
            payees = {t["payee"] for t in p._txns}
            self.assertIn("Later", payees)
            p._date_to.setDate(QDate.currentDate())
            p._apply_filter()
            self.assertNotIn("Later", {t["payee"] for t in p._txns})
            p._clear_filter()
            self.assertTrue(p._date_to.is_empty())
            p._date_to.setDate(QDate.currentDate())
            p.show_account(chk)
            self.assertTrue(p._date_to.is_empty())
            self.assertIn("Later", {t["payee"] for t in p._txns})

    def test_datefield_empty_value(self):
        f = W.DateField(allow_empty=True)
        seen = []
        f.dateChanged.connect(seen.append)
        f.clear()
        self.assertFalse(f.date().isValid())
        self.assertEqual(f.iso(), "")
        f._edit.setText("3/4/2026")
        f._edit.editingFinished.emit()
        self.assertEqual(f.iso(), "2026-03-04")
        f._edit.setText("")
        f._edit.textEdited.emit("")
        f._edit.editingFinished.emit()
        self.assertTrue(f.is_empty())
        self.assertFalse(seen[-1].isValid())
        # Fields without allow_empty never go blank.
        g = W.DateField()
        g._edit.setText("")
        g._edit.editingFinished.emit()
        self.assertTrue(g.date().isValid())


class AccountLinkTests(BothBooksTestCase):
    """A1: account names are links without an underline."""

    def test_no_underline_pointer_and_tooltip(self):
        from ui.pages.accounts import AccountsPage, COL_NAME
        from PyQt6.QtCore import Qt
        for db in self.each_book():
            self.checking(db, "Everyday")
            page = AccountsPage(db)
            page.refresh()
            items = []
            tree = page._tree
            for i in range(tree.topLevelItemCount()):
                it = tree.topLevelItem(i)
                if it.text(COL_NAME).strip() == "Everyday":
                    items.append(it)
            self.assertEqual(len(items), 1)
            it = items[0]
            self.assertFalse(it.font(COL_NAME).underline())
            self.assertEqual(it.toolTip(COL_NAME), "Open register")
            self.assertTrue(page.is_link(it, COL_NAME))
            page._on_item_entered(it, COL_NAME)
            self.assertEqual(tree.viewport().cursor().shape(),
                             Qt.CursorShape.PointingHandCursor)
            self.assertNotEqual(it.foreground(COL_NAME).color().name(), "#2f6bff")
            page._on_item_entered(it, 1)
            self.assertEqual(tree.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)


if __name__ == "__main__":
    unittest.main()
