from __future__ import annotations

import ipaddress
import secrets
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import Request


@dataclass(frozen=True)
class LocalSession:
    id: str
    token: str
    label: str
    role: str
    created_at: str

    def public(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "role": self.role,
            "created_at": self.created_at,
        }


def _loopback(value: str | None) -> bool:
    if not value:
        return False
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value.strip("[]")).is_loopback
    except ValueError:
        return False


def local_host_addresses() -> set[str]:
    addresses = {"127.0.0.1", "::1"}
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None):
            addresses.add(str(result[4][0]).split("%")[0])
    except OSError:
        pass
    return addresses


def request_is_host(request: Request) -> bool:
    client_host = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for", "")
    if _loopback(client_host) and forwarded:
        client_host = forwarded.split(",")[-1].strip()
    host_addresses = local_host_addresses()
    client_is_host = _loopback(client_host) or client_host in host_addresses
    origin = request.headers.get("origin")
    if origin:
        origin_host = urlsplit(origin).hostname
        if not _loopback(origin_host) and origin_host not in host_addresses:
            return False
    return client_is_host


class LocalAccessManager:
    def __init__(self) -> None:
        self._sessions: dict[str, LocalSession] = {}

    def issue_owner(self) -> LocalSession:
        return self._issue("This computer", "owner")

    def issue_lan(self) -> LocalSession:
        return self._issue("Trusted LAN browser", "lan")

    def _issue(self, label: str, role: str) -> LocalSession:
        session = LocalSession(
            id=secrets.token_urlsafe(12),
            token=secrets.token_urlsafe(32),
            label=label,
            role=role,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._sessions[session.token] = session
        return session

    def resolve(self, token: str | None) -> LocalSession | None:
        if not token:
            return None
        for candidate, session in self._sessions.items():
            if secrets.compare_digest(candidate, token):
                return session
        return None
