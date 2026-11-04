"""
The watched receipts folder ("inbox"): what's in it, what's already imported
and what still needs parsing or review — plus the batch import/parse used by
the Receipts page. Qt-free, so it is unit-testable; the page runs it on worker
threads.

The folder is READ-ONLY here: files are listed, stat'ed and (only when needed)
read to hash them or to copy them in through Database.add_receipt_file, which
keeps the SHA-256 de-duplication. Nothing in it is moved, renamed or changed.

Scans are cheap: a folder file matches an imported receipt by name + size
(the size of FinanceBook's own copy); a file is hashed only when another
receipt has the same size, so a renamed duplicate is still recognised. Hashes
are cached per (path, size, mtime). Online-only cloud placeholders (OneDrive /
Google Drive "Files On-Demand") are never opened during a scan — reading one
would download it.
"""
import os
import stat as _stat
from dataclasses import dataclass, field

from parsers import receipt as R

# Windows file attributes that mark a cloud placeholder whose data isn't local.
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_ONLINE_ONLY = (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN |
                FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
_HIDDEN = getattr(_stat, "FILE_ATTRIBUTE_HIDDEN", 0x2) | getattr(_stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)

_IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db", "icon\r"}
_IGNORED_SUFFIXES = (".tmp", ".temp", ".crdownload", ".part", ".partial", ".download",
                     ".opdownload", ".!ut", ".lnk", ".url", ".ini")
# Folders FinanceBook writes next to a database; never treat them as an inbox.
_OWN_DIRS = {"financebook receipts", "financebook backups"}

# Receipt states, in the order the page lists them.
NEW, NEEDS_PARSING, NEEDS_REVIEW, DRAFT, SAVED = (
    "new", "needs_parsing", "needs_review", "draft", "saved")
STATE_LABELS = {NEW: "New", NEEDS_PARSING: "Needs parsing", NEEDS_REVIEW: "Needs review",
                DRAFT: "Draft", SAVED: "Saved"}


class ScanError(Exception):
    """The folder can't be listed (message is user-presentable)."""


@dataclass
class InboxFile:
    path: str
    rel: str
    size: int
    mtime: float
    online_only: bool = False
    state: str = NEW
    receipt_id: str = ""
    renamed_duplicate: bool = False     # same content as a receipt filed under another name


@dataclass
class ScanResult:
    folder: str
    files: list = field(default_factory=list)          # supported InboxFile
    unsupported: list = field(default_factory=list)    # rel paths
    ignored: int = 0
    unreadable_dirs: int = 0
    hashed: int = 0
    error: str = ""
    cancelled: bool = False

    def count(self, state: str) -> int:
        return sum(1 for f in self.files if f.state == state)

    @property
    def imported(self) -> int:
        return sum(1 for f in self.files if f.state != NEW)

    @property
    def new_files(self) -> list:
        return [f for f in self.files if f.state == NEW]

    @property
    def online_only_new(self) -> int:
        return sum(1 for f in self.files if f.state == NEW and f.online_only)

    def signature(self) -> tuple:
        """Changes when the counts change (used to re-show a dismissed banner)."""
        return (len(self.files), self.count(NEW), self.count(NEEDS_PARSING),
                self.count(NEEDS_REVIEW))


def receipt_state(rc: dict, has_items: bool = False) -> str:
    """Where an imported receipt stands: needs parsing / needs review / draft / saved."""
    status = rc.get("status") or "new"
    if status == "booked":
        return SAVED
    if status == "reviewed":
        return DRAFT
    parsed = (rc.get("parse_method") or "") not in ("", "manual") or rc.get("vendor") \
        or float(rc.get("total") or 0)
    if rc.get("file_path") and not has_items and not parsed:
        return NEEDS_PARSING
    return NEEDS_REVIEW


def _ignored(name: str) -> bool:
    low = name.lower()
    return (low in _IGNORED_NAMES or low.startswith(("~$", ".~", "~")) or
            low.startswith(".") or low.endswith(_IGNORED_SUFFIXES))


def _is_online_only(st) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _ONLINE_ONLY)


def _is_hidden(st) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _HIDDEN)


def _friendly(e: OSError, folder: str) -> str:
    if isinstance(e, PermissionError):
        return "FinanceBook isn't allowed to open the receipts folder (permission denied)."
    if isinstance(e, NotADirectoryError):
        return "The receipts folder setting points to a file, not a folder."
    drive = os.path.splitdrive(folder)[0]
    if drive and not os.path.exists(drive + os.sep):
        return (f"The receipts folder is on {drive}, which isn't connected right now. "
                "Reconnect it and press Rescan.")
    if isinstance(e, FileNotFoundError):
        return ("The receipts folder wasn't found — it may have been moved or renamed. "
                "Choose it again.")
    return ("The receipts folder couldn't be read right now (it may be offline or on a "
            f"disconnected drive). [{type(e).__name__}]")


