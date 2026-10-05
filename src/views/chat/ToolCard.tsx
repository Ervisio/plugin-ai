import { useState } from 'react';
import { lazyMemo } from '../../ui/memo';
import { Button, Icon } from '../../kit';
import { t } from '../../i18n';
import { chat } from '../../chat/controller.ts';
import { describeCall } from '../../chat/summary.ts';
import type { ToolItem } from '../../chat/items.ts';

const STATUS_ICON: Record<ToolItem['status'], string> = { approval: 'lock', running: 'play', done: 'check', error: 'alert', denied: 'x' };

function fmtMs(ms?: number): string {
  if (ms === undefined) return '';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

/** One tool call: what it does, the person's approval when needed, live output while it runs, the result after. */
export const ToolCard = lazyMemo(function ToolCard({ item }: { item: ToolItem }) {
  const [open, setOpen] = useState(false);
  const [args, setArgs] = useState(false);
  const s = describeCall(item.name, item.input);
  const out = item.output ?? '';
  const long = out.split('\n').length > 8 || out.length > 900;
  const call = { id: item.id, name: item.name, input: item.input };
  return (
    <div className={`ai-tool ai-tool--${item.status === 'running' ? 'run' : item.status}`} role="group" aria-label={`${s.title} ${s.detail}`}>
      <div className="ai-tool-h">
        <span className="ai-tool-ic">{item.status === 'running' ? <span className="ai-spin" /> : <Icon name={STATUS_ICON[item.status]} />}</span>
        <span className="ai-tool-t">{s.title}</span>
        <span className="ai-tool-d" title={s.detail}>{s.detail}</span>
        {item.root && <span className="ai-tag ai-tag--warn">{t('tool.root')}</span>}
        {item.status === 'denied' && <span className="ai-tag">{t('tool.denied')}</span>}
        {item.ms !== undefined && <span className="ai-tool-ms">{fmtMs(item.ms)}</span>}
      </div>
      {item.status === 'approval' && (
        <>
          <pre className="ai-tool-args">{JSON.stringify(item.input, null, 2)}</pre>
          <div className="ai-tool-ask">
            <Button size="sm" variant="primary" icon="check" onClick={() => chat.decide(call, 'allow')}>{t('tool.allow')}</Button>
            <Button size="sm" variant="secondary" onClick={() => chat.decide(call, 'always')}>{t('tool.always', { tool: item.name })}</Button>
            <Button size="sm" variant="ghost" onClick={() => chat.decide(call, 'deny')}>{t('tool.deny')}</Button>
            {item.root && <span className="ai-note">{t('tool.rootNote')}</span>}
          </div>
        </>
      )}
      {item.status === 'running' && item.progress && <pre className="ai-tool-out">{item.progress}</pre>}
      {out && item.status !== 'running' && item.status !== 'approval' && (
        <>
          <pre className={`ai-tool-out${open ? ' ai-tool-out--open' : ''}`}>{open || !long ? out : out.split('\n').slice(0, 8).join('\n') + (long ? '\n…' : '')}</pre>
          <div className="ai-row" style={{ gap: 14 }}>
            {long && <button type="button" className="ai-tool-more" onClick={() => setOpen(!open)}>{open ? t('tool.less') : t('tool.more')}</button>}
            <button type="button" className="ai-tool-more" onClick={() => setArgs(!args)}>{args ? t('tool.hideArgs') : t('tool.showArgs')}</button>
          </div>
          {args && <pre className="ai-tool-args">{JSON.stringify(item.input, null, 2)}</pre>}
        </>
      )}
    </div>
  );
});
