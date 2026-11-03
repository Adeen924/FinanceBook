"""
Checks run before connecting FinanceBook to an existing database file — for
example one in a OneDrive folder that another laptop created.

Everything here reads the file READ-ONLY and never opens it through
Database(...), so a file that turns out to be wrong (a newer format, a sync
placeholder, a conflict copy) is never migrated, backed up or locked. Pure
Python (no Qt) so it can be unit-tested; the dialogs are in ui/connect_dialog.py.
"""
import os
import re
import sqlite3
import sys

from sheets import backup as _backup
from sheets.client import SCHEMA_VERSION
import db_lock

# Windows file attributes that mark a cloud "online-only" placeholder whose
# contents haven't been downloaded to this computer.
_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_PLACEHOLDER_ATTRS = (_FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_OPEN
                      | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)

_SQLITE_HEADER = b"SQLite format 3\x00"

# OneDrive names a conflict copy "<name>-<COMPUTERNAME>.db" (computer names are
# upper case, e.g. DESKTOP-AB12CD, optionally followed by -2, -3…); Google
# Drive uses "<name> (1).db".
_MACHINE_SUFFIX = re.compile(r"^(?=[A-Z0-9_-]*[A-Z])[A-Z0-9][A-Z0-9_-]{2,}(-\d+)?$")
_NUMBERED_SUFFIX = re.compile(r"^ \(\d+\)$")

SYNC_HINT = ("If the database is in OneDrive, wait for OneDrive to finish syncing, or "
             "right-click its folder and choose “Always keep on this device”, then try again.")


class ConnectCheck:
    """Result of validate_existing_database()."""
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.error = ""          # set → the file must not be connected
        self.warnings: list[str] = []
        self.version = 0
        self.needs_upgrade = False

    @property
    def ok(self) -> bool:
        return not self.error


def onedrive_dir() -> str:
    """The user's OneDrive folder if one is set up, else their home folder."""
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        path = os.environ.get(var)
        if path and os.path.isdir(path):
            return path
    return os.path.expanduser("~")


def is_cloud_placeholder(path: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        attrs = os.stat(path).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attrs & _PLACEHOLDER_ATTRS)


def _looks_like_copy_suffix(suffix: str) -> bool:
    return bool(_NUMBERED_SUFFIX.match(suffix)) or (
        suffix.startswith("-") and bool(_MACHINE_SUFFIX.match(suffix[1:])))


def conflict_original(path: str) -> str:
    """If path looks like a sync conflict copy of a file next to it, that file."""
    folder, name = os.path.split(os.path.abspath(path))
    stem, ext = os.path.splitext(name)
    for i, ch in enumerate(stem):
        if ch not in "- " or i == 0:
            continue
        if _looks_like_copy_suffix(stem[i:]):
            original = os.path.join(folder, stem[:i] + ext)
            if os.path.isfile(original):
                return original
    return ""


def conflict_copies(path: str) -> list[str]:
    """Sync conflict copies of path sitting next to it (e.g. finances-LAPTOP-2.db)."""
    folder, name = os.path.split(os.path.abspath(path))
    stem, ext = os.path.splitext(name)
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for other in names:
        o_stem, o_ext = os.path.splitext(other)
        if (o_ext.lower() == ext.lower() and o_stem != stem and o_stem.startswith(stem)
                and _looks_like_copy_suffix(o_stem[len(stem):])):
            out.append(os.path.join(folder, other))
    return sorted(out)


def leftover_wal(path: str) -> bool:
    """A non-empty -wal file next to the database: changes a previous session
    (an older FinanceBook, or one that crashed) never folded into the .db."""
    try:
        return os.path.getsize(path + "-wal") > 0
    except OSError:
        return False


def validate_existing_database(path: str) -> ConnectCheck:
    """Decide whether path can be connected, without modifying it in any way."""
    chk = ConnectCheck(path)
    p = chk.path
    if not os.path.exists(p):
        chk.error = f"Can't find this file:\n{p}"
        return chk
    if not os.path.isfile(p):
        chk.error = "That isn't a database file."
        return chk
    if is_cloud_placeholder(p):
        chk.error = ("This file is “online-only” — its contents haven't been downloaded to "
                     "this computer yet.\n\n" + SYNC_HINT)
        return chk
    try:
        size = os.path.getsize(p)
        with open(p, "rb") as f:
            header = f.read(len(_SQLITE_HEADER))
    except OSError as e:
        chk.error = f"Couldn't read the file: {e.strerror or e}"
        return chk
    if size == 0:
        chk.error = ("This file is empty (0 bytes). It may still be downloading, or "
                     "the sync hasn't finished.\n\n" + SYNC_HINT)
        return chk
    if header != _SQLITE_HEADER:
        chk.error = "That file is not a FinanceBook database."
        return chk
    try:
        _backup._check_is_financebook_db(p)
        chk.version = _backup.read_user_version(p)
    except ValueError as e:
        chk.error = str(e)
        return chk
    except sqlite3.Error as e:
        chk.error = f"That file couldn't be read as a database ({e})."
        return chk
    if chk.version > SCHEMA_VERSION:
        chk.error = ("This database was updated by a newer version of FinanceBook — "
                     "update this computer's app first (FinanceBook offers the update "
                     "when it starts while online), then connect it.")
        return chk
    chk.needs_upgrade = chk.version < SCHEMA_VERSION

    original = conflict_original(p)
    if original:
        chk.warnings.append(
            f"This looks like a sync conflict copy of “{os.path.basename(original)}”, "
            "which is in the same folder. You usually want to connect the original — "
            "the copy only holds changes made on one computer while the two were out "
            "of sync.")
    copies = conflict_copies(p)
    if copies:
        listed = "\n".join("  • " + os.path.basename(c) for c in copies[:5])
        chk.warnings.append(
            "OneDrive has made conflict copies of this database:\n" + listed + "\n"
            "They may contain changes that aren't in this file. FinanceBook connects "
            "only the file you picked; keep the copies until you've checked them.")
    if leftover_wal(p):
        lock = db_lock.read_lock(p) or {}
        other = lock.get("machine") if lock.get("machine") != db_lock.machine_name() else ""
        where = f" by {other}" if other else ""
        chk.warnings.append(
            f"A leftover “-wal” change file was found next to this database (left{where} "
            "when FinanceBook didn't close normally, or by an older version). Opening the "
            "database here will merge it in. If the database was last used on another "
            "computer, make sure that computer has finished syncing first — otherwise "
            "this file and its changes may not match.")
    if os.path.exists(p + "-journal"):
        chk.warnings.append(
            "An unfinished save (“-journal” file) was found next to this database. "
            "SQLite will roll it back when the database opens, so the half-finished "
            "change is discarded and the rest of the data is kept.")
    return chk
