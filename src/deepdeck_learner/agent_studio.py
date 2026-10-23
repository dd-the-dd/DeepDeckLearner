from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deepdeck_examples.decision_nodes import normalize_custom_nodes
from deepdeck_examples.programmatic import AGENT_SOURCE
from deepdeck_examples.strategic_graph import (
    ARCHETYPES,
    NODE_IDS,
    PROFILE_MIXES,
    STRATEGY_GRAPH_VERSION,
    STRATEGY_NODES,
    decision_graph_catalog,
)

from .entity_selection import labels

SCHEMA_VERSION = "deepdeck-programmatic-agent/v1"
DEFAULT_WEIGHTS = {node.id: node.default for node in STRATEGY_NODES}
HEURISTICS = tuple(
    {"id": node.id, "name": node.name, "description": node.description} for node in STRATEGY_NODES
)
PROFILES = frozenset({"cautious", "balanced", "aggressive"})
POSTURE_WEIGHTS = {
    "cautious": {
        "resource_preservation": 1.4,
        "timing_patience": 1.3,
        "protect_advantage": 1.4,
        "combat_survival": 1.2,
    },
    "balanced": {
        "resource_preservation": 1.0,
        "timing_patience": 1.0,
        "protect_advantage": 1.0,
        "combat_survival": 1.0,
    },
    "aggressive": {
        "resource_preservation": 0.8,
        "timing_patience": 0.8,
        "protect_advantage": 1.1,
        "combat_survival": 0.9,
    },
}
_policy_locks: dict[tuple[str, str], Any] = {}
_policy_locks_guard = threading.Lock()


class AgentRevisionConflict(ValueError):
    """The editor must reload before replacing a strategy changed elsewhere."""


@contextmanager
def _policy_lock(root: Path, agent_id: str) -> Iterator[None]:
    if not re.fullmatch(r"programmatic-[a-f0-9]{12}", agent_id):
        raise ValueError("Unknown programmatic agent.")
    key = (str(root.resolve()), agent_id)
    with _policy_locks_guard:
        lock = _policy_locks.setdefault(key, threading.RLock())
    with lock:
        yield


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)


def _clean_text(value: Any, *, field: str, minimum: int, maximum: int) -> str:
    text = str(value or "").strip()
    if not minimum <= len(text) <= maximum:
        raise ValueError(f"{field} must contain between {minimum} and {maximum} characters.")
    return text


def _agent_paths(root: Path) -> list[Path]:
    return sorted((root / ".deepdeck" / "agents").glob("*/agent.json"))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("The programmatic agent file is unavailable or invalid.") from error
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("The programmatic agent schema is not supported.")
    # V1 agent files created before the strategy graph remain usable. They are
    # upgraded in memory and persisted on the next normal edit.
    profile = str(value.get("profile", "balanced"))
    if profile not in PROFILES:
        profile = "balanced"
    weights = _weights(value.get("heuristics", {}))
    value["heuristics"] = weights
    value["strategyGraph"] = _strategy_graph(
        value.get("strategyGraph", {}),
        profile=profile,
        weights=weights,
    )
    value.setdefault("revisionReason", "Imported legacy policy.")
    value.setdefault("tags", [])
    value.setdefault("collections", [])
    return value


def find_agent(root: Path, agent_id: str) -> tuple[Path, dict[str, Any]]:
    if not re.fullmatch(r"programmatic-[a-f0-9]{12}", agent_id):
        raise ValueError("Unknown programmatic agent.")
    directory = (root / ".deepdeck" / "agents" / agent_id).resolve()
    agents_root = (root / ".deepdeck" / "agents").resolve()
    if directory.parent != agents_root:
        raise ValueError("Unknown programmatic agent.")
    policy = _load_json(directory / "agent.json")
    if policy.get("id") != agent_id:
        raise ValueError("The programmatic agent identity is invalid.")
    return directory, policy


