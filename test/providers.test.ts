import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AnthropicProvider, OpenAIProvider, createProvider, plainSchema, toOpenAIMessages, toOpenAITools } from '../src/chat/providers.ts';
import { ProviderError, type Msg, type ProviderConfig, type StreamRequest, type ToolDef } from '../src/chat/types.ts';
import { fakeFetch, json, sse, streamOf } from './helpers.ts';

const TOOLS: ToolDef[] = [
  { name: 'shell_exec', description: 'Run', inputSchema: { type: 'object', properties: { command: { type: 'string' } }, required: ['command'], additionalProperties: false } },
  { name: 'fs_read', description: 'Read', inputSchema: { type: 'object', properties: { path: { type: 'string' } } } },
];
const MSGS: Msg[] = [{ role: 'user', content: [{ type: 'text', text: 'hi' }] }];
const req = (extra: Partial<StreamRequest> = {}): StreamRequest => ({ system: 'SYS', messages: MSGS, tools: TOOLS, model: 'm1', maxTokens: 1000, ...extra });
const A: ProviderConfig = { id: 'a', kind: 'anthropic', name: 'A', baseUrl: 'https://api.anthropic.com/', apiKey: 'sk-ant-xyz' };
const O: ProviderConfig = { id: 'o', kind: 'openai', name: 'O', baseUrl: 'https://api.example.com/v1', apiKey: 'sk-oai' };

function collect() {
  const text: string[] = [];
  const tools: string[] = [];
  return { text, tools, h: { onText: (d: string) => text.push(d), onToolStart: (n: string) => tools.push(n) } };
}

// --- Anthropic ----------------------------------------------------------------------------------------------

const anthropicToolStream = () => sse([
  { event: 'message_start', data: { type: 'message_start', message: { usage: { input_tokens: 25, cache_read_input_tokens: 10, output_tokens: 1 } } } },
  { event: 'content_block_start', data: { type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } } },
  { event: 'content_block_delta', data: { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: 'Let me ' } } },
  { event: 'content_block_delta', data: { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: 'look.' } } },
  { event: 'content_block_stop', data: { type: 'content_block_stop', index: 0 } },
  { event: 'content_block_start', data: { type: 'content_block_start', index: 1, content_block: { type: 'tool_use', id: 'toolu_1', name: 'shell_exec', input: {} } } },
  { event: 'content_block_delta', data: { type: 'content_block_delta', index: 1, delta: { type: 'input_json_delta', partial_json: '{"comm' } } },
  { event: 'content_block_delta', data: { type: 'content_block_delta', index: 1, delta: { type: 'input_json_delta', partial_json: 'and": "ls -la"}' } } },
  { event: 'content_block_stop', data: { type: 'content_block_stop', index: 1 } },
  { event: 'ping', data: { type: 'ping' } },
  { event: 'message_delta', data: { type: 'message_delta', delta: { stop_reason: 'tool_use' }, usage: { output_tokens: 42 } } },
  { event: 'message_stop', data: { type: 'message_stop' } },
]);

test('anthropic: streams text, assembles a tool call from JSON fragments, reports usage', async () => {
  const { f, seen } = fakeFetch([anthropicToolStream()]);
  const c = collect();
  const r = await new AnthropicProvider(A, f).stream(req(), c.h);
  assert.deepEqual(c.text, ['Let me ', 'look.']);
  assert.deepEqual(c.tools, ['shell_exec']);
  assert.deepEqual(r.blocks, [{ type: 'text', text: 'Let me look.' }, { type: 'tool_use', id: 'toolu_1', name: 'shell_exec', input: { command: 'ls -la' } }]);
  assert.equal(r.stop, 'tool_use');
  assert.deepEqual(r.usage, { input: 35, output: 42, cacheRead: 10 });
  // the request
  assert.equal(seen[0].url, 'https://api.anthropic.com/v1/messages');
  const h = seen[0].init.headers as Record<string, string>;
  assert.equal(h['x-api-key'], 'sk-ant-xyz');
  assert.equal(h['anthropic-version'], '2023-06-01');
  assert.equal(h['anthropic-dangerous-direct-browser-access'], 'true');
  assert.equal(seen[0].body.stream, true);
  assert.equal(seen[0].body.max_tokens, 1000);
});

