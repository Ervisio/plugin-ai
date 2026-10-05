import { useState } from 'react';
import { Button, Chip, Input, Select, toast } from '../kit';
import { t } from '../i18n';
import { getSdk } from '../sdk';
import { PRESETS, type ProviderPreset } from '../chat/providers.ts';
import { fetchModels } from '../state/models';
import { settings, updateSettings } from '../state/settings';
import type { ProviderConfig } from '../chat/types.ts';

/** Add (or replace) an API provider: choose it, paste the key, test it, pick a model. Used by the chat and by Settings. */
export function ProviderForm({ onDone, initial }: { onDone?(): void; initial?: ProviderConfig }) {
  const [presetId, setPresetId] = useState(initial ? initial.id.split(':')[0] : 'anthropic');
  const preset = PRESETS.find((p) => p.id === presetId) as ProviderPreset;
  const [name, setName] = useState(initial?.name ?? '');
  const [baseUrl, setBaseUrl] = useState(initial?.baseUrl ?? preset.baseUrl);
  const [key, setKey] = useState(initial?.apiKey ?? '');
  const [show, setShow] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState(initial ? settings.get().models[initial.id] ?? '' : preset.defaultModel);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const custom = preset.note === 'custom';

  const pick = (id: string) => {
    const p = PRESETS.find((x) => x.id === id) as ProviderPreset;
    setPresetId(id);
    setBaseUrl(p.baseUrl);
    setModel(p.defaultModel);
    setModels([]);
    setError('');
  };

  const config = (): ProviderConfig => ({
    id: custom ? initial?.id ?? `custom:${Math.random().toString(36).slice(2, 7)}` : preset.id,
    kind: preset.kind,
    name: custom ? name.trim() || t('provider.custom') : preset.name,
    baseUrl: baseUrl.trim().replace(/\/+$/, ''),
    apiKey: key.trim(),
  });

  const test = async () => {
    setBusy(true);
    setError('');
    try {
      const cfg = config();
      // A new address needs an administrator's approval, after which Ervisio reloads this page: keep the provider.
      const list = await fetchModels(cfg, true, () => {
        updateSettings((st) => ({
          providers: [...st.providers.filter((p) => p.id !== cfg.id), cfg],
          active: st.active || cfg.id,
          models: { ...st.models, [cfg.id]: st.models[cfg.id] ?? model.trim() },
        }));
        toast.info(t('provider.reloading'));
      });
      setModels(list);
      if (!model || !list.includes(model)) setModel(list.find((m) => m === preset.defaultModel) ?? list[0] ?? '');
      toast.ok(t('provider.keyOk'), t('provider.modelsFound', { n: list.length }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const save = () => {
    const cfg = config();
    if (!model.trim()) {
      setError(t('provider.pickModel'));
      return;
    }
    updateSettings((s) => ({
      providers: [...s.providers.filter((p) => p.id !== cfg.id), cfg],
      active: cfg.id,
      models: { ...s.models, [cfg.id]: model.trim() },
    }));
    toast.ok(t('provider.saved', { name: cfg.name }));
    onDone?.();
  };

  const needsKey = preset.kind === 'anthropic' || !custom;
  const ready = baseUrl.trim() && (key.trim() || !needsKey);

  return (
    <div className="ai-form">
      <div>
        <div className="ai-muted" style={{ marginBottom: 8 }}>{t('provider.which')}</div>
        <div className="ai-presets">
          {PRESETS.map((p) => <Chip key={p.id} pressed={p.id === presetId} onClick={() => pick(p.id)}>{p.id === 'custom' ? t('provider.custom') : p.name}</Chip>)}
        </div>
      </div>
      {custom && (
        <>
          <Input label={t('provider.name')} value={name} onChange={(e) => setName(e.target.value)} placeholder="Ollama" />
          <Input label={t('provider.baseUrl')} value={baseUrl} mono hint={t('provider.baseUrlHint')} onChange={(e) => setBaseUrl(e.target.value)} />
        </>
      )}
      <Input
        label={needsKey ? t('provider.key') : t('provider.keyOptional')} type={show ? 'text' : 'password'} value={key} mono autoComplete="off" spellCheck={false}
        placeholder={preset.kind === 'anthropic' ? 'sk-ant-…' : ''}
        hint={<>{preset.keyUrl && <a onClick={() => getSdk().openExternal(preset.keyUrl)}>{t('provider.getKey')}</a>} {t('provider.keyStored')}</>}
        onChange={(e) => setKey(e.target.value)}
        end={<Button size="sm" variant="ghost" onClick={() => setShow(!show)}>{show ? t('common.hide') : t('common.show')}</Button>}
      />
      <div className="ai-row">
        <Button variant="secondary" icon="refresh" loading={busy} disabled={!ready} onClick={test}>{t('provider.test')}</Button>
        {models.length > 0 && <span className="ai-tag ai-tag--ok">{t('provider.modelsFound', { n: models.length })}</span>}
      </div>
      {error && <div className="ai-notice ai-notice--err" role="alert">{error}</div>}
      {models.length > 0 ? (
        <Select label={t('provider.model')} value={model} options={models.map((m) => ({ value: m, label: m }))} onChange={setModel} />
      ) : (
        <Input label={t('provider.model')} value={model} mono placeholder={preset.id === 'anthropic' ? 'claude-sonnet-5-5' : ''} hint={t('provider.modelHint')} onChange={(e) => setModel(e.target.value)} />
      )}
      <div className="ai-row">
        <Button variant="primary" icon="check" disabled={!ready || !model.trim()} onClick={save}>{t('provider.save')}</Button>
        {onDone && initial && <Button variant="ghost" onClick={onDone}>{t('common.cancel')}</Button>}
      </div>
    </div>
  );
}
