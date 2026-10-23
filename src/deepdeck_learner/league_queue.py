"""A durable, capacity-gated rotation of normal one-seat SDK League workers.

No membership policy lives here. The server grants places; the queue only
orders waiting agents and keeps per-agent deployment budgets across turns.
"""

from __future__ import annotations

import json
import math
import random
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .league_capacity import league_capacity
from .league_lifecycle import LeagueRunLimits
from .league_rotation_io import read_result, read_stop, stop_through, turn_paths
from .replay_lock import replay_storage_lock

ACTIVE = {"queued", "running"}


class QueueConflict(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def available_places(
    capacity: dict[str, Any], before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> int:
    """Discount registrations not proven present before the fresh server read."""
    if capacity.get("status") != "ready" or any(
        type(capacity.get(key)) is not int or capacity[key] < 0
        for key in ("limit", "used", "available")
    ):
        return 0
    if capacity["available"] != max(0, capacity["limit"] - capacity["used"]):
        return 0
    old = {job["id"]: job for job in before}
    active = [
        job for job in after if job["kind"] == "matchmaking.agent" and job["status"] in ACTIVE
    ]
    pending = 0
    seats = 0
    for job in active:
        count = job.get("worker_slots", 1)
        if type(count) is not int or count < 1:
            return 0
        seats += count
        previous = (old.get(job["id"], {}).get("details") or {}).get("leagueRun") or {}
        current = (job.get("details") or {}).get("leagueRun") or {}
        generation = previous.get("registrationGeneration")
        if (
            previous.get("serverRegistered") is not True
            or current.get("serverRegistered") is not True
            or type(generation) is not int
            or generation < 1
            or current.get("registrationGeneration") != generation
        ):
            pending += count
    return int(max(0, min(capacity["available"] - pending, capacity["limit"] - seats)))


class LeagueQueueService:
    def __init__(
        self,
        root: Path,
        manager: Any,
        *,
        capacity_reader: Callable[[], dict[str, Any]] = league_capacity,
    ) -> None:
        self.root, self.manager, self.capacity_reader = root, manager, capacity_reader
        self.path = root / ".deepdeck" / "learner.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread: threading.Thread | None = None
        self._controller_error: str | None = None
        self._thread_lock = threading.Lock()
        self._wake = threading.Event()
        with replay_storage_lock(root, namespace="league"), sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS league_rotation (singleton INTEGER PRIMARY KEY CHECK(singleton=1), payload TEXT NOT NULL)"
            )
            row = db.execute("SELECT payload FROM league_rotation WHERE singleton=1").fetchone()
            if row:
                queue = json.loads(row[0])
                # Fence existing turns even when the app dies before its UI can
                # request pause. Assigned matches finish; waiting tickets drain.
                for entry in queue["entries"]:
                    try:
                        self._fence(queue, entry)
                    except (OSError, ValueError):
                        # Workers treat unreadable controls as a stop request.
                        # A damaged queue must not prevent opening the app.
                        queue["message"] = (
                            "A rotation stop control is unreadable. Automatic dispatch is paused; review local storage."
                        )
                if queue["status"] != "paused" or any(e.get("turn") for e in queue["entries"]):
                    queue.update(
                        status="paused",
                        message="Application restarted. Existing turns are draining; resume explicitly when ready.",
                    )
                    db.execute(
                        "UPDATE league_rotation SET payload=? WHERE singleton=1",
                        (json.dumps(queue),),
                    )
            else:
                queue = {
                    "id": uuid.uuid4().hex,
                    "revision": 0,
                    "status": "paused",
                    "strategy": "round_robin",
                    "seed": secrets.randbits(31),
                    "seedSource": "random",
                    "draws": 0,
                    "cursor": None,
                    "sequence": 0,
                    "entries": [],
                    "history": [],
                    "capacity": None,
                    "message": None,
                    "updatedAt": now(),
                }
                db.execute("INSERT INTO league_rotation VALUES (1,?)", (json.dumps(queue),))

    def get(self) -> dict[str, Any]:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM league_rotation WHERE singleton=1").fetchone()
        if row is None:
            raise RuntimeError("League queue is unavailable.")
        value: dict[str, Any] = json.loads(row[0])
        if self._controller_error:
            value["controllerError"] = self._controller_error
        return value

    def _save(self, queue: dict[str, Any]) -> None:
        queue["updatedAt"] = now()
        queue.pop("controllerError", None)
        queue["history"] = queue["history"][-30:]
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE league_rotation SET payload=? WHERE singleton=1", (json.dumps(queue),)
            )
        self._controller_error = None

    def _fence(self, queue: dict[str, Any], entry: dict[str, Any]) -> None:
        turn = entry.get("turn")
        if turn:
            stop, _ = turn_paths(self.root, queue["id"], entry["id"], turn["dispatchId"])
            stop_through(stop, turn["sequence"])

    def _normalize_entry(self, item: Any) -> dict[str, Any]:
        from .agent_studio import find_agent
        from .deck_sync import published_deck_version

        if not isinstance(item, dict) or not isinstance(item.get("agentId"), str):
            raise ValueError("Choose a programmatic agent for the rotation.")
        _, policy = find_agent(self.root, item["agentId"])
        competition = item.get("competitionVersionId")
        decks = item.get("deckVersionIds")
        if not isinstance(competition, str) or not competition.strip() or len(competition) > 256:
            raise ValueError("Choose a League competition for every queued agent.")
        if (
            not isinstance(decks, list)
            or not decks
            or any(not isinstance(d, str) or not d or len(d) > 256 for d in decks)
            or len(set(decks)) != len(decks)
            or not set(decks).issubset(policy.get("deckVersionIds", []))
        ):
            raise ValueError("Choose available decks mastered by each queued agent.")
        for deck in policy.get("deckVersionIds", []):
            published_deck_version(self.root, deck)
        limits = LeagueRunLimits.from_payload(item.get("lease"))
        lease = (
            {"mode": "matches", "matches": limits.max_matches}
            if limits.max_matches
            else {"mode": "duration", "minutes": limits.duration_seconds // 60}
            if limits.duration_seconds
            else {"mode": "continuous"}
        )
        return {
            "agentId": item["agentId"],
            "name": str(policy.get("name", item["agentId"]))[:120],
            "format": policy.get("format"),
            "competitionVersionId": competition.strip(),
            "deckVersionIds": decks,
            "lease": lease,
        }

    @staticmethod
    def _check_revision(queue: dict[str, Any], revision: Any) -> None:
        if type(revision) is not int or revision != queue["revision"]:
            raise QueueConflict("The rotation changed elsewhere. Reload it before editing.")

    @staticmethod
    def _new_entry(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": uuid.uuid4().hex,
            "completedMatches": 0,
            "elapsedSeconds": 0.0,
            "state": "queued",
            "turn": None,
            "error": None,
            **item,
        }

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Append only the new entry, never a browser's snapshot of live turns."""
        item = self._normalize_entry(payload.get("entry"))
        with replay_storage_lock(self.root, namespace="league"):
            queue = self.get()
            self._check_revision(queue, payload.get("revision"))
            if any(e["agentId"] == item["agentId"] for e in queue["entries"]):
                raise ValueError("This agent is already on the rotation bench.")
            queue["entries"].append(self._new_entry(item))
            queue["revision"] += 1
            self._save(queue)
        # Wake an already enabled dispatcher; adding to a paused bench never
        # resumes it, including after an application restart.
        if queue["status"] != "paused" or any(e.get("turn") for e in queue["entries"]):
            self._start()
        return queue

    def remove(self, entry_id: str, *, revision: Any) -> dict[str, Any]:
        with replay_storage_lock(self.root, namespace="league"):
            queue = self.get()
            self._check_revision(queue, revision)
            entry = next((e for e in queue["entries"] if e["id"] == entry_id), None)
            if entry is None:
                raise ValueError("This agent is no longer on the rotation bench. Reload it.")
            if entry.get("turn"):
                raise QueueConflict(
                    "This agent now has an active turn. It was not removed and its match was not stopped. Pause the rotation and let the turn finish first."
                )
            entries = queue["entries"]
            if queue["cursor"] == entry["id"]:
                index = entries.index(entry)
                queue["cursor"] = (
                    entries[(index - 1) % len(entries)]["id"] if len(entries) > 1 else None
                )
            queue["entries"] = [e for e in entries if e["id"] != entry_id]
            queue["revision"] += 1
            if queue["status"] != "paused" and not any(
                e.get("turn") or e["state"] == "queued" for e in queue["entries"]
            ):
                queue.update(
                    status="paused",
                    message="No agents are waiting for a turn. The rotation is paused.",
                )
            self._save(queue)
        if queue["status"] != "paused" or any(e.get("turn") for e in queue["entries"]):
            self._start()
        return queue

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Configuration never publishes a deck, registers a runner or starts a
        # ticket. Use the existing explicit publication flow before enqueuing.
        raw = payload.get("entries")
        strategy = payload.get("strategy", "round_robin")
        if not isinstance(raw, list) or strategy not in ("round_robin", "random"):
            raise ValueError("Choose a rotation strategy and a list of agents.")
        normalized = []
        seen = set()
        for item in raw:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("agentId"), str)
                or item["agentId"] in seen
            ):
                raise ValueError("Add each agent only once to the rotation.")
            normalized.append(self._normalize_entry(item))
            seen.add(item["agentId"])
        with replay_storage_lock(self.root, namespace="league"):
            queue = self.get()
            self._check_revision(queue, payload.get("revision"))
            self._reconcile(queue)
            if queue["status"] != "paused" or any(e.get("turn") for e in queue["entries"]):
                raise ValueError(
                    "Pause the rotation and let active matches finish before editing it."
                )
            previous = {e["agentId"]: e for e in queue["entries"]}
            entries = []
            for item in normalized:
                old = previous.get(item["agentId"])
                # Retain actual usage, even if the user switches strategy/deck.
                entries.append({**old, **item} if old else self._new_entry(item))
            if "seed" in payload:
                seed = payload["seed"]
                if type(seed) is not int or not 0 <= seed < 2**63:
                    raise ValueError("Choose a whole-number seed from 0 to 2^63−1.")
                if seed != queue["seed"]:
                    queue.update(seed=seed, seedSource="explicit", draws=0)
            queue.update(
                entries=entries, strategy=strategy, revision=queue["revision"] + 1, message=None
            )
            self._retire_exhausted(queue)
            self._save(queue)
            return queue

    def control(self, action: str, *, revision: Any, entry_id: str | None = None) -> dict[str, Any]:
        with replay_storage_lock(self.root, namespace="league"):
            queue = self.get()
            if type(revision) is not int or revision != queue["revision"]:
                raise QueueConflict("The rotation changed elsewhere. Reload it before continuing.")
            self._reconcile(queue)
            if action == "pause":
                for entry in queue["entries"]:
                    self._fence(queue, entry)
                queue.update(
                    status="pausing" if any(e.get("turn") for e in queue["entries"]) else "paused",
                    message=None,
                )
            elif action == "resume":
                if queue["status"] != "paused" or any(e.get("turn") for e in queue["entries"]):
                    raise ValueError(
                        "Wait for existing turns to drain before resuming the rotation."
                    )
                self._retire_exhausted(queue)
                if not any(e["state"] == "queued" for e in queue["entries"]):
                    raise ValueError(
                        "Add an eligible agent or review blocked agents before resuming."
                    )
                queue.update(status="running", message="Waiting for fresh server capacity.")
            elif action == "retry":
                entry = next((e for e in queue["entries"] if e["id"] == entry_id), None)
                if (
                    queue["status"] != "paused"
                    or not entry
                    or entry["state"] != "blocked"
                    or entry.get("turn")
                ):
                    raise ValueError(
                        "Pause first, then review a blocked agent with no active turn."
                    )
                entry.update(state="queued", error=None)
                self._retire_exhausted(queue)
            else:
                raise ValueError("Choose pause, resume or retry for the rotation.")
            queue["revision"] += 1
            self._save(queue)
        self._start()
        return queue

    def _retire_exhausted(self, queue: dict[str, Any]) -> None:
        for entry in queue["entries"]:
            if entry.get("turn") or entry["state"] == "blocked":
                continue
            limits = LeagueRunLimits.from_payload(entry["lease"])
            done = (
                limits.max_matches is not None
                and entry["completedMatches"] >= limits.max_matches
                or limits.duration_seconds is not None
                and entry["elapsedSeconds"] >= limits.duration_seconds
            )
            entry["state"] = "done" if done else "queued"

    def _reconcile(self, queue: dict[str, Any]) -> None:
        for entry in queue["entries"]:
            turn = entry.get("turn")
            if not turn:
                continue
            if not turn.get("jobId"):
                with sqlite3.connect(self.path) as db:
                    rows = db.execute(
                        "SELECT id FROM jobs WHERE json_extract(payload,'$.details.leagueQueueDispatchId')=?",
                        (turn["dispatchId"],),
                    ).fetchall()
                if len(rows) > 1:
                    raise ValueError(
                        "Multiple workers claim a rotation turn. Pause and review their status."
                    )
                if rows:
                    turn["jobId"] = rows[0][0]
            job = self.manager.get(turn["jobId"]) if turn.get("jobId") else None
            if (
                job
                and job["status"] in ACTIVE
                and (job["status"] == "running" or self.manager.owns_job(job["id"]))
            ):
                # A queued row owned by this manager is a real scheduled task.
                # An orphan durable queued row is not a live process receipt.
                continue
            self._fence(queue, entry)
            _, result_path = turn_paths(self.root, queue["id"], entry["id"], turn["dispatchId"])
            result = read_result(result_path, turn["dispatchId"])
            error = None
            if result is None:
                error = "This turn stopped without a confirmed receipt. No match or duration was guessed. Review before retrying."
            elif job and job["status"] == "failed":
                error = "The worker reported an error after its receipt. Review before retrying."
            elif result["outcome"] == "failed":
                error = "The server match failed. No completed match was charged to this agent."
            if result:
                entry["completedMatches"] += result["completedEntries"]
                entry["elapsedSeconds"] += result["elapsedSeconds"]
            if job:
                self.manager.close_fenced_league_turn(
                    job["id"], turn["dispatchId"], confirmed=result is not None and error is None
                )
            queue["history"].append(
                {
                    "dispatchId": turn["dispatchId"],
                    "agentId": entry["agentId"],
                    "name": entry["name"],
                    "jobId": turn.get("jobId"),
                    "finishedAt": now(),
                    "result": result,
                    "error": error,
                }
            )
            entry.update(turn=None, state="blocked" if error else "queued", error=error)
        self._retire_exhausted(queue)
        if queue["status"] == "pausing" and not any(e.get("turn") for e in queue["entries"]):
            queue["status"] = "paused"

    def _choose(self, queue: dict[str, Any]) -> dict[str, Any] | None:
        entries: list[dict[str, Any]] = queue["entries"]
        eligible = [e for e in entries if e["state"] == "queued" and not e.get("turn")]
        if not eligible:
            return None
        if queue["strategy"] == "random":
            # A fresh initial seed by default; a saved draw index makes restart
            # recovery reproducible without repeating the same initial draw.
            return random.Random(f"{queue['seed']}:{queue['draws']}").choice(eligible)
        cursor = next((i for i, e in enumerate(entries) if e["id"] == queue["cursor"]), -1)
        return next(
            e
            for step in range(1, len(entries) + 1)
            for e in [entries[(cursor + step) % len(entries)]]
            if e in eligible
        )

    def tick(self) -> None:
        # Observe registrations BEFORE reading the server, then re-observe under
        # the dispatch lock. Network latency must not hold the profile lock.
        before = self.manager.list_jobs()
        capacity = self.capacity_reader()
        with replay_storage_lock(self.root, namespace="league"):
            queue = self.get()
            self._reconcile(queue)
            protected = {e["turn"]["dispatchId"] for e in queue["entries"] if e.get("turn")}
            self.manager.prune_league_turns(queue["id"], protected)
            queue["capacity"] = {**capacity, "checkedAt": now()}
            self._save(queue)
            if queue["status"] != "running":
                return
            after = self.manager.list_jobs()
            free = available_places(capacity, before, after)
            queue["message"] = (
                capacity.get("reason")
                if capacity.get("status") != "ready"
                else "Waiting for an account place."
                if not free
                else None
            )
            # Dispatch only one per fresh observation. The next poll sees this
            # worker as pending until the server confirms its registration.
            entry = self._choose(queue) if free else None
            if entry:
                dispatch = uuid.uuid4().hex
                stop, _ = turn_paths(self.root, queue["id"], entry["id"], dispatch)
                read_stop(stop)  # Fail closed on corrupt control, before spawn.
                queue["sequence"] += 1
                queue["draws"] += 1
                queue["cursor"] = entry["id"]
                turn: dict[str, Any] = {
                    "dispatchId": dispatch,
                    "sequence": queue["sequence"],
                    "jobId": None,
                    "createdAt": now(),
                }
                entry.update(turn=turn, state="playing")
                self._save(queue)  # Intent commits BEFORE JobManager persists/spawns.
                limits = LeagueRunLimits.from_payload(entry["lease"])
                remaining = (
                    max(1, math.ceil(limits.duration_seconds - entry["elapsedSeconds"]))
                    if limits.duration_seconds
                    else None
                )
                try:
                    job = self.manager.create(
                        {
                            "kind": "matchmaking.agent",
                            "model_id": entry["agentId"],
                            "agent": "programmatic",
                            "format": entry["format"],
                            "competition_version_id": entry["competitionVersionId"],
                            "deck_version_ids": entry["deckVersionIds"],
                            "connections": 1,
                            "continuous": False,
                            "speed": "1s",
                            "league_queue_turn": {
                                "queueId": queue["id"],
                                "entryId": entry["id"],
                                "dispatchId": dispatch,
                                "sequence": turn["sequence"],
                                "remainingSeconds": remaining,
                            },
                        }
                    )
                    turn["jobId"] = job["id"]
                except Exception:
                    # Keep the intent: create may have persisted a worker before
                    # returning an error. Reconcile by dispatch identity next tick.
                    self._fence(queue, entry)
                    queue.update(
                        status="pausing",
                        message="A worker launch was not confirmed. Draining and reviewing that turn before continuing.",
                    )
            elif capacity.get("status") == "ready" and not any(
                e.get("turn") or e["state"] == "queued" for e in queue["entries"]
            ):
                queue.update(
                    status="paused", message="All agents finished their budget or need review."
                )
            self._save(queue)

    def _start(self) -> None:
        with self._thread_lock:
            self._wake.set()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, daemon=True, name="league-rotation"
                )
                self._thread.start()

    def _run(self) -> None:
        while True:
            self._wake.clear()
            try:
                queue = self.get()
                if queue["status"] == "paused" and not any(e.get("turn") for e in queue["entries"]):
                    with self._thread_lock:
                        if not self._wake.is_set():
                            self._thread = None
                            return
                    continue
                self.tick()
            except Exception:
                # Do not erase the durable intent or invent an outcome when a
                # filesystem/DB/server read fails. Stop dispatching and fence.
                try:
                    with replay_storage_lock(self.root, namespace="league"):
                        queue = self.get()
                        for entry in queue["entries"]:
                            with suppress(OSError, ValueError):
                                self._fence(queue, entry)
                        queue.update(
                            status="paused",
                            message="Rotation paused because its state could not be verified. Existing matches are preserved; check storage and retry explicitly.",
                        )
                        self._save(queue)
                except Exception:
                    self._controller_error = "The rotation controller stopped and could not save its pause. Check available disk space and reload before resuming."
                with self._thread_lock:
                    if not self._wake.is_set():
                        self._thread = None
                        return
                # An explicit control request can arrive after the paused state
                # commits but before this thread exits. Do not lose its wakeup.
                continue
            self._wake.wait(5)
