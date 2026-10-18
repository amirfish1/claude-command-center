// Run with: node tests/throughput_delayed_rankings.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const html = fs.readFileSync(require('node:path').join(__dirname, '../static/throughput.html'), 'utf8');
const rankingsStart = html.indexOf('    async function fetchWeekRankings(');
const rankings = html.slice(rankingsStart, html.indexOf('    async function loadSessions(', rankingsStart));
const tableStart = html.indexOf('    function escapeThroughputText(');
const table = html.slice(tableStart, html.indexOf('    function modelBreakdownContext(', tableStart));
const findStart = html.indexOf('    function findSessionById(');
const findSession = html.slice(findStart, html.indexOf('    // Render session items', findStart));
(async () => {
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setContent('<section id="top-sessions-section"><span id="top-sessions-title"></span><div id="top-sessions-table"></div></section>');
    await page.addScriptTag({content: `
      let activeThroughputEngine = 'codex', activeThroughputRange = '7d', activeSessionId = 'all_7_days', weekRankings = {};
      const aggregateSessions = [{session_id:'all_7_days', aggregate:true}];
      const allSessions = [{session_id:'example-session', display_name:'Example task'}];
      const THROUGHPUT_RANGE_LABELS = {'7d':'Last 7 Days'};
      const throughputRangeSeconds = () => 604800;
      const renderSessionList = () => {}, selectSession = () => {};
      const fmtNum = n => String(n), fmtCost = n => '$' + n.toFixed(2);
      const hideAuxiliarySection = id => document.getElementById(id).style.display = 'none';
      let resolveRankings;
      window.fetch = () => new Promise(resolve => { resolveRankings = () => resolve({ok:true,json:async()=>({ok:true,sessions:[{session_id:'example-session',cache_adjusted_tokens:1000,cost_usd:2,turns:7,models:['gpt-test']}]})}); });
      ${findSession}
      ${table}
      ${rankings}
      renderTopSessions();
      window.pendingRankings = fetchWeekRankings();
    `});
    assert.equal(await page.$eval('#top-sessions-section', el => el.style.display), 'none');
    await page.evaluate(async () => {resolveRankings(); await window.pendingRankings;});
    assert.equal(await page.$eval('#top-sessions-section', el => el.style.display), '');
    assert.match(await page.$eval('#top-sessions-table', el => el.textContent), /Example task/);
    assert.match(await page.$eval('#top-sessions-table', el => el.textContent), /7 calls/);
    // A later response while viewing an individual session must not reveal aggregate tables.
    await page.evaluate(async () => {
      window.pendingRankings = fetchWeekRankings();
      activeSessionId = 'example-session';
      document.getElementById('top-sessions-section').style.display = 'none';
      resolveRankings(); await window.pendingRankings;
    });
    assert.equal(await page.$eval('#top-sessions-section', el => el.style.display), 'none');
    console.log('PASS: delayed rankings reveal aggregate sessions and respect individual-session selection');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
