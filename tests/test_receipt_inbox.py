"""Watched receipts folder: scan/classification, batch import + parse, AI gating,
and the per-computer folder setting. The folder must never be changed."""
import builtins
import os
import shutil
import sqlite3
import string
import tempfile
import time
import types
import unittest
from unittest import mock

import tests  # noqa: F401
import db_config
from parsers import receipt as R
from sheets.client import Database
from utils import receipt_inbox as I

TXT = "Corner Shop\n2026-05-01\nPaper 4.00\nTotal 4.00\n"
HTML = "<html><body><h1>Cafe Uno</h1><p>2026-05-02</p><p>Latte $4.50</p><p>Total $4.50</p></body></html>"
EML = ("From: Orders <orders@shop.example>\r\nSubject: Receipt\r\nContent-Type: text/plain\r\n\r\n"
       "Thanks\r\nCable 9.99\r\nTotal 9.99\r\n")


def snapshot(folder: str) -> dict:
    out = {}
    for root, dirs, files in os.walk(folder):
        for f in files:
            p = os.path.join(root, f)
            st = os.stat(p)
            out[os.path.relpath(p, folder)] = (st.st_size, st.st_mtime_ns)
    return out


class InboxCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "books.db"))
        self.inbox = os.path.join(self.dir, "My Receipts")
        os.makedirs(self.inbox)
        I._HASH_CACHE.clear()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def put(self, rel: str, data) -> str:
        p = os.path.join(self.inbox, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return p

    def scan(self, **kw):
        return I.scan_for_db(self.db, self.inbox, **kw)

    def states(self, res) -> dict:
        return {f.rel: f.state for f in res.files}

    def assertFolderUnchanged(self, before):
        self.assertEqual(snapshot(self.inbox), before)


class ScanTests(InboxCase):
    def test_empty_folder(self):
        res = self.scan()
        self.assertEqual((len(res.files), res.unsupported, res.ignored, res.error), (0, [], 0, ""))

    def test_mixed_files_and_ignored(self):
        for rel, data in (("a.txt", TXT), ("b.html", HTML), ("c.eml", EML), ("d.pdf", b"%PDF"),
                          ("e.JPG", b"\xff\xd8"), ("notes.docx", b"x"), ("archive.zip", b"x"),
                          ("~$draft.docx", b"x"), ("x.tmp", b"x"), ("Thumbs.db", b"x"),
                          ("desktop.ini", b"x"), (".hidden.txt", b"x"),
                          ("half.pdf.crdownload", b"x")):
            self.put(rel, data)
        before = snapshot(self.inbox)
        res = self.scan()
        self.assertEqual(sorted(f.rel for f in res.files),
                         ["a.txt", "b.html", "c.eml", "d.pdf", "e.JPG"])
        self.assertEqual(res.unsupported, ["archive.zip", "notes.docx"])
        self.assertEqual(res.ignored, 6)
        self.assertEqual(res.count(I.NEW), 5)
        self.assertFolderUnchanged(before)

    def test_subfolders(self):
        self.put("top.txt", TXT)
        self.put(os.path.join("2026", "May", "deep.txt"), TXT + "x")
        self.put(os.path.join("FinanceBook Receipts", "books", "copy.txt"), TXT)
        self.assertEqual([f.rel for f in self.scan().files], ["top.txt"])
        rels = [f.rel for f in self.scan(subfolders=True).files]
        self.assertEqual(rels, [os.path.join("2026", "May", "deep.txt"), "top.txt"])

    def test_classification(self):
        paths = {n: self.put(n, TXT.replace("Paper", n)) for n in
                 ("new.txt", "unparsed.txt", "parsed.txt", "draft.txt", "saved.txt")}
        ids = {}
        for n in ("unparsed.txt", "parsed.txt", "draft.txt", "saved.txt"):
            ids[n] = self.db.add_receipt_file(paths[n])[0]["id"]
        self.db.save_receipt({"id": ids["parsed.txt"], "parse_method": "text", "vendor": "Shop"})
        self.db.save_receipt({"id": ids["draft.txt"], "parse_method": "text", "status": "reviewed"})
        self.db.save_receipt({"id": ids["saved.txt"], "parse_method": "text", "status": "booked"})
        before = snapshot(self.inbox)
        res = self.scan()
        self.assertEqual(self.states(res), {"new.txt": I.NEW, "unparsed.txt": I.NEEDS_PARSING,
                                            "parsed.txt": I.NEEDS_REVIEW, "draft.txt": I.DRAFT,
                                            "saved.txt": I.SAVED})
        self.assertEqual((res.imported, res.hashed), (4, 0))     # matched by name + size
        self.assertFolderUnchanged(before)

    def test_failed_parse_still_needs_parsing(self):
        rid = self.db.add_receipt_file(self.put("scan.txt", TXT))[0]["id"]
        self.db.save_receipt({"id": rid, "parse_method": "manual", "raw_text": ""})
        self.assertEqual(self.states(self.scan()), {"scan.txt": I.NEEDS_PARSING})

    def test_renamed_duplicate_is_already_on_file(self):
        self.db.add_receipt_file(self.put("original.txt", TXT))
        self.put("renamed copy.txt", TXT)
        res = self.scan()
        dup = next(f for f in res.files if f.rel == "renamed copy.txt")
        self.assertTrue(dup.renamed_duplicate)
        self.assertEqual(dup.state, I.NEEDS_PARSING)
        self.assertEqual(res.count(I.NEW), 0)
        s = I.import_and_parse(self.db, [dup.path])
        self.assertEqual((s.imported, s.duplicates), ([], ["renamed copy.txt"]))
        self.assertEqual(len(self.db.get_receipts()), 1)

    def test_same_name_different_content_is_new(self):
        self.db.add_receipt_file(self.put("receipt.txt", TXT))
        self.put("receipt.txt", TXT + "Extra line 1.00\n")
        self.assertEqual(self.states(self.scan()), {"receipt.txt": I.NEW})

    def test_added_and_deleted_files_follow_rescans(self):
        self.put("one.txt", TXT)
        self.assertEqual(len(self.scan().files), 1)
        two = self.put("two.txt", HTML)
        self.assertEqual(sorted(self.states(self.scan())), ["one.txt", "two.txt"])
        os.remove(two)
        self.assertEqual(sorted(self.states(self.scan())), ["one.txt"])

    def test_online_only_files_are_never_opened(self):
        self.db.add_receipt_file(self.put("on file.txt", TXT))
        cloud = self.put("cloud copy.txt", TXT)        # same size: a hash candidate
        real_scandir = os.scandir

        class Entry:
            def __init__(self, e):
                self._e, self.name, self.path = e, e.name, e.path

            def is_dir(self, **kw):
                return self._e.is_dir(**kw)

            def is_file(self, **kw):
                return self._e.is_file(**kw)

            def stat(self, **kw):
                st = self._e.stat(**kw)
                attrs = I.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS if self.path == cloud else 0
                return types.SimpleNamespace(st_size=st.st_size, st_mtime=st.st_mtime,
                                             st_file_attributes=attrs)

        class Scandir:
            def __init__(self, p):
                self._it = real_scandir(p)

            def __enter__(self):
                return self

            def __iter__(self):
                return (Entry(e) for e in self._it)

            def __exit__(self, *a):
                self._it.close()

            def close(self):
                self._it.close()

        hashed, opened = [], []
        real_open = builtins.open

        def spy_open(f, *a, **k):
            opened.append(os.path.abspath(str(f)))
            return real_open(f, *a, **k)
        with mock.patch.object(I.os, "scandir", Scandir), \
                mock.patch.object(builtins, "open", spy_open):
            res = I.scan_folder(self.inbox, self.db.get_receipts(),
                                lambda rc: os.path.getsize(self.db.receipt_abs_path(rc)),
                                hasher=lambda p: hashed.append(p) or "x")
        f = next(f for f in res.files if f.path == cloud)
        self.assertTrue(f.online_only)
        self.assertEqual(f.state, I.NEW)
        self.assertEqual(res.online_only_new, 1)
        self.assertNotIn(cloud, hashed)
        self.assertNotIn(os.path.abspath(cloud), opened)

    def test_two_thousand_files_fast_and_unhashed(self):
        for n in range(2000):
            self.put(f"r{n:04d}.txt", "x" * (100 + n))       # every size different
        self.db.add_receipt_file(self.put("known.txt", "y" * 50))
        before = snapshot(self.inbox)
        hashed = []
        t = time.perf_counter()
        res = I.scan_folder(self.inbox, self.db.get_receipts(),
                            lambda rc: os.path.getsize(self.db.receipt_abs_path(rc)),
                            hasher=lambda p: hashed.append(p) or "")
        elapsed = time.perf_counter() - t
        self.assertEqual((len(res.files), res.count(I.NEW), res.imported), (2001, 2000, 1))
        self.assertEqual(hashed, [])
        self.assertLess(elapsed, 5.0)
        self.assertFolderUnchanged(before)

    def test_only_size_matches_are_hashed(self):
        self.db.add_receipt_file(self.put("a.txt", "z" * 300))
        self.put("b.txt", "q" * 300)            # same size, different content
        self.put("c.txt", "q" * 301)
        hashed = []
        from sheets.receipts_store import file_sha256
        res = I.scan_folder(self.inbox, self.db.get_receipts(),
                            lambda rc: os.path.getsize(self.db.receipt_abs_path(rc)),
                            hasher=lambda p: hashed.append(os.path.basename(p)) or file_sha256(p))
        self.assertEqual(hashed, ["b.txt"])
        self.assertEqual(self.states(res), {"a.txt": I.NEEDS_PARSING, "b.txt": I.NEW,
                                            "c.txt": I.NEW})
        # Unchanged files aren't hashed again on the next scan.
        I.scan_folder(self.inbox, self.db.get_receipts(),
                      lambda rc: os.path.getsize(self.db.receipt_abs_path(rc)),
                      hasher=lambda p: hashed.append(os.path.basename(p)) or file_sha256(p))
        self.assertEqual(hashed, ["b.txt"])

    def test_unavailable_folders(self):
        res = I.scan_for_db(self.db, os.path.join(self.dir, "gone"))
        self.assertIn("wasn't found", res.error)
        with mock.patch.object(I.os, "scandir", side_effect=PermissionError(13, "denied")):
            self.assertIn("permission denied", I.scan_for_db(self.db, self.inbox).error)
        used = {os.path.splitdrive(p)[0].upper() for p in (self.dir, os.getcwd())}
        letter = next(f"{c}:" for c in reversed(string.ascii_uppercase)
                      if f"{c}:" not in used and not os.path.exists(f"{c}:\\"))
        res = I.scan_for_db(self.db, f"{letter}\\Receipts")
        self.assertIn("isn't connected", res.error)
        f = self.put("file.txt", TXT)
        self.assertIn("a file, not a folder", I.scan_for_db(self.db, f).error)

    def test_spaces_and_unicode(self):
        self.inbox = os.path.join(self.dir, "Reçus été 2026", "sub dir")
        os.makedirs(self.inbox)
        p = self.put("Café ☕ receipt.txt", TXT)
        res = self.scan()
        self.assertEqual([(f.rel, f.state) for f in res.files], [("Café ☕ receipt.txt", I.NEW)])
        s = I.import_and_parse(self.db, [p])
        self.assertEqual(len(s.imported), 1)
        self.assertEqual(self.states(self.scan()), {"Café ☕ receipt.txt": I.NEEDS_REVIEW})


class BatchTests(InboxCase):
    def test_import_parse_summary_and_folder_unchanged(self):
        paths = [self.put("a.txt", TXT), self.put("b.html", HTML), self.put("c.eml", EML)]
        before = snapshot(self.inbox)
        seen = []
        s = I.import_and_parse(self.db, paths, progress=lambda d, t, n: seen.append((d, t, n)))
        self.assertEqual((len(s.imported), s.duplicates, s.failed, s.need_ai), (3, [], [], []))
        self.assertEqual(seen, [(0, 3, "a.txt"), (1, 3, "b.html"), (2, 3, "c.eml"), (3, 3, "")])
        self.assertEqual(s.text(), "Imported 3 · already on file 0 · couldn't read 0")
        vendors = sorted(rc["vendor"] for rc in self.db.get_receipts())
        self.assertEqual(vendors, ["Cafe Uno", "Corner Shop", "Orders"])
        self.assertFolderUnchanged(before)
        self.assertEqual(self.scan().count(I.NEEDS_REVIEW), 3)

    def test_cancel_stops_after_current_file(self):
        paths = [self.put(f"r{n}.txt", TXT + f"#{n}\n") for n in range(5)]
        calls = []

        def parse(p):
            calls.append(p)
            return R.parse_file(p, use_ai=False)
        s = I.import_and_parse(self.db, paths, parse=parse,
                               should_cancel=lambda: len(calls) >= 2)
        self.assertTrue(s.cancelled)
        self.assertEqual((s.done, len(s.imported)), (2, 2))
        self.assertIn("stopped after 2 of 5", s.text())
        self.assertEqual(len(self.db.get_receipts()), 2)

    def test_one_bad_file_never_stops_the_batch(self):
        good = self.put("good.txt", TXT)
        from tests import pdf_fixtures as F
        bad_pdf = self.put("broken.pdf", F.encrypted_pdf())
        boom = self.put("boom.txt", TXT + "boom\n")
        later = self.put("later.html", HTML)

        def parse(p):
            if p.endswith("boom.txt"):
                raise RuntimeError("parser crashed")
            return R.parse_file(p, use_ai=False)
        s = I.import_and_parse(self.db, [good, bad_pdf, boom, later], parse=parse)
        self.assertEqual(len(s.imported), 3)           # the crashed one stays "needs parsing"
        self.assertEqual([n for n, _ in s.failed], ["broken.pdf", "boom.txt"])
        self.assertEqual(s.need_ai, [])
        self.assertEqual(s.text(), "Imported 3 · already on file 0 · couldn't read 2")
        self.assertEqual(len(self.db.get_receipts()), 4)
        # The crashed file and the unreadable PDF both still need parsing.
        self.assertEqual({f.rel: f.state for f in self.scan().files},
                         {"good.txt": I.NEEDS_REVIEW, "broken.pdf": I.NEEDS_PARSING,
                          "boom.txt": I.NEEDS_PARSING, "later.html": I.NEEDS_REVIEW})

    def test_reparse_imported_receipts(self):
        rid = self.db.add_receipt_file(self.put("a.txt", TXT))[0]["id"]
        s = I.import_and_parse(self.db, receipt_ids=[rid])
        self.assertEqual(s.reparsed, [rid])
        self.assertEqual(self.db.get_receipt(rid)["vendor"], "Corner Shop")

    def test_images_need_ai_and_are_never_sent(self):
        img = self.put("photo.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
        with mock.patch.object(R, "parse_with_ai") as ai:
            s = I.import_and_parse(self.db, [img])
        ai.assert_not_called()
        self.assertEqual(len(s.need_ai), 1)
        self.assertEqual(s.failed, [])
        self.assertIn("1 need Read with AI", s.text())

    def test_read_with_ai_only_when_called(self):
        rid = self.db.add_receipt_file(self.put("photo.png", b"\x89PNG" + b"\0" * 10))[0]["id"]
        reader = mock.Mock(return_value=R.parse_text(TXT, "ai"))
        s = I.read_with_ai(self.db, [rid], "sk-test", "model", reader=reader)
        reader.assert_called_once()
        self.assertEqual(s.reparsed, [rid])
        self.assertEqual(self.db.get_receipt(rid)["parse_method"], "ai")
        reader.side_effect = R.AIReceiptError("Rate limited")
        s = I.read_with_ai(self.db, [rid], "sk-test", "model", reader=reader)
        self.assertEqual(s.failed, [("photo.png", "Rate limited")])


class FolderSettingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = db_config.SETTINGS_PATH
        db_config.SETTINGS_PATH = os.path.join(self.dir, "app_settings.json")

    def tearDown(self):
        db_config.SETTINGS_PATH = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_per_database_isolated_and_clearable(self):
        a, b = os.path.join(self.dir, "a.db"), os.path.join(self.dir, "b.db")
        self.assertEqual(db_config.get_receipt_folder(a), {"path": "", "subfolders": False})
        db_config.set_receipt_folder(a, os.path.join(self.dir, "inbox A"), subfolders=True)
        db_config.set_receipt_folder(b, os.path.join(self.dir, "inbox B"))
        self.assertEqual(db_config.get_receipt_folder(a)["path"],
                         os.path.join(self.dir, "inbox A"))
        self.assertTrue(db_config.get_receipt_folder(a)["subfolders"])
        self.assertEqual(db_config.get_receipt_folder(b)["path"],
                         os.path.join(self.dir, "inbox B"))
        # Same database, spelled differently.
        alt = os.path.join(self.dir, ".", "A.DB") if os.name == "nt" else a
        self.assertEqual(db_config.get_receipt_folder(alt)["path"],
                         os.path.join(self.dir, "inbox A"))
        db_config.clear_receipt_folder(a)
        self.assertEqual(db_config.get_receipt_folder(a)["path"], "")
        self.assertEqual(db_config.get_receipt_folder(b)["path"], os.path.join(self.dir, "inbox B"))
        self.assertEqual(db_config.get_value("last_receipt_folder_dir"),
                         os.path.join(self.dir, "inbox B"))

    def test_nothing_lands_in_the_database(self):
        path = os.path.join(self.dir, "shared.db")
        db = Database(path)
        folder = os.path.join(self.dir, "Very Distinct Inbox Name")
        db_config.set_receipt_folder(path, folder)
        db.close()
        with open(path, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"Very Distinct Inbox Name", raw)
        self.assertNotIn("Very Distinct Inbox Name".encode("utf-16-le"), raw)
        conn = sqlite3.connect(path)
        try:
            values = " ".join(str(v) for (v,) in conn.execute("SELECT value FROM settings"))
        finally:
            conn.close()
        self.assertNotIn("Inbox", values)


if __name__ == "__main__":
    unittest.main()
