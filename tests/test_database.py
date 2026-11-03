"""Migration safety and business-book setup, against real SQLite files."""
import os
import shutil
import sqlite3
import tempfile
import unittest

import tests  # noqa: F401
from sheets.client import Database, SCHEMA_VERSION
from sheets import backup

# Schema exactly as shipped in v1.0.22 (user_version never set).
V1_SCHEMA = """
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT DEFAULT '');
CREATE TABLE rules (id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', field TEXT NOT NULL DEFAULT 'payee',
  operator TEXT NOT NULL DEFAULT 'contains', value TEXT NOT NULL DEFAULT '', value2 TEXT DEFAULT '',
  category_id TEXT DEFAULT '', class_id TEXT DEFAULT '', priority INTEGER DEFAULT 0, active INTEGER DEFAULT 1, created_at TEXT);
CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT DEFAULT 'checking', institution TEXT DEFAULT '',
  opening_balance REAL DEFAULT 0, currency TEXT DEFAULT 'USD', active INTEGER DEFAULT 1, created_at TEXT);
CREATE TABLE transactions (id TEXT PRIMARY KEY, date TEXT, account_id TEXT, payee TEXT DEFAULT '', memo TEXT DEFAULT '',
  amount REAL DEFAULT 0, category_id TEXT DEFAULT '', class_id TEXT DEFAULT '', is_transfer INTEGER DEFAULT 0,
  transfer_pair_id TEXT DEFAULT '', reconciled INTEGER DEFAULT 0, reconcile_id TEXT DEFAULT '', notes TEXT DEFAULT '',
  import_hash TEXT DEFAULT '', created_at TEXT, loan_id TEXT DEFAULT '', principal_amount REAL DEFAULT 0,
  interest_amount REAL DEFAULT 0, split_group_id TEXT DEFAULT '');
CREATE TABLE categories (id TEXT PRIMARY KEY, name TEXT NOT NULL, parent_id TEXT DEFAULT '', type TEXT DEFAULT 'expense', active INTEGER DEFAULT 1);
CREATE TABLE classes (id TEXT PRIMARY KEY, name TEXT NOT NULL, parent_id TEXT DEFAULT '', active INTEGER DEFAULT 1);
CREATE TABLE reconciliations (id TEXT PRIMARY KEY, account_id TEXT, statement_date TEXT, statement_balance REAL DEFAULT 0,
  cleared_balance REAL DEFAULT 0, difference REAL DEFAULT 0, status TEXT DEFAULT 'open', created_at TEXT, completed_at TEXT DEFAULT '');
CREATE TABLE loans (id TEXT PRIMARY KEY, name TEXT NOT NULL, lender TEXT DEFAULT '', original_principal REAL DEFAULT 0,
  annual_rate REAL DEFAULT 0, term_months INTEGER DEFAULT 0, start_date TEXT DEFAULT '', payment_amount REAL DEFAULT 0,
  account_id TEXT DEFAULT '', interest_category_id TEXT DEFAULT '', created_at TEXT DEFAULT '');
INSERT INTO settings VALUES ('database_name','Personal Finances'),('default_categories_seeded','1');
INSERT INTO accounts VALUES ('A1','Checking','checking','Chase',1000.5,'USD',1,'2025-01-01 00:00:00');
INSERT INTO accounts VALUES ('A2','Visa','credit card','Chase',-200,'USD',1,'2025-01-01 00:00:00');
INSERT INTO categories VALUES ('C1','Groceries','','expense',1),('C2','Salary','','income',1);
INSERT INTO transactions (id,date,account_id,payee,amount,category_id,created_at)
  VALUES ('T1','2025-02-01','A1','Store',-45.67,'C1','x'),('T2','2025-02-02','A1','Job',2000,'C2','y');
"""


