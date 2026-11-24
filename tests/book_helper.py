"""
Both-book-types test helper (v1.19).

Every v1.19 feature must work in a PERSONAL book and a BUSINESS book, so the
new tests build a fresh Database for each and run their checks once per type:

    class MyTests(BothBooksTestCase):
        def test_something(self):
            for db in self.each_book():          # once per book type
                acct = self.checking(db)
                ...

each_book() yields a brand-new Database (its own temp file) for each book
type; every assertion message is prefixed with "[book=<type>]" so a failure
says which book type broke.
"""
import os
import shutil
import tempfile
import unittest

import tests  # noqa: F401  (puts app/ on sys.path)
from sheets.client import Database, BOOK_PERSONAL, BOOK_BUSINESS

BOOK_TYPES = (BOOK_PERSONAL, BOOK_BUSINESS)


def make_db(book_type: str, directory: str, name: str = "") -> Database:
    """A fresh Database of the given book type in directory."""
    path = os.path.join(directory, f"{name or book_type}.db")
    return Database(path, book_type=book_type)


class BothBooksTestCase(unittest.TestCase):
    """Base class: self.each_book() runs the body once per book type."""

    def setUp(self):
        super().setUp()
        self._dir = tempfile.mkdtemp()
        self._n = 0

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)
        super().tearDown()

    def new_db(self, book_type: str) -> Database:
        self._n += 1
        return make_db(book_type, self._dir, f"{book_type}{self._n}")

    current_book = ""

    def each_book(self):
        for bt in BOOK_TYPES:
            self.current_book = bt
            db = self.new_db(bt)
            self.assertEqual(db.is_business(), bt == BOOK_BUSINESS)
            yield db
        self.current_book = ""

    def _formatMessage(self, msg, standardMsg):
        out = super()._formatMessage(msg, standardMsg)
        return f"[book={self.current_book}] {out}" if self.current_book else out

    # ── small fixtures shared by the v1.19 tests ─────────────────────────────

    @staticmethod
    def checking(db, name: str = "Checking", opening: float = 1000.0) -> str:
        return db.save_account({"name": name, "type": "checking",
                                "opening_balance": str(opening)})["id"]

    @staticmethod
    def category(db, name: str, ctype: str = "expense") -> str:
        for c in db.get_categories(include_inactive=True):
            if c["name"] == name and c.get("type") == ctype:
                return c["id"]
        return db.save_category({"name": name, "type": ctype})["id"]
