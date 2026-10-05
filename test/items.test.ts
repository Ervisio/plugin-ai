import { test } from 'node:test';
import assert from 'node:assert/strict';
import { itemsFromMessages, reduceItems, type Item, type ToolItem } from '../src/chat/items.ts';
import { DENIED_TEXT, type AgentEvent } from '../src/chat/agent.ts';
import type { Msg } from '../src/chat/types.ts';

const call = { id: 't1', name: 'shell_exec', input: { command: 'ls' } };
const run = (events: AgentEvent[], start: Item[] = []) => events.reduce(reduceItems, start);

test('streamed text grows one assistant item and closes it', () => {
  const items = run([{ type: 'assistant-start' }, { type: 'text', delta: 'Hel' }, { type: 'text', delta: 'lo' }, { type: 'assistant-end', blocks: [] }]);
  assert.equal(items.length, 1);
  assert.deepEqual({ ...items[0], id: '' }, { kind: 'assistant', id: '', text: 'Hello', streaming: false });
});

test('a reply that is only tool calls leaves no empty assistant bubble', () => {
  const items = run([{ type: 'assistant-start' }, { type: 'tool-planning', name: 'x' }, { type: 'assistant-end', blocks: [] }]);
  assert.equal(items.length, 0);
});

test('a tool goes approval, running, done', () => {
  let items = run([{ type: 'tool-approval', call }]);
  assert.equal((items[0] as ToolItem).status, 'approval');
  items = run([{ type: 'tool-start', call, root: true }, { type: 'tool-progress', id: 't1', text: 'a' }, { type: 'tool-progress', id: 't1', text: 'b\nc' }], items);
  assert.equal(items.length, 1);
  assert.equal((items[0] as ToolItem).status, 'running');
  assert.equal((items[0] as ToolItem).root, true);
  assert.equal((items[0] as ToolItem).progress, 'a\nb\nc');
  items = run([{ type: 'tool-end', id: 't1', outcome: { text: 'out', isError: false, ms: 12 } }], items);
  assert.deepEqual({ ...(items[0] as ToolItem) }, { kind: 'tool', id: 't1', name: 'shell_exec', input: { command: 'ls' }, status: 'done', root: true, output: 'out', progress: undefined, ms: 12 });
});

test('errors and refusals', () => {
  let items = run([{ type: 'tool-start', call, root: false }, { type: 'tool-end', id: 't1', outcome: { text: 'exit 3', isError: true } }]);
  assert.equal((items[0] as ToolItem).status, 'error');
  items = run([{ type: 'tool-approval', call }, { type: 'tool-end', id: 't1', outcome: { text: DENIED_TEXT, isError: true, denied: true } }]);
  assert.equal((items[0] as ToolItem).status, 'denied');
});

test('progress is trimmed to the last lines', () => {
  const lines = Array.from({ length: 50 }, (_, i) => `l${i}`).join('\n');
  const items = run([{ type: 'tool-start', call, root: false }, { type: 'tool-progress', id: 't1', text: lines }]);
  const p = (items[0] as ToolItem).progress!.split('\n');
  assert.equal(p.length, 12);
  assert.equal(p[11], 'l49');
});

test('progress for an unknown tool is ignored; notices are appended', () => {
  assert.deepEqual(run([{ type: 'tool-progress', id: 'nope', text: 'x' }]), []);
  const items = run([{ type: 'notice', tone: 'warn', text: 'careful' }]);
  assert.equal(items[0].kind, 'notice');
});

test('a stored conversation becomes a transcript', () => {
  const msgs: Msg[] = [
    { role: 'user', content: [{ type: 'text', text: 'list files' }] },
    { role: 'assistant', content: [{ type: 'text', text: 'Sure.' }, { type: 'tool_use', id: 'a', name: 'fs_list', input: { path: '/' } }, { type: 'tool_use', id: 'b', name: 'shell_exec', input: { command: 'rm x', sudo: true } }, { type: 'tool_use', id: 'c', name: 'x', input: {} }] },
    { role: 'user', content: [
      { type: 'tool_result', tool_use_id: 'a', content: 'bin etc' },
      { type: 'tool_result', tool_use_id: 'b', content: DENIED_TEXT, is_error: true },
    ] },
    { role: 'assistant', content: [{ type: 'text', text: 'Done.' }] },
  ];
  const items = itemsFromMessages(msgs);
  assert.deepEqual(items.map((i) => i.kind), ['user', 'assistant', 'tool', 'tool', 'tool', 'assistant']);
  const tools = items.filter((i): i is ToolItem => i.kind === 'tool');
  assert.deepEqual(tools.map((t) => t.status), ['done', 'denied', 'error']);
  assert.equal(tools[1].root, true);
  assert.equal(tools[0].output, 'bin etc');
  assert.match(tools[2].output!, /Interrupted/);
});
