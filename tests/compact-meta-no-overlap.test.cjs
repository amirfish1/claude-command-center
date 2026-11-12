const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const css = fs.readFileSync('static/app.css', 'utf8');

test('reply metadata never covers prose across engines and input modes', async () => {
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    for (const touch of [false, true]) {
      const page = await browser.newPage();
      await page.setViewport({width: touch ? 390 : 1440, height: 900, isMobile: touch, hasTouch: touch});
      for (const compact of [false, true]) {
        for (const engine of ['claude', 'codex', 'kimi', 'devin']) {
          for (const final of [false, true]) {
            const merged = engine === 'codex' || engine === 'kimi';
            const controls = '<span class="msg-ts">30 minutes ago</span><span class="assistant-message-actions"><button class="assistant-message-action" aria-label="Speak">S</button><button class="assistant-message-action" aria-label="Copy">C</button></span>';
            const text = `<div class="assistant-text ${final ? 'cc-final' : ''}"><p>${'Scores improved from 24 to 29 out of 31 after a scoring fix. Every word of this reply must stay readable, including the last line. '.repeat(8)}</p></div>`;
            await page.setContent(`<style>${css}</style><body class="${compact ? 'conv-compact' : ''}"><div class="conv-pane ${merged ? 'is-webui-session' : ''} is-${engine}-session"><div class="conversations-view">${merged ? '<div class="kimi-turn">'+text+'<div class="kimi-answer-meta">'+controls+'</div></div>' : '<div class="event assistant">'+controls+text+'</div>'}</div></div></body>`);
            await page.addStyleTag({content: 'body{display:block!important;margin:16px}.conv-pane{display:block!important;height:auto!important}.conversations-view{display:block!important;overflow:visible!important}'});
            await page.hover('.assistant-text');
            if (merged) await page.hover('.kimi-answer-meta');
            const overlaps = await page.evaluate(() => {
              const range = document.createRange();
              range.selectNodeContents(document.querySelector('.assistant-text p'));
              const textBox = document.querySelector('.assistant-text').getBoundingClientRect();
              // Range rectangles include lines hidden by the two-line clamp.
              const lines = [...range.getClientRects()].map(line => ({
                left: line.left, right: line.right,
                top: Math.max(line.top, textBox.top), bottom: Math.min(line.bottom, textBox.bottom),
              })).filter(line => line.bottom > line.top);
              return [...document.querySelectorAll('.msg-ts,.assistant-message-actions')].flatMap(el => {
                if (getComputedStyle(el).display === 'none') return [];
                const box = el.getBoundingClientRect();
                return lines.some(line => box.left < line.right && box.right > line.left && box.top < line.bottom && box.bottom > line.top) ? [el.className] : [];
              });
            });
            if (process.env.META_SCREENSHOT_DIR) await page.screenshot({path: `${process.env.META_SCREENSHOT_DIR}/${engine}-${final ? 'final' : 'narration'}-${compact ? 'compact' : 'normal'}-${touch ? 'touch' : 'desktop'}.png`});
            assert.deepEqual(overlaps, [], `${engine} ${final ? 'final' : 'narration'} ${compact ? 'compact' : 'normal'} ${touch ? 'touch' : 'desktop'}`);
          }
        }
      }
      await page.close();
    }
  } finally { await browser.close(); }
});
