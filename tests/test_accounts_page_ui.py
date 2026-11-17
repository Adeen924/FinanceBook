"""v1.18 Accounts page: after an edit (add, move, make inactive, reactivate)
the Chart of Accounts stays on the row instead of jumping to the top (A2), and
a search box filters it as you type (A3)."""
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
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeySequence
    from PyQt6.QtTest import QTest
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover — no Qt available
    raise unittest.SkipTest(f"PyQt6 not available: {e}")


class _FakeCategoryDialog:
    """Stands in for CategoryDialog: accepts at once, renaming the account."""
    rename_to = ""

    def __init__(self, db, edit_cat=None, **kw):
        self.edit_cat = edit_cat

    def exec(self):
        return QDialog.DialogCode.Accepted

    def get_data(self):
        return dict(self.edit_cat, name=self.rename_to or self.edit_cat["name"])


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "a.db"))
        self.db.save_account({"name": "Checking", "type": "checking",
                              "institution": "First Bank"})
        from ui.pages.accounts import AccountsPage
        self.page = AccountsPage(self.db)
        self.page.resize(1200, 600)
        self.page.show()
        self.page.refresh()
        QTest.qWait(30)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        shutil.rmtree(self.dir, ignore_errors=True)

    def item(self, kind, ident):
        return self.page._items.get((kind, ident))

    def cat_id(self, name):
        return next(c["id"] for c in self.db.get_categories(include_inactive=True)
                    if c["name"] == name)


class StayOnRowTests(_Base):
    def setUp(self):
        super().setUp()
        # A long list: the personal defaults plus 60 more.
        for i in range(60):
            self.db.save_category({"name": f"Zeta {i:02d}", "type": "expense"})
        self.page.refresh()
        QTest.qWait(30)
        self.target = self.cat_id("Zeta 55")

    def visible_in_viewport(self, item):
        rect = self.page._tree.visualItemRect(item)
        return self.page._tree.viewport().rect().intersects(rect)

    def test_edit_far_down_stays_on_the_row(self):
        tree = self.page._tree
        self.assertGreater(tree.verticalScrollBar().maximum(), 0)
        e = self.page._entry(self.item("category", self.target))
        _FakeCategoryDialog.rename_to = "Zeta 55 renamed"
        with mock.patch("ui.pages.categories.CategoryDialog", _FakeCategoryDialog):
            self.page._edit_entry(e)
        QTest.qWait(30)
        it = self.item("category", self.target)
        self.assertIn("Zeta 55 renamed", it.text(0))
        self.assertIs(tree.currentItem(), it)
        self.assertGreater(tree.verticalScrollBar().value(), 0)
        self.assertTrue(self.visible_in_viewport(it))

    def test_gone_row_keeps_the_scroll_position(self):
        tree = self.page._tree
        tree.scrollToItem(self.item("category", self.target))
        QTest.qWait(10)
        before = tree.verticalScrollBar().value()
        self.assertGreater(before, 0)
        e = self.page._entry(self.item("category", self.target))
        self.page._make_inactive(e)
        QTest.qWait(30)
        self.assertIsNone(self.item("category", self.target))
        self.assertEqual(tree.verticalScrollBar().value(), before)

    def test_plain_refresh_keeps_selection(self):
        tree = self.page._tree
        it = self.item("category", self.target)
        tree.setCurrentItem(it)
        self.page.refresh()
        QTest.qWait(30)
        self.assertEqual(self.page._entry(tree.currentItem())["data"]["id"], self.target)
        self.assertTrue(self.visible_in_viewport(tree.currentItem()))

    def test_move_stays_on_the_row(self):
        parent = self.cat_id("Zeta 10")
        e = self.page._entry(self.item("category", self.target))
        self.page.move_entry(e, parent)
        QTest.qWait(30)
        cur = self.page._tree.currentItem()
        self.assertEqual(self.page._entry(cur)["data"]["id"], self.target)
        self.assertTrue(self.visible_in_viewport(cur))

    def test_reactivate_lands_on_the_row(self):
        self.db.set_category_active(self.target, False)
        self.page.refresh()
        e = next(x for x in self.page.inactive_entries() if x["data"]["id"] == self.target)
        self.page._reactivate_entry(e)
        QTest.qWait(30)
        cur = self.page._tree.currentItem()
        self.assertEqual(self.page._entry(cur)["data"]["id"], self.target)

    def test_inactive_tab_keeps_its_place(self):
        for i in range(40):
            cid = self.cat_id(f"Zeta {i:02d}")
            self.db.set_category_active(cid, False)
        self.page._tabs.setCurrentIndex(1)
        self.page.refresh()
        QTest.qWait(30)
        t = self.page._inactive_table
        rows = self.page.inactive_entries()
        row = next(i for i, x in enumerate(rows) if x["data"]["name"] == "Zeta 35")
        t.selectRow(row)
        t.scrollToItem(t.item(row, 0))
        QTest.qWait(10)
        self.assertGreater(t.verticalScrollBar().value(), 0)
        self.page.refresh()
        QTest.qWait(30)
        self.assertEqual(t.currentRow(), row)
        self.assertGreater(t.verticalScrollBar().value(), 0)


