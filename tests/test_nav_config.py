"""Pure-logic tests for utils.nav_config: presets, the stored preference,
personal books, hubs and receipt line types."""
import json
import os
import shutil
import tempfile
import unittest

import tests  # noqa: F401
from utils import nav_config as NC
from sheets.client import Database

# The sidebar a Personal book has always had — it must never change.
PERSONAL_ORDER = ["Dashboard", "Accounts", "Transactions", "Transfers", "Import",
                  "Reconcile", "Loans", "Recurring", "Rules", "Reports"]


def labels(book, enabled=NC.ALL_MODULES):
    return [it.label for it in NC.visible_items(book, enabled)]


class PresetTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(labels("business", NC.preset_modules("simple")),
                         ["Dashboard", "Accounts", "Transactions", "Receipts",
                          "Invoices & Bills", "Owner Investment", "Taxes", "Reports",
                          "Rules"])

    def test_standard(self):
        self.assertEqual(labels("business", NC.preset_modules("standard")),
                         ["Dashboard", "Accounts", "Transactions", "Receipts",
                          "Invoices & Bills", "Fixed Assets", "Owner Investment", "Taxes",
                          "Reports", "Rules"])

    def test_full(self):
        self.assertEqual(labels("business", NC.preset_modules("full")),
                         ["Dashboard", "Accounts", "Transactions", "Receipts",
                          "Invoices & Bills", "Inventory", "Fixed Assets", "People & Payroll",
                          "Owner Investment", "Taxes", "Reports", "Rules"])

    def test_presets_nest(self):
        s, st, f = (NC.preset_modules(k) for k in ("simple", "standard", "full"))
        self.assertTrue(s < st < f)
        self.assertEqual(f, NC.ALL_MODULES)
        self.assertEqual(NC.preset_modules("nonsense"), NC.ALL_MODULES)

    def test_preset_for(self):
        for p in NC.PRESETS:
            self.assertEqual(NC.preset_for(p.modules), p.key)
        self.assertEqual(NC.preset_for({"receipts"}), "custom")
        self.assertEqual(NC.preset_for(set(NC.preset_modules("simple")) | {"bogus"}), "simple")

    def test_every_page_reachable_in_full(self):
        shown = {p for it in NC.visible_items("business") for p in it.pages}
        self.assertEqual(shown | {"Settings"}, set(NC.ALL_PAGES))


class PersonalTests(unittest.TestCase):
    def test_unchanged_whatever_the_modules(self):
        for enabled in (NC.ALL_MODULES, frozenset(), NC.preset_modules("simple")):
            self.assertEqual(labels("personal", enabled), PERSONAL_ORDER)
            self.assertEqual([it.id for it in NC.visible_items("personal", enabled)],
                             PERSONAL_ORDER)
            self.assertFalse(any(it.is_hub for it in NC.visible_items("personal", enabled)))
        self.assertEqual([h for h, _ in NC.visible_sections("personal")],
                         ["OVERVIEW", "MONEY", "REPORTS"])

    def test_personal_receipt_kinds(self):
        self.assertEqual(NC.receipt_line_kinds("personal"), [("expense", "Expense")])

    def test_icons_unchanged(self):
        self.assertEqual([it.icon for it in NC.visible_items("personal")],
                         ["dashboard", "accounts", "transactions", "transfers", "import",
                          "reconcile", "loans", "calendar", "rules", "reports"])


class StoredPreferenceTests(unittest.TestCase):
    def test_missing_empty_corrupt_fall_back_to_full(self):
        for raw in (None, "", "   ", "{", "null", "[]", "42", '"x"', "{}",
                    '{"enabled": "receipts"}', '{"enabled": null}', b"bytes"):
            self.assertEqual(NC.parse_modules(raw), NC.ALL_MODULES, raw)

    def test_round_trip(self):
        for p in NC.PRESETS:
            self.assertEqual(NC.parse_modules(NC.dump_modules(p.modules)), p.modules)
        self.assertEqual(NC.parse_modules(NC.dump_modules(set())), frozenset())
        data = json.loads(NC.dump_modules(NC.preset_modules("simple")))
        self.assertEqual(data["preset"], "simple")

    def test_unknown_future_modules_ignored_but_preserved(self):
        raw = json.dumps({"enabled": ["receipts", "crm"], "known": list(NC.MODULE_KEYS) + ["crm"]})
        self.assertEqual(NC.parse_modules(raw), frozenset({"receipts"}))
        again = json.loads(NC.dump_modules({"receipts", "taxes"}, raw))
        self.assertIn("crm", again["enabled"])
        self.assertIn("crm", again["known"])
        self.assertEqual(NC.parse_modules(json.dumps(again)), frozenset({"receipts", "taxes"}))

    def test_non_string_entries_ignored(self):
        raw = json.dumps({"enabled": ["taxes", 3, None, {"a": 1}]})
        self.assertEqual(NC.parse_modules(raw), frozenset({"taxes"}))

    def test_module_added_later_defaults_on(self):
        # Written by a version that didn't know about payroll yet.
        known = [m for m in NC.MODULE_KEYS if m != "payroll"]
        raw = json.dumps({"enabled": ["receipts"], "known": known})
        self.assertEqual(NC.parse_modules(raw), frozenset({"receipts", "payroll"}))

    def test_known_missing_means_written_with_todays_list(self):
        self.assertEqual(NC.parse_modules('{"enabled": ["taxes"]}'), frozenset({"taxes"}))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_existing_business_db_without_key_is_full(self):
        db = Database(os.path.join(self.dir, "b.db"), book_type="business")
        self.assertEqual(db.get_setting(NC.NAV_SETTING_KEY), "")
        self.assertEqual(NC.enabled_modules(db), NC.ALL_MODULES)

    def test_saved_and_reopened(self):
        path = os.path.join(self.dir, "b.db")
        db = Database(path, book_type="business")
        NC.save_modules(db, NC.preset_modules("simple"))
        db.close()
        self.assertEqual(NC.enabled_modules(Database(path)), NC.preset_modules("simple"))

    def test_personal_ignores_key(self):
        db = Database(os.path.join(self.dir, "p.db"))
        db.set_setting(NC.NAV_SETTING_KEY, NC.dump_modules(set()))
        self.assertEqual(NC.enabled_modules(db), NC.ALL_MODULES)

    def test_corrupt_value_in_db(self):
        db = Database(os.path.join(self.dir, "b.db"), book_type="business")
        db.set_setting(NC.NAV_SETTING_KEY, "not json at all")
        self.assertEqual(NC.enabled_modules(db), NC.ALL_MODULES)


