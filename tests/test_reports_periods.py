"""v1.18 reports.

R1 — sub-accounts: the P&L shows every level of the Chart of Accounts,
expanded, with Expand all / Collapse all, a parent's own transactions as
"<Parent> (other)" and a "Total <Parent>" row; By Class, Budget vs Actual and
the CSV exports indent sub-accounts the same way.

R2 — period columns: a "Columns:" dropdown (Day, Week, Month, Quarter, Year,
Total) on the shared filter bar. period_buckets() makes the columns (weeks
start Monday, partial first/last periods carry their real dates). The P&L shows
one column per period plus Total (the old By Month tab is folded into it; "By
Month" still opens it with Month columns); By Class, By Vendor and Cash Flow
too. Computed lines land in their own period. More than 60 columns asks first,
and the account column stays frozen while the rest scroll."""
import csv
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database
from utils import reports as RP
from reports_export import pnl_csv_rows

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from PyQt6.QtCore import QDate
    _app = QApplication.instance() or QApplication([])
except Exception:           # pragma: no cover
    _app = None


def labels(start, end, period):
    return [b.label for b in RP.period_buckets(start, end, period)]


class PeriodBucketTests(unittest.TestCase):
    def test_week_starts_monday(self):
        self.assertEqual(RP.WEEK_START, 0)
        self.assertEqual(RP.period_key("2026-11-12", "week"), "2026-11-09")   # Thu → Mon
        self.assertEqual(RP.period_key("2026-11-16", "week"), "2026-11-16")   # a Monday
        self.assertEqual(RP.period_key("2026-11-22", "week"), "2026-11-16")   # Sunday

    def test_day_with_leap_day(self):
        b = RP.period_buckets("2028-02-27", "2028-03-01", "day")
        self.assertEqual([x.key for x in b],
                         ["2028-02-27", "2028-02-28", "2028-02-29", "2028-03-01"])
        self.assertEqual([x.label for x in b], ["Feb 27", "Feb 28", "Feb 29", "Mar 1"])
        self.assertEqual(RP.period_key("2028-02-29", "month"), "2028-02-01")

    def test_week_partial_first_and_last(self):
        b = RP.period_buckets("2026-11-12", "2026-11-30", "week")
        self.assertEqual([x.label for x in b],
                         ["Nov 12–15", "Wk of Nov 16", "Wk of Nov 23", "Nov 30"])
        self.assertEqual([(x.start, x.end) for x in b][0], ("2026-11-12", "2026-11-15"))
        self.assertEqual([x.partial for x in b], [True, False, False, True])

    def test_week_across_new_year(self):
        self.assertEqual(labels("2025-12-29", "2026-01-11", "week"),
                         ["Wk of Dec 29, 2025", "Wk of Jan 5, 2026"])
        self.assertEqual(labels("2026-01-01", "2026-01-11", "week"),
                         ["Jan 1–4", "Wk of Jan 5"])

    def test_month(self):
        self.assertEqual(labels("2026-01-15", "2026-03-31", "month"),
                         ["Jan 15–31 26", "Feb 26", "Mar 26"])
        self.assertEqual(labels("2026-11-01", "2026-11-16", "month"), ["Nov 1–16 26"])

    def test_quarter_edges(self):
        self.assertEqual(labels("2025-11-01", "2026-06-30", "quarter"),
                         ["Nov 1–Dec 31 25", "Q1 26", "Q2 26"])
        self.assertEqual(RP.period_key("2026-03-31", "quarter"), "2026-01-01")
        self.assertEqual(RP.period_key("2026-04-01", "quarter"), "2026-04-01")

    def test_year_edges(self):
        self.assertEqual(labels("2025-07-01", "2026-12-31", "year"),
                         ["Jul 1–Dec 31 2025", "2026"])
        self.assertEqual(RP.period_key("2025-12-31", "year"), "2025-01-01")

    def test_total(self):
        b = RP.period_buckets("2026-01-01", "2026-12-31", "total")
        self.assertEqual([(x.key, x.label) for x in b], [(RP.TOTAL, "Total")])

    def test_counts(self):
        self.assertEqual(len(RP.period_buckets("2026-01-01", "2026-12-31", "day")), 365)
        self.assertEqual(len(RP.period_buckets("2028-01-01", "2028-12-31", "day")), 366)
        self.assertEqual(len(RP.period_buckets("2026-01-01", "2026-12-31", "month")), 12)
        self.assertEqual(RP.period_choices()[-1], ("total", "Total"))


