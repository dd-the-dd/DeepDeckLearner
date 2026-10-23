from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from deepdeck_learner import deck_workshop, llm_gateway
from deepdeck_learner.agent_studio import create_agent, find_agent, update_agent
from deepdeck_learner.app import create_app
from deepdeck_learner.play_settings import local_play_settings


@pytest.mark.asyncio
async def test_local_api_remains_responsive_while_a_chat_tool_is_working(
    tmp_path, monkeypatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    llm_gateway.save_llm_settings(
        tmp_path, {"baseUrl": "http://127.0.0.1:11434/v1", "model": "fixture"}
    )

    def slow_tool(*_args):
        entered.set()
        release.wait(timeout=4)
        finished.set()
        return {"name": "Fixture import"}

    async def run(agent, *_args, **_kwargs):
        tool = next(tool for tool in agent.tools if tool.name == "import_league_deck")
        await tool.on_invoke_tool(None, '{"version_id":"fixture"}')
        return SimpleNamespace(final_output="Import finished.")

    monkeypatch.setattr(llm_gateway, "_execute_tool", slow_tool)
    monkeypatch.setattr(llm_gateway.Runner, "run", run)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(tmp_path)), base_url="http://testserver"
    ) as client:
        token = (await client.get("/api/v1/session")).json()["token"]
        request = asyncio.create_task(
            client.post(
                "/api/v1/agent-studio/chat",
                headers={"X-DeepDeck-Token": token},
                json={
                    "context": "deck",
                    "messages": [{"role": "user", "content": "Import this deck."}],
                },
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            response = await asyncio.wait_for(client.get("/api/v1/health"), timeout=1)
            assert response.status_code == 200 and not finished.is_set()
        finally:
            release.set()
            response = await request
        assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context,name",
    [
        ("agent", "list_agents"),
        ("agent", "start_playtest"),
        ("agent", "update_agent"),
        ("deck", "add_card"),
        ("deck", "import_league_deck"),
    ],
)
async def test_blocking_sdk_tools_and_edit_guards_do_not_run_on_the_ui_event_loop(
    tmp_path, monkeypatch, context, name
) -> None:
    loop_thread = threading.get_ident()
    guards = []

    def execute(*_args):
        assert threading.get_ident() != loop_thread
        return {"name": "Local action completed"}

    def guard(agent_id):
        assert threading.get_ident() != loop_thread
        guards.append(agent_id)

    monkeypatch.setattr(llm_gateway, "_execute_tool", execute)
    deck = deck_workshop.create_deck(
        tmp_path, {"name": "Concurrent edit fixture", "format": "legacy"}
    )
    events = []
    tool = next(
        tool
        for tool in llm_gateway._sdk_tools(tmp_path, lambda p: p, events, context, guard)
        if tool.name == name
    )
    response = json.loads(
        await tool.on_invoke_tool(
            None, json.dumps({"agent_id": "pilot", "deck_id": deck["id"], "expected_version": 1})
        )
    )
    assert response["ok"] is True
    assert guards == (["pilot"] if name == "update_agent" else [])
    assert len(events) == 1


@pytest.mark.asyncio
async def test_concurrent_chats_serialize_local_mutations_for_one_profile(
    tmp_path, monkeypatch
) -> None:
    active = 0
    maximum = 0
    count_lock = threading.Lock()

    def execute(*_args):
        nonlocal active, maximum
        with count_lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with count_lock:
            active -= 1
        return {"name": "Saved"}

    monkeypatch.setattr(llm_gateway, "_execute_tool", execute)
    deck = deck_workshop.create_deck(
        tmp_path, {"name": "Concurrent edit fixture", "format": "legacy"}
    )
    first = next(
        tool
        for tool in llm_gateway._sdk_tools(tmp_path, lambda p: p, [], "deck")
        if tool.name == "add_card"
    )
    second = next(
        tool
        for tool in llm_gateway._sdk_tools(tmp_path, lambda p: p, [], "deck")
        if tool.name == "move_card"
    )
    responses = await asyncio.gather(
        first.on_invoke_tool(None, json.dumps({"deck_id": deck["id"], "expected_version": 1})),
        second.on_invoke_tool(None, json.dumps({"deck_id": deck["id"], "expected_version": 1})),
    )
    assert all(json.loads(response)["ok"] for response in responses)
    assert maximum == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("game_format,seat_count", [("legacy", 2), ("commander", 4)])
async def test_playtest_tool_uses_the_app_engine_and_forwards_local_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, game_format: str, seat_count: int
) -> None:
    deck = deck_workshop.create_deck(tmp_path, {"name": "Test deck", "format": game_format})
    agent = create_agent(
        tmp_path, {"name": "Pilot", "format": game_format, "deckVersionIds": [deck["id"]]}
    )
    monkeypatch.setenv("DEEPDECK_LOCAL_ENGINE_URL", "http://127.0.0.1:8897/")
    events: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def launch(payload: dict[str, Any]) -> dict[str, Any]:
        # The real controller applies this same validator even when a compatible
        # provider ignores the advertised optional tool argument schema.
        settings = local_play_settings(payload, seats=len(payload["seats"]), has_human=True)
        calls.append(payload)
        return {"id": "owned-table", "settings": settings}

    tool = next(
        tool
        for tool in llm_gateway._sdk_tools(tmp_path, launch, events)
        if tool.name == "start_playtest"
    )
    base = {
        "agent_id": agent["id"],
        "player_deck_version_id": deck["id"],
        "agent_deck_version_id": "random",
    }
    response = json.loads(await tool.on_invoke_tool(None, json.dumps(base)))
    assert response["ok"] is True and calls[-1]["engine_url"] == "http://127.0.0.1:8897"
    assert len(calls[-1]["seats"]) == seat_count
    assert calls[-1]["seats"][0] == {"kind": "human", "deck_id": deck["id"]}
    assert all(
        seat["agent_id"] == agent["id"] and seat["deck_id"] == "random"
        for seat in calls[-1]["seats"][1:]
    )
    assert response["result"]["settings"]["humanDecisionTimeoutSeconds"] is None
    assert response["result"]["settings"]["matchTimeoutSeconds"] is None
    custom = {
        "seed": 42,
        "starting_seat": seat_count,
        "max_turns": 80,
        "human_decision_timeout_seconds": 180,
        "match_timeout_seconds": 1800,
    }
    response = json.loads(await tool.on_invoke_tool(None, json.dumps({**base, **custom})))
    assert response["ok"] is True
    assert all(calls[-1][key] == value for key, value in custom.items())
    rejected = json.loads(await tool.on_invoke_tool(None, json.dumps({**base, "max_turns": True})))
    assert rejected["ok"] is False and "whole number" in rejected["error"]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_chat_runs_through_agents_sdk_and_local_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_gateway.save_llm_settings(
        tmp_path,
        {
            "providerName": "Local test provider",
            "baseUrl": "http://127.0.0.1:11434/v1",
            "model": "test-model",
        },
    )
    observed: dict[str, Any] = {}

    async def fake_run(agent: Any, input: Any, **kwargs: Any) -> Any:
        observed.update({"agent": agent, "input": input, **kwargs})
        list_agents = next(tool for tool in agent.tools if tool.name == "list_agents")
        tool_result = await list_agents.on_invoke_tool(None, "{}")
        assert json.loads(tool_result)["ok"] is True
        return SimpleNamespace(final_output="Your local agents are ready.")

    monkeypatch.setattr(llm_gateway.Runner, "run", fake_run)

    result = await llm_gateway.chat(
        tmp_path,
        {
            "messages": [{"role": "user", "content": "What agents do I have?"}],
            "activeAgentId": "programmatic-123456789abc",
        },
        lambda payload: payload,
    )

    assert result["message"] == "Your local agents are ready."
    assert result["toolEvents"][0]["tool"] == "list_agents"
    assert observed["agent"].model.model == "test-model"
    assert observed["input"] == [{"role": "user", "content": "What agents do I have?"}]
    assert observed["run_config"].tracing_disabled is True
    assert "programmatic-123456789abc" in observed["agent"].instructions


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [llm_gateway.AgentsException, llm_gateway.OpenAIError])
async def test_model_failure_after_a_real_tool_save_returns_the_saved_outcome(
    tmp_path, monkeypatch, failure
) -> None:
    llm_gateway.save_llm_settings(
        tmp_path,
        {
            "baseUrl": "http://127.0.0.1:11434/v1",
            "model": "fixture",
            "apiKey": "local-fixture-secret",
        },
    )

    async def fail_after_save(agent, *_args, **_kwargs):
        tool = next(tool for tool in agent.tools if tool.name == "create_deck")
        result = json.loads(
            await tool.on_invoke_tool(
                None, '{"name":"Saved before disconnection","format":"legacy"}'
            )
        )
        assert result["ok"] is True
        raise failure("Provider disconnected: local-fixture-secret")

    monkeypatch.setattr(llm_gateway.Runner, "run", fail_after_save)
    result = await llm_gateway.chat(
        tmp_path,
        {"context": "deck", "messages": [{"role": "user", "content": "Create my local deck."}]},
        lambda p: p,
    )
    assert "Some actions completed" in result["message"]
    assert "[redacted]" in result["message"] and "local-fixture-secret" not in json.dumps(result)
    assert len(deck_workshop.list_decks(tmp_path)) == 1
    assert (
        result["toolEvents"][0]["tool"] == "create_deck" and result["toolEvents"][0]["ok"] is True
    )


