/** What the transcript shows, and how agent events and stored messages turn into it. Pure: no React, no SDK. */
import { DENIED_TEXT, type AgentEvent, type ToolCall } from './agent.ts';
import type { Block, Msg } from './types.ts';

export type ToolStatus = 'approval' | 'running' | 'done' | 'error' | 'denied';

export interface ToolItem {
  kind: 'tool';
  id: string;
  name: string;
  input: Record<string, unknown>;
  status: ToolStatus;
  root: boolean;
  output?: string;
  /** The latest lines the tool printed while it was running. */
  progress?: string;
  ms?: number;
}

export type Item =
  | { kind: 'user'; id: string; text: string }
  | { kind: 'assistant'; id: string; text: string; streaming: boolean }
  | ToolItem
  | { kind: 'notice'; id: string; tone: 'info' | 'warn' | 'err'; text: string };

let counter = 0;
export const itemId = (): string => `i${++counter}`;

const PROGRESS_LINES = 12;

function upsertTool(items: Item[], call: ToolCall, patch: Partial<ToolItem>): Item[] {
  const i = items.findIndex((x) => x.kind === 'tool' && x.id === call.id);
  if (i >= 0) return items.map((x, k) => (k === i ? ({ ...x, ...patch } as ToolItem) : x));
  return [...items, { kind: 'tool', id: call.id, name: call.name, input: call.input, status: 'running', root: false, ...patch }];
}

export function reduceItems(items: Item[], e: AgentEvent): Item[] {
  switch (e.type) {
    case 'text': {
      const last = items[items.length - 1];
      if (last?.kind === 'assistant' && last.streaming) return [...items.slice(0, -1), { ...last, text: last.text + e.delta }];
      return [...items, { kind: 'assistant', id: itemId(), text: e.delta, streaming: true }];
    }
    case 'assistant-end': {
      const last = items[items.length - 1];
      if (last?.kind === 'assistant' && last.streaming) return [...items.slice(0, -1), { ...last, streaming: false }];
      return items;
    }
    case 'tool-approval':
      return upsertTool(items, e.call, { status: 'approval' });
    case 'tool-start':
      return upsertTool(items, e.call, { status: 'running', root: e.root });
    case 'tool-progress': {
      const i = items.findIndex((x) => x.kind === 'tool' && x.id === e.id);
      if (i < 0) return items;
      const t = items[i] as ToolItem;
      const lines = ((t.progress ? t.progress + '\n' : '') + e.text).split('\n').slice(-PROGRESS_LINES).join('\n');
      return items.map((x, k) => (k === i ? { ...t, progress: lines } : x));
    }
    case 'tool-end': {
      const i = items.findIndex((x) => x.kind === 'tool' && x.id === e.id);
      if (i < 0) return items;
      const t = items[i] as ToolItem;
      const status: ToolStatus = e.outcome.denied ? 'denied' : e.outcome.isError ? 'error' : 'done';
      return items.map((x, k) => (k === i ? { ...t, status, output: e.outcome.text, ms: e.outcome.ms, progress: undefined } : x));
    }
    case 'notice':
      return [...items, { kind: 'notice', id: itemId(), tone: e.tone, text: e.text }];
    default:
      return items;
  }
}

/** Rebuild the transcript of a stored conversation. */
export function itemsFromMessages(messages: Msg[]): Item[] {
  const results = new Map<string, Extract<Block, { type: 'tool_result' }>>();
  for (const m of messages) for (const b of m.content) if (b.type === 'tool_result') results.set(b.tool_use_id, b);
  const items: Item[] = [];
  for (const m of messages) {
    for (const b of m.content) {
      if (b.type === 'text') {
        if (b.text) items.push(m.role === 'user' ? { kind: 'user', id: itemId(), text: b.text } : { kind: 'assistant', id: itemId(), text: b.text, streaming: false });
      } else if (b.type === 'tool_use') {
        const r = results.get(b.id);
        const status: ToolStatus = !r ? 'error' : r.content === DENIED_TEXT ? 'denied' : r.is_error ? 'error' : 'done';
        items.push({
          kind: 'tool', id: b.id, name: b.name, input: b.input, status, root: b.input.sudo === true,
          output: r ? r.content : 'Interrupted before it finished.',
        });
      }
    }
  }
  return items;
}
