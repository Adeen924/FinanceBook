"""Tracks where the active database lives and which databases are known.

The database file itself can now live anywhere the user chooses — a Google
Drive or OneDrive folder, a network share, etc. — so this small pointer file,
kept in FinanceBook's own settings folder, is what lets the app find it again
on the next launch and lets the sidebar list databases that aren't all in one
folder.
"""
import json
import os
import sys


def _settings_dir() -> str:
    # Test-only override, so tests can simulate two computers with separate
    # settings. Unset in normal use.
    override = os.environ.get("FINANCEBOOK_SETTINGS_DIR")
    if override:
        path = override
    elif getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        path = os.path.join(appdata, "FinanceBook")
    else:
        path = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(path, exist_ok=True)
    return path


SETTINGS_DIR    = _settings_dir()
SETTINGS_PATH   = os.path.join(SETTINGS_DIR, "app_settings.json")
DEFAULT_DB_PATH = os.path.join(SETTINGS_DIR, "finances.db")


def _load() -> dict:
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(cfg: dict):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get_active_db_path():
    return _load().get("active_db_path")


def get_known_databases() -> list:
    paths = _load().get("known_databases", [])
    return [p for p in paths if os.path.exists(p)]


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def register_database(path: str, make_active: bool = False):
    """Remember a database's location so it survives app restarts and shows
    up in the sidebar's database switcher."""
    path = os.path.abspath(path)
    cfg = _load()
    known = cfg.get("known_databases", [])
    existing = next((p for p in known if _same_path(p, path)), None)
    if existing is None:
        known.append(path)
    else:
        path = existing   # keep the spelling already stored — no near-duplicates
    cfg["known_databases"] = known
    if make_active:
        cfg["active_db_path"] = path
    _save(cfg)


def is_known_database(path: str) -> bool:
    return any(_same_path(p, path) for p in _load().get("known_databases", []))


def forget_database(path: str) -> str | None:
    """
    Remove a database from FinanceBook's list (the file itself is untouched).
    If it was the active one, another known database becomes active. Returns
    the active path afterwards (None if no databases are left).
    """
    cfg = _load()
    known = [p for p in cfg.get("known_databases", []) if not _same_path(p, path)]
    cfg["known_databases"] = known
    active = cfg.get("active_db_path")
    if active and _same_path(active, path):
        remaining = [p for p in known if os.path.exists(p)]
        cfg["active_db_path"] = remaining[0] if remaining else None
    _save(cfg)
    return cfg.get("active_db_path")


def get_flag(name: str, default: bool = False) -> bool:
    """Read a per-user boolean flag (stored in app_settings.json, so it survives
    app updates). Used e.g. to show the first-run guide only once, ever."""
    return bool(_load().get(name, default))


def set_flag(name: str, value: bool = True):
    cfg = _load()
    cfg[name] = bool(value)
    _save(cfg)


def current_user_name() -> str:
    """Name recorded in the audit trail: the one set in Settings, else the Windows user."""
    return get_value("user_name") or os.environ.get("USERNAME") or os.environ.get("USER") or "user"


def get_value(name: str, default: str = "") -> str:
    """Per-user (per-machine) string setting — not synced with any database."""
    v = _load().get(name, default)
    return v if isinstance(v, str) else default


def set_value(name: str, value: str):
    cfg = _load()
    cfg[name] = value
    _save(cfg)


# ── watched receipts folder (per computer, per database) ─────────────────────
# Kept here, not in the database: each computer has its own path to the folder.

def _db_key(db_path: str) -> str:
    return os.path.normcase(os.path.abspath(db_path))


def get_receipt_folder(db_path: str) -> dict:
    """{"path": str, "subfolders": bool} for this database on this computer ('' = none)."""
    entry = _load().get("receipt_folders", {}).get(_db_key(db_path)) or {}
    return {"path": entry.get("path", "") if isinstance(entry.get("path"), str) else "",
            "subfolders": bool(entry.get("subfolders", False))}


def set_receipt_folder(db_path: str, folder: str, subfolders: bool = False):
    cfg = _load()
    folders = cfg.get("receipt_folders") or {}
    folders[_db_key(db_path)] = {"path": os.path.abspath(folder), "subfolders": bool(subfolders)}
    cfg["receipt_folders"] = folders
    cfg["last_receipt_folder_dir"] = os.path.abspath(folder)
    _save(cfg)


def clear_receipt_folder(db_path: str):
    cfg = _load()
    folders = cfg.get("receipt_folders") or {}
    if folders.pop(_db_key(db_path), None) is not None:
        cfg["receipt_folders"] = folders
        _save(cfg)


# ── AI receipt reading (opt-in) ──────────────────────────────────────────────

def ai_settings() -> dict:
    from utils.secret_store import unprotect
    return {"enabled": get_flag("ai_receipts_enabled"),
            "api_key": unprotect(get_value("ai_receipts_key")),
            "model": get_value("ai_receipts_model") or "claude-opus-5-5",
            "notice_ack": get_flag("ai_receipts_notice_ack")}


def set_ai_api_key(key: str):
    from utils.secret_store import protect
    set_value("ai_receipts_key", protect(key.strip()))
