"""Exercise the player pages in the locally installed headless Edge browser.

Run with the app serving on port 8765. Screenshots go in ignored .deepdeck/ui-preview.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import websockets

ROOT = Path(__file__).resolve().parents[1]
APP_URL = os.environ.get("DEEPDECK_QA_APP_URL", "http://127.0.0.1:8765").rstrip("/")


async def verify(browser_url: str) -> None:
    async with websockets.connect(browser_url, max_size=20_000_000) as socket:
        counter = 0
        errors: list[str] = []

        async def command(method: str, params: dict | None = None) -> dict:
            nonlocal counter
            counter += 1
            request_id = counter
            await socket.send(
                json.dumps({"id": request_id, "method": method, "params": params or {}})
            )
            while True:
                message = json.loads(await socket.recv())
                if message.get("method") == "Runtime.exceptionThrown":
                    errors.append(str(message.get("params", {})))
                if message.get("id") == request_id:
                    if "error" in message:
                        raise RuntimeError(message["error"])
                    return message.get("result", {})

        async def evaluate(expression: str) -> object:
            response = await command(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True, "awaitPromise": True},
            )
            if "exceptionDetails" in response:
                raise RuntimeError(response["exceptionDetails"])
            return response.get("result", {}).get("value")

        await command("Page.enable")
        await command("Runtime.enable")
        # Capture settled layouts, not an in-flight drawer slide on slow CI GPUs.
        await command(
            "Emulation.setEmulatedMedia",
            {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]},
        )
        await command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        if os.environ.get("DEEPDECK_QA_ECOSYSTEM") == "1":
            english = os.environ.get("DEEPDECK_QA_ECOSYSTEM_LANGUAGE") == "en"
            await command(
                "Page.navigate",
                {
                    "url": APP_URL
                    + "/assets/deepdeck-ecosystem"
                    + ("-en" if english else "")
                    + ".html"
                },
            )
            deadline = time.monotonic() + 30
            while not await evaluate(
                "Boolean(document.querySelector('#architecture .ecosystem-flow'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("The ecosystem HTML did not render.")
                await asyncio.sleep(0.1)
            output = ROOT / ".deepdeck" / "ui-preview"
            output.mkdir(parents=True, exist_ok=True)
            assert await evaluate("document.querySelectorAll('.component').length===6")
            assert await evaluate(
                "[...document.querySelectorAll('a[href^=\"#\"]')].every(a=>document.getElementById(a.getAttribute('href').slice(1)))"
            )
            assert await evaluate(
                "document.querySelectorAll('.ecosystem-flow path.wire').length===10"
            )
            assert await evaluate("!document.querySelector('#explanations').open")
            assert await evaluate("performance.getEntriesByType('resource').length===0")
            for width, height, label in [(1440, 1100, "desktop"), (390, 844, "mobile")]:
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
                    "document.querySelector('#architecture').scrollIntoView({block:'start'})"
                )
                assert await evaluate("document.documentElement.scrollWidth<=innerWidth+1")
                if width < 500:
                    assert await evaluate(
                        "document.querySelector('.diagram-scroll').scrollWidth>document.querySelector('.diagram-scroll').clientWidth"
                    )
                    await evaluate("document.querySelector('#zoom-reset').click()")
                    assert await evaluate(
                        "document.querySelector('.diagram-scroll').scrollWidth<=document.querySelector('.diagram-scroll').clientWidth+1"
                    )
                shot = await command("Page.captureScreenshot", {"format": "png"})
                (output / f"ecosystem-{'en-' if english else ''}{label}.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
            await evaluate(
                "document.querySelector('.ecosystem-flow a[href=\"#sdk\"]').dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}))"
            )
            assert await evaluate("document.querySelector('#explanations').open")
            if not english:
                await evaluate("document.querySelector('#sdk details summary').click()")
                assert await evaluate("document.querySelector('#sdk details').open")
            assert not errors, errors
            print(
                "Ecosystem HTML: six roles, internal links, offline assets, desktop/mobile bounds and expandable explanations verified. No application action performed."
            )
            return
        await command("Page.navigate", {"url": APP_URL})
        deadline = time.monotonic() + 30
        while not await evaluate("Boolean(document.querySelector('.player-nav nav button'))"):
            if time.monotonic() > deadline:
                raise TimeoutError("The player app did not render.")
            await asyncio.sleep(0.2)
        output = ROOT / ".deepdeck" / "ui-preview"
        output.mkdir(parents=True, exist_ok=True)
        if os.environ.get("DEEPDECK_QA_CAPACITY_HELP") == "1":
            await evaluate("""(() => {
                const original=window.fetch.bind(window);window.__capacityHelpWrites=0;
                window.fetch=async(input,init)=>{
                    const method=(init?.method||input.method||'GET').toUpperCase();
                    if(!['GET','HEAD'].includes(method)){
                        window.__capacityHelpWrites++;
                        throw new Error('Read-only capacity-help review blocked a mutation');
                    }
                    return original(input,init);
                };
                [...document.querySelectorAll('.player-nav nav button')].find(button=>button.textContent.includes('League')).click();
            })()""")
            deadline = time.monotonic() + 30
            while not await evaluate(
                "Boolean(document.querySelector('.league-capacity .league-capacity-help a'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("Capacity help did not render beside the League count.")
                await asyncio.sleep(0.1)
            assert await evaluate(
                "document.querySelector('.league-capacity-help a').href==='https://www.deepdeckleague.com/support'"
            )
            assert await evaluate(
                "document.querySelector('.league-capacity-help').textContent.includes('Already supporting?')"
            )
            for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                    "document.querySelector('.league-capacity').scrollIntoView({block:'center'})"
                )
                await asyncio.sleep(0.2)
                assert await evaluate("document.documentElement.scrollWidth<=innerWidth+1"), (
                    "Capacity help overflows."
                )
                assert await evaluate(
                    "document.querySelector('.league-capacity-help a').getBoundingClientRect().height>=44"
                ), "Support link is too small for touch."
                shot = await command("Page.captureScreenshot", {"format": "png"})
                (output / f"league-capacity-patreon-{label}.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
            assert await evaluate("window.__capacityHelpWrites===0")
            assert not errors, errors
            print(
                "League capacity/Patreon explanation and safe support link verified beside the real server count on desktop/mobile. No deployment, subscription or account mutation.",
                flush=True,
            )
            return
        if os.environ.get("DEEPDECK_QA_RICH_CHAT") == "1":
            await evaluate("""(async()=>{
                const original=window.fetch.bind(window);window.__chatWrites=[];
                const art=(await (await original('/api/v1/catalog/cards/artwork?name='+encodeURIComponent("Atraxa, Praetors' Voice"))).json()).artwork;
                if(!art?.imageUrl)throw new Error('Real card artwork unavailable');
                window.fetch=async(input,init)=>{
                    const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
                    if(path==='/api/v1/agent-studio/llm-settings')return new Response(JSON.stringify({configured:true,providerName:'Streaming QA fixture',baseUrl:'http://localhost:11434/v1',model:'fixture',hasApiKey:false,leagueMcpEnabled:false}),{headers:{'Content-Type':'application/json'}});
                    if(path==='/api/v1/agent-studio/chat/stream'){
                        window.__chatWrites.push('fixture-only');
                        const encoder=new TextEncoder();
                        const filters={format:'commander',query:'',oracle_contains:'',semantic_text:'',types:[],color_identity:'',identity_match:'within',commander_only:true,color_count:4};
                        const event={tool:'set_card_search_filters',ok:true,result:{filters,source:'scryfall',query:'f:commander is:commander id=4',items:[{name:art.name,imageUrl:art.imageUrl,manaCost:'{G}{W}{U}{B}'}]}};
                        const message='Here is a four-color commander. {W}{U}{B}{G}\\n'+"'''magic_card -name \\\""+art.name+"\\\"'''";
                        return new Response(new ReadableStream({start(controller){
                            const send=value=>controller.enqueue(encoder.encode('data: '+JSON.stringify(value)+'\\n\\n'));
                            send({type:'delta',text:'Here is a four-color commander. {W}{U}{B}{G}\\n'});
                            window.__releaseChat=()=>{send({type:'tool',event});send({type:'delta',text:"'''magic_card -name \\\""+art.name+"\\\"'''"});send({type:'done',result:{message,toolEvents:[event],agents:[]}});controller.close();};
                        }}),{headers:{'Content-Type':'text/event-stream'}});
                    }
                    if((init?.method||'GET')!=='GET')throw new Error('Read-only chat QA blocked a mutation');
                    return original(input,init);
                };
                document.querySelector('[aria-label="Open DeepDeck copilot"]').click();
            })()""")
            deadline = time.monotonic() + 20
            while not await evaluate(
                "Boolean(document.querySelector('.assistant-composer textarea'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("Chat did not open")
                await asyncio.sleep(0.1)
            await evaluate("""(()=>{const input=document.querySelector('.assistant-composer textarea');
                Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'Find a four-color commander');input.dispatchEvent(new Event('input',{bubbles:true}));})()""")
            await evaluate("document.querySelector('.assistant-composer button').click()")
            deadline = time.monotonic() + 15
            while not await evaluate(
                "Boolean(document.querySelector('.assistant-rich-message .ms-w'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("No streamed fragment rendered before completion")
                await asyncio.sleep(0.1)
            assert await evaluate(
                "document.querySelector('.assistant-composer button').disabled&&document.querySelector('.assistant-rich-message').textContent.includes('four-color')"
            )
            await evaluate("window.__releaseChat()")
            deadline = time.monotonic() + 40
            while not await evaluate(
                "Boolean([...document.querySelectorAll('.chat-magic-card img')].find(img=>img.complete&&img.naturalWidth>0))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("Magic card block did not load real artwork")
                await asyncio.sleep(0.2)
            assert await evaluate(
                "document.querySelector('.assistant-refine-search [aria-label]')!==null||document.querySelector('.assistant-refine-search select')!==null"
            )
            assert await evaluate(
                "[...document.querySelectorAll('.assistant-refine-search select')].some(select=>select.value==='4')"
            )
            assert await evaluate("window.__chatWrites.length===1")
            for width, height, label in [(1440, 1000, "desktop"), (390, 844, "mobile")]:
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": 1,
                        "mobile": width < 500,
                    },
                )
                await evaluate("document.querySelector('.assistant-messages').scrollTop=0")
                assert await evaluate(
                    "document.querySelector('.assistant-drawer').getBoundingClientRect().right<=innerWidth+1"
                )
                shot = await command("Page.captureScreenshot", {"format": "png"})
                (output / f"rich-chat-{label}.png").write_bytes(base64.b64decode(shot["data"]))
            assert not errors, errors
            print(
                "Rich chat: streaming fixture text visible before completion, real Scryfall card artwork, mana glyphs and editable commander filters, desktop/mobile. No LLM request or local mutation sent."
            )
            return
        if os.environ.get("DEEPDECK_QA_LOCAL_SETTINGS") == "1":
            await evaluate("""(() => {
                const original=window.fetch.bind(window); window.__settingsWrites=[];
                window.fetch=async(input,init)=>{
                    const method=init?.method||input.method||'GET';
                    if(method!=='GET'){window.__settingsWrites.push(method);throw new Error('Read-only settings QA');}
                    return original(input,init);
                };
            })()""")
            deadline = time.monotonic() + 20
            while not await evaluate(
                "Boolean(document.querySelector('[aria-label=\"Local AI rhythm\"]'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("Local settings did not render.")
                await asyncio.sleep(0.1)
            assert (
                await evaluate(
                    "JSON.stringify([...document.querySelector('[aria-label=\"Local AI rhythm\"]').options].map(o=>o.value))"
                )
                == '["1ms","10ms","100ms"]'
            )
            assert await evaluate(
                "document.querySelector('[aria-label=\"Local AI rhythm\"]').value==='1ms'"
            )
            assert (
                await evaluate(
                    "JSON.stringify([...document.querySelector('[aria-label=\"Turn limit per game\"]').options].map(o=>o.value))"
                )
                == '["20","30","40","60","100"]'
            )
            assert await evaluate(
                "document.querySelector('[aria-label=\"Turn limit per game\"]').value==='60'"
            )
            assert (
                await evaluate(
                    "JSON.stringify([...document.querySelector('[aria-label=\"Match safety limit\"]').options].map(o=>o.value).filter(Boolean))"
                )
                == '["60","180","600"]'
            )
            await evaluate("document.querySelector('.competition-entry').click()")
            deadline = time.monotonic() + 20
            while not await evaluate(
                "Boolean([...document.querySelectorAll('.competition-builder button')].find(b=>b.textContent==='Add all agents'&&!b.disabled))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("No local agents available for competition QA.")
                await asyncio.sleep(0.1)
            await evaluate(
                "[...document.querySelectorAll('.competition-builder button')].find(b=>b.textContent==='Add all agents').click()"
            )
            await evaluate("""(() => {const field=document.querySelector('[aria-label="Competition format"]');
                field.value='random_season_cut';field.dispatchEvent(new Event('change',{bubbles:true}));})()""")

            async def edit_number(label: str, text: str, *, up: bool = False) -> None:
                await evaluate(
                    "document.querySelector('[aria-label='+JSON.stringify("
                    + json.dumps(label)
                    + ")+']').focus()"
                )
                await command(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "keyDown",
                        "key": "a",
                        "code": "KeyA",
                        "modifiers": 2,
                        "windowsVirtualKeyCode": 65,
                    },
                )
                await command(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "keyUp",
                        "key": "a",
                        "code": "KeyA",
                        "modifiers": 2,
                        "windowsVirtualKeyCode": 65,
                    },
                )
                await command(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "keyDown",
                        "key": "Backspace",
                        "code": "Backspace",
                        "windowsVirtualKeyCode": 8,
                    },
                )
                await command(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "keyUp",
                        "key": "Backspace",
                        "code": "Backspace",
                        "windowsVirtualKeyCode": 8,
                    },
                )
                assert await evaluate("document.activeElement.value===''"), (
                    f"{label} cannot be cleared."
                )
                await command("Input.insertText", {"text": text})
                if up:
                    await command(
                        "Input.dispatchKeyEvent",
                        {
                            "type": "keyDown",
                            "key": "ArrowUp",
                            "code": "ArrowUp",
                            "windowsVirtualKeyCode": 38,
                        },
                    )
                    await command(
                        "Input.dispatchKeyEvent",
                        {
                            "type": "keyUp",
                            "key": "ArrowUp",
                            "code": "ArrowUp",
                            "windowsVirtualKeyCode": 38,
                        },
                    )
                await asyncio.sleep(0.1)
                assert await evaluate("document.activeElement.value") == str(
                    int(text) + (1 if up else 0)
                )

            await edit_number("Playoff qualifiers", "2", up=True)
            await edit_number("Season rounds", "12", up=True)
            assert await evaluate(
                "document.querySelector('[aria-label=\"Playoff qualifiers\"]').value==='3'&&document.querySelector('[aria-label=\"Season rounds\"]').value==='13'"
            )
            readiness = await evaluate(
                "({disabled:document.querySelector('.competition-builder .launch-button').disabled,validation:document.querySelector('.competition-validation')?.textContent??'',engine:document.querySelector('.competition-builder .launch-bar').innerText})"
            )
            assert not readiness["validation"], readiness
            deadline = time.monotonic() + 30
            while await evaluate(
                "document.querySelector('.competition-builder .launch-button').disabled"
            ):
                if time.monotonic() > deadline:
                    raise AssertionError(readiness)
                await asyncio.sleep(0.2)
            await edit_number("Playoff qualifiers", "999")
            await evaluate("document.querySelector('.competition-builder .launch-button').click()")
            assert await evaluate(
                "document.querySelector('.competition-builder [role=alert]').textContent.includes('playoff qualifiers')"
            )
            assert await evaluate(
                "document.activeElement===document.querySelector('.competition-builder [role=alert]')"
            )
            await edit_number("Playoff qualifiers", "3")
            assert await evaluate("!document.querySelector('.competition-builder [role=alert]')")
            for width, height, label in [(1440, 1000, "desktop"), (390, 844, "mobile")]:
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
                    "document.querySelector('[aria-label=\"Playoff qualifiers\"]').scrollIntoView({block:'center'})"
                )
                assert await evaluate("document.documentElement.scrollWidth<=innerWidth+1")
                shot = await command("Page.captureScreenshot", {"format": "png"})
                (output / f"competition-settings-{label}.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
            assert await evaluate("window.__settingsWrites.length") == 0
            assert not errors, errors
            print(
                "Local settings: 1/10/100ms, 60-turn default, 1/3/10min; competition fields clear/type/ArrowUp work desktop/mobile. No match or competition created."
            )
            return
        if os.environ.get("DEEPDECK_QA_GLOBAL_COPILOT") == "1":
            assert await evaluate(
                "Boolean(document.querySelector('link[rel=icon][href=\"/assets/favicon.png\"]'))"
            )
            assert await evaluate(
                "fetch('/assets/favicon.png').then(r=>r.ok&&r.headers.get('content-type').includes('image/png'))"
            )
            await evaluate(
                "document.querySelector('button[aria-label=\"Open DeepDeck copilot\"]').click()"
            )
            deadline = time.monotonic() + 20
            while not await evaluate(
                "Boolean(document.querySelector('.assistant-layer.open .assistant-composer textarea'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("The configured global copilot did not open.")
                await asyncio.sleep(0.1)
            await evaluate("""(()=>{const field=document.querySelector('.assistant-composer textarea');
                Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(field,'QA draft: navigate without sending');
                field.dispatchEvent(new Event('input',{bubbles:true}));})()""")
            assert await evaluate(
                "getComputedStyle(document.querySelector('.assistant-layer.open')).pointerEvents==='none'&&getComputedStyle(document.querySelector('.assistant-drawer')).pointerEvents==='auto'"
            )
            for label in ["Decks", "Agents", "Competitions", "Replays", "Insights", "Play"]:
                await evaluate(
                    f"[...document.querySelectorAll('.assistant-page-nav button')].find(b=>b.textContent==={json.dumps(label)}).click()"
                )
                await asyncio.sleep(0.3)
                assert await evaluate(
                    "document.querySelector('.assistant-layer').classList.contains('open')&&document.querySelector('.assistant-composer textarea').value==='QA draft: navigate without sending'"
                )
            for width, height, label in [(1440, 1000, "desktop"), (390, 844, "mobile")]:
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
                    "document.querySelector('.assistant-drawer').getBoundingClientRect().right<=innerWidth+1"
                )
                screenshot = await command("Page.captureScreenshot", {"format": "png"})
                (output / f"global-copilot-{label}.png").write_bytes(
                    base64.b64decode(screenshot["data"])
                )
            await evaluate("document.querySelector('.assistant-drawer > header button').click()")
            await command(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
            )
            print(
                "Global copilot: retained unsent draft across pages, non-blocking layer, desktop/mobile, actual PNG favicon. No chat message sent."
            )
        if os.environ.get("DEEPDECK_QA_LOCAL_DIAGNOSTICS") == "1":
            await evaluate("""(() => {
              const previous=window.fetch;window.__localDiagnosticReads=0;
              window.__localDiagnosticOriginalFetch=previous;
              window.fetch=async(input,init)=>{
                const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
                if(path==='/api/v1/diagnostics')window.__localDiagnosticReads++;
                return previous(input,init);
              };
              document.querySelector('button[aria-label="Settings Accounts & storage"]').click();
            })()""")
            deadline = time.monotonic() + 15
            while not await evaluate(
                "Boolean(document.querySelector('.settings-layer.open .local-diagnostics'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("The local diagnostics settings did not render.")
                await asyncio.sleep(0.1)
            await evaluate("document.querySelector('.local-diagnostics > summary').click()")
            assert await evaluate("window.__localDiagnosticReads") == 0
            await evaluate(
                "Array.from(document.querySelectorAll('.local-diagnostics button')).find(b=>b.textContent==='Read local journal').click()"
            )
            deadline = time.monotonic() + 15
            while not await evaluate(
                "document.querySelector('.local-diagnostics').textContent.includes('stored ·')"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("The actual local diagnostic journal could not be read.")
                await asyncio.sleep(0.1)
            assert await evaluate("window.__localDiagnosticReads") == 1
            assert await evaluate(
                "document.querySelector('.local-diagnostics').textContent.includes('No API keys')"
            )
            await evaluate(
                "document.querySelector('.local-diagnostics').scrollIntoView({block:'start'})"
            )
            screenshot = await command("Page.captureScreenshot", {"format": "png"})
            (output / "local-diagnostics-desktop.png").write_bytes(
                base64.b64decode(screenshot["data"])
            )
            await command(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
            )
            await evaluate(
                "document.querySelector('.local-diagnostics').scrollIntoView({block:'start'})"
            )
            assert await evaluate(
                "(()=>{const r=document.querySelector('.local-diagnostics').getBoundingClientRect();return r.left>=-1&&r.right<=window.innerWidth+1;})()"
            )
            screenshot = await command("Page.captureScreenshot", {"format": "png"})
            (output / "local-diagnostics-mobile.png").write_bytes(
                base64.b64decode(screenshot["data"])
            )
            await command(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
            )
            await evaluate(
                "window.fetch=window.__localDiagnosticOriginalFetch;document.querySelector('.settings-drawer > header > button').click()"
            )
            print(
                "Local diagnostics: explicitly read the actual protected journal; opening Settings did not fetch it, no clipboard write or upload."
            )
        if os.environ.get("DEEPDECK_QA_LEAGUE_CONNECTION") == "1":
            await evaluate("""(() => {
              const original = window.fetch;
              window.__leagueQAOriginalFetch = original;
              window.__leagueQAWrites = [];
              window.fetch = async (input, init) => {
                const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
                const method = init?.method || input.method || 'GET';
                if (method !== 'GET') window.__leagueQAWrites.push(path);
                if (path === '/api/v1/agent-studio/mcp-test') return new Response(JSON.stringify({
                  connected:false,toolEvents:[{tool:'league_mcp_connection',ok:false,transport:'mcp',
                    errorCode:'membership_required',httpStatus:403,
                    error:'League MCP returned HTTP 403. An active Patreon tier 2 or higher membership is required for MCP access.'}]
                }),{status:200,headers:{'Content-Type':'application/json'}});
                if (method !== 'GET') throw new Error('Read-only League QA refuses other writes');
                return original(input,init);
              };
              window.dispatchEvent(new Event('deepdeck:open-league-settings'));
            })()""")
            deadline = time.monotonic() + 30
            while not await evaluate(
                "Boolean(document.querySelector('.settings-layer.open .league-key-connection'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("League key settings did not open.")
                await asyncio.sleep(0.2)
            while not await evaluate("""(() => {
              const panel=document.querySelector('.league-key-connection');
              return [...(panel?.querySelectorAll('button')||[])].some(
                b=>b.textContent.includes('Test saved key')&&!b.disabled);
            })()"""):
                if time.monotonic() > deadline:
                    raise TimeoutError("Saved key did not load for the read-only connection test.")
                await asyncio.sleep(0.2)
            await evaluate("""(() => {
              const panel=document.querySelector('.league-key-connection');
              const button=[...panel.querySelectorAll('button')].find(b=>b.textContent.includes('Test saved key'));
              if(!button || button.disabled) throw new Error('Saved-key test is unavailable');
              button.click();
            })()""")
            while not await evaluate(
                "document.querySelector('.league-key-connection')?.innerText.includes('HTTP 403')"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("MCP refusal did not render.")
                await asyncio.sleep(0.2)
            for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                    "document.querySelector('.league-key-connection .failed').scrollIntoView({block:'center'})"
                )
                await asyncio.sleep(0.2)
                layout = await evaluate("""(() => {
                  const panel=document.querySelector('.league-key-connection');
                  const links=[...panel.querySelectorAll('a')];
                  const patreon=links.find(a=>a.textContent.includes('View Patreon'));
                  const account=links.find(a=>a.textContent.includes('Sign in /'));
                  return {patreon:patreon?.href,account:account?.href,
                    overflow:document.documentElement.scrollWidth>innerWidth+1,
                    inputEmpty:panel.querySelector('input[type=password]').value==='',
                    optional:[...document.querySelectorAll('.settings-drawer details')].some(
                      d=>!d.open&&d.querySelector('summary')?.textContent.includes('Website session')),
                    visible:patreon.getBoundingClientRect().top>=0&&account.getBoundingClientRect().bottom<=innerHeight};
                })()""")
                assert layout == {
                    "patreon": "https://www.deepdeckleague.com/support",
                    "account": "https://www.deepdeckleague.com/account",
                    "overflow": False,
                    "inputEmpty": True,
                    "optional": True,
                    "visible": True,
                }, layout
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"league-key-refusal-{label}.png").write_bytes(
                    base64.b64decode(screenshot["data"])
                )
            assert await evaluate("window.__leagueQAWrites") == ["/api/v1/agent-studio/mcp-test"]
            await evaluate(
                "window.fetch=window.__leagueQAOriginalFetch;delete window.__leagueQAOriginalFetch"
            )
            assert not errors, errors
            print(
                "PASS: key-only setup, precise MCP refusal, Patreon/account links and optional website session on desktop/mobile. MCP response fixture only; no credential save, sign-in, paid LLM call or account mutation.",
                flush=True,
            )
            return
        if os.environ.get("DEEPDECK_QA_REPLAY_LAYOUT") == "1":
            await evaluate("document.querySelectorAll('.player-nav nav button')[5].click()")
            deadline = time.monotonic() + 40
            while not await evaluate(
                "Boolean(document.querySelector('.replay-toolbar .segmented button'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("Replay library did not appear.")
                await asyncio.sleep(0.2)
            await evaluate(
                "document.querySelectorAll('.replay-toolbar .segmented button')[2].click()"
            )
            while not await evaluate("Boolean(document.querySelector('.replay-watch'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "No local recording available for read-only replay layout QA."
                    )
                await asyncio.sleep(0.2)
            await evaluate("document.querySelector('.replay-watch').click()")
            while not await evaluate(
                "Boolean(document.querySelector('.replay-theater[open] canvas'))"
            ):
                if time.monotonic() > deadline:
                    detail = await evaluate("document.querySelector('.replay-theater')?.innerText")
                    raise TimeoutError(f"Replay canvas not rendered: {detail}; {errors}")
                await asyncio.sleep(0.2)
            assert await evaluate(
                "[...document.querySelectorAll('.pixi-playback-controls button')].some(b=>b.textContent==='Pause')"
            ), "Replays should start playing without an extra click."
            assert await evaluate(
                "document.querySelector('.match-sound-controls button').getAttribute('aria-pressed')==='true'"
            ), "Audio should be enabled by default."
            assert await evaluate(
                "Boolean(document.querySelector('.pixi-game-controls [aria-label=\"Replay frame\"]')) && !document.querySelector('.pixi-replay-controls')"
            ), "Playback, timeline, seat and sound must share one bottom bar."
            if os.environ.get("DEEPDECK_QA_TOKEN_REPLAY") == "1":
                # The newest saved Eldrazi vs Sneak and Show recording contains
                # two generated Eldrazi Spawn tokens at immutable frame 21.
                await evaluate(
                    "(()=>{const i=document.querySelector('[aria-label=\"Replay frame\"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'21');i.dispatchEvent(new Event('input',{bubbles:true}));})()"
                )
                deadline = time.monotonic() + 50
                while not await evaluate(
                    "(()=>{const d=document.querySelector('.pixi-game-canvas').dataset;return Number(d.tokenImagesExpected)>0 && d.tokenImagesExpected===d.tokenImagesReady && d.cardImagesLoading==='0';})()"
                ):
                    if time.monotonic() > deadline:
                        diagnostics = await evaluate(
                            "({...document.querySelector('.pixi-game-canvas').dataset})"
                        )
                        raise TimeoutError(f"Token artwork did not finish decoding: {diagnostics}")
                    await asyncio.sleep(0.2)
                print(
                    "Real recorded token image diagnostics: "
                    + json.dumps(
                        await evaluate("({...document.querySelector('.pixi-game-canvas').dataset})")
                    ),
                    flush=True,
                )
            for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": 1,
                        "mobile": width < 500,
                    },
                )
                await asyncio.sleep(0.5)
                sizes = await evaluate(
                    "(()=>{const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {width:r.width,height:r.height,top:r.top,bottom:r.bottom};};return {canvas:rect('.replay-theater canvas'),stage:rect('.pixi-replay-stage'),controls:rect('.pixi-game-controls')};})()"
                )
                shot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"replay-layout-{label}.png").write_bytes(base64.b64decode(shot["data"]))
                print("Replay layout " + label + ": " + json.dumps(sizes), flush=True)
                assert sizes["canvas"]["height"] >= 180 and sizes["canvas"]["width"] >= 300, (
                    "Replay board is collapsed."
                )
                assert sizes["controls"]["bottom"] <= height + 1, (
                    "Timeline controls fall outside viewport."
                )
                assert await evaluate(
                    "(()=>{const c=document.querySelector('canvas').getBoundingClientRect(),o=document.querySelector('.pixi-life-orb.local').getBoundingClientRect();return Math.abs(o.x+o.width/2-(c.x+c.width/2))<3&&o.width>=35;})()"
                ), "Local life orb must be larger and centered below the hand."
            assert not errors, errors
            print(
                "Real saved replay canvas and timeline verified desktop/mobile; recording only opened, no match or account changed.",
                flush=True,
            )
            return
        if os.environ.get("DEEPDECK_QA_LEAGUE_FEEDBACK") == "1":
            await evaluate("""(()=>{const original=window.fetch.bind(window);window.__leagueHelpQA={original,tests:0,writes:0};window.fetch=async(input,init)=>{
                const q=window.__leagueHelpQA,path=new URL(typeof input==='string'?input:input.url,location.href).pathname,method=(init?.method||input.method||'GET').toUpperCase();
                const json=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
                if(path==='/api/v1/agent-studio/mcp-test'&&method==='POST'){q.tests++;return json({connected:q.tests>1,toolEvents:[{tool:'league_mcp_connection',transport:'mcp',ok:q.tests>1,...(q.tests>1?{result:{tools:['league_search_cards']}}:{error:'Browser fixture: account MCP permission required.'})}]});}
                if(!['GET','HEAD'].includes(method)){q.writes++;throw new Error('Unexpected write blocked during League feedback QA.');}
                return original(input,init);
            };})()""")
            try:
                await evaluate("document.querySelectorAll('.player-nav nav button')[2].click()")
                deadline = time.monotonic() + 25
                while not await evaluate("Boolean(document.querySelector('.assistant-fab'))"):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Strategy chat did not appear.")
                    await asyncio.sleep(0.2)
                await evaluate("document.querySelector('.assistant-fab').click()")
                while not await evaluate(
                    "Boolean(document.querySelector('[aria-label=\"Assistant settings\"]'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Configured chat did not appear.")
                    await asyncio.sleep(0.2)
                await evaluate(
                    "document.querySelector('[aria-label=\"Assistant settings\"]').click()"
                )
                while not await evaluate("Boolean(document.querySelector('.league-access-help'))"):
                    if time.monotonic() > deadline:
                        raise TimeoutError("League connection help did not appear.")
                    await asyncio.sleep(0.1)
                await asyncio.sleep(0.3)
                await evaluate("document.querySelector('.chatgpt-welcome[open] button')?.click()")
                await evaluate(
                    "(()=>{let c=document.querySelector('.assistant-research-settings input[type=checkbox]');if(!c.checked)c.click();})()"
                )
                await evaluate(
                    "[...document.querySelectorAll('.assistant-research-settings button')].find(b=>b.textContent==='Test League tools · no LLM call').click()"
                )
                while not await evaluate(
                    "document.querySelector('.assistant-research-settings').innerText.includes('MCP connection failed')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("MCP failure feedback did not appear.")
                    await asyncio.sleep(0.1)
                await evaluate(
                    "[...document.querySelectorAll('.assistant-research-settings button')].find(b=>b.textContent==='Test League tools · no LLM call').click()"
                )
                while not await evaluate(
                    "document.querySelector('.assistant-research-settings').innerText.includes('MCP connected.')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("MCP success confirmation did not appear.")
                    await asyncio.sleep(0.1)
                await evaluate(
                    "[...document.querySelectorAll('.league-access-help button')].find(b=>b.textContent==='Connect League in Settings').click()"
                )
                assert await evaluate(
                    "document.querySelector('.settings-layer').classList.contains('open')"
                )
                assert await evaluate(
                    "Boolean(document.querySelector('.account-connection a[href]'))"
                )
                await evaluate(
                    "document.querySelector('.settings-layer > .assistant-scrim').click()"
                )
                for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                        "document.querySelector('.assistant-research-settings').scrollIntoView({block:'start'})"
                    )
                    await asyncio.sleep(0.3)
                    assert await evaluate(
                        "document.querySelector('.assistant-drawer').scrollWidth<=document.querySelector('.assistant-drawer').clientWidth+1"
                    ), "League helper overflows the chat."
                    shot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"league-feedback-{label}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
                assert await evaluate(
                    "window.__leagueHelpQA.tests===2&&window.__leagueHelpQA.writes===0"
                )
                assert not errors, errors
                print(
                    "League feedback: explicit failed/success MCP test, real settings shortcut and website link, desktop/mobile; fixture tests only, no LLM or account mutation.",
                    flush=True,
                )
            finally:
                await evaluate(
                    "window.fetch=window.__leagueHelpQA.original;delete window.__leagueHelpQA;"
                )
            return
        if os.environ.get("DEEPDECK_QA_CHAT_RESIZE") == "1":
            await evaluate("document.querySelectorAll('.player-nav nav button')[2].click()")
            deadline = time.monotonic() + 20
            while not await evaluate("Boolean(document.querySelector('.assistant-fab'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError("The strategy chat button did not appear.")
                await asyncio.sleep(0.2)
            await evaluate("document.querySelector('.assistant-fab').click()")
            await asyncio.sleep(0.4)
            await evaluate(
                "window.__resizeEvents=[];for(const t of ['pointerdown','pointermove','pointerup','lostpointercapture'])document.querySelector('.assistant-resize-handle').addEventListener(t,e=>window.__resizeEvents.push({type:e.type,x:e.clientX,button:e.button,id:e.pointerId}));"
            )
            start = await evaluate(
                "(()=>{let r=document.querySelector('.assistant-resize-handle').getBoundingClientRect();return {x:r.x+r.width*.75,y:r.y+r.height/2,width:document.querySelector('.assistant-drawer').getBoundingClientRect().width,hit:document.elementFromPoint(r.x+r.width*.75,r.y+r.height/2)?.className};})()"
            )
            await command(
                "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": start["x"], "y": start["y"]}
            )
            await command(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": start["x"],
                    "y": start["y"],
                    "button": "left",
                    "clickCount": 1,
                },
            )
            await asyncio.sleep(0.05)
            await command(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseMoved",
                    "x": start["x"] - 240,
                    "y": start["y"],
                    "button": "left",
                    "buttons": 1,
                },
            )
            await asyncio.sleep(0.15)
            await command(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": start["x"] - 240,
                    "y": start["y"],
                    "button": "left",
                    "clickCount": 1,
                },
            )
            width = await evaluate(
                "document.querySelector('.assistant-drawer').getBoundingClientRect().width"
            )
            assert abs(width - start["width"] - 240) < 2, (
                f"Pointer drag did not resize chat: {start} -> {width}; {await evaluate('window.__resizeEvents')}; {errors}."
            )
            await evaluate(
                "document.querySelector('.assistant-drawer button[aria-label=\"Close\"]').click();document.querySelector('.assistant-fab').click()"
            )
            await asyncio.sleep(0.4)
            assert (
                abs(
                    await evaluate(
                        "document.querySelector('.assistant-drawer').getBoundingClientRect().width"
                    )
                    - width
                )
                < 2
            )
            for viewport, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": viewport,
                        "height": height,
                        "deviceScaleFactor": 1,
                        "mobile": viewport < 500,
                    },
                )
                await asyncio.sleep(0.3)
                assert await evaluate(
                    "(()=>{let r=document.querySelector('.assistant-drawer').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1;})()"
                ), "Chat is outside viewport."
                shot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"chat-resize-{label}.png").write_bytes(base64.b64decode(shot["data"]))
            assert not errors, errors
            print(
                "Chat resize: real mouse drag +240 px, close/reopen retained, desktop/mobile bounds, no LLM request or settings mutation.",
                flush=True,
            )
            return
        if os.environ.get("DEEPDECK_QA_COMPETITION_SETUP") == "1":
            await evaluate("document.querySelector('.competition-entry').click()")
            deadline = time.monotonic() + 20
            while not await evaluate("Boolean(document.querySelector('.competition-builder'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError("Competition builder did not open.")
                await asyncio.sleep(0.2)
            await evaluate(
                "[...document.querySelectorAll('button')].find(b=>b.textContent==='Add all agents').click()"
            )
            await evaluate(
                "[...document.querySelectorAll('button')].find(b=>b.textContent==='Add all agents').click()"
            )
            await evaluate(
                "(()=>{let s=document.querySelector('select[aria-label=\"Competition format\"]');s.value='groups_cut';s.dispatchEvent(new Event('change',{bubbles:true}));})()"
            )
            assert await evaluate("Boolean(document.querySelector('input[aria-label=\"Groups\"]'))")
            await evaluate(
                "(()=>{let i=document.querySelector('input[aria-label=\"Playoff match wins to advance\"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'4');i.dispatchEvent(new Event('input',{bubbles:true}));})()"
            )
            assert await evaluate(
                "document.querySelector('.competition-builder').innerText.includes('Best of 7 matches')"
            )
            for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                    "document.querySelector('[aria-label=\"Help: Playoff match wins to advance\"]').scrollIntoView({block:'center'})"
                )
                await evaluate(
                    "document.querySelector('[aria-label=\"Help: Playoff match wins to advance\"]').click()"
                )
                await asyncio.sleep(0.3)
                assert await evaluate("Boolean(document.querySelector('[role=tooltip]'))")
                assert not await evaluate("document.documentElement.scrollWidth>innerWidth+1")
                if width < 500:
                    assert await evaluate(
                        "[...document.querySelectorAll('.field-help-button')].every(b=>b.getBoundingClientRect().height>=44)"
                    )
                shot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"competition-series-{label}.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
                await evaluate(
                    "document.querySelector('[aria-label=\"Help: Playoff match wins to advance\"]').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))"
                )
            await evaluate(
                "(()=>{let s=document.querySelector('select[aria-label=\"Game format\"]');s.value='commander';s.dispatchEvent(new Event('change',{bubbles:true}));})()"
            )
            assert await evaluate(
                "document.querySelector('select[aria-label=\"Competition format\"]').options.length===4"
            )
            assert not errors, errors
            print(
                "Competition setup: groups, best-of-seven explanation, accessible tooltip, four Commander formats, no desktop/mobile overflow. No competition, game or Engine start request submitted.",
                flush=True,
            )
            return
        if os.environ.get("DEEPDECK_QA_SPECTATOR") == "1":
            await evaluate("""(() => {
                const original=window.fetch.bind(window);
                const qa=window.__spectatorQA={original,sequence:40,writes:0,requests:[]};
                const job={id:'browser-spectator-fixture',kind:'playtest.batch',label:'Spectator preview',status:'running',created_at:new Date().toISOString(),logs:[],details:{sessionId:'browser-only',sessionStatus:'playing',currentMatchIndex:1,completed:0,total:1,engineUrl:'http://127.0.0.1:8787',deckNames:['Tempo','Combo']}};
                const card=(id,name)=>({instanceId:id,owner:id[0],controller:id[0],definition:{id,name,manaCost:'{U}',typeLine:'Instant',rules:[]},counters:{},tapped:false});
                const frame=sequence=>({sequence,revision:sequence,state:{status:'playing',turnNumber:sequence,step:'precombatMain',activePlayer:sequence%2,priorityPlayer:sequence%2,stack:[],events:[],combat:{attackers:[],blockers:[]},players:['a','b'].map((id,i)=>({id,name:i?'Combo pilot':'Tempo pilot',life:20,library:[null,null],hand:[card(id+'-card',i?'Daze':'Ponder')],graveyard:[],exile:[],battlefield:[],commandZone:[],sideboard:[],manaPool:[]}))},decision:null});
                window.fetch=async(input,init)=>{
                    const url=new URL(typeof input==='string'?input:input.url,location.href),method=(init?.method||input.method||'GET').toUpperCase();
                    qa.requests.push(url.pathname);
                    const json=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
                    if(!['GET','HEAD'].includes(method)){qa.writes++;throw new Error('Browser-only spectator QA blocks every mutation.');}
                    if(url.pathname==='/api/v1/jobs')return json([job]);
                    if(url.pathname==='/api/v1/jobs/'+job.id)return json(job);
                    if(url.pathname.startsWith('/game/sessions/browser-only/spectator')){
                        const first=url.pathname.endsWith('/spectator');
                        const count=first?1:3;
                        return json({frames:Array.from({length:count},()=>frame(first?qa.sequence:++qa.sequence)),hasMore:false});
                    }
                    return original(input,init);
                };
            })()""")
            try:
                deadline = time.monotonic() + 35
                while not await evaluate(
                    "[...document.querySelectorAll('.active-strip button')].some(b=>b.textContent==='Watch live')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("The fixture live job did not appear.")
                    await asyncio.sleep(0.2)
                await evaluate(
                    "[...document.querySelectorAll('.active-strip button')].find(b=>b.textContent==='Watch live').click()"
                )
                while not await evaluate(
                    "Boolean(document.querySelector('.live-match-theater canvas'))"
                ):
                    if time.monotonic() > deadline:
                        detail = await evaluate(
                            "document.querySelector('.live-match-theater')?.innerText"
                        )
                        raise TimeoutError(
                            f"The spectator canvas did not render: {detail}; {errors}"
                        )
                    await asyncio.sleep(0.2)
                assert await evaluate(
                    "document.querySelector('[aria-label=\"Spectator perspective\"]').options.length===3"
                )
                await evaluate(
                    "[...document.querySelectorAll('.pixi-playback-controls button')].find(b=>b.textContent==='Pause viewing').click()"
                )
                await asyncio.sleep(1.6)
                assert await evaluate(
                    "Number(document.querySelector('.pixi-playback-controls span').textContent.split(' ')[0])>=3"
                )
                await evaluate(
                    "(()=>{let s=document.querySelector('[aria-label=\"Spectator perspective\"]');s.value='b';s.dispatchEvent(new Event('change',{bubbles:true}));})()"
                )
                assert await evaluate(
                    "document.querySelector('[aria-label=\"Spectator perspective\"]').value==='b'"
                )
                assert await evaluate(
                    "document.querySelector('.match-sound-controls button').getAttribute('aria-pressed')==='true'"
                )
                await evaluate(
                    """(()=>{const q=window.__spectatorQA;q.audioStarts=0;q.audioFactory=AudioContext.prototype.createOscillator;AudioContext.prototype.createOscillator=function(...args){const voice=q.audioFactory.apply(this,args),start=voice.start.bind(voice);voice.start=(...timing)=>{q.audioStarts++;return start(...timing);};return voice;};})()"""
                )
                await command(
                    "Runtime.evaluate",
                    {
                        "expression": "document.body.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}))",
                        "userGesture": True,
                    },
                )
                await asyncio.sleep(0.2)
                assert await evaluate(
                    "document.querySelector('.match-sound-controls button').getAttribute('aria-pressed')==='true'"
                )
                assert await evaluate("window.__spectatorQA.audioStarts===0"), (
                    "Enabling sound must not replay a stale action."
                )
                await evaluate(
                    "[...document.querySelectorAll('.pixi-playback-controls button')].find(b=>b.textContent==='Resume viewing').click()"
                )
                audio_deadline = time.monotonic() + 8
                while not await evaluate("window.__spectatorQA.audioStarts>0"):
                    if time.monotonic() > audio_deadline:
                        raise TimeoutError("No sound followed the rendered forward playback.")
                    await asyncio.sleep(0.1)
                await evaluate(
                    "[...document.querySelectorAll('.pixi-playback-controls button')].find(b=>b.textContent==='Pause viewing').click()"
                )
                for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
                    await command(
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": width,
                            "height": height,
                            "deviceScaleFactor": 1,
                            "mobile": width < 500,
                        },
                    )
                    await asyncio.sleep(0.4)
                    assert await evaluate(
                        "document.querySelector('.live-match-theater').scrollWidth<=innerWidth+1"
                    ), "Spectator controls overflow horizontally."
                    assert await evaluate(
                        "document.querySelector('.live-match-theater canvas').getBoundingClientRect().height>180"
                    ), "Spectator controls leave no useful board area."
                    assert await evaluate(
                        "Boolean(document.querySelector('.pixi-game-controls .match-sound-controls')) && Boolean(document.querySelector('.pixi-game-controls [aria-label=\"Spectator perspective\"]'))"
                    ), "Sound and seat controls must be in the existing bottom Pixi toolbar."
                    assert await evaluate(
                        "(()=>{const canvas=document.querySelector('.live-match-theater canvas').getBoundingClientRect(),bar=document.querySelector('.pixi-game-controls').getBoundingClientRect();return Math.abs(bar.top-canvas.bottom)<3;})()"
                    ), "The bottom options toolbar must reserve space rather than cover the hand."
                    assert await evaluate(
                        "(()=>{const b=document.querySelector('.match-sound-controls button'),r=b.getBoundingClientRect();return b.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));})()"
                    ), "Sound controls are obscured."
                    shot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"spectator-buffered-{label}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
                assert await evaluate("window.__spectatorQA.writes===0")
                assert not errors, errors
                print(
                    "Spectator: real Pixi desktop/mobile canvas, seat/global selector, paused buffered AI frames, default-on gesture-unlocked audio; no server mutation.",
                    flush=True,
                )
            finally:
                await evaluate(
                    "document.querySelector('.live-match-theater .replay-theater-header button')?.click();window.fetch=window.__spectatorQA.original;if(window.__spectatorQA.audioFactory)AudioContext.prototype.createOscillator=window.__spectatorQA.audioFactory;delete window.__spectatorQA;"
                )
            return
        if os.environ.get("DEEPDECK_QA_COMPETITION_LIVE") == "1":
            await evaluate("document.querySelectorAll('.player-nav nav button')[4].click()")
            deadline = time.monotonic() + 40
            while not await evaluate(
                "[...document.querySelectorAll('.competition-history-card h3')].some(h=>h.textContent==='My local competition')"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "The requested competition was not available; no event was created."
                    )
                await asyncio.sleep(0.2)
            await evaluate(
                "[...document.querySelectorAll('.competition-history-card')].find(b=>b.querySelector('h3')?.textContent==='My local competition').click()"
            )
            while not await evaluate(
                "[...document.querySelectorAll('.competition-actions button')].some(b=>b.textContent==='Watch current match')"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "No current AI match; no competition was resumed or started."
                    )
                await asyncio.sleep(0.2)
            await evaluate(
                "[...document.querySelectorAll('.competition-actions button')].find(b=>b.textContent==='Watch current match').click()"
            )
            deadline = time.monotonic() + 60
            while not await evaluate(
                "Boolean(document.querySelector('.live-match-theater[open] canvas'))"
            ):
                if await evaluate("Boolean(document.querySelector('.app-recovery'))"):
                    raise AssertionError("Opening the competition spectator reached root recovery.")
                if time.monotonic() > deadline:
                    detail = await evaluate(
                        "document.querySelector('.live-match-theater')?.innerText?.slice(0,1500)"
                    )
                    raise TimeoutError(
                        f"The existing competition spectator did not render: {detail}"
                    )
                await asyncio.sleep(0.2)
            for width, height, label in [(1440, 1000, "desktop"), (390, 844, "mobile")]:
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": 1,
                        "mobile": width < 500,
                    },
                )
                await asyncio.sleep(0.4)
                assert await evaluate(
                    "Boolean(document.querySelector('.live-match-theater canvas')) && !document.querySelector('.app-recovery')"
                )
                shot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"competition-live-{label}.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
            await evaluate(
                "document.querySelector('.live-match-theater .replay-theater-header button').click()"
            )
            assert await evaluate(
                "document.querySelector('.competition-summary h2')?.textContent==='My local competition'"
            )
            assert not errors, errors
            print(
                "Actual My local competition spectator opened and closed on desktop/mobile. Read-only; no pause, stop, resume or gameplay mutation.",
                flush=True,
            )
            return
        if os.environ.get("DEEPDECK_QA_LIVE_ONLY") == "1":
            deadline = time.monotonic() + 60
            for nav_index in (0, 5):
                await evaluate(
                    f"document.querySelectorAll('.player-nav nav button')[{nav_index}].click()"
                )
                selector = ".active-strip button" if nav_index == 0 else ".live-now-row button"
                while not await evaluate(
                    f"[...document.querySelectorAll('{selector}')].some(b=>b.textContent==='Watch live')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("No running local match was available for live QA.")
                    await asyncio.sleep(0.2)
                await evaluate(
                    f"[...document.querySelectorAll('{selector}')].find(b=>b.textContent==='Watch live').click()"
                )
                while not await evaluate(
                    "Boolean(document.querySelector('.live-match-theater[open] canvas'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Live Pixi canvas did not appear.")
                    await asyncio.sleep(0.2)
                for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
                    await command(
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": width,
                            "height": height,
                            "deviceScaleFactor": 1,
                            "mobile": width < 500,
                        },
                    )
                    await asyncio.sleep(0.4)
                    screenshot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"batch-live-{nav_index}-{label}.png").write_bytes(
                        base64.b64decode(screenshot["data"])
                    )
                await evaluate(
                    "document.querySelector('.live-match-theater .replay-theater-header button').click()"
                )
                assert not await evaluate(
                    "Boolean(document.querySelector('.live-match-theater[open]'))"
                )
            assert not errors, errors
            print(
                "Real read-only Pixi spectator verified from Play and Replays on desktop/mobile. No stop or gameplay action submitted.",
                flush=True,
            )
            return
        await evaluate("document.querySelector('.avatar-button').click()")
        await asyncio.sleep(0.4)
        deadline = time.monotonic() + 20
        while not await evaluate("Boolean(document.querySelector('.league-server-settings > a'))"):
            if time.monotonic() > deadline:
                raise TimeoutError("Settings did not show the configured League server.")
            await asyncio.sleep(0.2)
        server_label = await evaluate("document.querySelector('.league-server-settings').innerText")
        assert server_label, "The League server diagnostic is empty."
        print("League target: " + json.dumps(server_label, ensure_ascii=True), flush=True)
        assert await evaluate("Boolean(document.querySelector('.account-connection'))"), (
            "The Settings account connection is missing."
        )
        assert await evaluate(
            "(() => {const form=document.querySelector('.account-connection form');"
            "return !form || getComputedStyle(form).gridTemplateColumns.trim().split(/\\s+/).length === 1;})()"
        ), "Account sign-in fields are not stacked into a readable single column."
        screenshot = await command(
            "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
        )
        (output / "settings.png").write_bytes(base64.b64decode(screenshot["data"]))
        await evaluate("document.querySelector('[aria-label=\"Close settings\"]').click()")
        if os.environ.get("DEEPDECK_QA_DECK_EDITOR") == "1":
            await evaluate("document.querySelectorAll('.player-nav nav button')[1].click()")
            before = await evaluate(
                "fetch('/api/v1/deck-workshop/decks').then(x => x.json()).then(x => x.items.map(d => d.id))"
            )
            if os.environ.get("DEEPDECK_QA_COLOR_IDENTITY") == "1":
                await evaluate(
                    "[...document.querySelectorAll('.deck-library-card .segmented button')].find(b=>b.textContent==='Commander').click()"
                )
            await evaluate("document.querySelector('.deck-library-card .primary-action').click()")
            deadline = time.monotonic() + 15
            while not await evaluate("Boolean(document.querySelector('.deck-editor-shell'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError("The new deck editor did not open.")
                await asyncio.sleep(0.2)
            created = await evaluate("localStorage.getItem('deepdeck.activeDeckId')")
            assert created and isinstance(before, list) and created not in before
            try:
                assert await evaluate("Boolean(document.querySelector('.deck-sync-bar'))")
                await evaluate(
                    "[...document.querySelectorAll('.deck-editor-actions button')]"
                    ".find(x => x.textContent === 'Save deck').click()"
                )
                deadline = time.monotonic() + 15
                while not await evaluate(
                    "Boolean(document.querySelector('.inline-success')) && [...document.querySelectorAll('.deck-editor-actions button')].some(b=>b.textContent==='Save deck' && !b.disabled)"
                ):
                    save_error = await evaluate(
                        "document.querySelector('.deck-editor-shell > .inline-error')?.textContent"
                    )
                    if save_error:
                        raise AssertionError(f"Saving the QA deck failed: {save_error}")
                    if time.monotonic() > deadline:
                        raise TimeoutError("Saving the new local deck did not complete.")
                    await asyncio.sleep(0.2)
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / "deck-editor.png").write_bytes(base64.b64decode(screenshot["data"]))
                # Simulate another local editor on this newly-created QA deck,
                # never on a player's existing list or on the League server.
                await evaluate("""(() => {
                    const field=document.querySelector('.deck-editor-meta input');
                    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,'Unsaved local QA name');
                    field.dispatchEvent(new Event('input',{bubbles:true}));
                })()""")
                concurrent = await evaluate("""(async () => {
                    const id=localStorage.getItem('deepdeck.activeDeckId');
                    const url='/api/v1/deck-workshop/decks/'+id;
                    const current=await (await fetch(url)).json();
                    const session=await (await fetch('/api/v1/session')).json();
                    const response=await fetch(url,{method:'PUT',headers:{'Content-Type':'application/json','X-DeepDeck-Token':session.token},
                        body:JSON.stringify({expectedVersion:current.version,name:'Other QA editor',tags:['qa-external']})});
                    if(!response.ok) throw new Error('The isolated concurrent edit failed.');
                    return await response.json();
                })()""")
                await evaluate(
                    "[...document.querySelectorAll('.deck-editor-actions button')].find(b=>b.textContent==='Save deck').click()"
                )
                deadline = time.monotonic() + 15
                while not await evaluate("Boolean(document.querySelector('.local-deck-conflict'))"):
                    if time.monotonic() > deadline:
                        raise TimeoutError("A stale deck save did not show its local conflict.")
                    await asyncio.sleep(0.1)
                assert await evaluate(
                    "document.querySelector('.deck-editor-meta input').value==='Unsaved local QA name'"
                ), "The unsaved name was lost."
                for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                        "document.querySelector('.local-deck-conflict').scrollIntoView({block:'start'})"
                    )
                    await asyncio.sleep(0.2)
                    assert not await evaluate(
                        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                    ), "The local deck conflict overflows."
                    screenshot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"deck-local-conflict-{label}.png").write_bytes(
                        base64.b64decode(screenshot["data"])
                    )
                await evaluate(
                    "[...document.querySelectorAll('.local-deck-conflict button')].find(b=>b.textContent==='Keep my edited details').click()"
                )
                deadline = time.monotonic() + 15
                while not await evaluate(
                    "!document.querySelector('.local-deck-conflict') && [...document.querySelectorAll('.deck-editor-actions button')].some(b=>b.textContent==='Save deck' && !b.disabled)"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("The explicit local draft reapply did not finish.")
                    await asyncio.sleep(0.1)
                retained = await evaluate(
                    "fetch('/api/v1/deck-workshop/decks/'+localStorage.getItem('deepdeck.activeDeckId')).then(r=>r.json())"
                )
                assert retained["name"] == "Unsaved local QA name" and retained["tags"] == [
                    "qa-external"
                ], retained
                assert retained["version"] == concurrent["version"] + 1
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
                )
                print(
                    "Local deck conflict: a stale real API save was refused; explicit reapply retained the concurrent tags and the unsaved name.",
                    flush=True,
                )
                await evaluate("document.querySelector('.advanced-card-search').open=true")
                await evaluate("""(() => {
                    const panel=document.querySelector('.advanced-card-search');
                    const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
                    const oracle=panel.querySelector('input[placeholder="destroy target creature"]');
                    set.call(oracle,'draw'); oracle.dispatchEvent(new Event('input',{bubbles:true}));
                    const maximum=panel.querySelector('input[type="number"][placeholder="Any"]');
                    set.call(maximum,'2'); maximum.dispatchEvent(new Event('input',{bubbles:true}));
                    const selects=panel.querySelectorAll('select');
                    selects[0].value='Instant'; selects[0].dispatchEvent(new Event('change',{bubbles:true}));
                    [...panel.querySelectorAll('.identity-colors button')].find(b=>b.textContent==='Blue').click();
                })()""")
                await evaluate(
                    "document.querySelector('.advanced-card-search form').requestSubmit()"
                )
                deadline = time.monotonic() + 30
                while not await evaluate(
                    "Boolean(document.querySelector('.advanced-card-search .assistant-card-results strong'))"
                ):
                    error = await evaluate(
                        "document.querySelector('.advanced-card-search .inline-error')?.textContent"
                    )
                    if error:
                        raise AssertionError(f"Production card research failed: {error}")
                    if time.monotonic() > deadline:
                        raise TimeoutError("Advanced card research did not return real results.")
                    await asyncio.sleep(0.2)
                native_query = await evaluate(
                    "document.querySelector('.assistant-search-query').textContent"
                )
                assert "mv<=2" in native_query and "id!=B" in native_query, native_query
                if os.environ.get("DEEPDECK_QA_COLOR_IDENTITY") == "1":

                    async def identity_click(name: str) -> None:
                        await evaluate(
                            "[...document.querySelectorAll('.color-identity-filter button')].find(b=>b.textContent==="
                            + json.dumps(name)
                            + ").click()"
                        )

                    async def identity_search(expected: str) -> None:
                        await evaluate(
                            "document.querySelector('.advanced-card-search form').requestSubmit()"
                        )
                        deadline = time.monotonic() + 30
                        while not await evaluate(
                            "document.querySelector('.advanced-card-search .assistant-search-query')?.textContent.includes("
                            + json.dumps(expected)
                            + ") && document.querySelector('.advanced-card-search button[type=submit]').getAttribute('aria-busy')==='false'"
                        ):
                            error = await evaluate(
                                "document.querySelector('.advanced-card-search .inline-error')?.textContent"
                            )
                            if error:
                                raise AssertionError(error)
                            if time.monotonic() > deadline:
                                raise TimeoutError(
                                    "Production color identity search did not finish."
                                )
                            await asyncio.sleep(0.1)

                    for name in ("White", "Black", "Red"):
                        await identity_click(name)
                    await identity_search("id!=G")
                    assert await evaluate(
                        "document.querySelectorAll('.identity-colors button[aria-pressed=true]').length===4"
                    )
                    assert await evaluate(
                        "document.querySelector('.advanced-card-search button[type=submit]').textContent.includes('Commander')"
                    )
                    for state in ("four-colors", "colorless"):
                        if state == "colorless":
                            await identity_click("Colorless only")
                            assert await evaluate(
                                "document.querySelectorAll('.identity-colors button[aria-pressed=true]').length===0"
                            )
                            await identity_search("id:C")
                        for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                                "document.querySelector('.color-identity-filter').scrollIntoView({block:'start'})"
                            )
                            await asyncio.sleep(0.2)
                            assert not await evaluate(
                                "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                            ), "Color identity filter overflows."
                            assert await evaluate(
                                "[...document.querySelectorAll('.color-identity-filter button')].every(b=>b.getBoundingClientRect().height>=44)"
                            )
                            shot = await command(
                                "Page.captureScreenshot",
                                {"format": "png", "captureBeyondViewport": False},
                            )
                            (output / f"color-identity-{state}-{label}.png").write_bytes(
                                base64.b64decode(shot["data"])
                            )
                    await identity_click("Green")
                    assert await evaluate(
                        "document.querySelector('.identity-modes button:last-child').getAttribute('aria-pressed')==='false'"
                    )
                    await identity_click("Any colors")
                    assert await evaluate(
                        "document.querySelectorAll('.identity-colors button[aria-pressed=true]').length===0"
                    )
                    await identity_click("Blue")
                    await identity_search("id!=B")
                    await command(
                        "Emulation.setDeviceMetricsOverride",
                        {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
                    )
                    print(
                        "Commander four-color and colorless searches verified against production, including exclusive modes and reset, real mana symbols and 44px controls on desktop/mobile. Only the disposable QA deck is edited.",
                        flush=True,
                    )
                if os.environ.get("DEEPDECK_QA_CARD_PAGES") == "1":
                    first_names = await evaluate(
                        "[...document.querySelectorAll('.advanced-card-search .assistant-card-results strong')].map(e=>e.textContent)"
                    )
                    assert len(first_names) == 20, (
                        "This production search needs more than twenty results for pagination QA."
                    )
                    await evaluate("""(() => {
                        const state=window.__cardPagesQA={original:window.fetch.bind(window),failOnce:true,requests:[]};
                        window.fetch=async(input,init)=>{
                            const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
                            if(path==='/api/v1/research/cards'){
                                state.requests.push(JSON.parse(init.body));
                                if(state.failOnce){state.failOnce=false;return new Response(JSON.stringify({detail:'Browser QA next-page failure'}),{status:503,headers:{'Content-Type':'application/json'}});}
                            }
                            return state.original(input,init);
                        };
                    })();""")
                    try:
                        await evaluate(
                            "[...document.querySelectorAll('.research-pagination button')].find(b=>b.textContent==='Next results').click()"
                        )
                        deadline = time.monotonic() + 25
                        while not await evaluate(
                            "document.querySelector('.advanced-card-search .inline-error')?.textContent.includes('next-page failure')"
                        ):
                            if time.monotonic() > deadline:
                                raise TimeoutError("Failed card pagination was not reported.")
                            await asyncio.sleep(0.1)
                        assert (
                            await evaluate(
                                "[...document.querySelectorAll('.advanced-card-search .assistant-card-results strong')].map(e=>e.textContent)"
                            )
                            == first_names
                        )
                        await evaluate(
                            "[...document.querySelectorAll('.research-pagination button')].find(b=>b.textContent==='Next results').click()"
                        )
                        while not await evaluate(
                            "document.querySelector('.research-pagination')?.textContent.includes('cards 21–')"
                        ):
                            error = await evaluate(
                                "document.querySelector('.advanced-card-search .inline-error')?.textContent"
                            )
                            if error and "next-page failure" not in error:
                                raise AssertionError(error)
                            if time.monotonic() > deadline:
                                raise TimeoutError("Production card pagination did not advance.")
                            await asyncio.sleep(0.1)
                        later_names = await evaluate(
                            "[...document.querySelectorAll('.advanced-card-search .assistant-card-results strong')].map(e=>e.textContent)"
                        )
                        assert not set(first_names) & set(later_names), (
                            "Production result windows overlapped."
                        )
                        assert await evaluate(
                            "JSON.stringify(window.__cardPagesQA.requests[0])===JSON.stringify(window.__cardPagesQA.requests[1])"
                        ), "Retry used different filters/cursor."
                        for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                                "document.querySelector('.research-pagination').scrollIntoView({block:'start'})"
                            )
                            await asyncio.sleep(0.2)
                            assert not await evaluate(
                                "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                            ), "Card result pagination overflows."
                            shot = await command(
                                "Page.captureScreenshot",
                                {"format": "png", "captureBeyondViewport": False},
                            )
                            (output / f"card-pagination-{label}.png").write_bytes(
                                base64.b64decode(shot["data"])
                            )
                        await evaluate(
                            "[...document.querySelectorAll('.research-pagination button')].find(b=>b.textContent==='Previous results').click()"
                        )
                        while not await evaluate(
                            "document.querySelector('.research-pagination')?.textContent.includes('cards 1–')"
                        ):
                            if time.monotonic() > deadline:
                                raise TimeoutError("Previous card results did not return.")
                            await asyncio.sleep(0.1)
                        await command(
                            "Emulation.setDeviceMetricsOverride",
                            {
                                "width": 1440,
                                "height": 1000,
                                "deviceScaleFactor": 1,
                                "mobile": False,
                            },
                        )
                        print(
                            "Production card windows 1–20 and 21–40, failed-next retention, identical retry and Previous verified on desktop/mobile. No LLM; only the disposable QA deck is edited.",
                            flush=True,
                        )
                    finally:
                        await evaluate(
                            "window.fetch=window.__cardPagesQA.original;delete window.__cardPagesQA;"
                        )
                # Save one actual result in a non-main zone, not just a screenshot.
                await evaluate("document.querySelectorAll('.deck-zone-tabs button')[1].click()")
                deadline = time.monotonic() + 30
                while not await evaluate(
                    "Boolean(document.querySelector('.assistant-card-results button:not(:disabled)'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("The researched card could not be added.")
                    await asyncio.sleep(0.2)
                await evaluate("document.querySelector('.assistant-card-results button').click()")
                while True:
                    saved = await evaluate(
                        f"fetch('/api/v1/deck-workshop/decks/{created}').then(x=>x.json())"
                    )
                    if any(
                        card.get("section") == "sideboard" and card.get("quantity") == 1
                        for card in saved.get("cards", [])
                    ):
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            "Adding the researched card did not persist in Sideboard."
                        )
                    await asyncio.sleep(0.2)
                await evaluate(
                    "document.querySelector('.advanced-card-search').scrollIntoView({block:'start'})"
                )
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / "card-research.png").write_bytes(base64.b64decode(screenshot["data"]))
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
                )
                assert not await evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                ), "The mobile card research overflows."
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / "card-research-mobile.png").write_bytes(
                    base64.b64decode(screenshot["data"])
                )
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
                )
                if os.environ.get("DEEPDECK_QA_DECK_STUDIES") == "1":

                    async def editor_settled() -> None:
                        deadline = time.monotonic() + 20
                        while not await evaluate(
                            "[...document.querySelectorAll('.deck-editor-actions button')].some(b=>b.textContent==='Save deck' && !b.disabled)"
                        ):
                            if time.monotonic() > deadline:
                                raise TimeoutError("The deck editor did not finish saving.")
                            await asyncio.sleep(0.1)

                    await editor_settled()
                    await evaluate("document.querySelectorAll('.deck-zone-tabs button')[0].click()")
                    await evaluate(
                        """(() => {const input=document.querySelector('.deck-card-search input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Island');input.dispatchEvent(new Event('input',{bubbles:true}));})()"""
                    )
                    await evaluate("document.querySelector('.deck-card-search').requestSubmit()")
                    await editor_settled()
                    for copies in range(2, 8):
                        await evaluate(
                            "[...document.querySelectorAll('.deck-card-list article')].find(a=>a.querySelector('.deck-card-copy strong').textContent==='Island').querySelector('.quantity-stepper button:last-child').click()"
                        )
                        await editor_settled()
                        assert await evaluate(
                            f"[...document.querySelectorAll('.deck-card-list article')].find(a=>a.querySelector('.deck-card-copy strong').textContent==='Island').querySelector('.quantity-stepper strong').textContent==='{copies}'"
                        )
                    await evaluate(
                        "[...document.querySelectorAll('.deck-studies button')].find(b=>b.textContent==='Draw a fresh hand').click()"
                    )
                    assert await evaluate(
                        "document.querySelectorAll('.study-practice-hand .study-card').length===7"
                    )
                    assert await evaluate(
                        "[...document.querySelectorAll('.study-practice-hand .study-card strong')].every(n=>n.textContent==='Island')"
                    )
                    await evaluate(
                        "[...document.querySelectorAll('.deck-studies button')].find(b=>b.textContent==='Annotate this hand').click()"
                    )
                    await evaluate(
                        """(() => {const form=document.querySelector('.study-note-form');const input=form.querySelector('input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'QA opening sample');input.dispatchEvent(new Event('input',{bubbles:true}));const select=form.querySelector('select');select.value='keep';select.dispatchEvent(new Event('change',{bubbles:true}));})()"""
                    )
                    await evaluate("document.querySelector('.study-note-form').requestSubmit()")
                    await editor_settled()
                    await evaluate(
                        "[...document.querySelectorAll('.deck-editor-actions button')].find(b=>b.textContent==='Save deck').click()"
                    )
                    await editor_settled()
                    await evaluate(
                        "[...document.querySelectorAll('.deck-studies button')].find(b=>b.textContent==='+ Add combo note').click()"
                    )
                    await evaluate(
                        """(() => {const form=document.querySelector('.study-note-form');for(const [field,value] of [[form.querySelector('input'),'QA combo study'],[form.querySelector('textarea'),'Island'],[form.querySelectorAll('textarea')[1],'Study only, not a verified combo.']]) {const proto=field.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(field,value);field.dispatchEvent(new Event('input',{bubbles:true}));}})()"""
                    )
                    await evaluate("document.querySelector('.study-note-form').requestSubmit()")
                    await editor_settled()
                    await evaluate(
                        "[...document.querySelectorAll('.deck-editor-actions button')].find(b=>b.textContent==='Save deck').click()"
                    )
                    await editor_settled()
                    study_saved = await evaluate(
                        "fetch('/api/v1/deck-workshop/decks/'+localStorage.getItem('deepdeck.activeDeckId')).then(r=>r.json())"
                    )
                    assert (
                        study_saved["openingHands"][0]["cards"] == ["Island"] * 7
                        and study_saved["openingHands"][0]["keep"] is True
                    )
                    assert study_saved["combos"][0]["name"] == "QA combo study"
                    await evaluate("document.querySelector('.study-relationship-panel').open=true")
                    for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                            "document.querySelector('.deck-studies').scrollIntoView({block:'start'})"
                        )
                        await asyncio.sleep(0.2)
                        assert not await evaluate(
                            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                        ), "The study notebook overflows."
                        shot = await command(
                            "Page.captureScreenshot",
                            {"format": "png", "captureBeyondViewport": False},
                        )
                        (output / f"deck-studies-{label}.png").write_bytes(
                            base64.b64decode(shot["data"])
                        )
                    await command(
                        "Emulation.setDeviceMetricsOverride",
                        {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
                    )
                    print(
                        "Study notebook: main-deck-only seven-card draw, manual combo and keep notes saved through the real UI and API; desktop/mobile layouts checked.",
                        flush=True,
                    )
                if os.environ.get("DEEPDECK_QA_CHAT_ADD") == "1":
                    # Only the newly created QA deck is written. The model is
                    # simulated, but the version conflict and retry use real HTTP.
                    suggested_name = next(
                        card["name"] for card in saved["cards"] if card["section"] == "sideboard"
                    )
                    fixture = {
                        "message": "One researched card to try.",
                        "agents": [],
                        "toolEvents": [
                            {
                                "tool": "search_cards",
                                "ok": True,
                                "result": {"items": [{"name": suggested_name}]},
                            }
                        ],
                    }
                    await evaluate(
                        "window.__chatAddQA={original:window.fetch.bind(window),url:"
                        + json.dumps(f"/api/v1/deck-workshop/decks/{created}")
                        + ",injected:false,saved:false,puts:0};"
                    )
                    try:
                        await evaluate(
                            """window.fetch=async(input,init)=>{
                            const state=window.__chatAddQA;
                            const url=new URL(typeof input==='string'?input:input.url,location.href);
                            const method=(init?.method||input.method||'GET').toUpperCase();
                            if(url.pathname==='/api/v1/agent-studio/llm-settings')return new Response(JSON.stringify({configured:true,providerName:'Browser QA · no LLM request',baseUrl:'http://127.0.0.1',hasApiKey:false,model:'fixture',leagueMcpEnabled:false}),{headers:{'Content-Type':'application/json'}});
                            if(url.pathname==='/api/v1/agent-studio/chat')return new Response(JSON.stringify("""
                            + json.dumps(fixture)
                            + """),{headers:{'Content-Type':'application/json'}});
                            if(state.saved && url.pathname==='/api/v1/catalog/downloaded-decks')return new Response(JSON.stringify({detail:'QA refresh unavailable'}),{status:503,headers:{'Content-Type':'application/json'}});
                            if(url.pathname===state.url && method==='PUT'){
                                state.puts++;
                                const body=JSON.parse(init.body);
                                if(!Number.isInteger(body.expectedVersion))throw new Error('The Add shortcut omitted its version guard.');
                                if(!state.injected){
                                    state.injected=true;
                                    const other=await state.original(input,{...init,body:JSON.stringify({expectedVersion:body.expectedVersion,description:'Concurrent QA description retained'})});
                                    state.concurrentStatus=other.status;
                                    if(!other.ok)throw new Error('Isolated concurrent edit failed.');
                                }
                                const response=await state.original(input,init);
                                state.addStatus=response.status;
                                if(response.ok)state.saved=true;
                                return response;
                            }
                            return state.original(input,init);
                        };"""
                        )
                        await evaluate(
                            "document.querySelector('.deck-editor-actions .secondary-action').click()"
                        )
                        deadline = time.monotonic() + 20
                        while not await evaluate(
                            "Boolean(document.querySelector('.assistant-composer textarea'))"
                        ):
                            if time.monotonic() > deadline:
                                raise TimeoutError("The QA card suggestion chat did not open.")
                            await asyncio.sleep(0.1)
                        await evaluate(
                            """(() => {const field=document.querySelector('.assistant-composer textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(field,'Suggest a researched card.');field.dispatchEvent(new Event('input',{bubbles:true}));})()"""
                        )
                        await evaluate(
                            "document.querySelector('.assistant-composer button').click()"
                        )
                        while not await evaluate(
                            "Boolean(document.querySelector('.assistant-drawer .assistant-card-results button:not(:disabled)'))"
                        ):
                            if time.monotonic() > deadline:
                                raise TimeoutError("The suggested card Add button did not appear.")
                            await asyncio.sleep(0.1)
                        await evaluate(
                            "document.querySelector('.assistant-drawer .assistant-card-results button').click()"
                        )
                        while not await evaluate(
                            "document.querySelector('.assistant-drawer .inline-error')?.textContent.includes('Nothing was overwritten')"
                        ):
                            if time.monotonic() > deadline:
                                diagnostic = await evaluate(
                                    "JSON.stringify({puts:window.__chatAddQA.puts,concurrentStatus:window.__chatAddQA.concurrentStatus,addStatus:window.__chatAddQA.addStatus,errors:[...document.querySelectorAll('.assistant-drawer .inline-error')].map(e=>e.textContent)})"
                                )
                                raise TimeoutError(
                                    f"The real stale Add did not show its safe conflict: {diagnostic}"
                                )
                            await asyncio.sleep(0.1)
                        assert await evaluate("window.__chatAddQA.puts===1"), (
                            "The conflicted Add retried automatically."
                        )
                        current = await evaluate("fetch(window.__chatAddQA.url).then(r=>r.json())")
                        assert not any(
                            c["name"] == suggested_name and c["section"] == "main"
                            for c in current["cards"]
                        )
                        assert current["description"] == "Concurrent QA description retained"
                        await evaluate(
                            "document.querySelector('.assistant-drawer .assistant-card-results button').click()"
                        )
                        while not await evaluate(
                            "document.querySelector('.assistant-drawer > .inline-error')?.textContent.includes('Do not add it again')"
                        ):
                            if time.monotonic() > deadline:
                                raise TimeoutError(
                                    "The confirmed Add did not distinguish a refresh failure."
                                )
                            await asyncio.sleep(0.1)
                        current = await evaluate("fetch(window.__chatAddQA.url).then(r=>r.json())")
                        assert (
                            sum(
                                c["quantity"]
                                for c in current["cards"]
                                if c["name"] == suggested_name and c["section"] == "main"
                            )
                            == 1
                        )
                        assert current["description"] == "Concurrent QA description retained"
                        assert await evaluate("window.__chatAddQA.puts===2")
                        for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
                            await command(
                                "Emulation.setDeviceMetricsOverride",
                                {
                                    "width": width,
                                    "height": height,
                                    "deviceScaleFactor": 1,
                                    "mobile": width < 500,
                                },
                            )
                            await asyncio.sleep(0.2)
                            assert not await evaluate(
                                "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                            ), "The saved-card refresh warning overflows."
                            shot = await command(
                                "Page.captureScreenshot",
                                {"format": "png", "captureBeyondViewport": False},
                            )
                            (output / f"chat-add-refresh-{label}.png").write_bytes(
                                base64.b64decode(shot["data"])
                            )
                        await evaluate(
                            "document.querySelector('.assistant-drawer [aria-label=\"Close\"]').click()"
                        )
                    finally:
                        await evaluate(
                            "window.fetch=window.__chatAddQA.original;delete window.__chatAddQA;"
                        )
                    await command(
                        "Emulation.setDeviceMetricsOverride",
                        {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
                    )
                    print(
                        "Chat Add: real version conflict refused without overwrite or auto-retry; explicit retry saved exactly one card and preserved concurrent metadata. Refresh failure clearly labelled saved, not failed. No LLM request.",
                        flush=True,
                    )
                await evaluate(
                    "document.querySelector('.deck-editor-actions .secondary-action').click()"
                )
                deadline = time.monotonic() + 10
                while not await evaluate(
                    "Boolean(document.querySelector('.assistant-setup, .assistant-context'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Assistant settings did not load.")
                    await asyncio.sleep(0.2)
                if await evaluate(
                    "Boolean(document.querySelector('[aria-label=\"Assistant settings\"]'))"
                ):
                    await evaluate(
                        "document.querySelector('[aria-label=\"Assistant settings\"]')?.click()"
                    )
                while not await evaluate(
                    "Boolean(document.querySelector('.assistant-research-settings'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("The optional MCP settings did not render.")
                    await asyncio.sleep(0.1)
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / "assistant-setup.png").write_bytes(base64.b64decode(screenshot["data"]))
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
                )
                assert await evaluate(
                    "document.querySelector('.assistant-drawer').getBoundingClientRect().right <= document.documentElement.clientWidth + 1"
                )
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / "assistant-setup-mobile.png").write_bytes(
                    base64.b64decode(screenshot["data"])
                )
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
                )
                await evaluate(
                    "document.querySelector('.assistant-drawer [aria-label=\"Close\"]').click()"
                )
                if os.environ.get("DEEPDECK_QA_DECK_TO_AGENT") == "1":
                    saved_deck = await evaluate(
                        f"fetch('/api/v1/deck-workshop/decks/{created}').then(r=>r.json())"
                    )
                    agents_before = await evaluate(
                        "fetch('/api/v1/agent-studio/agents').then(r=>r.json()).then(r=>r.items.map(a=>a.id).sort())"
                    )
                    deadline = time.monotonic() + 20
                    while not await evaluate(
                        "Boolean(document.querySelector('.deck-agent-handoff button:not(:disabled)'))"
                    ):
                        if time.monotonic() > deadline:
                            raise TimeoutError("The saved QA deck could not prepare a specialist.")
                        await asyncio.sleep(0.1)
                    for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                            "document.querySelector('.deck-agent-handoff').scrollIntoView({block:'start'})"
                        )
                        await asyncio.sleep(0.2)
                        assert not await evaluate(
                            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                        ), "Deck-to-agent shortcut overflows."
                        shot = await command(
                            "Page.captureScreenshot",
                            {"format": "png", "captureBeyondViewport": False},
                        )
                        (output / f"deck-agent-handoff-{label}.png").write_bytes(
                            base64.b64decode(shot["data"])
                        )
                    await evaluate("document.querySelector('.deck-agent-handoff button').click()")
                    while not await evaluate("Boolean(document.querySelector('.agent-create-v2'))"):
                        if time.monotonic() > deadline:
                            raise TimeoutError("The prepared agent form did not open.")
                        await asyncio.sleep(0.1)
                    deadline = time.monotonic() + 25
                    while not await evaluate(
                        "Boolean(document.querySelector('.agent-create-v2 button[type=submit]:not(:disabled)'))"
                    ):
                        error = await evaluate(
                            "document.querySelector('.agent-create-v2 [role=alert]')?.textContent"
                        )
                        if error:
                            raise AssertionError(error)
                        if time.monotonic() > deadline:
                            raise TimeoutError(
                                "The prepared deck did not finish loading into the agent form."
                            )
                        await asyncio.sleep(0.1)
                    expected_name = (saved_deck["name"] + " pilot")[:64]
                    assert (
                        await evaluate("document.querySelector('.agent-create-v2 input').value")
                        == expected_name
                    )
                    assert (
                        await evaluate("document.querySelector('.agent-create-v2 select').value")
                        == saved_deck["format"]
                    )
                    assert await evaluate(
                        "document.querySelector('.agent-create-v2 .mastered-decks').textContent.includes("
                        + json.dumps(saved_deck["name"])
                        + ")"
                    )
                    assert (
                        await evaluate(
                            "fetch('/api/v1/agent-studio/agents').then(r=>r.json()).then(r=>r.items.map(a=>a.id).sort())"
                        )
                        == agents_before
                    ), "Preparing a form created an agent automatically."
                    for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                            "document.querySelector('.agent-create-v2').scrollIntoView({block:'start'})"
                        )
                        await asyncio.sleep(0.2)
                        assert not await evaluate(
                            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                        ), "Prepared agent form overflows."
                        shot = await command(
                            "Page.captureScreenshot",
                            {"format": "png", "captureBeyondViewport": False},
                        )
                        (output / f"deck-agent-prepared-{label}.png").write_bytes(
                            base64.b64decode(shot["data"])
                        )
                    await evaluate(
                        "window.__handoffConfirm=window.confirm;window.confirm=()=>false;document.querySelectorAll('.player-nav nav button')[0].click()"
                    )
                    assert (
                        await evaluate("document.querySelector('.agent-create-v2 input')?.value")
                        == expected_name
                    ), "Refusing navigation discarded the prepared draft."
                    await evaluate(
                        "window.confirm=()=>true;document.querySelector('.agent-create-v2 header button').click();window.confirm=window.__handoffConfirm;delete window.__handoffConfirm;"
                    )
                    await asyncio.sleep(0.2)
                    await evaluate("document.querySelectorAll('.player-nav nav button')[0].click()")
                    await evaluate("document.querySelectorAll('.player-nav nav button')[2].click()")
                    assert not await evaluate(
                        "Boolean(document.querySelector('.agent-create-v2'))"
                    ), "A consumed deck shortcut reopened a creation draft on a later visit."
                    await command(
                        "Emulation.setDeviceMetricsOverride",
                        {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
                    )
                    print(
                        "Saved deck -> prepared specialist form verified on desktop/mobile, with the correct format/mastered deck, protected draft and one-shot handoff. No agent, playtest or League deployment was created.",
                        flush=True,
                    )
            finally:
                # Remove only the temporary workspace created by this browser test.
                deleted = await evaluate(
                    "(async () => { const session = await (await fetch('/api/v1/session')).json();"
                    f" const response = await fetch('/api/v1/deck-workshop/decks/{created}',"
                    "{method:'DELETE',headers:{'X-DeepDeck-Token':session.token}});"
                    " localStorage.removeItem('deepdeck.activeDeckId'); return response.ok; })()"
                )
                assert deleted, "The temporary QA deck could not be cleaned up."
                await evaluate(
                    "document.querySelector('.deck-editor-header .text-action')?.click()"
                )
            print(
                "Deck editor: researched production cards, saved one in Sideboard, checked assistant settings, then removed the temporary deck.",
                flush=True,
            )
        for index, name in enumerate(
            ("play", "decks", "agents", "league", "competitions", "replays", "insights")
        ):
            await evaluate(f"document.querySelectorAll('.player-nav nav button')[{index}].click()")
            await asyncio.sleep(0.8)
            title = await evaluate("document.querySelector('h1')?.textContent")
            assert title, f"{name}: missing page title"
            assert not await evaluate("document.body.innerText.includes('Something went wrong')"), (
                name
            )
            if name == "league":
                expected_leagues = await evaluate("""(async () => {
                    const account = await (await fetch('/api/v1/account/status')).json();
                    if (!account.valid) return null;
                    const agents = await (await fetch('/api/v1/agent-studio/agents')).json();
                    const selectedId = document.querySelector('.deploy-grid select').value;
                    const agent = agents.items.find(a => a.id === selectedId);
                    const catalog = await (await fetch('/api/v1/catalog/competitions')).json();
                    if (!Array.isArray(catalog.items)) throw new Error('League catalog unavailable');
                    return catalog.items.filter(c => c.status.trim().toLowerCase() === 'active'
                        && (!agent || c.format.trim().toLowerCase() === agent.format.toLowerCase()))
                        .map(c => c.versionId).sort();
                })()""")
                if expected_leagues is not None:
                    deadline = time.monotonic() + 20
                    while True:
                        displayed = await evaluate("""[...document.querySelectorAll('.deploy-grid select')[1].options]
                            .map(o => o.value).filter(Boolean).sort()""")
                        if displayed == expected_leagues:
                            break
                        if time.monotonic() > deadline:
                            raise AssertionError(
                                f"Visible leagues {displayed} differ from server {expected_leagues}"
                            )
                        await asyncio.sleep(0.2)
                    print(
                        f"League catalog: all {len(expected_leagues)} compatible active competitions visible.",
                        flush=True,
                    )
                assert await evaluate("""(() => {
                    const pool = document.querySelector('.league-deck-pool');
                    if (!pool) return true;
                    const checkbox = pool.querySelector('input[type=checkbox]');
                    const label = pool.querySelector('label');
                    const name = pool.querySelector('.deck-badge strong');
                    return getComputedStyle(pool).display === 'block'
                        && (!checkbox || checkbox.getBoundingClientRect().width <= 24)
                        && (!label || label.scrollWidth <= label.clientWidth + 1)
                        && (!name || parseFloat(getComputedStyle(name).fontSize) >= 12);
                })()"""), "League deck choices are clipped or inherit the old training layout."
            if index == 0:
                assert await evaluate(
                    "getComputedStyle(document.querySelector('.game-options .segmented'))"
                    ".position === 'static'"
                )
                await evaluate("document.querySelector('.local-play-settings').open=true")
                assert await evaluate(
                    "document.querySelector('.local-play-settings').innerText.includes('not League time controls')"
                )
                assert await evaluate(
                    "document.querySelector('.local-play-fields input[inputmode=numeric]').placeholder === 'Random each run'"
                )
                assert await evaluate(
                    "document.querySelector('[aria-label=\"Turn limit per game\"]').value === '60'"
                )
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / "play-settings.png").write_bytes(base64.b64decode(screenshot["data"]))
                await evaluate("document.querySelector('.local-play-settings').open=false")
                await evaluate("document.querySelector('.browse-seat-decks').click()")
                await asyncio.sleep(0.3)
                assert await evaluate("Boolean(document.querySelector('dialog[open]'))")
                await evaluate(
                    "document.querySelector('[aria-label=\"Close deck picker\"]').click()"
                )
                if os.environ.get("DEEPDECK_QA_LOCAL_PLAY") == "1":
                    # This mutating browser check is for the isolated standalone
                    # QA profile only, never the developer's/private library.
                    assert APP_URL != "http://127.0.0.1:8765"
                    assert await evaluate(
                        "fetch('/api/v1/agent-studio/agents').then(r=>r.json()).then(r=>r.items.length===1 && r.items[0].name==='Standalone QA pilot')"
                    )
                    before = await evaluate(
                        "fetch('/api/v1/jobs').then(r=>r.json()).then(r=>r.map(j=>j.id))"
                    )
                    await evaluate("""(() => {
                        const select=document.querySelector('.seat-card.human > label select');
                        if(!select || select.closest('label').childNodes[0].textContent.trim() !== 'Deck') throw new Error('The QA deck control is missing');
                        const option=[...select.options].find(option=>option.value && option.value !== 'random');
                        if(!option) throw new Error('The QA player deck is missing');
                        select.value=option.value; select.dispatchEvent(new Event('change',{bubbles:true}));
                    })()""")
                    await evaluate("document.querySelector('.launch-bar button').click()")
                    deadline = time.monotonic() + 45
                    saw_preparation = False
                    while not await evaluate(
                        "Boolean(document.querySelector('.full-game .local-pixi-table canvas'))"
                    ):
                        if not saw_preparation and await evaluate(
                            "Boolean(document.querySelector('.table-launch-page'))"
                        ):
                            saw_preparation = True
                            screenshot = await command(
                                "Page.captureScreenshot",
                                {"format": "png", "captureBeyondViewport": False},
                            )
                            (output / "table-preparation.png").write_bytes(
                                base64.b64decode(screenshot["data"])
                            )
                        if time.monotonic() > deadline:
                            raise TimeoutError(
                                "The real UI-created table did not render its Pixi canvas."
                            )
                        await asyncio.sleep(0.05)
                    assert saw_preparation, "Starting a real table did not show preparation."
                    screenshot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / "local-playtest.png").write_bytes(
                        base64.b64decode(screenshot["data"])
                    )
                    await evaluate("document.querySelector('.local-table-exit').click()")
                    deadline = time.monotonic() + 20
                    while not await evaluate("Boolean(document.querySelector('.player-nav'))"):
                        if time.monotonic() > deadline:
                            raise TimeoutError("Back to lab did not finalize the UI-created table.")
                        await asyncio.sleep(0.1)
                    jobs = await evaluate("fetch('/api/v1/jobs').then(r=>r.json())")
                    created = [job for job in jobs if job["id"] not in before]
                    assert len(created) == 1 and created[0]["status"] == "stopped", created
                    ui_job = created[0]
                    rows = await evaluate(
                        f"fetch('/api/v1/models/{ui_job['model_id']}/replays').then(r=>r.json()).then(r=>r.items)"
                    )
                    partial = next(
                        row
                        for row in rows
                        if row["id"].startswith(f"batch-{ui_job['details']['batchId']}-")
                    )
                    assert partial["gameStatus"] == "interrupted" and partial["winner"] is None, (
                        partial
                    )
                    assert await evaluate(
                        "fetch('/api/v1/statistics/local').then(r=>r.json()).then(r=>r.summary.matches===2 && r.summary.seatResults===4)"
                    )
                    print(
                        "Real player flow: started from Play, preparation shown, Pixi canvas rendered, Back to lab saved an interrupted trace without a fabricated result.",
                        flush=True,
                    )
                await evaluate(
                    "document.querySelectorAll('.game-options .segmented button')[1].click()"
                )
                assert await evaluate("document.querySelectorAll('.seat-card.human').length === 0")
                await evaluate(
                    "document.querySelectorAll('.game-options .segmented button')[0].click()"
                )
            if index == 2 and os.environ.get("DEEPDECK_QA_AGENT_WORKSHOP") == "1":
                deadline = time.monotonic() + 15
                while not await evaluate(
                    "Boolean(document.querySelector('.agent-editor-v2, .agent-create-v2'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("The agent workshop did not finish loading.")
                    await asyncio.sleep(0.2)
                assert await evaluate("""[...document.querySelectorAll('.agent-roster nav > button')]
                    .slice(0,3).every(button => {
                        const label=button.querySelector('span:not(.agent-monogram)');
                        return label.getBoundingClientRect().width > 80
                            && getComputedStyle(label).color === getComputedStyle(button).color
                            && button.getBoundingClientRect().height < 110;
                    })"""), "Agent names inherited the old sidebar's narrow, low-contrast style."
                if await evaluate("Boolean(document.querySelector('.agent-editor-v2'))"):
                    # Exercise unsaved drafts only. Do not mutate any user policy or deck.
                    await evaluate("""(() => {
                        const input=document.querySelector('.agent-editor-basics input');
                        const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
                        set.call(input, input.value+' QA draft');
                        input.dispatchEvent(new Event('input',{bubbles:true}));
                        window.__qaOriginalConfirm=window.confirm;
                        window.__qaConfirmCount=0;
                        window.confirm=() => { window.__qaConfirmCount++; return false; };
                    })()""")
                    await evaluate("document.querySelectorAll('.player-nav nav button')[0].click()")
                    assert await evaluate(
                        "window.__qaConfirmCount === 1 && Boolean(document.querySelector('.agent-editor-v2'))"
                    ), "Leaving an unsaved agent draft was not guarded."
                    assert await evaluate(
                        "[...document.querySelectorAll('.agent-next-actions button')].every(button => button.disabled)"
                    ), "An unsaved strategy could be sent to Play or League."
                    await evaluate("window.confirm=() => true")
                    await evaluate(
                        "[...document.querySelectorAll('.agent-save-bar button')]"
                        ".find(button => button.textContent === 'Discard draft').click()"
                    )
                    await evaluate(
                        "document.querySelector('.agent-patterns > header button').click()"
                    )
                    assert await evaluate("Boolean(document.querySelector('.agent-rule-form'))")
                    await evaluate(
                        "document.querySelector('.agent-rule-form input').scrollIntoView({block:'center'})"
                    )
                    screenshot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / "agent-rule-editor.png").write_bytes(
                        base64.b64decode(screenshot["data"])
                    )
                    await evaluate(
                        "[...document.querySelectorAll('.agent-rule-form button')]"
                        ".find(button => button.textContent === 'Cancel rule').click()"
                    )
                    await evaluate("""(() => {
                        const history=document.querySelector('.agent-history');
                        history.open=true; history.dispatchEvent(new Event('toggle'));
                    })()""")
                    deadline = time.monotonic() + 15
                    while not await evaluate(
                        "Boolean(document.querySelector('.agent-history li'))"
                    ):
                        error = await evaluate(
                            "document.querySelector('.agent-history .inline-error')?.textContent"
                        )
                        if error:
                            raise AssertionError(f"Agent version history failed: {error}")
                        if time.monotonic() > deadline:
                            raise TimeoutError("The agent's real saved versions did not load.")
                        await asyncio.sleep(0.2)
                    await evaluate("document.querySelector('.agent-history').open=false")
                    await evaluate("window.confirm=window.__qaOriginalConfirm")
                await evaluate("document.querySelector('.agent-roster > header button').click()")
                assert await evaluate("Boolean(document.querySelector('.agent-create-v2'))")
                await evaluate(
                    "document.querySelector('.agent-create-v2 .mastered-decks button').click()"
                )
                assert await evaluate(
                    "document.querySelector('dialog[open] h2').textContent === 'Choose a mastered deck'"
                )
                await evaluate(
                    "document.querySelector('[aria-label=\"Close deck picker\"]').click()"
                )
                await evaluate(
                    "document.querySelector('.agent-create-v2').scrollIntoView({block:'start'})"
                )
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / "agent-create.png").write_bytes(base64.b64decode(screenshot["data"]))
                if await evaluate(
                    "Boolean(document.querySelector('.agent-create-v2 > header > button'))"
                ):
                    await evaluate(
                        "document.querySelector('.agent-create-v2 > header > button').click()"
                    )
                await evaluate("window.scrollTo(0,0)")
                print(
                    "Agent workshop: readable roster, unsaved-draft guards, rule editor, real version history and mastered-deck picker verified without saving user data.",
                    flush=True,
                )
            if index == 1:
                deadline = time.monotonic() + 25
                while await evaluate(
                    "Boolean(document.querySelector('.workshop-deck-card'))"
                ) and not await evaluate(
                    "[...document.querySelectorAll('.workshop-deck-card .deck-artwork img')].some(image => image.complete && image.naturalWidth > 0)"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("No actual deck cover image loaded.")
                    await asyncio.sleep(0.3)
                await evaluate("document.fonts.ready")
                await evaluate("document.fonts.load('17px Mana')")
                assert await evaluate("document.fonts.check('17px Mana')"), (
                    "The bundled mana-symbol font did not load."
                )
            if name == "replays":
                await evaluate(
                    "document.querySelectorAll('.replay-toolbar .segmented button')[2].click()"
                )
                deadline = time.monotonic() + 30
                while await evaluate(
                    "Boolean(document.querySelector('.replay-library .list-loading'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("The replay library did not finish loading.")
                    await asyncio.sleep(0.3)
                gallery = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / "replay-library.png").write_bytes(base64.b64decode(gallery["data"]))
                if await evaluate("Boolean(document.querySelector('.replay-watch'))"):
                    await evaluate("""(() => {
                        const complete = document.querySelector('.replay-match-card[data-status="completed"] .replay-watch, .replay-match-card[data-status="complete"] .replay-watch');
                        (complete || document.querySelector('.replay-watch')).click();
                    })()""")
                    while not await evaluate(
                        "Boolean(document.querySelector('.replay-theater[open] .pixi-replay canvas'))"
                    ):
                        if time.monotonic() > deadline:
                            raise TimeoutError("The replay theater did not render its Pixi table.")
                        await asyncio.sleep(0.3)
                    assert await evaluate("""(() => {
                        const box=document.querySelector('.replay-theater').getBoundingClientRect();
                        return box.width >= innerWidth - 2 && box.height >= innerHeight - 2;
                    })()"""), "The replay does not occupy the whole window."
                    if os.environ.get("DEEPDECK_QA_REPLAY_PLAYBACK") == "1":
                        await evaluate("""(() => {
                            const select=document.querySelector('[aria-label="Replay speed"]');
                            const setter=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;
                            setter.call(select,'4'); select.dispatchEvent(new Event('change',{bubbles:true}));
                            const play=[...document.querySelectorAll('.pixi-playback-controls button')].find(b=>b.textContent==='Play');
                            play?.click();
                        })()""")
                        maximum = await evaluate(
                            """Number(document.querySelector('[aria-label="Replay frame"]').max)"""
                        )
                        playback_deadline = time.monotonic() + float(maximum) * 0.175 + 45
                        while not await evaluate(
                            """document.querySelector('[aria-label="Replay frame"]').value === document.querySelector('[aria-label="Replay frame"]').max"""
                        ):
                            if time.monotonic() > playback_deadline:
                                raise TimeoutError("Playback did not reach the last saved frame.")
                            await asyncio.sleep(0.4)
                        print(
                            f"Replay: all {int(maximum) + 1} frames played to the end without library-refresh reset.",
                            flush=True,
                        )
                    else:
                        await evaluate(
                            """document.querySelector('[aria-label="Last frame"]').click()"""
                        )
                    assert await evaluate(
                        "document.querySelector('.pixi-game-controls').getBoundingClientRect().bottom <= innerHeight"
                    ), "Replay controls are below the desktop viewport."
                    screenshot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / "replay-theater.png").write_bytes(
                        base64.b64decode(screenshot["data"])
                    )
                    print(
                        "Replay final caption: "
                        + str(
                            await evaluate(
                                "document.querySelector('.pixi-replay-caption').innerText"
                            )
                        ),
                        flush=True,
                    )
                    await evaluate(
                        "document.querySelector('.replay-theater-header > button').click()"
                    )
            screenshot = await command(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
            )
            (output / f"{name}.png").write_bytes(base64.b64decode(screenshot["data"]))
            print(f"{name}: {title}", flush=True)
        await command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        for index, name in enumerate(
            ("play", "decks", "agents", "league", "competitions", "replays", "insights")
        ):
            await evaluate(f"document.querySelectorAll('.player-nav nav button')[{index}].click()")
            await asyncio.sleep(0.5)
            if index == 0:
                await evaluate("document.querySelector('.local-play-settings').open=true")
                assert await evaluate(
                    "getComputedStyle(document.querySelector('.local-play-fields')).gridTemplateColumns.trim().split(/\\s+/).length === 1"
                ), "Mobile playtest settings are not stacked."
                await evaluate(
                    "document.querySelector('.local-play-settings').scrollIntoView({block:'start'})"
                )
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / "play-settings-mobile.png").write_bytes(
                    base64.b64decode(screenshot["data"])
                )
            overflow = await evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
            )
            if overflow:
                raise AssertionError(f"The mobile {name} page overflows horizontally.")
            if index == 2 and os.environ.get("DEEPDECK_QA_AGENT_WORKSHOP") == "1":
                deadline = time.monotonic() + 15
                while not await evaluate(
                    "Boolean(document.querySelector('.agent-editor-v2, .agent-create-v2'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("The mobile agent workshop did not finish loading.")
                    await asyncio.sleep(0.2)
                assert await evaluate("""[...document.querySelectorAll('.agent-roster nav > button')]
                    .slice(0,3).every(button => {
                        const label=button.querySelector('span:not(.agent-monogram)');
                        return label.getBoundingClientRect().width > 100
                            && getComputedStyle(label).color === getComputedStyle(button).color
                            && button.getBoundingClientRect().height < 110;
                    })"""), "The mobile agent roster is not readable."
                if await evaluate("Boolean(document.querySelector('.agent-editor-v2'))"):
                    await evaluate(
                        "document.querySelector('.agent-patterns > header button').click()"
                    )
                    await evaluate(
                        "document.querySelector('.agent-rule-form').scrollIntoView({block:'start'})"
                    )
                    assert not await evaluate(
                        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                    ), "The mobile card/tag rule editor overflows."
                    screenshot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / "agent-rule-editor-mobile.png").write_bytes(
                        base64.b64decode(screenshot["data"])
                    )
                    await evaluate(
                        "[...document.querySelectorAll('.agent-rule-form button')]"
                        ".find(button => button.textContent === 'Cancel rule').click()"
                    )
                    await evaluate(
                        "[...document.querySelectorAll('.agent-custom-nodes button')].find(b => b.textContent === 'Refill my hand').click()"
                    )
                    assert await evaluate(
                        "Boolean(document.querySelector('.agent-custom-nodes form'))"
                    )
                    assert await evaluate(
                        "[...document.querySelectorAll('.agent-save-bar button')].find(b => b.textContent === 'Save strategy').disabled"
                    ), "An unfinished node must not be silently saved."
                    for node_width, node_height, node_label in (
                        (1440, 1000, "desktop"),
                        (390, 844, "mobile"),
                    ):
                        await command(
                            "Emulation.setDeviceMetricsOverride",
                            {
                                "width": node_width,
                                "height": node_height,
                                "deviceScaleFactor": 1,
                                "mobile": node_width < 500,
                            },
                        )
                        await evaluate(
                            "document.querySelector('.agent-custom-nodes form').scrollIntoView({block:'start'})"
                        )
                        await asyncio.sleep(0.2)
                        assert not await evaluate(
                            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                        ), "Custom node editor overflows."
                        screenshot = await command(
                            "Page.captureScreenshot",
                            {"format": "png", "captureBeyondViewport": False},
                        )
                        (output / f"agent-decision-node-{node_label}.png").write_bytes(
                            base64.b64decode(screenshot["data"])
                        )
                    await evaluate(
                        "[...document.querySelectorAll('.agent-custom-nodes button')].find(b => b.textContent === 'Apply node to draft').click()"
                    )
                    assert await evaluate(
                        "Boolean(document.querySelector('.decision-node-flow'))"
                    ), "The custom graph preview is missing."
                    for node_width, node_height, node_label in (
                        (1440, 1000, "desktop"),
                        (390, 844, "mobile"),
                    ):
                        await command(
                            "Emulation.setDeviceMetricsOverride",
                            {
                                "width": node_width,
                                "height": node_height,
                                "deviceScaleFactor": 1,
                                "mobile": node_width < 500,
                            },
                        )
                        await evaluate(
                            "document.querySelector('.agent-custom-nodes').scrollIntoView({block:'start'})"
                        )
                        await asyncio.sleep(0.2)
                        assert not await evaluate(
                            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                        ), "Custom graph preview overflows."
                        screenshot = await command(
                            "Page.captureScreenshot",
                            {"format": "png", "captureBeyondViewport": False},
                        )
                        (output / f"agent-custom-graph-{node_label}.png").write_bytes(
                            base64.b64decode(screenshot["data"])
                        )
                    # Discard only the unsaved draft created in this fresh QA
                    # browser. Do not save or change a user's policy revision.
                    await evaluate(
                        "(() => {const confirm=window.confirm;window.confirm=()=>true;try{[...document.querySelectorAll('.agent-save-bar button')].find(b=>b.textContent==='Discard draft').click();}finally{window.confirm=confirm;}})()"
                    )
                    print(
                        "Custom node form, input -> node -> score preview, mobile layout and unsaved-draft guards verified without saving a user policy.",
                        flush=True,
                    )
                    await evaluate(
                        "document.querySelector('.agent-editor-v2').scrollIntoView({block:'start'})"
                    )
                    screenshot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / "agent-strategy-mobile.png").write_bytes(
                        base64.b64decode(screenshot["data"])
                    )
                    await evaluate("window.scrollTo(0,0)")
            screenshot = await command(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
            )
            (output / f"{name}-mobile.png").write_bytes(base64.b64decode(screenshot["data"]))
        await evaluate("document.querySelector('.avatar-button').click()")
        await asyncio.sleep(0.3)
        assert not await evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
        )
        assert await evaluate(
            "(() => {const drawer=document.querySelector('.settings-drawer');"
            " const header=drawer.querySelector('header');"
            " return Math.abs(header.getBoundingClientRect().width-drawer.clientWidth)<2"
            " && drawer.getBoundingClientRect().right<=document.documentElement.clientWidth+1"
            " && drawer.getBoundingClientRect().left>=0"
            " && getComputedStyle(header).flexDirection==='row';})()"
        ), "The mobile Settings header must fill the drawer width."
        screenshot = await command(
            "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
        )
        (output / "settings-mobile.png").write_bytes(base64.b64decode(screenshot["data"]))
        await evaluate("document.querySelector('[aria-label=\"Close settings\"]').click()")
        if os.environ.get("DEEPDECK_QA_OPEN_TABLE") == "1":
            await command(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
            )
            await evaluate("document.querySelectorAll('.player-nav nav button')[0].click()")
            await asyncio.sleep(1)
            opened = await evaluate(
                "(() => { const button = [...document.querySelectorAll('.active-strip button')]"
                ".find(x => x.textContent === 'Open table'); button?.click();"
                " return Boolean(button); })()"
            )
            assert opened, "No active table could be opened for the interactive check."
            deadline = time.monotonic() + 30
            while not await evaluate("Boolean(document.querySelector('.local-pixi-table canvas'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError("The live interactive table did not render.")
                await asyncio.sleep(0.3)
            session = await evaluate(
                "(async () => { const data = await (await fetch('/api/v1/jobs')).json();"
                " const job = (Array.isArray(data) ? data : data.items)"
                ".find(x => x.kind === 'playtest.table'"
                " && x.status === 'running');"
                " return job?.details; })()"
            )
            assert isinstance(session, dict) and session.get("sessionId")
            view_url = f"{session['engineUrl']}/game/sessions/{session['sessionId']}"
            before = await evaluate(f"fetch({json.dumps(view_url)}).then(x => x.json())")
            assert isinstance(before, dict)
            kept = await evaluate(
                "(() => { const button = [...document.querySelectorAll('button')]"
                ".find(x => x.textContent === 'Keep opening hand'); button?.click();"
                " return Boolean(button); })()"
            )
            assert kept, "The opening-hand decision was not available."
            deadline = time.monotonic() + 15
            while True:
                after = await evaluate(f"fetch({json.dumps(view_url)}).then(x => x.json())")
                if isinstance(after, dict) and after.get("revision", 0) > before.get("revision", 0):
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "The opening-hand action did not advance the Engine session."
                    )
                await asyncio.sleep(0.3)
            print("Interactive Commander: opening-hand action accepted by Engine.", flush=True)
            screenshot = await command(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
            )
            (output / "commander-live.png").write_bytes(base64.b64decode(screenshot["data"]))
            await evaluate("document.querySelector('.local-table-exit').click()")
        if os.environ.get("DEEPDECK_QA_STRATEGY_GRAPH") == "1":
            await command(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
            )
            await command("Page.navigate", {"url": APP_URL + "/assets/strategy-graphs.html"})
            deadline = time.monotonic() + 15
            while not await evaluate("Boolean(document.querySelector('#weight-rows tr'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError("The standalone HTML graph did not start.")
                await asyncio.sleep(0.2)
            for graph_tab in ("overview", "decisions", "weights", "protection"):
                await evaluate(f"document.querySelector('[data-tab=\"{graph_tab}\"]').click()")
                await asyncio.sleep(0.15)
                assert await evaluate("document.querySelectorAll('#weight-rows tr').length === 16")
                assert not await evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                ), f"Desktop graph {graph_tab} overflows."
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"strategy-{graph_tab}.png").write_bytes(
                    base64.b64decode(screenshot["data"])
                )
            for model in ("mulligan", "priority", "attack", "block", "selection"):
                await evaluate(f"document.querySelector('[data-model=\"{model}\"]').click()")
                assert await evaluate("document.querySelectorAll('.decision-step').length === 4")
            await evaluate("document.querySelector('[data-scenario=\"counterwar\"]').click()")
            assert await evaluate(
                "document.querySelector('.scenario-result strong').textContent.includes('réponse adverse')"
            )
            await command(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
            )
            for graph_tab in ("overview", "decisions", "weights", "protection"):
                await evaluate(f"document.querySelector('[data-tab=\"{graph_tab}\"]').click()")
                await asyncio.sleep(0.15)
                assert not await evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                ), f"Mobile graph {graph_tab} overflows."
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"strategy-{graph_tab}-mobile.png").write_bytes(
                    base64.b64decode(screenshot["data"])
                )
            print(
                "Standalone strategy graphs: 4 tabs, 5 decision graphs, 16 weights and stack scenarios verified on desktop/mobile.",
                flush=True,
            )
        if os.environ.get("DEEPDECK_QA_PLAY_PREPARATION") == "1":
            await evaluate("document.querySelectorAll('.player-nav nav button')[0].click()")
            deadline = time.monotonic() + 20
            while not await evaluate(
                'Boolean(document.querySelector(\'.seat-card.human > label select option[value]:not([value=""]):not([value="random"])\'))'
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("No local human deck is available for preparation QA.")
                await asyncio.sleep(0.1)
            human_deck = await evaluate("""(() => {const select=document.querySelector('.seat-card.human > label select');
                const option=[...select.options].find(o=>o.value && o.value!=='random');
                select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));return option.value;})()""")

            async def play_field(label: str, value: str) -> None:
                await evaluate(
                    """(() => {const label=[...document.querySelectorAll('.local-play-fields label')].find(l=>l.firstChild.textContent.startsWith("""
                    + json.dumps(label)
                    + """));
                    const field=label.querySelector('input,select');const value="""
                    + json.dumps(value)
                    + """;
                    if(field.tagName==='INPUT')Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,value);else field.value=value;
                    field.dispatchEvent(new Event(field.tagName==='INPUT'?'input':'change',{bubbles:true}));})()"""
                )
                await asyncio.sleep(0.1)

            async def play_mode(name: str) -> None:
                await evaluate(
                    "[...document.querySelectorAll('.game-options button')].find(b=>b.querySelector('strong')?.textContent==="
                    + json.dumps(name)
                    + ").click()"
                )
                await asyncio.sleep(0.2)

            await play_field("Shuffle seed", "77")
            await play_field("Human inactivity limit", "180")
            await play_mode("AI batch")
            await evaluate(
                "const format=document.querySelector('.game-options select');format.value='commander';format.dispatchEvent(new Event('change',{bubbles:true}));"
            )
            await play_field("Shuffle seed", "99")
            await play_field("Turn limit per game", "100")
            await play_field("Match safety limit", "180")
            await evaluate(
                """(() => {const field=document.querySelector('.game-options input[type=number]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,'7');field.dispatchEvent(new Event('input',{bubbles:true}));})()"""
            )
            await play_mode("Play a game")
            assert await evaluate(
                "document.querySelector('.game-options select').value==='legacy' && document.querySelectorAll('.seat-card.human').length===1"
            )
            assert await evaluate(
                "document.querySelector('.seat-card.human > label select').value==="
                + json.dumps(human_deck)
            )
            assert await evaluate(
                "document.querySelector('.local-play-fields input[inputmode=numeric]').value==='77'"
            )
            assert await evaluate(
                "[...document.querySelectorAll('.local-play-fields label')].find(l=>l.firstChild.textContent.startsWith('Match safety limit')).querySelector('select').value===''"
            )
            assert await evaluate(
                "[...document.querySelectorAll('.local-play-fields label')].find(l=>l.firstChild.textContent.startsWith('Human inactivity limit')).querySelector('select').value==='180'"
            )
            await evaluate("document.querySelectorAll('.player-nav nav button')[1].click()")
            await asyncio.sleep(0.3)
            await evaluate("document.querySelectorAll('.player-nav nav button')[0].click()")
            await asyncio.sleep(0.3)
            assert await evaluate(
                "document.querySelector('.seat-card.human > label select').value==="
                + json.dumps(human_deck)
            ), "Visiting Decks lost the prepared human deck."
            assert await evaluate(
                "document.querySelector('.local-play-fields input[inputmode=numeric]').value==='77'"
            ), "Visiting Decks reset the table seed."
            await evaluate("document.querySelectorAll('.player-nav nav button')[6].click()")
            deadline = time.monotonic() + 20
            while not await evaluate(
                "[...document.querySelectorAll('button')].some(b=>b.textContent==='Set up a 20-match batch')"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("Insights experiment shortcut did not appear.")
                await asyncio.sleep(0.1)
            await evaluate(
                "[...document.querySelectorAll('button')].find(b=>b.textContent==='Set up a 20-match batch').click()"
            )
            await asyncio.sleep(0.3)
            assert await evaluate(
                "document.querySelector('.game-options select').value==='commander' && document.querySelector('.game-options input[type=number]').value==='20'"
            ), "Insights did not apply its explicit twenty-match request to the remembered batch."
            assert await evaluate(
                "document.querySelector('.local-play-fields input[inputmode=numeric]').value==='99'"
            ), "Insights reset the batch settings."
            await evaluate(
                """(() => {const field=document.querySelector('.game-options input[type=number]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,'7');field.dispatchEvent(new Event('input',{bubbles:true}));})()"""
            )
            await play_mode("Play a game")
            assert await evaluate(
                "document.querySelector('.seat-card.human > label select').value==="
                + json.dumps(human_deck)
            )
            for name in ("table", "batch"):
                if name == "batch":
                    await play_mode("AI batch")
                    assert await evaluate(
                        "document.querySelector('.game-options select').value==='commander' && document.querySelectorAll('.seat-card.agent').length===4 && !document.querySelector('.seat-card.human')"
                    )
                    assert await evaluate(
                        "document.querySelector('.game-options input[type=number]').value==='7' && document.querySelector('.local-play-fields input[inputmode=numeric]').value==='99'"
                    )
                for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                        "document.querySelector('.game-options').scrollIntoView({block:'start'})"
                    )
                    await asyncio.sleep(0.2)
                    assert not await evaluate(
                        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                    ), "Play preparation overflows."
                    assert await evaluate("""[...document.querySelectorAll('.seat-card')].every(seat=>{
                        const avatar=seat.querySelector('.seat-avatar').getBoundingClientRect();
                        return [...seat.querySelectorAll(':scope > label,:scope > .selection-filters')].every(field=>field.getBoundingClientRect().left>=avatar.right);
                    })"""), "Seat controls were placed in the narrow avatar column."
                    shot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"play-preparation-{name}-{label}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
            print(
                "Table/batch and Decks/Insights round trips preserve human deck, independent formats, seeds, timers and batch size. Insights explicitly prepares twenty matches without resetting the batch settings. Desktop/mobile checked; no matches launched.",
                flush=True,
            )
        if os.environ.get("DEEPDECK_QA_PLAY_POOLS") == "1":
            from deepdeck_learner.entity_selection import resolve_seats

            assert APP_URL == "http://127.0.0.1:8765", (
                "Root-library pool QA targets the root app only."
            )
            resolved = resolve_seats(
                ROOT, [{"kind": "agent", "agent_id": "random", "deck_id": "random"}], "legacy", 0
            )[0]
            candidates = resolved["_agent_candidates"]
            expected_agents = len(candidates)
            expected_decks = len(
                {deck for candidate in candidates for deck in candidate["deck_ids"]}
            )
            assert expected_agents > 0
            expected_summary = (
                f"{expected_agents} playable agent"
                + ("s" if expected_agents != 1 else "")
                + f" · {expected_decks} available deck"
                + ("s" if expected_decks != 1 else "")
            )
            await evaluate("document.querySelectorAll('.player-nav nav button')[0].click()")
            await asyncio.sleep(0.3)
            await evaluate(
                "[...document.querySelectorAll('.game-options button')].find(b=>b.querySelector('strong')?.textContent==='Play a game').click()"
            )
            await evaluate(
                """(() => {const select=document.querySelector('.game-options select');select.value='legacy';select.dispatchEvent(new Event('change',{bubbles:true}));})()"""
            )
            await asyncio.sleep(0.3)
            await evaluate(
                """(() => {const human=document.querySelector('.seat-card.human > label select');const option=[...human.options].find(o=>o.value && o.value!=='random');human.value=option.value;human.dispatchEvent(new Event('change',{bubbles:true}));})()"""
            )
            await evaluate(
                """(() => {const agent=[...document.querySelectorAll('.seat-card.agent > label')].find(l=>l.firstChild.textContent==='Agent').querySelector('select');agent.value='random';agent.dispatchEvent(new Event('change',{bubbles:true}));})()"""
            )
            deadline = time.monotonic() + 20
            while not await evaluate(
                "document.querySelector('.seat-card.agent .seat-pool-preview > summary')?.textContent==="
                + json.dumps(expected_summary)
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "The random preview did not match the actual local controller candidates."
                    )
                await asyncio.sleep(0.1)
            await evaluate(
                "document.querySelector('.seat-card.agent .seat-pool-preview').open=true"
            )
            for state in ("ready", "empty"):
                if state == "empty":
                    # Simulate a remembered label that was deleted elsewhere,
                    # only in this disposable browser; never edit a real agent.
                    await evaluate(
                        """(() => {const label=[...document.querySelectorAll('.seat-card.agent .selection-filters label')].find(l=>l.textContent.trim().startsWith('Agent tag'));const select=label.querySelector('select');select.add(new Option('QA deleted filter','qa-deleted-filter'));select.value='qa-deleted-filter';select.dispatchEvent(new Event('change',{bubbles:true}));})()"""
                    )
                    await asyncio.sleep(0.3)
                    assert await evaluate(
                        "document.querySelector('.seat-card.agent .seat-pool-preview').open && document.querySelector('.launch-button').disabled"
                    ), "An empty remembered pool was launchable."
                    assert await evaluate(
                        "document.querySelector('.seat-card.agent .seat-pool-preview').innerText.includes('not widened automatically')"
                    )
                for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                        "document.querySelector('.seat-card.agent .seat-pool-preview').scrollIntoView({block:'start'})"
                    )
                    await asyncio.sleep(0.2)
                    assert not await evaluate(
                        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                    ), "The pool preview overflows."
                    assert await evaluate(
                        "[...document.querySelectorAll('.seat-card.human select')].every(field=>field.getBoundingClientRect().height<60)"
                    ), "Expanding the AI preview stretched the other seat controls."
                    shot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"play-pool-{state}-{label}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
            await evaluate(
                "[...document.querySelectorAll('.seat-card.agent .seat-pool-preview button')].find(b=>b.textContent==='Clear selection filters').click()"
            )
            await asyncio.sleep(0.3)
            assert await evaluate(
                "document.querySelector('.seat-card.agent .seat-pool-preview > summary').textContent==="
                + json.dumps(expected_summary)
            )
            assert await evaluate("!document.querySelector('.launch-button').disabled"), (
                "Explicitly clearing the stale filter did not restore the valid pool."
            )
            print(
                f"Random pool preview agrees with the actual controller: {expected_agents} agents / {expected_decks} unique decks. Missing-filter case and explicit recovery checked on desktop/mobile; no agents edited or matches launched.",
                flush=True,
            )
        if os.environ.get("DEEPDECK_QA_DECK_INSPECTION") == "1":
            # Exercise the actual SDK read tool against a local mastered deck.
            # Only the model's response and settings are substituted in this
            # disposable browser: never send paid inference or save credentials.
            from deepdeck_learner.llm_gateway import _sdk_tools

            with urlopen(f"{APP_URL}/api/v1/agent-studio/agents", timeout=10) as response:
                pilots = json.load(response)["items"]
            pilot = next(p for p in pilots if p.get("deckVersionIds"))
            events: list[dict] = []

            def no_launch(_):
                raise AssertionError("Read-only inspection must not launch a match.")

            tool = next(
                t for t in _sdk_tools(ROOT, no_launch, events, "agent") if t.name == "inspect_deck"
            )
            event = json.loads(
                await tool.on_invoke_tool(
                    None,
                    json.dumps(
                        {
                            "agent_id": pilot["id"],
                            "deck_version_id": pilot["deckVersionIds"][0],
                            "limit": 8,
                        }
                    ),
                )
            )
            assert event["ok"] and event["result"]["readOnly"]
            fixture = {
                "message": "Read-only tool result. No strategy was changed.",
                "toolEvents": events,
            }
            await command("Page.navigate", {"url": APP_URL})
            deadline = time.monotonic() + 30
            while not await evaluate("Boolean(document.querySelector('.player-nav nav button'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError("The app did not reopen for inspection QA.")
                await asyncio.sleep(0.2)
            await evaluate("document.querySelectorAll('.player-nav nav button')[2].click()")
            await asyncio.sleep(0.3)
            await evaluate(
                """window.__inspectionOriginalFetch = window.fetch;
                window.fetch = async (input, init) => {
                  const url = new URL(typeof input === 'string' ? input : input.url, location.href);
                  if (url.pathname === '/api/v1/agent-studio/llm-settings') return new Response(JSON.stringify({
                    configured:true, providerName:'Read-only browser verification', baseUrl:'http://127.0.0.1', model:'fixture', leagueMcpEnabled:false
                  }), {headers:{'Content-Type':'application/json'}});
                  if (url.pathname === '/api/v1/agent-studio/chat') return new Response(JSON.stringify("""
                + json.dumps(fixture)
                + """), {headers:{'Content-Type':'application/json'}});
                  if (!['GET','HEAD'].includes((init?.method || input.method || 'GET').toUpperCase())) throw new Error('No mutations permitted in inspection QA.');
                  return window.__inspectionOriginalFetch(input, init);
                };"""
            )
            await evaluate("document.querySelector('.assistant-fab').click()")
            while not await evaluate(
                "Boolean(document.querySelector('.assistant-composer textarea'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("The read-only assistant fixture did not open.")
                await asyncio.sleep(0.2)
            await evaluate("""(() => {const input=document.querySelector('.assistant-composer textarea');
                Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'Inspect the mastered deck without changing it.');
                input.dispatchEvent(new Event('input',{bubbles:true}));})()""")
            await evaluate("document.querySelector('.assistant-composer button').click()")
            while not await evaluate(
                "Boolean(document.querySelector('.assistant-deck-inspection'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("The actual inspection tool receipt did not render.")
                await asyncio.sleep(0.2)
            for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                    "document.querySelector('.assistant-deck-inspection').scrollIntoView({block:'start'})"
                )
                await asyncio.sleep(0.3)
                assert not await evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                ), "Inspection overflows."
                assert await evaluate(
                    "document.querySelector('.assistant-deck-inspection').innerText.includes('do not change how the agent plays')"
                )
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"deck-inspection-{label}.png").write_bytes(
                    base64.b64decode(screenshot["data"])
                )
            await evaluate(
                "document.querySelector('.assistant-drawer [aria-label=\"Close\"]').click(); window.fetch=window.__inspectionOriginalFetch; delete window.__inspectionOriginalFetch;"
            )
            print(
                "Actual SDK deck inspection rendered in the copilot on desktop/mobile. Model response fixture only; no paid inference, settings save or strategy mutation.",
                flush=True,
            )
        if os.environ.get("DEEPDECK_QA_LLM_CONNECTIONS") == "1":
            await command("Page.navigate", {"url": APP_URL})
            deadline = time.monotonic() + 30
            while not await evaluate("Boolean(document.querySelector('.player-nav nav button'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError("The app did not reopen for assistant QA.")
                await asyncio.sleep(0.2)
            await evaluate("document.querySelectorAll('.player-nav nav button')[2].click()")
            await asyncio.sleep(0.3)
            await evaluate("document.querySelector('.assistant-fab').click()")
            while not await evaluate("Boolean(document.querySelector('.assistant-setup select'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError("Assistant connections did not load.")
                await asyncio.sleep(0.2)
            count = await evaluate(
                "document.querySelector('.assistant-setup select').options.length"
            )
            assert count >= 12, f"Missing provider presets: {count}"
            for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": 1,
                        "mobile": width < 500,
                    },
                )
                for provider in ("anthropic", "chatgpt"):
                    await evaluate(
                        f"(() => {{const select=document.querySelector('.assistant-setup select'); select.value='{provider}'; select.dispatchEvent(new Event('change',{{bubbles:true}}));}})()"
                    )
                    await asyncio.sleep(0.35)
                    assert not await evaluate(
                        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                    ), "Assistant setup overflows horizontally."
                    assert await evaluate(
                        "(() => {const form=document.querySelector('.assistant-setup');return form.clientHeight > 0 && form.getBoundingClientRect().bottom <= innerHeight + 1;})()"
                    ), "Assistant setup cannot scroll within the window."
                    if provider == "chatgpt":
                        assert await evaluate(
                            "Boolean(document.querySelector('.chatgpt-connection'))"
                        )
                        assert not await evaluate(
                            "Boolean(document.querySelector('.assistant-setup input[type=password]'))"
                        )
                    screenshot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"llm-{provider}-{label}.png").write_bytes(
                        base64.b64decode(screenshot["data"])
                    )
            print(
                f"LLM setup: {count} presets; Claude API and ChatGPT consent screens verified on desktop/mobile. No sign-in, save or inference was triggered.",
                flush=True,
            )
        if os.environ.get("DEEPDECK_QA_INSIGHTS") == "1":
            await evaluate("document.querySelectorAll('.player-nav nav button')[6].click()")
            deadline = time.monotonic() + 30
            while not await evaluate(
                "Boolean(document.querySelector('.tracking-config select option[value=\"programmatic-e71bb931c368\"]'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("Advanced tracking targets did not load.")
                await asyncio.sleep(0.2)
            await evaluate(
                "(() => {const select=document.querySelector('.tracking-config select'); select.value='programmatic-e71bb931c368'; select.dispatchEvent(new Event('change',{bubbles:true}));})()"
            )
            while not await evaluate("Boolean(document.querySelector('.insight-curve svg'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "No real advanced numeric samples were available for this pilot."
                    )
                await asyncio.sleep(0.2)
            assert await evaluate("document.querySelectorAll('.tracking-group input').length === 6")
            for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": 1,
                        "mobile": width < 500,
                    },
                )
                await asyncio.sleep(0.3)
                for target in ("advanced-insights", "advanced-evidence"):
                    await evaluate(
                        f"document.querySelector('.{target}').scrollIntoView({{block:'start'}})"
                    )
                    await asyncio.sleep(0.2)
                    assert not await evaluate(
                        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                    ), "Advanced Insights overflows."
                    screenshot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"insights-{target}-{label}.png").write_bytes(
                        base64.b64decode(screenshot["data"])
                    )
            print(
                "Real advanced statistics, numeric turn curve, six opt-in groups and separate disk budget verified on desktop/mobile. No settings were changed by browser QA.",
                flush=True,
            )
        if os.environ.get("DEEPDECK_QA_INSIGHTS_RECOVERY") == "1":
            await evaluate("document.querySelectorAll('.player-nav nav button')[0].click()")
            await asyncio.sleep(0.2)
            await evaluate("""(() => {
                const state=window.__insightsRecoveryQA={original:window.fetch.bind(window),fail:true};
                window.fetch=async(input,init)=>{
                    const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
                    const method=(init?.method||input.method||'GET').toUpperCase();
                    if(!['GET','HEAD'].includes(method))throw new Error('Unexpected mutation blocked by Insights recovery QA.');
                    if(state.fail && path==='/api/v1/statistics/local')return new Response(JSON.stringify({detail:'Browser QA statistics read unavailable'}),{status:503,headers:{'Content-Type':'application/json'}});
                    return state.original(input,init);
                };
            })();""")
            try:
                await evaluate("document.querySelectorAll('.player-nav nav button')[6].click()")
                for state in ("initial", "refresh"):
                    if state == "refresh":
                        await evaluate(
                            "window.__insightsRecoveryQA.fail=false;document.querySelector('.insights-refresh button').click()"
                        )
                        deadline = time.monotonic() + 25
                        while not await evaluate(
                            "Boolean(document.querySelector('.performance-table tbody tr'))"
                        ):
                            if time.monotonic() > deadline:
                                raise TimeoutError("Actual local statistics did not load on retry.")
                            await asyncio.sleep(0.1)
                        names = await evaluate(
                            "[...document.querySelectorAll('.performance-table tbody tr td:first-child strong')].map(e=>e.textContent)"
                        )
                        await evaluate(
                            "window.__insightsRecoveryQA.fail=true;document.querySelector('.insights-refresh button').click()"
                        )
                    deadline = time.monotonic() + 25
                    while not await evaluate(
                        "document.querySelector('.insights-hub > [role=alert]')?.textContent.includes('Browser QA statistics read unavailable')"
                    ):
                        if time.monotonic() > deadline:
                            raise TimeoutError("Insights read failure was not explained.")
                        await asyncio.sleep(0.1)
                    assert await evaluate(
                        "document.querySelector('.insights-refresh button').textContent==='Retry results'"
                    )
                    if state == "initial":
                        assert await evaluate(
                            "document.querySelector('.performance-table h3')?.textContent==='Local results unavailable'"
                        )
                        assert await evaluate(
                            "[...document.querySelectorAll('.insight-kpis strong')].every(e=>e.textContent==='—')"
                        ), "Unknown statistics were shown as zero."
                        assert not await evaluate(
                            "Boolean(document.querySelector('.performance-table .dd-empty button'))"
                        ), "Read failure suggested replacing missing results with a new batch."
                    else:
                        assert (
                            await evaluate(
                                "[...document.querySelectorAll('.performance-table tbody tr td:first-child strong')].map(e=>e.textContent)"
                            )
                            == names
                        ), "Refresh failure removed the last loaded comparison."
                    await evaluate("document.querySelector('.insights-guide').open=false")
                    for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                            "document.querySelector('.insights-refresh').scrollIntoView({block:'start'})"
                        )
                        await asyncio.sleep(0.2)
                        assert not await evaluate(
                            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                        ), "Insights recovery overflows."
                        shot = await command(
                            "Page.captureScreenshot",
                            {"format": "png", "captureBeyondViewport": False},
                        )
                        (output / f"insights-recovery-{state}-{label}.png").write_bytes(
                            base64.b64decode(shot["data"])
                        )
                await evaluate(
                    "window.__insightsRecoveryQA.fail=false;document.querySelector('.insights-refresh button').click()"
                )
                deadline = time.monotonic() + 25
                while await evaluate(
                    "Boolean(document.querySelector('.insights-hub > [role=alert]'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Insights did not recover after retry.")
                    await asyncio.sleep(0.1)
                assert await evaluate(
                    "Boolean(document.querySelector('.performance-table tbody tr'))"
                )
                print(
                    "Insights unknown counts, retained actual comparisons and explicit retry verified on desktop/mobile. Browser-only read failures; no match, statistics preference or saved data changed.",
                    flush=True,
                )
            finally:
                await evaluate(
                    "window.fetch=window.__insightsRecoveryQA.original;delete window.__insightsRecoveryQA;"
                )
        if os.environ.get("DEEPDECK_QA_NAVIGATION") == "1":
            names = [
                "Play Local games",
                "Decks Build & sync",
                "Agents Teach your AI",
                "League Compete online",
                "Competitions Local tournaments (Events)",
                "Replays Watch every game",
                "Insights Track progress",
            ]
            for width, height in ((320, 740), (390, 844), (720, 1000), (900, 1000), (1440, 1000)):
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": 1,
                        "mobile": width <= 760,
                    },
                )
                await asyncio.sleep(0.2)
                assert await evaluate(
                    "JSON.stringify([...document.querySelectorAll('.player-nav nav button')].map(b=>b.getAttribute('aria-label')))"
                ) == json.dumps(names, separators=(",", ":")), (
                    "Navigation names changed with viewport."
                )
                tree = await command("Accessibility.getFullAXTree")
                accessible_names = {
                    node.get("name", {}).get("value")
                    for node in tree["nodes"]
                    if not node.get("ignored") and node.get("role", {}).get("value") == "button"
                }
                assert set(names).issubset(accessible_names), (
                    f"Navigation lost accessible names at {width}px."
                )
                assert await evaluate(
                    "getComputedStyle(document.querySelector('.nav-short-label')).color===getComputedStyle(document.querySelector('.nav-short-label').parentElement).color"
                ), "Compact label inherits an unrelated muted color."
                if width <= 760:
                    layout = await evaluate("""(() => {
                        const nav=document.querySelector('.player-nav').getBoundingClientRect();
                        return [...document.querySelectorAll('.player-nav nav button')].map(button=>{
                            const r=button.getBoundingClientRect(),text=button.querySelector('strong');
                            return {top:r.top,width:r.width,height:r.height,inside:r.left>=nav.left && r.right<=nav.right && r.top>=nav.top && r.bottom<=nav.bottom,clipped:text.scrollWidth>text.clientWidth};
                        });
                    })()""")
                    assert len({round(item["top"]) for item in layout}) == 1, (
                        f"Navigation wraps at {width}px: {layout}"
                    )
                    assert all(
                        item["inside"]
                        and not item["clipped"]
                        and item["width"] >= 44
                        and item["height"] >= 44
                        for item in layout
                    ), f"Navigation labels/targets fail at {width}px: {layout}"
                    assert await evaluate(
                        "getComputedStyle(document.querySelector('.nav-full-label')).display==='none' && getComputedStyle(document.querySelector('.nav-short-label')).display!=='none'"
                    )
                elif width == 900:
                    assert await evaluate(
                        "getComputedStyle(document.querySelector('.player-nav nav button > span')).display==='none'"
                    )
                    assert "Settings Accounts & storage" in accessible_names
                else:
                    assert await evaluate(
                        "getComputedStyle(document.querySelector('.nav-full-label')).display!=='none' && getComputedStyle(document.querySelector('.nav-short-label')).display==='none'"
                    )
                shot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"navigation-{width}.png").write_bytes(base64.b64decode(shot["data"]))
            await evaluate("document.querySelector('.player-nav nav button').focus()")
            for event in ("keyDown", "keyUp"):
                await command(
                    "Input.dispatchKeyEvent",
                    {"type": event, "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
                )
            assert await evaluate(
                "document.activeElement?.getAttribute('aria-label')==='Decks Build & sync'"
            )
            assert await evaluate("getComputedStyle(document.activeElement).outlineStyle==='solid'")
            for event in ("keyDown", "keyUp"):
                await command(
                    "Input.dispatchKeyEvent",
                    {
                        "type": event,
                        "key": "Enter",
                        "code": "Enter",
                        "windowsVirtualKeyCode": 13,
                        **({"text": "\r"} if event == "keyDown" else {}),
                    },
                )
            deadline = time.monotonic() + 5
            while not await evaluate(
                "document.querySelector('.player-nav nav [aria-current=page]')?.getAttribute('aria-label')==='Decks Build & sync'"
            ):
                if time.monotonic() > deadline:
                    raise AssertionError(
                        "Keyboard activation did not navigate: "
                        + str(
                            await evaluate(
                                "({active:document.activeElement?.outerHTML,current:document.querySelector('.player-nav nav [aria-current=page]')?.outerHTML})"
                            )
                        )
                    )
                await asyncio.sleep(0.1)
            print(
                "All seven navigation targets verified at 320/390/720/900/1440px: single mobile row, unclipped labels, 44px targets, full accessibility-tree names and keyboard navigation. No saved data changed.",
                flush=True,
            )
        if os.environ.get("DEEPDECK_QA_COMPETITION_RECOVERY") == "1":
            # Read failures exist only in this isolated browser. No tournament
            # is started, paused or resumed; successful reads use real history.
            await evaluate("document.querySelectorAll('.player-nav nav button')[0].click()")
            await asyncio.sleep(0.2)
            await evaluate("""(() => {
                const state=window.__competitionRecoveryQA={original:window.fetch.bind(window),fail:true};
                window.fetch=async(input,init)=>{
                    const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
                    const method=(init?.method||input.method||'GET').toUpperCase();
                    if(!['GET','HEAD'].includes(method))throw new Error('Unexpected mutation blocked by competition recovery QA.');
                    if(state.fail && path.startsWith('/api/v1/local-competitions'))return new Response(JSON.stringify({detail:'Browser QA history temporarily unavailable'}),{status:503,headers:{'Content-Type':'application/json'}});
                    return state.original(input,init);
                };
            })();""")
            try:
                await evaluate("document.querySelectorAll('.player-nav nav button')[4].click()")
                for state in ("initial", "refresh"):
                    if state == "refresh":
                        await evaluate(
                            "window.__competitionRecoveryQA.fail=false;[...document.querySelectorAll('.competition-history button')].find(b=>b.textContent==='Retry loading').click()"
                        )
                        deadline = time.monotonic() + 20
                        while not await evaluate(
                            "Boolean(document.querySelector('.competition-history-card'))"
                        ):
                            if time.monotonic() > deadline:
                                raise TimeoutError(
                                    "Real competition history did not recover after retry."
                                )
                            await asyncio.sleep(0.1)
                        await evaluate(
                            "document.querySelector('.competition-history-card').click()"
                        )
                        while not await evaluate(
                            "Boolean(document.querySelector('.competition-match'))"
                        ):
                            if time.monotonic() > deadline:
                                raise TimeoutError("Real competition detail did not load.")
                            await asyncio.sleep(0.1)
                        await evaluate("window.__competitionRecoveryQA.fail=true")
                    deadline = time.monotonic() + 20
                    expected = (
                        "Saved competitions could not be loaded."
                        if state == "initial"
                        else "Previously loaded results remain visible."
                    )
                    while not await evaluate(
                        "Boolean(document.querySelector('.competition-history [role=alert]')?.textContent.includes("
                        + json.dumps(expected)
                        + "))"
                    ):
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"Competition {state} error was not explained.")
                        await asyncio.sleep(0.1)
                    if state == "initial":
                        assert not await evaluate(
                            "[...document.querySelectorAll('.competition-history button')].some(b=>b.textContent==='Create your first competition')"
                        ), "A failed read was presented as an empty library."
                    else:
                        assert await evaluate(
                            "Boolean(document.querySelector('.competition-match'))"
                        ), "A failed refresh removed saved match details."
                    for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                            "document.querySelector('.competition-history [role=alert]').scrollIntoView({block:'center'})"
                        )
                        await asyncio.sleep(0.2)
                        assert not await evaluate(
                            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                        ), "Competition recovery overflows."
                        shot = await command(
                            "Page.captureScreenshot",
                            {"format": "png", "captureBeyondViewport": False},
                        )
                        (output / f"competition-recovery-{state}-{label}.png").write_bytes(
                            base64.b64decode(shot["data"])
                        )
                await evaluate(
                    "window.__competitionRecoveryQA.fail=false;[...document.querySelectorAll('.competition-history button')].find(b=>b.textContent==='Retry loading').click()"
                )
                deadline = time.monotonic() + 20
                while await evaluate(
                    "Boolean(document.querySelector('.competition-history [role=alert]'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Competition detail did not recover after retry.")
                    await asyncio.sleep(0.1)
                assert await evaluate("Boolean(document.querySelector('.competition-match'))")
                print(
                    "Competition load failures, retained results and explicit retry verified on desktop/mobile. Read-only real history; failures were browser fixtures, no tournament control submitted.",
                    flush=True,
                )
            finally:
                await evaluate(
                    "window.fetch=window.__competitionRecoveryQA.original;delete window.__competitionRecoveryQA;"
                )
        if os.environ.get("DEEPDECK_QA_COMPETITIONS") == "1":
            await command(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
            )
            await evaluate("document.querySelectorAll('.player-nav nav button')[4].click()")
            deadline = time.monotonic() + 25
            while not await evaluate(
                "Boolean(document.querySelector('.competition-history-card'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("No real competition is available for history verification.")
                await asyncio.sleep(0.2)
            await evaluate("document.querySelector('.competition-history-card').click()")
            while not await evaluate("Boolean(document.querySelector('.competition-match'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError("Competition match cards did not render.")
                await asyncio.sleep(0.2)
            for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": 1,
                        "mobile": width < 500,
                    },
                )
                await asyncio.sleep(0.3)
                assert not await evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                ), "Competition detail overflows."
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"competition-detail-{label}.png").write_bytes(
                    base64.b64decode(screenshot["data"])
                )
            await evaluate(
                "[...document.querySelectorAll('button')].find(b=>b.textContent.includes('All competitions')).click()"
            )
            deadline = time.monotonic() + 25
            while not await evaluate(
                "[...document.querySelectorAll('.competition-history-card')].some(b=>b.innerText.includes('Round robin'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "No real round-robin competition is available for matrix verification."
                    )
                await asyncio.sleep(0.2)
            await evaluate(
                "[...document.querySelectorAll('.competition-history-card')].find(b=>b.innerText.includes('Round robin')).click()"
            )
            while not await evaluate("Boolean(document.querySelector('.competition-matrix'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError("Round-robin matrix did not render.")
                await asyncio.sleep(0.2)
            assert await evaluate(
                "document.querySelectorAll('.competition-matrix tbody tr').length>=2"
            )
            assert await evaluate(
                "document.querySelectorAll('.competition-matrix button').length>=2"
            )
            assert await evaluate(
                "[...document.querySelectorAll('.competition-matrix tbody tr')].every(row=>row.querySelectorAll('[aria-label=\"Same entrant\"]').length===1)"
            )
            for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                    "document.querySelector('.competition-matrix-section').scrollIntoView({block:'center'})"
                )
                await asyncio.sleep(0.3)
                assert not await evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                ), "Matrix overflows the page."
                shot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"competition-matrix-{label}.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
            await evaluate("document.querySelectorAll('.player-nav nav button')[0].click()")
            await asyncio.sleep(0.2)
            await evaluate("document.querySelector('.competition-entry').click()")
            await evaluate(
                "[...document.querySelectorAll('button')].find(b => b.textContent === 'Add all agents').click()"
            )
            assert await evaluate(
                "document.querySelectorAll('.competition-entrants li').length >= 10"
            )
            for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": 1,
                        "mobile": width < 500,
                    },
                )
                await asyncio.sleep(0.3)
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"competition-builder-{label}.png").write_bytes(
                    base64.b64decode(screenshot["data"])
                )
                assert not await evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                ), await evaluate(
                    "JSON.stringify({viewport:innerWidth,client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,nodes:[...document.querySelectorAll('body *')].filter(e=>getComputedStyle(e).visibility!=='hidden' && e.getBoundingClientRect().right>document.documentElement.clientWidth+1).slice(0,15).map(e=>({tag:e.tagName,class:e.className,width:e.getBoundingClientRect().width}))})"
                )
            print(
                "Competition history, match cards, standings and 10-entrant builder verified on desktop/mobile. No competition was launched by browser QA.",
                flush=True,
            )
        if os.environ.get("DEEPDECK_QA_LIVE_BATCH") == "1":
            await command("Page.navigate", {"url": APP_URL})
            deadline = time.monotonic() + 45
            while not await evaluate("Boolean(document.querySelector('.active-strip button'))"):
                if time.monotonic() > deadline:
                    raise TimeoutError("No running batch is available to watch.")
                await asyncio.sleep(0.3)
            await evaluate(
                "[...document.querySelectorAll('.active-strip button')].find(button => button.textContent === 'Watch live').click()"
            )
            while not await evaluate(
                "Boolean(document.querySelector('.live-match-theater[open] canvas'))"
            ):
                error = await evaluate(
                    "document.querySelector('.live-match-theater .inline-error')?.textContent"
                )
                if error:
                    raise AssertionError(error)
                if time.monotonic() > deadline:
                    raise TimeoutError("The live Pixi spectator did not render.")
                await asyncio.sleep(0.3)
            for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": 1,
                        "mobile": width < 500,
                    },
                )
                await asyncio.sleep(0.5)
                assert not await evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                ), "The live viewer overflows horizontally."
                screenshot = await command(
                    "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                )
                (output / f"batch-live-{label}.png").write_bytes(
                    base64.b64decode(screenshot["data"])
                )
            await evaluate(
                "document.querySelector('.live-match-theater .replay-theater-header button').click()"
            )
            assert not await evaluate(
                "Boolean(document.querySelector('.live-match-theater[open]'))"
            )
            await evaluate("document.querySelectorAll('.player-nav nav button')[5].click()")
            await asyncio.sleep(0.3)
            assert await evaluate("Boolean(document.querySelector('.live-now-row button'))"), (
                "Replays did not expose the running batch."
            )
            await evaluate("document.querySelector('.live-now-row button').click()")
            while not await evaluate(
                "Boolean(document.querySelector('.live-match-theater[open] canvas'))"
            ):
                if time.monotonic() > deadline:
                    raise TimeoutError("Replays could not reopen the live spectator.")
                await asyncio.sleep(0.2)
            await evaluate(
                "document.querySelector('.live-match-theater .replay-theater-header button').click()"
            )
            print(
                "Real live Pixi table verified on desktop/mobile, opened from Play and Replays; leaving does not stop the batch.",
                flush=True,
            )
        if os.environ.get("DEEPDECK_QA_LEAGUE_ROTATION") == "1":
            with urlopen(f"{APP_URL}/api/v1/league-queue", timeout=10) as response:
                rotation_before = json.load(response)
            await evaluate("""window.__rotationQA={original:window.fetch.bind(window),writes:[],queue:{id:'browser-only-rotation',revision:4,status:'paused',strategy:'round_robin',message:'Browser-only preview; no actual worker is started.',history:[],capacity:{status:'unsupported',limit:null,used:null,available:null,reason:'Server capacity must be reported before automatic dispatch.',checkedAt:new Date().toISOString()},entries:[
                {id:'tempo',agentId:'tempo',name:'Dimir tempo · patient interaction',format:'legacy',competitionVersionId:'league',deckVersionIds:['deck-a'],lease:{mode:'matches',matches:10},completedMatches:3,elapsedSeconds:140,state:'queued',turn:null,error:null},
                {id:'combo',agentId:'combo',name:'Sneak Attack · protect the combo',format:'legacy',competitionVersionId:'league',deckVersionIds:['deck-b','deck-c'],lease:{mode:'duration',minutes:60},completedMatches:2,elapsedSeconds:360,state:'queued',turn:null,error:null},
                {id:'aggro',agentId:'aggro',name:'Death and Taxes · mana denial',format:'legacy',competitionVersionId:'league',deckVersionIds:['deck-d'],lease:{mode:'continuous'},completedMatches:12,elapsedSeconds:700,state:'queued',turn:null,error:null}
            ]}};
            window.__rotationQA.queue.history=[
                {dispatchId:'receipt-one',name:'Dimir tempo · confirmed receipt',finishedAt:new Date().toISOString(),error:null,result:{completedEntries:1,elapsedSeconds:72,outcome:'complete',matchId:'browser-match'}},
                {dispatchId:'receipt-two',name:'Sneak Attack · interrupted attempt',finishedAt:new Date().toISOString(),error:'Receipt missing; review before retrying.',result:null}
            ];
            window.fetch=async(input,init)=>{
                const state=window.__rotationQA;
                const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
                const method=(init?.method||input.method||'GET').toUpperCase();
                const response=value=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
                if(path==='/api/v1/league-queue' && method==='GET')return response(state.queue);
                if(path==='/api/v1/league-decks/prepare' && method==='POST')return response({deckVersionIds:JSON.parse(init.body).deckVersionIds});
                if(/^\/api\/v1\/deck-workshop\/decks\/[^/]+\/publish$/.test(path) && method==='POST')return response({});
                if(path.startsWith('/api/v1/league-queue') && ['PUT','POST'].includes(method)){
                    const payload=JSON.parse(init.body);if(payload.revision!==state.queue.revision)throw new Error('Stale rotation QA write.');
                    state.writes.push({path,payload});
                    if(method==='PUT'){state.queue.strategy=payload.strategy;state.queue.entries=payload.entries;}
                    else if(path==='/api/v1/league-queue/entries'){
                        if(Object.keys(payload).sort().join(',')!=='entry,revision')throw new Error('Append resent a stale bench snapshot.');
                        state.queue.entries.push({...payload.entry,id:'added',name:'New waiting pilot',format:'legacy',completedMatches:0,elapsedSeconds:0,state:'queued',turn:null,error:null});
                    }else if(path.endsWith('/remove')){
                        const id=path.split('/').at(-2),entry=state.queue.entries.find(e=>e.id===id);
                        if(!entry || entry.turn)throw new Error('QA attempted to remove an active turn.');
                        state.queue.entries=state.queue.entries.filter(e=>e.id!==id);
                    }else{
                        state.queue.status=path.endsWith('/resume')?'running':'paused';
                        if(state.queue.status==='running'){
                            state.queue.entries[0].turn={dispatchId:'protected-browser-turn',sequence:2,jobId:'browser-job',createdAt:new Date().toISOString()};
                            state.queue.entries[0].state='playing';state.activeSnapshot=JSON.stringify(state.queue.entries[0]);
                        }else{state.queue.entries[0].turn=null;state.queue.entries[0].state='queued';}
                    }
                    state.queue.revision++;return response(state.queue);
                }
                if(!['GET','HEAD'].includes(method))throw new Error('Unexpected rotation mutation blocked.');
                return state.original(input,init);
            };""")
            try:
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
                )
                await evaluate("document.querySelectorAll('.player-nav nav button')[3].click()")
                deadline = time.monotonic() + 30
                while not await evaluate(
                    "[...document.querySelectorAll('button')].some(b=>b.textContent==='Manage rotation')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Rotation entry did not render.")
                    await asyncio.sleep(0.2)
                await evaluate(
                    "[...document.querySelectorAll('button')].find(b=>b.textContent==='Manage rotation').click()"
                )
                while not await evaluate(
                    "document.querySelectorAll('.rotation-entries li').length===3"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Rotation fixture did not render.")
                    await asyncio.sleep(0.2)
                await evaluate(
                    "(()=>{const select=document.querySelector('.rotation-controls select');select.value='random';select.dispatchEvent(new Event('change',{bubbles:true}));})()"
                )
                while not await evaluate(
                    "document.querySelector('#rotation-order-help').innerText.includes('repeats are possible')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Random rotation did not save.")
                    await asyncio.sleep(0.1)
                await evaluate(
                    "[...document.querySelectorAll('.rotation-controls button')].find(b=>b.textContent==='Start rotation').click()"
                )
                while not await evaluate(
                    "Boolean([...document.querySelectorAll('.rotation-controls button')].find(b=>b.textContent==='Pause rotation'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Explicit rotation start did not settle.")
                    await asyncio.sleep(0.1)
                assert await evaluate(
                    "document.querySelector('.rotation-controls select').disabled"
                )
                assert await evaluate(
                    "!document.querySelector('.rotation-entries li:first-child button') && document.querySelectorAll('.rotation-entries button').length===2"
                )
                # Choose an actual available agent/League through the real form,
                # but intercept publication, slot preparation and append locally.
                while not await evaluate(
                    "document.querySelectorAll('.deploy-grid select')[1]?.options.length>1"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            "No compatible League was available to preview live append."
                        )
                    await asyncio.sleep(0.1)
                await evaluate(
                    "(()=>{const select=document.querySelectorAll('.deploy-grid select')[1];select.value=select.options[1].value;select.dispatchEvent(new Event('change',{bubbles:true}));})()"
                )
                while not await evaluate(
                    "Boolean(document.querySelector('.rotation-entry-actions button:not(:disabled)'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Add-to-rotation form did not become ready.")
                    await asyncio.sleep(0.1)
                await evaluate("document.querySelector('.rotation-entry-actions button').click()")
                while not await evaluate(
                    "document.querySelectorAll('.rotation-entries li').length===4"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Live append did not settle.")
                    await asyncio.sleep(0.1)
                assert await evaluate(
                    "document.querySelector('[aria-label=\"League submission accepted\"]').innerText.includes('Existing matches keep playing')"
                )
                await evaluate(
                    "[...document.querySelectorAll('.rotation-entries li')].find(row=>row.innerText.includes('Sneak Attack')).querySelector('button').click()"
                )
                while not await evaluate(
                    "document.querySelectorAll('.rotation-entries li').length===3"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Live removal did not settle.")
                    await asyncio.sleep(0.1)
                assert await evaluate(
                    "window.__rotationQA.queue.status==='running' && JSON.stringify(window.__rotationQA.queue.entries[0])===window.__rotationQA.activeSnapshot"
                ), "Editing the waiting bench changed an active turn."
                assert await evaluate(
                    "document.querySelector('[aria-label=\"Rotation overview\"]').innerText.includes('1 turn dispatched') && document.querySelector('[aria-label=\"Rotation overview\"]').innerText.includes('2 waiting')"
                )
                for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                        "document.querySelector('.league-rotation').scrollIntoView({block:'start'})"
                    )
                    await asyncio.sleep(0.3)
                    assert not await evaluate(
                        "document.documentElement.scrollWidth>document.documentElement.clientWidth+1"
                    ), "Agent rotation overflows."
                    assert await evaluate(
                        "[...document.querySelectorAll('.league-rotation button')].every(b=>b.getBoundingClientRect().height>=44)"
                    ), "Rotation controls are too small for touch."
                    shot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"league-rotation-{label}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
                    await evaluate(
                        "document.querySelector('.rotation-history').scrollIntoView({block:'start'})"
                    )
                    await asyncio.sleep(0.2)
                    assert await evaluate(
                        "document.querySelector('.rotation-history').innerText.includes('Result not confirmed') && document.querySelector('.rotation-history').innerText.includes('1m 12s deployed')"
                    )
                    assert not await evaluate(
                        "document.documentElement.scrollWidth>document.documentElement.clientWidth+1"
                    ), "Rotation history overflows."
                    shot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"league-rotation-history-{label}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
                    await evaluate(
                        "[...document.querySelectorAll('.league-rotation header button')].find(b=>b.textContent==='Hide rotation details').click()"
                    )
                    await evaluate(
                        "document.querySelector('.league-rotation').scrollIntoView({block:'start'})"
                    )
                    await asyncio.sleep(0.2)
                    assert await evaluate(
                        "document.querySelector('[aria-label=\"Rotation overview\"]').innerText.includes('1 turn dispatched')"
                    )
                    assert not await evaluate(
                        "Boolean(document.querySelector('.rotation-entries'))"
                    )
                    shot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"league-rotation-overview-{label}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
                    await evaluate(
                        "[...document.querySelectorAll('.league-rotation header button')].find(b=>b.textContent==='Show rotation details').click()"
                    )
                await evaluate(
                    "[...document.querySelectorAll('.rotation-controls button')].find(b=>b.textContent==='Pause rotation').click()"
                )
                while not await evaluate(
                    "Boolean([...document.querySelectorAll('.rotation-controls button')].find(b=>b.textContent==='Start rotation'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Explicit rotation pause did not settle.")
                    await asyncio.sleep(0.1)
                assert await evaluate("window.__rotationQA.writes.length===5")
                print(
                    "Rotation overview and bounded receipt history verified on desktop/mobile, including collapsed active counts and missing outcomes. Live append/removal preserved the exact active turn; all writes were browser fixtures, no real League deployment.",
                    flush=True,
                )
            finally:
                await evaluate(
                    "window.fetch=window.__rotationQA.original;delete window.__rotationQA;"
                )
            with urlopen(f"{APP_URL}/api/v1/league-queue", timeout=10) as response:
                assert json.load(response) == rotation_before, (
                    "Browser QA changed the real agent rotation."
                )
        if os.environ.get("DEEPDECK_QA_LEAGUE_RECEIPTS") == "1":
            # Every mutating request below is intercepted in this disposable
            # browser. No publication, matchmaking worker or real stop occurs.
            await evaluate("""window.__leagueReceiptQA={original:window.fetch.bind(window),job:null,starts:0,stops:0,failRefresh:false};
                window.fetch=async(input,init)=>{
                    const state=window.__leagueReceiptQA;
                    const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
                    const method=(init?.method||input.method||'GET').toUpperCase();
                    const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
                    if(path==='/api/v1/league-capacity')return response({status:'ready',limit:37,used:7,available:30,reason:'Browser-only server contract fixture'});
                    if(path==='/api/v1/league-decks/prepare' && method==='POST')return response({deckVersionIds:JSON.parse(init.body).deckVersionIds});
                    if(path==='/api/v1/jobs' && method==='POST'){
                        const payload=JSON.parse(init.body);if(payload.kind!=='matchmaking.agent')throw new Error('Unexpected QA job kind.');
                        state.starts++;state.lastPayload=payload;
                        state.job={id:'qa-league-receipt',kind:'matchmaking.agent',model_id:payload.model_id,label:'Browser-only League worker',status:'queued',logs:['Simulated controller acknowledgement; no actual deployment.'],argv:[],created_at:new Date().toISOString(),started_at:null,finished_at:null,exit_code:null,artifact_path:null};
                        state.job.details={leagueRun:{completedEntries:0,activeEntries:1,maxMatches:payload.league_run?.mode==='matches'?payload.league_run.matches:null,durationSeconds:payload.league_run?.mode==='duration'?payload.league_run.minutes*60:null}};
                        return response(state.job,202);
                    }
                    if(path==='/api/v1/jobs/qa-league-receipt/stop' && method==='POST'){
                        state.stops++;state.job={...state.job,status:'stopped'};state.failRefresh=true;return response(state.job);
                    }
                    if(path==='/api/v1/jobs' && method==='GET' && state.job){
                        return state.failRefresh?response({detail:'Browser QA roster refresh unavailable'},503):response([state.job]);
                    }
                    if(!['GET','HEAD'].includes(method))throw new Error('Unexpected mutation blocked in League receipt QA.');
                    return state.original(input,init);
                };""")
            try:
                await evaluate("document.querySelectorAll('.player-nav nav button')[3].click()")
                pilot = await evaluate(
                    "fetch('/api/v1/agent-studio/agents').then(r=>r.json()).then(r=>r.items.find(a=>a.format==='legacy' && a.decks.length && a.decks.every(d=>!d.id.startsWith('localdeck-') && !d.unavailable)).id)"
                )
                await evaluate(
                    "(() => {const select=document.querySelector('.deploy-grid select');select.value="
                    + json.dumps(pilot)
                    + ";select.dispatchEvent(new Event('change',{bubbles:true}));})()"
                )
                deadline = time.monotonic() + 25
                while not await evaluate(
                    "document.querySelectorAll('.deploy-grid select')[1]?.options.length>1"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            "No actual compatible League was available for receipt UI QA."
                        )
                    await asyncio.sleep(0.1)
                await evaluate(
                    """(() => {const select=document.querySelectorAll('.deploy-grid select')[1];select.value=select.options[1].value;select.dispatchEvent(new Event('change',{bubbles:true}));})()"""
                )
                while not await evaluate(
                    "Boolean(document.querySelector('.deploy-action button:not(:disabled)'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("The isolated League fixture did not become ready.")
                    await asyncio.sleep(0.1)
                if os.environ.get("DEEPDECK_QA_LEAGUE_RUN") == "1":
                    for mode in ("matches", "duration"):
                        await evaluate(
                            "(() => {const select=document.querySelector('.league-run-settings select');select.value="
                            + json.dumps(mode)
                            + ";select.dispatchEvent(new Event('change',{bubbles:true}));})()"
                        )
                        await asyncio.sleep(0.1)
                        await evaluate(
                            "(() => {const input=document.querySelector('.league-run-settings input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,"
                            + json.dumps("3" if mode == "matches" else "45")
                            + ");input.dispatchEvent(new Event('input',{bubbles:true}));})()"
                        )
                        for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                                "document.querySelector('.league-run-settings').scrollIntoView({block:'start'})"
                            )
                            await asyncio.sleep(0.2)
                            assert not await evaluate(
                                "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                            ), "League run settings overflow."
                            shot = await command(
                                "Page.captureScreenshot",
                                {"format": "png", "captureBeyondViewport": False},
                            )
                            (output / f"league-run-{mode}-{label}.png").write_bytes(
                                base64.b64decode(shot["data"])
                            )
                    await evaluate(
                        "(() => {const select=document.querySelector('.league-run-settings select');select.value='matches';select.dispatchEvent(new Event('change',{bubbles:true}));})()"
                    )
                    await asyncio.sleep(0.1)
                await evaluate("document.querySelector('.deploy-action button').click()")
                while not await evaluate(
                    "Boolean(document.querySelector('[aria-label=\"League submission accepted\"]')) && [...document.querySelectorAll('.active-deployments button')].some(b=>b.textContent==='Bring home')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            "A successful worker acknowledgement was not shown with its roster row."
                        )
                    await asyncio.sleep(0.1)
                for state in ("accepted", "stopped"):
                    if state == "stopped":
                        await evaluate(
                            "[...document.querySelectorAll('.active-deployments button')].find(b=>b.textContent==='Bring home').click()"
                        )
                        while not await evaluate(
                            "document.querySelector('.active-deployments').innerText.includes('stopped locally') && document.querySelector('.active-deployments .inline-error')?.textContent.includes('roster could not refresh')"
                        ):
                            if time.monotonic() > deadline:
                                raise TimeoutError(
                                    "The confirmed stop did not survive the simulated failed refresh."
                                )
                            await asyncio.sleep(0.1)
                        assert await evaluate(
                            "!document.querySelector('.active-deployments button')"
                        )
                    assert await evaluate(
                        "document.querySelector('[aria-label=\"League places\"]').innerText.includes('7 / 37 used')"
                    ), "The browser manufactured a freed server place."
                    for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                            "document.querySelector("
                            + json.dumps(
                                '[aria-label="League submission accepted"]'
                                if state == "accepted"
                                else ".active-deployments"
                            )
                            + ").scrollIntoView({block:'center'})"
                        )
                        await asyncio.sleep(0.2)
                        assert not await evaluate(
                            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                        ), "League receipt overflows."
                        shot = await command(
                            "Page.captureScreenshot",
                            {"format": "png", "captureBeyondViewport": False},
                        )
                        (output / f"league-receipt-{state}-{label}.png").write_bytes(
                            base64.b64decode(shot["data"])
                        )
                assert await evaluate(
                    "window.__leagueReceiptQA.starts===1 && window.__leagueReceiptQA.stops===1"
                )
                if os.environ.get("DEEPDECK_QA_LEAGUE_RUN") == "1":
                    assert await evaluate(
                        "JSON.stringify(window.__leagueReceiptQA.lastPayload.league_run)===JSON.stringify({mode:'matches',matches:3}) && window.__leagueReceiptQA.lastPayload.connections===1"
                    )
                    print(
                        "Finite League controls verified on desktop/mobile and the exact three-match request captured. All launch/stop requests were browser fixtures, with unchanged server-capacity display.",
                        flush=True,
                    )
                print(
                    "League acknowledgements and confirmed stop with a failed roster refresh verified on desktop/mobile. All mutations were browser fixtures; no publication, real worker or stop, and no local capacity adjustment.",
                    flush=True,
                )
            finally:
                await evaluate(
                    "window.fetch=window.__leagueReceiptQA.original;delete window.__leagueReceiptQA;"
                )
        if os.environ.get("DEEPDECK_QA_REPLAY_COLLECTION") == "1":
            with urlopen(f"{APP_URL}/api/v1/experience/settings", timeout=10) as response:
                real_before = json.load(response)
            # Settings writes and the agent report exist only in this fresh
            # browser. The machine's real quota/collection choice never changes.
            fixture_source = """(() => {
                const state=window.__replayCollectionQA={original:window.fetch.bind(window),updates:[],settings:{replayStorageLimitBytes:2147483648,replayStorageUsedBytes:12345,replayPollSeconds:1200,autoCollectLeagueReplays:false,replayStorageError:null}};
                const report={id:'qa-collection',model_id:'qa-collection',kind:'matchmaking.agent',status:'running',label:'Browser-only replay collector',logs:[],argv:[],created_at:new Date().toISOString(),started_at:null,finished_at:null,exit_code:null,artifact_path:null,details:{replaySync:{enabled:true,collected:4,pending:2,lastError:'Example quota failure from the last check; browser fixture only.',checkedAtUnixMs:Date.now()-900000}}};
                window.fetch=async(input,init)=>{
                    const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
                    const method=(init?.method||input.method||'GET').toUpperCase();
                    const response=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
                    if(path==='/api/v1/experience/settings'){
                        if(method==='PUT'){const update=JSON.parse(init.body);state.updates.push(update);state.settings={...state.settings,...update};}
                        return response(state.settings);
                    }
                    if(path==='/api/v1/jobs' && method==='GET')return response([report]);
                    if(!['GET','HEAD'].includes(method))throw new Error('Unexpected mutation blocked by replay settings QA.');
                    return state.original(input,init);
                };
            })();"""
            injection = await command(
                "Page.addScriptToEvaluateOnNewDocument", {"source": fixture_source}
            )
            await command("Page.navigate", {"url": APP_URL})
            try:
                deadline = time.monotonic() + 30
                while not await evaluate(
                    "Boolean(document.querySelector('.player-nav nav button'))"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("The replay settings fixture did not start.")
                    await asyncio.sleep(0.1)
                await evaluate("document.querySelectorAll('.player-nav nav button')[5].click()")
                while not await evaluate(
                    "document.querySelector('.replay-collection-status')?.innerText.includes('Automatic collection is paused')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Paused collection was not visible in Replays.")
                    await asyncio.sleep(0.1)
                assert await evaluate(
                    "document.querySelector('.replay-collection-status').innerText.includes('4 collected in total') && document.querySelector('.replay-collection-status').innerText.includes('2 pending in the last report')"
                )
                await evaluate("document.querySelector('.replay-collection-status button').click()")
                await evaluate(
                    """(() => {const field=document.querySelector('.replay-storage-settings input[type=range]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,'3');field.dispatchEvent(new Event('input',{bubbles:true}));})()"""
                )
                await evaluate(
                    "[...document.querySelectorAll('.replay-storage-settings button')].find(b=>b.textContent==='Save replay settings').click()"
                )
                while not await evaluate("window.__replayCollectionQA.updates.length===1"):
                    if time.monotonic() > deadline:
                        raise TimeoutError("The quota-only settings save did not finish.")
                    await asyncio.sleep(0.1)
                assert await evaluate(
                    "JSON.stringify(window.__replayCollectionQA.updates[0])===JSON.stringify({replayStorageLimitBytes:3221225472})"
                ), "Quota edit overwrote an untouched setting."
                assert await evaluate(
                    "!document.querySelector('.replay-storage-settings input[type=checkbox]').checked && window.__replayCollectionQA.settings.replayPollSeconds===1200"
                )
                for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                        "document.querySelector('.replay-storage-settings').scrollIntoView({block:'start'})"
                    )
                    await asyncio.sleep(0.2)
                    assert not await evaluate(
                        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                    ), "Replay settings overflow."
                    shot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"replay-collection-settings-{label}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
                await evaluate(
                    "document.querySelector('.replay-storage-settings input[type=checkbox]').click()"
                )
                await evaluate(
                    "[...document.querySelectorAll('.replay-storage-settings button')].find(b=>b.textContent==='Save replay settings').click()"
                )
                while not await evaluate("window.__replayCollectionQA.updates.length===2"):
                    if time.monotonic() > deadline:
                        raise TimeoutError("The explicit collection toggle did not save.")
                    await asyncio.sleep(0.1)
                assert await evaluate(
                    "JSON.stringify(window.__replayCollectionQA.updates[1])===JSON.stringify({autoCollectLeagueReplays:true})"
                )
                await evaluate("document.querySelector('[aria-label=\"Close settings\"]').click()")
                for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                        "document.querySelector('.replay-collection-status').scrollIntoView({block:'start'})"
                    )
                    await asyncio.sleep(0.2)
                    assert await evaluate(
                        "document.querySelector('.replay-collection-status').innerText.includes('Automatic collection is enabled')"
                    )
                    assert not await evaluate(
                        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                    ), "Replay collection status overflows."
                    shot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"replay-collection-status-{label}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
                print(
                    "Replay pause, quota-only partial save, explicit resume, pending/error reports and historical totals verified on desktop/mobile. Browser-only writes; real replay settings unchanged.",
                    flush=True,
                )
            finally:
                await command(
                    "Page.removeScriptToEvaluateOnNewDocument",
                    {"identifier": injection["identifier"]},
                )
                await evaluate(
                    "if(window.__replayCollectionQA){window.fetch=window.__replayCollectionQA.original;delete window.__replayCollectionQA;}"
                )
            with urlopen(f"{APP_URL}/api/v1/experience/settings", timeout=10) as response:
                real_after = json.load(response)
            assert all(
                real_after[key] == real_before[key]
                for key in (
                    "replayStorageLimitBytes",
                    "replayPollSeconds",
                    "autoCollectLeagueReplays",
                )
            ), "Browser QA changed real replay preferences."
        if os.environ.get("DEEPDECK_QA_REPLAY_LIBRARY_RECOVERY") == "1":
            await evaluate("""window.__libraryQA={original:window.fetch.bind(window),mode:'hold',release:[]};window.fetch=async(input,init)=>{
                const state=window.__libraryQA,path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
                const method=(init?.method||input.method||'GET').toUpperCase();
                if(/^\/api\/v1\/models\/[^/]+\/replays$/.test(path) && method==='GET'){
                    if(state.mode==='hold')await new Promise(resolve=>state.release.push(resolve));
                    if(state.mode==='fail')return new Response(JSON.stringify({detail:'Browser-only replay library unavailable'}),{status:503,headers:{'Content-Type':'application/json'}});
                }
                if(!['GET','HEAD'].includes(method))throw new Error('Unexpected mutation blocked during replay library read QA.');
                return state.original(input,init);
            };""")
            try:
                await evaluate(
                    "document.querySelectorAll('.player-nav nav button')[0].click();document.querySelectorAll('.player-nav nav button')[5].click()"
                )
                deadline = time.monotonic() + 35
                while not await evaluate("window.__libraryQA.release.length>0"):
                    if time.monotonic() > deadline:
                        raise TimeoutError("No replay reads were held for loading verification.")
                    await asyncio.sleep(0.1)
                assert await evaluate(
                    "document.querySelector('[aria-label=\"Replay library status\"]').innerText.includes('Loading your recordings') && !document.querySelector('[aria-label=\"Replay library status\"]').innerText.includes('0 recordings')"
                )
                assert not await evaluate(
                    "[...document.querySelectorAll('.replay-library h3')].some(h=>h.textContent==='No recording in this view')"
                )
                await evaluate(
                    "document.querySelectorAll('.replay-toolbar .segmented button')[2].click();window.__libraryQA.mode='pass';window.__libraryQA.release.splice(0).forEach(resolve=>resolve())"
                )
                while not await evaluate(
                    "Boolean(document.querySelector('.replay-watch')) && [...document.querySelectorAll('.replay-toolbar button')].some(b=>b.textContent==='Refresh library')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            "The real local replay library did not settle after releasing reads."
                        )
                    await asyncio.sleep(0.2)
                before_count = await evaluate(
                    "document.querySelectorAll('.replay-match-card').length"
                )
                await evaluate(
                    "window.__libraryQA.mode='fail';[...document.querySelectorAll('.replay-toolbar button')].find(b=>b.textContent==='Refresh library').click()"
                )
                while not await evaluate(
                    "[...document.querySelectorAll('button')].some(b=>b.textContent==='Retry replay libraries')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("A failed replay refresh did not show explicit retry.")
                    await asyncio.sleep(0.1)
                assert (
                    await evaluate("document.querySelectorAll('.replay-match-card').length")
                    == before_count
                ), "Failed reads erased known recordings."
                assert await evaluate(
                    "document.querySelector('[aria-label=\"Replay library status\"]').innerText.includes('Last loaded view')"
                )
                for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                        "document.querySelector('.replay-toolbar').scrollIntoView({block:'start'})"
                    )
                    await asyncio.sleep(0.2)
                    assert not await evaluate(
                        "document.documentElement.scrollWidth>document.documentElement.clientWidth+1"
                    ), "Replay retry view overflows."
                    assert await evaluate(
                        "document.querySelector('.replay-library-error button').getBoundingClientRect().height>=44"
                    ), "Replay retry control is too small for touch."
                    shot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"replay-library-recovery-{label}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
                await evaluate(
                    "window.__libraryQA.mode='pass';[...document.querySelectorAll('button')].find(b=>b.textContent==='Retry replay libraries').click()"
                )
                while await evaluate(
                    "[...document.querySelectorAll('button')].some(b=>b.textContent==='Retry replay libraries')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Replay retry did not clear the read failure.")
                    await asyncio.sleep(0.1)
                print(
                    f"Replay loading never fabricated an empty library; failed refresh retained all {before_count} real cards and manual retry recovered on desktop/mobile. Read-only checks; no replay or favorite was modified.",
                    flush=True,
                )
            finally:
                await evaluate(
                    'window.__libraryQA.mode="pass";window.__libraryQA.release.splice(0).forEach(resolve=>resolve());window.fetch=window.__libraryQA.original;delete window.__libraryQA;'
                )
        if os.environ.get("DEEPDECK_QA_REPLAY_INBOX") == "1":
            with urlopen(f"{APP_URL}/api/v1/replay-inbox", timeout=10) as response:
                before_inbox = json.load(response)["items"]
            await evaluate("""window.__inboxQA={original:window.fetch.bind(window),writes:0,job:null,done:false,fail:false,items:[
                {modelId:'browser-tempo',name:'Tempo pilot · returned from League',available:true,collected:4,pending:2,currentServerPending:2,otherServerPending:0,unknownServerPending:0,lastError:'Replay history was not ready at the last check.',lastAttemptAtUnixMs:Date.now()},
                {modelId:'browser-other',name:'Combo pilot · another League',available:true,collected:3,pending:1,currentServerPending:0,otherServerPending:1,unknownServerPending:0,lastError:null,lastAttemptAtUnixMs:null}
            ]};window.fetch=async(input,init)=>{
                const state=window.__inboxQA,path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
                const method=(init?.method||input.method||'GET').toUpperCase();
                const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
                if(path==='/api/v1/replay-inbox' && method==='GET')return state.fail?response({detail:'Browser-only inbox temporarily unavailable'},503):response({items:state.items,observedAtUnixMs:Date.now()});
                if(path==='/api/v1/experience/settings' && method==='GET'){const data=await (await state.original(input,init)).json();return response({...data,autoCollectLeagueReplays:true});}
                if(path==='/api/v1/replay-inbox/browser-tempo/collect' && method==='POST'){
                    state.writes++;state.job={id:'browser-replay-check',kind:'replays.collect',label:'Recover replays · Tempo pilot',model_id:'browser-tempo',status:'running',argv:[],logs:[],created_at:new Date().toISOString(),started_at:new Date().toISOString(),finished_at:null,exit_code:null,artifact_path:null,worker_slots:0};return response(state.job,202);
                }
                if(path==='/api/v1/jobs' && method==='GET')return response(state.job?[state.job]:[]);
                if(path==='/api/v1/jobs/browser-replay-check' && method==='GET')return response(state.job);
                if(!['GET','HEAD'].includes(method))throw new Error('Unexpected inbox mutation blocked.');
                return state.original(input,init);
            };""")
            try:
                await evaluate("document.querySelectorAll('.player-nav nav button')[5].click()")
                deadline = time.monotonic() + 35
                while not await evaluate(
                    "Boolean([...document.querySelectorAll('.replay-inbox button')].find(b=>b.textContent.includes('Recover pending replays for Tempo'))) "
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Pending replay recovery did not render.")
                    await asyncio.sleep(0.2)
                await evaluate(
                    "[...document.querySelectorAll('.replay-inbox button')].find(b=>b.textContent.includes('Recover pending replays for Tempo')).click()"
                )
                while not await evaluate(
                    "document.querySelector('.replay-inbox').innerText.includes('No agent was sent')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Recovery acknowledgement did not render.")
                    await asyncio.sleep(0.1)
                assert await evaluate(
                    "[...document.querySelectorAll('.replay-inbox button')].some(b=>b.textContent.includes('Checking replays') && b.disabled)"
                )
                assert await evaluate(
                    "[...document.querySelectorAll('.replay-inbox button')].find(b=>b.textContent.includes('another League')).disabled"
                )
                for width, height, label in ((1440, 1000, "desktop"), (390, 844, "mobile")):
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
                        "document.querySelector('.replay-inbox').scrollIntoView({block:'start'})"
                    )
                    await asyncio.sleep(0.3)
                    assert not await evaluate(
                        "document.documentElement.scrollWidth>document.documentElement.clientWidth+1"
                    ), "Replay inbox overflows."
                    assert await evaluate(
                        "[...document.querySelectorAll('.replay-inbox button')].every(b=>b.getBoundingClientRect().height>=44)"
                    )
                    shot = await command(
                        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
                    )
                    (output / f"replay-inbox-{label}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
                await evaluate(
                    "window.__inboxQA.job.status='completed';window.__inboxQA.job.finished_at=new Date().toISOString();window.__inboxQA.items[0]={...window.__inboxQA.items[0],pending:0,currentServerPending:0,collected:6,lastError:null};[...document.querySelectorAll('.replay-inbox button')].find(b=>b.textContent==='Reload pending downloads').click()"
                )
                while not await evaluate(
                    "document.querySelector('.replay-inbox').innerText.includes('Replay check finished')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Recovery completion did not refresh.")
                    await asyncio.sleep(0.1)
                assert await evaluate(
                    "window.__inboxQA.writes===1 && document.querySelector('.replay-inbox').innerText.includes('1 replay downloads pending')"
                )
                await evaluate(
                    "window.__inboxQA.fail=true;[...document.querySelectorAll('.replay-inbox button')].find(b=>b.textContent==='Reload pending downloads').click()"
                )
                while not await evaluate(
                    "document.querySelector('.replay-inbox').innerText.includes('Last loaded counts remain visible')"
                ):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Inbox outage did not preserve counts.")
                    await asyncio.sleep(0.1)
                assert await evaluate(
                    "document.querySelector('.replay-inbox').innerText.includes('1 pending')"
                )
                print(
                    "Replay recovery acknowledgement, no-agent-deployment wording, current-server gating, completion and retained counts after a read outage verified on desktop/mobile. All mutations were browser fixtures.",
                    flush=True,
                )
            finally:
                await evaluate("window.fetch=window.__inboxQA.original;delete window.__inboxQA;")
            with urlopen(f"{APP_URL}/api/v1/replay-inbox", timeout=10) as response:
                assert json.load(response)["items"] == before_inbox, (
                    "Browser QA changed the real replay inbox."
                )
        if errors:
            raise AssertionError(f"Browser exceptions: {errors}")
        print(f"No browser exceptions. Desktop and mobile screenshots: {output}")


def main() -> None:
    edge = (
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe"
    )
    if not edge.is_file():
        raise SystemExit("Microsoft Edge is not installed.")
    profile_root = (ROOT / ".deepdeck" / "ui-preview" / "browser-profiles").resolve()
    profile_root.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="deepdeck-ui-", dir=profile_root)).resolve()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        debug_port = listener.getsockname()[1]
    process = subprocess.Popen(
        [
            str(edge),
            "--headless",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            "--no-first-run",
            "--no-default-browser-check",
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{debug_port}/json/list", timeout=2) as response:
                    tabs = json.load(response)
                page = next((tab for tab in tabs if tab.get("type") == "page"), None)
                if page:
                    break
            except (URLError, TimeoutError):
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("Headless Edge did not start.")
            time.sleep(0.2)
        asyncio.run(verify(page["webSocketDebuggerUrl"]))
    finally:
        process.terminate()
        process.wait(timeout=10)
        # This is the fresh task-owned browser profile, never the user's profile.
        if profile.parent == profile_root and profile.name.startswith("deepdeck-ui-"):
            shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
