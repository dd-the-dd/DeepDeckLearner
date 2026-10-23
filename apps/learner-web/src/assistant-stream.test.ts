import {afterEach,expect,test,vi} from 'vitest';
import {readAssistantStream} from './assistant-stream';

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

const reply = {message:'Done.',toolEvents:[],agents:[]};
function fixture(chunks:string[]) {
  const encoder=new TextEncoder();
  return new Response(new ReadableStream<Uint8Array>({start(channel){
    for(const chunk of chunks) channel.enqueue(encoder.encode(chunk));
    channel.close();
  }}),{headers:{'Content-Type':'text/event-stream; charset=utf-8'}});
}

test.each(['\n','\r\n','\r'])('SSE accepts %j lines, comments, multiple data lines and split CRLF',async(newline)=>{
  const source=': keepalive'+newline+newline+'event: message'+newline+'data: {"type":"delta",'+newline+'data: "text":"Bonjour {U}"}'+newline+newline+
    'data: '+JSON.stringify({type:'done',result:reply})+newline+newline;
  const events:unknown[]=[];
  expect(await readAssistantStream(fixture([...source]),event=>events.push(event))).toEqual(reply);
  expect(events).toEqual([{type:'delta',text:'Bonjour {U}'},{type:'done',result:reply}]);
});

test.each([
  'PRIVATE_PROVIDER_DIAGNOSTIC',
  '{"type":"delta","text":17}',
  '{"type":"tool","event":{"tool":"create_deck","ok":"true"}}',
  '{"type":"done","result":{"message":"Fake completion"}}',
  '{"type":"unsupported","diagnostic":"PRIVATE_PROVIDER_DIAGNOSTIC"}',
])('malformed events are sanitized instead of presented as successful work: %s',async(data)=>{
  const seen=vi.fn();
  await expect(readAssistantStream(fixture(['data: '+data+'\n\n']),seen)).rejects.toThrow('incompatible stream');
  expect(seen).not.toHaveBeenCalled();
});

test('a completed action stays delivered once before a late provider error',async()=>{
  const tool={type:'tool',event:{tool:'create_deck',ok:true,result:{id:'localdeck-123456789abc'}}};
  const events:unknown[]=[];
  await expect(readAssistantStream(fixture([
    'data: '+JSON.stringify(tool)+'\n\n',
    'data: {"type":"error","message":"Connection interrupted. Review completed actions."}\n\n',
  ]),event=>events.push(event))).rejects.toThrow('Review completed actions');
  expect(events[0]).toEqual(tool);
  expect(events).toHaveLength(2);
});

test('an unfinished frame never becomes a successful final response',async()=>{
  await expect(readAssistantStream(fixture(['data: '+JSON.stringify({type:'done',result:reply})]),()=>undefined)).rejects.toThrow('ended early');
});

test('text output is bounded across fragments, not just within each network frame',async()=>{
  const onEvent=vi.fn();
  await expect(readAssistantStream(fixture(Array.from({length:11},()=> 'data: '+JSON.stringify({type:'delta',text:'a'.repeat(10_000)})+'\n\n')),onEvent)).rejects.toThrow('incompatible stream');
  expect(onEvent).toHaveBeenCalledTimes(10);
});

test('a socket read error is sanitized and releases the stream reader',async()=>{
  const stream=new ReadableStream<Uint8Array>({start(channel){channel.error(new Error('PRIVATE_PROVIDER_DIAGNOSTIC'));}});
  await expect(readAssistantStream(new Response(stream,{headers:{'Content-Type':'text/event-stream'}}),()=>undefined)).rejects.toThrow('ended early');
  expect(stream.locked).toBe(false);
});
