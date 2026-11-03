"""Connecting an existing database: read-only validation, registration,
read-only mode, a two-laptop round trip, and sync-safe journal files."""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

import tests  # noqa: F401
import db_config
import db_connect
import db_lock
from sheets import backup
from sheets.client import Database, SCHEMA_VERSION, NewerSchemaError, ReadOnlyUpgradeError
from tests.test_database import V1_SCHEMA

SIDE_FILES = ("-wal", "-shm", "-journal")


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def side_files(path):
    return [s for s in SIDE_FILES if os.path.exists(path + s)]


def make_v1(path):
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.commit()
    conn.close()


def make_current(path, payee="Coffee"):
    db = Database(path)
    acct = db.save_account({"name": "Checking"})
    db.save_transaction({"date": "2025-03-01", "account_id": acct["id"],
                         "payee": payee, "amount": -4.5})
    db.close()
    return acct


def payees(path):
    conn = sqlite3.connect(backup.ro_uri(path), uri=True)
    try:
        return sorted(r[0] for r in conn.execute("SELECT payee FROM transactions"))
    finally:
        conn.close()


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig_settings = db_config.SETTINGS_PATH
        db_config.SETTINGS_PATH = os.path.join(self.dir, "app_settings.json")

    def tearDown(self):
        db_lock._release_all()
        db_config.SETTINGS_PATH = self._orig_settings
        shutil.rmtree(self.dir, ignore_errors=True)


