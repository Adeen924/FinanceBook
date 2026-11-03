"""
Local secret storage for the optional AI receipt reader's API key.

The key lives in FinanceBook's per-user app_settings.json (never in the .db,
which may sync to a shared cloud folder). On Windows it is encrypted with DPAPI
(CryptProtectData), so it can only be decrypted by the same Windows user on the
same machine; elsewhere it falls back to base64 (obfuscation only). The key is
never logged or included in error messages.
"""
import base64
import sys

_PREFIX_DPAPI = "dpapi:"
_PREFIX_B64 = "b64:"


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


def protect(secret: str) -> str:
    if not secret:
        return ""
    enc = _dpapi(secret.encode("utf-8"), True)
    if enc is not None:
        return _PREFIX_DPAPI + base64.b64encode(enc).decode("ascii")
    return _PREFIX_B64 + base64.b64encode(secret.encode("utf-8")).decode("ascii")


def unprotect(stored: str) -> str:
    if not stored:
        return ""
    try:
        if stored.startswith(_PREFIX_DPAPI):
            dec = _dpapi(base64.b64decode(stored[len(_PREFIX_DPAPI):]), False)
            return dec.decode("utf-8") if dec is not None else ""
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
