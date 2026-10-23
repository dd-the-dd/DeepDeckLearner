"""Fenced stop requests and durable receipts for one rotation turn."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .deck_workshop import _write_json


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid League rotation identifier.")
    return value


def turn_paths(root: Path, queue_id: str, entry_id: str, dispatch_id: str) -> tuple[Path, Path]:
    base = root / ".deepdeck" / "league-queues" / identifier(queue_id)
    return base / "controls" / f"{identifier(entry_id)}.json", base / "turns" / identifier(
        dispatch_id
    ) / "result.json"


def stop_through(path: Path, sequence: int) -> None:
    if type(sequence) is not int or sequence < 1:
        raise ValueError("Invalid League dispatch sequence.")
    previous = read_stop(path)
    _write_json(path, {"through": max(previous, sequence)})


def read_stop(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as stream:
            text = stream.read(129)
    except FileNotFoundError:
        return 0
    if len(text) > 128:
        raise ValueError("Invalid League stop control.")
    value = json.loads(text)
    through = value.get("through") if isinstance(value, dict) else None
    if type(through) is not int or through < 0:
        raise ValueError("Invalid League stop control.")
    return through


def stop_requested(path: Path | None, sequence: int) -> bool:
    if path is None:
        return False
    try:
        return read_stop(path) >= sequence
    except (OSError, ValueError):
        # An unreadable control must not silently keep sending new tickets.
        return True


def write_result(
    path: Path | None,
    *,
    run_id: str | None,
    completed: int,
    elapsed: float,
    outcome: str | None,
    match_id: str | None,
    completed_match_ids: list[str] | None = None,
) -> None:
    if path is not None:
        result: dict[str, Any] = {
            "schemaVersion": "deepdeck-league-turn/v1",
            "runId": run_id,
            "completedEntries": completed,
            "elapsedSeconds": elapsed,
            "outcome": outcome,
            "matchId": match_id,
        }
        if completed_match_ids is not None:
            result.update(
                schemaVersion="deepdeck-league-turn/v2", completedMatchIds=completed_match_ids
            )
        _write_json(path, result)


def read_result(path: Path, dispatch_id: str) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as stream:
            text = stream.read(16_385)
    except FileNotFoundError:
        return None
    if len(text) > 16_384:
        raise ValueError("League turn receipt is too large.")
    result = json.loads(text)
    if (
        not isinstance(result, dict)
        or result.get("schemaVersion") not in ("deepdeck-league-turn/v1", "deepdeck-league-turn/v2")
        or result.get("runId") != dispatch_id
    ):
        raise ValueError("League turn receipt does not match this dispatch.")
    elapsed = result.get("elapsedSeconds")
    count = result.get("completedEntries")
    if type(count) is not int or not 0 <= count <= 1_000_000:
        raise ValueError("Invalid League completed count.")
    if result["schemaVersion"] == "deepdeck-league-turn/v1":
        if count not in {0, 1}:
            raise ValueError("Invalid legacy League completed count.")
    else:
        from uuid import UUID

        ids = result.get("completedMatchIds")
        if not isinstance(ids, list) or len(ids) != count:
            raise ValueError("League completed count does not match its authoritative IDs.")
        try:
            normalized = [str(UUID(value)) if isinstance(value, str) else None for value in ids]
        except ValueError as error:
            raise ValueError("Invalid League completed match ID.") from error
        if None in normalized or len(set(normalized)) != count:
            raise ValueError("Invalid or duplicate League completed match ID.")
        if count and result.get("matchId") not in ids:
            raise ValueError("League final match is not among completed matches.")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (float, int))
        or not 0 <= elapsed <= 315_360_000
        or not math.isfinite(elapsed)
    ):
        raise ValueError("Invalid League turn duration.")
    if result.get("outcome") not in (None, "complete", "failed", "cancelled"):
        raise ValueError("Invalid League turn outcome.")
    if result.get("matchId") is not None and (
        not isinstance(result["matchId"], str) or len(result["matchId"]) > 512
    ):
        raise ValueError("Invalid League match identifier.")
    if (
        (count > 0) != (result.get("outcome") == "complete")
        or result.get("outcome") == "complete"
        and not result.get("matchId")
    ):
        raise ValueError("Inconsistent League turn result.")
    return {
        key: result.get(key) for key in ("completedEntries", "elapsedSeconds", "outcome", "matchId")
    }
