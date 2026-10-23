"""Official public-client ChatGPT plan sharing, never reused Codex credentials.

https://developers.openai.com/siwc/token-sharing-open-source/sign-in
Callbacks use a dedicated loopback listener whose access logs are disabled.
Only explicitly initiated browser consent may register or replace an account.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import jwt

from .protected_credentials import read_credentials, write_credentials
from .replay_lock import replay_storage_lock

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
_flows: dict[str, dict[str, Any]] = {}
_guard = threading.RLock()


def _path(root: Path) -> Path:
    return root / ".deepdeck" / "chatgpt-credentials.bin"


def _load(root: Path) -> dict[str, Any]:
    return read_credentials(_path(root))


def status(root: Path) -> dict[str, Any]:
    with replay_storage_lock(root, namespace="llm"):
        data = _load(root)
    accounts = []
    for account_id, record in data.get("accounts", {}).items():
        accounts.append(
            {
                "id": account_id,
                "label": record.get("email") or "ChatGPT account",
                "connected": bool(
                    record.get("access_token") and PLAN_SCOPE in record.get("scopes", [])
                ),
                "registration": str(record.get("client_id", ""))[-8:],
            }
        )
    with _guard:
        pending_flow_id = next(
            (
                flow_id
                for flow_id, flow in _flows.items()
                if flow["root"] == root.resolve() and flow["status"] == "pending"
            ),
            None,
        )
    return {
        "accounts": accounts,
        "activeAccountId": data.get("activeAccountId"),
        "usageUrl": "https://chatgpt.com/settings/usage",
        "pendingFlowId": pending_flow_id,
    }


def _discovery() -> dict[str, Any]:
    response = httpx.get(ISSUER + "/.well-known/openid-configuration", timeout=15)
    response.raise_for_status()
    discovery = response.json()
    if discovery.get("issuer") != ISSUER:
        raise ValueError("Unexpected ChatGPT identity issuer.")
    return dict(discovery)


def _validate_identity(encoded: str, client_id: str, nonce: str) -> dict[str, Any]:
    discovery = _discovery()
    uri = str(discovery.get("jwks_uri", ""))
    if urlparse(uri).scheme != "https" or urlparse(uri).netloc != "auth.openai.com":
        raise ValueError("Unexpected ChatGPT signing-key location.")
    signing_key = jwt.PyJWKClient(uri, timeout=15).get_signing_key_from_jwt(encoded)
    identity = jwt.decode(
        encoded,
        signing_key.key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=ISSUER,
        options={"require": ["exp", "iss", "aud", "sub", "nonce"]},
    )
    if not secrets.compare_digest(str(identity["nonce"]), nonce):
        raise ValueError("The ChatGPT sign-in nonce did not match.")
    return dict(identity)


def _post_token(values: dict[str, str]) -> dict[str, Any]:
    response = httpx.post(TOKEN_URL, data=values, timeout=30)
    if response.is_error:
        # Never include token-endpoint bodies, credentials or authorization URLs.
        raise ValueError("ChatGPT authorization could not be renewed. Please sign in again.")
    result = response.json()
    if not isinstance(result, dict) or not isinstance(result.get("access_token"), str):
        raise ValueError("ChatGPT did not return an access token.")
    return result


def _merge_tokens(record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    expiry = result.get("expires_in")
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not 0 < expiry <= 86400:
        raise ValueError("ChatGPT returned an invalid credential expiry.")
    scopes = str(result.get("scope", "")).split()
    if PLAN_SCOPE not in scopes:
        raise ValueError("ChatGPT plan usage was not authorized. Enable it during browser sign-in.")
    if str(result.get("token_type", "")).lower() != "bearer":
        raise ValueError("ChatGPT returned an unsupported token type.")
    updated = {
        **record,
        "access_token": result["access_token"],
        "scopes": scopes,
        "expires_at": time.time() + expiry,
    }
    for field in ("refresh_token", "id_token"):
        if result.get(field):
            updated[field] = result[field]
    return updated


def _finish(root: Path, flow: dict[str, Any], query: dict[str, list[str]]) -> None:
    if not secrets.compare_digest(query.get("state", [""])[0], flow["state"]):
        raise ValueError("The browser sign-in state did not match.")
    if "error" in query:
        raise ValueError("ChatGPT sign-in was declined. Your previous connection was not changed.")
    issued = query.get("client_id", [flow["client_id"]])[0]
    if not issued or issued == "dynamic_agent_client":
        raise ValueError("ChatGPT registration was incomplete. Please start sign-in again.")
    if flow["client_id"] != "dynamic_agent_client" and issued != flow["client_id"]:
        raise ValueError("ChatGPT returned a different account registration.")
    code = query.get("code", [""])[0]
    if not code:
        raise ValueError("ChatGPT did not return an authorization code.")
    result = _post_token(
        {
            "grant_type": "authorization_code",
            "client_id": issued,
            "code": code,
            "code_verifier": flow["verifier"],
            "redirect_uri": flow["redirect_uri"],
            "resource": RESOURCE,
        }
    )
    identity = _validate_identity(str(result.get("id_token", "")), issued, flow["nonce"])
    with replay_storage_lock(root, namespace="llm"):
        data = _load(root)
        accounts = data.setdefault("accounts", {})
        previous = accounts.get(flow.get("account_id"), {})
        if previous and (previous["subject"] != identity["sub"] or previous["client_id"] != issued):
            raise ValueError("The signed-in identity did not match the selected ChatGPT account.")
        # Re-registering the same identity must not create duplicate active token sets.
        account_id = next(
            (
                key
                for key, value in accounts.items()
                if value.get("client_id") == issued and value.get("subject") == identity["sub"]
            ),
            uuid.uuid4().hex,
        )
        accounts[account_id] = _merge_tokens(
            {
                "client_id": issued,
                "subject": identity["sub"],
                "email": identity.get("email", ""),
                "issuer": ISSUER,
            },
            result,
        )
        data["activeAccountId"] = account_id
        write_credentials(_path(root), data)


def start(root: Path, account_id: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    with _guard:
        if any(flow["root"] == root and flow["status"] == "pending" for flow in _flows.values()):
            raise ValueError("A ChatGPT sign-in is already open. Finish or cancel it first.")
        # Bound completed attempts; never retain codes or tokens in this registry.
        for key in list(_flows):
            if (
                _flows[key]["status"] != "pending"
                and time.time() - _flows[key].get("startedAt", 0) > 900
            ):
                del _flows[key]
        with replay_storage_lock(root, namespace="llm"):
            data = _load(root)
            record = data.get("accounts", {}).get(account_id, {}) if account_id else {}
            if account_id and not record:
                raise ValueError("Unknown ChatGPT account.")
            data.setdefault("hostId", "urn:uuid:" + str(uuid.uuid4()))
            write_credentials(_path(root), data)
        verifier = secrets.token_urlsafe(64)
        flow: dict[str, Any] = {
            "root": root,
            "status": "pending",
            "state": secrets.token_urlsafe(32),
            "nonce": secrets.token_urlsafe(32),
            "verifier": verifier,
            "client_id": record.get("client_id", "dynamic_agent_client"),
            "account_id": account_id,
            "processing": False,
            "startedAt": time.time(),
        }
        flow_id = secrets.token_urlsafe(24)

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if parsed.path != "/auth/callback" or not secrets.compare_digest(
                    query.get("state", [""])[0], flow["state"]
                ):
                    self.send_error(400, "Invalid sign-in callback")
                    return
                with _guard:
                    if flow["status"] != "pending" or flow["processing"]:
                        self.send_error(409, "This sign-in attempt has already been used")
                        return
                    flow["processing"] = True
                try:
                    _finish(root, flow, query)
                    flow["status"] = "completed"
                    message = (
                        "ChatGPT connected. You can close this tab and return to DeepDeck Learner."
                    )
                except (ValueError, OSError, httpx.HTTPError, jwt.PyJWTError):
                    flow["status"] = "failed"
                    message = "Sign-in could not be completed. Return to DeepDeck Learner and try again. Your previous connection was kept."
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.end_headers()
                    self.wfile.write(message.encode())
                except OSError:
                    pass  # Closing the browser must still release the callback listener.
                finally:
                    flow["processing"] = False
                    flow["timer"].cancel()
                    server.shutdown()
                    server.server_close()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
        flow["redirect_uri"] = f"http://127.0.0.1:{server.server_port}/auth/callback"
        flow["server"] = server
        _flows[flow_id] = flow
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        timer = threading.Timer(600, lambda: cancel(root, flow_id))
        timer.daemon = True
        flow["timer"] = timer
        timer.start()
        params = {
            "client_id": flow["client_id"],
            "ext_agent_host_id": data["hostId"],
            "response_type": "code",
            "redirect_uri": flow["redirect_uri"],
            "scope": "openid profile email offline_access resource.invoke " + PLAN_SCOPE,
            "resource": RESOURCE,
            "state": flow["state"],
            "nonce": flow["nonce"],
            "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("="),
        }
        if record:
            if record.get("id_token"):
                params["id_token_hint"] = record["id_token"]
        else:
            params["agent_name_hint"] = "DeepDeck Learner"
        try:
            opened = webbrowser.open(ISSUER + "/api/accounts/authorize?" + urlencode(params))
        except OSError:
            opened = False
        if not opened:
            cancel(root, flow_id)
            raise ValueError(
                "The system browser could not be opened. Check your default browser and retry."
            )
        return {"flowId": flow_id, "status": "pending"}


def flow_status(root: Path, flow_id: str) -> dict[str, str]:
    with _guard:
        flow = _flows.get(flow_id)
        if not flow or flow["root"] != root.resolve():
            raise ValueError("Unknown or expired ChatGPT sign-in attempt.")
        return {"flowId": flow_id, "status": flow["status"]}


def cancel(root: Path, flow_id: str) -> dict[str, str]:
    with _guard:
        flow = _flows.get(flow_id)
        if not flow or flow["root"] != root.resolve():
            raise ValueError("Unknown ChatGPT sign-in attempt.")
        if flow["processing"]:
            raise ValueError("ChatGPT is completing sign-in. Please wait.")
        if flow["status"] == "pending":
            flow["status"] = "cancelled"
            flow["timer"].cancel()
            flow["server"].shutdown()
            flow["server"].server_close()
        return {"flowId": flow_id, "status": flow["status"]}


def select(root: Path, account_id: str) -> dict[str, Any]:
    with replay_storage_lock(root, namespace="llm"):
        data = _load(root)
        record = data.get("accounts", {}).get(account_id, {})
        if not record.get("access_token") or PLAN_SCOPE not in record.get("scopes", []):
            raise ValueError("Sign in to this ChatGPT account before selecting it.")
        data["activeAccountId"] = account_id
        write_credentials(_path(root), data)
    return status(root)


def access_token(root: Path) -> str:
    with replay_storage_lock(root, namespace="llm"):
        data = _load(root)
        record = data.get("accounts", {}).get(data.get("activeAccountId"), {})
        if not record.get("access_token") or PLAN_SCOPE not in record.get("scopes", []):
            raise ValueError("Connect a ChatGPT account and authorize plan usage first.")
        if float(record.get("expires_at", 0)) < time.time() + 60:
            if not record.get("refresh_token"):
                raise ValueError("Your ChatGPT connection expired. Please sign in again.")
            result = _post_token(
                {
                    "grant_type": "refresh_token",
                    "client_id": record["client_id"],
                    "refresh_token": record["refresh_token"],
                    "resource": RESOURCE,
                }
            )
            # OAuth may omit scope on refresh, retaining the existing grant.
            result.setdefault("scope", " ".join(record["scopes"]))
            updated = _merge_tokens(record, result)
            data["accounts"][data["activeAccountId"]] = updated
            write_credentials(_path(root), data)
            record = updated
        return str(record["access_token"])


def models(root: Path) -> dict[str, Any]:
    credential = access_token(root)
    response = httpx.get(
        RESOURCE + "/models", headers={"Authorization": "Bearer " + credential}, timeout=20
    )
    if response.is_error:
        raise ValueError(
            "ChatGPT models could not be loaded. Check your connection and account access."
        )
    items = response.json().get("models", [])
    return {
        "items": [
            {"id": item["slug"], "name": item.get("display_name", item["slug"])}
            for item in items
            if isinstance(item, dict)
            and item.get("visibility") == "list"
            and isinstance(item.get("slug"), str)
        ]
    }


def disconnect(root: Path, account_id: str) -> dict[str, Any]:
    confirmed = False
    with replay_storage_lock(root, namespace="llm"):
        data = _load(root)
        record = data.get("accounts", {}).get(account_id)
        if not record:
            raise ValueError("Unknown ChatGPT account.")
        if record.get("refresh_token"):
            try:
                endpoint = str(_discovery().get("revocation_endpoint", ""))
                if (
                    urlparse(endpoint).scheme == "https"
                    and urlparse(endpoint).netloc == "auth.openai.com"
                ):
                    for attempt in range(3):
                        try:
                            response = httpx.post(
                                endpoint,
                                data={
                                    "token": record["refresh_token"],
                                    "token_type_hint": "refresh_token",
                                    "client_id": record["client_id"],
                                },
                                timeout=8,
                            )
                            confirmed = response.status_code == 200
                            if confirmed or response.status_code < 500:
                                break
                        except httpx.HTTPError:
                            pass
                        if attempt < 2:
                            time.sleep(0.25 * 2**attempt)
            except (httpx.HTTPError, ValueError):
                pass
        for field in ("access_token", "refresh_token", "id_token", "scopes", "expires_at"):
            record.pop(field, None)
        if data.get("activeAccountId") == account_id:
            data["activeAccountId"] = None
        write_credentials(_path(root), data)
    return {**status(root), "revocationConfirmed": confirmed}
