const assert = require('node:assert/strict');
const { before, after, test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const source = require('./legacy-dashboard-source.cjs');
const helpersStart = source.indexOf('  function _kimiNormBlockText(');
const helpersEnd = source.indexOf('  function _kimiMoonHtml(', helpersStart);
assert.ok(helpersStart >= 0 && helpersEnd > helpersStart,
  'web-UI merged-turn helpers exist before _kimiMoonHtml');
const anchorCode = source.slice(source.indexOf('  function anchorCodexStateBadge('),
  source.indexOf('  function _reanchorToTailUnlessOnlyTransientBetween('));
const helpersCode = anchorCode
  + source.slice(source.indexOf('  function _kimiFlushRegroups('), helpersStart)
  + source.slice(helpersStart, helpersEnd);

let browser;
before(async () => { browser = await puppeteer.launch({ headless: true, executablePath: findChromePath(), args: ['--no-sandbox'] }); });
after(async () => { if (browser) await browser.close(); });

async function withPage(fn) {
  const page = await browser.newPage();
  try {
    return await fn(page);
  } finally {
    await page.close();
  }
}

async function loadHelpers(page) {
  await page.evaluate((code) => {
    (0, eval)('window.__fns = (() => {\n'
      + 'const _KIMI_GLYPHS = {};\n'
      + 'const escapeAttr = s => String(s).replace(/"/g, "&quot;");\n'
      + code
      + '\nreturn { _kimiAppendAssistantEvent };\n})();');
  }, helpersCode);
}

test('replaying one provisional assistant event upserts its marker, text, and metadata', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);

    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      const liveKey = 'turn-1:message-1';

      function marker() {
        const el = document.createElement('div');
        el.className = 'event assistant kimi-marker provisional';
        el.dataset.liveKey = liveKey;
        return el;
      }

      function text(value) {
        const el = document.createElement('div');
        el.className = 'assistant-text';
        el.textContent = value;
        return el;
      }

      function metadata(value) {
        const el = document.createElement('div');
        el.className = 'kimi-answer-meta';
        el.dataset.liveKey = liveKey;
        el._agentAnswerText = value;
        el.innerHTML = '<button data-copy-assistant-message>copy</button>';
        return el;
      }

      window.__fns._kimiAppendAssistantEvent(
        view,
        marker(),
        [text('Draft'), metadata('Draft')],
        null,
      );
      const existingMarker = view.querySelector('.event[data-live-key="' + liveKey + '"]');
      window.__fns._kimiAppendAssistantEvent(
        view,
        marker(),
        [text('Draft complete'), metadata('Draft complete')],
        existingMarker,
      );

      const meta = view.querySelector('.kimi-answer-meta');
      return {
        markers: view.querySelectorAll('.event[data-live-key="' + liveKey + '"]').length,
        texts: view.querySelectorAll('.assistant-text').length,
        metas: view.querySelectorAll('.kimi-answer-meta[data-live-key="' + liveKey + '"]').length,
        text: view.querySelector('.assistant-text').textContent,
        copiedText: meta && meta._agentAnswerText,
      };
    });

    assert.deepEqual(result, {
      markers: 1,
      texts: 1,
      metas: 1,
      text: 'Draft complete',
      copiedText: 'Draft complete',
    });
  });
});

test('different provisional assistant events keep distinct metadata rows', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);

    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      function append(liveKey) {
        const marker = document.createElement('div');
        marker.className = 'event assistant kimi-marker provisional';
        marker.dataset.liveKey = liveKey;
        const text = document.createElement('div');
        text.className = 'assistant-text';
        text.textContent = 'Same words';
        const meta = document.createElement('div');
        meta.className = 'kimi-answer-meta';
        meta.dataset.liveKey = liveKey;
        meta._agentAnswerText = 'Same words';
        window.__fns._kimiAppendAssistantEvent(view, marker, [text, meta], null);
      }

      append('turn-1:message-1');
      append('turn-1:message-2');
      return {
        markers: view.querySelectorAll('.event[data-live-key]').length,
        metas: view.querySelectorAll('.kimi-answer-meta[data-live-key]').length,
      };
    });

    assert.deepEqual(result, { markers: 2, metas: 2 });
  });
});

