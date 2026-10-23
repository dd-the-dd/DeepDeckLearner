from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from deepdeck_learner.replay_lock import ReplayStorageBusyError, replay_storage_lock


def replay_writer(root, name, attempted, finished, paused=None, release=None, expect_full=False):
    from deepdeck_learner.experience import ReplayStorageError, write_replay

    original = Path.write_bytes

    def write(path, content):
        if paused is not None and path.suffix == ".pending":
            paused.set()
            if not release.wait(20):
                raise TimeoutError("Test did not release the pending write")
        return original(path, content)

    Path.write_bytes = write
    attempted.set()
    try:
        destination = Path(root) / ".deepdeck/agents/pilot/replays" / f"{name}.json"
        try:
            write_replay(Path(root), destination, {"frames": [name * 200]})
        except ReplayStorageError:
            if not expect_full:
                raise
        else:
            if expect_full:
                raise AssertionError("Writer overwrote storage reserved for a favorite")
        finished.set()
    finally:
        Path.write_bytes = original


def favorite_worker(root, paused, release):
    from deepdeck_learner.training_platform import set_replay_favorite

    original = Path.replace

    def replace(path, destination):
        if path.parent.name == "replays":
            paused.set()
            if not release.wait(20):
                raise TimeoutError("Test did not release the favorite move")
        return original(path, destination)

    Path.replace = replace
    try:
        set_replay_favorite(Path(root), "pilot", "old", favorite=True)
    finally:
        Path.replace = original


def replay_profile(root, limit):
    run = root / ".deepdeck/agents/pilot"
    (run / "replays").mkdir(parents=True)
    (run / "local-model.json").write_text(json.dumps({"id": "pilot"}), encoding="utf-8")
    (root / ".deepdeck/experience.json").write_text(
        json.dumps({"replayStorageLimitBytes": limit}), encoding="utf-8"
    )
    return run


def finish_workers(children, release):
    release.set()
    for child in children:
        child.join(15)
        if child.is_alive():
            child.terminate()
            child.join(5)
            pytest.fail("Our replay QA worker did not finish")
        assert child.exitcode == 0


def test_simultaneous_writers_cannot_reserve_the_same_quota_space(tmp_path):
    from deepdeck_learner.experience import replay_storage_used

    limit = len(json.dumps({"frames": ["a" * 200]}, separators=(",", ":")).encode())
    run = replay_profile(tmp_path, limit)
    context = multiprocessing.get_context("spawn")
    paused, release = context.Event(), context.Event()
    a_attempted, a_finished, b_attempted, b_finished = [context.Event() for _ in range(4)]
    first = context.Process(
        target=replay_writer, args=(str(tmp_path), "a", a_attempted, a_finished, paused, release)
    )
    second = context.Process(
        target=replay_writer, args=(str(tmp_path), "b", b_attempted, b_finished)
    )
    children = [first]
    first.start()
    try:
        assert paused.wait(15)
        second.start()
        children.append(second)
        assert b_attempted.wait(15)
        assert not b_finished.wait(0.2), "Second writer bypassed the first reservation"
    finally:
        finish_workers(children, release)
    assert a_finished.is_set() and b_finished.is_set()
    assert replay_storage_used(tmp_path) <= limit
    assert not (run / "replays/a.json").exists()
    assert json.loads((run / "replays/b.json").read_text()) == {"frames": ["b" * 200]}


def test_a_favorite_move_and_quota_cleanup_are_one_transaction(tmp_path):
    run = replay_profile(tmp_path, 220)
    old = run / "replays/old.json"
    original = b'{"frames":[{"state":{"players":[]}}]}'
    old.write_bytes(original)
    context = multiprocessing.get_context("spawn")
    paused, release, attempted, finished = [context.Event() for _ in range(4)]
    favorite = context.Process(target=favorite_worker, args=(str(tmp_path), paused, release))
    writer = context.Process(
        target=replay_writer, args=(str(tmp_path), "b", attempted, finished, None, None, True)
    )
    children = [favorite]
    favorite.start()
    try:
        assert paused.wait(15)
        writer.start()
        children.append(writer)
        assert attempted.wait(15)
        assert not finished.wait(0.2), "Cleanup raced the in-progress favorite move"
    finally:
        finish_workers(children, release)
    assert finished.is_set()
    assert (run / "saved-replays/old.json").read_bytes() == original
    assert not (run / "replays/b.json").exists()


def hold_lock(root, acquired, release, crash):
    with replay_storage_lock(Path(root)):
        acquired.set()
        if not release.wait(20):
            raise TimeoutError("Test did not release the replay lock")
        if crash:
            # Exit without unwinding the lock context, but only after leaving
            # Event.wait: terminating inside multiprocessing synchronization
            # itself could corrupt its semaphore and hang the test parent.
            os._exit(3)


def test_replay_lock_is_reentrant_and_existing_lock_file_is_not_busy(tmp_path):
    for _ in range(2):
        with replay_storage_lock(tmp_path, timeout=0), replay_storage_lock(tmp_path, timeout=0):
            assert (tmp_path / ".deepdeck/replay-storage.lock").is_file()


@pytest.mark.parametrize("crash", [False, True])
def test_replay_lock_serializes_processes_and_is_released_on_exit(tmp_path, crash):
    context = multiprocessing.get_context("spawn")
    acquired, release = context.Event(), context.Event()
    child = context.Process(target=hold_lock, args=(str(tmp_path), acquired, release, crash))
    child.start()
    try:
        assert acquired.wait(15), "Child never acquired the profile lock"
        with pytest.raises(ReplayStorageBusyError), replay_storage_lock(tmp_path, timeout=0.05):
            pytest.fail("Two processes acquired the same profile lock")
        # Another profile is independent.
        with replay_storage_lock(tmp_path / "other", timeout=0):
            pass
        release.set()
        child.join(15)
        assert not child.is_alive()
        if not crash:
            assert child.exitcode == 0
        with replay_storage_lock(tmp_path, timeout=1):
            pass
    finally:
        release.set()
        child.join(2)
        if child.is_alive():
            child.terminate()
            child.join(5)
