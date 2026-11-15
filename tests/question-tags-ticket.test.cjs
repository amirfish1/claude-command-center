const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');

const question = (n, open) => ({n, tag: `[Q${n}:repair]`, slug: 'repair', answered: !open, mentions: [],
  first: {sid: 'parent', ts: '2026-01-01T00:00:00Z', uuid: 'ask', text: `[Q${n}:repair] Approve the plan?`},
  ticket: {ref: 'OPS-7', title: 'Repair', open, session_id: 'worker'}});

async function load(browser, questions, html) {
  const page = await browser.newPage();
  await page.setContent(html);
  await page.evaluate(list => {
    window.opened = []; window.focused = []; window.sent = [];
    window.fetch = async () => ({json: async () => ({ok: true, questions: window.list})});
    window.list = list;
    window.cccOpenTicket = ref => { opened.push(ref); return true; };
    window.cccFocusComposer = async sid => { focused.push(sid); return true; };
    window.cccSendQuestionAnswer = async (sid, text) => { sent.push(text); return {ok: true}; };
  }, questions);
  await page.addScriptTag({path: 'static/question-tags.js'});
  await page.waitForFunction(() => document.querySelector('.qtag-chip')?.title.includes('open ticket'));
  return page;
}

const needsYou = '<div class="session-state-block"><div class="ssb-row ssb-next"><span class="ssb-key">Needs you</span>'
  + 'Answer [Q9:repair]: approve the plan. <span class="ssb-actions"><button class="ssb-action">Approve plan</button></span></div></div>';

test('a ticket question goes by its ticket, and its chip opens the ticket', async () => {
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    const page = await load(browser, [question(9, true)], needsYou);
    assert.equal(await page.$eval('.qtag-chip', c => c.textContent), 'OPS-7');
    assert.match(await page.$eval('.qtag-chip', c => c.title), /\[Q9:repair\]/);
    await page.click('.qtag-chip');
    assert.deepEqual(await page.evaluate(() => opened), ['OPS-7']);
    assert.equal(await page.$('.qtag-sheet:not([hidden])'), null);
  } finally { await browser.close(); }
});

test('a chip right after its ticket ref names the question, not the ticket again', async () => {
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    const page = await load(browser, [question(9, true)],
      '<p class="assistant-text"><a>OPS-7</a> stays blocked on [Q9:repair].</p>');
    assert.equal(await page.$eval('.assistant-text', p => p.textContent), 'OPS-7 stays blocked on open question.');
  } finally { await browser.close(); }
});

test('Talk to worker focuses the worker session and sends nothing', async () => {
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    const page = await load(browser, [question(9, true)], needsYou);
    await page.waitForSelector('.ssb-actions [data-qtalk]');
    assert.deepEqual(await page.$$eval('.ssb-actions button', b => b.map(x => x.textContent)), ['Approve plan', 'Talk to worker']);
    await page.click('[data-qtalk]');
    assert.deepEqual(await page.evaluate(() => [focused, sent]), [['worker'], []]);
  } finally { await browser.close(); }
});

test('Talk to worker leaves once the ticket stops waiting, and never appears for a past question', async () => {
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    const page = await load(browser, [question(9, false)], needsYou);
    assert.equal(await page.$('[data-qtalk]'), null);
    const live = await load(browser, [question(9, true)], needsYou);
    await live.waitForSelector('[data-qtalk]');
    await live.evaluate(() => { window.list = window.list.map(q => ({...q, ticket: {...q.ticket, open: false}})); });
    await live.evaluate(() => { document.querySelector('.ssb-next').append(document.createElement('i')); });
    await live.evaluate(() => window.cccQuestionTags.open(9, 'parent'));
    await live.waitForFunction(() => !document.querySelector('.ssb-next [data-qtalk]'));
  } finally { await browser.close(); }
});

test('the question sheet leads to the ticket instead of a chat answer', async () => {
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    const page = await load(browser, [question(9, true)], needsYou);
    await page.evaluate(() => window.cccQuestionTags.open(9, 'parent'));
    await page.waitForSelector('.qtag-sheet:not([hidden]) [data-qticket]');
    assert.equal(await page.$('[data-qsend]'), null);
    assert.equal(await page.$eval('.qtag-head strong', h => h.textContent), 'OPS-7');
    await page.click('.qtag-sheet [data-qtalk]');
    assert.deepEqual(await page.evaluate(() => focused), ['worker']);
    assert.notEqual(await page.$('.qtag-sheet[hidden]'), null);
  } finally { await browser.close(); }
});

