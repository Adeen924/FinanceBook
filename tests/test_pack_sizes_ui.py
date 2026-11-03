"""Headless (offscreen Qt) tests of units per pack in the receipt review dialog.
Message boxes and the item dialog are mocked so nothing waits for a click."""
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
YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


def amazon() -> dict:
    with open(os.path.join(FIXTURES, "amazon_order_details.txt"), encoding="utf-8") as f:
        return R.parse_receipt_text(f.read())


class PackSizeDialogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.card = self.db.save_account({"name": "Card", "type": "credit card"})
        self.db.set_setting("default_funding_account", self.card["id"])
        self.rc = self.db.add_manual_receipt()
        self.item = self.db.save_item({"name": "Acrylic sheet", "sku": "EGO"})
        self.msgs = []
        self.answer = YES
        for name in ("warning", "information", "critical"):
            p = mock.patch.object(QMessageBox, name, staticmethod(
                lambda *a, n=name, **k: self.msgs.append((n, a[1], a[2])) or
                QMessageBox.StandardButton.Ok))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(QMessageBox, "question", staticmethod(
            lambda *a, **k: self.msgs.append(("question", a[1], a[2])) or self.answer))
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def open(self, parsed=None) -> RD.ReceiptReviewDialog:
        dlg = RD.ReceiptReviewDialog(self.db, self.db.get_receipt(self.rc["id"]), parsed=parsed)
        self.addCleanup(dlg.deleteLater)
        return dlg

    @staticmethod
    def cell(dlg, r, c) -> str:
        return dlg.table.item(r, c).text()

    def set_cell(self, dlg, r, c, text):
        dlg.table.item(r, c).setText(text)        # fires itemChanged like an edit

    def make_inventory(self, dlg, r=0, item=None):
        kind = dlg.table.cellWidget(r, RD.COL_KIND)
        kind.setCurrentIndex(kind.findData("inventory"))
        combo = dlg.table.cellWidget(r, RD.COL_ITEM)
        combo.setCurrentIndex(combo.findData((item or self.item)["id"]))
        combo.activated.emit(combo.currentIndex())

    def test_suggestion_shown_and_live_columns(self):
        dlg = self.open(amazon())
        upp = dlg.table.item(0, RD.COL_UPP)
        self.assertEqual(upp.text(), "6")
        self.assertEqual(upp.background().color(), RD.SUGGESTED_BG)
        self.assertIn("6 Pack", upp.toolTip())
        self.assertFalse(dlg.parse_note.isHidden())
        self.assertIn("Units per pack filled in from the description", dlg.parse_note.text())
        self.assertEqual(self.cell(dlg, 0, RD.COL_UNITS), "6")
        self.assertEqual(self.cell(dlg, 0, RD.COL_UCOST), "$0.998333")     # 5.99 / 6
        # Inventory: landed cost (5.99 + 0.46 tax) / 6.
        self.make_inventory(dlg)
        self.assertEqual(self.cell(dlg, 0, RD.COL_UCOST), "$1.075")
        dlg.spins["shipping"].setValue(1.20)
        self.assertEqual(self.cell(dlg, 0, RD.COL_UCOST), "$1.275")        # (6.45+1.20)/6
        dlg.spins["shipping"].setValue(0)
        # Qty change → units and per-unit cost follow (unit price stays $5.99).
        self.set_cell(dlg, 0, RD.COL_QTY, "2")
        self.assertEqual((self.cell(dlg, 0, RD.COL_UNITS), self.cell(dlg, 0, RD.COL_TOTAL)),
                         ("12", "11.98"))
        # A non-inventory line has its own units too.
        dlg._add_row({"description": "Tape", "qty": 1, "unit_price": 3, "line_total": 3})
        self.set_cell(dlg, 1, RD.COL_UPP, "4")
        self.assertEqual((self.cell(dlg, 1, RD.COL_UNITS), self.cell(dlg, 1, RD.COL_UCOST)),
                         ("4", "$0.75"))

    def test_ambiguous_title_not_applied(self):
        p = amazon()
        p["line_items"][0]["description"] = "USB cables 3 Pack of 10 ft"
        p["line_items"][0]["pack_suggestion"] = R.suggest_pack_size("USB cables 3 Pack of 10 ft")
        dlg = self.open(p)
        upp = dlg.table.item(0, RD.COL_UPP)
        self.assertEqual(upp.text(), "1")
        self.assertIn("ambiguous", upp.toolTip())
        self.assertIn("set Units per pack to 3", dlg.parse_note.text())

    def test_validation(self):
        dlg = self.open(amazon())
        for bad in ("0", "-2", "1.5", "", "x", "²"):
            self.set_cell(dlg, 0, RD.COL_UPP, bad)
            self.assertEqual(self.cell(dlg, 0, RD.COL_UNITS), "—", bad)
            self.assertEqual(dlg.table.item(0, RD.COL_UPP).background().color(), RD.INVALID_BG)
            self.assertIn("Units per pack must be a whole number", dlg.check_lbl.text())
            self.msgs.clear()
            dlg._book()
            self.assertFalse(dlg.booked, bad)
            self.assertEqual(self.msgs[-1][:2], ("warning", "Units per Pack"))
            dlg._save_draft()
            self.assertEqual(self.db.get_receipt_items(self.rc["id"]), [])
        self.set_cell(dlg, 0, RD.COL_UPP, "6")
        self.assertTrue(dlg.check_lbl.text().startswith("✓"), dlg.check_lbl.text())

    def test_draft_restore_book_correct_and_rebook(self):
        dlg = self.open(amazon())
        self.make_inventory(dlg)
        dlg._add_row({"description": "Tape", "qty": 1, "unit_price": 3, "line_total": 3})
        self.set_cell(dlg, 1, RD.COL_UPP, "4")
        dlg.spins["subtotal"].setValue(8.99)
        dlg.spins["total"].setValue(9.45)
        dlg._save_draft()
        saved = self.db.get_receipt_items(self.rc["id"])
        self.assertEqual([(i["description"][:7], i["units_per_pack"]) for i in saved],
                         [("Egofine", 6), ("Tape", 4)])

        again = self.open()
        self.assertEqual([self.cell(again, r, RD.COL_UPP) for r in range(2)], ["6", "4"])
        self.assertEqual([self.cell(again, r, RD.COL_UNITS) for r in range(2)], ["6", "4"])
        again._book()
        self.assertTrue(again.booked)
        moves = self.db.get_moves(self.item["id"])
        self.assertEqual(len(moves), 1)
        from utils.receipt_booking import landed_costs
        expected = landed_costs([{"line_total": "5.99"}, {"line_total": "3.00"}], tax="0.46")[0]
        self.assertEqual(expected, Decimal("6.30"))                 # 5.99 + 0.31 of the tax
        landed = Decimal(str(moves[0]["unit_cost"])) * 6
        self.assertEqual(moves[0]["qty"], 6)
        self.assertEqual(round(landed, 2), expected)
        balance = round(self.db.account_balance(self.card["id"]), 2)
        self.assertEqual(balance, -9.45)
        move_id = moves[0]["id"]

        # Reopen the booked receipt, correct the pack (it was a 12-pack), save.
        fix = self.open()
        self.set_cell(fix, 0, RD.COL_UPP, "12")
        self.assertEqual(self.cell(fix, 0, RD.COL_UNITS), "12")
        fix._save_draft()
        moves = self.db.get_moves(self.item["id"])
        self.assertEqual([(m["id"], m["qty"]) for m in moves], [(move_id, 12)])
        self.assertEqual(round(Decimal(str(moves[0]["unit_cost"])) * 12, 2), round(landed, 2))
        self.assertEqual(round(self.db.account_balance(self.card["id"]), 2), balance)
        self.assertEqual(self.db.get_receipt_items(self.rc["id"])[0]["units_per_pack"], 12)

        # Re-book: still one move, still 12 units, money unchanged.
        rebook = self.open()
        rebook._book()
        moves = self.db.get_moves(self.item["id"])
        self.assertEqual([(m["id"], m["qty"]) for m in moves], [(move_id, 12)])
        self.assertEqual(round(self.db.account_balance(self.card["id"]), 2), balance)

    def test_item_default_prefill_and_save(self):
        self.db.set_item_pack_size(self.item["id"], 6)
        p = amazon()
        p["line_items"][0]["pack_suggestion"] = None
        dlg = self.open(p)
        self.assertEqual(self.cell(dlg, 0, RD.COL_UPP), "1")
        self.make_inventory(dlg)
        self.assertEqual(self.cell(dlg, 0, RD.COL_UPP), "6")          # item default
        self.assertEqual([m for m in self.msgs if m[0] == "question"], [])
        self.answer = NO
        self.set_cell(dlg, 0, RD.COL_UPP, "8")
        self.assertEqual(self.msgs[-1][1], "Default Pack Size")
        self.assertEqual(self.db.get_item(self.item["id"])["pack_size"], 6)
        n = len(self.msgs)
        self.set_cell(dlg, 0, RD.COL_UPP, "8")                        # declined: not asked again
        self.assertEqual(len(self.msgs), n)
        self.answer = YES
        self.set_cell(dlg, 0, RD.COL_UPP, "10")
        self.assertEqual(self.db.get_item(self.item["id"])["pack_size"], 10)
        self.assertEqual(self.cell(dlg, 0, RD.COL_UPP), "10")         # the line keeps its value

    def test_new_item_gets_line_pack_size(self):
        dlg = self.open(amazon())
        kind = dlg.table.cellWidget(0, RD.COL_KIND)
        kind.setCurrentIndex(kind.findData("inventory"))
        seen = {}

        def fake_exec(item_dlg):
            seen["pack"] = item_dlg.pack.value()
            return QDialog.DialogCode.Accepted
        combo = dlg.table.cellWidget(0, RD.COL_ITEM)
        with mock.patch.object(INV.ItemDialog, "exec", fake_exec):
            combo.setCurrentIndex(combo.findData(RD.NEW_ITEM))
            combo.activated.emit(combo.currentIndex())
        self.assertEqual(seen["pack"], 6)
        new = next(i for i in self.db.get_items() if i["id"] != self.item["id"])
        self.assertEqual(new["pack_size"], 6)

    def test_personal_book_shows_units_columns(self):
        db = Database(os.path.join(self.dir, "personal.db"))
        try:
            rc = db.add_manual_receipt()
            dlg = RD.ReceiptReviewDialog(db, rc, parsed=amazon())
            self.addCleanup(dlg.deleteLater)
            for col in (RD.COL_UPP, RD.COL_UNITS, RD.COL_UCOST):
                self.assertFalse(dlg.table.isColumnHidden(col))
            self.assertEqual(dlg.table.item(0, RD.COL_UNITS).text(), "6")
            dlg._save_draft()
            self.assertEqual(db.get_receipt_items(rc["id"])[0]["units_per_pack"], 6)
        finally:
            db.close()

    def test_headers_not_clipped(self):
        dlg = self.open(amazon())
        hh = dlg.table.horizontalHeader()
        for col in range(dlg.table.columnCount()):
            if col == RD.COL_DESC or dlg.table.isColumnHidden(col):
                continue
            self.assertGreaterEqual(hh.sectionSize(col), hh.sectionSizeHint(col),
                                    RD.HEADERS[col])


if __name__ == "__main__":
    unittest.main()
