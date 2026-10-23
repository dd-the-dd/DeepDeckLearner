"""Public League research shared by manual controls and the deck copilot."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from time import monotonic
from typing import Any
from urllib.parse import urlencode

import httpx

from .catalogs import CatalogError
from .league_connection import platform_url


def _public_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        # Public research does not require or forward a saved account/LLM key.
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.get(platform_url() + "/" + path, params=params)
        body = response.json()
        if response.is_error:
            error = body.get("error", {}) if isinstance(body, dict) else {}
            message = error.get("message") if isinstance(error, dict) else None
            raise CatalogError(
                message or f"The League search returned HTTP {response.status_code}."
            )
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise CatalogError("The League returned an unsupported search result.")
        return data
    except (httpx.HTTPError, ValueError) as error:
        raise CatalogError("League search is unavailable. Your local deck is unchanged.") from error


def _format(value: Any) -> str:
    if value not in {"legacy", "commander"}:
        raise ValueError("Choose Legacy or Commander for research.")
    return str(value)


def card_query(arguments: dict[str, Any]) -> str:
    terms = []
    query = str(arguments.get("query", "")).strip()
    if query:
        terms.append(f"({query})")
    for field, operator in (("oracle_contains", "o:"), ("semantic_text", "o~")):
        if arguments.get(field):
            terms.append(operator + json.dumps(str(arguments[field]).strip(), ensure_ascii=False))
    types = arguments.get("types", [])
    if not isinstance(types, list) or len(types) > 10:
        raise ValueError("Choose at most 10 card types.")
    if types:
        terms.append(
            "("
            + " OR ".join("t:" + json.dumps(str(value), ensure_ascii=False) for value in types)
            + ")"
        )
    colors = str(arguments.get("color_identity", "")).strip().upper()
    if colors:
        if any(color not in "WUBRGC" for color in colors) or "C" in colors and colors != "C":
            raise ValueError("Color identity must use W, U, B, R, G, or C.")
        # Production supports color :, = and !=, not <=. Excluding colors
        # outside the deck identity also retains legal colorless cards.
        if arguments.get("identity_match", "within") == "exact":
            terms.append("id=" + colors)
        elif colors == "C":
            terms.append("id:C")
        else:
            terms.append(
                " ".join(f"id!={color}" for color in "WUBRG" if color not in colors)
                or "(id:C OR id!=C)"
            )
    for field, operator in (("min_mana_value", "mv>="), ("max_mana_value", "mv<=")):
        if arguments.get(field) is not None:
            value = float(arguments[field])
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError("Mana-value bounds must be between 0 and 100.")
            terms.append(operator + f"{value:g}")
    if (
        arguments.get("min_mana_value") is not None
        and arguments.get("max_mana_value") is not None
        and float(arguments["min_mana_value"]) > float(arguments["max_mana_value"])
    ):
        raise ValueError("Minimum mana value cannot exceed the maximum.")
    result = " ".join(terms)
    if not 1 <= len(result.encode("utf-8")) <= 512:
        raise ValueError("Enter a card search of at most 512 UTF-8 bytes.")
    return result


def card_search_plan(arguments: dict[str, Any]) -> dict[str, Any]:
    """One validated plan shared by the assistant and the manual search controls."""
    game_format = _format(arguments.get("format", "legacy"))
    commander = arguments.get("commander_only", False)
    if type(commander) is not bool:
        raise ValueError("Commander eligibility must be true or false.")
    if commander and game_format != "commander":
        raise ValueError("Choose Commander format when searching for a commander.")
    count = arguments.get("color_count")
    if count is not None and (type(count) is not int or not 0 <= count <= 5):
        raise ValueError("Choose zero to five color-identity colors.")
    match = arguments.get("identity_match", "within")
    if match not in {"within", "exact"}:
        raise ValueError("Choose within or exact color identity.")
    filters = {
        "format": game_format,
        "query": str(arguments.get("query", "")).strip(),
        "oracle_contains": str(arguments.get("oracle_contains", "")).strip(),
        "semantic_text": str(arguments.get("semantic_text", "")).strip(),
        "types": arguments.get("types", []),
        "color_identity": str(arguments.get("color_identity", "")).strip().upper(),
        "identity_match": match,
        "commander_only": commander,
    }
    for field in ("min_mana_value", "max_mana_value", "color_count"):
        if arguments.get(field) is not None:
            filters[field] = arguments[field]
    if count is not None and filters["color_identity"]:
        size = len(set(filters["color_identity"]) - {"C"})
        if count > size or match == "exact" and count != size:
            raise ValueError("The number of colors conflicts with the chosen identity.")
    # Extra constraints can stand alone; a raw query is not mandatory.
    query = card_query(
        {
            **filters,
            "query": filters["query"]
            or ("f:" + game_format if commander or count is not None else ""),
        }
    )
    canonical = card_query(
        {
            **filters,
            "query": filters["query"] or "f:" + game_format,
            "color_identity": "",
            "semantic_text": "",
        }
    )
    if filters["query"]:
        canonical += " f:" + game_format
    if filters["color_identity"]:
        canonical += (" id=" if match == "exact" else " id<=") + filters["color_identity"]
    if commander:
        canonical += " is:commander"
    if count is not None:
        canonical += f" id={count}"
    advanced = commander or count is not None
    if advanced and filters["semantic_text"]:
        raise ValueError(
            "Commander/color-count searches use Scryfall; semantic search is not available there."
        )
    if len(canonical.encode("utf-8")) > 512:
        raise ValueError("The complete card search must fit in 512 UTF-8 bytes.")
    return {
        "filters": filters,
        "query": canonical if advanced else query,
        "scryfallQuery": canonical,
        "scryfallUrl": "https://scryfall.com/search?" + urlencode({"q": canonical}),
        "source": "scryfall" if advanced else "league",
    }


@lru_cache(maxsize=128)
def _scryfall_page(query: str, page: int, cache_window: int) -> dict[str, Any]:
    """Bounded, short-lived public metadata cache; no account credentials."""
    del cache_window
    try:
        response = httpx.get(
            "https://api.scryfall.com/cards/search",
            params={"q": query, "page": page, "unique": "cards"},
            headers={"Accept": "application/json", "User-Agent": "DeepDeckLearner/0.2"},
            timeout=20,
            follow_redirects=False,
        )
        if response.status_code == 404:
            return {"items": [], "hasMore": False}
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise ValueError("Unsupported card results")
        return {
            "items": body["data"],
            "hasMore": body.get("has_more") is True,
            "pagination": {"page": page, "totalItems": body.get("total_cards", 0)},
        }
    except (httpx.HTTPError, ValueError) as error:
        raise CatalogError(
            "Scryfall search is unavailable. Your deck is unchanged; no broader search was substituted."
        ) from error


def search_cards(arguments: dict[str, Any]) -> dict[str, Any]:
    plan = card_search_plan(arguments)
    query = plan["query"]
    game_format = _format(arguments.get("format", "legacy"))
    page = arguments.get("page", 1)
    offset = arguments.get("page_offset", 0)
    if type(page) is not int or not 1 <= page <= 10_000:
        raise ValueError("Card search page is out of range.")
    if type(offset) is not int or not 0 <= offset <= 174:
        raise ValueError("Card search page offset must be a whole number between 0 and 174.")
    data = (
        _scryfall_page(query, page, int(monotonic() // 300))
        if plan["source"] == "scryfall"
        else _public_get("cards/search", {"q": query, "format": game_format, "page": page})
    )
    items = data["items"]
    if len(items) > 175:
        raise CatalogError("The League returned an unsupported card page size.")
    if offset and offset >= len(items):
        raise CatalogError(
            "These search results changed. Start the search again to browse the current cards."
        )
    window = items[offset : offset + 20]
    next_window = (
        {"page": page, "page_offset": offset + len(window)}
        if offset + len(window) < len(items)
        else {"page": page + 1, "page_offset": 0}
        if data.get("hasMore") is True and page < 10_000
        else None
    )
    return {
        **data,
        "items": window,
        **plan,
        "omittedItems": max(0, len(items) - len(window)),
        "resultWindow": {
            "page": page,
            "pageOffset": offset,
            "shown": len(window),
            "totalOnPage": len(items),
            "next": next_window,
            "navigationLimitReached": page == 10_000
            and data.get("hasMore") is True
            and next_window is None,
        },
    }


def search_decks(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    if len(query) > 512:
        raise ValueError("Deck search is too long.")
    page = int(arguments.get("page", 1))
    if not 1 <= page <= 100:
        raise ValueError("Choose a deck search page between 1 and 100.")
    semantic = bool(arguments.get("semantic", False))
    if semantic and not query:
        raise ValueError("Describe a deck before using semantic search.")
    colors = str(arguments.get("colors", "")).strip().upper()
    if any(value not in "WUBRGC, " for value in colors):
        raise ValueError("Deck colors must use W, U, B, R, G, or C.")
    data = _public_get(
        "decks",
        {
            "search": query,
            "format": _format(arguments.get("format", "legacy")),
            "colors": colors or None,
            "semantic": str(semantic).lower(),
            "page": page,
            "page_size": 12,
        },
    )
    return {**data, "query": query, "source": "league", "semantic": semantic}
