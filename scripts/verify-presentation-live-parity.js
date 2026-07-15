#!/usr/bin/env node

const fs = require('fs');
const puppeteer = require('../require-puppeteer.js');

const baseUrl = process.env.CCC_PRESENTATION_PARITY_URL || 'http://127.0.0.1:8090';
const timeout = Number(process.env.CCC_PRESENTATION_PARITY_TIMEOUT_MS) || 60000;
const requiredLabels = [
  'pending', 'queued', 'delivered', 'failed', 'removed', 'durable',
  'sending', 'thinking', 'long-thinking', 'generating', 'tool', 'tokens',
  'elapsed', 'stream', 'completed', 'approval', 'question', 'wake', 'warning',
  'error', 'done', 'attribute', 'class', 'disabled', 'enabled', 'click', 'input',
  'change', 'details', 'historical-cursor', 'split-pane', 'resize', 'off-restore',
  'legacy-mode-one', 'added', 'edited', 'tool-group', 'tool-complete',
  'approval-state', 'queue-reason', 'outcome-banner', 'dismissal', 'frame-bound',
  'completion-supersede', 'reactivation', 'refresh-stable', 'tail-auto-advance',
];
const passed = new Set();

function pass(label) {
  if (!requiredLabels.includes(label)) throw new Error(`unknown parity label: ${label}`);
  passed.add(label);
  process.stdout.write(`PASS ${label}\n`);
}

