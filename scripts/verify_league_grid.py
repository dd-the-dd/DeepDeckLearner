"""Loopback-only, fixture-only Edge QA; never enrolls a real League agent."""

from __future__ import annotations

import asyncio
import base64
import json
import time

import verify_player_app as browser
import websockets

FIXTURE = r"""(()=>{
 const original=window.fetch;
 const deck={id:'public-deck',name:'Tempo',format:'legacy',version:1,colors:['U'],playableCardCount:60};
 const agents=Array.from({length:9},(_,i)=>({id:'pilot-'+i,name:'Pilot '+(i+1),format:'legacy',revision:1,profile:'balanced',deckVersionIds:[deck.id],decks:[deck]}));
 const entries=agents.slice(0,7).map((a,i)=>({id:'entry-'+i,agentId:a.id,name:a.name,format:'legacy',competitionVersionId:'legacy',deckVersionIds:[deck.id],lease:{mode:'continuous'},completedMatches:0,elapsedSeconds:0,state:'queued',turn:null,error:null}));
 const queue={id:'queue',revision:4,status:'running',strategy:'round_robin',entries,history:[],capacity:{status:'ready',limit:12,used:4,available:8,checkedAt:'',reason:'Fixture'},message:null};
 window.__gridQA={writes:[],queue};
 window.fetch=async(input,init={})=>{
  const u=new URL(typeof input==='string'?input:input.url,location.href),p=u.pathname;
  if(!p.startsWith('/api/')){if(u.origin!==location.origin)throw Error('External fetch blocked');return original(input,init);}
  const method=init.method||'GET';let data;
  if(method!=='GET'){
   window.__gridQA.writes.push({path:p,method,body:JSON.parse(init.body||'{}')});
   if(p!=='/api/v1/league-queue/entries')throw Error('Unexpected fixture write: '+p);
   const payload=JSON.parse(init.body),a=agents.find(a=>a.id===payload.entry.agentId);
   if(payload.revision!==queue.revision)throw Error('Revision mismatch');
   queue.entries.push({...entries[0],...payload.entry,id:'new-entry',name:a.name});queue.revision++;data=queue;
  }else if(p==='/api/v1/session')data={token:'fixture-only'};
  else if(p==='/api/v1/league-queue')data=queue;
  else if(p==='/api/v1/status')data={engine:{healthy:true,url:'http://127.0.0.1:1'}};
  else if(p==='/api/v1/account/status')data={configured:true,valid:true,reason:'Fixture'};
  else if(p==='/api/v1/league-account')data={configured:false,valid:false,reason:'Fixture public decks'};
  else if(p==='/api/v1/league-connection')data={environment:'production',reachable:true,apiUrl:'https://www.deepdeckleague.com/api/v1',siteUrl:'https://www.deepdeckleague.com',capabilities:[],reason:'Fixture'};
  else if(p==='/api/v1/league-capacity')data=queue.capacity;
  else if(p==='/api/v1/agent-studio/agents')data={items:agents};
  else if(p==='/api/v1/catalog/downloaded-decks')data={items:[deck]};
  else if(p==='/api/v1/catalog/competitions')data={items:[{versionId:'legacy',name:'Legacy League',format:'Legacy',status:'active',timeControl:'1s'}]};
  else if(p==='/api/v1/experience/settings')data={autoCollectLeagueReplays:false,replayPollSeconds:600,replayStorageLimitBytes:2147483648,replayStorageUsedBytes:123};
  else if(p==='/api/v1/agent-studio/llm-settings')data={configured:false};
  else if(p==='/api/v1/jobs'||p.endsWith('/replays'))data=[];
  else if(p==='/api/v1/replay-inbox')data={items:[],observedAtUnixMs:0};
  else if(p==='/api/v1/games'||p==='/api/v1/local-competitions')data={items:[]};
  else throw Error('Unmocked API request: '+p);
  return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
 };
})()"""


