"""Local deployment limits, independent of server-owned League capacity."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any
from uuid import UUID

import httpx
from deepdeck_agent import (
    LeagueCompatibilityError,
    LeagueEntryConflict,
    LeagueEventEntry,
    LeagueMutationUncertain,
    LeagueUnavailableError,
)


def league_failure_message(error: BaseException) -> str:
    """User-facing failure without transport URLs, headers or credentials."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(seen) < 16:
        seen.add(id(current))
        # These errors describe the write/contract outcome more accurately than
        # their HTTP causes. In particular, a 503 cause is NOT permission to
        # replay a POST whose enrollment may already have committed.
        messages = {
            LeagueMutationUncertain: (
                "League enrollment could not be confirmed. The saved key was kept. "
                "Do not submit another registration until the current entry is checked "
                "on the League website."
            ),
            LeagueEntryConflict: (
                "This account already has a different agent or deck registered for this "
                "competition. Review its entry on the League website; it was not replaced."
            ),
            LeagueCompatibilityError: (
                "The League event response is incompatible with this app. Update the app/server; "
                "reconnecting or changing membership is not the fix. Your key was kept."
            ),
            LeagueUnavailableError: (
                "League competitions are not enabled on this server yet. Your key was kept; "
                "no retired matchmaking ticket was submitted."
            ),
        }
        for kind, message in messages.items():
            if isinstance(current, kind):
                return message
        if isinstance(current, httpx.HTTPStatusError):
            status = current.response.status_code
            message = ""
            try:
                if len(current.response.content) <= 65_536:
                    body = current.response.json()
                    detail = body.get("error") if isinstance(body, dict) else None
                    candidate = detail.get("message") if isinstance(detail, dict) else detail
                    if isinstance(candidate, str):
                        message = re.sub(r"[\x00-\x1f\x7f]", " ", candidate[:1000])
                        message = re.sub(r"ddl_agent_[A-Za-z0-9_-]+", "[redacted key]", message)
                        message = re.sub(
                            r"(?i)(?:Bearer\s+|ddl_session=)[^\s;,]+",
                            "[redacted credential]",
                            message,
                        )
                        message = re.sub(r"(?:https?|wss?)://[^\s]+", "[server URL]", message)[:400]
            except (ValueError, TypeError, httpx.ResponseNotRead):
                pass
            action = {
                400: "Check the League, published decks and agent configuration.",
                401: "Reconnect your League API key in Settings.",
                403: "Review this account's permissions on the League website.",
                409: "Check the current League places and active deployments before retrying.",
                429: "The server is rate-limiting requests. Wait before retrying.",
            }.get(status, "Check the server status before retrying.")
            return f"League request failed (HTTP {status}). {message} {action}".strip()
        current = current.__cause__ or current.__context__
    if isinstance(error, TimeoutError):
        return (
            "League registration was not confirmed in time. "
            "Check the server and API key in Settings."
        )
    return (
        "The League attempt failed. Your local agents and decks are saved. "
        "Check the connection in Settings."
    )


async def watch_registration(
    runner: Any, on_change: Callable[[bool, int], None], *, poll_seconds: float = 1.0
) -> None:
    """Observe SDK registration, not process existence or a locally queued entry.

    A new SDK registration object starts a new generation even if a disconnect
    and reconnect happened entirely between two samples.
    """
    previous: Any = object()
    generation = 0
    while True:
        registration = getattr(getattr(runner, "client", None), "registration", None)
        if registration is not previous:
            previous = registration
            generation += 1
            version = getattr(registration, "agent_version_id", None)
            on_change(isinstance(version, str) and bool(version), generation)
        await asyncio.sleep(poll_seconds)


@dataclass(frozen=True)
class LeagueRunLimits:
    max_matches: int | None = None
    duration_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.max_matches is not None and self.duration_seconds is not None:
            raise ValueError("Choose a match count or duration, not both.")
        for value, maximum in ((self.max_matches, 10_000), (self.duration_seconds, 604_800)):
            if value is not None and (type(value) is not int or not 1 <= value <= maximum):
                raise ValueError(
                    "League run limits must be positive whole numbers within the supported range."
                )

    @classmethod
    def from_payload(cls, value: Any) -> LeagueRunLimits:
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("Choose a League deployment duration.")
        mode = value.get("mode", "continuous")
        fields = {
            "continuous": {"mode"},
            "matches": {"mode", "matches"},
            "duration": {"mode", "minutes"},
        }
        if not isinstance(mode, str) or mode not in fields or set(value) - fields[mode]:
            raise ValueError("Choose continuous, a match count, or a duration for League play.")
        if mode == "continuous":
            return cls()
        field, high = ("matches", 10_000) if mode == "matches" else ("minutes", 10_080)
        amount = value.get(field)
        if type(amount) is not int or not 1 <= amount <= high:
            raise ValueError(f"League {field}: choose a whole number between 1 and {high}.")
        return cls(max_matches=amount) if mode == "matches" else cls(duration_seconds=amount * 60)

    def argv(self) -> list[str]:
        if self.max_matches is not None:
            return ["--league-max-matches", str(self.max_matches)]
        if self.duration_seconds is not None:
            return ["--league-duration-seconds", str(self.duration_seconds)]
        return []


