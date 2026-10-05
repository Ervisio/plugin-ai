/**
 * The agent loop: ask the model, run the tools it calls (asking the person first when the mode says so), give the
 * results back, repeat until it answers in words. No React, no SDK: everything it needs comes in `AgentDeps`.
 */
import { ProviderError, type Block, type Msg, type Provider, type ToolDef, type ToolOutcome, type Usage } from './types.ts';

export type ApprovalMode = 'ask' | 'auto-read' | 'auto';

export interface ToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
}

export type AgentEvent =
  | { type: 'assistant-start' }
  | { type: 'text'; delta: string }
  | { type: 'tool-planning'; name: string }
  | { type: 'assistant-end'; blocks: Block[] }
  | { type: 'tool-start'; call: ToolCall; root: boolean }
  | { type: 'tool-approval'; call: ToolCall }
  | { type: 'tool-progress'; id: string; text: string }
  | { type: 'tool-end'; id: string; outcome: ToolOutcome }
  | { type: 'usage'; usage: Usage }
  | { type: 'notice'; tone: 'info' | 'warn' | 'err'; text: string };

export interface RunOptions {
  root: boolean;
  signal: AbortSignal;
  onProgress(text: string): void;
}

export interface AgentDeps {
  provider: Provider;
  model: string;
  system: string;
  tools: ToolDef[];
  maxTokens: number;
  maxSteps: number;
  signal: AbortSignal;
  /** Read each time a tool is about to run, so the person can change it while the agent works. */
  mode(): ApprovalMode;
  /** Run every tool as root (the person turned "Root" on). A call with sudo=true is root either way. */
  root(): boolean;
  /** Does this call only read? Asked of the engine, without running anything. */
  classify(call: ToolCall): Promise<boolean>;
  runTool(call: ToolCall, o: RunOptions): Promise<ToolOutcome>;
  approve(call: ToolCall): Promise<'allow' | 'deny'>;
  /** The person said "always allow" for this tool: it runs without asking in any mode. */
  preApproved?(call: ToolCall): boolean;
  emit(e: AgentEvent): void;
  /** Characters of conversation to send before old tool output is shortened. */
  contextBudget?: number;
  sleep?(ms: number, signal: AbortSignal): Promise<void>;
}

export type TurnEnd = 'end' | 'aborted' | 'error' | 'steps' | 'max_tokens';

export interface TurnResult {
  messages: Msg[];
  end: TurnEnd;
  usage: Usage;
}

const RETRY_KINDS = new Set(['rate', 'overloaded', 'server']);
export const DENIED_TEXT = 'The user did not allow this action. Do not repeat it; ask what they would like instead.';

export function defaultSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => { clearTimeout(t); resolve(); }, { once: true });
  });
}

export function isRootCall(call: ToolCall, rootMode: boolean): boolean {
  return rootMode || call.input.sudo === true;
}

/** A tool call must be answered by exactly one result, in the user message right after it. */
function interrupted(call: ToolCall): Block {
  return { type: 'tool_result', tool_use_id: call.id, content: 'Interrupted by the user before this ran.', is_error: true };
}

