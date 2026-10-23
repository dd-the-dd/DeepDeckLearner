"""Shared distinction between playable cards and auxiliary game pieces."""

from __future__ import annotations

from typing import Any


def auxiliary_flags(card: dict[str, Any]) -> tuple[bool, bool]:
    type_line = str(card.get("typeLine", card.get("type_line", ""))).strip().casefold()
    section = str(card.get("section", "")).strip().casefold()
    layout = str(card.get("layout", "")).strip().casefold()
    token = (
        bool(card.get("isToken"))
        or type_line == "token"
        or type_line.startswith("token ")
        or layout in {"token", "double_faced_token"}
    )
    auxiliary = (
        token
        or bool(card.get("isGamePiece"))
        or type_line in {"card", "counter", "marker", "checklist", "helper"}
        or type_line.startswith(("emblem", "dungeon", "sticker", "art card"))
        or layout in {"emblem", "art_series"}
        or section in {"token", "tokens", "emblem", "dungeon", "marker", "markers"}
    )
    # Incomplete metadata is not proof of an auxiliary piece. In particular,
    # real companions such as Yorion still occupy a sideboard slot.
    return token, auxiliary


def playable_card_count(cards: list[Any]) -> int:
    total = 0
    for card in cards:
        if not isinstance(card, dict) or auxiliary_flags(card)[1]:
            continue
        if str(card.get("section", "main")).strip().casefold() in {"considering", "considered"}:
            continue
        try:
            total += max(0, int(card.get("quantity", 1)))
        except (TypeError, ValueError):
            continue
    return total
