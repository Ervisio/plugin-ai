/**
 * The two wire formats that cover nearly every model API: Anthropic's Messages API and the OpenAI-style Chat
 * Completions API (OpenAI, OpenRouter, Gemini's compatibility endpoint, Groq, DeepSeek, Mistral, xAI, Ollama, LM
 * Studio...). Both stream, both call tools. `fetch` is a parameter so tests can feed canned responses.
 */
import { readSse } from './sse.ts';
import {
  ProviderError,
  type Block,
  type Fetch,
  type Msg,
  type Provider,
  type ProviderConfig,
  type StopReason,
  type StreamHandlers,
  type StreamRequest,
  type StreamResult,
  type ToolDef,
  type Usage,
} from './types.ts';

export const ANTHROPIC_VERSION = '2023-06-01';

export interface ProviderPreset {
  id: string;
  kind: 'anthropic' | 'openai';
  name: string;
  baseUrl: string;
  /** A stable alias to start with; empty = pick one from the list the API returns. */
  defaultModel: string;
  keyUrl: string;
  /** 'custom' = the address is typed by the person and may need an administrator's approval. */
  note?: 'custom';
}

export const PRESETS: ProviderPreset[] = [
  { id: 'anthropic', kind: 'anthropic', name: 'Anthropic (Claude)', baseUrl: 'https://api.anthropic.com', defaultModel: 'claude-sonnet-5-5', keyUrl: 'https://console.anthropic.com/settings/keys' },
  { id: 'openai', kind: 'openai', name: 'OpenAI', baseUrl: 'https://api.openai.com/v1', defaultModel: '', keyUrl: 'https://platform.openai.com/api-keys' },
  { id: 'openrouter', kind: 'openai', name: 'OpenRouter', baseUrl: 'https://openrouter.ai/api/v1', defaultModel: '', keyUrl: 'https://openrouter.ai/keys' },
  { id: 'gemini', kind: 'openai', name: 'Google Gemini', baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', defaultModel: '', keyUrl: 'https://aistudio.google.com/apikey' },
  { id: 'groq', kind: 'openai', name: 'Groq', baseUrl: 'https://api.groq.com/openai/v1', defaultModel: 'llama-3.3-70b-versatile', keyUrl: 'https://console.groq.com/keys' },
  { id: 'deepseek', kind: 'openai', name: 'DeepSeek', baseUrl: 'https://api.deepseek.com/v1', defaultModel: 'deepseek-chat', keyUrl: 'https://platform.deepseek.com/api_keys' },
  { id: 'mistral', kind: 'openai', name: 'Mistral', baseUrl: 'https://api.mistral.ai/v1', defaultModel: 'mistral-large-latest', keyUrl: 'https://console.mistral.ai/api-keys' },
  { id: 'xai', kind: 'openai', name: 'xAI (Grok)', baseUrl: 'https://api.x.ai/v1', defaultModel: '', keyUrl: 'https://console.x.ai' },
  { id: 'custom', kind: 'openai', name: 'Custom (OpenAI-compatible)', baseUrl: 'http://localhost:11434/v1', defaultModel: '', keyUrl: '', note: 'custom' },
];

export function createProvider(cfg: ProviderConfig, fetchImpl: Fetch = (i, init) => fetch(i, init)): Provider {
  return cfg.kind === 'anthropic' ? new AnthropicProvider(cfg, fetchImpl) : new OpenAIProvider(cfg, fetchImpl);
}

// ---------------------------------------------------------------------------------------------------------------

async function failure(res: Response): Promise<ProviderError> {
  let message = `${res.status} ${res.statusText}`.trim();
  try {
    const text = await res.text();
    try {
      const j = JSON.parse(text) as { error?: { message?: string } | string; message?: string };
      const e = typeof j.error === 'string' ? j.error : j.error?.message ?? j.message;
      if (e) message = e;
    } catch {
      if (text) message = text.slice(0, 300);
    }
  } catch {
    // keep the status line
  }
  const retry = Number(res.headers.get('retry-after'));
  const retryAfter = Number.isFinite(retry) && retry > 0 ? retry : undefined;
  const s = res.status;
  const kind = s === 401 || s === 403 ? 'auth' : s === 429 ? 'rate' : s === 529 || s === 503 ? 'overloaded' : s === 400 || s === 404 || s === 413 || s === 422 ? 'bad_request' : s >= 500 ? 'server' : 'other';
  return new ProviderError(kind, message, s, retryAfter);
}

function networkError(e: unknown, signal?: AbortSignal): ProviderError {
  if (signal?.aborted || (e as { name?: string })?.name === 'AbortError') return new ProviderError('aborted', 'aborted');
  const msg = e instanceof Error ? e.message : String(e);
  return new ProviderError('network', msg === 'Failed to fetch' || msg === 'Load failed' ? 'Could not reach the API (network, a blocked host, or CORS).' : msg);
}

function joinUrl(base: string, path: string): string {
  return base.replace(/\/+$/, '') + path;
}

/** Remove what a strict API may reject; the engine's schemas are plain JSON Schema already. */
export function plainSchema(s: unknown): unknown {
  if (Array.isArray(s)) return s.map(plainSchema);
  if (s && typeof s === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(s)) {
      if (k === 'additionalProperties' || k === '$schema') continue;
      out[k] = plainSchema(v);
    }
    return out;
  }
  return s;
}