@pytest.mark.asyncio
async def test_model_failure_before_mutation_does_not_claim_work_was_saved(
    tmp_path, monkeypatch
) -> None:
    llm_gateway.save_llm_settings(
        tmp_path, {"baseUrl": "http://127.0.0.1:11434/v1", "model": "fixture"}
    )

    async def fail(agent, *_args, **_kwargs):
        tool = next(tool for tool in agent.tools if tool.name == "list_agents")
        await tool.on_invoke_tool(None, "{}")
        raise llm_gateway.OpenAIError("Provider disconnected")

    monkeypatch.setattr(llm_gateway.Runner, "run", fail)
    with pytest.raises(ValueError, match="could not complete the request"):
        await llm_gateway.chat(
            tmp_path, {"messages": [{"role": "user", "content": "Inspect my agents."}]}, lambda p: p
        )


def test_llm_endpoint_is_normalized_for_agents_sdk_client(tmp_path: Path) -> None:
    settings = llm_gateway.save_llm_settings(
        tmp_path,
        {
            "providerName": "OpenAI-compatible",
            "baseUrl": "https://example.test/v1/chat/completions",
            "model": "model-name",
            "apiKey": "secret",
        },
    )

    assert llm_gateway._sdk_base_url(settings["baseUrl"]) == "https://example.test/v1"
    saved = llm_gateway.save_llm_settings(
        tmp_path, {"baseUrl": settings["baseUrl"], "model": "new-model", "leagueMcpEnabled": True}
    )
    assert saved["hasApiKey"] is True and saved["leagueMcpEnabled"] is True
    assert "secret" not in json.dumps(saved)