class ValidationTests(TempDirCase):
    def test_missing_file(self):
        chk = db_connect.validate_existing_database(os.path.join(self.dir, "nope.db"))
        self.assertFalse(chk.ok)
        self.assertIn("Can't find", chk.error)

    def test_not_a_sqlite_file(self):
        p = os.path.join(self.dir, "notes.db")
        with open(p, "w") as f:
            f.write("just some text, not a database\n" * 10)
        chk = db_connect.validate_existing_database(p)
        self.assertIn("not a FinanceBook database", chk.error)

    def test_sqlite_without_financebook_tables(self):
        p = os.path.join(self.dir, "other.db")
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE photos (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()
        before = digest(p)
        chk = db_connect.validate_existing_database(p)
        self.assertIn("not a FinanceBook database", chk.error)
        self.assertEqual(digest(p), before)

    def test_zero_byte_file(self):
        p = os.path.join(self.dir, "finances.db")
        open(p, "wb").close()
        chk = db_connect.validate_existing_database(p)
        self.assertIn("empty", chk.error)
        self.assertIn("Always keep on this device", chk.error)
        self.assertEqual(os.path.getsize(p), 0)

    def test_newer_schema_refused_everywhere(self):
        p = os.path.join(self.dir, "finances.db")
        make_current(p)
        conn = sqlite3.connect(p)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        before = digest(p)
        chk = db_connect.validate_existing_database(p)
        self.assertIn("newer version of FinanceBook", chk.error)
        # The data-layer guard: no code path may open it with this older app.
        with self.assertRaises(NewerSchemaError):
            Database(p)
        with self.assertRaises(NewerSchemaError):
            Database(p, read_only=True)
        self.assertEqual(digest(p), before)
        self.assertFalse(os.path.isdir(backup.backup_dir(p)))

    def test_older_schema_needs_upgrade_and_is_backed_up(self):
        p = os.path.join(self.dir, "finances.db")
        make_v1(p)
        before = digest(p)
        chk = db_connect.validate_existing_database(p)
        self.assertTrue(chk.ok, chk.error)
        self.assertTrue(chk.needs_upgrade)
        self.assertEqual(chk.version, 0)
        self.assertEqual(digest(p), before, "validation must not modify the file")
        db = Database(p)
        self.assertTrue(db.last_migration_backup)
        self.assertTrue(os.path.exists(db.last_migration_backup))
        self.assertEqual(backup.read_user_version(p), SCHEMA_VERSION)
        self.assertEqual(payees(db.last_migration_backup), ["Job", "Store"])

    def test_current_schema_is_ok_and_untouched(self):
        p = os.path.join(self.dir, "finances.db")
        make_current(p)
        before = digest(p)
        chk = db_connect.validate_existing_database(p)
        self.assertTrue(chk.ok, chk.error)
        self.assertFalse(chk.needs_upgrade)
        self.assertEqual(chk.warnings, [])
        self.assertEqual(digest(p), before)
        self.assertEqual(side_files(p), [])
        self.assertFalse(os.path.exists(db_lock.lock_path(p)))

    def test_path_with_spaces_unicode_and_symbols(self):
        folder = os.path.join(self.dir, "OneDrive - Famille Ünïcødé", "Finances #1 & 100%")
        os.makedirs(folder)
        p = os.path.join(folder, "my finances €.db")
        make_current(p)
        chk = db_connect.validate_existing_database(p)
        self.assertTrue(chk.ok, chk.error)
        self.assertEqual(backup.read_setting_readonly(p, "default_categories_seeded"), "1")
        db_config.register_database(p, make_active=True)
        self.assertEqual(db_config.get_active_db_path(), os.path.abspath(p))
        self.assertEqual(db_config.get_known_databases(), [os.path.abspath(p)])
        self.assertEqual(payees(p), ["Coffee"])

    def test_fairly_long_path(self):
        # Paths over 260 characters need Windows long-path support turned on,
        # which is an OS setting; stay just under the classic limit here.
        folder = self.dir
        while len(folder) < 200:
            folder = os.path.join(folder, "sub folder " + str(len(folder)))
        os.makedirs(folder)
        p = os.path.join(folder, "finances.db")
        make_current(p)
        self.assertTrue(db_connect.validate_existing_database(p).ok)

    @unittest.skipUnless(sys.platform == "win32", "Windows file attributes")
    def test_cloud_placeholder_refused(self):
        import ctypes
        p = os.path.join(self.dir, "finances.db")
        make_current(p)
        FILE_ATTRIBUTE_OFFLINE, FILE_ATTRIBUTE_NORMAL = 0x1000, 0x80
        self.assertTrue(ctypes.windll.kernel32.SetFileAttributesW(p, FILE_ATTRIBUTE_OFFLINE))
        try:
            self.assertTrue(db_connect.is_cloud_placeholder(p))
            chk = db_connect.validate_existing_database(p)
            self.assertIn("online-only", chk.error)
        finally:
            ctypes.windll.kernel32.SetFileAttributesW(p, FILE_ATTRIBUTE_NORMAL)
        self.assertFalse(db_connect.is_cloud_placeholder(p))

    def test_conflict_copies_detected(self):
        orig = os.path.join(self.dir, "finances.db")
        copy = os.path.join(self.dir, "finances-DESKTOP-AB12CD.db")
        copy2 = os.path.join(self.dir, "finances (1).db")
        unrelated = os.path.join(self.dir, "finances-2024.db")
        for p in (orig, copy, copy2, unrelated):
            make_current(p)
        self.assertEqual(db_connect.conflict_original(copy), orig)
        self.assertEqual(db_connect.conflict_original(copy2), orig)
        self.assertEqual(db_connect.conflict_original(unrelated), "")
        self.assertEqual(db_connect.conflict_copies(orig), sorted([copy, copy2]))
        w = db_connect.validate_existing_database(copy).warnings
        self.assertTrue(any("conflict copy" in x for x in w))
        w = db_connect.validate_existing_database(orig).warnings
        self.assertTrue(any("DESKTOP-AB12CD" in x for x in w))
        self.assertEqual(db_connect.validate_existing_database(unrelated).warnings, [])

    def test_leftover_wal_warns_and_is_merged_on_open(self):
        src = os.path.join(self.dir, "a", "finances.db")
        os.makedirs(os.path.dirname(src))
        acct = make_current(src)
        # An older FinanceBook (WAL mode) killed with changes still in -wal.
        conn = sqlite3.connect(src)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO transactions (id, date, account_id, payee, amount) "
                     "VALUES ('W1', '2025-03-02', ?, 'Only in WAL', -1)", (acct["id"],))
        conn.commit()
        dst = os.path.join(self.dir, "b", "finances.db")
        os.makedirs(os.path.dirname(dst))
        shutil.copy(src, dst)
        shutil.copy(src + "-wal", dst + "-wal")
        conn.close()
        _write_foreign_lock(dst)
        chk = db_connect.validate_existing_database(dst)
        self.assertTrue(chk.ok)
        self.assertTrue(any("-wal" in w and "LAPTOP-B" in w for w in chk.warnings), chk.warnings)
        os.remove(db_lock.lock_path(dst))
        db = Database(dst)
        db.close()
        self.assertIn("Only in WAL", payees(dst))
        self.assertEqual(side_files(dst), [])


def _write_foreign_lock(db_path, machine="LAPTOP-B"):
    import time
    with open(db_lock.lock_path(db_path), "w", encoding="utf-8") as f:
        json.dump({"machine": machine, "user": "sam", "pid": 1, "acquired_at": time.time(),
                   "last_heartbeat": time.time(), "session_id": "foreign"}, f)


