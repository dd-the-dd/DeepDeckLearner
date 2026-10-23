"""Inspect an owned successful competition or mixed-Commander QA profile, without games."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import verify_player_app as browser
import websockets
from verify_desktop import wait_instance

ROOT = Path(__file__).resolve().parents[1]


async def verify(
    debug_url: str,
    url: str,
    output: Path,
    *,
    mixed: bool = False,
    grouped_matches: int = 0,
    failed_attempt: bool = False,
):
    async with websockets.connect(debug_url, max_size=20_000_000) as connection:
        serial = 0
        errors = []

        async def command(method, params=None):
            nonlocal serial
            serial += 1
            await connection.send(
                json.dumps({"id": serial, "method": method, "params": params or {}})
            )
            while True:
                packet = json.loads(await connection.recv())
                if packet.get("method") == "Runtime.exceptionThrown":
                    errors.append(packet["params"])
                if packet.get("id") == serial:
                    if packet.get("error"):
                        raise RuntimeError(packet["error"])
                    return packet.get("result", {})

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
                    raise TimeoutError(f"History UI condition: {code}")
                await asyncio.sleep(0.15)

        async def history(*, matrix=True):
            await evaluate(
                "[...document.querySelectorAll('.player-nav nav button')]"
                ".find(b=>b.textContent.includes('Competitions')).click()"
            )
            await until("Boolean(document.querySelector('.competition-history-card'))")
            await evaluate("document.querySelector('.competition-history-card').click()")
            await until(
                "Boolean(document.querySelector('.competition-matrix'))"
                if matrix
                else "Boolean(document.querySelector('.competition-summary'))"
            )

        await command("Page.enable")
        await command("Runtime.enable")
        await command(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """(()=>{
            const original=window.fetch.bind(window);window.__historyWrites=[];
            window.fetch=(input,init)=>{
                const method=(init?.method||input.method||'GET').toUpperCase();
                const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
                if(!['GET','HEAD'].includes(method)){
                    window.__historyWrites.push({method,path});
                    if(!/^\/api\/v1\/models\/(?:local-tables|programmatic-[a-z0-9]+)\/replays\/[^/]+\/leases(?:\/[^/]+)?$/.test(path))
                        throw new Error('Unexpected mutation blocked by read-only history QA.');
                }
                return original(input,init);
            };
        })()"""
            },
        )
        await command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        await command("Page.navigate", {"url": url})
        await until("Boolean(document.querySelector('.player-nav nav button'))")
        if grouped_matches:
            await evaluate(
                "[...document.querySelectorAll('.player-nav nav button')]"
                ".find(b=>b.textContent.includes('Competitions')).click()"
            )
            await until("Boolean(document.querySelector('.competition-history-card'))")
            assert await evaluate(
                f"document.querySelector('.competition-history-card').innerText"
                f".includes('{grouped_matches} matches played')"
            )
            await evaluate("document.querySelector('.competition-history-card').click()")
            await until("Boolean(document.querySelector('.competition-champion'))")
            assert await evaluate(
                "document.querySelector('.competition-summary').innerText.includes('completed')"
                " && document.querySelector('.competition-summary')"
                ".innerText.includes('first to 2')"
            )
            for heading, rows in [
                ("Group 1 standings", 4),
                ("Group 2 standings", 4),
                ("Qualification standings · playoff seeds", 4),
                ("Entrant statistics", 8),
            ]:
                assert await evaluate(
                    "[...document.querySelectorAll('.setup-card')]"
                    f".find(s=>s.querySelector('h3')?.textContent==={json.dumps(heading)})"
                    f"?.querySelectorAll('tbody tr').length==={rows}"
                )
            assert await evaluate("document.querySelectorAll('.competition-match.done').length===3")
            if failed_attempt:
                await until(
                    "[...document.querySelectorAll('summary')]"
                    ".some(s=>s.textContent==='Interrupted attempts & recordings')"
                )
                await evaluate(
                    "[...document.querySelectorAll('summary')]"
                    ".find(s=>s.textContent==='Interrupted attempts & recordings').click()"
                )
                await evaluate(
                    "[...document.querySelectorAll('button')]"
                    ".find(b=>b.textContent==='Replay interrupted attempt 1').click()"
                )
                await until("Boolean(document.querySelector('.replay-theater canvas'))")
                assert await evaluate(
                    "document.querySelector('.replay-partial-notice')?.innerText.includes('error')"
                )
                await evaluate(
                    "document.querySelector('button[aria-label=\"Last frame\"]').click()"
                )
                await until("document.querySelector('button[aria-label=\"Last frame\"]').disabled")
                shot = await command("Page.captureScreenshot", {"format": "png"})
                (output / "recovered-error-last-frame.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
                await evaluate(
                    "[...document.querySelectorAll('.replay-theater button')]"
                    ".find(b=>b.textContent.includes('Back to replays')).click()"
                )
                await history(matrix=False)
            await evaluate(
                "[...document.querySelectorAll('summary')]"
                ".find(s=>s.textContent==='Series matches & replays').click()"
            )
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
                await evaluate(
                    "document.querySelector('.competition-stage:last-child').scrollIntoView()"
                )
                assert await evaluate("document.documentElement.scrollWidth<=innerWidth+1")
                shot = await command("Page.captureScreenshot", {"format": "png"})
                (output / f"group-playoffs-{width}.png").write_bytes(base64.b64decode(shot["data"]))
            await evaluate(
                "[...document.querySelectorAll('button')]"
                ".find(b=>b.textContent==='Replay match 1').click()"
            )
            await until("Boolean(document.querySelector('.replay-theater canvas'))")
            await evaluate("document.querySelector('button[aria-label=\"Last frame\"]').click()")
            await until("document.querySelector('button[aria-label=\"Last frame\"]').disabled")
            assert not await evaluate("Boolean(document.querySelector('.replay-partial-notice'))")
            assert not errors, errors
            return
        if mixed:
            await evaluate(
                "[...document.querySelectorAll('.player-nav nav button')]"
                ".find(b=>b.textContent.includes('Replays')).click()"
            )
            await until("[...document.querySelectorAll('button')].some(b=>b.textContent==='Local')")
            await evaluate(
                "[...document.querySelectorAll('button')].find(b=>b.textContent==='Local').click()"
            )
            completed_card = (
                ".replay-match-card[data-status=complete],"
                ".replay-match-card[data-status=completed],"
                ".replay-match-card[data-status=gameOver]"
            )
            await until(f"Boolean(document.querySelector({json.dumps(completed_card)}))")
            await evaluate(
                f"document.querySelector({json.dumps(completed_card)})"
                ".querySelector('.replay-watch').click()"
            )
            await until("Boolean(document.querySelector('.replay-theater canvas'))")
            scene = (
                "document.querySelector('.local-pixi-vue-host')"
                "?._vnode?.component?.subTree?.props?.scene"
            )
            await until(f"{scene}?.players?.length===4")
            select = "document.querySelector('select[aria-label=\"Spectator perspective\"]')"
            assert await evaluate(f"{select}.options.length===5")
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
                await evaluate(
                    "document.querySelector('button[aria-label=\"First frame\"]').click()"
                )
                for perspective in [
                    "global",
                    "local-human",
                    "local-human-2",
                    "batch-seat-3",
                    "batch-seat-4",
                ]:
                    await evaluate(f"""(()=>{{
                        const element={select};
                        Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')
                            .set.call(element,{json.dumps(perspective)});
                        element.dispatchEvent(new Event('change',{{bubbles:true}}));
                    }})()""")
                    await until(f"{select}.value==={json.dumps(perspective)}")
                    if perspective == "global":
                        await until(
                            f"{scene}.players.every(p=>p.hand.length>0 "
                            "&& p.hand.every(c=>!c.faceDown))"
                        )
                    else:
                        await until(
                            f"{scene}.players.find(p=>p.local)?.id==={json.dumps(perspective)}"
                        )
                        assert await evaluate(
                            f"{scene}.players.find(p=>p.local).hand.every(c=>!c.faceDown)"
                        )
                        assert await evaluate(
                            f"{scene}.players.filter(p=>!p.local)"
                            ".every(p=>p.hand.every(c=>c.faceDown && !c.imageUrl))"
                        )
                    assert await evaluate(
                        f"{scene}.players.every(p=>p.zones.filter(z=>z.id==='library').every(z=>z.cards.every(c=>c.faceDown)))"
                    )
                await evaluate(
                    "document.querySelector('button[aria-label=\"Last frame\"]').click()"
                )
                await until("document.querySelector('button[aria-label=\"Last frame\"]').disabled")
                assert await evaluate(
                    "document.querySelector('.pixi-replay-caption').innerText.includes('32')"
                )
                assert not await evaluate(
                    "Boolean(document.querySelector('.replay-partial-notice'))"
                )
                assert await evaluate("document.documentElement.scrollWidth<=innerWidth+1")
                assert await evaluate("""(()=>{
                    const header=document.querySelector('.replay-theater-header');
                    const title=document.querySelector('#replay-theater-title');
                    const box=title.getBoundingClientRect();
                    return getComputedStyle(header).flexDirection==='row'
                        && box.left>=0 && box.right<=innerWidth+1
                        && header.getBoundingClientRect().height<130;
                })()"""), "Replay header is clipped or consumes too much board space."
                shot = await command("Page.captureScreenshot", {"format": "png"})
                (output / f"mixed-replay-last-{width}.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
            assert not errors, errors
            return
        await history()
        assert await evaluate(
            "document.querySelector('.competition-summary').innerText.includes('completed')"
        )
        assert await evaluate(
            "document.querySelector('.competition-matrix').innerText"
            ".includes('QA Ada') && document.querySelector('.competition-matrix')"
            ".innerText.includes('QA Ben')"
        )
        assert await evaluate(
            "document.querySelectorAll('.competition-matrix tbody tr').length===2"
        )
        for width, height in [(1440, 1000), (390, 844)]:
            await command(
                "Emulation.setDeviceMetricsOverride",
                {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 500},
            )
            assert await evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            shot = await command("Page.captureScreenshot", {"format": "png"})
            (output / f"history-{width}.png").write_bytes(base64.b64decode(shot["data"]))
        await evaluate(
            "[...document.querySelectorAll('summary')]"
            ".find(s=>s.textContent==='Interrupted attempts & recordings').click()"
        )
        await evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b=>b.textContent==='Replay interrupted attempt 1').click()"
        )
        await until("Boolean(document.querySelector('.replay-theater canvas'))")
        assert await evaluate(
            "document.querySelector('.replay-partial-notice')?.innerText.includes('interrupted')"
        )
        await evaluate(
            "[...document.querySelectorAll('.replay-theater button')]"
            ".find(b=>b.textContent.includes('Back to replays')).click()"
        )
        await history()
        await evaluate("document.querySelector('.competition-matrix button').click()")
        await until("Boolean(document.querySelector('.replay-theater canvas'))")
        await evaluate("document.querySelector('button[aria-label=\"Last frame\"]').click()")
        await until("document.querySelector('button[aria-label=\"Last frame\"]').disabled")
        assert await evaluate("!document.querySelector('.replay-partial-notice')")
        shot = await command("Page.captureScreenshot", {"format": "png"})
        (output / "complete-match-last-frame.png").write_bytes(base64.b64decode(shot["data"]))
        assert not errors, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mixed-commander", action="store_true")
    mode.add_argument("--grouped-commander", action="store_true")
    parser.add_argument("--failed-attempt", action="store_true")
    args = parser.parse_args()
    if args.failed_attempt and not args.grouped_commander:
        raise ValueError("The recovered-failure check requires grouped Commander mode.")
    report_path = args.report.resolve()
    validation = (ROOT / ".deepdeck/validation").resolve()
    if not report_path.is_relative_to(validation) or not report_path.parent.name.startswith(
        "mixed-commander-"
        if args.mixed_commander
        else "grouped-commander-"
        if args.grouped_commander
        else "human-competition-"
    ):
        raise ValueError("Inspect only an owned QA report of the selected kind.")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    profile = Path(report["profile"]).resolve()
    if profile != report_path.parent / "profile" or not report["success"]:
        raise ValueError("A successful owned QA profile is required.")
    if (profile / ".deepdeck/secrets.json").exists():
        raise ValueError("The history inspection does not load account credentials.")
    # Inspect the current built UI over the retained immutable match, rather
    # than accidentally validating an older copied frontend snapshot.
    shutil.copytree(
        ROOT / "apps/learner-web/dist", profile / "apps/learner-web/dist", dirs_exist_ok=True
    )
    environment = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
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
        cwd=report_path.parent,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    inspection_path = report_path.parent / (
        "replay-view-verification.json"
        if args.mixed_commander
        else "group-history-verification.json"
        if args.grouped_commander
        else "history-verification.json"
    )
    inspection = {"success": False, "readOnlyExceptReplayLeases": True}
    inspection_path.write_text(json.dumps(inspection), encoding="utf-8")
    try:
        instance = wait_instance(process, profile)
        with httpx.Client(base_url=instance["url"], timeout=30, trust_env=False) as client:
            stored_event = None
            if not args.mixed_commander:
                with sqlite3.connect(
                    (profile / ".deepdeck/learner.db").as_uri() + "?mode=ro", uri=True
                ) as db:
                    stored_event = db.execute(
                        "SELECT payload FROM local_competitions WHERE id=?", (report["eventId"],)
                    ).fetchone()[0]
            if not args.mixed_commander:
                event = client.get(f"/api/v1/local-competitions/{report['eventId']}").json()
                assert event["status"] == "completed"
            assert not any(
                job["status"] in {"queued", "running"} for job in client.get("/api/v1/jobs").json()
            )
            stats = client.get("/api/v1/statistics/local").json()
            people = {p["id"]: p for p in stats["players"]}
            for entrant in (
                event["entrants"] if not (args.mixed_commander or args.grouped_commander) else []
            ):
                standing = next(row for row in event["standings"] if row["id"] == entrant["id"])
                actual = people[entrant["player_id"]]
                assert actual["wins"] == standing["wins"] and actual["losses"] == standing["losses"]

            async def check(debug_url):
                await verify(
                    debug_url,
                    instance["url"],
                    report_path.parent,
                    mixed=args.mixed_commander,
                    grouped_matches=report["statistics"]["matches"]
                    if args.grouped_commander
                    else 0,
                    failed_attempt=args.failed_attempt,
                )

            browser.verify = check
            browser.main()
            assert client.get("/api/v1/statistics/local").json()["summary"] == stats["summary"]
            if stored_event is not None:
                with sqlite3.connect(
                    (profile / ".deepdeck/learner.db").as_uri() + "?mode=ro", uri=True
                ) as db:
                    assert (
                        db.execute(
                            "SELECT payload FROM local_competitions WHERE id=?",
                            (report["eventId"],),
                        ).fetchone()[0]
                        == stored_event
                    )
            inspection.update(
                {
                    "success": True,
                    "readOnlyExceptReplayLeases": True,
                    "statsUnchanged": True,
                    "statsMatchStandings": not (args.mixed_commander or args.grouped_commander),
                    "completedMatrix": not (args.mixed_commander or args.grouped_commander),
                    "partialAndCompleteReplayLinks": not (
                        args.mixed_commander or args.grouped_commander
                    ),
                    "groupsQualificationSeriesAndHistoryCounts": args.grouped_commander,
                    "recoveredFailedAttemptReplay": args.failed_attempt,
                    "storedCompetitionPayloadUnchanged": stored_event is not None,
                    "fourSeatAndGlobalReplayPerspectives": args.mixed_commander,
                    "desktopMobile": True,
                    "browserExceptions": 0,
                }
            )
    except Exception as error:
        inspection["failureType"] = type(error).__name__
        raise
    finally:
        if process.poll() is None:
            (profile / ".deepdeck/desktop-stop.request").touch()
            process.wait(timeout=30)
        inspection["processExitCode"] = process.returncode
        inspection["success"] = inspection["success"] and process.returncode == 0
        inspection_path.write_text(json.dumps(inspection, indent=2), encoding="utf-8")
    assert process.returncode == 0
    print(
        "PASS: four-seat/global perspectives, hidden libraries, last frame, desktop/mobile. "
        "Statistics unchanged; no match launched."
        if args.mixed_commander
        else "PASS: two groups, qualification, playoff series and real-match history counts; "
        "series replay to last frame, desktop/mobile. Statistics unchanged; no match launched."
        if args.grouped_commander
        else "PASS: real completed history, matrix, partial/full replay links, final frame, "
        "local stats versus standings, desktop/mobile. No match launched.",
        flush=True,
    )


if __name__ == "__main__":
    main()
