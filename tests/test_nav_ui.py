"""Headless (offscreen Qt) tests of the Business sidebar: hubs, optional
modules and presets, Settings → Sidebar & Features, offers to turn a hidden
module on, the guided tour, receipts line types and database switching.
Personal books must look exactly as before. Message boxes are mocked."""
import os
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
import db_config
import db_lock
from sheets.client import Database
from utils import nav_config as NC

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog, QWidget
    from PyQt6.QtCore import QEvent
    from PyQt6 import sip
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover — no Qt available
    raise unittest.SkipTest(f"PyQt6 not available: {e}")

from ui.main_window import MainWindow, HubPage, _page_constructors
from ui.database_dialog import DatabaseSetupDialog
from ui.guided_tour import GuidedTour
from ui.dialogs import receipt_dialog as RD

YES, NO, OK = (QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No,
               QMessageBox.StandardButton.Ok)
PERSONAL_SIDEBAR = ["Dashboard", "Accounts", "Transactions", "Transfers", "Import", "Reconcile",
                    "Loans", "Recurring", "Rules", "Reports", "Settings"]
OLD_PAGE_NAMES = ["Dashboard", "Accounts", "Transactions", "Transfers", "Import", "Reconcile",
                  "Loans", "Recurring", "Rules", "Categories", "Owner Investment", "Receipts",
                  "Assets", "Invoices & Bills", "Inventory", "Contacts", "People & Payroll",
                  "Taxes", "Reports", "Settings"]


def flush():
    for _ in range(2):
        _app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        _app.processEvents()


class NavCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig_settings = db_config.SETTINGS_PATH
        db_config.SETTINGS_PATH = os.path.join(self.dir, "app_settings.json")
        self.msgs = []
        self.answer = YES
        patches = [mock.patch.dict(os.environ, {"FINANCEBOOK_MACHINE_NAME": "LAPTOP-A"})]
        for name in ("warning", "critical", "information", "question"):
            patches.append(mock.patch.object(QMessageBox, name, staticmethod(self._box(name))))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.windows = []

    def _box(self, kind):
        def box(parent, title, text, *a, **k):
            self.msgs.append((kind, title, text))
            return self.answer if kind == "question" else OK
        return box

    def tearDown(self):
        for w in self.windows:
            w.close()
            w.deleteLater()
        flush()
        db_lock._release_all()
        db_config.SETTINGS_PATH = self._orig_settings
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_db(self, name="biz.db", book="business", preset=None):
        path = os.path.join(self.dir, name)
        db = Database(path, book_type=book)
        if preset:
            NC.save_modules(db, NC.preset_modules(preset))
        db.close()
        return path

    def window(self, path, read_only=False):
        lock = None
        if not read_only:
            lock = db_lock.DbLock(path)
            lock.acquire()
        w = MainWindow(Database(path, read_only=read_only), lock)
        w.resize(1100, 700)
        self.windows.append(w)
        return w

    @staticmethod
    def sidebar(w):
        return list(w._nav_buttons)

    @staticmethod
    def sidebar_text(w):
        return [b.text().strip().replace("&&", "&") for b in w._nav_buttons.values()]


