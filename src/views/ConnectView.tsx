import { useEffect, useMemo, useState } from 'react';
import { Button, Checkbox, Input, Segmented, Select, Skeleton, toast } from '../kit';
import { t } from '../i18n';
import { getSdk } from '../sdk';
import { ctl } from '../engine.ts';
import { go } from '../router';
import { settings, updateSettings, type ConnectDraft } from '../state/settings';
import { refreshServer, server, type Snippet } from '../state/server';
import { Code } from '../ui/Code';
import { PageHeader } from '../ui/PageHeader';
// The connector ships twice: as a file in the plugin folder, and compiled in here so the page can hand it out
// with the settings filled in (the sandboxed frame cannot read its own files as text).
import connector from '../../plugin/connect/ervisio-ai-connect.py?raw';

const PRESET_LINE = 'PRESET = None  # __PRESET__';

function b64(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let bin = '';
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin);
}

/** The connector with this page's choices written into it, so that running it needs no arguments. */
export function connectorWithPreset(preset: Record<string, unknown>): string {
  const line = `PRESET = json.loads(base64.b64decode("${b64(JSON.stringify(preset))}").decode("utf-8"))  # filled in by the Ervisio page`;
  return connector.includes(PRESET_LINE) ? connector.replace(PRESET_LINE, line) : connector;
}

function hostOfConsole(fallback: string): string {
  try {
    return new URL(getSdk().appOrigin).hostname;
  } catch {
    return fallback;
  }
}

