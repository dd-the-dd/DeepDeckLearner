from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

DEPENDENCIES = {
    "engine": Path("external/deepdeck-engine"),
    "pixi": Path("external/deepdeck-pixi"),
}


class DependencyTaskError(RuntimeError):
    pass


def dependency_path(root: Path, dependency: str) -> Path:
    relative = DEPENDENCIES.get(dependency)
    if relative is None:
        raise DependencyTaskError(f"Unsupported dependency: {dependency}")
    return root.resolve() / relative


def git_output(arguments: list[str], cwd: Path) -> str | None:
    git = shutil.which("git")
    if not git:
        return None
    result = subprocess.run(
        [git, *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        shell=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def pinned_revision(root: Path, dependency: str) -> str | None:
    relative = DEPENDENCIES.get(dependency)
    if relative is None:
        return None
    output = git_output(["ls-tree", "HEAD", "--", relative.as_posix()], root.resolve())
    if not output:
        return None
    fields = output.split()
    return fields[2] if len(fields) >= 3 and fields[1] == "commit" else None


def current_revision(root: Path, dependency: str) -> str | None:
    source = dependency_path(root, dependency)
    if not source.is_dir():
        return None
    return git_output(["rev-parse", "HEAD"], source)


def has_local_changes(root: Path, dependency: str) -> bool:
    source = dependency_path(root, dependency)
    if not source.is_dir():
        return False
    output = git_output(["status", "--porcelain"], source)
    return bool(output)


def engine_binary(root: Path) -> Path:
    name = "mtg-engine-server.exe" if os.name == "nt" else "mtg-engine-server"
    return dependency_path(root, "engine") / "target" / "debug" / name


def engine_build_current(root: Path) -> bool:
    binary = engine_binary(root)
    source = dependency_path(root, "engine")
    if not binary.is_file() or not source.is_dir():
        return False
    inputs = [source / "Cargo.toml", source / "Cargo.lock", *source.glob("src/**/*.rs")]
    newest_input = max(
        (path.stat().st_mtime for path in inputs if path.is_file()),
        default=float("inf"),
    )
    return binary.stat().st_mtime >= newest_input


def sync_dependency(root: Path, dependency: str) -> None:
    relative = DEPENDENCIES.get(dependency)
    if relative is None:
        raise DependencyTaskError(f"Unsupported dependency: {dependency}")
    if has_local_changes(root, dependency):
        raise DependencyTaskError(
            f"{dependency} contains local changes. Commit or move them before synchronizing."
        )
    git = shutil.which("git")
    if not git:
        raise DependencyTaskError("Git is required to synchronize dependencies.")
    commands = [
        [git, "submodule", "sync", "--", relative.as_posix()],
        [
            git,
            "submodule",
            "update",
            "--init",
            "--recursive",
            "--checkout",
            "--",
            relative.as_posix(),
        ],
    ]
    for command in commands:
        print(f"Running: {' '.join(command)}", flush=True)
        subprocess.run(command, cwd=root, check=True, shell=False)


def prepare_pixi(root: Path) -> None:
    source = dependency_path(root, "pixi")
    package = source / "package.json"
    if not package.is_file():
        raise DependencyTaskError("DeepDeckPixi is missing. Synchronize it first.")
    current = current_revision(root, "pixi")
    pinned = pinned_revision(root, "pixi")
    if not current or current != pinned:
        raise DependencyTaskError("DeepDeckPixi is not at the compatible revision. Sync it first.")
    npm = shutil.which("npm")
    if not npm:
        raise DependencyTaskError("Node.js and npm are required to prepare DeepDeckPixi.")
    for arguments in ([npm, "ci"], [npm, "run", "build"]):
        print(f"Running: {' '.join(arguments)}", flush=True)
        subprocess.run(arguments, cwd=source, check=True, shell=False)
    marker = root / ".deepdeck" / "dependencies" / "pixi-build-revision"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{current}\n", encoding="utf-8")
    print(f"DeepDeckPixi {current[:8]} is ready.", flush=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run an allowlisted dependency task.")
    result.add_argument("action", choices=("sync", "prepare-pixi"))
    result.add_argument("--root", type=Path, required=True)
    result.add_argument("--dependency", choices=tuple(DEPENDENCIES))
    return result


def main() -> None:
    arguments = parser().parse_args()
    root = arguments.root.resolve()
    if arguments.action == "sync":
        if not arguments.dependency:
            raise SystemExit("--dependency is required for sync")
        sync_dependency(root, arguments.dependency)
    else:
        prepare_pixi(root)


if __name__ == "__main__":
    try:
        main()
    except (DependencyTaskError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