class SidebarTests(NavCase):
    def test_personal_sidebar_unchanged(self):
        w = self.window(self.make_db("p.db", book="personal"))
        self.assertEqual(self.sidebar(w), PERSONAL_SIDEBAR)
        self.assertEqual(self.sidebar_text(w), PERSONAL_SIDEBAR)
        self.assertIsNone(w._add_features_btn)
        self.assertEqual(w._book_badge.text(), "PERSONAL BOOK")
        for name in PERSONAL_SIDEBAR:
            w._nav_to(name)
            self.assertNotIsInstance(w._stack.currentWidget(), HubPage)
            self.assertIs(w._stack.currentWidget(), w.current_page_widget())
            self.assertIsInstance(w.current_page_widget(), _page_constructors()[name])
        self.assertEqual(w._hub_cache, {})
        # Business pages are still not offered in a Personal book.
        self.assertIsNone(w.page("Receipts"))
        # Old ids are aliases: Categories → Accounts, Categories & Rules → Rules.
        self.assertIsInstance(w.page("Categories"), _page_constructors()["Accounts"])
        self.assertIsInstance(w.page("Categories & Rules"), _page_constructors()["Rules"])
        self.assertEqual(self.msgs, [])

    def test_personal_ignores_stored_modules(self):
        path = self.make_db("p.db", book="personal")
        db = Database(path)
        db.set_setting(NC.NAV_SETTING_KEY, NC.dump_modules(set()))
        db.close()
        self.assertEqual(self.sidebar(self.window(path)), PERSONAL_SIDEBAR)

    def test_business_sidebar_per_preset(self):
        for preset in ("simple", "standard", "full"):
            with self.subTest(preset=preset):
                w = self.window(self.make_db(f"{preset}.db", preset=preset))
                want = NC.visible_items("business", NC.preset_modules(preset))
                self.assertEqual(self.sidebar(w), [i.id for i in want] + ["Settings"])
                self.assertEqual(self.sidebar_text(w), [i.label for i in want] + ["Settings"])
                self.assertIsNotNone(w._add_features_btn)
        self.assertEqual(len(want), 12)      # Full: 12 items + Settings, versus 20 before

    def test_existing_business_db_without_key_shows_everything(self):
        w = self.window(self.make_db())
        self.assertEqual(w._modules, NC.ALL_MODULES)
        for name in OLD_PAGE_NAMES:
            self.assertIsNotNone(w._page_index(name), name)

    def test_corrupt_setting_falls_back_to_full(self):
        path = self.make_db()
        db = Database(path)
        db.set_setting(NC.NAV_SETTING_KEY, "{oops")
        db.close()
        w = self.window(path)
        self.assertEqual(len(w._nav_buttons), 13)

    def test_ampersand_labels_render(self):
        w = self.window(self.make_db())
        self.assertEqual(w._nav_buttons["Invoices & Bills"].text().strip(), "Invoices && Bills")


class NavigateTests(NavCase):
    def check_all_pages(self, w, enabled):
        ctors = _page_constructors()
        for name in OLD_PAGE_NAMES:
            hit = NC.locate(name, "business", enabled)
            if hit is None:
                continue
            item, _ = hit
            with self.subTest(page=name):
                w._nav_to(name)
                page = w.current_page_widget()
                self.assertIsInstance(page, ctors[NC.PAGE_ALIASES.get(name, name)])
                self.assertIs(w.page(name), page)
                if item is not None and item.is_hub:
                    hub = w._stack.currentWidget()
                    self.assertIsInstance(hub, HubPage)
                    self.assertEqual(hub.item.id, item.id)
                    self.assertEqual(hub.current_tab(), NC.PAGE_ALIASES.get(name, name))
                    self.assertTrue(hub.holds(page))
                    self.assertTrue(page.isVisibleTo(hub))
                else:
                    self.assertIs(w._stack.currentWidget(), page)
                active = [n for n, b in w._nav_buttons.items() if b._active]
                self.assertEqual(active, [item.id if item else "Settings"])
        # The same instance is reused (lazy, cached).
        self.assertIs(w.page("Import"), w.page("Import"))

    def test_every_old_name_every_preset(self):
        for preset in ("simple", "standard", "full"):
            w = self.window(self.make_db(f"{preset}.db", preset=preset))
            self.check_all_pages(w, NC.preset_modules(preset))
            self.assertEqual(self.msgs, [])

    def test_hub_ids_and_tab_clicks(self):
        w = self.window(self.make_db())
        self.assertIsInstance(w.page("Categories & Rules"), _page_constructors()["Rules"])
        self.assertEqual(w._current_page, "Rules")
        self.assertIsInstance(w.page("Categories"), _page_constructors()["Accounts"])
        hub = w._stack.currentWidget()
        self.assertEqual(hub.tab_labels(), ["Accounts", "Loans"])
        hub._buttons["Loans"].click()
        self.assertIsInstance(w.current_page_widget(), _page_constructors()["Loans"])
        self.assertEqual(w._current_page, "Loans")
        w._nav_to("Transactions")
        self.assertEqual(w._stack.currentWidget().tab_labels(),
                         ["Transactions", "Import", "Reconcile", "Transfers", "Recurring"])

    def test_open_account_transactions(self):
        path = self.make_db()
        db = Database(path)
        acct = db.save_account({"name": "Checking", "type": "checking"})
        db.close()
        w = self.window(path)
        w.page("Import")
        w.open_account_transactions(acct["id"])
        self.assertEqual(w._current_page, "Transactions")
        self.assertEqual(w._stack.currentWidget().current_tab(), "Transactions")

    def test_all_nav_buttons_navigate(self):
        w = self.window(self.make_db())
        for name in list(w._nav_buttons):
            w._nav_buttons[name].click()
            self.assertIsNotNone(w.current_page_widget(), name)
        self.assertEqual(self.msgs, [])