class SearchTests(_Base):
    def setUp(self):
        super().setUp()
        self.food = self.db.save_category({"name": "Foodies", "type": "expense"})["id"]
        self.snack = self.db.save_category({"name": "Snackery", "type": "expense",
                                            "parent_id": self.food})["id"]
        self.page.refresh()

    def search(self, text):
        self.page._search.setText(text)

    def names(self):
        return [(n, dim) for _k, n, dim in self.page.visible_entries()]

    def test_filters_by_name_case_insensitively(self):
        self.search("SNACKERY")
        self.assertIn(("Snackery", False), self.names())
        self.assertIn(("Foodies", True), self.names())          # parent kept, dimmed
        self.assertNotIn("Checking", [n for n, _ in self.names()])
        heads = [self.page._tree.topLevelItem(i) for i in range(self.page._tree.topLevelItemCount())]
        shown_heads = [h.text(0) for h in heads
                       if self.page._entry(h) is None and not h.isHidden()]
        self.assertEqual(shown_heads, ["EXPENSE"])

    def test_matches_type_and_institution(self):
        self.search("first bank")
        self.assertEqual([n for n, _ in self.names()], ["Checking"])
        self.search("checking")
        self.assertIn("Checking", [n for n, _ in self.names()])

    def test_matches_schedule_c_line_in_business_book(self):
        self.db.set_book_type("business")
        from ui.pages.accounts import AccountsPage
        page = AccountsPage(self.db)
        page.refresh()
        page._search.setText("line 24b")
        names = [n for _k, n, _d in page.visible_entries()]
        self.assertIn("Meals (50%)", names)
        self.assertNotIn("Travel", names)
        page.deleteLater()

    def test_no_match_message_and_clear_button(self):
        self.assertFalse(self.page._search_clear.isVisible())
        self.search("zzzz nothing")
        self.assertTrue(self.page._no_match.isVisible())
        self.assertEqual(self.page._no_match.text(), "No accounts match")
        self.assertTrue(self.page._search_clear.isVisible())
        self.page._search_clear.click()
        self.assertEqual(self.page._search.text(), "")
        self.assertFalse(self.page._no_match.isVisible())
        self.assertIn("Checking", [n for n, _ in self.names()])
        self.assertTrue(all(not d for _n, d in self.names()))   # nothing left dimmed

    def test_filter_survives_refresh_after_edit(self):
        self.search("snack")
        e = self.page._entry(self.item("category", self.snack))
        _FakeCategoryDialog.rename_to = "Snackery Two"
        with mock.patch("ui.pages.categories.CategoryDialog", _FakeCategoryDialog):
            self.page._edit_entry(e)
        self.assertEqual(self.page._search.text(), "snack")
        self.assertIn(("Snackery Two", False), self.names())
        self.assertNotIn("Checking", [n for n, _ in self.names()])

    def test_ctrl_f_focuses_search(self):
        self.assertEqual(self.page._find_shortcut.key(), QKeySequence("Ctrl+F"))
        self.page._tabs.setCurrentIndex(2)
        self.page.activateWindow()
        QTest.qWait(20)
        self.page._find_shortcut.activated.emit()
        QTest.qWait(20)
        self.assertEqual(self.page._tabs.currentIndex(), 0)
        self.assertIs(QApplication.focusWidget(), self.page._search)


if __name__ == "__main__":
    unittest.main()
