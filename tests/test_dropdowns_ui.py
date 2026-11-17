"""v1.15.1 dropdowns: with the app theme applied, every combo box's list drops
down BELOW its box (not over it), its rows are drawn by a QStyledItemDelegate
so the stylesheet's hover / selected looks apply, rows are at least 30 px tall
and the list is at least as wide as the box. The opened Match, Money out / in
and Account popups are saved as PNGs in tests/artifacts/ (git-ignored) so they
can be checked by eye."""
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database

try:
    from PyQt6.QtWidgets import QApplication, QComboBox, QStyledItemDelegate
    from PyQt6.QtCore import QPoint, Qt
    _app = QApplication.instance() or QApplication([])
    _HAVE_QT = True
except Exception:       # pragma: no cover
    _HAVE_QT = False

ARTIFACTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
_saved = {}


def setUpModule():
    if not _HAVE_QT:
        return
    from ui import styles
    _saved.update(sheet=_app.styleSheet(), palette=_app.palette(), font=_app.font())
    styles.apply_theme(_app)


def tearDownModule():
    """Put the application back the way the other test modules expect it."""
    if not _HAVE_QT or not _saved:
        return
    from ui import styles
    if styles._title_tint is not None:
        _app.removeEventFilter(styles._title_tint)
        styles._title_tint = None
    _app.setStyleSheet(_saved["sheet"])
    _app.setPalette(_saved["palette"])
    _app.setFont(_saved["font"])


