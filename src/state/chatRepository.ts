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
  messages: Msg[];
  usage: Usage;
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
  readonly warnings: string[] = [];
  constructor(files: ChatFiles) { this.files = files; }

  private async read(id: string): Promise<Chat | undefined> {
    try { return parseChat(await this.files.read(pathOf(id)), id); }
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
    try { entries = await this.files.list(CHAT_DIR); }
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
      try { await this.files.remove(path); }
      catch (e) { if (!missing(e)) throw e; }
    });
  }

  async rename(id: string, title: string): Promise<void> {
    const chat = await this.load(id);
    if (!chat) return;
    await this.save({ ...chat, title: title.trim().slice(0, 200) });
  }
}
