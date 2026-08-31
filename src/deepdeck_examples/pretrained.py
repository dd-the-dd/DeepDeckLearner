from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

RELEASE_TAG = "pretrained-agents-v1"
RELEASE_ROOT = (
    "https://github.com/dd-the-dd/DeepDeckLearner/releases/download/"
    f"{RELEASE_TAG}"
)


@dataclass(frozen=True)
class PretrainedAgent:
    id: str
    name: str
    architecture: str
    format: str
    training_step: int
    model_family: str
    observation_schema: str
    asset_name: str
    asset_bytes: int
    sha256: str
    description: str

    @property
    def url(self) -> str:
        return f"{RELEASE_ROOT}/{self.asset_name}"

    @property
    def run_name(self) -> str:
        return f"pretrained-{self.id.replace('.', '-')}"

    @property
    def checkpoint_name(self) -> str:
        return self.asset_name.removesuffix(".zip")


PRETRAINED_AGENTS = {
    "v12.1": PretrainedAgent(
        id="v12.1",
        name="Deep Deck V12.1",
        architecture="v12",
        format="legacy",
        training_step=418_148,
        model_family="structured-v12",
        observation_schema="structured-observation/v12",
        asset_name="v12.1-step-418148.zip",
        asset_bytes=121_448_506,
        sha256="57f79da48182f7fedd2cfd21f3f693acbefbbf5416bec2ada7a238989c8b1856",
        description="Frozen two-player Legacy policy trained by Deep Deck League.",
    ),
    "v11.1": PretrainedAgent(
        id="v11.1",
        name="Deep Deck V11.1",
        architecture="v11",
        format="commander",
        training_step=186_266,
        model_family="structured-v11",
        observation_schema="structured-observation/v11",
        asset_name="v11.1-step-186266.zip",
        asset_bytes=123_855_757,
        sha256="c0db8c7306e28ec4a8526bcf602d6da5686bd4ef5db508f2fe1cba64a72dbdf7",
        description="Frozen four-player Commander policy trained by Deep Deck League.",
    ),
}

Download = Callable[[PretrainedAgent, Path], None]


def project_root(path: str | Path | None = None) -> Path:
    configured = path or os.getenv("DEEPDECK_PROJECT_ROOT") or Path.cwd()
    return Path(configured).expanduser().resolve()


def checkpoint_path(agent: PretrainedAgent, root: str | Path | None = None) -> Path:
    return (
        project_root(root)
        / ".deepdeck"
        / "runs"
        / agent.run_name
        / "checkpoints"
        / agent.checkpoint_name
    )


def _valid_checkpoint(path: Path, agent: PretrainedAgent) -> bool:
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        (path / "checkpoint.pt").is_file()
        and manifest.get("schema_version") == "oracle-ai-checkpoint/v1"
        and manifest.get("model_family") == agent.model_family
        and manifest.get("observation_schema") == agent.observation_schema
        and int(manifest.get("training_step", -1)) == agent.training_step
    )


def _download_archive(agent: PretrainedAgent, destination: Path) -> None:
    request = urllib.request.Request(
        agent.url,
        headers={"User-Agent": "DeepDeckLearner-pretrained-agent/1"},
    )
    downloaded = 0
    next_report = 10
    print(
        f"Downloading {agent.name} ({agent.asset_bytes / (1024 * 1024):.0f} MiB)…",
        file=sys.stderr,
    )
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            downloaded += len(chunk)
            percent = downloaded * 100 // agent.asset_bytes
            if percent >= next_report:
                print(f"  {min(percent, 100)}%", file=sys.stderr)
                next_report += 10


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_archive(archive: Path, destination: Path) -> None:
    expected = {"checkpoint.pt", "manifest.json", "model-card.json"}
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        if names != expected:
            raise ValueError(
                "pretrained bundle must contain only checkpoint.pt, manifest.json, "
                "and model-card.json"
            )
        destination.mkdir(parents=True)
        for name in sorted(expected):
            with bundle.open(name) as source, (destination / name).open("wb") as output:
                shutil.copyfileobj(source, output)


