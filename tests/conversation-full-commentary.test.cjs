const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const css = fs.readFileSync('static/app.css', 'utf8');
const app = fs.readFileSync('static/app.js', 'utf8');

test('conversation commentary and expanded command rows wrap without clipping', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    for (const width of [390, 1200]) {
      await page.setViewport({ width, height: 900 });
      for (const mode of ['', 'conv-compact']) {
        await page.setContent(`<style>${css}</style><body class="${mode}"><div class="conv-pane"><div class="conversations-view" style="width:${width - 60}px">
          <div class="event assistant"><div class="assistant-text">${'Full commentary with several useful details. '.repeat(25)}</div></div>
          <div class="kimi-turn"><div class="assistant-text">${'Merged commentary remains readable. '.repeat(25)}</div></div>
          <div class="event assistant"><div class="assistant-text">Final answer.</div></div>
          <div class="tool-call-group"><div class="tool-call-group-header"><span class="tcg-arrow">▶</span><span class="tcg-label">${'command argument '.repeat(20)}</span><span class="tcg-result">${'Command completed with useful output. '.repeat(8)}</span><span class="tcg-meta">12:00</span></div><div class="tool-call-group-body"><div class="tool-call tool-command"><span class="tool-name">Bash</span><span class="tool-detail">${'command argument '.repeat(25)}</span></div></div></div>
        </div></div></body>`);
        for (const selector of ['.assistant-text', '.tcg-label', '.tcg-result', '.tool-detail']) {
          const clipped = await page.$$eval(selector, els => els.some(el => {
            const s = getComputedStyle(el);
            return s.textOverflow === 'ellipsis' || (parseInt(s.webkitLineClamp) > 0) || el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
          }));
          assert.equal(clipped, false, `${selector} clipped at ${width}px in ${mode || 'normal'}`);
        }
      }
    }
    await page.screenshot({ path: '/tmp/ccc-1313-commentary.png', fullPage: true });
  } finally { await browser.close(); }
});

test('command headers and result commentary retain the complete text', () => {
  const commandFn = app.slice(app.indexOf('  function fullCommandSummaryLabel('), app.indexOf('  function compactShellCommandLabel('));
  const summary = new Function('_stripCdWrapperPrefix', commandFn + '\nreturn fullCommandSummaryLabel;')(s => s);
  const command = 'command ' + 'argument '.repeat(50);
  assert.equal(summary(command), command.trim());
  const resultFn = app.slice(app.indexOf('  function toolCallResultSnippet('), app.indexOf('  function updateToolGroupLabel('));
  const snippet = new Function(resultFn + '\nreturn toolCallResultSnippet;')();
  const output = 'Complete result commentary '.repeat(15) + '\nA second line.';
  assert.equal(snippet({ querySelector: () => ({ textContent: output }) }), output.split('\n')[0].trim());
});
