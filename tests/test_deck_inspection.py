from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepdeck_learner.agent_studio import create_agent, update_agent
from deepdeck_learner.deck_inspection import MAX_RESULT_BYTES, inspect_deck


def cached(root: Path) -> Path:
    path = root / ".deepdeck" / "decks" / "test-deck.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "id": "test-deck",
                "name": "Test",
                "version": 1,
                "format": "legacy",
                "apiKey": "never-export-me",
                "tags": ["tempo"],
                "cards": [
                    {
                        "name": "Island",
                        "typeLine": "Basic Land — Island",
                        "quantity": 20,
                        "entryId": "a",
                    },
                    {
                        "name": "Ponder",
                        "typeLine": "Sorcery",
                        "manaCost": "{U}",
                        "quantity": 4,
                        "entryId": "b",
                        "tags": ["my-combo-role"],
                    },
                ],
                "combos": [
                    {
                        "name": "Personal plan",
                        "cards": ["Ponder"],
                        "notes": "Find protection",
                        "secret": "never-export-me",
                    }
                ],
                "openingHands": [{"name": "Keep", "cards": ["Island", "Ponder"], "keep": True}],
                "serverState": {"cohesion": {"findabilityLinks": {"b|a": 2}}},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_read_only_whitelist_and_explicit_active_pattern(tmp_path: Path) -> None:
    path = cached(tmp_path)
    agent = create_agent(
        tmp_path, {"name": "Pilot", "format": "legacy", "deckVersionIds": ["test-deck"]}
    )
    before = path.read_bytes()
    args = {"deck_version_id": "test-deck", "agent_id": agent["id"]}
    result = inspect_deck(tmp_path, args)
    ponder = result["cards"][1]
    assert ponder["deckTags"] == ["my-combo-role"]
    assert "my-combo-role" not in ponder["activeAgentTags"]
    assert result["readOnly"] is True
    assert result["relationships"][0] == {
        "kind": "findabilityLinks",
        "directed": True,
        "leftCard": "Ponder",
        "rightCard": "Island",
        "score": 2,
    }
    assert result["studyNotes"]["openingHands"][0]["keep"] is True
    assert "never-export-me" not in json.dumps(result)
    assert path.read_bytes() == before
    update_agent(
        tmp_path,
        agent["id"],
        {
            "expectedRevision": agent["revision"],
            "strategyGraph": {
                **agent["strategyGraph"],
                "patterns": [
                    {"name": "Personal role", "cardNames": ["Ponder"], "addTags": ["my-combo-role"]}
                ],
            },
        },
    )
    updated = inspect_deck(tmp_path, args)
    assert "my-combo-role" in updated["cards"][1]["activeAgentTags"]
    assert updated["agentRevision"] == agent["revision"] + 1
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "extra",
    [
        {"limit": True},
        {"offset": 1.5},
        {"notes_limit": 0},
        {"section": []},
        {"deck_version_id": "../secret"},
        {"expected_deck_revision": True},
    ],
)
def test_invalid_tool_arguments(tmp_path: Path, extra: dict) -> None:
    cached(tmp_path)
    with pytest.raises(ValueError):
        inspect_deck(tmp_path, {"deck_version_id": "test-deck", **extra})


