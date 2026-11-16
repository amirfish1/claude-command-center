const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = (process.env.ROW_REPORT_APP ? fs.readFileSync(process.env.ROW_REPORT_APP, 'utf8') : require('./legacy-dashboard-source.cjs'));
const css = fs.readFileSync(process.env.ROW_REPORT_CSS || require.resolve('../static/app.css'), 'utf8');
const taps = app.slice(app.indexOf('  const CONVERSATION_ROW_ACTION_SELECTOR ='),
  app.indexOf('  function wireConversationListScrollPause('));
const button = app.slice(app.indexOf('  function rowDecisionReportButton('),
  app.indexOf('  function rowDecisionReportSnapshot('));
const editor = app.slice(app.indexOf('  function openRowDecisionReportEditor('),
  app.indexOf('  function sessionQuickAction('));

test('touching the row report icon or target edge opens its editor without opening the session', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true });
    await page.setContent(`<meta name="viewport" content="width=device-width, initial-scale=1"><style>:root{--surface:#161b22;--surface-2:#21262d;--text:#e6edf3;--text-muted:#9da7b3;--bg:#0d1117;--border:#484f58;--accent:#80bfff}body{margin:0;background:var(--bg)}${css}</style>
      <div id="convList" style="position:fixed;inset:40px 0 auto;height:300px">
      <div class="conv-item" data-id="test-row" data-session-id="test-row"><div class="wa-row">
      <div class="wa-avatar">C</div><div class="wa-body"><div class="wa-line1"><span class="wa-title">Check the session result</span>
      <span id="reportSlot"></span><span class="wa-time">2m</span></div>
      <div class="wa-line2"><span class="wa-preview">Waiting for your review</span></div></div></div></div></div>`);
    await page.addScriptTag({ content: `
      const $convList=document.querySelector('#convList');
      const selectedListIds=new Set(); const isMobile=()=>true; const shouldUseTouchRowMode=()=>true;
      const trackedTableOpen=()=>false; const paneByPaneId=()=>null; const activePaneId=()=>'';
      const clearSelectedConversationRows=()=>{};
      window.opened=[]; window.sent=[];
      const selectConversation=id=>opened.push(id);
      const _rowDecisionReportSending=new Set(); let _rowDecisionReportEditor=null;
      const _ultraFreshRow=()=>({session_id:'test-row'});
      const rowDecisionReportSnapshot=()=>({session_id:'test-row',rect:{}});
      const captureRowDecisionScreenshot=async()=>null;
      const showOpToast=()=>{};
      window.fetch=async(url,opts)=>{sent.push({url,payload:JSON.parse(opts.body)});return {ok:true,json:async()=>({ok:true,ref:'TEST-1'})};};
      ${button}
      document.querySelector('#reportSlot').outerHTML=rowDecisionReportButton({session_id:'test-row'});
      ${editor}
      ${taps}
      document.querySelector('.conv-item').addEventListener('click',ev=>{
        if (!shouldSuppressSyntheticRowClick(ev.currentTarget.dataset.id)) activateConversationRowFromTap(ev.currentTarget,ev);
      });
    ` });
    const trigger = '[data-role="report-decision"]';
    const box = await page.$eval(trigger, el => {
      const r=el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height};
    });
    await page.touchscreen.tap(box.x+box.width/2, box.y+box.height/2);
    assert.deepEqual(await page.evaluate(() => opened), [], 'the touch pointerup must not open the session');
    await page.waitForSelector('.conv-report-editor');
    assert.equal(await page.evaluate(() => sent.length), 0, 'opening the editor sends nothing');
    await page.type('#rowReportNote', 'Wrong status on this row.');
    if (process.env.ROW_REPORT_SCREENSHOT) await page.screenshot({path:process.env.ROW_REPORT_SCREENSHOT});
    await page.click('.conv-report-editor [type="submit"]');
    await page.waitForFunction(() => !document.querySelector('.conv-report-editor'));
    assert.equal(await page.evaluate(() => sent[0].payload.reporter_note), 'Wrong status on this row.');
    assert.ok(box.width >= 44 && box.height >= 44, 'phone report target is at least 44 by 44 CSS pixels');
    await page.touchscreen.tap(box.x+2, box.y+2);
    await page.waitForSelector('.conv-report-editor');
    await page.click('[data-report-cancel]');
    assert.deepEqual(await page.evaluate(() => opened), [], 'target-edge tap must not open the session');
    assert.equal(await page.evaluate(() => sent.length), 1, 'cancel sends nothing');
    await page.touchscreen.tap(140, box.y+20);
    assert.deepEqual(await page.evaluate(() => opened), ['test-row'], 'the rest of the row still opens normally');
  } finally { await browser.close(); }
});
