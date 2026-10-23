from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from deepdeck_examples.run import _agent_and_config, parser
from deepdeck_learner import deck_sync, deck_workshop, league_account
from deepdeck_learner.agent_studio import create_agent, list_agents
from deepdeck_learner.app import create_app
from deepdeck_learner.jobs import JobManager, JobValidationError
from deepdeck_learner.private_store import load_private_json
from deepdeck_learner.settings import load_api_key, secrets_path

USER_ID = "5f92ef46-f594-40a4-b186-a8a94e92a5ea"
VERSION_ID = "2c3f12a1-dc67-42a5-b9b9-b278c64ec79b"


class Site:
    def __init__(self) -> None:
        self.workspaces: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.offline = False
        self.unknown_create = False
        self.ai_pool = {"capacity": {"limit": 1, "used": 0}, "items": []}
        self.capacity_full = False
        self.capabilities = ["league", "decks.edit", "ai-decks"]

    def request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1/")
        self.calls.append((request.method, path))
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        if path == "auth/password/login":
            assert json.loads(request.content)["password"] == "test-password"
            return httpx.Response(
                303,
                headers={
                    "set-cookie": "ddl_session=private-session-token; HttpOnly; Path=/",
                    "location": "/account",
                },
            )
        assert "ddl_session=private-session-token" in request.headers.get("cookie", "")
        if path == "me":
            return httpx.Response(200, json={"data": {"id": USER_ID, "displayName": "Test Player"}})
        if path == "me/agent-keys":
            return httpx.Response(
                201, json={"data": {"id": "key-1", "token": "ddl_agent_test_private_token_123456"}}
            )
        if path == "auth/logout":
            return httpx.Response(204)
        if path == "runtime-config":
            return httpx.Response(200, json={"data": {"capabilities": self.capabilities}})
        if path == "me/ai-decks":
            return httpx.Response(200, json={"data": self.ai_pool})
        if path.startswith("me/ai-decks/"):
            assert json.loads(request.content) == {"active": True}
            if self.capacity_full:
                return httpx.Response(
                    409,
                    json={
                        "error": {
                            "message": "All AI deck places are occupied; choose an active deck to replace"
                        }
                    },
                )
            for item in self.ai_pool["items"]:
                if item["deckId"] == path.split("/")[-1]:
                    item["selected"] = True
            return httpx.Response(200, json={"data": self.ai_pool})
        if path == "me/deck-sessions":
            if request.method == "GET":
                return httpx.Response(200, json={"data": list(self.workspaces.values())})
            payload = json.loads(request.content)
            value = {
                **payload,
                "id": f"workspace-{len(self.workspaces) + 1}",
                "deckId": "deck-1",
                "hasUnpublishedChanges": True,
            }
            self.workspaces[value["id"]] = value
            if self.unknown_create:
                self.unknown_create = False
                raise httpx.ReadTimeout("response lost after commit", request=request)
            return httpx.Response(200, json={"data": value})
        pieces = path.split("/")
        workspace_id = pieces[2]
        value = self.workspaces[workspace_id]
        if request.method == "PUT":
            value.update(json.loads(request.content))
            value["hasUnpublishedChanges"] = True
        elif path.endswith("/publish"):
            value.update(latestVersionId=VERSION_ID, hasUnpublishedChanges=False, version=1)
        return httpx.Response(200, json={"data": value})


@pytest.fixture
def site(tmp_path: Path, monkeypatch):
    site = Site()
    original = httpx.Client
    monkeypatch.setenv("DEEPDECK_PLATFORM_URL", "https://league.test/api/v1")
    monkeypatch.setattr(
        league_account.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(site.request), **kwargs),
    )
    monkeypatch.setenv("DEEPDECK_API_KEY", "old-environment-key")
    league_account.sign_in(tmp_path, "player", "test-password")
    monkeypatch.setattr(
        deck_workshop,
        "_scryfall_card",
        lambda name: {
            "id": "island-printing",
            "oracle_id": "island-oracle",
            "name": name,
            "type_line": "Basic Land — Island",
            "color_identity": ["U"],
            "image_uris": {"normal": "https://image.test/island.jpg"},
        },
    )
    return site


def _deck(root: Path) -> dict:
    return deck_workshop.create_deck(
        root,
        {
            "name": "Local Tempo",
            "format": "legacy",
            "cards": [
                {"name": "Island", "quantity": 60, "section": "main", "tags": ["mana-source"]}
            ],
        },
    )


def test_private_session_roundtrip_and_login_never_return_credentials(
    tmp_path: Path, site: Site
) -> None:
    saved = (tmp_path / ".deepdeck" / "league-session.json").read_text(encoding="utf-8")
    if os.name == "nt":
        assert "private-session-token" not in saved
    assert load_private_json(tmp_path, "league-session.json")["user"]["id"] == USER_ID
    signed_in = league_account.sign_in(tmp_path, "player", "test-password", create_agent_key=True)
    assert signed_in["valid"] is True and signed_in["agentKeyConfigured"] is True
    assert "private-session-token" not in json.dumps(signed_in)
    assert "ddl_agent_" not in json.dumps(signed_in)
    assert "test-password" not in saved
    os.environ["DEEPDECK_API_KEY"] = "older-env-value"
    assert load_api_key(tmp_path)
    assert os.environ["DEEPDECK_API_KEY"] == "ddl_agent_test_private_token_123456"
    assert secrets_path(tmp_path).is_file()