@pytest.mark.asyncio
async def test_agent_chat_requires_an_inspected_revision_and_preserves_newer_edits(
    tmp_path,
) -> None:
    deck = deck_workshop.create_deck(tmp_path, {"name": "Test deck", "format": "legacy"})
    policy = create_agent(tmp_path, {"name": "Test agent", "deckVersionIds": [deck["id"]]})
    update_agent(tmp_path, policy["id"], {"strategyNotes": "A newer manual idea"})
    events = []
    edit = next(
        tool
        for tool in llm_gateway._sdk_tools(tmp_path, lambda payload: payload, events)
        if tool.name == "update_agent"
    )
    definition = next(
        tool["function"]
        for tool in llm_gateway._tools()
        if tool["function"]["name"] == "update_agent"
    )
    assert "expected_revision" in definition["parameters"]["required"]
    for extra, message in [({}, "Inspect this agent"), ({"expected_revision": 1}, "changed since")]:
        result = json.loads(
            await edit.on_invoke_tool(
                None,
                json.dumps(
                    {
                        "agent_id": policy["id"],
                        "strategy_notes": "Stale copilot idea",
                        **extra,
                    }
                ),
            )
        )
        assert result["ok"] is False and message in result["error"]
        assert find_agent(tmp_path, policy["id"])[1]["strategyNotes"] == "A newer manual idea"
    saved = json.loads(
        await edit.on_invoke_tool(
            None,
            json.dumps(
                {
                    "agent_id": policy["id"],
                    "expected_revision": 2,
                    "heuristics": {"protect_advantage": 1.6},
                }
            ),
        )
    )
    assert saved["ok"] is True and saved["result"]["revision"] == 3
    assert saved["result"]["strategyNotes"] == "A newer manual idea"
    assert saved["result"]["heuristics"]["protect_advantage"] == 1.6