def list_agents(root: Path) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    for path in _agent_paths(root):
        try:
            policy = _load_json(path)
            # Deck names and publication status evolve independently of agent revisions.
            stored = {str(deck.get("id")): deck for deck in policy.get("decks", [])}
            decks = []
            for version_id in policy.get("deckVersionIds", []):
                try:
                    decks.append(_deck_summary(root, str(version_id), str(policy["format"])))
                except ValueError:
                    # An unavailable manually moved deck must not hide its agent.
                    decks.append(
                        {**stored.get(str(version_id), {}), "id": version_id, "unavailable": True}
                    )
            policy["decks"] = decks
            agents.append(policy)
        except ValueError:
            continue
    return sorted(agents, key=lambda item: str(item.get("updatedAt", "")), reverse=True)


def heuristic_catalog() -> dict[str, Any]:
    graph = decision_graph_catalog()
    return {
        **graph,
        "schemaVersion": "deepdeck-heuristic-catalog/v2",
        "profiles": sorted(PROFILES),
        "postureWeights": POSTURE_WEIGHTS,
        "items": list(HEURISTICS),
        "nodes": list(HEURISTICS),
        "weightSemantics": "multiplier",
    }


def _deck_summary(root: Path, version_id: str, expected_format: str) -> dict[str, Any]:
    if not version_id or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", version_id):
        raise ValueError("Every deck version id must be valid.")
    path = root / ".deepdeck" / "decks" / f"{version_id}.json"
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Download deck {version_id} before assigning it to an agent.") from error
    if (
        not isinstance(snapshot, dict)
        or str(snapshot.get("format", "")).casefold() != expected_format
    ):
        raise ValueError(f"Deck {version_id} must be a downloaded {expected_format} deck.")
    from .card_kinds import playable_card_count
    from .deck_visuals import deck_cover

    summary: dict[str, Any] = {
        "id": str(snapshot.get("id", version_id)),
        "deckId": snapshot.get("deckId"),
        "name": str(snapshot.get("name", "Local deck")),
        "tags": snapshot.get("tags", []),
        "collections": snapshot.get("collections", []),
        "creator": snapshot.get("creator"),
        "version": int(snapshot.get("version", 1) or 1),
        "format": expected_format,
        "colors": snapshot.get("colors", []),
        "coverCard": deck_cover(snapshot),
        "playableCardCount": playable_card_count(snapshot["cards"])
        if isinstance(snapshot.get("cards"), list)
        else int(snapshot.get("playableCardCount", snapshot.get("cardCount", 0)) or 0),
    }
    if version_id.startswith("localdeck-"):
        from .deck_sync import sync_summary
        from .deck_workshop import find_deck

        summary["syncStatus"] = sync_summary(find_deck(root, version_id))
    return summary


def _deck_ids(payload: dict[str, Any], current: dict[str, Any] | None = None) -> list[str]:
    raw = payload.get("deckVersionIds")
    if raw is None and current is not None:
        raw = current.get("deckVersionIds", [])
    if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
        raise ValueError("Choose between 1 and 100 downloaded decks for this agent.")
    values = list(dict.fromkeys(str(value).strip() for value in raw if str(value).strip()))
    if len(values) != len(raw):
        raise ValueError("Deck version ids must be non-empty and unique.")
    return values


def _weights(payload: Any, current: dict[str, Any] | None = None) -> dict[str, float]:
    raw = payload if isinstance(payload, dict) else {}
    base = dict(DEFAULT_WEIGHTS)
    if current:
        base.update({key: value for key, value in current.items() if key in base})
    unknown = set(raw) - set(DEFAULT_WEIGHTS)
    if unknown:
        raise ValueError(f"Unknown heuristics: {', '.join(sorted(unknown))}.")
    for key, value in raw.items():
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Heuristic {key} must be a number.") from error
        if not 0.0 <= number <= 2.0:
            raise ValueError(f"Heuristic {key} must be between 0 and 2.")
        base[key] = round(number, 3)
    return base


