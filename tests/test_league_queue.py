from __future__ import annotations

import copy
import json
import sqlite3
import uuid

import pytest

from deepdeck_learner.jobs import Job, JobManager
from deepdeck_learner.league_queue import LeagueQueueService, QueueConflict
from deepdeck_learner.league_rotation_io import stop_requested, turn_paths, write_result

CAPACITY = {
    "status": "ready",
    "limit": 1,
    "used": 0,
    "available": 1,
    "reason": "Server contract fixture",
}


class Manager:
    def __init__(self, root):
        self.root = root
        self.db = root / ".deepdeck" / "learner.db"
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.requests = []
        self.jobs = {}
        self.owned = set()
        self.fail_after_persist = False
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, payload TEXT)")

    def create(self, raw):
        queue = json.loads(
            sqlite3.connect(self.db).execute("SELECT payload FROM league_rotation").fetchone()[0]
        )
        turn = raw["league_queue_turn"]
        assert any(
            e.get("turn", {}).get("dispatchId") == turn["dispatchId"]
            for e in queue["entries"]
            if e.get("turn")
        )
        self.requests.append(raw)
        job = {
            "id": uuid.uuid4().hex,
            "kind": "matchmaking.agent",
            "status": "queued",
            "worker_slots": 1,
            "details": {"leagueQueueDispatchId": turn["dispatchId"]},
        }
        self.jobs[job["id"]] = job
        self.owned.add(job["id"])
        self.persist(job)
        if self.fail_after_persist:
            raise RuntimeError("Controller lost the acknowledgement after persisting.")
        return job

    def persist(self, job):
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?)", (job["id"], json.dumps(job)))

    def get(self, key):
        return copy.deepcopy(self.jobs.get(key))

    def list_jobs(self):
        return copy.deepcopy(list(self.jobs.values()))

    def owns_job(self, key):
        return key in self.owned

    def prune_league_turns(self, *_):
        pass

    def close_fenced_league_turn(self, *_args, **_kwargs):
        pass


@pytest.fixture
def rotation(tmp_path, monkeypatch):
    import deepdeck_learner.agent_studio as studio
    import deepdeck_learner.deck_sync as decks

    monkeypatch.setattr(
        studio,
        "find_agent",
        lambda root, key: (root, {"name": key, "format": "legacy", "deckVersionIds": ["deck"]}),
    )
    monkeypatch.setattr(decks, "published_deck_version", lambda root, key: key)
    monkeypatch.setattr(LeagueQueueService, "_start", lambda self: None)
    manager = Manager(tmp_path)
    service = LeagueQueueService(tmp_path, manager, capacity_reader=lambda: dict(CAPACITY))
    service.configure(
        {
            "revision": 0,
            "entries": [
                {
                    "agentId": key,
                    "competitionVersionId": "league",
                    "deckVersionIds": ["deck"],
                    "lease": {"mode": "matches", "matches": 2},
                }
                for key in ("tempo", "combo", "aggro")
            ],
        }
    )
    return service, manager


def complete(service, manager, *, outcome="complete", elapsed=10.0):
    queue = service.get()
    entry = next(e for e in queue["entries"] if e.get("turn"))
    turn = entry["turn"]
    _, receipt = turn_paths(service.root, queue["id"], entry["id"], turn["dispatchId"])
    write_result(
        receipt,
        run_id=turn["dispatchId"],
        completed=int(outcome == "complete"),
        elapsed=elapsed,
        outcome=outcome,
        match_id="match",
    )
    manager.jobs[turn["jobId"]]["status"] = "completed"
    manager.persist(manager.jobs[turn["jobId"]])
    return turn


def test_round_robin_orders_turns_and_retires_only_completed_match_budgets(rotation):
    service, manager = rotation
    service.control("resume", revision=1)
    for _ in range(6):
        service.tick()
        complete(service, manager)
    service.tick()
    assert [r["model_id"] for r in manager.requests] == ["tempo", "combo", "aggro"] * 2
    queue = service.get()
    assert queue["status"] == "paused"
    assert all(
        e["completedMatches"] == 2 and e["state"] == "done" and e["elapsedSeconds"] == 20
        for e in queue["entries"]
    )
    assert queue["sequence"] == 6 and len(queue["history"]) == 6
    assert queue["revision"] == 2  # Runtime progress does not conflict with configuration edits.
    service.tick()
    assert [e["completedMatches"] for e in service.get()["entries"]] == [2, 2, 2]