class RegisterTests(TempDirCase):
    def test_registers_once_and_sets_active(self):
        p = os.path.join(self.dir, "Shared", "finances.db")
        os.makedirs(os.path.dirname(p))
        make_current(p)
        before = digest(p)
        db_config.register_database(p, make_active=True)
        db_config.register_database(p.upper(), make_active=True)
        db_config.register_database(os.path.join(self.dir, "Shared", ".", "finances.db"))
        with open(db_config.SETTINGS_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(len(cfg["known_databases"]), 1)
        self.assertEqual(cfg["active_db_path"], os.path.abspath(p))
        self.assertTrue(db_config.is_known_database(p.lower()))
        self.assertEqual(digest(p), before)

    def test_reconnecting_known_database_just_switches(self):
        a = os.path.join(self.dir, "a.db")
        b = os.path.join(self.dir, "b.db")
        make_current(a)
        make_current(b)
        db_config.register_database(a, make_active=True)
        db_config.register_database(b, make_active=True)
        db_config.register_database(a, make_active=True)
        self.assertEqual(db_config.get_active_db_path(), os.path.abspath(a))
        self.assertEqual(sorted(db_config.get_known_databases()),
                         sorted([os.path.abspath(a), os.path.abspath(b)]))

    def test_settings_dir_env_override(self):
        import subprocess
        out = subprocess.run(
            [sys.executable, "-c", "import db_config; print(db_config.SETTINGS_DIR)"],
            cwd=tests.APP_DIR, capture_output=True, text=True,
            env=dict(os.environ, FINANCEBOOK_SETTINGS_DIR=os.path.join(self.dir, "lapB")))
        self.assertEqual(out.stdout.strip(), os.path.join(self.dir, "lapB"))
        out = subprocess.run(
            [sys.executable, "-c", "import db_config; print(db_config.SETTINGS_DIR)"],
            cwd=tests.APP_DIR, capture_output=True, text=True,
            env={k: v for k, v in os.environ.items() if k != "FINANCEBOOK_SETTINGS_DIR"})
        self.assertEqual(out.stdout.strip(), tests.APP_DIR)


class ReadOnlyTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.dir, "finances.db")
        self.acct = make_current(self.path)

    def test_open_read_only_changes_nothing(self):
        before = digest(self.path)
        db = Database(self.path, read_only=True)
        self.assertEqual(len(db.get_transactions()), 1)
        self.assertEqual(db.get_setting("default_categories_seeded"), "1")
        db.set_session_user("someone")
        self.assertEqual(db.auto_backup_if_due(), "")
        db.close()
        self.assertEqual(digest(self.path), before)
        self.assertEqual(side_files(self.path), [])
        self.assertFalse(os.path.isdir(backup.backup_dir(self.path)))

    def test_writes_fail_with_readonly_error(self):
        db = Database(self.path, read_only=True)
        for write in (lambda: db.save_account({"name": "X"}),
                      lambda: db.set_setting("k", "v"),
                      lambda: db.delete_transaction(db.get_transactions()[0]["id"]),
                      lambda: db.clear_all_data()):
            with self.assertRaises(sqlite3.OperationalError) as cm:
                write()
            self.assertIn("readonly database", str(cm.exception))
        self.assertEqual(payees(self.path), ["Coffee"])

    def test_flag_flipped_on_live_object_blocks_writes(self):
        db = Database(self.path)
        db.read_only = True
        with self.assertRaises(sqlite3.OperationalError):
            db.save_account({"name": "X"})

    def test_no_migration_read_only(self):
        old = os.path.join(self.dir, "old.db")
        make_v1(old)
        before = digest(old)
        with self.assertRaises(ReadOnlyUpgradeError):
            Database(old, read_only=True)
        self.assertEqual(digest(old), before)
        self.assertFalse(os.path.isdir(backup.backup_dir(old)))

    def test_missing_file_read_only_is_not_created(self):
        p = os.path.join(self.dir, "missing.db")
        with self.assertRaises(FileNotFoundError):
            Database(p, read_only=True)
        self.assertFalse(os.path.exists(p))

    def test_no_auto_backup_or_recurring_posting(self):
        db = Database(self.path)
        db.save_recurring({"name": "Rent", "kind": "transaction", "frequency": "monthly",
                           "next_date": "2025-01-01", "auto_post": 1, "active": 1,
                           "template": {"account_id": self.acct["id"], "payee": "Landlord",
                                        "amount": -1000}})
        db.close()
        ro = Database(self.path, read_only=True)
        self.assertEqual(ro.process_recurring("2025-06-01"), [])
        self.assertEqual(ro.auto_backup_if_due(), "")
        self.assertEqual(payees(self.path), ["Coffee"])
        self.assertFalse(os.path.isdir(backup.backup_dir(self.path)))
        # The writable copy does post them — proving the item really was due.
        self.assertTrue(Database(self.path).process_recurring("2025-06-01"))

    def test_restore_refuses_backup_from_newer_app(self):
        newer = os.path.join(self.dir, "newer.db")
        make_current(newer)
        conn = sqlite3.connect(newer)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        before = digest(self.path)
        with self.assertRaises(ValueError):
            backup.restore_database(newer, self.path)
        self.assertEqual(digest(self.path), before)


