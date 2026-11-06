/** Server-backed chat storage. The directory, rather than a shared index, is authoritative. */
import type { Block, Msg, Usage } from '../chat/types.ts';

export const CHAT_DIR = '~/.config/ervisio/plugins/ai/chats';
const MAX_BYTES = 3_500_000;
const ID = /^[a-z0-9][a-z0-9_-]{0,100}$/i;

export interface ChatMeta {
  id: string;
  title: string;
  created: number;
  updated: number;
  provider: string;
  model: string;
}

export interface Chat extends ChatMeta {
  /** Host metadata, excluded from the serialized transcript. */
  storageRevision?: string;
  leaseOwner?: string;
  messages: Msg[];
  usage: Usage;
}

export interface ChatDocuments {
  read(key: string): Promise<{ text: string; revision: string }>;
  write(key: string, text: string, revision?: string, owner?: string): Promise<{ revision: string }>;
  list(): Promise<{ path: string; type: string; size: number }[]>;
  remove(key: string, revision: string): Promise<void>;
  claim(key: string, owner: string): Promise<{ expires: number }>;
  release(key: string, owner: string): Promise<void>;
}

export interface ChatFiles {
  read(path: string): Promise<string>;
  write(path: string, data: string): Promise<void>;
  list(path: string): Promise<{ name: string; type: string }[]>;
  mkdir(path: string): Promise<void>;
  remove(path: string): Promise<void>;
}

export function missing(e: unknown): boolean {
  return (e as { code?: string })?.code === 'not_found';
}

function pathOf(id: string): string {
  if (!ID.test(id)) throw new Error('Invalid conversation ID.');
  return `${CHAT_DIR}/${id}.json`;
}

export function newChat(provider: string, model: string): Chat {
  const now = Date.now();
  const id = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2, 10) + now.toString(36);
  return { id, title: '', created: now, updated: now, provider, model, messages: [], usage: { input: 0, output: 0 } };
}

export function titleOf(messages: Msg[]): string {
  const first = messages.find((m) => m.role === 'user')?.content.find((b): b is Extract<Block, { type: 'text' }> => b.type === 'text');
  const text = (first?.text ?? '').replace(/\s+/g, ' ').trim();
  return text.length > 60 ? text.slice(0, 57) + '…' : text;
}

/** Bound the encoded bytes without mutating the live transcript or dropping user text. */
export function fit(chat: Chat): string {
  const copy: Chat = { ...chat, messages: chat.messages.map((m) => ({ ...m, content: [...m.content] })) };
  delete copy.storageRevision;
  delete copy.leaseOwner;
  let json = JSON.stringify(copy);
  for (const message of copy.messages) {
    if (new TextEncoder().encode(json).length <= MAX_BYTES) break;
    message.content = message.content.map((block) => block.type === 'tool_result' && block.content.length > 1500
      ? { ...block, content: block.content.slice(0, 1200) + '\n… (shortened to fit the file)' } : block);
    json = JSON.stringify(copy);
  }
  if (new TextEncoder().encode(json).length > MAX_BYTES) {
    throw new Error('This conversation is too large to save. Export it before starting a new conversation.');
  }
  return json;
}

function parseChat(text: string, id: string): Chat {
  const chat = JSON.parse(text) as Chat;
  if (chat?.id !== id || !Array.isArray(chat.messages) || !chat.messages.every((m) =>
    (m.role === 'user' || m.role === 'assistant') && Array.isArray(m.content)) ||
    !Number.isFinite(chat.created) || !Number.isFinite(chat.updated) ||
    !chat.usage || !Number.isFinite(chat.usage.input) || !Number.isFinite(chat.usage.output)) {
    throw new Error(`Conversation ${id} is not a valid chat file.`);
  }
  return chat;
}

function metaOf(chat: Chat): ChatMeta {
  return { id: chat.id, title: chat.title, created: chat.created, updated: chat.updated, provider: chat.provider, model: chat.model };
}

export class ChatRepository {
  private writes: Promise<unknown> = Promise.resolve();
  private readonly files: ChatFiles;
  private readonly documents?: () => ChatDocuments | undefined;
  private migration?: Promise<void>;
  private migrationWarnings: string[] = [];
  readonly warnings: string[] = [];
  constructor(files: ChatFiles, documents?: () => ChatDocuments | undefined) { this.files = files; this.documents = documents; }

  private key(id: string): string { pathOf(id); return `chats/${id}.json`; }

  private async ready(): Promise<void> {
    const data = this.documents?.();
    if (!data) return;
    if (!this.migration) this.migration = this.migrate(data).catch(e => { this.migration = undefined; throw e; });
    await this.migration;
  }

