import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Icon } from '../../kit';
import { t } from '../../i18n';
import { getSdk } from '../../sdk';
import { COMMANDS, parseInput, suggest } from '../../chat/commands.ts';
import { chat } from '../../chat/controller.ts';
import { settings, updateSettings } from '../../state/settings';
import { fetchModels } from '../../state/models';
import { activeProvider } from '../../state/settings';
import { toMarkdown } from './export';

const history: string[] = [];

/** The input line: Enter sends, Shift+Enter adds a line, `/` opens commands, `!` runs a shell command, Esc stops. */
export function Composer({ busy, tokens }: { busy: boolean; tokens: string }) {
  const [text, setText] = useState('');
  const [sel, setSel] = useState(0);
  const [hist, setHist] = useState(-1);
  const ref = useRef<HTMLTextAreaElement>(null);
  const sug = suggest(text);
  const shell = text.startsWith('!');

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 220) + 'px';
  }, [text]);
  useEffect(() => ref.current?.focus(), []);
  useEffect(() => setSel(0), [text]);

  const say = (msg: string, tone: 'info' | 'warn' | 'err' = 'info') =>
    chat.state.set((s) => ({ ...s, items: [...s.items, { kind: 'notice', id: `n${Date.now()}${Math.random()}`, tone, text: msg }] }));

  const runSlash = async (name: string, args: string) => {
    const s = settings.get();
    switch (name) {
      case 'new': case 'clear': chat.startNew(); break;
      case 'mode':
        if (['ask', 'auto-read', 'auto'].includes(args)) { updateSettings({ approval: args as 'ask' }); say(t('cmd.modeSet', { mode: t(`mode.${args}`) })); }
        else say(t('cmd.modeUsage', { mode: s.approval }), 'warn');
        break;
      case 'root':
        if (args === 'on' || args === 'off') { chat.setRoot(args === 'on'); say(args === 'on' ? t('cmd.rootOn') : t('cmd.rootOff'), args === 'on' ? 'warn' : 'info'); }
        else say(t('cmd.rootUsage', { state: s.root ? 'on' : 'off' }), 'warn');
        break;
      case 'model': {
        const p = activeProvider(s);
        if (!p) break;
        if (args) { updateSettings({ models: { ...s.models, [p.id]: args } }); say(t('cmd.modelSet', { model: args })); break; }
        try {
          const list = await fetchModels(p);
          say(t('cmd.modelList', { current: s.models[p.id] ?? '', list: list.slice(0, 40).join(', ') }));
        } catch (e) { say((e as Error).message, 'err'); }
        break;
      }
      case 'tools': {
        const tools = chat.state.get().tools;
        say(t('cmd.tools', { n: tools.length, list: tools.map((x) => x.name).join(', ') }));
        break;
      }
      case 'export': {
        const c = chat.state.get().chat;
        void getSdk().saveFile(`${(c.title || 'ervisio-ai-chat').replace(/[^\w-]+/g, '-').slice(0, 40)}.md`, toMarkdown(c), 'text/markdown');
        break;
      }
      case 'help':
        say(COMMANDS.map((c) => `/${c.name} ${c.usage}`.trim() + ': ' + t(`chat.${c.help}`)).join('\n') + '\n!command: ' + t('chat.cmd.shell'));
        break;
    }
  };

  const submit = () => {
    const p = parseInput(text);
    if (p.kind === 'empty') return;
    if (busy) return;
    history.unshift(text);
    setHist(-1);
    setText('');
    if (p.kind === 'slash') {
      if (!p.known) say(t('cmd.unknown', { name: p.name }), 'warn');
      else void runSlash(p.name, p.args);
    } else if (p.kind === 'shell') void chat.shell(p.command);
    else void chat.send(p.text);
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing) return;
    if (sug.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setSel((sel + 1) % sug.length); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); setSel((sel + sug.length - 1) % sug.length); return; }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && text !== `/${sug[sel].name}`)) { e.preventDefault(); setText(`/${sug[sel].name} `); return; }
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); return; }
    if (e.key === 'Escape' && busy) { e.preventDefault(); chat.stop(); return; }
    if (e.key === 'l' && e.ctrlKey) { e.preventDefault(); chat.startNew(); return; }
    const el = e.currentTarget;
    if (e.key === 'ArrowUp' && el.selectionStart === 0 && el.selectionEnd === 0 && history.length) {
      e.preventDefault();
      const n = Math.min(hist + 1, history.length - 1);
      setHist(n);
      setText(history[n]);
    } else if (e.key === 'ArrowDown' && hist >= 0 && el.selectionStart === el.value.length) {
      e.preventDefault();
      const n = hist - 1;
      setHist(n);
      setText(n < 0 ? '' : history[n]);
    }
  };

  return (
    <div className="ai-composer">
      {sug.length > 0 && (
        <div className="ai-slash" role="listbox">
          {sug.map((c, i) => (
            <button key={c.name} type="button" role="option" aria-selected={i === sel} onMouseDown={(e) => { e.preventDefault(); setText(`/${c.name} `); ref.current?.focus(); }}>
              <b>/{c.name}</b><span>{c.usage && <code>{c.usage} </code>}{t(`chat.${c.help}`)}</span>
            </button>
          ))}
        </div>
      )}
      <div className="ai-composer-in">
        <span className={`ai-composer-g${shell ? ' ai-composer-g--shell' : ''}`}>{shell ? '$' : '›'}</span>
        <textarea
          ref={ref} value={shell ? text.slice(1) : text} rows={1} spellCheck={false} aria-label={t('chat.input')}
          placeholder={busy ? t('chat.busyHint') : t('chat.placeholder')}
          onChange={(e) => setText(shell ? '!' + e.target.value : e.target.value)} onKeyDown={onKey}
        />
        {busy
          ? <button type="button" className="ai-send ai-send--stop" onClick={() => chat.stop()} aria-label={t('chat.stop')} title={t('chat.stop')}><Icon name="stop" /></button>
          : <button type="button" className="ai-send" disabled={!text.trim()} onClick={submit} aria-label={t('chat.send')} title={t('chat.send')}><Icon name="ai-send" /></button>}
      </div>
      <div className="ai-composer-ft">
        <span><kbd>Enter</kbd> {t('chat.hint.send')}</span>
        <span><kbd>Shift</kbd>+<kbd>Enter</kbd> {t('chat.hint.line')}</span>
        <span><kbd>/</kbd> {t('chat.hint.commands')}</span>
        <span><kbd>!</kbd> {t('chat.hint.shell')}</span>
        {busy && <span><kbd>Esc</kbd> {t('chat.hint.stop')}</span>}
        <span className="ai-grow" />
        <span>{tokens}</span>
      </div>
    </div>
  );
}
