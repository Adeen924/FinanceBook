"""Headless (offscreen Qt) tests of the connect flow, lock dialogs, read-only
banner and the startup "database not found" choices. Message boxes and file
pickers are mocked so nothing waits for a click."""
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
import db_config
import db_lock
from sheets.client import Database, SCHEMA_VERSION

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover — no Qt available
    raise unittest.SkipTest(f"PyQt6 not available: {e}")

from ui import connect_dialog
from ui.connect_dialog import LockConflictDialog
from ui.database_dialog import DatabaseSetupDialog
from ui.main_window import MainWindow
from tests.test_connect_database import make_current, make_v1, payees, side_files

YES, OK, CANCEL, NO = (QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.Ok,
                       QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.No)


def foreign_lock(db_path, machine="LAPTOP-B", **extra):
    info = {"machine": machine, "user": "sam", "pid": 1, "app_version": "1.9.0",
            "acquired_at": time.time() - 1800, "last_heartbeat": time.time() - 120,
            "session_id": "foreign-" + machine}
    info.update(extra)
    with open(db_lock.lock_path(db_path), "w", encoding="utf-8") as f:
        json.dump(info, f)


class UiCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig_settings = db_config.SETTINGS_PATH
        db_config.SETTINGS_PATH = os.path.join(self.dir, "app_settings.json")
        self.msgs = []
        patches = [mock.patch.dict(os.environ, {"FINANCEBOOK_MACHINE_NAME": "LAPTOP-A"})]
        self.answer = YES
        for name in ("warning", "critical", "information", "question"):
            patches.append(mock.patch.object(
                QMessageBox, name, staticmethod(self._fake_box(name))))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.windows = []

    def _fake_box(self, kind):
        def box(parent, title, text, *a, **k):
            self.msgs.append((kind, title, text))
            return self.answer if kind in ("question", "warning") and a else OK
        return box

    def tearDown(self):
        for w in self.windows:
            w.close()
            w.deleteLater()
        _app.processEvents()
        db_lock._release_all()
        db_config.SETTINGS_PATH = self._orig_settings
        shutil.rmtree(self.dir, ignore_errors=True)

    def new_db(self, name, payee="Coffee"):
        p = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        make_current(p, payee=payee)
        return p

    def window_on(self, path):
        lock = db_lock.DbLock(path)
        lock.acquire()
        db_config.register_database(path, make_active=True)
        w = MainWindow(Database(path), lock)
        self.windows.append(w)
        return w

    def pick(self, path):
        return mock.patch.object(connect_dialog.QFileDialog, "getOpenFileName",
                                 return_value=(path, ""))

    def titles(self):
        return [m[1] for m in self.msgs]


class ConnectDialogTests(UiCase):
    def test_welcome_dialog_offers_connect(self):
        target = self.new_db(os.path.join("OneDrive", "finances.db"))
        dlg = DatabaseSetupDialog(show_connect=True, show_book_type=True,
                                  default_location=self.dir)
        self.assertEqual(dlg._connect_btn.text(), "Connect Existing Database…")
        with self.pick(target):
            dlg._connect_btn.click()
        self.assertEqual(dlg.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dlg.connected_path, os.path.abspath(target))

    def test_plain_new_database_dialog_has_no_connect(self):
        dlg = DatabaseSetupDialog(default_location=self.dir)
        self.assertFalse(hasattr(dlg, "_connect_btn"))
        self.assertEqual(dlg.connected_path, "")

    def test_cancelled_picker_returns_none(self):
        with self.pick(""):
            self.assertIsNone(connect_dialog.choose_existing_database(None))

    def test_picker_starts_in_onedrive(self):
        with mock.patch.dict(os.environ, {"OneDrive": self.dir}), \
                mock.patch.object(connect_dialog.QFileDialog, "getOpenFileName",
                                  return_value=("", "")) as gof:
            connect_dialog.choose_existing_database(None)
        self.assertEqual(gof.call_args[0][2], self.dir)

    def test_newer_schema_refused_with_message(self):
        p = self.new_db("finances.db")
        conn = sqlite3.connect(p)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        with self.pick(p):
            self.assertIsNone(connect_dialog.choose_existing_database(None))
        self.assertEqual(self.titles(), ["Can't Connect Database"])
        self.assertIn("newer version", self.msgs[0][2])

    def test_older_schema_asks_before_upgrade(self):
        p = os.path.join(self.dir, "old.db")
        make_v1(p)
        self.answer = CANCEL
        self.assertIsNone(connect_dialog.confirm_existing_database(None, p))
        self.assertIn("other computer must also update", self.msgs[-1][2])
        self.answer = YES
        self.assertEqual(connect_dialog.confirm_existing_database(None, p), os.path.abspath(p))

    def test_conflict_copy_warning_can_cancel(self):
        self.new_db("finances.db")
        copy = self.new_db("finances-DESKTOP-XY12.db")
        self.answer = CANCEL
        self.assertIsNone(connect_dialog.confirm_existing_database(None, copy))
        self.assertIn("conflict copy", self.msgs[-1][2])


