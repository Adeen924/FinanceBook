"""v1.15.1: a new bank rule matches Any of its conditions by default. Existing
rules keep their saved mode, and a rule stored with a blank match_mode (saved
before v1.14) still means All."""
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database
from utils import rules as RL

try:
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    _HAVE_QT = True
except Exception:       # pragma: no cover
    _HAVE_QT = False


def cond(field, op, value, value2=""):
    return {"field": field, "operator": op, "value": value, "value2": value2}


class StoredModeTests(unittest.TestCase):
    """The blank-means-All fallback, without Qt."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "r.db"))
        self.food = next(c for c in self.db.get_categories()
                         if c["name"] == "Restaurants")["id"]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_default_for_new_rules_is_any(self):
        self.assertEqual(RL.DEFAULT_NEW_MATCH_MODE, "any")

    def test_blank_stored_mode_still_means_all(self):
        rule = self.db.save_rule({"name": "Two", "category_id": self.food,
                                  "match_mode": "any",
                                  "conditions": [cond("payee", "contains", "taco"),
                                                 cond("amount", "gt", "50")]})
        with self.db._conn() as c:
            c.execute("UPDATE rules SET match_mode='' WHERE id=?", (rule["id"],))
        got = next(r for r in self.db.get_rules() if r["id"] == rule["id"])
        self.assertEqual(got["match_mode"], "all")
        small_taco = {"payee": "TACO BELL", "amount": "-12"}
        self.assertFalse(RL.rule_matches(got, small_taco))
        self.assertFalse(RL.rule_matches(dict(got, match_mode=""), small_taco))
        self.assertTrue(RL.rule_matches(got, dict(small_taco, amount="-60")))
        # save_rule with no / a bad mode keeps the old All meaning too.
        self.assertEqual(self.db.save_rule({"name": "Legacy", "category_id": self.food,
                                            "value": "x"})["match_mode"], "all")
        self.assertEqual(self.db.save_rule({"name": "Bad", "category_id": self.food,
                                            "value": "x", "match_mode": "?"})["match_mode"],
                         "all")


@unittest.skipUnless(_HAVE_QT, "PyQt6 not available")
class RuleDialogDefaultTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "d.db"))
        self.food = next(c for c in self.db.get_categories()
                         if c["name"] == "Restaurants")["id"]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _dialog(self, rule=None):
        from ui.dialogs.rule_dialog import RuleDialog
        return RuleDialog(rule=rule, db=self.db)

    def test_new_rule_defaults_to_any(self):
        dlg = self._dialog()
        self.assertEqual([dlg.mode_combo.itemText(i) for i in range(dlg.mode_combo.count())],
                         ["Any", "All"])
        self.assertEqual(dlg.mode_combo.currentData(), "any")
        dlg.name_edit.setText("Food")
        dlg._rows[0].value_edit.setText("chipotle")
        dlg.cat_combo.select_by_data(self.food)
        self.assertEqual(dlg.get_data()["match_mode"], "any")

    def test_all_still_selectable(self):
        dlg = self._dialog()
        dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("all"))
        dlg.name_edit.setText("Both")
        dlg._rows[0].value_edit.setText("x")
        dlg.cat_combo.select_by_data(self.food)
        self.assertEqual(self.db.save_rule(dlg.get_data())["match_mode"], "all")

    def test_existing_all_rule_keeps_all(self):
        saved = self.db.save_rule({"name": "Costco gas", "category_id": self.food,
                                   "match_mode": "all",
                                   "conditions": [cond("payee", "contains", "costco"),
                                                  cond("amount", "gt", "50")]})
        rule = next(r for r in self.db.get_rules() if r["id"] == saved["id"])
        dlg = self._dialog(rule)
        self.assertEqual(dlg.mode_combo.currentData(), "all")
        self.assertEqual(dlg.get_data()["match_mode"], "all")

    def test_existing_blank_mode_rule_opens_as_all(self):
        dlg = self._dialog({"id": "R1", "name": "Old", "match_mode": "",
                            "category_id": self.food,
                            "conditions": [cond("payee", "contains", "old")]})
        self.assertEqual(dlg.mode_combo.currentData(), "all")
        dlg2 = self._dialog({"id": "R2", "name": "Older", "category_id": self.food,
                             "field": "payee", "operator": "contains", "value": "older"})
        self.assertEqual(dlg2.mode_combo.currentData(), "all")

    def test_three_restaurants_with_default_settings(self):
        dlg = self._dialog()
        dlg.name_edit.setText("Eating out")
        dlg._rows[0].value_edit.setText("CHIPOTLE")
        for name in ("PANERA", "SUBWAY"):
            row = dlg._add_row({"field": "payee", "operator": "contains"})
            row.value_edit.setText(name)
        dlg.cat_combo.select_by_data(self.food)
        self.db.save_rule(dlg.get_data())

        txns = [{"payee": "CHIPOTLE 1234 AUSTIN", "amount": "-14.20", "date": "2026-05-01"},
                {"payee": "PANERA BREAD #55", "amount": "-9.80", "date": "2026-05-02"},
                {"payee": "SUBWAY 00321", "amount": "-7.45", "date": "2026-05-03"},
                {"payee": "SHELL OIL", "amount": "-40.00", "date": "2026-05-04"}]
        self.db.apply_rules(txns)
        self.assertEqual([t.get("category_id") or "" for t in txns],
                         [self.food, self.food, self.food, ""])


if __name__ == "__main__":
    unittest.main()
