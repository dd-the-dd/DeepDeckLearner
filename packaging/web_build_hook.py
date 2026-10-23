"""Ship the verified Player build, without retained chunks from older interfaces."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def player_assets(root: Path, frontend: Path) -> list[Path]:
    manifest_path = frontend / "deepdeck-web.json"
    hint = (
        "Build the current Player frontend with npm run build in apps/learner-web "
        "before packaging. DEEPDECK_BUILD_WEB_DIST may point to an isolated build."
    )
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"The verified Player build is missing. {hint}") from error
    if manifest.get("schemaVersion") != "deepdeck-web/v1" or manifest.get("interface") != "player":
        raise ValueError(f"This frontend is not the Player application. {hint}")
    sources = manifest.get("sourceSha256", {})
    required = {"src/main.tsx", "src/PlayerApp.tsx"}
    if not isinstance(sources, dict) or not required.issubset(sources):
        raise ValueError(f"The Player build has no source provenance. {hint}")
    for relative, expected in sources.items():
        source = root / "apps/learner-web" / relative
        if not _safe_relative(relative) or not source.is_file():
            raise ValueError(f"Missing Player source: {relative}. {hint}")
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError(f"The Player build is stale: {relative}. {hint}")
    entry = (root / "apps/learner-web/src/main.tsx").read_text("utf-8")
    if not re.search(r"\bfrom\s+['\"]\./PlayerApp['\"]", entry):
        raise ValueError(f"The frontend entry point does not open PlayerApp. {hint}")
    files = manifest.get("files")
    if not isinstance(files, list) or "index.html" not in files:
        raise ValueError(f"The Player build has no index.html. {hint}")
    assets = [manifest_path]
    for relative in files:
        if not _safe_relative(relative) or not (frontend / relative).is_file():
            raise ValueError(f"Missing or invalid Player asset: {relative}. {hint}")
        assets.append(frontend / relative)
    return assets


def _safe_relative(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not PurePosixPath(value).is_absolute()
        and ".." not in PurePosixPath(value).parts
        and "\\" not in value
        and ":" not in value
    )


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root)
        frontend = Path(os.environ.get("DEEPDECK_BUILD_WEB_DIST", root / "apps/learner-web/dist"))
        destination = (
            "deepdeck_learner/web" if self.target_name == "wheel" else "apps/learner-web/dist"
        )
        for asset in player_assets(root, frontend):
            build_data.setdefault("force_include", {})[str(asset)] = (
                f"{destination}/{asset.relative_to(frontend).as_posix()}"
            )
