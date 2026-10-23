"""Bounded local-only operational metadata. Never store bodies, URLs or exception text."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import closing, suppress
from pathlib import Path
from typing import Any

MAX_RECORDS = 2000
MAX_DATABASE_BYTES = 8 * 1024 * 1024
APPLICATION_ID = 0x44444C44
RETENTION_SECONDS = 14 * 24 * 60 * 60
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class DiagnosticStore:
    def __init__(self, root: Path, *, max_records: int = MAX_RECORDS) -> None:
        self.path = root / ".deepdeck" / "diagnostics.sqlite3"
        self.max_records = max_records
        self.lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0.05)
        try:
            application_id = connection.execute("PRAGMA application_id").fetchone()[0]
            existing_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
            if application_id not in (0, APPLICATION_ID) or (
                application_id == 0 and existing_table
            ):
                raise sqlite3.DatabaseError("Unrecognized local diagnostic database.")
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            os.chmod(self.path, 0o600)
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA temp_store=MEMORY")
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}")
            connection.execute("""CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, created_at INTEGER NOT NULL, method TEXT NOT NULL,
                route TEXT NOT NULL, status INTEGER NOT NULL, duration_ms INTEGER NOT NULL,
                category TEXT NOT NULL
            )""")
            return connection
        except BaseException:
            connection.close()
            raise

    def append(
        self,
        *,
        reference: str,
        method: str,
        route: str,
        status: int,
        duration_ms: int,
        category: str,
    ) -> bool:
        # Only middleware-produced enum/template metadata enters this store.
        try:
            uuid.UUID(reference)
            if (
                method not in METHODS | {"OTHER", "CLIENT"}
                or category
                not in {
                    "action_completed",
                    "request_rejected",
                    "service_error",
                    "type_error",
                    "value_error",
                    "io_error",
                    "unexpected_error",
                    "response_interrupted",
                    "request_cancelled",
                    "connection_failed",
                    "render_error",
                }
                or len(route) > 180
                or not (100 <= status <= 599 or (method == "CLIENT" and status == 0))
            ):
                return False
            now = int(time.time())
            with self.lock, closing(self._connect()) as connection, connection:
                connection.execute(
                    "DELETE FROM events WHERE created_at < ?", (now - RETENTION_SECONDS,)
                )
                # Make space before insertion, including near the SQLite page quota.
                count = connection.execute("SELECT count(*) FROM events").fetchone()[0]
                excess = max(0, count - self.max_records + 1)
                if excess:
                    connection.execute(
                        "DELETE FROM events WHERE id IN "
                        "(SELECT id FROM events ORDER BY created_at,rowid LIMIT ?)",
                        (excess,),
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
                    (
                        reference,
                        now,
                        method,
                        route,
                        status,
                        min(max(0, duration_ms), 86_400_000),
                        category,
                    ),
                )
            return True
        except (OSError, ValueError, sqlite3.Error):
            # Disk full, read-only or busy storage must never break a player action.
            return False

    def report(self, *, errors_only: bool = True, limit: int = 50) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("Choose between 1 and 100 diagnostic entries.")
        with self.lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM events WHERE created_at < ?", (int(time.time()) - RETENTION_SECONDS,)
            )
            count = connection.execute("SELECT count(*) FROM events").fetchone()[0]
            rows = connection.execute(
                "SELECT id,created_at,method,route,status,duration_ms,category FROM events "
                "WHERE (? = 0 OR category != 'action_completed') "
                "ORDER BY created_at DESC,rowid DESC LIMIT ?",
                (errors_only, limit),
            ).fetchall()
        return {
            "localOnly": True,
            "limits": {
                "maxEntries": self.max_records,
                "databaseBytes": MAX_DATABASE_BYTES,
                "retentionDays": 14,
            },
            "storedEntries": count,
            "usedBytes": self.path.stat().st_size,
            "items": [
                dict(
                    zip(
                        (
                            "reference",
                            "createdAtUnix",
                            "method",
                            "route",
                            "status",
                            "durationMs",
                            "category",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in rows
            ],
        }


class LocalResponseInterrupted(RuntimeError):
    """Sanitized ASGI failure after response headers have already been sent."""


class LocalDiagnosticsMiddleware:
    def __init__(self, app: Any, *, store: DiagnosticStore, routes: frozenset[str]) -> None:
        self.app, self.store, self.routes = app, store, routes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        reference = str(uuid.uuid4())
        scope.setdefault("state", {})["local_request_id"] = reference
        started = time.monotonic()
        response_started = False
        status = 500
        category: str | None = None

        async def response_send(message: dict[str, Any]) -> None:
            nonlocal response_started, status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                message = dict(message)
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", reference.encode("ascii"))]
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, response_send)
        except asyncio.CancelledError:
            if not response_started:
                status = 499
            category = "request_cancelled"
            raise
        except Exception as error:
            category = (
                "type_error"
                if isinstance(error, TypeError)
                else "value_error"
                if isinstance(error, ValueError)
                else "io_error"
                if isinstance(error, OSError)
                else "unexpected_error"
            )
            if response_started:
                category = "response_interrupted"
                # Do not turn an interrupted stream into fake successful JSON or
                # let Uvicorn log the original private exception/traceback.
                raise LocalResponseInterrupted(
                    f"Local response interrupted. Reference: {reference}"
                ) from None
            status = 500
            content = json.dumps(
                {
                    "detail": (
                        "The local action could not be completed. "
                        "Check its current state before trying again."
                    ),
                    "requestId": reference,
                }
            ).encode("utf-8")
            await response_send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            await response_send({"type": "http.response.body", "body": content})
        finally:
            # Business outcomes that deliberately return HTTP 200 can still be
            # unsuccessful. Only explicitly allowlisted server markers qualify.
            marker = scope.get("state", {}).get("local_diagnostic_category")
            if category is None and marker == "connection_failed":
                category = marker
            method = scope.get("method", "OTHER")
            method = method if method in METHODS else "OTHER"
            route = getattr(scope.get("route"), "path", None)
            route = route if route in self.routes else "<unmatched>"
            # Read-only polling is not an action journal. Do not journal the
            # journal itself, static assets or private dynamic identifiers.
            if (
                scope.get("path", "").startswith("/api/")
                and not route.startswith("/api/v1/diagnostics")
                and (
                    status >= 400
                    or category is not None
                    or method in {"POST", "PUT", "PATCH", "DELETE"}
                )
            ):
                # Diagnostic-storage defects must not change the action response.
                with suppress(Exception):
                    await asyncio.to_thread(
                        self.store.append,
                        reference=reference,
                        method=method,
                        route=route,
                        status=status,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        category=category
                        or (
                            "service_error"
                            if status >= 500
                            else "request_rejected"
                            if status >= 400
                            else "action_completed"
                        ),
                    )
