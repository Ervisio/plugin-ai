import { useEffect, useState, type ReactNode } from 'react';
import { Badge, Button, Checkbox, ConfirmDialog, EmptyState, Icon, Input, Segmented, Select, Skeleton, Switch, Tabs, Textarea, toast } from '../kit';
import { t } from '../i18n';
import { ctl } from '../engine.ts';
import { go } from '../router';
import { refreshServer, server, type Check, type Policy, type ServerStatus, type TokenInfo } from '../state/server';
import { Code } from '../ui/Code';
import { PageHeader } from '../ui/PageHeader';

const CATEGORIES = ['shell', 'terminal', 'files', 'system', 'services', 'packages', 'network', 'docker', 'ervisio', 'meta'];

/** Run an administrative action: a spinner while it works, the new state afterwards, a toast when it fails. */
function useAct() {
  const [busy, setBusy] = useState('');
  const act = async <T,>(id: string, fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
    setBusy(id);
    try {
      const r = await fn();
      if (ok) toast.ok(ok);
      await refreshServer();
      return r;
    } catch (e) {
      toast.err(t('server.failed'), (e as Error).message);
      return undefined;
    } finally {
      setBusy('');
    }
  };
  return { busy, act };
}

function Step({ done, title, children }: { done?: boolean; title: ReactNode; children: ReactNode }) {
  return <section className={`ai-step${done ? ' ai-step--done' : ''}`}><h3>{title}</h3>{children}</section>;
}

// --- overview -----------------------------------------------------------------------------------------------------