test('anthropic: caches the system prompt, the tools and the newest message', async () => {
  const { f, seen } = fakeFetch([sse([{ data: { type: 'message_delta', delta: { stop_reason: 'end_turn' } } }])]);
  await new AnthropicProvider(A, f).stream(req(), collect().h);
  const b = seen[0].body;
  assert.deepEqual(b.system, [{ type: 'text', text: 'SYS', cache_control: { type: 'ephemeral' } }]);
  assert.equal(b.tools[0].cache_control, undefined);
  assert.deepEqual(b.tools[1].cache_control, { type: 'ephemeral' });
  assert.deepEqual(b.tools[0].input_schema.required, ['command']); // schemas are sent as they are
  assert.deepEqual(b.messages[0].content[0], { type: 'text', text: 'hi', cache_control: { type: 'ephemeral' } });
});

test('anthropic: tool results go back as tool_result blocks, errors flagged, empty text dropped', async () => {
  const msgs: Msg[] = [
    { role: 'user', content: [{ type: 'text', text: 'go' }] },
    { role: 'assistant', content: [{ type: 'text', text: '' }, { type: 'tool_use', id: 't1', name: 'fs_read', input: { path: '/x' } }] },
    { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: 'nope', is_error: true }] },
  ];
  const { f, seen } = fakeFetch([sse([{ data: { type: 'message_delta', delta: { stop_reason: 'end_turn' } } }])]);
  await new AnthropicProvider(A, f).stream(req({ messages: msgs, tools: [] }), collect().h);
  const b = seen[0].body;
  assert.equal(b.tools, undefined);
  assert.deepEqual(b.messages[1].content, [{ type: 'tool_use', id: 't1', name: 'fs_read', input: { path: '/x' } }]);
  assert.deepEqual(b.messages[2].content[0], { type: 'tool_result', tool_use_id: 't1', content: 'nope', is_error: true, cache_control: { type: 'ephemeral' } });
});

test('anthropic: a tool call cut off by the token limit is dropped and reported as max_tokens', async () => {
  const { f } = fakeFetch([sse([
    { data: { type: 'content_block_start', index: 0, content_block: { type: 'tool_use', id: 'x', name: 'fs_write', input: {} } } },
    { data: { type: 'content_block_delta', index: 0, delta: { type: 'input_json_delta', partial_json: '{"path": "/a", "content": "abc' } } },
    { data: { type: 'content_block_stop', index: 0 } },
    { data: { type: 'message_delta', delta: { stop_reason: 'tool_use' } } },
  ])]);
  const r = await new AnthropicProvider(A, f).stream(req(), collect().h);
  assert.deepEqual(r.blocks, []);
  assert.equal(r.stop, 'max_tokens');
});

test('anthropic: a tool with no arguments', async () => {
  const { f } = fakeFetch([sse([
    { data: { type: 'content_block_start', index: 0, content_block: { type: 'tool_use', id: 'x', name: 'server_context', input: {} } } },
    { data: { type: 'content_block_stop', index: 0 } },
    { data: { type: 'message_delta', delta: { stop_reason: 'tool_use' } } },
  ])]);
  const r = await new AnthropicProvider(A, f).stream(req(), collect().h);
  assert.deepEqual(r.blocks, [{ type: 'tool_use', id: 'x', name: 'server_context', input: {} }]);
});

test('anthropic: http errors become typed errors with the API message', async () => {
  for (const [status, kind] of [[401, 'auth'], [403, 'auth'], [429, 'rate'], [529, 'overloaded'], [400, 'bad_request'], [500, 'server'], [418, 'other']] as const) {
    const { f } = fakeFetch([json(status, { type: 'error', error: { type: 'x', message: `problem ${status}` } }, status === 429 ? { 'retry-after': '7' } : {})]);
    await assert.rejects(new AnthropicProvider(A, f).stream(req(), collect().h), (e: ProviderError) => {
      assert.equal(e.kind, kind);
      assert.equal(e.status, status);
      assert.equal(e.message, `problem ${status}`);
      if (status === 429) assert.equal(e.retryAfter, 7);
      return true;
    });
  }
});

test('anthropic: an error event inside the stream, a network failure, an abort', async () => {
  const { f } = fakeFetch([sse([{ data: { type: 'error', error: { type: 'overloaded_error', message: 'busy' } } }])]);
  await assert.rejects(new AnthropicProvider(A, f).stream(req(), collect().h), (e: ProviderError) => e.kind === 'overloaded' && e.message === 'busy');
  const net = fakeFetch([new TypeError('Failed to fetch')]);
  await assert.rejects(new AnthropicProvider(A, net.f).stream(req(), collect().h), (e: ProviderError) => e.kind === 'network' && /CORS/.test(e.message));
  const ac = new AbortController();
  ac.abort();
  const ab = fakeFetch([Object.assign(new Error('x'), { name: 'AbortError' })]);
  await assert.rejects(new AnthropicProvider(A, ab.f).stream(req({ signal: ac.signal }), collect().h), (e: ProviderError) => e.kind === 'aborted');
});

