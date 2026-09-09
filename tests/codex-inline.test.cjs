const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const puppeteer=require('../require-puppeteer.js');
let browser;
test.before(async()=>{browser=await puppeteer.launch({headless:true})});
test.after(async()=>{await browser.close()});
async function fixture(auto=false){
 const page=await browser.newPage();await page.setViewport({width:1400,height:900});
 await page.setContent('<body class="status-pos-right"><div class="conv-pane has-status-rail is-codex-session" data-pane-id="p1"><header class="conv-pane-header">Existing header</header><div class="conversations-view"><div class="event">Old transcript</div></div><aside class="status-rail">Existing rail</aside><div class="conv-input-bar"><textarea>Draft stays here</textarea><button>Send</button></div></div></body>');
 await page.addStyleTag({path:path.resolve('static/app.css')});await page.addStyleTag({path:path.resolve('static/codex-client.css')});
 await page.addStyleTag({content:'.conv-pane{width:1100px;height:800px}.conversations-view{display:block;overflow:auto}'});
 await page.evaluate(()=>{window.fetch=async(url)=>({ok:true,json:async()=>String(url).includes('/catalog')?{ok:true,methods:[]}:{ok:true,connected:true,generation:'g',cursor:1,thread:{id:'one',turns:[{id:'turn',status:'completed',items:[{id:'answer',type:'agentMessage',text:'Native answer',phase:'final_answer'}]}]},requests:[],events:[]}})});
 if(auto)await page.evaluate(()=>{window.CCCCodexClientContext=()=>({paneEl:document.querySelector('.conv-pane'),threadId:'one',repoPath:'/repo',paneId:'p1'})});
 await page.addScriptTag({path:path.resolve('static/codex-client.js')});return page;
}
test('native content occupies the existing transcript grid area with the original composer and rail',async()=>{
 const page=await fixture();try{
 const result=await page.evaluate(async()=>{
  const pane=document.querySelector('.conv-pane');const composer=pane.querySelector('.conv-input-bar');const rail=pane.querySelector('.status-rail');
  await window.CCCCodexClient.attachInline({paneEl:pane,viewEl:pane.querySelector('.conversations-view'),threadId:'one',repoPath:'/repo'});
  const root=pane.querySelector('.codex-client-shell');const view=pane.querySelector('.conversations-view');
  return{parent:root.parentElement===view,composer:pane.querySelector('.conv-input-bar')===composer,draft:composer.querySelector('textarea').value,rail:pane.querySelector('.status-rail')===rail,railDisplay:getComputedStyle(rail).display,newComposer:root.querySelectorAll('textarea').length,brand:root.textContent.includes('Codex workspace'),slot:getComputedStyle(view).gridArea,roots:pane.querySelectorAll(':scope > .codex-client-shell').length};
 });
 assert.equal(result.parent,true);assert.equal(result.composer,true);assert.equal(result.draft,'Draft stays here');assert.equal(result.rail,true);assert.notEqual(result.railDisplay,'none');assert.equal(result.newComposer,0);assert.equal(result.brand,false);assert.match(result.slot,/conv/);assert.equal(result.roots,0);
 }finally{await page.close()}
});
test('split panes retain independent native clients and selection clears only its own transcript',async()=>{
 const page=await fixture();try{
 const result=await page.evaluate(async()=>{
  const first=document.querySelector('.conv-pane'),second=first.cloneNode(true);second.dataset.paneId='p2';document.body.append(second);
  for(const [pane,id] of [[first,'one'],[second,'two']])await window.CCCCodexClient.attachInline({paneEl:pane,threadId:id,repoPath:'/repo'});
  const before=document.querySelectorAll('.codex-client-shell').length;
  window.dispatchEvent(new CustomEvent('ccc:conversation-selected',{detail:{paneEl:first,threadId:'other',paneId:'p1'}}));
  return{before,first:!!first.querySelector('.codex-client-shell'),second:!!second.querySelector('.codex-client-shell'),draft:second.querySelector('.conv-input-bar textarea').value};
 });assert.deepEqual(result,{before:2,first:false,second:true,draft:'Draft stays here'});
 }finally{await page.close()}
});
test('local images use the image route, not a broken absolute URL',async()=>{
 const page=await fixture();try{
 const src=await page.evaluate(()=>window.CCCCodexClient.__testing.renderItem({id:'image',type:'imageView',path:'/tmp/example picture.png'}).querySelector('img').getAttribute('src'));
 assert.equal(src,'/api/local-image?path=%2Ftmp%2Fexample%20picture.png');
 }finally{await page.close()}
});
test('live command details start collapsed instead of exposing command scripts',async()=>{
 const page=await fixture();try{
 assert.equal(await page.evaluate(()=>window.CCCCodexClient.__testing.renderItem({id:'running',type:'commandExecution',status:'inProgress',command:'long command'}).open),false);
 }finally{await page.close()}
});

