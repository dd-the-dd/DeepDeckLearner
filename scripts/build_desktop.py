"""Build a self-contained Windows player ZIP without bundling private workspaces."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# The isolated builder may contain the previously installed wheel. Packaging
# guards must inspect this checkout, just like the spec's source-first Analysis.
sys.path.insert(0, str(ROOT / "src"))
from deepdeck_learner import dependencies as _source_dependencies  # noqa: E402
from deepdeck_learner.dependencies import engine_build_current  # noqa: E402

LEARNER_SOURCE = Path(_source_dependencies.__file__).resolve()
if (ROOT / "src" / "deepdeck_learner" / "dependencies.py").resolve() != LEARNER_SOURCE:
    raise SystemExit(
        "The desktop builder imported another Learner installation. Run this script in a fresh Python process from the intended checkout."
    )


def git(*arguments: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(["git", *arguments], cwd=cwd, text=True).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def component_manifest(name: str, *, allow_dirty: bool = False) -> dict[str, str | bool]:
    source = ROOT / "external" / f"deepdeck-{name}"
    dirty = bool(git("status", "--porcelain", cwd=source))
    if dirty and not allow_dirty:
        raise SystemExit(
            f"{name} has local changes. They have not been overwritten. "
            "Use --allow-dirty-components only for an explicitly marked development preview."
        )
    revision = git("rev-parse", "HEAD", cwd=source)
    if name in {"engine", "pixi"} and revision != git("rev-parse", "origin/main", cwd=source):
        raise SystemExit(
            f"{name} is not at its fetched origin/main. Synchronize explicitly before packaging."
        )
    return {"revision": revision, "branch": "main" if name != "agent" else "pinned", "dirty": dirty}


def storage_status(destination: Path) -> dict[str, str | int | bool]:
    """Inspect a future output location without creating any directories."""
    target = destination.resolve()
    parent = target
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    free = shutil.disk_usage(parent).free
    recommended = 2 * 1024**3
    return {
        "path": str(target),
        "existingAncestor": str(parent),
        "freeBytes": free,
        "recommendedHeadroomBytes": recommended,
        "lowHeadroom": free < recommended,
    }


def prepare_web(stage: Path, *, skip_build: bool) -> Path:
    """Keep desktop assets separate from chunks retained for open source-app tabs."""
    if skip_build:
        web = ROOT / "apps/learner-web/dist"
    else:
        npm = shutil.which("npm")
        if not npm:
            raise SystemExit("Node/npm is needed by the package builder, not the player.")
        web = stage / "web"
        # stage is unique per build; never empty the running app's dist directory.
        subprocess.run(
            [npm, "run", "build", "--", "--outDir", str(web)],
            cwd=ROOT / "apps/learner-web",
            check=True,
        )
    if not (web / "index.html").is_file():
        raise SystemExit("The player frontend has not been built.")
    return web


def inspect_build(
    *, output_root: Path, work_root: Path, allow_dirty: bool, skip_web_build: bool
) -> dict[str, Any]:
    """Read-only preflight. Disk headroom is advisory, not a fabricated size estimate."""
    blockers = []
    warnings = []
    components = {}
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        blockers.append("This recipe builds Windows x64 on a Windows x64 host.")
    for name in ("engine", "pixi", "agent"):
        try:
            components[name] = component_manifest(name, allow_dirty=allow_dirty)
        except (SystemExit, OSError, subprocess.CalledProcessError) as error:
            blockers.append(f"{name}: {error}")
    binary = ROOT / "external/deepdeck-engine/target/release/mtg-engine-server.exe"
    current = binary.is_file() and engine_build_current(ROOT)
    newer = []
    if not binary.is_file():
        blockers.append("Build the Engine release executable before packaging.")
    elif not current:
        source = ROOT / "external/deepdeck-engine"
        inputs = [source / "Cargo.toml", source / "Cargo.lock", *source.glob("src/**/*.rs")]
        newer = sorted(
            str(path.relative_to(source))
            for path in inputs
            if path.is_file() and path.stat().st_mtime > binary.stat().st_mtime
        )
        blockers.append(
            "The Engine release binary is older than its source files. Rebuild it; this check does not bypass freshness guards."
        )
    web_present = (ROOT / "apps/learner-web/dist/index.html").is_file()
    if skip_web_build and not web_present:
        blockers.append("The player frontend is missing; build it before using --skip-web-build.")
    if not skip_web_build and not shutil.which("npm"):
        blockers.append("Node/npm is required by the package builder, not by the player.")
    if importlib.util.find_spec("PyInstaller") is None:
        blockers.append(
            "PyInstaller is missing from this Python environment. Use the isolated desktop-build environment."
        )
    storage = []
    for label, destination in (("output", output_root), ("work", work_root)):
        try:
            value = {"purpose": label, **storage_status(destination)}
            storage.append(value)
            if value["lowHeadroom"]:
                warnings.append(
                    f"Low disk headroom for {label}: {value['freeBytes']} bytes free. The 2 GiB recommendation is advisory, not a guaranteed build-size estimate."
                )
        except OSError as error:
            blockers.append(f"Cannot inspect {label} storage: {error}")
    return {
        "schemaVersion": "deepdeck-desktop-preflight/v1",
        "canStartBuild": not blockers,
        "builderPython": sys.executable,
        "learnerSource": str(LEARNER_SOURCE),
        "components": components,
        "engineCurrent": current,
        "newerEngineInputs": newer,
        "webBuildPresent": web_present,
        "storage": storage,
        "blockers": blockers,
        "warnings": warnings,
        "notice": "No build, checkout change, download or deletion was performed. Output/work paths on the same volume share the reported free space. Rust rebuilds and verification need additional headroom.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-web-build", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print a read-only JSON preflight; exit nonzero for missing prerequisites without starting a build.",
    )
    parser.add_argument(
        "--allow-dirty-components",
        action="store_true",
        help="Package uncommitted fixes in a marked development preview; never commits or changes branches.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "dist/desktop",
        help="Destination for timestamped builds (use a drive with enough space).",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=ROOT / ".deepdeck/desktop-build",
        help="Temporary PyInstaller work/staging directory.",
    )
    arguments = parser.parse_args()
    if arguments.check:
        result = inspect_build(
            output_root=arguments.output_root,
            work_root=arguments.work_root,
            allow_dirty=arguments.allow_dirty_components,
            skip_web_build=arguments.skip_web_build,
        )
        print(json.dumps(result, indent=2), flush=True)
        if not result["canStartBuild"]:
            raise SystemExit(1)
        return
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("This recipe builds Windows x64. Build each platform on its own host.")
    components = {}
    for name in ("engine", "pixi", "agent"):
        components[name] = component_manifest(name, allow_dirty=arguments.allow_dirty_components)
    binary = ROOT / "external/deepdeck-engine/target/release/mtg-engine-server.exe"
    if not binary.is_file():
        raise SystemExit("Build DeepDeckEngine's release executable before packaging.")
    if not engine_build_current(ROOT):
        raise SystemExit(
            "The Engine release binary is older than its source files. Rebuild it before packaging."
        )
    version = importlib.metadata.version("deepdeck-learner")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output = arguments.output_root.resolve() / stamp
    output.mkdir(parents=True, exist_ok=False)
    stage = arguments.work_root.resolve() / stamp
    stage.mkdir(parents=True, exist_ok=False)
    web = prepare_web(stage, skip_build=arguments.skip_web_build)
    web_index = web / "index.html"
    manifest = {
        "schemaVersion": "deepdeck-desktop/v1",
        "version": version,
        "platform": "windows-x64",
        "builtAt": datetime.now(timezone.utc).isoformat(),
        "channel": "development-preview",
        "components": components,
        "engineSha256": sha256(binary),
        "webIndexSha256": sha256(web_index),
        "learnerRevision": git("rev-parse", "HEAD"),
        "learnerHasUncommittedChanges": bool(git("status", "--porcelain")),
        "hasUncommittedComponents": any(c["dirty"] for c in components.values()),
    }
    manifest_path = stage / "deepdeck-runtime.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    environment = {
        **os.environ,
        "DEEPDECK_BUILD_MANIFEST": str(manifest_path),
        "DEEPDECK_BUILD_WEB_DIST": str(web),
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--distpath",
            str(output),
            "--workpath",
            str(stage / "work"),
            str(ROOT / "packaging/desktop/DeepDeck.spec"),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    folder = output / "DeepDeck"
    (folder / "START-HERE.txt").write_text(
        "DeepDeck - Windows x64 preview\n\n"
        "Extract the entire ZIP, then double-click DeepDeck.exe. Do not move the EXE alone.\n"
        "Python, Node, Git and Rust are not required to play.\n"
        "Microsoft Edge WebView2 Runtime is required for the native window.\n"
        "No LLM account is needed to play. Configure your own model only to use chat.\n\n"
        "Private decks, agents, keys, replays and logs are saved separately in:\n"
        "%LOCALAPPDATA%\\DeepDeck\\PlayerLab\\.deepdeck\\\n"
        "Replacing this application folder does not replace those files. Back up that data folder.\n"
        "Closing DeepDeck stops its active agents after confirmation; keep it open for League play.\n\n"
        "This development preview may include uncommitted fixes, identified in deepdeck-runtime.json.\n"
        "Dirty builds cannot be reproduced from the listed commits alone. Verify the archive checksum.\n"
        "This preview is unsigned and does not yet include an installer or automatic updates.\n"
        "Verify the publisher and checksum before running downloaded software.\n"
        "Developer source: https://github.com/dd-the-dd/DeepDeckLearner\n",
        encoding="utf-8",
    )
    shutil.copyfile(ROOT / "LICENSE", folder / "LICENSE.txt")
    archive = Path(
        shutil.make_archive(
            str(output / f"DeepDeck-{version}-windows-x64"), "zip", output, "DeepDeck"
        )
    )
    checksum = sha256(archive)
    archive.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
    print(
        json.dumps(
            {
                "folder": str(folder),
                "archive": str(archive),
                "bytes": archive.stat().st_size,
                "sha256": checksum,
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
