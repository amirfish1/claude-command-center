// Slice 2 of the Codex single-renderer merge (see
// CCC-private-docs/plans/2026-09-12-codex-single-renderer-merge.md): teaches
// renderConversationEvents to upsert/reconcile `data-live-key` provisional
// rows. Nothing feeds a live_key-bearing event yet (that's Slice 4) -- these
// tests exercise the standalone DOM helpers the render loop's per-event loop
// calls into directly: _upsertProvisionalNode, _findMatchingProvisionalNode,
// _removeStaleProvisionalsForTurn, _lastRenderedRowAnchor, _turnOrdinalBefore
// (all defined in static/app.js immediately above renderConversationEvents).
//
// renderConversationEvents itself has dozens of outer-scope dependencies
// (pane state, streaming bubbles, tool groups, ...) that make invoking the
// whole function in isolation impractical -- same reasoning that led
// tests/native-codex-last-jump.test.cjs to extract and exercise
// _prevUserMessageTarget/_nextUserMessageTarget directly. These helpers are
// genuine DOM code (dataset, classList, replaceWith, querySelectorAll), so
// puppeteer supplies a real DOM the same way that test does.
const assert = require('node:assert/strict');
const { before, after, test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

const helpersStart = source.indexOf('  // --- Provisional (live) event upsert/reconcile helpers');
const helpersEnd = source.indexOf('  function renderConversationEvents(', helpersStart);
assert.ok(helpersStart >= 0 && helpersEnd > helpersStart, 'provisional helper block exists before renderConversationEvents');
const helpersCode = source.slice(helpersStart, helpersEnd);

const selectorMatch = source.match(/const CONV_USER_MESSAGE_SELECTOR = '([^']+)';/);
assert.ok(selectorMatch, 'CONV_USER_MESSAGE_SELECTOR literal found in app.js');
const CONV_USER_MESSAGE_SELECTOR = selectorMatch[1];

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

// Loads the helper functions into `window.__fns` inside the page.
async function loadHelpers(page) {
  await page.evaluate((code) => {
    (0, eval)(code.replace(/^/, 'window.__fns = (() => {\n')
      + '\nreturn { _turnOrdinalBefore, _lastRenderedRowAnchor, _upsertProvisionalNode, _findMatchingProvisionalNode, _removeStaleProvisionalsForTurn, _confirmedResultForTurn };\n})();');
  }, helpersCode);
}

test('an upsert with unchanged content keeps the same <img> node', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      function buildProvisionalDiv(liveKey, src) {
        const div = document.createElement('div');
        div.className = 'event assistant provisional';
        div.dataset.liveKey = liveKey;
        div.dataset.provisional = 'true';
        div.innerHTML = '<div class="msg"><img class="msg-image" src="' + src + '" alt="pasted image" loading="lazy"></div>';
        return div;
      }
      const liveKey = 'turn-1:item-1';
      const first = window.__fns._upsertProvisionalNode(view, buildProvisionalDiv(liveKey, '/image-cache/s/1.png'), null);
      const imgBefore = view.querySelector('img');
      // A JS-only property (not a DOM attribute) so tagging it doesn't
      // perturb the innerHTML equality check the upsert uses to decide
      // whether to skip the replace -- an attribute like a dataset entry
      // would show up in innerHTML and defeat the very identity check this
      // test is verifying.
      imgBefore.__testMarker = 'original';

      // A second upsert for the same live_key with IDENTICAL rendered markup
      // (a steady-state re-poll of the same in-progress item) must not tear
      // down and recreate the <img> -- that recreation is the flicker bug
      // this merge exists to fix.
      const existing = view.querySelector('.event[data-live-key="' + liveKey + '"]');
      const second = buildProvisionalDiv(liveKey, '/image-cache/s/1.png');
      const after1 = window.__fns._upsertProvisionalNode(view, second, existing);
      const imgAfter = view.querySelector('img');
      return {
        sameNode: after1 === existing,
        sameImgNode: imgAfter === imgBefore,
        markerSurvived: imgAfter && imgAfter.__testMarker === 'original',
        nodeCount: view.querySelectorAll('.event[data-live-key]').length,
      };
    });
    assert.deepEqual(result, { sameNode: true, sameImgNode: true, markerSurvived: true, nodeCount: 1 });
  });
});

