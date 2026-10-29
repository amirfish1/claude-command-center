// B20 limit-hit demo: "Your Claude limit hits. Your 12 sessions keep going."
//
// Real CCC UI (current static bundle, list view) on a dataset generated at
// capture time: 12 fake sessions in 4 fake repos, every timestamp relative to
// "now" (no aged-out fixture dates). A fetch shim installed before app.js
// owns the list, live-activity, headroom and free-failover endpoints and plays a 3-act state machine the flow advances:
//
//   working -> limit  (Claude headroom hits 0%, fleet banner: 12 stopped)
//   limit   -> free   (cursor clicks "Continue all 12 free"; the banner's own
//                      POST /api/free-failover/fleet flips every row to free)
//
// Everything else still falls through to the seeded docs/demo/api fixtures.
// The fleet banner is a held pop-up, so the seed previews 'fleet-limit'.
//
// Record: scripts/story-capture/limit-hit-video.sh (records, captions, 16:9 +
// 9:16 mp4s, README gif). Raw clip only:
//   node scripts/story-capture/record.js --flow scripts/story-capture/flows/limit-hit.js --out /tmp/raw.mp4
'use strict';
const { LIST } = require('./_seeds.js');

const REPOS = [
  { label: 'acme-web', path: '/home/demo/code/acme-web' },
  { label: 'widgets-api', path: '/home/demo/code/widgets-api' },
  { label: 'billing-svc', path: '/home/demo/code/billing-svc' },
  { label: 'mobile-app', path: '/home/demo/code/mobile-app' },
];

const SESSIONS = [
  ['Add retry with backoff to webhook worker', 1, 'feat/webhook-retry', 'Edit', 'worker/retry.py'],
  ['Migrate settings page to the new form kit', 0, 'feat/settings-forms', 'Edit', 'src/settings/Form.tsx'],
  ['Fix flaky checkout e2e test', 0, 'fix/checkout-e2e', 'Bash', 'npm test -- checkout'],
  ['Write OpenAPI spec for /v2/orders', 1, 'docs/orders-spec', 'Write', 'openapi/orders.yaml'],
  ['Split invoice PDF renderer into a job', 2, 'feat/pdf-job', 'Edit', 'jobs/invoice_pdf.py'],
  ['Add dark mode tokens', 3, 'feat/dark-tokens', 'Edit', 'theme/tokens.ts'],
  ['Profile slow dashboard query', 1, 'perf/dashboard-query', 'Bash', 'pytest -k dashboard'],
  ['Upgrade React Native to 0.79', 3, 'chore/rn-079', 'Bash', 'pod install'],
  ['Refactor proration math + tests', 2, 'fix/proration', 'Edit', 'billing/proration.py'],
  ['Add image lazy-loading to blog', 0, 'feat/lazy-img', 'Edit', 'src/blog/Image.tsx'],
  ['Rate-limit the public search API', 1, 'feat/search-ratelimit', 'Edit', 'api/limits.py'],
  ['Offline queue for push receipts', 3, 'feat/offline-queue', 'Edit', 'src/queue/receipts.ts'],
];

function sid(i) {
  return '5e55' + String(i + 1).padStart(4, '0') + '-0000-4ddd-8ddd-' + String(i + 1).padStart(12, '0');
}

