from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from deepdeck_learner import access, secret_store
from deepdeck_learner import app as learner_app
from deepdeck_learner.app import create_app
from deepdeck_learner.settings import NetworkSettings, save_network_settings


def local_client(app):
    return TestClient(app, client=("127.0.0.1", 52100))


def local_token(client: TestClient) -> str:
    return client.get("/api/v1/session").json()["token"]


def authorized(token: str) -> dict[str, str]:
    return {"X-DeepDeck-Token": token}


def test_health_status_and_protected_job_start(tmp_path: Path) -> None:
    client = local_client(create_app(tmp_path))
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.get("/api/v1/status").status_code == 403
    token = local_token(client)
    status = client.get("/api/v1/status", headers=authorized(token)).json()
    assert status["controller"]["ready"] is True
    assert status["hosted"]["trajectory_training"] is False
    forbidden = client.post("/api/v1/jobs", json={"kind": "training.smoke"})
    assert forbidden.status_code == 403
    invalid = client.post(
        "/api/v1/jobs",
        headers={"X-DeepDeck-Token": token},
        json={"kind": "unknown"},
    )
    assert invalid.status_code == 422


def test_catalog_routes_keep_deck_identifiers_behind_named_results(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPDECK_API_KEY", "ddl_agent_test")
    monkeypatch.setattr(
        learner_app,
        "platform_decks",
        lambda search, game_format, page: {
            "items": [{"id": "deck-version", "name": "Reanimator", "format": game_format}],
            "pagination": {"page": page},
        },
    )
    client = local_client(create_app(tmp_path))
    response = client.get(
        "/api/v1/catalog/decks?search=reanimator&format=legacy",
        headers=authorized(local_token(client)),
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Reanimator"


def test_platform_catalog_requires_account_api_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPDECK_API_KEY", raising=False)
    client = local_client(create_app(tmp_path))
    headers = authorized(local_token(client))

    assert client.get("/api/v1/catalog/decks?format=legacy", headers=headers).status_code == 401


def test_training_profile_is_validated_and_persisted(tmp_path: Path) -> None:
    client = local_client(create_app(tmp_path))
    headers = authorized(local_token(client))
    profile = {
        "model": "v11",
        "format": "commander",
        "decks": [
            {
                "id": "deck-version-1",
                "name": "Alexios Equipment",
                "version": 3,
                "format": "commander",
            }
        ],
    }

    saved = client.put("/api/v1/training-profile", headers=headers, json=profile)

    assert saved.status_code == 200
    restarted = local_client(create_app(tmp_path))
    restarted_headers = authorized(local_token(restarted))
    assert restarted.get("/api/v1/training-profile", headers=restarted_headers).json() == profile


def test_training_profile_rejects_decks_from_another_format(tmp_path: Path) -> None:
    client = local_client(create_app(tmp_path))
    headers = authorized(local_token(client))

    response = client.put(
        "/api/v1/training-profile",
        headers=headers,
        json={
            "model": "v12",
            "format": "legacy",
            "decks": [
                {
                    "id": "commander-deck",
                    "name": "Alexios",
                    "version": 1,
                    "format": "commander",
                }
            ],
        },
    )

    assert response.status_code == 422
    assert client.get("/api/v1/catalog/competitions", headers=headers).status_code == 401


def test_trusted_lan_device_opens_without_pairing(tmp_path: Path) -> None:
    application = create_app(tmp_path)
    client = TestClient(application, client=("192.168.2.44", 52101))

    assert client.get("/api/v1/status").status_code == 403
    connected = client.get("/api/v1/session")
    assert connected.status_code == 200
    token = connected.json()["token"]
    assert client.get("/api/v1/status", headers=authorized(token)).status_code == 200
    settings = client.get("/api/v1/settings", headers=authorized(token)).json()
    assert settings["access"]["role"] == "lan"


def test_host_can_use_its_own_lan_address_without_pairing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        access,
        "local_host_addresses",
        lambda: {"127.0.0.1", "192.168.2.10"},
    )
    save_network_settings(tmp_path, NetworkSettings(mode="lan", port=8765))
    client = TestClient(create_app(tmp_path), client=("192.168.2.10", 52102))

    response = client.get(
        "/api/v1/session", headers={"Origin": "http://192.168.2.10:5174"}
    )

    assert response.status_code == 200
    assert response.json()["session"]["role"] == "owner"


def test_untrusted_browser_origin_is_rejected(tmp_path: Path) -> None:
    client = local_client(create_app(tmp_path))
    response = client.get("/api/v1/session", headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403


def test_owner_can_store_key_without_returning_secret(tmp_path: Path, monkeypatch) -> None:
    class MemoryKeyring:
        value: str | None = None

        def get_password(self, service: str, account: str) -> str | None:
            return self.value

        def set_password(self, service: str, account: str, value: str) -> None:
            self.value = value

        def delete_password(self, service: str, account: str) -> None:
            self.value = None

    monkeypatch.delenv("DEEPDECK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPDECK_LEARNER_API_KEY_SOURCE", raising=False)
    memory_keyring = MemoryKeyring()
    monkeypatch.setattr(secret_store, "keyring", memory_keyring)
    monkeypatch.setattr(learner_app, "verify_account_api_key", lambda value: None)
    client = local_client(create_app(tmp_path))
    headers = authorized(local_token(client))
    value = "ddl_agent_abcdefghijklmnopqrstuvwxyz"

    response = client.put("/api/v1/settings/api-key", headers=headers, json={"api_key": value})

    assert response.status_code == 200
    assert value not in response.text
    assert value not in client.get("/api/v1/settings", headers=headers).text
    assert not (tmp_path / ".env").exists()
    assert memory_keyring.value == value
    assert secret_store.AccountSecretStore(tmp_path).status().provider == "system"


def test_ui_never_falls_back_to_writing_dotenv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPDECK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPDECK_LEARNER_API_KEY_SOURCE", raising=False)
    monkeypatch.setattr(secret_store, "keyring", None)
    monkeypatch.setattr(learner_app, "verify_account_api_key", lambda value: None)
    client = local_client(create_app(tmp_path))

    response = client.put(
        "/api/v1/settings/api-key",
        headers=authorized(local_token(client)),
        json={"api_key": "ddl_agent_abcdefghijklmnopqrstuvwxyz"},
    )

    assert response.status_code == 409
    assert not (tmp_path / ".env").exists()


def test_network_mode_is_persisted_and_requires_restart(tmp_path: Path) -> None:
    client = local_client(create_app(tmp_path))
    headers = authorized(local_token(client))

    response = client.put(
        "/api/v1/settings/network",
        headers=headers,
        json={"mode": "lan", "port": 8765},
    )

    assert response.json()["restart_required"] is True
    payload = (tmp_path / ".deepdeck" / "learner.json").read_text("utf-8")
    assert '"mode": "lan"' in payload
