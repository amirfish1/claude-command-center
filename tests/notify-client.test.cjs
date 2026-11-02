'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
let browser;
let server;
let url;

test.before(async () => {
  server = http.createServer((req, res) => {
    if (req.url === '/static/notify.js' || req.url === '/static/popups.js') {
      res.writeHead(200, { 'Content-Type': 'text/javascript' });
      return res.end(fs.readFileSync(path.join(__dirname, '..', req.url)));
    }
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end('<!doctype html><script src="/static/popups.js"></script><script src="/static/notify.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  url = 'http://127.0.0.1:' + server.address().port;
  browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
});

test.after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => server.close(resolve));
});

test('notification API supports callable and object callers without bypassing approvals', async () => {
  const page = await browser.newPage();
  try {
    await page.goto(url, { waitUntil: 'load' });
    assert.equal(await page.evaluate(() => typeof window.cccNotify), 'function');
    const held = await page.evaluate(() => ({
      callable: window.cccNotify({ title: 'Held digest', kind: 'digest' }),
      object: window.cccNotify.show({ title: 'Held digest', kind: 'digest' }),
    }));
    assert.deepEqual(held, { callable: false, object: false });
    assert.equal(await page.$('.ccc-toast'), null);
    const shown = await page.evaluate(() => {
      localStorage.setItem('ccc-popups-preview', 'notify-digest');
      return window.cccNotify({ title: 'Daily report', body: 'A synthetic test.', kind: 'digest' });
    });
    assert.equal(shown, true);
    await page.waitForSelector('.ccc-toast-kind-digest');
    assert.equal(await page.$('[aria-label="Turn on notifications"]'), null);
    const legacy = await page.evaluate(() => {
      localStorage.setItem('ccc-popups-preview', 'notify-other');
      return window.cccNotify('Setup complete', 'Your team is ready.', 'success');
    });
    assert.equal(legacy, true);
    await page.waitForSelector('.ccc-toast-kind-success');
    assert.equal(await page.evaluate(() => typeof window.cccNotify._deliver), 'function');
    assert.equal(await page.evaluate(() => typeof window.cccNotify.setPref), 'function');
  } finally { await page.close(); }
});

test('notification policies preserve custom combinations and coordinate sounds', async () => {
  const page = await browser.newPage();
  try {
    await page.goto(url, {waitUntil:'load'});
    const result = await page.evaluate(() => {
      localStorage.clear();
      cccNotify.setPref('digest', false);
      const custom = cccNotify.policy();
      const permission = cccNotify.permissionState();
      const quiet = cccNotify.applyPolicy('quiet');
      const quietState = [cccNotify.enabled(), localStorage.getItem('ccc-sounds-enabled')];
      cccNotify.applyPolicy('action');
      const action = ['enabled','tasks','digest','milestones'].map(cccNotify.pref);
      const rejected = cccNotify.applyPolicy('__proto__');
      return {custom, quiet, quietState, action, rejected, policy:cccNotify.policy(),
        unchangedPermission: permission === cccNotify.permissionState()};
    });
    assert.deepEqual(result, {custom:'custom', quiet:true, quietState:[false,'0'],
      action:[true,true,false,false], rejected:false, policy:'action', unchangedPermission:true});
  } finally { await page.close(); }
});

test('a failed policy write rolls back the partial update', async () => {
  const page = await browser.newPage();
  try {
    await page.goto(url, {waitUntil:'load'});
    const result = await page.evaluate(() => {
      localStorage.clear();
      cccNotify.applyPolicy('all');
      const original = Storage.prototype.setItem;
      let fail = true;
      Storage.prototype.setItem = function(key, value) {
        if (key === 'ccc-notify-digest' && fail) { fail=false; throw new Error('Storage full'); }
        return original.call(this,key,value);
      };
      try { return {saved:cccNotify.applyPolicy('quiet'), policy:cccNotify.policy()}; }
      finally { Storage.prototype.setItem=original; }
    });
    assert.deepEqual(result,{saved:false,policy:'all'});
  } finally { await page.close(); }
});


