import { useLayoutEffect, useRef } from 'react';
import { lazyMemo } from '../../ui/memo';
import { Icon } from '../../kit';
import { t } from '../../i18n';
import { Markdown } from '../../chat/Markdown';
import { chat } from '../../chat/controller.ts';
import type { Item } from '../../chat/items.ts';
import { ToolCard } from './ToolCard';

const IDEAS: [string, string][] = [
  ['search', 'idea.disk'], ['alert', 'idea.failing'], ['shield', 'idea.security'], ['refresh', 'idea.update'],
  ['globe', 'idea.caddy'], ['server', 'idea.explain'],
];

const NoticeIcon: Record<string, string> = { info: 'info', warn: 'alert', err: 'alert' };

const Row = lazyMemo(function Row({ item }: { item: Item }) {
  switch (item.kind) {
    case 'user':
      return <div className="ai-user"><span className="ai-user-g">{item.text.startsWith('I ran `') ? '$' : '›'}</span><span className="ai-user-t">{item.text}</span></div>;
    case 'assistant':
      return <div className={`ai-ai${item.streaming ? ' ai-cursor' : ''}`}><Markdown text={item.text} /></div>;
    case 'tool':
      return <ToolCard item={item} />;
    case 'notice':
      return <div className={`ai-notice ai-notice--${item.tone}`} role={item.tone === 'err' ? 'alert' : 'status'}><Icon name={NoticeIcon[item.tone]} /><span>{item.text}</span></div>;
  }
});

/** The conversation. It follows the newest line unless the person scrolled up to read. */
export function Transcript({ items, busy, planning }: { items: Item[]; busy: boolean; planning: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = items.length ? el.scrollHeight : 0;
  }, [items, planning, busy]);
  const onScroll = () => {
    const el = ref.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90;
  };
  return (
    <div className="ai-transcript" ref={ref} onScroll={onScroll} role="log" aria-live="polite" aria-label={t('chat.transcript')}>
      {items.length === 0 ? (
        <div className="ai-empty">
          <span className="ai-ph-ic hue-ov"><Icon name="ai-sparkle" /></span>
          <h2>{t('chat.empty.title')}</h2>
          <p>{t('chat.empty.text')}</p>
          <div className="ai-ideas">
            {IDEAS.map(([icon, key]) => <button key={key} type="button" className="ai-idea" onClick={() => void chat.send(t(key))}><Icon name={icon} />{t(key)}</button>)}
          </div>
        </div>
      ) : items.map((it) => <Row key={it.id} item={it} />)}
      {busy && (planning || items[items.length - 1]?.kind === 'user') && (
        <div className="ai-note ai-row"><span className="ai-spin" />{planning ? t('chat.planning', { tool: planning }) : t('chat.thinking')}</div>
      )}
    </div>
  );
}