@pytest.mark.asyncio
async def test_real_sdk_tool_validates_declarative_nodes_before_saving(tmp_path) -> None:
    deck = deck_workshop.create_deck(tmp_path, {"name": "Test deck", "format": "legacy"})
    policy = create_agent(tmp_path, {"name": "Graph test", "deckVersionIds": [deck["id"]]})
    events = []
    edit = next(
        tool
        for tool in llm_gateway._sdk_tools(tmp_path, lambda payload: payload, events)
        if tool.name == "update_agent"
    )
    arguments = {
        "agent_id": policy["id"],
        "expected_revision": 1,
        "strategy_graph": {
            "customNodes": [
                {
                    "id": "refill",
                    "bias": 0,
                    "when": {"sourceTags": ["card_draw"], "actionKinds": ["castSpell"]},
                    "inputs": [{"from": "need.draw_cards", "weight": 1}],
                }
            ]
        },
    }
    result = json.loads(await edit.on_invoke_tool(None, json.dumps(arguments)))
    assert result["ok"] is True
    assert result["result"]["strategyGraph"]["customNodes"][0]["when"]["sourceTags"] == [
        "card_draw"
    ]
    arguments["expected_revision"] = 2
    arguments["strategy_graph"]["customNodes"][0]["inputs"] = [{"from": "node.refill", "weight": 1}]
    result = json.loads(await edit.on_invoke_tool(None, json.dumps(arguments)))
    assert result["ok"] is False and "cycle" in result["error"]
    assert find_agent(tmp_path, policy["id"])[1]["revision"] == 2
    assert len(events) == 2


