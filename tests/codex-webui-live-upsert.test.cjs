const assert = require('node:assert/strict');
const { before, after, test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const helpersStart = source.indexOf('  function _kimiNormBlockText(');
const helpersEnd = source.indexOf('  function _kimiMoonHtml(', helpersStart);
assert.ok(helpersStart >= 0 && helpersEnd > helpersStart,
  'web-UI merged-turn helpers exist before _kimiMoonHtml');
const helpersCode = source.slice(helpersStart, helpersEnd);

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

async function loadHelpers(page) {
  await page.evaluate((code) => {
    (0, eval)('window.__fns = (() => {\n'
      + 'const _kimiRegroupTools = () => {};\n'
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
