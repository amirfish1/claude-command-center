// CCC-202: desktop "Mobile view" twins phone rules under
// html.ccc-mobile-list-view #convList. #convList lives inside .sidebar, so a
// `.sidebar X` phone rule must twin as `#convList X`, not `#convList .sidebar X`.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
function extract(name) {
  const start = src.indexOf('  function ' + name + '(');
  assert.ok(start >= 0, name + ' not found');
  const end = src.indexOf('\n  }\n', start);
  return src.slice(start, end + 4);
}
const factory = new Function('CSSRule', '_MOBILE_LIST_CLASS', '_mobileListPhoneMatch',
  extract('_splitSelectorList') + extract('_mobileListTwinRules') + 'return _mobileListTwinRules;');
const twin = factory({ MEDIA_RULE: 4, STYLE_RULE: 1 }, 'ccc-mobile-list-view', () => true);
const rule = (selectorText, cssText) => ({ type: 1, selectorText, style: { cssText } });

test('a .sidebar-scoped phone rule twins onto #convList descendants', () => {
  const out = twin([rule('.sidebar .conv-worker-history-count', 'display: none;')], []);
  assert.deepStrictEqual(out, [
    ':where(html.ccc-mobile-list-view #convList) .conv-worker-history-count { display: none; }',
  ]);
});

test('#convList, plain and root selectors keep their existing twins', () => {
  const out = twin([
    rule('#convList .conv-item, .conv-pill, body .x, .sidebar', 'color: red;'),
  ], []);
  assert.deepStrictEqual(out, [
    ':where(html.ccc-mobile-list-view) #convList .conv-item, '
      + ':where(html.ccc-mobile-list-view #convList) .conv-pill { color: red; }',
  ]);
});
