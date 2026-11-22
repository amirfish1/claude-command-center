const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const puppeteer = require('../require-puppeteer');
const {findChromePath} = require('../puppeteer-browser-config');
const source = require('./legacy-dashboard-source.cjs');
const start = source.indexOf('  function _swipeActionButtonForRow(row) {');
const handler = source.slice(start, source.indexOf('  // WhatsApp-style drag-to-dismiss', start));
const css = fs.readFileSync('static/app.css', 'utf8') + fs.readFileSync('static/simple.css', 'utf8');

test('swipe left reveals More + Archive, full swipe archives, right swipe stars, More opens the sheet', async () => {
  const names = ['All','Features','Bugs','Workers','History','Simple'];
  const rows = names.map(name => `<div id="${name}" class="${name === 'Simple' ? 'simple-card simple-task-card' : 'conv-item'}" data-session-id="fixture-${name}" style="height:80px;margin:8px;background:#222"><div data-role="title" aria-label="${name} session">${name} session</div><button class="conv-archive-btn" data-role="archive" data-archived="${name !== 'History'}">${name === 'History' ? 'Restore' : 'Archive'}</button><button class="conv-trash-btn" data-role="trash" aria-label="Move to Trash">🗑</button>${name === 'All' ? '<button class="conv-today-star" data-role="today-star" aria-pressed="false" aria-label="Star for today">☆</button><button data-role="move-lane" aria-label="Move to Workers tab">💼</button>' : ''}</div>`).join('');
  const server = http.createServer((req,res) => {
    res.setHeader('Content-Type','text/html; charset=utf-8');
    res.end(`<meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}body{display:block;overflow-x:hidden;padding:0;background:#111;color:#eee}.conv-archive-btn,.conv-trash-btn,.conv-today-star,[data-role=move-lane]{float:right}</style>${rows}<script>
      window.actions=[]; window.opens=0; window.dragDepth=0;
      function isTouchPrimary(){return true}
      function isSimpleMode(){return false}
      function beginSidebarDrag(){window.dragDepth++}
      function endSidebarDrag(){window.dragDepth--}
      function cancelMobileConversationRowTap(){}
      function noteMobileConversationRowSwipe(){}
      document.querySelectorAll('[data-session-id]').forEach(row=>row.addEventListener('click',e=>{
        const b=e.target.closest('button[data-role]');
        if(b) actions.push(row.id+':'+(b.getAttribute('aria-label')||b.textContent));
        else if(!e.target.closest('.conv-swipe-tray')) opens++;
      }));
      ${handler}
      window.touch=(id,type,x,y=30)=>{
        const target=document.querySelector('#'+id+' [data-role=title]');
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
    const swipe=async(id,from,to)=>{ await touch(id,'touchstart',from); await new Promise(r=>setTimeout(r,90)); await touch(id,'touchmove',to); await new Promise(r=>setTimeout(r,90)); await touch(id,'touchmove',to); await touch(id,'touchend',to); };
    const depth=()=>page.evaluate(()=>dragDepth);
    const actions=()=>page.evaluate(()=>actions.slice());

    // Partial left swipe snaps open with More + Archive and holds redraws.
    await swipe('Bugs',300,190);
    assert.equal(await page.$eval('#Bugs',el=>el.classList.contains('is-swipe-open')),true);
    assert.deepEqual(await page.$$eval('#Bugs .conv-swipe-action',els=>els.map(e=>e.textContent.replace(/[^A-Za-z]/g,''))),['More','Archive']);
    assert.equal(await page.$eval('#Bugs',el=>el.style.getPropertyValue('--conv-row-swipe-offset')),'-148px');
    assert.equal(await depth(),1,'poll redraws held while the tray is open');
    await new Promise(r=>setTimeout(r,250));
    await page.screenshot({path:'/tmp/ccc-mobile-swipe-open.png'});
    await page.$eval('#Bugs [data-swipe-action=archive]',el=>el.click());
    assert.deepEqual(await actions(),['Bugs:Archive']);
    assert.equal(await depth(),0);

    // Full left swipe archives (or restores) without a second tap.
    for (const id of ['Features','History','Simple']) {
      await touch(id,'touchstart',360); await touch(id,'touchmove',300); await touch(id,'touchmove',60);
      assert.equal(await page.$eval('#'+id,el=>el.classList.contains('is-swipe-armed')),true);
      if (id==='Features') await page.screenshot({path:'/tmp/ccc-mobile-swipe-armed.png'});
      await touch(id,'touchend',60);
      await new Promise(r=>setTimeout(r,260));
    }
    assert.deepEqual((await actions()).slice(1),['Features:Archive','History:Restore','Simple:Archive']);
    assert.equal(await depth(),0);

    // Right swipe: only rows with a today star have a left tray.
    await swipe('Workers',60,200);
    assert.equal(await page.$('#Workers .conv-swipe-tray'),null);
    await swipe('All',60,150);
    assert.deepEqual(await page.$$eval('#All .conv-swipe-action',els=>els.map(e=>e.dataset.swipeAction)),['star']);
    // Tapping another row closes the open tray without opening that row.
    await touch('Workers','touchstart',200); await touch('Workers','touchend',200);
    await page.$eval('#Workers [data-role=title]',el=>el.click());
    await new Promise(r=>setTimeout(r,250));
    assert.equal(await page.$('#All .conv-swipe-tray'),null);
    assert.equal(await page.evaluate(()=>opens),0);
    assert.equal(await depth(),0);
    await touch('All','touchstart',30); await touch('All','touchmove',90); await touch('All','touchmove',330); await touch('All','touchend',330);
    await new Promise(r=>setTimeout(r,260));
    assert.equal((await actions()).at(-1),'All:Star for today');

    // More opens a sheet of the row's own actions; Trash sits in the red group.
    await swipe('All',300,190);
    await page.$eval('#All [data-swipe-action=more]',el=>el.click());
    await page.waitForSelector('.row-action-sheet.is-open');
    assert.equal(await page.$eval('.row-action-sheet-title',el=>el.textContent),'All session');
    assert.deepEqual(await page.$$eval('.row-action-sheet-group:not(.is-danger) .row-action-sheet-item',els=>els.map(e=>e.textContent.trim())),['☆Star for today','💼Move to Workers tab']);
    assert.deepEqual(await page.$$eval('.row-action-sheet-group.is-danger .row-action-sheet-item',els=>els.map(e=>e.dataset.sheetRole)),['trash']);
    assert.equal(await depth(),1,'row held in place under the sheet');
    await new Promise(r=>setTimeout(r,260));
    await page.screenshot({path:'/tmp/ccc-mobile-row-sheet.png'});
    await page.$eval('.row-action-sheet-group.is-danger .row-action-sheet-item',el=>el.click());
    assert.equal((await actions()).at(-1),'All:Move to Trash');
    await new Promise(r=>setTimeout(r,260));
    assert.equal(await page.$('.row-action-sheet'),null);
    assert.equal(await depth(),0);

    // Short slow swipes spring back; vertical drags, cancel and the edge never act.
    const before=(await actions()).length;
    await swipe('Bugs',300,280);
    await touch('Bugs','touchstart',300); await touch('Bugs','touchmove',295,180); await touch('Bugs','touchend',295,180);
    await touch('Bugs','touchstart',300); await touch('Bugs','touchmove',60); await touch('Bugs','touchcancel',60);
    await touch('Bugs','touchstart',5); await touch('Bugs','touchmove',-200); await touch('Bugs','touchend',-200);
    await new Promise(r=>setTimeout(r,260));
    assert.equal((await actions()).length,before,'cancellation and browser edge never act');
    assert.equal(await page.$eval('#Bugs',el=>el.style.getPropertyValue('--conv-row-swipe-offset')),'');
    assert.equal(await depth(),0);
    assert.equal(await page.evaluate(()=>opens),0,'synthetic clicks after swipes suppressed');
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
