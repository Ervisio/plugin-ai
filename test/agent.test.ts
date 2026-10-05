import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compactMessages, runTurn, type AgentDeps, type AgentEvent, type ToolCall } from '../src/chat/agent.ts';
import { ProviderError, type Block, type Msg, type Provider, type StreamRequest, type StreamResult, type ToolOutcome } from '../src/chat/types.ts';

const usage = { input: 10, output: 5 };
const say = (text: string): StreamResult => ({ blocks: [{ type: 'text', text }], stop: 'end', usage });
const call = (id: string, name: string, input: Record<string, unknown> = {}, text = ''): StreamResult => ({
  blocks: [...(text ? [{ type: 'text', text } as Block] : []), { type: 'tool_use', id, name, input }],
  stop: 'tool_use',
  usage,
});

/** A provider that plays back a script; each entry is a result or an error. It remembers what it was sent. */
function scripted(script: (StreamResult | Error)[]) {
  const sent: StreamRequest[] = [];
  const provider: Provider = {
    async stream(req, h) {
      sent.push(JSON.parse(JSON.stringify({ ...req, signal: undefined })));
      const next = script.shift();
      if (!next) throw new Error('script ended');
      if (next instanceof Error) throw next;
      for (const b of next.blocks) {
        if (b.type === 'text') h.onText(b.text);
        if (b.type === 'tool_use') h.onToolStart?.(b.name);
      }
      return next;
    },
    async listModels() { return []; },
  };
  return { provider, sent };
}

function setup(script: (StreamResult | Error)[], over: Partial<AgentDeps> = {}) {
  const { provider, sent } = scripted(script);
  const events: AgentEvent[] = [];
  const ran: { call: ToolCall; root: boolean }[] = [];
  const asked: ToolCall[] = [];
  const ac = new AbortController();
  const deps: AgentDeps = {
    provider, model: 'm', system: 'S', tools: [], maxTokens: 100, maxSteps: 10, signal: ac.signal,
    mode: () => 'auto', root: () => false,
    classify: async () => false,
    runTool: async (c, o): Promise<ToolOutcome> => { ran.push({ call: c, root: o.root }); o.onProgress('working'); return { text: `out of ${c.name}`, isError: false }; },
    approve: async (c) => { asked.push(c); return 'allow'; },
    emit: (e) => events.push(e),
    sleep: async () => undefined,
    ...over,
  };
  return { deps, events, ran, asked, sent, ac };
}

const user = (t: string): Msg => ({ role: 'user', content: [{ type: 'text', text: t }] });

test('a plain answer ends the turn', async () => {
  const { deps, events, sent } = setup([say('Hello')]);
  const r = await runTurn([user('hi')], deps);
  assert.equal(r.end, 'end');
  assert.deepEqual(r.messages.map((m) => m.role), ['user', 'assistant']);
  assert.deepEqual(events.map((e) => e.type), ['assistant-start', 'text', 'usage', 'assistant-end']);
  assert.deepEqual(r.usage, usage);
  assert.equal(sent[0].system, 'S');
});

test('a tool call is run and its result goes back to the model, which then answers', async () => {
  const { deps, events, ran, sent } = setup([call('t1', 'shell_exec', { command: 'id' }, 'Checking.'), say('You are root.')]);
  const r = await runTurn([user('who am i')], deps);
  assert.equal(r.end, 'end');
  assert.deepEqual(ran.map((x) => x.call.name), ['shell_exec']);
  assert.deepEqual(r.messages.map((m) => m.role), ['user', 'assistant', 'user', 'assistant']);
  assert.deepEqual(r.messages[2].content, [{ type: 'tool_result', tool_use_id: 't1', content: 'out of shell_exec' }]);
  assert.equal(sent.length, 2);
  assert.deepEqual(sent[1].messages[2].content[0], { type: 'tool_result', tool_use_id: 't1', content: 'out of shell_exec' });
  assert.deepEqual(r.usage, { input: 20, output: 10 });
  const types = events.map((e) => e.type);
  assert.ok(types.indexOf('tool-start') < types.indexOf('tool-progress') && types.indexOf('tool-progress') < types.indexOf('tool-end'));
  assert.ok(types.includes('tool-planning'));
});

test('several calls in one reply run in order and come back as one user message', async () => {
  const two: StreamResult = { blocks: [{ type: 'tool_use', id: 'a', name: 'fs_read', input: {} }, { type: 'tool_use', id: 'b', name: 'fs_list', input: {} }], stop: 'tool_use', usage };
  const { deps, ran } = setup([two, say('done')]);
  const r = await runTurn([user('x')], deps);
  assert.deepEqual(ran.map((x) => x.call.id), ['a', 'b']);
  assert.deepEqual((r.messages[2].content as Extract<Block, { type: 'tool_result' }>[]).map((b) => b.tool_use_id), ['a', 'b']);
});