test('anthropic: model list', async () => {
  const { f, seen } = fakeFetch([json(200, { data: [{ id: 'claude-a' }, { id: 'claude-b' }] })]);
  assert.deepEqual(await new AnthropicProvider(A, f).listModels(), ['claude-a', 'claude-b']);
  assert.equal(seen[0].url, 'https://api.anthropic.com/v1/models?limit=100');
});

// --- OpenAI-style ---------------------------------------------------------------------------------------------

test('openai: streams text and reports usage', async () => {
  const { f, seen } = fakeFetch([sse([
    { data: { choices: [{ delta: { role: 'assistant', content: 'Hel' } }] } },
    { data: { choices: [{ delta: { content: 'lo' } }] } },
    { data: { choices: [{ delta: {}, finish_reason: 'stop' }] } },
    { data: { choices: [], usage: { prompt_tokens: 12, completion_tokens: 3, prompt_tokens_details: { cached_tokens: 4 } } } },
    { data: '[DONE]' },
  ])]);
  const c = collect();
  const r = await new OpenAIProvider(O, f).stream(req({ tools: [] }), c.h);
  assert.deepEqual(c.text, ['Hel', 'lo']);
  assert.deepEqual(r, { blocks: [{ type: 'text', text: 'Hello' }], stop: 'end', usage: { input: 12, output: 3, cacheRead: 4 } });
  assert.equal(seen[0].url, 'https://api.example.com/v1/chat/completions');
  assert.equal((seen[0].init.headers as Record<string, string>).authorization, 'Bearer sk-oai');
  assert.equal(seen[0].body.tools, undefined);
  assert.deepEqual(seen[0].body.stream_options, { include_usage: true });
});

test('openai: parallel tool calls are assembled by index', async () => {
  const { f } = fakeFetch([sse([
    { data: { choices: [{ delta: { tool_calls: [{ index: 0, id: 'c0', type: 'function', function: { name: 'fs_read', arguments: '' } }] } }] } },
    { data: { choices: [{ delta: { tool_calls: [{ index: 1, id: 'c1', type: 'function', function: { name: 'shell_exec', arguments: '{"comm' } }] } }] } },
    { data: { choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: '{"path":' } }] } }] } },
    { data: { choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: '"/etc/hosts"}' } }, { index: 1, function: { arguments: 'and":"id"}' } }] } }] } },
    { data: { choices: [{ delta: {}, finish_reason: 'tool_calls' }] } },
  ])]);
  const c = collect();
  const r = await new OpenAIProvider(O, f).stream(req(), c.h);
  assert.deepEqual(c.tools, ['fs_read', 'shell_exec']);
  assert.deepEqual(r.blocks, [
    { type: 'tool_use', id: 'c0', name: 'fs_read', input: { path: '/etc/hosts' } },
    { type: 'tool_use', id: 'c1', name: 'shell_exec', input: { command: 'id' } },
  ]);
  assert.equal(r.stop, 'tool_use');
});

test('openai: tolerates servers that send arguments as an object, or say stop with tool calls', async () => {
  const { f } = fakeFetch([sse([
    { data: { choices: [{ delta: { tool_calls: [{ index: 0, id: 'x', function: { name: 'fs_read', arguments: { path: '/a' } } }] }, finish_reason: 'stop' }] } },
  ])]);
  const r = await new OpenAIProvider(O, f).stream(req(), collect().h);
  assert.deepEqual(r.blocks, [{ type: 'tool_use', id: 'x', name: 'fs_read', input: { path: '/a' } }]);
  assert.equal(r.stop, 'tool_use');
});

test('openai: a tool call with empty arguments, and one cut off', async () => {
  let r = await new OpenAIProvider(O, fakeFetch([sse([{ data: { choices: [{ delta: { tool_calls: [{ index: 0, id: 'x', function: { name: 'system_info', arguments: '' } }] }, finish_reason: 'tool_calls' }] } }])]).f).stream(req(), collect().h);
  assert.deepEqual(r.blocks, [{ type: 'tool_use', id: 'x', name: 'system_info', input: {} }]);
  r = await new OpenAIProvider(O, fakeFetch([sse([{ data: { choices: [{ delta: { tool_calls: [{ index: 0, id: 'x', function: { name: 'fs_write', arguments: '{"path": "/a", "con' } }] }, finish_reason: 'length' }] } }])]).f).stream(req(), collect().h);
  assert.deepEqual(r.blocks, []);
  assert.equal(r.stop, 'max_tokens');
});

