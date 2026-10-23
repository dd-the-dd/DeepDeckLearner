"""Run inside an isolated wheel installation, with no neural training extra."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import deepdeck_learner
from deepdeck_examples import local_batch, run
from deepdeck_learner import cli, desktop, desktop_worker, runtime, status
from deepdeck_learner.app import create_app


def main() -> None:
    assert importlib.util.find_spec("torch") is None, "Use a clean environment without PyTorch."
    assert callable(cli.main) and callable(desktop.main) and callable(desktop_worker.main)
    assert callable(run.main) and callable(local_batch.main)
    assert status.project_root() == runtime.desktop_data_root()
    web = Path(deepdeck_learner.__file__).parent / "web"
    manifest = json.loads((web / "deepdeck-web.json").read_text("utf-8"))
    assert manifest["interface"] == "player"
    assert all((web / name).is_file() for name in manifest["files"])
    with (
        tempfile.TemporaryDirectory(prefix="deepdeck-player-smoke-") as temporary,
        TestClient(create_app(Path(temporary))) as client,
    ):
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/agent-studio/heuristics").status_code == 200
        assert client.get("/api/v1/training/curriculum").status_code == 501
        response = client.get("/")
        assert response.status_code == 200
        assert response.content == (web / "index.html").read_bytes()
        for name in manifest["files"]:
            if name.startswith("assets/"):
                assert client.get("/" + name).status_code == 200, name
    print("Installed Player wheel, assets, controller and agent workers work without PyTorch.")


if __name__ == "__main__":
    main()