class HiddenModuleTests(NavCase):
    def test_offer_declined_stays_put(self):
        w = self.window(self.make_db(preset="simple"))
        w._nav_to("Reports")
        self.answer = NO
        w._nav_to("Inventory")
        self.assertEqual(self.msgs[-1][1], "Turn On Inventory?")
        self.assertEqual(w._current_page, "Reports")
        self.assertNotIn("Inventory", w._nav_buttons)
        self.assertIsNone(w.page("Inventory"))

    def test_offer_accepted_turns_module_on(self):
        path = self.make_db(preset="simple")
        w = self.window(path)
        w._nav_to("Loans")
        self.assertEqual(self.msgs[-1][1], "Turn On Loans?")
        self.assertEqual(w._current_page, "Loans")
        self.assertEqual(w._stack.currentWidget().item.id, "Accounts")
        self.assertIn("loans", NC.enabled_modules(w.db))
        w.close()
        db_lock._release_all()
        self.assertIn("loans", NC.enabled_modules(Database(path)))

    def test_dashboard_links_offer(self):
        w = self.window(self.make_db(preset="simple"))
        dash = w.page("Dashboard")
        self.answer = NO
        dash._goto("People & Payroll")
        self.assertEqual(self.msgs[-1][1], "Turn On People & Payroll?")
        self.assertEqual(w._current_page, "Dashboard")
        dash._goto("Invoices & Bills")          # enabled: no question
        self.assertEqual(w._current_page, "Invoices & Bills")
        self.assertEqual(len(self.msgs), 1)
        w._nav_to("Dashboard")
        dash = w.page("Dashboard")
        self.answer = YES
        dash._goto("Inventory")
        self.assertEqual(w._current_page, "Inventory")

    def test_read_only_cannot_enable(self):
        path = self.make_db(preset="simple")
        w = self.window(path, read_only=True)
        w._nav_to("Inventory")
        self.assertEqual(self.msgs[-1][0], "information")
        self.assertIn("read-only", self.msgs[-1][2])
        self.assertNotIn("inventory", NC.enabled_modules(w.db))


