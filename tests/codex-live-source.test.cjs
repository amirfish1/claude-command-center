// Slices 1-6 of the Codex single-renderer merge (see
// CCC-private-docs/plans/2026-09-12-codex-single-renderer-merge.md). Slices
// 1-3 built the server action, the data mapper, and the provisional-event
// upsert/renderer plumbing in app.js; Slice 4 wired them together behind an
// off-by-default flag; Slice 5 flipped the flag's default to on; Slice 6
// deleted the native inline renderer (static/codex-client.js) and the flag
// itself entirely, so the live overlay is now the only path -- unconditional,
// no opt-out.
//
// Covers:
//   - the overlay polls the `live-transcript` action, flattens
//     {turns:[...]} into one ordered events array, and hands it to a render
//     hook.
//   - polling stops once the last turn is not in progress and no requests
//     are pending; a later start() call resumes it.
//   - a generation change clears only the provisional (data-live-key) rows,
//     never confirmed (data-jsonl-line) rollout rows.
//   - the three-tier dedupe order (tool call_id, then turn_id+ordinal, then
//     normalized text) against a mock rollout state, using events shaped
//     the way this module's own flattening produces them.
const assert = require('node:assert/strict');
const { before, after, test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer');

const LIVE_SOURCE_JS = fs.readFileSync(path.join(__dirname, '../static/codex-live-source.js'), 'utf8');

const appSource = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const helpersStart = appSource.indexOf('  // --- Provisional (live) event upsert/reconcile helpers');
const helpersEnd = appSource.indexOf('  function renderConversationEvents(', helpersStart);
assert.ok(helpersStart >= 0 && helpersEnd > helpersStart, 'provisional helper block exists before renderConversationEvents');
const dedupeHelpersCode = appSource.slice(helpersStart, helpersEnd);

let browser;
before(async () => { browser = await puppeteer.launch({ headless: true }); });
after(async () => { if (browser) await browser.close(); });

async function withPage(fn) {
  const page = await browser.newPage();
  try {
    return await fn(page);
  } finally {
    await page.close();
  }
}

test('CCCCodexLiveSource.start() polls live-transcript with thread/repo query params and flattens turns in order', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conv-pane is-codex-session" data-pane-id="p1"><div class="conversations-view"></div></div>');
    await page.evaluate(() => {
      window.__fetchUrls = [];
      window.__rendered = [];
      window.CCCCodexClientContext = () => ({ paneId: 'p1', threadId: 'thread-1', repoPath: '/my/repo' });
      window.CCCCodexRenderLiveEvents = (paneId, events) => window.__rendered.push({ paneId, events });
      window.fetch = (url) => {
        window.__fetchUrls.push(String(url));
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true, generation: 'g1', cursor: 3,
            turns: [
              { turn_id: 't1', status: 'inProgress', events: [{ type: 'assistant', turn_id: 't1', live_key: 't1:a', blocks: [{ kind: 'text', text: 'first' }] }] },
              { turn_id: 't2', status: 'inProgress', events: [{ type: 'assistant', turn_id: 't2', live_key: 't2:a', blocks: [{ kind: 'text', text: 'second' }] }] },
            ],
            requests: [],
          }),
        });
      };
    });
    await page.addScriptTag({ content: LIVE_SOURCE_JS });
    const result = await page.evaluate(async () => {
      const pane = document.querySelector('.conv-pane');
      window.CCCCodexLiveSource.start(pane);
      await new Promise((resolve) => setTimeout(resolve, 50));
      return { url: window.__fetchUrls[0], rendered: window.__rendered };
    });
    assert.match(result.url, /^\/api\/codex\/client\/live-transcript\?/);
    const params = new URLSearchParams(result.url.split('?')[1]);
    assert.equal(params.get('thread_id'), 'thread-1');
    assert.equal(params.get('repo_path'), '/my/repo');
    assert.equal(result.rendered.length, 1);
    assert.equal(result.rendered[0].paneId, 'p1');
    assert.deepEqual(result.rendered[0].events.map((e) => e.live_key), ['t1:a', 't2:a']);
  });
});