def test_publish_preserves_local_identity_zones_tags_and_confirms_server_version(
    tmp_path: Path, site: Site
) -> None:
    deck = _deck(tmp_path)
    published = deck_sync.publish_deck(tmp_path, deck["id"])
    assert published["id"] == deck["id"]
    assert published["syncStatus"]["publishedVersionId"] == VERSION_ID
    assert published["syncStatus"]["needsPublishing"] is False
    remote = site.workspaces["workspace-1"]
    assert remote["state"]["cards"][0]["tags"] == ["mana-source"]
    assert remote["state"]["cards"][0]["isSideboard"] is False
    assert remote["state"]["deepdeckLearner"]["localDeckId"] == deck["id"]
    assert deck_sync.published_deck_version(tmp_path, deck["id"]) == VERSION_ID
    assert (tmp_path / ".deepdeck" / "decks" / f"{VERSION_ID}.json").is_file()
    changed = deck_workshop.update_deck(tmp_path, deck["id"], {"name": "Updated Tempo"})
    assert changed["syncStatus"]["needsPublishing"] is True
    with pytest.raises(ValueError, match="Publish the current"):
        deck_sync.published_deck_version(tmp_path, deck["id"])


def test_import_reuses_existing_local_identity(tmp_path: Path, site: Site) -> None:
    deck = _deck(tmp_path)
    published = deck_sync.publish_deck(tmp_path, deck["id"])
    imported = deck_sync.import_server_workspace(tmp_path, "workspace-1")
    assert imported["id"] == published["id"]
    assert len(deck_workshop.list_decks(tmp_path)) == 1
    assert imported["syncStatus"]["needsPublishing"] is False


def test_forking_a_published_snapshot_never_reuses_its_site_draft_binding(
    tmp_path: Path, site: Site
) -> None:
    deck = _deck(tmp_path)
    deck_sync.publish_deck(tmp_path, deck["id"])
    fork = deck_workshop.fork_downloaded_deck(tmp_path, VERSION_ID)
    assert fork["id"] != deck["id"]
    assert fork["syncStatus"]["linked"] is False
    assert "sync" not in fork
    synced = deck_sync.sync_deck(tmp_path, fork["id"])
    assert synced["sync"]["workspaceId"] != "workspace-1"
    assert len(site.workspaces) == 2


def test_deployment_maps_all_published_decks_without_modifying_local_agent(
    tmp_path: Path, site: Site
) -> None:
    deck = _deck(tmp_path)
    policy = create_agent(
        tmp_path, {"name": "Tempo", "format": "legacy", "deckVersionIds": [deck["id"]]}
    )
    directory = tmp_path / ".deepdeck" / "agents" / policy["id"]
    original = (directory / "agent.json").read_bytes()
    source = (directory / "agent.py").read_bytes()
    manager = JobManager(tmp_path)
    payload = {
        "model_id": policy["id"],
        "competition_version_id": "competition-1",
        "deck_version_ids": [deck["id"]],
        "continuous": True,
    }
    with pytest.raises(JobValidationError, match="Publish the current"):
        manager._matchmaking_command(payload)
    deck_sync.publish_deck(tmp_path, deck["id"])
    argv, _, _ = manager._matchmaking_command(payload)
    assert argv[argv.index("--deck-version-id") + 1] == VERSION_ID
    mapping_path = Path(argv[argv.index("--league-deck-id-map") + 1])
    assert json.loads(mapping_path.read_text()) == {deck["id"]: VERSION_ID}
    runtime_agent, config = _agent_and_config(parser().parse_args(argv[3:]))
    assert runtime_agent is not None
    assert config.decks.deck_ids == (VERSION_ID,)
    assert (directory / "agent.json").read_bytes() == original
    assert (directory / "agent.py").read_bytes() == source
    assert list_agents(tmp_path)[0]["decks"][0]["syncStatus"]["needsPublishing"] is False
    deck_workshop.update_deck(tmp_path, deck["id"], {"name": "Updated"})
    assert list_agents(tmp_path)[0]["decks"][0]["name"] == "Updated"
    assert list_agents(tmp_path)[0]["decks"][0]["syncStatus"]["needsPublishing"] is True


