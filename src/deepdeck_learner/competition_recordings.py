"""Recover a missing attempt link from its owned batch, never from exception text."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")


@lru_cache(maxsize=128)
def _matches_header(
    path: Path, _mtime: int, _size: int, event: str, match: str, batch: str, replay: str
) -> bool:
    # Our writer puts id/metadata before frames. Bound the read rather than
    # expanding a potentially huge recording on each history poll. Other file
    # layouts are left unresolved, never guessed from a matching filename.
    try:
        with path.open(encoding="utf-8") as stream:
            text = stream.read(65_536)
        decoder = json.JSONDecoder()
        cursor = len(text) - len(text.lstrip())
        if text[cursor : cursor + 1] != "{":
            return False
        cursor += 1
        header = {}
        while cursor < len(text):
            while cursor < len(text) and text[cursor] in " \r\n\t,":
                cursor += 1
            key, cursor = decoder.raw_decode(text, cursor)
            if not isinstance(key, str):
                return False
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if text[cursor : cursor + 1] != ":":
                return False
            cursor += 1
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            value, cursor = decoder.raw_decode(text, cursor)
            header[key] = value
            if key == "metadata":
                return (
                    header.get("schemaVersion") == "deepdeck-replay/v1"
                    and header.get("id") == replay
                    and isinstance(value, dict)
                    and value.get("competitionId") == event
                    and value.get("competitionMatchId") == match
                    and value.get("batchId") == batch
                )
    except (OSError, ValueError, UnicodeError, RuntimeError):
        pass
    return False


def recover_attempt_recording(
    root: Path, event_id: str, match_id: str, job: dict[str, Any] | None
) -> str | None:
    if not job or job.get("kind") not in {"playtest.batch", "playtest.table"}:
        return None
    raw = job.get("artifact_path")
    if not isinstance(raw, str) or not raw:
        return None
    root = root.resolve()
    try:
        artifact = Path(raw).resolve()
        if artifact.parent != root / ".deepdeck/batches" or not _ID.fullmatch(artifact.name):
            return None
        spec_path = (artifact / "batch-spec.json").resolve()
        if spec_path.parent != artifact:
            return None
        with spec_path.open(encoding="utf-8") as stream:
            text = stream.read(2 * 1024**2 + 1)
        if len(text) > 2 * 1024**2:
            return None
        spec = json.loads(text)
        if (
            not isinstance(spec, dict)
            or spec.get("id") != artifact.name
            or spec.get("competitionId") != event_id
            or spec.get("competitionMatchId") != match_id
            or spec.get("games") != 1
        ):
            return None
        seats = spec.get("seats")
        if not isinstance(seats, list) or not seats or not all(isinstance(s, dict) for s in seats):
            return None
        agents = [s.get("agentId") for s in seats if s.get("kind") != "human"]
        if any(not isinstance(key, str) or not _ID.fullmatch(key) for key in agents):
            return None
        collections = [root / ".deepdeck/agents" / key for key in agents]
        if not collections:
            collections = [root / ".deepdeck/local-tables"]
        replay = f"batch-{artifact.name}-0001"
        for collection in collections:
            for directory in ("saved-replays", "replays"):
                path = (collection / directory / f"{replay}.json").resolve()
                if not path.is_relative_to(root / ".deepdeck") or not path.is_file():
                    continue
                stat = path.stat()
                if _matches_header(
                    path, stat.st_mtime_ns, stat.st_size, event_id, match_id, artifact.name, replay
                ):
                    return replay
    except (OSError, ValueError, UnicodeError, RuntimeError):
        pass
    return None
