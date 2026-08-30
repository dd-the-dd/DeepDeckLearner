from __future__ import annotations

import ipaddress
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import Request

PAIRING_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


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


def request_is_loopback(request: Request) -> bool:
    client_host = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for", "")
    if _loopback(client_host) and forwarded:
        client_host = forwarded.split(",")[-1].strip()
    origin = request.headers.get("origin")
    if origin:
        origin_host = urlsplit(origin).hostname
        if not _loopback(origin_host):
            return False
    return _loopback(client_host)


class LocalAccessManager:
    def __init__(self) -> None:
        self._pairing_code = self._new_pairing_code()
        self._sessions: dict[str, LocalSession] = {}

    @staticmethod
    def _new_pairing_code() -> str:
        return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))

    @property
    def pairing_code(self) -> str:
        return self._pairing_code

    def issue_owner(self) -> LocalSession:
        return self._issue("This computer", "owner")

    def pair(self, code: str, label: str) -> LocalSession | None:
        normalized = code.strip().upper().replace("-", "")
        if not secrets.compare_digest(normalized, self._pairing_code):
            return None
        clean_label = " ".join(label.strip().split())[:80] or "LAN device"
        return self._issue(clean_label, "paired")

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

    def sessions(self) -> list[dict[str, str]]:
        return [session.public() for session in self._sessions.values()]

    def revoke(self, session_id: str) -> bool:
        for token, session in list(self._sessions.items()):
            if secrets.compare_digest(session.id, session_id):
                del self._sessions[token]
                return True
        return False

    def regenerate_pairing_code(self) -> str:
        self._pairing_code = self._new_pairing_code()
        self._sessions = {
            token: session for token, session in self._sessions.items() if session.role == "owner"
        }
        return self._pairing_code
