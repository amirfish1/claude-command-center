const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const editor = app.slice(app.indexOf('  function openRowDecisionReportEditor('), app.indexOf('  function sessionQuickAction('));

test('row report note supports cancel, retry, original evidence and desktop/phone input', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 800 });
    await page.setContent(`<style>:root{--surface:#161b22;--surface-2:#21262d;--text:#e6edf3;--text-muted:#9da7b3;--bg:#0d1117;--border:#484f58;--accent:#80bfff;--red:#ff7b72} ${css}</style>
      <div class="conv-item" data-session-id="test-report" style="position:fixed;right:12px;top:30px">
        <span id="rowText">Waiting for approval</span>
        <button type="button" data-role="report-decision" aria-expanded="false">Report</button>
      </div>`);
    await page.addScriptTag({ content: `
      const _rowDecisionReportSending=new Set(); let _rowDecisionReportEditor=null;
      window.requests=[]; window.failReport=false; window.toasts=[]; window.captureCount=0;
      const _ultraFreshRow=()=>({session_id:'test-report'});
      const rowDecisionReportSnapshot=(row,c)=>({session_id:c.session_id,visible_row:{text:document.querySelector('#rowText').textContent},rect:{x:1,y:2,width:3,height:4}});
      const captureRowDecisionScreenshot=async()=>{captureCount++;return 'captured image'};
      const showOpToast=(...args)=>toasts.push(args);
      window.fetch=async(url,opts)=>{requests.push({url,payload:JSON.parse(opts.body)});return {ok:!failReport,json:async()=>failReport?{error:'Queue unavailable. Try again.'}:{ok:true,ref:'DECISIONS-FIX-TEST'}}};
      ${editor}
    ` });
    const trigger = '[data-role="report-decision"]';
    await page.click(trigger);
    assert.equal(await page.evaluate(() => requests.length), 0, 'opening does not file a ticket');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'rowReportNote');
    await page.type('#rowReportNote', 'The approval button is missing.');
    await page.screenshot({ path: '/tmp/row-report-note-desktop.png' });
    await page.click('[data-report-cancel]');
    assert.equal(await page.$('.conv-report-editor'), null);
    assert.equal(await page.evaluate(() => requests.length), 0);
    assert.equal(await page.$eval(trigger, el => el.getAttribute('aria-expanded')), 'false');
    await page.click(trigger);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('.conv-report-editor'));
    assert.equal(await page.evaluate(() => requests.length), 0);

    await page.click(trigger);
    await page.type('#rowReportNote', '  The approval button is missing.  ');
    await page.evaluate(() => { failReport=true; document.querySelector('#rowText').textContent='Newer row'; });
    await page.click('.conv-report-editor [type="submit"]');
    await page.waitForFunction(() => !document.querySelector('.conv-report-error').hidden);
    assert.equal(await page.$eval('#rowReportNote', el => el.value), '  The approval button is missing.  ');
    assert.match(await page.$eval('.conv-report-error', el => el.textContent), /Queue unavailable/);
    await page.evaluate(() => { failReport=false; });
    await page.click('.conv-report-editor [type="submit"]');
    await page.waitForFunction(() => !document.querySelector('.conv-report-editor'));
    const sent = await page.evaluate(() => requests.at(-1));
    assert.equal(sent.url, '/api/decisions-fix/report');
    assert.equal(sent.payload.reporter_note, 'The approval button is missing.');
    assert.equal(sent.payload.visible_row.text, 'Waiting for approval', 'capture is from the original click');
    assert.equal(sent.payload.screenshot_b64, 'captured image');

    await page.setViewport({ width: 390, height: 844 });
    await page.click(trigger);
    const box = await page.$eval('.conv-report-editor', el => {
      const r=el.getBoundingClientRect(); return {left:r.left,right:r.right,top:r.top,bottom:r.bottom};
    });
    assert.ok(box.left>=0 && box.right<=390 && box.top>=0 && box.bottom<=844);
    assert.equal(await page.$eval('#rowReportNote', el => el.maxLength), 2000);
    await page.type('#rowReportNote', 'Wrong status on phone.');
    await page.screenshot({ path: '/tmp/row-report-note-phone.png' });
    await page.click('.conv-report-editor [type="submit"]');
    await page.waitForFunction(() => !document.querySelector('.conv-report-editor'));
    assert.equal(await page.evaluate(() => requests.at(-1).payload.reporter_note), 'Wrong status on phone.');
    await page.click(trigger);
    await page.click('.conv-report-editor [type="submit"]');
    await page.waitForFunction(() => !document.querySelector('.conv-report-editor'));
    assert.equal(await page.evaluate(() => requests.at(-1).payload.reporter_note), '', 'description remains optional');
  } finally {
    await browser.close();
  }
});