def test_deck_assistant_tools_mutate_only_local_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        deck_workshop,
        "_scryfall_card",
        lambda name: {
            "id": "brainstorm-printing",
            "oracle_id": "brainstorm-oracle",
            "name": name,
            "type_line": "Instant",
            "oracle_text": "Draw three cards.",
            "mana_cost": "{U}",
            "cmc": 1,
            "colors": ["U"],
            "color_identity": ["U"],
            "keywords": [],
            "layout": "normal",
        },
    )

    def noop(payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    deck = llm_gateway._execute_tool(  # noqa: SLF001
        tmp_path,
        "create_deck",
        {"name": "Chat Tempo", "format": "legacy", "description": "Protect a threat."},
        noop,
    )
    updated = llm_gateway._execute_tool(  # noqa: SLF001
        tmp_path,
        "add_card",
        {
            "deck_id": deck["id"],
            "card_name": "Brainstorm",
            "quantity": 4,
            "section": "main",
            "tags": ["selection", "instant"],
        },
        noop,
    )

    assert updated["cards"][0]["name"] == "Brainstorm"
    assert updated["cards"][0]["quantity"] == 4
    deck_tool_names = {tool["function"]["name"] for tool in llm_gateway._tools("deck")}  # noqa: SLF001
    assert {"get_deck", "add_card", "search_league_decks"} <= deck_tool_names
    assert {
        "update_agent",
        "create_agent",
        "import_decklist",
        "update_deck_cards",
    } <= deck_tool_names


def test_move_split_merge_and_relationship_scores_reuse_metadata_offline(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        deck_workshop,
        "_scryfall_card",
        lambda name: {"id": name, "name": name, "type_line": "Instant"},
    )
    deck = deck_workshop.create_deck(
        tmp_path,
        {
            "name": "Relationships",
            "format": "legacy",
            "cards": [
                {"name": "Brainstorm", "quantity": 4, "tags": ["selection"]},
                {"name": "Ponder", "quantity": 4},
            ],
        },
    )
    monkeypatch.setattr(
        deck_workshop,
        "_scryfall_card",
        lambda name: pytest.fail("Existing card metadata should work offline"),
    )

    def invoke(name, args):
        return llm_gateway._execute_tool(
            tmp_path, name, {"deck_id": deck["id"], **args}, lambda p: p
        )

    split = invoke(
        "move_card",
        {
            "card_name": "Brainstorm",
            "from_section": "main",
            "to_section": "sideboard",
            "quantity": 1,
        },
    )
    assert [c["quantity"] for c in split["cards"] if c["name"] == "Brainstorm"] == [3, 1]
    assert len({c["entryId"] for c in split["cards"]}) == 3
    with pytest.raises(ValueError, match="several zones"):
        invoke("edit_card", {"card_name": "Brainstorm", "quantity": 2})
    merged = invoke(
        "move_card", {"card_name": "Brainstorm", "from_section": "sideboard", "to_section": "main"}
    )
    assert merged["cards"][0]["quantity"] == 4
    assert merged["cards"][0]["entryId"] == deck["cards"][0]["entryId"]
    scored = invoke(
        "set_card_relationship",
        {"left_card": "Brainstorm", "right_card": "Ponder", "dimension": "combo", "score": 7},
    )
    from deepdeck_learner.deck_sync import _payload, content_fingerprint

    key = "|".join(sorted(c["entryId"] for c in scored["cards"]))
    assert scored["serverState"]["cohesion"]["cardLinks"] == {key: 7}
    assert _payload(scored)["state"]["cohesion"]["cardLinks"] == {key: 7}
    assert {c["id"] for c in _payload(scored)["state"]["cards"]} == {
        c["entryId"] for c in scored["cards"]
    }
    assert content_fingerprint(scored) != content_fingerprint(merged)
    cleared = invoke(
        "set_card_relationship", {"left_card": "Ponder", "right_card": "Brainstorm", "score": 0}
    )
    assert cleared["serverState"]["cohesion"]["cardLinks"] == {}


def test_relationships_follow_merged_card_identity_and_disappear_on_deletion(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(deck_workshop, "_scryfall_card", lambda name: {"id": name, "name": name})
    deck = deck_workshop.create_deck(
        tmp_path,
        {
            "name": "Merge",
            "format": "legacy",
            "cards": [{"name": "Brainstorm", "quantity": 2}, {"name": "Ponder", "quantity": 4}],
        },
    )

    def invoke(name, args):
        return llm_gateway._execute_tool(
            tmp_path, name, {"deck_id": deck["id"], **args}, lambda p: p
        )

    scored = invoke(
        "set_card_relationship", {"left_card": "Brainstorm", "right_card": "Ponder", "score": 8}
    )
    invoke(
        "move_card",
        {
            "card_name": "Brainstorm",
            "from_section": "main",
            "to_section": "sideboard",
            "quantity": 1,
        },
    )
    merged = invoke(
        "move_card", {"card_name": "Brainstorm", "from_section": "main", "to_section": "sideboard"}
    )
    original_key = next(iter(scored["serverState"]["cohesion"]["cardLinks"]))
    next_key = next(iter(merged["serverState"]["cohesion"]["cardLinks"]))
    assert original_key != next_key
    assert merged["cards"][-1]["entryId"] in next_key
    deleted = invoke(
        "edit_card", {"card_name": "Brainstorm", "from_section": "sideboard", "quantity": 0}
    )
    assert deleted["serverState"]["cohesion"]["cardLinks"] == {}
