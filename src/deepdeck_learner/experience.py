from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .replay_lock import replay_transaction

DEFAULT_REPLAY_LIMIT = 2 * 1024 * 1024 * 1024
DEFAULT_REPLAY_POLL_SECONDS = 10 * 60


class ReplayStorageError(OSError):
    """A recording could not be saved without exceeding its storage budget."""


def _settings_path(root: Path) -> Path:
    return root / ".deepdeck" / "experience.json"


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def replay_storage_used(root: Path) -> int:
    total = 0
    for path in _replay_files(root, include_saved=True):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _replay_files(root: Path, *, include_saved: bool = False) -> list[Path]:
    candidates: list[Path] = []
    for replay_root in (
        root / ".deepdeck" / "agents",
        root / ".deepdeck" / "runs",
        root / ".deepdeck" / "league-replays",
        root / ".deepdeck" / "local-tables",
    ):
        if not replay_root.is_dir():
            continue
        for path in replay_root.rglob("*.json"):
            # Collector indexes, model settings and viewing leases are not games.
            if path.parent.name == "replays" or (
                include_saved and path.parent.name == "saved-replays"
            ):
                candidates.append(path)
    return candidates


def _actively_viewed(path: Path) -> bool:
    lease_directory = path.parent / ".leases" / path.stem
    now = int(time.time())
    for lease in lease_directory.glob("*.lease") if lease_directory.is_dir() else ():
        try:
            if int(lease.read_text(encoding="utf-8").strip()) > now:
                return True
        except (OSError, ValueError):
            continue
    return False


@replay_transaction
def enforce_replay_quota(
    root: Path,
    *,
    reserve_bytes: int = 0,
    required_free_bytes: int = 0,
    protected_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Remove oldest unpinned, unviewed replays until local storage is under quota."""

    configured = _json_object(_settings_path(root))
    limit = int(configured.get("replayStorageLimitBytes", DEFAULT_REPLAY_LIMIT))
    used = replay_storage_used(root)
    removed: list[str] = []
    reclaimed = 0
    free = shutil.disk_usage(root).free
    candidates = sorted(
        _replay_files(root),
        key=lambda path: path.stat().st_mtime if path.exists() else float("inf"),
    )
    pinned_matches = {
        path.stem
        for path in _replay_files(root, include_saved=True)
        if path.parent.name == "saved-replays" and path.stem.startswith(("batch-", "league-"))
    }
    for path in candidates:
        if used + reserve_bytes <= limit and free + reclaimed >= required_free_bytes:
            break
        if path in protected_paths or path.stem in pinned_matches or _actively_viewed(path):
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        removed.append(str(path))
        reclaimed += size
        used -= size
    error = None
    if used + reserve_bytes > limit:
        error = (
            "Not enough replay storage. Favorites and games being watched were kept. "
            "Increase the storage limit or remove a favorite to make room."
        )
    elif free + reclaimed < required_free_bytes:
        error = "Not enough free disk space to save this replay. Free disk space and try again; favorites were kept."
    status = root / ".deepdeck" / "replay-storage-status.json"
    try:
        status.parent.mkdir(parents=True, exist_ok=True)
        pending = status.with_suffix(f".{uuid.uuid4().hex}.pending")
        pending.write_text(
            json.dumps({"error": error, "checkedAtUnixMs": int(time.time() * 1000)}),
            encoding="utf-8",
        )
        pending.replace(status)
    except OSError:
        pass  # The caller also gets the error if the disk cannot store diagnostics.
    return {
        "limitBytes": limit,
        "usedBytes": max(0, used),
        "reclaimedBytes": reclaimed,
        "removed": removed,
        "quotaExceeded": used + reserve_bytes > limit,
        "error": error,
    }


@replay_transaction
def write_replay(root: Path, destination: Path, value: dict[str, Any]) -> None:
    """Reserve space before committing a new recording; never evict favorites."""
    content = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    previous_size = destination.stat().st_size if destination.is_file() else 0
    report = enforce_replay_quota(
        root,
        reserve_bytes=max(0, len(content) - previous_size),
        required_free_bytes=len(content),
        protected_paths=(destination,),
    )
    if report["error"]:
        raise ReplayStorageError(report["error"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_suffix(f".{uuid.uuid4().hex}.pending")
    try:
        pending.write_bytes(content)
        pending.replace(destination)
    except OSError as error:
        pending.unlink(missing_ok=True)
        raise ReplayStorageError(
            "The replay could not be saved. Check free disk space; existing favorites were kept."
        ) from error


def experience_settings(root: Path) -> dict[str, Any]:
    value = _json_object(_settings_path(root))
    limit = int(value.get("replayStorageLimitBytes", DEFAULT_REPLAY_LIMIT))
    used = replay_storage_used(root)
    return {
        "replayStorageLimitBytes": limit,
        "replayStorageUsedBytes": used,
        "replayStorageError": (
            "Replay storage is full. Favorites and games being watched are protected. "
            "Increase the storage limit or remove a favorite to make room."
            if used > limit
            else _json_object(root / ".deepdeck" / "replay-storage-status.json").get("error")
        ),
        "replayPollSeconds": int(value.get("replayPollSeconds", DEFAULT_REPLAY_POLL_SECONDS)),
        "autoCollectLeagueReplays": bool(value.get("autoCollectLeagueReplays", True)),
    }


@replay_transaction
def save_experience_settings(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    current = experience_settings(root)
    try:
        replay_limit = int(
            payload.get("replayStorageLimitBytes", current["replayStorageLimitBytes"])
        )
        poll_seconds = int(payload.get("replayPollSeconds", current["replayPollSeconds"]))
    except (TypeError, ValueError) as error:
        raise ValueError("Replay storage and polling values must be integers.") from error
    if not 256 * 1024 * 1024 <= replay_limit <= 100 * 1024 * 1024 * 1024:
        raise ValueError("Replay storage must be between 256 MB and 100 GB.")
    if not 60 <= poll_seconds <= 3600:
        raise ValueError("Replay polling must be between 1 and 60 minutes.")
    auto_collect = payload.get("autoCollectLeagueReplays", current["autoCollectLeagueReplays"])
    if type(auto_collect) is not bool:
        raise ValueError("Automatic League replay collection must be true or false.")
    value = {
        "schemaVersion": "deepdeck-experience/v1",
        "replayStorageLimitBytes": replay_limit,
        "replayPollSeconds": poll_seconds,
        "autoCollectLeagueReplays": auto_collect,
    }
    path = _settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)
    # A pause/resume or polling-only edit is not a request to retire recordings.
    # New replay writes still enforce the existing cap independently.
    if "replayStorageLimitBytes" in payload:
        enforce_replay_quota(root)
    return experience_settings(root)
