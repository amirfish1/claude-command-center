// Opt-in rendered geometry regression; API fixtures never send messages or record audio.
// CCC_MOBILE_READER_TEST_URL=http://localhost:8090 node tests/mobile-recorder-jumps.browser.cjs
const assert=require('node:assert/strict');
const fs=require('fs');
const root=require('path').resolve(__dirname,'..')+'/';
const base=process.env.CCC_MOBILE_READER_TEST_URL;
if (!base) throw new Error('Set CCC_MOBILE_READER_TEST_URL to a running dashboard');
const puppeteer=require(root+'require-puppeteer.js');
const {findChromePath}=require(root+'puppeteer-browser-config.js');
(async()=>{
 const browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
 try {
 const page=await browser.newPage(); await page.setViewport({width:390,height:844,isMobile:true,hasTouch:true});
 await page.evaluateOnNewDocument(()=>{for(const key of ['ccc-onboarded','ccc-tour-done','ccc-engines-first-run-done'])localStorage.setItem(key,'1');localStorage.setItem('ccc-ui-mode','advanced');});
 await page.setRequestInterception(true);
 page.on('request',req=>{const url=new URL(req.url());
 if(url.pathname==='/static/app.js')return req.respond({contentType:'text/javascript',body:fs.readFileSync(process.env.CONV_JUMP_SOURCE || root+'static/app.js','utf8').replace('  function positionConversationEndAffordance(view) {',`  window.__mobileRecorderLayout = () => {
 const view=getConvView(); const pane=view.closest('.conv-pane');pane.classList.remove('has-new-session-context');pane.querySelector('.conv-input-bar').classList.add('visible');document.body.classList.add('mobile-show-main');
 view.innerHTML=Array.from({length:20},()=>'<div class="event user_text"><div class="user-msg">Review the layout.</div></div><div class="event assistant"><div class="assistant-text"><p>Read the conversation and jump between turns.</p></div></div>').join('');view.scrollTop=180;view._pinnedToBottom=false;
 window.__mobileRecorderPosition=()=>{for(const btn of [view._convEndButton,view._convNextButton,view._convLastButton,view._convSegmentButton]){btn.style.transition='none';btn.classList.add('visible');}positionConversationEndAffordance(view);};
 window.__mobileRecorderHidden=()=>positionConversationEndAffordance(view);
 window.__mobileRecorderPosition();
 };
 function positionConversationEndAffordance(view) {`)});
 if(url.pathname.startsWith('/api/'))return req.respond({contentType:'application/json',body:JSON.stringify(url.pathname==='/api/sessions'?[]:url.pathname==='/api/conversations/list'?{conversations:[],total:0}:{})});
 req.continue();});
 await page.goto(base+'/',{waitUntil:'domcontentloaded',timeout:60000});await page.waitForSelector('.conv-scroll-end-btn');await page.evaluate(()=>__mobileRecorderLayout());
 await page.waitForFunction(()=>{const t=getComputedStyle(document.querySelector('.main')).transform;return t==='none'||t==='matrix(1, 0, 0, 1, 0, 0)';});
 const measure=()=>page.evaluate(()=>{
   __mobileRecorderPosition();
   const rect=el=>el.getBoundingClientRect().toJSON();
   return {buttons:[...document.querySelectorAll('.conv-pane > :is(.conv-scroll-end-btn,.conv-scroll-last-btn,.conv-scroll-next-btn,.conv-scroll-segment-btn)')].map(rect),
     recorder:rect(document.getElementById('mobileTicketRecord'))};
 });
 const overlaps=(a,b)=>a.left<b.right && a.right>b.left && a.top<b.bottom && a.bottom>b.top;
 function check(geometry,width) {
   for(const button of geometry.buttons) {
     assert.equal(button.width,44); assert.equal(button.height,44);
     assert.ok(button.left>=0 && button.right<=width && button.top>=0, 'jump stays on screen');
     assert.ok(!overlaps(button,geometry.recorder),'recorder does not cover a jump control');
   }
   for(let i=0;i<geometry.buttons.length;i++)for(let j=i+1;j<geometry.buttons.length;j++)
     assert.ok(!overlaps(geometry.buttons[i],geometry.buttons[j]),'jump controls do not overlap');
 }
 for(const width of [375,390,430,700]) {
   await page.setViewport({width,height:844,isMobile:true,hasTouch:true});
   await page.waitForFunction(()=>{const t=getComputedStyle(document.querySelector('.main')).transform;return t==='none'||t==='matrix(1, 0, 0, 1, 0, 0)';});
   check(await measure(),width);
   console.log('PASS default recorder at '+width+'x844');
   if(width===390)await page.screenshot({path:process.env.CCC_MOBILE_RECORDER_SCREENSHOT || '/tmp/ccc-mobile-recorder-jumps.png'});
 }
 await page.setViewport({width:390,height:844,isMobile:true,hasTouch:true});
 await page.evaluate(()=>{
   const recorder=document.getElementById('mobileTicketRecord');
   Object.assign(recorder.style,{left:'210px',right:'auto',top:'700px'});
 });
 check(await measure(),390);
 console.log('PASS moved recorder');
 const visiblePadding=await page.$eval('#conversationsView',view=>view.style.paddingBottom);
 await page.evaluate(()=>{
   const view=document.getElementById('conversationsView');
   for(const control of [view._convEndButton,view._convNextButton,view._convLastButton,view._convSegmentButton])control.classList.remove('visible');
 });
 // Reposition without forcing the buttons visible, as happens at transcript end.
 await page.evaluate(()=>window.__mobileRecorderHidden());
 assert.equal(await page.$eval('#conversationsView',view=>view.style.paddingBottom),visiblePadding,'clearance stays stable when the jump controls hide');
 console.log('PASS hidden jump clearance');
 // A hidden recorder must not reserve space, and repeated layouts must stay stable.
 await page.evaluate(()=>{document.getElementById('mobileTicketRecord').style.display='none';});
 const hidden=await measure();
 assert.ok(hidden.buttons[0].bottom>700,'hidden recorder leaves the normal row position');
 assert.deepEqual((await measure()).buttons,hidden.buttons,'layout is stable');
 console.log('PASS hidden recorder');
 // Keyboard/composer growth changes the jump row, independently of the recorder.
 await page.evaluate(()=>{
   const recorder=document.getElementById('mobileTicketRecord');
   Object.assign(recorder.style,{display:'',left:'',right:'',top:''});
   document.querySelector('.conv-input-bar').style.minHeight='230px';
 });
 check(await measure(),390);
 console.log('PASS expanded composer');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
