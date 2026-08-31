from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .catalogs import (
    CatalogAuthenticationError,
    CatalogError,
    CatalogNotFoundError,
    account_api_key,
    active_competitions,
    download_platform_deck,
    local_deck_presentation,
    local_legal_decks,
    platform_decks,
    scryfall_image,
)
from .jobs import JobManager, JobValidationError
from .models import deck_statistics, local_models, training_statistics
from .resources import (
    delete_model_run,
    find_model_run,
    load_resource_plan,
    save_resource_plan,
)
from .settings import load_api_key, save_api_key
from .status import capability_status, project_root


def create_app(root: Path | None = None) -> FastAPI:
    resolved_root = (root or project_root()).resolve()
    load_api_key(resolved_root)
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

    @app.post("/api/v1/settings/api-key")
    def configure_api_key(
        payload: dict[str, Any], x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, bool]:
        authorize(x_deepdeck_token)
        try:
            save_api_key(resolved_root, str(payload.get("api_key", "")))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"configured": True}

    @app.get("/api/v1/jobs")
    def jobs() -> list[dict[str, Any]]:
        return manager.list_jobs()

    @app.get("/api/v1/models")
    def models() -> dict[str, Any]:
        return {"items": local_models(resolved_root, manager.list_jobs())}

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

    @app.get("/api/v1/statistics/training")
    def local_training_statistics() -> dict[str, Any]:
        return {"items": training_statistics(resolved_root)}

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
            account_api_key()
            return platform_decks(search, format, max(1, page))
        except CatalogAuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except CatalogError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.post("/api/v1/catalog/decks/{version_id}/download")
    def download_deck(
        version_id: str, x_deepdeck_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_deepdeck_token)
        try:
            return download_platform_deck(resolved_root, version_id)
        except CatalogAuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
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
        return FileResponse(index)

    return app


app = create_app()
