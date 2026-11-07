// CCC-143: one transcript render adds hundreds of nodes under a few hosts; the
// chip pass must visit each host once and repaint once, not once per node.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const source = fs.readFileSync(require.resolve('../static/question-tags.js'), 'utf8');

test('a render of many nodes repaints chips once and still chips them', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent('<div class="assistant-text" id="host"></div>');
    await page.evaluate(() => {
      window.fetch = async () => ({ json: async () => ({ ok: true, questions: [{ n: 7, tag: '[Q7:slug]' }] }) });
    });
    await page.addScriptTag({ content: source });
    const out = await page.evaluate(async () => {
      await new Promise(r => setTimeout(r, 50));
      const real = Document.prototype.querySelectorAll;
      let paints = 0;
      Document.prototype.querySelectorAll = function (selector) {
        if (selector === '.qtag-chip') paints++;
        return real.call(this, selector);
      };
      const host = document.getElementById('host');
      for (let i = 0; i < 300; i++) {
        const p = document.createElement('p');
        p.textContent = i === 299 ? 'see [Q7:slug]' : 'plain text';
        host.append(p);
      }
      await new Promise(r => setTimeout(r));
      Document.prototype.querySelectorAll = real;
      return { paints, chips: host.querySelectorAll('.qtag-chip').length };
    });
    assert.deepEqual(out, { paints: 1, chips: 1 });
  } finally {
    await browser.close();
  }
});
