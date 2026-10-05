// Checks that the version is the same everywhere it is written: plugin/manifest.json, package.json, the engine
// (plugin/engine/ervisio_ai/__init__.py) and the connector (plugin/connect/ervisio-ai-connect.py), and that
// CHANGELOG.md has a section for it. Run by CI; `npm run check` runs it locally.
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');
const found = {
  'plugin/manifest.json': JSON.parse(read('plugin/manifest.json')).version,
  'package.json': JSON.parse(read('package.json')).version,
  'engine __version__': read('plugin/engine/ervisio_ai/__init__.py').match(/__version__\s*=\s*"([^"]+)"/)?.[1],
  'connector VERSION': read('plugin/connect/ervisio-ai-connect.py').match(/^VERSION\s*=\s*"([^"]+)"/m)?.[1],
};
const want = found['plugin/manifest.json'];
let bad = false;
for (const [where, v] of Object.entries(found)) {
  if (v !== want) {
    console.error(`${where} says ${v ?? '(not found)'}, the manifest says ${want}`);
    bad = true;
  }
}
if (!new RegExp(`^## +\\[?v?${want.replace(/\./g, '\\.')}\\b`, 'm').test(read('CHANGELOG.md'))) {
  console.error(`CHANGELOG.md has no "## ${want}" section`);
  bad = true;
}
if (bad) process.exit(1);
console.log(`release ok: ${want}`);
