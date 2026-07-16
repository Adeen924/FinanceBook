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
    if getattr(sys, "frozen", False):
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
            with open(SETTINGS_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(cfg: dict):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def get_active_db_path():
    return _load().get("active_db_path")


def get_known_databases() -> list:
    paths = _load().get("known_databases", [])
    return [p for p in paths if os.path.exists(p)]


def register_database(path: str, make_active: bool = False):
    """Remember a database's location so it survives app restarts and shows
    up in the sidebar's database switcher."""
    cfg = _load()
    known = cfg.get("known_databases", [])
    if path not in known:
        known.append(path)
    cfg["known_databases"] = known
    if make_active:
        cfg["active_db_path"] = path
    _save(cfg)


def get_flag(name: str, default: bool = False) -> bool:
    """Read a per-user boolean flag (stored in app_settings.json, so it survives
    app updates). Used e.g. to show the first-run guide only once, ever."""
    return bool(_load().get(name, default))


def set_flag(name: str, value: bool = True):
    cfg = _load()
    cfg[name] = bool(value)
    _save(cfg)
