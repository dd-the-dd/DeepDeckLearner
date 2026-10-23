from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from .league_connection import platform_url as platform_url
from .private_store import load_private_json, remove_private_json, save_private_json
from .settings import save_api_key

SESSION_FILE = "league-session.json"


class LeagueAccountError(ValueError):
    pass


class LeagueAuthenticationError(LeagueAccountError):
    pass


def _session(root: Path) -> dict[str, Any]:
    value = load_private_json(root, SESSION_FILE)
    if value and value.get("platformUrl") != platform_url():
        raise LeagueAuthenticationError(
            "Connect your account for the currently selected League server."
        )
    return value


def research_headers(root: Path) -> dict[str, str]:
    """Credentials for the configured League only, never the model provider."""
    # An explicitly configured key must not be silently shadowed by an old
    # browser session belonging to another account.
    key = os.getenv("DEEPDECK_API_KEY", "").strip()
    if key:
        return {"Authorization": f"Bearer {key}"}
    session = _session(root)
    cookie_name = os.getenv("DDL_SESSION_COOKIE_NAME", "ddl_session")
    cookie = session.get("cookies", {}).get(cookie_name)
    if cookie:
        return {"Cookie": f"{cookie_name}={cookie}"}
    raise LeagueAuthenticationError("Add your League account API key to use MCP research tools.")


def _data(response: httpx.Response) -> Any:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code in {401, 403}:
        message = body.get("error", {}).get("message") if isinstance(body, dict) else None
        raise LeagueAuthenticationError(message or "Connect your League account again.")
    if response.is_error:
        message = body.get("error", {}).get("message") if isinstance(body, dict) else None
        raise LeagueAccountError(message or f"The League returned HTTP {response.status_code}.")
    if response.status_code == 204:
        return None
    if not isinstance(body, dict) or "data" not in body:
        raise LeagueAccountError("The League returned an unsupported response.")
    return body["data"]


@contextmanager
def account_client(root: Path) -> Iterator[httpx.Client]:
    session = _session(root)
    if not session.get("cookies"):
        raise LeagueAuthenticationError("Sign in to your League account to synchronize decks.")
    with httpx.Client(
        base_url=platform_url() + "/",
        cookies=session["cookies"],
        timeout=20,
        follow_redirects=False,
    ) as client:
        yield client


def request(client: httpx.Client, method: str, path: str, payload: Any = None) -> Any:
    try:
        response = client.request(method, path.lstrip("/"), json=payload)
    except httpx.HTTPError as error:
        raise LeagueAccountError(
            "The League is unavailable. Your local decks are still saved."
        ) from error
    return _data(response)


def sign_in(
    root: Path, identity: str, password: str, *, create_agent_key: bool = False
) -> dict[str, Any]:
    if not identity.strip() or not password:
        raise LeagueAuthenticationError("Enter your League email or username and password.")
    url = platform_url()
    with httpx.Client(base_url=url + "/", timeout=20, follow_redirects=False) as client:
        try:
            response = client.post(
                "auth/password/login", json={"identity": identity.strip(), "password": password}
            )
        except httpx.HTTPError as error:
            raise LeagueAccountError("The League sign-in service is unavailable.") from error
        if response.status_code not in {200, 204, 302, 303}:
            _data(response)
            raise LeagueAuthenticationError("The League could not sign you in.")
        user = request(client, "GET", "me")
        cookies = {
            cookie.name: cookie.value
            for cookie in client.cookies.jar
            if cookie.name == os.getenv("DDL_SESSION_COOKIE_NAME", "ddl_session")
        }
        if not cookies or not isinstance(user, dict) or not user.get("id"):
            raise LeagueAuthenticationError("The server did not return a valid account session.")
        # The password is never persisted or returned to React.
        save_private_json(
            root, SESSION_FILE, {"platformUrl": url, "cookies": cookies, "user": user}
        )
        result = {
            "configured": True,
            "valid": True,
            "user": user,
            "reason": "League account connected.",
        }
        if create_agent_key:
            try:
                key = request(
                    client,
                    "POST",
                    "me/agent-keys",
                    {"name": "DeepDeck Learner · local app", "expiresInDays": 90},
                )
                save_api_key(root, str(key.get("token", "")))
                result["agentKeyConfigured"] = True
            except (LeagueAccountError, ValueError) as error:
                result["agentKeyConfigured"] = False
                result["reason"] = f"Account connected. Agent-key setup needs attention: {error}"
        return result


def account_status(root: Path) -> dict[str, Any]:
    try:
        saved = _session(root)
        if not saved:
            return {
                "configured": False,
                "valid": False,
                "user": None,
                "reason": "Sign in to synchronize your decks.",
            }
        with account_client(root) as client:
            user = request(client, "GET", "me")
        return {"configured": True, "valid": True, "user": user, "reason": "Account connected."}
    except LeagueAuthenticationError as error:
        return {"configured": True, "valid": False, "user": None, "reason": str(error)}
    except (LeagueAccountError, OSError, ValueError) as error:
        return {"configured": True, "valid": None, "user": None, "reason": str(error)}


def sign_out(root: Path) -> dict[str, Any]:
    try:
        with account_client(root) as client:
            request(client, "POST", "auth/logout")
    except (LeagueAccountError, OSError, ValueError):
        pass
    finally:
        remove_private_json(root, SESSION_FILE)
    return {
        "configured": False,
        "valid": False,
        "user": None,
        "reason": "Signed out on this computer.",
    }