for (const liveFirst of [true, false]) {
  test(`saved/live reconciliation preserves one answer and actions (${liveFirst ? 'live' : 'saved'} first)`, async () => {
    await withPage(async page => {
      await page.setContent('<div class="conversations-view"></div>');
      await loadHelpers(page);
      const result = await page.evaluate(liveFirst => {
        const view = document.querySelector('.conversations-view');
        function append(live, turnId = 'turn-1') {
          const marker = document.createElement('div');
          marker.className = 'event assistant kimi-marker';
          marker.dataset.turnId = turnId;
          marker._agentAnswerText = 'I will inspect the CSV.';
          if (live) marker.dataset.liveKey = turnId + ':reply';
          else marker.dataset.jsonlLine = '60';
          const text = document.createElement('div');
          text.className = 'assistant-text';
          text.textContent = marker._agentAnswerText;
          const meta = document.createElement('div');
          meta.className = 'kimi-answer-meta';
          meta._agentAnswerText = marker._agentAnswerText;
          meta.innerHTML = '<button>Copy</button>';
          if (live) meta.dataset.liveKey = marker.dataset.liveKey;
          window.__fns._kimiAppendAssistantEvent(view, marker, [text, meta], null);
        }
        append(liveFirst);
        const originalText = view.querySelector('.assistant-text');
        // A result or user bubble may have moved the current append position.
        const boundary = document.createElement('div');
        boundary.className = 'event user_text';
        view.appendChild(boundary);
        append(!liveFirst);
        append(true); // replaying a full live snapshot must remain idempotent
        const first = {
          texts: view.querySelectorAll('.assistant-text').length,
          metas: view.querySelectorAll('.kimi-answer-meta').length,
          live: view.querySelectorAll('[data-live-key]').length,
          saved: view.querySelectorAll('[data-jsonl-line]').length,
          stable: originalText === view.querySelector('.assistant-text'),
        };
        append(false, 'turn-2'); // identical words in another turn are legitimate
        return { first, textsAfterNewTurn: view.querySelectorAll('.assistant-text').length };
      }, liveFirst);
      assert.deepEqual(result, {
        first: { texts: 1, metas: 1, live: 0, saved: 1, stable: true },
        textsAfterNewTurn: 2,
      });
    });
  });
}

test('saved tools adopt live rows before obsolete overlay cleanup', async () => {
  await withPage(async page => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      for (const live of [true, false]) {
        const marker = document.createElement('div');
        marker.className = 'event assistant kimi-marker';
        if (live) marker.dataset.liveKey = 't:tool';
        else marker.dataset.jsonlLine = '10';
        const tool = document.createElement('div');
        tool.className = 'kimi-tool';
        tool.dataset.toolUseId = 'read-file';
        tool.textContent = 'Read records.csv';
        window.__fns._kimiAppendAssistantEvent(view, marker, [tool], null);
      }
      view.querySelectorAll('[data-live-key]').forEach(el => el.remove());
      return view.querySelectorAll('.kimi-tool').length;
    });
    assert.equal(result, 1);
  });
});

for (const saved of [false, true]) {
  test(`one native reply replaces revised live text (${saved ? 'saved' : 'live'} update)`, async () => {
    await withPage(async page => {
      await page.setContent('<div class="conversations-view"></div>');
      await loadHelpers(page);
      const result = await page.evaluate(saved => {
        const view = document.querySelector('.conversations-view');
        function append(textValue, live) {
          const marker = document.createElement('div');
          marker.className = 'event assistant kimi-marker';
          marker.dataset.turnId = 'turn-1';
          marker.dataset.codexItemId = 'message-1';
          marker._agentAnswerText = textValue;
          if (live) marker.dataset.liveKey = 'turn-1:message-1';
          else marker.dataset.jsonlLine = '16';
          const text = document.createElement('div');
          text.className = 'assistant-text';
          text.textContent = textValue;
          const meta = document.createElement('div');
          meta.className = 'kimi-answer-meta';
          meta._agentAnswerText = textValue;
          meta.innerHTML = '<button>Copy</button>';
          window.__fns._kimiAppendAssistantEvent(view, marker, [text, meta],
            live ? view.querySelector('.kimi-marker[data-live-key]') : null);
        }
        append('**Draft', true);
        append('Draft complete.', !saved);
        append('Draft complete.', !saved);
        return {texts: Array.from(view.querySelectorAll('.assistant-text'), x => x.textContent),
          metas: view.querySelectorAll('.kimi-answer-meta').length,
          live: view.querySelectorAll('.kimi-marker[data-live-key]').length};
      }, saved);
      assert.deepEqual(result, {texts: ['Draft complete.'], metas: 1, live: saved ? 0 : 1});
    });
  });
}

test('working status stays after merged assistant updates without splitting the turn', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conversations-view"><div class="conv-codex-state">Working</div></div>');
    await loadHelpers(page);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      const badge = view.firstElementChild;
      for (let i = 0; i < 4; i++) {
        const marker = document.createElement('div');
        marker.className = 'event assistant kimi-marker';
        const text = document.createElement('div');
        text.className = 'assistant-text';
        text.textContent = 'Update ' + i;
        window.__fns._kimiAppendAssistantEvent(view, marker, [text], null);
      }
      return {
        turns: view.querySelectorAll('.kimi-turn').length,
        messages: view.querySelectorAll('.assistant-text').length,
        statusLast: view.lastElementChild === badge,
      };
    });
    assert.deepEqual(result, { turns: 1, messages: 4, statusLast: true });
  });
});

