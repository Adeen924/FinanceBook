"""v1.14 bank rules: several conditions (All / Any), money in / out and bank
account conditions, a mandatory account, rules that record transfers, and the
schema-12 migration of pre-1.14 single-condition rules."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import tests  # noqa: F401
from sheets.client import Database, SCHEMA_VERSION
from sheets import backup
from utils import rules as RL


def cond(field, op, value, value2=""):
    return {"field": field, "operator": op, "value": value, "value2": value2}


class ConditionTests(unittest.TestCase):
    T = {"payee": "COSTCO WHSE #123", "memo": "gas", "amount": "-62.50",
         "date": "2026-03-15", "account_id": "CHK"}

    def test_all_and_any(self):
        rule = {"match_mode": "all", "conditions": [cond("payee", "contains", "costco"),
                                                    cond("amount", "gt", "50")]}
        self.assertTrue(RL.rule_matches(rule, self.T))
        self.assertFalse(RL.rule_matches(rule, dict(self.T, amount="-20")))
        rule["match_mode"] = "any"
        self.assertTrue(RL.rule_matches(rule, dict(self.T, amount="-20")))
        self.assertFalse(RL.rule_matches(rule, dict(self.T, payee="TARGET", amount="-20")))
        self.assertFalse(RL.rule_matches({"conditions": []}, self.T))

    def test_money_in_out_and_bank_account(self):
        out = cond("direction", "is", "out")
        inn = cond("direction", "is", "in")
        self.assertTrue(RL.condition_matches(out, self.T))
        self.assertFalse(RL.condition_matches(inn, self.T))
        self.assertTrue(RL.condition_matches(inn, dict(self.T, amount="100")))
        self.assertTrue(RL.condition_matches(cond("account", "is", "CHK"), self.T))
        self.assertFalse(RL.condition_matches(cond("account", "is", "SAV"), self.T))
        self.assertFalse(RL.condition_matches(cond("account", "is", ""), self.T))

    def test_legacy_operators_still_work(self):
        self.assertTrue(RL.condition_matches(cond("amount", "between", "60", "70"), self.T))
        self.assertTrue(RL.condition_matches(cond("date", "day_of_month", "15"), self.T))
        self.assertTrue(RL.condition_matches(cond("memo", "equals", "GAS"), self.T))
        self.assertTrue(RL.condition_matches(cond("payee", "starts_with", "costco"), self.T))
        # A rule dict with no conditions list falls back to its own columns.
        self.assertTrue(RL.rule_matches({"field": "payee", "operator": "contains",
                                         "value": "whse"}, self.T))

    def test_describe(self):
        rule = {"match_mode": "all", "conditions": [
            cond("payee", "contains", "COSTCO"), cond("amount", "gt", "50"),
            cond("direction", "is", "out"), cond("account", "is", "CHK")]}
        self.assertEqual(RL.describe_rule(rule, {"CHK": "Checking"}),
                         "Payee contains “COSTCO” AND Amount > 50 AND Money out "
                         "AND Bank account is Checking")
        rule["match_mode"] = "any"
        self.assertIn(" OR ", RL.describe_rule(rule))


class RuleDbTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "r.db"))
        self.chk = self.db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.sav = self.db.save_account({"name": "Savings", "type": "savings"})["id"]
        self.gas = next(c for c in self.db.get_categories() if c["name"] == "Gas")["id"]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_save_and_load_conditions(self):
        r = self.db.save_rule({"name": "Costco gas", "match_mode": "any",
                               "conditions": [cond("payee", "contains", "costco"),
                                              cond("memo", "contains", "fuel")],
                               "category_id": self.gas, "priority": "5"})
        got = next(x for x in self.db.get_rules() if x["id"] == r["id"])
        self.assertEqual(got["match_mode"], "any")
        self.assertEqual([c["value"] for c in got["conditions"]], ["costco", "fuel"])
        self.assertEqual((got["field"], got["value"]), ("payee", "costco"))   # old columns
        # Editing replaces the conditions rather than appending.
        self.db.save_rule(dict(got, conditions=[cond("payee", "equals", "x")]))
        got = next(x for x in self.db.get_rules() if x["id"] == r["id"])
        self.assertEqual(len(got["conditions"]), 1)
        self.db.delete_rule(r["id"])
        with sqlite3.connect(self.db.db_path) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM rule_conditions").fetchone()[0], 0)

    def test_legacy_save_rule_shape(self):
        # The QuickBooks import still saves single-field rules.
        self.db.save_rule({"name": "AWS", "field": "payee", "operator": "contains",
                           "value": "aws", "category_id": self.gas})
        txns = [{"payee": "AWS EMEA", "amount": "-10", "account_id": self.chk}]
        self.db.apply_rules(txns)
        self.assertEqual(txns[0]["category_id"], self.gas)

    def test_apply_uses_all_mode_and_priority(self):
        self.db.save_rule({"name": "big", "priority": "10", "category_id": self.gas,
                           "conditions": [cond("payee", "contains", "shell"),
                                          cond("amount", "gt", "40")]})
        txns = [{"payee": "SHELL OIL", "amount": "-45", "account_id": self.chk},
                {"payee": "SHELL OIL", "amount": "-12", "account_id": self.chk}]
        self.db.apply_rules(txns)
        self.assertEqual(txns[0].get("category_id"), self.gas)
        self.assertFalse(txns[1].get("category_id"))

    def test_transfer_rule_on_import_links_other_side(self):
        self.db.save_rule({"name": "To savings", "transfer_account_id": self.sav,
                           "conditions": [cond("payee", "contains", "xfer to sav"),
                                          cond("direction", "is", "out")]})
        # The savings side was imported earlier.
        self.db.bulk_save_transactions([{"date": "2026-04-02", "account_id": self.sav,
                                         "payee": "XFER FROM CHK", "amount": "300",
                                         "import_hash": "s1"}])
        new = [{"date": "2026-04-01", "account_id": self.chk, "payee": "XFER TO SAV",
                "amount": "-300", "import_hash": "c1"},
               {"date": "2026-04-05", "account_id": self.chk, "payee": "XFER TO SAV",
                "amount": "-80", "import_hash": "c2"}]
        self.db.apply_rules(new)
        self.assertEqual(new[0]["_transfer_to"], self.sav)
        self.db.bulk_save_transactions(new)
        sav = self.db.get_transactions(account_id=self.sav)
        self.assertEqual(sorted(float(t["amount"]) for t in sav), [80, 300])  # one linked, one made
        self.assertTrue(all(t["is_transfer"] == "1" for t in
                            self.db.get_transactions(account_id=self.chk)))

    def test_transfer_rule_skips_its_own_account(self):
        self.db.save_rule({"name": "To savings", "transfer_account_id": self.sav,
                           "conditions": [cond("payee", "contains", "xfer")]})
        txns = [{"payee": "XFER", "amount": "50", "account_id": self.sav}]
        self.db.apply_rules(txns)
        self.assertNotIn("_transfer_to", txns[0])

    def test_apply_rules_to_all_makes_transfers(self):
        self.db.save_rule({"name": "To savings", "transfer_account_id": self.sav,
                           "conditions": [cond("payee", "contains", "xfer")]})
        self.db.save_rule({"name": "Gas", "category_id": self.gas,
                           "conditions": [cond("payee", "contains", "shell")]})
        self.db.save_transaction({"date": "2026-05-01", "account_id": self.chk,
                                  "payee": "XFER", "amount": "-25"})
        self.db.save_transaction({"date": "2026-05-01", "account_id": self.chk,
                                  "payee": "SHELL", "amount": "-30"})
        self.assertEqual(self.db.apply_rules_to_all(), 2)
        chk = {t["payee"]: t for t in self.db.get_transactions(account_id=self.chk)}
        self.assertEqual(chk["XFER"]["is_transfer"], "1")
        self.assertEqual(chk["SHELL"]["category_id"], self.gas)
        self.assertEqual([float(t["amount"]) for t in
                          self.db.get_transactions(account_id=self.sav)], [25])
        self.assertEqual(self.db.apply_rules_to_all(), 0)      # nothing left to do


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "old.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_v11(self):
        Database(self.path).close()
        c = sqlite3.connect(self.path)
        # A v11 file's audit triggers know only v11 columns; they are rebuilt on open.
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' "
                                 "AND tbl_name='rules'").fetchall():
            c.execute(f"DROP TRIGGER {name}")
        c.execute("DROP TABLE rule_conditions")
        c.execute("ALTER TABLE rules DROP COLUMN match_mode")
        c.execute("ALTER TABLE rules DROP COLUMN transfer_account_id")
        c.execute("INSERT INTO rules (id,name,field,operator,value,value2,category_id,priority,"
                  "active,created_at) VALUES ('R1','Costco','payee','contains','COSTCO','',"
                  "'CAT',3,1,'2025-01-01')")
        c.execute("INSERT INTO rules (id,name,field,operator,value,value2,category_id,priority,"
                  "active,created_at) VALUES ('R2','Big','amount','between','100','200',"
                  "'CAT',0,1,'2025-01-01')")
        c.execute("INSERT INTO transactions (id,date,account_id,payee,amount) "
                  "VALUES ('T1','2025-02-01','A','x',-5)")
        c.execute("PRAGMA user_version = 11")
        c.commit()
        c.close()

    def test_single_conditions_migrated_once(self):
        self.make_v11()
        db = Database(self.path)
        self.assertEqual(backup.read_user_version(self.path), SCHEMA_VERSION)
        self.assertGreaterEqual(SCHEMA_VERSION, 12)
        self.assertTrue(db.last_migration_backup)            # backed up first
        rules = {r["id"]: r for r in db.get_rules()}
        self.assertEqual(rules["R1"]["conditions"],
                         [cond("payee", "contains", "COSTCO")])
        self.assertEqual(rules["R2"]["conditions"],
                         [cond("amount", "between", "100", "200")])
        self.assertEqual(rules["R1"]["match_mode"], "all")
        self.assertEqual(rules["R1"]["transfer_account_id"], "")
        db.close()
        Database(self.path).close()                           # open again: idempotent
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM rule_conditions").fetchone()[0], 2)
            # old columns are kept
            self.assertEqual(c.execute("SELECT field, value FROM rules WHERE id='R1'").fetchone(),
                             ("payee", "COSTCO"))
        self.assertEqual(len(Database(self.path).get_transactions()), 1)

    def test_read_only_needs_upgrade_first(self):
        from sheets.client import ReadOnlyUpgradeError
        self.make_v11()
        with self.assertRaises(ReadOnlyUpgradeError):
            Database(self.path, read_only=True)


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox
    _app = QApplication.instance() or QApplication([])
    _HAVE_QT = True
except Exception:       # pragma: no cover
    _HAVE_QT = False


@unittest.skipUnless(_HAVE_QT, "PyQt6 not available")
class RuleDialogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "u.db"))
        self.chk = self.db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.sav = self.db.save_account({"name": "Savings", "type": "savings"})["id"]
        self.gas = next(c for c in self.db.get_categories() if c["name"] == "Gas")["id"]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_account_is_mandatory(self):
        from ui.dialogs.rule_dialog import RuleDialog
        dlg = RuleDialog(db=self.db)
        dlg.name_edit.setText("Costco")
        dlg._rows[0].value_edit.setText("costco")
        with mock.patch.object(QMessageBox, "warning") as warn:
            dlg._accept()
        self.assertEqual(dlg.result(), 0)
        self.assertIn("must set an account", warn.call_args[0][2])
        values = [dlg.cat_combo.itemData(i) for i in range(dlg.cat_combo.count())]
        self.assertNotIn("", values[1:])          # no "— No change —" choice
        dlg.cat_combo.select_by_data(self.gas)
        with mock.patch.object(QMessageBox, "warning") as warn:
            dlg._accept()
        warn.assert_not_called()
        self.assertEqual(dlg.result(), QDialog.DialogCode.Accepted)

    def test_conditions_rows_and_transfer_target(self):
        from ui.dialogs.rule_dialog import RuleDialog
        dlg = RuleDialog(db=self.db)
        dlg.name_edit.setText("Move to savings")
        dlg._rows[0].value_edit.setText("xfer")
        row = dlg._add_row({"field": "direction", "operator": "is", "value": "out"})
        row2 = dlg._add_row({"field": "account", "operator": "is", "value": self.chk})
        dlg._remove_row(row2)
        dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("any"))
        dlg.cat_combo.select_by_data("acct:" + self.sav)
        data = dlg.get_data()
        self.assertEqual(data["conditions"], [cond("payee", "contains", "xfer"),
                                              cond("direction", "is", "out")])
        self.assertEqual((data["match_mode"], data["transfer_account_id"], data["category_id"]),
                         ("any", self.sav, ""))
        saved = self.db.save_rule(data)
        dlg2 = RuleDialog(rule=next(r for r in self.db.get_rules() if r["id"] == saved["id"]),
                          db=self.db)
        self.assertEqual(len(dlg2._rows), 2)
        self.assertEqual(dlg2._rows[1].choice_combo.currentData(), "out")
        self.assertEqual(dlg2.cat_combo.current_data(), "acct:" + self.sav)

    def test_rules_page_double_click_edits(self):
        from ui.pages.rules import RulesPage
        from ui.dialogs import rule_dialog
        self.db.save_rule({"name": "Gas", "category_id": self.gas,
                           "conditions": [cond("payee", "contains", "shell"),
                                          cond("amount", "gt", "50")]})
        page = RulesPage(self.db)
        page.refresh()
        self.assertEqual(page._table.item(0, 1).text(), "Payee contains “shell” AND Amount > 50")
        self.assertEqual(page._table.item(0, 2).text(), "Gas")
        opened = []
        with mock.patch.object(rule_dialog.RuleDialog, "exec",
                               lambda self_: opened.append(self_.rule["name"]) or 0):
            page._table.cellDoubleClicked.emit(0, 1)
        self.assertEqual(opened, ["Gas"])


# ── v1.15: Apply to All Transactions with multi-condition rules ──────────────

class DateConditionTests(unittest.TestCase):
    T = {"payee": "RENT", "amount": "-1200", "date": "2026-03-15", "account_id": "CHK"}

    def test_normalize_date(self):
        for text in ("3/15/2026", "03/15/2026", "3-15-2026", "03-15-2026", "2026-03-15",
                     "2026/03/15", "3/15/26"):
            self.assertEqual(RL.normalize_date(text), "2026-03-15", text)
        for bad in ("13/5/2026", "2/30/2026", "abc", "", "15"):
            self.assertEqual(RL.normalize_date(bad), "", bad)

    def test_date_equals_accepts_typed_formats(self):
        for value in ("3/15/2026", "03-15-2026", "2026-03-15"):
            self.assertTrue(RL.condition_matches(cond("date", "equals", value), self.T), value)
        self.assertFalse(RL.condition_matches(cond("date", "equals", "3/16/2026"), self.T))
        self.assertFalse(RL.condition_matches(cond("date", "equals", "garbage"), self.T))

    def test_problem_text(self):
        self.assertEqual(RL.condition_problem(cond("date", "equals", "3/15/2026")), "")
        self.assertIn("date", RL.condition_problem(cond("date", "equals", "15th")).lower())
        self.assertEqual(RL.condition_problem(cond("date", "day_of_month", "31")), "")
        for bad in ("0", "32", "x", "1.5"):
            self.assertIn("1 and 31", RL.condition_problem(cond("date", "day_of_month", bad)))
        self.assertIn("number", RL.condition_problem(cond("amount", "gt", "abc")))


class ApplyAllTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "a.db"))
        self.chk = self.db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.sav = self.db.save_account({"name": "Savings", "type": "savings"})["id"]
        cats = {c["name"]: c["id"] for c in self.db.get_categories()}
        self.gas, self.rent, self.food = cats["Gas"], cats["Parking"], cats["Groceries"]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def add(self, payee, amount, date="2026-03-15", category_id="", h=""):
        return self.db.save_transaction({"date": date, "account_id": self.chk, "payee": payee,
                                         "amount": amount, "category_id": category_id,
                                         "import_hash": h})["id"]

    def test_multi_condition_rules_with_lock_orphan_and_dates(self):
        # ALL: payee contains SHELL, money out, amount > 20, in Checking.
        self.db.save_rule({"name": "Gas", "category_id": self.gas, "priority": "5",
                           "match_mode": "all",
                           "conditions": [cond("payee", "contains", "shell"),
                                          cond("direction", "is", "out"),
                                          cond("amount", "gt", "20"),
                                          cond("account", "is", self.chk)]})
        # ANY: rent on the 1st, or a specific date typed as M/D/YYYY.
        self.db.save_rule({"name": "Rent", "category_id": self.rent, "match_mode": "any",
                           "conditions": [cond("date", "day_of_month", "1"),
                                          cond("date", "equals", "3/20/2026")]})
        # ANY: two ways to spot a move to savings, recorded as a transfer.
        self.db.save_rule({"name": "Save", "transfer_account_id": self.sav, "match_mode": "any",
                           "conditions": [cond("payee", "contains", "xfer to sav"),
                                          cond("memo", "contains", "savings")]})
        self.db.save_category({"name": "Old Stuff", "type": "expense"})
        gone = next(c for c in self.db.get_categories() if c["name"] == "Old Stuff")["id"]

        locked = self.add("SHELL OIL", "-45", date="2025-12-20")         # locked period
        gas = self.add("SHELL OIL", "-45", h="i1")
        small = self.add("SHELL OIL", "-12")                              # fails amount > 20
        refund = self.add("SHELL OIL", "45")                              # fails money out
        rent1 = self.add("LANDLORD", "-1200", date="2026-04-01")
        rent2 = self.add("LANDLORD", "-1200", date="2026-03-20")
        orphan = self.add("SHELL OIL", "-60", category_id=gone)
        xfer = self.add("XFER TO SAV", "-100")
        keep = self.add("SHELL OIL", "-50", category_id=self.food)        # already filed
        self.db.delete_category(gone)                                     # now orphaned
        self.db.set_lock_date("2025-12-31")

        result = self.db.apply_rules_to_all()
        self.assertEqual(int(result), 5)            # gas, rent1, rent2, orphan, xfer
        self.assertEqual((result.categorized, result.skipped_locked, result.failed), (5, 1, 0))
        get = self.db.get_transaction
        self.assertEqual(get(gas)["category_id"], self.gas)
        self.assertEqual(get(orphan)["category_id"], self.gas)
        self.assertEqual(get(locked)["category_id"], "")
        self.assertEqual(get(small)["category_id"], "")
        self.assertEqual(get(refund)["category_id"], "")
        self.assertEqual(get(rent1)["category_id"], self.rent)
        self.assertEqual(get(rent2)["category_id"], self.rent)
        self.assertEqual(get(keep)["category_id"], self.food)
        self.assertEqual(get(xfer)["is_transfer"], "1")
        self.assertIn("skipped 1 in a locked period", result.message())

    def test_one_failure_does_not_stop_the_run(self):
        self.db.save_rule({"name": "Gas", "category_id": self.gas,
                           "conditions": [cond("payee", "contains", "shell")]})
        ids = [self.add("SHELL", "-10"), self.add("SHELL", "-20"), self.add("SHELL", "-30")]
        real = self.db.save_transaction

        def flaky(data):
            if float(data.get("amount") or 0) == -20:
                raise sqlite3.IntegrityError("boom")
            return real(data)
        with mock.patch.object(self.db, "save_transaction", side_effect=flaky):
            result = self.db.apply_rules_to_all()
        self.assertEqual((result.categorized, result.failed), (2, 1))
        self.assertIn("boom", result.message())
        self.assertEqual(sum(1 for i in ids if self.db.get_transaction(i)["category_id"]), 2)

    def test_uncategorized_helper_and_dashboard_count(self):
        from utils.ledger import is_uncategorized
        cats = {c["id"] for c in self.db.get_categories()}
        self.assertTrue(is_uncategorized({"category_id": ""}, cats))
        self.assertTrue(is_uncategorized({"category_id": "NOPE"}, cats))
        self.assertFalse(is_uncategorized({"category_id": self.gas}, cats))
        self.assertFalse(is_uncategorized({"category_id": "NOPE", "is_transfer": "1"}, cats))
        self.assertEqual(self.db.uncategorized_count(), 0)
        self.add("A", "-1")
        self.add("B", "-1", category_id="MISSING")
        self.assertEqual(self.db.uncategorized_count(), 2)


@unittest.skipUnless(_HAVE_QT, "PyQt6 not available")
class ApplyAllUITests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "u.db")
        self.db = Database(self.path)
        self.gas = next(c for c in self.db.get_categories() if c["name"] == "Gas")["id"]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_read_only_disables_apply(self):
        from ui.pages.rules import RulesPage
        page = RulesPage(Database(self.path, read_only=True))
        page.refresh()
        self.assertFalse(page._apply_btn.isEnabled())
        self.assertIn("read-only", page._apply_btn.toolTip())
        page = RulesPage(self.db)
        page.refresh()
        self.assertTrue(page._apply_btn.isEnabled())

    def test_apply_reports_locked(self):
        from ui.pages.rules import RulesPage
        chk = self.db.save_account({"name": "Checking", "type": "checking"})["id"]
        self.db.save_rule({"name": "Gas", "category_id": self.gas,
                           "conditions": [cond("payee", "contains", "shell")]})
        for d in ("2025-12-01", "2026-02-01"):
            self.db.save_transaction({"date": d, "account_id": chk, "payee": "SHELL",
                                      "amount": "-5"})
        self.db.set_lock_date("2025-12-31")
        page = RulesPage(self.db)
        with mock.patch.object(QMessageBox, "question",
                               return_value=QMessageBox.StandardButton.Yes), \
                mock.patch.object(QMessageBox, "information") as info:
            page._apply_all()
        self.assertEqual(info.call_args[0][2], "Categorized 1; skipped 1 in a locked period.")

    def test_rule_dialog_validates_and_normalizes_dates(self):
        from ui.dialogs.rule_dialog import RuleDialog
        dlg = RuleDialog(db=self.db)
        dlg.name_edit.setText("Rent day")
        row = dlg._rows[0]
        row.field_combo.setCurrentIndex(row.field_combo.findData("date"))
        row.value_edit.setText("15th")
        self.assertIn("date", row.problem().lower())
        row.value_edit.setText("3/15/2026")
        self.assertEqual(row.problem(), "")
        dlg.cat_combo.select_by_data(self.gas)
        self.assertEqual(dlg.get_data()["conditions"][0]["value"], "2026-03-15")
        row.op_combo.setCurrentIndex(row.op_combo.findData("day_of_month"))
        row.value_edit.setText("32")
        self.assertIn("1 and 31", row.problem())
