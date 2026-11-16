// CCC-254: a provisional (live overlay) tool row removed after grouping left
// the group header reading "4 tool calls" over three rows; Codex group
// headers also lacked the purple wt / ccc orchestration chips.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = require('./legacy-dashboard-source.cjs');
const slice = app.slice(app.indexOf('  function _kimiFlushRegroups('),
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
    await require('./conversation-content-fixture.cjs').install(page);
    await page.evaluate(() => { window.CCCToolResultImagesCardHtml = _toolResultImagesCardHtml; });
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
    await require('./conversation-content-fixture.cjs').install(page);
    await page.evaluate(() => { window.CCCToolResultImagesCardHtml = _toolResultImagesCardHtml; });
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

test('a first render regroups each turn once, after its events are in place', async () => {
  // CCC-143: regrouping per event re-read layout and rebuilt every group.
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent(`<div class="conversations-view"><div class="kimi-turn">
      ${row('call_a', 'Bash')}${row('call_b', 'Bash')}
    </div></div>`);
    await require('./conversation-content-fixture.cjs').install(page);
    await page.evaluate(() => { window.CCCToolResultImagesCardHtml = _toolResultImagesCardHtml; });
    await page.evaluate(code => {
      (0, eval)('const _KIMI_GLYPHS = new Proxy({}, { get: () => "" });'
        + 'const escapeAttr = s => String(s).replace(/"/g, "&quot;");'
        + code + '\nwindow.regroup = _kimiRegroupTools; window.flush = _kimiFlushRegroups;');
    }, slice);
    const out = await page.evaluate(() => {
      const turn = document.querySelector('.kimi-turn');
      regroup.batch = new Set();
      regroup(turn);
      regroup(turn);
      const during = turn.querySelectorAll('.kimi-tool-group').length;
      flush();
      return { during, after: turn.querySelectorAll('.kimi-tool-group').length, batch: regroup.batch };
    });
    assert.deepEqual(out, { during: 0, after: 1, batch: null });
  } finally {
    await browser.close();
  }
});

test('conversation media packs on the right without image-height gaps between text blocks', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1400, height: 900 });
    await page.setContent('<body class="conv-compact"><div class="conv-pane is-webui-session">'
      + '<div class="conversations-view"><div class="kimi-turn"></div></div></div></body>');
    await page.addStyleTag({ content: fs.readFileSync(require.resolve('../static/app.css'), 'utf8') });
    await require('./conversation-content-fixture.cjs').install(page);
    await page.evaluate(() => { window.CCCToolResultImagesCardHtml = _toolResultImagesCardHtml; });
    await page.evaluate(code => {
      (0, eval)('const _KIMI_GLYPHS = new Proxy({}, { get: () => "" });'
        + 'const escapeAttr = s => String(s).replace(/"/g, "&quot;");'
        + code + '\nwindow.regroup = _kimiRegroupTools;');
      const image = line => window.CCCToolResultImagesCardHtml({ images: [{ kind: 'base64', line,
        data: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=' }] }, 'example');
      const turn = document.querySelector('.kimi-turn');
      turn.innerHTML = '<div class="assistant-text">Checking the settings screenshot.</div>' + image(10)
        + '<div class="assistant-text">The session is running and visible.</div>' + image(20)
        + '<div class="assistant-text">The navigation path is confirmed.</div>';
      regroup(turn);
      for (const img of turn.querySelectorAll('img')) img.style.height = '200px';
    }, imageSlice + slice);
    const measure = () => page.evaluate(() => {
      const turn = document.querySelector('.kimi-turn');
      const texts = [...turn.querySelectorAll('.assistant-text')].map(n => n.getBoundingClientRect());
      const cards = [...turn.querySelectorAll('.image-gen-card')].map(n => n.getBoundingClientRect());
      return { gap: texts[1].top - texts[0].bottom,
        right: cards[0].left >= texts[0].right,
        topAligned: Math.abs(cards[0].top - texts[0].top) < 2,
        packed: Math.abs(cards[1].top - cards[0].bottom) < 2,
        overflow: turn.scrollWidth > turn.clientWidth,
        stacked: cards[0].top >= texts[2].bottom };
    });
    const desktop = await measure();
    assert.ok(desktop.right && desktop.topAligned && desktop.packed, JSON.stringify(desktop));
    assert.ok(desktop.gap <= 12 && !desktop.overflow, JSON.stringify(desktop));
    // The breakpoint follows the conversation width, even on a wide screen.
    await page.addStyleTag({ content: '.conv-pane { flex: none; width: 600px; }' });
    const narrowPane = await measure();
    assert.ok(narrowPane.stacked && !narrowPane.overflow, JSON.stringify(narrowPane));
    await page.setViewport({ width: 390, height: 844 });
    await page.addStyleTag({ content: '.conv-pane { flex: none; width: 100%; }' });
    const mobile = await measure();
    assert.ok(mobile.stacked && !mobile.overflow && mobile.packed, JSON.stringify(mobile));
  } finally {
    await browser.close();
  }
});