class MainWindowConnectTests(UiCase):
    def test_combo_has_new_and_connect_entries(self):
        w = self.window_on(self.new_db("a.db"))
        data = [w._db_combo.itemData(i) for i in range(w._db_combo.count())]
        self.assertEqual(data[-2:], ["__new__", "__connect__"])

    def test_connect_from_sidebar_switches_and_moves_lock(self):
        a = self.new_db("a.db", payee="A-data")
        b = self.new_db(os.path.join("OneDrive", "b.db"), payee="B-data")
        w = self.window_on(a)
        idx = w._db_combo.findData("__connect__")
        with self.pick(b):
            w._on_db_selected(idx)
        self.assertEqual(os.path.abspath(w.db.db_path), os.path.abspath(b))
        self.assertFalse(os.path.exists(db_lock.lock_path(a)), "old lock released")
        self.assertTrue(w._lock.owned and w._lock.still_ours())
        self.assertEqual(db_config.get_active_db_path(), os.path.abspath(b))
        self.assertEqual(sorted(db_config.get_known_databases()),
                         sorted([os.path.abspath(a), os.path.abspath(b)]))
        self.assertEqual([t["payee"] for t in w.db.get_transactions()], ["B-data"])
        self.assertTrue(w._ro_banner.isHidden())

        # Connecting it again (already known) just switches — no duplicate.
        with self.pick(a):
            w.connect_existing_database()
        with self.pick(b.upper()):
            w.connect_existing_database()
        with open(db_config.SETTINGS_PATH, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["known_databases"]), 2)

    def test_connecting_current_database_is_a_no_op(self):
        a = self.new_db("a.db")
        w = self.window_on(a)
        lock = w._lock
        with self.pick(a):
            self.assertTrue(w.connect_existing_database())
        self.assertIs(w._lock, lock)
        self.assertIn("already open", self.msgs[-1][2])

    def test_settings_page_has_connect_button(self):
        w = self.window_on(self.new_db("a.db"))
        page = w.page("Settings")
        from PyQt6.QtWidgets import QPushButton
        labels = [b.text() for b in page.findChildren(QPushButton)]
        self.assertIn("Connect Existing Database…", labels)
        with mock.patch.object(w, "connect_existing_database") as conn:
            page._connect_existing()
        conn.assert_called_once()

    def test_new_database_from_sidebar_is_locked(self):
        a = self.new_db("a.db")
        w = self.window_on(a)
        with mock.patch.object(DatabaseSetupDialog, "exec",
                               lambda d: (setattr(d, "database_name", "Shop Books"),
                                          setattr(d, "database_location", self.dir),
                                          QDialog.DialogCode.Accepted)[-1]):
            w._create_new_database()
        new = os.path.join(self.dir, "shop_books.db")
        self.assertEqual(os.path.abspath(w.db.db_path), os.path.abspath(new))
        self.assertEqual(w.db.get_setting("database_name"), "Shop Books")
        self.assertTrue(w._lock.still_ours())
        self.assertFalse(os.path.exists(db_lock.lock_path(a)))