test('an upsert with changed content replaces the node in place without moving position', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      const before1 = document.createElement('div');
      before1.className = 'event assistant provisional';
      before1.textContent = 'sibling before';
      view.appendChild(before1);

      const liveKey = 'turn-1:item-2';
      const first = document.createElement('div');
      first.className = 'event assistant provisional';
      first.dataset.liveKey = liveKey;
      first.textContent = 'first draft';
      window.__fns._upsertProvisionalNode(view, first, null);

      const after1 = document.createElement('div');
      after1.className = 'event assistant provisional';
      after1.textContent = 'sibling after';
      view.appendChild(after1);

      const existing = view.querySelector('.event[data-live-key="' + liveKey + '"]');
      const second = document.createElement('div');
      second.className = 'event assistant provisional';
      second.dataset.liveKey = liveKey;
      second.textContent = 'updated draft';
      const returned = window.__fns._upsertProvisionalNode(view, second, existing);

      return {
        replaced: returned === second && returned !== existing,
        existingDetached: !existing.isConnected,
        order: Array.from(view.children, (c) => c.textContent),
      };
    });
    assert.equal(result.replaced, true);
    assert.equal(result.existingDetached, true);
    assert.deepEqual(result.order, ['sibling before', 'updated draft', 'sibling after']);
  });
});

test('a brand-new provisional row is inserted after the last confirmed (line-keyed) row', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);
    const order = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      const confirmed1 = document.createElement('div');
      confirmed1.className = 'event assistant';
      confirmed1.dataset.jsonlLine = '10';
      confirmed1.textContent = 'confirmed 10';
      view.appendChild(confirmed1);

      const confirmed2 = document.createElement('div');
      confirmed2.className = 'event assistant';
      confirmed2.dataset.jsonlLine = '11';
      confirmed2.textContent = 'confirmed 11';
      view.appendChild(confirmed2);

      const provisional = document.createElement('div');
      provisional.className = 'event assistant provisional';
      provisional.dataset.liveKey = 'turn-1:item-3';
      provisional.textContent = 'provisional 3';
      window.__fns._upsertProvisionalNode(view, provisional, null);

      return Array.from(view.children, (c) => c.textContent);
    });
    assert.deepEqual(order, ['confirmed 10', 'confirmed 11', 'provisional 3']);
  });
});

test('the provisional node is removed on rollout match via turn_id + ordinal', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      // Provisional row is the 0th event.type row seen for turn "t1".
      const ordinal = window.__fns._turnOrdinalBefore(view, 't1'); // 0, view is empty
      const provisional = document.createElement('div');
      provisional.className = 'event assistant provisional';
      provisional.dataset.liveKey = 't1:item-agent-1';
      provisional.dataset.turnId = 't1';
      provisional.dataset.turnOrdinal = String(ordinal);
      provisional.textContent = 'Streaming reply text';
      view.appendChild(provisional);

      // The matching rollout event for the same turn/ordinal arrives with
      // DIFFERENT text (the live text was still streaming) -- turn+ordinal
      // matching must not depend on the text being identical.
      const confirmed = document.createElement('div');
      confirmed.className = 'event assistant';
      confirmed.dataset.jsonlLine = '42';
      confirmed.dataset.turnId = 't1';
      confirmed.textContent = 'Streaming reply text, now complete.';

      const match = window.__fns._findMatchingProvisionalNode(view, { turn_id: 't1' }, confirmed);
      if (match) match.remove();
      view.appendChild(confirmed);

      return {
        matched: match === provisional,
        remainingLiveKeyNodes: view.querySelectorAll('.event[data-live-key]').length,
        confirmedPresent: view.contains(confirmed),
      };
    });
    assert.deepEqual(result, { matched: true, remainingLiveKeyNodes: 0, confirmedPresent: true });
  });
});

test('the provisional node is removed on rollout match via text fallback when there is no turn_id', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      const provisional = document.createElement('div');
      provisional.className = 'event user_text provisional';
      provisional.dataset.liveKey = 't9:item-1';
      provisional.innerHTML = '<div class="user-msg">  Hello   there  </div>';
      view.appendChild(provisional);

      const confirmed = document.createElement('div');
      confirmed.className = 'event user_text';
      confirmed.dataset.jsonlLine = '7';
      confirmed.innerHTML = '<div class="user-msg">Hello there</div>';

      // No turn_id on this event -- must fall through to normalized text
      // equality.
      const match = window.__fns._findMatchingProvisionalNode(view, {}, confirmed);
      if (match) match.remove();

      return { matched: match === provisional, remaining: view.querySelectorAll('.event[data-live-key]').length };
    });
    assert.deepEqual(result, { matched: true, remaining: 0 });
  });
});

