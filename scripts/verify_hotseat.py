"""Real shared-screen tables in an isolated source profile; no League or LLM writes.

Uses an already healthy loopback Engine, creates only its own sessions, downloads
public decks, checks real Pixi handovers, then stops each table with a partial
recording. Does not claim a completed match, winner or full tournament audit.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import verify_player_app as browser
import websockets
from verify_desktop import wait_instance

ROOT = Path(__file__).resolve().parents[1]


async def check_browser(
    debug_url: str,
    app_url: str,
    output: Path,
    humans: int,
    competition: bool = False,
    expected_names: list[str] | None = None,
) -> None:
    async with websockets.connect(debug_url, max_size=20_000_000) as connection:
        serial = 0
        exceptions = []

        async def command(method, params=None):
            nonlocal serial
            serial += 1
            await connection.send(
                json.dumps({"id": serial, "method": method, "params": params or {}})
            )
            while True:
                event = json.loads(await connection.recv())
                if event.get("method") == "Runtime.exceptionThrown":
                    exceptions.append(event["params"])
                if event.get("id") == serial:
                    if "error" in event:
                        raise RuntimeError(event["error"])
                    return event.get("result", {})

        async def evaluate(code):
            result = await command(
                "Runtime.evaluate",
                {"expression": code, "returnByValue": True, "awaitPromise": True},
            )
            if result.get("exceptionDetails"):
                raise RuntimeError(result["exceptionDetails"])
            return result.get("result", {}).get("value")

        async def until(code):
            deadline = time.monotonic() + 40
            while not await evaluate(code):
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"Browser condition: {code}; "
                        f"{await evaluate('document.body.innerText.slice(-1800)')}"
                    )
                await asyncio.sleep(0.15)

        await command("Page.enable")
        await command("Runtime.enable")
        await command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        await command("Page.navigate", {"url": app_url})
        if competition:
            await until("Boolean(document.querySelector('.player-nav nav button'))")
            await evaluate(
                "[...document.querySelectorAll('.player-nav nav button')]"
                ".find(b=>b.textContent.includes('Competitions')).click()"
            )
            await until("Boolean(document.querySelector('.competition-history-card'))")
            await evaluate("document.querySelector('.competition-history-card').click()")
            await until(
                "[...document.querySelectorAll('button')]"
                ".some(b=>b.textContent==='Play current match')"
            )
            await evaluate(
                "[...document.querySelectorAll('button')]"
                ".find(b=>b.textContent==='Play current match').click()"
            )
        else:
            await until(
                "[...document.querySelectorAll('.active-strip button')]"
                ".some(b=>b.textContent==='Open table')"
            )
            await evaluate(
                "[...document.querySelectorAll('.active-strip button')]"
                ".find(b=>b.textContent==='Open table').click()"
            )
        ready = "document.querySelector('.local-seat-handover button')"
        for index in range(humans):
            await until(f"Boolean({ready})")
            assert not await evaluate("Boolean(document.querySelector('.local-pixi-table canvas'))")
            if expected_names:
                assert expected_names[index] in await evaluate(
                    "document.querySelector('.local-seat-handover h1').textContent"
                )
            if index == 0:
                for width, height in [(1440, 1000), (390, 844)]:
                    await command(
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": width,
                            "height": height,
                            "deviceScaleFactor": 1,
                            "mobile": width < 500,
                        },
                    )
                    assert await evaluate(
                        "document.querySelector('.local-seat-handover').scrollWidth<=innerWidth"
                    )
                    shot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"handover-{humans}-{width}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
                )
            await evaluate(f"{ready}.click()")
            await until("Boolean(document.querySelector('.local-pixi-table canvas'))")
            await until(
                "Boolean(document.querySelector('.local-pixi-vue-host')"
                "?._vnode?.component?.subTree?.props?.scene?.players)"
            )
            assert await evaluate("""(()=>{
                const scene=document.querySelector('.local-pixi-vue-host')
                    ._vnode.component.subTree.props.scene;
                const local=scene.players.filter(p=>p.local);
                return local.length===1 && local[0].hand.some(c=>!c.faceDown)
                    && scene.players.filter(p=>!p.local).every(p=>
                        p.hand.every(c=>c.faceDown && !c.imageUrl));
            })()"""), "Live Pixi scene exposed another seat’s hand."
            await until(
                "[...document.querySelectorAll('.local-pixi-table button')]"
                ".some(b=>b.textContent==='Keep opening hand')"
            )
            await evaluate(
                "[...document.querySelectorAll('.local-pixi-table button')]"
                ".find(b=>b.textContent==='Keep opening hand').click()"
            )
            await until("Boolean(document.querySelector('.local-seat-handover'))")
        await until(f"Boolean({ready})")
        await evaluate(f"{ready}.click()")
        await until("Boolean(document.querySelector('.local-hide-hand'))")
        await evaluate("document.querySelector('.local-hide-hand').click()")
        await until("Boolean(document.querySelector('.local-seat-handover'))")
        assert not await evaluate("Boolean(document.querySelector('.local-pixi-table canvas'))")
        await evaluate("document.querySelector('.local-table-exit').click()")
        await until("!document.querySelector('.local-pixi-table')")
        await evaluate(
            "[...document.querySelectorAll('.player-nav nav button')]"
            ".find(b=>b.textContent.includes('Replays')).click()"
        )
        await until("[...document.querySelectorAll('button')].some(b=>b.textContent==='Local')")
        await evaluate(
            "[...document.querySelectorAll('button')].find(b=>b.textContent==='Local').click()"
        )
        await until(
            "[...document.querySelectorAll('button')]"
            ".some(b=>b.textContent.includes('Watch match'))"
        )
        await evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b=>b.textContent.includes('Watch match')).click()"
        )
        await until("Boolean(document.querySelector('.replay-theater canvas'))")
        assert not exceptions, exceptions


def main() -> None:
    engine_url = "http://127.0.0.1:8787"
    httpx.get(engine_url + "/health").raise_for_status()
    parent = ROOT / ".deepdeck" / "validation"
    parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="hotseat-", dir=parent))
    print(f"Isolated shared-screen QA: {scratch}", flush=True)
    profile = scratch / "profile"
    shutil.copytree(ROOT / "apps/learner-web/dist", profile / "apps/learner-web/dist")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            (
                "DEEPDECK",
                "DDL_",
                "OPENAI_",
                "ANTHROPIC_",
                "GEMINI_",
                "GOOGLE_API_",
                "MISTRAL_",
                "DEEPSEEK_",
            )
        )
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "deepdeck_learner.desktop",
            "--headless",
            "--data-dir",
            str(profile),
            "--port",
            "0",
            "--engine-port",
            "8787",
        ],
        cwd=scratch,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    report = {"profile": str(profile), "cases": [], "paidCalls": False, "productionWrites": False}
    try:
        instance = wait_instance(process, profile)
        # First launch compiles real Oracle rules/enriches the public deck. This
        # is a bounded cold-start wait, not a retry of a possibly accepted POST.
        with httpx.Client(base_url=instance["url"], timeout=180) as client:
            client.headers["X-DeepDeck-Token"] = client.get("/api/v1/session").json()["token"]
            assert client.get("/api/v1/agent-studio/llm-settings").json()["configured"] is False
            for game_format, count in [("legacy", 2), ("commander", 4)]:
                available = client.get(
                    "/api/v1/catalog/decks", params={"format": game_format}
                ).json()["items"]
                deck = next(item for item in available if item.get("format") == game_format)
                downloaded = client.post(f"/api/v1/catalog/decks/{deck['id']}/download")
                downloaded.raise_for_status()
                response = client.post(
                    "/api/v1/jobs",
                    json={
                        "kind": "playtest.table",
                        "format": game_format,
                        "engine_url": engine_url,
                        "seed": 58422,
                        "starting_seat": 1,
                        "seats": [{"kind": "human", "deck_id": deck["id"]} for _ in range(count)],
                    },
                )
                response.raise_for_status()
                job = response.json()
                deadline = time.monotonic() + 60
                while not job.get("details", {}).get("sessionId"):
                    if job["status"] not in {"queued", "running"} or time.monotonic() > deadline:
                        raise RuntimeError(f"Owned hot-seat job did not open: {job}")
                    time.sleep(0.2)
                    job = client.get(f"/api/v1/jobs/{job['id']}").json()
                assert len(set(job["details"]["humanPlayerIds"])) == count

                async def verify(debug_url, human_count=count):
                    await check_browser(debug_url, instance["url"], scratch, human_count)

                browser.verify = verify
                browser.main()
                finished = client.get(f"/api/v1/jobs/{job['id']}").json()
                assert finished["status"] == "stopped", finished
                rows = client.get("/api/v1/models/local-tables/replays").json()["items"]
                row = rows[0]
                assert row["frameCount"] > count and row["gameStatus"] == "interrupted", row
                replay = client.get(f"/api/v1/models/local-tables/replays/{row['id']}").json()
                assert replay["metadata"]["outcome"]["winner"] is None
                assert len(replay["metadata"]["participants"]) == count
                assert not (profile / ".deepdeck/agents").exists()
                summary = client.get("/api/v1/statistics/local").json()["summary"]
                assert summary["matches"] == 0 and summary["seatResults"] == 0
                favorite = client.put(
                    f"/api/v1/models/local-tables/replays/{row['id']}/favorite",
                    json={"favorite": True},
                )
                favorite.raise_for_status()
                report["cases"].append(
                    {
                        "format": game_format,
                        "humans": count,
                        "deck": deck["name"],
                        "sessionId": job["details"]["sessionId"],
                        "replayId": row["id"],
                        "frames": row["frameCount"],
                        "status": row["gameStatus"],
                    }
                )
                print(
                    f"PASS: {game_format}, {count} human hands, "
                    "real Pixi handovers and partial recording.",
                    flush=True,
                )
    finally:
        if process.poll() is None:
            (profile / ".deepdeck/desktop-stop.request").touch()
            process.wait(timeout=30)
        report["success"] = len(report["cases"]) == 2 and process.returncode == 0
        (scratch / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert process.returncode == 0
    print(f"Evidence: {scratch / 'verification.json'}", flush=True)


if __name__ == "__main__":
    main()