def snapshot(path):
    conn = sqlite3.connect(path)
    try:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                for t in ("accounts", "transactions", "categories", "rules", "loans")}
    finally:
        conn.close()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "finances.db")
        conn = sqlite3.connect(self.path)
        conn.executescript(V1_SCHEMA)
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_upgrade_backs_up_and_keeps_data(self):
        before = snapshot(self.path)
        db = Database(self.path)
        self.assertTrue(db.last_migration_backup)
        self.assertTrue(os.path.exists(db.last_migration_backup))
        # The backup is the untouched v1 file.
        self.assertEqual(snapshot(db.last_migration_backup), before)
        # Original columns of every existing row are unchanged.
        after = snapshot(self.path)
        for table, rows in before.items():
            self.assertEqual([r[:len(rows[0])] if rows else r for r in after[table]][:len(rows)],
                             rows, table)
        self.assertEqual(db.book_type(), "personal")
        self.assertEqual(len(db.get_categories()), 2)   # no seeding on top of user data
        conn = sqlite3.connect(self.path)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()

    def test_second_open_does_not_back_up_again(self):
        Database(self.path)
        db2 = Database(self.path)
        self.assertEqual(db2.last_migration_backup, "")
        self.assertEqual(len(backup.list_backups(self.path)), 1)

    def test_old_code_paths_do_not_blank_new_columns(self):
        db = Database(self.path)
        t = db.get_transaction("T1")
        db.save_transaction(dict(t, paid_by="P1", reimbursable="1"))
        # A caller that doesn't know about paid_by (e.g. quick-categorize).
        legacy = {k: v for k, v in db.get_transaction("T1").items()
                  if k not in ("paid_by", "charged_to", "reimbursable")}
        legacy["category_id"] = "C1"
        db.save_transaction(legacy)
        t = db.get_transaction("T1")
        self.assertEqual(t["paid_by"], "P1")
        self.assertEqual(t["reimbursable"], "1")

    def test_switch_to_business_is_additive(self):
        db = Database(self.path)
        before = snapshot(self.path)
        db.set_book_type("business")
        after = snapshot(self.path)
        self.assertEqual(len(after["transactions"]), len(before["transactions"]))
        self.assertTrue(set(before["categories"]) <= set(
            r for r in after["categories"]) or len(after["categories"]) > len(before["categories"]))
        roles = {a.get("role") for a in db.get_accounts()}
        self.assertTrue({"owner_capital", "owner_draws", "due_to_owner", "petty_cash"} <= roles)
        db.set_book_type("business")    # idempotent
        self.assertEqual(len([a for a in db.get_accounts() if a.get("role") == "due_to_owner"]), 1)
        self.assertEqual(len(db.get_people()), 1)


class NewBookTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_business_book_seeds_schedule_c(self):
        db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        cats = {c["name"]: c for c in db.get_categories()}
        self.assertEqual(cats["Meals (50%)"]["tax_line"], "24b")
        self.assertEqual(float(cats["Meals (50%)"]["deduct_pct"]), 50.0)
        self.assertEqual(cats["Equipment Rental"]["tax_line"], "20a")
        self.assertNotIn("Groceries", cats)       # no personal tree
        self.assertEqual(db.get_person(db.owner_person_id())["name"], "Owner")

    def test_personal_book_unchanged(self):
        db = Database(os.path.join(self.dir, "p.db"))
        self.assertEqual(db.book_type(), "personal")
        self.assertFalse(any(a.get("role") for a in db.get_accounts()))
        self.assertIn("Groceries", {c["name"] for c in db.get_categories()})

    def test_split_keeps_owner_fields(self):
        db = Database(os.path.join(self.dir, "biz.db"), book_type="business")
        cats = {c["name"]: c["id"] for c in db.get_categories()}
        due = db.get_account_by_role("due_to_owner")
        base = {"date": "2026-03-01", "account_id": due["id"], "payee": "Staples",
                "amount": "-30", "paid_by": db.owner_person_id(), "reimbursable": "1"}
        lines = db.split_transaction(base, [
            {"category_id": cats["Supplies"], "amount": "20"},
            {"category_id": cats["Office Expense"], "amount": "10", "charged_to": "class:X"}])
        self.assertTrue(all(t["paid_by"] == db.owner_person_id() for t in lines))
        self.assertEqual(lines[1]["charged_to"], "class:X")
        self.assertEqual(db.account_balance(due["id"]), -30.0)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "shop.db")
        self.db = Database(self.path, book_type="business")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_auto_backup_prunes_and_respects_interval(self):
        self.db.set_setting("auto_backup_keep", "3")
        self.assertTrue(self.db.auto_backup_if_due())
        self.assertEqual(self.db.auto_backup_if_due(), "")   # not due yet
        self.db.set_setting("auto_backup_hours", "0")
        for _ in range(5):
            self.db.auto_backup_if_due()
        self.assertEqual(len(backup.list_backups(self.path, "auto")), 3)

    def test_backups_of_similar_names_are_separate(self):
        other = Database(os.path.join(self.dir, "shop-east.db"))
        other.backup_now()
        self.db.backup_now()
        self.assertEqual(len(backup.list_backups(self.path)), 1)

    def test_restore(self):
        snap = self.db.backup_now()
        self.db.save_account({"name": "New Bank", "type": "checking"})
        safety = backup.restore_database(snap, self.path)
        self.assertTrue(os.path.exists(safety))
        names = [a["name"] for a in Database(self.path).get_accounts()]
        self.assertNotIn("New Bank", names)

    def test_restore_rejects_non_financebook_file(self):
        junk = os.path.join(self.dir, "junk.db")
        sqlite3.connect(junk).execute("CREATE TABLE x (a)").connection.close()
        with self.assertRaises(ValueError):
            backup.restore_database(junk, self.path)


if __name__ == "__main__":
    unittest.main()
