"""One production-first target for account, catalog, SDK and replay traffic."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

DEFAULT_PLATFORM_URL = "https://www.deepdeckleague.com/api/v1"


def normalize_platform_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in {"https", "http"}
    ):
        raise ValueError(
            "The configured League URL must be an HTTP(S) API URL without credentials."
        )
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use HTTPS to connect to a hosted League account.")
    # This is the production site's canonical hostname, not a staging fallback.
    if parsed.hostname == "deepdeckleague.com" and parsed.port in {None, 443}:
        parsed = parsed._replace(scheme="https", netloc="www.deepdeckleague.com")
    return urlunsplit(parsed)


def platform_url() -> str:
    return normalize_platform_url(os.getenv("DEEPDECK_PLATFORM_URL", DEFAULT_PLATFORM_URL))


def agent_url() -> str:
    explicit = os.getenv("DEEPDECK_AGENT_URL", "").strip().rstrip("/")
    if not explicit:
        parsed = urlsplit(platform_url())
        return urlunsplit(
            parsed._replace(
                scheme="wss" if parsed.scheme == "https" else "ws",
                path=parsed.path + "/agents/connect",
            )
        )
    parsed = urlsplit(explicit)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in {"ws", "wss"}
    ):
        raise ValueError("The League agent URL must be a WebSocket URL without credentials.")
    if parsed.scheme == "ws" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use a secure WebSocket URL for a hosted League agent.")
    if parsed.hostname == "deepdeckleague.com" and parsed.port in {None, 443}:
        parsed = parsed._replace(scheme="wss", netloc="www.deepdeckleague.com")
    return urlunsplit(parsed)


def connection_status() -> dict[str, Any]:
    api_url = platform_url()
    parsed = urlsplit(api_url)
    environment = (
        "production"
        if parsed.hostname == "www.deepdeckleague.com"
        else "staging"
        if parsed.hostname == "staging.deepdeckleague.com"
        else "custom"
    )
    result: dict[str, Any] = {
        "apiUrl": api_url,
        "siteUrl": api_url.removesuffix("/api/v1").removesuffix("/v1"),
        "environment": environment,
        "reachable": False,
        "releaseProfile": None,
        "capabilities": [],
        "naturalCardSearchEnabled": False,
        "reason": "",
    }
    try:
        with httpx.Client(timeout=8, follow_redirects=False) as client:
            response = client.get(api_url + "/runtime-config")
        response.raise_for_status()
        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("capabilities"), list):
            raise ValueError("The League did not return its supported capabilities.")
        result.update(
            {
                "reachable": True,
                "releaseProfile": data.get("releaseProfile"),
                "capabilities": data["capabilities"],
                "naturalCardSearchEnabled": bool(data.get("naturalCardSearchEnabled")),
                "reason": "Production server reachable."
                if environment == "production"
                else "League server reachable.",
            }
        )
    except (httpx.HTTPError, ValueError):
        result["reason"] = (
            "The configured League server is unavailable. Local play remains available; no alternate server was selected."
        )
    return result