class SettingsSectionTests(NavCase):
    def test_toggle_updates_sidebar_and_persists(self):
        path = self.make_db()
        w = self.window(path)
        page = w.page("Settings")
        self.assertFalse(page._features_grp.isHidden())
        self.assertEqual(page._preset_combo.currentData(), "full")
        page._module_checks["inventory"].setChecked(False)
        self.assertNotIn("Inventory", w._nav_buttons)
        self.assertEqual(page._preset_combo.currentData(), "custom")
        self.assertIs(w.page("Settings"), page)        # the page itself survived the rebuild
        page._module_checks["loans"].setChecked(False)
        self.assertEqual(w.db.get_setting(NC.NAV_SETTING_KEY) != "", True)
        w.close()
        db_lock._release_all()
        w2 = self.window(path)
        self.assertNotIn("Inventory", w2._nav_buttons)
        self.assertIsNone(w2._page_index("Loans"))

    def test_preset_combo(self):
        w = self.window(self.make_db())
        page = w.page("Settings")
        page._preset_combo.setCurrentIndex(page._preset_combo.findData("simple"))
        page._preset_combo.activated.emit(page._preset_combo.currentIndex())
        self.assertEqual(w._modules, NC.preset_modules("simple"))
        self.assertEqual(self.sidebar(w)[:-1],
                         [i.id for i in NC.visible_items("business", NC.preset_modules("simple"))])
        self.assertFalse(page._module_checks["assets"].isChecked())
        self.assertTrue(page._module_checks["receipts"].isChecked())
        # "Custom" isn't a preset to apply.
        page._preset_combo.setCurrentIndex(page._preset_combo.findData("custom"))
        page._preset_combo.activated.emit(page._preset_combo.currentIndex())
        self.assertEqual(w._modules, NC.preset_modules("simple"))
        self.assertEqual(page._preset_combo.currentData(), "simple")

    def test_data_note_for_hidden_module(self):
        path = self.make_db()
        db = Database(path)
        for i in range(3):
            db.save_item({"name": f"Widget {i}"})
        db.close()
        w = self.window(path)
        page = w.page("Settings")
        self.assertIn("Has 3 items", page._module_notes["inventory"].text())
        page._module_checks["inventory"].setChecked(False)
        note = page._module_notes["inventory"].text()
        self.assertIn("Inventory is hidden but has 3 items", note)
        self.assertIn("doesn't delete anything", note)
        self.assertEqual(len(w.db.get_items(active_only=False)), 3)

    def test_read_only_disables_controls(self):
        w = self.window(self.make_db(), read_only=True)
        page = w.page("Settings")
        self.assertFalse(page._preset_combo.isEnabled())
        for chk in page._module_checks.values():
            self.assertFalse(chk.isEnabled())
            self.assertIn("read-only", chk.toolTip())
        self.assertFalse(page._features_ro_note.isHidden())

    def test_hidden_for_personal(self):
        w = self.window(self.make_db("p.db", book="personal"))
        self.assertTrue(w.page("Settings")._features_grp.isHidden())

    def test_add_features_link(self):
        w = self.window(self.make_db(preset="simple"))
        w.show()
        w._add_features_btn.click()
        flush()
        self.assertEqual(w._current_page, "Settings")
        page = w.current_page_widget()
        self.assertGreater(page._scroll.verticalScrollBar().value(), 0)
        w.hide()