CATS = {
    "sales": {"id": "sales", "name": "Sales", "type": "income", "parent_id": ""},
    "auto": {"id": "auto", "name": "Auto", "type": "expense", "parent_id": ""},
    "gas": {"id": "gas", "name": "Gas", "type": "expense", "parent_id": "auto"},
    "prem": {"id": "prem", "name": "Premium", "type": "expense", "parent_id": "gas"},
    "rep": {"id": "rep", "name": "Repairs", "type": "expense", "parent_id": "auto"},
    "rent": {"id": "rent", "name": "Rent", "type": "expense", "parent_id": ""},
}


def t(i, d, amt, cat):
    return {"id": i, "date": d, "account_id": "A", "amount": str(amt), "category_id": cat}


TXNS = [t("1", "2026-01-05", 1000, "sales"), t("2", "2026-01-06", -40, "gas"),
        t("3", "2026-02-07", -60, "rep"), t("4", "2026-02-08", -15, "auto"),
        t("5", "2026-03-09", -5, "prem"), t("6", "2026-03-10", -500, "rent")]


class StructureTests(unittest.TestCase):
    def lines(self, period="total"):
        rows = RP.pnl_rows(TXNS, CATS)
        buckets = RP.period_buckets("2026-01-01", "2026-03-31", period)
        rep = RP.pnl_report(rows, CATS, [b.key for b in buckets],
                            lambda r: RP.period_key(r["date"], period))
        return rep, RP.pnl_lines(rep)

    def test_parent_other_and_total_rows(self):
        _rep, lines = self.lines()
        flat = [(ln["label"], ln["depth"], ln["kind"]) for ln in lines]
        i = flat.index(("Auto", 1, "parent"))
        self.assertEqual(flat[i + 1:i + 7], [
            ("Gas", 2, "parent"), ("Premium", 3, "leaf"), ("Gas (other)", 3, "other"),
            ("Total Gas", 2, "total"), ("Repairs", 2, "leaf"), ("Auto (other)", 2, "other")])
        self.assertEqual(flat[i + 7], ("Total Auto", 1, "total"))
        tot = {ln["label"]: ln["amounts"][RP.TOTAL] for ln in lines if ln.get("amounts")}
        self.assertEqual(tot["Total Auto"], Decimal("120.00"))     # expenses shown positive
        self.assertEqual(tot["Auto (other)"], Decimal("15.00"))
        self.assertEqual(tot["Total Gas"], Decimal("45.00"))
        self.assertEqual(tot["Net Income"], Decimal("380.00"))

    def test_period_columns_add_up_to_total(self):
        rep, lines = self.lines("month")
        self.assertEqual(rep["columns"], ["2026-01-01", "2026-02-01", "2026-03-01", RP.TOTAL])
        for ln in lines:
            if ln.get("amounts"):
                parts = sum((ln["amounts"].get(k, Decimal(0)) for k in rep["columns"][:-1]),
                            Decimal(0))
                self.assertEqual(parts, ln["amounts"][RP.TOTAL], ln["label"])
        net = next(ln for ln in lines if ln["label"] == "Net Income")
        self.assertEqual(net["amounts"]["2026-02-01"], Decimal("-75.00"))

    def test_csv_rows_indent_subs_and_keep_flat_ones(self):
        out = pnl_csv_rows(RP.pnl_rows(TXNS, CATS), CATS)
        self.assertIn(["Expense", "Auto", ""], out)
        self.assertIn(["Expense", "    Gas", ""], out)
        self.assertIn(["Expense", "        Premium", "-5.00"], out)
        self.assertIn(["Expense", "    Auto (other)", "-15.00"], out)
        self.assertIn(["Expense", "Total Auto", "-120.00"], out)
        self.assertIn(["Expense", "Rent", "-500.00"], out)
        self.assertEqual(out[-1], ["", "Net income", "380.00"])

    def test_budget_vs_actual_indents_subs(self):
        rows = RP.pnl_rows(TXNS, CATS)
        out = RP.budget_vs_actual(rows, {"auto": 50}, CATS, "2026-01-01", "2026-03-31")
        names = [(r["name"], r.get("depth", 0)) for r in out]
        i = names.index(("Auto", 0))
        self.assertEqual(names[i + 1:i + 6], [("Gas", 1), ("Premium", 2), ("Gas (other)", 2),
                                              ("Repairs", 1), ("Auto (other)", 1)])
        auto = out[i]
        self.assertEqual(auto["budget"], Decimal("150.00"))
        self.assertEqual(auto["actual"], Decimal("120.00"))
        self.assertIsNone(out[i + 1]["budget"])