test('ask mode: waits for the person; allow runs it', async () => {
  const { deps, ran, asked, events } = setup([call('t1', 'fs_read'), say('ok')], { mode: () => 'ask' });
  await runTurn([user('x')], deps);
  assert.equal(asked.length, 1);
  assert.equal(ran.length, 1);
  assert.ok(events.findIndex((e) => e.type === 'tool-approval') < events.findIndex((e) => e.type === 'tool-start'));
});

test('ask mode: a refusal is told to the model and nothing runs', async () => {
  const { deps, ran, events, sent } = setup([call('t1', 'shell_exec', { command: 'rm x' }), say('Understood.')], { mode: () => 'ask', approve: async () => 'deny' });
  const r = await runTurn([user('x')], deps);
  assert.equal(ran.length, 0);
  const res = r.messages[2].content[0] as Extract<Block, { type: 'tool_result' }>;
  assert.equal(res.is_error, true);
  assert.match(res.content, /did not allow/);
  assert.equal(sent.length, 2);
  const end = events.find((e) => e.type === 'tool-end') as Extract<AgentEvent, { type: 'tool-end' }>;
  assert.equal(end.outcome.isError, true);
});

test('auto-read mode: reads run at once, changes ask first', async () => {
  const reads = new Set(['fs_read']);
  const mk = (name: string) => setup([call('t1', name), say('ok')], { mode: () => 'auto-read', classify: async (c) => reads.has(c.name) });
  let t = mk('fs_read');
  await runTurn([user('x')], t.deps);
  assert.equal(t.asked.length, 0);
  assert.equal(t.ran.length, 1);
  t = mk('fs_write');
  await runTurn([user('x')], t.deps);
  assert.equal(t.asked.length, 1);
  assert.equal(t.ran.length, 1);
});

test('"always allow" skips the question (and the classification) in any mode', async () => {
  let classified = 0;
  for (const mode of ['ask', 'auto-read'] as const) {
    const t = setup([call('t1', 'fs_write'), call('t2', 'shell_exec'), say('ok')], {
      mode: () => mode,
      classify: async () => { classified++; return false; },
      preApproved: (c) => c.name === 'fs_write',
    });
    await runTurn([user('x')], t.deps);
    assert.deepEqual(t.asked.map((c) => c.name), ['shell_exec'], mode);
    assert.equal(t.ran.length, 2);
  }
  assert.equal(classified, 1); // only shell_exec in auto-read was classified
});

test('auto-read mode: if the engine cannot classify, it asks', async () => {
  const t = setup([call('t1', 'fs_read'), say('ok')], { mode: () => 'auto-read', classify: async () => { throw new Error('engine down'); } });
  await runTurn([user('x')], t.deps);
  assert.equal(t.asked.length, 1);
});

test('auto mode never asks, and the mode is read every time', async () => {
  let mode: 'auto' | 'ask' = 'auto';
  const t = setup([call('a', 'x'), call('b', 'y'), say('ok')], {
    mode: () => mode,
    runTool: async (c) => { mode = 'ask'; return { text: 'r', isError: false }; },
  });
  await runTurn([user('x')], t.deps);
  assert.equal(t.asked.length, 1); // the second call saw the new mode
  assert.equal(t.asked[0].id, 'b');
});

test('root: the Root switch and sudo=true both run as root', async () => {
  let t = setup([call('a', 'fs_read', { path: '/x' }), say('ok')], { root: () => true });
  await runTurn([user('x')], t.deps);
  assert.equal(t.ran[0].root, true);
  t = setup([call('a', 'fs_read', { path: '/x', sudo: true }), say('ok')]);
  await runTurn([user('x')], t.deps);
  assert.equal(t.ran[0].root, true);
  t = setup([call('a', 'fs_read', { path: '/x' }), say('ok')]);
  await runTurn([user('x')], t.deps);
  assert.equal(t.ran[0].root, false);
});

test('a tool that throws becomes an error result, not a crash', async () => {
  const t = setup([call('a', 'x'), say('sorry')], { runTool: async () => { throw new Error('unlock refused'); } });
  const r = await runTurn([user('x')], t.deps);
  const res = r.messages[2].content[0] as Extract<Block, { type: 'tool_result' }>;
  assert.equal(res.is_error, true);
  assert.match(res.content, /unlock refused/);
  assert.equal(r.end, 'end');
});

test('stopping during a tool: the calls that did not run still get a result', async () => {
  const two: StreamResult = { blocks: [{ type: 'tool_use', id: 'a', name: 'slow', input: {} }, { type: 'tool_use', id: 'b', name: 'after', input: {} }], stop: 'tool_use', usage };
  const t = setup([two, say('never')], {});
  t.deps.runTool = async () => { t.ac.abort(); return { text: 'killed', isError: true }; };
  const r = await runTurn([user('x')], t.deps);
  assert.equal(r.end, 'aborted');
  const results = r.messages[2].content as Extract<Block, { type: 'tool_result' }>[];
  assert.deepEqual(results.map((b) => b.tool_use_id), ['a', 'b']);
  assert.match(results[1].content, /Interrupted/);
  assert.equal(t.sent.length, 1);
});

