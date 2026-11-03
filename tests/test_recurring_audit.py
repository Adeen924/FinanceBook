import os
import shutil
import sqlite3
import tempfile
import unittest

import tests  # noqa: F401
from utils.recurring import next_occurrence, due_occurrences


class ScheduleTests(unittest.TestCase):
    def test_month_end_anchor(self):
        self.assertEqual(next_occurrence("2026-01-31", "monthly"), "2026-02-28")
        self.assertEqual(next_occurrence("2026-02-28", "monthly", anchor_day=31), "2026-03-31")
        self.assertEqual(next_occurrence("2024-02-29", "yearly"), "2025-02-28")
        self.assertEqual(next_occurrence("2026-11-15", "quarterly"), "2027-02-15")
        self.assertEqual(next_occurrence("2026-12-28", "weekly"), "2027-01-04")

    def test_due_occurrences(self):
        self.assertEqual(due_occurrences("2026-01-15", "2026-04-01", "monthly"),
                         ["2026-01-15", "2026-02-15", "2026-03-15"])
        self.assertEqual(due_occurrences("2026-01-15", "2026-12-31", "monthly",
                                         end_date="2026-02-20"), ["2026-01-15", "2026-02-15"])
        self.assertEqual(due_occurrences("2026-05-01", "2026-04-01", "monthly"), [])


class DbFeatureTests(unittest.TestCase):
    def setUp(self):
        from sheets.client import Database
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        self.db.set_session_user("Tester")
        self.bank = self.db.save_account({"name": "Checking", "type": "checking"})
        self.cats = {c["name"]: c["id"] for c in self.db.get_categories()}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_recurring_auto_post_and_reminders(self):
        db = self.db
        r = db.save_recurring({"name": "Rent", "kind": "transaction", "frequency": "monthly",
                               "next_date": "2026-01-01", "auto_post": "1",
                               "template": {"account_id": self.bank["id"], "payee": "Landlord",
                                            "amount": 1200,
                                            "category_id": self.cats["Business Property Rent"]}})
        posted = db.process_recurring("2026-03-15")
        self.assertEqual(len(posted), 3)
        self.assertEqual(round(db.account_balance(self.bank["id"]), 2), -3600.0)
        self.assertEqual(db.get_recurring(r["id"])["next_date"], "2026-04-01")
        self.assertEqual(db.process_recurring("2026-03-15"), [])          # idempotent
        db.save_recurring({"name": "Software", "kind": "bill", "frequency": "monthly",
                           "next_date": "2026-03-20", "auto_post": "0", "remind_days": 7,
                           "template": {"contact_id": "", "amount": 49,
                                        "category_id": self.cats["Software & Subscriptions"]}})
        upcoming = db.upcoming_recurring("2026-03-15", days=7)
        self.assertEqual([u["name"] for u in upcoming], ["Software"])
        rec = db.get_recurring(upcoming[0]["id"])
        db.post_recurring(rec)
        self.assertEqual(len(db.get_bills()), 1)
        self.assertEqual(db.get_recurring(rec["id"])["next_date"], "2026-04-20")

    def test_audit_log_records_who_and_what(self):
        db = self.db
        t = db.save_transaction({"date": "2026-02-01", "account_id": self.bank["id"],
                                 "payee": "A", "amount": "-10"})
        db.save_transaction(dict(t, payee="B"))
        db.delete_transaction(t["id"])
        log = db.get_audit_log(table="transactions")
        ops = [e["op"] for e in log]
        self.assertEqual(sorted(ops), ["delete", "insert", "update"])
        self.assertTrue(all(e["user"] == "Tester" for e in log))
        upd = next(e for e in log if e["op"] == "update")
        self.assertIn('"payee":"B"', upd["details"].replace(" ", ""))
        self.assertEqual(db.get_transaction(t["id"]), None)

    def test_period_lock_blocks_edits(self):
        db = self.db
        old = db.save_transaction({"date": "2025-12-15", "account_id": self.bank["id"],
                                   "payee": "Old", "amount": "-5"})
        db.set_lock_date("2025-12-31")
        with self.assertRaises(sqlite3.DatabaseError) as cm:
            db.save_transaction({"date": "2025-12-20", "account_id": self.bank["id"],
                                 "payee": "Late", "amount": "-1"})
        self.assertIn("locked", str(cm.exception))
        with self.assertRaises(sqlite3.DatabaseError):
            db.save_transaction(dict(old, payee="Edited"))
        with self.assertRaises(sqlite3.DatabaseError):
            db.delete_transaction(old["id"])
        with self.assertRaises(sqlite3.DatabaseError):   # moving a new entry back into it
            t = db.save_transaction({"date": "2026-01-05", "account_id": self.bank["id"],
                                     "payee": "New", "amount": "-1"})
            db.save_transaction(dict(t, date="2025-11-01"))
        db.set_lock_date("")
        db.save_transaction(dict(old, payee="Edited"))
        self.assertEqual(db.get_transaction(old["id"])["payee"], "Edited")


if __name__ == "__main__":
    unittest.main()
