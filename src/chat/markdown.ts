/**
 * A small Markdown parser for chat replies: headings, paragraphs, lists (one nested level), tables, quotes, rules,
 * fenced code, and inline code, bold, italic, strikethrough and links. It builds a tree; the view turns the tree into
 * elements, so no HTML string is ever built from model output. An unclosed code fence runs to the end of the text, which
 * is what a half-streamed reply looks like.
 */

export type Inline =
  | { t: 'text'; v: string }
  | { t: 'code'; v: string }
  | { t: 'strong' | 'em' | 'del'; c: Inline[] }
  | { t: 'link'; href: string; c: Inline[] }
  | { t: 'br' };

export interface ListItem {
  c: Inline[];
  children?: ListNode;
}

export interface ListNode {
  t: 'list';
  ordered: boolean;
  start: number;
  items: ListItem[];
}

export type Node =
  | { t: 'p'; c: Inline[] }
  | { t: 'h'; level: number; c: Inline[] }
  | { t: 'code'; lang: string; v: string; open: boolean }
  | ListNode
  | { t: 'quote'; c: Node[] }
  | { t: 'table'; head: Inline[][]; align: ('left' | 'center' | 'right' | '')[]; rows: Inline[][][] }
  | { t: 'hr' };

const SAFE_URL = /^(https?:\/\/|mailto:)/i;

export function safeHref(url: string): string | null {
  const u = url.trim();
  return SAFE_URL.test(u) ? u : null;
}

// --- inline ----------------------------------------------------------------------------------------------------

/** How far a closing mark may be from its opening one. Bounds the work on text with many unmatched marks. */
const WINDOW = 3000;

/** Index of `needle` in src at or after `from`, looking at most `max` characters ahead; -1 when it is not there. */
function find(src: string, needle: string, from: number, max = WINDOW): number {
  const i = src.slice(from, from + max + needle.length).indexOf(needle);
  return i < 0 ? -1 : from + i;
}

export function parseInline(src: string): Inline[] {
  const out: Inline[] = [];
  let text = '';
  const pushText = () => {
    if (text) out.push({ t: 'text', v: text });
    text = '';
  };
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (c === '\\' && i + 1 < src.length && /[\\`*_{}[\]()#+\-.!~|>]/.test(src[i + 1])) {
      text += src[i + 1];
      i += 2;
      continue;
    }
    if (c === '`') {
      let n = 1;
      while (src[i + n] === '`') n++;
      const fence = '`'.repeat(n);
      const end = find(src, fence, i + n);
      if (end > 0) {
        pushText();
        out.push({ t: 'code', v: src.slice(i + n, end).replace(/^ (.*) $/s, '$1') });
        i = end + n;
        continue;
      }
    }
    if ((c === '*' || c === '_' || c === '~') && src[i + 1] === c) {
      const end = find(src, c + c, i + 2);
      if (end > i + 2 && !/^\s/.test(src[i + 2])) {
        pushText();
        out.push({ t: c === '~' ? 'del' : 'strong', c: parseInline(src.slice(i + 2, end)) });
        i = end + 2;
        continue;
      }
    }
    if ((c === '*' || c === '_') && src[i + 1] !== c && src[i + 1] !== ' ' && src[i + 1] !== undefined) {
      const wordBefore = c === '_' && i > 0 && /\w/.test(src[i - 1]);
      let end = i + 1;
      const limit = Math.min(src.length, i + 1 + 1000);
      while ((end = find(src, c, end, limit - end)) > 0 && (src[end - 1] === ' ' || src[end + 1] === c || (c === '_' && /\w/.test(src[end + 1] ?? '')))) end++;
      if (!wordBefore && end > i + 1) {
        pushText();
        out.push({ t: 'em', c: parseInline(src.slice(i + 1, end)) });
        i = end + 1;
        continue;
      }
    }
    if (c === '[') {
      const close = find(src, '](', i);
      if (close > i) {
        const end = find(src, ')', close + 2, 2000);
        if (end > close) {
          const href = safeHref(src.slice(close + 2, end).split(/\s+/)[0]);
          if (href) {
            pushText();
            out.push({ t: 'link', href, c: parseInline(src.slice(i + 1, close)) });
            i = end + 1;
            continue;
          }
        }
      }
    }
    if (c === 'h' && /^https?:\/\//.test(src.slice(i, i + 8)) && (i === 0 || /[\s(]/.test(src[i - 1]))) {
      const m = /^https?:\/\/[^\s<>)\]]+[^\s<>)\].,;:!?'"]/.exec(src.slice(i));
      if (m) {
        pushText();
        out.push({ t: 'link', href: m[0], c: [{ t: 'text', v: m[0] }] });
        i += m[0].length;
        continue;
      }
    }
    if (c === '\n') {
      pushText();
      out.push({ t: 'br' });
      i++;
      continue;
    }
    text += c;
    i++;
  }
  pushText();
  return out;
}

// --- blocks ----------------------------------------------------------------------------------------------------

