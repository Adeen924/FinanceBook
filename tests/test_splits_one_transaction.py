"""v1.19 — a split is ONE transaction (QuickBooks "-Split-"), in personal AND
business books.

Before v1.19 the register showed one row per split line ("⑂ COSTCO" twice or
three times), each with its own running-balance step, and opening a line
edited only that line. These tests pin the new model: the lines stay stored
as rows (so reports count each line in its own account), but the register,
the Transaction window, delete, reconcile, re-import and the Match engine
treat the group as one transaction.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.book_helper import BothBooksTestCase

try:
    from PyQt6.QtWidgets import QApplication, QDialog
    from PyQt6.QtCore import QDate
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover
    raise unittest.SkipTest(f"PyQt6 not available: {e}")


def pnl_by_category(db, start="2026-01-01", end="2026-12-31"):
    from utils.reports import pnl_rows
    out = {}
    for r in pnl_rows(db.get_transactions(start=start, end=end), db.category_map()):
        out[r["category_id"]] = round(out.get(r["category_id"], 0) + float(r["amount"]), 2)
    return out


class SplitFixture(BothBooksTestCase):
    def make(self, db):
        """Checking ($1000 opening) with a $10 coffee, a 3-way $100 Costco split
        (imported) and a $5 snack after it."""
        self.chk = self.checking(db)
        self.food = self.category(db, "Groceries")
        self.home = self.category(db, "Household")
        self.gift = self.category(db, "Gifts")
        db.save_transaction({"date": "2026-03-01", "account_id": self.chk, "payee": "Cafe",
                             "amount": "-10", "category_id": self.food})
        base = db.save_transaction({"date": "2026-03-02", "account_id": self.chk,
                                    "payee": "COSTCO", "amount": "-100",
                                    "import_hash": "h-costco"})
        lines = db.split_transaction(base, [
            {"category_id": self.food, "amount": "-60", "memo": "food"},
            {"category_id": self.home, "amount": "-30", "memo": "paper"},
            {"category_id": self.gift, "amount": "-10", "memo": "card"}], signed=True)
        db.save_transaction({"date": "2026-03-03", "account_id": self.chk, "payee": "Snack",
                             "amount": "-5", "category_id": self.food})
        self.base, self.lines = base, lines
        return lines[0]["split_group_id"]


class RegisterRowsTests(SplitFixture):
    def test_one_row_per_split_with_total_and_one_balance_step(self):
        for db in self.each_book():
            gid = self.make(db)
            rows = db.list_register_rows(account_id=self.chk)
            self.assertEqual([r["payee"] for r in rows], ["Cafe", "COSTCO", "Snack"])
            costco = rows[1]
            self.assertTrue(costco["is_split"])
            self.assertEqual(costco["line_count"], 3)
            self.assertEqual(float(costco["amount"]), -100.0)
            self.assertEqual(costco["split_group_id"], gid)
            self.assertEqual(costco["import_hash"], "h-costco")
            # The first line keeps the original transaction's id.
            self.assertEqual(costco["id"], self.base["id"])
            bal = db.running_balances(self.chk)
            self.assertEqual([round(bal[r["id"]], 2) for r in rows], [990.0, 890.0, 885.0])
            # Every line shows the balance after the whole transaction.
            self.assertEqual({round(bal[l["id"]], 2) for l in self.lines}, {890.0})

    def test_category_filter_search_and_class(self):
        for db in self.each_book():
            self.make(db)
            rows = db.list_register_rows(account_id=self.chk, category_ids={self.home})
            self.assertEqual(len(rows), 1)
            self.assertEqual(float(rows[0]["match_amount"]), -30.0)
            self.assertEqual(float(rows[0]["amount"]), -100.0)
            rows = db.list_register_rows(account_id=self.chk, search="paper")
            self.assertEqual([r["payee"] for r in rows], ["COSTCO"])
            self.assertEqual(len(db.list_register_rows(search="costco")), 1)

    def test_shared_fields_and_header(self):
        for db in self.each_book():
            gid = self.make(db)
            db.update_split_shared(gid, {"payee": "Costco Wholesale", "date": "2026-03-04",
                                         "reconciled": "1"})
            lines = db.get_split_group(gid)
            self.assertEqual({l["payee"] for l in lines}, {"Costco Wholesale"})
            self.assertEqual({l["date"] for l in lines}, {"2026-03-04"})
            self.assertEqual({l["reconciled"] for l in lines}, {"1"})
            full = db.get_transaction_with_lines(lines[2]["id"])
            self.assertEqual(full["header"]["id"], lines[0]["id"])
            self.assertEqual(len(full["lines"]), 3)
            self.assertEqual(sum(1 for l in lines if l.get("import_hash")), 1)

    def test_reports_count_each_line_in_its_own_account(self):
        for db in self.each_book():
            self.make(db)
            p = pnl_by_category(db)
            self.assertEqual(p[self.food], -75.0)
            self.assertEqual(p[self.home], -30.0)
            self.assertEqual(p[self.gift], -10.0)

    def test_split_with_signed_matches_legacy_split_in_reports(self):
        """The signed (Transaction window) and legacy (receipt / invoice)
        split paths store the same rows, so reports are unchanged."""
        for db in self.each_book():
            chk = self.checking(db)
            food = self.category(db, "Groceries")
            home = self.category(db, "Household")
            a = db.save_transaction({"date": "2026-04-01", "account_id": chk, "payee": "A",
                                     "amount": "-50"})
            db.split_transaction(a, [{"category_id": food, "amount": "20"},
                                     {"category_id": home, "amount": "30"}])
            legacy = pnl_by_category(db)
            for t in db.get_transactions():
                db.delete_transaction_full(t)
            b = db.save_transaction({"date": "2026-04-01", "account_id": chk, "payee": "A",
                                     "amount": "-50"})
            db.split_transaction(b, [{"category_id": food, "amount": "-20"},
                                     {"category_id": home, "amount": "-30"}], signed=True)
            self.assertEqual(pnl_by_category(db), legacy)

    def test_delete_removes_group_and_transfer_mirrors(self):
        for db in self.each_book():
            chk = self.checking(db)
            sav = self.checking(db, "Savings", 0)
            food = self.category(db, "Groceries")
            base = db.save_transaction({"date": "2026-05-01", "account_id": chk,
                                        "payee": "Mixed", "amount": "-100"})
            lines = db.split_transaction(base, [
                {"category_id": food, "amount": "-40"},
                {"transfer_to": sav, "amount": "-60"}], signed=True)
            self.assertEqual(round(db.account_balance(sav), 2), 60.0)
            mirror = db.list_register_rows(account_id=sav)
            self.assertEqual(len(mirror), 1)
            self.assertEqual(mirror[0]["split_source"], (chk, "Mixed"))
            head = db.get_transaction_with_lines(lines[1]["id"])["header"]
            db.delete_transaction_full(head)
            self.assertEqual(db.get_transactions(account_id=chk), [])
            self.assertEqual(db.get_transactions(account_id=sav), [])

    def test_remove_split(self):
        for db in self.each_book():
            gid = self.make(db)
            row = db.remove_split(gid, self.food)
            self.assertEqual(db.get_split_group(gid), [])
            self.assertEqual(row["id"], self.base["id"])
            self.assertEqual(float(row["amount"]), -100.0)
            self.assertEqual(row["category_id"], self.food)
            self.assertEqual(row["import_hash"], "h-costco")
            self.assertEqual(len(db.list_register_rows(account_id=self.chk)), 3)

    def test_reimporting_the_bank_row_is_still_a_duplicate(self):
        for db in self.each_book():
            self.make(db)
            self.assertIn("h-costco", db.get_existing_hashes())
            from utils.matching import find_matches
            m = find_matches(db, [{"date": "2026-03-02", "amount": "-100",
                                   "import_hash": "h-costco", "payee": "COSTCO"}], self.chk)
            self.assertEqual(m[0].kind, "")

    def test_match_engine_matches_a_split_group_as_one(self):
        for db in self.each_book():
            chk = self.checking(db)
            food = self.category(db, "Groceries")
            home = self.category(db, "Household")
            base = db.save_transaction({"date": "2026-06-10", "account_id": chk,
                                        "payee": "Target (hand-entered)", "amount": "-80"})
            lines = db.split_transaction(base, [{"category_id": food, "amount": "-50"},
                                                {"category_id": home, "amount": "-30"}],
                                         signed=True)
            from utils.matching import find_matches, MANUAL
            row = {"date": "2026-06-11", "amount": "-80", "payee": "TARGET 0123",
                   "import_hash": "h-target", "account_id": chk}
            m = find_matches(db, [row], chk)[0]
            self.assertEqual(m.kind, MANUAL)
            self.assertEqual(m.candidates[0].id, lines[0]["id"])
            out = db.import_transactions([dict(row, _match={"kind": "manual",
                                                            "id": lines[0]["id"]})])
            self.assertEqual(out["merged"], 1)
            group = db.get_split_group(lines[0]["split_group_id"])
            self.assertEqual(len(group), 2)
            self.assertEqual({l["date"] for l in group}, {"2026-06-11"})
            self.assertEqual(sum(1 for l in group if l["import_hash"] == "h-target"), 1)
            self.assertEqual({l["payee"] for l in group}, {"Target (hand-entered)"})
            # A different total never matches.
            m = find_matches(db, [dict(row, amount="-50", import_hash="h2")], chk)[0]
            self.assertNotEqual(m.kind, MANUAL)


class SourcesDisplayAsOneTests(BothBooksTestCase):
    def test_receipt_split_is_one_row(self):
        for db in self.each_book():
            chk = self.checking(db)
            a = self.category(db, "Groceries")
            b = self.category(db, "Household")
            rc = db.add_manual_receipt()
            items = [{"description": "Milk", "line_total": "10.00", "category_id": a,
                      "business": True, "class_id": "", "charged_to": "", "kind": "expense",
                      "qty": 1},
                     {"description": "Towels", "line_total": "20.00", "category_id": b,
                      "business": True, "class_id": "", "charged_to": "", "kind": "expense",
                      "qty": 1}]
            db.book_receipt(rc["id"], {"vendor": "Costco"}, items, account_id=chk,
                            date="2026-02-01")
            rows = db.list_register_rows(account_id=chk)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["is_split"])
            self.assertEqual(float(rows[0]["amount"]), -30.0)

    def test_invoice_payment_split_is_one_row_business(self):
        for db in self.each_book():
            if not db.is_business():
                continue        # invoices exist only in business books
            cats = {c["name"]: c["id"] for c in db.get_categories()}
            bank = db.save_account({"name": "Checking", "type": "checking"})
            cust = db.save_contact({"name": "Acme", "kind": "customer"})
            inv = db.save_doc("invoice", {"contact_id": cust["id"], "issue_date": "2026-03-01",
                                          "due_date": "2026-03-16", "status": "sent"}, [
                {"description": "A", "qty": 1, "unit_price": 100, "tax_rate": 0,
                 "category_id": cats["Consulting"]},
                {"description": "B", "qty": 1, "unit_price": 50, "tax_rate": 0,
                 "category_id": cats["Product Sales"]}])
            db.record_doc_payment("invoice", inv["id"], "2026-03-20", bank["id"], "150")
            rows = db.list_register_rows(account_id=bank["id"])
            self.assertEqual(len(rows), 1)
            self.assertEqual(float(rows[0]["amount"]), 150.0)
            self.assertEqual(rows[0]["line_count"], 2)

    def test_legacy_loan_transfer_split_is_one_row(self):
        """Older principal/interest splits (transfer mode) show as one row
        with the full payment and one mirror in the loan account."""
        for db in self.each_book():
            chk = self.checking(db)
            loan = db.save_account({"name": "Car Loan", "type": "loan",
                                    "opening_balance": "-5000"})["id"]
            interest = self.category(db, "Loan Interest")
            base = db.save_transaction({"date": "2026-01-15", "account_id": chk,
                                        "payee": "Car payment", "amount": "-300"})
            db.split_transaction(base, [
                {"category_id": "", "amount": "250", "transfer": True},
                {"category_id": interest, "amount": "50", "transfer": False}],
                transfer_to=loan)
            rows = db.list_register_rows(account_id=chk)
            self.assertEqual(len(rows), 1)
            self.assertEqual(float(rows[0]["amount"]), -300.0)
            self.assertEqual(round(db.account_balance(loan), 2), -4750.0)
            self.assertEqual(len(db.list_register_rows(account_id=loan)), 1)


class RegisterAndDialogUITests(SplitFixture):
    def page(self, db):
        from ui.pages.transactions import TransactionsPage
        p = TransactionsPage(db)
        p.resize(1200, 600)
        p.refresh()
        p.show_account(self.chk)
        return p

    def test_register_shows_split_once(self):
        for db in self.each_book():
            self.make(db)
            p = self.page(db)
            self.assertEqual(p._table.rowCount(), 3)
            row = next(i for i, t in enumerate(p._txns) if t["payee"] == "COSTCO")
            self.assertEqual(p._table.item(row, 3).text(), "-Split- (3)")
            self.assertIn("Household", p._table.item(row, 3).toolTip())
            self.assertIn("3 transactions", p._summary_label.text())
            self.assertEqual(p._table.item(row, 5).text(), "$890.00")
            self.assertEqual(p._table.item(2, 5).text(), "$885.00")
            p._table.selectRow(row)
            self.assertTrue(p._detail.isVisible() or p._detail.text())
            self.assertIn("Household", p._detail.text())

    def test_open_line_opens_whole_transaction_and_edit_updates_all_lines(self):
        from ui.dialogs import transaction_dialog as TD
        for db in self.each_book():
            gid = self.make(db)
            dlg = TD.TransactionDialog(db, transaction=db.get_transaction(self.lines[2]["id"]))
            self.assertTrue(dlg._split_chk.isChecked())
            self.assertEqual(len(dlg._split_box.rows()), 3)
            self.assertAlmostEqual(dlg.amount_spin.value(), 100.0)
            self.assertTrue(dlg._ok_btn.isEnabled())
            dlg.payee_edit.setText("Costco #123")
            dlg.date_edit.setDate(QDate(2026, 3, 5))
            dlg._accept()
            self.assertTrue(dlg.handled)
            lines = db.get_split_group(gid)
            self.assertEqual(len(lines), 3)
            self.assertEqual({l["payee"] for l in lines}, {"Costco #123"})
            self.assertEqual({l["date"] for l in lines}, {"2026-03-05"})
            self.assertEqual(sorted(float(l["amount"]) for l in lines), [-60.0, -30.0, -10.0])
            self.assertEqual(lines[0]["id"], self.base["id"])
            self.assertEqual(sum(1 for l in lines if l["import_hash"]), 1)

    def test_split_an_imported_transaction_three_ways_in_the_dialog(self):
        from ui.dialogs import transaction_dialog as TD
        for db in self.each_book():
            chk = self.checking(db)
            a, b, c = (self.category(db, n) for n in ("Groceries", "Household", "Gifts"))
            t = db.save_transaction({"date": "2026-07-01", "account_id": chk, "payee": "WALMART",
                                     "amount": "-90", "import_hash": "h-wm"})
            dlg = TD.TransactionDialog(db, transaction=t, start_split=True)
            ed = dlg._split_box
            self.assertEqual(len(ed.rows()), 2)
            r0, r1 = ed.rows()
            r0["acct"].select_by_data(a)
            r0["amount"].setValue(40)             # last line follows the remainder (T2)
            self.assertAlmostEqual(r1["amount"].value(), 50.0)
            r1["acct"].select_by_data(b)
            r1["amount"].setValue(30)             # typed: now the user's
            self.assertFalse(dlg._ok_btn.isEnabled())
            self.assertIn("Remaining: $20.00", ed.remaining_text())
            r2 = ed.add_line(c)                    # new line = remaining
            self.assertAlmostEqual(r2["amount"].value(), 20.0)
            self.assertTrue(dlg._ok_btn.isEnabled())
            dlg._accept()
            rows = db.list_register_rows(account_id=chk)
            self.assertEqual(len(rows), 1)
            self.assertEqual(float(rows[0]["amount"]), -90.0)
            self.assertEqual(rows[0]["id"], t["id"])
            self.assertEqual(rows[0]["import_hash"], "h-wm")
            self.assertEqual(pnl_by_category(db), {a: -40.0, b: -30.0, c: -20.0})

    def test_remove_split_in_dialog_asks_which_account(self):
        from ui.dialogs import transaction_dialog as TD
        for db in self.each_book():
            gid = self.make(db)
            dlg = TD.TransactionDialog(db, transaction=db.get_transaction(self.base["id"]))
            with mock.patch.object(TD, "ask_remove_split_target", return_value=self.home):
                dlg._split_chk.setChecked(False)
            self.assertEqual(dlg.cat_combo.current_data(), self.home)
            dlg._accept()
            data = dlg.get_data()
            if not dlg.handled:
                db.save_transaction(data)
            self.assertEqual(db.get_split_group(gid), [])
            t = db.get_transaction(self.base["id"])
            self.assertEqual((float(t["amount"]), t["category_id"]), (-100.0, self.home))

    def test_cancelled_remove_split_keeps_split(self):
        from ui.dialogs import transaction_dialog as TD
        for db in self.each_book():
            self.make(db)
            dlg = TD.TransactionDialog(db, transaction=db.get_transaction(self.base["id"]))
            with mock.patch.object(TD, "ask_remove_split_target", return_value=None):
                dlg._split_chk.setChecked(False)
            self.assertTrue(dlg._split_chk.isChecked())

    def test_reconcile_shows_one_row_per_split(self):
        for db in self.each_book():
            gid = self.make(db)
            from ui.pages.reconcile import ReconcilePage
            page = ReconcilePage(db)
            page.refresh()
            page._start_session(db.get_account(self.chk))
            self.assertEqual(len(page._session_txns), 3)
            self.assertEqual(page._sess_table.rowCount(), 3)
            costco = next(t for t in page._session_txns if t["payee"] == "COSTCO")
            self.assertEqual(float(costco["amount"]), -100.0)
            rec = db.save_reconciliation({"account_id": self.chk, "statement_date": "2026-03-31",
                                          "statement_balance": 885})
            db.complete_reconciliation(rec["id"], page.ids_for([costco]))
            self.assertEqual({l["reconciled"] for l in db.get_split_group(gid)}, {"1"})


if __name__ == "__main__":
    unittest.main()


class AuditAndDashboardTests(SplitFixture):
    def test_audit_log_shows_one_entry_per_split_change(self):
        from ui.dialogs.audit_dialog import collapse_split_entries
        for db in self.each_book():
            gid = self.make(db)
            db.update_split_shared(gid, {"payee": "Costco Wholesale"})
            entries = [e for e in db.get_audit_log("transactions")
                       if "Costco Wholesale" in (e.get("details") or "")]
            self.assertEqual(len(entries), 3)
            rows = collapse_split_entries(entries)
            self.assertEqual(len(rows), 1)
            self.assertIn("Split transaction (3 lines)", rows[0]["summary"])

    def test_dashboard_recent_shows_split_once(self):
        from datetime import date, timedelta
        for db in self.each_book():
            chk = self.checking(db)
            food = self.category(db, "Groceries")
            home = self.category(db, "Household")
            d = (date.today() - timedelta(days=2)).isoformat()
            base = db.save_transaction({"date": d, "account_id": chk, "payee": "Costco",
                                        "amount": "-50"})
            db.split_transaction(base, [{"category_id": food, "amount": "-20"},
                                        {"category_id": home, "amount": "-30"}], signed=True)
            from ui.pages.dashboard import DashboardPage
            page = DashboardPage(db)
            page.refresh()
            self.assertEqual(page._table.rowCount(), 1)
            self.assertEqual(page._table.item(0, 2).text(), "-Split- (2)")