@unittest.skipUnless(_HAVE_QT, "PyQt6 not available")
class DropdownTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "dd.db"))
        self.chk = self.db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.db.save_account({"name": "Chase Sapphire Reserve Visa", "type": "credit card"})
        self.db.save_account({"name": "Savings", "type": "savings"})
        self._open = []

    def tearDown(self):
        for w in self._open:
            w.close()
            w.deleteLater()
        _app.processEvents()
        shutil.rmtree(self.dir, ignore_errors=True)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _show(self, dlg):
        dlg.move(40, 30)
        dlg.show()
        _app.processEvents()
        self._open.append(dlg)
        return dlg

    def _rule_dialog(self):
        from ui.dialogs.rule_dialog import RuleDialog
        dlg = self._show(RuleDialog(db=self.db))
        dlg._add_row({"field": "direction", "operator": "is", "value": "out"})
        _app.processEvents()
        return dlg

    def _txn_dialog(self):
        from ui.dialogs.transaction_dialog import TransactionDialog
        return self._show(TransactionDialog(self.db, account_id=self.chk))

    @staticmethod
    def _open_popup(combo):
        combo.showPopup()
        _app.processEvents()
        return combo.view().window()

    @staticmethod
    def _want_width(combo):
        from ui.styles import POPUP_MAX_WIDTH
        return min(combo.view().sizeHintForColumn(0), max(combo.width(), POPUP_MAX_WIDTH))

    def _snap(self, popup, name):
        os.makedirs(ARTIFACTS, exist_ok=True)
        path = os.path.join(ARTIFACTS, name)
        self.assertTrue(popup.grab().save(path), path)

    def _check_open_popup(self, combo, shot=None):
        popup = self._open_popup(combo)
        try:
            self.assertTrue(popup.isVisible())
            bottom = combo.mapToGlobal(combo.rect().bottomLeft()).y()
            self.assertGreaterEqual(popup.geometry().top(), bottom,
                                    "the list should drop down below the box")
            self.assertGreaterEqual(popup.width(), combo.width())
            view = combo.view()
            for i in range(combo.count()):
                if combo.model().flags(combo.model().index(i, 0)) & Qt.ItemFlag.ItemIsEnabled:
                    self.assertGreaterEqual(view.visualRect(combo.model().index(i, 0)).height(),
                                            30, combo.itemText(i))
                    break
            if shot:
                self._snap(popup, shot)
        finally:
            combo.hidePopup()
            _app.processEvents()

    def _assert_all_styled(self, dlg):
        from ui.styles import combo_item_delegate_class
        cls = combo_item_delegate_class()
        combos = dlg.findChildren(QComboBox)
        self.assertTrue(combos)
        for cb in combos:
            name = cb.objectName() or type(cb).__name__
            self.assertIsInstance(cb.itemDelegate(), QStyledItemDelegate, name)
            self.assertIsInstance(cb.itemDelegate(), cls, name)
            if cb.completer() is not None:
                pop = cb.completer().popup()
                self.assertIsInstance(pop.itemDelegate(), cls, name)
                self.assertEqual(pop.objectName(), "ComboPopup")

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_every_combo_in_rule_and_transaction_dialogs_is_styled(self):
        self._assert_all_styled(self._rule_dialog())
        self._assert_all_styled(self._txn_dialog())

    def test_match_popup_drops_below_with_tall_rows(self):
        dlg = self._rule_dialog()
        self.assertEqual(dlg.mode_combo.count(), 2)
        self._check_open_popup(dlg.mode_combo, "rule_match_popup.png")
        self._check_open_popup(dlg._rows[0].field_combo)
        self._check_open_popup(dlg._rows[1].choice_combo)

    def test_report_columns_dropdown(self):
        """v1.18: the Reports page's Columns: dropdown uses the shared look."""
        from ui.pages.reports import ReportsPage
        from ui.styles import combo_item_delegate_class
        page = ReportsPage(self.db)
        page.resize(1200, 700)
        self._show(page)
        self.assertIsInstance(page._period_combo.itemDelegate(), combo_item_delegate_class())
        self._check_open_popup(page._period_combo, "report_columns_popup.png")

    def test_money_in_out_and_bank_account_popups(self):
        dlg = self._txn_dialog()
        self._check_open_popup(dlg.direction_combo, "txn_money_in_out_popup.png")
        self._check_open_popup(dlg.account_combo, "txn_bank_account_popup.png")

    def test_account_picker_popup_and_type_to_search(self):
        dlg = self._txn_dialog()
        picker = dlg.cat_combo
        popup = self._open_popup(picker)
        try:
            self.assertTrue(popup.isVisible())
            self.assertGreaterEqual(popup.width(), picker.width())
            # Long account names fit: as wide as the widest item (up to the cap).
            self.assertGreaterEqual(popup.width(), self._want_width(picker))
            self._snap(popup, "txn_account_picker_popup.png")
        finally:
            picker.hidePopup()
            _app.processEvents()
        # Group headers ("— Income —") stay unselectable.
        model = picker.model()
        headers = [i for i in range(picker.count()) if picker.itemText(i).startswith("— ")]
        self.assertTrue(headers)
        for i in headers:
            self.assertFalse(model.flags(model.index(i, 0)) & Qt.ItemFlag.ItemIsSelectable)
        # Type-to-search still filters, in the dropdown-styled completer popup.
        comp = picker.completer()
        comp.setCompletionPrefix("sapph")
        self.assertEqual(comp.completionCount(), 1)
        self.assertTrue(comp.currentCompletion().startswith("Chase Sapphire Reserve Visa"))
        comp.complete()
        _app.processEvents()
        pop = comp.popup()
        try:
            self.assertTrue(pop.isVisible())
            self.assertGreaterEqual(pop.width(), picker.width())
            self.assertGreaterEqual(pop.visualRect(pop.model().index(0, 0)).height(), 30)
            self._snap(pop, "txn_account_picker_search_popup.png")
        finally:
            pop.hide()
            _app.processEvents()

    def test_plain_combo_made_later_is_styled_too(self):
        """A brand-new dropdown gets the setup with no code of its own."""
        from ui.styles import combo_item_delegate_class
        cb = QComboBox()
        cb.addItems(["One", "Two — a much longer second option than the box"])
        cb.setFixedWidth(160)           # narrower than its longest item
        cb.move(40, 30)
        cb.show()
        _app.processEvents()
        self._open.append(cb)
        self.assertIsInstance(cb.itemDelegate(), combo_item_delegate_class())
        popup = self._open_popup(cb)
        try:
            self.assertGreaterEqual(popup.geometry().top(),
                                    cb.mapToGlobal(cb.rect().bottomLeft()).y())
            self.assertGreaterEqual(popup.width(), self._want_width(cb))
            self.assertGreater(popup.width(), cb.width())
        finally:
            cb.hidePopup()
            _app.processEvents()


    def test_database_selector_keeps_its_look_and_drops_below(self):
        from ui.styles import combo_item_delegate_class
        cb = QComboBox()
        cb.setObjectName("DbSelector")
        cb.addItems(["finances.db", "Business books 2026.db"])
        cb.move(40, 30)
        cb.show()
        _app.processEvents()
        self._open.append(cb)
        self.assertIsInstance(cb.itemDelegate(), combo_item_delegate_class())
        self._check_open_popup(cb, "db_selector_popup.png")

    def test_hidden_filter_combo_popup_is_set_up_before_it_shows(self):
        from ui.widgets import FilterComboBox
        from ui.styles import combo_item_delegate_class
        cb = FilterComboBox()
        pop = cb.completer().popup()
        self.assertEqual(pop.objectName(), "ComboPopup")
        self.assertIsInstance(pop.itemDelegate(), combo_item_delegate_class())

    def test_import_match_dropdown_is_styled_and_drops_below(self):
        """v1.17: the Import page's "2 possible matches" dropdown."""
        from ui.pages.import_page import ImportPage
        from ui.styles import combo_item_delegate_class
        for d in ("2026-11-03", "2026-11-06"):
            self.db.save_transaction({"date": d, "account_id": self.chk,
                                      "payee": "Rent", "amount": "-565"})
        page = ImportPage(self.db)
        page.resize(1200, 600)          # leaves room below the combo on the 800 px offscreen screen
        page.refresh()
        page._acct_combo.setCurrentIndex(page._acct_combo.findData(self.chk))
        path = os.path.join(os.path.dirname(__file__), "fixtures", "bank_csv",
                            "wellsfargo_checking.csv")
        with open(path, "rb") as f:
            page.set_file(f.read(), "wellsfargo_checking.csv")
        page._parse()
        # The offscreen screen is only 800 px tall; without the column-mapping
        # box the preview table sits mid-screen, as it does on a real monitor.
        page._col_grp.hide()
        self._show(page)
        combos = [w for w in page._match_widgets.values() if isinstance(w, QComboBox)]
        self.assertEqual(len(combos), 1)
        cb = combos[0]
        self.assertIsInstance(cb.itemDelegate(), combo_item_delegate_class())
        self._snap(page, "import_match_preview.png")
        self._check_open_popup(cb, "import_match_popup.png")


if __name__ == "__main__":
    unittest.main()