class TwoLaptopRoundTripTests(TempDirCase):
    """A creates in a shared "OneDrive" folder, B connects and edits, A sees it."""

    def laptop(self, name):
        db_config.SETTINGS_PATH = os.path.join(self.dir, f"settings-{name}", "app_settings.json")
        os.makedirs(os.path.dirname(db_config.SETTINGS_PATH), exist_ok=True)
        return mock.patch.dict(os.environ, {"FINANCEBOOK_MACHINE_NAME": name})

    def test_round_trip(self):
        shared = os.path.join(self.dir, "OneDrive", "Finances")
        os.makedirs(shared)
        path = os.path.join(shared, "finances.db")

        with self.laptop("LAPTOP-A"):
            lock = db_lock.DbLock(path)
            lock.acquire()
            db = Database(path)
            db.set_setting("database_name", "Family")
            acct = db.save_account({"name": "Joint Checking"})
            db.save_transaction({"date": "2025-04-01", "account_id": acct["id"],
                                 "payee": "Groceries", "amount": -82.10})
            db_config.register_database(path, make_active=True)
            with self.laptop("LAPTOP-B"):
                with self.assertRaises(db_lock.LockHeld) as cm:
                    db_lock.DbLock(path).acquire()
                self.assertEqual(cm.exception.info["machine"], "LAPTOP-A")
            db.close()
            lock.release()
        self.assertEqual(side_files(path), [])
        self.assertFalse(os.path.exists(db_lock.lock_path(path)))

        with self.laptop("LAPTOP-B"):
            self.assertIsNone(db_config.get_active_db_path())
            chk = db_connect.validate_existing_database(path)
            self.assertTrue(chk.ok and not chk.warnings and not chk.needs_upgrade)
            db_config.register_database(chk.path, make_active=True)
            lock = db_lock.DbLock(path)
            lock.acquire()
            db = Database(path)
            self.assertEqual(db.get_setting("database_name"), "Family")
            self.assertEqual([t["payee"] for t in db.get_transactions()], ["Groceries"])
            db.save_transaction({"date": "2025-04-02", "account_id": acct["id"],
                                 "payee": "Added on B", "amount": -5})
            db.close()
            lock.release()
            self.assertEqual(db_config.get_active_db_path(), os.path.abspath(path))
        self.assertEqual(side_files(path), [])

        # The file alone (as OneDrive would sync it) must hold B's change.
        plain = sqlite3.connect(path)
        try:
            self.assertEqual(plain.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(plain.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 2)
        finally:
            plain.close()

        with self.laptop("LAPTOP-A"):
            self.assertEqual(db_config.get_active_db_path(), os.path.abspath(path))
            lock = db_lock.DbLock(path)
            lock.acquire()
            db = Database(path)
            self.assertEqual(sorted(t["payee"] for t in db.get_transactions()),
                             ["Added on B", "Groceries"])
            db.close()
            lock.release()


class JournalModeTests(TempDirCase):
    def test_no_side_files_after_close_and_data_in_main_file(self):
        p = os.path.join(self.dir, "finances.db")
        make_current(p, payee="Saved")
        self.assertEqual(side_files(p), [])
        conn = sqlite3.connect(p)
        try:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(conn.execute("SELECT payee FROM transactions").fetchone()[0], "Saved")
        finally:
            conn.close()

    def test_legacy_wal_database_is_converted(self):
        p = os.path.join(self.dir, "finances.db")
        make_current(p)
        conn = sqlite3.connect(p)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
        db = Database(p)
        db.save_account({"name": "After"})
        db.close()
        self.assertEqual(side_files(p), [])
        conn = sqlite3.connect(p)
        try:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        finally:
            conn.close()

    def test_backup_and_restore_leave_no_side_files(self):
        p = os.path.join(self.dir, "finances.db")
        make_current(p, payee="Original")
        bak = backup.backup_database(p, "manual")
        self.assertEqual(side_files(bak), [])
        db = Database(p)
        db.save_transaction({"date": "2025-05-01", "account_id": db.get_accounts()[0]["id"],
                             "payee": "Later", "amount": -1})
        db.close()
        backup.restore_database(bak, p)
        db = Database(p)
        db.close()
        self.assertEqual(payees(p), ["Original"])
        self.assertEqual(side_files(p), [])


if __name__ == "__main__":
    unittest.main()