test('a reply chip answers the waiting ticket instead of the chat', async () => {
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    const page = await load(browser, [question(9, true)], needsYou);
    await page.waitForSelector('[data-qtalk]');
    const result = await page.evaluate(async () => {
      const posts = [], plain = window.fetch;
      window.fetch = async (url, options) => {
        if (url !== '/api/ux-fixes/answer') return plain(url, options);
        posts.push(JSON.parse(options.body));
        return {ok: true, json: async () => ({ok: true})};
      };
      const ref = await window.cccQuestionTags.answerTicket('[Q9:repair] yes, approved.');
      return {ref, posts, again: window.cccQuestionTags.answerTicket('[Q9:repair] yes, approved.')};
    });
    assert.equal(result.ref, 'OPS-7');
    assert.deepEqual(result.posts, [{ref: 'OPS-7', text: '[Q9:repair] yes, approved.'}]);
    assert.equal(result.again, null, 'an answered ticket falls back to chat');
    await page.waitForFunction(() => !document.querySelector('[data-qtalk]'));
  } finally { await browser.close(); }
});

test('other replies stay chat messages, and a refused answer is reported', async () => {
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    const page = await load(browser, [question(9, true)], needsYou);
    assert.equal(await page.evaluate(() => window.cccQuestionTags.answerTicket('Keep going.')), null);
    assert.equal(await page.evaluate(() => window.cccQuestionTags.answerTicket('[Q10:other] Yes.')), null);
    const error = await page.evaluate(async () => {
      window.fetch = async () => ({ok: false, status: 400, json: async () => ({error: 'not blocked'})});
      return window.cccQuestionTags.answerTicket('[Q9] Yes.').catch(e => e.message);
    });
    assert.equal(error, 'not blocked');
    await page.waitForSelector('[data-qtalk]');
  } finally { await browser.close(); }
});

test('a cross-session answer sends one receipt to its origin after the ticket accepts it', {timeout:15000}, async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page = await load(browser,[question(9,true)],needsYou);
    const result = await page.evaluate(async () => {
      const posts=[], notices=[];
      window.fetch=async (url,options) => {
        if(url==='/api/ux-fixes/answer')posts.push(JSON.parse(options.body));
        return {ok:true,json:async()=>({ok:true})};
      };
      let finish;
      window.cccSendQuestionAnswer=async (...args) => {
        notices.push(args);
        await new Promise(resolve=>finish=resolve);
        return {ok:true};
      };
      const first=cccQuestionTags.answerTicket('[Q9:repair] Approve only the repair.','parent');
      while(!finish)await new Promise(resolve=>setTimeout(resolve,0));
      const second=cccQuestionTags.answerTicket('[Q9:repair] Approve only the repair.','parent');
      const shared=first===second;
      finish();
      return {ref:await first,again:await second,shared,posts,notices};
    });
    assert.equal(result.ref,'OPS-7');
    assert.equal(result.shared,true,'a repeat while the origin receipt is pending must not fall back to another chat send');
    assert.equal(result.again,'OPS-7');
    assert.equal(result.posts.length,1);
    assert.equal(result.notices.length,1);
    assert.equal(result.notices[0][0],'parent');
    assert.match(result.notices[0][1],/^Decision recorded on OPS-7:\n\n\[Q9:repair\] Approve only the repair\./);
    assert.match(result.notices[0][1],/do not submit the answer again/);
    assert.deepEqual(result.notices[0][2],{echo:true});
  } finally {await browser.close();}
});

test('the worker session is already notified by the ticket endpoint and is not injected twice', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page=await load(browser,[question(9,true)],needsYou);
    await page.evaluate(async () => {
      window.fetch=async()=>({ok:true,json:async()=>({ok:true})});
      await cccQuestionTags.answerTicket('[Q9] Yes.','worker');
    });
    assert.deepEqual(await page.evaluate(()=>sent),[]);
  } finally {await browser.close();}
});

test('an origin receipt failure preserves the recorded answer and reports which delivery failed', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page=await load(browser,[question(9,true)],needsYou);
    const result=await page.evaluate(async () => {
      let answers=0;
      window.fetch=async url=>{
        if(url==='/api/ux-fixes/answer')answers++;
        return {ok:true,json:async()=>({ok:true})};
      };
      window.cccSendQuestionAnswer=async()=>({ok:false,error:'Origin offline'});
      const error=await cccQuestionTags.answerTicket('[Q9] Yes.','parent').catch(e=>({message:e.message,ticketAnswered:e.ticketAnswered}));
      return {error,answers,again:cccQuestionTags.answerTicket('[Q9] Yes.','parent')};
    });
    assert.equal(result.error.ticketAnswered,true);
    assert.match(result.error.message,/OPS-7 was answered.*receipt was not confirmed: Origin offline/);
    assert.equal(result.answers,1);
    assert.equal(result.again,null);
  } finally {await browser.close();}
});
