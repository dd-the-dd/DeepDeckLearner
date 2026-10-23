"""Native desktop shell. Installation assets never contain the user's data."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import logging
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, BinaryIO
from urllib.error import URLError
from urllib.request import urlopen

from .runtime import bundled_engine, desktop_data_root, frozen, hidden_process_options

# Responsive navigation contains a full label and a CSS-hidden compact label.
# textContent on their parent concatenates both, even when the UI is correct.
NATIVE_TAB_LABELS_JS = (
    "Array.from(document.querySelectorAll('.player-nav nav button'), "
    "b => b.querySelector('.nav-full-label')?.textContent "
    "?? b.querySelector('strong')?.textContent)"
)


class AlreadyRunningError(RuntimeError):
    pass


class ProfileLock:
    def __init__(self, root: Path) -> None:
        self.path = root / ".deepdeck" / "desktop.lock"
        self.stream: BinaryIO | None = None

    def __enter__(self) -> ProfileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                locking = importlib.import_module("fcntl")
                locking.flock(stream.fileno(), locking.LOCK_EX | locking.LOCK_NB)
        except OSError as error:
            stream.close()
            raise AlreadyRunningError("DeepDeck is already open for this data folder.") from error
        self.stream = stream
        return self

    def __exit__(self, *_args: Any) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None


def _ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=0.5) as response:
            return bool(response.status == 200)
    except (OSError, URLError, TimeoutError):
        return False


def _listener(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        # Do not attach to an unrelated process, including a developer's app.
        if port == 0:
            sock.close()
            raise
        sock.bind(("127.0.0.1", 0))
    return sock


def _write_instance(path: Path, value: dict[str, Any]) -> None:
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def _message(title: str, text: str) -> None:
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    else:
        print(f"{title}: {text}", file=sys.stderr)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Open the DeepDeck desktop player.")
    result.add_argument("--data-dir", type=Path, default=None)
    result.add_argument("--port", type=int, default=8765)
    result.add_argument("--engine-port", type=int, default=8787)
    result.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--native-smoke-test", action="store_true", help=argparse.SUPPRESS)
    return result


def launch(arguments: argparse.Namespace) -> None:
    root = (arguments.data_dir or desktop_data_root()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["DEEPDECK_LEARNER_ROOT"] = str(root)
    os.environ["DEEPDECK_LOCAL_ENGINE_URL"] = f"http://127.0.0.1:{arguments.engine_port}"
    os.chdir(root)
    state = root / ".deepdeck"
    state.mkdir(exist_ok=True)
    with ProfileLock(root):
        logs = state / "logs"
        logs.mkdir(exist_ok=True)
        with (logs / "desktop.log").open("a", encoding="utf-8", buffering=1) as output:
            old_stdout, old_stderr = sys.stdout, sys.stderr
            if sys.stdout is None:
                sys.stdout = output
            if sys.stderr is None:
                sys.stderr = output
            try:
                _run(arguments, root, logs)
            except Exception:
                traceback.print_exc(file=output)
                raise
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr


def _run(arguments: argparse.Namespace, root: Path, logs: Path) -> None:
    from .runtime import configure_local_http_loop

    configure_local_http_loop()
    import uvicorn

    from .app import create_app

    engine_url = f"http://127.0.0.1:{arguments.engine_port}"
    engine: subprocess.Popen[bytes] | None = None
    engine_log = (logs / "engine.log").open("ab")
    sock = _listener(arguments.port)
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    app = create_app(root)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    instance_path = root / ".deepdeck" / "desktop-instance.json"
    stop_path = root / ".deepdeck" / "desktop-stop.request"
    with contextlib.suppress(FileNotFoundError):
        stop_path.unlink()
    try:
        if not _ready(engine_url + "/health"):
            binary = bundled_engine()
            if binary is None:
                raise RuntimeError(
                    "The bundled game engine is unavailable. Extract the complete DeepDeck folder. For source development, start the game engine first."
                )
            environment = dict(os.environ)
            environment.update(
                {"MTG_ENGINE_ADDR": f"127.0.0.1:{arguments.engine_port}", "MTG_UI_ADDR": ""}
            )
            engine = subprocess.Popen(
                [str(binary)],
                cwd=root,
                env=environment,
                stdout=engine_log,
                stderr=subprocess.STDOUT,
                **hidden_process_options(),
            )
            deadline = time.monotonic() + 30
            while not _ready(engine_url + "/health"):
                if engine.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(
                        "The game engine could not start. See the local engine.log file."
                    )
                time.sleep(0.1)
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started or not _ready(url + "/api/v1/health"):
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("The local player could not start. See desktop.log.")
            time.sleep(0.1)
        _write_instance(
            instance_path,
            {
                "schemaVersion": "deepdeck-desktop-instance/v1",
                "pid": os.getpid(),
                "url": url,
                "engineUrl": engine_url,
                "ready": True,
                "frozen": frozen(),
            },
        )
        if arguments.headless:
            while not stop_path.is_file() and thread.is_alive():
                time.sleep(0.1)
        else:
            _window(app, url, root, arguments.native_smoke_test)
    finally:
        # Only jobs in this profile and the exact engine process we started.
        # Never stop a shared Engine that was already serving before launch.
        for job in app.state.manager.list_jobs():
            if job.get("status") in {"queued", "running"}:
                with contextlib.suppress(Exception):
                    app.state.manager.stop(job["id"])
        server.should_exit = True
        if thread.is_alive():
            thread.join(timeout=10)
        sock.close()
        if engine is not None and engine.poll() is None:
            engine.terminate()
            try:
                engine.wait(timeout=5)
            except subprocess.TimeoutExpired:
                engine.kill()
                engine.wait(timeout=5)
        engine_log.close()
        with contextlib.suppress(FileNotFoundError):
            instance_path.unlink()


def _allow_window_close(window: Any, manager: Any, smoke: bool) -> bool:
    if smoke:
        return True
    try:
        # Reuse the editor's browser guard, rather than losing a draft when the
        # native window closes without a normal browser navigation.
        dirty = bool(
            window.evaluate_js(
                "(() => { const event = new Event('beforeunload', {cancelable:true}); window.dispatchEvent(event); return event.defaultPrevented; })()"
            )
        )
    except Exception:
        dirty = False  # The window can still be closed if its page never loaded.
    active = any(job.get("status") in {"queued", "running"} for job in manager.list_jobs())
    if not dirty and not active:
        return True
    message = "Des modifications ne sont pas enregistrées. " if dirty else ""
    if active:
        message += "Des agents ou des parties sont encore actifs. Fermer l’application les arrêtera et interrompra la collecte des replays. "
    return bool(window.create_confirmation_dialog("Fermer DeepDeck ?", message + "Continuer ?"))


def _window(app: Any, url: str, root: Path, smoke: bool) -> None:
    import webview  # type: ignore[import-not-found]

    window = webview.create_window(
        "DeepDeck · Player Lab",
        url,
        width=1440,
        height=960,
        min_size=(900, 650),
        background_color="#f5f0e8",
        hidden=smoke,
    )

    def closing() -> bool:
        return _allow_window_close(window, app.state.manager, smoke)

    window.events.closing += closing

    def native_check() -> None:
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if window.evaluate_js("Boolean(document.querySelector('.player-nav nav button'))"):
                    _write_instance(
                        root / ".deepdeck" / "native-check.json",
                        {
                            "rendered": True,
                            "title": window.evaluate_js("document.title"),
                            "tabs": window.evaluate_js(
                                "document.querySelectorAll('.player-nav nav button').length"
                            ),
                            "tabLabels": window.evaluate_js(NATIVE_TAB_LABELS_JS),
                        },
                    )
                    return
                time.sleep(0.1)
            raise RuntimeError("The native webview did not render the player interface.")
        except Exception as error:
            _write_instance(
                root / ".deepdeck" / "native-check.json", {"rendered": False, "error": str(error)}
            )
        finally:
            window.destroy()

    webview.start(
        native_check if smoke else None,
        gui="edgechromium" if os.name == "nt" else None,
        private_mode=False,
        storage_path=str(root / ".deepdeck" / "webview"),
        debug=False,
    )


def main() -> None:
    multiprocessing.freeze_support()
    arguments = parser().parse_args()
    if not 0 <= arguments.port <= 65535 or not 1024 <= arguments.engine_port <= 65535:
        raise SystemExit("Invalid local port.")
    try:
        launch(arguments)
    except Exception as error:
        logging.exception("DeepDeck desktop startup failed")
        if not arguments.headless and not arguments.native_smoke_test:
            _message(
                "DeepDeck ne peut pas démarrer",
                f"{error}\n\nVos decks et agents n’ont pas été supprimés. Les détails sont dans le dossier .deepdeck/logs de vos données.",
            )
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