def test_cycle_receipt_can_exceed_threshold_and_reconciles_once(rotation):
    service, manager = rotation
    service.control("resume", revision=1)
    service.tick()
    queue = service.get()
    entry = next(e for e in queue["entries"] if e.get("turn"))
    turn = entry["turn"]
    _, receipt = turn_paths(service.root, queue["id"], entry["id"], turn["dispatchId"])
    matches = [str(uuid.UUID(int=i)) for i in (1, 2, 3)]
    write_result(
        receipt,
        run_id=turn["dispatchId"],
        completed=3,
        elapsed=90.0,
        outcome="complete",
        match_id=matches[-1],
        completed_match_ids=matches,
    )
    manager.jobs[turn["jobId"]]["status"] = "completed"
    manager.persist(manager.jobs[turn["jobId"]])
    service.tick()
    service.tick()
    saved = next(e for e in service.get()["entries"] if e["id"] == entry["id"])
    assert saved["state"] == "done" and saved["completedMatches"] == 3
    assert saved["elapsedSeconds"] == 90.0
    assert len([r for r in manager.requests if r["model_id"] == entry["agentId"]]) == 1


@pytest.mark.parametrize(
    "capacity",
    [
        {"status": "unsupported"},
        {"status": "unavailable"},
        dict(CAPACITY, used=1, available=0),
        dict(CAPACITY, available=3),
    ],
)
def test_no_worker_without_fresh_consistent_server_capacity(rotation, capacity):
    service, manager = rotation
    service.capacity_reader = lambda: capacity
    service.control("resume", revision=1)
    service.tick()
    assert not manager.requests


def test_pending_workers_reserve_places_before_registration_and_other_devices_count(rotation):
    service, manager = rotation
    service.control("resume", revision=1)
    service.tick()
    service.tick()
    assert len(manager.requests) == 1
    # An apparently available server place is insufficient when one unregistered
    # local worker is already heading to the same account.
    service.capacity_reader = lambda: dict(CAPACITY, limit=2, used=1, available=1)
    service.tick()
    assert len(manager.requests) == 1
    first = next(iter(manager.jobs.values()))
    first["details"]["leagueRun"] = {"serverRegistered": True, "registrationGeneration": 2}
    service.tick()
    assert len(manager.requests) == 2


def test_capacity_read_racing_reconnection_treats_changed_generation_as_pending(rotation):
    service, manager = rotation
    service.control("resume", revision=1)
    service.tick()
    first = next(iter(manager.jobs.values()))
    first["details"]["leagueRun"] = {"serverRegistered": True, "registrationGeneration": 1}

    def capacity():
        first["details"]["leagueRun"]["registrationGeneration"] += 1
        return dict(CAPACITY, limit=2, used=1, available=1)

    service.capacity_reader = capacity
    service.tick()
    assert len(manager.requests) == 1


def test_pause_fences_waiting_turn_finishes_receipt_without_dispatch_and_resume_never_reuses_identity(
    rotation,
):
    service, manager = rotation
    service.control("resume", revision=1)
    service.tick()
    queue = service.control("pause", revision=2)
    entry = queue["entries"][0]
    stop, _ = turn_paths(service.root, queue["id"], entry["id"], entry["turn"]["dispatchId"])
    assert queue["status"] == "pausing" and stop_requested(stop, entry["turn"]["sequence"])
    complete(service, manager)
    service.tick()
    assert service.get()["status"] == "paused" and len(manager.requests) == 1
    service.control("resume", revision=3)
    service.tick()
    assert manager.requests[1]["league_queue_turn"]["sequence"] == 2
    assert (
        manager.requests[1]["league_queue_turn"]["dispatchId"]
        != manager.requests[0]["league_queue_turn"]["dispatchId"]
    )


def test_failed_attempt_blocks_agent_without_spending_completed_match_quota(rotation):
    service, manager = rotation
    service.control("resume", revision=1)
    service.tick()
    complete(service, manager, outcome="failed")
    service.tick()
    queue = service.get()
    assert (
        queue["entries"][0]["state"] == "blocked" and queue["entries"][0]["completedMatches"] == 0
    )
    assert manager.requests[-1]["model_id"] == "combo"


