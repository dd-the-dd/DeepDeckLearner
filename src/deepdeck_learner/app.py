from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import chatgpt_auth
from .agent_studio import (
    AgentRevisionConflict,
    agent_revisions,
    heuristic_catalog,
    restore_agent_revision,
)
from .agent_studio import (
    create_agent as create_programmatic_agent,
)
from .agent_studio import (
    find_agent as find_programmatic_agent,
)
from .agent_studio import (
    list_agents as list_programmatic_agents,
)
from .agent_studio import (
    update_agent as update_programmatic_agent,
)
from .catalogs import (
    CatalogAuthenticationError,
    CatalogError,
    CatalogNotFoundError,
    account_api_key,
    active_competitions,
    cached_deck_download,
    cached_decks,
    deck_cover_presentation,
    download_platform_deck,
    downloaded_decks,
    local_deck_presentation,
    local_legal_decks,
    platform_decks,
    scryfall_card_artwork,
    scryfall_card_names,
    scryfall_image,
)
from .deck_edits import DeckRevisionConflict
from .deck_sync import (
    DeckSyncConflict,
    import_server_workspace,
    list_server_workspaces,
    prepare_league_decks,
    publish_deck,
    sync_deck,
)
from .deck_workshop import (
    create_deck as create_workshop_deck,
)
from .deck_workshop import (
    delete_deck as delete_workshop_deck,
)
from .deck_workshop import (
    find_deck as find_workshop_deck,
)
from .deck_workshop import (
    fork_downloaded_deck,
)
from .deck_workshop import (
    list_decks as list_workshop_decks,
)
from .deck_workshop import (
    update_deck as update_workshop_deck,
)
from .experience import experience_settings, save_experience_settings
from .jobs import JobManager, JobValidationError
from .league_account import (
    LeagueAccountError,
    LeagueAuthenticationError,
    sign_in,
    sign_out,
)
from .league_account import (
    account_status as site_account_status,
)
from .league_capacity import league_capacity
from .league_connection import connection_status, platform_url
from .league_mcp import league_research
from .llm_gateway import chat as agent_chat
from .llm_gateway import llm_settings, provider_models, save_llm_settings
from .local_diagnostics import DiagnosticStore, LocalDiagnosticsMiddleware
from .local_statistics import local_statistics
from .models import deck_statistics, local_models, training_statistics
from .replay_lock import ReplayStorageBusyError
from .research import search_cards as research_cards
from .research import search_decks as research_decks
from .resources import (
    delete_model_run,
    find_model_run,
    load_resource_plan,
    save_resource_plan,
)
from .runtime import local_engine_url
from .settings import load_api_key, save_api_key
from .status import capability_status, project_root
from .training_platform import (
    acquire_replay_lease,
    list_saved_replays,
    load_agent_training_contract,
    load_saved_replay,
    load_training_settings,
    release_replay_lease,
    renew_replay_lease,
    save_agent_training_control,
    save_replay_forever,
    save_training_settings,
    set_replay_favorite,
    training_evidence,
)

_ASSISTANT_STREAM_KEEPALIVE_SECONDS = 15.0


