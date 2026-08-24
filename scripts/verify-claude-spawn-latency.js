#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

function argValue(flag, fallback) {
  const index = process.argv.indexOf(flag);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function percentile(values, fraction) {
  const ordered = values.slice().sort((a, b) => a - b);
  return ordered[Math.max(0, Math.ceil(ordered.length * fraction) - 1)];
}

const baseUrl = argValue(
  '--url',
  process.env.CCC_CLAUDE_SPAWN_URL || 'http://127.0.0.1:8090',
);
const repoPath = path.resolve(argValue('--repo', process.cwd()));
const measuredRuns = Number(argValue('--runs', '5'));
const warmupRuns = Number(argValue('--warmup-runs', '1'));
const outputDir = path.resolve(argValue('--out', '/tmp/ccc-claude-spawn-latency'));
const timeoutMs = Number(process.env.CCC_CLAUDE_SPAWN_TIMEOUT_MS || 60000);
const prewarmMs = Number(argValue('--prewarm-ms', process.env.CCC_CLAUDE_PREWARM_MS || '12000'));
const prompt = 'Reply with exactly READY and nothing else.';

async function enterNewSession(page) {
  await page.waitForSelector('#sidebarNewBtn', { timeout: timeoutMs });
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.evaluate(() => document.querySelector('#sidebarNewBtn').click());
    try {
      await page.waitForFunction(() => {
        const input = document.querySelector('#convInput');
        const cwd = document.querySelector('#spawnCwdPicker');
        const title = document.querySelector('.ns-stage-title');
        return input && cwd && !input.disabled
          && title && title.textContent.trim() === 'New session';
      }, { timeout: 5000 });
      return;
    } catch (_) {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error('New session composer did not become ready after three clicks');
}

async function prepareComposer(page) {
  await page.evaluate((cwd) => {
    const engine = document.querySelector('#convInputEngineSelect');
    const cwdInput = document.querySelector('#spawnCwdPicker');
    const worktree = document.querySelector('#inlineWorktreeToggle');
    if (!engine || !cwdInput) throw new Error('new-session controls are missing');
    engine.value = 'claude';
    engine.dispatchEvent(new Event('change', { bubbles: true }));
    cwdInput.value = cwd;
    cwdInput.dispatchEvent(new Event('input', { bubbles: true }));
    cwdInput.dispatchEvent(new Event('change', { bubbles: true }));
    if (worktree) worktree.checked = false;
  }, repoPath);
  await page.waitForFunction(() => {
    const button = document.querySelector('#convSendBtn');
    return button && !button.disabled;
  }, { timeout: timeoutMs });
}

async function runOnce(page, ordinal, warmup) {
  let spawnedSessionId = null;
  try {
  const prewarmResponsePromise = page.waitForResponse((response) => {
    const request = response.request();
    return request.method() === 'POST'
      && new URL(response.url()).pathname === '/api/sessions/prewarm-claude';
  }, { timeout: timeoutMs });
  await enterNewSession(page);
  const newSessionState = await page.evaluate(() => ({
    stageTitle: (document.querySelector('.ns-stage-title') || {}).textContent || '',
    inputVisible: !!document.querySelector('#convInput'),
    sendDisabled: !!document.querySelector('#convSendBtn')?.disabled,
  }));
  process.stdout.write(`[claude-spawn] composer ${JSON.stringify(newSessionState)}\n`);
  await prepareComposer(page);
  await page.evaluate((message) => {
    const input = document.querySelector('#convInput');
    input.value = message;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    window.__claudeSpawnBenchmark = {
      startedAt: null,
      firstVisibleAt: null,
      targetSessionId: null,
    };
    const selector = '.stream-block-text.assistant-text, .event.assistant .assistant-text';
    const observer = new MutationObserver((records) => {
      const state = window.__claudeSpawnBenchmark;
      if (!state.targetSessionId) return;
      let text = null;
      for (const record of records) {
        const candidates = record.type === 'characterData'
          ? [record.target.parentElement]
          : Array.from(record.addedNodes || []);
        for (const node of candidates) {
          if (!node || node.nodeType !== Node.ELEMENT_NODE) continue;
          text = node.matches && node.matches(selector) ? node : node.querySelector(selector);
          if (text && text.textContent.trim()) break;
        }
        if (text && text.textContent.trim()) break;
      }
      const selectedSid = Array.from(document.querySelectorAll('[data-copy-session-id]'))
        .map((element) => element.dataset.copySessionId || '')
        .find((sid) => sid === state.targetSessionId) || '';
      const isSpawnStream = !!(text && text.matches('.stream-block-text.assistant-text'));
      if (!isSpawnStream && selectedSid !== state.targetSessionId) return;
      if (text && text.textContent.trim() && window.__claudeSpawnBenchmark.firstVisibleAt == null) {
        window.__claudeSpawnBenchmark.firstVisibleAt = performance.now();
        observer.disconnect();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }, prompt);
  // Prewarming is intentionally prompt-aware so the reserved Claude process
  // starts with its final durable name. Populate the composer first, then wait
  // for the debounced request caused by that input event.
  const prewarmResponse = await prewarmResponsePromise;
  if (!prewarmResponse.ok()) {
    throw new Error(`Claude prewarm failed with HTTP ${prewarmResponse.status()}`);
  }
  if (prewarmMs > 0) {
    process.stdout.write(`[claude-spawn] reservation ready; composer dwell ${prewarmMs}ms\n`);
    await new Promise((resolve) => setTimeout(resolve, prewarmMs));
  }

  const responsePromise = page.waitForResponse((response) => {
    const request = response.request();
    return request.method() === 'POST'
      && new URL(response.url()).pathname === '/api/sessions/spawn';
  }, { timeout: timeoutMs });

  await page.evaluate(() => {
    window.__claudeSpawnBenchmark.startedAt = performance.now();
    document.querySelector('#convSendBtn').click();
  });
  process.stdout.write('[claude-spawn] submit clicked; waiting for POST acknowledgement\n');
  const response = await responsePromise;
  const spawn = await response.json();
  process.stdout.write(`[claude-spawn] POST acknowledged ${response.status()}\n`);
  if (!spawn.ok || !spawn.session_id) {
    throw new Error(`Claude spawn was not acknowledged: ${JSON.stringify(spawn)}`);
  }
  spawnedSessionId = spawn.session_id;
  await page.evaluate((sid) => {
    window.__claudeSpawnBenchmark.targetSessionId = sid;
  }, spawn.session_id);

  await page.waitForFunction(() => {
    const state = window.__claudeSpawnBenchmark;
    return state && state.firstVisibleAt != null;
  }, { timeout: timeoutMs });

  const client = await page.evaluate(() => {
    const state = window.__claudeSpawnBenchmark;
    return {
      firstVisibleMs: state.firstVisibleAt - state.startedAt,
      text: (document.querySelector(
        '.stream-block-text.assistant-text, .event.assistant .assistant-text'
      ) || {}).textContent || '',
    };
  });
  await page.evaluate(async (sid, visibleMs) => {
    await fetch('/api/session/spawn-timeline/mark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sid,
        marks: { first_visible_output: visibleMs },
      }),
    });
  }, spawn.session_id, client.firstVisibleMs);

  const timeline = await page.waitForFunction(async (sid) => {
    const response = await fetch('/api/session/spawn-timeline?session_id=' + encodeURIComponent(sid), {
      cache: 'no-store',
    });
    const data = await response.json();
    const marks = data && data.timeline && data.timeline.marks;
    return marks && marks.client_response_received != null
      && marks.claude_first_text_delta != null
      && marks.client_first_visible_output != null
      ? data.timeline : false;
  }, { timeout: timeoutMs, polling: 50 }, spawn.session_id).then((handle) => handle.jsonValue());

  const marks = timeline.marks;
  const result = {
    ordinal,
    warmup,
    sessionId: spawn.session_id,
    pid: spawn.pid,
    prewarmed: !!timeline.prewarmed,
    prewarmAgeMs: timeline.prewarm_age_ms == null ? null : timeline.prewarm_age_ms,
    clickToAckMs: marks.client_response_received,
    clickToFirstTextDeltaMs: marks.claude_first_text_delta,
    deltaToVisiblePaintMs: marks.client_first_visible_output - marks.claude_first_text_delta,
    clickToFirstVisibleMs: marks.client_first_visible_output,
    observerFirstVisibleMs: Math.round(client.firstVisibleMs * 10) / 10,
    text: client.text.trim(),
    marks,
  };

  return result;
  } finally {
    if (spawnedSessionId) {
      // Use Node's HTTP client rather than the measured page so cleanup still
      // runs if Chromium crashes or detaches the frame mid-sample.
      await fetch(baseUrl + '/api/conversations/' + encodeURIComponent(spawnedSessionId) + '/trash', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: spawnedSessionId, trashed: true }),
      }).catch(() => {});
    }
  }
}

