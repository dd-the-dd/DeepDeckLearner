from __future__ import annotations

import sqlite3
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deepdeck_learner import app as learner_app
from deepdeck_learner.app import create_app
from deepdeck_learner.local_diagnostics import (
    MAX_DATABASE_BYTES,
    DiagnosticStore,
    LocalDiagnosticsMiddleware,
    LocalResponseInterrupted,
)


def append(store, *, reference=None, status=500):
    reference = reference or str(uuid.uuid4())
    assert store.append(
        reference=reference,
        method="POST",
        route="/api/v1/jobs/{job_id}",
        status=status,
        duration_ms=12,
        category="service_error" if status >= 500 else "action_completed",
    )
    return reference


def test_oldest_first_bound_retention_and_filters(tmp_path):
    store = DiagnosticStore(tmp_path, max_records=3)
    first = append(store)
    second = append(store, status=200)
    third = append(store)
    fourth = append(store)
    report = store.report(errors_only=False)
    assert report["storedEntries"] == 3
    assert [row["reference"] for row in report["items"]] == [fourth, third, second]
    assert first not in str(report)
    assert len(store.report()["items"]) == 2
    assert store.path.stat().st_size <= MAX_DATABASE_BYTES
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE events SET created_at=? WHERE id=?", (int(time.time()) - 15 * 86400, third)
        )
    assert store.report()["storedEntries"] == 2
    assert report["localOnly"] is True
    with pytest.raises(ValueError):
        store.report(limit=101)


def test_store_failure_is_bounded_and_does_not_echo_private_driver_details(tmp_path, monkeypatch):
    store = DiagnosticStore(tmp_path)

    def full():
        raise sqlite3.OperationalError("PRIVATE database or disk is full")

    monkeypatch.setattr(store, "_connect", full)
    assert (
        store.append(
            reference=str(uuid.uuid4()),
            method="POST",
            route="/api/v1/jobs",
            status=500,
            duration_ms=1,
            category="service_error",
        )
        is False
    )


def test_unrecognized_preexisting_database_is_not_changed(tmp_path):
    store = DiagnosticStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    with sqlite3.connect(store.path) as connection:
        connection.execute("CREATE TABLE personal_data(value TEXT)")
        connection.execute("INSERT INTO personal_data VALUES ('PRIVATE')")
    assert not store.append(
        reference=str(uuid.uuid4()),
        method="POST",
        route="/api/v1/jobs",
        status=500,
        duration_ms=1,
        category="service_error",
    )
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT value FROM personal_data").fetchone()[0] == "PRIVATE"
        assert connection.execute("PRAGMA application_id").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM sqlite_master WHERE name='events'").fetchone()[
                0
            ]
            == 0
        )


def test_unexpected_api_failure_has_real_reference_and_no_private_body_url_or_exception(
    tmp_path, monkeypatch
):
    def broken(*_):
        raise ValueError("PRIVATE exception text with API_KEY")

    monkeypatch.setattr(learner_app, "local_models", broken)
    client = TestClient(create_app(tmp_path))
    response = client.get(
        "/api/v1/models?search=PRIVATE_QUERY",
        headers={"Authorization": "PRIVATE_TOKEN", "Cookie": "PRIVATE_COOKIE"},
    )
    assert response.status_code == 500
    reference = response.headers["x-request-id"]
    uuid.UUID(reference)
    assert response.json()["requestId"] == reference
    assert "PRIVATE" not in response.text
    token = client.get("/api/v1/session").json()["token"]
    assert client.get("/api/v1/diagnostics").status_code == 403
    report = client.get("/api/v1/diagnostics", headers={"X-DeepDeck-Token": token})
    assert report.status_code == 200
    assert report.headers["cache-control"] == "no-store"
    assert report.json()["items"][0]["reference"] == reference
    assert report.json()["items"][0]["route"] == "/api/v1/models"
    assert report.json()["items"][0]["category"] == "value_error"
    assert "PRIVATE" not in report.text
    assert b"PRIVATE" not in (tmp_path / ".deepdeck/diagnostics.sqlite3").read_bytes()


def test_http_rejection_retains_status_code_and_get_polling_does_not_create_history(tmp_path):
    client = TestClient(create_app(tmp_path))
    for _ in range(4):
        assert client.get("/api/v1/health").status_code == 200
    assert not (tmp_path / ".deepdeck/diagnostics.sqlite3").exists()
    denied = client.post("/api/v1/jobs", json={"PRIVATE_PROMPT": "PRIVATE_KEY"})
    assert denied.status_code == 403
    report = DiagnosticStore(tmp_path).report()
    assert report["storedEntries"] == 1
    assert report["items"][0]["reference"] == denied.headers["x-request-id"]
    assert report["items"][0]["category"] == "request_rejected"
    assert "PRIVATE" not in str(report)


def test_failed_mcp_check_is_not_a_success_just_because_its_http_status_is_200(
    tmp_path, monkeypatch
):
    @asynccontextmanager
    async def refused(_root, _enabled, events):
        events.append({"ok": False, "error": "PRIVATE transport detail"})
        yield None

    monkeypatch.setattr(learner_app, "league_research", refused)
    client = TestClient(create_app(tmp_path))
    token = client.get("/api/v1/session").json()["token"]
    response = client.post("/api/v1/agent-studio/mcp-test", headers={"X-DeepDeck-Token": token})
    assert response.status_code == 200
    assert response.json()["connected"] is False
    report = DiagnosticStore(tmp_path).report()
    assert report["items"][0]["category"] == "connection_failed"
    assert report["items"][0]["status"] == 200
    assert "PRIVATE" not in str(report)