def test_prepare_league_activates_owned_selected_decks_but_never_replaces_slots(
    tmp_path: Path, site: Site
) -> None:
    deck = _deck(tmp_path)
    deck_sync.publish_deck(tmp_path, deck["id"])
    site.ai_pool["items"] = [{"deckId": "deck-1", "deckVersionId": VERSION_ID, "selected": False}]
    prepared = deck_sync.prepare_league_decks(tmp_path, [deck["id"]])
    assert prepared["deckVersionIds"] == [VERSION_ID]
    assert site.ai_pool["items"][0]["selected"] is True
    site.capacity_full = True
    site.ai_pool["items"][0]["selected"] = False
    with pytest.raises(league_account.LeagueAccountError, match="places are occupied"):
        deck_sync.prepare_league_decks(tmp_path, [deck["id"]])
    assert site.ai_pool["items"][0]["selected"] is False


def test_production_deployment_does_not_require_a_staging_only_ai_slots_api(
    tmp_path: Path, site: Site
) -> None:
    deck = _deck(tmp_path)
    deck_sync.publish_deck(tmp_path, deck["id"])
    site.capabilities = ["league", "decks.edit", "mcp"]
    prepared = deck_sync.prepare_league_decks(tmp_path, [deck["id"]])
    assert prepared["deckVersionIds"] == [VERSION_ID]
    assert prepared["slotManagementAvailable"] is False
    assert all(not path.startswith("me/ai-decks") for _, path in site.calls)


def test_sync_detects_site_conflict_and_keeps_both_versions_on_forced_replace(
    tmp_path: Path, site: Site
) -> None:
    deck = _deck(tmp_path)
    deck_sync.sync_deck(tmp_path, deck["id"])
    site.workspaces["workspace-1"]["name"] = "Changed on site"
    before = deepcopy(site.workspaces["workspace-1"])
    with pytest.raises(deck_sync.DeckSyncConflict, match="site draft changed"):
        deck_sync.sync_deck(tmp_path, deck["id"])
    assert site.workspaces["workspace-1"] == before
    deck_sync.sync_deck(tmp_path, deck["id"], force=True)
    assert site.workspaces["workspace-1"]["name"] == "Local Tempo"
    backup = next((tmp_path / ".deepdeck" / "deck-workspaces" / "remote-backups").rglob("*.json"))
    assert json.loads(backup.read_text())["name"] == "Changed on site"


def test_lost_create_response_is_recovered_without_duplicate_site_decks(
    tmp_path: Path, site: Site
) -> None:
    deck = _deck(tmp_path)
    site.unknown_create = True
    with pytest.raises(league_account.LeagueAccountError):
        deck_sync.sync_deck(tmp_path, deck["id"])
    assert len(site.workspaces) == 1
    recovered = deck_sync.sync_deck(tmp_path, deck["id"])
    assert recovered["sync"]["workspaceId"] == "workspace-1"
    assert len(site.workspaces) == 1


def test_pull_protects_local_changes_and_preserves_site_cohesion(
    tmp_path: Path, site: Site
) -> None:
    deck = _deck(tmp_path)
    deck_sync.publish_deck(tmp_path, deck["id"])
    site.workspaces["workspace-1"]["state"]["cohesion"] = {
        "strategies": [{"id": "tempo", "label": "Tempo"}]
    }
    deck_workshop.update_deck(tmp_path, deck["id"], {"name": "Unsent local change"})
    with pytest.raises(deck_sync.DeckSyncConflict, match="local changes"):
        deck_sync.import_server_workspace(tmp_path, "workspace-1", deck_id=deck["id"])
    pulled = deck_sync.import_server_workspace(
        tmp_path, "workspace-1", deck_id=deck["id"], discard_local=True
    )
    assert pulled["serverState"]["cohesion"]["strategies"][0]["id"] == "tempo"
    assert list(
        (tmp_path / ".deepdeck" / "deck-workspaces" / "revisions" / deck["id"]).glob("*.json")
    )


def test_offline_sync_retains_local_data_and_account_session(tmp_path: Path, site: Site) -> None:
    deck = _deck(tmp_path)
    site.offline = True
    with pytest.raises(league_account.LeagueAccountError, match="unavailable"):
        deck_sync.sync_deck(tmp_path, deck["id"])
    assert deck_workshop.find_deck(tmp_path, deck["id"])["name"] == "Local Tempo"
    assert league_account.account_status(tmp_path)["valid"] is None
    assert load_private_json(tmp_path, "league-session.json")


def test_session_is_not_forwarded_to_another_server(
    tmp_path: Path, site: Site, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPDECK_PLATFORM_URL", "https://different.test/api/v1")
    before = len(site.calls)
    assert league_account.account_status(tmp_path)["valid"] is False
    assert len(site.calls) == before


def test_sync_and_sign_in_routes_require_the_local_session_token(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("DEEPDECK_API_KEY", raising=False)
    client = TestClient(create_app(tmp_path))
    assert (
        client.post(
            "/api/v1/league-account/login", json={"identity": "x", "password": "x"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/deck-workshop/decks/localdeck-123456789abc/publish", json={}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/league-account").json()["configured"] is False
    assert (
        client.post("/api/v1/league-decks/prepare", json={"deckVersionIds": ["one"]}).status_code
        == 403
    )
