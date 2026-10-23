"""Build on Windows in the isolated desktop environment, not the training venv."""

import importlib.metadata
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

repo = Path(SPECPATH).resolve().parents[1]
web_dist = Path(os.environ.get("DEEPDECK_BUILD_WEB_DIST", str(repo / "apps/learner-web/dist")))
metadata_files = []
for distribution in importlib.metadata.distributions():
    name = distribution.metadata.get("Name")
    if name and name not in {"pip", "setuptools", "pyinstaller", "pyinstaller-hooks-contrib"}:
        metadata_files += copy_metadata(name)

common = dict(
    pathex=[str(repo / "src")],
    excludes=["torch", "numpy", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter", "pytest"],
    noarchive=False,
)
gui = Analysis(
    [str(repo / "packaging" / "desktop" / "main.py")],
    binaries=[(str(repo / "external" / "deepdeck-engine" / "target" / "release" / "mtg-engine-server.exe"), "engine")],
    datas=[(str(web_dist), "deepdeck_learner/web"),
           (os.environ["DEEPDECK_BUILD_MANIFEST"], "."), *metadata_files,
           *collect_data_files("agents"), *collect_data_files("webview")],
    hiddenimports=["deepdeck_learner.app", "webview.platforms.edgechromium", "webview.platforms.winforms",
                   "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets.websockets_impl", "uvicorn.lifespan.on"],
    **common,
)
gui_pyz = PYZ(gui.pure)
app = EXE(gui_pyz, gui.scripts, [], exclude_binaries=True, name="DeepDeck", console=False, debug=False, upx=False)

worker = Analysis(
    [str(repo / "packaging" / "desktop" / "worker.py")],
    datas=[], binaries=[],
    hiddenimports=["deepdeck_examples.run", "deepdeck_examples.local_batch"],
    **common,
)
worker_pyz = PYZ(worker.pure)
helper = EXE(worker_pyz, worker.scripts, [], exclude_binaries=True, name="DeepDeckWorker", console=True, debug=False, upx=False)
COLLECT(app, helper, gui.binaries, gui.datas, worker.binaries, worker.datas, strip=False, upx=False, name="DeepDeck")
