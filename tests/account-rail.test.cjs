/* Rail account affordance: state semantics and wiring.
 *
 * cccAccountState() is sliced out of static/app-rail.js and exercised in a
 * vm context (same technique as worker-ticket-history.test.cjs) so every
 * presentation state is asserted without a browser: the green dot is gated
 * on paired + enabled + loop online, and nothing else may turn green.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const rail = fs.readFileSync('static/app-rail.js', 'utf8');
const cloudRelay = fs.readFileSync('static/cloud-relay.js', 'utf8');

function harness() {
  const source = rail.slice(
    rail.indexOf('  function letterFor('),
    rail.indexOf('  function setGlyph('));
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  return ctx.cccAccountState;
}

const pairedOnline = {
  paired: true, enabled: true, env_disabled: false,
  account_email_masked: 'a***@example.com', device_id: 'dev-1234',
  loop: { state: 'online' },
};

test('green dot only when paired + enabled + loop online', () => {
  const st = harness();
  assert.equal(st(pairedOnline, true).dot, 'on');
  assert.equal(st(pairedOnline, true).key, 'online');
  assert.equal(st(pairedOnline, true).paired, true);
  assert.equal(st(pairedOnline, true).letter, 'A');
  // Every near-miss stays off green.
  for (const s of [
    { ...pairedOnline, loop: { state: 'offline' } },
    { ...pairedOnline, loop: { state: 'error', last_error: 'ws closed' } },
    { ...pairedOnline, loop: { state: 'connecting' } },
    { ...pairedOnline, loop: { state: 'off' } },
    { ...pairedOnline, loop: null },
    { ...pairedOnline, enabled: false },
    { ...pairedOnline, env_disabled: true },
    { ...pairedOnline, paired: false },
  ]) {
    const r = st(s, true);
    assert.notEqual(r.dot, 'on', JSON.stringify(s));
  }
});

test('offline and error states are amber, disabled/unpaired grey', () => {
  const st = harness();
  assert.equal(st({ ...pairedOnline, loop: { state: 'offline' } }, true).dot, 'warn');
  assert.equal(st({ ...pairedOnline, loop: { state: 'error' } }, true).dot, 'warn');
  assert.equal(st({ ...pairedOnline, loop: { state: 'connecting' } }, true).dot, 'warn');
  assert.equal(st({ ...pairedOnline, enabled: false }, true).dot, 'off');
  assert.equal(st({ ...pairedOnline, paired: false }, true).dot, 'off');
  assert.equal(st({ ...pairedOnline, env_disabled: true }, true).dot, 'off');
});

test('loading and unavailable are never green and never claim pairing', () => {
  const st = harness();
  // Unfetched: cached data may exist but nothing is confirmed yet.
  assert.equal(st(pairedOnline, false).key, 'loading');
  assert.equal(st(undefined, false).key, 'loading');
  assert.equal(st(undefined, true).key, 'loading');
  // Fetch failed / non-OK: honest error state, amber, no paired actions.
  const un = st(null, true);
  assert.equal(un.key, 'unavailable');
  assert.equal(un.dot, 'warn');
  assert.equal(un.paired, false);
});

test('unpaired state invites sign-in; env-disabled explains itself', () => {
  const st = harness();
  const un = st({ paired: false, enabled: false, device_id: '' }, true);
  assert.equal(un.key, 'unpaired');
  assert.match(un.sub, /sign in/i);
  const env = st({ paired: true, enabled: true, env_disabled: true,
    account_email_masked: 'a***@example.com', device_id: 'd' }, true);
  assert.equal(env.key, 'env_disabled');
  assert.match(env.sub, /disabled/i);
});

test('identity comes from the masked email and machine from device_id', () => {
  const st = harness();
  const r = st(pairedOnline, true);
  assert.equal(r.who, 'a***@example.com');
  assert.equal(r.machine, 'dev-1234');
  assert.equal(st({ ...pairedOnline, account_email_masked: '' }, true).who, 'Paired device');
});

test('the affordance is wired into the shared rail', () => {
  for (const needle of [
    'ccc-account', 'ccc-account-pop', 'ccc-account-dot',
    'aria-expanded', 'aria-haspopup',
    'Sign out of this machine',
    '/api/cloud-relay/indicator', '/api/cloud-relay/status',
    '/api/cloud-relay/unpair',
  ]) {
    assert.ok(rail.includes(needle), `rail missing ${needle}`);
  }
  // Avatar hangs off the viewport bottom via a fixed wrapper, so a long
  // app list can scroll underneath without pushing it away; the report
  // flag is the only rail-foot element left in the nav flow, centred in
  // the free space by its auto margins.
  assert.match(rail, /\.ccc-account-wrap \{/);
  assert.match(rail, /ccc-account-wrap \{[^}]*position: fixed[^}]*bottom: 8px/s);
  assert.match(rail, /document\.body\.appendChild\(accWrap\)/);
  assert.doesNotMatch(rail, /nav\.appendChild\(accountBtn\)/);
  assert.match(rail, /nav\.appendChild\(flag\)/);
  assert.match(rail, /margin-top: auto; margin-bottom: auto/);
  assert.doesNotMatch(rail, /ccc-report-flag \{[^}]*position: fixed/s);
  // Hidden with the rest of the rail chrome on phones.
  assert.match(rail, /max-width: 700px[\s\S]*ccc-account-wrap[\s\S]*ccc-account-pop/);
});

test('status fetch is bounded, latest-wins, and degrades to /status', () => {
  // Indicator-first with a 404/405 fallback so older servers keep working.
  assert.match(rail, /get\("\/api\/cloud-relay\/indicator"\)/);
  assert.match(rail, /r\.status === 404 \|\| r\.status === 405/);
  assert.match(rail, /get\("\/api\/cloud-relay\/status"\)/);
  // Timeout: an unanswered poll becomes "unavailable", not a hung dot.
  assert.match(rail, /AbortController/);
  assert.match(rail, /STATUS_TIMEOUT_MS = \d+/);
  // Latest-response-wins: a stale answer cannot repaint over a newer one.
  assert.match(rail, /statusSeq/);
  assert.match(rail, /seq !== statusSeq/);
  // Popover actions are stable nodes so repaint never drops focus.
  assert.match(rail, /var popPrimary = document\.createElement\("button"\)/);
  assert.match(rail, /var popSecondary = document\.createElement\("button"\)/);
  assert.doesNotMatch(rail, /popActions\.textContent = ""/);
});

test('sign in reaches the real Network dialog or hands off by URL', () => {
  // On the dashboard the existing button opens networkOpen() — the actual
  // pairing flow — rather than a look-alike dialog.
  assert.match(rail, /getElementById\("networkAccessBtn"\)/);
  assert.match(rail, /btn\.click\(\)/);
  // Everywhere else the rail consumes its own ?open_network=1 handoff.
  assert.match(rail, /NETWORK_HANDOFF_PARAM = "open_network"/);
  assert.match(rail, /openNetworkFromLocation/);
  // No cloud service URL is baked into the client.
  assert.doesNotMatch(rail, /https:\/\/[a-z0-9.-]+\.(com|net|io|dev|ai|app)/i);
});

test('pairing changes in the Network dialog repaint the avatar', () => {
  assert.match(cloudRelay, /ccc-cloud-relay-change/);
  assert.match(rail, /addEventListener\("ccc-cloud-relay-change"/);
  // Bounded cadence plus visibility handling, not an unbounded poll.
  assert.match(rail, /ACCOUNT_POLL_MS = 30000/);
  assert.match(rail, /visibilitychange/);
  assert.match(rail, /document\.hidden/);
});
