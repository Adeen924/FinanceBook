"""
platform_paths.py — the few things that differ between Windows and macOS.

Every check reads sys.platform when it is called (not at import), so tests can
mock it. Windows behaviour is exactly what FinanceBook did before this module
existed; macOS gets the native equivalent. Pure Python, no Qt.

    app_data_dir()          per-user settings folder (Windows %APPDATA%\\FinanceBook,
                            macOS ~/Library/Application Support/FinanceBook)
    cloud_folders()         synced folders found on this computer (OneDrive,
                            Google Drive, iCloud Drive) as (label, path)
    default_cloud_dir()     where "Connect existing database" starts browsing
    machine_name()          this computer's name as people know it
    ui_font_families()      preferred UI fonts, best first
    css_font_stack()        the same as a Qt style sheet font-family value
    base_font_point_size()  app font size (Mac points are smaller than Windows')
    shortcut_label(key)     "Ctrl+F" on Windows, "⌘F" on a Mac
    file_manager_name()     "File Explorer" / "Finder"
"""
import glob
import os
import sys

APP_NAME = "FinanceBook"


def is_windows() -> bool:
    return sys.platform == "win32"


def is_mac() -> bool:
    return sys.platform == "darwin"


# ── settings / data folder ───────────────────────────────────────────────────

def app_data_dir(create: bool = True) -> str:
    """FinanceBook's per-user settings folder (also the default spot offered
    for a new database)."""
    if is_mac():
        path = os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_NAME)
    else:
        # Windows (and anything else): unchanged from earlier versions.
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        path = os.path.join(appdata, APP_NAME)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


# ── cloud folders ────────────────────────────────────────────────────────────

def _mac_cloud_folders(home: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    storage = os.path.join(home, "Library", "CloudStorage")
    for pattern, label in (("OneDrive*", "OneDrive"), ("GoogleDrive-*", "Google Drive"),
                           ("Dropbox*", "Dropbox")):
        for path in sorted(glob.glob(os.path.join(storage, pattern))):
            if os.path.isdir(path):
                out.append((label, path))
    icloud = os.path.join(home, "Library", "Mobile Documents", "com~apple~CloudDocs")
    if os.path.isdir(icloud):
        out.append(("iCloud Drive", icloud))
    legacy = os.path.join(home, "OneDrive")        # older OneDrive for Mac installs
    if os.path.isdir(legacy) and not any(p == legacy for _, p in out):
        out.append(("OneDrive", legacy))
    return out


def cloud_folders() -> list[tuple[str, str]]:
    """Synced folders on this computer, OneDrive first: [(label, path)]."""
    out = []
    # Windows sets these; on a Mac they're only there if someone set them.
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        path = os.environ.get(var)
        if path and os.path.isdir(path) and not any(p == path for _, p in out):
            out.append(("OneDrive", path))
    if is_mac():
        out += [f for f in _mac_cloud_folders(os.path.expanduser("~")) if f not in out]
    return out


def default_cloud_dir() -> str:
    """The user's OneDrive folder if there is one, else another synced folder
    (Mac), else their home folder."""
    folders = cloud_folders()
    for label, path in folders:
        if label == "OneDrive":
            return path
    if folders and is_mac():
        return folders[0][1]
    return os.path.expanduser("~")


# ── computer name ────────────────────────────────────────────────────────────

def _mac_computer_name() -> str:
    """The name shown in System Settings → General → Sharing (what OneDrive
    also uses in conflict-copy names), e.g. "Adeen's MacBook Pro"."""
    import subprocess
    try:
        out = subprocess.run(["/usr/sbin/scutil", "--get", "ComputerName"],
                             capture_output=True, text=True, timeout=3)
        name = out.stdout.strip()
        if out.returncode == 0 and name:
            return name
    except Exception:
        pass
    return ""


def machine_name() -> str:
    # FINANCEBOOK_MACHINE_NAME is a test-only override for simulating two laptops.
    name = os.environ.get("FINANCEBOOK_MACHINE_NAME")
    if name:
        return name
    if is_mac():
        name = _mac_computer_name()
    else:
        name = os.environ.get("COMPUTERNAME", "")
    if not name:
        try:
            import socket
            name = socket.gethostname()
        except Exception:
            name = "unknown"
        if is_mac() and name.endswith(".local"):
            name = name[:-len(".local")]
    return name


# ── fonts ────────────────────────────────────────────────────────────────────

_WIN_FONTS = ["Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI"]
_WIN_DISPLAY = ["Segoe UI Variable Display", "Segoe UI Variable", "Segoe UI"]
_MAC_FONTS = [".AppleSystemUIFont", "Helvetica Neue"]


def ui_font_families(display: bool = False) -> list[str]:
    if is_mac():
        return list(_MAC_FONTS)
    return list(_WIN_DISPLAY if display else _WIN_FONTS)


def css_font_stack(display: bool = False) -> str:
    return ", ".join(f'"{f}"' for f in ui_font_families(display)) + ", sans-serif"


def base_font_point_size() -> int:
    """Qt points are 1/72" on a Mac but 1/96" on Windows, so the same look
    needs 13pt there (the macOS standard) for Windows' 10pt."""
    return 13 if is_mac() else 10


def html_font_family() -> str:
    """font-family for HTML documents (invoices) rendered by Qt."""
    return "'Helvetica Neue',Helvetica,Arial" if is_mac() else "'Segoe UI',Arial"


# ── shortcuts / wording ──────────────────────────────────────────────────────

_MAC_KEYS = {"ctrl": "⌘", "shift": "⇧", "alt": "⌥", "meta": "⌃"}


def shortcut_label(keys: str) -> str:
    """'Ctrl+F' → '⌘F' on a Mac (Qt maps Ctrl to ⌘ there); unchanged elsewhere."""
    if not is_mac():
        return keys
    parts = keys.split("+")
    out = ""
    for p in parts[:-1]:
        out += _MAC_KEYS.get(p.strip().lower(), p.strip())
    return out + parts[-1].strip()


def file_manager_name() -> str:
    return "Finder" if is_mac() else "File Explorer"
