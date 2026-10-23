"""One crash-safe, cross-process lock for a profile's replay transactions.

Batch workers and the web controller are different processes. A Python thread
lock alone cannot serialize quota reservations, favorites and viewing leases.
The lock file is not a sentinel: only the operating-system lock means busy.
"""

from __future__ import annotations

import errno
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Concatenate, ParamSpec, TypeVar


class ReplayStorageBusyError(OSError):
    pass


_registry_guard = threading.Lock()
_thread_locks: dict[tuple[Path, str], threading.RLock] = {}
_ownership = threading.local()
P = ParamSpec("P")
R = TypeVar("R")


@contextmanager
def replay_storage_lock(
    root: Path, *, timeout: float = 30, namespace: str = "replay"
) -> Iterator[None]:
    if namespace not in {"replay", "statistics", "llm", "decks", "league"}:
        raise ValueError("Unknown storage lock namespace")
    key = (root.resolve(), namespace)
    with _registry_guard:
        local = _thread_locks.setdefault(key, threading.RLock())
    deadline = time.monotonic() + timeout
    operation = "edit" if namespace in {"decks", "llm"} else "game"
    message = (
        f"{namespace.title()} storage is busy saving another {operation}. Try again in a moment."
    )
    if not local.acquire(timeout=max(0, timeout)):
        raise ReplayStorageBusyError(message)
    held: set[tuple[Path, str]] = getattr(_ownership, "held", set())
    _ownership.held = held
    try:
        if key in held:
            yield
            return
        path = key[0] / ".deepdeck" / f"{namespace}-storage.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    if sys.platform == "win32":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    if time.monotonic() >= deadline:
                        raise ReplayStorageBusyError(message) from error
                    time.sleep(min(0.025, max(0, deadline - time.monotonic())))
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                handle.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        local.release()


def replay_transaction(
    function: Callable[Concatenate[Path, P], R],
) -> Callable[Concatenate[Path, P], R]:
    @wraps(function)
    def transaction(root: Path, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with replay_storage_lock(root):
            return function(root, *args, **kwargs)

    return transaction
