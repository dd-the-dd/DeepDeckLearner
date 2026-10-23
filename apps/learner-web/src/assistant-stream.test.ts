import {afterEach,expect,test,vi} from 'vitest';

afterEach(()=>{vi.unstubAllGlobals();vi.resetModules();});

test('the HTTP client delivers split UTF-8 SSE fragments before the final result',async()=>{
  let channel!:ReadableStreamDefaultController<Uint8Array>;
  const encoder=new TextEncoder();
  const stream=new ReadableStream<Uint8Array>({start(controller){channel=controller;}});
  const fetch=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({token:'fixture'}))).mockResolvedValueOnce(new Response(stream,{headers:{'content-type':'text/event-stream'}}));
  vi.stubGlobal('fetch',fetch);
  const {chatWithAgentStudio}=await import('./api');
  const events:unknown[]=[];let completed=false;
  const response=chatWithAgentStudio([{role:'user',content:'Hi'}],'agent','','',event=>events.push(event)).then(result=>{completed=true;return result;});
  const delta=encoder.encode('data: '+JSON.stringify({type:'delta',text:'Déjà {R}'})+'\n\n');
  for(const byte of delta)channel.enqueue(new Uint8Array([byte]));
  await vi.waitFor(()=>expect(events).toEqual([{type:'delta',text:'Déjà {R}'}]));
  expect(completed).toBe(false);
  const result={message:'Déjà {R}',toolEvents:[],agents:[]};
  channel.enqueue(encoder.encode('data: '+JSON.stringify({type:'done',result})+'\n\n'));channel.close();
  expect(await response).toEqual(result);
  expect(String(fetch.mock.calls[1][0])).toBe('/api/v1/agent-studio/chat/stream');
  expect(fetch).toHaveBeenCalledTimes(2);
});

test('an interrupted stream never silently resends a potentially mutating request',async()=>{
  const stream=new ReadableStream<Uint8Array>({start(controller){controller.close();}});
  const fetch=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({token:'fixture'}))).mockResolvedValueOnce(new Response(stream,{headers:{'content-type':'text/event-stream'}}));
  vi.stubGlobal('fetch',fetch);
  const {chatWithAgentStudio}=await import('./api');
  await expect(chatWithAgentStudio([{role:'user',content:'Create my deck'}],'deck','','',()=>undefined)).rejects.toThrow('Completed actions were kept');
  expect(fetch).toHaveBeenCalledTimes(2);
});
