const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const app = fs.readFileSync('static/app.js', 'utf8');
function routine(event) {
  const start = app.indexOf('  function _isRoutineInjectEvent(');
  assert.notEqual(start, -1, 'routine inject classifier exists');
  const end = app.indexOf('  function _railLogPaneVisible(', start);
  return new Function(app.slice(start, end) + '; return _isRoutineInjectEvent;')()(event);
}
test('routine inject requests and known fallback skips can be filtered separately', () => {
  for (const event of [
    {category:'inject',verb:'INJECT',detail:'session=test'},
    {category:'inject',verb:'DEDUPE',detail:'session=test'},
    {category:'inject',verb:'UDS-SKIP',detail:'session=test reason=no_socket_path'},
    {category:'inject',verb:'UDS-SKIP',detail:'reason=slash_command_needs_fifo'},
    {category:'inject',verb:'UDS',detail:'receipt=delivered text="hello"'},
    {category:'inject',verb:'UDS',detail:'receipt=queued text="hello"'},
  ]) assert.equal(routine(event),true,JSON.stringify(event));
});
test('failed, blocked, unconfirmed and unknown outcomes stay visible', () => {
  for (const event of [
    {category:'inject',verb:'UDS-FAIL',detail:'error=socket_closed'},
    {category:'inject',verb:'BLOCKED',detail:'reason=throttle'},
    {category:'inject',verb:'FAILED',detail:'delivery failed'},
    {category:'inject',verb:'UDS',detail:'receipt=unknown text="receipt=delivered"'},
    {category:'inject',verb:'UDS',detail:'session=one text="says receipt=delivered exactly"'},
    {category:'inject',verb:'UDS-SKIP',detail:'reason=new_unknown_reason'},
    {category:'inject',verb:'UNRECOGNIZED',detail:'whatever'},
    {category:'spawn',verb:'SPAWN',detail:'session=test'},
  ]) assert.equal(routine(event),false,JSON.stringify(event));
});
