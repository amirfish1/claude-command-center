const assert = require('assert');
const qt = require('../static/question-tags.js');
const parts = qt.splitTags('ask [Q33:gap-month] now, again [Q33:gap-month].');
assert.strictEqual(parts.filter(p => p.tag).length, 2);
assert.strictEqual(parts[0].text, 'ask ');
assert.strictEqual(parts[1].n, 33);
assert.deepStrictEqual(qt.splitTags('no tags [Q] or [Q1] here').filter(p => p.tag), []);
assert.strictEqual(qt.openQuestions([{ answered: true }, { answered: false }]).length, 1);
console.log('ok');
// CCC-258: bare "Q528" / "[Q528]" chip only when the index knows the number.
const known = n => n === 528;
const short = qt.splitTags('Existing Q528 evidence; [Q528] answered; Q4 revenue; Q999 too', known);
assert.deepStrictEqual(short.filter(p => p.tag).map(p => [p.n, p.short]), [[528, true], [528, true]]);
assert.strictEqual(short.map(p => p.text || p.tag).join(''), 'Existing Q528 evidence; [Q528] answered; Q4 revenue; Q999 too');
assert.deepStrictEqual(qt.splitTags('Existing Q528 evidence').filter(p => p.tag), []);
assert.deepStrictEqual(qt.splitTags('FAQ528 and Q528x', known).filter(p => p.tag), []);
console.log('ok short refs');
