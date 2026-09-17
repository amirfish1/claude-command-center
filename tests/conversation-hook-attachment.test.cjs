const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

test('app.css contains .event.attachment and hook-attachment styles', () => {
  const css = fs.readFileSync(path.join(__dirname, '../static/app.css'), 'utf8');
  assert.ok(css.includes('.event.attachment'), 'should define .event.attachment');
  assert.ok(css.includes('.hook-attachment-details'), 'should define .hook-attachment-details');
  assert.ok(css.includes('.hook-attachment-summary'), 'should define .hook-attachment-summary');
  assert.ok(css.includes('.hook-attachment-badge'), 'should define .hook-attachment-badge');
  assert.ok(css.includes('.hook-attachment-preview'), 'should define .hook-attachment-preview');
  assert.ok(css.includes('.hook-attachment-body'), 'should define .hook-attachment-body');
});

test('app.js handles attachment event rendering', () => {
  const js = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
  assert.ok(js.includes("ev.type === 'attachment'"), 'should handle ev.type === attachment');
  assert.ok(js.includes('hook-attachment-details'), 'should create hook-attachment-details');
  assert.ok(js.includes('hook-attachment-summary'), 'should create hook-attachment-summary');
  assert.ok(js.includes('hook-attachment-badge'), 'should create hook-attachment-badge');
  assert.ok(js.includes('hook-attachment-body'), 'should create hook-attachment-body');
});
