"""Bounded deck-list imports and absolute bulk edits using the workshop writer."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any

from .deck_edits import check_deck_version, deck_transaction
from .deck_workshop import SECTIONS, create_deck, find_deck, update_deck


def parse_decklist(text: Any) -> list[dict[str, Any]]:
    if not isinstance(text, str) or not text.strip() or len(text) > 100_000:
        raise ValueError("Provide a nonempty deck list of at most 100,000 characters.")
    headers = {
        "main": "main",
        "main deck": "main",
        "mainboard": "main",
        "deck": "main",
        "commander": "commander",
        "commanders": "commander",
        "commandant": "commander",
        "commandante": "commander",
        "sideboard": "sideboard",
        "reserve": "sideboard",
        "companion": "companion",
        "considering": "considering",
        "consideration": "considering",
        "considerations": "considering",
        "maybeboard": "considering",
        **{
            label: "main"
            for label in [
                "creatures",
                "artifacts",
                "artefacts",
                "enchantments",
                "enchantements",
                "instants",
                "sorceries",
                "ephemeres et rituels",
                "planeswalkers",
                "lands",
                "terrains",
            ]
        },
    }
    section = "main"
    cards: dict[tuple[str, str], dict[str, Any]] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("\ufeff")
        if not line or re.fullmatch(r"```(?:text|txt|plaintext)?", line, re.I):
            continue
        heading = line.strip("#[]: ").casefold()
        heading = re.sub(r"\s*(?:[—–-]\s*\d+|\(\d+\))$", "", heading).strip()
        heading = "".join(
            c for c in unicodedata.normalize("NFKD", heading) if not unicodedata.combining(c)
        )
        if heading in headers:
            section = headers[heading]
            continue
        if line.startswith("//"):
            continue
        zone = section
        if line.upper().startswith("SB:"):
            line, zone = line[3:].strip(), "sideboard"
        match = re.fullmatch(r"(\d+)[xX]?\s+(.+)", line)
        if not match:
            raise ValueError(
                f"Deck-list line {number}: use '1 Card name' or a supported zone heading. "
                "Nothing was imported."
            )
        quantity, name = int(match[1]), match[2].strip()
        # Arena printing suffix, not a parenthetical portion of the card name.
        name = re.sub(r"\s+\([A-Za-z0-9]{2,8}\)\s+[A-Za-z0-9★-]+(?:\s+\*F\*)?$", "", name)
        if not 1 <= quantity <= 99 or not name or len(name) > 200:
            raise ValueError(
                f"Deck-list line {number}: require a card name and quantity from 1 to 99."
            )
        key = (name.casefold(), zone)
        card = cards.setdefault(key, {"name": name, "quantity": 0, "section": zone, "tags": []})
        card["quantity"] += quantity
        if card["quantity"] > 99 or len(cards) > 500:
            raise ValueError(
                "A list supports at most 500 entries and 99 copies of each card per zone."
            )
    if not cards:
        raise ValueError("The deck list contains no cards. Nothing was imported.")
    return list(cards.values())


def import_decklist(root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    # Resolve all names before create_deck writes a new workspace. A bad name
    # therefore never leaves a half-imported list or changes an existing deck.
    cards = parse_decklist(arguments.get("deck_list"))
    return create_deck(
        root,
        {
            "name": arguments.get("name"),
            "format": arguments.get("format"),
            "description": arguments.get("description", ""),
            "cards": cards,
        },
    )


@deck_transaction
def update_deck_cards(root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    deck_id = str(arguments.get("deck_id", ""))
    deck = find_deck(root, deck_id)
    check_deck_version(deck, arguments.get("expected_version"))
    changes = arguments.get("cards")
    if not isinstance(changes, list) or not 1 <= len(changes) <= 500:
        raise ValueError("Supply from 1 to 500 card changes.")
    cards = deepcopy(deck["cards"])
    seen = set()
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("Each card change must be an object.")
        name, section = change.get("name"), change.get("section", "main")
        quantity = change.get("quantity")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 200
            or section not in SECTIONS
        ):
            raise ValueError("Supply an exact card name and a supported zone for each change.")
        if type(quantity) is not int or not 0 <= quantity <= 99:
            raise ValueError("Card quantity must be a whole number from 0 to 99.")
        key = (name.strip().casefold(), section)
        if key in seen:
            raise ValueError("Supply each card/zone only once in a bulk edit.")
        seen.add(key)
        matches = [card for card in cards if (card["name"].casefold(), card["section"]) == key]
        if len(matches) > 1:
            raise ValueError(
                "This card/zone has multiple entries. Inspect and resolve the ambiguity first."
            )
        existing = matches[0] if matches else None
        if quantity == 0:
            if existing is not None:
                cards.remove(existing)
        elif existing is not None:
            existing["quantity"] = quantity
            if "tags" in change:
                existing["tags"] = change["tags"]
        else:
            cards.append(
                {
                    "name": name.strip(),
                    "quantity": quantity,
                    "section": section,
                    "tags": change.get("tags", []),
                }
            )
    return update_deck(root, deck_id, {"cards": cards, "expectedVersion": deck["version"]})
