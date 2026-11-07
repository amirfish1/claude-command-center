// The floating pill counts what needs the owner now: the Decisions count,
// never unanswered transcript tags. Clicking it opens Decisions.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const qt = require('../static/question-tags.js');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');

const NOW = Date.parse('2026-11-06T12:00:00Z');
const q = (n, extra) => Object.assign({n, tag: '[Q' + n + ':s' + n + ']', slug: 's' + n, answered: false, mentions: [],
  first: {sid: 'sid' + n, ts: '2026-10-01T00:00:00Z', uuid: 'u' + n, text: '- [Q' + n + ':s' + n + '] Should we do thing ' + n + '?'}}, extra);
const row = (origin, body, extra) => Object.assign({adapter: 'watchtower-block', origin_id: origin, aliases: [], body, needs_answer: true}, extra);

test('classify: queue / open ticket / recent / history, nothing dropped', () => {
  const rows = [row('WT-1', '[Q1:s1] Pick one?'), row('GH-9', 'no tag here')];
  const list = [
    q(1),                                                              // in the queue by tag
    q(2, {ticket: {ref: 'GH-9', open: true}}),                         // in the queue by ticket ref
    q(3, {ticket: {ref: 'WT-3', open: true}}),                         // open ticket that is not an owner wait
    q(4, {first: {sid: 's', ts: '2026-11-06T04:00:00Z', uuid: 'x', text: '[Q4:s4] now?'}}),  // recent
    q(5, {mentions: [{sid: 's', ts: '2026-11-06T09:00:00Z', uuid: 'y'}]}),                   // old, but reminded today
    q(6),                                                              // history
    q(7, {answered: true}),
  ];
  const g = qt.groups(list, rows, NOW);
  assert.deepEqual(Object.fromEntries(Object.entries(g).map(([k, v]) => [k, v.map(x => x.n)])),
    {queue: [1, 2], ticket: [3], recent: [4, 5], history: [6]});
  assert.equal(qt.classify(list[6], qt.queueKeys(rows), NOW), 'answered');
});

test('plain strips tag syntax and markdown', () => {
  assert.equal(qt.plain('- [Q9:gap-month] **If** a member `skips`'), 'If a member skips');
});

async function load(browser, {questions, decision}) {
  const page = await browser.newPage();
  await page.setViewport({width: 1200, height: 800});
  await page.setContent('<style>' + fs.readFileSync('static/question-tags.css', 'utf8') + '</style>'
    + '<button data-conv-tab="decisions2" id="d2" onclick="window.d2clicks=(window.d2clicks||0)+1">Decisions</button>');
  await page.evaluate((questions, decision) => {
    window.decision = decision;
    window.fetch = async (url) => ({json: async () => String(url).includes('decision-inbox')
      ? window.decision : {ok: true, questions}});
  }, questions, decision);
  await page.addScriptTag({path: 'static/question-tags.js'});
  return page;
}
const pillText = (page) => page.$eval('#qtagPill', p => p.hidden ? 'hidden' : p.textContent);

test('pill shows the Decisions count, not the tag backlog, and tracks it while it refreshes', async () => {
  const stale = Array.from({length: 295}, (_, i) => q(i + 1));
  const decision = {ok: true, source_current: true, needs_answer_count: 54,
    requests: Array.from({length: 54}, (_, i) => row('WT-' + i, 'ask ' + i))};
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    const page = await load(browser, {questions: stale, decision});
    await page.waitForFunction(() => document.getElementById('qtagPill') && !document.getElementById('qtagPill').hidden);
    assert.equal(await pillText(page), '? 54', 'not 295');
    assert.equal(await page.evaluate(() => window.cccQuestionTags._pillCount()), 54);

    // Decisions reports a new count: the pill follows at once.
    await page.evaluate(() => { window.decision = {ok: true, source_current: true, needs_answer_count: 53, requests: []};
      window.dispatchEvent(new CustomEvent('ccc:decision-count', {detail: {count: 53}})); });
    assert.equal(await pillText(page), '? 53');
    await page.waitForFunction(() => window.cccQuestionTags._pillCount() === 53);

    // An unverified source (no count) never replaces the last verified number.
    await page.evaluate(() => { window.decision = {ok: true, source_current: false, needs_answer_count: null, requests: []};
      window.dispatchEvent(new CustomEvent('ccc:decision-count', {detail: {count: null}})); });
    await new Promise(r => setTimeout(r, 300));
    assert.equal(await pillText(page), '? 53');

    // Zero hides the pill.
    await page.evaluate(() => { window.decision = {ok: true, source_current: true, needs_answer_count: 0, requests: []};
      window.dispatchEvent(new CustomEvent('ccc:decision-count', {detail: {count: 0}})); });
    assert.equal(await pillText(page), 'hidden');
  } finally { await browser.close(); }
});

test('clicking the pill opens Decisions; history is separate, labelled, and loses nothing', async () => {
  const questions = [
    q(1), // queue
    q(3, {ticket: {ref: 'WT-3', title: 'Open ticket question', open: true}}),
    q(4, {first: {sid: 's', ts: new Date().toISOString(), uuid: 'x', text: '[Q4:s4] Recent live ask?'}}),
    q(6),
  ];
  const decision = {ok: true, source_current: true, needs_answer_count: 1, requests: [row('WT-1', '[Q1:s1] Pick one?')]};
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    const page = await load(browser, {questions, decision});
    await page.waitForFunction(() => document.getElementById('qtagPill') && !document.getElementById('qtagPill').hidden);
    await page.click('#qtagPill');
    assert.equal(await page.evaluate(() => window.d2clicks), 1, 'click goes to Decisions');
    assert.equal(await page.$eval('.qtag-sheet', s => s ? s.hidden : true).catch(() => true), true, 'no competing list on click');

    // The history view (options menu / chip "Question history").
    await page.evaluate(() => window.cccQuestionTags.open(6, 'sid6'));
    await page.waitForSelector('.qtag-sheet:not([hidden]) [data-qlist]');
    await page.click('[data-qlist]');
    const text = await page.$eval('.qtag-card', c => c.innerText);
    assert.match(text, /Your to-do list is Decisions: 1 needs you/);
    assert.match(text, /Asked in a session, not in Decisions \(2\)/);
    assert.match(text, /Open ticket question/, 'open-ticket tag stays visible');
    assert.match(text, /Recent live ask\?/, 'recent live ask stays visible');
    assert.doesNotMatch(text, /\[Q\d+:/, 'no raw tag syntax');
    assert.equal(await page.$eval('details.qtag-old', d => d.open), false, 'old backlog collapsed');
    assert.match(await page.$eval('details.qtag-old summary', s => s.textContent), /Older tagged questions \(1\).*not a to-do list/);
    assert.doesNotMatch(text, /Pick one\?/, 'queue items are not repeated as a second to-do list');
  } finally { await browser.close(); }
});