test('stops polling once the turn completes and no requests are pending', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conv-pane is-codex-session" data-pane-id="p1"><div class="conversations-view"></div></div>');
    await page.evaluate(() => {
      window.__CCC_TEST_LIVE_POLL_MS = 40;
      window.__CCC_TEST_LIVE_POLL_MAX_MS = 40;
      window.__fetchCount = 0;
      window.CCCCodexClientContext = () => ({ paneId: 'p1', threadId: 'thread-1', repoPath: '/repo' });
      window.CCCCodexRenderLiveEvents = () => {};
      window.fetch = () => {
        window.__fetchCount++;
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true, generation: 'g1', cursor: 1, turns: [{ turn_id: 't1', status: 'completed', events: [] }], requests: [] }),
        });
      };
    });
    await page.addScriptTag({ content: LIVE_SOURCE_JS });
    const counts = await page.evaluate(async () => {
      const pane = document.querySelector('.conv-pane');
      window.CCCCodexLiveSource.start(pane);
      await new Promise((resolve) => setTimeout(resolve, 60));
      const first = window.__fetchCount;
      await new Promise((resolve) => setTimeout(resolve, 200));
      const second = window.__fetchCount;
      return { first, second };
    });
    // One fetch to learn the turn is over; no more after that even though
    // plenty of time passed for several more poll intervals.
    assert.equal(counts.first, 1);
    assert.equal(counts.second, 1);
  });
});

test('a generation change clears provisional event and metadata rows, not confirmed rollout rows', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conv-pane is-codex-session" data-pane-id="p1"><div class="conversations-view"><div class="event assistant" data-jsonl-line="5">confirmed</div><div class="event assistant provisional" data-live-key="t1:old">stale provisional</div><div class="kimi-answer-meta" data-live-key="t1:old">stale actions</div></div></div>');
    await page.evaluate(() => { window.CCCCodexRenderLiveEvents = () => {}; });
    await page.addScriptTag({ content: LIVE_SOURCE_JS });
    const result = await page.evaluate(() => {
      const pane = document.querySelector('.conv-pane');
      const view = pane.querySelector('.conversations-view');
      const entry = { paneEl: pane, paneId: 'p1', context: { threadId: 't', repoPath: '/r' }, generation: 'g1', active: true };
      window.CCCCodexLiveSource.__testing.applySnapshot(entry, { generation: 'g2', cursor: 1, turns: [], requests: [] });
      return {
        confirmedSurvived: !!view.querySelector('[data-jsonl-line="5"]'),
        staleProvisionalRemoved: !view.querySelector('[data-live-key="t1:old"]'),
      };
    });
    assert.deepEqual(result, { confirmedSurvived: true, staleProvisionalRemoved: true });
  });
});

// CCC-1145: an overlay that goes quiet WITHOUT a generation change (a stuck
// session's stale turn falling off the app-server's live view) used to
// strand its provisional rows on screen forever — only a generation change
// cleared them. An empty snapshot now clears them too.
test('an empty overlay snapshot clears provisional event and metadata rows even at the same generation', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conv-pane is-codex-session" data-pane-id="p1"><div class="conversations-view"><div class="event assistant" data-jsonl-line="5">confirmed</div><div class="event assistant provisional" data-live-key="t1:old">ghost turn</div><div class="kimi-answer-meta" data-live-key="t1:old">ghost actions</div></div></div>');
    await page.evaluate(() => { window.CCCCodexRenderLiveEvents = () => {}; });
    await page.addScriptTag({ content: LIVE_SOURCE_JS });
    const result = await page.evaluate(() => {
      const pane = document.querySelector('.conv-pane');
      const view = pane.querySelector('.conversations-view');
      const entry = { paneEl: pane, paneId: 'p1', context: { threadId: 't', repoPath: '/r' }, generation: 'g1', active: true };
      const stillActive = window.CCCCodexLiveSource.__testing.applySnapshot(entry, { generation: 'g1', cursor: 2, turns: [], requests: [] });
      return {
        stillActive,
        confirmedSurvived: !!view.querySelector('[data-jsonl-line="5"]'),
        ghostRemoved: !view.querySelector('[data-live-key="t1:old"]'),
      };
    });
    assert.deepEqual(result, { stillActive: false, confirmedSurvived: true, ghostRemoved: true });
  });
});