def test_duration_is_cumulative_deployment_time_not_waiting_in_local_queue(rotation):
    service, manager = rotation
    queue = service.get()
    entries = [{**queue["entries"][0], "lease": {"mode": "duration", "minutes": 1}}]
    service.configure({"revision": 1, "entries": entries})
    service.control("resume", revision=2)
    service.tick()
    assert manager.requests[-1]["league_queue_turn"]["remainingSeconds"] == 60
    complete(service, manager, elapsed=40)
    service.tick()
    assert manager.requests[-1]["league_queue_turn"]["remainingSeconds"] == 20
    complete(service, manager, elapsed=35)
    service.tick()
    assert len(manager.requests) == 2
    assert service.get()["entries"][0]["state"] == "done"
    assert (
        service.get()["entries"][0]["elapsedSeconds"] == 75
    )  # Assigned match finishes past deadline.


def test_restart_never_dispatches_and_recovers_persisted_intent_without_duplicate(rotation):
    service, manager = rotation
    service.control("resume", revision=1)
    manager.fail_after_persist = True
    service.tick()
    assert service.get()["entries"][0]["turn"]["jobId"] is None
    restarted = LeagueQueueService(service.root, manager, capacity_reader=lambda: dict(CAPACITY))
    assert restarted.get()["status"] == "paused"
    restarted.tick()
    assert restarted.get()["entries"][0]["turn"]["jobId"] in manager.jobs
    assert len(manager.requests) == 1
    complete(restarted, manager)
    restarted.tick()
    assert restarted.get()["entries"][0]["completedMatches"] == 1


def test_missing_receipt_is_not_a_win_duration_or_automatic_retry(rotation):
    service, manager = rotation
    service.control("resume", revision=1)
    service.tick()
    first = next(iter(manager.jobs.values()))
    first["status"] = "stopped"
    service.tick()
    entry = service.get()["entries"][0]
    assert (
        entry["state"] == "blocked"
        and entry["completedMatches"] == 0
        and entry["elapsedSeconds"] == 0
    )
    assert "No match or duration was guessed" in entry["error"]


def test_configuration_conflicts_and_unsafe_decks_are_rejected(rotation):
    service, manager = rotation
    queue = service.get()
    with pytest.raises(QueueConflict):
        service.configure({"revision": 0, "entries": []})
    with pytest.raises(ValueError, match="mastered"):
        service.configure(
            {"revision": 1, "entries": [{**queue["entries"][0], "deckVersionIds": ["unknown"]}]}
        )
    with pytest.raises(ValueError, match="only once"):
        service.configure({"revision": 1, "entries": [queue["entries"][0]] * 2})
    service.control("resume", revision=1)
    with pytest.raises(ValueError, match="Pause"):
        service.configure({"revision": 2, "entries": []})
    assert not manager.requests


def test_random_uses_fresh_default_seed_and_persisted_draw_cursor(rotation, tmp_path):
    service, manager = rotation
    queue = service.get()
    other = LeagueQueueService(tmp_path / "other", Manager(tmp_path / "other"))
    assert queue["seed"] != other.get()["seed"]
    service.configure({"revision": 1, "entries": queue["entries"], "strategy": "random", "seed": 0})
    service.control("resume", revision=2)
    service.tick()
    first = manager.requests[-1]["model_id"]
    assert first in {"tempo", "combo", "aggro"}
    assert service.get()["draws"] == 1 and service.get()["seed"] == 0


def test_active_job_remains_visible_beyond_dashboard_window(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)
    old = Job(
        id=str(uuid.uuid4()),
        kind="matchmaking.agent",
        label="Old active worker",
        argv=["python", "-m", "deepdeck_examples.run", "--league-run-id", "a" * 32],
        created_at="2000",
        status="running",
    )
    manager._persist(old)
    for i in range(110):
        manager._persist(
            Job(
                id=str(uuid.uuid4()),
                kind="playtest.batch",
                label="Later result",
                argv=[],
                created_at=f"2026-{i:03}",
                status="completed",
            )
        )
    monkeypatch.setattr(manager, "_live_process_commands", lambda: [" ".join(old.argv)])
    assert old.id in {item["id"] for item in manager.list_jobs()}
    assert len(manager.list_jobs()) == 101