function parseArgs(text: string): Record<string, unknown> | null {
  const t = text.trim();
  if (!t) return {};
  try {
    const v = JSON.parse(t) as unknown;
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

// --- Anthropic -------------------------------------------------------------------------------------------------

export class AnthropicProvider implements Provider {
  cfg: ProviderConfig;
  f: Fetch;
  constructor(cfg: ProviderConfig, f: Fetch) {
    this.cfg = cfg;
    this.f = f;
  }

  headers(): Record<string, string> {
    return {
      'content-type': 'application/json',
      'x-api-key': this.cfg.apiKey,
      'anthropic-version': ANTHROPIC_VERSION,
      // The plugin runs in the user's browser, with the user's own key: this header says so to the API.
      'anthropic-dangerous-direct-browser-access': 'true',
    };
  }

  async listModels(signal?: AbortSignal): Promise<string[]> {
    let res: Response;
    try {
      res = await this.f(joinUrl(this.cfg.baseUrl, '/v1/models?limit=100'), { headers: this.headers(), signal });
    } catch (e) {
      throw networkError(e, signal);
    }
    if (!res.ok) throw await failure(res);
    const j = (await res.json()) as { data?: { id: string }[] };
    return (j.data ?? []).map((m) => m.id);
  }

  body(req: StreamRequest): Record<string, unknown> {
    const messages = req.messages.map((m) => ({
      role: m.role,
      content: m.content
        .filter((b) => b.type !== 'text' || b.text !== '')
        .map((b): Record<string, unknown> => {
          if (b.type === 'text') return { type: 'text', text: b.text };
          if (b.type === 'tool_use') return { type: 'tool_use', id: b.id, name: b.name, input: b.input };
          return { type: 'tool_result', tool_use_id: b.tool_use_id, content: b.content, ...(b.is_error ? { is_error: true } : {}) };
        }),
    })).filter((m) => m.content.length > 0);
    // Cache everything up to the newest message: the next turn re-sends it as a prefix.
    const last = messages[messages.length - 1];
    if (last) last.content[last.content.length - 1] = { ...last.content[last.content.length - 1], cache_control: { type: 'ephemeral' } };
    const tools = req.tools.map((t, i) => ({
      name: t.name,
      description: t.description,
      input_schema: t.inputSchema,
      ...(i === req.tools.length - 1 ? { cache_control: { type: 'ephemeral' } } : {}),
    }));
    return {
      model: req.model,
      max_tokens: req.maxTokens,
      stream: true,
      system: [{ type: 'text', text: req.system, cache_control: { type: 'ephemeral' } }],
      ...(tools.length ? { tools } : {}),
      messages,
    };
  }

  async stream(req: StreamRequest, h: StreamHandlers): Promise<StreamResult> {
    let res: Response;
    try {
      res = await this.f(joinUrl(this.cfg.baseUrl, '/v1/messages'), { method: 'POST', headers: this.headers(), body: JSON.stringify(this.body(req)), signal: req.signal });
    } catch (e) {
      throw networkError(e, req.signal);
    }
    if (!res.ok) throw await failure(res);
    if (!res.body) throw new ProviderError('other', 'The API sent no body.');

    const blocks: (Block & { _json?: string; _bad?: boolean })[] = [];
    const usage: Usage = { input: 0, output: 0 };
    let stop: StopReason = 'other';
    try {
      for await (const ev of readSse(res.body)) {
        if (!ev.data || ev.event === 'ping') continue;
        let d: any;
        try {
          d = JSON.parse(ev.data);
        } catch {
          continue;
        }
        switch (d.type) {
          case 'message_start': {
            const u = d.message?.usage ?? {};
            usage.input = (u.input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0);
            usage.cacheRead = u.cache_read_input_tokens ?? 0;
            usage.output = u.output_tokens ?? 0;
            break;
          }
          case 'content_block_start': {
            const cb = d.content_block ?? {};
            if (cb.type === 'tool_use') {
              blocks[d.index] = { type: 'tool_use', id: cb.id, name: cb.name, input: {}, _json: '' };
              h.onToolStart?.(cb.name);
            } else if (cb.type === 'text') {
              blocks[d.index] = { type: 'text', text: cb.text ?? '' };
              if (cb.text) h.onText(cb.text);
            }
            break;
          }
          case 'content_block_delta': {
            const b = blocks[d.index];
            const delta = d.delta ?? {};
            if (delta.type === 'text_delta' && b?.type === 'text') {
              b.text += delta.text;
              h.onText(delta.text);
            } else if (delta.type === 'input_json_delta' && b?.type === 'tool_use') {
              b._json = (b._json ?? '') + delta.partial_json;
            }
            break;
          }
          case 'content_block_stop': {
            const b = blocks[d.index];
            if (b?.type === 'tool_use') {
              const parsed = parseArgs(b._json ?? '');
              if (parsed) b.input = parsed;
              else b._bad = true;
            }
            break;
          }
          case 'message_delta': {
            const r = d.delta?.stop_reason;
            stop = r === 'end_turn' || r === 'stop_sequence' ? 'end' : r === 'tool_use' ? 'tool_use' : r === 'max_tokens' ? 'max_tokens' : 'other';
            if (d.usage?.output_tokens != null) usage.output = d.usage.output_tokens;
            break;
          }
          case 'error':
            throw new ProviderError(d.error?.type === 'overloaded_error' ? 'overloaded' : d.error?.type === 'rate_limit_error' ? 'rate' : 'server', d.error?.message ?? 'The API reported an error.');
        }
      }
    } catch (e) {
      if (e instanceof ProviderError) throw e;
      throw networkError(e, req.signal);
    }
    const clean: Block[] = [];
    for (const b of blocks) {
      if (!b) continue;
      if (b.type === 'tool_use') {
        if (b._bad) continue; // cut off mid-argument (max tokens): it cannot be run
        clean.push({ type: 'tool_use', id: b.id, name: b.name, input: b.input });
      } else if (b.type === 'text') {
        if (b.text) clean.push({ type: 'text', text: b.text });
      }
    }
    if (blocks.some((b) => b && b.type === 'tool_use' && b._bad) && stop === 'tool_use') stop = 'max_tokens';
    return { blocks: clean, stop, usage };
  }
}

// --- OpenAI-style ----------------------------------------------------------------------------------------------

type OAIMsg =
  | { role: 'system' | 'user'; content: string }
  | { role: 'assistant'; content: string | null; tool_calls?: { id: string; type: 'function'; function: { name: string; arguments: string } }[] }
  | { role: 'tool'; tool_call_id: string; content: string };

export function toOpenAIMessages(system: string, messages: Msg[]): OAIMsg[] {
  const out: OAIMsg[] = [{ role: 'system', content: system }];
  for (const m of messages) {
    if (m.role === 'assistant') {
      const text = m.content.filter((b): b is Extract<Block, { type: 'text' }> => b.type === 'text').map((b) => b.text).join('');
      const calls = m.content.filter((b): b is Extract<Block, { type: 'tool_use' }> => b.type === 'tool_use');
      out.push({
        role: 'assistant',
        content: text || null,
        ...(calls.length ? { tool_calls: calls.map((c) => ({ id: c.id, type: 'function' as const, function: { name: c.name, arguments: JSON.stringify(c.input) } })) } : {}),
      });
    } else {
      // tool results must directly follow the assistant message that asked for them, then any user text
      for (const b of m.content) if (b.type === 'tool_result') out.push({ role: 'tool', tool_call_id: b.tool_use_id, content: b.content });
      const text = m.content.filter((b): b is Extract<Block, { type: 'text' }> => b.type === 'text').map((b) => b.text).join('\n');
      if (text) out.push({ role: 'user', content: text });
    }
  }
  return out;
}

export function toOpenAITools(tools: ToolDef[]): unknown[] {
  return tools.map((t) => ({ type: 'function', function: { name: t.name, description: t.description, parameters: plainSchema(t.inputSchema) } }));
}

export class OpenAIProvider implements Provider {
  cfg: ProviderConfig;
  f: Fetch;
  constructor(cfg: ProviderConfig, f: Fetch) {
    this.cfg = cfg;
    this.f = f;
  }

  headers(): Record<string, string> {
    const h: Record<string, string> = { 'content-type': 'application/json' };
    if (this.cfg.apiKey) h.authorization = `Bearer ${this.cfg.apiKey}`;
    return h;
  }

  async listModels(signal?: AbortSignal): Promise<string[]> {
    let res: Response;
    try {
      res = await this.f(joinUrl(this.cfg.baseUrl, '/models'), { headers: this.headers(), signal });
    } catch (e) {
      throw networkError(e, signal);
    }
    if (!res.ok) throw await failure(res);
    const j = (await res.json()) as { data?: { id: string }[]; models?: { name?: string; id?: string }[] };
    const ids = (j.data ?? []).map((m) => m.id);
    if (ids.length) return ids;
    return (j.models ?? []).map((m) => m.id ?? m.name ?? '').filter(Boolean);
  }

  async stream(req: StreamRequest, h: StreamHandlers): Promise<StreamResult> {
    const body = {
      model: req.model,
      stream: true,
      stream_options: { include_usage: true },
      messages: toOpenAIMessages(req.system, req.messages),
      ...(req.tools.length ? { tools: toOpenAITools(req.tools) } : {}),
    };
    let res: Response;
    try {
      res = await this.f(joinUrl(this.cfg.baseUrl, '/chat/completions'), { method: 'POST', headers: this.headers(), body: JSON.stringify(body), signal: req.signal });
    } catch (e) {
      throw networkError(e, req.signal);
    }
    if (!res.ok) throw await failure(res);
    if (!res.body) throw new ProviderError('other', 'The API sent no body.');

    let text = '';
    const calls: { id: string; name: string; args: string }[] = [];
    const usage: Usage = { input: 0, output: 0 };
    let finish = '';
    try {
      for await (const ev of readSse(res.body)) {
        if (!ev.data || ev.data === '[DONE]') continue;
        let d: any;
        try {
          d = JSON.parse(ev.data);
        } catch {
          continue;
        }
        if (d.error) throw new ProviderError('server', typeof d.error === 'string' ? d.error : d.error.message ?? 'The API reported an error.');
        if (d.usage) {
          usage.input = d.usage.prompt_tokens ?? usage.input;
          usage.output = d.usage.completion_tokens ?? usage.output;
          usage.cacheRead = d.usage.prompt_tokens_details?.cached_tokens ?? usage.cacheRead;
        }
        const choice = d.choices?.[0];
        if (!choice) continue;
        const delta = choice.delta ?? {};
        if (typeof delta.content === 'string' && delta.content) {
          text += delta.content;
          h.onText(delta.content);
        }
        for (const tc of delta.tool_calls ?? []) {
          const i = tc.index ?? calls.length;
          if (!calls[i]) {
            calls[i] = { id: tc.id ?? `call_${i}`, name: '', args: '' };
          }
          if (tc.id) calls[i].id = tc.id;
          if (tc.function?.name) {
            if (!calls[i].name) h.onToolStart?.(tc.function.name);
            calls[i].name += tc.function.name;
          }
          if (tc.function?.arguments != null) calls[i].args += typeof tc.function.arguments === 'string' ? tc.function.arguments : JSON.stringify(tc.function.arguments);
        }
        if (choice.finish_reason) finish = choice.finish_reason;
      }
    } catch (e) {
      if (e instanceof ProviderError) throw e;
      throw networkError(e, req.signal);
    }
    const blocks: Block[] = [];
    if (text) blocks.push({ type: 'text', text });
    let bad = false;
    for (const c of calls) {
      if (!c || !c.name) continue;
      const input = parseArgs(c.args);
      if (!input) {
        bad = true;
        continue;
      }
      blocks.push({ type: 'tool_use', id: c.id, name: c.name, input });
    }
    let stop: StopReason = finish === 'stop' ? 'end' : finish === 'tool_calls' || finish === 'function_call' ? 'tool_use' : finish === 'length' ? 'max_tokens' : 'other';
    if (blocks.some((b) => b.type === 'tool_use') && stop !== 'max_tokens') stop = 'tool_use'; // some servers say "stop" with tool calls
    if (bad) stop = 'max_tokens';
    return { blocks, stop, usage };
  }
}
