// Run with: node tests/share_run_dialog_harness.cjs
// Saving must never hand over an export older than what the boxes say.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'share-run.js'), 'utf8');

function node(tag) {
  return {
    tag, children: [], attrs: {}, classList: { add() {}, remove() {} }, listeners: {}, value: '',
    set textContent(v) { this.text = v; }, get textContent() { return this.text; },
    setAttribute(k, v) { this.attrs[k] = v; if (k === 'disabled') this.disabled = true; if (k === 'hidden') this.hidden = true; },
    addEventListener(k, fn) { this.listeners[k] = fn; },
    append(...kids) { this.children.push(...kids); },
    replaceChildren(...kids) { this.children = kids; },
    remove() {}, click() {},
  };
}

const walk = (root, out = []) => {
  out.push(root);
  (root.children || []).forEach(kid => typeof kid === 'object' && walk(kid, out));
  return out;
};
const settle = () => new Promise(resolve => setImmediate(resolve));

function load() {
  const body = node('body');
  const requests = [];
  const blobs = [];
  const context = {
    window: { localStorage: { getItem: () => null, setItem() {} } },
    document: { body, hidden: false, createElement: node, addEventListener() {}, querySelector: () => null },
    fetch: url => new Promise(resolve => requests.push({
      url,
      reply: html => resolve({ json: async () => ({ ok: true, html, svg: '<svg/>', hidden_summary: 'hidden' }) }),
    })),
    setTimeout: () => 0,
    URL: { createObjectURL: blob => { blobs.push(blob); return 'blob:x'; }, revokeObjectURL() {} },
    Blob: function Blob(parts) { this.text = parts.join(''); },
    URLSearchParams, encodeURIComponent, Number, String, Object, Promise, Date,
  };
  vm.runInNewContext(source, context);
  context.window.cccShareRun.open({ sid: 'abc' });
  const all = () => walk(body);
  return {
    requests, blobs,
    hide: () => all().find(n => n.tag === 'input'),
    button: label => all().find(n => n.tag === 'button' && n.text === label),
  };
}

(async () => {
  const run = load();
  const save = run.button('Save page');
  const image = run.button('Save image');
  assert.equal(save.disabled, true, 'nothing to save before the first export');
  run.requests[0].reply('first');
  await settle();
  assert.equal(save.disabled, false);

  // Typed but not applied: saving is off, and a forced click saves nothing.
  const hide = run.hide();
  hide.value = 'office';
  hide.listeners.input();
  assert.equal(save.disabled, true);
  assert.equal(image.disabled, true);
  save.listeners.click();
  assert.equal(run.blobs.length, 0);

  // Undoing the edit restores the export that matches the boxes.
  hide.value = '';
  hide.listeners.input();
  assert.equal(save.disabled, false);

  // Enter applies, and the new term is in the request.
  hide.value = 'office';
  hide.listeners.input();
  hide.listeners.keydown({ key: 'Enter', preventDefault() {} });
  assert.match(run.requests[1].url, /hide=office/);
  assert.equal(save.disabled, true, 'still off while redacting');

  // A slower, older reply must not replace a newer one.
  hide.value = 'office,paper';
  hide.listeners.keydown({ key: 'Enter', preventDefault() {} });
  run.requests[2].reply('newest');
  await settle();
  run.requests[1].reply('older');
  await settle();
  assert.equal(save.disabled, false);
  save.listeners.click();
  assert.equal(run.blobs[0].text, 'newest');

  // Typing during a request leaves the reply unsaveable.
  hide.value = 'office';
  hide.listeners.keydown({ key: 'Enter', preventDefault() {} });
  hide.value = 'office,table';
  hide.listeners.input();
  run.requests[3].reply('behind the boxes');
  await settle();
  assert.equal(save.disabled, true);

  console.log('share-run dialog harness ok');
})().catch(err => { console.error(err); process.exit(1); });
