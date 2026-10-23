from __future__ import annotations

from pathlib import Path

import pytest

from deepdeck_learner import dependencies
from deepdeck_learner.dependencies import DependencyTaskError


def test_git_diagnostics_timeout_is_unknown_not_status_failure(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(dependencies.shutil, "which", lambda _: "git")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(dependencies.subprocess, "run", timeout)
    assert dependencies.git_output(["rev-parse", "HEAD"], tmp_path) is None


def test_pinned_revision_reads_the_parent_gitlink(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        dependencies,
        "git_output",
        lambda arguments, cwd: "160000 commit 1234567890abcdef\texternal/deepdeck-engine",
    )

    assert dependencies.pinned_revision(tmp_path, "engine") == "1234567890abcdef"


def test_target_revision_reads_origin_main(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "external" / "deepdeck-engine").mkdir(parents=True)
    monkeypatch.setattr(
        dependencies,
        "git_output",
        lambda arguments, cwd: (
            "abcdef1234567890"
            if arguments == ["rev-parse", "--verify", "refs/remotes/origin/main"]
            else None
        ),
    )

    assert dependencies.tracked_branch("engine") == "main"
    assert dependencies.target_revision(tmp_path, "engine") == "abcdef1234567890"


def test_sync_refuses_to_overwrite_submodule_changes(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "external" / "deepdeck-engine").mkdir(parents=True)
    monkeypatch.setattr(dependencies, "has_local_changes", lambda root, dependency: True)

    with pytest.raises(DependencyTaskError, match="local changes"):
        dependencies.sync_dependency(tmp_path, "engine")


def test_sync_updates_tracked_dependency_from_remote_main(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "external" / "deepdeck-engine").mkdir(parents=True)
    commands: list[list[str]] = []
    monkeypatch.setattr(dependencies, "has_local_changes", lambda root, dependency: False)
    monkeypatch.setattr(dependencies.shutil, "which", lambda executable: "git")
    monkeypatch.setattr(
        dependencies.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command),
    )
    monkeypatch.setattr(dependencies, "target_revision", lambda root, dependency: "main-tip")
    monkeypatch.setattr(dependencies, "current_revision", lambda root, dependency: "main-tip")

    dependencies.sync_dependency(tmp_path, "engine")

    assert commands == [
        ["git", "submodule", "sync", "--", "external/deepdeck-engine"],
        [
            "git",
            "submodule",
            "update",
            "--init",
            "--remote",
            "--checkout",
            "--",
            "external/deepdeck-engine",
        ],
    ]


def test_engine_build_is_current_only_when_binary_is_newer(tmp_path: Path) -> None:
    source = tmp_path / "external" / "deepdeck-engine"
    (source / "src").mkdir(parents=True)
    manifest = source / "Cargo.toml"
    rust_source = source / "src" / "main.rs"
    manifest.write_text("[package]\nname='test'\n", encoding="utf-8")
    rust_source.write_text("fn main() {}\n", encoding="utf-8")
    binary = dependencies.engine_binary(tmp_path)
    assert binary.parent.name == "release"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"binary")

    assert dependencies.engine_build_current(tmp_path)
    rust_source.touch()
    rust_source.write_text('fn main() { println!("new"); }\n', encoding="utf-8")
    assert not dependencies.engine_build_current(tmp_path)


def test_prepare_pixi_uses_fixed_commands_and_records_revision(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "external" / "deepdeck-pixi"
    source.mkdir(parents=True)
    (source / "package.json").write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(dependencies, "current_revision", lambda root, dependency: "abc123")
    monkeypatch.setattr(dependencies, "target_revision", lambda root, dependency: "abc123")
    monkeypatch.setattr(dependencies.shutil, "which", lambda executable: "npm")
    monkeypatch.setattr(
        dependencies.subprocess,
        "run",
        lambda arguments, **kwargs: calls.append(arguments),
    )

    dependencies.prepare_pixi(tmp_path)

    assert calls == [["npm", "ci"], ["npm", "run", "build"]]
    assert (tmp_path / ".deepdeck" / "dependencies" / "pixi-build-revision").read_text(
        "utf-8"
    ) == "abc123\n"


def test_bootstrap_syncs_missing_sources_prepares_pixi_and_starts_engine(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(dependencies, "has_local_changes", lambda root, dependency: False)
    monkeypatch.setattr(dependencies, "current_revision", lambda root, dependency: None)
    monkeypatch.setattr(dependencies, "target_revision", lambda root, dependency: "main-tip")
    monkeypatch.setattr(
        dependencies,
        "sync_dependency",
        lambda root, dependency: calls.append(("sync", dependency)),
    )
    monkeypatch.setattr(dependencies, "pixi_build_current", lambda root: False)
    monkeypatch.setattr(
        dependencies, "prepare_pixi", lambda root: calls.append(("prepare", "pixi"))
    )
    monkeypatch.setattr(dependencies, "engine_healthy", lambda: False)
    monkeypatch.setattr(
        dependencies, "start_engine", lambda root: calls.append(("start", "engine"))
    )

    dependencies.bootstrap_local_stack(tmp_path)

    assert calls == [
        ("sync", "engine"),
        ("sync", "pixi"),
        ("prepare", "pixi"),
        ("start", "engine"),
    ]


def test_bootstrap_fetches_main_even_when_cached_revision_is_current(
    tmp_path: Path, monkeypatch
) -> None:
    for dependency, manifest in (("engine", "Cargo.toml"), ("pixi", "package.json")):
        source = tmp_path / "external" / f"deepdeck-{dependency}"
        source.mkdir(parents=True)
        (source / manifest).write_text("", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(dependencies, "has_local_changes", lambda root, dependency: False)
    monkeypatch.setattr(dependencies, "current_revision", lambda root, dependency: "cached-main")
    monkeypatch.setattr(dependencies, "target_revision", lambda root, dependency: "cached-main")
    monkeypatch.setattr(
        dependencies,
        "sync_dependency",
        lambda root, dependency: calls.append(dependency),
    )
    monkeypatch.setattr(dependencies, "pixi_build_current", lambda root: True)
    monkeypatch.setattr(dependencies, "engine_healthy", lambda: True)

    dependencies.bootstrap_local_stack(tmp_path)

    assert calls == ["engine", "pixi"]


def test_bootstrap_refuses_dirty_dependency_before_any_sync(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        dependencies,
        "has_local_changes",
        lambda root, dependency: dependency == "pixi",
    )

    with pytest.raises(DependencyTaskError, match="pixi contains local changes"):
        dependencies.bootstrap_local_stack(tmp_path)
