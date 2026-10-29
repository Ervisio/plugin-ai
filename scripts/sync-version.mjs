// Writes the package version into the engine and the connector; run by
// `npm version` (the "version" script), which the release workflow uses.
import { readFileSync, writeFileSync } from 'node:fs';

const v = process.env.npm_package_version || JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version;
const edit = (p, re, line) => {
  const u = new URL(`../${p}`, import.meta.url);
  const s = readFileSync(u, 'utf8');
  if (!re.test(s)) throw new Error(`${p}: no version line`);
  writeFileSync(u, s.replace(re, line));
};
edit('plugin/engine/ervisio_ai/__init__.py', /__version__\s*=\s*"[^"]+"/, `__version__ = "${v}"`);
edit('plugin/connect/ervisio-ai-connect.py', /^VERSION\s*=\s*"[^"]+"/m, `VERSION = "${v}"`);
console.log(`version ${v} written to the engine and the connector`);
