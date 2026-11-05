import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CHAT_DIR, ChatRepository, fit, newChat, type ChatFiles } from '../src/state/chatRepository.ts';

function account() {
  const files = new Map<string, string>();
  let directory = false;
  let failWrite = false;
  const missing = () => Object.assign(new Error('Missing'), { code: 'not_found' });
  const api: ChatFiles = {
    async read(path) { const data = files.get(path); if (data === undefined) throw missing(); return data; },
    async write(path, data) {
      if (failWrite) throw Object.assign(new Error('Permission denied'), { code: 'forbidden' });
      if (!directory) throw missing();
      files.set(path, data);
    },
    async mkdir() { directory = true; },
    async list() {
      if (!directory) throw missing();
      return [...files.keys()].map((path) => ({ name: path.slice(CHAT_DIR.length + 1), type: 'file' }));
    },
    async remove(path) { if (!files.delete(path)) throw missing(); },
  };
  return { files, api, denyWrites: (deny: boolean) => { failWrite = deny; } };
}

function conversation(text = 'hello') {
  const chat = newChat('openai', 'model');
  chat.messages = [{ role: 'user', content: [{ type: 'text', text }] }];
  return chat;
}

test('new account is empty, save creates the folder, another browser recovers the chat without an index', async () => {
  const user = account();
  const first = new ChatRepository(user.api);
  assert.deepEqual(await first.list(), []);
  const chat = conversation('a conversation to keep');
  await first.save(chat);
  const nextBrowser = new ChatRepository(user.api);
  assert.deepEqual((await nextBrowser.list()).map((item) => item.id), [chat.id]);
  assert.deepEqual(await nextBrowser.load(chat.id), chat);
  assert.equal(user.files.has(`${CHAT_DIR}/index.json`), false);
});

test('history follows each server account; another user has no access to the first user chat', async () => {
  const alice = new ChatRepository(account().api);
  const bob = new ChatRepository(account().api);
  const chat = conversation('private account history');
  await alice.save(chat);
  assert.deepEqual(await bob.list(), []);
  assert.equal(await bob.load(chat.id), undefined);
});

test('simultaneous conversations do not overwrite a shared index, stale index and 200 cap are ignored', async () => {
  const user = account();
  const a = new ChatRepository(user.api);
  const b = new ChatRepository(user.api);
  await a.save(conversation('first'));
  user.files.set(`${CHAT_DIR}/index.json`, '[]');
  const more = Array.from({ length: 205 }, (_, i) => conversation(`chat ${i}`));
  await Promise.all(more.map((chat, i) => (i % 2 ? a : b).save(chat)));
  assert.equal((await a.list()).length, 206);
});

test('failed saves stay visible, preserve the existing transcript and can be retried', async () => {
  const user = account();
  const repository = new ChatRepository(user.api);
  const chat = conversation('original');
  await repository.save(chat);
  const before = user.files.get(`${CHAT_DIR}/${chat.id}.json`);
  user.denyWrites(true);
  chat.messages.push({ role: 'assistant', content: [{ type: 'text', text: 'reply' }] });
  await assert.rejects(repository.save(chat), /Permission denied/);
  assert.equal(user.files.get(`${CHAT_DIR}/${chat.id}.json`), before);
  user.denyWrites(false);
  await repository.save(chat);
  assert.equal((await repository.load(chat.id))?.messages.length, 2);
});

test('a damaged transcript does not conceal healthy chats or get silently deleted', async () => {
  const user = account();
  const repository = new ChatRepository(user.api);
  const healthy = conversation();
  await repository.save(healthy);
  user.files.set(`${CHAT_DIR}/broken.json`, '{');
  assert.equal((await repository.list())[0].id, healthy.id);
  assert.match(repository.warnings.join(), /broken/);
  assert.equal(user.files.get(`${CHAT_DIR}/broken.json`), '{');
  await assert.rejects(repository.load('broken'));
});

test('rename and deletion persist across reload, traversal IDs are rejected', async () => {
  const user = account();
  const repository = new ChatRepository(user.api);
  const chat = conversation();
  await repository.save(chat);
  await repository.rename(chat.id, 'New title');
  assert.equal((await new ChatRepository(user.api).load(chat.id))?.title, 'New title');
  await assert.rejects(repository.load('../settings'), /Invalid conversation/);
  assert.throws(() => repository.remove('../settings'), /Invalid conversation/);
  await repository.remove(chat.id);
  assert.deepEqual(await new ChatRepository(user.api).list(), []);
});

test('file fitting uses UTF-8 bytes and never mutates a live conversation', () => {
  const chat = conversation('keep this exact user prompt');
  const output = '⚡'.repeat(1_200_000);
  chat.messages.push({ role: 'user', content: [{ type: 'tool_result', tool_use_id: 'call', content: output }] });
  const json = fit(chat);
  assert.ok(new TextEncoder().encode(json).length <= 3_500_000);
  assert.equal(chat.messages[1].content[0].type === 'tool_result' && chat.messages[1].content[0].content, output);
  assert.match(json, /keep this exact user prompt/);
  assert.throws(() => fit(conversation('⚡'.repeat(1_200_000))), /too large to save/);
});
