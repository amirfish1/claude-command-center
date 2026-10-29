// Every puppeteer.launch() in the .cjs suite must pass --no-sandbox.
//
// Linux hosts where AppArmor restricts unprivileged user namespaces
// (Ubuntu 23.10+, the hermes VM, most CI runners) abort Chrome at startup
// with "No usable sandbox!", which silently turned whole UI test files red
// on those hosts. The suite only loads local fixtures, so running without
// the sandbox is safe. This guard fails the moment a new test forgets it.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('every puppeteer.launch in tests/*.cjs passes --no-sandbox', () => {
  const dir = __dirname;
  const offenders = [];
  for (const name of fs.readdirSync(dir).filter(f => f.endsWith('.cjs'))) {
    const src = fs.readFileSync(path.join(dir, name), 'utf8');
    const re = /puppeteer\.launch\(/g;
    let m;
    while ((m = re.exec(src))) {
      // The options object is short; look a few hundred chars ahead.
      const window = src.slice(m.index, m.index + 400);
      if (!window.includes('--no-sandbox')) {
        const line = src.slice(0, m.index).split('\n').length;
        offenders.push(`${name}:${line}`);
      }
    }
  }
  assert.deepEqual(offenders, [], `puppeteer.launch without --no-sandbox: ${offenders.join(', ')}`);
});
