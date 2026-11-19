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

test('tool image stack stays within the reply and opens a usable gallery', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1400, height: 900, hasTouch: true });
    await page.setContent('<body class="conv-compact"><div class="conv-pane is-webui-session">'
      + '<div class="conversations-view"><div class="kimi-turn"></div></div></div></body>');
    await page.addStyleTag({ content: fs.readFileSync(require.resolve('../static/app.css'), 'utf8') });
    await require('./conversation-content-fixture.cjs').install(page);
    await page.evaluate(() => { window.CCCToolResultImagesCardHtml = _toolResultImagesCardHtml; });
    await page.evaluate(code => {
      (0, eval)('const _KIMI_GLYPHS = new Proxy({}, { get: () => "" });'
        + 'const escapeAttr = s => String(s).replace(/"/g, "&quot;");'
        + code + '\nwindow.regroup = _kimiRegroupTools;');
      const turn = document.querySelector('.kimi-turn');
      turn.innerHTML = '<div class="assistant-text">The screenshots are ready.</div>'
        + window.CCCToolResultImagesCardHtml({ images: Array.from({ length: 7 }, (_, idx) => ({
          kind: 'base64', line: 10, idx,
          data: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=',
        })) }, 'example');
      regroup(turn);
    }, imageSlice + slice);
    await page.addScriptTag({ content: fs.readFileSync(require.resolve('../static/conversation-interactions.js'), 'utf8') });
    await page.evaluate(() => {
      CCCConversationInteractions.createController({ state: {
        openMediaLightbox: src => { window.openedImage = src; },
      }, helpers: {} }).installConversationInteraction79();
    });
    const desktop = await page.evaluate(() => {
      const turn = document.querySelector('.kimi-turn');
      const text = turn.querySelector('.assistant-text').getBoundingClientRect();
      const stack = turn.querySelector('.conversation-media-stack').getBoundingClientRect();
      const turnHeight = turn.getBoundingClientRect().height;
      const rail = turn.querySelector('.conversation-media-rail');
      rail.remove();
      const withoutImages = turn.getBoundingClientRect().height;
      turn.prepend(rail);
      return { textHeight: text.height, stackHeight: stack.height, turnHeight, withoutImages,
        right: stack.left >= text.right, gap: stack.top - text.top,
        label: turn.querySelector('.conversation-media-stack').textContent, overflow: turn.scrollWidth > turn.clientWidth };
    });
    assert.ok(desktop.stackHeight <= desktop.turnHeight && desktop.turnHeight === desktop.withoutImages, JSON.stringify(desktop));
    assert.ok(desktop.right && Math.abs(desktop.gap) <= 2 && !desktop.overflow, JSON.stringify(desktop));
    assert.match(desktop.label, /7 images/);
    await page.click('.conversation-media-stack');
    assert.equal(await page.$eval('.conversation-media-gallery', el => el.matches(':popover-open')), true);
    const gallery = await page.evaluate(() => {
      const gallery = document.querySelector('.conversation-media-gallery');
      const before = gallery.querySelector('img');
      const focused = document.activeElement;
      regroup(document.querySelector('.kimi-turn'));
      const box = gallery.getBoundingClientRect();
      return { open: gallery.matches(':popover-open'), sameImage: gallery.querySelector('img') === before,
        sameFocus: document.activeElement === focused, images: gallery.querySelectorAll('figure').length,
        downloads: gallery.querySelectorAll('a[download]').length, bounded: box.top >= 0 && box.bottom <= innerHeight,
        noOverflow: gallery.scrollWidth === gallery.clientWidth };
    });
    assert.deepEqual(gallery, { open: true, sameImage: true, sameFocus: true, images: 7, downloads: 7, bounded: true, noOverflow: true });
    await page.click('.conversation-media-close');
    assert.equal(await page.$eval('.conversation-media-gallery', el => el.matches(':popover-open')), false);
    assert.equal(await page.$eval('.conversation-media-stack', el => el === document.activeElement), true);
    await page.keyboard.press('Enter');
    await page.keyboard.press('Escape');
    assert.equal(await page.$eval('.conversation-media-gallery', el => el.matches(':popover-open')), false);
    // A narrow split pane follows its own width, rather than the browser width.
    await page.addStyleTag({ content: '.conv-pane { flex: none; width: 600px; }' });
    const stacked = () => page.evaluate(() => {
      const turn = document.querySelector('.kimi-turn');
      const text = turn.querySelector('.assistant-text').getBoundingClientRect();
      const stack = turn.querySelector('.conversation-media-stack').getBoundingClientRect();
      return { stacked: stack.top >= text.bottom, height: stack.height, overflow: turn.scrollWidth > turn.clientWidth };
    });
    assert.deepEqual(await stacked(), { stacked: true, height: 72, overflow: false });
    await page.setViewport({ width: 390, height: 844, hasTouch: true });
    await page.addStyleTag({ content: '.conv-pane { flex: none; width: 100%; }' });
    assert.deepEqual(await stacked(), { stacked: true, height: 72, overflow: false });
    await page.tap('.conversation-media-stack');
    const phone = await page.$eval('.conversation-media-gallery', el => {
      const rect = el.getBoundingClientRect();
      const focus = document.activeElement.getBoundingClientRect();
      return { open: el.matches(':popover-open'), bounded: rect.left >= 0 && rect.right <= innerWidth && rect.bottom <= innerHeight,
        scrolls: el.scrollHeight > el.clientHeight,
        closeReachable: document.elementFromPoint(focus.left + focus.width / 2, focus.top + focus.height / 2) === document.activeElement };
    });
    assert.deepEqual(phone, { open: true, bounded: true, scrolls: true, closeReachable: true });
    await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
    assert.equal(await page.$eval('.tool-result-image', el => getComputedStyle(el).animationName), 'none');
    await page.click('.tool-result-image-zoom');
    assert.equal(await page.$eval('.conversation-media-gallery', el => el.matches(':popover-open')), false);
    assert.ok(await page.evaluate(() => window.openedImage.startsWith('data:image/png;base64,')));
  } finally {
    await browser.close();
  }
});
