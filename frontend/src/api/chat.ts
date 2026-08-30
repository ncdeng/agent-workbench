import { BASE, asArray, asRecord, normalizeToolEvent, readJsonOrThrow } from './core';
import type { ChatHistoryMessage, StreamCallbacks } from './types';

export async function fetchChatHistory(): Promise<ChatHistoryMessage[]> {
  const res = await fetch(`${BASE}/api/chat/history`);
  const data = asRecord(await readJsonOrThrow(res));
  return asArray(data.messages).map((value) => {
    const message = asRecord(value);
    return {
      role: message.role === 'user' ? 'user' : 'assistant',
      content: typeof message.content === 'string' ? message.content : '',
      images: typeof message.images === 'number' ? message.images : undefined,
      toolEvents: Array.isArray(message.toolEvents) ? message.toolEvents.map(normalizeToolEvent) : undefined,
    };
  });
}

export async function streamChat(
  message: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
    signal,
  });
  if (!res.ok) {
    await readJsonOrThrow(res);
    throw new Error(`HTTP ${res.status}`);
  }
  if (!res.body) throw new Error('Response body unavailable');

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let done = false;
  let terminalFrame = false;

  const handleFrame = (frame: string) => {
    const dataLines: string[] = [];
    for (const line of frame.split('\n')) {
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
    }
    if (dataLines.length === 0) return;
    const payload = dataLines.join('\n');
    if (payload === '[DONE]') {
      done = true;
      callbacks.onDone?.();
      return;
    }
    let evt: Record<string, unknown>;
    try {
      evt = asRecord(JSON.parse(payload));
    } catch (err) {
      callbacks.onError?.(`Failed to parse SSE frame: ${(err as Error).message}`);
      return;
    }
    switch (evt.type) {
      case 'start':
        callbacks.onStart?.();
        break;
      case 'tool_event':
        if (evt.event) callbacks.onToolEvent?.(normalizeToolEvent(evt.event));
        break;
      case 'reply':
        terminalFrame = true;
        callbacks.onReply?.(typeof evt.content === 'string' ? evt.content : '');
        break;
      case 'error':
        terminalFrame = true;
        callbacks.onError?.(typeof evt.message === 'string' ? evt.message : 'Unknown error');
        break;
    }
  };

  try {
    while (!done) {
      const { value, done: streamDone } = await reader.read();
      if (value) {
        buffer += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (frame) handleFrame(frame);
          if (done) break;
        }
      }
      if (streamDone) {
        buffer += decoder.decode();
        if (buffer.trim()) handleFrame(buffer);
        break;
      }
    }
    if (!done && !terminalFrame) {
      callbacks.onError?.('Stream ended before [DONE]');
    }
  } finally {
    try { reader.releaseLock(); } catch {}
  }
}
