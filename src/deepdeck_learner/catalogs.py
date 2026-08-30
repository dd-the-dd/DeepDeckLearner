from __future__ import annotations

import os
from typing import Any

import httpx

from .jobs import is_loopback_url


class CatalogError(RuntimeError):
    pass


class CatalogAuthenticationError(CatalogError):
    pass


def account_api_key() -> str:
    api_key = os.getenv("DEEPDECK_API_KEY", "").strip()
    if not api_key:
        raise CatalogAuthenticationError(
            "Add DEEPDECK_API_KEY to the project .env and restart DeepDeckLearner."
        )
    return api_key


def _platform_url() -> str:
    return os.getenv("DEEPDECK_PLATFORM_URL", "https://staging.deepdeckleague.com/api/v1").rstrip(
        "/"
    )


def _data(response: httpx.Response) -> Any:
    try:
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise CatalogError(f"The deck catalog request failed: {error}") from error
    if not isinstance(body, dict) or "data" not in body:
        raise CatalogError("The deck catalog returned an unsupported response.")
    return body["data"]


def platform_decks(search: str, game_format: str, page: int = 1) -> dict[str, Any]:
    if game_format not in {"legacy", "commander"}:
        raise CatalogError("Format must be legacy or commander.")
    with httpx.Client(timeout=10.0) as client:
        response = client.get(
            f"{_platform_url()}/agents/catalog/decks",
            headers={"Authorization": f"Bearer {account_api_key()}"},
            params={
                "page": page,
                "page_size": 12,
                "search": search.strip() or None,
                "format": game_format,
            },
        )
    data = _data(response)
    if not isinstance(data, dict):
        raise CatalogError("The deck catalog did not return a page.")
    return data


def active_competitions() -> dict[str, Any]:
    with httpx.Client(timeout=10.0) as client:
        response = client.get(
            f"{_platform_url()}/agents/catalog/competitions",
            headers={"Authorization": f"Bearer {account_api_key()}"},
            params={"status": "active", "page": 1, "page_size": 100},
        )
    data = _data(response)
    if not isinstance(data, dict):
        raise CatalogError("The competition catalog did not return a page.")
    return data


def platform_training_lots() -> list[dict[str, Any]]:
    with httpx.Client(timeout=10.0) as client:
        response = client.get(
            f"{_platform_url()}/agents/training-lots",
            headers={"Authorization": f"Bearer {account_api_key()}"},
        )
    data = _data(response)
    if not isinstance(data, list):
        raise CatalogError("The training lot catalog did not return a list.")
    return [lot for lot in data if isinstance(lot, dict)]


def download_training_lot(lot_id: str) -> tuple[dict[str, Any], int]:
    with httpx.Client(timeout=30.0) as client:
        response = client.get(
            f"{_platform_url()}/agents/training-lots/{lot_id}/manifest",
            headers={"Authorization": f"Bearer {account_api_key()}"},
        )
    downloaded_bytes = len(response.content)
    data = _data(response)
    if not isinstance(data, dict) or data.get("schema") != "deepdeck-training-lot/v1":
        raise CatalogError("The training lot manifest uses an unsupported schema.")
    return data, downloaded_bytes


def verify_account_api_key(api_key: str) -> None:
    with httpx.Client(timeout=10.0) as client:
        response = client.get(
            f"{_platform_url()}/agents/catalog/competitions",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"status": "active", "page": 1, "page_size": 1},
        )
    if response.status_code in {401, 403}:
        raise CatalogAuthenticationError("Deep Deck League rejected this API key.")
    _data(response)


def local_legal_decks(engine_url: str, game_format: str) -> list[dict[str, str]]:
    if not is_loopback_url(engine_url):
        raise CatalogError("Local deck discovery requires a loopback Engine URL.")
    if game_format not in {"legacy", "commander"}:
        raise CatalogError("Format must be legacy or commander.")
    try:
        response = httpx.post(
            f"{engine_url.rstrip('/')}/game/decks/legal",
            json={"gameMode": game_format, "openingHandSize": 7},
            timeout=20.0,
        )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise CatalogError(f"The local Engine deck catalog failed: {error}") from error
    if body.get("schemaVersion") != "mtg-legal-deck-catalog/v1":
        raise CatalogError("The local Engine returned an unsupported deck catalog.")
    decks = body.get("decks")
    if not isinstance(decks, list):
        raise CatalogError("The local Engine deck catalog has no deck list.")
    return [deck for deck in decks if isinstance(deck, dict)]