  /** Copy once, leave original files untouched and never replace managed data. */
  private async migrate(data: ChatDocuments): Promise<void> {
    const marker = 'migration/chats-v1.json';
    try {
      const saved = JSON.parse((await data.read(marker)).text) as { warnings?: string[] };
      this.migrationWarnings = saved.warnings || [];
      return;
    } catch (e) { if (!missing(e)) throw e; }
    let entries: { name: string; type: string }[];
    try { entries = await this.files.list(CHAT_DIR); }
    catch (e) { if (!missing(e)) throw e; entries = []; }
    const warnings: string[] = [];
    for (const entry of entries) {
      const id = entry.name.replace(/\.json$/, '');
      if (entry.type !== 'file' || !entry.name.endsWith('.json') || id === 'index' || !ID.test(id)) continue;
      try { await data.read(this.key(id)); continue; } catch (e) { if (!missing(e)) throw e; }
      let chat: Chat;
      const original = await this.files.read(pathOf(id));
      try { chat = parseChat(original, id); }
      catch { warnings.push(`${id}: The original chat file needs repair; it has been preserved in the legacy folder.`); continue; }
      try { await data.write(this.key(id), fit(chat), ''); }
      catch (e) { if ((e as { code?: string }).code !== 'conflict') throw e; }
    }
    try { await data.write(marker, JSON.stringify({ warnings }), ''); }
    catch (e) { if ((e as { code?: string }).code !== 'conflict') throw e; }
    this.migrationWarnings = warnings;
  }

  private async read(id: string): Promise<Chat | undefined> {
    try {
      const data = this.documents?.();
      if (data) {
        await this.ready();
        const file = await data.read(this.key(id));
        return { ...parseChat(file.text, id), storageRevision: file.revision };
      }
      return parseChat(await this.files.read(pathOf(id)), id);
    }
    catch (e) { if (missing(e)) return undefined; throw e; }
  }

  async load(id: string): Promise<Chat | undefined> {
    await this.writes;
    return this.read(id);
  }

  async list(): Promise<ChatMeta[]> {
    await this.writes;
    this.warnings.length = 0;
    let entries: { name: string; type: string }[];
    try {
      await this.ready();
      this.warnings.push(...this.migrationWarnings);
      const data = this.documents?.();
      entries = data ? (await data.list()).filter(entry => entry.path.startsWith('chats/')).map(entry => ({ name: entry.path.slice(6), type: entry.type })) : await this.files.list(CHAT_DIR);
    }
    catch (e) { if (missing(e)) return []; throw e; }
    const names = entries.filter((entry) => entry.type === 'file' && entry.name.endsWith('.json') && entry.name !== 'index.json')
      .map((entry) => entry.name.slice(0, -5)).filter((id) => ID.test(id));
    const list: ChatMeta[] = [];
    for (let i = 0; i < names.length; i += 6) {
      const batch = await Promise.all(names.slice(i, i + 6).map(async (id) => {
        try { return await this.read(id); }
        catch (e) {
          // One damaged transcript must not hide every other conversation.
          this.warnings.push(`${id}: ${(e as Error).message}`);
          return undefined;
        }
      }));
      for (const chat of batch) if (chat) list.push(metaOf(chat));
    }
    return list.sort((a, b) => b.updated - a.updated || a.id.localeCompare(b.id));
  }

  private serialize<T>(work: () => Promise<T>): Promise<T> {
    const result = this.writes.then(work, work);
    this.writes = result.catch(() => undefined);
    return result;
  }

  save(chat: Chat): Promise<void> {
    chat.updated = Date.now();
    if (!chat.title) chat.title = titleOf(chat.messages);
    const file = pathOf(chat.id);
    const data = fit(chat);
    return this.serialize(async () => {
      const documents = this.documents?.();
      if (documents) {
        await this.ready();
        const saved = await documents.write(this.key(chat.id), data, chat.storageRevision || '', chat.leaseOwner);
        chat.storageRevision = saved.revision;
        return;
      }
      try { await this.files.write(file, data); }
      catch (e) {
        if (!missing(e)) throw e;
        await this.files.mkdir(CHAT_DIR);
        await this.files.write(file, data);
      }
    });
  }

  remove(id: string): Promise<void> {
    const path = pathOf(id);
    return this.serialize(async () => {
      const data = this.documents?.();
      if (data) {
        await this.ready();
        const chat = await this.read(id);
        if (chat) await data.remove(this.key(id), chat.storageRevision!);
        return;
      }
      try { await this.files.remove(path); }
      catch (e) { if (!missing(e)) throw e; }
    });
  }

  async rename(id: string, title: string): Promise<void> {
    const chat = await this.load(id);
    if (!chat) return;
    await this.save({ ...chat, title: title.trim().slice(0, 200) });
  }

  /** The full page and floating panel share one short server-side turn lease. */
  async withLease<T>(chat: Chat, work: (fresh: Chat) => Promise<T>, onLost?: (e: Error) => void): Promise<T> {
    const data = this.documents?.();
    if (!data) return work(chat);
    await this.ready();
    let fresh = await this.read(chat.id);
    if (!fresh) {
      if (chat.storageRevision) throw new Error('This conversation was removed. Start a new conversation.');
      await this.save(chat);
      fresh = await this.read(chat.id);
    }
    const owner = Array.from(crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join('');
    const key = this.key(chat.id);
    await data.claim(key, owner);
    let lost: Error | undefined;
    let renewing: Promise<unknown> | undefined;
    const timer = setInterval(() => {
      if (renewing || lost) return;
      renewing = data.claim(key, owner).catch(e => { lost = e instanceof Error ? e : new Error(String(e)); onLost?.(lost); }).finally(() => { renewing = undefined; });
    }, 30_000);
    try {
      fresh = await this.read(chat.id);
      if (!fresh) throw new Error('This conversation was removed. Start a new conversation.');
      const result = await work({ ...fresh, leaseOwner: owner });
      if (lost) throw lost;
      return result;
    } finally {
      clearInterval(timer);
      await renewing;
      await data.release(key, owner).catch(() => {});
    }
  }
}
