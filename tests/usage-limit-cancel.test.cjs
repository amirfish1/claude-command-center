const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const app = require('./legacy-dashboard-source.cjs');

test('usage-limit auto-resume stays out of the composer UI', () => {
  assert.doesNotMatch(app, /usage-limit-resume-banner/);
  assert.doesNotMatch(app, /RESUMING IN/);
  assert.doesNotMatch(app, /_cancelUsageLimitAutoResume/);
});
