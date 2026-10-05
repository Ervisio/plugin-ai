// Every key the code asks for must exist in src/i18n/strings.ts, in both languages, and no string may be left empty.
// Keys built at run time (t(`mode.${m}`)) are checked against the prefixes below.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const walk = (d) => readdirSync(d).flatMap((n) => (statSync(join(d, n)).isDirectory() ? walk(join(d, n)) : [join(d, n)]));
const files = walk('src').filter((f) => /\.(ts|tsx)$/.test(f) && !f.includes('strings.ts'));
const source = files.map((f) => readFileSync(f, 'utf8')).join('\n');
const strings = readFileSync('src/i18n/strings.ts', 'utf8');
const have = new Set([...strings.matchAll(/^\s*'([^']+)':\s*\[/gm)].map((m) => m[1]));

const missing = [];
for (const m of source.matchAll(/\bt\((['`])([^'`]+)\1/g)) {
  const key = m[2];
  if (key.includes('${')) continue;
  if (!have.has(key)) missing.push(key);
}
// dynamic families: [prefix, values]
const families = [
  ['mode.', ['ask', 'auto', 'auto-read'], ['', '.short', '.text']],
  ['cat.', ['shell', 'terminal', 'files', 'system', 'services', 'packages', 'network', 'docker', 'ervisio', 'meta'], ['']],
  ['nav.', ['chat', 'server', 'connect', 'activity', 'settings'], ['']],
  ['chat.cmd.', ['new', 'clear', 'model', 'mode', 'root', 'tools', 'export', 'help', 'shell'], ['']],
];
for (const [p, vals, sufs] of families) for (const v of vals) for (const s of sufs) if (!have.has(p + v + s)) missing.push(p + v + s);
for (const k of ['idea.disk', 'idea.failing', 'idea.security', 'idea.update', 'idea.caddy', 'idea.explain']) if (!have.has(k)) missing.push(k);

const empty = [...strings.matchAll(/^\s*'([^']+)':\s*\[\s*(['"`])(.*?)\2\s*,\s*(['"`])(.*?)\4\s*\]/gms)].filter((m) => !m[3].trim() || !m[5].trim()).map((m) => m[1]);
const unused = [...have].filter((k) => !source.includes(`'${k}'`) && !source.includes(`\`${k}\``) && !/^(mode|cat|nav|chat\.cmd|idea)\./.test(k) && !source.includes(k));

let bad = false;
if (missing.length) { console.error('Missing strings:\n  ' + [...new Set(missing)].join('\n  ')); bad = true; }
if (empty.length) { console.error('Empty translations:\n  ' + empty.join('\n  ')); bad = true; }
if (unused.length) console.warn('Unused strings (remove them):\n  ' + unused.join('\n  '));
if (bad) process.exit(1);
console.log(`i18n ok: ${have.size} strings, English and Italian.`);
