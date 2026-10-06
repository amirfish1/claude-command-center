const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const fxJs = fs.readFileSync('static/fx.js', 'utf8');
const fxCss = fs.readFileSync('static/fx.css', 'utf8');
const indexHtml = fs.readFileSync('static/index.html', 'utf8');

test('window.cccFx exposes the documented kit contract', () => {
  assert.match(fxJs, /window\.cccFx\s*=/);
  const apiBlock = fxJs.slice(fxJs.indexOf('window.cccFx'));
  for (const key of ['play', 'confetti', 'countUp', 'reducedMotion', 'muted']) {
    assert.ok(apiBlock.includes(key), `cccFx.${key} missing`);
  }
});

test('all six sound cues are synthesized in fx.js', () => {
  for (const name of ['welcome', 'step', 'success', 'coin', 'error', 'whoosh']) {
    assert.match(fxJs, new RegExp(`\\b${name}\\s*:\\s*function`), `sound ${name} missing`);
  }
  // No audio assets: everything is Web Audio oscillators/noise.
  assert.doesNotMatch(fxJs, /new Audio\(|\.mp3|\.wav|\.ogg/);
});

test('kit is gated by the shared sounds pref and reduced-motion', () => {
  assert.ok(fxJs.includes('ccc-sounds-enabled'));
  assert.ok(fxJs.includes('prefers-reduced-motion'));
  assert.ok(fxCss.includes('prefers-reduced-motion'));
});

test('index.html loads fx.css and fx.js before app.js', () => {
  assert.ok(indexHtml.includes('href="/static/fx.css"'));
  assert.ok(indexHtml.includes('src="/static/fx.js"'));
  assert.ok(indexHtml.indexOf('/static/fx.js') < indexHtml.indexOf('src="/static/app.js"'));
});

test('fx.css ships the effect classes', () => {
  for (const cls of ['.fx-confetti-canvas', '.fx-shimmer', '.fx-glow', '.fx-pop']) {
    assert.ok(fxCss.includes(cls), `${cls} missing`);
  }
});