@unittest.skipIf(_app is None, "PyQt6 not available")
class _PageBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "r.db"))
        db = self.db
        self.chk = db.save_account({"name": "Checking", "type": "checking"})["id"]
        mk = lambda name, typ, parent="": db.save_category(  # noqa: E731
            {"name": name, "type": typ, "parent_id": parent})["id"]
        self.sales = mk("Zales", "income")
        self.auto = mk("Autos", "expense")
        self.gas = mk("Gasoline", "expense", self.auto)
        self.rep = mk("Fixes", "expense", self.auto)
        self.cls_a = db.save_class({"name": "Project A"})["id"]
        for d, amt, cat, cls in (("2026-01-05", 1000, self.sales, self.cls_a),
                                 ("2026-01-06", -40, self.gas, ""),
                                 ("2026-02-07", -60, self.rep, self.cls_a),
                                 ("2026-02-08", -15, self.auto, ""),
                                 ("2026-03-10", -5, self.gas, "")):
            db.save_transaction({"date": d, "account_id": self.chk, "payee": f"P{amt}",
                                 "amount": str(amt), "category_id": cat, "class_id": cls})
        from ui.pages.reports import ReportsPage
        self.page = ReportsPage(db)
        self.page.resize(1100, 700)
        self.page._date_from.setDate(QDate(2026, 1, 1))
        self.page._date_to.setDate(QDate(2026, 3, 31))
        self.page.refresh()

    def tearDown(self):
        self.page.deleteLater()
        shutil.rmtree(self.dir, ignore_errors=True)

    def items(self, tree=None):
        tree = tree or self.page._pnl_tree
        out, stack = [], [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            it = stack.pop(0)
            out.append(it)
            stack[0:0] = [it.child(i) for i in range(it.childCount())]
        return out

    def find(self, text):
        return next(it for it in self.items() if it.text(0).strip() == text)

    def set_period(self, key, confirm=True):
        with mock.patch.object(self.page, "_confirm_columns", return_value=confirm) as m:
            self.page._period_combo.setCurrentIndex(self.page._period_combo.findData(key))
        return m


class PnlTreeTests(_PageBase):
    def test_sub_accounts_visible_and_expanded(self):
        auto = self.find("Autos")
        self.assertTrue(auto.isExpanded())
        kids = [auto.child(i).text(0).strip() for i in range(auto.childCount())]
        self.assertEqual(kids, ["Fixes", "Gasoline", "Autos (other)", "Total Autos"])
        self.assertEqual(self.find("Autos (other)").text(1), "$15.00")
        self.assertEqual(self.find("Total Autos").text(1), "$120.00")
        self.assertEqual(auto.text(1), "")              # expanded: amount on the Total row
        self.assertTrue(all(it.isExpanded() for it in self.items() if it.childCount()))

    def test_expand_and_collapse_all(self):
        self.page._collapse_all_btn.click()
        self.assertFalse(any(it.isExpanded() for it in self.items() if it.childCount()))
        self.assertEqual(self.find("Autos").text(1), "$120.00")    # collapsed: shows its total
        self.page._expand_all_btn.click()
        self.assertTrue(all(it.isExpanded() for it in self.items() if it.childCount()))
        self.assertEqual(self.find("Autos").text(1), "")

    def test_columns_dropdown(self):
        combo = self.page._period_combo
        self.assertEqual([combo.itemText(i) for i in range(combo.count())],
                         ["Day", "Week", "Month", "Quarter", "Year", "Total"])
        self.assertEqual(combo.currentData(), "total")
        self.set_period("month")
        tree = self.page._pnl_tree
        heads = [tree.headerItem().text(i) for i in range(tree.columnCount())]
        self.assertEqual(heads, ["Account", "Jan 26", "Feb 26", "Mar 26", "Total"])
        gas = self.find("Gasoline")
        self.assertEqual([gas.text(i) for i in range(1, 5)],
                         ["$40.00", "—", "$5.00", "$45.00"])
        net = self.find("Net Income")
        self.assertEqual(net.text(4), "$880.00")
        self.assertEqual(net.text(2), "($75.00)")

    def test_too_many_columns_asks(self):
        self.page._date_from.setDate(QDate(2026, 1, 1))
        self.page._date_to.setDate(QDate(2026, 12, 31))
        m = self.set_period("day", confirm=False)
        m.assert_called_once_with(365)
        self.assertEqual(self.page._period_combo.currentData(), "total")   # put back
        self.assertEqual(self.page._pnl_tree.columnCount(), 2)
        self.set_period("day", confirm=True)
        self.assertEqual(self.page._pnl_tree.columnCount(), 367)
        frozen = self.page._pnl_frozen.view
        self.assertTrue(frozen.isVisible() or not self.page.isVisible())
        self.assertFalse(frozen.isColumnHidden(0))
        self.assertTrue(all(frozen.isColumnHidden(c) for c in range(1, 367)))
        self.assertEqual(frozen.columnWidth(0), self.page._pnl_tree.columnWidth(0))
        self.set_period("week", confirm=False)            # 53 columns: no question
        self.assertEqual(self.page._period_combo.currentData(), "week")

    def test_csv_export_has_the_same_columns(self):
        self.set_period("month")
        path = os.path.join(self.dir, "pnl.csv")
        self.page._export_pnl_csv(path)
        with open(path, encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["Account", "Jan 26", "Feb 26", "Mar 26", "Total"])
        by = {r[0]: r for r in rows}
        self.assertEqual(by["        Gasoline"][1:], ["40.00", "", "5.00", "45.00"])
        self.assertEqual(by["    Total Autos"][-1], "120.00")
        self.assertEqual(by["Net Income"][-1], "880.00")

    def test_by_month_alias(self):
        self.page._tabs.setCurrentIndex(1)
        self.page.show_report("By Month")
        self.assertEqual(self.page._tabs.currentIndex(), 0)
        self.assertEqual(self.page._period_combo.currentData(), "month")
        self.assertNotIn("By Month", [self.page._tabs.tabText(i)
                                      for i in range(self.page._tabs.count())])

    def test_balance_sheet_has_no_columns(self):
        self.page._tabs.setCurrentIndex(1)
        self.assertFalse(self.page._period_combo.isEnabled())
        self.page._tabs.setCurrentIndex(0)
        self.assertTrue(self.page._period_combo.isEnabled())


class OtherTabsTests(_PageBase):
    def tab(self, label):
        tabs = self.page._tabs
        i = next(i for i in range(tabs.count()) if tabs.tabText(i) == label)
        tabs.setCurrentIndex(i)
        return tabs.widget(i)

    def test_by_class_total_columns_are_classes(self):
        w = self.tab("By Class / Project")
        self.assertEqual(w.header, ["Account", "Project A", "(no class)", "Total"])
        names = [r[0] for r in w.rows]
        self.assertIn("        Gasoline", names)
        self.assertIn("    Total Autos", names)

    def test_by_class_with_month_columns(self):
        w = self.tab("By Class / Project")
        self.set_period("month")
        self.assertEqual(w.header, ["Account", "Jan 26", "Feb 26", "Mar 26", "Total"])
        self.assertIn("PROJECT A", [r[0] for r in w.rows])
        self.assertIn("(NO CLASS)", [r[0] for r in w.rows])

    def test_by_vendor_and_cash_flow_months(self):
        w = self.tab("By Vendor")
        self.set_period("month")
        self.assertEqual(w.header[0], "Vendor / customer")
        self.assertEqual(w.header[1:4], ["Jan 26", "Feb 26", "Mar 26"])
        w = self.tab("Cash Flow")
        self.assertEqual(self.page._period_combo.currentData(), "total")   # per tab
        self.set_period("month")
        self.assertEqual(w.header, ["Cash flow", "Jan 26", "Feb 26", "Mar 26", "Total"])
        ending = next(r for r in w.rows if r[0] == "Ending cash")
        self.assertEqual(ending[-1], ending[3])
        beginning = next(r for r in w.rows if r[0] == "Beginning cash")
        self.assertEqual(beginning[2], ending[1])          # Feb begins where Jan ended

    def test_budget_vs_actual_subs(self):
        w = self.tab("Budget vs Actual")
        names = [r[0] for r in w.rows]
        i = names.index("Autos")
        self.assertEqual(names[i + 1:i + 4], ["    Fixes", "    Gasoline", "    Autos (other)"])
        self.assertFalse(self.page._period_combo.isEnabled())

    def test_grid_csv_has_the_same_columns(self):
        w = self.tab("By Vendor")
        self.set_period("month")
        path = os.path.join(self.dir, "v.csv")
        w.export(path)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(next(csv.reader(f)), w.header)


@unittest.skipIf(_app is None, "PyQt6 not available")
class CogsAndComputedTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "b.db"), book_type="business")
        db = self.db
        bank = db.save_account({"name": "Bank", "type": "checking"})["id"]
        item = db.save_item({"name": "Widget", "sku": "W"})["id"]
        db.receive_stock(item, "2026-01-10", 10, 5, account_id=bank)
        db.sell_stock(item, "2026-02-12", 4, 12, account_id=bank)
        mats = next(c["id"] for c in db.get_categories() if c["name"] == "Materials")
        db.save_transaction({"date": "2026-03-01", "account_id": bank, "payee": "Steel",
                             "amount": "-10", "category_id": mats})
        from ui.pages.reports import ReportsPage
        self.page = ReportsPage(db)
        self.page._date_from.setDate(QDate(2026, 1, 1))
        self.page._date_to.setDate(QDate(2026, 3, 31))
        self.page.refresh()

    def tearDown(self):
        self.page.deleteLater()
        shutil.rmtree(self.dir, ignore_errors=True)

    def tops(self):
        t = self.page._pnl_tree
        return [t.topLevelItem(i).text(0) for i in range(t.topLevelItemCount())]

    def test_layout_and_gross_profit(self):
        tops = self.tops()
        self.assertEqual(tops[:4], ["Income", "Cost of Goods Sold", "Gross Profit", "Expenses"])
        t = self.page._pnl_tree
        gp = t.topLevelItem(2)
        self.assertEqual(gp.text(1), "$18.00")          # 48 sales − 20 inventory − 10 materials
        cogs = t.topLevelItem(1)
        names = []
        stack = [cogs]
        while stack:
            it = stack.pop()
            names.append(it.text(0).strip())
            stack.extend(it.child(i) for i in range(it.childCount()))
        self.assertIn("Inventory sold (computed)", names)
        self.assertIn("Materials", names)

    def test_computed_cogs_lands_in_its_month(self):
        with mock.patch.object(self.page, "_confirm_columns", return_value=True):
            self.page._period_combo.setCurrentIndex(self.page._period_combo.findData("month"))
        t = self.page._pnl_tree
        heads = [t.headerItem().text(i) for i in range(t.columnCount())]
        stack = [t.topLevelItem(i) for i in range(t.topLevelItemCount())]
        inv = None
        while stack:
            it = stack.pop()
            if it.text(0).strip() == "Inventory sold (computed)":
                inv = it
            stack.extend(it.child(i) for i in range(it.childCount()))
        self.assertEqual(heads[1:], ["Jan 26", "Feb 26", "Mar 26", "Total"])
        self.assertEqual([inv.text(i) for i in range(1, 5)], ["—", "$20.00", "—", "$20.00"])

    def test_schedule_c_line_4(self):
        tabs = self.page._tabs
        i = next(i for i in range(tabs.count()) if tabs.tabText(i) == "Schedule C")
        tabs.setCurrentIndex(i)
        w = tabs.widget(i)
        line4 = next(r for r in w.rows if str(r[0]).startswith("Line 4"))
        self.assertEqual(line4[2], Decimal("30.00"))    # computed 20 + Materials 10


if __name__ == "__main__":
    unittest.main()
