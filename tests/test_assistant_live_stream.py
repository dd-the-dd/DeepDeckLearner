"""Actual loopback HTTP/SDK streaming; no real provider, credentials or user data."""

from __future__ import annotations

import asyncio
import json
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from openai import AsyncOpenAI

from deepdeck_learner import app as app_module
from deepdeck_learner import llm_gateway
from deepdeck_learner.app import create_app


@asynccontextmanager
async def local_controller(root):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    # Disable lifecycle workers: only this disposable controller's HTTP route is
    # exercised. The ordinary app and its queue/game processes stay untouched.
    server = uvicorn.Server(
        uvicorn.Config(create_app(root), lifespan="off", ws="none", log_level="error")
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:

        async def wait_started():
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_started(), 5)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
            yield client
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            listener.close()


@pytest.mark.asyncio
async def test_http_delivers_text_and_completed_tools_before_model_finishes(tmp_path, monkeypatch):
    release = asyncio.Event()
    calls = []
    completed_tool = {"tool": "create_deck", "ok": True, "result": {"id": "fixture-only"}}

    async def chat(_root, payload, *_args, on_event):
        calls.append(payload)
        on_event({"type": "delta", "text": "Déjà {U}."})
        on_event({"type": "tool", "event": completed_tool})
        await release.wait()
        return {"message": "Déjà {U}. Finished.", "toolEvents": [completed_tool], "agents": []}

    monkeypatch.setattr(app_module, "agent_chat", chat)
    async with local_controller(tmp_path) as client:
        token = (await client.get("/api/v1/session")).json()["token"]
        async with client.stream(
            "POST",
            "/api/v1/agent-studio/chat/stream",
            headers={"X-DeepDeck-Token": token},
            json={"messages": [{"role": "user", "content": "Help me"}]},
        ) as response:
            assert response.status_code == 200
            events = []
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                events.append(event)
                if event["type"] == "tool":
                    assert not release.is_set(), "The model must still be waiting"
                    assert events[1] == {"type": "delta", "text": "Déjà {U}."}
                    assert event["event"] == completed_tool
                    release.set()
            assert events[-1]["type"] == "done"
            assert len(calls) == 1


