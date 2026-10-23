from __future__ import annotations

import ctypes
import errno
import json
import os
import threading
from pathlib import Path

import pytest

from deepdeck_learner import deck_workshop


def sharing_error(code: int = 5) -> PermissionError:
    error = PermissionError("Windows destination temporarily open")
    error.winerror = code
    return error


def test_transient_windows_replace_failure_keeps_atomic_save(tmp_path, monkeypatch):
    target = tmp_path / "deck.json"
    target.write_text('{"version":1}', encoding="utf-8")
    replace = Path.replace
    calls = []
    waits = []

    def flaky(pending, destination):
        calls.append(destination)
        assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}
        if len(calls) < 3:
            raise sharing_error()
        return replace(pending, destination)

    monkeypatch.setattr(Path, "replace", flaky)
    monkeypatch.setattr(deck_workshop.time, "sleep", waits.append)
    deck_workshop._write_json(target, {"version": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 2}
    assert waits == [0.01, 0.02]
    assert not target.with_suffix(".json.pending").exists()


@pytest.mark.parametrize(
    "error,retries",
    [
        (sharing_error(32), 8),
        (sharing_error(33), 8),
        (PermissionError("Permanent permissions"), 1),
        (OSError(errno.ENOSPC, "Disk full"), 1),
    ],
)
def test_permanent_errors_are_reported_without_destroying_old_deck(
    tmp_path, monkeypatch, error, retries
):
    target = tmp_path / "deck.json"
    target.write_text('{"version":1}', encoding="utf-8")
    calls = []
    waits = []

    def fail(*_):
        calls.append(1)
        raise error

    monkeypatch.setattr(Path, "replace", fail)
    monkeypatch.setattr(deck_workshop.time, "sleep", waits.append)
    with pytest.raises(OSError) as caught:
        deck_workshop._write_json(target, {"version": 2})
    assert caught.value is error
    assert len(calls) == retries
    assert sum(waits) < 1.3
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}
    assert json.loads(target.with_suffix(".json.pending").read_text(encoding="utf-8")) == {
        "version": 2
    }


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows file-sharing semantics")
def test_real_windows_reader_may_finish_before_atomic_replace(tmp_path):
    from ctypes import wintypes

    target = tmp_path / "deck.json"
    target.write_text('{"version":1}', encoding="utf-8")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Read/write sharing deliberately omits FILE_SHARE_DELETE, just like a
    # reader that briefly holds the cached JSON open during a UI refresh.
    handle = kernel.CreateFileW(str(target), 0x80000000, 3, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    timer = threading.Timer(0.05, lambda: kernel.CloseHandle(handle))
    timer.start()
    try:
        deck_workshop._write_json(target, {"version": 2})
    finally:
        timer.join()
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 2}
