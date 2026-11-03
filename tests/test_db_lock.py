"""The database lock file: acquire/release, stale detection, take-over,
heartbeat, and a real second process holding the lock."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import tests
import db_lock
from db_lock import DbLock, LockHeld

_CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
import db_lock
lock = db_lock.DbLock(sys.argv[2])
lock.acquire()
print("locked", flush=True)
if sys.argv[3] == "wait":
    sys.stdin.readline()
    lock.release()
    print("released", flush=True)
else:
    time.sleep(60)
"""


def _write_lock(db_path, **fields):
    info = {"machine": "LAPTOP-B", "user": "sam", "pid": 4242, "app_version": "1.8.1",
            "acquired_at": time.time() - 600, "last_heartbeat": time.time(),
            "session_id": "other-session"}
    info.update(fields)
    with open(db_lock.lock_path(db_path), "w", encoding="utf-8") as f:
        json.dump(info, f)
    return info


class LockTestCase(unittest.TestCase):
    machine = "LAPTOP-A"

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "finances.db")
        open(self.db, "wb").close()
        self._env = mock.patch.dict(os.environ, {"FINANCEBOOK_MACHINE_NAME": self.machine})
        self._env.start()

    def tearDown(self):
        db_lock._release_all()
        self._env.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def read(self):
        with open(db_lock.lock_path(self.db), encoding="utf-8") as f:
            return json.load(f)


class AcquireReleaseTests(LockTestCase):
    def test_acquire_writes_lock_with_details(self):
        lock = DbLock(self.db)
        lock.acquire()
        info = self.read()
        self.assertTrue(lock.owned)
        self.assertEqual(info["machine"], "LAPTOP-A")
        self.assertEqual(info["pid"], os.getpid())
        self.assertEqual(info["session_id"], lock.session_id)
        for key in ("user", "app_version", "acquired_at", "last_heartbeat"):
            self.assertIn(key, info)
        self.assertTrue(db_lock.lock_path(self.db).endswith("finances.db.lock"))

    def test_release_removes_file_and_is_idempotent(self):
        lock = DbLock(self.db)
        lock.acquire()
        lock.release()
        self.assertFalse(os.path.exists(db_lock.lock_path(self.db)))
        lock.release()
        self.assertFalse(lock.owned)

    def test_second_acquirer_on_same_machine_is_refused(self):
        first = DbLock(self.db)
        first.acquire()
        with self.assertRaises(LockHeld) as cm:
            DbLock(self.db).acquire()
        self.assertTrue(cm.exception.same_machine)
        self.assertEqual(self.read()["session_id"], first.session_id)

    def test_live_lock_from_other_machine_is_refused(self):
        _write_lock(self.db)
        with self.assertRaises(LockHeld) as cm:
            DbLock(self.db).acquire()
        self.assertFalse(cm.exception.same_machine)
        text = db_lock.describe(cm.exception.info)
        self.assertIn("LAPTOP-B", text)
        self.assertIn("sam", text)
        self.assertIn("last active just now", text)

    def test_other_machine_dead_pid_is_not_trusted(self):
        # We can't see the other laptop's processes — only its heartbeat counts.
        _write_lock(self.db, pid=999999)
        with self.assertRaises(LockHeld):
            DbLock(self.db).acquire()

    def test_reacquiring_own_lock_is_fine(self):
        lock = DbLock(self.db)
        lock.acquire()
        lock.owned = False      # e.g. state lost, file still ours
        lock.acquire()
        self.assertTrue(lock.owned)


