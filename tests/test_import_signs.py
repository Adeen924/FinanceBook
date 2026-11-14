"""v1.15 money direction: an imported transaction keeps its sign when an account
is picked in the Transaction dialog (Money out / Money in selector), CSV/XLSX
Debit/Credit columns are read as magnitudes, a split or receipt never flips an
existing entry, and the one-time review finds imported entries whose sign
disagrees with their account type."""
import io
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database

try:
    from PyQt6.QtWidgets import QApplication, QDialog
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover — no Qt available
    raise unittest.SkipTest(f"PyQt6 not available: {e}")


def _cat(db, name):
    return next(c for c in db.get_categories() if c["name"] == name)["id"]


class _DbCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "s.db"))
        self.chk = self.db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.sav = self.db.save_account({"name": "Savings", "type": "savings"})["id"]
        self.gas = _cat(self.db, "Gas")
        self.food = _cat(self.db, "Groceries")
        self.salary = _cat(self.db, "Salary")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def imported(self, amount, category_id="", h="h1", date="2026-03-01"):
        return self.db.save_transaction({"date": date, "account_id": self.chk,
                                         "payee": "BANK LINE", "amount": amount,
                                         "category_id": category_id, "import_hash": h})


class DialogDirectionTests(_DbCase):
    def dialog(self, txn=None):
        from ui.dialogs.transaction_dialog import TransactionDialog
        dlg = TransactionDialog(self.db, transaction=txn, account_id=self.chk)
        dlg.accept = lambda: QDialog.accept(dlg)
        return dlg

    def test_uncategorized_imported_withdrawal_keeps_its_sign(self):
        t = self.imported("-50.00")
        dlg = self.dialog(self.db.get_transaction(t["id"]))
        self.assertEqual(dlg.amount_spin.value(), 50)
        self.assertEqual(dlg.direction_combo.currentData(), "out")
        dlg.cat_combo.select_by_data(self.gas)
        self.assertEqual(dlg.get_data()["amount"], "-50.0")
        # Picking an income account doesn't turn it into a deposit either.
        dlg.cat_combo.select_by_data(self.salary)
        self.assertEqual(dlg.get_data()["amount"], "-50.0")

    def test_uncategorized_imported_deposit_keeps_its_sign(self):
        t = self.imported("200")
        dlg = self.dialog(self.db.get_transaction(t["id"]))
        self.assertEqual(dlg.direction_combo.currentData(), "in")
        dlg.cat_combo.select_by_data(self.salary)
        self.assertEqual(dlg.get_data()["amount"], "200.0")
        dlg.cat_combo.select_by_data(self.food)        # e.g. a store refund
        self.assertEqual(dlg.get_data()["amount"], "200.0")

    def test_categorized_withdrawal_round_trip(self):
        t = self.imported("-30", self.gas)
        dlg = self.dialog(self.db.get_transaction(t["id"]))
        self.assertEqual((dlg.amount_spin.value(), dlg.direction_combo.currentData()),
                         (30, "out"))
        self.assertEqual(dlg.get_data()["amount"], "-30.0")
        dlg.cat_combo.select_by_data(self.food)
        self.assertEqual(dlg.get_data()["amount"], "-30.0")

    def test_explicit_direction_change_is_saved(self):
        t = self.imported("-50")
        dlg = self.dialog(self.db.get_transaction(t["id"]))
        dlg.cat_combo.select_by_data(self.gas)
        dlg.direction_combo.setCurrentIndex(dlg.direction_combo.findData("in"))
        self.assertEqual(dlg.get_data()["amount"], "50.0")

    def test_refund(self):
        # New entry: a negative amount on an expense account is a refund (money in).
        dlg = self.dialog()
        dlg.cat_combo.select_by_data(self.food)
        dlg.amount_spin.setValue(-20)
        self.assertEqual(dlg.get_data()["amount"], "20.0")
        # Leaving the field tidies it: positive amount, Money in.
        dlg.amount_spin.editingFinished.emit()
        self.assertEqual((dlg.amount_spin.value(), dlg.direction_combo.currentData()),
                         (20, "in"))
        self.assertEqual(dlg.get_data()["amount"], "20.0")
        # An existing refund re-opens as Money in and saves unchanged.
        t = self.imported("20", self.food, h="r1")
        dlg = self.dialog(self.db.get_transaction(t["id"]))
        self.assertEqual(dlg.direction_combo.currentData(), "in")
        self.assertEqual(dlg.get_data()["amount"], "20.0")

    def test_new_entry_follows_the_accounts_natural_direction(self):
        dlg = self.dialog()
        dlg.amount_spin.setValue(40)
        dlg.cat_combo.select_by_data(self.gas)
        self.assertEqual(dlg.direction_combo.currentData(), "out")
        self.assertEqual(dlg.get_data()["amount"], "-40.0")
        dlg.cat_combo.select_by_data(self.salary)
        self.assertEqual(dlg.direction_combo.currentData(), "in")
        self.assertEqual(dlg.get_data()["amount"], "40.0")
        # Once the user picks a direction, changing the account leaves it alone.
        dlg.direction_combo.setCurrentIndex(dlg.direction_combo.findData("out"))
        dlg.direction_combo.activated.emit(dlg.direction_combo.currentIndex())
        dlg.cat_combo.select_by_data(self.salary)
        dlg.cat_combo.select_by_data(self.gas)
        dlg.cat_combo.select_by_data(self.salary)
        self.assertEqual(dlg.get_data()["amount"], "-40.0")

    def test_transfer_round_trip(self):
        dlg = self.dialog()
        dlg.amount_spin.setValue(125)
        dlg.cat_combo.select_by_data("acct:" + self.sav)
        self.assertEqual(dlg.direction_combo.currentData(), "out")
        dlg._accept()
        sav = self.db.get_transactions(account_id=self.sav)
        self.assertEqual([float(t["amount"]) for t in sav], [125])
        dlg2 = self.dialog(sav[0])
        self.assertEqual((dlg2.amount_spin.value(), dlg2.direction_combo.currentData()),
                         (125, "in"))
        dlg2.amount_spin.setValue(150)
        dlg2._accept()
        self.assertEqual([float(t["amount"]) for t in
                          self.db.get_transactions(account_id=self.chk)], [-150])
        self.assertEqual([float(t["amount"]) for t in
                          self.db.get_transactions(account_id=self.sav)], [150])

    def test_imported_deposit_made_a_transfer_keeps_direction(self):
        t = self.imported("300")
        dlg = self.dialog(self.db.get_transaction(t["id"]))
        dlg.cat_combo.select_by_data("acct:" + self.sav)
        dlg._accept()
        self.assertEqual(float(self.db.get_transaction(t["id"])["amount"]), 300)
        self.assertEqual([float(x["amount"]) for x in
                          self.db.get_transactions(account_id=self.sav)], [-300])

    def test_loan_payment_amount_is_money_out(self):
        loan = self.db.save_loan({"name": "Car", "original_principal": "10000",
                                  "annual_rate": "6", "term_months": "48",
                                  "start_date": "2026-01-01", "payment_amount": "235",
                                  "interest_category_id": self.gas})
        t = self.imported("-235")
        dlg = self.dialog(self.db.get_transaction(t["id"]))
        dlg._loan_chk.setChecked(True)
        dlg._loan_combo.setCurrentIndex(dlg._loan_combo.findData(loan["id"]))
        data = dlg.get_data()
        self.assertEqual(data["amount"], "-235.0")
        self.assertEqual(data["loan_id"], loan["id"])


