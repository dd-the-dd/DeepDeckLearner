from __future__ import annotations

import pytest

from deepdeck_examples.joust import (
    POLICY_DECK_NAMES,
    QUALIFIER_PODS,
    _deck_versions_from_items,
    _winner_key,
)


def test_deck_catalog_requires_all_ten_named_commanders() -> None:
    items = [
        {"name": name, "id": f"version-{key}"}
        for key, name in POLICY_DECK_NAMES.items()
    ]
    assert _deck_versions_from_items(items) == {
        key: f"version-{key}" for key in POLICY_DECK_NAMES
    }
    with pytest.raises(ValueError, match="missing"):
        _deck_versions_from_items(items[:-1])


def test_qualifiers_cover_eight_unique_commanders_before_the_championship() -> None:
    entrants = [key for pod in QUALIFIER_PODS for key in pod]
    assert len(entrants) == 8
    assert len(set(entrants)) == 8
    assert set(POLICY_DECK_NAMES) - set(entrants) == {"yenna", "sektuar"}


def test_winner_is_resolved_from_the_immutable_deck_version() -> None:
    versions = {"aang": "version-aang", "azula": "version-azula"}
    match = {
        "summary": {
            "participants": [
                {"deckVersionId": "version-aang", "placement": 2},
                {"deckVersionId": "version-azula", "placement": 1},
            ]
        }
    }
    assert _winner_key(match, versions) == "azula"