def _bounded_float(value: Any, *, field: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a number.") from error
    if not minimum <= number <= maximum:
        raise ValueError(f"{field} must be between {minimum:g} and {maximum:g}.")
    return round(number, 4)


def _archetype_mix(value: Any, fallback: dict[str, float]) -> dict[str, float]:
    if value is None:
        return dict(fallback)
    if not isinstance(value, dict):
        raise ValueError("strategyGraph.archetypeMix must be an object.")
    unknown = set(value) - set(ARCHETYPES)
    if unknown:
        raise ValueError(f"Unknown archetypes: {', '.join(sorted(unknown))}.")
    result = {
        archetype: _bounded_float(
            value.get(archetype, 0.0),
            field=f"Archetype {archetype}",
            minimum=0.0,
            maximum=1.0,
        )
        for archetype in ARCHETYPES
    }
    total = sum(result.values())
    if total <= 0:
        raise ValueError("At least one archetype weight must be greater than zero.")
    return {key: round(item / total, 4) for key, item in result.items()}


def _pattern_strings(value: Any, *, field: str, maximum: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be a list containing at most {maximum} values.")
    result = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    if any(len(item) > 160 for item in result):
        raise ValueError(f"Every {field} value must contain at most 160 characters.")
    return result


def _patterns(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("strategyGraph.patterns must contain at most 100 patterns.")
    result: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError("Every strategy pattern must be an object.")
        identifier = str(item.get("id", f"pattern-{index + 1}")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", identifier):
            raise ValueError("Strategy pattern ids must be short identifiers.")
        if identifier in identifiers:
            raise ValueError("Strategy pattern ids must be unique.")
        identifiers.add(identifier)
        adjustments = item.get("nodeAdjustments", {})
        if not isinstance(adjustments, dict):
            raise ValueError("Pattern nodeAdjustments must be an object.")
        unknown = set(adjustments) - NODE_IDS
        if unknown:
            raise ValueError(f"Unknown pattern nodes: {', '.join(sorted(unknown))}.")
        result.append(
            {
                "id": identifier,
                "cardNames": _pattern_strings(
                    item.get("cardNames"), field="pattern cardNames", maximum=50
                ),
                "textContains": _pattern_strings(
                    item.get("textContains"), field="pattern textContains", maximum=20
                ),
                "requireTags": _pattern_strings(
                    item.get("requireTags"), field="pattern requireTags", maximum=20
                ),
                "addTags": _pattern_strings(
                    item.get("addTags"), field="pattern addTags", maximum=20
                ),
                "nodeAdjustments": {
                    str(key): _bounded_float(
                        adjustment,
                        field=f"Pattern node {key}",
                        minimum=-2.0,
                        maximum=2.0,
                    )
                    for key, adjustment in adjustments.items()
                },
            }
        )
    return result


def _strategy_graph(
    value: Any,
    *,
    profile: str,
    weights: dict[str, float],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    previous = current if isinstance(current, dict) else {}
    version = str(raw.get("version", previous.get("version", STRATEGY_GRAPH_VERSION)))
    if version != STRATEGY_GRAPH_VERSION:
        raise ValueError(f"Only strategy graph {STRATEGY_GRAPH_VERSION} is supported.")
    mix_value = raw.get("archetypeMix", previous.get("archetypeMix"))
    patterns_value = raw.get("patterns", previous.get("patterns", []))
    temperature_value = raw.get("temperature", previous.get("temperature", 0.0))
    adaptation_value = raw.get("deckAdaptation", previous.get("deckAdaptation", 0.65))
    seed_value = raw.get("seed", previous.get("seed", 0))
    temperature = _bounded_float(
        temperature_value,
        field="Decision noise",
        minimum=0.0,
        maximum=1.0,
    )
    seed = _bounded_float(seed_value, field="Decision seed", minimum=0, maximum=2**31 - 1)
    return {
        "version": version,
        "archetypeMix": _archetype_mix(mix_value, PROFILE_MIXES[profile]),
        "weights": weights,
        "deckAdaptation": _bounded_float(
            adaptation_value,
            field="Deck adaptation",
            minimum=0.0,
            maximum=1.0,
        ),
        "temperature": temperature,
        "seed": int(seed),
        "patterns": _patterns(patterns_value),
        "customNodes": normalize_custom_nodes(
            raw.get("customNodes", previous.get("customNodes", [])), NODE_IDS
        ),
    }


def _card_names(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"{field} must be a list containing at most 100 card names.")
    result = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    if any(len(item) > 160 for item in result):
        raise ValueError(f"Every {field} card name must contain at most 160 characters.")
    return result


def _metadata(directory: Path, policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": "local-model/v1",
        "id": policy["id"],
        "name": policy["name"],
        "architecture": "programmatic",
        "format": policy["format"],
        "description": policy["description"],
        "createdAt": policy["createdAt"],
        "checkpointPath": str(directory),
        "source": "local-programmatic-agent",
        "reservePlaytest": True,
        "selfPlayAllSeats": False,
        "decks": policy["decks"],
        "agentSdkRuntime": {
            "module": "deepdeck_examples.run",
            "arguments": ["programmatic"],
            "checkpointArgument": "--agent-config",
            "requiredFiles": ["agent.json", "agent.py"],
        },
    }


def _save(directory: Path, policy: dict[str, Any]) -> dict[str, Any]:
    _write_json(directory / "agent.json", policy)
    source = directory / "agent.py"
    if not source.is_file():
        source.write_text(AGENT_SOURCE, encoding="utf-8")
    revision = max(1, int(policy.get("revision", 1)))
    _write_json(directory / "revisions" / f"revision-{revision:04d}.json", policy)
    (directory / "revisions" / f"revision-{revision:04d}.py").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _write_json(directory / "local-model.json", _metadata(directory, policy))
    return policy


def create_agent(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    name = _clean_text(payload.get("name"), field="Agent name", minimum=2, maximum=64)
    game_format = str(payload.get("format", "legacy")).casefold()
    if game_format not in {"legacy", "commander"}:
        raise ValueError("Format must be legacy or commander.")
    profile = str(payload.get("profile", "balanced")).casefold()
    if profile not in PROFILES:
        raise ValueError("Profile must be cautious, balanced, or aggressive.")
    deck_ids = _deck_ids(payload)
    agent_id = f"programmatic-{uuid.uuid4().hex[:12]}"
    directory = root / ".deepdeck" / "agents" / agent_id
    now = _now()
    preferences = payload.get("cardPreferences", {})
    if not isinstance(preferences, dict):
        raise ValueError("cardPreferences must be an object.")
    graph_payload = payload.get("strategyGraph", {})
    graph_weights = graph_payload.get("weights", {}) if isinstance(graph_payload, dict) else {}
    weights = _weights(payload.get("heuristics", graph_weights))
    policy = {
        "schemaVersion": SCHEMA_VERSION,
        "id": agent_id,
        "name": name,
        "tags": labels(payload.get("tags")),
        "collections": labels(payload.get("collections")),
        "description": str(
            payload.get("description", "Deterministic deck-specific agent.")
        ).strip()[:500],
        "format": game_format,
        "profile": profile,
        "deckVersionIds": deck_ids,
        "decks": [_deck_summary(root, deck_id, game_format) for deck_id in deck_ids],
        "heuristics": weights,
        "strategyGraph": _strategy_graph(
            graph_payload,
            profile=profile,
            weights=weights,
        ),
        "cardPreferences": {
            "prefer": _card_names(preferences.get("prefer"), field="preferred"),
            "avoid": _card_names(preferences.get("avoid"), field="avoided"),
            "holdForInteraction": _card_names(
                preferences.get("holdForInteraction"), field="held-for-interaction"
            ),
        },
        "strategyNotes": str(payload.get("strategyNotes", "")).strip()[:4000],
        "revisionReason": str(payload.get("revisionReason", "Agent created.")).strip()[:500],
        "revision": 1,
        "createdAt": now,
        "updatedAt": now,
    }
    return _save(directory, policy)


def update_agent(root: Path, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with _policy_lock(root, agent_id):
        return _update_agent(root, agent_id, payload)


def _update_agent(root: Path, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    directory, current = find_agent(root, agent_id)
    if "expectedRevision" in payload:
        expected = payload["expectedRevision"]
        if type(expected) is not int or expected < 1:
            raise ValueError("expectedRevision must be a positive revision number.")
        if expected != current.get("revision"):
            raise AgentRevisionConflict(
                "This agent changed since you opened it. Reload its latest strategy before saving; your draft has not replaced it."
            )
    game_format = str(payload.get("format", current["format"])).casefold()
    if game_format not in {"legacy", "commander"}:
        raise ValueError("Format must be legacy or commander.")
    profile = str(payload.get("profile", current["profile"])).casefold()
    if profile not in PROFILES:
        raise ValueError("Profile must be cautious, balanced, or aggressive.")
    deck_ids = _deck_ids(payload, current)
    old_preferences = current.get("cardPreferences", {})
    preferences = payload.get("cardPreferences", old_preferences)
    if not isinstance(preferences, dict):
        raise ValueError("cardPreferences must be an object.")
    old_graph = current.get("strategyGraph", {})
    graph_payload = payload.get("strategyGraph", {})
    if graph_payload is not None and not isinstance(graph_payload, dict):
        raise ValueError("strategyGraph must be an object.")
    graph_weights = graph_payload.get("weights", {}) if isinstance(graph_payload, dict) else {}
    requested_weights = payload.get("heuristics", graph_weights)
    if profile != current["profile"] and isinstance(requested_weights, dict):
        # Posture is independent of deck identity. Making a combo pilot more
        # cautious must not silently turn its archetype blend into control.
        # Explicit weights in the same edit take precedence over the preset.
        requested_weights = {**POSTURE_WEIGHTS[profile], **requested_weights}
    weights = _weights(requested_weights, current.get("heuristics", {}))
    graph = _strategy_graph(
        graph_payload,
        profile=profile,
        weights=weights,
        current=old_graph,
    )
    policy = {
        **current,
        "tags": labels(payload.get("tags", current.get("tags"))),
        "collections": labels(payload.get("collections", current.get("collections"))),
        "name": _clean_text(
            payload.get("name", current["name"]), field="Agent name", minimum=2, maximum=64
        ),
        "description": str(payload.get("description", current.get("description", ""))).strip()[
            :500
        ],
        "format": game_format,
        "profile": profile,
        "deckVersionIds": deck_ids,
        "decks": [_deck_summary(root, deck_id, game_format) for deck_id in deck_ids],
        "heuristics": weights,
        "strategyGraph": graph,
        "cardPreferences": {
            "prefer": _card_names(preferences.get("prefer"), field="preferred"),
            "avoid": _card_names(preferences.get("avoid"), field="avoided"),
            "holdForInteraction": _card_names(
                preferences.get("holdForInteraction"), field="held-for-interaction"
            ),
        },
        "strategyNotes": str(
            payload.get("strategyNotes", current.get("strategyNotes", ""))
        ).strip()[:4000],
        "revisionReason": str(payload.get("revisionReason", "Policy updated.")).strip()[:500],
        "revision": int(current.get("revision", 0)) + 1,
        "updatedAt": _now(),
    }
    return _save(directory, policy)


def agent_revisions(root: Path, agent_id: str) -> list[dict[str, Any]]:
    directory, current = find_agent(root, agent_id)
    result = []
    for path in (directory / "revisions").glob("revision-*.json"):
        if not re.fullmatch(r"revision-\d{4,12}\.json", path.name):
            continue
        try:
            policy = _load_json(path)
            revision = int(policy["revision"])
            if policy.get("id") != agent_id or revision < 1:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        result.append(
            {
                "revision": revision,
                "name": policy["name"],
                "createdAt": policy.get("updatedAt", policy.get("createdAt")),
                "reason": policy.get("revisionReason", "Strategy saved."),
                "profile": policy["profile"],
                "deckCount": len(policy.get("deckVersionIds", [])),
                "patternCount": len(policy.get("strategyGraph", {}).get("patterns", [])),
                "current": revision == current.get("revision"),
                "hasPythonSnapshot": path.with_suffix(".py").is_file(),
            }
        )
    return sorted(result, key=lambda item: int(item["revision"]), reverse=True)


def restore_agent_revision(
    root: Path, agent_id: str, revision: int, expected_revision: Any
) -> dict[str, Any]:
    if type(revision) is not int or not 1 <= revision <= 999_999_999_999:
        raise ValueError("Choose an existing agent revision.")
    with _policy_lock(root, agent_id):
        directory, current = find_agent(root, agent_id)
        historical = _load_json(directory / "revisions" / f"revision-{revision:04d}.json")
        if historical.get("id") != agent_id or historical.get("revision") != revision:
            raise ValueError("The selected revision does not belong to this agent.")
        if revision == current.get("revision"):
            raise ValueError("This strategy is already the current revision.")
        return _update_agent(
            root,
            agent_id,
            {
                **historical,
                "expectedRevision": expected_revision,
                "revisionReason": f"Restored strategy and deck pool from revision {revision}. Current Python source preserved.",
            },
        )
