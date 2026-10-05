/** Conversations: one JSON file each in ~/.config/ervisio/plugins/ai/chats, and an index to list them quickly. */
import { getSdk } from '../sdk';
import { writeEnsured } from './files';
import type { Block, Msg, Usage } from '../chat/types.ts';

const DIR = '~/.config/ervisio/plugins/ai/chats';
const MAX_BYTES = 3_500_000; // the SDK writes at most 4 MiB

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

export function newChat(provider: string, model: string): Chat {
  const now = Date.now();
  return { id: Math.random().toString(36).slice(2, 10) + now.toString(36), title: '', created: now, updated: now, provider, model, messages: [], usage: { input: 0, output: 0 } };
}

export function titleOf(messages: Msg[]): string {
  const first = messages.find((m) => m.role === 'user')?.content.find((b): b is Extract<Block, { type: 'text' }> => b.type === 'text');
  const t = (first?.text ?? '').replace(/\s+/g, ' ').trim();
  return t.length > 60 ? t.slice(0, 57) + '…' : t;
}

/** Shorten the oldest tool output until the conversation fits in one file. */
export function fit(chat: Chat): string {
  let json = JSON.stringify(chat);
  for (let i = 0; json.length > MAX_BYTES && i < chat.messages.length; i++) {
    const m = chat.messages[i];
    m.content = m.content.map((b) => (b.type === 'tool_result' && b.content.length > 1500 ? { ...b, content: b.content.slice(0, 1200) + '\n… (shortened to fit the file)' } : b));
    json = JSON.stringify(chat);
  }
  return json;
}

export async function listChats(): Promise<ChatMeta[]> {
  try {
    const index = JSON.parse(await getSdk().files.read(`${DIR}/index.json`)) as ChatMeta[];
    return index.sort((a, b) => b.updated - a.updated);
  } catch {
    return [];
  }
}

async function writeIndex(items: ChatMeta[]): Promise<void> {
  await writeEnsured(`${DIR}/index.json`, JSON.stringify(items));
}

export async function saveChat(chat: Chat): Promise<void> {
  chat.updated = Date.now();
  if (!chat.title) chat.title = titleOf(chat.messages);
  await writeEnsured(`${DIR}/${chat.id}.json`, fit(chat));
  const rest = (await listChats()).filter((c) => c.id !== chat.id);
  const meta: ChatMeta = { id: chat.id, title: chat.title, created: chat.created, updated: chat.updated, provider: chat.provider, model: chat.model };
  await writeIndex([meta, ...rest].slice(0, 200));
}

export async function loadChat(id: string): Promise<Chat | undefined> {
  try {
    return JSON.parse(await getSdk().files.read(`${DIR}/${id}.json`)) as Chat;
  } catch {
    return undefined;
  }
}

export async function deleteChat(id: string): Promise<void> {
  try {
    await getSdk().files.remove(`${DIR}/${id}.json`);
  } catch {
    // already gone
  }
  await writeIndex((await listChats()).filter((c) => c.id !== id));
}

export async function renameChat(id: string, title: string): Promise<void> {
  const c = await loadChat(id);
  if (!c) return;
  c.title = title;
  await writeEnsured(`${DIR}/${id}.json`, fit(c));
  await writeIndex((await listChats()).map((m) => (m.id === id ? { ...m, title } : m)));
}