def test_paging_filters_and_bounded_unicode(tmp_path: Path) -> None:
    path = cached(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cards"] = [
        {"name": f"Card {i}", "typeLine": "Sorcery", "oracleText": "漢" * 4000} for i in range(50)
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    offset = 0
    names = []
    while True:
        result = inspect_deck(
            tmp_path, {"deck_version_id": "test-deck", "offset": offset, "limit": 50}
        )
        assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= MAX_RESULT_BYTES
        names.extend(card["name"] for card in result["cards"])
        if not result["pagination"]["hasMore"]:
            break
        assert result["pagination"]["nextOffset"] > offset
        offset = result["pagination"]["nextOffset"]
    assert names == [f"Card {i}" for i in range(50)]
    filtered = inspect_deck(tmp_path, {"deck_version_id": "test-deck", "search": "Card 49"})
    assert [card["name"] for card in filtered["cards"]] == ["Card 49"]


@pytest.mark.asyncio
async def test_sdk_inspection_then_explicit_pattern_changes_real_scoring(tmp_path: Path) -> None:
    from deepdeck_agent import Decision, Game

    from deepdeck_examples.strategic_graph import StrategyPolicy, WeightedDecisionGraph
    from deepdeck_learner.agent_studio import find_agent
    from deepdeck_learner.llm_gateway import _sdk_tools

    path = cached(tmp_path)
    original = path.read_bytes()
    node = {
        "id": "personal-search",
        "bias": 0.5,
        "when": {"sourceTags": ["my-combo-role"], "actionKinds": ["castSpell"]},
    }
    agent = create_agent(
        tmp_path,
        {
            "name": "Pilot",
            "deckVersionIds": ["test-deck"],
            "strategyGraph": {"customNodes": [node]},
        },
    )
    events = []

    def no_launch(_):
        pytest.fail("Inspection and graph edits must not launch matches.")

    tools = {tool.name: tool for tool in _sdk_tools(tmp_path, no_launch, events, "agent")}
    assert "inspect_deck" in {tool.name for tool in _sdk_tools(tmp_path, no_launch, [], "deck")}
    args = {"deck_version_id": "test-deck", "agent_id": agent["id"]}
    inspected = json.loads(await tools["inspect_deck"].on_invoke_tool(None, json.dumps(args)))
    assert inspected["ok"] is True
    assert find_agent(tmp_path, agent["id"])[1]["revision"] == 1
    assert path.read_bytes() == original
    source = {
        "instanceId": "ponder",
        "owner": "p1",
        "controller": "p1",
        "definition": {"id": "ponder", "name": "Ponder", "typeLine": "Sorcery", "manaCost": "{U}"},
    }
    game = Game(
        {
            "turnNumber": 4,
            "activePlayer": 0,
            "step": "postcombatMain",
            "stack": [],
            "players": [
                {
                    "id": p,
                    "life": 20,
                    "hand": [source] if p == "p1" else [],
                    "battlefield": [],
                    "library": [],
                    "graveyard": [],
                    "exile": [],
                }
                for p in ("p1", "p2")
            ],
        },
        "p1",
    )
    decision = Decision(
        "test:0",
        "p1",
        {
            "kind": "priority",
            "options": [
                {"id": "cast", "kind": "castSpell", "cardInstanceId": "ponder"},
                {"id": "pass", "kind": "passPriority"},
            ],
        },
        game,
    )
    before = WeightedDecisionGraph(StrategyPolicy.from_mapping(agent["strategyGraph"]))
    edited = json.loads(
        await tools["update_agent"].on_invoke_tool(
            None,
            json.dumps(
                {
                    "agent_id": agent["id"],
                    "expected_revision": inspected["result"]["agentRevision"],
                    "strategy_graph": {
                        **agent["strategyGraph"],
                        "patterns": [
                            {
                                "name": "Use my deck role",
                                "cardNames": ["Ponder"],
                                "addTags": ["my-combo-role"],
                            }
                        ],
                    },
                }
            ),
        )
    )
    assert edited["ok"] is True
    after = WeightedDecisionGraph(StrategyPolicy.from_mapping(edited["result"]["strategyGraph"]))
    assert after.score_priority(decision, decision.actions[0]).total - before.score_priority(
        decision, decision.actions[0]
    ).total == pytest.approx(50)
    assert after.score_priority(decision, decision.actions[1]) == before.score_priority(
        decision, decision.actions[1]
    )
    assert [event["tool"] for event in events] == ["inspect_deck", "update_agent"]
    assert path.read_bytes() == original
    assert find_agent(tmp_path, agent["id"])[1]["revision"] == 2


def test_independent_note_pages_and_untrusted_numbers(tmp_path: Path) -> None:
    path = cached(tmp_path)
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["combos"] = [{"name": f"Combo {i}"} for i in range(12)]
    snapshot["cards"][0]["manaValue"] = 10**1000
    snapshot["serverState"]["cohesion"]["findabilityLinks"]["b|a"] = 10**1000
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    result = inspect_deck(
        tmp_path, {"deck_version_id": "test-deck", "notes_offset": 5, "notes_limit": 5}
    )
    assert result["cards"][0]["manaValue"] is None
    assert result["relationships"][0]["score"] is None
    assert [note["name"] for note in result["studyNotes"]["combos"]] == [
        f"Combo {i}" for i in range(5, 10)
    ]
    assert result["notePagination"]["combos"]["nextOffset"] == 10
    assert result["notePagination"]["openingHands"]["nextOffset"] is None


def test_mastery_and_format_checked_without_changing_snapshot(tmp_path: Path) -> None:
    path = cached(tmp_path)
    agent = create_agent(tmp_path, {"name": "Pilot", "deckVersionIds": ["test-deck"]})
    args = {"deck_version_id": "not-mastered", "agent_id": agent["id"]}
    with pytest.raises(ValueError, match="not mastered"):
        inspect_deck(tmp_path, args)
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["format"] = "commander"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="format no longer matches"):
        inspect_deck(tmp_path, {**args, "deck_version_id": "test-deck"})
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_sdk_transport_keeps_bounded_unicode_utf8(tmp_path: Path) -> None:
    from deepdeck_learner.llm_gateway import _sdk_tools

    path = cached(tmp_path)
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["cards"] = [
        {"name": f"Card {i}", "typeLine": "Sorcery", "oracleText": "漢" * 4000} for i in range(50)
    ]
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    tool = next(
        t for t in _sdk_tools(tmp_path, lambda _: {}, [], "agent") if t.name == "inspect_deck"
    )
    payload = await tool.on_invoke_tool(
        None, json.dumps({"deck_version_id": "test-deck", "limit": 50})
    )
    assert json.loads(payload)["ok"] is True
    assert len(payload.encode("utf-8")) <= MAX_RESULT_BYTES


def test_subsequent_pages_can_require_the_original_revision(tmp_path: Path) -> None:
    path = cached(tmp_path)
    first = inspect_deck(tmp_path, {"deck_version_id": "test-deck", "limit": 1})
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["version"] = 2
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(ValueError, match="changed between inspection pages"):
        inspect_deck(
            tmp_path,
            {
                "deck_version_id": "test-deck",
                "offset": first["pagination"]["nextOffset"],
                "expected_deck_revision": first["deckRevision"],
            },
        )
    assert inspect_deck(tmp_path, {"deck_version_id": "test-deck"})["deckRevision"] == 2
