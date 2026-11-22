const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const puppeteer=require('../require-puppeteer.js');
const {findChromePath}=require('../puppeteer-browser-config.js');
const app=(process.env.CONV_JUMP_SOURCE ? fs.readFileSync(process.env.CONV_JUMP_SOURCE, 'utf8') : require('./legacy-dashboard-source.cjs'));
const css=fs.readFileSync(require.resolve('../static/app.css'),'utf8');
const targets=app.slice(app.indexOf('  const CONV_USER_MESSAGE_SELECTOR ='),app.indexOf('  function positionConversationEndAffordance('));
const position=app.slice(app.indexOf('  function positionConversationEndAffordance('),app.indexOf('  function _syncSpeakerTools('));
const update=app.slice(app.indexOf('  function updateConversationEndAffordance('),app.indexOf('  // Surface a plain-language banner at the end'));
const attach=app.slice(app.indexOf('  function attachConversationEndAffordance('),app.indexOf('    // Floating speaker: reads the assistant turn'))+'\nview._convEndAffordanceAttached=true;\n}';
const earlier=app.slice(app.indexOf('  function _insertLoadEarlierBanner('),app.indexOf('  // Codex rollouts spend several JSONL lines per visible row'));
let browser;
before(async()=>{browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});});
after(async()=>{await browser?.close();});
const user=(id,extra='')=>`<div id="${id}" class="event user_text ${extra}" data-jsonl-line="${id.replace(/\D/g,'')||1}"><div class="user-msg">User request ${id}</div></div>`;
const reply=()=>'<div class="event assistant"><div class="assistant-text">Assistant response</div></div>';
async function open(content,width=1200){
 const page=await browser.newPage();await page.setViewport({width,height:850});
 await page.setContent(`<meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}
 body{margin:0;background:#161b22;color:#ddd}.conv-pane{position:absolute;top:100px;left:0;width:100%;height:650px}
 .conversations-view{overflow:auto;padding:0;scroll-behavior:auto}
 .conversations-view .event{position:relative;margin:16px;height:auto;padding:10px}.conversations-view .assistant{height:520px}
 .conversations-view .user_text{min-height:45px}.conversations-view .compact-resume-event.is-pinned-in-sticky{display:none}
 </style><div class="conv-pane" data-pane-id="p"><div class="conversations-view">${content}</div></div>`);
 await page.addScriptTag({content:`
 const view=document.querySelector('.conversations-view');const pane={conversationId:'example-session',firstLine:100};
 const CONV_BOTTOM_TOLERANCE=80;const paneByPaneId=()=>pane;
 const isConversationAtBottom=v=>v.scrollHeight-v.scrollTop-v.clientHeight<=2;
 const jumpPresentationToEnd=()=>false;
 const scrollConversationToEnd=v=>v.scrollTop=v.scrollHeight;
 const _ensureLoadEarlierStyle=()=>{};const _contNextParentForPane=()=>false;
 const _showConvLoading=()=>({remove(){}});window.loads=0;window.fetchPage=async()=>{};
 const fetchConversationEvents=async()=>{loads++;await fetchPage();};
 ${targets}\n${position}\n${update}\n${attach}\n${earlier}
 attachConversationEndAffordance(view);view.addEventListener('scroll',()=>updateConversationEndAffordance(view));view.scrollTop=view.scrollHeight;updateConversationEndAffordance(view);
 `});return page;
}
async function at(page,id) {
 await page.waitForFunction(id=>{
  const el=document.getElementById(id),view=document.querySelector('.conversations-view');
  return el && Math.abs(el.getBoundingClientRect().top-view.getBoundingClientRect().top-12)<3;
 },{timeout:4000},id);
}

test('hidden compaction is skipped instead of moving Last one header at a time',async()=>{
 const page=await open(user('u10')+reply()+reply()+user('system20','compact-resume-event is-pinned-in-sticky')+reply()+user('peer30','peer-message')+reply());
 try{
  const before=await page.evaluate(()=>({scroll:view.scrollTop,target:_prevUserMessageTarget(view)?.el.id,top:_prevUserMessageTarget(view)?.el.getBoundingClientRect().top}));
  await page.click('.conv-scroll-last-btn');
  await at(page,'u10');
  const after=await page.evaluate(()=>view.scrollTop);
  assert.ok(before.scroll-after>1000,'one click crosses the replies to the real user request');
  if(process.env.CONV_JUMP_SCREENSHOT)await page.screenshot({path:process.env.CONV_JUMP_SCREENSHOT});
  console.log(JSON.stringify({before,after}));
 }finally{await page.close();}
});

test('a hidden original ask becomes readable and is reached in one click',async()=>{
 const page=await open(user('u10','is-pinned-in-sticky')+reply()+reply()+reply());
 try{await page.click('.conv-scroll-last-btn');await at(page,'u10');assert.equal(await page.$eval('#u10',el=>el.checkVisibility({checkVisibilityCSS:true})),true);}finally{await page.close();}
});

