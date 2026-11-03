"""
SQLite database backend.
All data lives in finances.db — a single file synced by OneDrive / Google Drive.
The file is kept in rollback-journal mode so it is complete on disk after every
commit (see Database._conn).
No external services or credentials required.
"""
import os
import sqlite3
import uuid
from datetime import datetime, timedelta

from sheets import backup as _backup
from sheets.receipts_store import ReceiptsMixin, RECEIPTS_SCHEMA
from sheets.assets_store import AssetsMixin, ComputedMixin, ASSETS_SCHEMA
from sheets.sales_store import SalesMixin, SALES_SCHEMA
from sheets.inventory_store import InventoryMixin, INVENTORY_SCHEMA
from sheets.payroll_store import PayrollMixin, PAYROLL_SCHEMA, PEOPLE_COLUMNS
from sheets.tax_store import TaxMixin, TAX_SCHEMA
from sheets.admin_store import AdminMixin, ADMIN_SCHEMA

# Bump whenever SCHEMA or _migrate() changes. An existing database with a lower
# PRAGMA user_version is backed up before it is migrated.
#   1  — implicit: everything up to v1.0.22 (user_version was never set → 0)
#   2  — business books: people, owner roles, paid_by/charged_to, tax lines
#   3  — receipts, receipt line items, attachments, vendor memory
#   4  — fixed assets / prepaids, asset usage
#   5  — contacts, invoices, bills, document payments; transactions.contact_id
#   6  — inventory items and stock movements
#   7  — employees (people columns), time entries, payroll runs
#   8  — mileage log, budgets
#   9  — recurring items, audit log + triggers, period lock, created/updated_by
SCHEMA_VERSION = 9

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rules (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    field       TEXT NOT NULL DEFAULT 'payee',
    operator    TEXT NOT NULL DEFAULT 'contains',
    value       TEXT NOT NULL DEFAULT '',
    value2      TEXT DEFAULT '',
    category_id TEXT DEFAULT '',
    class_id    TEXT DEFAULT '',
    priority    INTEGER DEFAULT 0,
    active      INTEGER DEFAULT 1,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    type             TEXT DEFAULT 'checking',
    institution      TEXT DEFAULT '',
    opening_balance  REAL DEFAULT 0,
    currency         TEXT DEFAULT 'USD',
    active           INTEGER DEFAULT 1,
    created_at       TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id               TEXT PRIMARY KEY,
    date             TEXT,
    account_id       TEXT,
    payee            TEXT DEFAULT '',
    memo             TEXT DEFAULT '',
    amount           REAL DEFAULT 0,
    category_id      TEXT DEFAULT '',
    class_id         TEXT DEFAULT '',
    is_transfer      INTEGER DEFAULT 0,
    transfer_pair_id TEXT DEFAULT '',
    reconciled       INTEGER DEFAULT 0,
    reconcile_id     TEXT DEFAULT '',
    notes            TEXT DEFAULT '',
    import_hash      TEXT DEFAULT '',
    created_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_txn_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_txn_date    ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txn_hash    ON transactions(import_hash);

CREATE TABLE IF NOT EXISTS categories (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    parent_id TEXT DEFAULT '',
    type      TEXT DEFAULT 'expense',
    active    INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS classes (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    parent_id TEXT DEFAULT '',
    active    INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS reconciliations (
    id                TEXT PRIMARY KEY,
    account_id        TEXT,
    statement_date    TEXT,
    statement_balance REAL DEFAULT 0,
    cleared_balance   REAL DEFAULT 0,
    difference        REAL DEFAULT 0,
    status            TEXT DEFAULT 'open',
    created_at        TEXT,
    completed_at      TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS loans (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    lender               TEXT DEFAULT '',
    original_principal   REAL DEFAULT 0,
    annual_rate          REAL DEFAULT 0,
    term_months          INTEGER DEFAULT 0,
    start_date           TEXT DEFAULT '',
    payment_amount       REAL DEFAULT 0,
    account_id           TEXT DEFAULT '',
    interest_category_id TEXT DEFAULT '',
    created_at           TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS people (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    role       TEXT DEFAULT 'owner',
    email      TEXT DEFAULT '',
    phone      TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    active     INTEGER DEFAULT 1,
    created_at TEXT DEFAULT ''
);
"""

# Columns added to existing tables after the initial schema. Columns are only
# ever ADDED — existing rows are never rewritten by a migration.
_NEW_COLUMNS = [
    ("transactions", "loan_id",          "TEXT DEFAULT ''"),
    ("transactions", "principal_amount", "REAL DEFAULT 0"),
    ("transactions", "interest_amount",  "REAL DEFAULT 0"),
    ("transactions", "split_group_id",   "TEXT DEFAULT ''"),
    # v2 — business books
    ("transactions", "paid_by",          "TEXT DEFAULT ''"),
    ("transactions", "charged_to",       "TEXT DEFAULT ''"),
    ("transactions", "reimbursable",     "INTEGER DEFAULT 0"),
    ("transactions", "updated_at",       "TEXT DEFAULT ''"),
    # v5 — vendors / customers
    ("transactions", "contact_id",       "TEXT DEFAULT ''"),
    ("accounts",     "role",             "TEXT DEFAULT ''"),
    ("categories",   "tax_line",         "TEXT DEFAULT ''"),
    ("categories",   "deduct_pct",       "REAL DEFAULT 100"),
    # v9 — audit trail
    ("transactions", "created_by",       "TEXT DEFAULT ''"),
    ("transactions", "updated_by",       "TEXT DEFAULT ''"),
] + PEOPLE_COLUMNS

# Optional transaction columns: written only when the caller's dict has the key,
# so older code paths that build a txn dict from scratch can't blank them.
_TXN_EXTRA_COLS = ("paid_by", "charged_to", "reimbursable", "contact_id")

# Fields the UI expects as strings "0"/"1"
_BOOL_FIELDS  = {"is_transfer", "reconciled", "active", "reimbursable"}
# Fields the UI expects as strings (amounts)
_FLOAT_FIELDS = {"amount", "opening_balance", "statement_balance",
                 "cleared_balance", "difference",
                 "principal_amount", "interest_amount",
                 "original_principal", "annual_rate", "payment_amount",
                 "deduct_pct"}

BOOK_PERSONAL = "personal"
BOOK_BUSINESS = "business"

# Built-in owner accounts for business books: role → (name, type).
BUSINESS_ACCOUNTS = {
    "owner_capital": ("Owner's Equity / Capital Contributions", "equity"),
    "owner_draws":   ("Owner's Draws", "equity"),
    "due_to_owner":  ("Due to Owner", "liability"),
    "petty_cash":    ("Petty Cash", "cash"),
}


def _to_dict(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in _BOOL_FIELDS:
        if k in d:
            d[k] = str(int(d[k]) if d[k] is not None else 0)
    for k in _FLOAT_FIELDS:
        if k in d:
            d[k] = str(float(d[k]) if d[k] is not None else 0.0)
    return d


def _to_dicts(rows) -> list[dict]:
    return [_to_dict(r) for r in rows]


class NewerSchemaError(Exception):
    """The file was upgraded by a newer FinanceBook than this one."""
    def __init__(self, version: int):
        super().__init__(
            f"This database was updated by a newer version of FinanceBook "
            f"(database format {version}; this app supports up to {SCHEMA_VERSION}). "
            "Update FinanceBook on this computer first, then open it again.")
        self.version = version


class ReadOnlyUpgradeError(Exception):
    """A read-only open was asked for, but the file needs a schema upgrade first."""
    def __init__(self, version: int):
        super().__init__(
            "This database was last saved by an older version of FinanceBook and "
            "needs a one-time upgrade, which can't be done while it's open read-only. "
            "Open it for editing once it's no longer in use elsewhere.")
        self.version = version


class _ClosingConnection(sqlite3.Connection):
    """`with self._conn() as c:` commits (or rolls back) AND closes, so no
    connection lingers holding a Windows file lock (matters for restore)."""
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


class Database(ReceiptsMixin, AssetsMixin, ComputedMixin, SalesMixin, InventoryMixin,
               PayrollMixin, TaxMixin, AdminMixin):
    def __init__(self, db_path: str, book_type: str | None = None,
                 read_only: bool = False):
        """
        Open (creating/migrating as needed) the database at db_path.
        book_type is only used when the file is brand new: 'business' seeds the
        Schedule C categories and owner accounts instead of the personal tree.

        read_only=True opens an existing, up-to-date database without writing
        anything — no migration, no triggers, no seeding — and every later
        write fails with "attempt to write a readonly database". Used when
        another computer holds the database's lock.
        """
        self.db_path = db_path
        self.last_migration_backup = ""
        self.session_user = ""
        self.read_only = read_only
        version = self._existing_version()
        if version is not None and version > SCHEMA_VERSION:
            # An older app must never open (and "migrate" or rewrite triggers
            # in) a file a newer app has already upgraded.
            raise NewerSchemaError(version)
        if read_only:
            if version is None:
                raise FileNotFoundError(db_path)
            if version < SCHEMA_VERSION:
                raise ReadOnlyUpgradeError(version)
            return
        self._init_schema(book_type)

    def _existing_version(self) -> int | None:
        """user_version of the file on disk, or None if it doesn't exist yet."""
        if not os.path.exists(self.db_path) or os.path.getsize(self.db_path) == 0:
            return None
        return _backup.read_user_version(self.db_path)

    # ── internal ──────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        if self.read_only:
            conn.execute("PRAGMA query_only=ON")
        else:
            # Rollback-journal (DELETE) mode rather than WAL: after every commit
            # the .db file alone is complete, with no -wal/-shm side files for
            # OneDrive / Google Drive to sync out of step with it. Databases
            # created in WAL mode by older versions are converted on first use;
            # if another connection is open the switch waits for a later one.
            try:
                conn.execute("PRAGMA journal_mode=DELETE")
            except sqlite3.OperationalError:
                pass
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def close(self):
        """
        Leave the file self-contained for syncing: fold any WAL left by an
        older version (or a crash) into the .db and remove the side files.
        Connections are per-operation, so none are held open here.
        """
        if self.read_only or not os.path.exists(self.db_path):
            return
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("PRAGMA journal_mode=DELETE")
            finally:
                conn.close()
        except sqlite3.Error:
            pass

    def _init_schema(self, book_type: str | None = None):
        is_new = (not os.path.exists(self.db_path)
                  or os.path.getsize(self.db_path) == 0)
        if not is_new:
            self._backup_before_migration()
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            conn.executescript(RECEIPTS_SCHEMA)
            conn.executescript(ASSETS_SCHEMA)
            conn.executescript(SALES_SCHEMA)
            conn.executescript(INVENTORY_SCHEMA)
            conn.executescript(PAYROLL_SCHEMA)
            conn.executescript(TAX_SCHEMA)
            conn.executescript(ADMIN_SCHEMA)
        self._migrate()
        self._install_triggers()
        if is_new and book_type in (BOOK_PERSONAL, BOOK_BUSINESS):
            self.set_setting("book_type", book_type)
        if self.is_business():
            self.ensure_business_setup()
        else:
            self.seed_default_categories()

    def _backup_before_migration(self):
        """Copy an existing database aside before its schema is upgraded."""
        conn = sqlite3.connect(self.db_path)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            has_data = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transactions'"
            ).fetchone() is not None
        finally:
            conn.close()
        if has_data and version < SCHEMA_VERSION:
            # If this fails we must not migrate: let the error reach the caller
            # (main.py shows it) rather than touching an un-backed-up file.
            self.last_migration_backup = _backup.backup_database(
                self.db_path, f"pre-migration-v{SCHEMA_VERSION}")

    def seed_default_categories(self):
        """
        Seed the built-in category tree into a brand-new database.

        Runs at most once per database: it is skipped if the database already
        has categories (e.g. an existing file) or has been seeded before, so a
        user who deletes the defaults won't have them reappear.
        """
        if self.get_setting("default_categories_seeded") == "1":
            return
        if self.get_categories():
            # Existing database with its own categories — mark as handled, never
            # add the defaults on top of the user's data.
            self.set_setting("default_categories_seeded", "1")
            return
        try:
            from default_categories import DEFAULT_CATEGORIES
        except Exception:
            return
        for ctype, groups in DEFAULT_CATEGORIES.items():
            for parent_name, children in groups.items():
                parent = self.save_category({"name": parent_name, "type": ctype})
                for child in children:
                    self.save_category({"name": child, "type": ctype,
                                        "parent_id": parent["id"]})
        self.set_setting("default_categories_seeded", "1")

    def _migrate(self):
        """Add columns that were introduced after the initial schema."""
        with self._conn() as c:
            for table, col, typedef in _NEW_COLUMNS:
                existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                if col not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
            c.execute("CREATE INDEX IF NOT EXISTS idx_txn_split ON transactions(split_group_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_txn_pair ON transactions(transfer_pair_id)")
            if c.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _new_id() -> str:
        return str(uuid.uuid4())[:8].upper()

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── accounts ──────────────────────────────────────────────────────────────

    def get_accounts(self, active: int | None = 1) -> list[dict]:
        """
        Accounts ordered by name. active=1 (default) returns only active accounts
        — the behaviour every existing caller relies on. active=0 returns the
        inactive/hidden ones; active=None returns all.
        """
        q = "SELECT * FROM accounts"
        p: list = []
        if active is not None:
            q += " WHERE active=?"
            p.append(active)
        q += " ORDER BY name"
        with self._conn() as c:
            return _to_dicts(c.execute(q, p).fetchall())

    def set_account_active(self, account_id: str, active: bool):
        with self._conn() as c:
            c.execute("UPDATE accounts SET active=? WHERE id=?",
                      (1 if active else 0, account_id))

    def get_account(self, account_id: str) -> dict | None:
        with self._conn() as c:
            return _to_dict(c.execute(
                "SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone())

    def save_account(self, data: dict) -> dict:
        ob = float(data.get("opening_balance") or 0)
        active = 1 if str(data.get("active", "1")) == "1" else 0
        with self._conn() as c:
            if data.get("id") and self.get_account(data["id"]):
                c.execute(
                    "UPDATE accounts SET name=?,type=?,institution=?,"
                    "opening_balance=?,currency=?,active=? WHERE id=?",
                    (data.get("name",""), data.get("type","checking"),
                     data.get("institution",""), ob,
                     data.get("currency","USD"), active, data["id"]))
                if "role" in data:
                    c.execute("UPDATE accounts SET role=? WHERE id=?",
                              (data.get("role") or "", data["id"]))
            else:
                data["id"] = self._new_id()
                data["created_at"] = self._now()
                c.execute(
                    "INSERT INTO accounts "
                    "(id,name,type,institution,opening_balance,currency,active,created_at,role) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (data["id"], data.get("name",""), data.get("type","checking"),
                     data.get("institution",""), ob,
                     data.get("currency","USD"), 1, data["created_at"],
                     data.get("role") or ""))
        return data

    def get_account_by_role(self, role: str) -> dict | None:
        """First active account with a built-in role (e.g. 'due_to_owner')."""
        with self._conn() as c:
            return _to_dict(c.execute(
                "SELECT * FROM accounts WHERE role=? ORDER BY active DESC, created_at LIMIT 1",
                (role,)).fetchone())

    def delete_account(self, account_id: str):
        with self._conn() as c:
            c.execute("UPDATE accounts SET active=0 WHERE id=?", (account_id,))

    # ── transactions ──────────────────────────────────────────────────────────

    def get_transactions(self, account_id: str = None, start: str = None,
                         end: str = None, include_transfers: bool = True) -> list[dict]:
        q = "SELECT * FROM transactions WHERE 1=1"
        p: list = []
        if account_id:
            q += " AND account_id=?";  p.append(account_id)
        if start:
            q += " AND date>=?";       p.append(start)
        if end:
            q += " AND date<=?";       p.append(end)
        if not include_transfers:
            q += " AND is_transfer=0"
        q += " ORDER BY date DESC, created_at DESC"
        with self._conn() as c:
            return _to_dicts(c.execute(q, p).fetchall())

    def get_transaction(self, txn_id: str) -> dict | None:
        with self._conn() as c:
            return _to_dict(c.execute(
                "SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone())

    def save_transaction(self, data: dict) -> dict:
        amt      = float(data.get("amount") or 0)
        is_tr    = 1 if str(data.get("is_transfer","0")) == "1" else 0
        recon    = 1 if str(data.get("reconciled","0")) == "1" else 0
        loan_id  = data.get("loan_id") or ""
        prin_amt = float(data.get("principal_amount") or 0)
        int_amt  = float(data.get("interest_amount") or 0)
        split_gid = data.get("split_group_id") or ""
        extras = [k for k in _TXN_EXTRA_COLS if k in data]
        if "reimbursable" in data:
            data["reimbursable"] = "1" if str(data.get("reimbursable") or "0") == "1" else "0"
        with self._conn() as c:
            if data.get("id") and self.get_transaction(data["id"]):
                c.execute(
                    "UPDATE transactions SET date=?,account_id=?,payee=?,memo=?,amount=?,"
                    "category_id=?,class_id=?,is_transfer=?,transfer_pair_id=?,"
                    "reconciled=?,reconcile_id=?,notes=?,import_hash=?,"
                    "loan_id=?,principal_amount=?,interest_amount=?,split_group_id=? WHERE id=?",
                    (data.get("date",""), data.get("account_id",""),
                     data.get("payee",""), data.get("memo",""), amt,
                     data.get("category_id",""), data.get("class_id",""),
                     is_tr, data.get("transfer_pair_id",""),
                     recon, data.get("reconcile_id",""),
                     data.get("notes",""), data.get("import_hash",""),
                     loan_id, prin_amt, int_amt, split_gid, data["id"]))
            else:
                data["id"] = self._new_id()
                data["created_at"] = self._now()
                c.execute(
                    "INSERT INTO transactions "
                    "(id,date,account_id,payee,memo,amount,category_id,class_id,"
                    "is_transfer,transfer_pair_id,reconciled,reconcile_id,notes,import_hash,"
                    "loan_id,principal_amount,interest_amount,split_group_id,created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (data["id"], data.get("date",""), data.get("account_id",""),
                     data.get("payee",""), data.get("memo",""), amt,
                     data.get("category_id",""), data.get("class_id",""),
                     is_tr, data.get("transfer_pair_id",""),
                     recon, data.get("reconcile_id",""),
                     data.get("notes",""), data.get("import_hash",""),
                     loan_id, prin_amt, int_amt, split_gid, data["created_at"]))
            if extras:
                c.execute(
                    f"UPDATE transactions SET {','.join(k + '=?' for k in extras)},"
                    "updated_at=? WHERE id=?",
                    [int(data[k]) if k == "reimbursable" else (data.get(k) or "")
                     for k in extras] + [self._now(), data["id"]])
            if self.session_user:
                c.execute("UPDATE transactions SET updated_by=?, "
                          "created_by=COALESCE(NULLIF(created_by,''),?) WHERE id=?",
                          (self.session_user, self.session_user, data["id"]))
        return data

    def bulk_save_transactions(self, rows: list[dict]) -> list[dict]:
        saved = []
        for data in rows:
            data["id"] = self._new_id()
            data["created_at"] = self._now()
            saved.append(self.save_transaction(data))
        return saved

    def delete_transaction(self, txn_id: str):
        with self._conn() as c:
            c.execute("DELETE FROM transactions WHERE id=?", (txn_id,))

    def delete_transaction_full(self, txn: dict):
        """
        Delete a transaction together with everything tied to it: the matching
        side of a transfer (same transfer_pair_id) and every line of a split
        (same split_group_id). Use this for the "Delete" action so a transfer
        never leaves an orphaned half behind.
        """
        pair = txn.get("transfer_pair_id") or ""
        gid  = txn.get("split_group_id") or ""
        with self._conn() as c:
            if pair:
                c.execute("DELETE FROM transactions WHERE transfer_pair_id=?", (pair,))
            if gid:
                c.execute("DELETE FROM transactions WHERE split_group_id=?", (gid,))
            if txn.get("id"):
                c.execute("DELETE FROM transactions WHERE id=?", (txn["id"],))

    def link_transfer(self, id_a: str, id_b: str):
        pair_id = self._new_id()
        with self._conn() as c:
            c.execute(
                "UPDATE transactions SET is_transfer=1, transfer_pair_id=? WHERE id IN (?,?)",
                (pair_id, id_a, id_b))

    def unlink_transfer(self, txn_id: str):
        with self._conn() as c:
            row = c.execute(
                "SELECT transfer_pair_id FROM transactions WHERE id=?",
                (txn_id,)).fetchone()
            if row and row["transfer_pair_id"]:
                c.execute(
                    "UPDATE transactions SET is_transfer=0, transfer_pair_id='' "
                    "WHERE transfer_pair_id=?", (row["transfer_pair_id"],))

    def get_transfer_partner(self, txn: dict) -> dict | None:
        """The other transaction in a transfer pair, or None."""
        pid = txn.get("transfer_pair_id", "")
        if not pid:
            return None
        with self._conn() as c:
            return _to_dict(c.execute(
                "SELECT * FROM transactions WHERE transfer_pair_id=? AND id!=? LIMIT 1",
                (pid, txn.get("id", ""))).fetchone())

    def get_transfer_category_id(self) -> str:
        """
        Return the id of the built-in "Account Transfers" category, creating it
        the first time it is needed. Both sides of every transfer are filed under
        this category so transfers never show up as uncategorized. Transfers are
        excluded from P&L regardless, so this is just a clean, consistent label.
        """
        for c in self.get_categories():
            if c.get("name") == "Account Transfers" and not c.get("parent_id"):
                return c["id"]
        return self.save_category({"name": "Account Transfers", "type": "expense"})["id"]

    def save_transfer_pair(self, source: dict, dest_account_id: str) -> dict:
        """
        Record a transfer from one entry: save `source` and auto-create (or update)
        the mirror transaction in dest_account_id, both flagged is_transfer and
        sharing a transfer_pair_id. The user only enters one side; the matching
        side is kept in sync here.

        Direction is fixed regardless of the sign the user typed: money LEAVES the
        source account (negative) and ARRIVES in the destination account
        (positive). Both sides are filed under the "Account Transfers" category and
        need no payee. Both have is_transfer=1, so they are excluded from P&L and
        the dashboard (which already filter transfers out).
        """
        source = dict(source)
        amt = abs(float(source.get("amount") or 0))
        tcat = self.get_transfer_category_id()
        source["is_transfer"] = "1"
        source["amount"] = str(-amt)          # money out of the source account
        source["category_id"] = tcat
        pair_id = source.get("transfer_pair_id") or self._new_id()
        source["transfer_pair_id"] = pair_id
        saved = self.save_transaction(source)

        mirror = self.get_transfer_partner(saved)
        self.save_transaction({
            "id":               mirror["id"] if mirror else "",
            "date":             saved.get("date", ""),
            "account_id":       dest_account_id,
            "payee":            saved.get("payee", ""),
            "memo":             saved.get("memo", ""),
            "amount":           str(amt),       # money into the destination account
            "category_id":      tcat,
            "class_id":         saved.get("class_id", ""),
            "is_transfer":      "1",
            "transfer_pair_id": pair_id,
            "reconciled":       mirror.get("reconciled", "0") if mirror else "0",
            "notes":            saved.get("notes", ""),
            "split_group_id":   "",
            # The vendor/customer applies to both sides; paid_by does not (the
            # owner paying into a company account isn't an owner-paid expense).
            "contact_id":       saved.get("contact_id", "") or "",
        })
        return saved

    def delete_transfer_pair(self, pair_id: str):
        """Delete both sides of a transfer (every row sharing the pair id)."""
        if not pair_id:
            return
        with self._conn() as c:
            c.execute("DELETE FROM transactions WHERE transfer_pair_id=?", (pair_id,))

    # ── splits ────────────────────────────────────────────────────────────────
    # A "split" lets one real-world payment (e.g. a Costco run) be divided across
    # several categories. It is stored as N sibling transactions sharing a
    # split_group_id; they sum to the original amount, so balances and reports
    # (which aggregate per transaction) keep working with no special handling.

    def get_split_group(self, group_id: str) -> list[dict]:
        if not group_id:
            return []
        with self._conn() as c:
            return _to_dicts(c.execute(
                "SELECT * FROM transactions WHERE split_group_id=? ORDER BY created_at",
                (group_id,)).fetchall())

    def split_transaction(self, base_txn: dict, splits: list[dict],
                          transfer_to: str = "") -> list[dict]:
        """
        Replace base_txn — or, if it is already part of a split, its whole group —
        with the given split lines.

        Each split line is a dict: {category_id, amount, memo (optional),
        class_id (optional), transfer (optional bool)}. Shared fields (date,
        account, payee, reconciled, notes) are inherited from base_txn. The
        original import_hash is preserved on the first line so re-importing the
        source file still de-duplicates.

        If transfer_to is given, the split is a transfer split: ALL lines are
        outflows from the source account (negative), but only the lines flagged
        "transfer" move to the destination account. Those flagged lines are
        is_transfer=1, filed under "Account Transfers", and summed into ONE mirror
        in transfer_to (positive — money arriving). Unflagged lines are ordinary
        spending in the source account (e.g. loan interest) and stay out of the
        transfer. (For backward compatibility, a line with no "transfer" key in a
        transfer split is treated as a transfer.) Returns the saved source-side
        child transactions.
        """
        gid = base_txn.get("split_group_id") or self._new_id()
        has_dest = bool(transfer_to)

        def _is_tr(s):
            return has_dest and bool(s.get("transfer", True))

        any_transfer_line = any(_is_tr(s) for s in splits)
        tcat = self.get_transfer_category_id() if any_transfer_line else ""
        pair_id = base_txn.get("transfer_pair_id") or (self._new_id() if any_transfer_line else "")
        preserved_hash = base_txn.get("import_hash", "") or ""
        with self._conn() as c:
            if base_txn.get("split_group_id"):
                for r in c.execute(
                        "SELECT import_hash FROM transactions WHERE split_group_id=?",
                        (gid,)).fetchall():
                    if r["import_hash"] and not preserved_hash:
                        preserved_hash = r["import_hash"]
                c.execute("DELETE FROM transactions WHERE split_group_id=?", (gid,))
            elif base_txn.get("id"):
                c.execute("DELETE FROM transactions WHERE id=?", (base_txn["id"],))
            # Remove any prior transfer mirror tied to this entry before rebuilding.
            old_pair = base_txn.get("transfer_pair_id", "")
            if old_pair:
                c.execute("DELETE FROM transactions WHERE transfer_pair_id=?", (old_pair,))

        saved = []
        moved_total = 0.0   # sum of the parts that move to the destination account
        for i, s in enumerate(splits):
            raw = float(s.get("amount") or 0)
            line_is_transfer = _is_tr(s)
            cat = s.get("category_id", "")
            if has_dest:
                # In a transfer split every line leaves the source account.
                amt = -abs(raw)
                if line_is_transfer:
                    moved_total += abs(raw)
                    cat = cat or tcat
            else:
                # Plain split: the category type sets the sign, just like a normal
                # transaction (expense subtracts, income adds, uncategorized keeps
                # whatever sign was entered so refunds still work).
                ctype = self._category_type(cat)
                if ctype == "income":
                    amt = abs(raw)
                elif ctype in ("expense", "debt_repayment"):
                    amt = -abs(raw)
                else:
                    amt = raw
            child = {
                "date":          base_txn.get("date", ""),
                "account_id":    base_txn.get("account_id", ""),
                "payee":         base_txn.get("payee", ""),
                "memo":          s.get("memo") or base_txn.get("memo", ""),
                "amount":        str(round(amt, 2)),
                "category_id":   cat,
                "class_id":      s.get("class_id") or base_txn.get("class_id", ""),
                "is_transfer":   "1" if line_is_transfer else "0",
                "transfer_pair_id": pair_id if line_is_transfer else "",
                "reconciled":    base_txn.get("reconciled", "0"),
                "notes":         base_txn.get("notes", ""),
                "import_hash":   preserved_hash if i == 0 else "",
                "split_group_id": gid,
            }
            # Owner/person attribution is shared by every line; a line may
            # override who it's charged to (e.g. a receipt item for a project).
            for k in _TXN_EXTRA_COLS:
                if k in s:
                    child[k] = s[k]
                elif k in base_txn:
                    child[k] = base_txn[k]
            saved.append(self.save_transaction(child))

        # One mirror in the destination for the portion that actually moved.
        if any_transfer_line and moved_total > 0.005:
            self.save_transaction({
                "date":             base_txn.get("date", ""),
                "account_id":       transfer_to,
                "payee":            base_txn.get("payee", ""),
                "memo":             base_txn.get("memo", ""),
                "amount":           str(round(moved_total, 2)),   # money arriving
                "category_id":      tcat,
                "class_id":         base_txn.get("class_id", ""),
                "is_transfer":      "1",
                "transfer_pair_id": pair_id,
                "reconciled":       "0",
                "notes":            base_txn.get("notes", ""),
                "split_group_id":   "",
            })
        return saved

    def delete_split_group(self, group_id: str):
        with self._conn() as c:
            c.execute("DELETE FROM transactions WHERE split_group_id=?", (group_id,))

    def clear_all_data(self):
        """
        Delete user data, but KEEP the category list (and the settings table:
        database name, etc.). Categories are part of the app's setup, not the
        per-import data, so they survive a reset.
        """
        self.set_lock_date("")      # a reset deliberately clears locked periods too
        with self._conn() as c:
            c.execute("DELETE FROM transactions")
            c.execute("DELETE FROM accounts")
            c.execute("DELETE FROM classes")
            c.execute("DELETE FROM reconciliations")
            c.execute("DELETE FROM rules")
            c.execute("DELETE FROM loans")
            # Receipt files and their line items are kept; only their links to
            # the deleted transactions go.
            c.execute("DELETE FROM attachments")
            c.execute("UPDATE receipts SET transaction_id='', split_group_id='', "
                      "status=CASE WHEN status IN ('booked','attached') THEN 'reviewed' "
                      "ELSE status END")
        # Business books need their built-in owner accounts back.
        if self.is_business():
            self.ensure_business_setup()

    def get_existing_hashes(self) -> set:
        with self._conn() as c:
            rows = c.execute(
                "SELECT import_hash FROM transactions WHERE import_hash!=''").fetchall()
            return {r["import_hash"] for r in rows}

    # ── categories ────────────────────────────────────────────────────────────

    def get_categories(self) -> list[dict]:
        with self._conn() as c:
            return _to_dicts(c.execute(
                "SELECT * FROM categories WHERE active=1 ORDER BY name").fetchall())

    def get_category(self, cat_id: str) -> dict | None:
        with self._conn() as c:
            return _to_dict(c.execute(
                "SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone())

    def _category_type(self, cat_id: str) -> str:
        """Return a category's type ('expense'/'income'/'debt_repayment'), or ''."""
        if not cat_id:
            return ""
        with self._conn() as c:
            row = c.execute(
                "SELECT type FROM categories WHERE id=?", (cat_id,)).fetchone()
        return (row["type"] if row else "") or ""

    def save_category(self, data: dict) -> dict:
        with self._conn() as c:
            if data.get("id") and self.get_category(data["id"]):
                c.execute("UPDATE categories SET name=?,parent_id=?,type=? WHERE id=?",
                          (data["name"], data.get("parent_id",""),
                           data.get("type","expense"), data["id"]))
            else:
                data["id"] = self._new_id()
                c.execute(
                    "INSERT INTO categories (id,name,parent_id,type,active) VALUES (?,?,?,?,1)",
                    (data["id"], data["name"], data.get("parent_id",""),
                     data.get("type","expense")))
            if "tax_line" in data:
                c.execute("UPDATE categories SET tax_line=? WHERE id=?",
                          (data.get("tax_line") or "", data["id"]))
            if "deduct_pct" in data:
                c.execute("UPDATE categories SET deduct_pct=? WHERE id=?",
                          (float(data.get("deduct_pct") or 100), data["id"]))
        return data

    def delete_category(self, cat_id: str):
        with self._conn() as c:
            c.execute("UPDATE categories SET active=0 WHERE id=?", (cat_id,))

    # ── classes ───────────────────────────────────────────────────────────────

    def get_classes(self) -> list[dict]:
        with self._conn() as c:
            return _to_dicts(c.execute(
                "SELECT * FROM classes WHERE active=1 ORDER BY name").fetchall())

    def save_class(self, data: dict) -> dict:
        with self._conn() as c:
            if data.get("id"):
                c.execute("UPDATE classes SET name=?,parent_id=? WHERE id=?",
                          (data["name"], data.get("parent_id",""), data["id"]))
            else:
                data["id"] = self._new_id()
                c.execute(
                    "INSERT INTO classes (id,name,parent_id,active) VALUES (?,?,?,1)",
                    (data["id"], data["name"], data.get("parent_id","")))
        return data

    def delete_class(self, class_id: str):
        with self._conn() as c:
            c.execute("UPDATE classes SET active=0 WHERE id=?", (class_id,))

    # ── reconciliations ───────────────────────────────────────────────────────

    def get_reconciliations(self, account_id: str = None) -> list[dict]:
        q = "SELECT * FROM reconciliations"
        p: list = []
        if account_id:
            q += " WHERE account_id=?"; p.append(account_id)
        q += " ORDER BY created_at DESC"
        with self._conn() as c:
            return _to_dicts(c.execute(q, p).fetchall())

    def get_reconciliation(self, rec_id: str) -> dict | None:
        with self._conn() as c:
            return _to_dict(c.execute(
                "SELECT * FROM reconciliations WHERE id=?", (rec_id,)).fetchone())

    def save_reconciliation(self, data: dict) -> dict:
        with self._conn() as c:
            if data.get("id") and self.get_reconciliation(data["id"]):
                c.execute(
                    "UPDATE reconciliations SET statement_date=?,statement_balance=?,"
                    "cleared_balance=?,difference=?,status=?,completed_at=? WHERE id=?",
                    (data.get("statement_date",""),
                     float(data.get("statement_balance") or 0),
                     float(data.get("cleared_balance") or 0),
                     float(data.get("difference") or 0),
                     data.get("status","open"),
                     data.get("completed_at",""), data["id"]))
            else:
                data["id"] = self._new_id()
                data["created_at"] = self._now()
                c.execute(
                    "INSERT INTO reconciliations "
                    "(id,account_id,statement_date,statement_balance,cleared_balance,"
                    "difference,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (data["id"], data.get("account_id",""),
                     data.get("statement_date",""),
                     float(data.get("statement_balance") or 0),
                     float(data.get("cleared_balance") or 0),
                     float(data.get("difference") or 0),
                     data.get("status","open"), data["created_at"]))
        return data

    def complete_reconciliation(self, rec_id: str, txn_ids: list[str]):
        with self._conn() as c:
            if txn_ids:
                ph = ",".join("?" * len(txn_ids))
                c.execute(
                    f"UPDATE transactions SET reconciled=1, reconcile_id=? WHERE id IN ({ph})",
                    [rec_id] + txn_ids)
            c.execute(
                "UPDATE reconciliations SET status='completed', completed_at=? WHERE id=?",
                (self._now(), rec_id))

    # ── balance helpers ───────────────────────────────────────────────────────

    def account_balance(self, account_id: str) -> float:
        acct = self.get_account(account_id)
        if not acct:
            return 0.0
        opening = float(acct.get("opening_balance") or 0)
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE account_id=?",
                (account_id,)).fetchone()
        return opening + (row[0] or 0.0)

    def running_balances(self, account_id: str) -> dict:
        """
        Map each transaction id -> the account's balance *after* that
        transaction. Computed over the account's entire history in
        chronological order (oldest first) starting from the opening balance,
        so every value is the true running balance at that point regardless of
        how the register happens to be filtered on screen.
        """
        acct = self.get_account(account_id)
        if not acct:
            return {}
        bal = float(acct.get("opening_balance") or 0)
        out: dict = {}
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, amount FROM transactions WHERE account_id=? "
                "ORDER BY date ASC, created_at ASC", (account_id,)).fetchall()
        for r in rows:
            bal += float(r[1] or 0)
            out[r[0]] = bal
        return out

    def cleared_balance(self, account_id: str) -> float:
        acct = self.get_account(account_id)
        if not acct:
            return 0.0
        opening = float(acct.get("opening_balance") or 0)
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(amount),0) FROM transactions "
                "WHERE account_id=? AND reconciled=1",
                (account_id,)).fetchone()
        return opening + (row[0] or 0.0)

    def get_institutions(self) -> list[str]:
        """Return sorted list of distinct institution names already on file."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT DISTINCT institution FROM accounts "
                "WHERE institution != '' ORDER BY institution COLLATE NOCASE"
            ).fetchall()
            return [r["institution"] for r in rows]

    # ── settings ──────────────────────────────────────────────────────────────

    def get_setting(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))

    # ── rules ─────────────────────────────────────────────────────────────────

    def get_rules(self) -> list[dict]:
        with self._conn() as c:
            return _to_dicts(c.execute(
                "SELECT * FROM rules WHERE active IN (0,1) ORDER BY priority DESC, name"
            ).fetchall())

    def save_rule(self, data: dict) -> dict:
        active = 1 if str(data.get("active", "1")) == "1" else 0
        priority = int(data.get("priority") or 0)
        with self._conn() as c:
            existing = c.execute("SELECT id FROM rules WHERE id=?",
                                 (data.get("id", ""),)).fetchone()
            if existing:
                c.execute(
                    "UPDATE rules SET name=?,field=?,operator=?,value=?,value2=?,"
                    "category_id=?,class_id=?,priority=?,active=? WHERE id=?",
                    (data.get("name", ""), data.get("field", "payee"),
                     data.get("operator", "contains"), data.get("value", ""),
                     data.get("value2", ""), data.get("category_id", ""),
                     data.get("class_id", ""), priority, active, data["id"]))
            else:
                data["id"] = self._new_id()
                data["created_at"] = self._now()
                c.execute(
                    "INSERT INTO rules (id,name,field,operator,value,value2,"
                    "category_id,class_id,priority,active,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (data["id"], data.get("name", ""), data.get("field", "payee"),
                     data.get("operator", "contains"), data.get("value", ""),
                     data.get("value2", ""), data.get("category_id", ""),
                     data.get("class_id", ""), priority, active, data["created_at"]))
        return data

    def delete_rule(self, rule_id: str):
        with self._conn() as c:
            c.execute("DELETE FROM rules WHERE id=?", (rule_id,))

    @staticmethod
    def _rule_matches(rule: dict, txn: dict) -> bool:
        field    = rule.get("field", "payee")
        operator = rule.get("operator", "contains")
        value    = str(rule.get("value", ""))
        value2   = str(rule.get("value2", ""))

        if field in ("payee", "memo"):
            text = (txn.get("payee") or txn.get("memo") or "").lower()
            if field == "memo":
                text = (txn.get("memo") or "").lower()
            v = value.lower()
            if operator == "contains":    return v in text
            if operator == "equals":      return text == v
            if operator == "starts_with": return text.startswith(v)
            if operator == "ends_with":   return text.endswith(v)
        elif field == "amount":
            try:
                amt = abs(float(txn.get("amount") or 0))
                v   = float(value)
                if operator == "equals":  return abs(amt - v) < 0.005
                if operator == "gt":      return amt > v
                if operator == "lt":      return amt < v
                if operator == "between":
                    v2 = float(value2)
                    return min(v, v2) <= amt <= max(v, v2)
            except (ValueError, TypeError):
                return False
        elif field == "date":
            date_str = txn.get("date", "")
            if operator == "equals":
                return date_str == value
            if operator == "day_of_month":
                try:
                    return int(date_str.split("-")[2]) == int(value)
                except (IndexError, ValueError):
                    return False
        return False

    def apply_rules(self, txns: list[dict]) -> list[dict]:
        """Apply active rules to a list of transaction dicts (modifies in-place).
        Rules win; then a matching vendor/customer fills the contact and, if the
        transaction is still uncategorized, that contact's default category."""
        self._apply_category_rules(txns)
        self.apply_contacts(txns)
        return txns

    def _apply_category_rules(self, txns: list[dict]) -> list[dict]:
        rules = [r for r in self.get_rules()
                 if str(r.get("active", "1")) == "1"]
        rules.sort(key=lambda r: -(int(r.get("priority") or 0)))
        for txn in txns:
            if txn.get("category_id"):
                continue  # don't overwrite already-categorized
            for rule in rules:
                if self._rule_matches(rule, txn):
                    if rule.get("category_id"):
                        txn["category_id"] = rule["category_id"]
                    if rule.get("class_id"):
                        txn["class_id"] = rule["class_id"]
                    break
        return txns

    def apply_rules_to_all(self) -> int:
        """Re-apply rules to all uncategorized transactions. Returns count updated."""
        txns = self.get_transactions(include_transfers=True)
        uncategorized = [t for t in txns if not t.get("category_id")]
        self.apply_rules(uncategorized)
        count = 0
        for t in uncategorized:
            if t.get("category_id"):
                self.save_transaction(t)
                count += 1
        return count

    # ── loans ─────────────────────────────────────────────────────────────────

    def get_loans(self) -> list[dict]:
        with self._conn() as c:
            return _to_dicts(c.execute(
                "SELECT * FROM loans ORDER BY name COLLATE NOCASE").fetchall())

    def get_loan(self, loan_id: str) -> dict | None:
        with self._conn() as c:
            return _to_dict(c.execute(
                "SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone())

    def save_loan(self, data: dict) -> dict:
        with self._conn() as c:
            if data.get("id") and self.get_loan(data["id"]):
                c.execute(
                    "UPDATE loans SET name=?,lender=?,original_principal=?,annual_rate=?,"
                    "term_months=?,start_date=?,payment_amount=?,account_id=?,"
                    "interest_category_id=? WHERE id=?",
                    (data.get("name",""), data.get("lender",""),
                     float(data.get("original_principal") or 0),
                     float(data.get("annual_rate") or 0),
                     int(data.get("term_months") or 0),
                     data.get("start_date",""),
                     float(data.get("payment_amount") or 0),
                     data.get("account_id",""),
                     data.get("interest_category_id",""),
                     data["id"]))
            else:
                data["id"] = self._new_id()
                data["created_at"] = self._now()
                c.execute(
                    "INSERT INTO loans "
                    "(id,name,lender,original_principal,annual_rate,term_months,"
                    "start_date,payment_amount,account_id,interest_category_id,created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (data["id"], data.get("name",""), data.get("lender",""),
                     float(data.get("original_principal") or 0),
                     float(data.get("annual_rate") or 0),
                     int(data.get("term_months") or 0),
                     data.get("start_date",""),
                     float(data.get("payment_amount") or 0),
                     data.get("account_id",""),
                     data.get("interest_category_id",""),
                     data["created_at"]))
        return data

    def delete_loan(self, loan_id: str):
        with self._conn() as c:
            c.execute("DELETE FROM loans WHERE id=?", (loan_id,))

    def get_loan_transactions(self, loan_id: str) -> list[dict]:
        with self._conn() as c:
            return _to_dicts(c.execute(
                "SELECT * FROM transactions WHERE loan_id=? ORDER BY date",
                (loan_id,)).fetchall())

    # ── book type (personal / business) ──────────────────────────────────────

    def book_type(self) -> str:
        return self.get_setting("book_type", BOOK_PERSONAL) or BOOK_PERSONAL

    def is_business(self) -> bool:
        return self.book_type() == BOOK_BUSINESS

    def set_book_type(self, book_type: str):
        """
        Switch this database's book type. Turning a database into a Business
        book is additive: it adds the Schedule C categories, owner accounts and
        the Owner person, and never removes or rewrites existing data.
        """
        if book_type not in (BOOK_PERSONAL, BOOK_BUSINESS):
            raise ValueError(book_type)
        self.set_setting("book_type", book_type)
        if book_type == BOOK_BUSINESS:
            self.ensure_business_setup()

    def ensure_business_setup(self):
        """Idempotently create everything a business book relies on."""
        self.seed_business_categories()
        for role, (name, atype) in BUSINESS_ACCOUNTS.items():
            if not self.get_account_by_role(role):
                self.save_account({"name": name, "type": atype, "role": role,
                                   "institution": "Built-in (no bank)"})
        self.owner_person_id()
        self.get_transfer_category_id()

    def seed_business_categories(self):
        """
        Add the Schedule C category tree once (skips names that already exist),
        then any categories added in later releases (BUSINESS_CATEGORY_ADDITIONS,
        tracked by business_categories_version) — so a category the user deleted
        is never re-added.
        """
        from business_categories import BUSINESS_CATEGORIES, BUSINESS_CATEGORY_ADDITIONS
        if self.get_setting("business_categories_seeded") == "1":
            have = int(self.get_setting("business_categories_version", "1") or 1)
            for ver, entries in sorted(BUSINESS_CATEGORY_ADDITIONS.items()):
                if ver > have:
                    for ctype, name, line, pct in entries:
                        if not any(c["name"] == name and not c.get("parent_id")
                                   for c in self.get_categories()):
                            self.save_category({"name": name, "type": ctype,
                                                "tax_line": line, "deduct_pct": pct})
            self.set_setting("business_categories_version",
                             str(max(BUSINESS_CATEGORY_ADDITIONS, default=1)))
            return
        existing = {(c["name"].lower(), c.get("parent_id") or "", c.get("type")): c
                    for c in self.get_categories()}

        def _ensure(name, ctype, parent_id, tax_line, pct):
            key = (name.lower(), parent_id, ctype)
            cat = existing.get(key)
            if cat:
                if not cat.get("tax_line"):
                    cat = self.save_category(dict(cat, tax_line=tax_line, deduct_pct=pct))
                return cat
            cat = self.save_category({"name": name, "type": ctype, "parent_id": parent_id,
                                      "tax_line": tax_line, "deduct_pct": pct})
            existing[key] = cat
            return cat

        for ctype, parents in BUSINESS_CATEGORIES.items():
            for name, line, pct, children in parents:
                parent = _ensure(name, ctype, "", line, pct)
                for child in children:
                    if isinstance(child, tuple):
                        cname, cline, cpct = child
                    else:
                        cname, cline, cpct = child, line, pct
                    _ensure(cname, ctype, parent["id"], cline, cpct)
        for ver, entries in sorted(BUSINESS_CATEGORY_ADDITIONS.items()):
            for ctype, name, line, pct in entries:
                _ensure(name, ctype, "", line, pct)
        self.set_setting("business_categories_seeded", "1")
        self.set_setting("default_categories_seeded", "1")
        self.set_setting("business_categories_version",
                         str(max(BUSINESS_CATEGORY_ADDITIONS, default=1)))

    # ── people ───────────────────────────────────────────────────────────────

    def get_people(self, active_only: bool = True) -> list[dict]:
        q = "SELECT * FROM people"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY CASE role WHEN 'owner' THEN 0 ELSE 1 END, name COLLATE NOCASE"
        with self._conn() as c:
            return _to_dicts(c.execute(q).fetchall())

    def get_person(self, person_id: str) -> dict | None:
        with self._conn() as c:
            return _to_dict(c.execute(
                "SELECT * FROM people WHERE id=?", (person_id,)).fetchone())

    def save_person(self, data: dict) -> dict:
        cols = ("name", "role", "email", "phone", "notes")
        active = 1 if str(data.get("active", "1")) == "1" else 0
        with self._conn() as c:
            if data.get("id") and self.get_person(data["id"]):
                c.execute(
                    f"UPDATE people SET {','.join(k + '=?' for k in cols)},active=? WHERE id=?",
                    [data.get(k) or "" for k in cols] + [active, data["id"]])
            else:
                data["id"] = self._new_id()
                data["created_at"] = self._now()
                c.execute(
                    "INSERT INTO people (id,name,role,email,phone,notes,active,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    [data["id"]] + [data.get(k) or "" for k in cols]
                    + [active, data["created_at"]])
        return data

    def owner_person_id(self) -> str:
        """The business owner's person id, creating "Owner" the first time."""
        pid = self.get_setting("owner_person_id")
        if pid and self.get_person(pid):
            return pid
        owners = [p for p in self.get_people(active_only=False) if p.get("role") == "owner"]
        person = owners[0] if owners else self.save_person({"name": "Owner", "role": "owner"})
        self.set_setting("owner_person_id", person["id"])
        return person["id"]

    def get_or_create_role_account(self, role: str, name: str, atype: str) -> dict:
        acct = self.get_account_by_role(role)
        if acct:
            if str(acct.get("active", "1")) != "1":
                self.set_account_active(acct["id"], True)
            return acct
        return self.save_account({"name": name, "type": atype, "role": role,
                                  "institution": "Built-in (no bank)"})

    # ── backups ──────────────────────────────────────────────────────────────

    def backup_now(self, kind: str = "manual") -> str:
        return _backup.backup_database(self.db_path, kind)

    def auto_backup_if_due(self) -> str:
        """
        Daily automatic backup, keeping the newest N (setting auto_backup_keep,
        default 10). Returns the new backup's path, or '' if none was due.
        Never raises — a failed backup must not stop the app from opening.
        Skipped in read-only mode: the copy's owner makes the backups.
        """
        if self.read_only:
            return ""
        try:
            if self.get_setting("auto_backup_enabled", "1") != "1":
                return ""
            last = self.get_setting("last_auto_backup")
            hours = float(self.get_setting("auto_backup_hours", "24") or 24)
            if last:
                try:
                    age = datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                    if age < timedelta(hours=hours):
                        return ""
                except ValueError:
                    pass
            path = _backup.backup_database(self.db_path, "auto")
            self.set_setting("last_auto_backup", self._now())
            keep = int(self.get_setting("auto_backup_keep", "10") or 10)
            _backup.prune_backups(self.db_path, "auto", keep)
            return path
        except Exception:
            return ""

    def reload(self):
        pass  # no-op — SQLite reads are always live

