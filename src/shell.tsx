import { useEffect } from 'react';
import { Icon, Skeleton } from './kit';
import { t } from './i18n';
import { chat } from './chat/controller.ts';
import { go, route, type View } from './router';
import { loadSettings, loaded, retrySettings, settings, settingsError } from './state/settings';
import { getSdk } from './sdk';
import { NAV_ICONS } from './ui/icons';
import { ActivityView } from './views/ActivityView';
import { ChatView } from './views/chat/ChatView';
import { ConnectView } from './views/ConnectView';
import { ServerView } from './views/ServerView';
import { SettingsView } from './views/SettingsView';
import { Welcome } from './views/Welcome';

const GROUPS: { label: string; items: Exclude<View, 'welcome'>[] }[] = [
  { label: 'nav.assistant', items: ['chat'] },
  { label: 'nav.server', items: ['server', 'connect', 'activity'] },
  { label: 'nav.setup', items: ['settings'] },
];

/** The page: inner sidebar and the current view. */
export function App({ native = false }: { native?: boolean }) {
  const ready = loaded.use();
  const s = settings.use();
  const view = route.use();
  const st = chat.state.use();
  const error = settingsError.use();
  const compact = getSdk().view?.surface === 'assistant';

  useEffect(() => {
    void (async () => {
      await loadSettings();
      const cfg = settings.get();
      go(native || compact ? 'chat' : cfg.welcomed ? cfg.landing : 'welcome');
      void chat.init();
    })();
  }, [native, compact]);

  useEffect(() => getSdk().visibility?.onChange(hidden => { if (!hidden) void chat.refreshVisible(); }), []);

  if (!ready) return <div className="ai-root hue-ov"><Skeleton height={300} style={{ borderRadius: 18 }} /></div>;
  const shown: View = !s.welcomed && !native && !compact && !error ? 'welcome' : view === 'welcome' ? 'chat' : view;
  if (shown === 'welcome') return <div className="ai-root hue-ov"><Welcome /></div>;

  return (
    <div className={`ai-root hue-ov${native ? ' ai-root--ervisios' : ''}${compact ? ' ai-root--compact' : ''}${shown === 'chat' ? ' ai-root--fill' : ''}`}>
      {error && <div className="ai-storage-error" role="alert"><span>{error}</span><button type="button" onClick={() => void retrySettings()}>{t('common.retry')}</button></div>}
      <div className="ai-shell">
        <nav className="ai-nav" aria-label={t('nav.aria')}>
          {GROUPS.map((g) => (
            <div key={g.label} style={{ display: 'contents' }}>
              <div className="ai-nav-gl">{t(g.label)}</div>
              {g.items.map((id) => (
                <button key={id} type="button" className="ai-nav-it" aria-current={shown === id ? 'page' : undefined} onClick={() => go(id)}>
                  <Icon name={NAV_ICONS[id]} />{t(`nav.${id}`)}
                </button>
              ))}
            </div>
          ))}
          <div className="ai-nav-ft">
            <span className={`ai-dot ai-dot--${st.engineError ? 'err' : st.identity ? 'ok' : 'info'}`} />
            <span>{st.engineError ? t('nav.engineDown') : st.identity ? t('nav.engine', { version: st.identity.version, user: st.identity.user }) : '…'}</span>
          </div>
        </nav>
        <div className="ai-main">
          <div className="ai-view">
            {shown === 'chat' && <ChatView />}
            {shown === 'server' && <ServerView />}
            {shown === 'connect' && <ConnectView />}
            {shown === 'activity' && <ActivityView />}
            {shown === 'settings' && <SettingsView />}
          </div>
        </div>
      </div>
    </div>
  );
}
