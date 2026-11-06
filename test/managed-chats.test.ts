import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CHAT_DIR, ChatRepository, newChat, type ChatDocuments, type ChatFiles } from '../src/state/chatRepository.ts';

function fixture() {
  const legacy = new Map<string, string>();
  const files: ChatFiles = {
    async read(path) { if (!legacy.has(path)) throw Object.assign(new Error('missing'), { code: 'not_found' }); return legacy.get(path)!; },
    async list() { return [...legacy.keys()].map(path => ({ name: path.slice(CHAT_DIR.length + 1), type: 'file' })); },
    async write() { throw new Error('legacy home is read-only'); },
    async mkdir() { throw new Error('legacy home is read-only'); },
    async remove() { throw new Error('legacy home is read-only'); },
  };
  const documents = new Map<string, { text: string; revision: string }>();
  const leases = new Map<string, string>();
  let revision = 0;
  const conflict = () => Object.assign(new Error('Conversation changed or is active in another panel'), { code: 'conflict' });
  const data: ChatDocuments = {
    async read(key) { const value = documents.get(key); if (!value) throw Object.assign(new Error('missing'), { code: 'not_found' }); return { ...value }; },
    async list() { return [...documents].map(([path, value]) => ({ path, type: 'file', size: value.text.length })); },
    async write(key, text, expected = '', owner) {
      if ((documents.get(key)?.revision || '') !== expected || leases.has(key) && leases.get(key) !== owner) throw conflict();
      const value = { text, revision: String(++revision) }; documents.set(key, value); return { revision: value.revision };
    },
    async remove(key, expected) { if (documents.get(key)?.revision !== expected || leases.has(key)) throw conflict(); documents.delete(key); },
    async claim(key, owner) { if (leases.has(key) && leases.get(key) !== owner) throw conflict(); leases.set(key, owner); return { expires: Date.now() + 120000 }; },
    async release(key, owner) { if (leases.get(key) === owner) leases.delete(key); },
  };
  return { legacy, documents, leases, repository: () => new ChatRepository(files, () => data) };
}

test('managed chats migrate once from a read-only home and deletion survives a fresh panel', async () => {
  const f = fixture(); const old = newChat('openai', 'test');
  old.messages.push({ role: 'user', content: [{ type: 'text', text: 'keep this history' }] });
  f.legacy.set(`${CHAT_DIR}/${old.id}.json`, JSON.stringify(old));
  const repo = f.repository(); assert.equal((await repo.list()).length, 1);
  const loaded = (await repo.load(old.id))!; assert.ok(loaded.storageRevision);
  await repo.withLease(loaded, async chat => {
    chat.messages.push({ role: 'assistant', content: [{ type: 'text', text: 'persisted answer' }] });
    await repo.save(chat);
  });
  assert.equal((await f.repository().load(old.id))!.messages.length, 2);
  assert.equal(JSON.parse(f.legacy.get(`${CHAT_DIR}/${old.id}.json`)!).messages.length, 1);
  assert.ok(!f.documents.get(`chats/${old.id}.json`)!.text.includes('leaseOwner'));
  await repo.remove(old.id);
  assert.deepEqual(await f.repository().list(), []);
  await assert.rejects(repo.withLease(loaded, async () => {}), /removed/);
});

test('full and floating panels cannot overwrite or delete an active turn; next turn reads the latest answer', async () => {
  const f = fixture(); const a = f.repository(); const b = f.repository();
  const initial = newChat('openai', 'test'); await a.save(initial);
  const stale = (await b.load(initial.id))!;
  let release!: () => void; let entered!: () => void;
  const ready = new Promise<void>(resolve => { entered = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  const first = a.withLease(initial, async chat => {
    entered(); await gate;
    chat.messages.push({ role: 'assistant', content: [{ type: 'text', text: 'first answer' }] });
    await a.save(chat);
  });
  await ready;
  await assert.rejects(b.withLease(stale, async () => {}), /another panel/);
  await assert.rejects(b.remove(initial.id), /another panel/);
  release(); await first;
  await assert.rejects(b.save(stale), /changed/);
  await b.withLease(stale, async chat => {
    assert.equal(chat.messages.length, 1);
    chat.messages.push({ role: 'user', content: [{ type: 'text', text: 'next prompt' }] });
    await b.save(chat);
  });
  assert.equal((await a.load(initial.id))!.messages.length, 2);
  assert.equal(f.leases.size, 0);
});

test('separate managed accounts never share conversations', async () => {
  const alice = fixture().repository(); const bob = fixture().repository();
  const chat = newChat('', ''); await alice.save(chat);
  assert.deepEqual(await bob.list(), []); assert.equal(await bob.load(chat.id), undefined);
});
