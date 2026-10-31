import { type ReactNode } from 'react';
import { lazyMemo } from '../ui/memo';
import { getSdk } from '../sdk';
import { parseMarkdown, type Inline, type ListNode, type Node } from './markdown.ts';
import { copyText } from '../ui/copy';
import { IconButton } from '../kit';
import { t } from '../i18n';

function inline(c: Inline[], k = ''): ReactNode[] {
  return c.map((x, i) => {
    const key = `${k}${i}`;
    switch (x.t) {
      case 'text': return x.v;
      case 'code': return <code key={key}>{x.v}</code>;
      case 'strong': return <strong key={key}>{inline(x.c, key)}</strong>;
      case 'em': return <em key={key}>{inline(x.c, key)}</em>;
      case 'del': return <del key={key}>{inline(x.c, key)}</del>;
      case 'br': return <br key={key} />;
      case 'link': return <a key={key} href={x.href} onClick={(e) => { e.preventDefault(); getSdk().openExternal(x.href); }}>{inline(x.c, key)}</a>;
    }
  });
}

function list(n: ListNode, k: string): ReactNode {
  const items = n.items.map((it, i) => <li key={i}>{inline(it.c, `${k}${i}`)}{it.children && list(it.children, `${k}${i}.`)}</li>);
  return n.ordered ? <ol start={n.start}>{items}</ol> : <ul>{items}</ul>;
}

function CodeBlock({ lang, v }: { lang: string; v: string }) {
  return (
    <div className="ai-md-code">
      <div className="ai-md-code-h"><span>{lang}</span><IconButton icon="copy" size="sm" label={t('common.copy')} onClick={() => void copyText(v)} /></div>
      <pre>{v}</pre>
    </div>
  );
}

function block(n: Node, i: number): ReactNode {
  switch (n.t) {
    case 'p': return <p key={i}>{inline(n.c, `p${i}`)}</p>;
    case 'h': { const H = `h${n.level}` as 'h1'; return <H key={i}>{inline(n.c, `h${i}`)}</H>; }
    case 'code': return <CodeBlock key={i} lang={n.lang} v={n.v} />;
    case 'list': return <div key={i}>{list(n, `l${i}`)}</div>;
    case 'quote': return <blockquote key={i}>{n.c.map(block)}</blockquote>;
    case 'hr': return <hr key={i} />;
    case 'table':
      return (
        <table key={i}>
          <thead><tr>{n.head.map((c, k) => <th key={k} style={{ textAlign: n.align[k] || undefined }}>{inline(c, `th${i}${k}`)}</th>)}</tr></thead>
          <tbody>{n.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k} style={{ textAlign: n.align[k] || undefined }}>{inline(c, `td${i}${j}${k}`)}</td>)}</tr>)}</tbody>
        </table>
      );
  }
}

/** Model output as elements. Nothing here ever sets HTML. */
export const Markdown = lazyMemo(function Markdown({ text }: { text: string }) {
  return <div className="ai-md">{parseMarkdown(text).map(block)}</div>;
});
