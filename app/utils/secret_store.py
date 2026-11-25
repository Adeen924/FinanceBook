"""
Local secret storage for the optional AI receipt reader's API key.

The key lives in FinanceBook's per-user app_settings.json (never in the .db,
which may sync to a shared cloud folder). On Windows it is encrypted with DPAPI
(CryptProtectData), so it can only be decrypted by the same Windows user on the
same machine. On macOS the secret itself goes into the login Keychain (through
/usr/bin/security, secret passed on stdin, never on a command line) and the
settings file keeps only a "keychain:<name>" pointer. Elsewhere, or if the
Keychain can't be used, it falls back to base64 (obfuscation only). The key is
never logged or included in error messages.
"""
import base64
import subprocess
import sys

_PREFIX_DPAPI = "dpapi:"
_PREFIX_B64 = "b64:"
_PREFIX_KEYCHAIN = "keychain:"

_SECURITY = "/usr/bin/security"
_KEYCHAIN_SERVICE = "FinanceBook"


def _dpapi(data: bytes, encrypt: bool) -> bytes | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = BLOB()
        crypt32 = ctypes.windll.crypt32
        fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
        ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x01,  # UI_FORBIDDEN
                ctypes.byref(blob_out))
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def _sec_quote(s: str) -> str:
    """Quote a value for `security -i` (it splits its input like a shell)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _keychain_set(name: str, secret: str) -> bool:
    if sys.platform != "darwin":
        return False
    if any(c in secret for c in "\r\n\0"):
        return False
    # `security -i` reads the command from stdin, so the secret never appears
    # in a process listing. -U updates an existing item.
    cmd = ("add-generic-password -U -s " + _sec_quote(_KEYCHAIN_SERVICE)
           + " -a " + _sec_quote(name) + " -w " + _sec_quote(secret) + "\n")
    try:
        proc = subprocess.run([_SECURITY, "-i"], input=cmd.encode("utf-8"),
                              capture_output=True, timeout=15)
    except Exception:
        return False
    return proc.returncode == 0 and _keychain_get(name) == secret


def _keychain_get(name: str) -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(
            [_SECURITY, "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-a", name, "-w"],
            capture_output=True, timeout=15)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8").rstrip("\n")


def keychain_delete(name: str) -> None:
    if sys.platform != "darwin":
        return
    try:
        subprocess.run([_SECURITY, "delete-generic-password", "-s", _KEYCHAIN_SERVICE,
                        "-a", name], capture_output=True, timeout=15)
    except Exception:
        pass


def protect(secret: str, name: str = "secret") -> str:
    """Encode a secret for app_settings.json. `name` labels its Keychain item
    on a Mac (one item per name; protecting again replaces it)."""
    if not secret:
        if sys.platform == "darwin":
            keychain_delete(name)
        return ""
    enc = _dpapi(secret.encode("utf-8"), True)
    if enc is not None:
        return _PREFIX_DPAPI + base64.b64encode(enc).decode("ascii")
    if _keychain_set(name, secret):
        return _PREFIX_KEYCHAIN + name
    return _PREFIX_B64 + base64.b64encode(secret.encode("utf-8")).decode("ascii")


def unprotect(stored: str) -> str:
    if not stored:
        return ""
    try:
        if stored.startswith(_PREFIX_DPAPI):
            dec = _dpapi(base64.b64decode(stored[len(_PREFIX_DPAPI):]), False)
            return dec.decode("utf-8") if dec is not None else ""
        if stored.startswith(_PREFIX_KEYCHAIN):
            return _keychain_get(stored[len(_PREFIX_KEYCHAIN):]) or ""
        if stored.startswith(_PREFIX_B64):
            return base64.b64decode(stored[len(_PREFIX_B64):]).decode("utf-8")
    except Exception:
        return ""
    return ""


def mask(secret: str) -> str:
    """'sk-ant-…abcd' style display string."""
    if not secret:
        return ""
    return secret[:6] + "…" + secret[-4:] if len(secret) > 12 else "…" * 3
