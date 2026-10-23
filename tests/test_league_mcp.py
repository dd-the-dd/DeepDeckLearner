from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from deepdeck_learner.league_account import SESSION_FILE, research_headers
from deepdeck_learner.league_mcp import _connection_diagnostic, league_research, mcp_url
from deepdeck_learner.private_store import save_private_json


@pytest.fixture
def mcp_site(monkeypatch):
    calls: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(
                {
                    "body": body,
                    "authorization": self.headers.get("Authorization"),
                    "cookie": self.headers.get("Cookie"),
                    "path": self.path,
                }
            )
            method = body["method"]
            if method == "notifications/initialized":
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if method == "initialize":
                value = {
                    "protocolVersion": body["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "league-contract-test", "version": "1"},
                }
            elif method == "tools/list":
                value = {
                    "tools": [
                        {
                            "name": name,
                            "description": name,
                            "inputSchema": {
                                "type": "object",
                                "properties": {"q": {"type": "string"}},
                                "required": ["q"],
                            }
                            if name == "search_cards"
                            else {"type": "object", "properties": {}},
                        }
                        for name in [
                            "search_cards",
                            "get_profile",
                            "publish_deck",
                            "save_deck_workspace",
                        ]
                    ]
                }
            elif method == "tools/call":
                assert body["params"]["name"] == "search_cards"
                data = {
                    "items": [
                        {"name": f"Card {number}", "oracleText": "Draw a card."}
                        for number in range(30)
                    ],
                    "pagination": {"page": 1},
                }
                value = {
                    "content": [{"type": "text", "text": json.dumps(data)}],
                    "structuredContent": {"data": data},
                    "isError": False,
                }
            else:
                value = {}
            response = json.dumps(
                {"jsonrpc": "2.0", "id": body.get("id"), "result": value}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}/api/v1"
    monkeypatch.setenv("DEEPDECK_PLATFORM_URL", url)
    monkeypatch.setenv("DEEPDECK_API_KEY", "ddl_agent_contract_fixture")
    monkeypatch.setenv("DEEPDECK_LLM_API_KEY", "private-model-provider-key")
    yield url, calls
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


@pytest.mark.asyncio
async def test_native_mcp_handshake_discovery_and_call_use_sdk_transport_read_only(
    tmp_path: Path, mcp_site
) -> None:
    events = []
    async with league_research(tmp_path, True, events) as server:
        assert server is not None
        tools = await server.list_tools()
        assert [tool.name for tool in tools] == ["league_search_cards"]
        result = await server.call_tool("league_search_cards", {"q": 'o:"draw"'})
        body = result.model_dump(by_alias=True)
        assert body["isError"] is False
        assert len(body["structuredContent"]["items"]) == 20
        assert body["structuredContent"]["omittedItems"] == 10
        before = len(mcp_site[1])
        denied = await server.call_tool("league_publish_deck", {})
        assert denied.model_dump(by_alias=True)["isError"] is True
        assert len(mcp_site[1]) == before
    methods = [call["body"]["method"] for call in mcp_site[1]]
    assert (
        "initialize" in methods
        and "notifications/initialized" in methods
        and "tools/call" in methods
    )
    assert all(call["path"] == "/mcp" for call in mcp_site[1])
    assert all(call["authorization"] == "Bearer ddl_agent_contract_fixture" for call in mcp_site[1])
    assert "private-model-provider-key" not in json.dumps(mcp_site[1])
    assert events[-1]["transport"] == "mcp" and events[-1]["ok"] is True


@pytest.mark.asyncio
async def test_mcp_disabled_never_connects_and_missing_credentials_preserve_local_flow(
    tmp_path: Path, monkeypatch
) -> None:
    events = []
    async with league_research(tmp_path, False, events) as server:
        assert server is None and events == []
    monkeypatch.delenv("DEEPDECK_API_KEY", raising=False)
    async with league_research(tmp_path, True, events) as server:
        assert server is None
    assert events[0]["ok"] is False
    assert "Local editing" in events[0]["error"]


def test_mcp_uses_the_bound_site_session_but_never_forwards_it_to_a_different_server(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("DEEPDECK_API_KEY", raising=False)
    monkeypatch.setenv("DEEPDECK_PLATFORM_URL", "https://www.deepdeckleague.com/api/v1")
    save_private_json(
        tmp_path,
        SESSION_FILE,
        {
            "platformUrl": "https://www.deepdeckleague.com/api/v1",
            "cookies": {"ddl_session": "private-site-cookie"},
        },
    )
    assert research_headers(tmp_path) == {"Cookie": "ddl_session=private-site-cookie"}
    assert mcp_url() == "https://www.deepdeckleague.com/mcp"
    monkeypatch.setenv("DEEPDECK_PLATFORM_URL", "https://another.test/api/v1")
    with pytest.raises(ValueError, match="currently selected"):
        research_headers(tmp_path)


def test_configured_api_key_is_not_shadowed_by_a_saved_website_session(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPDECK_API_KEY", "ddl_agent_fake_key_for_test")
    save_private_json(
        tmp_path,
        SESSION_FILE,
        {"platformUrl": "https://another.test/api/v1", "cookies": {"ddl_session": "old-cookie"}},
    )
    assert research_headers(tmp_path) == {"Authorization": "Bearer ddl_agent_fake_key_for_test"}


@pytest.mark.parametrize(
    "status,code",
    [(401, "credential_rejected"), (403, "access_denied"), (503, "server_unavailable")],
)
def test_mcp_denial_preserves_server_reason_but_redacts_secrets(status, code):
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://example.test/mcp"),
        json={
            "error": {
                "message": (
                    "Server entitlement required. ddl_agent_private_123 "
                    "Bearer secret ddl_session=cookie"
                )
            }
        },
    )
    transport = httpx.HTTPStatusError(
        "must not surface raw transport error", request=response.request, response=response
    )
    wrapper = RuntimeError("SDK wrapper")
    wrapper.__cause__ = transport
    result = _connection_diagnostic(wrapper)
    assert result["httpStatus"] == status and result["errorCode"] == code
    assert "Server entitlement required" in result["error"]
    assert "private_123" not in result["error"] and "secret" not in result["error"]
    assert "cookie" not in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 403])
