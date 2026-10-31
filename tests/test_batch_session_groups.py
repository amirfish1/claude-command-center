"""Batch runs fold by their task while preserving real session trees."""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_batch_groups_match_task_and_preserve_boundaries():
    source = (ROOT / 'static/app.js').read_text()
    keys = source[source.index('    const _repeatGroupRawTitle ='):source.index('    const _repeatGroupStorageKey =')]
    fold = source[source.index('    const _foldRepeatTaskClusters ='):source.index('    const _allTabClusters =')]
    script = '''
const assert = require('node:assert/strict');
const rowRawTitle = c => ({rawTitle:c.display_name});
const sidebarRowDisplayTitle = s => s;
''' + keys + fold + '''
const opening = 'Summarize the customer support transcripts from the last 24 hours.';
const row = (id, title, extra = {}) => ({id, display_name:title, engine:'hermes',
  hermes_source:'cli', hermes_profile:'support', model:'test-model',
  folder_path:'/example/support', first_message:opening + '\\n\\nChannel: ' + id, ...extra});
const a = row('a', 'Daily support activity #3');
const b = row('b', 'Review customer updates #12');
assert.equal(_repeatGroupKey(a), _repeatGroupKey(b));
assert.equal(_repeatGroupKey(a), _repeatGroupKey({...b, hermes_profile:'', repeat_group_profile:'support'}));
assert.notEqual(_repeatGroupKey(a), _repeatGroupKey({...b, hermes_profile:'', repeat_group_profile:'other'}));
assert.equal(_repeatGroupTitle(a), opening.slice(0, -1));
for (const extra of [{hermes_profile:'other'}, {model:'other'}, {folder_path:'/example/other'},
  {hermes_source:'chat'}, {name_overridden:true}, {engine:'codex'},
  {first_message:'A different task that has enough words to qualify here.\\n\\nInput: b'}]) {
  assert.notEqual(_repeatGroupKey(a), _repeatGroupKey(row('b', 'Different title', extra)));
}
assert.equal(_repeatTaskTitle(row('c', 'Short', {first_message:'Do this\\n\\nInput'})), '');
assert.equal(_repeatTaskTitle(row('c', 'Single', {first_message:opening})), '');
const cluster = c => ({rows:[{card:c, depth:0}]});
const unrelated = cluster(row('u', 'Unrelated', {first_message:'Inspect settings'}));
const tree = {rows:[{card:row('parent', 'Parent'), depth:0}, {card:row('child', 'Child'), depth:1}]};
const input = [cluster(a), unrelated, cluster(b), tree];
const result = _foldRepeatTaskClusters(input);
assert.equal(result.length, 3);
assert.deepEqual(result[0].rows.map(x => x.card.id), ['a', 'b']);
assert.equal(result[0].repeatKey, _repeatGroupKey(a));
assert.equal(result[1], unrelated);
assert.equal(result[2], tree);
assert.equal(input[0].rows.length, 1, 'folding must not mutate input clusters');
'''
    subprocess.run(['node', '-e', script], cwd=ROOT, check=True, capture_output=True, text=True)
