/**
 * Shared shapes of the chat. Everything in src/chat is plain TypeScript with no React and no SDK, so it runs under
 * `node --test` too (the provider code takes `fetch` as a parameter).
 */

/** A tool as the engine lists it (the MCP tool definition). */
export interface ToolDef {
  name: string;
  title?: string;
  description: string;
  inputSchema: Record<string, unknown>;
  annotations?: { readOnlyHint?: boolean; destructiveHint?: boolean; title?: string };
}

export type Block =
  | { type: 'text'; text: string }
  | { type: 'tool_use'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; tool_use_id: string; content: string; is_error?: boolean };

/** One message of the conversation, in the provider-neutral form the chat stores. */
export interface Msg {
  role: 'user' | 'assistant';
  content: Block[];
}

export interface Usage {
  input: number;
  output: number;
  cacheRead?: number;
}

export type StopReason = 'end' | 'tool_use' | 'max_tokens' | 'other';

export interface StreamRequest {
  system: string;
  messages: Msg[];
  tools: ToolDef[];
  model: string;
  maxTokens: number;
  signal?: AbortSignal;
}

export interface StreamHandlers {
  onText(delta: string): void;
  /** A tool call started streaming: its name is known, its arguments are not yet. */
  onToolStart?(name: string): void;
}

export interface StreamResult {
  blocks: Block[];
  stop: StopReason;
  usage: Usage;
}

export type ProviderKind = 'anthropic' | 'openai';

export interface ProviderConfig {
  id: string;
  kind: ProviderKind;
  name: string;
  baseUrl: string;
  apiKey: string;
}

export interface Provider {
  stream(req: StreamRequest, h: StreamHandlers): Promise<StreamResult>;
  listModels(signal?: AbortSignal): Promise<string[]>;
}

export type ProviderErrorKind = 'auth' | 'rate' | 'overloaded' | 'bad_request' | 'server' | 'network' | 'aborted' | 'other';

export class ProviderError extends Error {
  kind: ProviderErrorKind;
  status?: number;
  retryAfter?: number;
  constructor(kind: ProviderErrorKind, message: string, status?: number, retryAfter?: number) {
    super(message);
    this.name = 'ProviderError';
    this.kind = kind;
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

/** What running a tool through the engine returns. */
export interface ToolOutcome {
  text: string;
  isError: boolean;
  /** The person said no (the tool did not run). */
  denied?: boolean;
  ms?: number;
}