function Overview({ s }: { s: ServerStatus }) {
  const { busy, act } = useAct();
  const { info } = server.use();
  const [bind, setBind] = useState(s.config.bind === '127.0.0.1' ? 'local' : 'all');
  const [port, setPort] = useState(String(s.config.port));
  const [tls, setTls] = useState<string>(s.config.tls);
  const eng = s.engine;
  const sshOk = info?.ssh.running;
  const svc = s.service;
  const network = { bind: bind === 'local' ? '127.0.0.1' : '0.0.0.0', port: Number(port) || 7420, tls };

  return (
    <div className="ai-col">
      <div className="ai-steps">
        <Step done={eng.installed && !eng.outdated} title={t('server.step1')}>
          <p>{t('server.step1.text')}</p>
          <dl className="ai-kv">
            <dt>{t('server.engine')}</dt><dd>{eng.installed ? <>{t('server.installedAt', { path: eng.path })} <span className="ai-tag">v{eng.version}</span>{eng.outdated && <span className="ai-tag ai-tag--warn">{t('server.update', { v: s.version })}</span>}</> : t('server.notInstalled')}</dd>
            <dt>Python</dt><dd className="ai-mono">{s.python}</dd>
            <dt>tmux</dt><dd>{s.tools.tmux ? t('server.tmuxOk') : <>{t('server.tmuxNo')} <Button size="sm" variant="secondary" loading={busy === 'tmux'} onClick={() => act('tmux', () => ctl('deps_install', { packages: ['tmux'] }, { root: true }), t('server.tmuxInstalled'))}>{t('server.install')}</Button></>}</dd>
            <dt>Ervisio</dt><dd>{s.ervisio_bridge ? <span className="ai-mono">{s.ervisio_bridge}</span> : t('server.noBridge')}</dd>
          </dl>
          <div className="ai-row">
            {!eng.installed && <Button variant="primary" icon="download" loading={busy === 'engine'} onClick={() => act('engine', () => ctl('install', { service: false }, { root: true }), t('server.engineInstalled'))}>{t('server.installEngine')}</Button>}
            {eng.installed && eng.outdated && <Button variant="primary" icon="refresh" loading={busy === 'up'} onClick={() => act('up', () => ctl('upgrade', {}, { root: true }), t('server.upgraded'))}>{t('server.upgrade')}</Button>}
          </div>
        </Step>

        <Step title={t('server.step2')}>
          <p>{t('server.step2.text')}</p>
          <div className="ai-grid-2">
            <div className="ai-card">
              <div className="ai-card-h"><h3>{t('server.ssh.title')}</h3><Badge tone="ok">{t('server.recommended')}</Badge></div>
              <p>{t('server.ssh.text')}</p>
              <div className="ai-row"><span className={`ai-dot ai-dot--${sshOk ? 'ok' : 'warn'}`} /><span className="ai-note">{info ? (sshOk ? t('server.ssh.up', { port: info.ssh.port }) : t('server.ssh.down', { port: info.ssh.port })) : '…'}</span></div>
              {info && info.ssh.permit_root_login === 'no' && <div className="ai-note">{t('server.ssh.noRoot')}</div>}
              <Button variant="secondary" icon="ai-plug" onClick={() => go('connect')}>{t('server.ssh.connect')}</Button>
            </div>
            <div className="ai-card">
              <div className="ai-card-h"><h3>{t('server.http.title')}</h3>{svc.active ? <Badge tone="ok" dot>{t('server.running')}</Badge> : <Badge tone="neutral">{svc.installed ? t('server.stopped') : t('server.notSetUp')}</Badge>}</div>
              <p>{t('server.http.text')}</p>
              {svc.active && <dl className="ai-kv"><dt>URL</dt><dd className="ai-mono">{s.url}</dd><dt>{t('server.process')}</dt><dd className="ai-mono">{svc.manager} · pid {svc.pid ?? '?'}{s.reachable ? '' : ' · ' + t('server.noAnswer')}</dd></dl>}
              <div className="ai-form-row">
                <Select label={t('server.bind')} value={bind} options={[{ value: 'local', label: t('server.bind.local') }, { value: 'all', label: t('server.bind.all') }]} onChange={setBind} />
                <Input label={t('server.port')} value={port} mono inputMode="numeric" onChange={(e) => setPort(e.target.value.replace(/\D/g, ''))} />
                <Select label="TLS" value={tls} options={[{ value: 'none', label: t('server.tls.none') }, { value: 'self-signed', label: t('server.tls.self') }]} onChange={setTls} />
              </div>
              {bind === 'all' && tls === 'none' && <div className="ai-notice ai-notice--warn"><Icon name="alert" /><span>{t('server.warnPlain')}</span></div>}
              {tls === 'self-signed' && <div className="ai-note">{t('server.selfSignedNote')}</div>}
              <div className="ai-row">
                {!svc.installed
                  ? <Button variant="primary" icon="play" disabled={!eng.installed && false} loading={busy === 'svc'} onClick={() => act('svc', () => ctl('install', { service: true, ...network }, { root: true }), t('server.started'))}>{t('server.startHttp')}</Button>
                  : <>
                    <Button variant="secondary" icon="check" loading={busy === 'cfg'} onClick={() => act('cfg', () => ctl('config_set', network, { root: true }), t('server.applied'))}>{t('server.apply')}</Button>
                    {svc.active
                      ? <><Button variant="secondary" icon="refresh" loading={busy === 'restart'} onClick={() => act('restart', () => ctl('restart', {}, { root: true }))}>{t('server.restart')}</Button>
                        <Button variant="danger" icon="stop" loading={busy === 'stop'} onClick={() => act('stop', () => ctl('stop', {}, { root: true }))}>{t('server.stop')}</Button></>
                      : <Button variant="primary" icon="play" loading={busy === 'start'} onClick={() => act('start', () => ctl('start', {}, { root: true }))}>{t('server.start')}</Button>}
                  </>}
              </div>
              {svc.installed && svc.manager === 'systemd' && <Switch checked={svc.enabled} onChange={(v) => void act('boot', () => ctl(v ? 'enable' : 'disable', {}, { root: true }))} label={t('server.atBoot')} />}
            </div>
          </div>
        </Step>

        <Step title={t('server.step3')}>
          <p>{t('server.step3.text')}</p>
          <div className="ai-row"><Button variant="primary" icon="ai-plug" onClick={() => go('connect')}>{t('server.openConnect')}</Button></div>
        </Step>
      </div>
    </div>
  );
}

