import { Icon } from '../kit';
import { t } from '../i18n';
import { go } from '../router';
import { updateSettings } from '../state/settings';

const CAPS: [string, string][] = [
  ['terminal', 'welcome.cap.shell'], ['files', 'welcome.cap.files'], ['services', 'welcome.cap.services'], ['software', 'welcome.cap.packages'],
  ['server', 'welcome.cap.docker'], ['ai-chat', 'welcome.cap.terminals'], ['plugins', 'welcome.cap.ervisio'], ['star', 'welcome.cap.memory'],
];

/** First run: the two ways of using the plugin. */
export function Welcome() {
  const choose = (landing: 'chat' | 'server') => {
    updateSettings({ welcomed: true, landing });
    go(landing);
  };
  return (
    <div className="ai-welcome hue-ov">
      <div className="ai-row" style={{ gap: 12 }}>
        <span className="ai-ph-ic"><Icon name="ai-sparkle" /></span>
        <span className="ai-muted" style={{ fontSize: 13.5 }}>{t('welcome.kicker')}</span>
      </div>
      <h1>{t('welcome.title')}</h1>
      <p>{t('welcome.intro')}</p>
      <div className="ai-choose">
        <button type="button" className="ai-choice hue-term" onClick={() => choose('chat')}>
          <span className="ai-choice-ic"><Icon name="ai-chat" /></span>
          <h2>{t('welcome.chat.title')}</h2>
          <p>{t('welcome.chat.text')}</p>
          <ul>
            <li><Icon name="check" />{t('welcome.chat.a')}</li>
            <li><Icon name="check" />{t('welcome.chat.b')}</li>
            <li><Icon name="check" />{t('welcome.chat.c')}</li>
          </ul>
          <span className="ai-choice-go">{t('welcome.chat.go')}<Icon name="right" /></span>
        </button>
        <button type="button" className="ai-choice hue-ov" onClick={() => choose('server')}>
          <span className="ai-choice-ic"><Icon name="ai-plug" /></span>
          <h2>{t('welcome.server.title')}</h2>
          <p>{t('welcome.server.text')}</p>
          <ul>
            <li><Icon name="check" />{t('welcome.server.a')}</li>
            <li><Icon name="check" />{t('welcome.server.b')}</li>
            <li><Icon name="check" />{t('welcome.server.c')}</li>
          </ul>
          <span className="ai-choice-go">{t('welcome.server.go')}<Icon name="right" /></span>
        </button>
      </div>
      <div className="ai-caps hue-plg">{CAPS.map(([icon, key]) => <span key={key}><Icon name={icon} />{t(key)}</span>)}</div>
      <p className="ai-note">{t('welcome.both')}</p>
    </div>
  );
}