def create_app(root: Path | None = None) -> FastAPI:
    resolved_root = (root or project_root()).resolve()
    load_api_key(resolved_root)
    manager = JobManager(resolved_root)
    from .competitions import CompetitionService

    local_competitions = CompetitionService(resolved_root, manager)
    from .league_queue import LeagueQueueService, QueueConflict

    league_rotation = LeagueQueueService(resolved_root, manager)
    session_token = secrets.token_urlsafe(32)
    app = FastAPI(title="DeepDeckLearner local controller", version="1.0")

    @app.exception_handler(ReplayStorageBusyError)
    async def replay_storage_busy(_request: Request, error: ReplayStorageBusyError) -> JSONResponse:
        return JSONResponse(
            status_code=503, content={"detail": str(error)}, headers={"Retry-After": "1"}
        )

    app.state.manager = manager
    app.state.session_token = session_token
    diagnostics = DiagnosticStore(resolved_root)

    @app.get("/api/v1/diagnostics")
    def local_diagnostic_report(
        errors_only: bool = True,
        limit: int = 50,
        x_deepdeck_token: str | None = Header(default=None),
    ) -> JSONResponse:
        authorize(x_deepdeck_token)
        try:
            report = diagnostics.report(errors_only=errors_only, limit=limit)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except (OSError, sqlite3.Error):
            raise HTTPException(
                status_code=503,
                detail="Local diagnostics could not be read. Your decks, agents and replays were not changed.",
            ) from None
        return JSONResponse(report, headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/diagnostics/ui")
    async def local_render_diagnostic(
        request: Request, x_deepdeck_token: str | None = Header(default=None)
    ) -> JSONResponse:
        authorize(x_deepdeck_token)
        body = bytearray()
        async for part in request.stream():
            if len(body) + len(part) > 512:
                raise HTTPException(status_code=413, detail="Diagnostic marker is too large.")
            body.extend(part)
        try:
            marker = json.loads(body)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid diagnostic marker.") from None
        if marker != {"kind": "render_error"}:
            raise HTTPException(status_code=422, detail="Only a render-error marker is accepted.")
        reference = request.state.local_request_id
        recorded = await asyncio.to_thread(
            diagnostics.append,
            reference=reference,
            method="CLIENT",
            route="app/render",
            status=0,
            duration_ms=0,
            category="render_error",
        )
        return JSONResponse(
            {"reference": reference, "recorded": recorded}, headers={"Cache-Control": "no-store"}
        )

    account_cache: dict[str, Any] = {"key": None, "checked": 0.0, "value": None}
    site_account_cache: dict[str, Any] = {"checked": 0.0, "value": None}
    connection_cache: dict[str, Any] = {"checked": 0.0, "value": None, "url": None}

    @app.get("/api/v1/league-capacity")
    def account_league_capacity() -> dict[str, Any]:
        return league_capacity()

    @app.get("/api/v1/league-queue")
    def read_league_queue() -> dict[str, Any]:
        return league_rotation.get()

    @app.get("/api/v1/replay-inbox")
    def read_replay_inbox() -> dict[str, Any]:
        from .league_replays import replay_inbox_status

        return replay_inbox_status(resolved_root)

    @app.post("/api/v1/replay-inbox/{model_id}/collect", status_code=202)
    def recover_pending_replays(
        model_id: str, x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return manager.create({"kind": "replays.collect", "model_id": model_id})
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.put("/api/v1/league-queue")
    def configure_league_queue(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return league_rotation.configure(payload)
        except QueueConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/league-queue/entries")
    def append_league_queue_entry(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return league_rotation.append(payload)
        except QueueConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/league-queue/entries/{entry_id}/remove")
    def remove_league_queue_entry(
        entry_id: str, payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return league_rotation.remove(entry_id, revision=payload.get("revision"))
        except QueueConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/league-queue/{action}")
    def control_league_queue(
        action: str, payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return league_rotation.control(
                action, revision=payload.get("revision"), entry_id=payload.get("entryId")
            )
        except QueueConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/league-connection")
    def league_connection() -> dict[str, Any]:
        try:
            url = platform_url()
            now = time.monotonic()
            cached = connection_cache["value"]
            if (
                connection_cache["url"] == url
                and isinstance(cached, dict)
                and now - connection_cache["checked"] < 60
            ):
                return cached
            value = connection_status()
            connection_cache.update({"checked": now, "value": value, "url": url})
            return value
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def sync_error(error: Exception) -> HTTPException:
        if isinstance(error, DeckRevisionConflict):
            return HTTPException(
                status_code=409,
                detail={"code": "local_deck_revision_conflict", "message": str(error)},
            )
        code = (
            409
            if isinstance(error, DeckSyncConflict)
            else 401
            if isinstance(error, LeagueAuthenticationError)
            else 502
            if isinstance(error, LeagueAccountError)
            else 422
        )
        return HTTPException(status_code=code, detail=str(error))

    @app.get("/api/v1/league-account")
    def league_account_status() -> dict[str, Any]:
        now = time.monotonic()
        cached = site_account_cache["value"]
        if isinstance(cached, dict) and now - site_account_cache["checked"] < 60:
            return cached
        value = site_account_status(resolved_root)
        site_account_cache.update({"checked": now, "value": value})
        return value

    @app.post("/api/v1/league-account/login")
    def login_site_account(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            value = sign_in(
                resolved_root,
                str(payload.get("identity", "")),
                str(payload.get("password", "")),
                create_agent_key=bool(payload.get("createAgentKey", False)),
            )
        except (OSError, ValueError) as error:
            raise sync_error(error) from error
        site_account_cache.update({"checked": time.monotonic(), "value": value})
        account_cache.update({"key": None, "checked": 0.0, "value": None})
        return value

    @app.post("/api/v1/league-account/logout")
    def logout_site_account(x_deepdeck_token: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        value = sign_out(resolved_root)
        site_account_cache.update({"checked": time.monotonic(), "value": value})
        return value

    @app.post("/api/v1/league-decks/prepare")
    def prepare_site_decks(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        ids = payload.get("deckVersionIds")
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 100
            or not all(isinstance(value, str) and value for value in ids)
        ):
            raise HTTPException(status_code=422, detail="Choose between 1 and 100 decks.")
        try:
            return prepare_league_decks(resolved_root, ids)
        except (OSError, ValueError) as error:
            raise sync_error(error) from error

    def authorize(value: str | None) -> None:
        if not value or not secrets.compare_digest(value, session_token):
            raise HTTPException(status_code=403, detail="Invalid local session token.")

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/session")
    def session() -> dict[str, str]:
        return {"token": session_token}

    @app.get("/api/v1/status")
    def status(engine_url: str | None = None) -> dict[str, Any]:
        return capability_status(resolved_root, engine_url or local_engine_url())

    @app.get("/api/v1/experience/settings")
    def get_experience_settings() -> dict[str, Any]:
        return experience_settings(resolved_root)

    @app.put("/api/v1/experience/settings")
    def update_experience_settings(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return save_experience_settings(resolved_root, payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/account/status")
    def account_status() -> dict[str, Any]:
        key = os.getenv("DEEPDECK_API_KEY", "").strip()
        if not key:
            return {"configured": False, "valid": False, "reason": "Add an agent API key."}
        now = time.monotonic()
        cached = account_cache.get("value")
        if (
            account_cache.get("key") == key
            and isinstance(cached, dict)
            and now - float(account_cache.get("checked", 0.0)) < 60
        ):
            return cached
        try:
            active_competitions()
            value = {"configured": True, "valid": True, "reason": "League account connected."}
        except CatalogAuthenticationError as error:
            value = {"configured": True, "valid": False, "reason": str(error)}
        except CatalogError as error:
            value = {"configured": True, "valid": None, "reason": str(error)}
        account_cache.update({"key": key, "checked": now, "value": value})
        return value

    @app.post("/api/v1/settings/api-key")
    def configure_api_key(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, bool]:
        authorize(x_deepdeck_token)
        try:
            save_api_key(resolved_root, str(payload.get("api_key", "")))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        account_cache.update({"key": None, "checked": 0.0, "value": None})
        return {"configured": True}

    @app.get("/api/v1/jobs")
    def jobs(view: Literal["all", "player"] = "all") -> list[dict[str, Any]]:
        return manager.list_jobs(player_only=view == "player")

    @app.get("/api/v1/local-competitions")
    def competition_history() -> dict[str, Any]:
        return {"items": local_competitions.list()}

    @app.get("/api/v1/local-competitions/{competition_id}")
    def competition_detail(competition_id: str) -> dict[str, Any]:
        try:
            return local_competitions.get(competition_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Competition not found.") from error

    @app.post("/api/v1/local-competitions", status_code=202)
    def create_local_competition(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return local_competitions.create(payload)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/local-competitions/{competition_id}/{action}")
    def control_local_competition(
        competition_id: str, action: str, x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return local_competitions.control(competition_id, action)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Competition not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/models")
    def models() -> dict[str, Any]:
        return {"items": local_models(resolved_root, manager.list_jobs())}

    @app.get("/api/v1/agent-studio/heuristics")
    def programmatic_heuristics() -> dict[str, Any]:
        return heuristic_catalog()

    @app.get("/api/v1/agent-studio/agents")
    def programmatic_agents() -> dict[str, Any]:
        return {"items": list_programmatic_agents(resolved_root)}

    @app.get("/api/v1/agent-studio/agents/{agent_id}")
    def programmatic_agent(agent_id: str) -> dict[str, Any]:
        try:
            return find_programmatic_agent(resolved_root, agent_id)[1]
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/v1/agent-studio/agents", status_code=201)
    def create_local_programmatic_agent(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return create_programmatic_agent(resolved_root, payload)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.put("/api/v1/agent-studio/agents/{agent_id}")
    def update_local_programmatic_agent(
        agent_id: str,
        payload: dict[str, Any],
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            assert_agent_editable(agent_id)
            return update_programmatic_agent(resolved_root, agent_id, payload)
        except AgentRevisionConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def assert_agent_editable(agent_id: str) -> None:
        if manager.model_has_active_workers(agent_id):
            raise AgentRevisionConflict("Stop this agent before editing or restoring its strategy.")

    @app.get("/api/v1/agent-studio/agents/{agent_id}/revisions")
    def programmatic_agent_revisions(agent_id: str) -> dict[str, Any]:
        try:
            return {"items": agent_revisions(resolved_root, agent_id)}
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/v1/agent-studio/agents/{agent_id}/revisions/{revision}/restore")
    def restore_programmatic_agent(
        agent_id: str,
        revision: int,
        payload: dict[str, Any],
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            assert_agent_editable(agent_id)
            return restore_agent_revision(
                resolved_root, agent_id, revision, payload.get("expectedRevision")
            )
        except AgentRevisionConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/agent-studio/llm-settings")
    def local_llm_settings() -> dict[str, Any]:
        return llm_settings(resolved_root)

    @app.get("/api/v1/agent-studio/chatgpt")
    def chatgpt_accounts(x_deepdeck_token: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        return chatgpt_auth.status(resolved_root)

    @app.get("/api/v1/agent-studio/chatgpt/models")
    def chatgpt_models(x_deepdeck_token: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return chatgpt_auth.models(resolved_root)
        except (OSError, ValueError, httpx.HTTPError) as error:
            raise HTTPException(
                status_code=422,
                detail="ChatGPT models could not be loaded. Check sign-in and plan access.",
            ) from error

    @app.get("/api/v1/agent-studio/chatgpt/flows/{flow_id}")
    def chatgpt_flow(
        flow_id: str, x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return chatgpt_auth.flow_status(resolved_root, flow_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/v1/agent-studio/chatgpt/{action}")
    def chatgpt_action(
        action: str, payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            if action == "start":
                account_id = payload.get("accountId")
                return chatgpt_auth.start(resolved_root, str(account_id) if account_id else None)
            if action == "select":
                return chatgpt_auth.select(resolved_root, str(payload.get("accountId", "")))
            if action == "disconnect":
                return chatgpt_auth.disconnect(resolved_root, str(payload.get("accountId", "")))
            if action == "cancel":
                return chatgpt_auth.cancel(resolved_root, str(payload.get("flowId", "")))
            raise HTTPException(status_code=404, detail="Unknown ChatGPT action.")
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.put("/api/v1/agent-studio/llm-settings")
    def configure_local_llm(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return save_llm_settings(resolved_root, payload)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/agent-studio/llm-models")
    def available_llm_models(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return provider_models(resolved_root, payload)
        except (OSError, ValueError, httpx.HTTPError) as error:
            raise HTTPException(
                status_code=422,
                detail="Models could not be loaded. Check the provider URL and API key, or enter a model ID manually.",
            ) from error

    @app.post("/api/v1/agent-studio/chat")
    async def programmatic_agent_chat(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return await agent_chat(resolved_root, payload, manager.create, assert_agent_editable)
        except httpx.HTTPError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except (JobValidationError, OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/agent-studio/chat/stream")
    async def programmatic_agent_chat_stream(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> StreamingResponse:
        authorize(x_deepdeck_token)

        async def output() -> AsyncIterator[str]:
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            loop = asyncio.get_running_loop()
            sent_chars = 0

            def emit(event: dict[str, Any]) -> None:
                nonlocal sent_chars
                if event.get("type") == "delta":
                    sent_chars += len(str(event.get("text", "")))
                    if sent_chars > 100_000:
                        raise ValueError("The streamed reply exceeded the local display limit.")
                loop.call_soon_threadsafe(queue.put_nowait, event)

            async def run() -> None:
                try:
                    result = await agent_chat(
                        resolved_root, payload, manager.create, assert_agent_editable, on_event=emit
                    )
                    emit({"type": "done", "result": result})
                except (httpx.HTTPError, JobValidationError, OSError, ValueError):
                    # Never leak provider credentials or exception URLs into a stream.
                    emit(
                        {
                            "type": "error",
                            "message": "The assistant stream was interrupted. Inspect any completed actions before sending again. No action was retried automatically.",
                        }
                    )
                except Exception:
                    emit(
                        {
                            "type": "error",
                            "message": "The assistant could not finish. Review completed actions before retrying. No action was repeated automatically.",
                        }
                    )

            task = asyncio.create_task(run())
            try:
                yield 'data: {"type":"status","message":"Connecting to your assistant…"}\n\n'
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=_ASSISTANT_STREAM_KEEPALIVE_SECONDS)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"
                    if event["type"] in {"done", "error"}:
                        break
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(
            output(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/agent-studio/mcp-test")
    async def test_league_mcp(
        request: Request,
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        events: list[dict[str, Any]] = []
        async with league_research(resolved_root, True, events) as server:
            if server is None:
                request.state.local_diagnostic_category = "connection_failed"
            return {"connected": server is not None, "toolEvents": events}

    @app.post("/api/v1/research/cards")
    def search_research_cards(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return research_cards(payload)
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/research/decks")
    def search_research_decks(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return research_decks(payload)
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/models", status_code=201)
    def create_model(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            model_id = manager.prepare_model(payload)
        except (JobValidationError, OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return next(
            model
            for model in local_models(resolved_root, manager.list_jobs())
            if model.get("id") == model_id
        )

    @app.delete("/api/v1/models/{model_id}")
    def delete_model(
        model_id: str, x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        if manager.model_has_active_workers(model_id):
            raise HTTPException(
                status_code=409,
                detail="Stop this agent's active jobs before deleting its files.",
            )
        try:
            return delete_model_run(resolved_root, model_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.put("/api/v1/models/{model_id}")
    def update_model(
        model_id: str,
        payload: dict[str, Any],
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            updated_id = manager.update_model(model_id, payload)
        except (JobValidationError, OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return next(
            model
            for model in local_models(resolved_root, manager.list_jobs())
            if model.get("id") == updated_id
        )

    @app.get("/api/v1/models/{model_id}/resources")
    def model_resources(model_id: str) -> dict[str, int]:
        try:
            run, _ = find_model_run(resolved_root, model_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return load_resource_plan(run)

    @app.put("/api/v1/models/{model_id}/resources")
    def update_model_resources(
        model_id: str,
        payload: dict[str, Any],
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, int]:
        authorize(x_deepdeck_token)
        try:
            return save_resource_plan(resolved_root, model_id, payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/resources")
    def resources() -> dict[str, Any]:
        return manager.resources()

    @app.get("/api/v1/statistics/decks")
    def training_deck_statistics() -> dict[str, Any]:
        return {"items": deck_statistics(resolved_root)}

    @app.get("/api/v1/statistics/local")
    def local_match_statistics(days: int = 0) -> dict[str, Any]:
        try:
            return local_statistics(resolved_root, days)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/statistics/storage")
    def statistics_storage_status() -> dict[str, Any]:
        from .statistics_store import storage_status

        return storage_status(resolved_root)

    @app.put("/api/v1/statistics/storage")
    def statistics_storage_settings(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        from .statistics_store import StatisticsStorageError, save_settings

        try:
            return save_settings(resolved_root, payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except StatisticsStorageError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/v1/statistics/advanced")
    def advanced_match_statistics(kind: str, identity: str, days: int = 0) -> dict[str, Any]:
        from .statistics_store import advanced_statistics

        try:
            return advanced_statistics(resolved_root, kind, identity, days)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/statistics/training")
    def local_training_statistics(window: str = "200") -> dict[str, Any]:
        try:
            return {"items": training_statistics(resolved_root, window)}
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/training/curriculum")
    def training_curriculum() -> dict[str, Any]:
        try:
            from oracle_ai.training.curriculum import curriculum_catalog
        except ModuleNotFoundError as error:
            raise HTTPException(
                status_code=501,
                detail="Neural training is optional. Use the source checkout with the deep-learning dependencies for this advanced workflow.",
            ) from error
        return curriculum_catalog()

    @app.get("/api/v1/models/{model_id}/training-settings")
    def model_training_settings(model_id: str) -> dict[str, Any]:
        try:
            return load_training_settings(resolved_root, model_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.put("/api/v1/models/{model_id}/training-settings")
    def update_model_training_settings(
        model_id: str,
        payload: dict[str, Any],
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        if manager.model_has_active_workers(model_id):
            raise HTTPException(
                status_code=409,
                detail="Stop this agent before changing its training stages or cadence.",
            )
        try:
            return save_training_settings(resolved_root, model_id, payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/models/{model_id}/evidence")
    def model_training_evidence(model_id: str) -> dict[str, Any]:
        try:
            return training_evidence(resolved_root, model_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/v1/models/{model_id}/training-contract")
    def model_training_contract(model_id: str) -> dict[str, Any]:
        try:
            return load_agent_training_contract(resolved_root, model_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.put("/api/v1/models/{model_id}/training-control")
    def update_model_training_control(
        model_id: str,
        payload: dict[str, Any],
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return save_agent_training_control(resolved_root, model_id, payload)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/models/{model_id}/replays")
    def model_replays(model_id: str) -> dict[str, Any]:
        try:
            return {"items": list_saved_replays(resolved_root, model_id)}
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/v1/models/{model_id}/replays/{replay_id}")
    def model_replay(model_id: str, replay_id: str) -> dict[str, Any]:
        try:
            return load_saved_replay(resolved_root, model_id, replay_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/models/{model_id}/replays/{replay_id}/leases")
    def acquire_model_replay_lease(
        model_id: str,
        replay_id: str,
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return acquire_replay_lease(resolved_root, model_id, replay_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.put("/api/v1/models/{model_id}/replays/{replay_id}/leases/{lease_id}")
    def renew_model_replay_lease(
        model_id: str,
        replay_id: str,
        lease_id: str,
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, int]:
        authorize(x_deepdeck_token)
        try:
            return renew_replay_lease(resolved_root, model_id, replay_id, lease_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.delete("/api/v1/models/{model_id}/replays/{replay_id}/leases/{lease_id}")
    def release_model_replay_lease(
        model_id: str,
        replay_id: str,
        lease_id: str,
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        authorize(x_deepdeck_token)
        try:
            return release_replay_lease(resolved_root, model_id, replay_id, lease_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/models/{model_id}/replays/{replay_id}/save")
    def permanently_save_model_replay(
        model_id: str,
        replay_id: str,
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        authorize(x_deepdeck_token)
        try:
            return save_replay_forever(resolved_root, model_id, replay_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.put("/api/v1/models/{model_id}/replays/{replay_id}/favorite")
    def favorite_model_replay(
        model_id: str,
        replay_id: str,
        payload: dict[str, Any],
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        authorize(x_deepdeck_token)
        if type(payload.get("favorite")) is not bool:
            raise HTTPException(status_code=422, detail="Choose whether this game is a favorite.")
        try:
            return set_replay_favorite(
                resolved_root, model_id, replay_id, favorite=payload["favorite"]
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ReplayStorageBusyError:
            raise
        except OSError as error:
            raise HTTPException(
                status_code=507,
                detail="The favorite could not be saved. Check available disk space.",
            ) from error

    @app.get("/api/v1/games")
    def active_local_games() -> dict[str, Any]:
        return {"items": manager.games()}

    @app.post("/api/v1/games/{game_id}/stop")
    def stop_game(
        game_id: str, x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            found = manager.cancel_game(game_id)
        except JobValidationError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        if not found:
            raise HTTPException(status_code=404, detail="Active game not found.")
        return found

    @app.get("/api/v1/catalog/decks")
    def deck_catalog(search: str = "", format: str = "legacy", page: int = 1) -> dict[str, Any]:
        try:
            return platform_decks(search, format, max(1, page))
        except CatalogAuthenticationError as error:
            local = cached_decks(resolved_root, search, format, max(1, page))
            if local["items"]:
                return local
            raise HTTPException(status_code=401, detail=str(error)) from error
        except CatalogError as error:
            local = cached_decks(resolved_root, search, format, max(1, page))
            if local["items"]:
                return {**local, "offline": True}
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.get("/api/v1/catalog/downloaded-decks")
    def downloaded_deck_catalog(format: str = "") -> dict[str, Any]:
        try:
            return {"items": downloaded_decks(resolved_root, format)}
        except CatalogError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/deck-workshop/decks")
    def workshop_decks() -> dict[str, Any]:
        return {"items": list_workshop_decks(resolved_root)}

    @app.get("/api/v1/deck-workshop/server-decks")
    def owned_site_decks() -> dict[str, Any]:
        try:
            return {"items": list_server_workspaces(resolved_root)}
        except (OSError, ValueError) as error:
            raise sync_error(error) from error

    @app.post("/api/v1/deck-workshop/server-decks/{workspace_id}/import")
    def import_owned_site_deck(
        workspace_id: str, x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return import_server_workspace(resolved_root, workspace_id)
        except (OSError, ValueError) as error:
            raise sync_error(error) from error

    @app.post("/api/v1/deck-workshop/decks/{deck_id}/sync")
    def upload_owned_site_deck(
        deck_id: str, payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return sync_deck(
                resolved_root,
                deck_id,
                force=bool(payload.get("replaceSiteDraft", False)),
                expected_version=payload.get("expectedVersion"),
            )
        except (OSError, ValueError) as error:
            raise sync_error(error) from error

    @app.post("/api/v1/deck-workshop/decks/{deck_id}/publish")
    def publish_owned_site_deck(
        deck_id: str, payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return publish_deck(
                resolved_root,
                deck_id,
                force=bool(payload.get("replaceSiteDraft", False)),
                expected_version=payload.get("expectedVersion"),
            )
        except (OSError, ValueError) as error:
            raise sync_error(error) from error

    @app.post("/api/v1/deck-workshop/decks/{deck_id}/pull")
    def pull_owned_site_deck(
        deck_id: str, payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            deck = find_workshop_deck(resolved_root, deck_id)
            workspace_id = str(deck.get("sync", {}).get("workspaceId", ""))
            return import_server_workspace(
                resolved_root,
                workspace_id,
                deck_id=deck_id,
                discard_local=bool(payload.get("replaceLocalDraft", False)),
                expected_version=payload.get("expectedVersion"),
            )
        except (OSError, ValueError) as error:
            raise sync_error(error) from error

    @app.get("/api/v1/deck-workshop/decks/{deck_id}")
    def workshop_deck(deck_id: str) -> dict[str, Any]:
        try:
            return find_workshop_deck(resolved_root, deck_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/v1/deck-workshop/decks", status_code=201)
    def create_local_deck(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return create_workshop_deck(resolved_root, payload)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/deck-workshop/from-download/{version_id}", status_code=201)
    def fork_local_deck(
        version_id: str, x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return fork_downloaded_deck(resolved_root, version_id)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.put("/api/v1/deck-workshop/decks/{deck_id}")
    def update_local_deck(
        deck_id: str,
        payload: dict[str, Any],
        x_deepdeck_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        from .deck_edits import DeckRevisionConflict

        try:
            return update_workshop_deck(resolved_root, deck_id, payload)
        except DeckRevisionConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.delete("/api/v1/deck-workshop/decks/{deck_id}")
    def remove_local_deck(
        deck_id: str, x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        for agent in list_programmatic_agents(resolved_root):
            if deck_id in agent.get("deckVersionIds", []):
                raise HTTPException(
                    status_code=409,
                    detail=f"Unlink this deck from {agent['name']} in the agent workshop before deleting it.",
                )
        try:
            return delete_workshop_deck(resolved_root, deck_id)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/v1/catalog/decks/{version_id}/download")
    def download_deck(
        version_id: str, x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        local = cached_deck_download(resolved_root, version_id)
        if local is not None:
            return local
        try:
            return download_platform_deck(resolved_root, version_id)
        except CatalogAuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.get("/api/v1/catalog/decks/{version_id}/cover")
    def deck_cover_artwork(version_id: str) -> dict[str, Any]:
        try:
            return deck_cover_presentation(resolved_root, version_id)
        except CatalogNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.get("/api/v1/catalog/decks/{version_id}/presentation")
    def deck_presentation(version_id: str) -> dict[str, Any]:
        try:
            return local_deck_presentation(resolved_root, version_id)
        except CatalogNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except CatalogError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.get("/api/scryfall-images/{image_path:path}", include_in_schema=False)
    def card_image(image_path: str) -> Response:
        try:
            content, content_type = scryfall_image(image_path)
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return Response(
            content=content,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )

    @app.get("/api/v1/catalog/cards/autocomplete")
    def card_name_autocomplete(query: str = "") -> dict[str, list[str]]:
        try:
            return {"data": scryfall_card_names(query)}
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.get("/api/v1/catalog/cards/artwork")
    def missing_card_artwork(
        name: str, token: bool = False, power: str = "", toughness: str = ""
    ) -> dict[str, Any]:
        try:
            return {"artwork": scryfall_card_artwork(name, token, power, toughness)}
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.get("/api/v1/training/deck-pool")
    def training_deck_pool() -> dict[str, Any]:
        path = resolved_root / ".deepdeck" / "training-deck-pool.json"
        if not path.is_file():
            return {"decks": []}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"decks": []}
        return value if isinstance(value, dict) else {"decks": []}

    @app.put("/api/v1/training/deck-pool")
    def save_training_deck_pool(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        decks = payload.get("decks", [])
        if (
            not isinstance(decks, list)
            or len(decks) > 100
            or not all(isinstance(deck, dict) and isinstance(deck.get("id"), str) for deck in decks)
        ):
            raise HTTPException(
                status_code=422, detail="A training pool may contain up to 100 valid decks."
            )
        path = resolved_root / ".deepdeck" / "training-deck-pool.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(".pending")
        value = {"decks": decks}
        pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pending.replace(path)
        return value

    @app.get("/api/v1/catalog/competitions")
    def competition_catalog() -> dict[str, Any]:
        try:
            account_api_key()
            return active_competitions()
        except CatalogAuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.get("/api/v1/catalog/local-decks")
    def local_deck_catalog(
        format: str = "legacy", engine_url: str = "http://127.0.0.1:8787"
    ) -> list[dict[str, str]]:
        try:
            return local_legal_decks(engine_url, format)
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        found = manager.get(job_id)
        if not found:
            raise HTTPException(status_code=404, detail="Job not found.")
        return found

    @app.post("/api/v1/jobs", status_code=202)
    def start_job(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return manager.create(payload)
        except JobValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/jobs/{job_id}/stop")
    def stop_job(
        job_id: str, x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        found = manager.stop(job_id)
        if not found:
            raise HTTPException(status_code=404, detail="Job not found.")
        return found

    source_frontend = resolved_root / "apps" / "learner-web" / "dist"
    packaged_frontend = Path(__file__).resolve().parent / "web"
    frontend = source_frontend if source_frontend.is_dir() else packaged_frontend
    assets = frontend / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend_route(path: str) -> FileResponse:
        index = frontend / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=503,
                detail="Frontend is not built. Run npm run build in apps/learner-web.",
            )
        return FileResponse(
            index,
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    app.add_middleware(
        LocalDiagnosticsMiddleware,
        store=diagnostics,
        routes=frozenset(route.path for route in app.routes if hasattr(route, "path")),
    )
    return app


_default_app_lock = threading.Lock()


def __getattr__(name: str) -> Any:
    """Keep the uvicorn :app entry point without opening a profile on import.

    Importing create_app for tests, desktop startup or tooling must not run
    competition/League recovery against the user's default workspace.
    """
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    with _default_app_lock:
        if "app" not in globals():
            globals()["app"] = create_app()
        return globals()["app"]