export async function runTurn(history: Msg[], d: AgentDeps): Promise<TurnResult> {
  const messages = [...history];
  const total: Usage = { input: 0, output: 0 };
  const sleep = d.sleep ?? defaultSleep;
  let step = 0;
  for (;;) {
    if (d.signal.aborted) return { messages, end: 'aborted', usage: total };
    if (step++ >= d.maxSteps) {
      d.emit({ type: 'notice', tone: 'warn', text: `Stopped after ${d.maxSteps} steps in one go. Say "continue" to go on.` });
      return { messages, end: 'steps', usage: total };
    }
    d.emit({ type: 'assistant-start' });
    let result;
    for (let attempt = 0; ; attempt++) {
      try {
        result = await d.provider.stream(
          { system: d.system, messages: compactMessages(messages, d.contextBudget ?? 600_000), tools: d.tools, model: d.model, maxTokens: d.maxTokens, signal: d.signal },
          { onText: (delta) => d.emit({ type: 'text', delta }), onToolStart: (name) => d.emit({ type: 'tool-planning', name }) },
        );
        break;
      } catch (e) {
        if (d.signal.aborted || (e instanceof ProviderError && e.kind === 'aborted')) return { messages, end: 'aborted', usage: total };
        if (e instanceof ProviderError && RETRY_KINDS.has(e.kind) && attempt < 3) {
          const wait = Math.min(e.retryAfter ?? 2 ** (attempt + 1), 30);
          d.emit({ type: 'notice', tone: 'warn', text: `${e.message} Trying again in ${wait} s.` });
          await sleep(wait * 1000, d.signal);
          continue;
        }
        d.emit({ type: 'notice', tone: 'err', text: e instanceof Error ? e.message : String(e) });
        return { messages, end: 'error', usage: total };
      }
    }
    total.input += result.usage.input;
    total.output += result.usage.output;
    if (result.usage.cacheRead) total.cacheRead = (total.cacheRead ?? 0) + result.usage.cacheRead;
    d.emit({ type: 'usage', usage: result.usage });

    const calls = result.blocks.filter((b): b is Extract<Block, { type: 'tool_use' }> => b.type === 'tool_use').map((b) => ({ id: b.id, name: b.name, input: b.input }));
    if (result.blocks.length) {
      messages.push({ role: 'assistant', content: result.blocks });
      d.emit({ type: 'assistant-end', blocks: result.blocks });
    }
    if (!calls.length) {
      if (result.stop === 'max_tokens') {
        d.emit({ type: 'notice', tone: 'warn', text: 'The reply was cut at the token limit. Say "continue", or raise the limit in Settings.' });
        return { messages, end: 'max_tokens', usage: total };
      }
      if (!result.blocks.length) d.emit({ type: 'notice', tone: 'warn', text: 'The model answered with nothing.' });
      return { messages, end: 'end', usage: total };
    }

    const results: Block[] = [];
    for (const call of calls) {
      if (d.signal.aborted) {
        results.push(interrupted(call));
        continue;
      }
      const outcome = await execute(call, d);
      results.push({ type: 'tool_result', tool_use_id: call.id, content: outcome.text, ...(outcome.isError ? { is_error: true } : {}) });
    }
    messages.push({ role: 'user', content: results });
    if (d.signal.aborted) return { messages, end: 'aborted', usage: total };
  }
}

async function execute(call: ToolCall, d: AgentDeps): Promise<ToolOutcome> {
  const root = isRootCall(call, d.root());
  const denied: ToolOutcome = { text: DENIED_TEXT, isError: true, denied: true };
  const mode = d.mode();
  let needsApproval = mode === 'ask';
  if (mode === 'auto-read' && !d.preApproved?.(call)) {
    let readOnly = false;
    try {
      readOnly = await d.classify(call);
    } catch {
      readOnly = false;
    }
    needsApproval = !readOnly;
  }
  if (needsApproval && d.preApproved?.(call)) needsApproval = false;
  if (needsApproval) {
    d.emit({ type: 'tool-approval', call });
    if ((await d.approve(call)) === 'deny') {
      d.emit({ type: 'tool-start', call, root });
      d.emit({ type: 'tool-end', id: call.id, outcome: denied });
      return denied;
    }
  }
  d.emit({ type: 'tool-start', call, root });
  const started = Date.now();
  let outcome: ToolOutcome;
  try {
    outcome = await d.runTool(call, { root, signal: d.signal, onProgress: (text) => d.emit({ type: 'tool-progress', id: call.id, text }) });
  } catch (e) {
    outcome = { text: `Could not run ${call.name}: ${e instanceof Error ? e.message : String(e)}`, isError: true };
  }
  outcome = { ...outcome, ms: outcome.ms ?? Date.now() - started };
  d.emit({ type: 'tool-end', id: call.id, outcome });
  return outcome;
}

// --- keeping the conversation within the model's context ------------------------------------------------------

export function messageSize(m: Msg): number {
  let n = 0;
  for (const b of m.content) n += b.type === 'text' ? b.text.length : b.type === 'tool_result' ? b.content.length : JSON.stringify(b.input).length + b.name.length;
  return n;
}

/**
 * When the conversation is longer than `budget` characters (about 4 per token), shorten the OLDEST tool results to
 * their start and end until it fits. Nothing is deleted from the stored conversation: only what is sent changes.
 */
export function compactMessages(messages: Msg[], budget: number): Msg[] {
  let size = messages.reduce((n, m) => n + messageSize(m), 0);
  if (size <= budget) return messages;
  const out = messages.map((m) => ({ ...m, content: [...m.content] }));
  const keepFresh = Math.max(out.length - 6, 0); // the last few messages stay whole
  for (let i = 0; i < keepFresh && size > budget; i++) {
    const m = out[i];
    for (let j = 0; j < m.content.length && size > budget; j++) {
      const b = m.content[j];
      if (b.type === 'tool_result' && b.content.length > 2000) {
        const cut = b.content.length - 1800;
        m.content[j] = { ...b, content: `${b.content.slice(0, 1000)}\n… [${cut} characters of old output left out to save space] …\n${b.content.slice(-800)}` };
        size -= cut;
      }
    }
  }
  return out;
}