function findChromePath() {
  const candidates = [
    process.env.CCC_PRESENTATION_PARITY_CHROME,
    '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ].filter(Boolean);
  return candidates.find(candidate => {
    try { fs.accessSync(candidate, fs.constants.X_OK); return true; } catch (_) { return false; }
  });
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: findChromePath(),
    args: ['--no-sandbox'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });

  async function waitForParity(label, paneId = 'p1') {
    await page.waitForFunction(({ label, paneId }) => {
      const api = window.__cccPresentationParity;
      const pane = document.querySelector(`.conv-pane[data-pane-id="${paneId}"]`);
      const view = pane && pane.querySelector('.conversations-view');
      if (!api || !view) return false;
      const source = Array.from(view.children).find(node => node.dataset.parityLabel === label);
      const mirror = Array.from(view.querySelectorAll('.conv-presentation-live-item [data-parity-label]'))
        .find(node => node.dataset.parityLabel === label);
      const wrapper = mirror && mirror.closest('.conv-presentation-live-item');
      if (!source || !mirror || !wrapper || !wrapper.dataset.presentationProjectionId) return false;
      const sourceSnapshot = api.snapshot(source);
      const mirrorSnapshot = api.snapshot(mirror);
      return api.same(sourceSnapshot, mirrorSnapshot)
        && mirrorSnapshot.getClientRects > 0
        && mirrorSnapshot.inLiveViewport;
    }, { timeout }, { label, paneId });
    pass(label);
  }

  async function upsert(label, key, className, html, paneId = 'p1') {
    await page.evaluate(({ label, key, className, html, paneId }) => {
      const view = document.querySelector(`.conv-pane[data-pane-id="${paneId}"] .conversations-view`);
      let root = Array.from(view.children).find(node => node.dataset.parityKey === key);
      if (!root) {
        root = document.createElement('div');
        root.dataset.parityKey = key;
        view.insertBefore(root, view.querySelector(':scope > .conv-presentation-stage'));
      }
      root.dataset.parityLabel = label;
      root.className = className;
      root.innerHTML = html;
    }, { label, key, className, html, paneId });
  }

  try {
    await page.evaluateOnNewDocument(() => {
      localStorage.removeItem('ccc-last-conv');
      localStorage.removeItem('ccc-last-conv:all');
      localStorage.setItem('ccc-conv-presentation-mode', 'off');
      localStorage.setItem('ccc-split-state:all', JSON.stringify({
        orientation: 'vertical',
        ratio: 0.5,
        activeIndex: 0,
        panes: [
          { id: 'p1', conversationId: null },
          { id: 'p2', conversationId: null },
        ],
      }));
    });
    await page.goto(baseUrl, { waitUntil: 'load', timeout });
    await page.waitForSelector('.conv-pane[data-pane-id="p2"] .conversations-view', { timeout });

    await page.evaluate(() => {
      function answer(line, title) {
        const paragraphs = Array.from({ length: 28 }, (_, index) =>
          `<p>${title} paragraph ${index + 1} gives the canonical transcript enough height.</p>`
        ).join('');
        return `<div class="event assistant" data-jsonl-line="${line}">`
          + `<div class="assistant-text">${paragraphs}</div></div>`;
      }
      function transcript(prefix) {
        return `<div class="event user_text"><div class="user-msg" data-raw-text="${prefix} prompt one">${prefix} prompt one</div></div>`
          + answer('10', `${prefix} answer one`)
          + `<div class="event user_text"><div class="user-msg" data-raw-text="${prefix} prompt two">${prefix} prompt two</div></div>`
          + answer('20', `${prefix} answer two`);
      }
      document.querySelectorAll('.conv-pane[data-pane-id]').forEach(pane => {
        const view = pane.querySelector('.conversations-view');
        view.innerHTML = transcript(pane.dataset.paneId);
        view.className = 'conversations-view';
        pane.dataset.presentationMode = 'off';
        const toolbar = pane.querySelector('[data-role="presentation-toolbar"]');
        toolbar.hidden = false;
      });

      function elementPath(root, target) {
        const path = [];
        for (let node = target; node && node !== root; node = node.parentElement) {
          path.unshift(Array.prototype.indexOf.call(node.parentElement.children, node));
        }
        return path.join('.');
      }
      function snapshot(node) {
        const list = node.closest('.conv-presentation-live-list');
        const rect = node.getBoundingClientRect();
        const listRect = list && list.getBoundingClientRect();
        const controls = Array.from(node.querySelectorAll('input,textarea,select,button,details')).map(el => ({
          path: elementPath(node, el),
          tag: el.tagName.toLowerCase(),
          type: el.type || '',
          value: 'value' in el ? el.value : '',
          checked: 'checked' in el ? el.checked : false,
          disabled: 'disabled' in el ? el.disabled : false,
          selectedIndex: 'selectedIndex' in el ? el.selectedIndex : -1,
          open: 'open' in el ? el.open : false,
          text: String(el.textContent || '').replace(/\s+/g, ' ').trim(),
        }));
        const attrs = {};
        ['data-parity-label', 'data-state', 'data-click-count', 'data-input-value',
          'data-change-value', 'aria-label', 'aria-pressed', 'title', 'disabled', 'open']
          .forEach(name => {
            if (node.hasAttribute(name)) attrs[name] = node.getAttribute(name);
          });
        return {
          tag: node.tagName.toLowerCase(),
          className: Array.from(node.classList).sort().join(' '),
          text: String(node.textContent || '').replace(/\s+/g, ' ').trim(),
          attrs,
          controls,
          getClientRects: node.getClientRects().length,
          inLiveViewport: !listRect || (rect.bottom > listRect.top && rect.top < listRect.bottom),
        };
      }
      function same(left, right) {
        const semantic = value => ({
          tag: value.tag,
          className: value.className,
          text: value.text,
          attrs: value.attrs,
          controls: value.controls,
        });
        return JSON.stringify(semantic(left)) === JSON.stringify(semantic(right));
      }
      window.__cccPresentationParity = { snapshot, same };

      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      const maxScroll = view.scrollHeight - view.clientHeight;
      if (maxScroll < 40) throw new Error('synthetic transcript is not scrollable');
      view.scrollTop = Math.min(160, maxScroll);
      view._pinnedToBottom = false;
      window.__cccPresentationRestoreScroll = view.scrollTop;
    });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));

    await page.evaluate(() => {
      document.querySelector('.conv-pane[data-pane-id="p1"] [data-presentation-mode="2"]').click();
    });
    await page.waitForFunction(() => {
      const pane = document.querySelector('.conv-pane[data-pane-id="p1"]');
      const stage = pane && pane.querySelector('.conv-presentation-stage');
      return pane && pane.dataset.presentationMode === '2' && stage && stage.getClientRects().length > 0;
    }, { timeout });
    await new Promise(resolve => setTimeout(resolve, 300));

    const refreshAnimationStarts = await page.evaluate(() => new Promise((resolve, reject) => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      const slot = view && view.querySelector('.conv-presentation-slide-slot');
      const source = view && view.querySelector(':scope > .event.assistant');
      if (!view || !slot || !slot.firstElementChild || !source) {
        reject(new Error('presentation refresh probe could not find its fixture'));
        return;
      }
      const previousSlide = slot.firstElementChild;
      let animationStarts = 0;
      const onAnimationStart = event => {
        if (event.animationName === 'conv-presentation-enter'
            && event.target.closest('.conv-presentation-slide-slot')) animationStarts += 1;
      };
      view.addEventListener('animationstart', onAnimationStart, true);
      source.dataset.refreshProbe = String(Date.now());
      const deadline = performance.now() + 2000;
      const waitForRefresh = () => {
        if (slot.firstElementChild !== previousSlide) {
          setTimeout(() => {
            view.removeEventListener('animationstart', onAnimationStart, true);
            resolve(animationStarts);
          }, 250);
          return;
        }
        if (performance.now() >= deadline) {
          view.removeEventListener('animationstart', onAnimationStart, true);
          reject(new Error('presentation slide was not refreshed'));
          return;
        }
        requestAnimationFrame(waitForRefresh);
      };
      requestAnimationFrame(waitForRefresh);
    }));
    if (refreshAnimationStarts !== 0) {
      throw new Error(`unchanged presentation refresh replayed ${refreshAnimationStarts} entrance animation(s)`);
    }
    pass('refresh-stable');

    await page.evaluate(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      const next = view.querySelector('[data-presentation-nav="1"]');
      while (next && !next.disabled) next.click();
      window.__cccAutoAdvancePreviousCount = view._presentationDeck.length;
      window.__cccAutoAdvancePreviousIndex = view._presentationIndex;
    });
    await upsert(
      'tail-auto-advance',
      'tail-auto-advance',
      'event assistant',
      '<div class="assistant-text"><p>New answer while following the presentation tail</p></div>',
    );
    await waitForParity('tail-auto-advance');
    const autoAdvanceState = await page.evaluate(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      const selectedText = view.querySelector('.conv-presentation-slide-slot').textContent;
      return {
        previousCount: window.__cccAutoAdvancePreviousCount,
        previousIndex: window.__cccAutoAdvancePreviousIndex,
        currentCount: view._presentationDeck.length,
        currentIndex: view._presentationIndex,
        selectedNewAnswer: selectedText.includes('New answer while following the presentation tail'),
        followed: window.__cccAutoAdvancePreviousIndex === window.__cccAutoAdvancePreviousCount - 1
        && view._presentationDeck.length > window.__cccAutoAdvancePreviousCount
        && view._presentationIndex === view._presentationDeck.length - 1
        && selectedText.includes('New answer while following the presentation tail'),
      };
    });
    if (!autoAdvanceState.followed) {
      throw new Error('presentation stayed on the old tail after a new answer added slides: '
        + JSON.stringify(autoAdvanceState));
    }

    await upsert('pending', 'send', 'event user_text is-pending', '<div class="user-msg">Pending message</div>');
    await waitForParity('pending');
    await upsert('queued', 'send', 'event user_text is-queued', '<div class="user-msg">Queued message</div>');
    await waitForParity('queued');
    await upsert('delivered', 'send', 'event user_text is-delivered', '<div class="user-msg">Delivered message</div>');
    await waitForParity('delivered');
    await upsert('failed', 'send', 'event user_text is-failed', '<div class="user-msg">Failed message</div>');
    await waitForParity('failed');
    await page.evaluate(() => {
      const root = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view > [data-parity-key="send"]');
      root.remove();
    });
    await page.waitForFunction(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      return !view.querySelector(':scope > [data-parity-key="send"]')
        && !view.querySelector('.conv-presentation-live-item [data-parity-label="failed"]');
    }, { timeout });
    pass('removed');

    await upsert('durable', 'durable', 'event user_text', '<div class="user-msg">Durable user message</div>');
    await waitForParity('durable');
    await upsert('added', 'generic-root', 'event generic-event', '<span>Newly added canonical root</span>');
    await waitForParity('added');
    await page.evaluate(() => {
      const root = document.querySelector('[data-parity-key="generic-root"]');
      root.dataset.parityLabel = 'edited';
      root.querySelector('span').firstChild.data = 'Edited canonical root text';
    });
    await waitForParity('edited');
    const withinFrame = await page.evaluate(() => new Promise(resolve => {
      const root = document.querySelector('[data-parity-key="generic-root"]');
      root.dataset.parityLabel = 'frame-bound';
      root.querySelector('span').firstChild.data = 'Projected by the next animation frame';
      queueMicrotask(() => requestAnimationFrame(() => {
        const view = root.parentElement;
        const mirror = Array.from(view.querySelectorAll('.conv-presentation-live-item [data-parity-label]'))
          .find(node => node.dataset.parityLabel === 'frame-bound');
        if (!mirror) { resolve(false); return; }
        const sourceSnapshot = window.__cccPresentationParity.snapshot(root);
        const mirrorSnapshot = window.__cccPresentationParity.snapshot(mirror);
        resolve(window.__cccPresentationParity.same(sourceSnapshot, mirrorSnapshot)
          && mirrorSnapshot.getClientRects > 0);
      }));
    }));
    if (!withinFrame) throw new Error('live projection missed the next animation frame');
    pass('frame-bound');
    await upsert('sending', 'activity', 'conv-live-activity is-sending', '<span>Sending</span>');
    await waitForParity('sending');
    await upsert('thinking', 'activity', 'conv-live-activity is-thinking', '<span>Thinking</span>');
    await waitForParity('thinking');
    await upsert('long-thinking', 'activity', 'conv-live-activity is-thinking is-long', '<span>Thinking deeply for a while</span>');
    await waitForParity('long-thinking');
    await upsert('generating', 'activity', 'conv-live-activity is-generating', '<span>Generating answer</span>');
    await waitForParity('generating');
    await upsert('tool', 'activity', 'conv-live-tool-inline', '<span class="tool-name">Tool: inspect repository</span>');
    await waitForParity('tool');
    await page.evaluate(() => {
      const root = document.querySelector('[data-parity-key="activity"]');
      root.dataset.parityLabel = 'tokens';
      root.dataset.state = 'tokens';
      root.insertAdjacentHTML('beforeend', '<span class="token-count">1,024 tokens</span>');
    });
    await waitForParity('tokens');
    await page.evaluate(() => {
      const root = document.querySelector('[data-parity-key="activity"]');
      root.dataset.parityLabel = 'elapsed';
      root.dataset.state = 'elapsed';
      root.querySelector('.tool-name').firstChild.data = 'Tool running · elapsed 9s';
    });
    await waitForParity('elapsed');
    await upsert('tool-group', 'tool-group', 'tool-call-group', '<details open><summary>Repository inspection</summary><pre>rg presentation</pre></details>');
    await waitForParity('tool-group');
    await page.evaluate(() => {
      const root = document.querySelector('[data-parity-key="tool-group"]');
      root.dataset.parityLabel = 'tool-complete';
      root.classList.add('is-complete');
      root.querySelector('summary').firstChild.data = 'Repository inspection complete';
    });
    await waitForParity('tool-complete');

    await upsert('stream', 'stream', 'stream-bubble', '<div class="assistant-text"><p>Streaming partial answer</p></div>');
    await page.evaluate(() => {
      const root = document.querySelector('[data-parity-key="stream"]');
      root.dataset.state = 'expanded';
      root.querySelector('p').firstChild.data += ' with another chunk';
    });
    await waitForParity('stream');
    await page.evaluate(() => {
      const root = document.querySelector('[data-parity-key="stream"]');
      root.dataset.parityLabel = 'completed';
      root.dataset.jsonlLine = '30';
      root.className = 'event assistant';
      root.querySelector('p').firstChild.data = 'Completed streamed answer';
    });
    await waitForParity('completed');
    await page.waitForFunction(() => {
      const pane = document.querySelector('.conv-pane[data-pane-id="p1"]');
      const view = pane.querySelector('.conversations-view');
      const source = view.querySelector(':scope > [data-parity-label="completed"]');
      const mirror = view.querySelector('.conv-presentation-live-item [data-parity-label="completed"]');
      const slide = view.querySelector('.conv-presentation-slide-slot');
      return source && mirror && slide && view._presentationDeck.some(item => (
        item.textContent.includes('Completed streamed answer')
      ));
    }, { timeout });
    await upsert('completion-supersede', 'stream-next', 'stream-bubble', '<div class="assistant-text"><p>Next streaming answer</p></div>');
    await page.waitForFunction(() => document.querySelector(
      '.conv-pane[data-pane-id="p1"] .conv-presentation-live-item [data-parity-label="completion-supersede"]'
    ), { timeout });
    await page.evaluate(() => {
      const root = document.querySelector('[data-parity-key="stream-next"]');
      root.dataset.jsonlLine = '40';
      root.className = 'event assistant';
      root.querySelector('p').firstChild.data = 'Newer completed answer';
    });
    await waitForParity('completion-supersede');
    await page.waitForFunction(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      return !view.querySelector('.conv-presentation-live-item [data-parity-label="completed"]')
        && view.querySelectorAll('.conv-presentation-live-item [data-parity-label="completion-supersede"]').length === 1
        && view._presentationDeck.some(slide => slide.textContent.includes('Newer completed answer'));
    }, { timeout });

    await upsert('approval', 'approval', 'event approval-event', '<button type="button">Approve command</button>');
    await waitForParity('approval');
    await page.evaluate(() => {
      const root = document.querySelector('[data-parity-key="approval"]');
      root.dataset.parityLabel = 'approval-state';
      root.dataset.state = 'approved';
      root.querySelector('button').disabled = true;
      root.querySelector('button').firstChild.data = 'Command approved';
    });
    await waitForParity('approval-state');
    await upsert('question', 'question', 'event question-event', '<label>Answer <input value="Option A"></label>');
    await waitForParity('question');
    await upsert('wake', 'wake', 'event wake-event', '<span>Worker resumed</span>');
    await waitForParity('wake');
    await upsert('queue-reason', 'queue-reason', 'event queue-reason-event', '<span>Queued behind an active tool call</span>');
    await waitForParity('queue-reason');
    await upsert('warning', 'notice', 'event warning-event', '<span>Context is almost full</span>');
    await waitForParity('warning');
    await upsert('error', 'notice', 'event error-event', '<span>Command failed</span>');
    await waitForParity('error');
    await upsert('done', 'notice', 'event done-event', '<span>Task complete</span>');
    await waitForParity('done');
    await upsert('outcome-banner', 'outcome', 'conv-outcome-banner conv-outcome-error', '<strong>Session stopped</strong><button type="button">Dismiss</button>');
    await waitForParity('outcome-banner');
    await page.evaluate(() => {
      document.querySelector('[data-parity-key="outcome"]').remove();
    });
    await page.waitForFunction(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      return !view.querySelector(':scope > [data-parity-key="outcome"]')
        && !view.querySelector('.conv-presentation-live-item [data-parity-label="outcome-banner"]');
    }, { timeout });
    pass('dismissal');

    await upsert('attribute', 'stateful', 'event stateful-event', '<button type="button">State control</button>');
    await page.evaluate(() => { document.querySelector('[data-parity-key="stateful"]').dataset.state = 'active'; });
    await waitForParity('attribute');
    await page.evaluate(() => {
      const root = document.querySelector('[data-parity-key="stateful"]');
      root.dataset.parityLabel = 'class';
      root.classList.add('is-highlighted');
    });
    await waitForParity('class');
    await page.evaluate(() => {
      const root = document.querySelector('[data-parity-key="stateful"]');
      root.dataset.parityLabel = 'disabled';
      root.querySelector('button').disabled = true;
    });
    await waitForParity('disabled');
    await page.evaluate(() => {
      const root = document.querySelector('[data-parity-key="stateful"]');
      root.dataset.parityLabel = 'enabled';
      root.querySelector('button').disabled = false;
      root.dataset.state = 'enabled';
    });
    await waitForParity('enabled');

    await page.evaluate(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      const root = document.createElement('div');
      root.className = 'event interaction-event';
      root.dataset.parityKey = 'click';
      root.dataset.parityLabel = 'click';
      root.innerHTML = '<button type="button">Click canonical</button>';
      root.querySelector('button').addEventListener('click', () => {
        root.dataset.clickCount = String(Number(root.dataset.clickCount || 0) + 1);
        root.querySelector('button').textContent = 'Clicked canonical';
      });
      view.insertBefore(root, view.querySelector(':scope > .conv-presentation-stage'));
    });
    await waitForParity('click');
    await page.evaluate(() => {
      const modal = document.getElementById('whatsNewModal');
      if (modal) modal.classList.remove('open');
    });
    await page.click('.conv-pane[data-pane-id="p1"] .conv-presentation-live-item [data-parity-label="click"] button');
    await page.waitForFunction(() => {
      const source = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view > [data-parity-label="click"]');
      const mirror = document.querySelector('.conv-pane[data-pane-id="p1"] .conv-presentation-live-item [data-parity-label="click"]');
      return source && source.dataset.clickCount === '1' && mirror && mirror.dataset.clickCount === '1';
    }, { timeout });

    await page.evaluate(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      const root = document.createElement('div');
      root.className = 'event interaction-event';
      root.dataset.parityKey = 'input';
      root.dataset.parityLabel = 'input';
      root.innerHTML = '<input type="text" value="before">';
      root.querySelector('input').addEventListener('input', event => { root.dataset.inputValue = event.target.value; });
      view.insertBefore(root, view.querySelector(':scope > .conv-presentation-stage'));
    });
    await waitForParity('input');
    await page.$eval('.conv-pane[data-pane-id="p1"] .conv-presentation-live-item [data-parity-label="input"] input', input => {
      input.value = 'typed in presentation';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await page.waitForFunction(() => {
      const source = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view > [data-parity-label="input"]');
      const mirror = document.querySelector('.conv-pane[data-pane-id="p1"] .conv-presentation-live-item [data-parity-label="input"]');
      return source && source.dataset.inputValue === 'typed in presentation'
        && source.querySelector('input').value === 'typed in presentation'
        && mirror && mirror.querySelector('input').value === 'typed in presentation';
    }, { timeout });

    await page.evaluate(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      const root = document.createElement('div');
      root.className = 'event interaction-event';
      root.dataset.parityKey = 'change';
      root.dataset.parityLabel = 'change';
      root.innerHTML = '<select><option>A</option><option>B</option></select>';
      root.querySelector('select').addEventListener('change', event => { root.dataset.changeValue = event.target.value; });
      view.insertBefore(root, view.querySelector(':scope > .conv-presentation-stage'));
    });
    await waitForParity('change');
    await page.select('.conv-pane[data-pane-id="p1"] .conv-presentation-live-item [data-parity-label="change"] select', 'B');
    await page.waitForFunction(() => {
      const source = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view > [data-parity-label="change"]');
      const mirror = document.querySelector('.conv-pane[data-pane-id="p1"] .conv-presentation-live-item [data-parity-label="change"]');
      return source && source.dataset.changeValue === 'B' && source.querySelector('select').value === 'B'
        && mirror && mirror.querySelector('select').value === 'B';
    }, { timeout });

    await upsert('details', 'details', 'event details-event', '<details><summary>More detail</summary><p>Exact detail body</p></details>');
    await waitForParity('details');
    await page.click('.conv-pane[data-pane-id="p1"] .conv-presentation-live-item [data-parity-label="details"] summary');
    await page.waitForFunction(() => {
      const source = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view > [data-parity-label="details"] details');
      const mirror = document.querySelector('.conv-pane[data-pane-id="p1"] .conv-presentation-live-item [data-parity-label="details"] details');
      return source && source.open && mirror && mirror.open;
    }, { timeout });

    await page.evaluate(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      const prev = view.querySelector('[data-presentation-nav="-1"]');
      while (prev && !prev.disabled) prev.click();
      const liveList = view.querySelector('.conv-presentation-live-list');
      if (liveList) liveList.scrollTop = liveList.scrollHeight;
      window.__cccHistoricalCursor = view._presentationIndex;
    });
    await upsert('historical-cursor', 'history-update', 'event wake-event', '<span>Live update while reading history</span>');
    await waitForParity('historical-cursor');
    await page.waitForFunction(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      return view._presentationIndex === window.__cccHistoricalCursor;
    }, { timeout });

    await page.evaluate(() => {
      document.querySelector('.conv-pane[data-pane-id="p2"] [data-presentation-mode="2"]').click();
    });
    await page.waitForFunction(() => document.querySelector('.conv-pane[data-pane-id="p2"]')?.dataset.presentationMode === '2', { timeout });
    await upsert('split-pane', 'split-update', 'event wake-event', '<span>Only pane two update</span>', 'p2');
    await waitForParity('split-pane', 'p2');
    await upsert('resize', 'resize-update', 'event wake-event', '<span>Pane one remains independently live</span>', 'p1');
    await waitForParity('resize', 'p1');
    await page.evaluate(() => {
      const p1 = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      const p2 = document.querySelector('.conv-pane[data-pane-id="p2"] .conversations-view');
      if (p1.querySelector('.conv-presentation-live-item [data-parity-label="split-pane"]')) {
        throw new Error('pane two update leaked into pane one');
      }
      if (p2.querySelector('.conv-presentation-live-item [data-parity-label="resize"]')) {
        throw new Error('pane one update leaked into pane two');
      }
      window.__cccResizeState = {
        width: p1.querySelector('.conv-presentation-slide-slot').clientWidth,
        p1Index: p1._presentationIndex,
        p2Index: p2._presentationIndex,
      };
    });

    await page.setViewport({ width: 1180, height: 820 });
    await page.waitForFunction(() => {
      const p1 = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      const p2 = document.querySelector('.conv-pane[data-pane-id="p2"] .conversations-view');
      const slot = p1 && p1.querySelector('.conv-presentation-slide-slot');
      return slot && slot.clientWidth !== window.__cccResizeState.width
        && p1._presentationIndex === window.__cccResizeState.p1Index
        && p2._presentationIndex === window.__cccResizeState.p2Index
        && p1.querySelector('.conv-presentation-live-item [data-parity-label="resize"]')
        && p2.querySelector('.conv-presentation-live-item [data-parity-label="split-pane"]');
    }, { timeout });

    await page.evaluate(() => {
      document.querySelector('.conv-pane[data-pane-id="p1"] [data-presentation-mode="off"]').click();
    });
    await page.waitForFunction(() => {
      const pane = document.querySelector('.conv-pane[data-pane-id="p1"]');
      const view = pane.querySelector('.conversations-view');
      const source = view.querySelector(':scope > [data-parity-label="resize"]');
      return pane.dataset.presentationMode === 'off'
        && !view.classList.contains('is-presentation-mode')
        && !view.querySelector(':scope > .conv-presentation-stage')
        && Math.abs(view.scrollTop - window.__cccPresentationRestoreScroll) <= 2
        && source && getComputedStyle(source).display !== 'none';
    }, { timeout });
    pass('off-restore');

    await page.evaluate(() => {
      const pane = document.querySelector('.conv-pane[data-pane-id="p1"]');
      pane.querySelector('[data-presentation-mode="2"]').click();
    });
    await page.waitForFunction(() => {
      const pane = document.querySelector('.conv-pane[data-pane-id="p1"]');
      return pane && pane.dataset.presentationMode === '2'
        && pane.querySelector('.conv-presentation-stage');
    }, { timeout });
    await page.evaluate(() => {
      const pane = document.querySelector('.conv-pane[data-pane-id="p1"]');
      const view = pane.querySelector('.conversations-view');
      const prev = view.querySelector('[data-presentation-nav="-1"]');
      while (prev && !prev.disabled) prev.click();
      window.__cccLegacySlideKey = view._presentationDeck[view._presentationIndex]
        .dataset.presentationKey;
      localStorage.setItem('ccc-conv-presentation-mode', '1');
      pane.dataset.presentationMode = localStorage.getItem('ccc-conv-presentation-mode');
      pane.querySelector('[data-presentation-mode="2"]').click();
    });
    await page.waitForFunction(() => {
      const pane = document.querySelector('.conv-pane[data-pane-id="p1"]');
      const view = pane && pane.querySelector('.conversations-view');
      const slide = view && view._presentationDeck && view._presentationDeck[view._presentationIndex];
      return pane && pane.dataset.presentationMode === '2' && slide
        && slide.dataset.presentationKey === window.__cccLegacySlideKey;
    }, { timeout });
    pass('legacy-mode-one');
    await upsert('reactivation', 'reactivation', 'event wake-event', '<span>One update after reactivation</span>');
    await waitForParity('reactivation');
    await page.waitForFunction(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      return view.querySelectorAll('.conv-presentation-live-item [data-parity-label="reactivation"]').length === 1
        && view._presentationProjection
        && view._presentationProjection.entries.size === view.querySelectorAll('.conv-presentation-live-item').length;
    }, { timeout });

    const missing = requiredLabels.filter(label => !passed.has(label));
    if (missing.length) throw new Error(`parity matrix did not execute: ${missing.join(', ')}`);
    process.stdout.write(`PASS all ${requiredLabels.length} presentation parity checks\n`);
  } finally {
    await browser.close();
  }
})().catch(error => {
  process.stderr.write(`FAIL ${error.stack || error.message}\n`);
  process.exitCode = 1;
});