test('task_complete removes every remaining provisional row for that turn, matched or not', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      const p1 = document.createElement('div');
      p1.className = 'event tool_use provisional';
      p1.dataset.liveKey = 't5:item-1';
      view.appendChild(p1);
      const p2 = document.createElement('div');
      p2.className = 'event assistant provisional';
      p2.dataset.liveKey = 't5:item-2';
      view.appendChild(p2);
      const meta = document.createElement('div');
      meta.className = 'kimi-answer-meta';
      meta.dataset.liveKey = 't5:item-2';
      view.appendChild(meta);
      // A provisional row from a DIFFERENT turn must survive.
      const other = document.createElement('div');
      other.className = 'event assistant provisional';
      other.dataset.liveKey = 't6:item-1';
      view.appendChild(other);
      const otherMeta = document.createElement('div');
      otherMeta.className = 'kimi-answer-meta';
      otherMeta.dataset.liveKey = 't6:item-1';
      view.appendChild(otherMeta);

      window.__fns._removeStaleProvisionalsForTurn(view, 't5');

      return {
        remainingEventKeys: Array.from(view.querySelectorAll('.event[data-live-key]'), (n) => n.dataset.liveKey),
        remainingMetaKeys: Array.from(view.querySelectorAll('.kimi-answer-meta[data-live-key]'), (n) => n.dataset.liveKey),
      };
    });
    assert.deepEqual(result, {
      remainingEventKeys: ['t6:item-1'],
      remainingMetaKeys: ['t6:item-1'],
    });
  });
});

test('Last/Previous/Next jump buttons count one copy across a provisional+rollout overlap', async () => {
  await withPage(async (page) => {
    // Simulate the transient state right before reconcile: both the
    // provisional row and its rollout counterpart are present at once.
    await page.setContent(`<div class="conversations-view">
      <div class="event user_text" data-jsonl-line="10"><div class="user-msg">Hello</div></div>
      <div class="event user_text provisional" data-live-key="t1:item-1"><div class="user-msg">Hello</div></div>
    </div>`);
    const count = await page.evaluate((sel) => document.querySelectorAll(sel).length, CONV_USER_MESSAGE_SELECTOR);
    assert.equal(count, 1, 'a purely-provisional row must not be an extra jump target alongside its rollout row');

    // After reconcile removes the provisional row, still exactly one.
    await page.evaluate(() => document.querySelector('[data-live-key]').remove());
    const countAfter = await page.evaluate((sel) => document.querySelectorAll(sel).length, CONV_USER_MESSAGE_SELECTOR);
    assert.equal(countAfter, 1);
  });
});

// CCC-1145: a session stuck at a usage limit keeps serving a finished turn's
// items from the live overlay after the transcript (including the turn's
// result row) has rendered. The render loop's per-event guard consults this
// helper to drop those stale overlay items instead of duplicating the turn.
test('_confirmedResultForTurn is true only when a confirmed result row exists for that turn', async () => {
  await withPage(async (page) => {
    await page.setContent(`<div class="conversations-view">
      <div class="event assistant" data-jsonl-line="40" data-turn-id="t1">confirmed text</div>
      <div class="event result" data-jsonl-line="41" data-turn-id="t1">Done</div>
      <div class="event result" data-jsonl-line="55" data-turn-id="t2">Done</div>
    </div>`);
    await loadHelpers(page);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      return {
        withResult: window.__fns._confirmedResultForTurn(view, 't1'),
        otherTurn: window.__fns._confirmedResultForTurn(view, 't2'),
        missingTurn: window.__fns._confirmedResultForTurn(view, 'nope'),
        nullTurn: window.__fns._confirmedResultForTurn(view, null),
        noView: window.__fns._confirmedResultForTurn(null, 't1'),
        // A provisional result row (live_key, no jsonl line) must NOT count:
        // the turn is not confirmed-complete until the rollout says so.
        provisionalOnly: (() => {
          const ghost = document.createElement('div');
          ghost.className = 'event result provisional';
          ghost.dataset.liveKey = 't3:item-1';
          ghost.dataset.turnId = 't3';
          view.appendChild(ghost);
          const verdict = window.__fns._confirmedResultForTurn(view, 't3');
          ghost.remove();
          return verdict;
        })(),
      };
    });
    assert.deepEqual(result, {
      withResult: true,
      otherTurn: true,
      missingTurn: false,
      nullTurn: false,
      noView: false,
      provisionalOnly: false,
    });
  });
});
