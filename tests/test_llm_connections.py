from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from deepdeck_learner import chatgpt_auth as auth
from deepdeck_learner import llm_gateway as gateway
from deepdeck_learner.app import create_app
from deepdeck_learner.protected_credentials import read_credentials, write_credentials


@pytest.fixture
def sign_in(tmp_path, monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    urls = []
    monkeypatch.setattr(auth.webbrowser, "open", lambda url: urls.append(url) or True)
    monkeypatch.setattr(
        auth,
        "_discovery",
        lambda: {
            "issuer": auth.ISSUER,
            "jwks_uri": auth.ISSUER + "/jwks",
            "revocation_endpoint": auth.ISSUER + "/revoke",
        },
    )
    monkeypatch.setattr(
        jwt,
        "PyJWKClient",
        lambda *args, **kwargs: SimpleNamespace(
            get_signing_key_from_jwt=lambda encoded: SimpleNamespace(key=private.public_key())
        ),
    )

    def complete(*, subject="person", account_id=None, nonce_override=None):
        flow = auth.start(tmp_path, account_id)
        params = parse_qs(urlparse(urls[-1]).query)
        issued = params["client_id"][0]
        if issued == "dynamic_agent_client":
            issued = "oaiapp_test_registration"
        encoded = jwt.encode(
            {
                "iss": auth.ISSUER,
                "aud": issued,
                "sub": subject,
                "nonce": nonce_override or params["nonce"][0],
                "exp": time.time() + 300,
                "email": "test@example.test",
            },
            private,
            algorithm="RS256",
        )
        result = {
            "access_token": "private-access",
            "refresh_token": "private-refresh",
            "id_token": encoded,
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": auth.PLAN_SCOPE + " offline_access",
        }
        monkeypatch.setattr(auth, "_post_token", lambda values: result)
        callback = (
            params["redirect_uri"][0]
            + "?"
            + urlencode({"state": params["state"][0], "client_id": issued, "code": "private-code"})
        )
        response = httpx.get(callback, timeout=10)
        assert response.status_code == 200
        return flow, params

    yield complete, urls
    for flow_id, flow in list(auth._flows.items()):
        if flow["root"] == tmp_path.resolve() and flow["status"] == "pending":
            auth.cancel(tmp_path, flow_id)


def test_real_loopback_sign_in_validates_identity_and_reuses_registration(tmp_path, sign_in):
    complete, _urls = sign_in
    flow, first = complete()
    assert auth.flow_status(tmp_path, flow["flowId"])["status"] == "completed"
    public = auth.status(tmp_path)
    assert len(public["accounts"]) == 1
    assert "private-" not in json.dumps(public)
    assert auth.access_token(tmp_path) == "private-access"
    account_id = public["activeAccountId"]
    _, second = complete(account_id=account_id)
    assert second["client_id"] == ["oaiapp_test_registration"]
    assert first["ext_agent_host_id"] == second["ext_agent_host_id"]
    assert "agent_name_hint" not in second
    assert "id_token_hint" in second
    assert len(auth.status(tmp_path)["accounts"]) == 1


@pytest.mark.parametrize("wrong", ["nonce", "subject"])
def test_bad_identity_does_not_replace_previous_connection(tmp_path, sign_in, wrong):
    complete, _urls = sign_in
    complete()
    before = read_credentials(auth._path(tmp_path))
    account_id = auth.status(tmp_path)["activeAccountId"]
    flow, _params = complete(
        account_id=account_id,
        subject="intruder" if wrong == "subject" else "person",
        nonce_override="wrong" if wrong == "nonce" else None,
    )
    assert auth.flow_status(tmp_path, flow["flowId"])["status"] == "failed"
    assert read_credentials(auth._path(tmp_path)) == before


def test_wrong_state_does_not_consume_attempt_and_cancel_closes_listener(tmp_path, sign_in):
    _complete, urls = sign_in
    flow = auth.start(tmp_path)
    params = parse_qs(urlparse(urls[-1]).query)
    assert httpx.get(params["redirect_uri"][0] + "?state=wrong&code=secret").status_code == 400
    assert auth.flow_status(tmp_path, flow["flowId"])["status"] == "pending"
    with pytest.raises(ValueError, match="already open"):
        auth.start(tmp_path)
    assert auth.cancel(tmp_path, flow["flowId"])["status"] == "cancelled"
    with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
        httpx.get(params["redirect_uri"][0], timeout=1)


def test_rotating_refresh_serializes_concurrent_requests(tmp_path, sign_in, monkeypatch):
    sign_in[0]()
    data = read_credentials(auth._path(tmp_path))
    data["accounts"][data["activeAccountId"]]["expires_at"] = time.time() - 1
    write_credentials(auth._path(tmp_path), data)
    calls = []

    def refresh(values):
        calls.append(values)
        time.sleep(0.03)
        return {
            "access_token": "replacement",
            "refresh_token": "rotated",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    monkeypatch.setattr(auth, "_post_token", refresh)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: auth.access_token(tmp_path), range(16)))
    assert results == ["replacement"] * 16
    assert len(calls) == 1 and calls[0]["client_id"] != "dynamic_agent_client"
    assert (
        read_credentials(auth._path(tmp_path))["accounts"][data["activeAccountId"]]["refresh_token"]
        == "rotated"
    )


