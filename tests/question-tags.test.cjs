const assert = require('assert');
const qt = require('../static/question-tags.js');
const parts = qt.splitTags('ask [Q33:gap-month] now, again [Q33:gap-month].');
assert.strictEqual(parts.filter(p => p.tag).length, 2);
assert.strictEqual(parts[0].text, 'ask ');
assert.strictEqual(parts[1].n, 33);
assert.deepStrictEqual(qt.splitTags('no tags [Q] or [Q1] here').filter(p => p.tag), []);
assert.strictEqual(qt.openQuestions([{ answered: true }, { answered: false }]).length, 1);
console.log('ok');
