from __future__ import annotations

import base64
import ctypes
import json
import os
import re
from pathlib import Path
from typing import Any


def _path(root: Path, name: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,60}\.json", name):
        raise ValueError("Invalid private settings file.")
    return root / ".deepdeck" / name


def _windows_crypt(value: bytes, *, decrypt: bool = False) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    operation.restype = wintypes.BOOL
    # User-bound, noninteractive DPAPI; deliberately not LOCAL_MACHINE scope.
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)):
        raise OSError("Windows could not access this user's saved League session.")
    try:
        return ctypes.string_at(destination.data, destination.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(destination.data)


def save_private_json(root: Path, name: str, value: dict[str, Any]) -> None:
    path = _path(root, name)
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    envelope: dict[str, Any]
    if os.name == "nt":
        envelope = {
            "protection": "windows-dpapi-user",
            "data": base64.b64encode(_windows_crypt(payload)).decode("ascii"),
        }
    else:
        envelope = {"protection": "private-file", "value": value}
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(envelope), encoding="utf-8")
    os.chmod(pending, 0o600)
    pending.replace(path)


def load_private_json(root: Path, name: str) -> dict[str, Any]:
    path = _path(root, name)
    if not path.is_file():
        return {}
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if envelope.get("protection") == "windows-dpapi-user":
            if os.name != "nt":
                raise OSError("Reconnect the League account on this computer.")
            value = json.loads(_windows_crypt(base64.b64decode(envelope["data"]), decrypt=True))
        else:
            value = envelope.get("value", {})
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(
            "The saved League session is unreadable. Connect the account again."
        ) from error
    return value if isinstance(value, dict) else {}


def remove_private_json(root: Path, name: str) -> None:
    _path(root, name).unlink(missing_ok=True)
