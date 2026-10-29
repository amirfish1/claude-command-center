const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const helpers = app.slice(app.indexOf('  function anchorCodexStateBadge('),
  app.indexOf('  function _reanchorToTailUnlessOnlyTransientBetween('))
  + app.slice(app.indexOf('  function _kimiRegroupTools('), app.indexOf('  function _kimiMoonHtml('));
const compact = app.slice(app.indexOf('  function _ccUnitsOf('),
  app.indexOf('  // CCC-454: transcript view segmented control'));

test('compact live replays preserve tool groups, fold chips, and reader position', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent(`<style>${css}</style><body class="conv-compact"><div class="conversations-view" style="height:200px;overflow:auto"></div></body>`);
    await page.evaluate(code => {
      (0, eval)('const _KIMI_GLYPHS = {};\n' + code
        + '\nwindow.appendAssistant = _kimiAppendAssistantEvent; window.markRuns = _ccMarkRuns;');
    }, helpers + compact);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      function append(id, html) {
        const marker = document.createElement('div');
        marker.className = 'event assistant kimi-marker';
        marker.dataset.liveKey = 'turn:' + id;
        const holder = document.createElement('div');
        holder.innerHTML = html;
        appendAssistant(view, marker, [...holder.children], view.querySelector('.kimi-marker[data-live-key="turn:' + id + '"]'));
      }
      function replay() {
        append('text', '<div class="assistant-text"><p>Checking the live transcript.</p></div>');
        for (let i = 0; i < 3; i++) {
          append('tool' + i, '<div class="kimi-tool" data-tool-use-id="tool' + i + '"><div class="kimi-tool-name">Bash</div><div class="kimi-tool-body">Tool details</div></div>');
        }
      }
      replay(); markRuns(view);
      const group = view.querySelector('.kimi-tool-group');
      const chip = view.querySelector('.cc-run-chip');
      const paragraph = view.querySelector('.assistant-text p');
      chip.click();
      group.classList.remove('collapsed');
      view.scrollTop = 20;
      const before = { height: view.scrollHeight, top: view.scrollTop };
      for (let i = 0; i < 10; i++) { replay(); markRuns(view); }
      const stable = { group: group === view.querySelector('.kimi-tool-group'),
        chip: chip === view.querySelector('.cc-run-chip'), paragraph: paragraph === view.querySelector('.assistant-text p'),
        expanded: !group.classList.contains('collapsed') && chip.classList.contains('cc-run-open'),
        height: view.scrollHeight === before.height, scroll: view.scrollTop === before.top };
      append('text', '<div class="assistant-text"><p>Checking the live transcript. New content.</p></div>');
      markRuns(view);
      return { ...stable, textUpdated: view.querySelector('.assistant-text').textContent === 'Checking the live transcript. New content.',
        chips: view.querySelectorAll('.cc-run-chip').length };
    });
    assert.deepEqual(result, { group: true, chip: true, paragraph: true, expanded: true,
      height: true, scroll: true, textUpdated: true, chips: 1 });
  } finally { await browser.close(); }
});
