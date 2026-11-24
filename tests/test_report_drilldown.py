"""v1.19 R1 — report drill-down, in personal AND business books.

Double-clicking (or Enter on) a report amount opens Transactions filtered the
way the cell was computed. The test: for EVERY drillable cell of the P&L
(Total and By Period columns), By Class, By Vendor and the Balance Sheet
(personal Net Worth and business layouts), the register it opens adds up to
the number in the cell. Before v1.19 report lines did nothing on
double-click and TransactionsPage had no show_filtered().
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.book_helper import BothBooksTestCase

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from PyQt6.QtCore import QDate
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover
    raise unittest.SkipTest(f"PyQt6 not available: {e}")

from utils import reports as RP


def money(text: str):
    t = (text or "").strip()
    if not t or t == "—":
        return None
    neg = t.startswith("(") or t.startswith("-")
    v = float(t.strip("()-$").replace(",", "").replace("$", ""))
    return -v if neg else v


class _Base(BothBooksTestCase):
    def seed(self, db):
        mk = lambda n, t, p="": db.save_category({"name": n, "type": t, "parent_id": p})["id"]  # noqa
        self.chk = self.checking(db, "Checking", 500)
        self.sav = self.checking(db, "Savings", 100)
        self.card = db.save_account({"name": "Visa", "type": "credit card",
                                     "opening_balance": "-50"})["id"]
        self.food = mk("Fooding", "expense")
        self.groc = mk("Grocers", "expense", self.food)
        self.dine = mk("Dinings", "expense", self.food)
        self.home = mk("Homeware", "expense")
        self.pay = mk("Wages", "income")
        self.cls = db.save_class({"name": "Project X"})["id"]
        self.vendor = db.add_payee("Costco")
        T = db.save_transaction
        T({"date": "2026-01-03", "account_id": self.chk, "payee": "Job", "amount": "2000",
           "category_id": self.pay})
        T({"date": "2026-01-10", "account_id": self.chk, "payee": "Cafe", "amount": "-12",
           "category_id": self.dine, "class_id": self.cls})
        T({"date": "2026-02-02", "account_id": self.card, "payee": "Shop", "amount": "-30",
           "category_id": self.food})
        T({"date": "2026-02-20", "account_id": self.chk, "payee": "Refund", "amount": "8",
           "category_id": self.groc})
        T({"date": "2026-03-01", "account_id": self.card, "payee": "Uncat", "amount": "-4"})
        base = T({"date": "2026-02-15", "account_id": self.chk, "payee": "Costco",
                  "contact_id": self.vendor["id"], "amount": "-100"})
        db.split_transaction(base, [
            {"category_id": self.groc, "amount": "-60", "class_id": self.cls},
            {"category_id": self.home, "amount": "-25"},
            {"transfer_to": self.sav, "amount": "-15"}], signed=True)
        db.save_transfer_pair({"date": "2026-03-05", "account_id": self.chk, "payee": "Pay card",
                               "amount": "200"}, self.card)

    def pages(self, db):
        from ui.pages.reports import ReportsPage
        from ui.pages.transactions import TransactionsPage
        rp = ReportsPage(db)
        rp.resize(1200, 700)
        rp._date_from.setDate(QDate(2026, 1, 1))
        rp._date_to.setDate(QDate(2026, 3, 31))
        tp = TransactionsPage(db)
        tp.refresh()
        return rp, tp

    @staticmethod
    def register_net(tp, args):
        tp.show_filtered(**args)
        return round(sum(tp.shown_amount(t) for t in tp._txns), 2)

    @staticmethod
    def items(tree):
        out, stack = [], [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            it = stack.pop(0)
            out.append(it)
            stack[0:0] = [it.child(i) for i in range(it.childCount())]
        return out


class PnlDrillTests(_Base):
    def check_tree(self, rp, tp):
        from ui.pages.reports import _DRILL
        checked = 0
        for it in self.items(rp._pnl_tree):
            line = it.data(0, _DRILL)
            if not line:
                continue
            for col in range(1, rp._pnl_tree.columnCount()):
                cell = money(it.text(col))
                if cell is None:
                    continue
                target = rp.pnl_drill_target(it, col)
                self.assertIsNotNone(target, it.text(0))
                if target[0] != "register":
                    self.assertEqual(cell, 0.0, it.text(0))
                    continue
                net = self.register_net(tp, target[1])
                sign = 1 if line["kind"] == "summary" else (
                    -1 if line["section"] in RP.MEMO_ORDER else RP.SECTION_SIGN[line["section"]])
                self.assertAlmostEqual(net * sign, cell, places=2,
                                       msg=f"{it.text(0)} col {col}")
                self.assertIn("Filtered from report", tp._chip_label.text())
                checked += 1
        return checked

    def test_pnl_total_and_period_cells_equal_their_register(self):
        for db in self.each_book():
            self.seed(db)
            rp, tp = self.pages(db)
            rp.refresh()
            n = self.check_tree(rp, tp)
            self.assertGreater(n, 8)
            with mock.patch.object(rp, "_confirm_columns", return_value=True):
                rp._period_combo.setCurrentIndex(rp._period_combo.findData("month"))
            self.assertEqual(rp._pnl_tree.columnCount(), 5)
            self.assertGreater(self.check_tree(rp, tp), n)
            # A parent line includes its sub-accounts; the split's Grocers line shows
            # its own amount with the full total in the tooltip.
            it = next(i for i in self.items(rp._pnl_tree) if i.text(0).strip() == "Grocers")
            target = rp.pnl_drill_target(it, 4)
            tp.show_filtered(**target[1])
            costco = next(t for t in tp._txns if t["payee"] == "Costco")
            self.assertEqual(float(costco["match_amount"]), -60.0)
            self.assertEqual(float(costco["amount"]), -100.0)

    def test_class_filter_and_computed_line(self):
        for db in self.each_book():
            self.seed(db)
            rp, tp = self.pages(db)
            rp.refresh()
            rp._class_combo.setCurrentIndex(rp._class_combo.findData(self.cls))
            self.assertEqual(rp._class_combo.currentData(), self.cls)
            rp._run()
            self.assertGreater(self.check_tree(rp, tp), 3)
            tp.show_filtered(**rp.pnl_drill_target(
                next(i for i in self.items(rp._pnl_tree)
                     if i.text(0).strip() == "Total Expenses"), 1)[1])
            self.assertEqual(sorted(t["payee"] for t in tp._txns), ["Cafe", "Costco"])
            # A computed-only line explains itself instead of opening an empty list.
            from ui.report_tabs import drill_target
            t = drill_target([{"computed": "depreciation", "date": "2026-01-31"}],
                             "2026-01-01", "2026-01-31", "", "Depreciation")
            self.assertEqual(t[0], "computed")
            self.assertEqual(t[1], "Assets")


class GridDrillTests(_Base):
    def check_grid(self, rp, tp, tab):
        checked = 0
        for r, row in enumerate(tab.rows):
            for c in range(1, len(row)):
                v = row[c]
                if not hasattr(v, "quantize") or not v:
                    continue
                target = tab.drill_target(r, c)
                self.assertIsNotNone(target, row[0])
                self.assertEqual(target[0], "register", row[0])
                net = self.register_net(tp, target[1])
                label = str(row[0]).strip()
                if tab.drill[r].get("line") is not None:
                    line = tab.drill[r]["line"]
                    sign = 1 if line["kind"] == "summary" else RP.SECTION_SIGN.get(
                        line["section"], -1)
                else:           # By Vendor: Spent is shown positive
                    sign = -1 if tab.header[c] == "Spent" else 1
                self.assertAlmostEqual(net * sign, float(v), places=2,
                                       msg=f"{label} / {tab.header[c]}")
                checked += 1
        return checked

    def test_by_class_and_by_vendor(self):
        from ui.report_tabs import ByClassTab, ByVendorTab
        for db in self.each_book():
            self.seed(db)
            rp, tp = self.pages(db)
            for cls_tab in (ByClassTab, ByVendorTab):
                for period in ("total", "month"):
                    tab = cls_tab(db)
                    tab.run("2026-01-01", "2026-03-31", "", period)
                    self.assertGreater(self.check_grid(rp, tp, tab), 3,
                                       f"{cls_tab.__name__} {period}")
            tab = ByVendorTab(db)
            tab.run("2026-01-01", "2026-03-31", "", "total")
            r = next(i for i, row in enumerate(tab.rows) if row[0] == "Costco")
            target = tab.drill_target(r, 1)
            self.assertEqual(target[1]["contact_id"], {self.vendor["id"]})


class BalanceSheetDrillTests(_Base):
    def test_account_lines_equal_their_register_both_layouts(self):
        from ui.pages.reports import BS_TAB
        for db in self.each_book():
            self.seed(db)
            rp, tp = self.pages(db)
            rp._tabs.setCurrentIndex(BS_TAB)
            rp.refresh()
            accts = {a["id"]: a for a in db.get_accounts(active=None)}
            checked = 0
            for it in self.items(rp._bs_tree):
                target = rp.bs_drill_target(it)
                if not target or target[0] != "register":
                    continue
                args = target[1]
                cell = money(it.text(1))
                if args.get("account_id"):
                    tp.show_filtered(**args)
                    acct = accts[args["account_id"]]
                    bal = float(acct.get("opening_balance") or 0) + sum(
                        float(t["amount"]) for t in tp._txns)
                    # The last row's running balance is the same figure.
                    if tp._txns:
                        last = max(tp._txns, key=lambda t: (t["date"], t["created_at"]))
                        self.assertAlmostEqual(db.running_balances(acct["id"])[last["id"]],
                                               bal, places=2)
                    is_liab = acct.get("type") in ("credit card", "loan", "liability")
                    if db.is_business() and is_liab:
                        bal = -bal          # accountant layout: owed shown positive
                    self.assertAlmostEqual(bal, cell, places=2, msg=it.text(0))
                    checked += 1
                elif it.text(0).startswith("Net income"):
                    net = self.register_net(tp, args)
                    self.assertAlmostEqual(net, cell, places=2)
                    checked += 1
            self.assertGreaterEqual(checked, 3)
            # Net Worth / Opening balance equity: explained, not an empty list.
            with mock.patch.object(QMessageBox, "information") as info:
                eq = next(i for i in self.items(rp._bs_tree)
                          if i.text(0).startswith(("Net Worth", "Opening balance")))
                self.assertEqual(rp.drill_bs(eq)[0], "computed")
                info.assert_called_once()


if __name__ == "__main__":
    unittest.main()
