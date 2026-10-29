const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');

function harness() {
  const streams = [], blobs = [];
  const context = vm.createContext({
    ReadableStream, Response, Blob,
    URL: { createObjectURL: blob => { blobs.push(blob); return 'blob:complete'; } },
    _ttsStreamUrl: response => { streams.push(response.body.getReader()); return 'blob:stream'; },
  });
  const start = source.indexOf('  function _ttsStreamClip(');
  vm.runInContext(source.slice(start, source.indexOf('  function _ttsFetchNeural(', start)), context);
  let input, resolve, reject;
  const done = new Promise((a, b) => { resolve = a; reject = b; });
  done.catch(() => {});
  const response = new Response(new ReadableStream({start(controller) { input = controller; }}));
  const clip = context._ttsStreamClip(response, done, resolve, reject);
  return { input, clip, streams, blobs };
}

const tick = () => new Promise(resolve => setImmediate(resolve));

test('in-flight preparation can play and replay received bytes before EOF, then retain a blob', async () => {
  const {input, clip, streams, blobs} = harness();
  let complete = false;
  clip.done.then(() => { complete = true; });
  input.enqueue(new Uint8Array([1, 2]));
  await tick();
  assert.equal(streams.length, 0, 'preparation must not create a player');
  assert.equal(clip.blob, null);
  clip.createUrl();
  assert.deepEqual(Array.from((await streams[0].read()).value), [1, 2]);
  assert.equal(complete, false, 'playback bytes available before completion');
  clip.createUrl();
  assert.deepEqual(Array.from((await streams[1].read()).value), [1, 2]);
  input.enqueue(new Uint8Array([3, 4]));
  for (const reader of streams) assert.deepEqual(Array.from((await reader.read()).value), [3, 4]);
  input.close();
  await clip.done;
  for (const reader of streams) assert.equal((await reader.read()).done, true);
  assert.deepEqual(Array.from(new Uint8Array(await clip.blob.arrayBuffer())), [1, 2, 3, 4]);
  assert.equal(clip.createUrl(), 'blob:complete');
  assert.equal(streams.length, 2, 'completed replay uses retained blob');
  assert.equal(blobs[0], clip.blob);
});

test('stopping a consumer preserves generation and the replay cache', async () => {
  const {input, clip, streams} = harness();
  clip.createUrl();
  await streams[0].cancel();
  input.enqueue(new Uint8Array([5, 6]));
  input.close();
  await clip.done;
  assert.deepEqual(Array.from(new Uint8Array(await clip.blob.arrayBuffer())), [5, 6]);
});

test('failed streams reject completion and consumers instead of caching truncated audio', async () => {
  const {input, clip, streams} = harness();
  clip.createUrl();
  const read = streams[0].read();
  const failure = new Error('disconnected');
  input.error(failure);
  await assert.rejects(clip.done, /disconnected/);
  await assert.rejects(read, /disconnected/);
  assert.equal(clip.blob, null);
});