test('openai: messages are converted with tool results right after the assistant message', () => {
  const msgs: Msg[] = [
    { role: 'user', content: [{ type: 'text', text: 'go' }] },
    { role: 'assistant', content: [{ type: 'text', text: 'ok' }, { type: 'tool_use', id: 'a', name: 'fs_read', input: { path: '/x' } }, { type: 'tool_use', id: 'b', name: 'shell_exec', input: { command: 'id' } }] },
    { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'a', content: 'A' }, { type: 'tool_result', tool_use_id: 'b', content: 'B', is_error: true }, { type: 'text', text: 'and then?' }] },
    { role: 'assistant', content: [{ type: 'tool_use', id: 'c', name: 'fs_read', input: {} }] },
  ];
  const out = toOpenAIMessages('SYS', msgs);
  assert.deepEqual(out, [
    { role: 'system', content: 'SYS' },
    { role: 'user', content: 'go' },
    { role: 'assistant', content: 'ok', tool_calls: [
      { id: 'a', type: 'function', function: { name: 'fs_read', arguments: '{"path":"/x"}' } },
      { id: 'b', type: 'function', function: { name: 'shell_exec', arguments: '{"command":"id"}' } }] },
    { role: 'tool', tool_call_id: 'a', content: 'A' },
    { role: 'tool', tool_call_id: 'b', content: 'B' },
    { role: 'user', content: 'and then?' },
    { role: 'assistant', content: null, tool_calls: [{ id: 'c', type: 'function', function: { name: 'fs_read', arguments: '{}' } }] },
  ]);
});

test('openai: tools use function declarations without additionalProperties', () => {
  const t = toOpenAITools(TOOLS) as { type: string; function: { name: string; parameters: any } }[];
  assert.equal(t[0].type, 'function');
  assert.equal(t[0].function.name, 'shell_exec');
  assert.equal('additionalProperties' in t[0].function.parameters, false);
  assert.deepEqual(plainSchema({ a: [{ additionalProperties: false, b: 1 }] }), { a: [{ b: 1 }] });
});

test('openai: no authorization header for a local server without a key; model list formats', async () => {
  const local = fakeFetch([json(200, { data: [{ id: 'llama3' }] }), json(200, { models: [{ name: 'models/gemini-x' }] })]);
  const p = new OpenAIProvider({ ...O, apiKey: '', baseUrl: 'http://localhost:11434/v1/' }, local.f);
  assert.deepEqual(await p.listModels(), ['llama3']);
  assert.equal(local.seen[0].url, 'http://localhost:11434/v1/models');
  assert.equal('authorization' in (local.seen[0].init.headers as object), false);
  assert.deepEqual(await p.listModels(), ['models/gemini-x']);
});

test('openai: errors, including a plain-text body', async () => {
  const { f } = fakeFetch([new Response('upstream exploded', { status: 502 }), json(401, { error: { message: 'Incorrect API key' } })]);
  const p = new OpenAIProvider(O, f);
  await assert.rejects(p.stream(req(), collect().h), (e: ProviderError) => e.kind === 'server' && e.message === 'upstream exploded');
  await assert.rejects(p.stream(req(), collect().h), (e: ProviderError) => e.kind === 'auth' && e.message === 'Incorrect API key');
});

test('chunk boundaries do not matter', async () => {
  const body = 'data: {"choices":[{"delta":{"content":"ab"}}]}\n\ndata: {"choices":[{"delta":{"content":"cd"},"finish_reason":"stop"}]}\n\n';
  for (const cut of [1, 7, 20, 41, body.length - 1]) {
    const { f } = fakeFetch([streamOf([body.slice(0, cut), body.slice(cut)])]);
    const r = await new OpenAIProvider(O, f).stream(req({ tools: [] }), collect().h);
    assert.equal((r.blocks[0] as { text: string }).text, 'abcd', `cut at ${cut}`);
  }
});

test('createProvider picks the wire format', () => {
  assert.ok(createProvider(A) instanceof AnthropicProvider);
  assert.ok(createProvider(O) instanceof OpenAIProvider);
});