class LockFlowTests(UiCase):
    def _choose(self, choice):
        def exec_(dlg):
            self.dialog_text = " ".join(
                lbl.text() for lbl in dlg.findChildren(__import__("PyQt6.QtWidgets").QtWidgets.QLabel))
            dlg.choice = choice
            return 1
        return mock.patch.object(LockConflictDialog, "exec", exec_)

    def test_in_use_elsewhere_open_read_only(self):
        a = self.new_db("a.db")
        b = self.new_db("b.db", payee="B-data")
        foreign_lock(b)
        w = self.window_on(a)
        with self._choose(LockConflictDialog.READ_ONLY):
            self.assertTrue(w.open_database(b))
        self.assertIn("In use on LAPTOP-B by sam", self.dialog_text)
        self.assertIn("last active 2 min ago", self.dialog_text)
        self.assertIn("Take Over", self.dialog_text)
        self.assertTrue(w.db.read_only)
        self.assertIsNone(w._lock)
        self.assertFalse(w._ro_banner.isHidden())
        self.assertIn("LAPTOP-B", w._ro_label.text())
        self.assertIn("read-only", w.windowTitle())
        self.assertFalse(w._heartbeat.isActive())
        self.assertEqual(db_lock.read_lock(b)["machine"], "LAPTOP-B")
        with self.assertRaises(sqlite3.OperationalError):
            w.db.save_account({"name": "nope"})
        # Every page renders without trying to write.
        self.msgs.clear()
        for name in list(w._nav_buttons):
            w._nav_to(name)
        self.assertEqual(self.msgs, [])

    def test_same_machine_instance_is_named(self):
        b = self.new_db("b.db")
        other = db_lock.DbLock(b)
        other.acquire()
        w = self.window_on(self.new_db("a.db"))
        with self._choose(LockConflictDialog.CANCEL):
            self.assertFalse(w.open_database(b))
        self.assertIn("another FinanceBook window on this computer", self.dialog_text)

    def test_cancel_keeps_current_database(self):
        a = self.new_db("a.db")
        b = self.new_db("b.db")
        foreign_lock(b)
        w = self.window_on(a)
        with self._choose(LockConflictDialog.CANCEL):
            self.assertFalse(w.open_database(b))
        self.assertEqual(os.path.abspath(w.db.db_path), os.path.abspath(a))
        self.assertTrue(w._lock.still_ours())

    def test_take_over(self):
        a = self.new_db("a.db")
        b = self.new_db("b.db")
        foreign_lock(b)
        w = self.window_on(a)
        with self._choose(LockConflictDialog.TAKE_OVER):
            self.assertTrue(w.open_database(b))
        self.assertFalse(w.db.read_only)
        self.assertEqual(db_lock.read_lock(b)["machine"], "LAPTOP-A")
        self.assertTrue(w._heartbeat.isActive())

    def test_open_for_editing_after_other_side_closed(self):
        a = self.new_db("a.db")
        b = self.new_db("b.db")
        foreign_lock(b)
        w = self.window_on(a)
        with self._choose(LockConflictDialog.READ_ONLY):
            w.open_database(b)
        os.remove(db_lock.lock_path(b))     # the other laptop closed and synced
        w.open_database(w.db.db_path)       # the banner's "Open for Editing"
        self.assertFalse(w.db.read_only)
        self.assertTrue(w._ro_banner.isHidden())
        self.assertTrue(w._lock.still_ours())

    def test_heartbeat_detects_take_over(self):
        a = self.new_db("a.db")
        w = self.window_on(a)
        foreign_lock(a, last_heartbeat=time.time())     # someone took over
        w._on_heartbeat()
        self.assertTrue(w.db.read_only)
        self.assertFalse(w._ro_banner.isHidden())
        self.assertIn("Database Taken Over", self.titles())
        with self.assertRaises(sqlite3.OperationalError):
            w.db.set_setting("x", "y")
        w.close()
        self.assertEqual(db_lock.read_lock(a)["machine"], "LAPTOP-B",
                         "closing must not delete the new owner's lock")

    def test_heartbeat_after_sleep_asks_and_can_go_read_only(self):
        a = self.new_db("a.db")
        w = self.window_on(a)
        w._lock.last_beat = time.time() - db_lock.STALE_AFTER_SECONDS - 60
        self.answer = NO
        w._on_heartbeat()
        self.assertIn("Welcome Back", self.titles())
        self.assertTrue(w.db.read_only)
        self.assertFalse(os.path.exists(db_lock.lock_path(a)))

    def test_heartbeat_after_sleep_keep_editing(self):
        a = self.new_db("a.db")
        w = self.window_on(a)
        w._lock.last_beat = time.time() - db_lock.STALE_AFTER_SECONDS - 60
        self.answer = YES
        w._on_heartbeat()
        self.assertFalse(w.db.read_only)
        self.assertGreater(w._lock.last_beat, time.time() - 5)

    def test_close_releases_lock_and_leaves_no_side_files(self):
        a = self.new_db("a.db")
        w = self.window_on(a)
        w.db.save_account({"name": "Savings"})
        w.close()
        self.assertFalse(os.path.exists(db_lock.lock_path(a)))
        self.assertEqual(side_files(a), [])

    def test_read_only_write_gets_friendly_message(self):
        import main
        main._install_error_handler()
        import sys
        try:
            sys.excepthook(sqlite3.OperationalError,
                           sqlite3.OperationalError("attempt to write a readonly database"), None)
        finally:
            sys.excepthook = sys.__excepthook__
        self.assertEqual(self.msgs[-1][1], "Read-Only")


