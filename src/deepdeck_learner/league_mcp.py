"""Optional, read-only League MCP connection owned by the local Agents SDK."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, cast

from agents.mcp import MCPServerStreamableHttp
from agents.mcp._compat import MCP_HTTPX
from mcp import Tool as MCPTool
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from .league_account import LeagueAuthenticationError, research_headers
from .league_connection import platform_url

ALLOWED_TOOLS = frozenset({"search_cards", "list_decks", "get_deck_workspace", "list_card_tags"})
PREFIX = "league_"


def mcp_url() -> str:
    base = platform_url()
    if not base.endswith("/api/v1"):
        raise ValueError("League MCP requires an API base URL ending with /api/v1.")
    return base.removesuffix("/api/v1") + "/mcp"


def _http_client_factory(
    headers: dict[str, str] | None = None, timeout: Any = None, auth: Any = None
) -> Any:
    # The SDK selects httpx/httpx2 for the installed MCP version. In either case,
    # do not redirect the League cookie or token to a login page or another host.
    return MCP_HTTPX.AsyncClient(
        headers=headers, timeout=timeout, auth=auth, follow_redirects=False
    )


def _bounded(value: Any) -> Any:
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        value = dict(value)
        items = value["items"]
        if len(items) > 20:
            value["items"] = items[:20]
            value["omittedItems"] = len(items) - 20
            value["truncated"] = True
    if len(json.dumps(value, default=str)) > 100_000:
        return {"error": "The tool result was too large. Narrow the search or use a smaller page."}
    return value


class LeagueResearchMCP(MCPServerStreamableHttp):
    def __init__(self, root: Path, events: list[dict[str, Any]]) -> None:
        super().__init__(
            name="DeepDeck League research",
            params={
                "url": mcp_url(),
                "headers": research_headers(root),
                "timeout": 8.0,
                "sse_read_timeout": 20.0,
                "httpx_client_factory": _http_client_factory,
            },
            client_session_timeout_seconds=12,
            cache_tools_list=True,
            tool_filter={"allowed_tool_names": sorted(ALLOWED_TOOLS)},
            use_structured_content=True,
        )
        self.events = events

    async def list_tools(self, run_context: Any = None, agent: Any = None) -> list[MCPTool]:
        tools = await super().list_tools(run_context, agent)
        # Prefixes prevent collisions with local list_decks/search_cards tools.
        return [
            tool.model_copy(update={"name": PREFIX + tool.name}, deep=True)
            for tool in tools
            if tool.name in ALLOWED_TOOLS
        ]

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None, meta: dict[str, Any] | None = None
    ) -> CallToolResult:
        native_name = tool_name.removeprefix(PREFIX)
        if not tool_name.startswith(PREFIX) or native_name not in ALLOWED_TOOLS:
            return CallToolResult.model_validate(
                {
                    "content": [
                        TextContent(
                            type="text",
                            text="This League tool is not allowed. Edit the local draft instead.",
                        )
                    ],
                    "isError": True,
                }
            )
        try:
            result = await super().call_tool(native_name, arguments, meta)
            body = result.model_dump(by_alias=True)
            value = body.get("structuredContent")
            if value is None:
                texts = [
                    item.get("text", "")
                    for item in body.get("content", [])
                    if item.get("type") == "text"
                ]
                try:
                    value = json.loads("\n".join(texts))
                except ValueError:
                    value = {"message": "\n".join(texts)}
            # The site's native MCP wraps successful structured results in data.
            if isinstance(value, dict) and set(value) == {"data"}:
                value = value["data"]
            if isinstance(value, list):
                value = {"items": value}
            elif not isinstance(value, dict):
                value = {"message": value}
            value = _bounded(value)
            failed = bool(body.get("isError")) or isinstance(value, dict) and "error" in value
        except Exception:
            # This optional transport must not leak headers or block local tools.
            value = {"error": "League research failed. Local deck editing is still available."}
            failed = True
        detail = value.get("error", value) if failed and isinstance(value, dict) else None
        if isinstance(detail, dict):
            detail = detail.get("message", "League research did not complete.")
        self.events.append(
            {
                "tool": tool_name,
                "transport": "mcp",
                "ok": not failed,
                "arguments": arguments or {},
                "result": value,
                **({"error": str(detail)} if failed else {}),
            }
        )
        return CallToolResult.model_validate(
            {
                "content": [TextContent(type="text", text=json.dumps(value, default=str))],
                "structuredContent": value,
                "isError": failed,
            }
        )


def _connection_diagnostic(error: BaseException) -> dict[str, Any]:
    """Surface the server's denial without leaking transport headers or secrets."""
    pending = [error]
    seen: set[int] = set()
    while pending and len(seen) < 32:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, LeagueAuthenticationError):
            return {
                "errorCode": "credential_required",
                "error": "Add a League account API key in Settings, then test MCP access. "
                "Local editing and public catalog search remain available.",
            }
        if isinstance(current, ValidationError) and any(
            issue.get("type") == "missing" and "resultType" in issue.get("loc", ())
            for issue in current.errors(
                include_input=False, include_context=False, include_url=False
            )
        ):
            return {
                "errorCode": "protocol_mismatch",
                "error": "League MCP returned an incompatible response format "
                "(missing resultType). A compatibility update is needed on the server or client. "
                "This is not a request to sign in again or change your membership. "
                "Your saved key was kept; local editing and public catalog search "
                "remain available.",
            }
        response = getattr(current, "response", None)
        status = getattr(response, "status_code", None)
        if response is not None and isinstance(status, int):
            message = ""
            detail: Any = {}
            try:
                body = response.json()
                detail = body.get("error", {}) if isinstance(body, dict) else {}
                candidate = detail.get("message") if isinstance(detail, dict) else None
                if isinstance(candidate, str):
                    message = re.sub(r"[\x00-\x1f\x7f]", " ", candidate[:1000])
                    message = re.sub(r"ddl_agent_[A-Za-z0-9_-]+", "[redacted key]", message)
                    message = re.sub(
                        r"(?i)(?:Bearer\s+|ddl_session=)[^\s;,]+", "[redacted credential]", message
                    )
                    message = re.sub(r"https?://[^\s]+", "[server URL]", message)[:500]
            except (ValueError, TypeError, AttributeError):
                pass
            membership_required = status == 403 and "patreon" in message.lower()
            if status == 429:
                data = detail.get("data", {}) if isinstance(detail, dict) else {}
                quota = data.get("quota", {}) if isinstance(data, dict) else {}
                daily = isinstance(quota, dict) and quota.get("reason") == "daily_quota"
                return {
                    "errorCode": "daily_quota_reached" if daily else "rate_limited",
                    "httpStatus": status,
                    "error": (
                        "Your daily League MCP allowance has been reached. It resets at 00:00 UTC. "
                        "Patreon provides a higher allowance. "
                        if daily
                        else "League MCP is rate-limited. Wait for the server's Retry-After interval before trying again. "
                    )
                    + "Your saved key is still valid; do not reconnect or create a replacement key. "
                    "Local editing and public catalog search remain available.",
                }
            action = (
                "View Patreon options on the League support page. If you already subscribe, "
                "sign in to your League account and connect Patreon, then test again."
                if membership_required
                else "MCP permission denied. Manage account access on the League website."
                if status == 403
                else "Key expired, revoked or missing a required scope. Check it on League."
                if status == 401
                else "The MCP server could not complete this check. Retry with your saved key."
            )
            return {
                "errorCode": "membership_required"
                if membership_required
                else "access_denied"
                if status == 403
                else "credential_rejected"
                if status == 401
                else "server_unavailable",
                "httpStatus": status,
                "error": f"League MCP returned HTTP {status}. {message} {action} "
                "Local editing and public catalog search remain available.",
            }
        for nested in (
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
            *getattr(current, "exceptions", ()),
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return {
        "errorCode": "connection_unavailable",
        "error": "League MCP is unavailable. Check the server connection and retry. "
        "Your saved key was kept. Local editing and public catalog search remain available.",
    }


async def _diagnose_connection(root: Path, error: BaseException) -> dict[str, Any]:
    diagnostic = _connection_diagnostic(error)
    if diagnostic["errorCode"] != "connection_unavailable":
        return diagnostic
    # Some SDK versions discard the HTTP exception while initializing. A read-only
    # ping recovers the auth refusal, without creating a session or invoking a tool.
    try:
        async with _http_client_factory(headers=research_headers(root), timeout=8.0) as client:
            response = await client.post(
                mcp_url(),
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": "access-check", "method": "ping"},
            )
            response.raise_for_status()
    except Exception as probe_error:
        return _connection_diagnostic(probe_error)
    return diagnostic


@asynccontextmanager
async def league_research(
    root: Path, enabled: bool, events: list[dict[str, Any]]
) -> AsyncIterator[LeagueResearchMCP | None]:
    if not enabled:
        yield None
        return
    server: LeagueResearchMCP | None = None
    try:
        server = LeagueResearchMCP(root, events)
        await cast(Any, server).connect()
        tools = await server.list_tools()
    except Exception as error:
        if server is not None:
            with suppress(Exception):
                await cast(Any, server).cleanup()
        events.append(
            {
                "tool": "league_mcp_connection",
                "transport": "mcp",
                "ok": False,
                **(await _diagnose_connection(root, error)),
            }
        )
        yield None
        return
    events.append(
        {
            "tool": "league_mcp_connection",
            "transport": "mcp",
            "ok": True,
            "result": {"tools": [tool.name for tool in tools]},
        }
    )
    try:
        yield server
    finally:
        with suppress(Exception):
            await cast(Any, server).cleanup()
