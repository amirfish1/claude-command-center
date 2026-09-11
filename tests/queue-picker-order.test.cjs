const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/app.js', 'utf8');
const start = app.indexOf('  function _uxqPickerGroups(');
const end = app.indexOf('  function _uxqPickerQueueRow(', start);
function groups(queues, filter = '', items = []) {
  const context = {
    queues, filter, items, _uxqPicker: {},
    _uxqPickerQueueRow: q => ({kind:'queue', name:q.name}),
    _uxqPickerTicketRow: t => ({kind:'ticket', name:t.ref}),
    _uxqItemRef: t => t.ref,
  };
  vm.createContext(context);
  const result = vm.runInContext(app.slice(start,end) + '\n_uxqPickerGroups(queues, items, filter)', context);
  return {groups:JSON.parse(JSON.stringify(result)),flat:JSON.parse(JSON.stringify(context._uxqPicker.flat))};
}
const queue = (name, age, extra = {}) => ({name, lastActivitySeconds:age, needsInputCount:0, openCount:0, ...extra});
test('all queues appear once in latest-touch order regardless of name, needs-input or family', () => {
  const queues = [queue('ALPHA',900,{needsInputCount:5}),queue('ZETA',10),queue('ALPHA-CHILD',5,{parent:'ALPHA'}),...Array.from({length:6},(_,i)=>queue('MIDDLE-'+i,20+i))];
  const before = queues.map(q=>q.name);
  const out = groups(queues);
  assert.deepEqual(out.flat.map(q=>q.name),['ALPHA-CHILD','ZETA',...Array.from({length:6},(_,i)=>'MIDDLE-'+i),'ALPHA']);
  assert.equal(out.groups.length,1);
  assert.deepEqual(queues.map(q=>q.name),before);
});
test('search keeps matching queues in latest-touch order and ticket results after queues', () => {
  const out = groups([queue('MATCH-OLD',500,{needsInputCount:4}),queue('MATCH-NEW',2),queue('OTHER',0)],'match',[{ref:'MATCH-1',status:'open'}]);
  assert.deepEqual(out.flat.map(q=>q.name),['MATCH-NEW','MATCH-OLD','MATCH-1']);
});
test('missing/invalid activity sorts last with deterministic alphabetical ties', () => {
  const out = groups([queue('Z-UNKNOWN',null),queue('D-BAD',NaN),queue('B-TIE',5),queue('C-NEGATIVE',-1),queue('A-TIE',5)]);
  assert.deepEqual(out.flat.map(q=>q.name),['A-TIE','B-TIE','C-NEGATIVE','D-BAD','Z-UNKNOWN']);
});
test('a refreshed touch moves that queue first on the next render', () => {
  const queues=[queue('FIRST',5),queue('SECOND',20)];
  assert.deepEqual(groups(queues).flat.map(q=>q.name),['FIRST','SECOND']);
  queues[1].lastActivitySeconds=0;
  assert.deepEqual(groups(queues).flat.map(q=>q.name),['SECOND','FIRST']);
});