async function warmDashboard(page, initial) {
  if (!initial) {
    await page.evaluate(() => {
      localStorage.removeItem('ccc-last-conv');
      localStorage.removeItem('ccc-last-conv:all');
      localStorage.removeItem('ccc-last-view');
      localStorage.removeItem('ccc-split-state:all');
    });
  }
  const initialArchiveResponse = page.waitForResponse((response) => {
    return new URL(response.url()).pathname === '/api/conversations/list';
  }, { timeout: timeoutMs });
  if (initial) await page.goto(baseUrl, { waitUntil: 'load', timeout: timeoutMs });
  else await page.reload({ waitUntil: 'load', timeout: timeoutMs });
  await initialArchiveResponse;
  await new Promise((resolve) => setTimeout(resolve, 500));
}

(async () => {
  fs.mkdirSync(outputDir, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: findChromePath(),
    args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage();
    page.on('console', (message) => {
      if (message.text().includes('[spawn-perf]')) {
        process.stdout.write(`[browser] ${message.text()}\n`);
      }
    });
    page.on('pageerror', (error) => {
      process.stdout.write(`[browser-error] ${error && error.stack ? error.stack : error}\n`);
    });
    await page.setViewport({ width: 1440, height: 1000 });
    await page.evaluateOnNewDocument((cwd) => {
      localStorage.setItem('ccc-session-view', 'list');
      localStorage.setItem('ccc-spawn-cwd', cwd);
      localStorage.setItem('ccc-spawn-stats', '1');
      // A fresh Puppeteer profile would otherwise open the First Flight
      // overlay after 2.5s and make a DOM-only transcript observation look
      // visible while the conversation is actually covered.
      localStorage.setItem('ccc-tour-done', '1');
      localStorage.removeItem('ccc-last-conv');
      localStorage.removeItem('ccc-last-conv:all');
      localStorage.removeItem('ccc-last-view');
      localStorage.removeItem('ccc-split-state:all');
    }, repoPath);
    await warmDashboard(page, true);

    const results = [];
    const totalRuns = measuredRuns + warmupRuns;
    for (let index = 0; index < totalRuns; index += 1) {
      const warmup = index < warmupRuns;
      const measuredIndex = index - warmupRuns + 1;
      process.stdout.write(`[claude-spawn] ${warmup ? `warmup ${index + 1}/${warmupRuns}` : `run ${measuredIndex}/${measuredRuns}`}...\n`);
      const result = await runOnce(page, index, warmup);
      results.push(result);
      process.stdout.write(
        `[claude-spawn] ack=${result.clickToAckMs}ms first-visible=${result.clickToFirstVisibleMs}ms `
        + `delta-paint=${result.deltaToVisiblePaintMs}ms\n`,
      );
      if (index < totalRuns - 1) await warmDashboard(page, false);
    }

    await page.screenshot({ path: path.join(outputDir, 'final.png'), fullPage: true });
    const measured = results.filter((row) => !row.warmup);
    const summary = {
      baseUrl,
      repoPath,
      prompt,
      prewarmDwellMs: prewarmMs,
      warmupRuns,
      measuredRuns,
      targetsMs: {
        p75ClickToAck: 1000,
        p75DeltaToVisiblePaint: 250,
        p75ClickToFirstVisible: 10000,
      },
      p50Ms: {
        clickToAck: percentile(measured.map((row) => row.clickToAckMs), 0.50),
        deltaToVisiblePaint: percentile(measured.map((row) => row.deltaToVisiblePaintMs), 0.50),
        clickToFirstVisible: percentile(measured.map((row) => row.clickToFirstVisibleMs), 0.50),
      },
      p75Ms: {
        clickToAck: percentile(measured.map((row) => row.clickToAckMs), 0.75),
        deltaToVisiblePaint: percentile(measured.map((row) => row.deltaToVisiblePaintMs), 0.75),
        clickToFirstVisible: percentile(measured.map((row) => row.clickToFirstVisibleMs), 0.75),
      },
      allPrewarmed: measured.every((row) => row.prewarmed),
      results,
    };
    summary.pass = summary.allPrewarmed
      && summary.p75Ms.clickToAck <= summary.targetsMs.p75ClickToAck
      && summary.p75Ms.deltaToVisiblePaint <= summary.targetsMs.p75DeltaToVisiblePaint
      && summary.p75Ms.clickToFirstVisible <= summary.targetsMs.p75ClickToFirstVisible;
    fs.writeFileSync(
      path.join(outputDir, 'results.json'),
      JSON.stringify(summary, null, 2) + '\n',
      'utf8',
    );
    process.stdout.write(`${JSON.stringify({
      p50Ms: summary.p50Ms,
      p75Ms: summary.p75Ms,
      allPrewarmed: summary.allPrewarmed,
    })}\n`);
    if (!summary.pass) throw new Error(`Claude spawn latency budget failed; see ${outputDir}/results.json`);
    process.stdout.write(`PASS Claude spawn latency budget (${outputDir}/results.json)\n`);
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exit(1);
});
