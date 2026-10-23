from __future__ import annotations

import pytest

from deepdeck_learner.deck_workshop import create_deck, find_deck, update_deck


@pytest.mark.parametrize(
    "field,note",
    [
        ("combos", "not an object"),
        ("combos", {"name": False}),
        ("combos", {"name": " "}),
        ("combos", {"cards": [{}]}),
        ("combos", {"notes": {}}),
        ("combos", {"notes": "a" * 2001}),
        ("combos", {"cards": ["Island"] * 21}),
        ("openingHands", {"keep": "yes"}),
        ("openingHands", {"keep": 1}),
        ("openingHands", {"cards": ["a" * 201]}),
    ],
)
def test_invalid_notes_do_not_mutate_a_deck(tmp_path, field, note):
    deck = create_deck(tmp_path, {"name": "Study deck", "format": "legacy"})
    with pytest.raises(ValueError):
        update_deck(tmp_path, deck["id"], {"expectedVersion": 1, field: [note]})
    assert find_deck(tmp_path, deck["id"])["version"] == 1


def test_repeated_hand_copies_and_note_metadata_are_retained(tmp_path):
    deck = create_deck(tmp_path, {"name": "Study deck", "format": "legacy"})
    updated = update_deck(
        tmp_path,
        deck["id"],
        {
            "expectedVersion": 1,
            "combos": [
                {
                    "name": " Plan ",
                    "cards": [" Thalia, Guardian of Thraben "],
                    "custom": {"source": "import"},
                }
            ],
            "openingHands": [
                {
                    "name": "Keep example",
                    "cards": ["Island", "Island", "Brainstorm"],
                    "keep": True,
                    "notes": " Functional.",
                }
            ],
        },
    )
    assert updated["combos"][0] == {
        "name": "Plan",
        "cards": ["Thalia, Guardian of Thraben"],
        "notes": "",
        "custom": {"source": "import"},
    }
    assert updated["openingHands"][0]["cards"] == ["Island", "Island", "Brainstorm"]
    assert updated["openingHands"][0]["notes"] == "Functional."


def test_duplicate_note_names_cannot_make_conflict_merges_ambiguous(tmp_path):
    deck = create_deck(tmp_path, {"name": "Study deck", "format": "legacy"})
    with pytest.raises(ValueError, match="unique"):
        update_deck(tmp_path, deck["id"], {"combos": [{"name": "Plan"}, {"name": "plan"}]})
