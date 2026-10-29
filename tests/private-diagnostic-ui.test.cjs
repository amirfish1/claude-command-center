const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const q2 = fs.readFileSync('static/q2.js', 'utf8');
const app = fs.readFileSync('static/app.js', 'utf8');
const html = fs.readFileSync('static/index.html', 'utf8');

test('Q2 first click opens existing report flow for the selected queue', () => {
  assert.match(q2, /data-q2-report-diagnostics/);
  assert.match(q2, /\?report_diagnostics=/);
});

test('diagnostic mode has one final private action and no destination choice', () => {
  assert.match(app, /async function bugOpenDiagnosticReport/);
  assert.match(app, /bugDiagnosticMode/);
  assert.match(app, /Send privately/);
  assert.match(app, /\/api\/bug-report\/private/);
  assert.match(html, /id="bugReportDestinationSection"/);
  assert.match(html, /id="bugReportIdentitySection"/);
});

const rail = fs.readFileSync('static/app-rail.js', 'utf8');

test('report-a-problem defaults to the private destination', () => {
  assert.match(html, /name="bugDest" value="private" checked/);
  assert.match(html, /name="bugDest" value="github">/);
  assert.doesNotMatch(app, /mailto:/);
  assert.match(app, /bugDestination\(\) === 'private'/);
});

test('7-day diagnostics are attached by default and shown in the preview', () => {
  assert.match(html, /id="bugDiagInclude" checked/);
  assert.match(html, /Show exactly what's sent/);
  assert.match(app, /\/api\/telemetry\/health-report\?hours=/);
  assert.match(app, /BUG_DIAG_HOURS = 168/);
  // The appendix is built into the same markdown the preview renders.
  assert.match(app, /const desc = bugFullDescription\(\);/);
  assert.match(app, /description: bugFullDescription\(\)/);
});

test('every page with the rail gets the report flag, wired to the dashboard modal', () => {
  assert.match(rail, /ccc-report-flag/);
  assert.match(rail, /window\.cccOpenReport/);
  assert.match(rail, /\/\?report_problem=/);
  assert.match(app, /window\.cccOpenReport = function/);
  assert.match(app, /bugOpenProblemFromLocation\(\);/);
});