async def test_sdk_hidden_http_error_uses_only_read_only_ping(tmp_path, monkeypatch, status):
    from deepdeck_learner import league_mcp

    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(status, json={"error": {"message": "Patreon required"}})

    monkeypatch.setenv("DEEPDECK_API_KEY", "ddl_agent_fake_test")
    monkeypatch.setattr(
        league_mcp,
        "_http_client_factory",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs),
    )
    result = await league_mcp._diagnose_connection(tmp_path, RuntimeError("SDK hid HTTP"))
    assert calls == [{"jsonrpc": "2.0", "id": "access-check", "method": "ping"}]
    assert result["errorCode"] == (
        "membership_required" if status == 403 else "connection_unavailable"
    )


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("daily_quota", "daily_quota_reached"),
        ("account_rate", "rate_limited"),
        ("server_rate", "rate_limited"),
    ],
)
def test_quota_denial_preserves_credentials_and_explains_waiting(reason, expected):
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://example.test/mcp"),
        json={"error": {"message": "Allowance reached", "data": {"quota": {"reason": reason}}}},
    )
    diagnostic = _connection_diagnostic(
        httpx.HTTPStatusError("limited", request=response.request, response=response)
    )
    assert diagnostic["errorCode"] == expected
    assert "do not reconnect" in diagnostic["error"]
    if reason == "daily_quota":
        assert "00:00 UTC" in diagnostic["error"]
        assert "Patreon" in diagnostic["error"]


@pytest.mark.parametrize("payload", [None, {"error": {"data": None}}, {"error": []}])
def test_unstructured_rate_denial_never_crashes_the_diagnostic(payload):
    response = httpx.Response(
        429, request=httpx.Request("POST", "https://example.test/mcp"), json=payload
    )
    diagnostic = _connection_diagnostic(
        httpx.HTTPStatusError("limited", request=response.request, response=response)
    )
    assert diagnostic["errorCode"] == "rate_limited"


