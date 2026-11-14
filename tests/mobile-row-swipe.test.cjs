const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const puppeteer = require('../require-puppeteer');
const {findChromePath} = require('../puppeteer-browser-config');
const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('  function _swipeActionButtonForRow(row) {');
const handler = source.slice(start, source.indexOf('  // WhatsApp-style drag-to-dismiss', start));
const css = fs.readFileSync('static/app.css', 'utf8') + fs.readFileSync('static/simple.css', 'utf8');

test('session swipes archive across lists, cancel safely and never open or trash a row', async () => {
  const rows = ['All','Features','Bugs','Workers','History','Simple'].map(name => `<div id="${name}" class="${name === 'Simple' ? 'simple-card simple-task-card' : 'conv-item'}" data-session-id="fixture-${name}" style="height:80px;margin:8px;background:#222"><span>${name} session</span><button class="conv-archive-btn" data-archived="${name !== 'History'}">${name === 'History' ? 'Restore' : 'Archive'}</button><button class="conv-trash-btn">Trash</button></div>`).join('');
  const server = http.createServer((req,res) => {
    res.setHeader('Content-Type','text/html; charset=utf-8');
    res.end(`<meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}body{display:block;overflow:auto;padding:0;background:#111;color:#eee}.conv-archive-btn,.conv-trash-btn{float:right}</style>${rows}<script>
      window.actions=[]; window.opens=0; window.dragDepth=0;
      function isTouchPrimary(){return true}
      function isSimpleMode(){return false}
      function beginSidebarDrag(){window.dragDepth++}
      function endSidebarDrag(){window.dragDepth--}
      function cancelMobileConversationRowTap(){}
      function noteMobileConversationRowSwipe(){}
      document.querySelectorAll('[data-session-id]').forEach(row=>row.addEventListener('click',e=>{
        if(e.target.closest('button')) actions.push(row.id+':'+e.target.textContent);
        else opens++;
      }));
      ${handler}
      window.touch=(id,type,x,y=30)=>{
        const target=document.getElementById(id);
        const touch=new Touch({identifier:1,target,clientX:x,clientY:y});
        target.dispatchEvent(new TouchEvent(type,{bubbles:true,cancelable:true,touches:type==='touchend'||type==='touchcancel'?[]:[touch],changedTouches:[touch]}));
      };
    </script>`);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page=await browser.newPage();
    await page.setViewport({width:390,height:850,isMobile:true,hasTouch:true});
    await page.goto('http://127.0.0.1:'+server.address().port);
    const touch=async(id,type,x,y)=>page.evaluate((...args)=>window.touch(...args),id,type,x,y);
    for(const id of ['All','Features','Bugs','Workers','History','Simple']) {
      const left=id==='Features'||id==='Workers'||id==='Simple';
      await touch(id,'touchstart',left?280:60);
      await touch(id,'touchmove',left?140:200);
      assert.equal(await page.evaluate(()=>dragDepth),1,'poll redraws held during gesture');
      assert.equal(await page.$eval('#'+id,el=>el.classList.contains('is-swipe-armed')),true);
      assert.match(await page.$eval('#'+id,el=>el.dataset.swipeActionLabel),/Release to (archive|restore)/);
      if(id==='Bugs') await page.screenshot({path:'/tmp/ccc-mobile-swipe-armed.png'});
      await touch(id,'touchend',left?140:200);
      await page.$eval('#'+id+' span',el=>el.click());
      assert.equal(await page.evaluate(()=>dragDepth),0);
    }
    assert.deepEqual(await page.evaluate(()=>actions),['All:Archive','Features:Archive','Bugs:Archive','Workers:Archive','History:Restore','Simple:Archive']);
    assert.equal(await page.evaluate(()=>opens),0,'synthetic click after swipe suppressed');
    // A deliberate slow short swipe springs back; vertical drags remain scrolls.
    await touch('All','touchstart',60);
    await new Promise(resolve=>setTimeout(resolve,120));
    await touch('All','touchmove',90);
    await touch('All','touchend',90);
    await touch('All','touchstart',60);
    await touch('All','touchmove',65,180);
    await touch('All','touchend',65,180);
    await touch('All','touchstart',60);
    await touch('All','touchmove',220);
    await touch('All','touchcancel',220);
    await touch('All','touchstart',5);
    await touch('All','touchmove',220);
    await touch('All','touchend',220);
    assert.equal(await page.evaluate(()=>actions.length),6,'cancellation and browser edge never archive');
    assert.equal(await page.evaluate(()=>dragDepth),0);
    assert.equal(await page.$eval('#All',el=>el.style.getPropertyValue('--conv-row-swipe-offset')),'');
    // A new row inserted after boot inherits delegation without rewiring.
    await page.evaluate(()=>{const row=document.getElementById('All').cloneNode(true);row.id='Added';document.body.append(row);row.querySelector('button').onclick=()=>actions.push('Added:Archive');});
    await touch('Added','touchstart',60); await touch('Added','touchmove',220); await touch('Added','touchend',220);
    assert.equal(await page.evaluate(()=>actions.at(-1)),'Added:Archive');
  } finally { await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve)); }
});