async def verify(browser_url: str) -> None:
    output = browser.ROOT / ".deepdeck/ui-preview/league-agent-grid"
    output.mkdir(parents=True, exist_ok=True)
    async with websockets.connect(browser_url, max_size=20_000_000) as ws:
        counter, errors = 0, []

        async def command(method: str, params: dict | None = None) -> dict:
            nonlocal counter
            counter += 1
            await ws.send(json.dumps({"id": counter, "method": method, "params": params or {}}))
            while True:
                message = json.loads(await ws.recv())
                if message.get("method") == "Runtime.exceptionThrown":
                    errors.append(message["params"])
                if message.get("id") == counter:
                    if "error" in message:
                        raise RuntimeError(message["error"])
                    return message.get("result", {})

        async def evaluate(expression: str):
            result = await command(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True, "awaitPromise": True},
            )
            if result.get("exceptionDetails"):
                raise RuntimeError(result["exceptionDetails"])
            return result.get("result", {}).get("value")

        async def until(expression: str):
            deadline = time.monotonic() + 15
            while not await evaluate(expression):
                if time.monotonic() > deadline:
                    raise TimeoutError(await evaluate("document.body.innerText.slice(-2000)"))
                await asyncio.sleep(0.1)

        assert browser.APP_URL.startswith(("http://127.0.0.1:", "http://localhost:"))
        for method in ["Page.enable", "Runtime.enable", "Network.enable"]:
            await command(method)
        await command("Network.setBlockedURLs", {"urls": ["https://*"]})
        await command("Page.addScriptToEvaluateOnNewDocument", {"source": FIXTURE})
        await command("Page.navigate", {"url": browser.APP_URL})
        await until("!!document.querySelector('button[aria-label^=\"League \" ]')")
        await evaluate("document.querySelector('button[aria-label^=\"League \" ]').click()")
        await until("document.querySelectorAll('.agent-pool-grid [role=row]').length===9")
        assert await evaluate(
            "document.querySelector('[aria-label=Agent]').value===''&&window.__gridQA.writes.length===0"
        )
        for name, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
            await command(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": width,
                    "height": height,
                    "deviceScaleFactor": 1,
                    "mobile": name == "mobile",
                },
            )
            await asyncio.sleep(0.2)
            assert await evaluate("document.documentElement.scrollWidth<=innerWidth+1"), name
            shot = await command("Page.captureScreenshot", {"format": "png"})
            (output / f"league-{name}.png").write_bytes(base64.b64decode(shot["data"]))
        await evaluate(
            "(()=>{const choose=(label,value)=>{const e=document.querySelector('[aria-label='+label+']');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,value);e.dispatchEvent(new Event('change',{bubbles:true}));};choose('Agent','pilot-7');choose('League','legacy');})()"
        )
        await until(
            "window.__gridQA.queue.entries.length===8&&document.querySelectorAll('.agent-pool-grid [role=row]').length===10"
        )
        assert await evaluate(
            "window.__gridQA.writes.length===1&&window.__gridQA.writes[0].body.entry.lease.mode==='continuous'"
        )
        await evaluate("document.querySelector('button[aria-label^=\"Replays \" ]').click()")
        await until("!!document.querySelector('.replay-storage-trigger')")
        assert await evaluate("!document.querySelector('.replay-storage-dialog').open")
        await evaluate("document.querySelector('.replay-storage-trigger').click()")
        assert await evaluate(
            "document.querySelector('.replay-storage-dialog').open&&!!document.querySelector('.replay-storage-dialog input[type=range]')"
        )
        assert await evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        shot = await command("Page.captureScreenshot", {"format": "png"})
        (output / "replay-settings-mobile.png").write_bytes(base64.b64decode(shot["data"]))
        assert await evaluate("window.__gridQA.writes.length===1")
        assert not errors, errors
        print(
            "PASS: desktop/mobile grid, blank first row, automatic single append, preserved roster, native replay-settings dialog; zero real API calls or browser exceptions."
        )


if __name__ == "__main__":
    browser.verify = verify
    browser.main()
