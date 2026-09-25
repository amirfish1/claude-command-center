// e0ffdee7 landed its hunks at the wrong offsets: a composer listener ran at
// the dashboard IIFE's top level and threw on every page load, which left the
// "Loading conversations..." screen up forever. Pin each piece to its function.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const app = fs.readFileSync('static/app.js', 'utf8');

function body(name) {
  const start = app.indexOf('  function ' + name + '(');
  assert.ok(start > 0, name + ' exists');
  const end = app.indexOf('\n  }\n', start);
  return app.slice(start, end);
}

test('ticket composer owns its queue select and auto-pick listener', () => {
  const composer = body('openQueueTicketComposer');
  assert.match(composer, /\+\s+\(queueSelectHtml \|\| ''\)\n\s+\+\s+'<\/div>'/, 'select is part of the dialog body markup');
  assert.ok(composer.includes('window.__cccSuggestTicketQueue(textarea.value)'));
  assert.ok(!/const close = \(value\) => \{\n\s+\+/.test(composer), 'no stray markup inside close()');
});

test('global + Ticket handler is a top-level function, not nested in another handler', () => {
  const fn = app.indexOf('  async function _newGlobalTicket(');
  assert.ok(fn > 0);
  const preceding = app.slice(app.lastIndexOf('\n  }\n', fn), fn);
  assert.ok(!preceding.includes('fetch('), 'nothing between the previous function and _newGlobalTicket');
});
