/** The person's settings, kept in ~/.config/ervisio/plugins/ai/settings.json (the folder is private to the account). */
import { getSdk } from '../sdk';
import { writeEnsured } from './files';
import { createStore } from './store.ts';
import { missing } from './chatRepository.ts';
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
export const settingsError = createStore<string>('');
let revision = '';
let writable = false;
let loading: Promise<void> | undefined;
let saving: Promise<void> = Promise.resolve();

function decodeSettings(text: string): Settings {
  const raw = JSON.parse(text) as Partial<Settings>;
  if (!raw || typeof raw !== 'object' || !Array.isArray(raw.providers)) throw new Error('The saved AI settings need repair. The original data has been preserved.');
  return { ...DEFAULTS, ...raw, connect: { ...DEFAULTS.connect, ...(raw.connect ?? {}) }, version: 1 };
}

export async function loadSettings(): Promise<void> {
  if (!loading) loading = (async () => {
    try {
      const data = getSdk().data;
      let text: string | undefined;
      if (data) {
        try { const file = await data.read('settings.json'); text = file.text; revision = file.revision; }
        catch (e) { if (!missing(e)) throw e; }
      }
      if (text === undefined) {
        try { text = await getSdk().files.read(FILE); }
        catch (e) { if (!missing(e)) throw e; }
        const imported = text === undefined ? structuredClone(DEFAULTS) : decodeSettings(text);
        if (data) {
          try { revision = (await data.write('settings.json', JSON.stringify(imported), '')).revision; }
          catch (e) {
            if ((e as { code?: string }).code !== 'conflict') throw e;
            const winner = await data.read('settings.json'); text = winner.text; revision = winner.revision;
          }
        }
      }
      settings.set(text === undefined ? structuredClone(DEFAULTS) : decodeSettings(text));
      writable = true;
      settingsError.set('');
    } catch (e) { writable = false; settingsError.set((e as Error).message); }
    loaded.set(true);
  })();
  await loading;
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
  const snapshot = JSON.stringify(settings.get(), null, 2);
  pending = false;
  const work = saving.then(async () => {
    if (!writable) throw new Error('The original settings could not be loaded. Retry loading them before saving.');
    const data = getSdk().data;
    if (data) revision = (await data.write('settings.json', snapshot, revision)).revision;
    else await writeEnsured(FILE, snapshot);
    settingsError.set('');
  });
  saving = work.catch(() => {});
  try {
    await work;
  } catch (e) {
    pending = true;
    settingsError.set((e as Error).message);
    getSdk().ui.toast.err('Could not save the settings', (e as Error).message);
  }
}

export async function retrySettings(): Promise<void> {
  if (writable) { await flushSettings(); return; }
  loading = undefined;
  await loadSettings();
  // On an initial read failure no on-disk state was replaced. Keep that state
  // authoritative when retrying rather than silently saving temporary defaults.
  if (writable) pending = false;
}

export function activeProvider(s: Settings): ProviderConfig | undefined {
  return s.providers.find((p) => p.id === s.active) ?? s.providers[0];
}

export function presetOf(p: ProviderConfig) {
  return PRESETS.find((x) => x.id === p.id.split(':')[0]);
}
