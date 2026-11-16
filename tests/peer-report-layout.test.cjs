const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const css = fs.readFileSync(process.env.PEER_REPORT_CSS || require.resolve('../static/app.css'), 'utf8');
const helpers = app.slice(app.indexOf('  const PEER_LABEL_RE ='), app.indexOf('  function renderImageDescriptors('));

test('peer reports keep their preview, disclosure and expanded evidence readable', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    // Normal and compact readers; the latter must not clamp a native disclosure.
    for (const [width, engine, theme, compact, zoom] of [
      [390, 'is-webui-session is-codex-session', 'light', true, 1.5],
      [1340, 'is-webui-session is-codex-session', 'light', false, 1.5],
      [900, '', 'dark', true, 1],
      [390, 'is-webui-session', 'dark', false, 1],
      [900, 'is-webui-session is-codex-session', 'stitch', true, 1.5],
    ]) {
      const page = await browser.newPage();
      try {
        await page.setViewport({ width, height: 950 });
        await page.setContent(`<html data-theme="${theme === 'stitch' ? 'dark' : theme}"><body class="${compact ? 'conv-compact' : ''}">
          <div class="conv-pane ${engine} ${theme === 'light' ? 'has-conv-bg conv-bg-light' : theme === 'stitch' ? 'has-conv-bg' : ''}" ${theme === 'stitch' ? 'data-conv-bg="stitch"' : ''} style="${theme === 'light' ? '--conv-bg:#f8f3e9;--conv-text:#20252d;--conv-text-muted:#64696f;--conv-accent:#245a9a;--conv-user-text:#245a9a' : '--conv-bg:#0d1117;--conv-text:#c9d1d9;--conv-accent:#6e8caf'}">
            <div class="conversations-view"><div class="event user_text peer-message has-user-steer"><div class="user-msg"><span class="peer-sender"><span class="peer-sender-name">Review worker</span><span class="peer-sender-mode">claimed via relay · reply route available</span></span><div id="report"></div></div><button class="user-message-steer">Steer</button></div>
            <div class="event assistant"><div class="assistant-text">I’ll check the changes and validation before marking that part complete.</div></div></div>
          </div></body></html>`);
        await page.addStyleTag({ content: css + '\nbody{display:block;margin:0;padding:12px;height:auto;overflow:auto}.conv-pane{height:auto;display:block}.conversations-view{display:block!important;overflow:visible;height:auto;zoom:' + zoom + '}' + (theme === 'light' ? '.conversations-view{background:var(--conv-bg)}' : '.conv-pane{--conv-text:var(--text);--conv-accent:var(--accent)}') });
        await require('./conversation-content-fixture.cjs').install(page);
        await page.addScriptTag({ content: `
          const escapeHtml = s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
          const escapeAttr = escapeHtml; const linkifyPath = escapeHtml; const linkifyPastedImages = s => s;
          const currentConversation = ''; const currentSession = null; const sessionIdByConv = {};
          function activeConvRepoPath() { return ''; }
          ${helpers}
          document.querySelector('#report').outerHTML = formatPeerMessageHtml('STATUS: SUCCEEDED\\nSUMMARY: Merged two queue review skills and their older alias into one unified review workflow, preserving the operational checks, content review, safe repairs and escalation instructions for every queue.\\nFILES: src/review.js; tests/review.test.js\\nVALIDATION: All checks passed.');
        ` });
        const measure = () => page.evaluate(() => {
          const row = document.querySelector('.peer-message'), msg = row.querySelector('.user-msg');
          const preview = row.querySelector('summary'), detail = row.querySelector('.peer-report-expand');
          const rect = el => { const r = el.getBoundingClientRect(); return { top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height }; };
          const range = document.createRange(); range.selectNodeContents(row.querySelector('.peer-report-summary'));
          const lastTextBottom = Math.max(...Array.from(range.getClientRects(), r => r.bottom));
          return { row:rect(row), msg:rect(msg), preview:rect(preview), detail:rect(detail), lastTextBottom,
            next:rect(row.nextElementSibling), clip:getComputedStyle(msg).overflowY,
            innerFill:getComputedStyle(msg).backgroundImage, innerColor:getComputedStyle(msg).backgroundColor,
            outerFill:getComputedStyle(row).backgroundImage, scrollWidth:document.documentElement.scrollWidth,
            sender:rect(row.querySelector('.peer-sender')), steer:rect(row.querySelector('button')),
            body:rect(row.querySelector('.peer-report-body')) };
        });
        let m = await measure();
        const label = `${width}/${engine}/${theme}/compact=${compact}`;
        if (process.env.PEER_REPORT_SCREENSHOTS) await page.screenshot({ path: `${process.env.PEER_REPORT_SCREENSHOTS}/${theme}-${width}-${compact ? 'compact' : 'normal'}.png`, fullPage:true });
        assert.ok(m.detail.top >= m.lastTextBottom - 1, `${label}: Details overlaps preview text`);
        assert.ok(m.msg.bottom >= m.preview.bottom - 1, `${label}: preview is clipped`);
        assert.ok(m.next.top >= m.row.bottom - 1, `${label}: report covers following reply`);
        assert.ok(m.scrollWidth <= width, `${label}: horizontal overflow`);
        assert.ok(m.sender.right <= m.steer.left + 1 || m.sender.top >= m.steer.bottom, `${label}: attribution overlaps Steer`);
        assert.equal(m.innerFill, 'none', `${label}: no second tinted bubble`);
        assert.equal(m.innerColor, 'rgba(0, 0, 0, 0)', `${label}: one report surface`);
        await page.focus('.peer-report-preview');
        await page.keyboard.press('Enter');
        assert.equal(await page.$eval('.peer-report', el => el.open), true);
        m = await measure();
        assert.ok(m.msg.bottom >= m.body.bottom - 1, `${label}: expanded evidence clipped`);
        assert.ok(m.next.top >= m.body.bottom - 1, `${label}: expanded evidence overlaps reply`);
        await page.keyboard.press('Space');
        assert.equal(await page.$eval('.peer-report', el => el.open), false);
        await page.click('.peer-report-preview');
        assert.equal(await page.$eval('.peer-report', el => el.open), true);
      } finally { await page.close(); }
    }
  } finally { await browser.close(); }
});
