const {test}=require('node:test');
const assert=require('node:assert/strict');
require('../static/conversation-defaults.js');

test('Antigravity exposes native compaction while unsupported engines stay excluded',()=>{
  const initializers=globalThis.CCCConversationDefaults.createInitializers({state:{},helpers:{}});
  const excluded=initializers.COMPACT_UNSUPPORTED_SOURCES();
  assert.equal(excluded.has('antigravity'),false);
  assert.equal(excluded.has('cursor'),true);
  assert.equal(excluded.has('gemini'),true);
});