// Runs in the page before any app script. Must stay self-contained.
function installShim(sessions, repos) {
  const scene = { name: 'working', freeSince: 0 };
  window.__demoScene = scene;
  const t0 = Math.floor(Date.now() / 1000);
  // Claude's 5-hour window: resets ~2h from "now".
  const resetAt = t0 + 2 * 3600 + 14 * 60;

  function conv(row, i) {
    const repo = repos[row.repo];
    const now = Math.floor(Date.now() / 1000);
    const working = scene.name !== 'limit';
    const free = scene.name === 'free';
    return {
      id: row.sid, session_id: row.sid, source: 'interactive', engine: 'claude',
      folder_label: repo.label, folder_path: repo.path, slug: repo.label,
      session_cwd: repo.path, session_cwd_exists: true, session_cwd_is_worktree: false,
      mtime: now - (working ? (i % 4) * 7 + 2 : 40 + i * 3), modified: now - 30,
      last_interacted: now - 60 - i * 20, size: 40000 + i * 5300,
      first_message: row.title, display_name: row.title, name_overridden: false,
      git_branch: row.branch, branch: row.branch, archived: false, worktree_dirty: true,
      has_commit: i % 3 === 0, has_push: false, has_edit: true, pr_notes: [],
      is_live: true, last_event_type: working ? 'assistant' : 'system',
      model: free ? 'glm-4.6' : 'claude-opus-4-6',
      sidecar_status: working ? 'active' : 'idle', sidecar_has_writes: true,
      sidecar_tool: working ? row.tool : null, sidecar_file: working ? row.file : null,
      sidecar_ts: now - 3, sidecar_in_flight: working,
      needs_approval: false, question_waiting: false,
      usage_limit_resume_at: scene.name === 'limit' ? resetAt : null,
    };
  }

  function list() {
    const conversations = sessions.map(conv);
    return { ok: true, conversations, count: conversations.length,
      total_count: conversations.length, window: 'all', cached: true };
  }

  // Row activity overlay ("Editing file", live dot) polls separately.
  function liveActivity() {
    const working = scene.name !== 'limit';
    const out = {};
    sessions.forEach((row) => {
      out[row.sid] = { is_live: true, sidecar_in_flight: working,
        sidecar_status: working ? 'active' : 'idle',
        sidecar_tool: working ? row.tool : null, sidecar_file: working ? row.file : null,
        sidecar_ts: Math.floor(Date.now() / 1000) - 2 };
    });
    return { ok: true, sessions: out };
  }

  function headroom() {
    const left = scene.name === 'working' ? 6 : 0;
    return { ok: true, rows: [
      { id: 'claude:default', engine: 'claude', label: 'Claude', available: true,
        percent_left: left, resets_at: resetAt, burn_pct_per_hour: 31 },
      { id: 'free_router:default', engine: 'free_router', label: 'Free router', available: true, unlimited: true },
    ] };
  }

  function fleet() {
    if (scene.name === 'working') return { ok: true, groups: [] };
    const free = scene.name === 'free';
    return { ok: true, auto_resume_max_per_minute: 5, groups: [{
      key: 'claude', engine: 'claude', engine_label: 'Claude',
      can_continue_free: true, can_auto_resume: true,
      sessions: sessions.map((row) => (free
        ? { session_id: row.sid, display_name: row.title, state: 'free',
            free_model: 'GLM-4.6 via your router', free_since: scene.freeSince }
        : { session_id: row.sid, display_name: row.title, state: 'limited',
            detected_at: t0, resume_at: resetAt, limit_window: 'five_hour' })),
    }] };
  }

  function json(body) {
    return Promise.resolve(new Response(JSON.stringify(body),
      { status: 200, headers: { 'content-type': 'application/json' } }));
  }

  function pathOf(input) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    let p = '';
    try { p = new URL(url, location.href).pathname; } catch (_) {}
    // Demo mode rewrites /api/x to <fixtureBase>/x.json before it reaches us.
    return p.replace(/^\/docs\/demo\/api\//, '/api/').replace(/\.json$/, '');
  }

  // Innermost fetch: app.js (startup gate) and demo mode both wrap
  // window.fetch later and end up calling this for every GET.
  const native = window.fetch.bind(window);
  window.fetch = function demoVideoFetch(input, init) {
    const p = pathOf(input);
    if (p === '/api/conversations/list') return json(list());
    if (p === '/api/headroom') return json(headroom());
    if (p === '/api/sessions/live-activity') return json(liveActivity());
    if (p === '/api/free-failover/status') return json({ ok: true, sessions: [] });
    // Seeded GitHub issues carry months-old dates; keep them out of the list.
    if (p === '/api/issues/all') return json({ fetched_at: Math.floor(Date.now() / 1000), errors: [], issues: [] });
    if (p === '/api/free-failover/fleet') return json(fleet());
    return native(input, init);
  };

  // Demo mode stubs every POST with {ok, demo} (and a read-only toast), so
  // the banner's own POST is answered by an outermost wrapper added once
  // every app wrapper is in place.
  window.addEventListener('load', () => {
    const below = window.fetch;
    window.fetch = function demoVideoPost(input, init) {
      const method = String((init && init.method) || 'GET').toUpperCase();
      if (method !== 'POST' || pathOf(input) !== '/api/free-failover/fleet') return below(input, init);
      const body = JSON.parse((init && init.body) || '{}');
      const results = {};
      (body.session_ids || []).forEach((id) => { results[id] = { ok: true }; });
      if (body.action === 'continue') {
        scene.name = 'free';
        scene.freeSince = Math.floor(Date.now() / 1000);
        // Let the list and bars catch up with the banner.
        setTimeout(() => window.__demoRefresh && window.__demoRefresh(), 50);
      }
      return json({ ok: true, results });
    };
  });
}

