from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .decision_audit import audit_status
from .statistics_store import stored_rows


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _interval(wins: int, total: int) -> list[float] | None:
    if not total:
        return None
    z = 1.96
    proportion = wins / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return [max(0.0, center - margin), min(1.0, center + margin)]


def local_statistics(root: Path, days: int = 0) -> dict[str, Any]:
    """Summarize played matches, counting a shared replay once across agent seats."""
    if days not in {0, 7, 30, 90}:
        raise ValueError("Choose all time, 7, 30, or 90 days.")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    rows = []
    for kind, directory in (("agent", "agents"), ("player", "players")):
        for path in (root / ".deepdeck" / directory).glob("*/local-results.jsonl"):
            metadata = _object(path.parent / "local-model.json")
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for index, line in enumerate(lines):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("result") not in {"win", "loss", "draw"}:
                    continue
                try:
                    created = datetime.fromisoformat(
                        str(row.get("createdAt", "")).replace("Z", "+00:00")
                    )
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                except ValueError:
                    created = None
                if cutoff and (created is None or created < cutoff):
                    continue
                rows.append(
                    {
                        **row,
                        "actorId": path.parent.name,
                        "actorKind": kind,
                        "actorName": row.get("actorName")
                        or metadata.get("name")
                        or ("You" if kind == "player" else path.parent.name),
                        "matchId": row.get("replayId")
                        or f"{row.get('batchId', path.parent.name)}:{row.get('gameIndex', index)}",
                        "createdAtUnixMs": int(created.timestamp() * 1000) if created else None,
                    }
                )
    for row in stored_rows(root):
        try:
            created = datetime.fromisoformat(str(row.get("createdAt", "")).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except ValueError:
            created = None
        if row.get("result") not in {"win", "loss", "draw"} or (
            cutoff and (created is None or created < cutoff)
        ):
            continue
        rows.append(
            {
                **row,
                "actorName": row.get("actorName") or row.get("actorId"),
                "matchId": row.get("replayId") or f"{row.get('batchId')}:{row.get('gameIndex')}",
                "createdAtUnixMs": int(created.timestamp() * 1000) if created else None,
            }
        )
    deck_names: dict[str, str] = {}
    for path in (root / ".deepdeck" / "decks").glob("*.json"):
        value = _object(path)
        deck_names[path.stem] = str(value.get("name", path.stem))

    def group(selected: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in selected:
            grouped[str(row.get(field, "unknown"))].append(row)
        summaries = []
        for identity, played in grouped.items():
            wins = sum(row["result"] == "win" for row in played)
            losses = sum(row["result"] == "loss" for row in played)
            draws = len(played) - wins - losses
            name = (
                deck_names.get(identity, identity)
                if field == "deckVersionId"
                else str(played[0]["actorName"])
            )
            summaries.append(
                {
                    "id": identity,
                    "name": name,
                    "matches": len(played),
                    "wins": wins,
                    "losses": losses,
                    "draws": draws,
                    "winRate": wins / len(played),
                    "confidence95": _interval(wins, len(played)),
                    "decks": len({str(row.get("deckVersionId")) for row in played}),
                    "decisionIssues": len(
                        {
                            row["matchId"]
                            for row in played
                            if audit_status(row.get("decisionAudit")) == "degraded"
                        }
                    ),
                }
            )
        return sorted(summaries, key=lambda row: (-row["matches"], -row["winRate"], row["name"]))

    matches: dict[str, dict[str, Any]] = {}
    for row in rows:
        match = matches.setdefault(
            row["matchId"],
            {
                "id": row["matchId"],
                "createdAtUnixMs": row["createdAtUnixMs"],
                "format": row.get("format"),
                "participants": [],
                "winner": None,
                "decisionReliability": audit_status(row.get("decisionAudit")),
            },
        )
        incoming = audit_status(row.get("decisionAudit"))
        severity = {"confirmed": 0, "unknown": 1, "unconfirmed": 2, "degraded": 3}
        if severity[incoming] > severity[match["decisionReliability"]]:
            match["decisionReliability"] = incoming
        match["participants"].append(
            {
                "name": row["actorName"],
                "deck": deck_names.get(str(row.get("deckVersionId")), "Local deck"),
                "result": row["result"],
            }
        )
        if row["result"] == "win":
            match["winner"] = row["actorName"]
    agents = group([row for row in rows if row["actorKind"] == "agent"], "actorId")
    players = group([row for row in rows if row["actorKind"] == "player"], "actorId")
    decks = group(rows, "deckVersionId")
    return {
        "days": days,
        "summary": {
            "matches": len(matches),
            "seatResults": len(rows),
            "agents": len(agents),
            "players": len(players),
            "decks": len(decks),
        },
        "agents": agents,
        "decks": decks,
        "players": players,
        "reliability": {
            f"{status}Matches": sum(
                match["decisionReliability"] == status for match in matches.values()
            )
            for status in ("confirmed", "degraded", "unconfirmed", "unknown")
        },
        "recentMatches": sorted(
            matches.values(), key=lambda row: row["createdAtUnixMs"] or 0, reverse=True
        )[:20],
    }