test('real Simple cards archive running tasks, restore history and recover failed requests', async () => {
  const cards = source.slice(source.indexOf('  var _simpleTaskRowsById = new Map();'), source.indexOf('  function _simpleIsWorkingRow(row) {'));
  const lifecycle = source.slice(source.indexOf('  async function _setOpenSessionLifecycle('), source.indexOf('  // A misclick on Archive'));
  const server = http.createServer((req,res) => {
    res.setHeader('Content-Type','text/html; charset=utf-8');
    res.end(`<meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style><body class="ccc-simple-mode"><div id="cards"></div><script>
      const row={id:'fixture-running',session_id:'fixture-running',display_name:'Running task',archived:false};
      const conversationsData=[row], archiveData=[], currentRepoBacklogData=[], _simpleHistoryRows=[row];
      const $convSearch={value:''};let currentConversation=null;
      window.calls=[];window.undo=null;window.failNext=false;
      function escapeHtml(s){return String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')}
      const escapeAttr=escapeHtml;
      function sessionEngineIconHtml(){return ''}
      function _simpleIsWorkingRow(){return true}
      function _simpleIsUnseenFinished(){return false}
      function _simpleMemoryLine(){return ''}
      function isSimpleMode(){return true}
      function setOptimisticOverride(){}
      function updateInputBar(){}
      function filterConversations(){return []}
      function renderSidebar(){}
      function _simpleHomeRefresh(){}
      function _simpleHistoryApply(){document.getElementById('cards').innerHTML=_simpleTaskCardHtml(row,'Working',true)}
      function showOpToast(message,kind){window.lastToast=message}
      function _showArchivedUndoToast(row){window.undo=()=>_setOpenSessionLifecycle('unarchive',row)}
      function rowRepoPath(){return '/example/project'}
      function archivePayloadForRow(row,sid,archived){return {session_id:sid,archived}}
      async function ccPostJson(url,payload){
        calls.push({url,payload});
        await new Promise(resolve=>setTimeout(resolve,60));
        if(window.failNext){window.failNext=false;return {ok:false,error:'fixture error'}}
        return {ok:true,archived:payload.archived,session_ids:[row.session_id]};
      }
      ${lifecycle}
      ${cards}
      _simpleHistoryApply();
    </script>`);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page=await browser.newPage();
    await page.setViewport({width:390,height:650,isMobile:true,hasTouch:true});
    await page.goto('http://127.0.0.1:'+server.address().port);
    await page.click('[data-simple-archive]');
    assert.equal(await page.$eval('[data-simple-archive]',el=>el.disabled),true,'in-flight replacement stays disabled');
    await page.waitForFunction(()=>window.calls.length===1 && !document.querySelector('[data-simple-archive]').disabled);
    assert.equal(await page.$eval('[data-simple-archive]',el=>el.getAttribute('aria-label')),'Restore');
    await page.evaluate(()=>window.undo());
    await page.waitForFunction(()=>document.querySelector('[data-simple-archive]').dataset.archived==='true');
    await page.evaluate(()=>{window.failNext=true});
    await page.click('[data-simple-archive]');
    await page.waitForFunction(()=>window.lastToast?.includes('fixture error'));
    assert.equal(await page.$eval('[data-simple-archive]',el=>el.getAttribute('aria-label')),'Archive','failure rolls back card');
    assert.deepEqual(await page.evaluate(()=>calls.map(call=>call.payload.archived)),[true,false,true]);
    assert.equal(await page.$eval('.simple-card-archive-btn',el=>el.getBoundingClientRect().width>=44),true);
    await page.screenshot({path:'/tmp/ccc-mobile-simple-card.png'});
  } finally {await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
});
