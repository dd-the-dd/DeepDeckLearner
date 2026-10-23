"""Choose representative deck artwork from its actual cards, without an LLM.

This is a presentation heuristic, not a claim about measured in-game impact.
Only the commander and main deck participate; sideboard/considering cards and
tokens must not become a deck's public identity.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from .card_kinds import auxiliary_flags


def _image(card: dict[str, Any]) -> str:
    images = card.get("image_uris")
    values = [card.get("imageUri"), card.get("imageUrl"), card.get("urlFront")]
    if isinstance(images, dict):
        values.extend([images.get("normal"), images.get("large"), images.get("small")])
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
            ):
                return value
        except ValueError:
            continue
    faces = card.get("faces", card.get("card_faces"))
    if isinstance(faces, list) and faces and isinstance(faces[0], dict):
        return _image(
            {key: value for key, value in faces[0].items() if key not in {"faces", "card_faces"}}
        )
    return ""


def deck_cover(deck: dict[str, Any]) -> dict[str, Any] | None:
    cards = deck.get("cards")
    if not isinstance(cards, list):
        return None
    deck_name = str(deck.get("name", "")).casefold()
    candidates: list[tuple[float, str, dict[str, Any], str]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        section = str(card.get("section", "main")).casefold()
        type_line = str(card.get("typeLine", card.get("type_line", ""))).casefold()
        if (
            section not in {"main", "commander"}
            or auxiliary_flags(card)[1]
            or type_line.startswith(("token", "emblem", "dungeon"))
        ):
            continue
        name = str(card.get("name", "")).strip()
        image = _image(card)
        try:
            quantity = float(card.get("quantity", 1))
        except (TypeError, ValueError):
            continue
        if not name or not image or not quantity > 0:
            continue
        score = min(quantity, 4) * 2
        reason = "main-deck-card"
        # Prefer a threat/build-around over generic lands or support spells.
        if "land" in type_line:
            score -= 100
        elif any(
            kind in type_line for kind in ("creature", "planeswalker", "enchantment", "artifact")
        ):
            score += 12
        tags = card.get("tags", [])
        if isinstance(tags, list):
            normalized = {str(tag).casefold().replace("_", "-") for tag in tags}
            if normalized.intersection(
                {"signature", "build-around", "combo-piece", "win-condition", "finisher"}
            ):
                score += 60
                reason = "strategy-tag"
        lowered = name.casefold()
        words = set(re.findall(r"[a-z]{4,}", lowered)) - {"the", "with", "from", "into", "card"}
        if lowered in deck_name or any(
            word in set(re.findall(r"[a-z]{4,}", deck_name)) for word in words
        ):
            score += 120
            reason = "deck-name"
        if section == "commander":
            score += 1000
            reason = "commander"
        candidates.append((score, lowered, card, reason))
    if not candidates:
        return None
    # A stable tie-break keeps deck covers consistent across refreshes.
    _, _, selected, reason = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
    return {
        "name": selected["name"],
        "cardId": selected.get("cardId") or selected.get("scryfallId"),
        "imageUrl": _image(selected),
        "reason": reason,
    }
