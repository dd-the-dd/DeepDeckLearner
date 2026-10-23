from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def recipe():
    path = Path(__file__).resolve().parents[1] / "scripts/build_desktop.py"
    spec = importlib.util.spec_from_file_location("desktop_recipe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("dirty", [False, True])
def test_preview_records_component_provenance_without_changing_checkout(monkeypatch, dirty):
    module = recipe()
    calls = []

    def git(*args, **kwargs):
        calls.append(args)
        return (
            " M src/session.rs"
            if args[0] == "status" and dirty
            else ""
            if args[0] == "status"
            else "main-revision"
        )

    monkeypatch.setattr(module, "git", git)
    value = module.component_manifest("engine", allow_dirty=True)
    assert value == {"revision": "main-revision", "branch": "main", "dirty": dirty}
    assert all(args[0] in {"status", "rev-parse"} for args in calls)


def test_dirty_components_still_require_explicit_preview_opt_in(monkeypatch):
    module = recipe()
    monkeypatch.setattr(module, "git", lambda *a, **kw: " M source")
    with pytest.raises(SystemExit, match="allow-dirty-components"):
        module.component_manifest("pixi")


def test_development_preview_does_not_bypass_main_dependency_rule(monkeypatch):
    module = recipe()

    def git(*args, **kwargs):
        if args[0] == "status":
            return " M source"
        return "feature-revision" if args[1] == "HEAD" else "main-revision"

    monkeypatch.setattr(module, "git", git)
    with pytest.raises(SystemExit, match="origin/main"):
        module.component_manifest("engine", allow_dirty=True)


def preflight_recipe(tmp_path, monkeypatch):
    from types import SimpleNamespace

    module = recipe()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(
        module,
        "component_manifest",
        lambda name, **kwargs: {"revision": name, "branch": "main", "dirty": False},
    )
    monkeypatch.setattr(module, "engine_build_current", lambda root: True)
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(module.shutil, "disk_usage", lambda path: SimpleNamespace(free=3 * 1024**3))
    binary = tmp_path / "external/deepdeck-engine/target/release/mtg-engine-server.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture binary")
    web = tmp_path / "apps/learner-web/dist/index.html"
    web.parent.mkdir(parents=True)
    web.write_text("fixture", encoding="utf-8")
    return module


def test_preflight_inspects_future_directories_without_creating_or_building(tmp_path, monkeypatch):
    module = preflight_recipe(tmp_path, monkeypatch)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **kw: pytest.fail("A check must not run a build.")
    )
    output = tmp_path / "not-created-output"
    work = tmp_path / "not-created-work"
    result = module.inspect_build(
        output_root=output, work_root=work, allow_dirty=False, skip_web_build=True
    )
    assert result["canStartBuild"] is True
    assert result["blockers"] == [] and result["engineCurrent"] is True
    assert not output.exists() and not work.exists()
    assert result["storage"][0]["existingAncestor"] == str(tmp_path)


def test_preflight_keeps_engine_freshness_and_component_guards(tmp_path, monkeypatch):
    module = preflight_recipe(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "engine_build_current", lambda root: False)

    def wrong_branch(name, **kwargs):
        raise SystemExit("not at origin/main")

    monkeypatch.setattr(module, "component_manifest", wrong_branch)
    result = module.inspect_build(
        output_root=tmp_path / "out",
        work_root=tmp_path / "work",
        allow_dirty=True,
        skip_web_build=True,
    )
    assert result["canStartBuild"] is False
    assert result["engineCurrent"] is False
    assert len(result["blockers"]) == 4
    assert any("freshness guards" in problem for problem in result["blockers"])


def test_low_space_is_explicitly_advisory_not_an_invented_artifact_size(tmp_path, monkeypatch):
    from types import SimpleNamespace

    module = preflight_recipe(tmp_path, monkeypatch)
    monkeypatch.setattr(module.shutil, "disk_usage", lambda path: SimpleNamespace(free=123))
    result = module.inspect_build(
        output_root=tmp_path / "out",
        work_root=tmp_path / "work",
        allow_dirty=True,
        skip_web_build=True,
    )
    assert result["canStartBuild"] is True
    assert len(result["warnings"]) == 2
    assert result["storage"][0]["freeBytes"] == 123
    assert "advisory" in result["warnings"][0]


