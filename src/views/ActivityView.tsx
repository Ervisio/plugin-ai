import { useEffect, useMemo, useState } from 'react';
import { Button, EmptyState, Input, Segmented, Select, Skeleton, Switch, toast } from '../kit';
import { t } from '../i18n';
import { ctl } from '../engine.ts';
import type { AuditEntry } from '../state/server';
import { PageHeader } from '../ui/PageHeader';

function summary(e: AuditEntry): string {
  const a = e.args ?? {};
  const v = a.command ?? a.path ?? a.method ?? a.url ?? a.unit ?? a.action ?? a.pattern ?? a.id ?? (Object.keys(a).length ? JSON.stringify(a) : '');
  return String(v).replace(/\s+/g, ' ').slice(0, 140);
}

/** Everything the AI did on this machine, newest first. */
export function ActivityView() {
  const [scope, setScope] = useState<'mine' | 'server'>('mine');
  const [entries, setEntries] = useState<AuditEntry[] | undefined>();
  const [text, setText] = useState('');
  const [errors, setErrors] = useState(false);
  const [tool, setTool] = useState('');
  const [open, setOpen] = useState<AuditEntry | null>(null);
  const [live, setLive] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    try {
      const r = await ctl<{ entries: AuditEntry[] }>('audit', { limit: 300 }, { root: scope === 'server' });
      setEntries(r.entries);
    } catch (e) {
      toast.err(t('server.failed'), (e as Error).message);
      setEntries([]);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => { setEntries(undefined); void load(); }, [scope]);
  useEffect(() => {
    if (!live) return;
    const h = setInterval(() => void load(), 5000);
    return () => clearInterval(h);
  }, [live, scope]);

  const tools = useMemo(() => [...new Set((entries ?? []).map((e) => e.tool).filter(Boolean) as string[])].sort(), [entries]);
  const shown = (entries ?? []).filter((e) => (!errors || e.ok === false) && (!tool || e.tool === tool) && (!text || JSON.stringify(e).toLowerCase().includes(text.toLowerCase())));

  return (
    <div className="ai-col">
      <PageHeader icon="clock" hue="ov" title={t('activity.title')} subtitle={t('activity.subtitle')}
        actions={<><Segmented value={scope} onChange={(v) => setScope(v as 'mine')} options={[{ value: 'mine', label: t('activity.mine'), icon: 'user' }, { value: 'server', label: t('activity.server'), icon: 'server' }]} />
          <Button variant="ghost" icon="refresh" loading={busy} onClick={() => void load()}>{t('common.refresh')}</Button></>} />
      <div className="ai-row">
        <div style={{ flex: '1 1 260px' }}><Input icon="search" value={text} placeholder={t('activity.search')} onChange={(e) => setText(e.target.value)} /></div>
        <Select value={tool} onChange={setTool} options={[{ value: '', label: t('activity.allTools') }, ...tools.map((x) => ({ value: x, label: x }))]} />
        <Switch checked={errors} onChange={setErrors} label={t('activity.errors')} />
        <Switch checked={live} onChange={setLive} label={t('activity.live')} />
      </div>
      {scope === 'server' && <div className="ai-note">{t('activity.serverNote')}</div>}
      {entries === undefined ? <Skeleton height={240} style={{ borderRadius: 18 }} /> : shown.length === 0 ? (
        <EmptyState icon="clock" hue="ov" title={t('activity.none')} text={t('activity.noneText')} />
      ) : (
        <div className="ai-tablewrap">
          <table className="ai-table">
            <thead><tr><th>{t('activity.time')}</th><th className="ai-hide-md">{t('activity.client')}</th><th>{t('activity.tool')}</th><th>{t('activity.what')}</th><th className="ai-num">{t('activity.took')}</th></tr></thead>
            <tbody>
              {shown.map((e, i) => (
                <tr key={`${e.time}${i}`} className={`ai-click${e.ok === false ? ' ai-bad' : ''}`} onClick={() => setOpen(open === e ? null : e)}>
                  <td className="ai-mono ai-muted" style={{ whiteSpace: 'nowrap' }}>{e.time.slice(5, 19).replace('T', ' ')}</td>
                  <td className="ai-hide-md ai-muted">{e.client}</td>
                  <td><span className="ai-tag">{e.tool}</span>{e.root && <span className="ai-tag ai-tag--warn" style={{ marginLeft: 4 }}>root</span>}{e.denied && <span className="ai-tag ai-tag--err" style={{ marginLeft: 4 }}>{t('activity.refused')}</span>}</td>
                  <td className="ai-mono" style={{ overflowWrap: 'anywhere' }}>{summary(e)}{e.ok === false && e.error && <div className="ai-muted" style={{ color: 'var(--err)' }}>{e.error.split('\n')[0]}</div>}</td>
                  <td className="ai-num">{e.ms !== undefined ? (e.ms < 1000 ? `${e.ms} ms` : `${(e.ms / 1000).toFixed(1)} s`) : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && (
        <div className="ai-drawer">
          <div className="ai-card-h"><h3>{open.tool}</h3><Button size="sm" variant="ghost" icon="close" onClick={() => setOpen(null)}>{t('common.close')}</Button></div>
          <pre className="ai-logs">{JSON.stringify(open, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
