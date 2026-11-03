"""Headless (offscreen Qt) tests of the receipt review dialog: shipping fields,
inventory item picker, booking and re-booking, and background parsing on the
Receipts page. Message boxes and the item dialog are mocked."""
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from parsers import receipt as R
from sheets.client import Database

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog
    from PyQt6.QtCore import Qt
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover — no Qt available
    raise unittest.SkipTest(f"PyQt6 not available: {e}")

from ui.dialogs import receipt_dialog as RD
from ui.pages import inventory as INV

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
YES = QMessageBox.StandardButton.Yes


def digikey() -> dict:
    with open(os.path.join(FIXTURES, "digikey_invoice.txt"), encoding="utf-8") as f:
        return R.parse_receipt_text(f.read())


class ReviewDialogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.card = self.db.save_account({"name": "Biz Card", "type": "credit card"})
        self.db.set_setting("default_funding_account", self.card["id"])
        self.rc = self.db.add_manual_receipt()
        self.msgs = []
        for name in ("warning", "information", "critical"):
            p = mock.patch.object(QMessageBox, name, staticmethod(
                lambda *a, n=name, **k: self.msgs.append((n, a[1], a[2])) or
                QMessageBox.StandardButton.Ok))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(QMessageBox, "question", staticmethod(
            lambda *a, **k: self.msgs.append(("question", a[1], a[2])) or YES))
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def open(self, parsed=None) -> RD.ReceiptReviewDialog:
        rc = self.db.get_receipt(self.rc["id"])
        dlg = RD.ReceiptReviewDialog(self.db, rc, parsed=parsed)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def set_kind(self, dlg, row, kind):
        combo = dlg.table.cellWidget(row, RD.COL_KIND)
        combo.setCurrentIndex(combo.findData(kind))

    def test_digikey_populates_and_balances(self):
        dlg = self.open(digikey())
        self.assertEqual(dlg.vendor_edit.text(), "DigiKey")
        self.assertEqual(dlg.date_edit.date().toString("yyyy-MM-dd"), "2026-10-13")
        self.assertEqual(dlg.notes_edit.text(), "Order # 900000001")
        self.assertEqual({k: round(sp.value(), 2) for k, sp in dlg.spins.items()},
                         {"subtotal": 33.68, "shipping": 13.99, "discount": 0.0, "duties": 0.0,
                          "tax": 2.61, "tip": 0.0, "total": 50.28})
        it = dlg.items()[0]
        self.assertEqual((it["description"], it["qty"], it["unit_price"], it["line_total"],
                          it["sku"]),
                         ("RASPBERRY PI PICO 2 W RP2350", 4.0, 8.42, 33.68, "2648-SC1633CT-ND"))
        self.assertTrue(dlg.check_lbl.text().startswith("✓"), dlg.check_lbl.text())
        dlg.spins["shipping"].setValue(10)
        self.assertTrue(dlg.check_lbl.text().startswith("⚠"))
        self.assertIn("Items + shipping + tax = $46.29", dlg.check_lbl.text())

    def test_inventory_line_needs_an_item(self):
        dlg = self.open(digikey())
        item_cell = dlg.table.cellWidget(0, RD.COL_ITEM)
        self.assertTrue(item_cell.isHidden())
        self.set_kind(dlg, 0, "inventory")
        self.assertFalse(item_cell.isHidden())
        self.assertIn("Choose an inventory item", dlg.check_lbl.text())
        dlg._book()
        self.assertFalse(dlg.booked)
        self.assertEqual(self.msgs[-1][1], "Choose Inventory Items")
        self.assertIn("RASPBERRY PI PICO 2 W RP2350", self.msgs[-1][2])

    def test_inventory_category_sets_type(self):
        dlg = self.open(digikey())
        inv = self.db.inventory_category_id()
        dlg = self.open(digikey())          # reopened so the new category is listed
        dlg.table.cellWidget(0, RD.COL_CAT).select_by_data(inv)
        self.assertEqual(dlg.table.cellWidget(0, RD.COL_KIND).currentData(), "inventory")

    def create_item_via_picker(self, dlg, row=0):
        seen = {}

        def fake_exec(item_dlg):
            seen.update(name=item_dlg.name.text(), sku=item_dlg.sku.text(),
                        title=item_dlg.windowTitle())
            return QDialog.DialogCode.Accepted
        combo = dlg.table.cellWidget(row, RD.COL_ITEM)
        with mock.patch.object(INV.ItemDialog, "exec", fake_exec):
            combo.setCurrentIndex(combo.findData(RD.NEW_ITEM))
            combo.activated.emit(combo.currentIndex())
        return seen

    def test_new_item_prefilled_with_sku_then_book_and_rebook(self):
        dlg = self.open(digikey())
        self.set_kind(dlg, 0, "inventory")
        seen = self.create_item_via_picker(dlg)
        self.assertEqual(seen, {"name": "RASPBERRY PI PICO 2 W RP2350",
                                "sku": "2648-SC1633CT-ND", "title": "Add Item"})
        item = self.db.get_items()[0]
        self.assertEqual(dlg.items()[0]["item_id"], item["id"])
        self.assertTrue(dlg.check_lbl.text().startswith("✓"), dlg.check_lbl.text())
        dlg._book()
        self.assertTrue(dlg.booked)

        txns = [t for t in self.db.get_transactions() if t["account_id"] == self.card["id"]]
        self.assertEqual([(t["category_id"], float(t["amount"])) for t in txns],
                         [(self.db.inventory_category_id(), -50.28)])
        self.assertIn("Order # 900000001", txns[0]["notes"])
        moves = self.db.get_moves(item["id"])
        self.assertEqual(len(moves), 1)
        self.assertEqual((moves[0]["qty"], round(moves[0]["unit_cost"], 2), moves[0]["receipt_id"]),
                         (4, 12.57, self.rc["id"]))       # (33.68 + 13.99 + 2.61) / 4
        saved = self.db.get_receipt_items(self.rc["id"])[0]
        self.assertEqual((saved["item_id"], saved["sku"]), (item["id"], "2648-SC1633CT-ND"))

        # Re-book the same receipt: replaces the transaction and the stock.
        again = self.open()
        self.assertEqual(again.items()[0]["item_id"], item["id"])
        again._book()
        self.assertTrue(again.booked)
        self.assertIn("Already Booked", [m[1] for m in self.msgs])
        self.assertEqual(len(self.db.get_moves(item["id"])), 1)
        self.assertEqual(round(self.db.account_balance(self.card["id"]), 2), -50.28)
        self.assertEqual(self.db.item_status(item)["qty"], Decimal(4))

    def test_rebook_as_expense_removes_received_stock(self):
        item = self.db.save_item({"name": "Pico", "sku": "X1"})
        dlg = self.open(digikey())
        self.set_kind(dlg, 0, "inventory")
        combo = dlg.table.cellWidget(0, RD.COL_ITEM)
        combo.setCurrentIndex(combo.findData(item["id"]))
        dlg._book()
        self.assertEqual(len(self.db.get_moves(item["id"])), 1)
        again = self.open()
        self.set_kind(again, 0, "expense")
        again._book()
        self.assertTrue(again.booked)
        self.assertEqual(self.db.get_moves(item["id"]), [])
        self.assertEqual(round(self.db.account_balance(self.card["id"]), 2), -50.28)

    def test_separate_mode_books_shipping_line_to_shipping_category(self):
        dlg = self.open(digikey())
        sup = next(c["id"] for c in self.db.get_categories() if c["name"] == "Supplies")
        dlg.table.cellWidget(0, RD.COL_CAT).select_by_data(sup)
        dlg.tax_mode.setCurrentIndex(dlg.tax_mode.findData("separate"))
        ship = next(c["id"] for c in self.db.get_categories() if c["name"] == "Shipping")
        self.assertEqual(dlg.ship_cat.current_data(), ship)        # business default
        dlg._book()
        txns = [t for t in self.db.get_transactions() if t["account_id"] == self.card["id"]]
        by_cat = {t["category_id"]: round(float(t["amount"]), 2) for t in txns}
        self.assertEqual(by_cat[sup], -33.68)
        self.assertEqual(by_cat[ship], -13.99)
        self.assertEqual(round(sum(by_cat.values()), 2), -50.28)

    def test_draft_keeps_item_choice(self):
        item = self.db.save_item({"name": "Pico", "sku": "X1"})
        dlg = self.open(digikey())
        self.set_kind(dlg, 0, "inventory")
        combo = dlg.table.cellWidget(0, RD.COL_ITEM)
        combo.setCurrentIndex(combo.findData(item["id"]))
        dlg._save_draft()
        reopened = self.open()
        self.assertEqual(reopened.items()[0]["item_id"], item["id"])
        self.assertEqual(reopened.items()[0]["kind"], "inventory")
        self.assertEqual(round(reopened.spins["shipping"].value(), 2), 13.99)

    def test_works_with_no_items_yet_and_ai_note(self):
        dlg = self.open(dict(digikey(), date_note="Date is ambiguous (08/10/2026) — check it."))
        combo = dlg.table.cellWidget(0, RD.COL_ITEM)
        self.assertEqual([combo.itemData(i) for i in range(combo.count())], ["", RD.NEW_ITEM])
        self.assertFalse(dlg.parse_note.isHidden())
        self.assertIn("ambiguous", dlg.parse_note.text())


class PersonalLineCategoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "conv.db"))
        self.db.set_book_type("business")
        self.rc = self.db.add_manual_receipt()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_personal_line_gets_personal_categories(self):
        dlg = RD.ReceiptReviewDialog(self.db, self.rc, parsed=digikey())
        self.addCleanup(dlg.deleteLater)
        groceries = next(c["id"] for c in self.db.get_categories() if c["name"] == "Groceries")
        cat = dlg.table.cellWidget(0, RD.COL_CAT)
        self.assertEqual(cat.findData(groceries), -1)
        dlg.table.cellWidget(0, RD.COL_BIZ)._chk.setChecked(False)
        self.assertGreaterEqual(dlg.table.cellWidget(0, RD.COL_CAT).findData(groceries), 0)


class ReceiptsPageParseTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_add_file_parses_off_the_ui_thread(self):
        from ui.pages.receipts import ReceiptsPage
        page = ReceiptsPage(self.db)
        self.addCleanup(page.deleteLater)
        opened = []
        with mock.patch.object(ReceiptsPage, "_review", lambda self, rc, parsed=None:
                               opened.append((rc, parsed))):
            page.add_files([os.path.join(FIXTURES, "digikey_like_winansi.pdf")])
            self.assertIsNotNone(page.last_worker)
            self.assertTrue(page.last_worker.wait(30000))
            for _ in range(20):
                QApplication.processEvents()
                if opened:
                    break
        self.assertEqual(len(opened), 1)
        rc, parsed = opened[0]
        self.assertEqual((rc["vendor"], rc["receipt_date"]), ("DigiKey", "2026-02-03"))
        self.assertEqual((rc["shipping"], rc["tax"], rc["total"]), (6.99, 1.2, 16.69))
        self.assertEqual(rc["notes"], "Order # 90000001")
        self.assertEqual(parsed["line_items"][0]["sku"], "123-FAKE01-ND")


if __name__ == "__main__":
    unittest.main()