class SplitAndReceiptDirectionTests(_DbCase):
    def test_splitting_an_imported_deposit_keeps_it_money_in(self):
        t = self.imported("50")
        lines = self.db.split_transaction(
            self.db.get_transaction(t["id"]),
            [{"category_id": self.food, "amount": "30"},
             {"category_id": self.gas, "amount": "20"}])
        self.assertAlmostEqual(sum(float(x["amount"]) for x in lines), 50)
        self.assertTrue(all(float(x["amount"]) > 0 for x in lines))

    def test_splitting_a_withdrawal_and_a_paycheck_still_work(self):
        t = self.imported("-50")
        lines = self.db.split_transaction(
            self.db.get_transaction(t["id"]),
            [{"category_id": self.food, "amount": "30"},
             {"category_id": self.gas, "amount": "20"}])
        self.assertEqual(sorted(float(x["amount"]) for x in lines), [-30, -20])
        pay = self.imported("800", h="p1")
        tax = _cat(self.db, "Gas")
        lines = self.db.split_transaction(
            self.db.get_transaction(pay["id"]),
            [{"category_id": self.salary, "amount": "1000"},
             {"category_id": tax, "amount": "200"}])
        self.assertEqual(sorted(float(x["amount"]) for x in lines), [-200, 1000])


