from __future__ import annotations

import json
import re
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import httpx

from .card_kinds import auxiliary_flags, playable_card_count
from .deck_edits import check_deck_version, deck_transaction
from .deck_visuals import deck_cover

SCHEMA_VERSION = "deepdeck-local-deck/v1"
FORMATS = frozenset({"legacy", "commander"})
SECTIONS = frozenset({"main", "sideboard", "considering", "commander", "companion"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_root(root: Path) -> Path:
    return root / ".deepdeck" / "deck-workspaces"


def _snapshot_path(root: Path, deck_id: str) -> Path:
    return root / ".deepdeck" / "decks" / f"{deck_id}.json"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Windows readers (including indexing/antivirus) can temporarily deny the
    # delete-sharing needed by replace. Keep the atomic write and old target;
    # retry only these OS errors, never delete the destination to force a save.
    for attempt in range(8):
        try:
            pending.replace(path)
            break
        except PermissionError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 7:
                raise
            time.sleep(0.01 * 2**attempt)


def _deck_path(root: Path, deck_id: str) -> Path:
    if not re.fullmatch(r"localdeck-[a-f0-9]{12}", deck_id):
        raise ValueError("Unknown local deck.")
    path = (_workspace_root(root) / f"{deck_id}.json").resolve()
    if path.parent != _workspace_root(root).resolve():
        raise ValueError("Unknown local deck.")
    return path


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError("Unknown local deck.") from error
    except (OSError, ValueError) as error:
        raise ValueError("The local deck file is unavailable or invalid.") from error
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("The local deck schema is not supported.")
    return value


def _text(value: Any, *, field: str, maximum: int, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"{field} is required.")
    if len(result) > maximum:
        raise ValueError(f"{field} must contain at most {maximum} characters.")
    return result


def _format(value: Any) -> str:
    result = str(value or "legacy").strip().casefold()
    if result not in FORMATS:
        raise ValueError("Format must be legacy or commander.")
    return result


def _section(value: Any) -> str:
    result = str(value or "main").strip().casefold()
    if result not in SECTIONS:
        raise ValueError(
            "Card section must be main, sideboard, considering, commander, or companion."
        )
    return result


def _tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 30:
        raise ValueError("A card can contain at most 30 strategy tags.")
    result = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    if any(len(item) > 80 for item in result):
        raise ValueError("Every card tag must contain at most 80 characters.")
    return result


def _image_uri(card: dict[str, Any]) -> str:
    images = card.get("image_uris")
    if isinstance(images, dict):
        return str(images.get("normal") or images.get("large") or "")
    faces = card.get("card_faces")
    if isinstance(faces, list) and faces and isinstance(faces[0], dict):
        face_images = faces[0].get("image_uris")
        if isinstance(face_images, dict):
            return str(face_images.get("normal") or face_images.get("large") or "")
    return ""


def _scryfall_card(name: str) -> dict[str, Any]:
    try:
        response = httpx.get(
            "https://api.scryfall.com/cards/named",
            params={"exact": name},
            timeout=12.0,
            headers={"User-Agent": "DeepDeckLearner/0.2 deck-workshop"},
        )
        response.raise_for_status()
        card = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise ValueError(f"Card '{name}' could not be resolved with Scryfall.") from error
    if not isinstance(card, dict) or not card.get("id") or not card.get("name"):
        raise ValueError(f"Card '{name}' returned incomplete metadata.")
    return card


def _resolved_card(
    card: dict[str, Any], *, quantity: int, section: str, tags: list[str]
) -> dict[str, Any]:
    card_id = str(card["id"])
    result: dict[str, Any] = {
        "entryId": str(uuid.uuid4()),
        "cardId": card_id,
        "printingId": card_id,
        "scryfallId": card_id,
        "oracleId": card.get("oracle_id"),
        "name": str(card["name"]),
        "quantity": quantity,
        "section": section,
        "tags": tags,
        "typeLine": str(card.get("type_line") or ""),
        "oracleText": str(card.get("oracle_text") or ""),
        "manaCost": str(card.get("mana_cost") or ""),
        "manaValue": float(card.get("cmc") or 0),
        "colors": card.get("colors") if isinstance(card.get("colors"), list) else [],
        "colorIdentity": (
            card.get("color_identity") if isinstance(card.get("color_identity"), list) else []
        ),
        "keywords": card.get("keywords") if isinstance(card.get("keywords"), list) else [],
        "layout": str(card.get("layout") or "normal"),
        "power": card.get("power"),
        "toughness": card.get("toughness"),
        "imageUri": _image_uri(card),
        "imageUrl": _image_uri(card),
        "isToken": False,
        "isGamePiece": False,
    }
    faces = card.get("card_faces")
    if isinstance(faces, list):
        result["faces"] = faces
    result["isToken"], result["isGamePiece"] = auxiliary_flags(result)
    return result


def _cards(value: Any, current: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError("A local deck must contain a card list of at most 500 entries.")
    existing = {
        (str(item.get("name", "")).casefold(), str(item.get("section", "main"))): item
        for item in (current or [])
        if isinstance(item, dict) and item.get("scryfallId")
    }
    by_entry = {str(item["entryId"]): item for item in (current or []) if item.get("entryId")}
    by_name = {key[0]: value for key, value in existing.items()}
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("Every deck card must be an object.")
        name = _text(raw.get("name"), field="Card name", maximum=200, required=True)
        section = _section(raw.get("section"))
        try:
            quantity = int(raw.get("quantity", 1))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Quantity for {name} must be a whole number.") from error
        if not 0 <= quantity <= 99:
            raise ValueError(f"Quantity for {name} must be between 0 and 99.")
        if quantity == 0:
            continue
        key = (name.casefold(), section)
        if key in seen:
            raise ValueError(f"{name} appears more than once in {section}.")
        seen.add(key)
        card_tags = _tags(raw.get("tags"))
        previous = by_entry.get(str(raw.get("entryId", "")))
        if previous is not None and str(previous.get("name", "")).casefold() != name.casefold():
            raise ValueError("A card entry id cannot be reused for a different card name.")
        previous = previous or existing.get(key)
        preserve_identity = previous is not None
        previous = previous or by_name.get(name.casefold())
        if previous is not None:
            entry = dict(previous)
            if preserve_identity:
                entry.setdefault("entryId", raw.get("entryId") or str(uuid.uuid4()))
            else:
                entry["entryId"] = raw.get("entryId") or str(uuid.uuid4())
            entry.update({"quantity": quantity, "section": section, "tags": card_tags})
            result.append(entry)
            continue
        result.append(
            _resolved_card(_scryfall_card(name), quantity=quantity, section=section, tags=card_tags)
        )
    entry_ids = [card.get("entryId") for card in result if card.get("entryId")]
    if len(entry_ids) != len(set(entry_ids)):
        raise ValueError("Every card entry must have a distinct identity.")
    return result


def _colors(cards: list[dict[str, Any]]) -> list[str]:
    values: set[str] = set()
    for card in cards:
        raw = card.get("colorIdentity", [])
        if isinstance(raw, list):
            values.update(str(color) for color in raw if str(color) in {"W", "U", "B", "R", "G"})
    return [color for color in ("W", "U", "B", "R", "G") if color in values]


def _materialize(root: Path, deck: dict[str, Any]) -> None:
    snapshot = dict(deck)
    snapshot.pop("schemaVersion", None)
    snapshot["id"] = deck["id"]
    snapshot["deckId"] = deck["id"]
    snapshot["creator"] = "You · local"
    snapshot["colors"] = _colors(deck.get("cards", []))
    snapshot["cardCount"] = playable_card_count(deck.get("cards", []))
    _write_json(_snapshot_path(root, str(deck["id"])), snapshot)


def _summary(deck: dict[str, Any]) -> dict[str, Any]:
    from .deck_sync import sync_summary

    cards = deck.get("cards", []) if isinstance(deck.get("cards"), list) else []
    counts = {
        section: sum(
            int(card.get("quantity", 0))
            for card in cards
            if isinstance(card, dict)
            and card.get("section", "main") == section
            and not auxiliary_flags(card)[1]
        )
        for section in SECTIONS
    }
    playable_count = sum(counts[key] for key in ("main", "sideboard", "commander", "companion"))
    return {
        "id": deck["id"],
        "name": deck["name"],
        "description": deck.get("description", ""),
        "tags": deck.get("tags", []),
        "collections": deck.get("collections", []),
        "format": deck["format"],
        "version": int(deck.get("version", 1)),
        "colors": _colors(cards),
        "coverCard": deck_cover(deck),
        "playableCardCount": playable_count,
        "sectionCounts": counts,
        "readyForPlay": playable_count > 0,
        "origin": deck.get("origin"),
        "createdAt": deck.get("createdAt"),
        "updatedAt": deck.get("updatedAt"),
        "workspace": True,
        "cached": True,
        "syncStatus": sync_summary(deck),
    }


def list_decks(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(_workspace_root(root).glob("localdeck-*.json")):
        try:
            result.append(_summary(_load(path)))
        except ValueError:
            continue
    return sorted(result, key=lambda item: str(item.get("updatedAt", "")), reverse=True)


def find_deck(root: Path, deck_id: str) -> dict[str, Any]:
    from .deck_sync import sync_summary

    deck = _load(_deck_path(root, deck_id))
    cards = []
    for card in deck.get("cards", []):
        token, piece = auxiliary_flags(card)
        cards.append({**card, "isToken": token, "isGamePiece": piece})
    return {
        **deck,
        "tags": deck.get("tags", []),
        "collections": deck.get("collections", []),
        "cards": cards,
        "syncStatus": sync_summary(deck),
    }


@deck_transaction
def store_workspace(root: Path, deck: dict[str, Any], *, backup: bool = False) -> dict[str, Any]:
    """Persist a synchronized workspace without treating sync metadata as a deck edit."""
    path = _deck_path(root, str(deck.get("id", "")))
    if deck.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("The local deck schema is not supported.")
    if backup and path.is_file():
        previous = _load(path)
        history = _workspace_root(root) / "revisions" / str(deck["id"])
        _write_json(
            history / f"{previous.get('version', 1)}-{uuid.uuid4().hex[:12]}.json", previous
        )
    value = {key: item for key, item in deck.items() if key != "syncStatus"}
    _write_json(path, value)
    _materialize(root, value)
    return find_deck(root, str(deck["id"]))


@deck_transaction
def create_deck(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from .entity_selection import labels

    deck_id = f"localdeck-{uuid.uuid4().hex[:12]}"
    now = _now()
    raw_cards = payload.get("cards", [])
    deck = {
        "schemaVersion": SCHEMA_VERSION,
        "id": deck_id,
        "name": _text(
            payload.get("name") or "Untitled deck", field="Deck name", maximum=120, required=True
        ),
        "description": _text(payload.get("description"), field="Description", maximum=2_000),
        "tags": labels(payload.get("tags")),
        "collections": labels(payload.get("collections")),
        "format": _format(payload.get("format")),
        "version": 1,
        "cards": _cards(raw_cards),
        "combos": [],
        "openingHands": [],
        "origin": {"kind": "local"},
        "createdAt": now,
        "updatedAt": now,
    }
    _write_json(_deck_path(root, deck_id), deck)
    _materialize(root, deck)
    return find_deck(root, deck_id)


@deck_transaction
def fork_downloaded_deck(root: Path, version_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", version_id):
        raise ValueError("Unknown downloaded deck.")
    try:
        source = json.loads(_snapshot_path(root, version_id).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("Download this deck before opening it in the workshop.") from error
    if not isinstance(source, dict) or not isinstance(source.get("cards"), list):
        raise ValueError("The downloaded deck snapshot is invalid.")
    deck_id = f"localdeck-{uuid.uuid4().hex[:12]}"
    now = _now()
    deck = {
        **source,
        "schemaVersion": SCHEMA_VERSION,
        "id": deck_id,
        "deckId": deck_id,
        "name": _text(source.get("name") or "Imported deck", field="Deck name", maximum=120),
        "description": _text(source.get("description"), field="Description", maximum=2_000),
        "format": _format(source.get("format")),
        "version": 1,
        "origin": {"kind": "league", "versionId": version_id},
        "createdAt": now,
        "updatedAt": now,
    }
    # A fork is a new editable identity, never another writer for the source draft.
    deck.pop("sync", None)
    deck.pop("syncStatus", None)
    _write_json(_deck_path(root, deck_id), deck)
    _materialize(root, deck)
    return find_deck(root, deck_id)


def _reconcile_card_links(current: dict[str, Any], updated: dict[str, Any]) -> None:
    """Keep known relationship identities valid without pruning imported unknowns."""
    current_cards = {
        str(card["entryId"]): card for card in current.get("cards", []) if card.get("entryId")
    }
    next_cards = {
        str(card["entryId"]): card for card in updated.get("cards", []) if card.get("entryId")
    }
    removed = current_cards.keys() - next_cards.keys()
    if not removed:
        return
    replacements: dict[str, str | None] = {}
    for entry_id in removed:
        matches = [
            key
            for key, card in next_cards.items()
            if card["name"].casefold() == current_cards[entry_id]["name"].casefold()
        ]
        replacements[entry_id] = matches[0] if len(matches) == 1 else None
    state = deepcopy(updated.get("serverState", {}))
    cohesion = state.get("cohesion")
    if not isinstance(cohesion, dict):
        return
    for field in ("cardLinks", "findabilityLinks", "castabilityLinks"):
        links = cohesion.get(field)
        if not isinstance(links, dict):
            continue
        reconciled: dict[str, Any] = {}
        for key, score in links.items():
            parts = str(key).split("|")
            ids = [unquote(part) for part in parts]
            next_key = key
            if len(ids) == 2 and any(part in removed for part in ids):
                remapped = [replacements.get(part, part) for part in ids]
                if None in remapped or remapped[0] == remapped[1]:
                    continue
                encoded = [quote(str(part), safe="") for part in remapped]
                next_key = "|".join(sorted(encoded) if field == "cardLinks" else encoded)
            if next_key in reconciled and reconciled[next_key] != score:
                raise ValueError(
                    "These zone entries have conflicting relationship scores. Clear or align their scores before merging them."
                )
            reconciled[next_key] = score
        cohesion[field] = reconciled
    updated["serverState"] = state


def _study_notes(value: Any, *, hands: bool = False) -> list[dict[str, Any]]:
    label = "Opening-hand" if hands else "Combo"
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"A deck can contain at most 100 {label.lower()} notes.")
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 150_000:
        raise ValueError(f"{label} notes exceed their storage limit.")
    result = []
    names: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ValueError(f"Each {label.lower()} note must be an object.")
        name = raw.get("name", f"{label} {index + 1}")
        text = raw.get("notes", "")
        cards = raw.get("cards", [])
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise ValueError("Give every study note a name of 1–120 characters.")
        if name.strip().casefold() in names:
            raise ValueError("Study note names must be unique within each section.")
        names.add(name.strip().casefold())
        if not isinstance(text, str) or len(text) > 2_000:
            raise ValueError("Study notes must be text of at most 2,000 characters.")
        if (
            not isinstance(cards, list)
            or len(cards) > 20
            or any(not isinstance(card, str) or not 1 <= len(card.strip()) <= 200 for card in cards)
        ):
            raise ValueError(
                "A study note may list at most 20 short card names, including repeated copies."
            )
        if hands and "keep" in raw and type(raw["keep"]) is not bool:
            raise ValueError("An opening-hand keep annotation must be a boolean.")
        result.append(
            {
                **raw,
                "name": name.strip(),
                "notes": text.strip(),
                "cards": [c.strip() for c in cards],
            }
        )
    return result


@deck_transaction
def update_deck(root: Path, deck_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .entity_selection import labels

    path = _deck_path(root, deck_id)
    current = _load(path)
    if "expectedVersion" in payload:
        check_deck_version(current, payload["expectedVersion"])
    updated = dict(current)
    for field in ("tags", "collections"):
        if field in payload:
            updated[field] = labels(payload[field])
    if "name" in payload:
        updated["name"] = _text(payload.get("name"), field="Deck name", maximum=120, required=True)
    if "description" in payload:
        updated["description"] = _text(
            payload.get("description"), field="Description", maximum=2_000
        )
    if "cards" in payload:
        updated["cards"] = _cards(payload["cards"], current.get("cards", []))
    if "combos" in payload:
        updated["combos"] = _study_notes(payload["combos"])
    if "openingHands" in payload:
        updated["openingHands"] = _study_notes(payload["openingHands"], hands=True)
    if "cohesion" in payload:
        cohesion = payload["cohesion"]
        if not isinstance(cohesion, dict) or len(json.dumps(cohesion)) > 150_000:
            raise ValueError("Deck relationships must be a bounded object.")
        updated["serverState"] = {**current.get("serverState", {}), "cohesion": cohesion}
    if "cards" in payload:
        _reconcile_card_links(current, updated)
    updated["version"] = int(current.get("version", 1)) + 1
    updated["updatedAt"] = _now()
    _write_json(path, updated)
    _materialize(root, updated)
    return find_deck(root, deck_id)


@deck_transaction
def delete_deck(root: Path, deck_id: str) -> dict[str, Any]:
    path = _deck_path(root, deck_id)
    deck = _load(path)
    path.unlink()
    snapshot = _snapshot_path(root, deck_id)
    if snapshot.exists():
        snapshot.unlink()
    return {"id": deck_id, "name": deck.get("name", "Local deck"), "deleted": True}
