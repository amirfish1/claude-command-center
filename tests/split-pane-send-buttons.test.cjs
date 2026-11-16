const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const app=require('./legacy-dashboard-source.cjs');
function buildPaneSrc(){
 const start=app.indexOf('  function buildPaneElement(paneId) {');
 assert.notEqual(start,-1,'buildPaneElement exists');
 return app.slice(start,app.indexOf('\n  }\n',start));
}
test('cloned split panes wire Submit+ (phone mode) to their own pane',()=>{
 assert.match(buildPaneSrc(),/querySelector\('\.submit-plus-btn'\)[\s\S]*?submitPlus\(paneId\)/);
});
test('cloned split panes wire Send-queue to their own pane',()=>{
 assert.match(buildPaneSrc(),/querySelector\('\.send-queue-btn'\)[\s\S]*?sendToTerminal\(paneId, 'send_queue'\)/);
});
test('submitPlus activates the target pane before arming the phone-mode read',()=>{
 const start=app.indexOf('  async function submitPlus(paneId) {');
 const body=app.slice(start,app.indexOf('\n  }\n',start));
 assert.ok(body.indexOf('setActivePaneById(paneId)')>-1);
 assert.ok(body.indexOf('setActivePaneById(paneId)')<body.indexOf('_armPhoneModeRead('));
});