class SpreadsheetSignTests(unittest.TestCase):
    def parse(self, text, name="bank.csv"):
        from parsers.spreadsheet import parse_spreadsheet
        rows, _w = parse_spreadsheet(text.encode(), name, "CHK")
        return [float(r["amount"]) for r in rows]

    def test_negative_debits(self):
        self.assertEqual(self.parse("Date,Description,Debit,Credit\n"
                                    "01/05/2026,COFFEE,-50.00,\n"
                                    "01/06/2026,PAYROLL,,1000.00\n"), [-50, 1000])

    def test_positive_debits(self):
        self.assertEqual(self.parse("Date,Description,Withdrawal,Deposit\n"
                                    "01/05/2026,COFFEE,50.00,\n"
                                    "01/06/2026,PAYROLL,,\"1,000.00\"\n"), [-50, 1000])

    def test_parentheses(self):
        self.assertEqual(self.parse("Date,Description,Debit,Credit\n"
                                    "01/05/2026,COFFEE,(50.00),\n"
                                    "01/06/2026,REFUND,,(25.00)\n"), [-50, 25])

    def test_single_amount_column_unchanged(self):
        self.assertEqual(self.parse("Date,Description,Amount\n"
                                    "01/05/2026,COFFEE,-50.00\n"
                                    "01/06/2026,PAYROLL,1000\n"), [-50, 1000])

    def test_reverse_signs_option(self):
        from parsers.spreadsheet import parse_spreadsheet
        rows, _w = parse_spreadsheet(b"Date,Description,Amount\n01/05/2026,STORE,42.10\n",
                                     "card.csv", "CC", reverse_signs=True)
        self.assertEqual(float(rows[0]["amount"]), -42.10)

    def test_xlsx_negative_debits(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Date", "Description", "Debit", "Credit"])
        ws.append(["01/05/2026", "COFFEE", "-50.00", None])
        ws.append(["01/06/2026", "PAYROLL", None, "1000"])
        buf = io.BytesIO()
        wb.save(buf)
        self.assertEqual(self.parse_bytes(buf.getvalue(), "bank.xlsx"), [-50, 1000])

    def parse_bytes(self, data, name):
        from parsers.spreadsheet import parse_spreadsheet
        rows, _w = parse_spreadsheet(data, name, "CHK")
        return [float(r["amount"]) for r in rows]

    def test_qb_detail_negative_debits(self):
        import openpyxl
        from parsers.spreadsheet import parse_qb_multi_account_excel
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["", "Date", "Name", "Memo", "Split", "Debit", "Credit"])
        ws.append(["Checking", None, None, None, None, None, None])
        ws.append(["", "01/05/2026", "COFFEE", "", "Meals", "-50.00", None])
        ws.append(["", "01/06/2026", "CLIENT", "", "Sales", None, "900"])
        buf = io.BytesIO()
        wb.save(buf)
        txns, _w = parse_qb_multi_account_excel(buf.getvalue())
        self.assertEqual([float(t["amount"]) for t in txns], [-50, 900])


class FlippedImportReviewTests(_DbCase):
    def test_review_lists_only_disagreeing_imports(self):
        bad_exp = self.imported("50", self.gas, h="a")          # flipped withdrawal
        bad_inc = self.imported("-900", self.salary, h="b")     # flipped deposit
        self.imported("-50", self.gas, h="c")                   # fine
        self.imported("900", self.salary, h="d")                # fine
        self.imported("40", "", h="e")                          # uncategorized
        self.db.save_transaction({"date": "2026-03-01", "account_id": self.chk,
                                  "amount": "15", "category_id": self.gas})  # typed in app
        found = {t["id"] for t in self.db.find_flipped_imports()}
        self.assertEqual(found, {bad_exp["id"], bad_inc["id"]})

    def test_flip_respects_the_period_lock(self):
        old = self.imported("50", self.gas, h="a", date="2025-12-01")
        new = self.imported("60", self.gas, h="b", date="2026-03-01")
        self.db.set_lock_date("2025-12-31")
        flipped, locked = self.db.flip_transaction_signs([old["id"], new["id"]])
        self.assertEqual((flipped, locked), (1, 1))
        self.assertEqual(float(self.db.get_transaction(new["id"])["amount"]), -60)
        self.assertEqual(float(self.db.get_transaction(old["id"])["amount"]), 50)


