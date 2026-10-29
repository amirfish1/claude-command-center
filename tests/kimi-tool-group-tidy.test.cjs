// CCC-254: a provisional (live overlay) tool row removed after grouping left
// the group header reading "4 tool calls" over three rows; Codex group
// headers also lacked the purple wt / ccc orchestration chips.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const slice = app.slice(app.indexOf('  function _kimiRegroupTools('),
  app.indexOf('  // kimi-web parity (messagesToTurns flushGroup)'));

const row = (id, name, orch, live) => `<div class="kimi-tool" data-tool-use-id="${id}"`
  + (orch ? ` data-cc-orch="${orch}"` : '') + (live ? ` data-live-key="t1:${id}"` : '')
  + `><div class="kimi-tool-head"><span class="kimi-tool-name">${name}</span></div></div>`;

test('removing a provisional tool row recounts the group and keeps orch chips', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent(`<div class="conversations-view"><div class="kimi-turn">
      ${row('call_a', 'Bash', 'wt answer')}${row('exec-live', 'Bash', 'wt answer', true)}
      ${row('call_b', 'Stdin')}${row('call_c', 'Stdin')}
    </div></div>`);
    await page.evaluate(code => {
      (0, eval)('const _KIMI_GLYPHS = new Proxy({}, { get: () => "" });'
        + 'const escapeAttr = s => String(s).replace(/"/g, "&quot;");'
        + code + '\nwindow.regroup = _kimiRegroupTools;');
    }, slice);
    const out = await page.evaluate(() => {
      const turn = document.querySelector('.kimi-turn');
      regroup(turn);
      const before = turn.querySelector('.kimi-tg-title').textContent;
      document.querySelector('[data-live-key]').remove();
      window.CCCKimiTidyToolGroups(document.querySelector('.conversations-view'));
      const after = turn.querySelector('.kimi-tg-title').textContent;
      const chips = [...turn.querySelectorAll('.kimi-tg-orch-chip')].map(c => c.dataset.ccOrch);
      // Dropping to one row dissolves the group.
      turn.querySelectorAll('[data-tool-use-id="call_b"], [data-tool-use-id="call_c"]').forEach(n => n.remove());
      window.CCCKimiTidyToolGroups(turn);
      return { before, after, chips,
        groupsLeft: turn.querySelectorAll('.kimi-tool-group').length,
        rowsLeft: turn.querySelectorAll('.kimi-tool').length };
    });
    assert.equal(out.before, '4 tool calls (Bash, Stdin)');
    assert.equal(out.after, '3 tool calls (Bash, Stdin)');
    assert.deepEqual(out.chips, ['wt answer']);
    assert.equal(out.groupsLeft, 0);
    assert.equal(out.rowsLeft, 1);
  } finally {
    await browser.close();
  }
});