def _register_agent(agent: PretrainedAgent, root: Path, checkpoint: Path) -> None:
    run = checkpoint.parent.parent
    metadata = {
        "schemaVersion": "local-model/v1",
        "id": f"deepdeck-{agent.id.replace('.', '-')}",
        "name": agent.name,
        "architecture": agent.architecture,
        "format": agent.format,
        "description": agent.description,
        "createdAt": "2026-08-31T00:00:00Z",
        "checkpointPath": str(checkpoint.resolve()),
        "reservePlaytest": True,
        "selfPlayAllSeats": False,
        "decks": [],
        "source": "official-pretrained",
        "pretrainedVersion": agent.id,
        "trainingStep": agent.training_step,
        "releaseUrl": (
            "https://github.com/dd-the-dd/DeepDeckLearner/releases/tag/"
            f"{RELEASE_TAG}"
        ),
    }
    run.mkdir(parents=True, exist_ok=True)
    (run / "local-model.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    catalog = root / ".deepdeck" / "pretrained-agents.json"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    installed: list[dict[str, object]] = []
    try:
        value = json.loads(catalog.read_text(encoding="utf-8"))
        raw_installed = value.get("installed", []) if isinstance(value, dict) else []
        installed = [item for item in raw_installed if isinstance(item, dict)]
    except (OSError, ValueError):
        pass
    installed = [item for item in installed if item.get("id") != agent.id]
    installed.append(
        {
            **asdict(agent),
            "checkpointPath": str(checkpoint.resolve()),
            "sha256": agent.sha256,
        }
    )
    catalog.write_text(
        json.dumps(
            {"schemaVersion": "deepdeck-pretrained-catalog/v1", "installed": installed},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def install_pretrained_agent(
    agent_id: str,
    root: str | Path | None = None,
    *,
    download: Download = _download_archive,
) -> Path:
    try:
        agent = PRETRAINED_AGENTS[agent_id]
    except KeyError as error:
        available = ", ".join(PRETRAINED_AGENTS)
        raise ValueError(f"unknown pretrained agent {agent_id}; available: {available}") from error
    resolved_root = project_root(root)
    destination = checkpoint_path(agent, resolved_root)
    if _valid_checkpoint(destination, agent):
        _register_agent(agent, resolved_root, destination)
        return destination

    downloads = resolved_root / ".deepdeck" / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / agent.asset_name
    partial = downloads / f"{agent.asset_name}.partial"
    partial.unlink(missing_ok=True)
    if not archive.is_file() or _sha256(archive) != agent.sha256:
        archive.unlink(missing_ok=True)
        download(agent, partial)
        if _sha256(partial) != agent.sha256:
            partial.unlink(missing_ok=True)
            raise ValueError(f"checksum verification failed for {agent.asset_name}")
        partial.replace(archive)

    temporary = destination.with_name(f"{destination.name}.installing")
    if temporary.exists():
        shutil.rmtree(temporary)
    _extract_archive(archive, temporary)
    if not _valid_checkpoint(temporary, agent):
        shutil.rmtree(temporary)
        raise ValueError(f"{agent.asset_name} contains an incompatible checkpoint")
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary.replace(destination)
    _register_agent(agent, resolved_root, destination)
    return destination


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Manage official pretrained Deep Deck agents.")
    result.add_argument("command", choices=("list", "install", "path"))
    result.add_argument("agent", nargs="?", choices=tuple(PRETRAINED_AGENTS))
    result.add_argument("--project-root", default=None)
    return result


def main() -> None:
    arguments = parser().parse_args()
    if arguments.command == "list":
        for agent in PRETRAINED_AGENTS.values():
            installed = _valid_checkpoint(
                checkpoint_path(agent, arguments.project_root), agent
            )
            status = "installed" if installed else "available"
            print(
                f"{agent.id}\t{status}\t{agent.format}\tstep {agent.training_step}\t"
                f"{agent.asset_bytes / (1024 * 1024):.0f} MiB"
            )
        return
    if not arguments.agent:
        raise SystemExit(f"{arguments.command} requires an agent: {', '.join(PRETRAINED_AGENTS)}")
    path = (
        install_pretrained_agent(arguments.agent, arguments.project_root)
        if arguments.command == "install"
        else checkpoint_path(PRETRAINED_AGENTS[arguments.agent], arguments.project_root)
    )
    print(path)


if __name__ == "__main__":
    main()
