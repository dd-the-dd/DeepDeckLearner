from __future__ import annotations

import json
import socket
import uuid
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .access import LocalAccessManager, request_is_host
from .bundles import deck_bundles
from .catalogs import (
    CatalogAuthenticationError,
    CatalogError,
    account_api_key,
    active_competitions,
    download_training_lot,
    local_legal_decks,
    platform_decks,
    platform_training_lots,
    verify_account_api_key,
)
from .jobs import JobManager, JobValidationError
from .secret_store import AccountSecretStore
from .settings import (
    NetworkSettings,
    load_network_settings,
    load_training_settings,
    save_network_settings,
    save_training_settings,
    training_settings_from_payload,
)
from .status import capability_status, project_root


def _safe_browser_origin(origin: str, lan_enabled: bool) -> bool:
    host = urlsplit(origin).hostname
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or (lan_enabled and (address.is_private or address.is_link_local))


def _lan_addresses(port: int) -> list[str]:
    addresses: set[str] = set()
    preferred: str | None = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("1.1.1.1", 80))
            preferred = probe.getsockname()[0]
    except OSError:
        pass
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = result[4][0]
            parsed = ip_address(address)
            if not parsed.is_loopback and (parsed.is_private or parsed.is_link_local):
                addresses.add(f"http://{address}:{port}")
    except OSError:
        pass
    def address_order(value: str) -> tuple[int, str]:
        host = urlsplit(value).hostname or ""
        parsed = ip_address(host)
        if parsed in ip_network("192.168.0.0/16"):
            rank = 0
        elif preferred is not None and host == preferred:
            rank = 1
        else:
            rank = 2
        return (rank, value)

    return sorted(addresses, key=address_order)