// --- tokens ---------------------------------------------------------------------------------------------------------

function Tokens({ s }: { s: ServerStatus }) {
  const [tokens, setTokens] = useState<TokenInfo[] | undefined>();
  const [label, setLabel] = useState('');
  const [scope, setScope] = useState('full');
  const [days, setDays] = useState('');
  const [once, setOnce] = useState<{ label: string; token: string } | null>(null);
  const [revoke, setRevoke] = useState<TokenInfo | null>(null);
  const { busy, act } = useAct();
  const load = async () => {
    try {
      setTokens((await ctl<{ tokens: TokenInfo[] }>('token_list', {}, { root: true })).tokens);
    } catch (e) {
      toast.err(t('server.failed'), (e as Error).message);
      setTokens([]);
    }
  };
  useEffect(() => { void load(); }, [s.tokens]);
  const create = async () => {
    const r = await act('create', () => ctl<{ token: { token: string; label: string } }>('token_create', { label: label.trim(), scope, days: days ? Number(days) : undefined }, { root: true }));
    if (r) {
      setOnce({ label: r.token.label, token: r.token.token });
      setLabel('');
      void load();
    }
  };
  return (
    <div className="ai-col">
      <div className="ai-card">
        <div className="ai-card-h"><h3>{t('tokens.new')}</h3></div>
        <p>{t('tokens.text')}</p>
        <div className="ai-form-row">
          <Input label={t('tokens.label')} value={label} placeholder={t('tokens.labelPh')} onChange={(e) => setLabel(e.target.value)} />
          <Select label={t('tokens.scope')} value={scope} options={[{ value: 'full', label: t('tokens.scope.full') }, { value: 'readonly', label: t('tokens.scope.ro') }]} onChange={setScope} />
          <Input label={t('tokens.expires')} value={days} mono inputMode="numeric" placeholder={t('tokens.never')} onChange={(e) => setDays(e.target.value.replace(/\D/g, ''))} />
        </div>
        <div className="ai-row"><Button variant="primary" icon="key" disabled={!label.trim()} loading={busy === 'create'} onClick={create}>{t('tokens.create')}</Button></div>
        {once && (
          <div className="ai-token-once" role="status">
            <b>{t('tokens.once', { label: once.label })}</b>
            <Code text={once.token} wrap />
            <span className="ai-note">{t('tokens.onceNote')}</span>
          </div>
        )}
      </div>
      {tokens === undefined ? <Skeleton height={80} style={{ borderRadius: 18 }} /> : tokens.length === 0 ? (
        <EmptyState icon="key" hue="ov" title={t('tokens.none')} text={t('tokens.noneText')} />
      ) : (
        <div className="ai-tablewrap">
          <table className="ai-table">
            <thead><tr><th>{t('tokens.label')}</th><th>{t('tokens.scope')}</th><th className="ai-hide-md">{t('tokens.created')}</th><th>{t('tokens.lastUsed')}</th><th /></tr></thead>
            <tbody>
              {tokens.map((k) => (
                <tr key={k.id}>
                  <td><b>{k.label}</b><div className="ai-muted ai-mono">{k.id}</div></td>
                  <td><span className={`ai-tag ${k.scope === 'full' ? 'ai-tag--acc' : ''}`}>{k.scope === 'full' ? t('tokens.scope.full') : t('tokens.scope.ro')}</span></td>
                  <td className="ai-hide-md ai-muted">{k.created.slice(0, 10)}{k.expires ? ` → ${k.expires.slice(0, 10)}` : ''}</td>
                  <td className="ai-muted">{k.last_used ? `${k.last_used.slice(0, 16).replace('T', ' ')}${k.last_ip ? ' · ' + k.last_ip : ''}` : t('tokens.neverUsed')}</td>
                  <td className="ai-num"><Button size="sm" variant="ghost" icon="trash" onClick={() => setRevoke(k)}>{t('tokens.revoke')}</Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ConfirmDialog open={!!revoke} onClose={() => setRevoke(null)} danger title={t('tokens.revokeTitle', { label: revoke?.label ?? '' })} description={t('tokens.revokeText')} confirmLabel={t('tokens.revoke')}
        onConfirm={async () => { const k = revoke!; setRevoke(null); await act('revoke', () => ctl('token_revoke', { id: k.id }, { root: true }), t('tokens.revoked')); void load(); }} />
    </div>
  );
}

// --- safety ---------------------------------------------------------------------------------------------------------

function Safety({ s }: { s: ServerStatus }) {
  const [p, setP] = useState<Policy>(s.policy);
  const [paths, setPaths] = useState(s.policy.deny_paths.join('\n'));
  const [methods, setMethods] = useState(s.policy.ervisio_deny_methods.join('\n'));
  const { busy, act } = useAct();
  const lines = (v: string) => v.split('\n').map((x) => x.trim()).filter(Boolean);
  const toggleCat = (c: string) => setP({ ...p, disabled_categories: p.disabled_categories.includes(c) ? p.disabled_categories.filter((x) => x !== c) : [...p.disabled_categories, c] });
  return (
    <div className="ai-form" style={{ maxWidth: 760 }}>
      <div className="ai-card">
        <div className="ai-card-h"><h3>{t('safety.mode')}</h3></div>
        <Segmented value={p.mode} options={[{ value: 'full', label: t('safety.full'), icon: 'unlock' }, { value: 'readonly', label: t('safety.readonly'), icon: 'eye' }]} onChange={(v) => setP({ ...p, mode: v as Policy['mode'] })} />
        <p>{p.mode === 'full' ? t('safety.fullText') : t('safety.readonlyText')}</p>
      </div>
      <div className="ai-switchrow"><div><b>{t('safety.guard')}</b><span>{t('safety.guardText')}</span></div><Switch checked={p.guard} onChange={(v) => setP({ ...p, guard: v })} aria-label={t('safety.guard')} /></div>
      <div className="ai-switchrow"><div><b>{t('safety.root')}</b><span>{t('safety.rootText')}</span></div><Switch checked={p.allow_root} onChange={(v) => setP({ ...p, allow_root: v })} aria-label={t('safety.root')} /></div>
      <div className="ai-switchrow"><div><b>{t('safety.audit')}</b><span>{t('safety.auditText')}</span></div><Switch checked={p.audit} onChange={(v) => setP({ ...p, audit: v })} aria-label={t('safety.audit')} /></div>
      <div className="ai-card">
        <div className="ai-card-h"><h3>{t('safety.cats')}</h3></div>
        <p>{t('safety.catsText')}</p>
        <div className="ai-cats">{CATEGORIES.map((c) => <Checkbox key={c} checked={!p.disabled_categories.includes(c)} onChange={() => toggleCat(c)} label={t(`cat.${c}`)} />)}</div>
      </div>
      <Textarea label={t('safety.paths')} hint={t('safety.pathsHint')} rows={3} mono value={paths} onChange={(e) => setPaths(e.target.value)} placeholder={'/etc/ssh\n/root/.gnupg'} />
      <Textarea label={t('safety.methods')} hint={t('safety.methodsHint')} rows={2} mono value={methods} onChange={(e) => setMethods(e.target.value)} placeholder="^users\.\n^config\.set" />
      <div className="ai-row">
        <Button variant="primary" icon="check" loading={busy === 'policy'} onClick={() => act('policy', () => ctl('policy_set', { ...p, deny_paths: lines(paths), ervisio_deny_methods: lines(methods) }, { root: true }), t('safety.saved'))}>{t('safety.save')}</Button>
      </div>
    </div>
  );
}

// --- logs and checks ------------------------------------------------------------------------------------------------

function Logs({ s }: { s: ServerStatus }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setBusy(true);
    try { setText((await ctl<{ text: string }>('logs', { lines: 150 }, { root: true })).text); } catch (e) { toast.err(t('server.failed'), (e as Error).message); } finally { setBusy(false); }
  };
  useEffect(() => { if (s.service.installed) void load(); }, []);
  if (!s.service.installed) return <EmptyState icon="file" hue="ov" title={t('logs.none')} text={t('logs.noneText')} />;
  return (
    <div className="ai-col">
      <div className="ai-row"><Button variant="secondary" icon="refresh" loading={busy} onClick={load}>{t('common.refresh')}</Button></div>
      <div className="ai-logs">{text || t('logs.empty')}</div>
    </div>
  );
}

function Checks() {
  const [checks, setChecks] = useState<Check[] | undefined>();
  const { busy, act } = useAct();
  const load = async () => {
    try { setChecks((await ctl<{ checks: Check[] }>('doctor')).checks); } catch (e) { toast.err(t('server.failed'), (e as Error).message); setChecks([]); }
  };
  useEffect(() => { void load(); }, []);
  if (!checks) return <Skeleton height={160} style={{ borderRadius: 18 }} />;
  return (
    <div className="ai-col">
      <div className="ai-row"><Button variant="secondary" icon="refresh" onClick={() => { setChecks(undefined); void load(); }}>{t('checks.run')}</Button></div>
      <div className="ai-checks">
        {checks.map((c) => (
          <div key={c.name} className="ai-check">
            <span className={`ai-dot ai-dot--${c.level === 'ok' ? 'ok' : c.level === 'warn' ? 'warn' : 'info'}`} />
            <b>{c.name}</b>
            <span>{c.detail}{!c.ok && c.fix && <i>{c.fix}</i>}</span>
            {c.name === 'tmux' && !c.ok && <Button size="sm" variant="secondary" loading={busy === 'tmux'} onClick={() => act('tmux', async () => { await ctl('deps_install', { packages: ['tmux'] }, { root: true }); await load(); })}>{t('server.install')}</Button>}
          </div>
        ))}
      </div>
    </div>
  );
}

// --- page -----------------------------------------------------------------------------------------------------------

export function ServerView() {
  const st = server.use();
  const [tab, setTab] = useState('overview');
  useEffect(() => { void refreshServer(); }, []);
  const s = st.status;
  const active = s?.service.active;
  return (
    <div className="ai-col">
      <PageHeader icon="server" hue="ov" title={t('server.title')} subtitle={t('server.subtitle')}
        actions={s && <><span className={`ai-pill ai-pill--${active ? 'ok' : ''}`}><span className={`ai-dot ai-dot--${active ? 'ok' : 'info'}`} />{active ? t('server.running') : s.engine.installed ? t('server.engineReady') : t('server.notInstalled')}</span>
          <Button variant="ghost" icon="refresh" loading={st.loading} onClick={() => void refreshServer()}>{t('common.refresh')}</Button></>} />
      {st.error && !s && (
        <div className="ai-card ai-card--err"><p>{st.error}</p><div className="ai-row"><Button variant="secondary" icon="refresh" onClick={() => void refreshServer()}>{t('common.retry')}</Button></div></div>
      )}
      {!s && !st.error && <><Skeleton height={140} style={{ borderRadius: 22 }} /><Skeleton height={260} style={{ borderRadius: 18 }} /></>}
      {s && (
        <>
          <Tabs value={tab} onChange={setTab} variant="pill" items={[
            { id: 'overview', label: t('server.tab.overview'), icon: 'server' }, { id: 'tokens', label: t('server.tab.tokens'), icon: 'key', count: s.tokens || undefined },
            { id: 'safety', label: t('server.tab.safety'), icon: 'shield' }, { id: 'logs', label: t('server.tab.logs'), icon: 'file' }, { id: 'checks', label: t('server.tab.checks'), icon: 'check' },
          ]} />
          {tab === 'overview' && <Overview s={s} />}
          {tab === 'tokens' && <Tokens s={s} />}
          {tab === 'safety' && <Safety s={s} />}
          {tab === 'logs' && <Logs s={s} />}
          {tab === 'checks' && <Checks />}
        </>
      )}
    </div>
  );
}

