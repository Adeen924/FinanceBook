"""v1.19 Payees (P1) and recall of a payee's last transaction (P2), in
personal AND business books.

Before v1.19 a personal book had no Payees / Contacts page (the personal
sidebar was a fixed list) and the Transaction window built its vendor /
customer field only when db.is_business(), so a personal book could never
link a payee. These tests pin the shared implementation.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.book_helper import BothBooksTestCase

try:
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover
    raise unittest.SkipTest(f"PyQt6 not available: {e}")


class PayeeStoreTests(BothBooksTestCase):
    def seed(self, db):
        self.chk = self.checking(db)
        self.food = self.category(db, "Groceries")
        self.home = self.category(db, "Household")
        for i, (payee, amt) in enumerate([("WALMART #1234", -20), ("Walmart  Store 5678", -30),
                                          ("walmart", -5), ("PAYROLL ACME", 900),
                                          ("Kroger 0042", -12)]):
            db.save_transaction({"date": f"2026-02-{i + 1:02d}", "account_id": self.chk,
                                 "payee": payee, "amount": str(amt), "category_id": self.food})
        base = db.save_transaction({"date": "2026-02-10", "account_id": self.chk,
                                    "payee": "WALMART #9", "amount": "-50"})
        db.split_transaction(base, [{"category_id": self.food, "amount": "-20"},
                                    {"category_id": self.home, "amount": "-30"}], signed=True)

    def test_unlinked_names_group_case_spacing_and_store_numbers(self):
        for db in self.each_book():
            self.seed(db)
            groups = {g["key"]: g for g in db.unlinked_payee_names()}
            wm = groups["walmart"]
            self.assertEqual(wm["name"], "Walmart")
            self.assertEqual(wm["count"], 4)            # the split counts once
            self.assertEqual(len(wm["ids"]), 5)         # every split line is linked
            self.assertIn("kroger", groups)

    def test_create_and_link_then_summary(self):
        for db in self.each_book():
            self.seed(db)
            payee, n = db.create_payee_and_link("Walmart", "walmart")
            self.assertEqual(n, 4)
            self.assertNotIn("walmart", {g["key"] for g in db.unlinked_payee_names()})
            row = next(p for p in db.payee_summary() if p["id"] == payee["id"])
            self.assertEqual(row["n_txns"], 4)
            self.assertEqual(row["spent"], 105.0)
            self.assertEqual(row["last_used"], "2026-02-10")
            # The register filters by payee; the split shows once.
            rows = db.list_register_rows(contact_id=payee["id"])
            self.assertEqual(len(rows), 4)
            # Future imports link through match_contact / apply_contacts.
            t = {"payee": "WALMART SUPERCENTER #77", "amount": "-3"}
            db.apply_rules([t])
            self.assertEqual(t.get("contact_id"), payee["id"])

    def test_rename_merge_inactive(self):
        for db in self.each_book():
            self.seed(db)
            a, _ = db.create_payee_and_link("Walmart", "walmart")
            b, _ = db.create_payee_and_link("Kroger", "kroger")
            db.save_contact(dict(db.get_contact(a["id"]), name="Wal-Mart"))
            self.assertEqual(db.get_contact(a["id"])["name"], "Wal-Mart")
            moved = db.merge_payees(a["id"], [b["id"]])
            self.assertEqual(moved, 1)
            self.assertEqual(db.get_contact(b["id"])["active"], 0)
            self.assertEqual(next(p for p in db.payee_summary()
                                  if p["id"] == a["id"])["n_txns"], 5)
            db.save_contact(dict(db.get_contact(a["id"]), active="0"))
            self.assertNotIn(a["id"], {p["id"] for p in db.payee_summary()})
            self.assertIn(a["id"], {p["id"] for p in db.payee_summary(include_inactive=True)})


class PayeesPageTests(BothBooksTestCase):
    def test_personal_sidebar_has_payees_and_business_contacts_tab(self):
        from utils import nav_config as NC
        for db in self.each_book():
            bt = db.book_type()
            hit = NC.locate("Payees", bt)
            self.assertIsNotNone(hit)
            if db.is_business():
                self.assertEqual(hit[1], "Contacts")
                from ui.pages.contacts import ContactsPage
                page = ContactsPage(db)
                page.refresh()
                page.show_payees()
                self.assertEqual(page._tabs.tabText(1), "Payees")
                panel = page.payees
            else:
                self.assertEqual(hit[1], "Payees")
                self.assertEqual(NC.locate("Contacts", bt)[1], "Payees")
                labels = [it.label for it in NC.visible_items(bt)]
                self.assertEqual(labels.index("Payees"), labels.index("Transactions") + 1)
                from ui.pages.payees import PayeesPage
                page = PayeesPage(db)
                page.refresh()
                panel = page.panel
            chk = self.checking(db)
            db.save_transaction({"date": "2026-01-02", "account_id": chk,
                                 "payee": "SHELL OIL 5521", "amount": "-40"})
            db.save_transaction({"date": "2026-01-09", "account_id": chk,
                                 "payee": "Shell Oil", "amount": "-35"})
            panel.refresh()
            self.assertEqual(panel.unlinked.rowCount(), 1)
            payee, n = panel.create_and_link(panel._unlinked[0],
                                             confirm=lambda g, txns: "Shell")
            self.assertEqual(n, 2)
            self.assertEqual(panel.table.rowCount(), 1)
            self.assertEqual(panel.table.item(0, 1).text(), "2")
            self.assertEqual(panel.unlinked.rowCount(), 0)


class TransactionDialogPayeeTests(BothBooksTestCase):
    def dialog(self, db, **kw):
        from ui.dialogs.transaction_dialog import TransactionDialog
        return TransactionDialog(db, **kw)

    def test_autocomplete_links_payee_in_both_books(self):
        for db in self.each_book():
            chk = self.checking(db)
            food = self.category(db, "Groceries")
            p = db.add_payee("Trader Joe's")
            dlg = self.dialog(db, account_id=chk)
            names = [dlg._payee_completer.model().index(i, 0).data()
                     for i in range(dlg._payee_completer.model().rowCount())]
            self.assertIn("Trader Joe's", names)
            dlg.payee_edit.setText("trader joe's")
            dlg._on_payee_chosen()
            dlg.amount_spin.setValue(12)
            dlg.cat_combo.select_by_data(food)
            self.assertTrue(dlg._new_payee_chk.isHidden())
            dlg._accept()
            t = db.save_transaction(dlg.get_data())
            self.assertEqual(t["contact_id"], p["id"])

    def test_add_as_new_payee_on_save(self):
        for db in self.each_book():
            chk = self.checking(db)
            dlg = self.dialog(db, account_id=chk)
            dlg.payee_edit.setText("Corner Bakery")
            dlg.amount_spin.setValue(7)
            self.assertFalse(dlg._new_payee_chk.isHidden())
            dlg._new_payee_chk.setChecked(True)
            dlg._accept()
            t = db.save_transaction(dlg.get_data())
            payee = db.find_payee_by_name("Corner Bakery")
            self.assertIsNotNone(payee)
            self.assertEqual(t["contact_id"], payee["id"])

    def test_free_text_payee_still_works(self):
        for db in self.each_book():
            chk = self.checking(db)
            dlg = self.dialog(db, account_id=chk)
            dlg.payee_edit.setText("Someone New")
            dlg.amount_spin.setValue(3)
            dlg._accept()
            t = db.save_transaction(dlg.get_data())
            self.assertEqual((t["payee"], t.get("contact_id") or ""), ("Someone New", ""))
            self.assertIsNone(db.find_payee_by_name("Someone New"))

    def test_recall_last_transaction_fills_only_empty_fields_on_new(self):
        for db in self.each_book():
            chk = self.checking(db)
            food = self.category(db, "Groceries")
            other = self.category(db, "Dining")
            cls = db.save_class({"name": "Home"})["id"] if hasattr(db, "save_class") else ""
            p = db.add_payee("Costco")
            db.save_transaction({"date": "2026-01-05", "account_id": chk, "payee": "Costco",
                                 "contact_id": p["id"], "amount": "-88.5",
                                 "category_id": food, "memo": "monthly run",
                                 "class_id": cls})
            dlg = self.dialog(db, account_id=chk)
            dlg.cat_combo.select_by_data(other)          # the user typed this one
            dlg.payee_edit.setText("Costco")
            dlg._on_payee_chosen()
            self.assertAlmostEqual(dlg.amount_spin.value(), 88.5)
            self.assertEqual(dlg.memo_edit.text(), "monthly run")
            self.assertEqual(dlg.cat_combo.current_data(), other)     # not overwritten
            self.assertFalse(dlg._undo_autofill.isHidden())
            self.assertIn("fff4c2", dlg.amount_spin.styleSheet())
            if cls:
                got = (dlg.charged_combo.current_data() if db.is_business()
                       else dlg.class_combo.currentData())
                self.assertIn(cls, got)
            dlg.undo_autofill()
            self.assertAlmostEqual(dlg.amount_spin.value(), 0.0)
            self.assertEqual(dlg.memo_edit.text(), "")
            self.assertEqual(dlg.cat_combo.current_data(), other)
            self.assertTrue(dlg._undo_autofill.isHidden())

    def test_recall_split_lines_and_never_when_editing(self):
        for db in self.each_book():
            chk = self.checking(db)
            food = self.category(db, "Groceries")
            home = self.category(db, "Household")
            p = db.add_payee("Target")
            base = db.save_transaction({"date": "2026-01-05", "account_id": chk,
                                        "payee": "Target", "contact_id": p["id"],
                                        "amount": "-50"})
            db.split_transaction(base, [{"category_id": food, "amount": "-20"},
                                        {"category_id": home, "amount": "-30"}], signed=True)
            dlg = self.dialog(db, account_id=chk)
            dlg.payee_edit.setText("Target")
            dlg._on_payee_chosen()
            self.assertTrue(dlg._split_chk.isChecked())
            self.assertEqual([round(l["amount"], 2) for l in dlg._split_box.lines()],
                             [20.0, 30.0])
            self.assertTrue(dlg._ok_btn.isEnabled())
            # Editing an existing transaction never autofills.
            plain = db.save_transaction({"date": "2026-01-06", "account_id": chk,
                                         "payee": "x", "amount": "-1"})
            dlg = self.dialog(db, transaction=plain)
            dlg.payee_edit.setText("Target")
            dlg._on_payee_chosen()
            self.assertAlmostEqual(dlg.amount_spin.value(), 1.0)
            self.assertFalse(dlg._split_chk.isChecked())


if __name__ == "__main__":
    unittest.main()
