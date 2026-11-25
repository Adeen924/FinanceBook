"""Mac support (v1.20): platform_paths with sys.platform mocked to win32 and
darwin, Keychain secrets, Mac conflict copies, the launcher's app/ search order
and rollback, and the Mac updater's swap and runtime check. Windows behaviour
must come out exactly as before."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import tests  # noqa: F401
from utils import platform_paths as PP

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import launcher  # noqa: E402
import updater   # noqa: E402


def on(platform):
    return mock.patch.object(sys, "platform", platform)


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_app(self, path, version):
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "main.py"), "w") as f:
            f.write("# app\n")
        with open(os.path.join(path, "VERSION"), "w") as f:
            f.write(version)
        return path


class AppDataDirTests(TempDirCase):
    def test_windows_uses_appdata(self):
        with on("win32"), mock.patch.dict(os.environ, {"APPDATA": self.dir}):
            self.assertEqual(PP.app_data_dir(), os.path.join(self.dir, "FinanceBook"))
        self.assertTrue(os.path.isdir(os.path.join(self.dir, "FinanceBook")))

    def test_windows_without_appdata_falls_back_to_home(self):
        env = {k: v for k, v in os.environ.items() if k != "APPDATA"}
        with on("win32"), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("os.path.expanduser", return_value=self.dir):
            self.assertEqual(PP.app_data_dir(create=False),
                             os.path.join(self.dir, "FinanceBook"))

    def test_mac_uses_application_support(self):
        with on("darwin"), mock.patch("os.path.expanduser", return_value=self.dir):
            self.assertEqual(PP.app_data_dir(create=False),
                             os.path.join(self.dir, "Library", "Application Support",
                                          "FinanceBook"))


class CloudFolderTests(TempDirCase):
    def test_windows_onedrive_from_environment(self):
        with on("win32"), mock.patch.dict(os.environ, {"OneDrive": self.dir}):
            self.assertEqual(PP.cloud_folders()[0], ("OneDrive", self.dir))
            self.assertEqual(PP.default_cloud_dir(), self.dir)

    def test_windows_without_onedrive_is_home(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("OneDrive")}
        with on("win32"), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("os.path.expanduser", return_value=self.dir):
            self.assertEqual(PP.cloud_folders(), [])
            self.assertEqual(PP.default_cloud_dir(), self.dir)

    def test_mac_finds_cloudstorage_and_icloud(self):
        home = self.dir
        cs = os.path.join(home, "Library", "CloudStorage")
        od = os.path.join(cs, "OneDrive-Personal")
        gd = os.path.join(cs, "GoogleDrive-me@example.com")
        ic = os.path.join(home, "Library", "Mobile Documents", "com~apple~CloudDocs")
        for p in (od, gd, ic):
            os.makedirs(p)
        env = {k: v for k, v in os.environ.items() if not k.startswith("OneDrive")}
        with on("darwin"), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("os.path.expanduser", return_value=home):
            got = PP.cloud_folders()
            self.assertEqual(got, [("OneDrive", od), ("Google Drive", gd), ("iCloud Drive", ic)])
            self.assertEqual(PP.default_cloud_dir(), od)
            shutil.rmtree(od)
            self.assertEqual(PP.default_cloud_dir(), gd)   # no OneDrive: next synced folder


class NameAndFontTests(unittest.TestCase):
    def test_machine_name(self):
        env = {k: v for k, v in os.environ.items() if k != "FINANCEBOOK_MACHINE_NAME"}
        with on("win32"), mock.patch.dict(os.environ, dict(env, COMPUTERNAME="DESKTOP-AB12"),
                                          clear=True):
            self.assertEqual(PP.machine_name(), "DESKTOP-AB12")
        with on("darwin"), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(PP, "_mac_computer_name", return_value=""), \
                mock.patch("socket.gethostname", return_value="Adeens-MacBook-Pro.local"):
            self.assertEqual(PP.machine_name(), "Adeens-MacBook-Pro")

    def test_fonts(self):
        with on("win32"):
            self.assertIn('"Segoe UI"', PP.css_font_stack())
            self.assertIn("Segoe UI Variable Display", PP.css_font_stack(display=True))
            self.assertEqual(PP.base_font_point_size(), 10)
            self.assertIn("Segoe UI", PP.html_font_family())
        with on("darwin"):
            self.assertIn(".AppleSystemUIFont", PP.css_font_stack())
            self.assertNotIn("Segoe", PP.css_font_stack(display=True))
            self.assertEqual(PP.base_font_point_size(), 13)
            self.assertNotIn("Segoe", PP.html_font_family())

    def test_shortcut_labels(self):
        with on("win32"):
            self.assertEqual(PP.shortcut_label("Ctrl+F"), "Ctrl+F")
            self.assertEqual(PP.file_manager_name(), "File Explorer")
        with on("darwin"):
            self.assertEqual(PP.shortcut_label("Ctrl+F"), "⌘F")
            self.assertEqual(PP.shortcut_label("Ctrl+Shift+S"), "⌘⇧S")
            self.assertEqual(PP.file_manager_name(), "Finder")


class SecretStoreTests(unittest.TestCase):
    def setUp(self):
        from utils import secret_store
        self.S = secret_store

    def test_mac_uses_keychain_and_keeps_secret_off_command_line(self):
        store = {}
        calls = []

        def run(args, input=None, capture_output=True, timeout=None):
            calls.append((args, input))
            r = mock.Mock(returncode=0, stdout=b"")
            if args[1] == "-i":
                store["k"] = "sk-ant-123\"x"
            elif args[1] == "find-generic-password":
                if "k" in store:
                    r.stdout = (store["k"] + "\n").encode()
                else:
                    r.returncode = 44
            return r

        with on("darwin"), mock.patch.object(self.S.subprocess, "run", side_effect=run):
            stored = self.S.protect('sk-ant-123"x', name="ai_receipts_key")
            self.assertEqual(stored, "keychain:ai_receipts_key")
            self.assertEqual(self.S.unprotect(stored), 'sk-ant-123"x')
        for args, _inp in calls:
            self.assertNotIn("sk-ant-123", " ".join(args))   # never in argv
        self.assertIn(b"add-generic-password", calls[0][1])

    def test_mac_falls_back_to_base64_if_keychain_fails(self):
        with on("darwin"), mock.patch.object(self.S.subprocess, "run",
                                             side_effect=OSError("no security")):
            stored = self.S.protect("abc")
        self.assertTrue(stored.startswith("b64:"))
        self.assertEqual(self.S.unprotect(stored), "abc")

    def test_windows_never_touches_keychain(self):
        with on("win32"), mock.patch.object(self.S, "_dpapi", return_value=b"enc"), \
                mock.patch.object(self.S.subprocess, "run") as run:
            self.assertTrue(self.S.protect("abc").startswith("dpapi:"))
        run.assert_not_called()


class OcrMessageTests(unittest.TestCase):
    def test_messages(self):
        from parsers import ocr
        with on("darwin"):
            self.assertFalse(ocr.available())
            with self.assertRaises(ocr.OCRError) as cm:
                ocr.ocr_image("x.png")
            self.assertIn("isn't available on Mac yet", str(cm.exception))
        with on("win32"), mock.patch.object(ocr, "_powershell", return_value=""):
            with self.assertRaises(ocr.OCRError) as cm:
                ocr.ocr_image("x.png")
            self.assertIn("Windows 10 or 11", str(cm.exception))


class MacConflictCopyTests(TempDirCase):
    def test_mac_computer_names_and_icloud_copies(self):
        import db_connect
        from tests.test_connect_database import make_current
        orig = os.path.join(self.dir, "finances.db")
        mac = os.path.join(self.dir, "finances-Adeens-MacBook-Pro.db")
        mac2 = os.path.join(self.dir, "finances-Adeens-MacBook-Pro-2.db")
        icloud = os.path.join(self.dir, "finances 2.db")
        win = os.path.join(self.dir, "finances-DESKTOP-AB12CD.db")
        unrelated = os.path.join(self.dir, "finances-backup.db")
        year = os.path.join(self.dir, "finances 2024.db")
        for p in (orig, mac, mac2, icloud, win, unrelated, year):
            make_current(p)
        for p in (mac, mac2, icloud, win):
            self.assertEqual(db_connect.conflict_original(p), orig, p)
        self.assertEqual(db_connect.conflict_original(unrelated), "")
        self.assertEqual(db_connect.conflict_original(year), "")
        self.assertEqual(db_connect.conflict_copies(orig), sorted([mac, mac2, icloud, win]))

    def test_this_macs_own_name(self):
        import db_connect
        with mock.patch.dict(os.environ, {"FINANCEBOOK_MACHINE_NAME": "Studio"}):
            self.assertTrue(db_connect._looks_like_copy_suffix("-Studio"))
            self.assertTrue(db_connect._looks_like_copy_suffix("-Studio-3"))
            self.assertFalse(db_connect._looks_like_copy_suffix("-Archive"))


class LauncherTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.bundled = self.make_app(os.path.join(self.dir, "Bundle", "app"), "1.19.0")
        self.support = os.path.join(self.dir, "Support")
        self.user = os.path.join(self.support, "app")

    def test_bundled_when_no_update(self):
        self.assertEqual(launcher.choose_mac_app_dir(self.bundled, self.support),
                         (self.bundled, ""))

    def test_newer_update_wins_older_does_not(self):
        self.make_app(self.user, "1.20.0")
        self.assertEqual(launcher.choose_mac_app_dir(self.bundled, self.support)[0], self.user)
        self.make_app(self.user, "1.18.0")      # e.g. after rebuilding a newer .app
        self.assertEqual(launcher.choose_mac_app_dir(self.bundled, self.support)[0],
                         self.bundled)
        self.make_app(self.user, "1.19.0")
        self.assertEqual(launcher.choose_mac_app_dir(self.bundled, self.support)[0],
                         self.bundled)

    def test_versions(self):
        self.assertTrue(launcher.version_newer("1.20.0", "1.19.0"))
        self.assertTrue(launcher.version_newer("1.10.0", "1.9.9"))
        self.assertFalse(launcher.version_newer("1.20.0-beta.1", "1.20.0"))
        self.assertFalse(launcher.version_newer("", "1.0"))
        self.assertTrue(launcher.version_newer("1.0", ""))

    def test_rollback_after_failed_start(self):
        self.make_app(self.user, "1.20.0")
        with open(os.path.join(self.support, "boot-marker.json"), "w") as f:
            json.dump({"app": self.user, "version": "1.20.0"}, f)
        app_dir, note = launcher.choose_mac_app_dir(self.bundled, self.support)
        self.assertEqual(app_dir, self.bundled)
        self.assertIn("1.20.0 couldn't start", note)
        self.assertIn("1.19.0", note)
        self.assertFalse(os.path.exists(self.user))
        self.assertTrue(os.path.isfile(os.path.join(self.user + ".failed", "main.py")))
        self.assertFalse(os.path.exists(os.path.join(self.support, "boot-marker.json")))
        # The next start is quiet and stays on the bundled app.
        self.assertEqual(launcher.choose_mac_app_dir(self.bundled, self.support),
                         (self.bundled, ""))

    def test_marker_for_other_folder_is_just_cleared(self):
        self.make_app(self.user, "1.20.0")
        with open(os.path.join(self.support, "boot-marker.json"), "w") as f:
            f.write("not json")
        self.assertEqual(launcher.choose_mac_app_dir(self.bundled, self.support),
                         (self.user, ""))

    def test_windows_launcher_untouched(self):
        with on("win32"), mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(launcher, "_main_mac") as mac, \
                mock.patch.object(launcher.os.path, "isfile", return_value=True), \
                mock.patch.object(launcher.runpy, "run_path") as run, \
                mock.patch.object(sys, "executable",
                                  os.path.join(self.dir, "FinanceBook.exe")):
            launcher.main()
        mac.assert_not_called()
        run.assert_called_once_with(os.path.join(self.dir, "app", "main.py"),
                                    run_name="__main__")


class MacUpdaterTests(TempDirCase):
    def test_swap_keeps_backup(self):
        support = self.dir
        self.make_app(os.path.join(support, "app"), "1.19.0")
        new = self.make_app(os.path.join(support, "app.new"), "1.20.0")
        os.makedirs(os.path.join(support, "app.failed"))
        target = updater.mac_install_app(support, new)
        self.assertEqual(launcher.read_version(target), "1.20.0")
        self.assertEqual(launcher.read_version(os.path.join(support, "app.bak")), "1.19.0")
        self.assertFalse(os.path.exists(new))
        self.assertFalse(os.path.exists(os.path.join(support, "app.failed")))

    def test_first_update_has_nothing_to_back_up(self):
        new = self.make_app(os.path.join(self.dir, "app.new"), "1.20.0")
        updater.mac_install_app(self.dir, new)
        self.assertEqual(launcher.read_version(os.path.join(self.dir, "app")), "1.20.0")

    def test_bad_payload_changes_nothing(self):
        self.make_app(os.path.join(self.dir, "app"), "1.19.0")
        bad = os.path.join(self.dir, "app.new")
        os.makedirs(bad)
        with self.assertRaises(RuntimeError):
            updater.mac_install_app(self.dir, bad)
        self.assertEqual(launcher.read_version(os.path.join(self.dir, "app")), "1.19.0")

    def test_failed_rename_restores_previous(self):
        self.make_app(os.path.join(self.dir, "app"), "1.19.0")
        new = self.make_app(os.path.join(self.dir, "app.new"), "1.20.0")
        real = os.replace
        calls = []

        def flaky(a, b):
            calls.append(a)
            if a == new:
                raise OSError("disk full")
            return real(a, b)
        with mock.patch.object(updater.os, "replace", side_effect=flaky):
            with self.assertRaises(OSError):
                updater.mac_install_app(self.dir, new)
        self.assertEqual(launcher.read_version(os.path.join(self.dir, "app")), "1.19.0")

    def test_runtime_check(self):
        built = "PyQt6==6.7.1\nrequests\n# comment\n"
        self.assertFalse(updater.mac_needs_rebuild(
            "v1.20.0", built, fetch=lambda t: "requests \nPyQt6==6.7.1\n"))
        self.assertTrue(updater.mac_needs_rebuild(
            "v1.21.0", built, fetch=lambda t: "PyQt6==6.8.0\nrequests\n"))

        def offline(_t):
            raise OSError("offline")
        self.assertFalse(updater.mac_needs_rebuild("v1.21.0", built, fetch=offline))

    def _release(self, assets):
        resp = mock.Mock()
        resp.json.return_value = {"tag_name": "v99.0.0", "body": "x", "html_url": "u",
                                  "assets": assets}
        return resp

    def test_same_asset_on_both_platforms(self):
        assets = [{"name": "FinanceBook-99.0.0.zip", "browser_download_url": "f", "size": 1},
                  {"name": "app-99.0.0.zip", "browser_download_url": "a", "size": 2}]
        for plat in ("win32", "darwin"):
            with on(plat), mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch("requests.get", return_value=self._release(assets)), \
                    mock.patch.object(updater, "mac_needs_rebuild", return_value=False):
                info = updater.check_for_updates()
            self.assertEqual(info["app_asset"]["name"], "app-99.0.0.zip")
            self.assertFalse(info["needs_rebuild"])

    def test_mac_without_app_asset_needs_rebuild(self):
        assets = [{"name": "FinanceBook-99.0.0.zip", "browser_download_url": "f", "size": 1}]
        with on("darwin"), mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch("requests.get", return_value=self._release(assets)):
            self.assertTrue(updater.check_for_updates()["needs_rebuild"])
        with on("win32"), mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch("requests.get", return_value=self._release(assets)):
            self.assertFalse(updater.check_for_updates()["needs_rebuild"])

    def test_payload_zip_has_nothing_windows_only(self):
        """The release zips app/* — check what that would hold."""
        app = os.path.join(REPO, "app")
        bad = []
        for root, _dirs, files in os.walk(app):
            if "__pycache__" in root:
                continue
            for f in files:
                if f.lower().endswith((".exe", ".dll", ".bat", ".ps1", ".pyd")):
                    bad.append(os.path.join(root, f))
        self.assertEqual(bad, [])


_QAPP = None


class MacMenuTests(TempDirCase):
    def _window(self, platform):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        global _QAPP
        _QAPP = QApplication.instance() or QApplication([])
        from sheets.client import Database
        from ui.main_window import MainWindow
        db = Database(os.path.join(self.dir, platform + ".db"))
        self.addCleanup(db.close)
        with on(platform):
            w = MainWindow(db, None)
        self.addCleanup(w.close)
        return w

    def test_mac_menu_roles_and_shortcuts(self):
        from PyQt6.QtGui import QAction, QKeySequence
        w = self._window("darwin")
        acts = w._mac_actions
        self.assertEqual(acts["about"].menuRole(), QAction.MenuRole.AboutRole)
        self.assertEqual(acts["settings"].menuRole(), QAction.MenuRole.PreferencesRole)
        self.assertEqual(acts["quit"].menuRole(), QAction.MenuRole.QuitRole)
        self.assertEqual(acts["quit"].shortcut(), QKeySequence(QKeySequence.StandardKey.Quit))
        acts["settings"].trigger()
        self.assertEqual(w._current_page, "Settings")

    def test_windows_has_no_menu_bar(self):
        w = self._window("win32")
        self.assertFalse(hasattr(w, "_mac_actions"))
        self.assertEqual(w.menuBar().actions(), [])


if __name__ == "__main__":
    unittest.main()