def test_membership_denial_proposes_patreon_without_hardcoding_a_tier():
    response = httpx.Response(
        403,
        request=httpx.Request("POST", "https://example.test/mcp"),
        json={"error": {"message": "Active Patreon membership required"}},
    )
    error = httpx.HTTPStatusError("denied", request=response.request, response=response)
    result = _connection_diagnostic(error)
    assert result["errorCode"] == "membership_required"
    assert "connect Patreon" in result["error"]
    assert "tier 2" not in result["error"]


def test_missing_protocol_discriminator_is_not_a_credential_or_membership_failure():
    from pydantic import BaseModel, ValidationError

    class ModernResult(BaseModel):
        resultType: str

    try:
        ModernResult.model_validate({"private": "PRIVATE_KEY_PROMPT"})
    except ValidationError as error:
        wrapped = RuntimeError("PRIVATE_WRAPPER")
        wrapped.__cause__ = error
        result = _connection_diagnostic(wrapped)
    assert result["errorCode"] == "protocol_mismatch"
    assert "missing resultType" in result["error"]
    assert "saved key was kept" in result["error"]
    assert "PRIVATE" not in json.dumps(result)
    assert "httpStatus" not in result  # Validation is not a fabricated HTTP 401/403.


@pytest.mark.asyncio
async def test_known_protocol_error_does_not_make_a_redundant_auth_probe(tmp_path, monkeypatch):
    from pydantic import BaseModel, ValidationError

    from deepdeck_learner import league_mcp

    class ModernResult(BaseModel):
        resultType: str

    def forbidden_probe(**_):
        raise AssertionError("No auth/network probe is needed for a known schema mismatch")

    monkeypatch.setattr(league_mcp, "_http_client_factory", forbidden_probe)
    try:
        ModernResult.model_validate({})
    except ValidationError as error:
        result = await league_mcp._diagnose_connection(tmp_path, error)
    assert result["errorCode"] == "protocol_mismatch"


@pytest.mark.asyncio
async def test_real_sdk_chat_round_trip_with_local_compatible_provider_and_native_mcp(
    tmp_path, mcp_site
) -> None:
    """No fake Runner or paid model: exercise both real SDK HTTP transports."""
    from deepdeck_learner import llm_gateway

    requests = []

    class Provider(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(
                {
                    "body": body,
                    "authorization": self.headers.get("Authorization"),
                    "path": self.path,
                }
            )
            if len(requests) == 1:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "search-1",
                            "type": "function",
                            "function": {
                                "name": "league_search_cards",
                                "arguments": '{"q":"o:draw"}',
                            },
                        }
                    ],
                }
                reason = "tool_calls"
            else:
                message = {
                    "role": "assistant",
                    "content": "League returned card research. Your local deck was not changed.",
                }
                reason = "stop"
            response = json.dumps(
                {
                    "id": f"chat-{len(requests)}",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "contract-local-model",
                    "choices": [{"index": 0, "message": message, "finish_reason": reason}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        llm_gateway.save_llm_settings(
            tmp_path,
            {
                "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                "model": "contract-local-model",
                "leagueMcpEnabled": True,
            },
        )
        result = await llm_gateway.chat(
            tmp_path,
            {
                "context": "deck",
                "messages": [
                    {"role": "user", "content": "Find draw spells, without editing my deck."}
                ],
            },
            lambda payload: pytest.fail("No game should start"),
        )
        assert "not changed" in result["message"]
        assert len(requests) == 2
        names = {tool["function"]["name"] for tool in requests[0]["body"]["tools"]}
        assert {
            "league_search_cards",
            "search_cards",
            "move_card",
            "set_card_relationship",
        } <= names
        assert not any("publish" in name or "get_profile" in name for name in names)
        assert all(request["path"] == "/v1/chat/completions" for request in requests)
        assert all(
            request["authorization"] == "Bearer private-model-provider-key" for request in requests
        )
        assert "ddl_agent_contract_fixture" not in json.dumps(requests)
        assert any(
            message["role"] == "tool" and "Card 0" in str(message["content"])
            for message in requests[1]["body"]["messages"]
        )
        event = next(
            event for event in result["toolEvents"] if event["tool"] == "league_search_cards"
        )
        assert event["ok"] is True and len(event["result"]["items"]) == 20
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
