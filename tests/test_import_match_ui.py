"""v1.17 Import page "Match" column, end to end with the Wells Fargo fixtures:
the checking file's −400.00 "ONLINE TRANSFER … TO VISA SIGNATURE CARD" and the
card file's +400.00 "ONLINE PAYMENT THANK YOU" become one linked transfer; a
hand-typed −565.00 rent entry absorbs the bank's row; unticking / "Don't match"
imports a plain new row; several candidates give a dropdown; re-importing adds
nothing."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox, QCheckBox, QComboBox
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover — no Qt available
    raise unittest.SkipTest(f"PyQt6 not available: {e}")

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "bank_csv")
ARTIFACTS = os.path.join(os.path.dirname(__file__), "artifacts")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


class MatchColumnTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "i.db"))
        self.chk = self.db.save_account({"name": "WF Checking", "type": "checking"})["id"]
        self.card = self.db.save_account({"name": "WF Visa", "type": "credit card"})["id"]
        self.rent = self.db.save_category({"name": "Rent", "type": "expense"})["id"]
        self.messages = []

        def box(*a, **k):
            self.messages.append(a[2] if len(a) > 2 else "")
            return QMessageBox.StandardButton.Ok
        boxes = {n: staticmethod(box) for n in ("information", "warning", "critical")}
        self._patch = mock.patch.multiple(QMessageBox, **boxes)
        self._patch.start()
        from ui.pages.import_page import ImportPage
        self.page = ImportPage(self.db)
        self.page.resize(1200, 600)
        self.page.refresh()

    def tearDown(self):
        self._patch.stop()
        self.page.deleteLater()
        shutil.rmtree(self.dir, ignore_errors=True)

    # ── helpers ──
    def load(self, account_id, name):
        p = self.page
        p._acct_combo.setCurrentIndex(p._acct_combo.findData(account_id))
        p.set_file(fixture(name), name)
        p._parse()

    def import_file(self, account_id, name):
        self.load(account_id, name)
        self.page._import()

    def match_cells(self):
        """{payee: (row, widget or item text)} for every row with a Match cell."""
        from ui.pages.import_page import MATCH_COL
        t = self.page._table
        out = {}
        for r in range(t.rowCount()):
            w = t.cellWidget(r, MATCH_COL)
            payee = t.item(r, 1).text() if t.item(r, 1) else ""
            if w is not None:
                inner = w.findChild(QCheckBox) or w.findChild(QComboBox)
                out[payee] = inner
            elif t.item(r, MATCH_COL) is not None and t.item(r, MATCH_COL).text():
                out[payee] = t.item(r, MATCH_COL).text()
        return out

    def section_headers(self):
        t = self.page._table
        return [t.item(r, 0).text().strip() for r in range(t.rowCount())
                if t.columnSpan(r, 0) > 1 and t.item(r, 0)]

    def txns(self, account):
        return self.db.get_transactions(account_id=account)

    def grab(self, name):
        try:
            os.makedirs(ARTIFACTS, exist_ok=True)
            self.page.grab().save(os.path.join(ARTIFACTS, name))
        except Exception:
            pass

    # ── tests ──
    def test_match_column_present(self):
        t = self.page._table
        headers = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
        self.assertEqual(headers[-1].lower(), "match")      # DataTable shows headers in caps

    def test_wells_fargo_transfer_pair_end_to_end(self):
        self.import_file(self.chk, "wellsfargo_checking.csv")
        self.assertEqual(len(self.txns(self.chk)), 3)
        self.load(self.card, "wellsfargo_credit.csv")
        cells = self.match_cells()
        box = cells["ONLINE PAYMENT THANK YOU"]
        self.assertIsInstance(box, QCheckBox)
        self.assertTrue(box.isChecked())
        self.assertTrue(box.text().startswith("⇄ Transfer · WF Checking 11/05/2026"))
        self.assertIn("ONLINE TRANSFER", box.toolTip())
        self.assertNotIn("INTEREST CHARGE ON PURCHASES", cells)
        self.assertTrue(any(h.startswith("MATCHED") for h in self.section_headers()))
        self.assertIn("1 new · 1 matched", self.page._status_label.text())
        self.grab("import_match_wells_fargo.png")
        self.page._import()
        self.assertIn("1 linked as transfers", self.messages[-1])

        card = {t["payee"]: t for t in self.txns(self.card)}
        chk = {t["payee"][:15]: t for t in self.txns(self.chk)}
        pay, xfer = card["ONLINE PAYMENT THANK YOU"], chk["ONLINE TRANSFER"]
        self.assertEqual((pay["is_transfer"], xfer["is_transfer"]), ("1", "1"))
        self.assertEqual(pay["transfer_pair_id"], xfer["transfer_pair_id"])
        self.assertTrue(pay["transfer_pair_id"])
        tcat = self.db.get_transfer_category_id()
        self.assertEqual((pay["category_id"], xfer["category_id"]), (tcat, tcat))
        # Everything else untouched.
        self.assertEqual(card["INTEREST CHARGE ON PURCHASES"]["is_transfer"], "0")
        others = [t for t in self.txns(self.chk) if t["id"] != xfer["id"]]
        self.assertTrue(all(t["is_transfer"] == "0" and not t["transfer_pair_id"]
                            for t in others))
        # Reports leave the pair out.
        from utils.reports import pnl_rows
        cats = {c["id"]: c for c in self.db.get_categories()}
        rows = pnl_rows(self.db.get_transactions(), cats)
        self.assertFalse(any(abs(abs(float(r.get("amount", 0))) - 400) < 0.005 for r in rows))

        # Re-importing either file adds nothing and links nothing more.
        pairs_before = {t["transfer_pair_id"] for t in self.db.get_transactions()
                        if t["transfer_pair_id"]}
        for acct, name in ((self.chk, "wellsfargo_checking.csv"),
                           (self.card, "wellsfargo_credit.csv")):
            self.load(acct, name)
            self.assertEqual(self.page._parsed, [])
            self.assertIn("duplicates skipped", self.page._status_label.text())
            self.page._import()
        self.assertEqual((len(self.txns(self.chk)), len(self.txns(self.card))), (3, 2))
        pairs_after = {t["transfer_pair_id"] for t in self.db.get_transactions()
                       if t["transfer_pair_id"]}
        self.assertEqual(pairs_before, pairs_after)

        # Transfers → Linked Transfers lists it; Unlink splits it.
        from ui.pages.transfers import TransfersPage
        tp = TransfersPage(self.db)
        tp.refresh()
        self.assertEqual({t["id"] for t in tp._linked}, {pay["id"], xfer["id"]})
        self.db.unlink_transfer(pay["id"])
        self.assertEqual(self.db.get_transaction(xfer["id"])["is_transfer"], "0")
        tp.deleteLater()

    def test_card_first_then_checking_also_links(self):
        self.import_file(self.card, "wellsfargo_credit.csv")
        self.load(self.chk, "wellsfargo_checking.csv")
        box = self.match_cells()[
            next(k for k in self.match_cells() if k.startswith("ONLINE TRANSFER"))]
        self.assertTrue(box.text().startswith("⇄ Transfer · WF Visa 11/04/2026"))
        self.page._import()
        self.assertEqual(sum(t["is_transfer"] == "1" for t in self.db.get_transactions()), 2)

    def test_unticking_imports_as_new(self):
        self.import_file(self.chk, "wellsfargo_checking.csv")
        self.load(self.card, "wellsfargo_credit.csv")
        box = self.match_cells()["ONLINE PAYMENT THANK YOU"]
        box.setChecked(False)
        self.assertIn("2 new · 0 matched", self.page._status_label.text())
        self.assertNotIn("_match", self.page.import_rows()[0])
        self.page._import()
        self.assertFalse(any(t["is_transfer"] == "1" for t in self.db.get_transactions()))
        self.assertEqual(len(self.txns(self.card)), 2)

    def test_manual_entry_merges(self):
        typed = self.db.save_transaction({"date": "2026-11-04", "account_id": self.chk,
                                          "payee": "Rent", "amount": "-565",
                                          "category_id": self.rent})
        self.load(self.chk, "wellsfargo_checking.csv")
        cells = self.match_cells()
        key = next(k for k in cells if k.startswith("ZELLE TO"))
        self.assertEqual(cells[key].text(), "✓ Matches your entry · 11/04/2026")
        self.assertTrue(cells[key].isChecked())
        self.page._import()
        rent = [t for t in self.txns(self.chk) if abs(float(t["amount"]) + 565) < 0.005]
        self.assertEqual(len(rent), 1)
        self.assertEqual(rent[0]["id"], typed["id"])
        self.assertEqual((rent[0]["category_id"], rent[0]["payee"]), (self.rent, "Rent"))
        self.assertEqual(rent[0]["date"], "2026-11-05")
        self.assertIn("1 merged into your entries", self.messages[-1])
        # Re-import: still one.
        self.import_file(self.chk, "wellsfargo_checking.csv")
        self.assertEqual(len(self.txns(self.chk)), 3)

    def test_several_candidates_dropdown(self):
        a = self.db.save_transaction({"date": "2026-11-03", "account_id": self.chk,
                                      "payee": "Rent (early)", "amount": "-565"})
        b = self.db.save_transaction({"date": "2026-11-06", "account_id": self.chk,
                                      "payee": "Rent", "amount": "-565"})
        self.load(self.chk, "wellsfargo_checking.csv")
        cells = self.match_cells()
        combo = cells[next(k for k in cells if k.startswith("ZELLE TO"))]
        self.assertIsInstance(combo, QComboBox)
        self.assertEqual(combo.currentIndex(), -1)
        self.assertEqual(combo.placeholderText(), "2 possible matches")
        items = [combo.itemText(i) for i in range(combo.count())]
        self.assertEqual(items, ["✓ Matches your entry · 11/06/2026",
                                 "✓ Matches your entry · 11/03/2026", "Don't match"])
        self.assertIn("Rent (early)", combo.itemData(1, 3))      # ToolTipRole
        self.assertIn("3 new · 0 matched", self.page._status_label.text())
        self.grab("import_match_dropdown.png")
        # Pick the second one.
        combo.setCurrentIndex(1)
        combo.activated.emit(1)
        self.assertIn("2 new · 1 matched", self.page._status_label.text())
        self.page._import()
        self.assertEqual(self.db.get_transaction(a["id"])["date"], "2026-11-05")
        self.assertEqual(self.db.get_transaction(b["id"])["import_hash"], "")
        self.assertEqual(len(self.txns(self.chk)), 4)

    def test_dont_match_imports_as_new(self):
        for d in ("2026-11-03", "2026-11-06"):
            self.db.save_transaction({"date": d, "account_id": self.chk,
                                      "payee": "Rent", "amount": "-565"})
        self.load(self.chk, "wellsfargo_checking.csv")
        cells = self.match_cells()
        combo = cells[next(k for k in cells if k.startswith("ZELLE TO"))]
        combo.setCurrentIndex(0)
        combo.activated.emit(0)
        last = combo.count() - 1
        combo.setCurrentIndex(last)
        combo.activated.emit(last)
        self.assertIn("3 new · 0 matched", self.page._status_label.text())
        self.page._import()
        self.assertEqual(len(self.txns(self.chk)), 5)
        self.assertEqual(sum(1 for t in self.txns(self.chk) if t["import_hash"]), 3)

    def test_choice_claims_candidate_one_to_one(self):
        self.load(self.chk, "wellsfargo_checking.csv")
        p = self.page
        # Simulate two rows offered the same candidate: picking it for one
        # releases it from the other.
        p._choice = {0: "X", 1: ""}
        p._set_choice(1, "X")
        self.assertEqual(p._choice, {0: "", 1: "X"})


if __name__ == "__main__":
    unittest.main()
