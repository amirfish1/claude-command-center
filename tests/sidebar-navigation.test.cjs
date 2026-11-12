const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/sidebar-navigation.js'), 'utf8');
function storage() {
  const values = new Map();
  return {getItem:k=>values.has(k)?values.get(k):null, setItem:(k,v)=>values.set(k,String(v))};
}
function open(shared, session=storage()) {
  const window = {};
  vm.runInNewContext(source, {window, localStorage:shared, sessionStorage:session});
  return {nav:window.CCCNavigation, session};
}
test('windows start on All, diverge while open, and return to All on reload', () => {
  const shared = storage(); shared.setItem('ccc-sidebar-tab','ideas');
  const a=open(shared), b=open(shared);
  assert.equal(a.nav.getTab(),'alltickets'); assert.equal(b.nav.getTab(),'alltickets');
  a.nav.setTab('decisions2'); b.nav.setTab('jobs');
  shared.setItem('ccc-sidebar-tab','workers');
  assert.equal(a.nav.getTab(),'decisions2'); assert.equal(b.nav.getTab(),'jobs');
  assert.equal(open(shared,a.session).nav.getTab(),'alltickets');
  assert.equal(open(shared,b.session).nav.getTab(),'alltickets');
  assert.equal(shared.getItem('ccc-sidebar-tab'),'workers');
});
test('fresh browsers and restored mobile sessions ignore saved Features selections', () => {
  const shared=storage(), a=open(shared);
  shared.setItem('ccc-sidebar-tab','workers');
  assert.equal(open(shared,a.session).nav.getTab(),'alltickets');
  assert.equal(open(storage()).nav.getTab(),'alltickets');
  a.session.setItem('ccc-sidebar-tab','ideas');
  assert.equal(open(shared,a.session).nav.getTab(),'alltickets');
});
test('blocked browser storage retains a usable per-window in-memory selection', () => {
  const blocked={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}};
  const a=open(blocked,blocked),b=open(blocked,blocked);
  assert.equal(a.nav.getTab(),'alltickets'); assert.equal(b.nav.getTab(),'alltickets');
  a.nav.setTab('decisions2'); b.nav.setTab('ideas');
  assert.equal(a.nav.getTab(),'decisions2'); assert.equal(b.nav.getTab(),'ideas');
});