test('applySnapshot reports active=true while a turn is inProgress or a request is pending', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conv-pane is-codex-session" data-pane-id="p1"><div class="conversations-view"></div></div>');
    await page.evaluate(() => { window.CCCCodexRenderLiveEvents = () => {}; });
    await page.addScriptTag({ content: LIVE_SOURCE_JS });
    const result = await page.evaluate(() => {
      const pane = document.querySelector('.conv-pane');
      const mkEntry = () => ({ paneEl: pane, paneId: 'p1', context: { threadId: 't', repoPath: '/r' }, generation: null, active: true });
      const running = window.CCCCodexLiveSource.__testing.applySnapshot(mkEntry(), { generation: 'g1', turns: [{ turn_id: 't1', status: 'inProgress', events: [] }], requests: [] });
      const pendingRequest = window.CCCCodexLiveSource.__testing.applySnapshot(mkEntry(), { generation: 'g1', turns: [{ turn_id: 't1', status: 'completed', events: [] }], requests: [{ id: 1 }] });
      const idle = window.CCCCodexLiveSource.__testing.applySnapshot(mkEntry(), { generation: 'g1', turns: [{ turn_id: 't1', status: 'completed', events: [] }], requests: [] });
      return { running, pendingRequest, idle };
    });
    assert.deepEqual(result, { running: true, pendingRequest: true, idle: false });
  });
});

test('three-tier dedupe order against a mock rollout state (call_id, then turn_id+ordinal, then text)', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conversations-view"></div>');
    await page.evaluate((code) => {
      (0, eval)(code.replace(/^/, 'window.__fns = (() => {\n')
        + '\nreturn { _turnOrdinalBefore, _findMatchingProvisionalNode };\n})();');
    }, dedupeHelpersCode);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');

      // Tier 1: a rollout tool row whose call_id equals a live item id --
      // this module's own flattenTurns output carries the live item id as
      // `id` on tool_use blocks (see ccc_server/codex_live_events.py's
      // _map_tool_item), matched here via tool_use_id on the rollout event.
      const toolProvisional = document.createElement('div');
      toolProvisional.className = 'event assistant provisional';
      toolProvisional.dataset.liveKey = 't1:call_abc';
      view.appendChild(toolProvisional);
      const toolConfirmed = document.createElement('div');
      toolConfirmed.className = 'event tool_result';
      toolConfirmed.dataset.jsonlLine = '10';
      const tier1Match = window.__fns._findMatchingProvisionalNode(view, { tool_use_id: 'call_abc' }, toolConfirmed);
      const tier1Ok = tier1Match === toolProvisional;
      if (tier1Match) tier1Match.remove();
      view.appendChild(toolConfirmed);

      // Tier 2: no call_id equality possible (per the plan's Slice-1
      // finding, app-server item ids never equal rollout call_ids in
      // practice) -- falls through to turn_id + ordinal position.
      const ordinal = window.__fns._turnOrdinalBefore(view, 't2');
      const turnProvisional = document.createElement('div');
      turnProvisional.className = 'event assistant provisional';
      turnProvisional.dataset.liveKey = 't2:item-1';
      turnProvisional.dataset.turnId = 't2';
      turnProvisional.dataset.turnOrdinal = String(ordinal);
      turnProvisional.textContent = 'still streaming';
      view.appendChild(turnProvisional);
      const turnConfirmed = document.createElement('div');
      turnConfirmed.className = 'event assistant';
      turnConfirmed.dataset.jsonlLine = '11';
      turnConfirmed.dataset.turnId = 't2';
      turnConfirmed.textContent = 'now complete';
      const tier2Match = window.__fns._findMatchingProvisionalNode(view, { turn_id: 't2', tool_use_id: 'no-such-call' }, turnConfirmed);
      const tier2Ok = tier2Match === turnProvisional;
      if (tier2Match) tier2Match.remove();
      view.appendChild(turnConfirmed);

      // Tier 3: no turn_id on the confirmed event at all (e.g. an older
      // rollout shape) -- falls through to normalized text equality.
      const textProvisional = document.createElement('div');
      textProvisional.className = 'event user_text provisional';
      textProvisional.dataset.liveKey = 't3:item-1';
      textProvisional.innerHTML = '<div class="user-msg">  spaced   out  </div>';
      view.appendChild(textProvisional);
      const textConfirmed = document.createElement('div');
      textConfirmed.className = 'event user_text';
      textConfirmed.dataset.jsonlLine = '12';
      textConfirmed.innerHTML = '<div class="user-msg">spaced out</div>';
      const tier3Match = window.__fns._findMatchingProvisionalNode(view, {}, textConfirmed);
      const tier3Ok = tier3Match === textProvisional;
      if (tier3Match) tier3Match.remove();
      view.appendChild(textConfirmed);

      return {
        tier1Ok, tier2Ok, tier3Ok,
        remainingProvisional: view.querySelectorAll('[data-live-key]').length,
      };
    });
    assert.deepEqual(result, { tier1Ok: true, tier2Ok: true, tier3Ok: true, remainingProvisional: 0 });
  });
});
