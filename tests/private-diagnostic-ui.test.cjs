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
