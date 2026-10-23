from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from deepdeck_examples.run import _agent_and_config, parser
from deepdeck_learner.agent_studio import (
    AgentRevisionConflict,
    agent_revisions,
    create_agent,
    find_agent,
    heuristic_catalog,
    restore_agent_revision,
    update_agent,
)
from deepdeck_learner.app import create_app
from deepdeck_learner.jobs import Job, JobManager
from deepdeck_learner.llm_gateway import llm_settings, save_llm_settings
from deepdeck_learner.models import local_models
from deepdeck_learner.settings import save_api_key, secrets_path


def _deck(root, version_id: str = "deck-v1") -> None:
    path = root / ".deepdeck" / "decks" / f"{version_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": version_id,
                "name": "Tempo Test",
                "version": 1,
                "format": "legacy",
                "colors": ["U"],
                "playableCardCount": 60,
                "cards": [
                    {
                        "id": "island",
                        "name": "Island",
                        "typeLine": "Basic Land — Island",
                        "section": "main",
                        "quantity": 60,
                        "rules": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_programmatic_agent_is_private_playable_model(tmp_path) -> None:
    _deck(tmp_path)
    policy = create_agent(
        tmp_path,
        {
            "name": "Tempo Pilot",
            "format": "legacy",
            "profile": "balanced",
            "deckVersionIds": ["deck-v1"],
        },
    )

    directory = tmp_path / ".deepdeck" / "agents" / policy["id"]
    assert (directory / "agent.json").is_file()
    assert (directory / "revisions" / "revision-0001.json").is_file()
    models = local_models(tmp_path, [])
    assert len(models) == 1
    assert models[0]["source"] == "local-programmatic-agent"
    assert models[0]["ready"] is True

    arguments = parser().parse_args(["programmatic", "--agent-config", str(directory)])
    _, config = _agent_and_config(arguments)
    assert config.name == "Tempo Pilot"
    assert config.decks.deck_ids == ("deck-v1",)


def test_python_agent_is_executable_preserved_and_versioned_with_policy_updates(tmp_path) -> None:
    _deck(tmp_path)
    policy = create_agent(
        tmp_path, {"name": "Python Pilot", "format": "legacy", "deckVersionIds": ["deck-v1"]}
    )
    directory = tmp_path / ".deepdeck" / "agents" / policy["id"]
    source = directory / "agent.py"
    customized = source.read_text(encoding="utf-8").replace(
        'policy.get("profile", "balanced")', '"cautious"'
    )
    source.write_text(customized, encoding="utf-8")
    update_agent(tmp_path, policy["id"], {"strategyNotes": "Policy edit preserves local Python."})
    assert source.read_text(encoding="utf-8") == customized
    assert (directory / "revisions" / "revision-0002.py").read_text(encoding="utf-8") == customized
    agent, _ = _agent_and_config(
        parser().parse_args(["programmatic", "--agent-config", str(directory)])
    )
    assert agent.profile.value == "cautious"


def test_programmatic_agent_updates_only_bounded_policy(tmp_path) -> None:
    _deck(tmp_path)
    policy = create_agent(
        tmp_path,
        {"name": "Control Pilot", "format": "legacy", "deckVersionIds": ["deck-v1"]},
    )
    updated = update_agent(
        tmp_path,
        policy["id"],
        {
            "heuristics": {"interaction": 1.8, "combat_pressure": 0.3},
            "cardPreferences": {
                "prefer": ["Force of Will"],
                "avoid": [],
                "holdForInteraction": ["Force of Will"],
            },
        },
    )

    assert updated["revision"] == 2
    assert updated["heuristics"]["interaction"] == 1.8
    assert updated["heuristics"]["card_value"] == 1.0
    assert updated["cardPreferences"]["holdForInteraction"] == ["Force of Will"]
    assert {item["id"] for item in heuristic_catalog()["items"]} == set(updated["heuristics"])
    assert updated["strategyGraph"]["weights"] == updated["heuristics"]
    directory = tmp_path / ".deepdeck" / "agents" / policy["id"]
    assert (directory / "revisions" / "revision-0001.json").is_file()
    assert (directory / "revisions" / "revision-0002.json").is_file()


def test_strategy_restore_creates_a_new_revision_and_preserves_current_python(tmp_path) -> None:
    _deck(tmp_path)
    original = create_agent(
        tmp_path, {"name": "Versioned Pilot", "format": "legacy", "deckVersionIds": ["deck-v1"]}
    )
    directory, _ = find_agent(tmp_path, original["id"])
    source = directory / "agent.py"
    customized = (
        source.read_text(encoding="utf-8") + "\n# User-owned customization, do not replace.\n"
    )
    source.write_text(customized, encoding="utf-8")
    updated = update_agent(
        tmp_path, original["id"], {"heuristics": {"interaction": 1.9}, "expectedRevision": 1}
    )
    restored = restore_agent_revision(tmp_path, original["id"], 1, updated["revision"])
    assert restored["revision"] == 3 and restored["heuristics"] == original["heuristics"]
    assert source.read_text(encoding="utf-8") == customized
    history = agent_revisions(tmp_path, original["id"])
    assert [item["revision"] for item in history] == [3, 2, 1]
    assert [item["current"] for item in history] == [True, False, False]
    assert all(item["hasPythonSnapshot"] for item in history)
    assert "revision 1" in history[0]["reason"]


def test_concurrent_edit_conflict_leaves_policy_and_history_unchanged(tmp_path) -> None:
    _deck(tmp_path)
    original = create_agent(
        tmp_path, {"name": "Conflict Pilot", "format": "legacy", "deckVersionIds": ["deck-v1"]}
    )
    update_agent(tmp_path, original["id"], {"strategyNotes": "A newer edit", "expectedRevision": 1})
    with pytest.raises(AgentRevisionConflict, match="changed since"):
        update_agent(
            tmp_path, original["id"], {"strategyNotes": "Stale browser edit", "expectedRevision": 1}
        )
    with pytest.raises(AgentRevisionConflict):
        restore_agent_revision(tmp_path, original["id"], 1, 1)
    assert find_agent(tmp_path, original["id"])[1]["strategyNotes"] == "A newer edit"
    assert len(agent_revisions(tmp_path, original["id"])) == 2


def test_two_simultaneous_revision_checked_writes_cannot_replace_each_other(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    _deck(tmp_path)
    original = create_agent(
        tmp_path, {"name": "Parallel Pilot", "format": "legacy", "deckVersionIds": ["deck-v1"]}
    )

    def change(note):
        try:
            return update_agent(
                tmp_path, original["id"], {"strategyNotes": note, "expectedRevision": 1}
            )["revision"]
        except AgentRevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(change, ["Browser one", "Browser two"]))
    assert results.count(2) == 1 and results.count("conflict") == 1
    assert len(agent_revisions(tmp_path, original["id"])) == 2


def test_revision_routes_require_session_and_report_conflicts(tmp_path) -> None:
    _deck(tmp_path)
    policy = create_agent(
        tmp_path, {"name": "API Versioning", "format": "legacy", "deckVersionIds": ["deck-v1"]}
    )
    client = TestClient(create_app(tmp_path))
    url = f"/api/v1/agent-studio/agents/{policy['id']}"
    headers = {"X-DeepDeck-Token": client.get("/api/v1/session").json()["token"]}
    assert (
        client.post(url + "/revisions/1/restore", json={"expectedRevision": 1}).status_code == 403
    )
    assert (
        client.put(
            url, headers=headers, json={"expectedRevision": 1, "strategyNotes": "New"}
        ).status_code
        == 200
    )
    assert (
        client.put(
            url, headers=headers, json={"expectedRevision": 1, "strategyNotes": "Old"}
        ).status_code
        == 409
    )
    assert client.post(url + "/revisions/1/restore", headers=headers, json={}).status_code == 422
    restored = client.post(
        url + "/revisions/1/restore", headers=headers, json={"expectedRevision": 2}
    )
    assert restored.status_code == 200 and restored.json()["revision"] == 3
    assert len(client.get(url + "/revisions").json()["items"]) == 3


def test_manual_history_and_sdk_chat_share_active_seat_edit_guard(tmp_path, monkeypatch) -> None:
    from deepdeck_learner import llm_gateway

    _deck(tmp_path)
    policy = create_agent(tmp_path, {"name": "Opponent seat", "deckVersionIds": ["deck-v1"]})
    app = create_app(tmp_path)
    manager = app.state.manager
    queued = Job(
        id="queued-table",
        kind="playtest.table",
        label="Two agents",
        argv=[],
        model_id="some-other-agent",
        details={"seats": [{"agentId": policy["id"]}]},
    )
    manager._jobs[queued.id] = queued  # noqa: SLF001
    monkeypatch.setattr(manager, "resources", lambda: {"workers": []})
    save_llm_settings(tmp_path, {"baseUrl": "http://127.0.0.1:11434/v1", "model": "fixture"})

    async def invoke_edit(agent, *_args, **_kwargs):
        edit = next(tool for tool in agent.tools if tool.name == "update_agent")
        await edit.on_invoke_tool(
            None,
            json.dumps(
                {
                    "agent_id": policy["id"],
                    "expected_revision": 1,
                    "strategy_notes": "Copilot edit",
                }
            ),
        )
        return SimpleNamespace(final_output="Tool result handled.")

    monkeypatch.setattr(llm_gateway.Runner, "run", invoke_edit)
    client = TestClient(app)
    headers = {"X-DeepDeck-Token": client.get("/api/v1/session").json()["token"]}
    url = f"/api/v1/agent-studio/agents/{policy['id']}"
    assert (
        client.put(url, headers=headers, json={"strategyNotes": "Manual edit"}).status_code == 409
    )
    assert (
        client.post(
            url + "/revisions/1/restore", headers=headers, json={"expectedRevision": 1}
        ).status_code
        == 409
    )
    blocked = client.post(
        "/api/v1/agent-studio/chat",
        headers=headers,
        json={"messages": [{"role": "user", "content": "Tune my agent"}]},
    )
    assert blocked.status_code == 200
    event = blocked.json()["toolEvents"][0]
    assert event["tool"] == "update_agent" and event["ok"] is False
    assert "Stop this agent" in event["error"]
    assert find_agent(tmp_path, policy["id"])[1]["revision"] == 1
    queued.status = "completed"
    allowed = client.post(
        "/api/v1/agent-studio/chat",
        headers=headers,
        json={"messages": [{"role": "user", "content": "Tune my agent"}]},
    )
    assert allowed.status_code == 200 and allowed.json()["toolEvents"][0]["ok"] is True
    assert find_agent(tmp_path, policy["id"])[1]["revision"] == 2


def test_strategy_graph_accepts_bounded_llm_customization(tmp_path) -> None:
    _deck(tmp_path)
    policy = create_agent(
        tmp_path,
        {
            "name": "Combo Pilot",
            "format": "legacy",
            "deckVersionIds": ["deck-v1"],
            "strategyGraph": {
                "archetypeMix": {"combo": 0.8, "control": 0.2},
                "deckAdaptation": 0.8,
                "weights": {"combo_progress": 1.7, "combat_pressure": 0.4},
                "temperature": 0.25,
                "seed": 42,
                "patterns": [
                    {
                        "id": "protect-engine",
                        "cardNames": ["Aluren"],
                        "addTags": ["combo_enabler"],
                        "nodeAdjustments": {"protect_advantage": 1.2},
                    }
                ],
            },
        },
    )

    graph = policy["strategyGraph"]
    assert graph["version"] == "strategy-graph/v1"
    assert graph["temperature"] == 0.25
    assert graph["deckAdaptation"] == 0.8
    assert graph["archetypeMix"]["combo"] == 0.8
    assert graph["weights"]["combo_progress"] == 1.7
    assert graph["patterns"][0]["addTags"] == ["combo_enabler"]


def test_strategy_graph_rejects_unbounded_or_unknown_llm_changes(tmp_path) -> None:
    _deck(tmp_path)
    base = {
        "name": "Safe Pilot",
        "format": "legacy",
        "deckVersionIds": ["deck-v1"],
    }

    with pytest.raises(ValueError, match="between 0 and 2"):
        create_agent(tmp_path, {**base, "heuristics": {"interaction": 3.0}})
    with pytest.raises(ValueError, match="Unknown pattern nodes"):
        create_agent(
            tmp_path,
            {
                **base,
                "strategyGraph": {
                    "patterns": [{"id": "unsafe", "nodeAdjustments": {"execute_python": 1.0}}]
                },
            },
        )


def test_changing_posture_preserves_combo_identity_and_explicit_weights(tmp_path) -> None:
    _deck(tmp_path)
    policy = create_agent(
        tmp_path,
        {
            "name": "Preset Pilot",
            "format": "legacy",
            "deckVersionIds": ["deck-v1"],
            "strategyGraph": {"archetypeMix": {"combo": 1}},
            "heuristics": {"interaction": 1.7},
        },
    )

    updated = update_agent(
        tmp_path, policy["id"], {"profile": "cautious", "heuristics": {"combat_survival": 1.8}}
    )

    assert updated["strategyGraph"]["archetypeMix"] == policy["strategyGraph"]["archetypeMix"]
    assert updated["strategyGraph"]["archetypeMix"]["combo"] == 1
    assert updated["heuristics"]["interaction"] == 1.7
    assert updated["heuristics"]["protect_advantage"] == 1.4
    assert updated["heuristics"]["timing_patience"] == 1.3
    assert updated["heuristics"]["combat_survival"] == 1.8
    assert heuristic_catalog()["postureWeights"]["cautious"]["protect_advantage"] == 1.4


def test_llm_settings_do_not_replace_league_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPDECK_API_KEY", "test-placeholder")
    monkeypatch.delenv("DEEPDECK_LLM_API_KEY", raising=False)
    league_key = "ddl_agent_123456789012345678901234"
    save_api_key(tmp_path, league_key)
    saved = save_llm_settings(
        tmp_path,
        {
            "providerName": "Local Ollama",
            "baseUrl": "http://localhost:11434/v1/",
            "model": "qwen3:8b",
            "apiKey": "ollama",
        },
    )

    expected = {
        "providerName": "Local Ollama",
        "baseUrl": "http://localhost:11434/v1",
        "model": "qwen3:8b",
        "hasApiKey": True,
        "configured": True,
        "leagueMcpEnabled": False,
    }
    assert {key: saved[key] for key in expected} == expected
    assert saved["authMode"] == "apiKey" and saved["providers"]
    assert llm_settings(tmp_path)["configured"] is True
    secrets = json.loads(secrets_path(tmp_path).read_text(encoding="utf-8"))
    assert secrets["api_key"] == league_key
    assert "llm_api_key" not in secrets
    from deepdeck_learner.protected_credentials import read_credentials

    assert (
        read_credentials(tmp_path / ".deepdeck" / "llm-api-credentials.bin")["keys"][
            "http://localhost:11434/v1"
        ]
        == "ollama"
    )


def test_agent_studio_http_api_works_without_an_llm(tmp_path) -> None:
    _deck(tmp_path)
    client = TestClient(create_app(tmp_path))
    token = client.get("/api/v1/session").json()["token"]
    response = client.post(
        "/api/v1/agent-studio/agents",
        headers={"X-DeepDeck-Token": token},
        json={
            "name": "API Pilot",
            "format": "legacy",
            "deckVersionIds": ["deck-v1"],
        },
    )

    assert response.status_code == 201
    assert client.get("/api/v1/agent-studio/agents").json()["items"][0]["name"] == "API Pilot"
    assert client.get("/api/v1/models").json()["items"][0]["ready"] is True
    assert client.get("/api/v1/agent-studio/llm-settings").json()["configured"] is False


def test_programmatic_agent_uses_existing_playtest_runtime(tmp_path, monkeypatch) -> None:
    _deck(tmp_path)
    _deck(tmp_path, "player-deck")
    policy = create_agent(
        tmp_path,
        {"name": "Playtest Pilot", "format": "legacy", "deckVersionIds": ["deck-v1"]},
    )
    directory = tmp_path / ".deepdeck" / "agents" / policy["id"]
    card = {
        "id": "card-1",
        "name": "Island",
        "typeLine": "Basic Land — Island",
        "sourceSessionId": "deck-v1",
        "rules": [],
    }
    (directory / "training-decks.json").write_text(
        json.dumps({"Tempo Test · v1 · deck-v1": [card]}),
        encoding="utf-8",
    )
    manager = JobManager(tmp_path)
    monkeypatch.setattr(
        manager,
        "_refresh_playtest_deck_rules",
        lambda _engine_url, _version_id, cards: cards,
    )

    payload = {
        "model_id": policy["id"],
        "format": "legacy",
        "deck_version_id": "player-deck",
        "opponent_deck_version_id": "deck-v1",
        "engine_url": "http://127.0.0.1:8787",
    }
    argv, label, _ = manager._playtest_command(payload)  # noqa: SLF001

    assert "programmatic" in argv
    assert argv[argv.index("--agent-config") + 1] == str(directory.resolve())
    assert payload["_job_details"]["playerDeck"]["id"] == "player-deck"
    assert label == "Playtest Pilot · local legacy"
