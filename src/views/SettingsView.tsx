import { useState } from 'react';
import { Button, ConfirmDialog, Dialog, Segmented, Select, Switch, Textarea, toast } from '../kit';
import { t } from '../i18n';
import { getSdk } from '../sdk';
import { chat } from '../chat/controller.ts';
import { deleteChat, listChats } from '../state/chats';
import { activeProvider, settings, updateSettings } from '../state/settings';
import { PageHeader } from '../ui/PageHeader';
import { ProviderForm } from './ProviderForm';
import type { ApprovalMode } from '../chat/agent.ts';
import type { ProviderConfig } from '../chat/types.ts';

const mask = (k: string): string => (k ? `••••${k.slice(-4)}` : '—');

export function SettingsView() {
  const s = settings.use();
  const current = activeProvider(s);
  const [adding, setAdding] = useState<ProviderConfig | 'new' | null>(null);
  const [removing, setRemoving] = useState<ProviderConfig | null>(null);
  const [wipe, setWipe] = useState(false);
  const v = getSdk().plugin.version;

  return (
    <div className="ai-col">
      <PageHeader icon="cog" hue="ov" title={t('settings.title')} subtitle={t('settings.subtitle')} />

      <div className="ai-card">
        <div className="ai-card-h"><h3>{t('settings.providers')}</h3><Button size="sm" variant="secondary" icon="plus" onClick={() => setAdding('new')}>{t('settings.addProvider')}</Button></div>
        <p>{t('settings.providersText')}</p>
        {s.providers.length === 0 && <div className="ai-note">{t('settings.noProviders')}</div>}
        <div className="ai-col" style={{ gap: 8 }}>
          {s.providers.map((p) => (
            <div key={p.id} className="ai-switchrow">
              <div>
                <b>{p.name} {p.id === current?.id && <span className="ai-tag ai-tag--acc">{t('settings.inUse')}</span>}</b>
                <span className="ai-mono">{p.baseUrl} · {s.models[p.id] || t('settings.noModel')} · {mask(p.apiKey)}</span>
              </div>
              {p.id !== current?.id && <Button size="sm" variant="secondary" onClick={() => updateSettings({ active: p.id })}>{t('settings.use')}</Button>}
              <Button size="sm" variant="ghost" icon="edit" onClick={() => setAdding(p)}>{t('common.edit')}</Button>
              <Button size="sm" variant="ghost" icon="trash" onClick={() => setRemoving(p)}>{t('common.delete')}</Button>
            </div>
          ))}
        </div>
        <div className="ai-note">{t('settings.keyWhere')}</div>
      </div>

      <div className="ai-card">
        <div className="ai-card-h"><h3>{t('settings.behavior')}</h3></div>
        <div className="ai-form" style={{ maxWidth: 'none' }}>
          <div>
            <div className="ai-muted" style={{ marginBottom: 8 }}>{t('settings.approval')}</div>
            <Segmented value={s.approval} onChange={(a) => updateSettings({ approval: a as ApprovalMode })}
              options={[{ value: 'auto-read', label: t('mode.auto-read'), icon: 'eye' }, { value: 'ask', label: t('mode.ask'), icon: 'lock' }, { value: 'auto', label: t('mode.auto'), icon: 'zap' }]} />
            <div className="ai-note" style={{ marginTop: 8 }}>{t(`mode.${s.approval}.text`)}</div>
          </div>
          <div className="ai-switchrow"><div><b>{t('settings.root')}</b><span>{t('settings.rootText')}</span></div><Switch checked={s.root} onChange={(on) => updateSettings({ root: on })} aria-label={t('settings.root')} /></div>
          <div className="ai-form-row">
            <Select label={t('settings.maxSteps')} value={String(s.maxSteps)} onChange={(x) => updateSettings({ maxSteps: Number(x) })} options={[10, 20, 40, 80, 150].map((n) => ({ value: String(n), label: String(n) }))} />
            <Select label={t('settings.maxTokens')} value={String(s.maxTokens)} onChange={(x) => updateSettings({ maxTokens: Number(x) })} options={[4000, 8000, 16000, 32000, 64000].map((n) => ({ value: String(n), label: n.toLocaleString() }))} />
          </div>
          <Textarea label={t('settings.extra')} hint={t('settings.extraHint')} rows={4} value={s.extraPrompt} onChange={(e) => updateSettings({ extraPrompt: e.target.value })} placeholder={t('settings.extraPh')} />
        </div>
      </div>

      <div className="ai-card">
        <div className="ai-card-h"><h3>{t('settings.data')}</h3></div>
        <p>{t('settings.dataText')}</p>
        <div className="ai-row"><Button variant="danger" icon="trash" onClick={() => setWipe(true)}>{t('settings.wipe')}</Button></div>
      </div>

      <div className="ai-card">
        <div className="ai-card-h"><h3>{t('settings.about')}</h3></div>
        <dl className="ai-kv"><dt>{t('settings.plugin')}</dt><dd>Ervisio AI {v}</dd><dt>{t('settings.engine')}</dt><dd>{chat.state.get().identity?.version ?? '—'}</dd></dl>
        <div className="ai-row"><Button size="sm" variant="ghost" icon="externallink" onClick={() => getSdk().openExternal('https://github.com/Ervisio/plugin-ai')}>GitHub</Button>
          <Button size="sm" variant="ghost" onClick={() => updateSettings({ welcomed: false })}>{t('settings.again')}</Button></div>
      </div>

      <Dialog open={!!adding} onClose={() => setAdding(null)} title={adding === 'new' ? t('settings.addProvider') : t('settings.editProvider')} size="lg">
        {adding && <ProviderForm initial={adding === 'new' ? undefined : adding} onDone={() => setAdding(null)} />}
      </Dialog>
      <ConfirmDialog open={!!removing} onClose={() => setRemoving(null)} danger title={t('settings.removeTitle', { name: removing?.name ?? '' })} description={t('settings.removeText')} confirmLabel={t('common.delete')}
        onConfirm={() => { const p = removing!; setRemoving(null); updateSettings((x) => ({ providers: x.providers.filter((q) => q.id !== p.id), active: x.active === p.id ? '' : x.active })); }} />
      <ConfirmDialog open={wipe} onClose={() => setWipe(false)} danger title={t('settings.wipeTitle')} description={t('settings.wipeText')} confirmLabel={t('settings.wipe')} confirmText="DELETE"
        onConfirm={async () => { setWipe(false); for (const c of await listChats()) await deleteChat(c.id); chat.startNew(); await chat.refreshList(); toast.ok(t('settings.wiped')); }} />
    </div>
  );
}
