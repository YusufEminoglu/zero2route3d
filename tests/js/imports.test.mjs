// Every relative import in the viewer must point at an existing file, and the
// offline export must be able to bundle the modules into one classic scope.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const jsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../web/js');
const VENDORED = new Set(['three.module.js', 'OrbitControls.js']);

test('relative imports resolve', () => {
  const missing = [];
  for (const name of fs.readdirSync(jsRoot).filter((f) => f.endsWith('.js'))) {
    const code = fs.readFileSync(path.join(jsRoot, name), 'utf8');
    for (const m of code.matchAll(/^\s*import\s[^;]*?from\s+'(\.[^']+)'/gm)) {
      if (!fs.existsSync(path.resolve(jsRoot, m[1]))) missing.push(`${name} -> ${m[1]}`);
    }
  }
  assert.deepEqual(missing, []);
});

test('first-party modules use plain named imports (the offline bundler strips them)', () => {
  const bad = [];
  for (const name of fs.readdirSync(jsRoot).filter((f) => f.endsWith('.js') && !VENDORED.has(f))) {
    const code = fs.readFileSync(path.join(jsRoot, name), 'utf8');
    for (const m of code.matchAll(/^\s*import\s([^;]*?)from\s+'(\.[^']+)'/gm)) {
      const clause = m[1];
      if (/\bas\b/.test(clause) && !/^\*\s+as\s+THREE\s*$/.test(clause.trim())) bad.push(`${name}: ${clause.trim()}`);
    }
    if (/^export\s+default\b/m.test(code)) bad.push(`${name}: export default`);
  }
  assert.deepEqual(bad, []);
});
