from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_cross_platform_workbench_script_exposes_setup_update_and_start() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "workbench.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )

    assert result.returncode == 0
    assert "setup" in result.stdout
    assert "update" in result.stdout
    assert "start" in result.stdout
    source = script.read_text("utf-8")
    assert "pwsh" not in source
    assert "shell=False" in source
