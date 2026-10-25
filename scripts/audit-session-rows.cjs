#!/usr/bin/env node
/* Opt-in, read-only browser audit; only --file writes deduplicated WT tickets. */
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const audit = require('../static/row-audit.js');
const digest = value => crypto.createHash('sha256').update(value).digest('hex').slice(0, 20);
function incident(result, sid) {
  if (result.findings.some(x => x.kind === 'pending') || !result.source.answer) return null;
  const failures = result.findings.filter(x => x.code);
  if (!failures.length) return null;
  return {sid, turn: digest(result.source.answer), failures};
}
async function main() {
  const args = process.argv.slice(2);
  const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
  const home = path.join(os.homedir(), '.claude', 'command-center');
  const base = option('--url', null) || fs.readFileSync(path.join(home, 'port.txt'), 'utf8').trim();
  const queue = option('--queue', 'CCC');
  const out = option('--output', path.join(home, 'row-audit'));
  fs.mkdirSync(out, {recursive:true});
  const report = {started:new Date().toISOString(), url:base, queue, scope:'Features and Small bugs, last 7 days, desktop and mobile', checked:[], incidents:[], errors:[]};
  const get = async endpoint => {
    const response = await fetch(new URL(endpoint, base), {signal:AbortSignal.timeout(30000)});
    if (!response.ok) throw Error(`${endpoint}: HTTP ${response.status}`);
    return response.json();
  };
  const sessions = new Map((await get('/api/conversations/all?stale_ok=1')).conversations.map(row => [row.session_id,row]));
  const puppeteer = require('../require-puppeteer');
  const {findChromePath} = require('../puppeteer-browser-config');
  const browser = await puppeteer.launch({headless:true, executablePath:findChromePath(), args:['--no-sandbox']});
  const incidents = new Map();
  try {
    for (const viewport of [{name:'desktop',width:1280,height:900},{name:'mobile',width:390,height:844}]) {
      const page = await browser.newPage();
      await page.setViewport(viewport);
      await page.evaluateOnNewDocument(() => {localStorage.setItem('ccc-pwa-install-dismissed','1'); localStorage.setItem('ccc-tour-done','skipped'); localStorage.setItem('ccc-onboarded','1'); localStorage.setItem('ccc-engines-first-run-done','1'); localStorage.setItem('ccc-archive-window','7d'); localStorage.setItem('ccc-archive-engine-filter','');});
      await page.goto(base, {waitUntil:'domcontentloaded',timeout:60000});
      await page.waitForSelector('[data-conv-tab="ideas"]',{timeout:60000});
      await page.evaluate(() => {for (const id of ['etsLater','updLaterBtn']) document.getElementById(id)?.click();});
      for (const tab of ['ideas','smallbugs']) {
        await page.$eval(`[data-conv-tab="${tab}"]`, el => el.click());
        await new Promise(resolve => setTimeout(resolve, 1800));
        const ids = await page.$$eval('#convList .conv-item[data-session-id]', els => [...new Set(els.filter(el => el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0).map(el => el.dataset.sessionId))]);
        console.log(`${viewport.name}/${tab}: ${ids.length} rendered rows`);
        for (const sid of ids) {
          const row = sessions.get(sid);
          if (!row || row.archived || row.trashed) continue;
          try {
            const selector = `#convList .conv-item[data-session-id="${sid}"]`;
            await page.$eval(selector, el => el.scrollIntoView({block:'center'}));
            await new Promise(resolve => setTimeout(resolve, 1500));
            const rendered = await page.$eval(selector, el => {
              const visible = e => !!e && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().height > 0 && getComputedStyle(e).visibility !== 'hidden';
              const first = query => [...el.querySelectorAll(query)].find(visible);
              const action = first('.conv-quick-action');
              const key = action?.dataset.quickActionKey;
              let reply = '';try {reply=JSON.parse(key)[2].reply;} catch (_) {}
              return {checked:true, close:!!first('.conv-ready-close'), questions:!!first('.qtag-chip'),
                working:!!first('.conv-working-dot,.wa-dot'),
                action:action ? {label:action.textContent.trim(),reply} : null};
            });
            const data = await get(`/api/conversations/${encodeURIComponent(sid)}?tail=80`);
            const result = audit.inspect(row, data.events || [], rendered);
            report.checked.push({sid,tab,viewport:viewport.name,status:result.status,findings:result.findings});
            const issue = incident(result,sid);
            if (issue) {
              const entry = incidents.get(sid) || {...issue,title:row.ai_title || row.display_name || sid, views:[]};
              entry.views.push({viewport:viewport.name, failures:issue.failures, rendered});
              incidents.set(sid,entry);
              const screenshot = path.join(out, `${sid}-${viewport.name}.png`);
              await page.screenshot({path:screenshot});
            }
          } catch (error) {report.errors.push({sid,viewport:viewport.name,error:String(error)});}
        }
      }
      await page.close();
    }
  } finally {await browser.close();}
  if (!report.checked.length) report.errors.push({error:'No session rows were inspected; audit coverage is unavailable.'});
  // Recheck the source before ticketing: a new turn supersedes the observation.
  const current = new Map((await get('/api/conversations/all?stale_ok=1')).conversations.map(row => [row.session_id,row]));
  for (const entry of incidents.values()) {
    const row = current.get(entry.sid);
    const data = await get(`/api/conversations/${entry.sid}?tail=80`);
    if (!row || ['working','running'].includes(row.state) || row.codex_state === 'working' || row.pending_spawn) continue;
    const source = audit.latest(data.events || []);
    if (source.userAfter || digest(source.answer) !== entry.turn) continue;
    const key = `ccc-row-audit:${digest(os.hostname())}:${entry.sid}:${entry.turn}`;
    const text = `Hourly rendered-row audit found an explicitly finished turn without its expected control.\n\nSession: ${entry.sid}\nURL: ${base}\nTurn digest: ${entry.turn}\nObserved: ${new Date().toISOString()}\n\n${entry.views.map(view => `${view.viewport}: ${view.failures.map(f => f.text).join(' ')}`).join('\n')}\n\nEvidence screenshots: ${out}/${entry.sid}-<viewport>.png\nVerify the current transcript and rendered controls before repairing. Active or newer-request turns were excluded; completion claims are self-reported.`;
    let ticket = null;
    if (args.includes('--file')) ticket = execFileSync('wt',['add','-q',queue,'--title',`Session row missing control: ${entry.title}`.slice(0,150),'--text',text,'--type','bug','--dedupe-key',key],{encoding:'utf8',timeout:30000}).trim();
    report.incidents.push({...entry,dedupeKey:key,ticket});
  }
  report.finished = new Date().toISOString();
  fs.writeFileSync(path.join(out,'last-run.json'), JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({checked:report.checked.length,incidents:report.incidents.length,errors:report.errors.length,report:path.join(out,'last-run.json')}));
  if (report.errors.length) process.exitCode=1;
}
module.exports = {incident,digest};
if (require.main === module) main().catch(error => {console.error(error); process.exitCode=1;});