class LayoutTests(unittest.TestCase):
    def test_hubs(self):
        items = {it.id: it for it in NC.visible_items("business")}
        self.assertEqual(items["Transactions"].pages,
                         ("Transactions", "Import", "Reconcile", "Transfers", "Recurring"))
        self.assertEqual(items["Accounts"].pages, ("Accounts", "Loans"))
        self.assertEqual(items["Invoices & Bills"].pages, ("Invoices & Bills", "Contacts"))
        self.assertEqual(items["Rules"].pages, ("Rules",))
        self.assertNotIn("Categories & Rules", items)

    def test_loans_tab_follows_module(self):
        items = {it.id: it for it in NC.visible_items("business", NC.ALL_MODULES - {"loans"})}
        self.assertEqual(items["Accounts"].pages, ("Accounts",))
        self.assertFalse(items["Accounts"].is_hub)
        self.assertIsNone(NC.locate("Loans", "business", NC.ALL_MODULES - {"loans"}))

    def test_contacts_always_reachable(self):
        for enabled in (NC.ALL_MODULES, frozenset(), NC.ALL_MODULES - {"invoices"}):
            hit = NC.locate("Contacts", "business", enabled)
            self.assertIsNotNone(hit)
        item, page = NC.locate("Contacts", "business", frozenset())
        self.assertEqual((item.id, item.label, page), ("Contacts", "Contacts", "Contacts"))
        item, page = NC.locate("Contacts", "business", NC.ALL_MODULES)
        self.assertEqual(item.id, "Invoices & Bills")

    def test_everything_off_keeps_core(self):
        self.assertEqual(labels("business", frozenset()),
                         ["Dashboard", "Accounts", "Transactions", "Contacts", "Reports",
                          "Rules"])

    def test_locate(self):
        for name in NC.ALL_PAGES:
            hit = NC.locate(name, "business")
            self.assertIsNotNone(hit, name)
            self.assertEqual(hit[1], name)
        item, page = NC.locate("Import", "business")
        self.assertEqual((item.id, page), ("Transactions", "Import"))
        # v1.14: Categories are part of Accounts; the old ids still resolve.
        item, page = NC.locate("Categories & Rules", "business")
        self.assertEqual((item.id, page), ("Rules", "Rules"))
        item, page = NC.locate("Categories", "business")
        self.assertEqual((item.id, page), ("Accounts", "Accounts"))
        self.assertEqual(NC.locate("Categories", "personal")[1], "Accounts")
        self.assertEqual(NC.locate("Settings", "personal"), (None, "Settings"))
        self.assertIsNone(NC.locate("Receipts", "personal"))
        self.assertIsNone(NC.locate("Nope", "business"))

    def test_hidden_modules_not_located(self):
        for m in NC.MODULES:
            off = NC.ALL_MODULES - {m.key}
            for p in m.pages:
                self.assertIsNone(NC.locate(p, "business", off))
                self.assertEqual(NC.module_for_page(p), m.key)
        for always in ("Dashboard", "Transactions", "Import", "Contacts", "Reports", "Rules"):
            self.assertIsNone(NC.module_for_page(always))

    def test_each_page_once(self):
        for p in NC.PRESETS:
            pages = [pg for it in NC.visible_items("business", p.modules) for pg in it.pages]
            self.assertEqual(len(pages), len(set(pages)))


class ReceiptKindTests(unittest.TestCase):
    def test_follow_modules(self):
        k = lambda en, keep=(): [x for x, _ in NC.receipt_line_kinds("business", en, keep)]  # noqa: E731
        self.assertEqual(k(NC.ALL_MODULES), ["expense", "asset", "inventory"])
        self.assertEqual(k(NC.preset_modules("standard")), ["expense", "asset"])
        self.assertEqual(k(NC.preset_modules("simple")), ["expense"])
        # A line already saved as Inventory keeps its type when the module is off.
        self.assertEqual(k(NC.preset_modules("simple"), {"inventory"}), ["expense", "inventory"])


if __name__ == "__main__":
    unittest.main()
