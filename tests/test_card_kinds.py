from __future__ import annotations

import json

import pytest

from deepdeck_learner.card_kinds import auxiliary_flags, playable_card_count
from deepdeck_learner.catalogs import cached_decks
from deepdeck_learner.deck_workshop import create_deck, find_deck, list_decks
from deepdeck_learner.jobs import JobManager


@pytest.mark.parametrize(
    "card,expected",
    [
        ({"name": "Energy Reserve", "typeLine": "Card"}, (False, True)),
        ({"name": "Energy Reserve", "type_line": "Card", "layout": "token"}, (True, True)),
        ({"name": "Illusion", "typeLine": "Token Creature — Illusion"}, (True, True)),
        ({"typeLine": "Token"}, (True, True)),
        ({"typeLine": "Emblem — Teferi"}, (False, True)),
        ({"typeLine": "Dungeon"}, (False, True)),
        ({"typeLine": "Marker"}, (False, True)),
        ({"typeLine": "Creature", "layout": "art_series"}, (False, True)),
        ({"typeLine": "Creature", "isGamePiece": True}, (False, True)),
        (
            {
                "typeLine": "Legendary Creature — Bird Serpent",
                "name": "Yorion, Sky Nomad",
                "section": "companion",
            },
            (False, False),
        ),
        ({"typeLine": "Artifact Creature — Construct", "section": "sideboard"}, (False, False)),
        ({"typeLine": "Creature", "name": "Token Collector"}, (False, False)),
        ({"typeLine": "Enchantment", "name": "Counterbalance"}, (False, False)),
        ({"name": "Missing metadata"}, (False, False)),
    ],
)
def test_only_actual_game_pieces_are_classified_as_auxiliary(card, expected):
    assert auxiliary_flags(card) == expected
    assert JobManager._auxiliary_game_piece_flags(card) == expected


def test_yorion_setup_counts_80_main_15_sideboard_and_keeps_all_game_pieces(tmp_path):
    cards = [
        {
            "cardId": "plains",
            "name": "Plains",
            "typeLine": "Basic Land — Plains",
            "quantity": 80,
            "section": "main",
        },
        {
            "cardId": "germ",
            "name": "Germ",
            "typeLine": "Token Creature — Phyrexian Germ",
            "quantity": 2,
            "section": "main",
        },
        {
            "cardId": "swords",
            "name": "Swords to Plowshares",
            "typeLine": "Instant",
            "quantity": 14,
            "section": "sideboard",
        },
        {
            "cardId": "yorion",
            "name": "Yorion, Sky Nomad",
            "typeLine": "Legendary Creature — Bird Serpent",
            "quantity": 1,
            "section": "sideboard",
        },
        {
            "cardId": "energy",
            "name": "Energy Reserve",
            "typeLine": "Card",
            "quantity": 1,
            "section": "sideboard",
        },
        {
            "cardId": "illusion",
            "name": "Illusion",
            "typeLine": "Token Creature — Illusion",
            "quantity": 1,
            "section": "sideboard",
        },
    ]
    path = tmp_path / ".deepdeck" / "decks" / "yorion.json"
    path.parent.mkdir(parents=True)
    original = json.dumps({"name": "Death and Taxes (Yorion)", "format": "legacy", "cards": cards})
    path.write_text(original)
    _, prepared = JobManager(tmp_path)._cached_playtest_deck("yorion", "legacy")
    assert len(prepared) == 99  # Keep auxiliary definitions for token creation.
    assert sum(not card["isGamePiece"] and not card["isSideboard"] for card in prepared) == 80
    assert sum(not card["isGamePiece"] and card["isSideboard"] for card in prepared) == 15
    assert playable_card_count(cards) == 95
    assert cached_decks(tmp_path, "", "legacy")["items"][0]["playableCardCount"] == 95
    assert path.read_text() == original  # No destructive editing of the deck.


def test_workshop_counts_and_returned_flags_match_engine_without_losing_markers(
    tmp_path, monkeypatch
):
    from deepdeck_learner import deck_workshop

    def resolve(name):
        return {
            "id": name,
            "name": name,
            "type_line": "Card" if name == "Energy Reserve" else "Instant",
            "image_uris": {},
        }

    monkeypatch.setattr(deck_workshop, "_scryfall_card", resolve)
    deck = create_deck(
        tmp_path,
        {
            "name": "Markers",
            "format": "legacy",
            "cards": [
                {"name": "Swords", "quantity": 15, "section": "sideboard"},
                {"name": "Energy Reserve", "quantity": 1, "section": "sideboard"},
            ],
        },
    )
    assert len(find_deck(tmp_path, deck["id"])["cards"]) == 2
    assert deck["cards"][1]["isGamePiece"]
    summary = list_decks(tmp_path)[0]
    assert summary["sectionCounts"]["sideboard"] == 15
    assert summary["playableCardCount"] == 15