export function ConnectView() {
  const st = server.use();
  const cfg = settings.use();
  const d = cfg.connect;
  const set = (p: Partial<ConnectDraft>) => updateSettings((s) => ({ connect: { ...s.connect, ...p } }));
  const [token, setToken] = useState('');
  const [way, setWay] = useState<'direct' | 'tunnel'>('direct');
  const [snippets, setSnippets] = useState<Snippet[] | undefined>();
  const [pick, setPick] = useState('claude-code');
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (!st.status) void refreshServer(); }, []);
  const status = st.status;
  const info = st.info;

  // Fill in what the server can tell us, once
  useEffect(() => {
    if (!status || !info) return;
    const patch: Partial<ConnectDraft> = {};
    if (!d.host) patch.host = hostOfConsole(status.hostname);
    if (d.port === 22 && info.ssh.port !== 22) patch.port = info.ssh.port;
    if (Object.keys(patch).length) set(patch);
  }, [status?.hostname, info?.ssh.port]);

  const cfgS = status?.config;
  const scheme = cfgS?.tls === 'none' ? 'http' : 'https';
  const url = useMemo(() => {
    if (!cfgS) return '';
    const host = way === 'tunnel' ? '127.0.0.1' : d.host || 'your-server';
    return `${scheme}://${host}:${cfgS.port}/mcp`;
  }, [cfgS?.port, scheme, way, d.host]);
  const name = d.name || 'ervisio-' + (d.host || 'server').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const httpReady = !!status?.service.active;

  const params = d.mode === 'ssh'
    ? { mode: 'ssh', name, host: d.host, user: d.user, port: d.port, identity: d.identity }
    : { mode: 'http', name, url, token: token || 'eai_PASTE_YOUR_TOKEN', insecure: cfgS?.tls === 'self-signed' };

  // The settings for each tool come from the engine, which uses the connector's own code
  const key = JSON.stringify(params);
  useEffect(() => {
    if (!status) return;
    let live = true;
    ctl<{ clients: Snippet[] }>('connect_snippets', params).then((r) => live && setSnippets(r.clients), () => live && setSnippets([]));
    return () => { live = false; };
  }, [key, !!status]);

  const createToken = async () => {
    setBusy(true);
    try {
      const r = await ctl<{ token: { token: string } }>('token_create', { label: name }, { root: true });
      setToken(r.token.token);
      toast.ok(t('connect.tokenMade'));
      void refreshServer();
    } catch (e) {
      toast.err(t('server.failed'), (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const download = async () => {
    const preset = d.mode === 'ssh'
      ? { mode: 'ssh', name, host: d.host, user: d.user, port: d.port, identity: d.identity }
      : { mode: 'http', name, url, ...(d.embedToken && token ? { token } : {}), insecure: cfgS?.tls === 'self-signed' };
    await getSdk().saveFile('ervisio-ai-connect.py', connectorWithPreset(preset), 'text/x-python');
  };

  const snippet = snippets?.find((s) => s.id === pick);
  const runCmd = 'python ervisio-ai-connect.py';
  const httpMissing = d.mode === 'http' && (!httpReady || !token);

  return (
    <div className="ai-col">
      <PageHeader icon="ai-plug" hue="ov" title={t('connect.title')} subtitle={t('connect.subtitle')} />
      {!status && <Skeleton height={240} style={{ borderRadius: 18 }} />}
      {status && !status.engine.installed && (
        <div className="ai-card ai-card--warn"><p>{t('connect.needEngine')}</p><div className="ai-row"><Button variant="primary" onClick={() => go('server')}>{t('connect.goServer')}</Button></div></div>
      )}
      {status && (
        <div className="ai-steps">
          <section className="ai-step">
            <h3>{t('connect.step1')}</h3>
            <Segmented value={d.mode} onChange={(v) => set({ mode: v as 'ssh' | 'http' })} options={[{ value: 'ssh', label: t('connect.ssh'), icon: 'lock' }, { value: 'http', label: t('connect.http'), icon: 'globe' }]} />
            {d.mode === 'ssh' ? (
              <>
                <p>{t('connect.ssh.text')}</p>
                <div className="ai-form-row">
                  <Input label={t('connect.host')} value={d.host} mono onChange={(e) => set({ host: e.target.value.trim() })} hint={t('connect.hostHint')} />
                  <Input label={t('connect.user')} value={d.user} mono onChange={(e) => set({ user: e.target.value.trim() })} hint={info ? t('connect.userHint', { users: info.users.filter((u) => u.ssh_keys).map((u) => u.name).join(', ') || '—' }) : ''} />
                  <Input label={t('connect.port')} value={String(d.port)} mono inputMode="numeric" onChange={(e) => set({ port: Number(e.target.value.replace(/\D/g, '')) || 22 })} />
                </div>
                <Input label={t('connect.identity')} value={d.identity} mono placeholder="~/.ssh/id_ed25519" hint={t('connect.identityHint')} onChange={(e) => set({ identity: e.target.value.trim() })} />
                {info && !info.ssh.running && <div className="ai-notice ai-notice--warn"><span>{t('connect.sshDown', { port: info.ssh.port })}</span></div>}
                {info && info.ssh.permit_root_login === 'no' && d.user === 'root' && <div className="ai-notice ai-notice--warn"><span>{t('connect.rootNo')}</span></div>}
              </>
            ) : (
              <>
                <p>{t('connect.http.text')}</p>
                {!httpReady && <div className="ai-notice ai-notice--warn"><span>{t('connect.httpOff')} <Button size="sm" variant="secondary" onClick={() => go('server')}>{t('connect.goServer')}</Button></span></div>}
                {httpReady && cfgS?.bind === '127.0.0.1' && (
                  <Select label={t('connect.reach')} value={way} onChange={(v) => setWay(v as 'direct' | 'tunnel')}
                    options={[{ value: 'direct', label: t('connect.reach.direct') }, { value: 'tunnel', label: t('connect.reach.tunnel') }]} />
                )}
                {way === 'tunnel' && cfgS && <Code title={t('connect.tunnel')} text={`ssh -N -L ${cfgS.port}:127.0.0.1:${cfgS.port} ${d.user || 'root'}@${d.host || 'your-server'}`} />}
                <Input label="URL" value={url} mono readOnly />
                <div className="ai-form-row">
                  <Input label={t('connect.token')} value={token} mono type="password" autoComplete="off" placeholder="eai_…" onChange={(e) => setToken(e.target.value.trim())} hint={t('connect.tokenHint')} />
                  <div style={{ display: 'flex', alignItems: 'flex-end' }}><Button variant="secondary" icon="key" loading={busy} onClick={createToken}>{t('connect.newToken')}</Button></div>
                </div>
                {cfgS?.tls === 'self-signed' && <div className="ai-note">{t('connect.selfSigned')}</div>}
              </>
            )}
            <Input label={t('connect.name')} value={d.name} placeholder={name} mono hint={t('connect.nameHint')} onChange={(e) => set({ name: e.target.value.replace(/[^A-Za-z0-9_-]/g, '-') })} />
          </section>

          <section className="ai-step">
            <h3>{t('connect.step2')}</h3>
            <p>{t('connect.step2.text')}</p>
            <div className="ai-row">
              <Button variant="primary" icon="download" disabled={httpMissing || !d.host && d.mode === 'ssh'} onClick={download}>{t('connect.download')}</Button>
              {d.mode === 'http' && token && <Checkbox checked={d.embedToken} onChange={(v) => set({ embedToken: v })} label={t('connect.embed')} />}
            </div>
            <Code title={t('connect.runIt')} text={`${runCmd}\n${runCmd} --dry-run   # ${t('connect.dry')}\n${runCmd} --list      # ${t('connect.list')}`} />
            <p className="ai-note">{t('connect.step2.note')}</p>
            {d.mode === 'http' && d.embedToken && token && <div className="ai-notice ai-notice--warn"><span>{t('connect.embedWarn')}</span></div>}
          </section>

          <section className="ai-step">
            <h3>{t('connect.step3')}</h3>
            <p>{t('connect.step3.text')}</p>
            {!snippets ? <Skeleton height={120} style={{ borderRadius: 14 }} /> : (
              <>
                <div className="ai-clients">
                  {snippets.map((s) => <button key={s.id} type="button" className="ai-client" aria-pressed={s.id === pick} onClick={() => setPick(s.id)}>{s.label}</button>)}
                </div>
                {snippet && (
                  <div className="ai-col" style={{ gap: 8 }}>
                    {snippet.unsupported && <div className="ai-notice ai-notice--warn"><span>{snippet.unsupported}</span></div>}
                    <div className="ai-note">{t('connect.where')} <b>{snippet.where}</b></div>
                    <Code text={snippet.body} title={snippet.lang} secret={d.mode === 'http' && !!token} />
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