class LeagueRunBudget:
    """Count finished matches while allowing an enrolled competition to drain.

    ``observe_event`` and ``may_enroll`` serve the current event protocol. The
    reserve/finish helpers remain compatible with older callers, but are not
    used to cancel or bound individual matches of a season/playoff cycle.
    """

    def __init__(
        self,
        limits: LeagueRunLimits,
        *,
        clock: Callable[[], float] = time.monotonic,
        stop_requested: Callable[[], bool] = lambda: False,
    ):
        self.limits = limits
        self.clock = clock
        self.started = clock()
        self.completed = 0
        self.reserved = 0
        self.stop_requested = stop_requested
        self.completed_match_ids: set[str] = set()

    def remaining_seconds(self) -> float | None:
        duration = self.limits.duration_seconds
        return None if duration is None else max(0.0, duration - (self.clock() - self.started))

    def expired(self) -> bool:
        return self.stop_requested() or self.remaining_seconds() == 0

    def may_enroll(self) -> bool:
        """Limits prevent the NEXT enrollment, never truncate an enrolled cycle."""
        return not self.expired() and (
            self.limits.max_matches is None or self.completed < self.limits.max_matches
        )

    def observe_event(self, event: dict[str, Any]) -> None:
        """Count unique authoritative completed matches, not byes or WS traffic."""
        matches = event.get("ownMatches")
        if not isinstance(matches, list):
            raise LeagueCompatibilityError("League ownMatches is missing.")
        for match in matches:
            if not isinstance(match, dict) or match.get("status") != "complete":
                continue
            value = match.get("matchId")
            try:
                match_id = str(UUID(value)) if isinstance(value, str) else None
            except ValueError:
                match_id = None
            if match_id is None:
                raise LeagueCompatibilityError("Invalid completed League match ID.")
            self.completed_match_ids.add(match_id)
        self.completed = len(self.completed_match_ids)

    def reserve(self) -> bool:
        maximum = self.limits.max_matches
        if self.expired() or maximum is not None and self.completed + self.reserved >= maximum:
            return False
        self.reserved += 1
        return True

    def finish(self, status: str | None) -> None:
        if self.reserved <= 0:
            raise RuntimeError("No League entry was reserved.")
        self.reserved -= 1
        if status == "complete":
            self.completed += 1


