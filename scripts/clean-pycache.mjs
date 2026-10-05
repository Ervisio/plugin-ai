// Python bytecode must never ship in the plugin: the signature covers every file of the folder, and a __pycache__
// written by a test run would be one more file nobody listed. Run before the build and before the pack.
import { readdirSync, rmSync, statSync } from 'node:fs';
import { join } from 'node:path';

function clean(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (!statSync(p).isDirectory()) {
      if (name.endsWith('.pyc')) rmSync(p);
      continue;
    }
    if (name === '__pycache__') rmSync(p, { recursive: true, force: true });
    else clean(p);
  }
}

for (const root of process.argv.slice(2).length ? process.argv.slice(2) : ['plugin']) clean(root);
