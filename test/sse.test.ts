import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readSse } from '../src/chat/sse.ts';
import { streamOf } from './helpers.ts';

async function collect(pieces: string[]) {
  const out = [];
  for await (const e of readSse(streamOf(pieces).body!)) out.push(e);
  return out;
}

test('splits events and keeps the event name', async () => {
  const ev = await collect(['event: a\ndata: 1\n\ndata: 2\n\n']);
  assert.deepEqual(ev, [{ event: 'a', data: '1' }, { event: 'message', data: '2' }]);
});

test('joins multi-line data, drops comments, accepts CRLF and CR', async () => {
  const ev = await collect([': keepalive\r\ndata: one\r\ndata: two\r\n\r\ndata: three\r\r']);
  assert.deepEqual(ev, [{ event: 'message', data: 'one\ntwo' }, { event: 'message', data: 'three' }]);
});

test('an event split across chunks, even in the middle of a character', async () => {
  const bytes = new TextEncoder().encode('data: caffè ☕\n\n');
  const cut = 14; // inside the coffee cup
  const body = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(bytes.slice(0, cut)); c.enqueue(bytes.slice(cut)); c.close(); } });
  const out = [];
  for await (const e of readSse(body)) out.push(e);
  assert.deepEqual(out, [{ event: 'message', data: 'caffè ☕' }]);
});

test('a last event without the closing blank line is still delivered', async () => {
  assert.deepEqual(await collect(['data: tail']), [{ event: 'message', data: 'tail' }]);
});

test('a field without a colon, and a value without the leading space', async () => {
  assert.deepEqual(await collect(['data:x\ndata\n\n']), [{ event: 'message', data: 'x\n' }]);
});
