#!/usr/bin/env node

const fs = require('fs');
const puppeteer = require('../require-puppeteer.js');

const baseUrl = process.env.CCC_PRESENTATION_MODE3_URL || 'http://127.0.0.1:8090';
const timeout = Number(process.env.CCC_PRESENTATION_MODE3_TIMEOUT_MS) || 30000;

function chromePath() {
  return [
    process.env.CCC_PRESENTATION_MODE3_CHROME,
    '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ].filter(Boolean).find(candidate => {
    try { fs.accessSync(candidate, fs.constants.X_OK); return true; } catch (_) { return false; }
  });
}

function artifact(prefix) {
  return {
    version: 1,
    deck_title: `${prefix} deck`,
    theme: 'violet',
    slides: [
      { id: 'statement', layout: 'statement', title: 'Statement', statement: `${prefix} thesis` },
      { id: 'bullets', layout: 'bullets', title: 'Bullets', items: ['One', 'Two'] },
      { id: 'steps', layout: 'steps', title: 'Steps', items: [{ label: 'Start', text: 'Begin here' }] },
      { id: 'comparison', layout: 'comparison', title: 'Comparison', left: { title: 'Before', items: ['Old'] }, right: { title: 'After', items: ['New'] } },
      { id: 'metrics', layout: 'metrics', title: 'Metrics', items: [{ value: '8', label: 'safe layouts' }] },
      { id: 'quote', layout: 'quote', title: 'Quote', quote: 'A focused deck', attribution: 'CCC' },
      { id: 'code', layout: 'code', title: 'Code', code: 'const safe = true;', language: 'js' },
      { id: 'summary', layout: 'summary', title: 'Summary', takeaway: 'Mode 3 is rendered', actions: ['Ship it'] },
    ],
  };
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: chromePath(), args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });
  try {
    await page.evaluateOnNewDocument(() => {
      localStorage.removeItem('ccc-last-conv');
      localStorage.removeItem('ccc-last-conv:all');
      localStorage.setItem('ccc-conv-presentation-mode', 'off');
      localStorage.removeItem('ccc-conv-presentation-mode-by-conversation');
    });
    await page.goto(baseUrl, { waitUntil: 'load', timeout });
    await page.waitForSelector('.conv-pane[data-pane-id="p1"] .conversations-view', { timeout });
    await page.evaluate(initialArtifact => {
      const pane = document.querySelector('.conv-pane[data-pane-id="p1"]');
      const view = pane.querySelector('.conversations-view');
      view.innerHTML = '<div class="event user_text"><div class="user-msg" data-raw-text="Explain Mode 3">Explain Mode 3</div></div>'
        + '<div class="event assistant" data-jsonl-line="10"><div class="assistant-text"><p>Transcript fallback.</p></div></div>';
      const answer = view.querySelector('.event.assistant');
      answer._presentationArtifact = initialArtifact;
      answer.dataset.presentationArtifact = '1';
      pane.querySelector('[data-role="presentation-toolbar"]').hidden = false;
      pane.querySelector('[data-presentation-mode="3"]').click();
    }, artifact('Initial'));
    await page.waitForFunction(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      return view && view._presentationDeck && view._presentationDeck.length === 8
        && view.querySelector('.conv-mode3-slide');
    }, { timeout });

    const initial = await page.evaluate(() => {
      const pane = document.querySelector('.conv-pane[data-pane-id="p1"]');
      const view = pane.querySelector('.conversations-view');
      const progress = pane.querySelector('[data-role="presentation-progress"]');
      return {
        layouts: view._presentationDeck.map(slide => slide.className.match(/layout-([^ ]+)/)[1]),
        index: view._presentationIndex,
        progressVisible: !!progress && !progress.hidden && progress.getClientRects().length > 0,
        dockAbsent: !pane.querySelector('.conv-presentation-dock'),
      };
    });
    const expected = ['statement', 'bullets', 'steps', 'comparison', 'metrics', 'quote', 'code', 'summary'];
    if (JSON.stringify(initial.layouts) !== JSON.stringify(expected)
        || initial.index !== 0 || !initial.progressVisible || !initial.dockAbsent) {
      throw new Error(`invalid initial Mode 3 render: ${JSON.stringify(initial)}`);
    }
    process.stdout.write('PASS authored-layouts-and-toolbar\n');

    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      return view && view._presentationIndex === 1;
    }, { timeout });
    await page.keyboard.press('ArrowLeft');
    await page.waitForFunction(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      return view && view._presentationIndex === 0;
    }, { timeout });
    process.stdout.write('PASS keyboard-navigation\n');

    await page.evaluate(nextArtifact => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      view._presentationIndex = view._presentationDeck.length - 1;
      const answer = document.createElement('div');
      answer.className = 'event assistant';
      answer.dataset.jsonlLine = '20';
      answer.innerHTML = '<div class="assistant-text"><p>New answer prose.</p></div>';
      answer._presentationArtifact = nextArtifact;
      answer.dataset.presentationArtifact = '1';
      view.insertBefore(answer, view.querySelector(':scope > .conv-presentation-stage'));
    }, artifact('New'));
    await new Promise(resolve => setTimeout(resolve, 1200));
    const tailState = await page.evaluate(() => {
      const view = document.querySelector('.conv-pane[data-pane-id="p1"] .conversations-view');
      const current = view && view._presentationDeck && view._presentationDeck[view._presentationIndex];
      return {
        count: view && view._presentationDeck && view._presentationDeck.length,
        index: view && view._presentationIndex,
        answerKey: current && current.dataset.answerKey,
        artifactSlideId: current && current.dataset.artifactSlideId,
        projectionRefreshDeck: !!(view && view._presentationProjection && view._presentationProjection.refreshDeck),
      };
    });
    if (tailState.count !== 16 || tailState.answerKey !== '20'
        || tailState.artifactSlideId !== 'statement') {
      throw new Error(`tail did not open first new-answer slide: ${JSON.stringify(tailState)}`);
    }
    process.stdout.write('PASS tail-opens-first-new-answer-slide\n');
  } finally {
    await browser.close();
  }
})().catch(error => {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exit(1);
});
