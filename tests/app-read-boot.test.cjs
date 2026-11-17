const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/app-read-boot.js','utf8');
function run(reads){
  const calls=[],listeners={},window={addEventListener:(name,fn)=>{listeners[name]=fn;}};
  const document={getElementById:()=>({textContent:JSON.stringify({app:'demo',reads}),remove(){}})};
  const fetch=async(input,options)=>{calls.push({input,options});return {ok:true,json:async()=>({fresh:true})};};
  vm.runInNewContext(source,{window,document,URL,location:{href:'https://example.test/view/demo',origin:'https://example.test'},fetch});
  return {api:window.CCCAppReads,calls,listeners};
}
test('declared exact read is consumed once and then uses the real network',async()=>{
  const r=run({'/app-static/demo/items.json':'{"rows":[1]}'});
  assert.equal(JSON.stringify(await r.api.json('/app-static/demo/items.json')),'{"rows":[1]}');
  assert.equal(r.calls.length,0);
  assert.equal((await r.api.json('/app-static/demo/items.json')).fresh,true);
  assert.equal(r.calls.length,1);assert.equal(r.calls[0].options.method,'GET');
  assert.equal(r.calls[0].options.cache,'no-store');
});
test('origin/query mismatch and malformed data keep existing fetch semantics',async()=>{
  const r=run({'/app-static/demo/items.json':'broken'});
  await r.api.json('https://other.test/app-static/demo/items.json');
  await r.api.json('/app-static/demo/items.json?refresh=1');
  await r.api.json('/app-static/demo/items.json');
  assert.equal(r.calls.length,3);
});
for(const event of ['ccc:app-ready','ccc:app-fully-ready','pagehide'])test(event+' releases unused bootstrap values',async()=>{
  const r=run({'/api/apps/data?app=demo&key=choice':'{"value":1}'});
  r.listeners[event]();
  assert.equal((await r.api.json('/api/apps/data?app=demo&key=choice')).fresh,true);
  assert.equal(r.calls.length,1);
});

test('aborted reads do not consume a supplied value',async()=>{
  const r=run({'/app-static/demo/items.json':'{"rows":[1]}'});
  const abort=new AbortController();abort.abort();
  await assert.rejects(r.api.json('/app-static/demo/items.json',{signal:abort.signal}),error=>error.name==='AbortError');
  assert.equal(r.calls.length,0);
  assert.equal((await r.api.json('/app-static/demo/items.json')).rows[0],1);
});
