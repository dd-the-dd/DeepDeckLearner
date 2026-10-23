from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from deepdeck_learner.app import create_app
from deepdeck_learner.experience import (
    ReplayStorageError,
    enforce_replay_quota,
    experience_settings,
    write_replay,
)
from deepdeck_learner.training_platform import (
    acquire_replay_lease,
    list_saved_replays,
    release_replay_lease,
    renew_replay_lease,
    set_replay_favorite,
)


def pilot(root, name):
    run = root / ".deepdeck" / "agents" / name
    run.mkdir(parents=True)
    (run / "local-model.json").write_text(json.dumps({"id": name}))
    return run


def game(run, replay_id, *, age=1):
    path = run / "replays" / f"{replay_id}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schemaVersion": "deepdeck-replay/v1",
                "metadata": {},
                "frames": [{"state": {"players": [{"id": "p1", "name": "You"}]}}],
            }
        )
    )
    os.utime(path, (age, age))
    return path


def limit(root, size):
    (root / ".deepdeck" / "experience.json").write_text(
        json.dumps({"replayStorageLimitBytes": size})
    )


def test_favorite_protects_every_copy_of_a_shared_match_and_reports_full_storage(tmp_path):
    left, right = pilot(tmp_path, "left"), pilot(tmp_path, "right")
    game(left, "batch-shared-0001")
    game(right, "batch-shared-0001")
    set_replay_favorite(tmp_path, "left", "batch-shared-0001", favorite=True)
    limit(tmp_path, 1)
    report = enforce_replay_quota(tmp_path)
    assert report["removed"] == []
    assert report["quotaExceeded"] and "Favorites" in report["error"]
    assert experience_settings(tmp_path)["replayStorageError"]
    assert list_saved_replays(tmp_path, "left")[0]["saved"]
    assert list_saved_replays(tmp_path, "right")[0]["saved"]


def test_removing_favorite_while_watching_keeps_lease_until_viewer_closes(tmp_path):
    run = pilot(tmp_path, "viewer")
    game(run, "match")
    set_replay_favorite(tmp_path, "viewer", "match", favorite=True)
    lease = acquire_replay_lease(tmp_path, "viewer", "match")
    set_replay_favorite(tmp_path, "viewer", "match", favorite=False)
    limit(tmp_path, 1)
    assert enforce_replay_quota(tmp_path)["removed"] == []
    renew_replay_lease(tmp_path, "viewer", "match", lease["leaseId"])
    release_replay_lease(tmp_path, "viewer", "match", lease["leaseId"])
    assert len(enforce_replay_quota(tmp_path)["removed"]) == 1
    assert list_saved_replays(tmp_path, "viewer") == []


def test_oldest_unprotected_match_is_removed_first_and_newer_survives(tmp_path):
    run = pilot(tmp_path, "retention")
    oldest = game(run, "old", age=1)
    newest = game(run, "new", age=100)
    limit(tmp_path, newest.stat().st_size)
    result = enforce_replay_quota(tmp_path)
    assert result["removed"] == [str(oldest)]
    assert newest.is_file() and result["error"] is None


def test_human_only_collection_shares_quota_and_favorites_without_a_model(tmp_path):
    run = tmp_path / ".deepdeck" / "local-tables"
    run.mkdir(parents=True)
    oldest = game(run, "batch-human-old", age=1)
    retained_size = oldest.stat().st_size
    newest = game(run, "batch-human-new", age=100)
    set_replay_favorite(tmp_path, "local-tables", oldest.stem, favorite=True)
    limit(tmp_path, retained_size)
    report = enforce_replay_quota(tmp_path)
    assert not newest.exists() and report["error"] is None
    rows = list_saved_replays(tmp_path, "local-tables")
    assert len(rows) == 1 and rows[0]["saved"] is True
    assert not (run / "local-model.json").exists()
    limit(tmp_path, 1)
    assert enforce_replay_quota(tmp_path)["quotaExceeded"] is True


def test_generic_episode_names_do_not_favorite_unrelated_models(tmp_path):
    left, right = pilot(tmp_path, "left"), pilot(tmp_path, "right")
    game(left, "episode-1")
    game(right, "episode-1")
    set_replay_favorite(tmp_path, "left", "episode-1", favorite=True)
    assert not list_saved_replays(tmp_path, "right")[0]["saved"]


def test_favorite_api_requires_local_token_and_a_boolean(tmp_path):
    run = pilot(tmp_path, "pilot")
    game(run, "match")
    with TestClient(create_app(tmp_path)) as client:
        url = "/api/v1/models/pilot/replays/match/favorite"
        assert client.put(url, json={"favorite": True}).status_code == 403
        headers = {"X-DeepDeck-Token": client.get("/api/v1/session").json()["token"]}
        assert client.put(url, json={"favorite": "false"}, headers=headers).status_code == 422
        assert client.put(url, json={"favorite": True}, headers=headers).json() == {"saved": True}
        assert client.put(url, json={"favorite": False}, headers=headers).json() == {"saved": False}
        assert list_saved_replays(tmp_path, "pilot")[0]["participants"] == [
            {"name": "You", "playerId": "p1"}
        ]


def test_new_recording_reserves_space_and_removes_oldest_not_the_new_game(tmp_path):
    run = pilot(tmp_path, "writer")
    old = game(run, "old", age=1)
    new = run / "replays" / "new.json"
    payload = {"frames": [{"state": {"turnNumber": 2}}]}
    limit(tmp_path, 100)
    write_replay(tmp_path, new, payload)
    assert not old.exists()
    assert json.loads(new.read_text()) == payload


def test_a_new_recording_that_cannot_fit_reports_error_without_overwriting_favorites(tmp_path):
    run = pilot(tmp_path, "writer")
    game(run, "precious")
    set_replay_favorite(tmp_path, "writer", "precious", favorite=True)
    pinned = run / "saved-replays" / "precious.json"
    before = pinned.read_bytes()
    limit(tmp_path, pinned.stat().st_size + 5)
    new = run / "replays" / "new.json"
    with pytest.raises(ReplayStorageError, match="Not enough replay storage"):
        write_replay(tmp_path, new, {"frames": ["x" * 100]})
    assert pinned.read_bytes() == before
    assert not new.exists()
    assert experience_settings(tmp_path)["replayStorageError"]


def test_full_physical_disk_is_a_visible_error_even_with_quota_remaining(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from deepdeck_learner import experience

    run = pilot(tmp_path, "disk")
    limit(tmp_path, 10_000)
    monkeypatch.setattr(experience.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(ReplayStorageError, match="free disk space"):
        write_replay(tmp_path, run / "replays" / "new.json", {"frames": [1]})
    assert "free disk space" in experience_settings(tmp_path)["replayStorageError"]