test('a conversation painted before client startup upgrades automatically',async()=>{
 const page=await fixture(true);try{
  await page.waitForSelector('.conversations-view > .codex-client-shell.is-inline');
  assert.equal(await page.$eval('.conv-input-bar textarea',e=>e.value),'Draft stays here');
 }finally{await page.close()}
});
test('late engine identification upgrades an already-painted conversation',async()=>{
 const page=await fixture();try{
  await page.evaluate(()=>{const pane=document.querySelector('.conv-pane');pane.classList.remove('is-codex-session');window.CCCCodexClientContext=()=>({paneEl:pane,threadId:'one',repoPath:'/repo',paneId:'p1'});pane.classList.add('is-codex-session')});
  await page.waitForSelector('.conversations-view > .is-inline',{timeout:1000});
 }finally{await page.close()}
});

// Batch-1 recovery assertions: the inline native renderer reuses the
// original CCC step renderer (kimi tool rows/groups + thinking blocks)
// instead of generic Reasoning/Command cards. The real helpers are sliced
// out of app.js and evaluated in the page so the assertions run against the
// shipped projection, not a copy.
const STEP_ITEMS=[
 {id:'r1',type:'reasoning',summary:'',content:''},
 {id:'r2',type:'reasoning',summary:'Re-reading the steer path before editing'},
 {id:'c1',type:'commandExecution',status:'completed',command:'git status --short',aggregatedOutput:' M static/app.js'},
 {id:'c2',type:'commandExecution',status:'completed',command:'sed -n 1,5p app.js',commandActions:[{type:'read',path:'/repo/static/app.js'}]},
 {id:'c3',type:'commandExecution',status:'completed',command:'rg needle src',commandActions:[{type:'search',query:'needle haystack',path:'/repo/src'}]},
 {id:'w1',type:'webSearch',query:'codex desktop ipc protocol'},
 {id:'m1',type:'mcpToolCall',status:'completed',tool:'notes/search',arguments:{q:'ipc'},result:{content:'note body'}},
];
function stepHelperSlices(){
 const app=fs.readFileSync('static/app.js','utf8');
 const hStart=app.indexOf('  // Fold real-world tool-name spellings');
 const hEnd=app.indexOf('  // kimi-web parity (messagesToTurns flushGroup)');
 const sStart=app.indexOf('  window.CCCCodexStepNode = function');
 const sEnd=app.indexOf('  window.CCCCodexInlineStateChanged');
 assert.ok(hStart>0&&hEnd>hStart,'kimi helper slice found');
 assert.ok(sStart>0&&sEnd>sStart,'codex step projection slice found');
 return app.slice(hStart,hEnd)+'\n'+app.slice(sStart,sEnd);
}
async function stepFixture(){
 const page=await browser.newPage();await page.setViewport({width:1400,height:900});
 await page.setContent('<body class="status-pos-right"><div class="conv-pane has-status-rail is-codex-session" data-pane-id="p1"><header class="conv-pane-header">Existing header</header><div class="conversations-view"><div class="event">Old transcript</div></div><aside class="status-rail">Existing rail</aside><div class="conv-input-bar"><textarea>Draft stays here</textarea><button>Send</button></div></div></body>');
 await page.addStyleTag({path:path.resolve('static/app.css')});await page.addStyleTag({path:path.resolve('static/codex-client.css')});
 await page.addStyleTag({content:'.conv-pane{width:1100px;height:800px}.conversations-view{display:block;overflow:auto}'});
 await page.evaluate((source,items)=>{
  window.toolDisplayName=name=>String(name||'');
  window.formatToolCallDetail=()=>({display:''});
  window.escapeHtml=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  window.escapeAttr=value=>window.escapeHtml(value).replace(/"/g,'&quot;');
  window.renderMarkdown=value=>window.escapeHtml(value);
  (0,eval)(source);
  window.__items=items;
  window.fetch=async(url)=>{
   const u=String(url);
   if(u.includes('/catalog'))return{ok:true,json:async()=>({ok:true,methods:[]})};
   const generation=u.includes('/events')&&window.__bump?'g2':'g';
   return{ok:true,json:async()=>({ok:true,connected:true,generation,cursor:1,thread:{id:'one',turns:[{id:'turn',status:'completed',items:window.__items}]},requests:[],events:[]})};
  };
  window.CCCCodexClientContext=()=>({paneEl:document.querySelector('.conv-pane'),threadId:'one',repoPath:'/repo',paneId:'p1'});
 },stepHelperSlices(),STEP_ITEMS);
 await page.addScriptTag({path:path.resolve('static/codex-client.js')});
 await page.waitForSelector('.conversations-view > .codex-client-shell.is-inline .codex-client-turn');
 return page;
}

test('inline steps keep meaningful command, file, query, and output summaries',async()=>{
 const page=await stepFixture();try{
  const result=await page.evaluate(()=>{
   const arg=id=>{const row=document.querySelector('.kimi-tool[data-tool-use-id="'+id+'"]');return row?row.querySelector('.kimi-tool-arg')?.textContent||'':null};
   const group=document.querySelector('.kimi-tool-group');
   return{
    c1:arg('c1'),c2:arg('c2'),c3:arg('c3'),w1:arg('w1'),
    m1:document.querySelector('.kimi-tool[data-tool-use-id="m1"] .kimi-tool-name')?.textContent||null,
    groupTitle:group?.querySelector('.kimi-tg-title')?.textContent||null,
    groupCollapsed:group?group.classList.contains('collapsed'):null,
    peek:document.querySelector('.kimi-tool[data-tool-use-id="c1"] .kimi-tool-peek')?.textContent||'',
    genericCards:document.querySelectorAll('.codex-client-card').length,
   };
  });
  assert.equal(result.c1,'git status --short');
  assert.equal(result.c2,'/repo/static/app.js');
  assert.ok(result.c3.includes('needle haystack')&&result.c3.includes('/repo/src'));
  assert.equal(result.w1,'codex desktop ipc protocol');
  assert.equal(result.m1,'notes/search');
  assert.ok(result.groupTitle.startsWith('5 tool calls'));
  assert.equal(result.groupCollapsed,true);
  assert.ok(result.peek.includes('M static/app.js'));
  assert.equal(result.genericCards,0);
 }finally{await page.close()}
});

test('empty reasoning renders no row while real reasoning keeps its text',async()=>{
 const page=await stepFixture();try{
  const result=await page.evaluate(()=>({
   thinking:Array.from(document.querySelectorAll('.kimi-thinking')).map(node=>node.textContent),
   markers:document.querySelectorAll('.kimi-marker[hidden]').length,
   reasoningLabels:Array.from(document.querySelectorAll('.conversations-view *')).filter(node=>!node.children.length&&/^\s*Reasoning\s*$/.test(node.textContent)).length,
  }));
  assert.equal(result.thinking.length,1);
  assert.ok(result.thinking[0].includes('Re-reading the steer path before editing'));
  assert.ok(result.markers>=1);
  assert.equal(result.reasoningLabels,0);
 }finally{await page.close()}
});

test('expanded steps and groups survive a live refresh',async()=>{
 const page=await stepFixture();try{
  await page.evaluate(()=>{
   document.querySelector('.kimi-tool-group .kimi-tool-group-head').click();
   document.querySelector('.kimi-tool[data-tool-use-id="c1"] .kimi-tool-head').click();
   window.__oldGroup=document.querySelector('.kimi-tool-group');
   window.__bump=true;
  });
  await page.waitForFunction(()=>{
   const group=document.querySelector('.kimi-tool-group');
   return group&&group!==window.__oldGroup;
  },{timeout:8000});
  const result=await page.evaluate(()=>({
   groupCollapsed:document.querySelector('.kimi-tool-group').classList.contains('collapsed'),
   rowOpen:document.querySelector('.kimi-tool[data-tool-use-id="c1"]').classList.contains('open'),
  }));
  assert.deepEqual(result,{groupCollapsed:false,rowOpen:true});
 }finally{await page.close()}
});
