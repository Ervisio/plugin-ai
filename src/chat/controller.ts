/**
 * The conversation currently open in the chat page: it owns the messages, runs the agent, keeps the transcript and
 * saves the conversation. One instance (`chat`) lives as long as the page does, so switching between the plugin's
 * views does not lose a running reply.
 */
import { getSdk } from '../sdk';
import { callTool, classify, listTools, whoami, type Identity } from '../engine.ts';
import { activeProvider, settings, updateSettings } from '../state/settings.ts';
import { createStore } from '../state/store.ts';
import { deleteChat, listChats, loadChat, newChat, saveChat, type Chat, type ChatMeta } from '../state/chats.ts';
import { runTurn, type AgentEvent, type ToolCall } from './agent.ts';
import { itemId, itemsFromMessages, reduceItems, type Item } from './items.ts';
import { buildSystemPrompt } from './prompt.ts';
import { createProvider, PRESETS } from './providers.ts';
import type { Msg, ProviderConfig, ToolDef, Usage } from './types.ts';

export interface ChatState {
  chat: Chat;
  items: Item[];
  busy: boolean;
  /** Name of a tool the model has started to call (arguments still streaming). */
  planning: string;
  tools: ToolDef[];
  identity?: Identity;
  /** The engine could not be reached: why. */
  engineError?: string;
  list: ChatMeta[];
}

const LANGUAGES: Record<string, string> = { en: 'English', it: 'Italian', de: 'German', fr: 'French', es: 'Spanish', pt: 'Portuguese' };
const PRESET_HOSTS = new Set(PRESETS.filter((p) => p.note !== 'custom').map((p) => new URL(p.baseUrl).hostname));

export function isManifestHost(cfg: ProviderConfig): boolean {
  const u = new URL(cfg.baseUrl);
  return u.protocol === 'https:' && PRESET_HOSTS.has(u.hostname);
}

/**
 * A host outside the manifest's list must be approved by an administrator once, and Ervisio then reloads the plugin's
 * frames (the new address joins the page's content policy only on load). `beforeAsk` runs first so the caller can save
 * what the reload would otherwise throw away.
 */
export async function ensureHostAllowed(cfg: ProviderConfig, beforeAsk?: () => void | Promise<void>): Promise<void> {
  if (isManifestHost(cfg)) return;
  const u = new URL(cfg.baseUrl);
  const net = getSdk().network;
  if (!net) throw new Error('This Ervisio is too old to approve extra hosts (needs 0.5).');
  await beforeAsk?.();
  const r = await net.request(u.host, { scheme: u.protocol === 'http:' ? 'http' : 'https' });
  // The frame is about to be replaced: do not carry on with a request the old policy would refuse.
  if (r.reloading) await new Promise((resolve) => setTimeout(resolve, 8000));
}

export class ChatController {
  readonly state = createStore<ChatState>({
    chat: newChat('', ''), items: [], busy: false, planning: '', tools: [], list: [],
  });
  private abort?: AbortController;
  private pending?: (d: 'allow' | 'deny') => void;
  private allowed = new Set<string>();
  private queue: AgentEvent[] = [];
  private raf = 0;

  private patch(p: Partial<ChatState>): void {
    this.state.set((s) => ({ ...s, ...p }));
  }

  /** Load what the chat needs from the server. Safe to call again (after an install, say). */
  async init(): Promise<void> {
    try {
      const [identity, list] = await Promise.all([whoami(), listTools()]);
      this.patch({ identity, tools: list.tools, engineError: undefined });
    } catch (e) {
      this.patch({ engineError: (e as Error).message });
    }
    this.patch({ list: await listChats() });
  }

  async refreshList(): Promise<void> {
    this.patch({ list: await listChats() });
  }

  startNew(): void {
    if (this.state.get().busy) return;
    const s = settings.get();
    const p = activeProvider(s);
    this.patch({ chat: newChat(p?.id ?? '', p ? s.models[p.id] ?? '' : ''), items: [], planning: '' });
  }

  async open(id: string): Promise<void> {
    if (this.state.get().busy) return;
    const c = await loadChat(id);
    if (!c) return;
    this.patch({ chat: c, items: itemsFromMessages(c.messages), planning: '' });
  }

  async remove(id: string): Promise<void> {
    await deleteChat(id);
    if (this.state.get().chat.id === id) this.startNew();
    await this.refreshList();
  }

  // -- events ------------------------------------------------------------------------------------------------

  private emit = (e: AgentEvent): void => {
    if (e.type === 'tool-planning') {
      this.patch({ planning: e.name });
      return;
    }
    this.queue.push(e);
    if (!this.raf) this.raf = requestAnimationFrame(() => this.flush());
  };

  private flush(): void {
    this.raf = 0;
    const events = this.queue.splice(0);
    if (!events.length) return;
    this.state.set((s) => {
      let items = s.items;
      let planning = s.planning;
      for (const e of events) {
        items = reduceItems(items, e);
        if (e.type === 'tool-start' || e.type === 'tool-approval' || e.type === 'assistant-end') planning = '';
      }
      return { ...s, items, planning };
    });
  }