def list_folder(folder: str, subfolders: bool = False, should_cancel=None) -> ScanResult:
    """List the folder (stat only — no file is opened)."""
    res = ScanResult(folder=folder)
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        try:
            os.scandir(folder).close()
        except OSError as e:
            res.error = _friendly(e, folder)
            return res
        res.error = _friendly(NotADirectoryError(), folder)
        return res
    stack = [folder]
    first = True
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError as e:
            if first:
                res.error = _friendly(e, folder)
                return res
            res.unreadable_dirs += 1
            continue
        first = False
        with it:
            for entry in it:
                if should_cancel and should_cancel():
                    res.cancelled = True
                    return res
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if subfolders and not _ignored(entry.name) \
                                and entry.name.lower() not in _OWN_DIRS:
                            stack.append(entry.path)
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    res.ignored += 1
                    continue
                if _ignored(entry.name) or _is_hidden(st) or not entry.is_file():
                    res.ignored += 1
                    continue
                rel = os.path.relpath(entry.path, folder)
                if R._ext(entry.name) not in R.SUPPORTED_EXTS:
                    res.unsupported.append(rel)
                    continue
                res.files.append(InboxFile(path=entry.path, rel=rel, size=st.st_size,
                                           mtime=st.st_mtime, online_only=_is_online_only(st)))
    res.files.sort(key=lambda f: f.rel.lower())
    res.unsupported.sort(key=str.lower)
    return res


_HASH_CACHE: dict = {}


def _cached_hash(f: InboxFile, hasher) -> str:
    key = (os.path.normcase(f.path), f.size, f.mtime)
    if key not in _HASH_CACHE:
        _HASH_CACHE[key] = hasher(f.path)
    return _HASH_CACHE[key]


def scan_folder(folder: str, receipts: list[dict], copy_size, items_ids=(),
                subfolders: bool = False, hasher=None, should_cancel=None) -> ScanResult:
    """
    List the folder and classify each supported file against the receipts on
    file. copy_size(receipt) → size of FinanceBook's copy or None if missing.
    items_ids: receipt ids that have saved line items.
    """
    if hasher is None:
        from sheets.receipts_store import file_sha256 as hasher
    res = list_folder(folder, subfolders, should_cancel)
    if res.error or res.cancelled:
        return res
    with_items = set(items_ids)
    by_name_size: dict = {}
    by_size: dict = {}
    by_sha: dict = {}
    for rc in receipts:
        if not rc.get("file_path"):
            continue
        size = copy_size(rc)
        name = (rc.get("original_name") or "").lower()
        if size is not None:
            by_name_size.setdefault((name, size), rc)
            by_size.setdefault(size, []).append(rc)
        else:
            by_size.setdefault(("?", name), []).append(rc)    # copy missing: match by name
        if rc.get("sha256"):
            by_sha.setdefault(rc["sha256"], rc)
    for f in res.files:
        if should_cancel and should_cancel():
            res.cancelled = True
            return res
        name = os.path.basename(f.path).lower()
        rc = by_name_size.get((name, f.size))
        if rc is None and not f.online_only and (by_size.get(f.size) or by_size.get(("?", name))):
            try:
                sha = _cached_hash(f, hasher)
                res.hashed += 1
            except OSError:
                sha = ""
            rc = by_sha.get(sha) if sha else None
            if rc is not None and (rc.get("original_name") or "").lower() != name:
                f.renamed_duplicate = True
        if rc is not None:
            f.receipt_id = rc["id"]
            f.state = receipt_state(rc, rc["id"] in with_items)
    return res


def scan_for_db(db, folder: str, subfolders: bool = False, should_cancel=None) -> ScanResult:
    """scan_folder against a Database (receipts read once, up front)."""
    receipts = db.get_receipts()

    def copy_size(rc):
        try:
            return os.stat(db.receipt_abs_path(rc)).st_size
        except OSError:
            return None
    return scan_folder(folder, receipts, copy_size, db.receipt_ids_with_items(),
                       subfolders=subfolders, should_cancel=should_cancel)


# ── batch import + parse ─────────────────────────────────────────────────────

def receipt_fields(parsed: dict) -> dict:
    """The receipts-table columns filled from a parse result."""
    out = {"vendor": parsed["vendor"], "receipt_date": parsed["date"],
           "parse_method": parsed["method"], "raw_text": parsed["raw_text"]}
    for k in R.MONEY_FIELDS:
        out[k] = parsed.get(k) or 0
    if parsed.get("order_number"):
        out["notes"] = f"Order # {parsed['order_number']}"
    return out


