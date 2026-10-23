from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from deepdeck_learner import dependencies, desktop, desktop_worker, runtime, status
from deepdeck_learner.jobs import JobManager


def test_windows_http_loop_policy_is_controller_only(monkeypatch) -> None:
    import asyncio

    installed = []
    policy = object()
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", lambda: policy, raising=False)
    monkeypatch.setattr(asyncio, "set_event_loop_policy", installed.append)
    runtime.configure_local_http_loop()
    assert installed == [policy]
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    runtime.configure_local_http_loop()
    assert installed == [policy]


def _bundle(root: Path, monkeypatch) -> Path:
    bundle = root / "bundle"
    bundle.mkdir()
    (bundle / "deepdeck-runtime.json").write_text(
        json.dumps(
            {
                "schemaVersion": "deepdeck-desktop/v1",
                "components": {
                    "engine": {"revision": "engine-main"},
                    "pixi": {"revision": "pixi-main"},
                },
            }
        )
    )
    (bundle / "engine").mkdir()
    name = "mtg-engine-server.exe" if os.name == "nt" else "mtg-engine-server"
    (bundle / "engine" / name).write_bytes(b"test engine")
    frontend = bundle / "deepdeck_learner" / "web"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("<html></html>")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    return bundle


def test_desktop_data_stays_outside_the_installation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "User data"))
    monkeypatch.delenv("DEEPDECK_LEARNER_ROOT", raising=False)
    _bundle(tmp_path, monkeypatch)
    assert status.project_root() == tmp_path / "User data" / "DeepDeck" / "PlayerLab"
    assert not status.project_root().exists()  # Path discovery does not copy any user data.


