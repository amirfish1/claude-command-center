const respondConversationAsset = require('./conversation-assets.cjs');
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
      if (respondConversationAsset(request)) return;
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
          // Normal view folds the entire work sequence. Reveal its history
          // before exercising the individual thought disclosure beneath it.
          if (!compact) view.querySelector('.activity-run-toggle').click();
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
        await page.evaluate(() => {
          const activity = document.querySelector('.activity-run-toggle[aria-expanded="false"]');
          if (!document.body.classList.contains('conv-compact')) activity?.click();
          document.querySelector('.thinking-run > button').scrollIntoView({block:'center'});
        });
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
    // Replay the reported alternating thought/command sequence through the
    // shipped renderer. Geometry is measured on the visible disclosure.
    for (const width of [1280, 390]) {
      await page.setViewport({width, height:850});
      await page.evaluate(() => window.cccMobileNav.show());
      await page.waitForFunction(() => !document.querySelector('.main').getAnimations().some(a => a.playState === 'running'));
      for (const merged of [false, true, 'codex']) {
        const result = await page.evaluate(({merged}) => {
          const h = window.thinkingTest, paneId = h.activePaneId(), view = h.getConvViewForPane(paneId);
          const pane = view.closest('.conv-pane');
          pane.classList.toggle('is-webui-session', !!merged);
          pane.classList.toggle('is-codex-session', merged === 'codex');
          document.body.classList.remove('conv-compact');
          view.replaceChildren();
          let line = 980000;
          const render = blocks => h.renderConversationEvents([{type:'assistant',line:++line,blocks}],paneId,{initialLoad:true});
          render([{kind:'thinking',text:'Investigating the playback rate.'}]);
          const slot = view.querySelector('.activity-run'), heading = slot.querySelector('button');
          const top = heading.getBoundingClientRect().top, height = slot.getBoundingClientRect().height;
          const stable = () => slot === view.querySelector('.activity-run') && heading.checkVisibility()
            && Math.abs(top - heading.getBoundingClientRect().top) < 1
            && Math.abs(height - slot.getBoundingClientRect().height) < 1;
          render([{kind:'tool_use',name:'Bash',id:'activity-first',detail:'Read the worklet',command:'cat worklet.js'}]);
          const firstStable = stable();
          const commandEllipsis = slot.querySelector('.activity-run-preview').textContent === 'Read the worklet...';
          // Saved transcripts may insert empty reasoning between commands.
          // These invisible entries must preserve the single activity slot.
          render([{kind:'thinking',text:''}]);
          render([{kind:'tool_use',name:'Bash',id:'activity-second',detail:'Check how rate is used ' + 'and its tests '.repeat(60),command:'rg rate worklet.js'}]);
          const longStable = stable();
          const oneRun = view.querySelectorAll('.activity-run').length === 1;
          const newestCommand = slot.querySelector('.activity-run-preview').textContent.startsWith('Check how rate is used');
          render([{kind:'thinking',text:'Now implement pitch-preserving speed.'}]);
          const thoughtStable = stable();
          const latestThought = slot.querySelector('.activity-run-preview').textContent === 'Now implement pitch-preserving speed...';
          const collapsedSources = [...view.querySelectorAll('.activity-run-source')].every(el=>!el.checkVisibility());
          heading.click();
          const historyVisible = [...view.querySelectorAll('.activity-run-source')].every(el=>el.checkVisibility());
          const commandsRetained = view.textContent.includes('Read the worklet') && view.textContent.includes('Check how rate is used');
          heading.click();
          const html = view.innerHTML;
          h._syncThinkingRuns(view);
          const idempotent = html === view.innerHTML;
          render([{kind:'text',text:'Playback speed is fixed.'}]);
          render([{kind:'tool_use',name:'Bash',id:'activity-after-reply',detail:'Run the tests'}]);
          const replyBoundary = view.querySelectorAll('.activity-run').length === 2;
          render([{kind:'tool_use',name:'AskUserQuestion',id:'activity-question',detail:'Which voice do you prefer?'}]);
          const question = [...view.querySelectorAll('.tool-call, .kimi-tool')].find(el=>el.textContent.includes('Which voice'));
          const questionVisible = !!question?.checkVisibility();
          render([{kind:'thinking',text:'Waiting for the preference.'}]);
          const lastSlot = [...view.querySelectorAll('.activity-run')].pop();
          const questionBoundary = !!(question.compareDocumentPosition(lastSlot) & Node.DOCUMENT_POSITION_FOLLOWING);
          render([{kind:'tool_use',name:'Bash',id:'activity-failure',detail:'Run failing check',tool_status:'failed'}]);
          render([{kind:'thinking',text:'Investigating the failed check.'}]);
          const failurePreserved = lastSlot.classList.contains('has-error')
            && lastSlot.querySelector('.activity-run-preview').textContent.startsWith('Failed: ');
          const overflow = view.scrollWidth > view.clientWidth + 1;
          document.body.dataset.convView = 'verbose';
          const verbosePreserved = [...view.querySelectorAll('.activity-run')].every(el=>!el.checkVisibility())
            && [...view.querySelectorAll('.activity-run-source')].every(el=>el.checkVisibility());
          document.body.dataset.convView = 'normal';
          return {firstStable,commandEllipsis,longStable,oneRun,newestCommand,thoughtStable,latestThought,collapsedSources,historyVisible,
            commandsRetained,idempotent,replyBoundary,questionVisible,questionBoundary,failurePreserved,verbosePreserved,overflow};
        },{merged});
        assert.deepEqual(result,{firstStable:true,commandEllipsis:true,longStable:true,oneRun:true,newestCommand:true,thoughtStable:true,latestThought:true,
          collapsedSources:true,historyVisible:true,commandsRetained:true,idempotent:true,replyBoundary:true,
          questionVisible:true,questionBoundary:true,failurePreserved:true,verbosePreserved:true,overflow:false},JSON.stringify({width,merged}));
        await page.focus('.activity-run-toggle');
        await page.keyboard.press('Enter');
        assert.equal(await page.$eval('.activity-run-toggle',el=>el.getAttribute('aria-expanded')),'true');
        await page.keyboard.press('Space');
        assert.equal(await page.$eval('.activity-run-toggle',el=>el.getAttribute('aria-expanded')),'false');
        assert.equal(await page.$eval('.activity-run-toggle',el=>{
          const r=el.getBoundingClientRect();
          return r.left>=0 && r.top>=0 && r.right<=innerWidth && r.bottom<=innerHeight
            && el.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));
        }),true,'the heading is visible and reachable in the actual viewport');
        if (process.env.CCC_THINKING_SCREENSHOTS) await page.screenshot({path:process.env.CCC_THINKING_SCREENSHOTS + '/activity-' + width + '-' + merged + '.png'});
      }
    }
    for (const width of [1280, 390]) {
      await page.setViewport({width, height:850});
      await page.evaluate(() => window.cccMobileNav.show());
      await page.waitForFunction(() => !document.querySelector('.main').getAnimations().some(a => a.playState === 'running'));
      for (const merged of [false, true, 'codex']) for (const narration of [false, true]) {
        const result = await page.evaluate(({merged, narration}) => {
          const h = window.thinkingTest, paneId = h.activePaneId(), view = h.getConvViewForPane(paneId);
          const pane = view.closest('.conv-pane');
          pane.classList.toggle('is-webui-session', !!merged);
          pane.classList.toggle('is-codex-session', merged === 'codex');
          document.body.classList.add('conv-compact');
          document.body.dataset.convView = 'compact';
          view.replaceChildren();
          let line = 990000;
          const render = block => h.renderConversationEvents([{type:'assistant',line:++line,blocks:[block]}], paneId, {initialLoad:true});
          const tool = (id, detail) => render({kind:'tool_use',name:'Bash',id,detail,command:'wt find SAMPLE-1'});
          if (narration) render({kind:'text',text:'Checking the report.'});
          const findPreview = () => view.querySelector('.cc-run-preview.is-tool-preview');
          tool('compact-first','Read the report');
          const first = findPreview()?.textContent;
          tool('compact-second','Look up the OAuth project owner…');
          const preview = findPreview();
          const before = preview?.getBoundingClientRect();
          const second = preview?.textContent;
          tool('compact-third','Check the latest entries ' + 'and compare timestamps '.repeat(20));
          const latest = findPreview();
          const after = latest?.getBoundingClientRect();
          const stable = !!before && !!after && Math.abs(before.top - after.top) < 1
            && Math.abs(before.height - after.height) < 1 && after.height > 0;
          const description = latest?.textContent.startsWith('Check the latest entries ');
          const newestOnly = view.querySelectorAll('.cc-run-preview.is-tool-preview').length === 1;
          const overflow = view.scrollWidth > view.clientWidth + 1;
          const chip = view.querySelector('.cc-run-chip');
          const group = latest?.parentElement;
          const disclosure = group?.querySelector('.tool-call-group-header, .kimi-tool-group-head');
          const oneHeading = !chip || !disclosure?.matches('.tool-call-group-header') || !disclosure.checkVisibility();
          (chip || disclosure)?.click();
          const expanded = !latest?.checkVisibility()
            && [...view.querySelectorAll('.tool-call, .kimi-tool')].every(row=>row.checkVisibility());
          (chip || group?.querySelector('.cc-run-fold') || disclosure)?.click();
          const collapsed = latest?.checkVisibility();
          document.body.classList.remove('conv-compact');
          document.body.dataset.convView = 'normal';
          const normal = !latest?.checkVisibility() && view.querySelector('.activity-run-preview')?.checkVisibility();
          document.body.classList.add('conv-compact');
          document.body.dataset.convView = 'compact';
          return {first,second,stable,description,newestOnly,oneHeading,overflow,expanded,collapsed,normal};
        }, {merged, narration});
        assert.deepEqual(result,{first:'Read the report...',second:'Look up the OAuth project owner...',stable:true,
          description:true,newestOnly:true,oneHeading:true,overflow:false,expanded:true,collapsed:true,normal:true},
          JSON.stringify({width,merged,narration}));
        if (process.env.CCC_THINKING_SCREENSHOTS && narration) {
          await page.screenshot({path:process.env.CCC_THINKING_SCREENSHOTS + '/compact-activity-' + width + '-' + merged + '.png'});
        }
      }
    }
  } finally { await browser.close(); }
});
