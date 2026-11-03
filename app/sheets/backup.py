"""
Database backups — pure sqlite3/os, no Qt.

Backups live in a "FinanceBook Backups" folder next to the database file, so a
database kept in OneDrive / Google Drive has its backups synced with it. Copies
are made with SQLite's online-backup API, which is consistent even while the
database is being written (a plain file copy could catch it mid-transaction).
"""
import os
import re
import sqlite3
import urllib.parse
from datetime import datetime

BACKUP_DIRNAME = "FinanceBook Backups"


def ro_uri(path: str) -> str:
    """
    A read-only SQLite URI for path. The path is percent-encoded so folder
    names containing spaces, '#', '%' or non-ASCII characters open correctly;
    a UNC network path (//server/share/…) keeps its leading slashes.
    """
    p = os.path.abspath(path).replace("\\", "/")
    quoted = urllib.parse.quote(p, safe="/:")
    return ("file://" + quoted if p.startswith("//") else "file:" + quoted) + "?mode=ro"


def read_user_version(path: str) -> int:
    """PRAGMA user_version of a database file, read without modifying it."""
    conn = sqlite3.connect(ro_uri(path), uri=True)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def backup_dir(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), BACKUP_DIRNAME)


def _stem(db_path: str) -> str:
    return os.path.splitext(os.path.basename(db_path))[0]


def backup_database(db_path: str, kind: str = "manual", dest: str = "") -> str:
    """
    Copy db_path to a timestamped backup and return its path.
    kind: 'auto', 'manual', 'pre-migration-v3', 'pre-restore', …
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    if not dest:
        folder = backup_dir(db_path)
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        kind = re.sub(r"[^\w.-]+", "-", kind) or "backup"
        dest = os.path.join(folder, f"{_stem(db_path)}-{kind}-{stamp}.db")
        i = 2
        base = dest[:-3]
        while os.path.exists(dest):
            dest = f"{base}-{i}.db"
            i += 1
    src = sqlite3.connect(db_path)
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    return dest


def list_backups(db_path: str, kind: str | None = None) -> list[str]:
    """Backups of this database, newest first (optionally only one kind)."""
    folder = backup_dir(db_path)
    if not os.path.isdir(folder):
        return []
    # The kind list is fixed so a database named "shop" never claims the
    # backups of one named "shop-east".
    pat = re.compile(r"^" + re.escape(_stem(db_path))
                     + r"-(auto|manual|pre-restore|pre-migration-v\d+)"
                     + r"-\d{8}-\d{6}(-\d+)?\.db$")
    out = []
    for name in os.listdir(folder):
        m = pat.match(name)
        if not m or (kind is not None and m.group(1) != kind):
            continue
        out.append(os.path.join(folder, name))
    out.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return out


def prune_backups(db_path: str, kind: str, keep: int) -> int:
    """Delete all but the newest `keep` backups of one kind. Returns count removed."""
    removed = 0
    for path in list_backups(db_path, kind)[max(keep, 1):]:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def restore_database(backup_path: str, db_path: str) -> str:
    """
    Replace db_path's contents with backup_path. The current database is backed
    up first (kind 'pre-restore'); that path is returned so the UI can mention it.
    """
    if not os.path.exists(backup_path):
        raise FileNotFoundError(backup_path)
    _check_is_financebook_db(backup_path)
    from sheets.client import SCHEMA_VERSION    # here, not at the top: client imports this module
    if read_user_version(backup_path) > SCHEMA_VERSION:
        raise ValueError("That backup was made by a newer version of FinanceBook. "
                         "Update FinanceBook on this computer first, then restore it.")
    safety = backup_database(db_path, "pre-restore") if os.path.exists(db_path) else ""
    src = sqlite3.connect(backup_path)
    try:
        out = sqlite3.connect(db_path)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    return safety


def _check_is_financebook_db(path: str):
    conn = sqlite3.connect(ro_uri(path), uri=True)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    if not {"transactions", "accounts", "settings"} <= names:
        raise ValueError("That file is not a FinanceBook database.")


def read_setting_readonly(db_path: str, key: str, default: str = "") -> str:
    """Read one settings value without migrating or locking the database."""
    try:
        conn = sqlite3.connect(ro_uri(db_path), uri=True)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        finally:
            conn.close()
        return row[0] if row and row[0] is not None else default
    except Exception:
        return default