test('saved history restores chronological order when a later reply appeared live first', async () => {
  await withPage(async page => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      function append(id, value, line) {
        const marker = document.createElement('div');
        marker.className = 'event assistant kimi-marker';
        marker.dataset.turnId = 'turn-1';
        marker.dataset.codexItemId = id;
        marker._agentAnswerText = value;
        if (line == null) marker.dataset.liveKey = 'turn-1:' + id;
        else marker.dataset.jsonlLine = String(line);
        const text = document.createElement('div');
        text.className = 'assistant-text';
        text.textContent = value;
        const meta = document.createElement('div');
        meta.className = 'kimi-answer-meta';
        meta._agentAnswerText = value;
        meta.textContent = 'Copy ' + id;
        window.__fns._kimiAppendAssistantEvent(view, marker, [text, meta], null);
      }
      append('final', 'Final answer.', null);
      const liveText = view.querySelector('.assistant-text');
      append('first', 'First update.', 14);
      append('second', 'Second update.', 49);
      append('final', 'Final answer.', 190);
      append('final', 'Final answer.', null);
      return {
        texts: [...view.querySelectorAll('.assistant-text')].map(x => x.textContent),
        actions: [...view.querySelectorAll('.kimi-answer-meta')].map(x => x._agentAnswerText),
        lines: [...view.querySelectorAll('.kimi-marker')].map(x => x.dataset.jsonlLine),
        preserved: view.querySelectorAll('.assistant-text')[2] === liveText,
        provisional: view.querySelectorAll('[data-live-key]').length,
      };
    });
    assert.deepEqual(result, {
      texts: ['First update.', 'Second update.', 'Final answer.'],
      actions: ['First update.', 'Second update.', 'Final answer.'],
      lines: ['14', '49', '190'], preserved: true, provisional: 0,
    });
  });
});

test('saved tools and replies stay ahead of the live tail during incremental confirmation', async () => {
  await withPage(async page => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      function append(id, line, tool = false) {
        const marker = document.createElement('div');
        marker.className = 'event assistant kimi-marker';
        marker.dataset.turnId = 'turn-1';
        if (line == null) marker.dataset.liveKey = 'turn-1:' + id;
        else marker.dataset.jsonlLine = String(line);
        const block = document.createElement('div');
        block.className = tool ? 'kimi-tool' : 'assistant-text';
        if (tool) block.dataset.toolUseId = id;
        else marker._agentAnswerText = id;
        block.textContent = id;
        window.__fns._kimiAppendAssistantEvent(view, marker, [block],
          line == null ? view.querySelector('.kimi-marker[data-live-key="turn-1:' + id + '"]') : null);
      }
      const order = () => [...view.querySelectorAll('.assistant-text, .kimi-tool')].map(x => x.textContent);
      append('tool-a', null, true);
      append('tool-b', null, true);
      append('Later reply', null);
      const tool = view.querySelector('[data-tool-use-id="tool-a"]');
      view.querySelector('.kimi-tool-group').classList.remove('collapsed');
      append('Earlier reply', 10);
      const during = order();
      append('tool-a', 20, true);
      append('tool-b', 30, true);
      append('Later reply', 40);
      // Late overlay replays and final cleanup must not undo adoption.
      append('tool-a', null, true);
      view.querySelectorAll('[data-live-key]').forEach(x => x.remove());
      return { during, after: order(), preservedTool: tool === view.querySelector('[data-tool-use-id="tool-a"]'),
        expanded: !view.querySelector('.kimi-tool-group').classList.contains('collapsed'),
        tools: view.querySelectorAll('.kimi-tool').length };
    });
    assert.deepEqual(result, {
      during: ['Earlier reply', 'tool-a', 'tool-b', 'Later reply'],
      after: ['Earlier reply', 'tool-a', 'tool-b', 'Later reply'],
      preservedTool: true, expanded: true, tools: 2,
    });
  });
});

test('legacy partial replies adopt the complete saved text without restoring the old draft', async () => {
  await withPage(async page => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);
    const texts = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      function append(value, line) {
        const marker = document.createElement('div');
        marker.className = 'event assistant kimi-marker';
        marker.dataset.turnId = 'turn-1';
        marker._agentAnswerText = value;
        if (line == null) marker.dataset.liveKey = 'turn-1:legacy-reply';
        else marker.dataset.jsonlLine = String(line);
        const block = document.createElement('div');
        block.className = 'assistant-text';
        block.textContent = value;
        window.__fns._kimiAppendAssistantEvent(view, marker, [block], null);
      }
      append('This is the final answer with a streamed prefix', null);
      append('An earlier progress update.', 10);
      append('This is the final answer with a streamed prefix and the complete ending.', 20);
      return [...view.querySelectorAll('.assistant-text')].map(x => x.textContent);
    });
    assert.deepEqual(texts, ['An earlier progress update.',
      'This is the final answer with a streamed prefix and the complete ending.']);
  });
});
