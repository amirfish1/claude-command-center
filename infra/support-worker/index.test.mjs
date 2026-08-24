import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { ReportSubmission, handleRequest, validateEnvelope } from './index.js';

const envelope = () => ({
  schema_version: 1,
  request_id: '123e4567-e89b-42d3-a456-426614174000',
  ccc_version: '5.19.0',
  report_text: 'the exact visible text',
});

class Storage {
  constructor() { this.values = new Map(); this.alarmAt = 0; }
  get(key) { return this.values.get(key); }
  put(key, value) { this.values.set(key, value); }
  setAlarm(value) { this.alarmAt = value; }
  deleteAll() { this.values.clear(); }
}

function submissionEnv() {
  const sent = [];
  return {
    sent,
    env: {
      SUPPORT_TO: 'maintainer@example.invalid',
      SUPPORT_FROM: 'support@example.invalid',
      EMAIL: { send: async message => { sent.push(message); return { messageId: 'hidden' }; } },
    },
  };
}

test('closed envelope rejects unknown fields and oversized text', () => {
  assert.equal(validateEnvelope({ ...envelope(), session_id: 'hidden' }), 'invalid envelope fields');
  assert.match(validateEnvelope({ ...envelope(), report_text: 'x'.repeat(48_001) }), /exceeds/);
});

test('durable submission sends exact text once and stores no body', async () => {
  const storage = new Storage();
  const { env, sent } = submissionEnv();
  const actor = new ReportSubmission({ storage }, env);
  const makeRequest = () => new Request('https://internal/', {
    method: 'POST', body: JSON.stringify(envelope()),
    headers: { 'Content-Type': 'application/json' },
  });
  const first = await (await actor.fetch(makeRequest())).json();
  const second = await (await actor.fetch(makeRequest())).json();
  assert.deepEqual(first, { ok: true, report_id: 'RPT-123E4567' });
  assert.deepEqual(second, first);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].text, envelope().report_text);
  assert.equal(sent[0].subject, '[RPT-123E4567] CCC private queue diagnostics');
  assert.equal(JSON.stringify([...storage.values]), '[["result",{"ok":true,"report_id":"RPT-123E4567"}]]');
});

test('public handler rate limits before delivery', async () => {
  const env = {
    REPORT_RATE_LIMITER: { limit: async () => ({ success: false }) },
    REPORT_SUBMISSIONS: { idFromName: () => assert.fail('must not dispatch') },
  };
  const response = await handleRequest(new Request('https://support.test/v1/report', {
    method: 'POST', body: JSON.stringify(envelope()),
    headers: { 'Content-Type': 'application/json', 'CF-Connecting-IP': '192.0.2.1' },
  }), env);
  assert.equal(response.status, 429);
});

test('source contains no application logging', () => {
  const source = fs.readFileSync(new URL('./index.js', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /console\.(?:log|error)/);
});
