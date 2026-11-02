'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const app=fs.readFileSync(process.env.CCC_TEST_SETTINGS_APP||path.join(__dirname,'../static/app.js'),'utf8');
const start=app.indexOf('  function ensureSettingsRowCrumb('),end=app.indexOf('  function clearSettingsSearch(',start),source=app.slice(start,end);
let server,browser,url;
const fixture=`<!doctype html><meta charset="utf-8"><div class="settings-modal"><div id="settingsPane"><section class="settings-section is-active-section" data-section-id="appearance"><h2 class="settings-section-eyebrow">Appearance</h2><div id="fontRow" class="settings-row"><span class="settings-row-label">Font</span><button>System</button></div><div id="soundRow" class="settings-row"><span class="settings-row-label">Stop sounds</span><button>On</button></div><div id="unavailableRow" class="settings-row" hidden><span class="settings-row-label">Font unavailable</span></div></section></div><div id="empty"></div><span id="query"></span></div><script>
const $settingsPane=document.getElementById('settingsPane'),$settingsModalInner=document.querySelector('.settings-modal'),$settingsEmptyState=document.getElementById('empty'),$settingsEmptyQuery=document.getElementById('query');let _settingsSearchIndex=null;
${source}
window.search=applySettingsSearch;window.invalidate=()=>{_settingsSearchIndex=null;};
</script>`;
test.before(async()=>{assert(start>0&&end>start);server=http.createServer((req,res)=>{res.setHeader('Content-Type','text/html; charset=utf-8');res.end(fixture);});await new Promise(r=>server.listen(0,'127.0.0.1',r));url='http://127.0.0.1:'+server.address().port;browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});});
test.after(async()=>{await browser?.close();if(server)await new Promise(r=>server.close(r));});
test('native index rebuild preserves pre-search row visibility and clearing restores the editor',async()=>{
 const page=await browser.newPage();try{await page.goto(url);const result=await page.evaluate(()=>{
   search('stop sounds');const filtered=document.getElementById('fontRow').hidden;invalidate();search('font');const matched=!document.getElementById('fontRow').hidden;
   invalidate();search('stop sounds');invalidate();search('');const restored=!document.getElementById('fontRow').hidden&&!document.getElementById('soundRow').hidden;
   const nativeHidden=document.getElementById('unavailableRow').hidden;
   document.getElementById('fontRow').hidden=true;invalidate();search('font');const nativeChangePreserved=document.getElementById('fontRow').hidden;search('');
   return {filtered,matched,restored,nativeHidden,nativeChangePreserved};
 });assert.deepEqual(result,{filtered:true,matched:true,restored:true,nativeHidden:true,nativeChangePreserved:true});}finally{await page.close();}
});
test('late native rows join the index without losing existing or originally hidden rows',async()=>{
 const page=await browser.newPage();try{await page.goto(url);const result=await page.evaluate(()=>{
   search('stop sounds');const row=document.createElement('div');row.className='settings-row';row.innerHTML='<span class="settings-row-label">Voice</span>';document.querySelector('.settings-section').append(row);invalidate();search('voice');const visible=!row.hidden;invalidate();search('');
   return {visible,font:!document.getElementById('fontRow').hidden,voice:!row.hidden,nativeHidden:document.getElementById('unavailableRow').hidden};
 });assert.deepEqual(result,{visible:true,font:true,voice:true,nativeHidden:true});}finally{await page.close();}
});