class RebuildTests(NavCase):
    def test_current_page_moves_into_new_hub(self):
        w = self.window(self.make_db(preset="simple"))
        accounts = w.page("Accounts")
        self.assertIs(w._stack.currentWidget(), accounts)      # plain: no Loans tab
        w.set_modules(NC.preset_modules("standard"))
        self.assertIs(w.current_page_widget(), accounts)
        hub = w._stack.currentWidget()
        self.assertIsInstance(hub, HubPage)
        self.assertTrue(hub.holds(accounts))
        self.assertEqual(hub.tab_labels(), ["Accounts", "Loans"])
        # And back out of the hub again.
        w.set_modules(NC.preset_modules("simple"))
        flush()
        self.assertIs(w.current_page_widget(), accounts)
        self.assertIs(w._stack.currentWidget(), accounts)
        self.assertTrue(sip.isdeleted(hub))
        self.assertEqual(w._hub_cache, {})

    def test_rebuild_drops_other_pages_and_hubs(self):
        w = self.window(self.make_db())
        pages = [w.page(n) for n in ("Import", "Rules", "Inventory", "Contacts")]
        hubs = list(w._hub_cache.values())
        settings = w.page("Settings")
        w.set_modules(NC.preset_modules("standard"))
        flush()
        self.assertTrue(all(sip.isdeleted(p) for p in pages))
        self.assertTrue(all(sip.isdeleted(h) for h in hubs))
        self.assertEqual(set(w._page_cache), {"Settings"})
        self.assertIs(w._page_cache["Settings"], settings)
        self.assertEqual(w._stack.count(), 1)

    def test_hidden_current_page_goes_to_dashboard(self):
        w = self.window(self.make_db())
        inv = w.page("Inventory")
        w.set_modules(NC.preset_modules("simple"))
        flush()
        self.assertEqual(w._current_page, "Dashboard")
        self.assertTrue(sip.isdeleted(inv))

    def test_repeated_toggles_dont_leak(self):
        w = self.window(self.make_db())
        w.page("Loans")
        for preset in ("simple", "full", "standard", "full") * 3:
            w.set_modules(NC.preset_modules(preset))
            w.page("Import")
            w.page("Loans")
        flush()
        self.assertLessEqual(w._stack.count(), 2)
        self.assertEqual(len(w.findChildren(HubPage)), len(w._hub_cache))

    def test_reload_keeps_current_hub(self):
        w = self.window(self.make_db())
        imp = w.page("Import")
        w.page("Rules")
        w._nav_to("Import")
        w._reload()
        flush()
        self.assertIs(w.current_page_widget(), imp)
        self.assertEqual(set(w._page_cache), {"Import"})
        self.assertEqual(w._stack.currentWidget().current_tab(), "Import")
        w._nav_to("Reconcile")
        self.assertEqual(w._stack.currentWidget().current_tab(), "Reconcile")

    def test_switch_personal_business(self):
        biz = self.make_db("biz.db", preset="simple")
        per = self.make_db("per.db", book="personal")
        w = self.window(biz)
        old = [w.page(n) for n in ("Import", "Receipts", "Rules")]
        self.assertTrue(w.open_database(per))
        flush()
        self.assertEqual(self.sidebar(w), PERSONAL_SIDEBAR)
        self.assertTrue(all(sip.isdeleted(p) for p in old))
        self.assertEqual(w._hub_cache, {})
        self.assertEqual(set(w._page_cache), {"Dashboard"})
        w.page("Import")
        self.assertNotIsInstance(w._stack.currentWidget(), HubPage)
        self.assertTrue(w.open_database(biz))
        flush()
        self.assertEqual(self.sidebar(w)[:-1],
                         [i.id for i in NC.visible_items("business", NC.preset_modules("simple"))])
        self.assertIsInstance(w.page("Import"), _page_constructors()["Import"])
        self.assertIsInstance(w._stack.currentWidget(), HubPage)

    def test_book_type_switch_in_settings(self):
        w = self.window(self.make_db("p.db", book="personal"))
        page = w.page("Settings")
        page._toggle_book_type()                      # → Business (answer Yes)
        self.assertTrue(w.db.is_business())
        self.assertEqual(w._modules, NC.ALL_MODULES)  # no key yet → everything on
        self.assertEqual(len(w._nav_buttons), 13)
        self.assertIs(w.current_page_widget(), page)
        self.assertFalse(page._features_grp.isHidden())
        page._toggle_book_type()                      # → Personal
        self.assertEqual(self.sidebar(w), PERSONAL_SIDEBAR)
        self.assertTrue(page._features_grp.isHidden())
        self.assertEqual(w._hub_cache, {})


class DataStillReportedTests(NavCase):
    def report_text(self, w):
        rp = w.page("Reports")
        out = []
        for tab in (0, 1):
            rp._tabs.setCurrentIndex(tab)
            rp._run()
            tree = rp._pnl_tree if tab == 0 else rp._bs_tree
            stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
            while stack:
                it = stack.pop(0)
                out.append((it.text(0), it.text(1)))
                stack.extend(it.child(i) for i in range(it.childCount()))
        return out

    def test_hidden_inventory_and_assets_still_in_reports(self):
        path = self.make_db()
        db = Database(path)
        bank = db.save_account({"name": "Bank", "type": "checking"})
        today = date.today()
        early = (today - timedelta(days=40)).isoformat()
        item = db.save_item({"name": "Widget", "sku": "W"})
        db.receive_stock(item["id"], early, 10, 5, account_id=bank["id"])
        db.sell_stock(item["id"], today.isoformat(), 4, 12, account_id=bank["id"])
        db.save_asset({"name": "Laptop", "purchase_date": f"{today.year}-01-01",
                       "in_service_date": f"{today.year}-01-01", "cost": 2400,
                       "life_months": 36, "method": "sl", "status": "active"})
        db.close()
        w = self.window(path)
        before = self.report_text(w)
        flat = " ".join(f"{a} {b}" for a, b in before)
        self.assertIn("Cost of Goods Sold", flat)
        self.assertIn("Depreciation", flat)
        self.assertIn("20.00", flat)            # COGS: 4 × $5
        w.set_modules(NC.preset_modules("simple"))
        self.assertNotIn("Inventory", w._nav_buttons)
        self.assertNotIn("Assets", w._nav_buttons)
        self.assertEqual(self.report_text(w), before)


