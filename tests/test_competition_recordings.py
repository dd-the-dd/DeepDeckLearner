from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from deepdeck_learner.competition_recordings import recover_attempt_recording
from deepdeck_learner.competitions import CompetitionService, create_competition
from deepdeck_learner.jobs import Job, JobManager


def fixture(root: Path, event="event", match="m1", *, human=False):
    batch = "deadbeef01234567"
    artifact = root / ".deepdeck/batches" / batch
    artifact.mkdir(parents=True)
    spec = {
        "id": batch,
        "games": 1,
        "competitionId": event,
        "competitionMatchId": match,
        "seats": [{"kind": "human" if human else "agent", "agentId": "agent-a"}],
    }
    manifest = artifact / "batch-spec.json"
    manifest.write_text(json.dumps(spec), encoding="utf-8")
    replay_id = f"batch-{batch}-0001"
    collection = root / (".deepdeck/local-tables" if human else ".deepdeck/agents/agent-a")
    path = collection / "replays" / f"{replay_id}.json"
    path.parent.mkdir(parents=True)
    value = {
        "schemaVersion": "deepdeck-replay/v1",
        "id": replay_id,
        "metadata": {
            "batchId": batch,
            "competitionId": event,
            "competitionMatchId": match,
            "gameStatus": "error",
        },
        "frames": [],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    job = {
        "id": "legacy-job",
        "kind": "playtest.table" if human else "playtest.batch",
        "status": "failed",
        "artifact_path": str(artifact),
    }
    return job, manifest, spec, path, value


@pytest.mark.parametrize("human", [True, False])
@pytest.mark.parametrize("favorite", [True, False])
def test_recover_owned_recording_including_human_collection_and_favorites(
    tmp_path, human, favorite
):
    job, _, _, path, value = fixture(tmp_path, human=human)
    if favorite:
        saved = path.parent.parent / "saved-replays" / path.name
        saved.parent.mkdir()
        path.rename(saved)
    assert recover_attempt_recording(tmp_path, "event", "m1", job) == value["id"]


@pytest.mark.parametrize("field", ["competitionId", "competitionMatchId", "batchId", "id"])
def test_filename_alone_cannot_authorize_a_recording_link(tmp_path, field):
    job, _, _, path, value = fixture(tmp_path)
    (value if field == "id" else value["metadata"])[field] = "not-this-attempt"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert recover_attempt_recording(tmp_path, "event", "m1", job) is None


@pytest.mark.parametrize(
    "change",
    ["outside", "bad-agent", "bad-spec", "wrong-match", "multi-game", "missing", "corrupt"],
)
def test_recovery_rejects_unbound_or_unavailable_files(tmp_path, change):
    job, manifest, spec, path, _ = fixture(tmp_path)
    if change == "outside":
        job["artifact_path"] = str(tmp_path.parent)
    elif change == "bad-agent":
        spec["seats"][0]["agentId"] = "../outside"
    elif change == "bad-spec":
        spec["id"] = "other"
    elif change == "wrong-match":
        spec["competitionMatchId"] = "other"
    elif change == "multi-game":
        spec["games"] = 2
    elif change == "missing":
        path.unlink()
    else:
        path.write_text("not a replay", encoding="utf-8")
    manifest.write_text(json.dumps(spec), encoding="utf-8")
    assert recover_attempt_recording(tmp_path, "event", "m1", job) is None


def test_recovery_header_read_is_bounded_and_does_not_expand_frames(tmp_path, monkeypatch):
    job, _, _, path, value = fixture(tmp_path)
    value["frames"] = [{"state": {"privateHand": "x" * 200_000}}]
    path.write_text(json.dumps(value), encoding="utf-8")
    original = Path.open
    reads = []

    class Bounded:
        def __enter__(self):
            self.stream = original(path, encoding="utf-8")
            return self

        def __exit__(self, *_args):
            self.stream.close()

        def read(self, size):
            reads.append(size)
            assert size <= 65_536
            return self.stream.read(size)

    monkeypatch.setattr(
        Path,
        "open",
        lambda target, *a, **k: Bounded() if target == path else original(target, *a, **k),
    )
    assert recover_attempt_recording(tmp_path, "event", "m1", job) == value["id"]
    assert reads == [65_536]


def test_old_attempt_is_hydrated_without_database_write_or_result_change(tmp_path):
    event = create_competition(
        {"type": "single", "seed": 1, "entrants": [{"agent_id": "a"}, {"agent_id": "b"}]}
    )
    match = event["matches"][0]
    job, _, _, _, recording = fixture(tmp_path, event["id"], match["id"])
    event["status"] = "paused"
    match.update(status="error", attempts=[{"jobId": job["id"], "error": "Engine failed"}])
    service = CompetitionService(tmp_path, SimpleNamespace(get=lambda _key: job))
    service._save(event)
    with sqlite3.connect(service.path) as db:
        before = db.execute("SELECT payload FROM local_competitions").fetchone()[0]
    view = service.get(event["id"])
    assert view["matches"][0]["attempts"][0]["replayId"] == recording["id"]
    assert view["champion"] is None and all(row["played"] == 0 for row in view["standings"])
    with sqlite3.connect(service.path) as db:
        assert db.execute("SELECT payload FROM local_competitions").fetchone()[0] == before


@pytest.mark.parametrize("saved", [True, False])
def test_recording_marker_propagates_to_failed_attempt_without_recording_a_result(tmp_path, saved):
    manager = JobManager(tmp_path)
    event = create_competition(
        {"type": "single", "seed": 1, "entrants": [{"agent_id": "a"}, {"agent_id": "b"}]}
    )
    match = event["matches"][0]
    artifact = tmp_path / ".deepdeck/batches/abc"
    job = Job(
        id="failed-job",
        kind="playtest.batch",
        label="QA",
        argv=[],
        status="failed",
        artifact_path=str(artifact),
        details={"completed": 0, "total": 1},
    )
    body = {
        "schemaVersion": "deepdeck-recording-status/v1",
        "batchId": "abc",
        "gameIndex": 1,
        "saved": saved,
        "replayId": "batch-abc-0001" if saved else None,
        "gameStatus": "error",
        "completed": 1,
        "winner": "batch-seat-1",
    }
    manager._consume_process_marker(job, "DEEPDECK_RECORDING_STATUS " + json.dumps(body))
    assert job.details["completed"] == 0 and "winner" not in job.details
    assert job.status == "failed" and job.details["replayId"] == body["replayId"]
    job.logs.append("Engine failed")
    manager._persist(job)
    service = CompetitionService(tmp_path, manager)
    event.update(status="running", activeJobId=job.id, activeMatchId=match["id"])
    match["status"] = "playing"
    service.tick(event)
    assert event["status"] == "paused" and event["champion"] is None
    assert match["attempts"][0]["replayId"] == body["replayId"]
    assert all(row["played"] == 0 for row in service.get(event["id"])["standings"])


@pytest.mark.parametrize(
    "change", ["other-batch", "traversal", "bad-type", "winner-only", "outside", "wrong-job"]
)
def test_recording_marker_ignores_invalid_or_unbound_acknowledgements(tmp_path, change):
    manager = JobManager(tmp_path)
    job = Job(
        id="job",
        kind="playtest.batch",
        label="QA",
        argv=[],
        artifact_path=str(tmp_path / ".deepdeck/batches/abc"),
        details={"completed": 0},
    )
    body = {
        "schemaVersion": "deepdeck-recording-status/v1",
        "batchId": "abc",
        "gameIndex": 1,
        "saved": True,
        "replayId": "batch-abc-0001",
        "gameStatus": "error",
    }
    if change == "other-batch":
        body["batchId"] = "other"
    elif change == "traversal":
        body["replayId"] = "../private"
    elif change == "bad-type":
        body["saved"] = "true"
    elif change == "winner-only":
        body = {"winner": "batch-seat-1", "completed": 1}
    elif change == "outside":
        job.artifact_path = str(tmp_path.parent)
    else:
        job.kind = "training.pool"
    manager._consume_process_marker(job, "DEEPDECK_RECORDING_STATUS " + json.dumps(body))
    assert job.details == {"completed": 0}
