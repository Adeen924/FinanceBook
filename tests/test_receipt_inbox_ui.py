"""Headless (offscreen Qt) tests of the watched receipts folder on the Receipts
page: status line, banner, batch parse, review queue, filters and read-only.
Message boxes are mocked so nothing waits for a click."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401
import db_config
from parsers import receipt as R
from sheets.client import Database
from utils import receipt_inbox as I
from tests.test_receipt_inbox import TXT, HTML, EML, snapshot

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog
    _app = QApplication.instance() or QApplication([])
except Exception as e:      # pragma: no cover — no Qt available
    raise unittest.SkipTest(f"PyQt6 not available: {e}")

from ui.pages.receipts import ReceiptsPage
from ui.dialogs import receipt_dialog as RD

YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


def pump(cond, tries=400):
    for _ in range(tries):
        QApplication.processEvents()
        if cond():
            return True
        QApplication.instance().thread().msleep(10)
    return cond()


class InboxPageCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = db_config.SETTINGS_PATH
        db_config.SETTINGS_PATH = os.path.join(self.dir, "app_settings.json")
        self.db_path = os.path.join(self.dir, "books.db")
        self.db = Database(self.db_path, book_type="business")
        self.inbox = os.path.join(self.dir, "Downloaded receipts")
        os.makedirs(self.inbox)
        for name, data in (("corner.txt", TXT), ("cafe.html", HTML), ("order.eml", EML),
                           ("notes.docx", "not a receipt")):
            with open(os.path.join(self.inbox, name), "w", encoding="utf-8") as f:
                f.write(data)
        self.before = snapshot(self.inbox)
        I._HASH_CACHE.clear()
        self.msgs = []
        self.answer = NO
        for name in ("information", "warning", "critical"):
            p = mock.patch.object(QMessageBox, name, staticmethod(
                lambda *a, n=name, **k: self.msgs.append((n, a[1], a[2])) or
                QMessageBox.StandardButton.Ok))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(QMessageBox, "question", staticmethod(
            lambda *a, **k: self.msgs.append(("question", a[1], a[2])) or self.answer))
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        from ui.pages import receipts as P
        for w in list(P._RUNNING):          # let background scans finish first
            w.wait(15000)
        for _ in range(5):
            QApplication.processEvents()
        # The watched folder is never changed by anything the page does.
        self.assertEqual(snapshot(self.inbox), self.before)
        db_config.SETTINGS_PATH = self._orig
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def page(self, db=None) -> ReceiptsPage:
        page = ReceiptsPage(db or self.db)
        self.addCleanup(page.deleteLater)
        page.refresh()
        return page

    def scanned(self, page):
        page.scan = None
        page.rescan()
        self.assertTrue(pump(lambda: page.scan is not None), "scan didn't finish")
        return page.scan

    def batch(self, page, start):
        start()
        self.assertTrue(pump(lambda: not page.last_worker.isRunning()
                             and getattr(page, "last_summary", None) is not None, 2000))
        return page.last_summary


class FolderStatusTests(InboxPageCase):
    def test_invitation_when_no_folder(self):
        page = self.page()
        self.assertIn("Keep downloaded receipts in a folder?", page._folder_lbl.text())
        self.assertTrue(page._rescan_btn.isHidden())
        self.assertTrue(page._banner.isHidden())
        self.assertTrue(page._status_lbl.isHidden())

    def test_status_banner_and_not_now(self):
        db_config.set_receipt_folder(self.db_path, self.inbox)
        page = self.page()
        self.assertIn(self.inbox, page._folder_lbl.text())
        self.scanned(page)
        text = page._status_lbl.text()
        self.assertIn("3 files in folder · 0 imported · 3 new", text)
        self.assertIn("1 other file ignored (not receipts)", text)
        self.assertFalse(page._banner.isHidden())
        self.assertEqual(page._parse_btn.text(), "Parse 3 new")
        page._notnow_btn.click()
        self.assertTrue(page._banner.isHidden())
        self.scanned(page)                                  # same counts: stays hidden
        self.assertTrue(page._banner.isHidden())
        extra = os.path.join(self.inbox, "late.txt")
        with open(extra, "w", encoding="utf-8") as f:
            f.write(TXT + "late\n")
        self.before = snapshot(self.inbox)
        self.scanned(page)                                  # counts changed: back again
        self.assertFalse(page._banner.isHidden())
        self.assertEqual(page._parse_btn.text(), "Parse 4 new")

    def test_parse_flow_queue_and_filters(self):
        db_config.set_receipt_folder(self.db_path, self.inbox)
        page = self.page()
        self.scanned(page)
        s = self.batch(page, page._parse_btn.click)
        self.assertEqual(s.text(), "Imported 3 · already on file 0 · couldn't read 0")
        self.assertEqual(self.msgs[-1][1], "Receipts Imported")
        self.assertTrue(pump(lambda: page.scan is not None and page.scan.count(I.NEW) == 0))
        self.scanned(page)
        self.assertIn("3 files in folder · 3 imported · 0 new", page._status_lbl.text())
        self.assertIn("3 imported but not reviewed", page._status_lbl.text())
        self.assertEqual(page._banner_review_btn.text(), "Review (3)")

        # Filters, and the clickable "not reviewed" count.
        page._on_status_link(I.NEEDS_REVIEW)
        self.assertEqual(page._filter.currentData(), I.NEEDS_REVIEW)
        self.assertEqual(page._table.rowCount(), 3)
        page.set_filter(I.SAVED)
        self.assertEqual(page._table.rowCount(), 0)

        # Review queue: oldest first, "n of 3" in the title, Next after each save.
        titles = []

        def fake_exec(dlg):
            titles.append(dlg.windowTitle())
            return QDialog.DialogCode.Accepted
        self.answer = YES
        with mock.patch.object(RD.ReceiptReviewDialog, "exec", fake_exec):
            page.start_review_queue()
        self.assertEqual(titles, ["Review Receipt — 1 of 3", "Review Receipt — 2 of 3",
                                  "Review Receipt — 3 of 3"])
        self.assertEqual([m[2] for m in self.msgs if m[1] == "Next Receipt"],
                         ["Open the next receipt (2 of 3)?", "Open the next receipt (3 of 3)?"])

        # Cancel exits the queue. (These receipts weren't really saved, so the
        # first one is read again in the background before its dialog opens.)
        titles.clear()
        with mock.patch.object(RD.ReceiptReviewDialog, "exec",
                               lambda dlg: titles.append(dlg.windowTitle()) or
                               QDialog.DialogCode.Rejected):
            page.start_review_queue()
            self.assertTrue(pump(lambda: titles and not page.last_worker.isRunning()))
            pump(lambda: False, 30)                   # nothing else should open
        self.assertEqual(titles, ["Review Receipt — 1 of 3"])

        # Save one to the books: counts and the Saved filter follow.
        rid = page.review_queue()[0]["id"]
        self.db.save_receipt({"id": rid, "status": "booked"})
        page.refresh_table()
        self.assertEqual(page._table.rowCount(), 1)          # Saved filter still on
        self.scanned(page)
        self.assertIn("2 imported but not reviewed", page._status_lbl.text())
        self.assertIn("1 saved to books", page._status_lbl.text())

    def test_renamed_duplicate_never_imported_twice(self):
        db_config.set_receipt_folder(self.db_path, self.inbox)
        page = self.page()
        self.scanned(page)
        self.batch(page, page.parse_inbox)
        dup_dir = os.path.join(self.dir, "Second inbox")
        os.makedirs(dup_dir)
        shutil.copy2(os.path.join(self.inbox, "corner.txt"), os.path.join(dup_dir, "renamed.txt"))
        page.set_folder(dup_dir)
        self.assertTrue(pump(lambda: page.scan is not None and page.scan.folder == dup_dir))
        self.assertIn("1 is a copy of receipts already on file", page._status_lbl.text())
        self.assertEqual(page.scan.count(I.NEW), 0)
        self.assertEqual(len(self.db.get_receipts()), 3)

    def test_ai_files_need_two_confirmations(self):
        img = os.path.join(self.inbox, "photo.png")
        with open(img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
        self.before = snapshot(self.inbox)
        db_config.set_receipt_folder(self.db_path, self.inbox)
        db_config.set_flag("ai_receipts_enabled", True)
        db_config.set_ai_api_key("sk-test-not-real")
        page = self.page()
        self.scanned(page)
        with mock.patch.object(R, "parse_with_ai") as ai:
            self.answer = NO                                  # "Read them with AI now?" → No
            s = self.batch(page, page.parse_inbox)
            self.assertEqual(len(s.need_ai), 1)
            self.assertIn("1 need Read with AI", s.text())
            ai.assert_not_called()
            page._offer_ai(s.need_ai)                         # ask again
            asked = [m[1] for m in self.msgs if m[0] == "question"]
            self.assertEqual(asked[-1], "Read with AI?")
            ai.assert_not_called()
            # Yes to the offer but No to "Send to Anthropic?" → still nothing sent.
            answers = iter([YES, NO])
            with mock.patch.object(QMessageBox, "question", staticmethod(
                    lambda *a, **k: self.msgs.append(("question", a[1], a[2]))
                    or next(answers))):
                page._offer_ai(s.need_ai)
            self.assertEqual(self.msgs[-1][1], "Send to Anthropic?")
            self.assertIn("photo.png", self.msgs[-1][2])
            ai.assert_not_called()

    def test_ai_off_means_nothing_can_be_sent(self):
        page = self.page()
        self.answer = YES
        with mock.patch.object(R, "parse_with_ai") as ai:
            page._offer_ai(["X1"])
        ai.assert_not_called()
        self.assertEqual(self.msgs[-1][1], "AI Receipt Reading Is Off")

    def test_cancel_stops_after_current_file(self):
        db_config.set_receipt_folder(self.db_path, self.inbox)
        page = self.page()
        self.scanned(page)
        real = I.import_and_parse

        def slow(**kw):
            n = {"calls": 0}

            def parse(p):
                n["calls"] += 1
                if n["calls"] == 1:
                    page.last_worker.cancelled.set()          # as if Cancel was pressed
                return R.parse_file(p, use_ai=False)
            return real(parse=parse, **kw)
        with mock.patch.object(I, "import_and_parse", slow):
            s = self.batch(page, page.parse_inbox)
        self.assertTrue(s.cancelled)
        self.assertEqual((s.done, len(s.imported)), (1, 1))
        self.assertIn("stopped after 1 of 3", self.msgs[-1][2] if self.msgs[-1][1] ==
                      "Receipts Imported" else s.text())

    def test_unavailable_folder_message(self):
        db_config.set_receipt_folder(self.db_path, os.path.join(self.dir, "gone"))
        page = self.page()
        self.scanned(page)
        self.assertIn("wasn't found", page._status_lbl.text())
        self.assertTrue(page._banner.isHidden())

    def test_read_only_scans_but_cannot_import(self):
        db_config.set_receipt_folder(self.db_path, self.inbox)
        self.db.close()
        ro = Database(self.db_path, read_only=True)
        page = self.page(ro)
        self.scanned(page)
        self.assertIn("3 new", page._status_lbl.text())
        for w in (page._add_btn, page._manual_btn, page._parse_btn):
            self.assertFalse(w.isEnabled())
            self.assertIn("read-only", w.toolTip())
        self.assertFalse(page._drop.acceptDrops())
        page.parse_inbox()
        self.assertEqual(ro.get_receipts(), [])
        dlg = RD.ReceiptReviewDialog(ro, {"id": "none", "status": "new"})
        self.addCleanup(dlg.deleteLater)
        self.assertFalse(dlg.book_btn.isEnabled())
        self.assertFalse(dlg.draft_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