async def serve_event_cycles(
    runner: Any,
    entry: LeagueEventEntry,
    budget: LeagueRunBudget,
    *,
    once: bool,
    on_event: Callable[[dict[str, Any] | None], Awaitable[None]],
    on_uncertain: Callable[[BaseException], None] | None = None,
    poll_seconds: float = 10.0,
) -> None:
    """Enroll once per cycle, then drain season AND playoffs before rotating.

    Own entries/decks remain pinned for the entire event. Only safe reads retry;
    an uncertain enrollment is surfaced and its POST is never replayed. A stop
    watermark does not withdraw, forfeit or disconnect an already enrolled agent.
    The caller owns the decision WebSocket and keeps it running until this exits.
    """

    async def read(operation: Callable[[], Awaitable[Any]], *, enrolled: bool) -> Any:
        while True:
            try:
                return await operation()
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500 and error.response.status_code not in {
                    408,
                    425,
                    429,
                }:
                    raise
            except httpx.TransportError:
                pass
            if not enrolled and not budget.may_enroll():
                return None
            await asyncio.sleep(poll_seconds)

    while True:
        # Read first so a restarted worker can resume its owner's active event,
        # even if its duration/stop watermark has since expired. No new POST is
        # authorized in that case.
        league = runner.league
        selected = await read(partial(league.find_event, entry), enrolled=False)
        if selected is None:
            runner.league_event = None
            await on_event(None)
            if not budget.may_enroll():
                return
            await asyncio.sleep(poll_seconds)
            continue
        own = selected.get("ownEntry")
        already_enrolled = (
            isinstance(own, dict)
            and own.get("withdrawnAt") is None
            and own.get("agentVersionId") == league.agent_version_id
            and own.get("deckVersionId") in entry.deck_pool
        )
        if not already_enrolled and not budget.may_enroll():
            return
        # join() reconciles uncertain outcomes once with GET, never retries POST.
        try:
            current = selected if already_enrolled else await league.join(selected["id"], entry)
        except LeagueMutationUncertain as error:
            if on_uncertain is not None:
                on_uncertain(error)
            # A committed enrollment may have lost its acknowledgement. Keep
            # the decision socket alive and reconcile with READS ONLY, even past
            # a local deadline. Never rotate to another agent or repeat POST.
            while True:
                await asyncio.sleep(poll_seconds)
                current = await read(partial(league.event, selected["id"]), enrolled=True)
                own = current.get("ownEntry")
                if isinstance(own, dict) and own.get("withdrawnAt") is None:
                    if (
                        own.get("agentVersionId") != league.agent_version_id
                        or own.get("deckVersionId") not in entry.deck_pool
                    ):
                        raise LeagueEntryConflict("Another entry was confirmed.") from error
                    break
                if current["phase"] != "registration":
                    # Registration has closed and no entry belongs to us. It is
                    # now safe to stop, but NOT to call the write a success.
                    raise error
        budget.reserved = 1
        fixed_deck = current.get("ownEntry", {}).get("deckVersionId")
        try:
            while True:
                own = current.get("ownEntry")
                if not isinstance(own, dict) or own.get("withdrawnAt") is not None:
                    # Honour an explicit withdrawal without fighting/rejoining it.
                    return
                if (
                    own.get("agentVersionId") != league.agent_version_id
                    or own.get("deckVersionId") != fixed_deck
                ):
                    raise LeagueEntryConflict("The enrolled agent or deck changed.")
                runner.league_event = current
                budget.observe_event(current)
                await on_event(current)
                if current["phase"] in {"complete", "cancelled"}:
                    break
                # Deliberately do NOT inspect the local stop/deadline here.
                await asyncio.sleep(poll_seconds)
                current = await read(partial(league.event, selected["id"]), enrolled=True)
        finally:
            budget.reserved = 0
        if once or entry.event_id is not None or not budget.may_enroll():
            return
        await asyncio.sleep(poll_seconds)
        await runner.wait_until_connected(timeout=30)


async def join_with_limits(
    runner: Any, entry: Any, budget: LeagueRunBudget, *, once: bool
) -> dict[str, Any] | None:
    """Compatibility only: the active Learner DDL flow uses serve_event_cycles."""
    last_error: Exception | None = None
    while not budget.expired():
        try:
            return await runner.join_matchmaking(entry)  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as error:
            if (
                once
                or error.response.status_code < 500
                and error.response.status_code not in {408, 425, 429}
            ):
                raise
            last_error = error
        except httpx.TransportError as error:
            if once:
                raise
            last_error = error
        remaining = budget.remaining_seconds()
        await asyncio.sleep(min(2.0, remaining) if remaining is not None else 2.0)
    if last_error is not None:
        raise RuntimeError(
            "League duration elapsed after a registration error; "
            "the registration outcome could not be confirmed."
        ) from last_error
    return None


async def wait_for_limited_match(
    runner: Any, ticket: dict[str, Any], budget: LeagueRunBudget, *, poll_seconds: float = 5.0
) -> str | None:
    """Compatibility for retired tickets, never used for event registrations."""
    current = ticket
    while True:
        if current.get("status") == "matched" and isinstance(current.get("matchId"), str):
            return str(current["matchId"])
        if current.get("status") == "cancelled":
            return None
        if budget.expired():
            try:
                # League cancellation is atomic for queued tickets. If it races
                # assignment, a 409 requires re-reading, then serving that match.
                await runner.cancel_matchmaking_ticket(str(current["id"]))
                return None
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 409:
                    raise
                current = await runner.get_matchmaking_ticket(str(current["id"]))
                if current.get("status") not in {"matched", "cancelled"}:
                    raise RuntimeError(
                        "Could not confirm whether the expired League ticket was assigned."
                    ) from error
                continue
        remaining = budget.remaining_seconds()
        await asyncio.sleep(min(poll_seconds, remaining) if remaining is not None else poll_seconds)
        current = await runner.get_matchmaking_ticket(str(current["id"]))
