from __future__ import annotations

from pathlib import Path

import pytest

from deepdeck_learner import dependencies
from deepdeck_learner.dependencies import DependencyTaskError


def test_pinned_revision_reads_the_parent_gitlink(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        dependencies,
        "git_output",
        lambda arguments, cwd: (
            "160000 commit 1234567890abcdef\texternal/deepdeck-engine"
        ),
    )

    assert dependencies.pinned_revision(tmp_path, "engine") == "1234567890abcdef"


def test_sync_refuses_to_overwrite_submodule_changes(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "external" / "deepdeck-engine").mkdir(parents=True)
    monkeypatch.setattr(dependencies, "has_local_changes", lambda root, dependency: True)

    with pytest.raises(DependencyTaskError, match="local changes"):
        dependencies.sync_dependency(tmp_path, "engine")


def test_engine_build_is_current_only_when_binary_is_newer(tmp_path: Path) -> None:
    source = tmp_path / "external" / "deepdeck-engine"
    (source / "src").mkdir(parents=True)
    manifest = source / "Cargo.toml"
    rust_source = source / "src" / "main.rs"
    manifest.write_text("[package]\nname='test'\n", encoding="utf-8")
    rust_source.write_text("fn main() {}\n", encoding="utf-8")
    binary = dependencies.engine_binary(tmp_path)
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"binary")

    assert dependencies.engine_build_current(tmp_path)
    rust_source.touch()
    rust_source.write_text("fn main() { println!(\"new\"); }\n", encoding="utf-8")
    assert not dependencies.engine_build_current(tmp_path)


def test_prepare_pixi_uses_fixed_commands_and_records_revision(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "external" / "deepdeck-pixi"
    source.mkdir(parents=True)
    (source / "package.json").write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(dependencies, "current_revision", lambda root, dependency: "abc123")
    monkeypatch.setattr(dependencies, "pinned_revision", lambda root, dependency: "abc123")
    monkeypatch.setattr(dependencies.shutil, "which", lambda executable: "npm")
    monkeypatch.setattr(
        dependencies.subprocess,
        "run",
        lambda arguments, **kwargs: calls.append(arguments),
    )

    dependencies.prepare_pixi(tmp_path)

    assert calls == [["npm", "ci"], ["npm", "run", "build"]]
    assert (
        tmp_path / ".deepdeck" / "dependencies" / "pixi-build-revision"
    ).read_text("utf-8") == "abc123\n"