test('Next never targets the original ask already shown in the sticky header',async()=>{
 // One-prompt worker session read from the top: the hidden pinned ask was the
 // only "next" stop, so each tap nudged the view a few pixels (phone report).
 const page=await open(user('u10','is-pinned-in-sticky')+'<div class="conv-cache-chip" style="height:20px;margin-top:60px"></div>'+reply()+reply()+reply(),390);
 try{
  await page.evaluate(()=>{view.scrollTop=0;updateConversationEndAffordance(view);});
  const s=await page.evaluate(()=>({target:_nextUserMessageTarget(view)?.id||null,
   next:view._convNextButton.classList.contains('visible'),end:view._convEndButton.classList.contains('visible')}));
  assert.deepEqual(s,{target:null,next:false,end:true});
 }finally{await page.close();}
});

test('Previous and Next step between actual user turns, including a mirrored ask',async()=>{
 const page=await open(user('u10')+reply()+user('peer20','peer-message')+user('skill21','skill-loaded-event')+user('task22','task-notification-event')+user('provisional23','provisional') .replace('data-jsonl-line="23"','data-live-key="turn:item"')+reply()+user('u30','is-dynamic-pinned-in-sticky')+reply()+reply());
 try{await page.click('.conv-scroll-last-btn');await at(page,'u30');assert.equal(await page.$eval('#u30',el=>getComputedStyle(el).visibility),'visible');await page.click('.conv-scroll-last-btn');await at(page,'u10');await page.click('.conv-scroll-next-btn');await at(page,'u30');}finally{await page.close();}
});

test('Last loads past a synthetic tail and first-prompt copy to find the nearest real user',async()=>{
 const page=await open(user('u1','conv-first-prompt-pin is-pinned-in-sticky')+reply()+user('compact80','compact-resume-event')+reply()+user('peer90','peer-message')+reply(),390);
 try{
  await page.evaluate(()=>{
   _insertLoadEarlierBanner(view,'example-session','p');
   window.fetchPage=async()=>{await new Promise(resolve=>setTimeout(resolve,80));const top=view.scrollTop,height=view.scrollHeight;view.querySelector('.conv-load-earlier')?.remove();const block=document.createElement('div');block.innerHTML='<div class="event user_text" id="u50" data-jsonl-line="50"><div class="user-msg">The latest human request</div></div><div class="event assistant" style="height:520px">Earlier response</div>';view.insertBefore(block,view.querySelector('.event.assistant'));pane.firstLine=50;view.scrollTop=top+view.scrollHeight-height;_insertLoadEarlierBanner(view,'example-session','p');};updateConversationEndAffordance(view);
  });
  await page.click('.conv-scroll-last-btn');await page.evaluate(()=>document.querySelector('.conv-scroll-last-btn').click());await at(page,'u50');
  assert.equal(await page.evaluate(()=>loads),1,'repeat clicks share the history load');
  if(process.env.CONV_JUMP_PHONE_SCREENSHOT)await page.screenshot({path:process.env.CONV_JUMP_PHONE_SCREENSHOT});
 }finally{await page.close();}
});

test('a failed history fetch stays retryable and does not jump to the distant first prompt',async()=>{
 const page=await open(user('u1','conv-first-prompt-pin is-pinned-in-sticky')+reply()+user('peer90','peer-message')+reply());
 try{const before=await page.evaluate(()=>{_insertLoadEarlierBanner(view,'example-session','p');window.fetchPage=async()=>{throw Error('History unavailable');};updateConversationEndAffordance(view);return view.scrollTop;});await page.click('.conv-scroll-last-btn');await page.waitForFunction(()=>!view._convUserJumpPending);assert.equal(await page.evaluate(()=>view.scrollTop),before);assert.match(await page.$eval('.conv-load-earlier',el=>el.textContent),/Retry/);assert.equal(await page.$eval('.conv-scroll-last-btn',el=>el.disabled),false);}finally{await page.close();}
});

test('switching conversations during a history lookup cancels its jump',async()=>{
 const page=await open(user('u1','conv-first-prompt-pin is-pinned-in-sticky')+reply()+user('peer90','peer-message')+reply());
 try{const before=await page.evaluate(()=>{_insertLoadEarlierBanner(view,'example-session','p');window.fetchPage=()=>new Promise(resolve=>window.finishPage=resolve);updateConversationEndAffordance(view);return view.scrollTop;});await page.click('.conv-scroll-last-btn');await page.evaluate(()=>{pane.conversationId='another-session';finishPage();});await page.waitForFunction(()=>!view._convUserJumpPending);assert.equal(await page.evaluate(()=>view.scrollTop),before);}finally{await page.close();}
});

test('history without a new page stops without jumping to the first-prompt copy',async()=>{
 const page=await open(user('u1','conv-first-prompt-pin is-pinned-in-sticky')+reply()+user('peer90','peer-message')+reply());
 try{
  const before=await page.evaluate(()=>{_insertLoadEarlierBanner(view,'example-session','p');updateConversationEndAffordance(view);return view.scrollTop;});
  await page.click('.conv-scroll-last-btn');
  await page.waitForFunction(()=>!view._convUserJumpPending);
  assert.equal(await page.evaluate(()=>loads),1);
  assert.equal(await page.evaluate(()=>view.scrollTop),before);
 }finally{await page.close();}
});