  private flushNow(): void {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.flush();
  }

  private addItem(item: Item): void {
    this.state.set((s) => ({ ...s, items: [...s.items, item] }));
  }

  // -- running -----------------------------------------------------------------------------------------------

  /** Why a message cannot be sent yet, or '' when it can. */
  blocker(): string {
    const s = this.state.get();
    const cfg = settings.get();
    const p = activeProvider(cfg);
    if (s.engineError) return 'engine';
    if (!p || !p.apiKey && p.kind === 'anthropic') return 'provider';
    if (!cfg.models[p.id]) return 'model';
    return '';
  }

  async send(text: string): Promise<void> {
    const s0 = this.state.get();
    if (s0.busy || !text.trim()) return;
    const cfg = settings.get();
    const provider = activeProvider(cfg);
    if (!provider) return;
    const model = cfg.models[provider.id];
    const msg: Msg = { role: 'user', content: [{ type: 'text', text }] };
    const chat: Chat = { ...s0.chat, provider: provider.id, model, messages: [...s0.chat.messages, msg] };
    this.patch({ chat, items: [...s0.items, { kind: 'user', id: itemId(), text }], busy: true, planning: '' });
    this.abort = new AbortController();
    try {
      await ensureHostAllowed(provider);
      const st = this.state.get();
      const id = st.identity;
      const system = buildSystemPrompt({
        host: id?.hostname ?? 'this server', user: id?.user ?? 'the signed-in user', home: id?.home ?? '~',
        language: LANGUAGES[getSdk().lang().slice(0, 2)] ?? 'the language the person writes in', extra: cfg.extraPrompt, tools: st.tools,
      });
      const result = await runTurn(chat.messages, {
        provider: createProvider(provider), model, system, tools: st.tools, maxTokens: cfg.maxTokens, maxSteps: cfg.maxSteps, signal: this.abort.signal,
        mode: () => settings.get().approval,
        root: () => settings.get().root,
        classify: (c) => classify(c.name, c.input),
        runTool: (c, o) => callTool(c.name, c.input, { root: o.root, signal: o.signal, onProgress: o.onProgress }),
        approve: (c) => new Promise((resolve) => { this.pending = resolve; void c; }),
        preApproved: (c) => this.allowed.has(c.name),
        emit: this.emit,
      });
      this.flushNow();
      const usage: Usage = { input: chat.usage.input + result.usage.input, output: chat.usage.output + result.usage.output };
      const done: Chat = { ...chat, messages: result.messages, usage };
      this.patch({ chat: done });
      await saveChat(done);
    } catch (e) {
      this.flushNow();
      this.addItem({ kind: 'notice', id: itemId(), tone: 'err', text: (e as Error).message });
      const failed = { ...this.state.get().chat };
      void saveChat(failed);
    } finally {
      this.pending = undefined;
      this.abort = undefined;
      this.patch({ busy: false, planning: '' });
      void this.refreshList();
    }
  }

  stop(): void {
    this.abort?.abort();
    this.pending?.('deny');
  }

  /** The answer to an approval card. */
  decide(call: ToolCall, d: 'allow' | 'deny' | 'always'): void {
    if (d === 'always') this.allowed.add(call.name);
    this.pending?.(d === 'deny' ? 'deny' : 'allow');
    this.pending = undefined;
  }

  /** `!command`: run it now, as the person typed it, and let the model see what came out. */
  async shell(command: string): Promise<void> {
    const s0 = this.state.get();
    if (s0.busy || !command.trim()) return;
    const root = settings.get().root;
    const id = `sh${Date.now().toString(36)}`;
    const call: ToolCall = { id, name: 'shell_exec', input: { command } };
    this.patch({ busy: true, items: [...s0.items, { kind: 'tool', id, name: 'shell_exec', input: call.input, status: 'running', root }] });
    this.abort = new AbortController();
    const outcome = await callTool('shell_exec', call.input, { root, signal: this.abort.signal, onProgress: (text) => this.emit({ type: 'tool-progress', id, text }) })
      .catch((e: Error) => ({ text: e.message, isError: true, ms: undefined as number | undefined }));
    this.emit({ type: 'tool-end', id, outcome });
    this.flushNow();
    const note: Msg = { role: 'user', content: [{ type: 'text', text: `I ran \`${command}\`${root ? ' as root' : ''}:\n\n\`\`\`\n${outcome.text.slice(0, 20000)}\n\`\`\`` }] };
    const chat = { ...this.state.get().chat, messages: [...this.state.get().chat.messages, note] };
    this.patch({ chat, busy: false });
    this.abort = undefined;
    void saveChat(chat).then(() => this.refreshList());
  }

  clear(): void {
    if (this.state.get().busy) return;
    this.startNew();
  }

  setRoot(on: boolean): void {
    updateSettings({ root: on });
  }
}

export const chat = new ChatController();