def test_wheel_data_stays_outside_python_installation(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPDECK_LEARNER_ROOT", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    package = tmp_path / "Python/Lib/site-packages/deepdeck_learner/status.py"
    monkeypatch.setattr(status, "__file__", str(package))
    profile = tmp_path / "private-player-data"
    monkeypatch.setattr(status, "desktop_data_root", lambda: profile)
    assert status.project_root() == profile
    assert not profile.exists()


def test_source_launch_keeps_the_workspace_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPDECK_LEARNER_ROOT", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    package = tmp_path / "src/deepdeck_learner/status.py"
    package.parent.mkdir(parents=True)
    package.touch()
    (tmp_path / "pyproject.toml").touch()
    monkeypatch.setattr(status, "__file__", str(package))
    assert status.project_root() == tmp_path


def test_frozen_dependencies_use_embedded_main_revisions_without_git(tmp_path, monkeypatch) -> None:
    bundle = _bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(
        dependencies,
        "git_output",
        lambda *args, **kwargs: pytest.fail("A player should not need Git"),
    )
    monkeypatch.setattr(status, "engine_healthy", lambda url: True)
    actual = status.capability_status(tmp_path / "user-profile", "http://127.0.0.1:8897")
    assert actual["engine"]["built"] is True
    assert actual["engine"]["synced"] is True
    assert actual["engine"]["revision"] == "engine-main"
    assert actual["pixi"]["built"] is True and actual["pixi"]["build_present"] is True
    assert dependencies.engine_binary(tmp_path).parent == bundle / "engine"


def test_desktop_engine_restart_uses_its_bundle_and_dedicated_port(tmp_path, monkeypatch) -> None:
    bundle = _bundle(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPDECK_LOCAL_ENGINE_URL", "http://127.0.0.1:8897")
    manager = JobManager(tmp_path / "user-data")
    assert manager._dependency_for("dependency.engine.start", {}) == "engine"
    command, _label, artifact = manager._dependency_command("dependency.engine.start", {})
    assert command == [
        str(
            bundle
            / "engine"
            / ("mtg-engine-server.exe" if os.name == "nt" else "mtg-engine-server")
        )
    ]
    assert artifact is None
    assert manager._engine_environment() == {"MTG_ENGINE_ADDR": "127.0.0.1:8897", "MTG_UI_ADDR": ""}


def test_frozen_worker_has_real_console_dispatch_and_no_arbitrary_module(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    executable = tmp_path / "DeepDeck.exe"
    worker = tmp_path / ("DeepDeckWorker.exe" if os.name == "nt" else "DeepDeckWorker")
    worker.touch()
    monkeypatch.setattr(sys, "executable", str(executable))
    assert runtime.worker_argv(
        [str(executable), "-m", "deepdeck_examples.local_batch", "--spec", "a file.json"]
    ) == [str(worker), "-m", "deepdeck_examples.local_batch", "--spec", "a file.json"]
    with pytest.raises(ValueError, match="not included"):
        runtime.worker_argv([str(executable), "-m", "os"])
    assert runtime.worker_argv(["native-engine.exe"]) == ["native-engine.exe"]
    worker.unlink()
    with pytest.raises(ValueError, match="Extract the complete"):
        runtime.worker_argv([str(executable), "-m", "deepdeck_examples.run"])


def test_source_worker_arguments_remain_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    args = [sys.executable, "-m", "oracle_ai.training.league", "--config", "advanced.yml"]
    assert runtime.worker_argv(args) == args


def test_console_worker_dispatches_only_an_allowlisted_module(monkeypatch) -> None:
    monkeypatch.setattr(desktop_worker.multiprocessing, "freeze_support", lambda: None)
    observed = []
    monkeypatch.setattr(
        desktop_worker.runpy,
        "run_module",
        lambda module, **kwargs: observed.append((module, list(sys.argv))),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "DeepDeckWorker.exe",
            "-m",
            "deepdeck_examples.run",
            "programmatic",
            "--agent-config",
            "my agent",
        ],
    )
    desktop_worker.main()
    assert observed == [
        (
            "deepdeck_examples.run",
            ["deepdeck_examples.run", "programmatic", "--agent-config", "my agent"],
        )
    ]
    monkeypatch.setattr(sys, "argv", ["DeepDeckWorker.exe", "-m", "os"])
    with pytest.raises(SystemExit, match="only DeepDeck"):
        desktop_worker.main()


def test_profile_lock_prevents_two_windows_editing_the_same_data(tmp_path) -> None:
    with (
        desktop.ProfileLock(tmp_path),
        pytest.raises(desktop.AlreadyRunningError),
        desktop.ProfileLock(tmp_path),
    ):
        pytest.fail("The active lock must not be acquired twice")
    with desktop.ProfileLock(tmp_path):
        pass  # An old lock file is not mistaken for a live owner.


def test_desktop_never_attaches_to_an_unrelated_local_server() -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with desktop._listener(occupied.getsockname()[1]) as listener:
            assert listener.getsockname()[0] == "127.0.0.1"
            assert listener.getsockname()[1] != occupied.getsockname()[1]


def test_player_controller_import_does_not_require_the_training_stack(tmp_path) -> None:
    script = """
import importlib.abc
import sys
class NoTraining(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'numpy', 'yaml'}:
            raise ModuleNotFoundError('Optional training dependency: '+fullname, name=fullname)
sys.meta_path.insert(0, NoTraining())
from fastapi.testclient import TestClient
from deepdeck_learner.app import create_app
from pathlib import Path
client = TestClient(create_app(Path(sys.argv[1])))
assert client.get('/api/v1/health').status_code == 200
assert client.get('/api/v1/agent-studio/heuristics').status_code == 200
assert client.get('/api/v1/training/curriculum').status_code == 501
"""
    environment = {**os.environ, "DEEPDECK_LEARNER_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("dirty,active", [(True, False), (False, True), (True, True)])
def test_native_close_warns_about_drafts_and_active_agents(dirty, active) -> None:
    prompts = []
    window = SimpleNamespace(
        evaluate_js=lambda code: dirty,
        create_confirmation_dialog=lambda title, message: prompts.append(message) or False,
    )
    manager = SimpleNamespace(list_jobs=lambda: [{"status": "running"}] if active else [])
    assert desktop._allow_window_close(window, manager, False) is False
    assert ("modifications" in prompts[0]) is dirty
    assert ("agents" in prompts[0]) is active
    assert desktop._allow_window_close(window, manager, True) is True


def test_native_labels_do_not_concatenate_hidden_compact_navigation_text() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is only needed for this developer-side JS regression")
    labels = ["Play", "Decks", "Agents", "League", "Competitions", "Replays", "Insights"]
    script = """
      const labels = LABELS;
      const buttons = labels.map(label => ({querySelector(selector) {
        if(selector === '.nav-full-label')
          return label === 'Competitions' ? {textContent:label} : null;
        if(selector === 'strong')
          return {textContent: label === 'Competitions' ? 'CompetitionsEvents' : label};
        throw new Error('Unexpected selector: '+selector);
      }}));
      globalThis.document = {querySelectorAll: () => buttons};
      console.log(JSON.stringify(EXPRESSION));
    """.replace("LABELS", json.dumps(labels)).replace("EXPRESSION", desktop.NATIVE_TAB_LABELS_JS)
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == labels


def test_native_close_of_idle_saved_work_needs_no_extra_confirmation() -> None:
    window = SimpleNamespace(
        evaluate_js=lambda code: False,
        create_confirmation_dialog=lambda *args: pytest.fail("No destructive work is pending"),
    )
    assert desktop._allow_window_close(window, SimpleNamespace(list_jobs=lambda: []), False) is True
