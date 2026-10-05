/** The model lists of the providers, fetched from their APIs and remembered while the page is open. */
import { useEffect, useState } from 'react';
import { createProvider } from '../chat/providers.ts';
import { ensureHostAllowed } from '../chat/controller.ts';
import type { ProviderConfig } from '../chat/types.ts';

const cache = new Map<string, string[]>();

export async function fetchModels(cfg: ProviderConfig, force = false, beforeAsk?: () => void | Promise<void>): Promise<string[]> {
  const key = `${cfg.id}|${cfg.baseUrl}|${cfg.apiKey.slice(-6)}`;
  if (!force && cache.has(key)) return cache.get(key)!;
  await ensureHostAllowed(cfg, beforeAsk);
  const ids = (await createProvider(cfg).listModels()).sort();
  cache.set(key, ids);
  return ids;
}

export function useModels(cfg: ProviderConfig | undefined): { models: string[]; loading: boolean; error: string; reload(): void } {
  const [state, setState] = useState<{ models: string[]; loading: boolean; error: string }>({ models: [], loading: !!cfg, error: '' });
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!cfg) return;
    let live = true;
    setState((s) => ({ ...s, loading: true, error: '' }));
    fetchModels(cfg, n > 0).then(
      (models) => live && setState({ models, loading: false, error: '' }),
      (e: Error) => live && setState({ models: [], loading: false, error: e.message }),
    );
    return () => { live = false; };
  }, [cfg?.id, cfg?.baseUrl, cfg?.apiKey, n]);
  return { ...state, reload: () => setN((x) => x + 1) };
}
