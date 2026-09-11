const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const puppeteer = require('puppeteer');

const app = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/app.css', 'utf8');

function slice(from, to) {
  const start = app.indexOf(from);
  const end = app.indexOf(to);
  assert.ok(start !== -1 && end > start, `could not slice ${from}`);
  return app.slice(start, end);
}

const ctx = {};
vm.createContext(ctx);
vm.runInContext(
  slice('  function relativeTime(ts) {', '  // CCC-1033: full-word relative time')
  + slice('    const _repeatGroupRange = (values, format, newestFirst) => {',
          '    const _renderRepeatGroup = (cards, opts, key) => {')
  // `const` in a vm script does not land on the context object.
  + '\n;globalThis._repeatGroupRange = _repeatGroupRange;',
  ctx
);

const agoSec = (s) => Date.now() / 1000 - s;

test('a folded group summarises the span it hides, not just its newest row', () => {
  // The bug this replaces: the header showed only the newest stamp, so four
  // spawns reaching back five hours read as one session from an hour ago.
  const ages = ctx._repeatGroupRange(
    [agoSec(3600), agoSec(5 * 3600), agoSec(2 * 3600)], ctx.relativeTime, true);
  assert.equal(ages, '1h–5h');

  // Percentages read the other way round: smallest first.
  assert.equal(ctx._repeatGroupRange([12, 3, 7], v => v, false), '3–12');

  // One distinct value is not a range -- print it once.
  assert.equal(ctx._repeatGroupRange([9, 9, 9], v => v, false), '9');
  assert.equal(ctx._repeatGroupRange([agoSec(60), agoSec(90)], ctx.relativeTime, true), '1m');

  // Rows that report nothing must not fabricate an endpoint.
  assert.equal(ctx._repeatGroupRange([], v => v, false), '');
  assert.equal(ctx._repeatGroupRange([null, undefined, NaN, 'x'], v => v, false), '');
  assert.equal(ctx._repeatGroupRange([null, 5, 'x'], v => v, false), '5');
});

test('the group context span is styled, not an unstyled leftover', async () => {
  const browser = await puppeteer.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1400, height: 800 });
    await page.setContent(
      `<html><head><style>${css}</style></head><body><div id="convList">`
      + `<div class="conv-repeat-group"><div class="conv-repeat-group-header">`
      + `<button type="button" class="conv-repeat-group-toggle">`
      + `<span class="conv-repeat-group-title">Drain the CCC queue</span>`
      + `<span class="conv-repeat-group-count">×4</span>`
      + `<span class="conv-repeat-group-ctx">3–12%</span>`
      + `<span class="conv-repeat-group-rel">1h–5h</span>`
      + `</button></div></div></div></body></html>`
    );
    const seen = await page.evaluate(() => {
      const el = document.querySelector('.conv-repeat-group-ctx');
      const cs = getComputedStyle(el);
      return {
        text: el.textContent,
        display: cs.display,
        // A bare unstyled span inherits the parent size; the rule sets 11px.
        fontSize: cs.fontSize,
        width: Math.round(el.getBoundingClientRect().width),
      };
    });
    assert.equal(seen.text, '3–12%');
    assert.notEqual(seen.display, 'none');
    assert.equal(seen.fontSize, '11px', 'the .conv-repeat-group-ctx rule must actually bind');
    assert.ok(seen.width > 0);
  } finally { await browser.close(); }
});