const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const BULLET = /^(\s*)([-*+])\s+(.*)$/;
const NUMBERED = /^(\s*)(\d{1,9})[.)]\s+(.*)$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

function splitRow(line: string): string[] {
  let l = line.trim();
  if (l.startsWith('|')) l = l.slice(1);
  if (l.endsWith('|') && !l.endsWith('\\|')) l = l.slice(0, -1);
  const cells: string[] = [];
  let cur = '';
  for (let i = 0; i < l.length; i++) {
    if (l[i] === '\\' && l[i + 1] === '|') {
      cur += '|';
      i++;
    } else if (l[i] === '|') {
      cells.push(cur.trim());
      cur = '';
    } else cur += l[i];
  }
  cells.push(cur.trim());
  return cells;
}

export function parseMarkdown(src: string): Node[] {
  return parseBlocks(src.replace(/\r\n?/g, '\n').split('\n'));
}

function parseBlocks(lines: string[]): Node[] {
  const out: Node[] = [];
  let i = 0;
  const startsBlock = (l: string): boolean => FENCE.test(l) || HEADING.test(l) || HR.test(l) || QUOTE.test(l) || BULLET.test(l) || NUMBERED.test(l);
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === '') {
      i++;
      continue;
    }
    const f = FENCE.exec(line);
    if (f) {
      const mark = f[1][0];
      const len = f[1].length;
      const body: string[] = [];
      i++;
      let open = true;
      while (i < lines.length) {
        const m = new RegExp(`^\\s{0,3}\\${mark}{${len},}\\s*$`).exec(lines[i]);
        if (m) {
          open = false;
          i++;
          break;
        }
        body.push(lines[i]);
        i++;
      }
      out.push({ t: 'code', lang: f[2], v: body.join('\n'), open });
      continue;
    }
    const h = HEADING.exec(line);
    if (h) {
      out.push({ t: 'h', level: h[1].length, c: parseInline(h[2]) });
      i++;
      continue;
    }
    if (HR.test(line)) {
      out.push({ t: 'hr' });
      i++;
      continue;
    }
    if (QUOTE.test(line)) {
      const body: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i])) {
        body.push(QUOTE.exec(lines[i])![1]);
        i++;
      }
      out.push({ t: 'quote', c: parseBlocks(body) });
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      const head = splitRow(line);
      const align = splitRow(lines[i + 1]).map((c) => (c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : c.startsWith(':') ? 'left' : '') as 'left' | 'center' | 'right' | '');
      i += 2;
      const rows: Inline[][][] = [];
      while (i < lines.length && lines[i].trim() !== '' && lines[i].includes('|')) {
        const cells = splitRow(lines[i]);
        rows.push(head.map((_, k) => parseInline(cells[k] ?? '')));
        i++;
      }
      out.push({ t: 'table', head: head.map((c) => parseInline(c)), align, rows });
      continue;
    }
    const b = BULLET.exec(line) ?? NUMBERED.exec(line);
    if (b) {
      const list = parseList(lines, i);
      out.push(list.node);
      i = list.next;
      continue;
    }
    const para: string[] = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== '' && !startsBlock(lines[i])) {
      para.push(lines[i]);
      i++;
    }
    out.push({ t: 'p', c: parseInline(para.join('\n')) });
  }
  return out;
}

function parseList(lines: string[], from: number): { node: ListNode; next: number } {
  const first = BULLET.exec(lines[from]) ?? NUMBERED.exec(lines[from])!;
  const indent = first[1].length;
  const ordered = NUMBERED.test(lines[from]) && !BULLET.test(lines[from]);
  const node: ListNode = { t: 'list', ordered, start: ordered ? Number(first[2]) : 1, items: [] };
  let i = from;
  while (i < lines.length) {
    const line = lines[i];
    const m = (ordered ? NUMBERED : BULLET).exec(line);
    if (m && m[1].length === indent) {
      node.items.push({ c: parseInline(m[3]) });
      i++;
      continue;
    }
    const nested = BULLET.exec(line) ?? NUMBERED.exec(line);
    if (nested && nested[1].length > indent && node.items.length) {
      const sub = parseList(lines, i);
      node.items[node.items.length - 1].children = sub.node;
      i = sub.next;
      continue;
    }
    // a continuation line of the last item (indented, not a new block)
    if (line.trim() !== '' && /^\s+\S/.test(line) && node.items.length && !nested) {
      const last = node.items[node.items.length - 1];
      last.c = [...last.c, { t: 'br' }, ...parseInline(line.trim())];
      i++;
      continue;
    }
    break;
  }
  return { node, next: i };
}

/** The plain text of an inline run (for copy buttons and tests). */
export function inlineText(c: Inline[]): string {
  return c.map((x) => (x.t === 'text' || x.t === 'code' ? x.v : x.t === 'br' ? '\n' : x.t === 'strong' || x.t === 'em' || x.t === 'del' || x.t === 'link' ? inlineText(x.c) : '')).join('');
}