def test_bounded_rotation_diagnostics_never_remove_manual_jobs_replays_or_stop_fences(tmp_path):
    manager = JobManager(tmp_path)
    queue_id, entry_id = "a" * 32, "b" * 32
    manual = Job(
        id=str(uuid.uuid4()),
        kind="matchmaking.agent",
        label="Manual",
        argv=[],
        status="completed",
        finished_at="now",
    )
    manager._persist(manual)
    turns = []
    replay = tmp_path / ".deepdeck" / "replays" / "favorite.json"
    replay.parent.mkdir(parents=True)
    replay.write_text("protected", encoding="utf-8")
    for i in range(5):
        dispatch = uuid.uuid4().hex
        stop, result = turn_paths(tmp_path, queue_id, entry_id, dispatch)
        stop.parent.mkdir(parents=True, exist_ok=True)
        stop.write_text('{"through":5}', encoding="utf-8")
        write_result(
            result, run_id=dispatch, completed=1, elapsed=1, outcome="complete", match_id="m"
        )
        job = Job(
            id=str(uuid.uuid4()),
            kind="matchmaking.agent",
            label="Turn",
            argv=[],
            status="completed",
            finished_at="now",
            created_at=str(i),
            details={
                "leagueQueueId": queue_id,
                "leagueQueueEntryId": entry_id,
                "leagueQueueDispatchId": dispatch,
            },
        )
        manager._jobs[job.id] = job
        manager._persist(job)
        log = tmp_path / ".deepdeck" / "worker-logs" / f"{job.id}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("line\n" * 30_000, encoding="utf-8")
        turns.append((job, log, result))
    manager.prune_league_turns(queue_id, {turns[0][0].details["leagueQueueDispatchId"]}, retain=2)
    assert turns[0][1].is_file() and turns[0][2].is_file()
    assert all(not log.exists() and not result.exists() for _, log, result in turns[1:3])
    assert all(log.stat().st_size < 65_536 and result.exists() for _, log, result in turns[3:])
    assert manager.get(manual.id) and replay.read_text() == "protected" and stop.exists()
    assert len(manager._jobs) == 3


