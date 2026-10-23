from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from .runtime import bundle_directory, bundled_engine, component_revision, frozen

DEPENDENCIES = {
    "engine": Path("external/deepdeck-engine"),
    "pixi": Path("external/deepdeck-pixi"),
}
TRACKED_BRANCHES = {dependency: "main" for dependency in DEPENDENCIES}
DEFAULT_ENGINE_URL = "http://127.0.0.1:8787"


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
    try:
        result = subprocess.run(
            [git, *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Revision diagnostics must not bring down /status on a busy disk.
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def pinned_revision(root: Path, dependency: str) -> str | None:
    if frozen():
        return component_revision(dependency)
    relative = DEPENDENCIES.get(dependency)
    if relative is None:
        return None
    output = git_output(["ls-tree", "HEAD", "--", relative.as_posix()], root.resolve())
    if not output:
        return None
    fields = output.split()
    return fields[2] if len(fields) >= 3 and fields[1] == "commit" else None


def tracked_branch(dependency: str) -> str | None:
    return TRACKED_BRANCHES.get(dependency)


def target_revision(root: Path, dependency: str) -> str | None:
    """Return the last fetched origin tip that this dependency follows."""
    if frozen():
        return component_revision(dependency)
    branch = tracked_branch(dependency)
    if not branch:
        return pinned_revision(root, dependency)
    source = dependency_path(root, dependency)
    if not source.is_dir():
        return None
    return git_output(["rev-parse", "--verify", f"refs/remotes/origin/{branch}"], source)


def current_revision(root: Path, dependency: str) -> str | None:
    if frozen():
        return component_revision(dependency)
    source = dependency_path(root, dependency)
    if not source.is_dir():
        return None
    return git_output(["rev-parse", "HEAD"], source)


def has_local_changes(root: Path, dependency: str) -> bool:
    if frozen():
        return False
    source = dependency_path(root, dependency)
    if not source.is_dir() or current_revision(root, dependency) is None:
        return False
    output = git_output(["status", "--porcelain"], source)
    return bool(output)


def engine_binary(root: Path) -> Path:
    bundled = bundled_engine()
    if bundled is not None:
        return bundled
    name = "mtg-engine-server.exe" if os.name == "nt" else "mtg-engine-server"
    return dependency_path(root, "engine") / "target" / "release" / name


def engine_build_environment() -> dict[str, str]:
    """Discover installed Windows build tools outside a Developer Command Prompt."""
    if os.name != "nt":
        return {}
    environment: dict[str, str] = {}
    program_files = Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
    libraries = [value for value in os.environ.get("LIB", "").split(os.pathsep) if value]
    includes = [value for value in os.environ.get("INCLUDE", "").split(os.pathsep) if value]
    binaries: list[str] = []
    sdk_root = program_files / "Windows Kits" / "10" / "Lib"
    sdk_versions = sorted(sdk_root.glob("*"), reverse=True)
    for version in sdk_versions:
        system = version / "um" / "x64"
        runtime = version / "ucrt" / "x64"
        if (system / "kernel32.lib").is_file() and (runtime / "ucrt.lib").is_file():
            libraries.extend((str(system), str(runtime)))
            sdk_include = sdk_root.parent / "Include" / version.name
            includes.extend(
                str(sdk_include / part)
                for part in ("ucrt", "shared", "um", "winrt")
                if (sdk_include / part).is_dir()
            )
            sdk_bin = sdk_root.parent / "bin" / version.name / "x64"
            if sdk_bin.is_dir():
                binaries.append(str(sdk_bin))
            break
    vswhere = program_files / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.is_file():
        result = subprocess.run(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            shell=False,
        )
        installation = result.stdout.strip()
        if result.returncode == 0 and installation:
            for version in sorted(
                (Path(installation) / "VC" / "Tools" / "MSVC").glob("*"), reverse=True
            ):
                library = version / "lib" / "x64"
                if (library / "libcmt.lib").is_file():
                    libraries.append(str(library))
                    includes.append(str(version / "include"))
                    compiler = version / "bin" / "Hostx64" / "x64"
                    if (compiler / "cl.exe").is_file():
                        binaries.append(str(compiler))
                    break
    if libraries:
        environment["LIB"] = os.pathsep.join(dict.fromkeys(libraries))
    if includes:
        environment["INCLUDE"] = os.pathsep.join(dict.fromkeys(includes))
    if binaries:
        environment["PATH"] = os.pathsep.join((*binaries, os.environ.get("PATH", "")))
    if not shutil.which("link"):
        rustc = shutil.which("rustc")
        if rustc:
            result = subprocess.run(
                [rustc, "--print", "sysroot"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
                shell=False,
            )
            if result.returncode == 0:
                linker = (
                    Path(result.stdout.strip())
                    / "lib"
                    / "rustlib"
                    / "x86_64-pc-windows-msvc"
                    / "bin"
                    / "rust-lld.exe"
                )
                if linker.is_file():
                    environment["RUSTFLAGS"] = (
                        f"{os.environ.get('RUSTFLAGS', '')} -C linker={linker}".strip()
                    )
    return environment


def engine_build_current(root: Path) -> bool:
    if frozen():
        return bundled_engine() is not None
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


def pixi_build_current(root: Path) -> bool:
    if frozen():
        bundle = bundle_directory()
        return bool(
            bundle
            and component_revision("pixi")
            and (bundle / "deepdeck_learner" / "web" / "index.html").is_file()
        )
    source = dependency_path(root, "pixi")
    revision = current_revision(root, "pixi")
    marker = root / ".deepdeck" / "dependencies" / "pixi-build-revision"
    built_revision = marker.read_text("utf-8").strip() if marker.is_file() else None
    return bool(
        revision and built_revision == revision and (source / "dist" / "index.js").is_file()
    )


def engine_healthy(url: str = DEFAULT_ENGINE_URL) -> bool:
    try:
        with urlopen(f"{url.rstrip('/')}/health", timeout=0.5) as response:  # noqa: S310
            return 200 <= int(response.status) < 300
    except (OSError, URLError, TimeoutError):
        return False


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
            "--remote",
            "--checkout",
            "--",
            relative.as_posix(),
        ],
    ]
    for command in commands:
        print(f"Running: {' '.join(command)}", flush=True)
        subprocess.run(command, cwd=root, check=True, shell=False)
    expected = target_revision(root, dependency)
    current = current_revision(root, dependency)
    if not expected or current != expected:
        branch = tracked_branch(dependency) or "configured revision"
        raise DependencyTaskError(f"DeepDeck{dependency.title()} did not reach origin/{branch}.")


def prepare_pixi(root: Path) -> None:
    source = dependency_path(root, "pixi")
    package = source / "package.json"
    if not package.is_file():
        raise DependencyTaskError("DeepDeckPixi is missing. Synchronize it first.")
    current = current_revision(root, "pixi")
    target = target_revision(root, "pixi")
    if not current or current != target:
        raise DependencyTaskError("DeepDeckPixi is not at origin/main. Sync it first.")
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


def start_engine(root: Path) -> None:
    source = dependency_path(root, "engine")
    manifest = source / "Cargo.toml"
    if not manifest.is_file():
        raise DependencyTaskError("DeepDeckEngine is missing after synchronization.")
    if current_revision(root, "engine") != target_revision(root, "engine"):
        raise DependencyTaskError("DeepDeckEngine is not at origin/main.")
    if engine_build_current(root):
        command = [str(engine_binary(root))]
    else:
        cargo = shutil.which("cargo")
        if not cargo:
            raise DependencyTaskError(
                "Rust and Cargo are required for the first DeepDeckEngine build."
            )
        command = [
            cargo,
            "run",
            "--release",
            "--manifest-path",
            str(manifest),
            "--locked",
            "--bin",
            "mtg-engine-server",
        ]
    print(f"Running: {' '.join(command)}", flush=True)
    subprocess.run(
        command, cwd=root, env={**os.environ, **engine_build_environment()}, check=True, shell=False
    )


def bootstrap_local_stack(root: Path) -> None:
    """Prepare Engine and Pixi from origin/main and keep Engine running."""
    for dependency in DEPENDENCIES:
        if has_local_changes(root, dependency):
            raise DependencyTaskError(
                f"{dependency} contains local changes. Commit or move them before setup."
            )
    for dependency in DEPENDENCIES:
        source = dependency_path(root, dependency)
        expected_file = source / ("Cargo.toml" if dependency == "engine" else "package.json")
        state = "Updating" if expected_file.is_file() else "Installing"
        print(f"{state} DeepDeck{dependency.title()} from origin/main...", flush=True)
        sync_dependency(root, dependency)
    if not pixi_build_current(root):
        print("Preparing the DeepDeckPixi renderer...", flush=True)
        prepare_pixi(root)
    else:
        print("DeepDeckPixi is already built.", flush=True)
    if engine_healthy():
        print("DeepDeckEngine is already running.", flush=True)
        return
    print("Starting DeepDeckEngine...", flush=True)
    start_engine(root)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run an allowlisted dependency task.")
    result.add_argument("action", choices=("sync", "prepare-pixi", "bootstrap"))
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
    elif arguments.action == "prepare-pixi":
        prepare_pixi(root)
    else:
        bootstrap_local_stack(root)


if __name__ == "__main__":
    try:
        main()
    except (DependencyTaskError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