@pytest.mark.asyncio
async def test_http_disconnect_cancels_waiting_generation_without_resending(tmp_path, monkeypatch):
    cancelled = asyncio.Event()
    calls = []

    async def chat(*_args, on_event):
        calls.append(1)
        on_event({"type": "delta", "text": "A partial reply"})
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(app_module, "agent_chat", chat)
    async with local_controller(tmp_path) as client:
        token = (await client.get("/api/v1/session")).json()["token"]
        async with client.stream(
            "POST",
            "/api/v1/agent-studio/chat/stream",
            headers={"X-DeepDeck-Token": token},
            json={},
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: ") and json.loads(line[6:])["type"] == "delta":
                    break
        await asyncio.wait_for(cancelled.wait(), 5)
        assert calls == [1]


@pytest.mark.asyncio
async def test_quiet_model_keeps_http_open_on_python_310(tmp_path, monkeypatch):
    release = asyncio.Event()
    monkeypatch.setattr(app_module, "_ASSISTANT_STREAM_KEEPALIVE_SECONDS", 0.01)

    async def chat(*_args, on_event):
        await release.wait()
        on_event({"type": "delta", "text": "Ready"})
        return {"message": "Ready", "toolEvents": [], "agents": []}

    monkeypatch.setattr(app_module, "agent_chat", chat)
    async with local_controller(tmp_path) as client:
        token = (await client.get("/api/v1/session")).json()["token"]
        async with client.stream(
            "POST",
            "/api/v1/agent-studio/chat/stream",
            headers={"X-DeepDeck-Token": token},
            json={},
        ) as response:
            events = []
            heartbeat = False
            async for line in response.aiter_lines():
                if line == ": keepalive":
                    heartbeat = True
                    release.set()
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
            assert heartbeat, "A quiet model must not be mistaken for a disconnected stream"
            assert [event["type"] for event in events] == ["status", "delta", "done"]


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", [False, True])
async def test_actual_sdk_forwards_compatible_provider_text_before_completion(
    tmp_path, monkeypatch, subscription
):
    release = asyncio.Event()
    received = asyncio.Event()
    requests = []
    events = []

    def chunk(delta, finish_reason=None):
        return (
            "data: "
            + json.dumps(
                {
                    "id": "fixture-stream",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                }
            )
            + "\n\n"
        ).encode()

    class ProviderStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            if subscription:
                for sequence, text in [(1, "Bonjour {R}"), (2, ".")]:
                    if sequence == 2:
                        await release.wait()
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "type": "response.output_text.delta",
                                "sequence_number": sequence,
                                "item_id": "fixture-message",
                                "output_index": 0,
                                "content_index": 0,
                                "delta": text,
                                "logprobs": [],
                            }
                        )
                        + "\n\n"
                    ).encode()
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "response.completed",
                            "sequence_number": 3,
                            "response": {
                                "id": "fixture-response",
                                "object": "response",
                                "created_at": 1,
                                "model": "fixture",
                                "status": "completed",
                                "parallel_tool_calls": True,
                                "tool_choice": "auto",
                                "tools": [],
                                "output": [
                                    {
                                        "type": "message",
                                        "id": "fixture-message",
                                        "role": "assistant",
                                        "status": "completed",
                                        "content": [
                                            {
                                                "type": "output_text",
                                                "text": "Bonjour {R}.",
                                                "annotations": [],
                                            }
                                        ],
                                    }
                                ],
                                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                            },
                        }
                    )
                    + "\n\n"
                ).encode()
                return
            yield chunk({"role": "assistant", "content": "Bonjour {R}"})
            await release.wait()
            yield chunk({"content": "."})
            yield chunk({}, "stop")
            yield b"data: [DONE]\n\n"

    def transport(request):
        requests.append(json.loads(request.content))
        expected = (
            llm_gateway.chatgpt_auth.RESOURCE.rstrip("/") + "/responses"
            if subscription
            else "http://127.0.0.1:11434/v1/chat/completions"
        )
        assert str(request.url) == expected
        assert requests[-1]["stream"] is True
        if subscription:
            assert requests[-1]["store"] is False
            assert requests[-1]["tools"][0]["type"] == "namespace"
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=ProviderStream()
        )

    real_factory = AsyncOpenAI

    def client(**options):
        return real_factory(
            **options, http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport))
        )

    llm_gateway.save_llm_settings(
        tmp_path, {"baseUrl": "http://127.0.0.1:11434/v1", "model": "fixture"}
    )
    monkeypatch.setattr(llm_gateway, "AsyncOpenAI", client)
    monkeypatch.setattr(llm_gateway, "_api_key", lambda *_: "fixture-only")
    if subscription:
        settings = llm_gateway.llm_settings(tmp_path)
        settings.update(authMode="chatgpt", configured=True)
        monkeypatch.setattr(llm_gateway, "llm_settings", lambda *_: settings)
        monkeypatch.setattr(llm_gateway.chatgpt_auth, "access_token", lambda *_: "fixture-only")

    def on_event(event):
        events.append(event)
        if event["type"] == "delta":
            received.set()

    task = asyncio.create_task(
        llm_gateway.chat(
            tmp_path,
            {"messages": [{"role": "user", "content": "Help"}]},
            lambda _: {},
            on_event=on_event,
        )
    )
    try:
        await asyncio.wait_for(received.wait(), 5)
        assert not task.done()
        assert not release.is_set()
        release.set()
        result = await asyncio.wait_for(task, 5)
        assert result["message"] == "Bonjour {R}."
        assert (
            "".join(event["text"] for event in events if event["type"] == "delta")
            == result["message"]
        )
        assert len(requests) == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
