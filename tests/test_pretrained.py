from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from deepdeck_examples import pretrained


def _bundle(path: Path, agent: pretrained.PretrainedAgent) -> str:
    manifest = {
        "schema_version": "oracle-ai-checkpoint/v1",
        "model_family": agent.model_family,
        "observation_schema": agent.observation_schema,
        "training_step": agent.training_step,
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("checkpoint.pt", b"verified weights")
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("model-card.json", "{}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_official_catalog_exposes_legacy_and_commander_agents() -> None:
    assert pretrained.PRETRAINED_AGENTS["v12.1"].format == "legacy"
    assert pretrained.PRETRAINED_AGENTS["v12.1"].training_step == 418_148
    assert pretrained.PRETRAINED_AGENTS["v11.1"].format == "commander"
    assert pretrained.PRETRAINED_AGENTS["v11.1"].training_step == 186_266


def test_install_verifies_and_registers_a_pretrained_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "source.zip"
    original = pretrained.PRETRAINED_AGENTS["v12.1"]
    digest = _bundle(archive, original)
    model = replace(
        original,
        asset_name="test.zip",
        asset_bytes=archive.stat().st_size,
        sha256=digest,
    )
    monkeypatch.setitem(pretrained.PRETRAINED_AGENTS, "v12.1", model)
    downloads = 0

    def copy_download(_agent: pretrained.PretrainedAgent, destination: Path) -> None:
        nonlocal downloads
        downloads += 1
        destination.write_bytes(archive.read_bytes())

    checkpoint = pretrained.install_pretrained_agent(
        "v12.1", tmp_path / "project", download=copy_download
    )
    cached = pretrained.install_pretrained_agent(
        "v12.1", tmp_path / "project", download=copy_download
    )

    assert checkpoint == cached
    assert downloads == 1
    assert (checkpoint / "checkpoint.pt").read_bytes() == b"verified weights"
    metadata = json.loads((checkpoint.parent.parent / "local-model.json").read_text("utf-8"))
    assert metadata["id"] == "deepdeck-v12-1"
    assert metadata["source"] == "official-pretrained"
    assert Path(metadata["checkpointPath"]) == checkpoint


def test_install_rejects_a_bundle_with_the_wrong_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = replace(pretrained.PRETRAINED_AGENTS["v11.1"], sha256="0" * 64)
    monkeypatch.setitem(pretrained.PRETRAINED_AGENTS, "v11.1", model)

    def corrupt_download(_agent: pretrained.PretrainedAgent, destination: Path) -> None:
        destination.write_bytes(b"not the published model")

    with pytest.raises(ValueError, match="checksum verification failed"):
        pretrained.install_pretrained_agent(
            "v11.1", tmp_path / "project", download=corrupt_download
        )

    assert not pretrained.checkpoint_path(model, tmp_path / "project").exists()
