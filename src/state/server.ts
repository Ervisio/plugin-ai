/** What the server pages know about the engine on this machine. */
import { ctl } from '../engine.ts';
import { createStore } from './store.ts';

export interface ServerStatus {
  version: string;
  root: boolean;
  hostname: string;
  engine: { installed: boolean; version: string | null; path: string; wrapper: boolean; outdated: boolean };
  service: { manager: string; installed: boolean; active: boolean; enabled: boolean; pid: number | null };
  config: { bind: string; port: number; tls: 'none' | 'self-signed' | 'custom'; cert: string; key: string; allowed_origins: string[]; allowed_hosts: string[] };
  url: string;
  reachable: boolean;
  tokens: number | null;
  policy: Policy;
  tools: Record<string, boolean>;
  ervisio_bridge: string | null;
  python: string;
}

export interface Policy {
  mode: 'full' | 'readonly';
  disabled_tools: string[];
  disabled_categories: string[];
  allow_root: boolean;
  guard: boolean;
  deny_paths: string[];
  ervisio_deny_methods: string[];
  max_timeout_sec: number;
  audit: boolean;
}

export interface Check { name: string; ok: boolean; detail: string; fix: string; level: 'ok' | 'warn' | 'info' }

export interface ConnectInfo {
  hostname: string;
  addresses: string[];
  ssh: { port: number; running: boolean; permit_root_login: string; password_auth: string; installed: boolean };
  users: { name: string; uid: number; home: string; ssh_keys: boolean }[];
  config: ServerStatus['config'];
  wrapper: string;
  stdio_command: string;
}

export interface TokenInfo { id: string; label: string; scope: 'full' | 'readonly'; created: string; expires: string | null; last_used: string | null; last_ip: string | null }

export interface Snippet { id: string; label: string; where: string; lang: 'json' | 'toml' | 'shell'; body: string; unsupported: string | null }

export interface AuditEntry {
  time: string; client?: string; transport?: string; ip?: string; tool?: string; args?: Record<string, unknown>; ok?: boolean; error?: string; ms?: number;
  exit_code?: number; root?: boolean; denied?: boolean;
}

interface ServerState {
  status?: ServerStatus;
  info?: ConnectInfo;
  error?: string;
  loading: boolean;
}

export const server = createStore<ServerState>({ loading: false });

/** Read the state (as the signed-in user: no administrator rights needed). */
export async function refreshServer(): Promise<void> {
  server.set((s) => ({ ...s, loading: true }));
  try {
    const [status, info] = await Promise.all([ctl<ServerStatus>('status'), ctl<ConnectInfo>('connect_info')]);
    server.set({ status, info, loading: false });
  } catch (e) {
    server.set((s) => ({ ...s, loading: false, error: (e as Error).message }));
  }
}
