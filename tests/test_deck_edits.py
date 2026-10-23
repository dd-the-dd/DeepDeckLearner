from __future__ import annotations

import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deepdeck_learner.app import create_app
from deepdeck_learner.deck_edits import DeckRevisionConflict
from deepdeck_learner.deck_workshop import create_deck, find_deck, update_deck
from deepdeck_learner.llm_gateway import _sdk_tools, _tools
from deepdeck_learner.replay_lock import replay_storage_lock


def writer(root, deck_id, started, finished, output):
    started.set()
    try:
        result = update_deck(Path(root), deck_id, {"expectedVersion": 1, "name": "Worker edit"})
        output.put(result["version"])
    except DeckRevisionConflict:
        output.put("conflict")
    finally:
        finished.set()


def test_api_rejects_stale_revision_without_changing_cards_or_snapshot(tmp_path):
    client = TestClient(create_app(tmp_path))
    token = {"X-DeepDeck-Token": client.get("/api/v1/session").json()["token"]}
    deck = create_deck(tmp_path, {"name": "Original", "format": "legacy"})
    url = f"/api/v1/deck-workshop/decks/{deck['id']}"
    assert (
        client.put(
            url, json={"expectedVersion": 1, "name": "Changed elsewhere"}, headers=token
        ).status_code
        == 200
    )
    snapshot = tmp_path / ".deepdeck" / "decks" / f"{deck['id']}.json"
    before = snapshot.read_bytes()
    result = client.put(
        url, json={"expectedVersion": 1, "name": "Stale", "cards": []}, headers=token
    )
    assert result.status_code == 409 and "local deck changed" in result.json()["detail"]
    assert find_deck(tmp_path, deck["id"])["name"] == "Changed elsewhere"
    assert snapshot.read_bytes() == before


@pytest.mark.parametrize("version", [True, False, None, 0, -1, 1.0, "1"])
def test_invalid_expected_versions_never_mutate(tmp_path, version):
    deck = create_deck(tmp_path, {"name": "Original", "format": "legacy"})
    with pytest.raises(ValueError):
        update_deck(tmp_path, deck["id"], {"name": "Invalid", "expectedVersion": version})
    assert find_deck(tmp_path, deck["id"])["version"] == 1


def test_concurrent_same_version_writers_have_one_winner(tmp_path):
    deck = create_deck(tmp_path, {"name": "Original", "format": "legacy"})

    def edit(name):
        try:
            return update_deck(tmp_path, deck["id"], {"expectedVersion": 1, "name": name})[
                "version"
            ]
        except DeckRevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(edit, ["First", "Second"]))
    assert sorted(map(str, results)) == ["2", "conflict"]
    assert find_deck(tmp_path, deck["id"])["version"] == 2


@pytest.mark.parametrize("action", ["sync", "publish", "pull"])
def test_stale_sync_revision_is_refused_before_account_or_network_access(tmp_path, action):
    from deepdeck_learner.deck_workshop import store_workspace

    client = TestClient(create_app(tmp_path))
    token = {"X-DeepDeck-Token": client.get("/api/v1/session").json()["token"]}
    deck = create_deck(tmp_path, {"name": "Original", "format": "legacy"})
    deck["sync"] = {"workspaceId": "11111111-1111-4111-8111-111111111111"}
    store_workspace(tmp_path, deck)
    update_deck(tmp_path, deck["id"], {"expectedVersion": 1, "name": "New local content"})
    response = client.post(
        f"/api/v1/deck-workshop/decks/{deck['id']}/{action}",
        headers=token,
        json={"expectedVersion": 1, "replaceLocalDraft": True, "replaceSiteDraft": True},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "local_deck_revision_conflict"
    assert find_deck(tmp_path, deck["id"])["name"] == "New local content"


def test_cross_process_deck_transaction_waits_then_rechecks_version(tmp_path):
    deck = create_deck(tmp_path, {"name": "Original", "format": "legacy"})
    ctx = multiprocessing.get_context("spawn")
    started, finished = ctx.Event(), ctx.Event()
    output = ctx.Queue()
    process = ctx.Process(
        target=writer, args=(str(tmp_path), deck["id"], started, finished, output)
    )
    try:
        with replay_storage_lock(tmp_path, namespace="decks"):
            process.start()
            assert started.wait(20)
            assert not finished.wait(0.15)
            update_deck(tmp_path, deck["id"], {"expectedVersion": 1, "name": "Controller edit"})
        assert finished.wait(20)
        process.join(10)
        assert process.exitcode == 0
        assert output.get(timeout=2) == "conflict"
        assert find_deck(tmp_path, deck["id"])["name"] == "Controller edit"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
        output.close()


@pytest.mark.asyncio
async def test_sdk_requires_fresh_inspected_version_for_each_deck_mutation(tmp_path):
    deck = create_deck(tmp_path, {"name": "Original", "format": "legacy"})
    events = []
    tool = next(
        t
        for t in _sdk_tools(tmp_path, lambda _: {}, events, "deck")
        if t.name == "update_deck_plan"
    )
    for version, message in [(None, "Inspect this deck"), (2, "local deck changed")]:
        args = {"deck_id": deck["id"], "name": "Rejected"}
        if version is not None:
            args["expected_version"] = version
        result = json.loads(await tool.on_invoke_tool(None, json.dumps(args)))
        assert not result["ok"] and message in result["error"]
    result = json.loads(
        await tool.on_invoke_tool(
            None, json.dumps({"deck_id": deck["id"], "name": "Intentional", "expected_version": 1})
        )
    )
    assert result["ok"] and result["result"]["version"] == 2
    stale = json.loads(
        await tool.on_invoke_tool(
            None, json.dumps({"deck_id": deck["id"], "description": "Stale", "expected_version": 1})
        )
    )
    assert not stale["ok"]
    assert find_deck(tmp_path, deck["id"])["name"] == "Intentional"
    assert find_deck(tmp_path, deck["id"])["description"] == ""
    for definition in _tools("deck"):
        if definition["function"]["name"] in {
            "add_card",
            "edit_card",
            "move_card",
            "set_card_relationship",
            "update_deck_plan",
        }:
            assert "expected_version" in definition["function"]["parameters"]["required"]
