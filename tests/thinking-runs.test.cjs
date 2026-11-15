const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const marker = '\n})();\n\n/* === Hero: Fleet Pulse === */';
assert.ok(app.includes(marker));
const exposed = app.replace(marker, '\nwindow.thinkingTest={renderConversationEvents,activePaneId,getConvViewForPane,_syncThinkingRuns,_prependConversationEvents};' + marker);

test('real renderers replace consecutive thoughts in a steady slot, then preserve activity boundaries', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (request.isNavigationRequest()) return request.respond({ status: 200, contentType: 'text/html',
        body: fs.readFileSync(require.resolve('../static/index.html'), 'utf8') });
      if (url.pathname === '/static/app.js') return request.respond({ status: 200, contentType: 'application/javascript', body: exposed });
      if (url.pathname === '/static/app.css') return request.respond({ status: 200, contentType: 'text/css', body: css });
      return request.respond({ status: 200, contentType: 'application/json', body: '[]' });
    });
    await page.goto('http://ccc-thinking.test/', { waitUntil: 'load' });
    await page.waitForFunction(() => !!window.thinkingTest);
    for (const width of [1280, 390]) {
      await page.setViewport({ width, height: 850 });
      await page.evaluate(() => window.cccMobileNav.show());
      await page.waitForFunction(() => !document.querySelector('.main').getAnimations().some(a => a.playState === 'running'));
      for (const merged of [false, true, 'codex']) for (const compact of [false, true]) {
        const result = await page.evaluate(({merged, compact}) => {
          const h = window.thinkingTest, paneId = h.activePaneId(), view = h.getConvViewForPane(paneId);
          const pane = view.closest('.conv-pane');
          pane.classList.toggle('is-webui-session', !!merged);
          pane.classList.toggle('is-codex-session', merged === 'codex');
          document.body.classList.toggle('conv-compact', compact);
          // Keep the real reader visible without requiring sidebar navigation.
          view.style.cssText = 'display:block;position:fixed;inset:60px 16px 100px;overflow:auto;z-index:9999;background:var(--bg)';
          view.replaceChildren();
          let line = 970000;
          const render = blocks => h.renderConversationEvents([{type:'assistant',line:++line,blocks}], paneId, {initialLoad:true});
          const thought = text => ({kind:'thinking',text});
          render([thought('First thought')]);
          const slot = view.querySelector('.thinking-run'), heading = slot.querySelector('button');
          const before = heading.getBoundingClientRect().top, height = slot.getBoundingClientRect().height;
          render([thought('Earlier paragraph\n\nNewest thought ' + 'with details '.repeat(80))]);
          const latestParagraph = slot.querySelector('.thinking-run-preview').textContent.startsWith('Newest thought');
          render([thought('Latest short thought')]);
          const stable = slot === view.querySelector('.thinking-run') && heading === slot.querySelector('button')
            && Math.abs(before - heading.getBoundingClientRect().top) < 1
            && Math.abs(height - slot.getBoundingClientRect().height) < 1;
          const preview = slot.querySelector('.thinking-run-preview').textContent;
          const sourceCount = view.querySelectorAll('.thinking-run-source').length;
          const sourcesHidden = [...view.querySelectorAll('.thinking-run-source')].every(el => !el.checkVisibility());
          heading.click();
          const fullVisible = slot.querySelector('.t-body').checkVisibility() && heading.getAttribute('aria-expanded') === 'true';
          render([thought('Another paragraph\n\nUpdated while expanded')]);
          const expansionRetained = heading.getAttribute('aria-expanded') === 'true'
            && slot.querySelector('.t-body').textContent.includes('Another paragraph');
          heading.click();
          render([{kind:'text',text:'A visible reply.'}, thought('After the reply')]);
          render([{kind:'tool_use',name:'Bash',id:'thinking-boundary',detail:'pwd'}, thought('After the tool')]);
          const boundaryCount = view.querySelectorAll('.thinking-run').length;
          const sourceTexts = [...view.querySelectorAll('.thinking-run-source')].map(el => el.textContent);
          const html = view.innerHTML;
          h._syncThinkingRuns(view);
          const idempotent = html === view.innerHTML;
          const overflow = view.scrollWidth > view.clientWidth + 1;
          // Real detach/render/reattach paging must join a thinking run at
          // the page seam, leaving the latest thought in the shared slot.
          h._prependConversationEvents([{type:'assistant',line:960000,blocks:[thought('Older page thought')]}], paneId);
          const joinedPage = view.querySelectorAll('.thinking-run').length === 3
            && view.querySelector('.thinking-run-preview').textContent === 'Updated while expanded';
          h.renderConversationEvents([{type:'result',line:++line}],paneId,{initialLoad:true});
          render([thought('New turn thought')]);
          const turnBoundary = view.querySelectorAll('.thinking-run').length === 4;
          return {stable,latestParagraph,preview,sourceCount,sourcesHidden,fullVisible,expansionRetained,boundaryCount,
            originalsRetained:sourceTexts.some(t=>t.includes('First thought')),idempotent,overflow,joinedPage,turnBoundary};
        }, {merged, compact});
        assert.deepEqual(result, {stable:true,latestParagraph:true,preview:'Latest short thought',sourceCount:3,sourcesHidden:true,
          fullVisible:true,expansionRetained:true,boundaryCount:3,originalsRetained:true,idempotent:true,overflow:false,
          joinedPage:true,turnBoundary:true},
        JSON.stringify({width,merged,compact}));
        await page.evaluate(() => document.querySelector('.thinking-run > button').scrollIntoView({block:'center'}));
        await page.waitForFunction(() => {
          const button = document.querySelector('.thinking-run > button'), rect = button.getBoundingClientRect();
          return rect.width > 0 && rect.top >= 0 && rect.bottom <= innerHeight
            && button.contains(document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2));
        });
        await page.focus('.thinking-run > button');
        await page.keyboard.press('Enter');
        assert.equal(await page.$eval('.thinking-run > button', el => el.getAttribute('aria-expanded')), 'true');
        await page.keyboard.press('Space');
        assert.equal(await page.$eval('.thinking-run > button', el => el.getAttribute('aria-expanded')), 'false');
        if (process.env.CCC_THINKING_SCREENSHOTS) await page.screenshot({path:process.env.CCC_THINKING_SCREENSHOTS + '/thinking-' + width + '-' + merged + '-' + compact + '.png'});
      }
    }
  } finally { await browser.close(); }
});
