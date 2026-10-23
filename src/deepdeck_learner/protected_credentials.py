"""Private local credentials; Windows DPAPI, owner-only files elsewhere."""

from __future__ import annotations

import ctypes
import json
import os
import uuid
from pathlib import Path
from typing import Any


def _windows_protect(data: bytes, *, decrypt: bool = False) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    operation = (
        ctypes.windll.crypt32.CryptUnprotectData
        if decrypt
        else ctypes.windll.crypt32.CryptProtectData
    )
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("Windows could not protect the local LLM credentials.")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


def read_credentials(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = path.read_bytes()
    if data.startswith(b"DPAPI\0"):
        if os.name != "nt":
            raise ValueError("These LLM credentials belong to another Windows installation.")
        data = _windows_protect(data[6:], decrypt=True)
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Invalid local LLM credential file.")
    return value


def write_credentials(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value).encode("utf-8")
    if os.name == "nt":
        data = b"DPAPI\0" + _windows_protect(data)
    pending = path.with_name(f".{path.name}.{uuid.uuid4().hex}.pending")
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)