def test_http_queue_mutations_are_protected_and_revision_guarded(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from deepdeck_learner.app import create_app

    monkeypatch.setattr(LeagueQueueService, "_start", lambda self: None)
    client = TestClient(create_app(tmp_path))
    path = "/api/v1/league-queue"
    queue = client.get(path).json()
    assert queue["status"] == "paused" and queue["entries"] == []
    assert client.put(path, json={"revision": 0, "entries": []}).status_code == 403
    token = {"X-DeepDeck-Token": client.get("/api/v1/session").json()["token"]}
    assert client.put(path, json={"revision": 0, "entries": []}, headers=token).status_code == 200
    assert client.put(path, json={"revision": 0, "entries": []}, headers=token).status_code == 409
    assert client.post(path + "/resume", json={"revision": 1}, headers=token).status_code == 422


def test_consumed_results_and_history_stay_bounded_across_many_turns(rotation):
    service, manager = rotation
    queue = service.get()
    service.configure(
        {"revision": 1, "entries": [{**queue["entries"][0], "lease": {"mode": "continuous"}}]}
    )
    service.control("resume", revision=2)
    for _ in range(40):
        service.tick()
        complete(service, manager)
    service.control("pause", revision=3)
    queue = service.get()
    assert len(queue["history"]) == 30 and queue["entries"][0]["completedMatches"] == 40
    assert queue["sequence"] == 40


def test_orphan_queued_job_is_fenced_and_archived_without_spawning_a_duplicate(
    tmp_path, monkeypatch
):
    manager = JobManager(tmp_path)
    service = LeagueQueueService(tmp_path, manager, capacity_reader=lambda: dict(CAPACITY))
    queue = service.get()
    dispatch, entry_id = "c" * 32, "b" * 32
    job = Job(
        id=str(uuid.uuid4()),
        kind="matchmaking.agent",
        label="Persisted intent",
        argv=[],
        details={
            "leagueQueueId": queue["id"],
            "leagueQueueEntryId": entry_id,
            "leagueQueueDispatchId": dispatch,
        },
    )
    manager._persist(job)  # No attached task owns this queued row after restart.
    queue["entries"] = [
        {
            "id": entry_id,
            "agentId": "pilot",
            "name": "Pilot",
            "format": "legacy",
            "competitionVersionId": "league",
            "deckVersionIds": ["deck"],
            "lease": {"mode": "continuous"},
            "completedMatches": 0,
            "elapsedSeconds": 0.0,
            "state": "playing",
            "error": None,
            "turn": {"dispatchId": dispatch, "sequence": 1, "jobId": None},
        }
    ]
    service._save(queue)
    service.tick()
    assert manager.get(job.id)["status"] == "stopped"
    assert service.get()["entries"][0]["state"] == "blocked"
    stop, _ = turn_paths(tmp_path, queue["id"], entry_id, dispatch)
    assert stop_requested(stop, 1)
    assert len(manager.list_jobs()) == 1


def test_cleanup_preserves_a_stopped_but_still_attached_worker_log(tmp_path):
    manager = JobManager(tmp_path)
    job = Job(
        id=str(uuid.uuid4()),
        kind="matchmaking.agent",
        label="Stopping",
        argv=[],
        status="stopped",
        finished_at="now",
        details={
            "leagueQueueId": "a" * 32,
            "leagueQueueEntryId": "b" * 32,
            "leagueQueueDispatchId": "c" * 32,
        },
    )
    job.process = object()
    manager._jobs[job.id] = job
    manager._persist(job)
    log = tmp_path / ".deepdeck" / "worker-logs" / f"{job.id}.log"
    log.parent.mkdir(parents=True)
    log.write_text("still open", encoding="utf-8")
    manager.prune_league_turns("a" * 32, set(), retain=0)
    assert log.read_text() == "still open" and manager.get(job.id)


def test_controller_errors_pause_without_dispatch_and_disk_failure_stays_visible(
    rotation, monkeypatch
):
    service, manager = rotation
    service.control("resume", revision=1)

    def fail():
        raise OSError("disk fixture")

    monkeypatch.setattr(service, "tick", fail)
    service._run()
    assert service.get()["status"] == "paused" and not manager.requests
    service.control("resume", revision=2)
    monkeypatch.setattr(service, "_save", lambda _: fail())
    service._run()
    assert "could not save its pause" in service.get()["controllerError"]
    assert not manager.requests and service._thread is None


def test_explicit_resume_wakeup_cannot_be_lost_during_error_shutdown(rotation, monkeypatch):
    service, manager = rotation
    service.control("resume", revision=1)
    original_save = service._save
    calls = []

    def save(queue):
        original_save(queue)
        if len(calls) == 1 and queue["status"] == "paused":
            # The resume request commits immediately after the error pause,
            # while the old controller thread has not yet returned.
            resumed = service.get()
            resumed["status"] = "running"
            original_save(resumed)
            service._wake.set()

    def tick():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("transient fixture")
        queue = service.get()
        queue["status"] = "paused"
        original_save(queue)
        service._wake.set()

    monkeypatch.setattr(service, "_save", save)
    monkeypatch.setattr(service, "tick", tick)
    service._run()
    assert len(calls) == 2 and service.get()["status"] == "paused" and not manager.requests


def new_entry(agent="midrange"):
    return {
        "agentId": agent,
        "competitionVersionId": "league",
        "deckVersionIds": ["deck"],
        "lease": {"mode": "matches", "matches": 5},
    }


def test_live_append_preserves_exact_active_turn_and_counts_without_starting_a_second_worker(
    rotation,
):
    service, manager = rotation
    service.control("resume", revision=1)
    service.tick()
    before = service.get()
    after = service.append(
        {
            "revision": 2,
            "entry": {
                **new_entry(),
                "completedMatches": 999,
                "elapsedSeconds": 999,
                "turn": {"jobId": "forged"},
            },
        }
    )
    assert after["status"] == "running" and after["revision"] == 3
    assert after["entries"][:-1] == before["entries"]
    assert after["cursor"] == before["cursor"] and after["sequence"] == before["sequence"]
    assert after["entries"][-1]["completedMatches"] == 0 and after["entries"][-1]["turn"] is None
    assert len(manager.requests) == 1
    complete(service, manager)
    service.tick()
    assert manager.requests[-1]["model_id"] == "combo"


@pytest.mark.parametrize("status", ["paused", "pausing"])
def test_append_never_resumes_a_paused_or_draining_queue(rotation, status):
    service, manager = rotation
    if status == "pausing":
        service.control("resume", revision=1)
        service.tick()
        service.control("pause", revision=2)
    before = service.get()
    after = service.append({"revision": before["revision"], "entry": new_entry()})
    assert after["status"] == status and after["entries"][:-1] == before["entries"]
    assert len(manager.requests) == (1 if status == "pausing" else 0)


def test_append_validates_only_the_new_agent_and_never_republishes_existing_decks(
    rotation, monkeypatch
):
    import deepdeck_learner.agent_studio as studio

    service, manager = rotation

    def find(root, key):
        assert key == "midrange", (
            "An unrelated waiting or active agent must not be rewritten/revalidated."
        )
        return root, {"name": "Midrange", "format": "legacy", "deckVersionIds": ["deck"]}

    monkeypatch.setattr(studio, "find_agent", find)
    service.append({"revision": 1, "entry": new_entry()})
    assert len(service.get()["entries"]) == 4 and not manager.requests


def test_runtime_dispatch_racing_remove_protects_the_new_active_turn(rotation):
    service, manager = rotation
    snapshot = service.control("resume", revision=1)
    entry = snapshot["entries"][0]
    service.tick()  # Runtime dispatch intentionally does not change the config revision.
    before = service.get()
    with pytest.raises(QueueConflict, match="active turn"):
        service.remove(entry["id"], revision=snapshot["revision"])
    assert service.get() == before and len(manager.requests) == 1
    stop, _ = turn_paths(
        service.root, before["id"], entry["id"], before["entries"][0]["turn"]["dispatchId"]
    )
    assert not stop_requested(stop, before["sequence"])


def test_live_remove_changes_only_the_waiting_entry_and_keeps_the_match_running(rotation):
    service, manager = rotation
    service.control("resume", revision=1)
    service.tick()
    before = service.get()
    removed = before["entries"][1]
    after = service.remove(removed["id"], revision=2)
    assert after["status"] == "running" and after["revision"] == 3
    assert (
        after["entries"][0] == before["entries"][0] and after["entries"][1] == before["entries"][2]
    )
    assert len(manager.requests) == 1
    complete(service, manager)
    service.tick()
    assert manager.requests[-1]["model_id"] == "aggro"


@pytest.mark.parametrize("index", [0, 1, 2])
def test_removing_round_robin_cursor_keeps_the_next_agent_in_order(rotation, index):
    service, _ = rotation
    queue = service.get()
    queue["cursor"] = queue["entries"][index]["id"]
    expected = queue["entries"][(index + 1) % 3]["id"]
    service._save(queue)
    after = service.remove(queue["cursor"], revision=1)
    assert service._choose(after)["id"] == expected


def test_removing_last_waiting_agent_pauses_even_when_server_capacity_is_unavailable(rotation):
    service, manager = rotation
    service.capacity_reader = lambda: {"status": "unsupported", "reason": "No capacity contract"}
    queue = service.control("resume", revision=1)
    for entry in list(queue["entries"]):
        queue = service.remove(entry["id"], revision=queue["revision"])
    assert queue["status"] == "paused" and queue["entries"] == []
    assert not manager.requests


def test_concurrent_append_requests_have_one_revision_winner_and_no_overwritten_entries(rotation):
    from concurrent.futures import ThreadPoolExecutor

    service, manager = rotation

    def add(name):
        try:
            service.append({"revision": 1, "entry": new_entry(name)})
            return "saved"
        except QueueConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(add, ["midrange", "control"]))
    assert sorted(results) == ["conflict", "saved"]
    assert (
        len(service.get()["entries"]) == 4
        and service.get()["revision"] == 2
        and not manager.requests
    )