class SignCheckDialogTests(_DbCase):
    def test_flip_selected_and_locked_rows(self):
        from PyQt6.QtCore import Qt
        from ui.dialogs.sign_check_dialog import SignCheckDialog, COL_PICK
        old = self.imported("50", self.gas, h="a", date="2025-12-01")
        new = self.imported("60", self.gas, h="b", date="2026-03-01")
        other = self.imported("-70", self.salary, h="c", date="2026-03-02")
        self.db.set_lock_date("2025-12-31")
        dlg = SignCheckDialog(self.db)
        self.assertEqual(dlg.table.rowCount(), 3)
        self.assertIn("locked", dlg.status.text())
        locked_item = dlg.table.item(0, COL_PICK)
        self.assertFalse(locked_item.flags() & Qt.ItemFlag.ItemIsEnabled)
        dlg._set_all(True)
        self.assertEqual(set(dlg.selected_ids()), {new["id"], other["id"]})
        dlg._flip(dlg.selected_ids())
        self.assertEqual(float(self.db.get_transaction(new["id"])["amount"]), -60)
        self.assertEqual(float(self.db.get_transaction(other["id"])["amount"]), 70)
        self.assertEqual(float(self.db.get_transaction(old["id"])["amount"]), 50)
        self.assertEqual(dlg.table.rowCount(), 1)          # only the locked one left

    def test_read_only_disables_flipping(self):
        from sheets.client import Database as _Db
        from ui.dialogs.sign_check_dialog import SignCheckDialog
        self.imported("50", self.gas)
        ro = _Db(self.db.db_path, read_only=True)
        dlg = SignCheckDialog(ro)
        self.assertFalse(dlg.flip_selected_btn.isEnabled())
        self.assertIn("read-only", dlg.status.text())


class ImportHashCompatTests(unittest.TestCase):
    def test_negative_debit_rows_keep_their_old_duplicate_key(self):
        import hashlib
        from parsers.spreadsheet import parse_spreadsheet
        rows, _w = parse_spreadsheet(b"Date,Description,Debit,Credit\n01/05/2026,COFFEE,-50.00,\n",
                                     "b.csv", "CHK")
        legacy = hashlib.md5("CHK|2026-01-05|50.0|COFFEE".encode()).hexdigest()[:16]
        self.assertEqual(rows[0]["import_hash"], legacy)
        self.assertEqual(float(rows[0]["amount"]), -50)


class DirectionConditionAgreesWithImporters(unittest.TestCase):
    """ISSUE 1d: the rules' Money in / out condition reads the stored sign, which
    every importer now writes as negative = money out."""
    def test_csv_and_ofx(self):
        from utils.rules import condition_matches
        from parsers.spreadsheet import parse_spreadsheet
        out = {"field": "direction", "operator": "is", "value": "out"}
        inn = dict(out, value="in")
        rows, _w = parse_spreadsheet(b"Date,Description,Debit,Credit\n"
                                     b"01/05/2026,COFFEE,-50.00,\n01/06/2026,PAY,,100\n",
                                     "b.csv", "CHK")
        self.assertTrue(condition_matches(out, rows[0]))
        self.assertTrue(condition_matches(inn, rows[1]))
        try:
            from parsers.qfx import parse_ofx
        except ImportError:         # pragma: no cover — ofxparse not installed
            self.skipTest("ofxparse not installed")
        ofx = (
            "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:USASCII\n"
            "CHARSET:1252\nCOMPRESSION:NONE\nOLDFILEUID:NONE\nNEWFILEUID:NONE\n\n"
            "<OFX><SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS>"
            "<DTSERVER>20260110<LANGUAGE>ENG</SONRS></SIGNONMSGSRSV1>"
            "<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>"
            "<STMTRS><CURDEF>USD<BANKACCTFROM><BANKID>1<ACCTID>2<ACCTTYPE>CHECKING"
            "</BANKACCTFROM><BANKTRANLIST><DTSTART>20260101<DTEND>20260110"
            "<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260105<TRNAMT>-50.00<FITID>A1"
            "<NAME>COFFEE</STMTTRN>"
            "<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260106<TRNAMT>100.00<FITID>A2"
            "<NAME>PAY</STMTTRN>"
            "</BANKTRANLIST><LEDGERBAL><BALAMT>50<DTASOF>20260110</LEDGERBAL>"
            "</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>\n")
        txns = parse_ofx(io.BytesIO(ofx.encode()), "CHK")
        self.assertTrue(condition_matches(out, txns[0]))
        self.assertTrue(condition_matches(inn, txns[1]))


if __name__ == "__main__":
    unittest.main()