def needs_ai(parsed: dict) -> bool:
    """Offline reading found nothing — only Read with AI (or typing) will help."""
    return parsed.get("method") == "manual" and not parsed.get("raw_text") and any(
        w in (R.MSG_IMAGE, R.MSG_NO_TEXT_PDF) for w in parsed.get("warnings") or [])


@dataclass
class BatchSummary:
    total: int = 0
    done: int = 0
    imported: list = field(default_factory=list)       # receipt ids added and parsed
    reparsed: list = field(default_factory=list)       # receipt ids parsed again
    duplicates: list = field(default_factory=list)     # file names already on file
    failed: list = field(default_factory=list)         # (name, reason)
    need_ai: list = field(default_factory=list)        # receipt ids
    parsed: dict = field(default_factory=dict)         # receipt id → parse result
    cancelled: bool = False

    def text(self) -> str:
        parts = [f"Imported {len(self.imported)}"]
        if self.reparsed:
            parts.append(f"parsed again {len(self.reparsed)}")
        parts.append(f"already on file {len(self.duplicates)}")
        parts.append(f"couldn't read {len(self.failed)}")
        msg = " · ".join(parts)
        if self.need_ai:
            msg += f" · {len(self.need_ai)} need Read with AI"
        if self.cancelled:
            msg += f"  (stopped after {self.done} of {self.total})"
        return msg


def import_and_parse(db, paths=(), receipt_ids=(), progress=None, should_cancel=None,
                     parse=None) -> BatchSummary:
    """
    Import each new file (copy + SHA-256 de-dup via add_receipt_file) and read
    it with the OFFLINE parsers only; receipt_ids are already-imported receipts
    to parse again. One file's failure never stops the batch; should_cancel is
    checked between files, so a cancel stops cleanly after the current one.
    progress(done, total, name) is called before each file.
    """
    parse = parse or (lambda p: R.parse_file(p, use_ai=False))
    jobs = [("file", p) for p in paths] + [("receipt", rid) for rid in receipt_ids]
    out = BatchSummary(total=len(jobs))
    for n, (kind, ref) in enumerate(jobs):
        if should_cancel and should_cancel():
            out.cancelled = True
            break
        name = os.path.basename(ref) if kind == "file" else \
            ((db.get_receipt(ref) or {}).get("original_name") or ref)
        if progress:
            progress(n, len(jobs), name)
        try:
            if kind == "file":
                rc, dup = db.add_receipt_file(ref)
                if dup:
                    out.duplicates.append(name)
                    out.done += 1
                    continue
            else:
                rc = db.get_receipt(ref)
                if not rc:
                    raise OSError("receipt no longer exists")
            try:
                parsed = parse(db.receipt_abs_path(rc))
            except Exception as e:
                # Left imported but unparsed, so it shows as "needs parsing".
                out.failed.append((name, f"couldn't be read ({type(e).__name__})"))
                out.done += 1
                continue
            db.save_receipt(dict(receipt_fields(parsed), id=rc["id"]))
            out.parsed[rc["id"]] = parsed
            (out.imported if kind == "file" else out.reparsed).append(rc["id"])
            if needs_ai(parsed):
                out.need_ai.append(rc["id"])
            elif parsed.get("method") == "manual" and parsed.get("warnings"):
                out.failed.append((name, parsed["warnings"][0]))
        except OSError as e:
            out.failed.append((name, e.strerror or str(e) or type(e).__name__))
        except Exception as e:      # never let one file stop the batch
            out.failed.append((name, f"{type(e).__name__}"))
        out.done += 1
    if progress:
        progress(out.done, len(jobs), "")
    return out


def read_with_ai(db, receipt_ids, api_key: str, model: str, progress=None,
                 should_cancel=None, reader=None) -> BatchSummary:
    """
    Send the given receipts to the AI reader one by one. Only ever called after
    the user explicitly confirmed sending these files (see the Receipts page).
    """
    reader = reader or R.parse_with_ai
    out = BatchSummary(total=len(receipt_ids))
    for n, rid in enumerate(receipt_ids):
        if should_cancel and should_cancel():
            out.cancelled = True
            break
        rc = db.get_receipt(rid) or {}
        name = rc.get("original_name") or rid
        if progress:
            progress(n, len(receipt_ids), name)
        try:
            parsed = reader(db.receipt_abs_path(rc), api_key, model)
            db.save_receipt(dict(receipt_fields(parsed), id=rid))
            out.parsed[rid] = parsed
            out.reparsed.append(rid)
        except R.AIReceiptError as e:
            out.failed.append((name, str(e)))
        except Exception as e:
            out.failed.append((name, type(e).__name__))
        out.done += 1
    if progress:
        progress(out.done, len(receipt_ids), "")
    return out