def create_app(root: Path | None = None) -> FastAPI:
    resolved_root = (root or project_root()).resolve()
    manager = JobManager(resolved_root)
    access = LocalAccessManager()
    secret_store = AccountSecretStore(resolved_root)
    secret_store.load_into_environment()
    network = load_network_settings(resolved_root)
    app = FastAPI(
        title="DeepDeckLearner local controller",
        version="1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.manager = manager
    app.state.access = access
    app.state.secret_store = secret_store
    app.state.network = network
    app.state.restart_callback = None

    @app.middleware("http")
    async def local_security(request: Request, call_next):  # type: ignore[no-untyped-def]
        origin = request.headers.get("origin")
        if origin and not _safe_browser_origin(origin, network.mode == "lan"):
            return JSONResponse(status_code=403, content={"detail": "Untrusted browser origin."})
        public_paths = {
            "/api/v1/health",
            "/api/v1/session",
        }
        if request.url.path.startswith("/api/v1/") and request.url.path not in public_paths:
            session = access.resolve(request.headers.get("x-deepdeck-token"))
            if session is None:
                return JSONResponse(
                    status_code=403, content={"detail": "A local session is required."}
                )
            request.state.local_session = session
        return await call_next(request)

    def require_owner(request: Request) -> None:
        session = getattr(request.state, "local_session", None)
        if session is None or session.role != "owner" or not request_is_host(request):
            raise HTTPException(
                status_code=403,
                detail="This setting can only be changed from this computer.",
            )

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/session")
    def session(request: Request) -> dict[str, Any]:
        existing = access.resolve(request.headers.get("x-deepdeck-token"))
        if existing is not None:
            return {"token": existing.token, "session": existing.public()}
        issued = access.issue_owner() if request_is_host(request) else access.issue_lan()
        return {"token": issued.token, "session": issued.public()}

    @app.get("/api/v1/status")
    def status(engine_url: str = "http://127.0.0.1:8787") -> dict[str, Any]:
        return capability_status(resolved_root, engine_url)

    @app.get("/api/v1/settings")
    def settings(request: Request) -> dict[str, Any]:
        session = request.state.local_session
        secret = secret_store.status()
        return {
            "network": {
                "mode": network.mode,
                "port": network.port,
                "restart_required": False,
                "lan_urls": _lan_addresses(network.port),
            },
            "account": {
                "configured": secret.configured,
                "provider": secret.provider,
                "externally_managed": secret.externally_managed,
            },
            "access": {
                "role": session.role,
            },
        }

    @app.put("/api/v1/settings/api-key")
    async def save_api_key(request: Request) -> dict[str, Any]:
        require_owner(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Invalid API-key request.")
        value = str(payload.get("api_key", ""))
        try:
            from .secret_store import validate_api_key

            verified = validate_api_key(value)
            verify_account_api_key(verified)
            result = secret_store.save(verified)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except CatalogAuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "configured": result.configured,
            "provider": result.provider,
            "externally_managed": result.externally_managed,
        }

    @app.delete("/api/v1/settings/api-key")
    def delete_api_key(request: Request) -> dict[str, Any]:
        require_owner(request)
        try:
            result = secret_store.delete()
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "configured": result.configured,
            "provider": result.provider,
            "externally_managed": result.externally_managed,
        }

    @app.put("/api/v1/settings/network")
    async def update_network(request: Request) -> dict[str, Any]:
        require_owner(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Invalid network settings.")
        mode = str(payload.get("mode", ""))
        port = payload.get("port")
        if mode not in {"local", "lan"}:
            raise HTTPException(status_code=422, detail="Mode must be local or lan.")
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
            raise HTTPException(status_code=422, detail="Port must be between 1024 and 65535.")
        updated = NetworkSettings(mode=mode, port=port)
        save_network_settings(resolved_root, updated)
        return {"mode": mode, "port": port, "restart_required": updated != network}

    @app.post("/api/v1/settings/restart", status_code=202)
    def restart(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
        require_owner(request)
        callback = app.state.restart_callback
        if callback is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Automatic restart is unavailable in development mode. "
                    "Restart the controller terminal."
                ),
            )
        background_tasks.add_task(callback)
        return {"status": "restarting"}

    @app.get("/api/v1/training-profile")
    def training_profile() -> dict[str, Any]:
        configured = load_training_settings(resolved_root)
        return {
            "model": configured.model,
            "format": configured.format,
            "decks": [
                {
                    "id": deck.id,
                    "name": deck.name,
                    "version": deck.version,
                    "format": deck.format,
                }
                for deck in configured.decks
            ],
        }

    @app.put("/api/v1/training-profile")
    async def update_training_profile(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            configured = training_settings_from_payload(payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        save_training_settings(resolved_root, configured)
        return {
            "model": configured.model,
            "format": configured.format,
            "decks": [
                {
                    "id": deck.id,
                    "name": deck.name,
                    "version": deck.version,
                    "format": deck.format,
                }
                for deck in configured.decks
            ],
        }

    @app.get("/api/v1/jobs")
    def jobs() -> list[dict[str, Any]]:
        return manager.list_jobs()

    @app.get("/api/v1/catalog/decks")
    def deck_catalog(search: str = "", format: str = "legacy", page: int = 1) -> dict[str, Any]:
        try:
            account_api_key()
            return platform_decks(search, format, max(1, page))
        except CatalogAuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.get("/api/v1/catalog/deck-bundles")
    def bundle_catalog(format: str = "legacy") -> dict[str, Any]:
        if format not in {"legacy", "commander"}:
            raise HTTPException(status_code=422, detail="Format must be legacy or commander.")
        return {"items": deck_bundles(format)}

    @app.get("/api/v1/catalog/training-lots")
    def training_lot_catalog() -> dict[str, Any]:
        try:
            account_api_key()
            return {"items": platform_training_lots()}
        except CatalogAuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.post("/api/v1/catalog/training-lots/{lot_id}/download")
    def load_training_lot(lot_id: str) -> dict[str, Any]:
        try:
            normalized_id = str(uuid.UUID(lot_id))
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Training lot id is invalid.") from error
        try:
            account_api_key()
            manifest, downloaded_bytes = download_training_lot(normalized_id)
        except CatalogAuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        destination = resolved_root / ".deepdeck" / "training-lots" / f"{normalized_id}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temporary.replace(destination)
        decks = [
            {
                "id": deck.get("id"),
                "name": deck.get("name"),
                "version": deck.get("version"),
                "format": manifest.get("format"),
                "playableCardCount": sum(
                    int(card.get("quantity", 0)) for card in deck.get("cards", [])
                    if isinstance(card, dict)
                ),
            }
            for deck in manifest.get("decks", []) if isinstance(deck, dict)
        ]
        return {
            "id": manifest.get("id"),
            "name": manifest.get("name"),
            "format": manifest.get("format"),
            "decks": decks,
            "downloadedBytes": downloaded_bytes,
            "path": str(destination),
        }

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
    def start_job(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return manager.create(payload)
        except JobValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/jobs/{job_id}/stop")
    def stop_job(job_id: str) -> dict[str, Any]:
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
        return FileResponse(index)

    return app


app = create_app()