// LIMIT_HIT_LAYOUT=portrait records the 9:16 take in CCC's real mobile
// layout (540x960, upscaled 2x to 1080x1920); landscape is 1280x720 (1.5x to
// 1920x1080). Headless screencast frames are CSS-pixel sized whatever the
// deviceScaleFactor, hence the upscale. LIMIT_HIT_MARKS=<file> writes act timestamps (ms since
// the flow started) so the caption track can follow the real clip.
const PORTRAIT = process.env.LIMIT_HIT_LAYOUT === 'portrait';
const marks = {};
let started = 0;
function mark(name) { marks[name] = Date.now() - started; }

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  viewport: PORTRAIT ? '540x960' : '1280x720',
  localStorage: {
    ...LIST,
    'ccc-sidebar-width': PORTRAIT ? '340' : '1080',
    'ccc-coding-density': 'detailed',
    'ccc-popups-preview': 'fleet-limit',
  },
  lead: 2800,
  tail: 4500,
  async setup(page) {
    const rows = SESSIONS.map(([title, repo, branch, tool, file], i) => (
      { title, repo, branch, tool, file, sid: sid(i) }));
    await page.evaluateOnNewDocument(installShim, rows, REPOS);
    // The list is the subject: hide the floating voice button (mobile) and
    // the "Select a session" placeholder squeezed beside the wide sidebar.
    await page.evaluateOnNewDocument(() => {
      document.addEventListener('DOMContentLoaded', () => {
        const style = document.createElement('style');
        style.textContent = '#voiceMobileBtn{display:none!important}'
          + '#conversationsView>.empty-state{visibility:hidden}';
        document.head.appendChild(style);
      });
    });
  },
  async run(ctx) {
    started = Date.now();
    await ctx.eval(() => {
      window.__demoRefresh = function () {
        if (window.cccHeadroom) window.cccHeadroom.poll();
        if (window.cccFleetFailover) window.cccFleetFailover.refresh();
        // The sidebar re-fetches its list on focus return (app.js
        // _resumeForegroundPollers); its own poll is 90s.
        window.dispatchEvent(new Event('focus'));
      };
    });
    // The sidebar holds re-renders while the pointer hovers a row; park it
    // off the list before the state changes.
    await ctx.move(PORTRAIT ? { x: 470, y: 780 } : { x: 1220, y: 420 }, { duration: 500 });
    // Act 2: the limit hits.
    await ctx.eval(() => { window.__demoScene.name = 'limit'; window.__demoRefresh(); });
    await ctx.waitFor('#cccFleetFailoverHost [data-act="continue"]');
    mark('limit');
    // Rows drop their "Editing file" activity once the list re-polls. A
    // refresh fired while boot's list load is in flight dedupes onto it, so
    // re-fire until the rows read stopped (bounded).
    for (let i = 0; i < 16; i++) {
      const stopped = await ctx.eval(() => !/Editing file|Bash command|Writing file/
        .test((document.querySelector('.conv-item') || {}).innerText || ''));
      if (stopped) break;
      if (i % 3 === 2) await ctx.eval(() => window.dispatchEvent(new Event('focus')));
      await ctx.pause(500);
    }
    await ctx.pause(2400);
    // Act 3: one click, the fleet keeps going.
    mark('click');
    await ctx.click('#cccFleetFailoverHost [data-act="continue"]', { duration: 900, settle: 300 });
    await ctx.waitFor('#cccFleetFailoverHost [data-act="expand"]');
    mark('free');
    await ctx.pause(700);
    await ctx.click('#cccFleetFailoverHost [data-act="expand"]', { duration: 700, settle: 200 });
    // Park the pointer off the card so the rows read cleanly.
    await ctx.move(PORTRAIT ? { x: 470, y: 900 } : { x: 1180, y: 640 }, { duration: 700 });
    mark('end');
    if (process.env.LIMIT_HIT_MARKS) {
      require('fs').writeFileSync(process.env.LIMIT_HIT_MARKS, JSON.stringify(marks) + '\n');
    }
  },
};