test('stopping while the model is answering', async () => {
  const ac = new AbortController();
  const t = setup([new ProviderError('aborted', 'aborted')]);
  ac.abort();
  const r = await runTurn([user('x')], { ...t.deps, signal: ac.signal });
  assert.equal(r.end, 'aborted');
  assert.equal(r.messages.length, 1);
});

test('the step limit ends a runaway loop', async () => {
  const script = Array.from({ length: 20 }, (_, i) => call(`t${i}`, 'x'));
  const t = setup(script, { maxSteps: 3 });
  const r = await runTurn([user('x')], t.deps);
  assert.equal(r.end, 'steps');
  assert.equal(t.sent.length, 3);
  assert.ok(t.events.some((e) => e.type === 'notice' && /Stopped after 3 steps/.test(e.text)));
});

test('rate limits and overload are retried with the API\'s wait; other errors are not', async () => {
  const waits: number[] = [];
  let t = setup([new ProviderError('rate', 'Slow down.', 429, 4), new ProviderError('overloaded', 'Busy.', 529), say('finally')], { sleep: async (ms) => { waits.push(ms); } });
  let r = await runTurn([user('x')], t.deps);
  assert.equal(r.end, 'end');
  assert.deepEqual(waits, [4000, 4000]);   // 4 s from retry-after, then 2**(1+1)=4 s
  t = setup([new ProviderError('auth', 'Incorrect API key.', 401), say('no')]);
  r = await runTurn([user('x')], t.deps);
  assert.equal(r.end, 'error');
  assert.equal(t.sent.length, 1);
  assert.ok(t.events.some((e) => e.type === 'notice' && e.tone === 'err' && e.text === 'Incorrect API key.'));
  t = setup(Array.from({ length: 6 }, () => new ProviderError('server', 'boom', 500)), { sleep: async () => undefined });
  r = await runTurn([user('x')], t.deps);
  assert.equal(r.end, 'error');
  assert.equal(t.sent.length, 4); // the first try and three retries
});

test('a reply cut at the token limit is reported', async () => {
  const t = setup([{ blocks: [{ type: 'text', text: 'partial' }], stop: 'max_tokens', usage }]);
  const r = await runTurn([user('x')], t.deps);
  assert.equal(r.end, 'max_tokens');
  assert.ok(t.events.some((e) => e.type === 'notice' && /token limit/.test(e.text)));
});

test('an empty answer is noticed and does not leave an empty message behind', async () => {
  const t = setup([{ blocks: [], stop: 'end', usage }]);
  const r = await runTurn([user('x')], t.deps);
  assert.equal(r.messages.length, 1);
  assert.ok(t.events.some((e) => e.type === 'notice'));
});

test('the history is not changed in place', async () => {
  const h = [user('x')];
  const t = setup([say('y')]);
  await runTurn(h, t.deps);
  assert.equal(h.length, 1);
});

// --- compaction -----------------------------------------------------------------------------------------------

const withResult = (i: number, size: number): Msg[] => [
  { role: 'assistant', content: [{ type: 'tool_use', id: `t${i}`, name: 'x', input: {} }] },
  { role: 'user', content: [{ type: 'tool_result', tool_use_id: `t${i}`, content: `START${i}-` + 'x'.repeat(size) + '-END' }] },
];

test('compaction leaves a short conversation alone', () => {
  const m = [user('hi'), ...withResult(1, 100)];
  assert.equal(compactMessages(m, 100000), m);
});

test('compaction shortens the oldest tool output first, keeps start and end, spares the last messages', () => {
  const m: Msg[] = [user('go')];
  for (let i = 0; i < 8; i++) m.push(...withResult(i, 20000));
  const out = compactMessages(m, 90000);
  const text = (i: number) => (out[i].content[0] as Extract<Block, { type: 'tool_result' }>).content;
  assert.ok(text(2).length < 2500);
  assert.ok(text(2).startsWith('START0-'));
  assert.ok(text(2).endsWith('-END'));
  assert.match(text(2), /left out to save space/);
  assert.ok(text(out.length - 1).length > 20000); // the newest result is whole
  assert.equal(out.length, m.length);
  assert.ok((m[2].content[0] as Extract<Block, { type: 'tool_result' }>).content.length > 20000); // the original is untouched
  assert.ok(out.reduce((n, x) => n + x.content.reduce((k, b) => k + (b.type === 'tool_result' ? b.content.length : 0), 0), 0) <= 90000 + 6 * 20010);
});