def test_live_entry_routes_are_authorized_atomic_and_never_fall_through_to_control(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    import deepdeck_learner.agent_studio as studio
    import deepdeck_learner.deck_sync as decks
    from deepdeck_learner.app import create_app

    monkeypatch.setattr(
        studio,
        "find_agent",
        lambda root, key: (root, {"name": key, "format": "legacy", "deckVersionIds": ["deck"]}),
    )
    monkeypatch.setattr(decks, "published_deck_version", lambda root, key: key)
    monkeypatch.setattr(LeagueQueueService, "_start", lambda self: None)
    client = TestClient(create_app(tmp_path))
    base = "/api/v1/league-queue"
    payload = {"revision": 0, "entry": new_entry()}
    assert client.post(base + "/entries", json=payload).status_code == 403
    token = {"X-DeepDeck-Token": client.get("/api/v1/session").json()["token"]}
    response = client.post(base + "/entries", json=payload, headers=token)
    assert response.status_code == 200 and response.json()["status"] == "paused"
    entry_id = response.json()["entries"][0]["id"]
    assert client.post(base + "/entries", json=payload, headers=token).status_code == 409
    remove = base + f"/entries/{entry_id}/remove"
    assert client.post(remove, json={"revision": 1}).status_code == 403
    assert client.post(remove, json={"revision": 0}, headers=token).status_code == 409
    assert client.post(remove, json={"revision": 1}, headers=token).json()["entries"] == []
