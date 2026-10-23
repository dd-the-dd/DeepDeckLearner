import json

import pytest

from deepdeck_learner.agent_studio import create_agent, update_agent
from deepdeck_learner.deck_workshop import create_deck, find_deck, update_deck
from deepdeck_learner.entity_selection import labels, resolve_seats


def catalog(root):
    directory = root / ".deepdeck" / "decks"
    directory.mkdir(parents=True)
    for name, tag in [("a", "tempo"), ("b", "combo"), ("c", "tempo")]:
        (directory / f"{name}.json").write_text(
            json.dumps(
                {
                    "id": name,
                    "name": name,
                    "format": "legacy",
                    "cards": [],
                    "tags": [tag],
                    "collections": ["friday"],
                }
            ),
            encoding="utf-8",
        )
    first = create_agent(
        root,
        {
            "name": "First",
            "format": "legacy",
            "deckVersionIds": ["a", "b"],
            "tags": ["blue"],
            "collections": ["friday"],
        },
    )
    second = create_agent(
        root, {"name": "Second", "format": "legacy", "deckVersionIds": ["c"], "tags": ["red"]}
    )
    return first, second


def test_seeded_random_freezes_candidates_and_intersects_mastered(tmp_path):
    first, _ = catalog(tmp_path)
    seats = [
        {
            "kind": "agent",
            "agent_id": "random",
            "deck_id": "random",
            "agent_tags": ["blue"],
            "deck_tags": ["tempo"],
        }
    ]
    result = resolve_seats(tmp_path, seats, "legacy", 42)
    assert result == resolve_seats(tmp_path, seats, "legacy", 42)
    assert result[0]["agent_id"] == first["id"]
    assert result[0]["_deck_candidates"] == ["a"]
    assert result[0]["_selection"]["agentCandidates"] == [first["id"]]


def test_empty_filtered_pool_never_falls_back(tmp_path):
    first, _ = catalog(tmp_path)
    for seat in [
        {"kind": "human", "deck_id": "random", "deck_tags": ["missing"]},
        {
            "kind": "agent",
            "agent_id": first["id"],
            "deck_id": "random",
            "deck_collections": ["missing"],
        },
        {"kind": "agent", "agent_id": first["id"], "deck_id": "c"},
    ]:
        with pytest.raises(ValueError):
            resolve_seats(tmp_path, [seat], "legacy", 1)


def test_human_random_all_eligible_and_agent_label_revision_persists(tmp_path):
    first, _ = catalog(tmp_path)
    result = resolve_seats(
        tmp_path, [{"kind": "human", "deck_id": "random", "deck_tags": ["tempo"]}], "legacy", 1
    )
    assert result[0]["_selection"]["deckCandidates"] == ["a", "c"]
    updated = update_agent(
        tmp_path, first["id"], {"tags": [" BLUE ", "blue"], "collections": ["test"]}
    )
    assert updated["tags"] == ["blue"]
    assert updated["collections"] == ["test"]
    assert labels([" Tempo ", "tempo"]) == ["tempo"]
    with pytest.raises(ValueError):
        labels("tempo")


def test_deck_labels_persist_separately_from_card_tags(tmp_path):
    deck = create_deck(
        tmp_path,
        {"name": "Labels", "format": "legacy", "tags": [" TEMPO "], "collections": ["Friday"]},
    )
    assert find_deck(tmp_path, deck["id"])["tags"] == ["tempo"]
    updated = update_deck(tmp_path, deck["id"], {"tags": ["combo"], "collections": []})
    snapshot = json.loads(
        (tmp_path / ".deepdeck" / "decks" / f"{deck['id']}.json").read_text(encoding="utf-8")
    )
    assert updated["tags"] == snapshot["tags"] == ["combo"]
    assert snapshot["collections"] == []
