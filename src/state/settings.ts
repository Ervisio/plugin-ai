/** The person's settings, kept in ~/.config/ervisio/plugins/ai/settings.json (the folder is private to the account). */
import { getSdk } from '../sdk';
import { writeEnsured } from './files';
import { createStore } from './store.ts';
import { PRESETS } from '../chat/providers.ts';
import type { ProviderConfig } from '../chat/types.ts';
import type { ApprovalMode } from '../chat/agent.ts';

const FILE = '~/.config/ervisio/plugins/ai/settings.json';

export interface ConnectDraft {
  mode: 'ssh' | 'http';
  host: string;
  user: string;
  port: number;
  identity: string;
  name: string;
  embedToken: boolean;
}

export interface Settings {
  version: 1;
  /** false until the person has chosen how to use the plugin */
  welcomed: boolean;
  landing: 'chat' | 'server';
  providers: ProviderConfig[];
  active: string;
  models: Record<string, string>;
  approval: ApprovalMode;
  root: boolean;
  maxTokens: number;
  maxSteps: number;
  extraPrompt: string;
  connect: ConnectDraft;
}

export const DEFAULTS: Settings = {
  version: 1,
  welcomed: false,
  landing: 'chat',
  providers: [],
  active: '',
  models: {},
  approval: 'auto-read',
  root: false,
  maxTokens: 16000,
  maxSteps: 40,
  extraPrompt: '',
  connect: { mode: 'ssh', host: '', user: 'root', port: 22, identity: '', name: '', embedToken: true },
};

export const settings = createStore<Settings>(DEFAULTS);
export const loaded = createStore<boolean>(false);

export async function loadSettings(): Promise<void> {
  try {
    const raw = JSON.parse(await getSdk().files.read(FILE)) as Partial<Settings>;
    settings.set({ ...DEFAULTS, ...raw, connect: { ...DEFAULTS.connect, ...(raw.connect ?? {}) }, version: 1 });
  } catch {
    // no file yet: the defaults stand
  }
  loaded.set(true);
}

let timer: ReturnType<typeof setTimeout> | undefined;
let pending = false;

/** Change settings; they are written to disk shortly after. */
export function updateSettings(patch: Partial<Settings> | ((s: Settings) => Partial<Settings>)): void {
  settings.set((s) => ({ ...s, ...(typeof patch === 'function' ? patch(s) : patch) }));
  pending = true;
  clearTimeout(timer);
  timer = setTimeout(() => void flushSettings(), 400);
}

export async function flushSettings(): Promise<void> {
  clearTimeout(timer);
  if (!pending) return;
  pending = false;
  try {
    await writeEnsured(FILE, JSON.stringify(settings.get(), null, 2));
  } catch (e) {
    getSdk().ui.toast.err('Could not save the settings', (e as Error).message);
  }
}

export function activeProvider(s: Settings): ProviderConfig | undefined {
  return s.providers.find((p) => p.id === s.active) ?? s.providers[0];
}

export function presetOf(p: ProviderConfig) {
  return PRESETS.find((x) => x.id === p.id.split(':')[0]);
}
