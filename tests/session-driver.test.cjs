const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../static/app.js'),'utf8');
const start=source.indexOf('  function sessionDriverHtml('),end=source.indexOf('  function refreshSessionDriverHeaders(',start);
const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const render=vm.runInNewContext(source.slice(start,end)+'\nsessionDriverHtml;', {escapeHtml:escape,escapeAttr:escape,Date});
test('driver and owner ages are distinct for unattended sessions',()=>{
 const html=render({id:'target',turn_driver:{kind:'session',name:'12345678-1234-1234-1234-123456789abc',at:49900},last_owner_input_at:6800},50000,true);
 assert.match(html,/Driven by Session 12345678/);assert.match(html,/1m ago/);assert.match(html,/You · <span[^>]*>12h ago/);
});
test('owner, service, background and missing evidence remain distinguishable',()=>{
 assert.match(render({turn_driver:{kind:'human',at:99},last_owner_input_at:99},100,true),/Driven by You/);
 assert.doesNotMatch(render({turn_driver:{kind:'human',at:99}},100),/conv-driver-owner/);
 assert.match(render({turn_driver:{kind:'service',name:'scheduler',at:99}},100,true),/Driven by Service scheduler/);
 assert.match(render({turn_driver:{kind:'background',at:99}},100,true),/Driven by Background task/);
 assert.equal(render({id:'unknown'},100),'');
 assert.match(render({id:'unknown'},100,true),/Driven by Unknown/);
 assert.match(render({id:'unknown'},100,true),/You · no input recorded/);
 assert.equal(render({source:'backlog'}),'');
});
test('untrusted names are escaped and malformed timestamps do not break rows',()=>{
 const html=render({id:'" onmouseover="bad',turn_driver:{kind:'session',name:'<img src=x>',at:1e100}},100);
 assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);assert.match(html,/not recorded/);
});
test('driver clock changes do not invalidate the sidebar structural signature',()=>{
 const begin=source.indexOf('  const _VOLATILE_TIME_RE ='),end=source.indexOf('\n',begin);
 const re=vm.runInNewContext(source.slice(begin,end)+'\n_VOLATILE_TIME_RE;');
 const row={id:'target',turn_driver:{kind:'session',name:'Reviewer',at:100},last_owner_input_at:50};
 const first=render(row,500),second=render(row,560);
 assert.notEqual(first,second);
 assert.equal(first.replace(re,'$1$3'),second.replace(re,'$1$3'));
});

test('list badge stays compact and retains owner detail only in tooltip and header',()=>{
 const row={id:'child',turn_driver:{kind:'session',id:'12345678-1234-1234-1234-123456789abc',basis:'subagent',at:100}};
 const list=render(row,200);
 assert.match(list,/conv-driver-label">Session 12345678/);
 assert.doesNotMatch(list,/conv-driver-owner|conv-driver-label">Driven by Session/);
 assert.match(list,/Last owner input: not recorded/);
 assert.doesNotMatch(render(row,200,true),/conv-driver-owner/);
 row.last_owner_input_at=50;
 assert.match(render(row,200,true),/conv-driver-owner/);
 assert.doesNotMatch(render(row,200),/conv-driver-owner/);
});