def test_preflight_reports_missing_builder_and_web_without_hiding_other_failures(
    tmp_path, monkeypatch
):
    module = preflight_recipe(tmp_path, monkeypatch)
    (tmp_path / "apps/learner-web/dist/index.html").unlink()
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: None)
    result = module.inspect_build(
        output_root=tmp_path / "out",
        work_root=tmp_path / "work",
        allow_dirty=False,
        skip_web_build=True,
    )
    assert result["canStartBuild"] is False
    assert result["webBuildPresent"] is False
    assert len(result["blockers"]) == 2


def test_check_cli_reports_failure_before_staging_or_packaging(tmp_path, monkeypatch, capsys):
    import json

    module = preflight_recipe(tmp_path, monkeypatch)
    monkeypatch.setattr(module.sys, "argv", ["build_desktop.py", "--check"])
    monkeypatch.setattr(
        module,
        "inspect_build",
        lambda **kwargs: {"canStartBuild": False, "blockers": ["Engine needs rebuild"]},
    )
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **kw: pytest.fail("Preflight must not package.")
    )
    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 1
    assert json.loads(capsys.readouterr().out)["blockers"] == ["Engine needs rebuild"]
    assert not (tmp_path / "dist").exists()
    assert not (tmp_path / ".deepdeck").exists()


def test_builder_imports_checkout_instead_of_an_older_installed_copy(tmp_path):
    import json
    import os
    import shutil
    import subprocess
    import sys

    original = Path(__file__).resolve().parents[1] / "scripts" / "build_desktop.py"
    script = tmp_path / "checkout" / "scripts" / "build_desktop.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(original, script)
    local = script.parent.parent / "src" / "deepdeck_learner"
    installed = tmp_path / "old-installation" / "deepdeck_learner"
    for package, current in ((local, False), (installed, True)):
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "dependencies.py").write_text(
            f"def engine_build_current(root): return {current}\n", encoding="utf-8"
        )
    output = tmp_path / "unused-output"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--check",
            "--skip-web-build",
            "--output-root",
            str(output),
            "--work-root",
            str(tmp_path / "unused-work"),
        ],
        env={**os.environ, "PYTHONPATH": str(installed.parent)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    report = json.loads(result.stdout)
    assert Path(report["learnerSource"]) == local / "dependencies.py"
    assert report["canStartBuild"] is False
    assert not output.exists() and not (tmp_path / "unused-work").exists()


def test_desktop_web_build_is_isolated_from_running_app(tmp_path, monkeypatch):
    module = recipe()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.shutil, "which", lambda name: "npm.cmd")
    current = tmp_path / "apps/learner-web/dist"
    current.mkdir(parents=True)
    (current / "index.html").write_text("running app", encoding="utf-8")
    (current / "old-chunk.js").write_text("keep for existing tabs", encoding="utf-8")
    stage = tmp_path / "unique-build"
    stage.mkdir()
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        output = Path(command[-1])
        output.mkdir()
        (output / "index.html").write_text("new packaged app", encoding="utf-8")

    monkeypatch.setattr(module.subprocess, "run", run)
    web = module.prepare_web(stage, skip_build=False)
    assert web == stage / "web"
    assert calls == [
        (
            ["npm.cmd", "run", "build", "--", "--outDir", str(web)],
            {"cwd": tmp_path / "apps/learner-web", "check": True},
        )
    ]
    assert (current / "index.html").read_text("utf-8") == "running app"
    assert (current / "old-chunk.js").is_file()
    assert not (web / "old-chunk.js").exists()


def test_skip_desktop_web_build_reuses_explicit_existing_assets(tmp_path, monkeypatch):
    module = preflight_recipe(tmp_path, monkeypatch)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **kw: pytest.fail("No build requested.")
    )
    expected = tmp_path / "apps/learner-web/dist"
    assert module.prepare_web(tmp_path / "stage", skip_build=True) == expected


def test_desktop_web_build_does_not_package_missing_output(tmp_path, monkeypatch):
    module = recipe()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.shutil, "which", lambda name: "npm.cmd")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: None)
    with pytest.raises(SystemExit, match="frontend has not been built"):
        module.prepare_web(tmp_path / "stage", skip_build=False)
