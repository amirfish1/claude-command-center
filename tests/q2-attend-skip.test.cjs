const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const serverPy = fs.readFileSync('server.py', 'utf8');
const q2js = fs.readFileSync('static/q2.js', 'utf8');
const css = fs.readFileSync('static/q2.css', 'utf8');

function sliceFn(src, startMark, endMark) {
  const start = src.indexOf(startMark);
  assert.ok(start >= 0, `${startMark.trim()} found`);
  const end = src.indexOf(endMark, start);
  assert.ok(end > start, `end marker ${endMark.trim()} found after start`);
  return src.slice(start, end);
}

test('server: skip endpoint routes to _wt_queue_attend_skip', () => {
  assert.match(serverPy, /if path == "\/api\/queue\/attend\/skip":/, 'skip route registered');
  const route = sliceFn(serverPy, 'if path == "/api/queue/attend/skip":', 'if path == "/api/ux-fixes/set-priority":');
  assert.match(route, /_wt_queue_attend_skip\(/, 'route calls the skip helper');
  assert.match(route, /send_json\(result, 200 if result\.get\("ok"\) else 400\)/, 'route maps ok/error to status');
});

test('server: skip defers pending question into skipped_questions', () => {
  const fn = sliceFn(serverPy, 'def _wt_queue_attend_skip(queue):', '\ndef _queue_config_from_payload');
  assert.match(fn, /record\.pop\("pending_question", None\)/, 'pending slot cleared so the next question can escalate');
  assert.match(fn, /record\["skipped_questions"\] = skipped/, 'question stored in skipped_questions');
  assert.match(fn, /skipped\[-_QUEUE_ATTENDANT_SKIPPED_CAP:\]/, 'list bounded by the cap');
  assert.match(fn, /no pending question to skip/, 'skipping without a pending question is an error');
  assert.match(fn, /Owner answer: Skip for now/, 'attendant resumed with a move-on directive');
});

test('server: re-escalating a skipped ticket drops it from the skipped list', () => {
  const fn = sliceFn(serverPy, 'def _wt_queue_attend_question(queue, ref, question, options):', 'def _wt_queue_attend_answer');
  assert.match(fn, /record\.pop\("skipped_questions", None\)/, 'matching skipped entry removed on re-escalation');
  assert.match(fn, /str\(s\.get\("ref"\) or ""\) == ref_norm/, 'match is by ticket ref');
});

test('server: next run prompt resurfaces skipped questions', () => {
  const fn = sliceFn(serverPy, 'def _wt_queue_attend_prompt(queue, repo_path, skipped=None):', 'def _wt_queue_attend_status');
  assert.match(fn, /SKIP-FOR-NOWed/, 'prompt names the skipped escalations');
  assert.match(fn, /ONE at a time/, 're-escalation keeps one-question-at-a-time semantics');
  const start = sliceFn(serverPy, 'def _wt_queue_attend_start(queue):', 'def _wt_queue_attend_report');
  assert.match(start, /_wt_queue_attend_prompt\(queue_norm, repo_path, skipped\)/, 'start feeds skipped list to the prompt');
  assert.match(start, /record\["skipped_questions"\] = skipped/, 'start carries skipped list into the fresh record');
});

test('server: status payload exposes skipped_questions', () => {
  const fn = sliceFn(serverPy, 'def _wt_queue_attend_status(queue):', 'def _wt_queue_attend_start');
  assert.match(fn, /"skipped_questions":/, 'skipped list present in both return dicts');
  assert.ok((fn.match(/"skipped_questions":/g) || []).length >= 2, 'idle and record branches both return it');
});

test('board: waiting-phase card has a Skip for now button', () => {
  const render = sliceFn(q2js, "} else if (phase === 'waiting') {", '} else {');
  assert.match(render, /data-q2-attend-skip-now/, 'skip button rendered in the waiting head');
  assert.match(render, /Skip for now/, 'button label');
  assert.match(render, /attendQuestionHtml\(state\.attendQuestion\) \+ attendSkippedHtml\(\)/, 'skipped note appended under the question card');
  assert.match(render, /data-q2-attend-refresh/, 'existing Refresh button untouched');
});

test('board: skipAttendQuestion posts to the skip endpoint and resumes working', () => {
  const fn = sliceFn(q2js, 'async function skipAttendQuestion() {', '\n  // ── render: chrome');
  assert.match(fn, /postJson\('\/api\/queue\/attend\/skip', \{ queue: queue \}\)/, 'posts to the skip endpoint');
  assert.match(fn, /state\.attendQuestion = null;/, 'pending question cleared locally');
  assert.match(fn, /state\.attendPhase = 'working';/, 'card flips back to working');
  assert.match(fn, /state\.attendAnswerError = e\.message/, 'failure surfaces inline');
});

test('board: click routes and poll state', () => {
  assert.match(q2js, /e\.target\.closest\('\[data-q2-attend-skip-now\]'\)[\s\S]*?skipAttendQuestion\(\)/, 'click handler calls skipAttendQuestion');
  assert.match(q2js, /state\.attendSkipped = \(data && data\.skipped_questions\) \|\| \[\];/, 'poll response feeds attendSkipped');
  assert.match(q2js, /attendSkipping: false/, 'in-flight flag in state');
});

test('board: skipped note rendered in ended phases and styled', () => {
  assert.match(q2js, /function attendSkippedHtml\(\)/, 'helper exists');
  const render = sliceFn(q2js, 'if (state.attendLastReport && state.attendLastReport.summary) {', '} else {\n        bodyHtml = \'\';');
  assert.match(render, /attendSkippedHtml\(\)/, 'note appended to the done/idle bodies');
  assert.match(css, /\.q2-attend-skipped \{/, 'style rule exists');
});
