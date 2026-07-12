#!/usr/bin/env node

const fs = require('fs');
const http = require('http');
const path = require('path');
const puppeteer = require('../require-puppeteer.js');

const ROOT = path.resolve(__dirname, '..');
const HTML = fs.readFileSync(path.join(ROOT, 'static', 'throughput.html'));
const FIRST_OUT = process.env.THROUGHPUT_FIRST_OUT || '/tmp/ccc-throughput-first-paint.png';
const FRESH_OUT = process.env.THROUGHPUT_FRESH_OUT || '/tmp/ccc-throughput-refreshed.png';

function findChromePath() {
  const candidates = [
    process.env.SNAPSHOT_CHROME,
    '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ].filter(Boolean);
  return candidates.find((candidate) => {
    try { fs.accessSync(candidate, fs.constants.X_OK); return true; } catch (_) { return false; }
  });
}

function hourlyRows(multiplier) {
  const rows = [];
  const start = Date.now() - 14 * 86400000;
  // Browser persistence compacts the server's hourly payload to the chart's
  // native three-hour resolution; seed that real repeat-visit shape.
  for (let index = 0; index < 14 * 8; index += 1) {
    const raw = ((index * 7919) % 850000 + 80000) * multiplier * 3;
    rows.push({
      hour: new Date(start + index * 3 * 3600000).toISOString(),
      raw_context_tokens: raw,
      effective_input_tokens: Math.round(raw * 0.16),
      output_tokens: Math.round(raw * 0.025),
      cache_read_tokens: Math.round(raw * 0.78),
      cache_write_tokens: Math.round(raw * 0.06),
      total_tokens: Math.round(raw * 1.025),
      turns: 2 + (index % 7),
      cost_usd: raw / 450000,
    });
  }
  return rows;
}

function bootstrap(generatedAt, multiplier, state) {
  const hourly = hourlyRows(multiplier);
  const totalRaw = hourly.reduce((sum, row) => sum + row.raw_context_tokens, 0);
  const totalOut = hourly.reduce((sum, row) => sum + row.output_tokens, 0);
  const totalEffective = hourly.reduce((sum, row) => sum + row.effective_input_tokens, 0);
  const resetAt = new Date(Date.now() + 3.5 * 86400000).toISOString();
  return {
    schema: 1,
    session_id: 'all_7_days',
    engine: 'claude',
    generated_at: generatedAt,
    throughput: {
      ok: true,
      session_id: 'all_7_days',
      scope: { aggregate: true, engine: 'claude', range: 'Last 7 Days', total_turns: 1842 * multiplier },
      summary: {
        total_turns: 1842 * multiplier,
        turns_with_tokens: 1842 * multiplier,
        total_raw_context_tokens: totalRaw,
        total_effective_input_tokens: totalEffective,
        total_output_tokens: totalOut,
        total_active_duration_sec: 68000,
        cache_hit_ratio: 0.91,
        cost_usd: 312.48 * multiplier,
        avg_input_tpm: 380000,
        avg_output_tps: 52,
        hourly,
        daily: [],
        per_model: [{
          model: 'claude-opus-4-6',
          turns: 1842 * multiplier,
          raw_context_tokens: totalRaw,
          effective_input_tokens: totalEffective,
          output_tokens: totalOut,
          cache_hit_ratio: 0.91,
          cost_usd: 312.48 * multiplier,
        }],
      },
      turns: [],
    },
    weekly: {
      available: true,
      pct_per_token: 0.00000072,
      display_pct: 44 * multiplier,
      real_pct: 44 * multiplier,
      est_pct: 45 * multiplier,
      projected_pct: 82 * multiplier,
      weekly_resets_at: resetAt,
      week_start: new Date(new Date(resetAt).getTime() - 7 * 86400000).toISOString(),
      week_start_source: 'reset',
      fable_pct: 17.5,
      fetched_at: new Date(generatedAt * 1000).toISOString(),
      codex: { ok: true, weekly_pct: 13, projected_pct: 29, plan_type: 'plus', session: { pct: 8 } },
    },
    reset_events: [
      { id: 'weekly-reset', kind: 'scheduled', window: 'seven_day', detected_at: new Date(Date.now() - 3.5 * 86400000).toISOString(), source: 'usage' },
      { id: 'manual-reset', kind: 'manual', window: 'seven_day', detected_at: new Date(Date.now() - 1.5 * 86400000).toISOString(), source: 'user' },
    ],
    refresh: {
      state,
      elapsed_ms: state === 'complete' ? 1180 : 940,
      expected_ms: 1180,
      sessions_discovered: 281,
      sessions_read: state === 'complete' ? 281 : 176,
      cache_hits: state === 'complete' ? 269 : 168,
      parsed: state === 'complete' ? 12 : 8,
      last_refreshed_at: generatedAt,
    },
  };
}

const stale = bootstrap(Date.now() / 1000 - 900, 1, 'complete');
const fresh = bootstrap(Date.now() / 1000, 1.08, 'complete');
let initialCalls = 0;
let statusCalls = 0;

function json(response, body, status = 200) {
  const encoded = Buffer.from(JSON.stringify(body));
  response.writeHead(status, {
    'content-type': 'application/json',
    'content-length': encoded.length,
    'cache-control': 'no-store',
  });
  response.end(encoded);
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');
  if (url.pathname === '/throughput' || url.pathname === '/throughput.html') {
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'content-length': HTML.length });
    response.end(HTML);
    return;
  }
  if (url.pathname === '/api/throughput/initial') {
    initialCalls += 1;
    const model = initialCalls === 1 ? stale : fresh;
    const delay = initialCalls === 1 ? 250 : 0;
    setTimeout(() => json(response, { ok: true, bootstrap: model }), delay);
    return;
  }
  if (url.pathname === '/api/throughput/refresh/start') {
    json(response, {
      job_id: 'verify-job', state: 'refreshing', started_at: Date.now() / 1000 - 0.15,
      expected_ms: 1180, sessions_discovered: 281, sessions_read: 23,
      cache_hits: 21, parsed: 2, last_refreshed_at: stale.generated_at, error: null,
    }, 202);
    return;
  }
  if (url.pathname === '/api/throughput/refresh/status') {
    statusCalls += 1;
    const complete = statusCalls >= 3;
    json(response, complete ? Object.assign({}, fresh.refresh, {
      job_id: 'verify-job', state: 'complete', completed_at: fresh.generated_at,
    }) : {
      job_id: 'verify-job', state: 'refreshing', started_at: Date.now() / 1000 - 0.4,
      expected_ms: 1180, sessions_discovered: 281, sessions_read: 90 * statusCalls,
      cache_hits: 84 * statusCalls, parsed: 6 * statusCalls,
      last_refreshed_at: stale.generated_at, error: null,
    });
    return;
  }
  if (url.pathname === '/api/conversations') return json(response, { conversations: [] });
  if (url.pathname === '/api/throughput/week-rankings') return json(response, { rankings: [] });
  if (url.pathname === '/api/throughput/history') return json(response, { daily: [] });
  json(response, { ok: true });
});

