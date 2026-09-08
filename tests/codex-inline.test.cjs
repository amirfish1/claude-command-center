const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const puppeteer=require('../require-puppeteer.js');
let browser;
test.before(async()=>{browser=await puppeteer.launch({headless:true})});
test.after(async()=>{await browser.close()});
async function fixture(){
 const page=await browser.newPage();await page.setViewport({width:1400,height:900});
 await page.setContent('<body class="status-pos-right"><div class="conv-pane has-status-rail is-codex-session" data-pane-id="p1"><header class="conv-pane-header">Existing header</header><div class="conversations-view"><div class="event">Old transcript</div></div><aside class="status-rail">Existing rail</aside><div class="conv-input-bar"><textarea>Draft stays here</textarea><button>Send</button></div></div></body>');
 await page.addStyleTag({path:path.resolve('static/app.css')});await page.addStyleTag({path:path.resolve('static/codex-client.css')});
 await page.addStyleTag({content:'.conv-pane{width:1100px;height:800px}.conversations-view{display:block;overflow:auto}'});
 await page.evaluate(()=>{window.fetch=async(url)=>({ok:true,json:async()=>String(url).includes('/catalog')?{ok:true,methods:[]}:{ok:true,connected:true,generation:'g',cursor:1,thread:{id:'one',turns:[{id:'turn',status:'completed',items:[{id:'answer',type:'agentMessage',text:'Native answer',phase:'final_answer'}]}]},requests:[],events:[]}})});
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
