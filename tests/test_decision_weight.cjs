const test = require('node:test');
const assert = require('node:assert');
const weight = require('../static/decision-weight.js');

test('real money, customers, public and permanent titles are major with a reason', () => {
  assert.deepEqual(weight({title: 'Honor the $99 offer for 164 studios?'}), {level: 'Major', reason: 'moves real money'});
  assert.equal(weight({title: 'Spend about 39 cents on a test?'}).level, 'Major');
  assert.equal(weight({title: 'Answer the 3 new email replies?'}).reason, 'reaches customers');
  assert.equal(weight({title: 'Replace the public demo?'}).reason, 'is public');
  assert.equal(weight({title: 'Erase copies when a user is permanently deleted?'}).reason, "can't be undone");
});

test('anything else is minor, and risk words in the body do not raise it', () => {
  const row = {title: 'Log back in on the server', body: 'A $500 charge cannot be undone.'};
  assert.equal(weight(row).level, 'Minor');
});

test('assistant behavior alone is minor; actual consequences still count', () => {
  assert.equal(weight({title: "Change the assistant's instructions?"}).level, 'Minor');
  assert.equal(weight({title: "Adjust the assistant's memory wording?"}).level, 'Minor');
  assert.equal(weight({title: 'Have the assistant refund charges?'}).reason, 'moves real money');
  assert.equal(weight({title: 'Have the assistant send customers a message?'}).reason, 'reaches customers');
});