class StaleLockTests(LockTestCase):
    def test_stale_by_heartbeat_is_replaced(self):
        _write_lock(self.db, last_heartbeat=time.time() - db_lock.STALE_AFTER_SECONDS - 5)
        lock = DbLock(self.db)
        lock.acquire()
        self.assertEqual(self.read()["session_id"], lock.session_id)

    def test_just_under_threshold_is_still_live(self):
        _write_lock(self.db, last_heartbeat=time.time() - db_lock.STALE_AFTER_SECONDS + 30)
        with self.assertRaises(LockHeld):
            DbLock(self.db).acquire()

    def test_heartbeat_in_the_future_counts_as_live(self):
        # The other laptop's clock is ahead — don't treat that as stale.
        _write_lock(self.db, last_heartbeat=time.time() + 3600)
        with self.assertRaises(LockHeld):
            DbLock(self.db).acquire()

    def test_stale_by_dead_pid_on_this_machine(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertFalse(db_lock.pid_alive(p.pid))
        _write_lock(self.db, machine="LAPTOP-A", pid=p.pid)
        lock = DbLock(self.db)
        lock.acquire()
        self.assertEqual(self.read()["session_id"], lock.session_id)

    def test_corrupt_empty_or_odd_lock_files_are_stale(self):
        for content in ("", "{not json", "[]", '{"machine": "X"}', "\x00\x00\x00"):
            with self.subTest(content=content):
                with open(db_lock.lock_path(self.db), "w", encoding="utf-8") as f:
                    f.write(content)
                self.assertEqual(db_lock.read_lock(self.db), {"corrupt": True})
                lock = DbLock(self.db)
                lock.acquire()
                self.assertEqual(self.read()["session_id"], lock.session_id)
                lock.release()

    def test_missing_lock_reads_as_none(self):
        self.assertIsNone(db_lock.read_lock(self.db))
        self.assertTrue(db_lock.is_stale(None))

    def test_pid_alive(self):
        self.assertTrue(db_lock.pid_alive(os.getpid()))
        self.assertFalse(db_lock.pid_alive(0))


class TakeOverAndHeartbeatTests(LockTestCase):
    def test_take_over_and_original_owner_detects_loss(self):
        a = DbLock(self.db)
        a.acquire()
        b = DbLock(self.db)
        with mock.patch.dict(os.environ, {"FINANCEBOOK_MACHINE_NAME": "LAPTOP-B"}):
            with self.assertRaises(LockHeld):
                b.acquire()
            b.acquire(force=True)
        self.assertEqual(self.read()["machine"], "LAPTOP-B")
        self.assertFalse(a.still_ours())
        self.assertFalse(a.heartbeat())
        self.assertFalse(a.owned)
        a.release()                         # must not delete B's lock
        self.assertEqual(self.read()["session_id"], b.session_id)

    def test_heartbeat_refreshes_timestamp(self):
        lock = DbLock(self.db)
        lock.acquire()
        info = self.read()
        info["last_heartbeat"] = time.time() - 500
        with open(lock.path, "w", encoding="utf-8") as f:
            json.dump(info, f)
        self.assertTrue(lock.heartbeat())
        after = self.read()
        self.assertGreater(after["last_heartbeat"], time.time() - 5)
        self.assertEqual(after["acquired_at"], info["acquired_at"])

    def test_lock_deleted_mid_session_is_recreated(self):
        lock = DbLock(self.db)
        lock.acquire()
        os.remove(lock.path)
        self.assertTrue(lock.heartbeat())
        self.assertEqual(self.read()["session_id"], lock.session_id)

    def test_lock_corrupted_mid_session_is_reclaimed(self):
        lock = DbLock(self.db)
        lock.acquire()
        with open(lock.path, "w") as f:
            f.write("garbage")
        self.assertTrue(lock.heartbeat())
        self.assertEqual(self.read()["session_id"], lock.session_id)

    def test_heartbeat_survives_transient_write_errors(self):
        lock = DbLock(self.db)
        lock.acquire()
        with mock.patch.object(lock, "_overwrite", side_effect=PermissionError):
            self.assertTrue(lock.heartbeat())
            with open(lock.path, "w") as f:
                f.write("garbage")
            self.assertTrue(lock.heartbeat())
        os.remove(lock.path)
        with mock.patch.object(lock, "_create_exclusive", side_effect=PermissionError):
            self.assertTrue(lock.heartbeat())
        self.assertTrue(lock.owned)

    def test_atexit_releases_live_locks(self):
        lock = DbLock(self.db)
        lock.acquire()
        db_lock._release_all()
        self.assertFalse(os.path.exists(lock.path))

    def test_constants_are_sane(self):
        self.assertGreaterEqual(db_lock.HEARTBEAT_SECONDS, 60)
        self.assertLessEqual(db_lock.HEARTBEAT_SECONDS, 120)
        self.assertGreaterEqual(db_lock.STALE_AFTER_SECONDS, 10 * 60)
        self.assertLessEqual(db_lock.STALE_AFTER_SECONDS, 15 * 60)
        self.assertGreater(db_lock.STALE_AFTER_SECONDS, 4 * db_lock.HEARTBEAT_SECONDS)


class TwoProcessTests(LockTestCase):
    """A real second FinanceBook process holding the lock."""

    def _spawn(self, machine, mode):
        env = dict(os.environ, FINANCEBOOK_MACHINE_NAME=machine)
        p = subprocess.Popen([sys.executable, "-c", _CHILD, tests.APP_DIR, self.db, mode],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True)
        self.addCleanup(p.stdout.close)
        self.addCleanup(p.stdin.close)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        self.assertEqual(p.stdout.readline().strip(), "locked")
        return p

    def test_other_laptop_process_holds_then_releases(self):
        p = self._spawn("LAPTOP-B", "wait")
        try:
            with self.assertRaises(LockHeld) as cm:
                DbLock(self.db).acquire()
            self.assertEqual(cm.exception.info["machine"], "LAPTOP-B")
            self.assertEqual(cm.exception.info["pid"], p.pid)
            p.stdin.write("\n")
            p.stdin.flush()
            self.assertEqual(p.stdout.readline().strip(), "released")
        finally:
            p.wait(10)
        lock = DbLock(self.db)
        lock.acquire()
        self.assertTrue(lock.owned)

    def test_killed_process_on_this_machine_leaves_stale_lock(self):
        p = self._spawn("LAPTOP-A", "sleep")
        with self.assertRaises(LockHeld) as cm:
            DbLock(self.db).acquire()
        self.assertTrue(cm.exception.same_machine)
        p.kill()                            # forced kill: no release, no atexit
        p.wait(10)
        self.assertTrue(os.path.exists(db_lock.lock_path(self.db)))
        lock = DbLock(self.db)
        lock.acquire()                      # dead pid on this machine → stale
        self.assertEqual(self.read()["session_id"], lock.session_id)


if __name__ == "__main__":
    unittest.main()
