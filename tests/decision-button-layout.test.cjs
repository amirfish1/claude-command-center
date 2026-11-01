const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

test('phone decisions wrap long labels below the prompt beside audio', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    for (const width of [320, 390]) {
      await page.setViewport({ width, height: 844, isMobile: true, hasTouch: true });
      await page.setContent(`<meta name="viewport" content="width=device-width,initial-scale=1"><style>${fs.readFileSync('static/app.css', 'utf8')}
        body { display:block; } #convList { width:${width - 32}px; }
        .conv-item { padding:0; } #convList#convList .conv-item .conv-main-row { display:flex!important; width:100%; }
        </style><div id="convList"><div class="conv-item"><div class="conv-main-row has-inline-step">
        <div class="conv-outcome is-inline is-next is-next-step">
          <span class="conv-outcome-step" data-label="Decide">Restore the earlier session, or leave it archived.</span>
          <span class="conv-quick-action-slot is-decision"><button class="conv-quick-action">Leave the earlier session archived and keep all existing work</button><button class="conv-quick-action">Unarchive it</button></span>
          <button class="conv-row-read" aria-label="Read reply">▶</button>
        </div></div></div></div>`);
      const layout = await page.evaluate(() => {
        const prompt = document.querySelector('.conv-outcome-step').getBoundingClientRect();
        const slot = document.querySelector('.conv-quick-action-slot').getBoundingClientRect();
        return { promptBottom: prompt.bottom, buttonsTop: slot.top,
          buttons: [...document.querySelectorAll('.conv-quick-action')].map(button => ({
            text: button.textContent, width: button.clientWidth, contentWidth: button.scrollWidth,
            right: button.getBoundingClientRect().right, visible: !!button.getClientRects().length,
          })), viewport: innerWidth };
      });
      assert.ok(layout.buttonsTop >= layout.promptBottom - 1, 'choices belong below the prompt');
      assert.deepEqual(layout.buttons.map(button => button.text), ['Leave the earlier session archived and keep all existing work', 'Unarchive it']);
      for (const button of layout.buttons) {
        assert.ok(button.visible && button.width > 0);
        assert.ok(button.contentWidth <= button.width + 1, `${button.text} must be fully readable at ${width}px`);
        assert.ok(button.right <= layout.viewport, 'button must stay inside the phone viewport');
      }
    }
  } finally { await browser.close(); }
});
