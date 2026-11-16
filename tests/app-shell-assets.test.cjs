const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app-shell-assets.js', 'utf8');

function run(mobile = false) {
  const executed = [], downloads = [], timers = [], events = [];
  let release;
  function marker(src, defer = false, textContent = '') {
    return {src, textContent, attributes: [{name:'type',value:'application/ccc-dashboard'},
      ...(src ? [{name:'src',value:src}] : []), ...(defer ? [{name:'defer',value:''}] : [])],
    hasAttribute: name => name === 'defer' && defer,
    replaceWith(script) {executed.push(script.src || script.textContent); script.onload?.();}};
  }
  const styles = [{dataset:{cccHref:'/static/app.css?v=123'}}];
  const listeners = {};
  const document = {readyState:'complete',
    addEventListener: (name, handler) => { listeners[name] = handler; },
    getElementById: () => ({}),
    documentElement: {classList: {remove() {}}},
    querySelectorAll: selector => selector.startsWith('link') ? styles : [
      marker('/static/deferred.js', true), marker('/static/app-rail.js?v=123',true), marker('/static/first.js'), marker('',false,'inline()'), marker('/static/last.js')],
    createElement: () => ({setAttribute(name,value) {this[name] = value;}}),
    head: {appendChild: link => downloads.push(link.href)}};
  const window = {__cccAppFrameReady:new Promise(resolve => {release=resolve;}),
    dispatchEvent: event => events.push(event.type)};
  vm.runInNewContext(source, {window,document,Event,URLSearchParams,location:{search:''},
    matchMedia:()=>({matches:mobile,addEventListener(){}}),setTimeout:fn => timers.push(fn)});
  return {executed,downloads,styles,release,timers,events,window,listeners};
}

test('desktop navigation starts alone; explicit dashboard intent preserves blocking/defer order', async () => {
  const result=run();
  assert.deepEqual(result.downloads,[]); assert.deepEqual(result.executed,[]);
  assert.equal(result.styles[0].href,undefined);
  result.release(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(result.styles[0].href,'/static/app.css?v=123');
  assert.deepEqual(result.executed,['/static/app-rail.js?v=123']);
  assert.deepEqual(result.downloads,[]);
  assert.deepEqual(result.events,[]);
  const one = result.window.__cccEnsureDashboard();
  assert.equal(one,result.window.__cccEnsureDashboard());
  await one;
  assert.deepEqual(result.executed,['/static/app-rail.js?v=123','/static/first.js','inline()','/static/last.js','/static/deferred.js']);
  assert.equal(result.downloads.length,3);
  assert.deepEqual(result.events,['ccc:dashboard-assets-ready']);
});

test('a missing app-ready signal cannot strand the surrounding navigation',async()=>{
  const result=run(true);result.timers[0]();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(result.executed.length,5);
  result.release();await new Promise(resolve => setImmediate(resolve));
  assert.equal(result.executed.length,5,'late readiness must not replay scripts twice');
});
