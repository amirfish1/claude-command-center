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

test('publishing a source commit to a branch is not public exposure', () => {
  const title = 'May I publish tested commit abcdef123 to next to add opt-in 15-second following for urgent project releases, without starting a production release?';
  assert.equal(weight({title}).level, 'Minor');
  assert.equal(weight({title: 'Publish verified commit abcdef123 to origin/main?'}).level, 'Minor');
  assert.equal(weight({title: 'Publish commit abcdef123 to feature/release-follow?'}).level, 'Minor');
  assert.equal(weight({title: 'Approve this source commit because a rule requires explicit permission?'}).level, 'Minor');
});

test('source-publication wording preserves actual consequential actions', () => {
  for (const [suffix, reason] of [
    ['and start a production release', 'changes the live product'],
    ['and publish a public announcement', 'is public'],
    ['and send customers a message', 'reaches customers'],
    ['and charge $99', 'moves real money'],
    ['and permanently erase archived records', "can't be undone"],
  ]) {
    assert.equal(weight({title: `Publish tested commit abcdef123 to next ${suffix}?`}).reason, reason);
  }
  assert.equal(weight({title: 'Publish a new website?'}).reason, 'is public');
  assert.equal(weight({title: 'Publish commit abcdef123 to the public website?'}).reason, 'is public');
  assert.equal(weight({title: 'Publish commit abcdef123 to customers?'}).reason, 'is public');
  assert.equal(weight({title: 'Start a production release without starting a production release?'}).level, 'Major');
});