test('desktop notification leaves the reader header focus target unobstructed', async () => {
  const page = await browser.newPage();
  try {
    await page.setViewport({width:1440,height:1000});
    await page.goto(url,{waitUntil:'load'});
    await page.evaluate(()=>{
      const details=document.createElement('details');
      details.style.cssText='position:fixed;left:1143px;top:13px';
      details.innerHTML='<summary data-role="pane-more" style="width:27px;height:26px">More</summary>';
      document.body.append(details);
      localStorage.setItem('ccc-popups-preview','notify-other');
      cccNotify({title:'Synthetic update',body:'Header remains reachable.',kind:'success'});
    });
    await page.waitForSelector('.ccc-toast');
    await page.focus('[data-role="pane-more"]');
    const result=await page.evaluate(()=>{
      const target=document.activeElement,r=target.getBoundingClientRect();
      const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
      return {hit:hit===target||target.contains(hit),toastTop:document.querySelector('.ccc-toast').getBoundingClientRect().top,bottom:r.bottom};
    });
    assert.equal(result.hit,true);assert(result.toastTop>result.bottom);
  } finally { await page.close(); }
});

for (const native of [false,true]) test('notification policies govern banner delivery and sound; native='+native,async()=>{
 const page=await browser.newPage();try{
  await page.goto(url,{waitUntil:'load'});
  const result=await page.evaluate(native=>{
   localStorage.clear();const messages=[];
   Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});
   Object.defineProperty(navigator,'serviceWorker',{configurable:true,value:undefined});
   class FakeNotification{static permission='granted';constructor(title,opts){messages.push({title,...opts});}}
   window.Notification=FakeNotification;
   if(native)window.webkit={messageHandlers:{cccNotify:{postMessage:value=>messages.push(value)}}};
   let serial=0;const send=kind=>cccNotify({id:'ntf_test_policy_'+serial++,title:kind,kind});
   cccNotify.applyPolicy('quiet');const quiet=['task','needs_input','digest','info','error'].map(send);
   cccNotify.applyPolicy('action');const action=['task','needs_input','session_died','error','info','success','digest','milestone'].map(send);
   const actionMessages=messages.splice(0);
   cccNotify.applyPolicy('all');const all=['task','digest','info'].map(send);const allMessages=messages.splice(0);
   cccNotify.applyPolicy('action');cccNotify.setPref('digest',true);const custom={policy:cccNotify.policy(),info:send('info')};
   return {quiet,action,actionMessages,all,allMessages,custom};
  },native);
  assert.deepEqual(result.quiet,[false,false,false,false,false]);
  assert.deepEqual(result.action,[true,true,true,true,false,false,false,false]);
  assert.equal(result.actionMessages.length,4);assert(result.actionMessages.every(m=>native?m.kind==='silent':m.silent===true));
  assert.deepEqual(result.all,[true,true,true]);assert(result.allMessages.every(m=>native?m.kind!=='silent':m.silent===false));
  assert.deepEqual(result.custom,{policy:'custom',info:true});
 }finally{await page.close();}
});

test('queued alerts cannot bypass a later Quiet policy',async()=>{
 const page=await browser.newPage();try{
  await page.goto(url,{waitUntil:'load'});
  await page.evaluate(()=>{localStorage.clear();Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.hasFocus=()=>true;cccNotify.applyPolicy('all');for(let i=0;i<4;i++)cccNotify({id:'ntf_test_queue_'+i,title:'Queued '+i,kind:'task'});cccNotify.applyPolicy('quiet');});
  assert.equal(await page.$$('.ccc-toast').then(rows=>rows.length),3);
  await page.click('.ccc-toast-close');
  await page.waitForFunction(()=>document.querySelectorAll('.ccc-toast').length===2);
  assert.doesNotMatch(await page.$eval('#cccNotifyStack',n=>n.textContent),/Queued 3/);
 }finally{await page.close();}
});
