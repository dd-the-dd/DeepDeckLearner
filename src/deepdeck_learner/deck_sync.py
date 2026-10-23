from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .deck_edits import check_deck_version
from .deck_workshop import SCHEMA_VERSION, find_deck, list_decks, store_workspace
from .league_account import (
    SESSION_FILE,
    LeagueAccountError,
    LeagueAuthenticationError,
    account_client,
    platform_url,
    request,
)
from .private_store import load_private_json
from .replay_lock import replay_storage_lock

_locks: dict[str, Any] = {}
_locks_guard = threading.Lock()


class DeckSyncConflict(LeagueAccountError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _deck_lock(root: Path, deck_id: str) -> Iterator[None]:
    key = str(root.resolve()) + ":" + deck_id
    with _locks_guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock, replay_storage_lock(root, namespace="decks"):
        yield


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def content_fingerprint(deck: dict[str, Any]) -> str:
    return _hash(
        {
            "name": deck.get("name"),
            "description": deck.get("description", ""),
            "format": deck.get("format"),
            "cards": [
                {
                    key: card.get(key)
                    for key in ("name", "quantity", "section", "scryfallId", "tags")
                }
                for card in deck.get("cards", [])
            ],
            "combos": deck.get("combos", []),
            "openingHands": deck.get("openingHands", []),
            "cohesion": deck.get("serverState", {}).get("cohesion"),
        }
    )


def _remote_fingerprint(workspace: dict[str, Any]) -> str:
    return _hash(
        {
            "name": workspace.get("name"),
            "description": workspace.get("description") or "",
            "isMetaDeck": bool(workspace.get("isMetaDeck", False)),
            "state": workspace.get("state", {}),
        }
    )


def _safe_id(value: Any) -> str:
    result = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", result):
        raise LeagueAccountError("The League returned an invalid workspace identifier.")
    return result


def _owner(root: Path) -> str:
    session = load_private_json(root, SESSION_FILE)
    user_id = str(session.get("user", {}).get("id", ""))
    if not user_id:
        raise LeagueAuthenticationError("Sign in to your League account to synchronize decks.")
    return user_id


def _check_binding(root: Path, sync: dict[str, Any]) -> str:
    owner = _owner(root)
    if sync and (sync.get("ownerId") != owner or sync.get("platformUrl") != platform_url()):
        raise DeckSyncConflict(
            "This deck is linked to a different League account or server. "
            "Fork it to publish a new copy."
        )
    return owner


def sync_summary(deck: dict[str, Any]) -> dict[str, Any]:
    sync = deck.get("sync", {})
    current = content_fingerprint(deck)
    return {
        "linked": bool(sync.get("workspaceId")),
        "workspaceId": sync.get("workspaceId"),
        "publishedVersionId": sync.get("publishedVersionId"),
        "needsUpload": sync.get("syncedFingerprint") != current,
        "needsPublishing": not sync.get("publishedVersionId")
        or sync.get("publishedFingerprint") != current,
        "syncedAt": sync.get("syncedAt"),
    }


def _payload(deck: dict[str, Any]) -> dict[str, Any]:
    state = deepcopy(deck.get("serverState", {}))
    cards = []
    for card in deck.get("cards", []):
        entry = deepcopy(card)
        section = str(card.get("section", "main"))
        printing = card.get("scryfallId") or card.get("printingId") or card.get("cardId")
        entry.update(
            {
                "id": card.get("entryId")
                or str(uuid.uuid5(uuid.NAMESPACE_URL, f"{deck['id']}:{card['name']}:{section}")),
                "scryfallId": printing,
                "isCommander": section == "commander",
                "isSideboard": section == "sideboard",
                "isConsidering": section == "considering",
                "isCompanion": section == "companion",
                "isToken": bool(card.get("isToken"))
                or str(card.get("typeLine", "")).casefold().startswith("token"),
            }
        )
        entry["isGamePiece"] = bool(card.get("isGamePiece")) or entry["isToken"]
        entry["selectedOption"] = {**(card.get("selectedOption") or {}), "scryfallId": printing}
        cards.append(entry)
    state.update({"format": deck["format"], "cards": cards})
    state["deepdeckLearner"] = {
        "localDeckId": deck["id"],
        "combos": deck.get("combos", []),
        "openingHands": deck.get("openingHands", []),
    }
    return {
        "name": deck["name"],
        "description": deck.get("description", ""),
        "isMetaDeck": bool(deck.get("isMetaDeck", False)),
        "state": state,
    }


def list_server_workspaces(root: Path) -> list[dict[str, Any]]:
    with account_client(root) as client:
        result = request(client, "GET", "me/deck-sessions")
    if not isinstance(result, list):
        raise LeagueAccountError("The League did not return a deck workspace list.")
    return [workspace for workspace in result if isinstance(workspace, dict)]


def sync_deck(
    root: Path, deck_id: str, *, force: bool = False, expected_version: Any = None
) -> dict[str, Any]:
    with _deck_lock(root, deck_id):
        deck = find_deck(root, deck_id)
        if expected_version is not None:
            check_deck_version(deck, expected_version)
        sync = dict(deck.get("sync", {}))
        owner = _check_binding(root, sync)
        with account_client(root) as client:
            remote = None
            workspace_id = sync.get("workspaceId")
            if workspace_id:
                workspace_id = _safe_id(workspace_id)
                remote = request(client, "GET", f"me/deck-sessions/{workspace_id}")
                if sync.get("remoteFingerprint") != _remote_fingerprint(remote) and not force:
                    raise DeckSyncConflict(
                        "The site draft changed since your last sync. Download its changes "
                        "or explicitly replace it with this local version."
                    )
            else:
                # Recover an uncertain create response instead of creating another deck.
                items = request(client, "GET", "me/deck-sessions")
                remote = next(
                    (
                        item
                        for item in items
                        if isinstance(item, dict)
                        and item.get("state", {}).get("deepdeckLearner", {}).get("localDeckId")
                        == deck_id
                    ),
                    None,
                )
                if remote:
                    workspace_id = _safe_id(remote.get("id"))
                    if not force and _remote_fingerprint(remote) != _remote_fingerprint(
                        _payload(deck)
                    ):
                        raise DeckSyncConflict(
                            "A linked site draft already exists. "
                            "Import it or choose to replace the site draft."
                        )
            if remote is not None and force:
                backup = root / ".deepdeck" / "deck-workspaces" / "remote-backups" / deck_id
                backup.mkdir(parents=True, exist_ok=True)
                (backup / f"{uuid.uuid4().hex}.json").write_text(
                    json.dumps(remote, indent=2), encoding="utf-8"
                )
            if workspace_id:
                remote = request(client, "PUT", f"me/deck-sessions/{workspace_id}", _payload(deck))
            else:
                remote = request(client, "POST", "me/deck-sessions", _payload(deck))
            workspace_id = _safe_id(remote.get("id"))
        deck["sync"] = {
            **sync,
            "platformUrl": platform_url(),
            "ownerId": owner,
            "workspaceId": workspace_id,
            "deckId": remote.get("deckId"),
            "remoteFingerprint": _remote_fingerprint(remote),
            "syncedFingerprint": content_fingerprint(deck),
            "syncedAt": _now(),
        }
        return store_workspace(root, deck)


def publish_deck(
    root: Path, deck_id: str, *, force: bool = False, expected_version: Any = None
) -> dict[str, Any]:
    with _deck_lock(root, deck_id):
        deck = sync_deck(root, deck_id, force=force, expected_version=expected_version)
        sync = dict(deck["sync"])
        with account_client(root) as client:
            remote = request(
                client, "POST", f"me/deck-sessions/{_safe_id(sync['workspaceId'])}/publish"
            )
        version_id = str(remote.get("latestVersionId") or "")
        try:
            uuid.UUID(version_id)
        except ValueError as error:
            raise LeagueAccountError(
                "The site saved the draft but did not confirm a published deck version."
            ) from error
        if remote.get("hasUnpublishedChanges"):
            raise LeagueAccountError("The site has not finished publishing this deck yet.")
        deck["sync"] = {
            **sync,
            "publishedVersionId": version_id,
            "publishedFingerprint": content_fingerprint(deck),
            "remoteFingerprint": _remote_fingerprint(remote),
            "publishedAt": _now(),
        }
        deck = store_workspace(root, deck)
        snapshot = {
            **json.loads(
                (root / ".deepdeck" / "decks" / f"{deck_id}.json").read_text(encoding="utf-8")
            ),
            "id": version_id,
            "deckId": remote.get("deckId"),
            "version": remote.get("version", 1),
            "creator": "You · League",
        }
        cache = root / ".deepdeck" / "decks" / f"{version_id}.json"
        pending = cache.with_suffix(".pending")
        pending.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        pending.replace(cache)
        return deck


def import_server_workspace(
    root: Path,
    workspace_id: str,
    *,
    deck_id: str | None = None,
    discard_local: bool = False,
    expected_version: Any = None,
) -> dict[str, Any]:
    workspace_id = _safe_id(workspace_id)
    if deck_id is None:
        # Opening an already imported site draft must not create another local deck.
        for item in list_decks(root):
            candidate = find_deck(root, str(item["id"]))
            binding = candidate.get("sync", {})
            if (
                binding.get("workspaceId") == workspace_id
                and binding.get("platformUrl") == platform_url()
            ):
                _check_binding(root, binding)
                deck_id = str(candidate["id"])
                break
    with _deck_lock(root, deck_id or workspace_id):
        current = find_deck(root, deck_id) if deck_id else None
        if current and expected_version is not None:
            check_deck_version(current, expected_version)
        owner = _check_binding(root, (current or {}).get("sync", {}))
        if current and sync_summary(current)["needsUpload"] and not discard_local:
            raise DeckSyncConflict(
                "This deck has local changes. Save them to the site or explicitly download "
                "the site version; a local backup will be kept."
            )
        with account_client(root) as client:
            remote = request(client, "GET", f"me/deck-sessions/{workspace_id}")
        state = remote.get("state", {})
        cards = []
        for raw in state.get("cards", []):
            selected = raw.get("selectedOption") or {}
            section = raw.get("section") or next(
                (
                    section
                    for field, section in (
                        ("isCommander", "commander"),
                        ("isCompanion", "companion"),
                        ("isConsidering", "considering"),
                        ("isSideboard", "sideboard"),
                    )
                    if raw.get(field)
                ),
                "main",
            )
            printing = (
                raw.get("scryfallId")
                or selected.get("scryfallId")
                or raw.get("printingId")
                or raw.get("cardId")
                or raw.get("id")
            )
            cards.append(
                {
                    **raw,
                    "entryId": raw.get("id"),
                    "scryfallId": printing,
                    "cardId": printing,
                    "oracleId": raw.get("oracleId") or raw.get("oracle_id"),
                    "section": section,
                    "quantity": int(raw.get("quantity", 1)),
                    "tags": raw.get("tags", []),
                    "typeLine": raw.get("typeLine") or raw.get("type_line", ""),
                    "manaCost": raw.get("manaCost") or raw.get("mana_cost", ""),
                    "manaValue": raw.get("manaValue", raw.get("cmc", 0)),
                    "colorIdentity": raw.get("colorIdentity", raw.get("color_identity", [])),
                    "imageUri": raw.get("imageUri")
                    or raw.get("imageUrl")
                    or raw.get("image_uris", {}).get("normal", ""),
                }
            )
        notes = state.get("deepdeckLearner", {})
        deck = {
            **(current or {}),
            "schemaVersion": SCHEMA_VERSION,
            "id": deck_id or f"localdeck-{uuid.uuid4().hex[:12]}",
            "name": remote["name"],
            "description": remote.get("description") or "",
            "format": state.get("format", "legacy"),
            "cards": cards,
            "combos": notes.get("combos", []),
            "openingHands": notes.get("openingHands", []),
            "serverState": state,
            "isMetaDeck": bool(remote.get("isMetaDeck", False)),
            "version": int((current or {}).get("version", 0)) + 1,
            "createdAt": (current or {}).get("createdAt", _now()),
            "updatedAt": _now(),
            "origin": {"kind": "league", "versionId": remote.get("latestVersionId")},
        }
        fingerprint = content_fingerprint(deck)
        deck["sync"] = {
            "platformUrl": platform_url(),
            "ownerId": owner,
            "workspaceId": workspace_id,
            "deckId": remote.get("deckId"),
            "publishedVersionId": remote.get("latestVersionId"),
            "remoteFingerprint": _remote_fingerprint(remote),
            "syncedFingerprint": fingerprint,
            "syncedAt": _now(),
        }
        if not remote.get("hasUnpublishedChanges") and remote.get("latestVersionId"):
            deck["sync"]["publishedFingerprint"] = fingerprint
        return store_workspace(root, deck, backup=bool(current))


def published_deck_version(root: Path, deck_id: str) -> str:
    if not deck_id.startswith("localdeck-"):
        return deck_id
    deck = find_deck(root, deck_id)
    summary = sync_summary(deck)
    if summary["needsPublishing"]:
        raise ValueError(
            f"Publish the current version of {deck['name']} before sending its agent to the League."
        )
    if deck.get("sync", {}).get("platformUrl") != platform_url():
        raise ValueError("This deck was published to another League server.")
    return str(summary["publishedVersionId"])


def prepare_league_decks(root: Path, deck_ids: list[str]) -> dict[str, Any]:
    """Explicit deployment preparation; never evict an existing AI deck slot."""
    versions = list(dict.fromkeys(published_deck_version(root, value) for value in deck_ids))
    with account_client(root) as client:
        runtime = request(client, "GET", "runtime-config")
        if not isinstance(runtime, dict) or not isinstance(runtime.get("capabilities"), list):
            raise LeagueAccountError("The League did not confirm its supported capabilities.")
        if "ai-decks" not in runtime["capabilities"]:
            return {
                "deckVersionIds": versions,
                "pool": None,
                "slotManagementAvailable": False,
                "notice": "This release does not expose personal AI deck slots. Matchmaking validates the selected decks against the competition pool.",
            }
        pool = request(client, "GET", "me/ai-decks")
        if not isinstance(pool, dict) or not isinstance(pool.get("items"), list):
            raise LeagueAccountError("The League did not return your AI deck slots.")
        owned = {str(item.get("deckVersionId")): item for item in pool["items"]}
        for version in versions:
            item = owned.get(version)
            if item and not item.get("selected"):
                pool = request(
                    client, "PUT", f"me/ai-decks/{_safe_id(item.get('deckId'))}", {"active": True}
                )
    return {"deckVersionIds": versions, "pool": pool, "slotManagementAvailable": True}
