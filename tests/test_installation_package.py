from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def packaging_hook():
    pytest.importorskip("hatchling")
    path = Path(__file__).resolve().parents[1] / "packaging/web_build_hook.py"
    spec = importlib.util.spec_from_file_location("web_build_hook", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def player_build(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    source = root / "apps/learner-web/src"
    source.mkdir(parents=True)
    (source / "main.tsx").write_text("import App from './PlayerApp';", encoding="utf-8")
    (source / "PlayerApp.tsx").write_text("export default () => 'Play';", encoding="utf-8")
    frontend = tmp_path / "isolated-build"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text("Player build", encoding="utf-8")
    (frontend / "assets/player.js").write_text("current player", encoding="utf-8")
    (frontend / "assets/old-cockpit.js").write_text("retained for an old tab", encoding="utf-8")
    manifest = {
        "schemaVersion": "deepdeck-web/v1",
        "interface": "player",
        "sourceSha256": {
            f"src/{path.name}": hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source.iterdir()
        },
        "files": ["index.html", "assets/player.js"],
    }
    (frontend / "deepdeck-web.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, frontend


def test_packaging_requires_a_player_build(packaging_hook, tmp_path):
    with pytest.raises(ValueError, match="verified Player build is missing"):
        packaging_hook.player_assets(tmp_path, tmp_path / "old-build")


def test_packaging_rejects_stale_sources(packaging_hook, tmp_path):
    root, frontend = player_build(tmp_path)
    (root / "apps/learner-web/src/PlayerApp.tsx").write_text("updated player", encoding="utf-8")
    with pytest.raises(ValueError, match="stale: src/PlayerApp.tsx"):
        packaging_hook.player_assets(root, frontend)


def test_packaging_rejects_an_old_entry_point_even_with_matching_hashes(packaging_hook, tmp_path):
    root, frontend = player_build(tmp_path)
    entry = root / "apps/learner-web/src/main.tsx"
    entry.write_text("import App from './App';", encoding="utf-8")
    path = frontend / "deepdeck-web.json"
    manifest = json.loads(path.read_text("utf-8"))
    manifest["sourceSha256"]["src/main.tsx"] = hashlib.sha256(entry.read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="entry point does not open PlayerApp"):
        packaging_hook.player_assets(root, frontend)


@pytest.mark.parametrize("relative", ["../private.txt", "/private.txt", "C:/private.txt"])
def test_manifest_cannot_include_files_outside_the_build(packaging_hook, tmp_path, relative):
    root, frontend = player_build(tmp_path)
    path = frontend / "deepdeck-web.json"
    manifest = json.loads(path.read_text("utf-8"))
    manifest["files"].append(relative)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Player asset"):
        packaging_hook.player_assets(root, frontend)


def test_packaging_omits_retained_chunks_without_deleting_them(packaging_hook, tmp_path):
    root, frontend = player_build(tmp_path)
    assets = packaging_hook.player_assets(root, frontend)
    assert {path.relative_to(frontend).as_posix() for path in assets} == {
        "deepdeck-web.json",
        "index.html",
        "assets/player.js",
    }
    assert (frontend / "assets/old-cockpit.js").is_file()
