from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


def test_cross_platform_workbench_script_exposes_setup_update_and_start() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "workbench.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )

    assert result.returncode == 0
    assert "setup" in result.stdout
    assert "update" in result.stdout
    assert "start" in result.stdout
    source = script.read_text("utf-8")
    assert "pwsh" not in source
    assert "shell=False" in source


@pytest.mark.parametrize(
    "deep_learning,dev,expected",
    [
        (False, False, ""),
        (True, False, "[deep-learning]"),
        (False, True, "[dev]"),
        (True, True, "[deep-learning,dev]"),
    ],
)
@pytest.mark.parametrize("update_only", [False, True])
def test_setup_keeps_neural_dependencies_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deep_learning: bool,
    dev: bool,
    expected: str,
    update_only: bool,
) -> None:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "workbench_under_test", root / "scripts/workbench.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    python = tmp_path / "venv-python"
    python.touch()
    commands: list[list[str]] = []
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "require", lambda executable, hint: executable)
    monkeypatch.setattr(module, "venv_python", lambda: python)
    monkeypatch.setattr(module, "run", commands.append)
    module.setup(update_only=update_only, deep_learning=deep_learning, dev=dev)
    assert commands[-1] == [str(python), "-m", "pip", "install", "-e", f"{tmp_path}{expected}"]
    assert not any("uninstall" in command for command in commands)


@pytest.mark.parametrize("command", ["setup", "update"])
def test_optional_install_arguments_are_documented(command: str) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/workbench.py"
    result = subprocess.run(
        [sys.executable, str(script), command, "--help"], capture_output=True, text=True, check=True
    )
    assert "--deep-learning" in result.stdout
    assert "--dev" in result.stdout
