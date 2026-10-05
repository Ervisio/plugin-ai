/**
 * Talking to the engine on the server. The page never runs anything itself: it writes a small request file and
 * starts one of the plugin's declared commands, which reads it (see plugin/manifest.json).
 *
 *   ai-call / ai-root-call   `ervisio-ai call <id>`   one tool call, as you / as root; JSON lines come back
 *   ai-ctl  / ai-root-ctl    `ervisio-ai ctl <id>`    administration (status, install, tokens...)
 *   ai-info                  tools | whoami | version
 *
 * The root variants are `admin` commands: Ervisio asks for administrator rights the first time and remembers them
 * for a few minutes.
 */
import { getSdk, type PluginError } from './sdk';
import { writeEnsured } from './state/files';
import type { ToolDef, ToolOutcome } from './chat/types.ts';

const USER_SPOOL = '~/.config/ervisio/plugins/ai/spool';
const ROOT_SPOOL = '/var/lib/ervisio-ai/spool';
/** The commands in the manifest give an engine call 600 s; shorter limits inside tools are clamped to stay below it. */
export const MAX_TOOL_SECONDS = 590;

export class EngineError extends Error {
  code: string;
  constructor(message: string, code = 'engine') {
    super(message);
    this.name = 'EngineError';
    this.code = code;
  }
}

export function newId(): string {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
}

function friendly(e: unknown): EngineError {
  const err = e as PluginError;
  if (err?.code === 'needs_admin') return new EngineError('Administrator rights were not granted.', 'needs_admin');
  if (err?.code === 'forbidden') return new EngineError(err.message || 'Not allowed.', 'forbidden');
  return new EngineError(err?.message || String(e), err?.code || 'engine');
}

async function writeRequest(root: boolean, id: string, body: unknown): Promise<void> {
  const dir = root ? ROOT_SPOOL : USER_SPOOL;
  try {
    await writeEnsured(`${dir}/${id}.json`, JSON.stringify(body));
  } catch (e) {
    throw friendly(e);
  }
}

export interface Identity {
  user: string;
  uid: number;
  home: string;
  root: boolean;
  hostname: string;
  version: string;
  engine: string;
  data_dir: string;
}

/** Run `ai-info`: one JSON document on stdout. */
async function info<T>(what: 'tools' | 'whoami' | 'version'): Promise<T> {
  let r;
  try {
    r = await getSdk().api.exec('ai-info', [what]);
  } catch (e) {
    throw friendly(e);
  }
  if (r.exitCode !== 0) {
    const gone = /can't open file|No such file|not found/i.test(r.stderr);
    throw new EngineError(gone
      ? 'The engine is not where the plugin expects it (/var/lib/ervisio/plugins/ai/engine). Install the plugin from the marketplace, or copy the built folder there when developing.'
      : r.stderr.trim() || `The engine exited with ${r.exitCode}.`, gone ? 'missing' : 'engine');
  }
  if (what === 'version') return r.stdout.trim() as unknown as T;
  try {
    return JSON.parse(r.stdout) as T;
  } catch {
    throw new EngineError('The engine answered with something that is not JSON.');
  }
}

let identity: Promise<Identity> | undefined;
export function whoami(): Promise<Identity> {
  identity ??= info<Identity>('whoami').catch((e) => {
    identity = undefined;
    throw e;
  });
  return identity;
}

export interface ToolList {
  version: string;
  policy: Record<string, unknown>;
  tools: ToolDef[];
}
export const listTools = (): Promise<ToolList> => info<ToolList>('tools');
export const engineVersion = (): Promise<string> => info<string>('version');

/** Run one command with a request file and collect its JSON lines. */
function runLines(command: string, id: string, signal: AbortSignal | undefined, onLine: (o: Record<string, any>) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return resolve();
    let done = false;
    const stderr: string[] = [];
    const h = getSdk().api.execStream(command, [id], {
      onLine(stream, line) {
        if (stream === 'stderr') {
          stderr.push(line);
          return;
        }
        try {
          onLine(JSON.parse(line));
        } catch {
          // not one of ours
        }
      },
      onExit(code) {
        if (done) return;
        done = true;
        if (code !== 0 && code !== 130 && code !== 143) reject(new EngineError(stderr.slice(-3).join(' ').trim() || `The engine exited with ${code}.`));
        else resolve();
      },
      onError(e) {
        if (done) return;
        done = true;
        reject(friendly(e));
      },
    });
    signal?.addEventListener('abort', () => {
      h.close();
      if (!done) {
        done = true;
        resolve();
      }
    }, { once: true });
  });
}

export interface CallOptions {
  root: boolean;
  signal?: AbortSignal;
  onProgress?(text: string): void;
}

/** Run one tool, as the signed-in user or (root: true) with administrator rights. */
export async function callTool(name: string, input: Record<string, unknown>, o: CallOptions): Promise<ToolOutcome> {
  const args = clampTimeout(name, input);
  const id = newId();
  await writeRequest(o.root, id, { tool: name, args, client: 'ervisio-chat' });
  const started = Date.now();
  let result: { content?: { text?: string }[]; isError?: boolean } | undefined;
  await runLines(o.root ? 'ai-root-call' : 'ai-call', id, o.signal, (m) => {
    if (m.t === 'progress') o.onProgress?.(String(m.message ?? ''));
    else if (m.t === 'result') result = m.result;
    else if (m.t === 'error') throw new EngineError(String(m.message));
  });
  if (o.signal?.aborted && !result) return { text: 'Stopped before it finished.', isError: true, ms: Date.now() - started };
  if (!result) throw new EngineError('The engine returned no result.');
  return { text: result.content?.map((c) => c.text ?? '').join('\n') ?? '', isError: !!result.isError, ms: Date.now() - started };
}

/** Does this call only read? Asked of the engine, which runs nothing. */
export async function classify(name: string, input: Record<string, unknown>): Promise<boolean> {
  const id = newId();
  await writeRequest(false, id, { tool: name, args: input, classify: true });
  let read = false;
  await runLines('ai-call', id, undefined, (m) => {
    if (m.t === 'result') read = !!m.result?.classify?.readOnly;
  });
  return read;
}

/** Keep a tool's own timeout under what the command allows. */
export function clampTimeout(name: string, input: Record<string, unknown>): Record<string, unknown> {
  if (['shell_exec', 'docker_cli', 'package_manage', 'ssh_exec'].includes(name)) {
    const t = typeof input.timeout === 'number' ? input.timeout : undefined;
    if (t === undefined || t > MAX_TOOL_SECONDS) return { ...input, timeout: Math.min(t ?? 120, MAX_TOOL_SECONDS) };
  }
  return input;
}

/** Administration. `root: true` for anything that changes the server. */
export async function ctl<T = Record<string, any>>(action: string, params: Record<string, unknown> = {}, o: { root?: boolean } = {}): Promise<T> {
  const id = newId();
  const root = !!o.root;
  await writeRequest(root, id, { action, params });
  let out: T | undefined;
  await runLines(root ? 'ai-root-ctl' : 'ai-ctl', id, undefined, (m) => {
    if (m.t === 'result') out = m.result as T;
    else if (m.t === 'error') throw new EngineError(String(m.message), 'ctl');
  });
  if (out === undefined) throw new EngineError('The engine returned no result.');
  return out;
}