(async () => {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  const origin = `http://127.0.0.1:${port}`;
  const browser = await puppeteer.launch({
    executablePath: findChromePath(),
    args: ['--no-sandbox'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1500, height: 1000, deviceScaleFactor: 1 });
  await page.evaluateOnNewDocument((model) => {
    window.__apiRequestTimes = [];
    const originalFetch = window.fetch;
    window.fetch = function(input, init) {
      const url = String(input && input.url || input || '');
      if (url.includes('/api/')) window.__apiRequestTimes.push(performance.now());
      return originalFetch.call(this, input, init);
    };
    const key = `ccc-throughput-bootstrap:v1:${model.engine}:${model.session_id}`;
    localStorage.setItem(key, JSON.stringify(model));
  }, stale);

  await page.goto(`${origin}/throughput.html`, { waitUntil: 'domcontentloaded', timeout: 10000 });
  await page.waitForFunction(() => Number.isFinite(window.__throughputBootstrapRenderMs), { timeout: 5000 });
  const first = await page.evaluate(() => ({
    renderMs: window.__throughputBootstrapRenderMs,
    bootStartedMs: window.__throughputBootStartedMs,
    applyMs: window.__throughputBootstrapApplyMs,
    source: window.__throughputBootstrapSource,
    apiRequestTimes: window.__apiRequestTimes.slice(),
    title: document.getElementById('chart-title-label').textContent,
    chartText: document.getElementById('throughput-chart').textContent,
  }));
  await page.screenshot({ path: FIRST_OUT, fullPage: true });

  if (first.source !== 'browser') throw new Error(`first source was ${first.source}, expected browser`);
  if (first.renderMs >= 100) {
    throw new Error(`cached render ${first.renderMs.toFixed(1)}ms exceeded 100ms (boot ${first.bootStartedMs.toFixed(1)}ms, apply ${first.applyMs.toFixed(1)}ms)`);
  }
  if (first.apiRequestTimes.some((time) => time < first.renderMs)) {
    throw new Error(`an API request started before cached render (${JSON.stringify(first.apiRequestTimes)})`);
  }
  if (first.title !== '3-Hour Cache-Adjusted Burn') throw new Error(`unexpected chart: ${first.title}`);
  if (first.chartText.includes('Preparing first snapshot') || first.chartText.includes('Weekly quota context unavailable')) {
    throw new Error(`cached graph did not render final data: ${first.chartText}`);
  }

  await page.waitForFunction(() => window.__throughputBootstrapSource === 'refresh', { timeout: 10000 });
  await page.screenshot({ path: FRESH_OUT, fullPage: true });
  const finalState = await page.evaluate(() => ({
    status: document.getElementById('refresh-primary').textContent,
    detail: document.getElementById('refresh-secondary').textContent,
    resetMarkers: document.querySelectorAll('.reset-marker-hit').length,
    title: document.getElementById('chart-title-label').textContent,
  }));
  if (!finalState.status.includes('Updated')) throw new Error(`missing completed status: ${finalState.status}`);
  if (!finalState.detail.includes('sessions') || !finalState.detail.includes('cached') || !finalState.detail.includes('parsed')) {
    throw new Error(`missing refresh counters: ${finalState.detail}`);
  }
  if (finalState.resetMarkers < 1) throw new Error('final graph lost reset-limit markers');
  if (finalState.title !== first.title) throw new Error('graph structure changed during refresh');

  console.log(JSON.stringify({
    cachedRenderMs: Number(first.renderMs.toFixed(1)),
    scriptBootMs: Number(first.bootStartedMs.toFixed(1)),
    bootstrapApplyMs: Number(first.applyMs.toFixed(1)),
    apiRequestsBeforeRender: first.apiRequestTimes.filter((time) => time < first.renderMs).length,
    firstScreenshot: FIRST_OUT,
    refreshedScreenshot: FRESH_OUT,
    finalStatus: finalState.status,
    finalDetail: finalState.detail,
    resetMarkers: finalState.resetMarkers,
  }, null, 2));
  await browser.close();
  server.close();
})().catch((error) => {
  console.error(error.stack || error);
  server.close();
  process.exitCode = 1;
});