def test_dynamic_route_ids_and_queries_never_enter_the_journal(tmp_path):
    store = DiagnosticStore(tmp_path)
    app = FastAPI()

    @app.post("/api/v1/decks/{deck_id}")
    def action(deck_id: str, body: dict):
        return {"ok": True}

    app.add_middleware(
        LocalDiagnosticsMiddleware, store=store, routes=frozenset({"/api/v1/decks/{deck_id}"})
    )
    response = TestClient(app).post(
        "/api/v1/decks/PRIVATE_ID?key=PRIVATE_QUERY",
        json={"secret": "PRIVATE_BODY"},
        headers={"X-Request-ID": "PRIVATE_HEADER"},
    )
    assert response.status_code == 200
    uuid.UUID(response.headers["x-request-id"])
    report = store.report(errors_only=False)
    assert report["items"][0]["route"] == "/api/v1/decks/{deck_id}"
    assert "PRIVATE" not in str(report)


def test_render_marker_is_local_authorized_bounded_and_cannot_receive_exception_text(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.post("/api/v1/diagnostics/ui", json={"kind": "render_error"}).status_code == 403
    token = client.get("/api/v1/session").json()["token"]
    headers = {"X-DeepDeck-Token": token}
    assert (
        client.post(
            "/api/v1/diagnostics/ui",
            headers=headers,
            json={"kind": "render_error", "message": "PRIVATE"},
        ).status_code
        == 422
    )
    assert (
        client.post("/api/v1/diagnostics/ui", headers=headers, json={"kind": "PRIVATE"}).status_code
        == 422
    )
    assert (
        client.post("/api/v1/diagnostics/ui", headers=headers, content=b"x" * 513).status_code
        == 413
    )
    saved = client.post("/api/v1/diagnostics/ui", headers=headers, json={"kind": "render_error"})
    assert saved.status_code == 200
    assert saved.json()["recorded"] is True
    assert saved.json()["reference"] == saved.headers["x-request-id"]
    report = client.get("/api/v1/diagnostics", headers=headers).json()
    assert report["storedEntries"] == 1
    assert report["items"][0]["reference"] == saved.json()["reference"]
    assert report["items"][0]["method"] == "CLIENT"
    assert report["items"][0]["status"] == 0  # A display failure is not an HTTP 500.
    assert report["items"][0]["category"] == "render_error"
    assert "PRIVATE" not in str(report)


def test_unmatched_api_requests_are_diagnostic_without_recording_their_path(tmp_path):
    store = DiagnosticStore(tmp_path)
    app = FastAPI()
    app.add_middleware(LocalDiagnosticsMiddleware, store=store, routes=frozenset())
    response = TestClient(app).get("/api/v1/PRIVATE_PATH?key=PRIVATE_QUERY")
    assert response.status_code == 404
    report = store.report()
    assert report["items"][0]["route"] == "<unmatched>"
    assert "PRIVATE" not in str(report)


def test_diagnostic_failure_never_changes_a_successful_player_response(tmp_path, monkeypatch):
    store = DiagnosticStore(tmp_path)

    def failed(**_):
        raise RuntimeError("PRIVATE diagnostic bug")

    monkeypatch.setattr(store, "append", failed)
    app = FastAPI()

    @app.post("/api/v1/action")
    def action():
        return {"ok": True}

    app.add_middleware(
        LocalDiagnosticsMiddleware, store=store, routes=frozenset({"/api/v1/action"})
    )
    assert TestClient(app).post("/api/v1/action").json() == {"ok": True}


@pytest.mark.asyncio
async def test_interrupted_stream_keeps_committed_status_but_is_not_reported_as_success(tmp_path):
    store = DiagnosticStore(tmp_path)

    async def streaming(scope, _receive, send):
        scope["route"] = SimpleNamespace(path="/api/v1/replay")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise ValueError("PRIVATE_STREAM_DETAIL")

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    wrapped = LocalDiagnosticsMiddleware(
        streaming, store=store, routes=frozenset({"/api/v1/replay"})
    )
    with pytest.raises(LocalResponseInterrupted) as error:
        await wrapped({"type": "http", "method": "GET", "path": "/api/v1/replay"}, receive, send)
    assert "PRIVATE" not in "".join(traceback.format_exception(error.value))
    assert sent[0]["status"] == 200
    assert len(sent) == 1  # No invented success body appended to an interrupted stream.
    record = store.report()["items"][0]
    assert record["status"] == 200
    assert record["category"] == "response_interrupted"


@pytest.mark.asyncio
async def test_websocket_scope_passes_through_unchanged_without_a_journal(tmp_path):
    scope = {"type": "websocket"}
    observed = []

    async def websocket(s, receive, send):
        observed.append(s)

    await LocalDiagnosticsMiddleware(
        websocket, store=DiagnosticStore(tmp_path), routes=frozenset()
    )(scope, None, None)
    assert observed == [scope]
    assert not (tmp_path / ".deepdeck/diagnostics.sqlite3").exists()