class ReceiptKindsTests(NavCase):
    def kinds(self, db, rc):
        dlg = RD.ReceiptReviewDialog(db, db.get_receipt(rc["id"]))
        self.addCleanup(dlg.deleteLater)
        if dlg.table.rowCount() == 0:
            dlg._add_row({"description": "x", "qty": 1, "unit_price": 1, "line_total": 1})
        combo = dlg.table.cellWidget(0, RD.COL_KIND)
        return ([combo.itemData(i) for i in range(combo.count())], combo.currentData(),
                dlg.table.isColumnHidden(RD.COL_ITEM))

    def test_follow_modules(self):
        db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.addCleanup(db.close)
        rc = db.add_manual_receipt()
        self.assertEqual(self.kinds(db, rc)[0], ["expense", "asset", "inventory"])
        NC.save_modules(db, NC.preset_modules("standard"))
        kinds, _, item_hidden = self.kinds(db, rc)
        self.assertEqual(kinds, ["expense", "asset"])
        self.assertTrue(item_hidden)
        NC.save_modules(db, NC.preset_modules("simple"))
        self.assertEqual(self.kinds(db, rc)[0], ["expense"])

    def test_saved_line_keeps_kind_when_module_hidden(self):
        db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.addCleanup(db.close)
        rc = db.add_manual_receipt()
        db.replace_receipt_items(rc["id"], [{"description": "Bolts", "qty": 1, "unit_price": 3,
                                             "line_total": 3, "kind": "inventory"}])
        NC.save_modules(db, NC.preset_modules("simple"))
        kinds, current, item_hidden = self.kinds(db, rc)
        self.assertEqual(kinds, ["expense", "inventory"])
        self.assertEqual(current, "inventory")
        self.assertFalse(item_hidden)

    def test_personal_unchanged(self):
        db = Database(os.path.join(self.dir, "p.db"))
        self.addCleanup(db.close)
        rc = db.add_manual_receipt()
        self.assertEqual(self.kinds(db, rc)[0], ["expense"])


class TourTests(NavCase):
    def run_tour(self, w):
        w.show()
        tour = GuidedTour(w)
        tour.start()
        seen = []
        guard = 0
        while tour.overlay is not None and guard < 60:
            _app.processEvents()
            tour._apply_step()
            step = tour.steps[tour.i]
            seen.append((step.get("nav"), tour.overlay._title_lbl.text()))
            if step.get("nav"):
                self.assertEqual(w._page_index(step["nav"]) is not None, True)
            tour.next()
            guard += 1
        self.assertIsNone(tour.overlay)
        w.hide()
        return seen

    def test_every_preset(self):
        for preset in ("simple", "standard", "full"):
            with self.subTest(preset=preset):
                w = self.window(self.make_db(f"{preset}.db", preset=preset))
                seen = self.run_tour(w)
                navs = {n for n, _ in seen}
                enabled = NC.preset_modules(preset)
                for m in NC.MODULES:
                    for p in m.pages:
                        if p in {s["nav"] for s in __import__("ui.guided_tour").guided_tour.STEPS}:
                            self.assertEqual(p in navs, m.key in enabled, (preset, p))
                self.assertIn("Need more (or less)?", [t for _, t in seen])
                self.assertEqual(self.msgs, [])

    def test_personal(self):
        w = self.window(self.make_db("p.db", book="personal"))
        seen = self.run_tour(w)
        titles = [t for _, t in seen]
        self.assertIn("Running a business?", titles)
        self.assertNotIn("Need more (or less)?", titles)
        self.assertEqual(self.msgs, [])

    def test_business_text_mentions_tabs(self):
        w = self.window(self.make_db())
        tour = GuidedTour(w)
        step = next(s for s in tour.steps if s["nav"] == "Import")
        self.assertIn("tab under <b>Transactions</b>", step["body"](w))
        per = self.window(self.make_db("p.db", book="personal"))
        self.assertNotIn("tab under", step["body"](per))


