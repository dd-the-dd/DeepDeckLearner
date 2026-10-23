import type { AssistantStreamEvent, AssistantToolEvent, ProgrammaticAgent } from './api';

export type AssistantReply = { message: string; toolEvents: AssistantToolEvent[]; agents: ProgrammaticAgent[] };

const incompatible = () => new Error('The assistant sent an incompatible stream. Completed actions were kept; review them before sending again.');
const interrupted = () => new Error('The assistant connection ended early. Completed actions were kept; do not repeat the request blindly.');
const maxFrame = 2_000_000;
const maxText = 100_000;

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function tool(value: unknown): value is AssistantToolEvent {
  return record(value) && typeof value.tool === 'string' && value.tool.length <= 200 && typeof value.ok === 'boolean';
}

function eventFrom(data: string): AssistantStreamEvent {
  let value: unknown;
  try { value = JSON.parse(data); } catch { throw incompatible(); }
  if (!record(value)) throw incompatible();
  switch (value.type) {
    case 'delta':
      if (typeof value.text === 'string' && value.text.length <= maxText) return { type: 'delta', text: value.text };
      break;
    case 'status':
    case 'error':
      if (typeof value.message === 'string' && value.message.length <= 2_000) return { type: value.type, message: value.message };
      break;
    case 'tool':
      if (tool(value.event)) return { type: 'tool', event: value.event };
      break;
    case 'done': {
      const result = value.result;
      if (record(result) && typeof result.message === 'string' && result.message.length <= maxText &&
        Array.isArray(result.toolEvents) && result.toolEvents.length <= 512 && result.toolEvents.every(tool) && Array.isArray(result.agents)) {
        return { type: 'done', result: result as AssistantReply };
      }
    }
  }
  // Never echo malformed frames: they may contain provider diagnostics/secrets.
  throw incompatible();
}

/** Read real SSE fragments. A closed connection is never a completed turn.
 * No automatic retry: tools may already have saved a deck or started a match.
 */
export async function readAssistantStream(response: Response, onEvent: (event: AssistantStreamEvent) => void): Promise<AssistantReply> {
  if (!response.body || response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'text/event-stream') {
    throw new Error('Streaming is unavailable. No request was retried; check completed actions before sending again.');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', data: string[] = [], frameLength = 0, textLength = 0;
  try {
    while (true) {
      let part: ReadableStreamReadResult<Uint8Array>;
      try { part = await reader.read(); } catch { throw interrupted(); }
      buffer += decoder.decode(part.value, { stream: !part.done });
      if (buffer.length + frameLength > maxFrame) throw incompatible();
      let end: number;
      while ((end = buffer.search(/[\r\n]/)) >= 0) {
        // CRLF may itself be split across two network chunks.
        if (buffer[end] === '\r' && end === buffer.length - 1 && !part.done) break;
        const line = buffer.slice(0, end);
        buffer = buffer.slice(end + (buffer[end] === '\r' && buffer[end + 1] === '\n' ? 2 : 1));
        if (line === '') {
          frameLength = 0;
          if (!data.length) continue; // comments/keepalives have no data
          const event = eventFrom(data.join('\n'));
          data = [];
          if (event.type === 'delta') {
            textLength += event.text.length;
            if (textLength > maxText) throw incompatible();
          }
          onEvent(event);
          if (event.type === 'done') return event.result;
          if (event.type === 'error') throw new Error(event.message);
        } else if (line.startsWith('data:')) {
          const value = line.slice(5).replace(/^ /, '');
          data.push(value);
          frameLength += value.length;
          if (frameLength > maxFrame) throw incompatible();
        }
      }
      if (part.done) throw interrupted();
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
