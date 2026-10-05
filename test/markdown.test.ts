import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inlineText, parseInline, parseMarkdown, safeHref, type Node } from '../src/chat/markdown.ts';

const types = (n: Node[]) => n.map((x) => x.t);

test('paragraphs, headings and rules', () => {
  const n = parseMarkdown('# Title\n\nFirst line\nsecond line\n\n---\n\n### Small ###\nlast');
  assert.deepEqual(types(n), ['h', 'p', 'hr', 'h', 'p']);
  assert.equal((n[0] as { level: number }).level, 1);
  assert.equal((n[3] as { level: number }).level, 3);
  assert.equal(inlineText((n[1] as { c: any }).c), 'First line\nsecond line');
  assert.equal(inlineText((n[3] as { c: any }).c), 'Small');
});

test('fenced code keeps its text and language, and an unclosed fence runs to the end', () => {
  let n = parseMarkdown('Run:\n\n```bash\nls -la\n# not a heading\n```\nDone');
  assert.deepEqual(types(n), ['p', 'code', 'p']);
  assert.deepEqual(n[1], { t: 'code', lang: 'bash', v: 'ls -la\n# not a heading', open: false });
  n = parseMarkdown('```py\nprint(1)\nprint(');
  assert.deepEqual(n, [{ t: 'code', lang: 'py', v: 'print(1)\nprint(', open: true }]);
  n = parseMarkdown('````\n```\ninner\n```\n````');
  assert.equal((n[0] as { v: string }).v, '```\ninner\n```');
  n = parseMarkdown('~~~\nx\n~~~');
  assert.equal((n[0] as { v: string }).v, 'x');
});

test('inline: code, bold, italic, strikethrough, escapes', () => {
  const c = parseInline('use `rm -rf` **now** and *then* ~~never~~ \\*literal\\*');
  assert.deepEqual(c.map((x) => x.t), ['text', 'code', 'text', 'strong', 'text', 'em', 'text', 'del', 'text']);
  assert.equal(inlineText(c), 'use rm -rf now and then never *literal*');
  assert.deepEqual(parseInline('``a ` b``'), [{ t: 'code', v: 'a ` b' }]);
});

test('inline: snake_case words and lone asterisks are not emphasis', () => {
  assert.equal(inlineText(parseInline('my_var_name and 2 * 3 * 4')), 'my_var_name and 2 * 3 * 4');
  assert.deepEqual(parseInline('a_b_c').map((x) => x.t), ['text']);
});

test('inline: links, only safe schemes; bare urls become links', () => {
  let c = parseInline('see [docs](https://example.com/a?b=1) now');
  assert.deepEqual(c[1], { t: 'link', href: 'https://example.com/a?b=1', c: [{ t: 'text', v: 'docs' }] });
  c = parseInline('[click](javascript:alert(1))');
  assert.equal(c.some((x) => x.t === 'link'), false);
  c = parseInline('open https://example.com/path, then');
  assert.equal((c[1] as { href: string }).href, 'https://example.com/path');
  assert.equal(safeHref('data:text/html,x'), null);
  assert.equal(safeHref('mailto:a@b.c'), 'mailto:a@b.c');
});

test('lists: bullets, numbers, nesting, continuation lines', () => {
  const n = parseMarkdown('- one\n- two\n  - inner a\n  - inner b\n- three\n\n3. third\n4. fourth\n   continues');
  assert.deepEqual(types(n), ['list', 'list']);
  const l = n[0] as any;
  assert.equal(l.ordered, false);
  assert.equal(l.items.length, 3);
  assert.equal(l.items[1].children.items.length, 2);
  const o = n[1] as any;
  assert.equal(o.ordered, true);
  assert.equal(o.start, 3);
  assert.equal(inlineText(o.items[1].c), 'fourth\ncontinues');
});

test('a list interrupts a paragraph, a table does too', () => {
  const n = parseMarkdown('Steps:\n- a\n- b\nafter');
  assert.deepEqual(types(n), ['p', 'list', 'p']);
});

test('tables with alignment and escaped pipes', () => {
  const n = parseMarkdown('| Name | Size |\n|:-----|-----:|\n| a \\| b | 10 |\n| c |\n\nafter');
  assert.deepEqual(types(n), ['table', 'p']);
  const t = n[0] as any;
  assert.deepEqual(t.align, ['left', 'right']);
  assert.equal(inlineText(t.rows[0][0]), 'a | b');
  assert.equal(inlineText(t.rows[1][1]), ''); // a short row is padded
  assert.equal(inlineText(t.head[1]), 'Size');
});

test('block quotes nest other blocks', () => {
  const n = parseMarkdown('> **Note**\n> - a\n> - b');
  assert.deepEqual(types(n), ['quote']);
  assert.deepEqual(types((n[0] as any).c), ['p', 'list']);
});

test('html is just text', () => {
  const c = parseInline('<img src=x onerror=alert(1)> <b>x</b>');
  assert.equal(inlineText(c), '<img src=x onerror=alert(1)> <b>x</b>');
  assert.deepEqual(c.map((x) => x.t), ['text']);
});

test('windows line ends, empty input, and a pathological input finish quickly', () => {
  assert.deepEqual(types(parseMarkdown('a\r\n\r\nb')), ['p', 'p']);
  assert.deepEqual(parseMarkdown(''), []);
  const t0 = Date.now();
  parseMarkdown('*'.repeat(20000) + '\n' + '['.repeat(5000) + '\n' + '`'.repeat(3000) + '\n' + '_a'.repeat(5000));
  assert.ok(Date.now() - t0 < 2000, 'took ' + (Date.now() - t0) + ' ms');
});