class NewDatabaseTests(NavCase):
    def test_dialog_preset_only_for_business(self):
        dlg = DatabaseSetupDialog(show_book_type=True, default_location=self.dir)
        self.addCleanup(dlg.deleteLater)
        self.assertTrue(dlg._preset_box.isHidden())
        dlg._business_rb.setChecked(True)
        self.assertFalse(dlg._preset_box.isHidden())
        self.assertTrue(dlg._preset_rbs["standard"].isChecked())
        dlg._preset_rbs["simple"].setChecked(True)
        dlg._accept()
        self.assertEqual((dlg.book_type, dlg.preset), ("business", "simple"))

    def test_personal_choice_has_no_preset_ui(self):
        dlg = DatabaseSetupDialog(show_book_type=True, default_location=self.dir)
        self.addCleanup(dlg.deleteLater)
        dlg._accept()
        self.assertEqual(dlg.book_type, "personal")
        plain = DatabaseSetupDialog(default_location=self.dir)
        self.addCleanup(plain.deleteLater)
        self.assertFalse(hasattr(plain, "_preset_box"))

    def test_create_new_business_database_saves_preset(self):
        w = self.window(self.make_db("p.db", book="personal"))

        def accept(d):
            d.database_name, d.database_location = "Shop", self.dir
            d.book_type, d.preset = "business", "simple"
            return QDialog.DialogCode.Accepted
        with mock.patch.object(DatabaseSetupDialog, "exec", accept):
            w._create_new_database()
        self.assertTrue(w.db.is_business())
        self.assertEqual(NC.enabled_modules(w.db), NC.preset_modules("simple"))
        self.assertEqual(self.sidebar(w)[:-1],
                         [i.id for i in NC.visible_items("business", NC.preset_modules("simple"))])


class TwoLaptopTests(NavCase):
    def test_choice_follows_the_database(self):
        path = self.make_db()
        db_config.SETTINGS_PATH = os.path.join(self.dir, "lapA", "app_settings.json")
        os.makedirs(os.path.dirname(db_config.SETTINGS_PATH))
        a = self.window(path)
        a.page("Settings")._module_checks["payroll"].setChecked(False)
        a.close()
        db_lock._release_all()
        with mock.patch.dict(os.environ, {"FINANCEBOOK_MACHINE_NAME": "LAPTOP-B"}):
            db_config.SETTINGS_PATH = os.path.join(self.dir, "lapB", "app_settings.json")
            os.makedirs(os.path.dirname(db_config.SETTINGS_PATH))
            b = self.window(path)
            self.assertNotIn("People & Payroll", b._nav_buttons)
            self.assertIn("Inventory", b._nav_buttons)


class MinimumSizeTests(NavCase):
    def test_hub_tabs_fit_at_minimum_size(self):
        w = self.window(self.make_db())
        w.resize(1100, 700)
        w.show()
        for item in ("Transactions", "Accounts", "Invoices & Bills"):
            w._nav_to(item)
            _app.processEvents()
            hub = w._stack.currentWidget()
            for b in hub._buttons.values():
                self.assertGreaterEqual(b.width(), b.sizeHint().width(), b.text())
        w.reveal_add_features()
        _app.processEvents()
        btn = w._add_features_btn
        top = btn.mapTo(w._nav_scroll.viewport(), btn.rect().topLeft()).y()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(top + btn.height(), w._nav_scroll.viewport().height())
        w.hide()


if __name__ == "__main__":
    unittest.main()
