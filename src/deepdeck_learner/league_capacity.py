"""Read League seat capacity; subscription policy belongs exclusively to DDL."""

from __future__ import annotations

import os
from typing import Any

import httpx

from .league_connection import platform_url


def league_capacity() -> dict[str, Any]:
    unavailable: dict[str, Any] = {
        "status": "unavailable",
        "limit": None,
        "used": None,
        "available": None,
        "reason": "Connect your League account to see its available places.",
    }
    key = os.getenv("DEEPDECK_API_KEY", "").strip()
    if not key:
        return unavailable
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.get(
                f"{platform_url()}/matchmaking/capacity",
                headers={"Authorization": f"Bearer {key}"},
            )
        if response.status_code in {404, 405, 501}:
            return {
                **unavailable,
                "status": "unsupported",
                "reason": "This server does not report League capacity yet.",
            }
        if response.status_code in {401, 403}:
            return {
                **unavailable,
                "reason": "Connect an agent key with matchmaking permission to see League capacity.",
            }
        response.raise_for_status()
        body = response.json()
        value = body.get("data") if isinstance(body, dict) else None
        if not isinstance(value, dict) or any(
            type(value.get(field)) is not int or value[field] < 0
            for field in ("limit", "used", "available")
        ):
            raise ValueError("Invalid capacity response")
        if value["available"] != max(0, value["limit"] - value["used"]):
            raise ValueError("Inconsistent capacity response")
        # Deliberately select numeric fields, not a provider's membership data.
        return {
            "status": "ready",
            **{field: value[field] for field in ("limit", "used", "available")},
            "reason": "Capacity supplied and enforced by the League server.",
        }
    except (httpx.HTTPError, ValueError):
        # Never infer a membership tier or manufacture a quota on a failed read.
        return {
            **unavailable,
            "reason": "League capacity is temporarily unavailable. Try again shortly.",
        }
