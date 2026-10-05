import { useEffect, useState } from 'react';
import { Button, DropdownMenu, Icon, IconButton, type MenuItem } from '../../kit';
import { t } from '../../i18n';
import { chat } from '../../chat/controller.ts';
import { go } from '../../router';
import { activeProvider, presetOf, settings, updateSettings } from '../../state/settings';
import { useModels } from '../../state/models';
import { ProviderForm } from '../ProviderForm';
import { Composer } from './Composer';
import { Transcript } from './Transcript';
import type { ApprovalMode } from '../../chat/agent.ts';

const MODES: ApprovalMode[] = ['auto-read', 'ask', 'auto'];

function fmtK(n: number): string {
  return n >= 10000 ? `${(n / 1000).toFixed(0)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function Sidebar() {
  const st = chat.state.use();
  return (
    <aside className="ai-chat-side" aria-label={t('chat.history')}>
      <Button variant="secondary" icon="plus" block disabled={st.busy} onClick={() => chat.startNew()}>{t('chat.new')}</Button>
      <div className="ai-chat-side-list">
        {st.list.length === 0 && <div className="ai-muted" style={{ padding: 10 }}>{t('chat.noHistory')}</div>}
        {st.list.map((c) => (
          <button key={c.id} type="button" className="ai-chat-it" aria-current={c.id === st.chat.id} onClick={() => void chat.open(c.id)} title={c.title}>
            <span>{c.title || t('chat.untitled')}</span>
            <IconButton icon="trash" size="sm" label={t('common.delete')} className="ai-chat-del" onClick={(e) => { e.stopPropagation(); void chat.remove(c.id); }} />
          </button>
        ))}
      </div>
    </aside>
  );
}

function Bar() {
  const st = chat.state.use();
  const cfg = settings.use();
  const provider = activeProvider(cfg)!;
  const { models } = useModels(provider);
  const model = cfg.models[provider.id];
  const items: MenuItem[] = [
    { type: 'heading', label: provider.name },
    ...models.slice(0, 30).map((m): MenuItem => ({ id: m, label: m + (m === model ? '  ✓' : ''), onSelect: () => updateSettings((s) => ({ models: { ...s.models, [provider.id]: m } })) })),
    ...(cfg.providers.length > 1 ? [{ type: 'separator' } as MenuItem, { type: 'heading', label: t('chat.otherProviders') } as MenuItem,
      ...cfg.providers.filter((p) => p.id !== provider.id).map((p): MenuItem => ({ id: 'p-' + p.id, label: p.name, onSelect: () => updateSettings({ active: p.id }) }))] : []),
    { type: 'separator' },
    { id: 'settings', label: t('chat.providerSettings'), icon: 'cog', onSelect: () => go('settings') },
  ];
  const modeItems: MenuItem[] = MODES.map((m) => ({ id: m, label: t(`mode.${m}`) + (m === cfg.approval ? '  ✓' : ''), onSelect: () => updateSettings({ approval: m }) }));
  return (
    <div className="ai-chat-bar">
      <h2>{st.chat.title || t('chat.untitled')}</h2>
      <DropdownMenu items={items} aria-label={t('chat.model')} trigger={(p) => <button type="button" className="ai-pill" {...p} title={t('chat.model')}><Icon name="ai-sparkle" />{model || t('chat.pickModel')}</button>} />
      <DropdownMenu items={modeItems} aria-label={t('chat.approval')} trigger={(p) => <button type="button" className={`ai-pill${cfg.approval === 'auto' ? ' ai-pill--warn' : ''}`} {...p} title={t('chat.approval')}><Icon name="shield" />{t(`mode.${cfg.approval}.short`)}</button>} />
      <button type="button" className={`ai-pill${cfg.root ? ' ai-pill--warn' : ''}`} aria-pressed={cfg.root} onClick={() => chat.setRoot(!cfg.root)} title={t('chat.rootHint')}>
        <Icon name={cfg.root ? 'unlock' : 'lock'} />{cfg.root ? t('chat.rootOn') : t('chat.rootOff')}
      </button>
    </div>
  );
}

function EngineProblem({ message }: { message: string }) {
  return (
    <div className="ai-setup" role="alert">
      <h2>{t('chat.engine.title')}</h2>
      <p className="ai-note">{message}</p>
      <div className="ai-row">
        <Button variant="primary" icon="refresh" onClick={() => void chat.init()}>{t('common.retry')}</Button>
        <Button variant="ghost" onClick={() => go('server')}>{t('chat.engine.server')}</Button>
      </div>
    </div>
  );
}

export function ChatView() {
  const st = chat.state.use();
  const cfg = settings.use();
  const provider = activeProvider(cfg);
  const [adding, setAdding] = useState(false);
  useEffect(() => { void chat.refreshList(); }, []);

  if (st.engineError) return <div className="ai-view" style={{ justifyContent: 'center', flex: 1 }}><EngineProblem message={st.engineError} /></div>;
  if (!provider || !cfg.models[provider.id] || adding) {
    return (
      <div className="ai-view" style={{ flex: 1, overflowY: 'auto' }}>
        <div className="ai-setup">
          <h2>{provider ? t('chat.addProvider') : t('chat.setup.title')}</h2>
          <p className="ai-note">{t('chat.setup.text')}</p>
          <ProviderForm onDone={() => setAdding(false)} />
        </div>
      </div>
    );
  }
  const tokens = st.chat.usage.input + st.chat.usage.output > 0 ? t('chat.tokens', { input: fmtK(st.chat.usage.input), output: fmtK(st.chat.usage.output) }) : presetOf(provider)?.name ?? provider.name;
  return (
    <div className="ai-chat">
      <Sidebar />
      <div className="ai-chat-main">
        <Bar />
        <Transcript items={st.items} busy={st.busy} planning={st.planning} />
        <Composer busy={st.busy} tokens={tokens} />
      </div>
    </div>
  );
}

