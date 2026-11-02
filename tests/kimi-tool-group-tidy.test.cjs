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
const imageSlice = app.slice(app.indexOf('  function _toolResultImageKey('),
  app.indexOf('  // `codex_request` pending event:'));

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

test('tool images share a gallery across five tools, but stop at assistant text', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent('<div class="kimi-turn"></div>');
    await page.evaluate(code => {
      (0, eval)('const _KIMI_GLYPHS = new Proxy({}, { get: () => "" });'
        + 'const escapeAttr = s => String(s).replace(/"/g, "&quot;");'
        + code + '\nwindow.regroup = _kimiRegroupTools;');
    }, imageSlice + slice);
    const out = await page.evaluate(rows => {
      const turn = document.querySelector('.kimi-turn');
      const image = (line, id) => window.CCCToolResultImagesCardHtml({ tool_use_id: id,
        images: [{ kind: 'base64', line, idx: 0 }] }, 'test-session');
      turn.innerHTML = rows[0] + image(10, 'first') + '<div class="kimi-marker"></div>'
        + rows.slice(1).join('') + image(20, 'last');
      const firstImage = turn.querySelector('img');
      regroup(turn);
      const gallery = turn.querySelector('.tool-result-images');
      const bundled = {
        cards: turn.querySelectorAll('.tool-result-images').length,
        count: gallery.querySelector('.image-gen-card-head').textContent.trim(),
        keys: [...gallery.querySelectorAll('figure')].map(el => el.dataset.toolImageKey),
        owners: [...gallery.querySelectorAll('figure')].map(el => el.dataset.imageToolUseId),
        controls: gallery.querySelectorAll('[data-zoom-src], a[download]').length,
        tools: turn.querySelector('.kimi-tg-title').textContent,
        sameImage: firstImage === gallery.querySelector('img'),
        outsideTools: !gallery.closest('.kimi-tool-group'),
      };
      // A late result for an earlier tool must prepend to the gallery.
      turn.querySelector('.kimi-tool').insertAdjacentHTML('afterend', image(5, 'early'));
      regroup(turn);
      const lateKeys = [...turn.querySelectorAll('figure')].map(el => el.dataset.toolImageKey);
      // Repeated grouping must preserve nodes, order, and expanded tool state.
      turn.querySelector('.kimi-tool-group').classList.remove('collapsed');
      regroup(turn);
      const expanded = !turn.querySelector('.kimi-tool-group').classList.contains('collapsed');
      turn.insertAdjacentHTML('beforeend', '<div class="assistant-text">Here is the result.</div>'
        + rows[0] + image(30, 'separate'));
      regroup(turn);
      return { bundled, lateKeys, expanded,
        afterText: turn.querySelectorAll('.tool-result-images').length,
        finalKeys: [...turn.querySelectorAll('figure')].map(el => el.dataset.toolImageKey) };
    }, Array.from({ length: 6 }, (_, i) => row('tool-' + i, 'Bash')));
    assert.deepEqual(out.bundled, {
      cards: 1, count: '◫ 2 images from tool', keys: ['10:0', '20:0'], owners: ['first', 'last'],
      controls: 4, tools: '6 tool calls (Bash)', sameImage: true, outsideTools: true,
    });
    assert.deepEqual(out.lateKeys, ['5:0', '10:0', '20:0']);
    assert.equal(out.expanded, true);
    assert.equal(out.afterText, 2);
    assert.deepEqual(out.finalKeys, ['5:0', '10:0', '20:0', '30:0']);
  } finally {
    await browser.close();
  }
});