class DeleteAndRestoreTests(UiCase):
    def _trash(self):
        def move(path):
            os.remove(path)
            return True, ""
        return mock.patch("PyQt6.QtCore.QFile.moveToTrash", side_effect=move)

    def test_delete_switches_away_and_removes_lock(self):
        a = self.new_db("a.db")
        b = self.new_db("b.db")
        db_config.register_database(b)
        w = self.window_on(a)
        name = w.db.get_setting("database_name") or "a.db"
        with mock.patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=(name, True)), \
                self._trash():
            self.assertTrue(w.delete_current_database())
        self.assertFalse(os.path.exists(a))
        self.assertFalse(os.path.exists(db_lock.lock_path(a)))
        self.assertEqual(os.path.abspath(w.db.db_path), os.path.abspath(b))
        self.assertTrue(w._lock.still_ours())

    def test_delete_refused_read_only(self):
        a = self.new_db("a.db")
        b = self.new_db("b.db")
        db_config.register_database(a)
        foreign_lock(b)
        w = self.window_on(a)
        with LockFlowTests._choose(self, LockConflictDialog.READ_ONLY):
            w.open_database(b)
        self.assertFalse(w.delete_current_database())
        self.assertTrue(os.path.exists(b))

    def test_delete_aborted_if_target_locked_and_cancelled(self):
        a = self.new_db("a.db")
        b = self.new_db("b.db")
        db_config.register_database(b)
        foreign_lock(b)
        w = self.window_on(a)
        name = w.db.get_setting("database_name") or "a.db"
        with mock.patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=(name, True)), \
                LockFlowTests._choose(self, LockConflictDialog.CANCEL), self._trash():
            self.assertFalse(w.delete_current_database())
        self.assertTrue(os.path.exists(a))
        self.assertTrue(w._lock.still_ours())

    def test_restore_keeps_lock_and_is_refused_read_only(self):
        a = self.new_db("a.db", payee="Before")
        w = self.window_on(a)
        bak = w.db.backup_now("manual")
        w.db.save_account({"name": "After backup"})
        lock = w._lock
        page = w.page("Settings")
        self.answer = OK
        with mock.patch("ui.pages.settings.QFileDialog.getOpenFileName", return_value=(bak, "")):
            page._restore_backup()
        self.assertIn("Restored", self.titles())
        self.assertIs(w._lock, lock)
        self.assertTrue(lock.still_ours())
        self.assertNotIn("After backup", [x["name"] for x in w.db.get_accounts()])

        w.db.read_only = True
        page = w.page("Settings")
        with mock.patch("ui.pages.settings.QFileDialog.getOpenFileName",
                        return_value=(bak, "")) as gof:
            page._restore_backup()
        gof.assert_not_called()


