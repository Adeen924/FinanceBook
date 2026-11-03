"""FinanceBook's receipt copies live in their own folder, apart from the user's files."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
from sheets.client import Database
from sheets.receipts_store import file_sha256


class ReceiptFolderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "finances.db"))
        # The user's own receipts folder, next to the database.
        self.mine = os.path.join(self.dir, "receipts")
        os.makedirs(self.mine)
        self.my_file = self.write(os.path.join(self.mine, "my-own-receipt.pdf"), b"%PDF mine")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, path, data: bytes) -> str:
        with open(path, "wb") as f:
            f.write(data)
        return path

    def legacy_receipt(self, name: str, data: bytes) -> dict:
        """A receipt copied by v1.10.0 or older into the shared receipts folder."""
        path = self.write(os.path.join(self.mine, name), data)
        with self.db._conn() as c:
            c.execute("INSERT INTO receipts (id,file_path,original_name,sha256,status) "
                      "VALUES (?,?,?,?,'booked')",
                      (name, os.path.join("receipts", name), name, file_sha256(path)))
        return self.db.get_receipt(name)

    def test_new_copies_go_to_their_own_folder(self):
        src = self.write(os.path.join(self.mine, "amazon.pdf"), b"%PDF amazon")
        rc, dup = self.db.add_receipt_file(src)
        path = self.db.receipt_abs_path(rc)
        self.assertEqual(os.path.dirname(path),
                         os.path.join(self.dir, "FinanceBook Receipts", "finances"))
        self.assertFalse(rc["file_path"].startswith("receipts"))
        self.assertEqual(sorted(os.listdir(self.mine)), ["amazon.pdf", "my-own-receipt.pdf"])
        self.assertTrue(self.db.add_receipt_file(src)[1])          # duplicate still detected

    def test_two_databases_in_one_folder_stay_apart(self):
        biz = Database(os.path.join(self.dir, "business.db"), book_type="business")
        try:
            src = self.write(os.path.join(self.dir, "x.txt"), b"Shop\nTotal 1.00\n")
            a = self.db.receipt_abs_path(self.db.add_receipt_file(src)[0])
            b = biz.receipt_abs_path(biz.add_receipt_file(src)[0])
            self.assertNotEqual(os.path.dirname(a), os.path.dirname(b))
        finally:
            biz.close()

    def test_delete_only_removes_finance_book_copies(self):
        rc, _ = self.db.add_receipt_file(self.my_file)
        copy = self.db.receipt_abs_path(rc)
        self.db.delete_receipt(rc["id"])
        self.assertFalse(os.path.exists(copy))
        self.assertTrue(os.path.exists(self.my_file))
        # A record pointing outside FinanceBook's folders never deletes that file.
        outside = self.write(os.path.join(self.dir, "elsewhere.pdf"), b"x")
        with self.db._conn() as c:
            c.execute("INSERT INTO receipts (id,file_path) VALUES ('OUT1','elsewhere.pdf')")
        self.db.delete_receipt("OUT1")
        self.assertTrue(os.path.exists(outside))

    def test_old_copies_keep_working_and_move_on_request(self):
        old = self.legacy_receipt("20260101-abc-old.pdf", b"%PDF old copy")
        self.assertTrue(os.path.isfile(self.db.receipt_abs_path(old)))
        self.assertEqual([r["id"] for r in self.db.legacy_receipt_copies()], [old["id"]])

        res = self.db.move_legacy_receipts()
        self.assertEqual(res, {"moved": 1, "missing": 0, "failed": 0})
        moved = self.db.get_receipt(old["id"])
        new_path = self.db.receipt_abs_path(moved)
        self.assertEqual(os.path.dirname(new_path),
                         os.path.join(self.dir, "FinanceBook Receipts", "finances"))
        self.assertEqual(file_sha256(new_path), old["sha256"])
        self.assertFalse(os.path.exists(os.path.join(self.mine, "20260101-abc-old.pdf")))
        # The user's own file and folder are untouched.
        self.assertEqual(os.listdir(self.mine), ["my-own-receipt.pdf"])
        self.assertEqual(self.db.legacy_receipt_copies(), [])

    def test_missing_files_are_skipped_and_empty_old_folder_removed(self):
        os.remove(self.my_file)
        a = self.legacy_receipt("20260101-aaa-a.pdf", b"a")
        self.legacy_receipt("20260101-bbb-b.pdf", b"b")
        os.remove(self.db.receipt_abs_path(a))
        res = self.db.move_legacy_receipts()
        self.assertEqual(res, {"moved": 1, "missing": 1, "failed": 0})
        self.assertFalse(os.path.exists(self.mine))     # only FinanceBook's copies were there

    def test_name_clash_with_a_different_file_gets_a_new_name(self):
        old = self.legacy_receipt("20260101-ccc-c.pdf", b"original")
        os.makedirs(self.db.receipts_dir(), exist_ok=True)
        clash = self.write(os.path.join(self.db.receipts_dir(), "20260101-ccc-c.pdf"), b"other")
        self.assertEqual(self.db.move_legacy_receipts()["moved"], 1)
        path = self.db.receipt_abs_path(self.db.get_receipt(old["id"]))
        self.assertNotEqual(path, clash)
        self.assertEqual(file_sha256(path), old["sha256"])
        with open(clash, "rb") as f:
            self.assertEqual(f.read(), b"other")


class SettingsReceiptFilesUiTests(unittest.TestCase):
    def setUp(self):
        try:
            from PyQt6.QtWidgets import QApplication
            self.app = QApplication.instance() or QApplication([])
        except Exception as e:      # pragma: no cover — no Qt available
            self.skipTest(f"PyQt6 not available: {e}")
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "finances.db"))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_move_button_appears_and_moves(self):
        from PyQt6.QtWidgets import QMessageBox
        from ui.pages.settings import SettingsPage
        os.makedirs(os.path.join(self.dir, "receipts"))
        path = os.path.join(self.dir, "receipts", "20260101-ddd-d.pdf")
        with open(path, "wb") as f:
            f.write(b"d")
        with self.db._conn() as c:
            c.execute("INSERT INTO receipts (id,file_path,sha256) VALUES ('D1',?,?)",
                      (os.path.join("receipts", "20260101-ddd-d.pdf"), file_sha256(path)))
        page = SettingsPage(self.db)
        self.addCleanup(page.deleteLater)
        page.refresh()
        self.assertFalse(page._move_receipts_btn.isHidden())
        self.assertIn("1 receipt copy", page._legacy_lbl.text())
        with mock.patch.object(QMessageBox, "question",
                               staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)), \
                mock.patch.object(QMessageBox, "information", staticmethod(lambda *a, **k: None)):
            page._move_receipts_btn.click()
        self.assertTrue(page._move_receipts_btn.isHidden())
        self.assertTrue(os.path.isfile(self.db.receipt_abs_path(self.db.get_receipt("D1"))))


if __name__ == "__main__":
    unittest.main()
