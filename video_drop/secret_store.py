"""Keep the iPhone passcode encrypted at rest with Windows DPAPI.

`.env` holds `PHONE_PASSCODE_DPAPI=<base64>`: the passcode encrypted by
CryptProtectData for the signed-in Windows user. Only that user on this PC can
decrypt it; a copied `.env` is useless elsewhere. Nothing here prints or logs a
value. A plain `PHONE_PASSCODE` (environment or `.env`) still works for people who
set it on purpose; `scripts/set_passcode.py` writes only the encrypted form and
removes a plain line it finds.
"""

from __future__ import annotations

import base64
import ctypes
import os
import sys

KEY = "PHONE_PASSCODE_DPAPI"
_ENTROPY = b"Auto iPhone Uploader / PHONE_PASSCODE"
_UI_FORBIDDEN = 0x1  # CRYPTPROTECT_UI_FORBIDDEN


class SecretStoreError(RuntimeError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _call(function, data: bytes) -> bytes:
    if sys.platform != "win32":
        raise SecretStoreError("Encrypted passcodes need Windows (DPAPI)")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    source, _keep = _blob(data)
    entropy, _keep_entropy = _blob(_ENTROPY)
    out = _Blob()
    ok = getattr(crypt32, function)(ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                                    _UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise SecretStoreError(f"Windows could not {'encrypt' if function == 'CryptProtectData' else 'decrypt'} "
                               "the passcode for this user")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(secret: str) -> str:
    """Encrypt for this Windows user; returns base64 text for `.env`."""
    return base64.b64encode(_call("CryptProtectData", secret.encode("utf-8"))).decode("ascii")


def unprotect(token: str) -> str:
    try:
        raw = base64.b64decode(token.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise SecretStoreError("The saved passcode is not readable; run scripts\\set_passcode.py again") from exc
    return _call("CryptUnprotectData", raw).decode("utf-8")


def passcode(values: dict[str, str]) -> str | None:
    """The passcode from an encrypted `.env` entry, or None (never raises at import time)."""
    token = values.get(KEY) or os.environ.get(KEY)
    if not token:
        return None
    try:
        return unprotect(token)
    except SecretStoreError:
        return None