class MissingDatabaseStartupTests(UiCase):
    def _click(self, label):
        def exec_(box):
            return 0
        def clicked(box):
            return next(b for b in box.buttons() if b.text() == label)
        return mock.patch.multiple(QMessageBox, exec=exec_, clickedButton=clicked)

    def test_quit_never_creates_a_file(self):
        import main
        missing = os.path.join(self.dir, "OneDrive", "finances.db")
        with self._click("Quit"):
            self.assertIsNone(main._resolve_missing_database(missing))
        self.assertFalse(os.path.exists(missing))

    def test_retry_once_folder_appears(self):
        import main
        path = self.new_db(os.path.join("OneDrive", "finances.db"))
        with self._click("Retry"):
            self.assertEqual(main._resolve_missing_database(path), (path, None, None, None))

    def test_choose_another(self):
        import main
        other = self.new_db("other.db")
        with self._click("Choose Another Database…"), self.pick(other):
            self.assertEqual(main._resolve_missing_database(os.path.join(self.dir, "gone.db")),
                             (os.path.abspath(other), None, None, None))

    def test_create_new(self):
        import main
        accept = lambda d: (setattr(d, "database_name", "Fresh"),
                            setattr(d, "database_location", self.dir),
                            setattr(d, "book_type", "business"),
                            setattr(d, "preset", "simple"),
                            QDialog.DialogCode.Accepted)[-1]
        with self._click("Create New…"), mock.patch.object(DatabaseSetupDialog, "exec", accept):
            path, name, book, preset = main._resolve_missing_database(
                os.path.join(self.dir, "x", "gone.db"))
        self.assertEqual((os.path.dirname(path), name, book, preset),
                         (self.dir, "Fresh", "business", "simple"))


class UpdateInteractionTests(UiCase):
    def test_one_click_update_releases_lock_before_exit(self):
        import updater
        a = self.new_db("a.db")
        w = self.window_on(a)
        with mock.patch.object(updater, "_write_helper_ps1"),                 mock.patch("subprocess.Popen"),                 mock.patch.object(updater.os, "_exit", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                updater._launch_helper_and_exit(w, os.path.join(self.dir, "new_app"))
        self.assertFalse(os.path.exists(db_lock.lock_path(a)))
        self.assertIsNone(w._lock)

    def test_newer_database_at_startup_offers_update(self):
        import main
        with mock.patch("updater.check_for_updates", return_value=None):
            main._offer_update_for_newer_database(os.path.join(self.dir, "f.db"))
        self.assertEqual(self.titles(), ["Update Needed", "Update Needed"])
        self.assertIn("releases", self.msgs[-1][2])
        info = {"latest_version": "9.9.9"}
        with mock.patch("updater.check_for_updates", return_value=info),                 mock.patch("updater._show_update_dialog") as show:
            main._offer_update_for_newer_database(os.path.join(self.dir, "f.db"))
        show.assert_called_once_with(None, info)


if __name__ == "__main__":
    unittest.main()
