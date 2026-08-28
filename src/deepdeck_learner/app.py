from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .catalogs import CatalogError, active_competitions, local_legal_decks, platform_decks
from .jobs import JobManager, JobValidationError
from .status import capability_status, project_root


def create_app(root: Path | None = None) -> FastAPI:
    resolved_root = (root or project_root()).resolve()
    manager = JobManager(resolved_root)
    session_token = secrets.token_urlsafe(32)
    app = FastAPI(title="DeepDeckLearner local controller", version="1.0")
    app.state.manager = manager
    app.state.session_token = session_token

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
    def status(engine_url: str = "http://127.0.0.1:8787") -> dict[str, Any]:
        return capability_status(resolved_root, engine_url)

    @app.get("/api/v1/jobs")
    def jobs() -> list[dict[str, Any]]:
        return manager.list_jobs()

    @app.get("/api/v1/catalog/decks")
    def deck_catalog(search: str = "", format: str = "legacy", page: int = 1) -> dict[str, Any]:
        try:
            return platform_decks(search, format, max(1, page))
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.get("/api/v1/catalog/competitions")
    def competition_catalog() -> dict[str, Any]:
        try:
            return active_competitions()
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
        return FileResponse(index)

    return app


app = create_app()
