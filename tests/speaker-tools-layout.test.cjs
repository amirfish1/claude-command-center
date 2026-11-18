const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {functionSource} = require('./conversation-source.cjs');

test('hidden speaker controls never read geometry and update when revealed or resized', () => {
  const classes = new Set();
  let reads = 0, shown = false, buttonSize = 56, dialSize = 84;
  const next = {}, tools = {style: {}, querySelector: () => next};
  const btn = {_speakerTools: tools, classList: {contains: name => classes.has(name)}};
  for (const [object, name, value] of [[tools, 'offsetWidth', () => dialSize],
    [btn, 'offsetWidth', () => buttonSize], [btn, 'offsetLeft', () => 100], [btn, 'offsetTop', () => 200]]) {
    Object.defineProperty(object, name, {get() {
      assert.ok(shown, 'a hidden ring must not force a transcript layout');
      reads++; return value();
    }});
  }
  const context = vm.createContext({});
  const sync = vm.runInContext('(' + functionSource('_syncSpeakerTools') + ')', context);
  sync(btn);
  assert.equal(tools.hidden, true);
  assert.equal(reads, 0);
  assert.equal(next.disabled, true);

  shown = true; classes.add('visible'); sync(btn);
  assert.equal(tools.hidden, false);
  assert.deepEqual(tools.style, {left: '86px', top: '186px'});
  buttonSize = 40; dialSize = 56; classes.add('speaking'); sync(btn);
  assert.deepEqual(tools.style, {left: '92px', top: '192px'});
  assert.equal(next.disabled, false);

  classes.delete('visible'); shown = false; const before = reads; sync(btn);
  assert.equal(reads, before);
  assert.equal(next.disabled, false, 'playback state still updates while hidden');
});