def test_endpoint_keys_are_isolated_and_legacy_key_migrates(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPDECK_LLM_API_KEY", raising=False)
    folder = tmp_path / ".deepdeck"
    folder.mkdir()
    gateway._config_path(tmp_path).write_text(
        json.dumps({"baseUrl": "https://first.test/v1", "model": "one"})
    )
    gateway.secrets_path(tmp_path).write_text(
        json.dumps({"llm_api_key": "old-private", "api_key": "league-kept"})
    )
    gateway.save_llm_settings(
        tmp_path, {"baseUrl": "https://second.test/v1", "model": "two", "apiKey": "second-private"}
    )
    assert gateway._api_key(tmp_path, "https://first.test/v1") == "old-private"
    assert gateway._api_key(tmp_path, "https://second.test/v1") == "second-private"
    assert gateway._api_key(tmp_path, "https://third.test/v1") == ""
    assert json.loads(gateway.secrets_path(tmp_path).read_text()) == {"api_key": "league-kept"}
    assert "private" not in json.dumps(gateway.llm_settings(tmp_path))


def test_environment_key_stays_bound_to_first_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPDECK_LLM_API_KEY", "env-private")
    monkeypatch.delenv("DEEPDECK_LLM_BASE_URL", raising=False)
    gateway.save_llm_settings(tmp_path, {"baseUrl": "https://first.test/v1", "model": "one"})
    assert gateway._api_key(tmp_path, "https://first.test/v1") == "env-private"
    second = gateway.save_llm_settings(
        tmp_path, {"baseUrl": "https://second.test/v1", "model": "two"}
    )
    assert second["hasApiKey"] is False


def test_subscription_cannot_send_credentials_to_custom_endpoint(tmp_path):
    with pytest.raises(ValueError, match="official OpenAI"):
        gateway.save_llm_settings(
            tmp_path, {"authMode": "chatgpt", "baseUrl": "https://other.test/v1", "model": "one"}
        )


def test_model_listing_uses_only_selected_endpoint_key_and_never_infers(tmp_path, monkeypatch):
    gateway.save_llm_settings(
        tmp_path, {"baseUrl": "https://first.test/v1", "model": "one", "apiKey": "secret-first"}
    )
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"data": [{"id": "model-id"}]})

    monkeypatch.setattr(gateway.httpx, "get", get)
    assert gateway.provider_models(tmp_path, {"baseUrl": "https://second.test/v1"})["items"] == [
        {"id": "model-id", "name": "model-id"}
    ]
    assert calls[0][1]["headers"] == {} and calls[0][0].endswith("/models")


def test_auth_endpoints_require_local_authorization(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path))
    assert client.post("/api/v1/agent-studio/chatgpt/start", json={}).status_code == 403
    assert client.get("/api/v1/agent-studio/chatgpt/models").status_code == 403
    assert client.post("/api/v1/agent-studio/llm-models", json={}).status_code == 403
    token = client.get("/api/v1/session").json()["token"]
    response = client.get("/api/v1/agent-studio/chatgpt", headers={"X-DeepDeck-Token": token})
    assert response.status_code == 200 and response.json()["accounts"] == []
