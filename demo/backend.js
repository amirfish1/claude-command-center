// In-browser stand-in for the CCC server, used only by the static demo.
//
// The UI is the real static/ bundle; this file answers its /api/* calls from
// the invented data in dataset.js and keeps state in memory, so a tapped
// decision really changes the row, the transcript and the counts. Nothing
// leaves the browser and a reload starts over.
(function () {
  'use strict';
  const data = window.__TRY_DEMO_DATA__;
  const now = () => Date.now() / 1000;
  const misses = window.__demoMisses = {};
  const HOME = '/home/demo/code/';
  // Short simulation clock: fast enough to see inside ten seconds of looking.
  const TICK_MS = 2200;

  // Skip first-run chrome. Each key is only a default, so a visitor's own
  // choices (view, theme) survive a reload.
  const PREFS = {
    'ccc-last-seen-version': 'demo', 'ccc-whats-new-dismissed-version': 'demo',
    'ccc-pwa-install-dismissed': '9999999999999', 'ccc-telemetry-bar-dismissed': '1',
    'ccc-onboarded': '1', 'ccc-engines-first-run-done': '1', 'ccc-tour-done': '1', 'ccc-status-rail-collapsed': '1',
  };
  try { for (const k in PREFS) if (localStorage.getItem(k) === null) localStorage.setItem(k, PREFS[k]); } catch (_) {}

  function sid(i) {
    const n = String(i + 1).padStart(12, '0');
    return 'dddddddd-0000-4000-8000-' + n;
  }
  function isoAt(t) { return new Date(t * 1000).toISOString(); }

  function footer(s) {
    const st = s.session_state;
    const lines = ['<session-state>', 'DID: ' + (st.did || ''), 'INSIGHT: ' + (st.insight || ''),
      'NEXT_STEP_USER: ' + (st.next_step_user || 'None.')];
    const comp = Object.assign({}, st.completion);
    delete comp.valid; delete comp.ready_to_close; delete comp.reasons;
    lines.push('<!--', 'NEXT_ACTION: ' + JSON.stringify(st.next_actions || null),
      'COMPLETION: ' + JSON.stringify(comp), '-->', '</session-state>');
    return lines.join('\n');
  }
  function axis(score, evidence) { return { score, evidence }; }
  function completion(kind, plan) {
    const ready = kind === 'ready';
    const open = kind === 'decide' ? 0.5 : 0;
    return {
      version: 1, valid: true, ready_to_close: ready, reasons: ready ? [] : ['Work remains.'],
      resolution: axis(ready ? 1 : 0.6, ready ? 'Fix is in and checked.' : 'Part of the work is done.'),
      verification: axis(ready ? 0.9 : 0.5, ready ? 'Targeted tests pass.' : 'Some checks still to run.'),
      delivery: Object.assign(axis(ready ? 1 : 0, ready ? 'Merged to the invented main branch.' : 'On a branch.'), { applicable: true }),
      decision_dependency: axis(open, open ? 'A choice is waiting on you.' : 'No decision needed.'),
      caveats: [],
      remaining_actions: plan ? [{ text: plan, owner: 'agent', confidence: 0.9, downside: 0.1, reversible: true, within_scope: true }] : [],
    };
  }
  function stateFor(kind, spec) {
    const chips = spec.chips ? spec.chips.map(c => ({ label: c.label, reply: c.reply })) : null;
    const st = { did: spec.did || '', insight: spec.insight || '',
      next_step_user: kind === 'decide' ? spec.next : 'None.',
      completion: completion(kind, spec.plan) };
    if (chips && chips.length) { st.next_actions = chips; st.next_action = chips[0]; }
    return st;
  }

  const sessions = data.sessions.map((spec, i) => {
    const t = now() - spec.age * 60 - i;
    const s = {
      session_id: sid(i), id: sid(i), source: spec.engine, engine: spec.engine,
      folder_label: spec.repo, folder_path: HOME + spec.repo, slug: spec.repo, session_cwd: HOME + spec.repo,
      session_cwd_exists: true, session_cwd_is_worktree: false, pinned_repo: false,
      mtime: t, modified: t, size: 40000 + i * 917, first_message: spec.prompt, ai_title: null,
      branch: spec.branch, git_branch: spec.branch, effective_branch: spec.branch,
      display_name: spec.title, spawn_named: true, name_overridden: false, auto_titled: false,
      archived: false, trashed: false, verified: false, pinned: false, pin_rank: 0,
      worktree_dirty: spec.kind !== 'ready', has_commit: true, has_push: spec.kind === 'ready', has_edit: true,
      tail_pr_number: null, tail_pr_url: null, pr_state: null, pr_notes: [], is_live: true,
      subagent_in_flight_count: 0, workflows: [], model: spec.engine === 'claude' ? 'claude-sonnet-5-5' : '',
      needs_approval: false, question_waiting: false, pending_tool: null, pending_file: null,
      sidecar_status: null, sidecar_has_writes: false, sidecar_tool: null, sidecar_in_flight: false,
      last_event_type: 'assistant', state: 'idle', last_assistant_text: '', last_assistant_preview: '',
    };
    const events = [];
    const rec = { s, spec, events, line: 0, stepIndex: 0, timers: [] };
    pushEvent(rec, { type: 'user_text', ts: isoAt(t - 240), text: spec.prompt }, true);
    (spec.work || []).forEach((w, n) => pushEvent(rec, {
      type: 'assistant', ts: isoAt(t - 200 + n * 30), blocks: [{ kind: 'tool_use', name: w[0], detail: w[1] }] }, true));
    if (spec.kind === 'working') {
      s.state = 'working';
      s.last_assistant_text = s.last_assistant_preview = spec.say;
      pushEvent(rec, { type: 'assistant', ts: isoAt(t), blocks: [{ kind: 'text', text: spec.say }] }, true);
      setTool(s, spec.steps[0]);
    } else if (spec.kind === 'approve') {
      s.session_state = stateFor('update', spec);
      s.session_state.next_step_user = spec.approval;
      s.state = 'waiting';
      s.needs_approval = true;
      s.needs_approval_message = spec.approval;
      s.pending_tool = spec.tool;
      s.pending_file = spec.file;
      s.last_assistant_text = spec.approval;
      pushEvent(rec, { type: 'assistant', ts: isoAt(t), blocks: [
        { kind: 'text', text: spec.did + ' Waiting for your permission to apply it.' },
        { kind: 'tool_use', name: spec.tool, detail: spec.file }] }, true);
    } else {
      finishTurn(rec, spec.kind, spec, t, true);
    }
    return rec;
  });
  const byId = new Map(sessions.map(r => [r.s.session_id, r]));

  function setTool(s, step) {
    s.sidecar_status = 'active'; s.sidecar_in_flight = true;
    s.sidecar_tool = step[0]; s.sidecar_file = step[1];
    s.sidecar_ts = now(); s.sidecar_has_writes = step[0] === 'Edit' || step[0] === 'Write';
  }
  function clearTool(s) {
    s.sidecar_status = null; s.sidecar_in_flight = false; s.sidecar_tool = null; s.sidecar_file = null;
  }
  function touch(s) { const t = now(); s.mtime = t; s.modified = t; s.size += 311; }

  // The real server pushes "the list changed" over SSE; do the same in-page so
  // a row flips at the moment its session does, not at the next slow poll.
  function invalidateRows() {
    const events = window.__cccDashboardEvents;
    if (!events) return;
    events.schedule('sessions');
    events.schedule('archive');
  }

  const subscribers = new Set();
  function pushEvent(rec, ev, quiet) {
    rec.line += 1;
    const full = Object.assign({ line: rec.line }, ev);
    rec.events.push(full);
    if (!quiet || quiet === 'keep-order') {
      if (!quiet) touch(rec.s);
      subscribers.forEach(sub => sub.deliver(rec.s.session_id, full));
    }
  }
  function finishTurn(rec, kind, spec, t, quiet) {
    const s = rec.s;
    s.session_state = stateFor(kind, spec);
    s.state = 'idle';
    s.last_event_type = 'assistant';
    clearTool(s);
    const prose = [spec.did, spec.insight].filter(Boolean).join(' ');
    s.last_assistant_text = s.last_assistant_preview = prose;
    pushEvent(rec, { type: 'assistant', ts: isoAt(t || now()),
      blocks: [{ kind: 'text', text: prose + '\n\n' + footer(s) }] }, quiet);
    if (!quiet) invalidateRows();
  }

  // A reply or tap: the agent picks the work back up, then lands it.
  function deliver(rec, text) {
    const s = rec.s;
    const chips = (rec.spec.chips || []).concat((rec.spec.becomes && rec.spec.becomes.chips) || []);
    const chip = chips.find(c => c.reply === text);
    pushEvent(rec, { type: 'user_text', ts: isoAt(now()), text });
    s.last_event_type = 'user';
    s.needs_approval = false; s.needs_approval_message = '';
    s.pending_tool = null; s.pending_file = null;
    s.state = 'working';
    s.session_state = null;
    rec.timers.forEach(clearTimeout);
    const tool = (chip && chip.tool) || ['Read', 'README.md'];
    const test = (chip && chip.test) || (rec.spec.kind === 'approve' ? 'scripts/health-check.sh staging' : 'npm test');
    const after = (chip && chip.after) || rec.spec.after
      || 'Pretend agent here: this is a demo, so no real agent read that. Install CCC to reply to real sessions.';
    const say = (ms, fn) => rec.timers.push(setTimeout(fn, ms));
    say(900, () => {
      s.last_event_type = 'assistant';
      s.last_assistant_text = s.last_assistant_preview = 'On it.';
      setTool(s, tool);
      pushEvent(rec, { type: 'assistant', ts: isoAt(now()), blocks: [
        { kind: 'text', text: 'On it.' }, { kind: 'tool_use', name: tool[0], detail: tool[1] }] });
      invalidateRows();
    });
    say(2600, () => {
      setTool(s, ['Bash', test]);
      pushEvent(rec, { type: 'assistant', ts: isoAt(now()), blocks: [{ kind: 'tool_use', name: 'Bash', detail: test }] });
    });
    say(4600, () => finishTurn(rec, 'ready', { did: after, insight: 'Checks passed on the invented test suite.' }));
  }

  // Working sessions keep moving while you watch, and two of them end their
  // turn with a fresh decision, so the count of who needs you changes live.
  let tick = 0;
  setInterval(() => {
    tick += 1;
    const working = sessions.filter(r => r.spec.kind === 'working' && r.s.state === 'working' && !r.landed);
    if (!working.length) return;
    const rec = working[tick % working.length];
    const steps = rec.spec.steps;
    rec.stepIndex += 1;
    if (rec.stepIndex >= steps.length && rec.spec.becomes) {
      rec.landed = true;
      rec.spec.chips = rec.spec.becomes.chips;
      finishTurn(rec, 'decide', rec.spec.becomes);
      return;
    }
    const step = steps[rec.stepIndex % steps.length];
    setTool(rec.s, step);
    pushEvent(rec, { type: 'assistant', ts: isoAt(now()), blocks: [{ kind: 'tool_use', name: step[0], detail: step[1] }] }, 'keep-order');
  }, TICK_MS);

  const api = window.__tryDemo = { sessions, counts() {
    const live = sessions.map(r => r.s).filter(s => !s.archived && !s.trashed);
    return {
      total: live.length,
      working: live.filter(s => s.state === 'working').length,
      needsYou: live.filter(s => s.needs_approval || (s.state !== 'working' && s.session_state
        && !/^none/i.test(s.session_state.next_step_user || 'none'))).length,
    };
  } };

  // ---- Static answers ----------------------------------------------------
  const ENGINES = ['claude', 'codex', 'gemini', 'cursor'];
  const APP_VERSION = 'demo';
  const STATIC = {
    '/api/features': { version: APP_VERSION, morning: false, preview: {}, preview_meta: [] },
    '/api/config': { app_name: 'Claude Command Center', title_strip: [], repo: HOME + data.repos[0],
      vercel_enabled: false, vercel_project: '', pkood_enabled: false, gh_enabled: false, orgs: [],
      capabilities: { platform: 'demo', screenshots: false, annotate: false, terminalJump: false, launchTerminal: false,
        answerPermission: true, folderPicker: false, desktopDeepLinks: false, revealFile: false, openBrowser: false, notifications: false },
      annotate_target: '' },
    '/api/identity': { label: 'demo', install_path: '', port: 0, pid: 0, version: APP_VERSION, node_id: 'demo' },
    '/api/version': { version: APP_VERSION, last_updated: '', pid: 0, started_at: now(), uptime_s: 0, code_rev: 'demo' },
    '/api/version/check': { ok: true, update_available: false, current: APP_VERSION, latest: APP_VERSION },
    '/api/healthcheck': { checks: [], overall: 'ok' },
    '/api/health': { cpu: 3, build_ms: { last: 4, avg: 4, count: 1 }, recent_errors: 0, error_window_min: 5, uptime_s: 60, pid: 0,
      resume: {}, stray_processes_reaped: [], inject_blocked: [], worker_compat: { ok: true, reachable: true, idle: true } },
    '/api/onboarding/status': { completed: true, completed_at: '2026-01-01T00:00:00Z', clis: {} },
    '/api/onboarding/moment-zero': { ok: true, fresh_install: false, has_history: true,
      signals: { onboarding_completed: true, claude_transcripts: true, codex_sessions: true } },
    '/api/engines/installed': { engines: ENGINES.map(e => ({ engine: e, label: e[0].toUpperCase() + e.slice(1), installed: true, kind: 'cli', detail: 'demo' })) },
    '/api/engines/doctor': { generated_at: isoAt(now()), engines: Object.fromEntries(ENGINES.map(e => [e, {
      cli_present: true, cli_bin: e, auth_present: true, auth_source: 'demo', byok_ready: false, byok_profile_present: false, smoke_dry_run: { ok: true } }])) },
    '/api/engines/update-status': { ok: true, automatic: false, interval_seconds: 0, running: false, engines: {} },
    '/api/engines/models': { ok: true, engines: {}, models: {} },
    '/api/spawn-defaults': { ok: true, engine: 'claude', models: { claude: 'claude-sonnet-5-5' }, reasoning_effort: '', auto_compact_k: 0,
      worker_engine: 'claude', worker_model: '', worker_reasoning_effort: '', worker_auto_compact_k: 0,
      worker_fallback: { enabled: false, models: [] }, model_profiles: {}, disabled_engines: [], worker_inherit_session: false, worker_overrides: {} },
    '/api/repo/list': { repos: data.repos.map(r => ({ path: HOME + r, name: r, label: r })), current: HOME + data.repos[0], active: HOME + data.repos[0] },
    '/api/repo/guess/status': { ok: true, running: false, done: true },
    '/api/loading-status': { active: false, title: '', message: '', phase: 'ready', started_at: 0, updated_at: 0, steps: [] },
    '/api/archive/loading-status': { active: false, title: '', message: '', phase: 'ready', started_at: 0, updated_at: 0, steps: [] },
    '/api/history/status': { ok: true, ready: true, indexing: false },
    '/api/today-stars': { ok: true, stars: {} },
    '/api/question-tags': { ok: true, questions: [], scanning: false, refreshing: false },
    '/api/telemetry/status': { opt_in: false, opted_in: false, retired: true, asked_at: '2026-01-01', install_id_present: false, env_disabled: true },
    '/api/config-consent': { ok: true, items: [], needs_review: 0, auto_review: 0, engines_present: {}, notice: { pending: false, items: [] } },
    '/api/apps': { apps: [] },
    '/api/conv-reader/info': { port: 0, ready: false },
    '/api/settings/capabilities': { capabilities: {} },
    '/api/control-plane/status': { ok: true, available: false, active: 0, queued: 0, counts: {}, drain: { enabled: false } },
    '/api/free-runtime/status': { ok: true, ready: false, engines: {} },
    '/api/objects': { objects: [], parents: {} },
    '/api/jobs': { ok: true, hosts: {}, jobs: [] },
    '/api/group-chats/active': { ok: true, chats: [] },
    '/api/queue/list': { ok: true, queues: [] },
    '/api/queue/status': { ok: true, items: [] },
    '/api/model-picker/picks': { ok: true, picks: [] },
    '/api/secrets/pending': { ok: true, requests: [] },
    '/api/notify/pending': { ok: true, items: [] },
    '/api/interrupt-asks': { ok: true, items: [] },
    '/api/watchtower/alerts': { ok: true, alerts: [] },
    '/api/watchtower/service/status': { ok: true, installed: false, running: false, state: 'absent' },
    '/api/headroom': { ok: true, rows: [], accounts: [] },
    '/api/usage/current': { ok: false },
    '/api/savings': { ok: false },
    '/api/sessions/spawned': { ok: true, sessions: [] },
    '/api/voice/status': { ok: true, enabled: false, available: false },
    '/api/background-runs': { ok: true, runs: [] },
    '/api/bridge-recovery/blockers': { ok: true, sessions: [] },
    '/api/byok/profiles': { ok: true, profiles: [] },
    '/api/byok/usage': { ok: true, usage: [] },
    '/api/car-mode/status': { ok: true, enabled: false },
    '/api/cloud-relay/indicator': { ok: true, state: 'off', enabled: false },
    '/api/codex/stuck-summary': { ok: true, stuck: [], count: 0 },
    '/api/free-failover/fleet': { ok: true, walls: [], engines: [] },
    '/api/free-failover/status': { ok: true, active: false },
    '/api/free-runtime/tts-voices': { ok: true, voices: [] },
    '/api/group-chats/archived': { ok: true, chats: [] },
    '/api/issues/all': { ok: true, issues: [] },
    '/api/local-speech/status': { ok: true, available: false },
    '/api/queue/import-doc': { ok: true, docs: [] },
    '/api/restart-recovery': { ok: true, active: false, items: [] },
    '/api/settings/changes': { ok: true, changes: [] },
    '/api/settings/history': { ok: true, history: [] },
    '/api/settings/preferences/registry': { ok: true, preferences: [] },
    '/api/settings/profiles': { ok: true, profiles: [] },
    '/api/settings/resets': { ok: true, resets: [] },
    '/api/system/anomalies': { ok: true, anomalies: [] },
    '/api/system/services': { ok: true, services: [] },
    '/api/throughput/daily': { ok: true, days: [] },
    '/api/throughput/history': { ok: true, history: [] },
    '/api/vault': { ok: true, entries: [] },
    '/api/wt/workers': { ok: true, workers: [] },
    '/api/sessions/children': { ok: true, children: [] },
    '/api/sessions/family': { ok: true, sessions: [] },
    '/api/session/spawn-timeline': { ok: true, events: [] },
    '/api/federation/handoff/status': { ok: true, active: false },
    '/api/nextjs/status': { ok: true, running: false },
    '/api/voice-focus': { ok: true },
  };

  const INSTALL_NOTE = 'That needs a real machine behind it. Install CCC to do this with your own agents.';
  const SILENT_POSTS = new Set(['/api/client-log', '/api/perf-event', '/api/objects/import',
    '/api/free-runtime/tts-log', '/api/free-runtime/tts']);

  function snapshot() { return sessions.map(r => JSON.parse(JSON.stringify(r.s))); }
  function attention() {
    const items = sessions.filter(r => r.s.needs_approval || (r.s.state !== 'working' && r.s.session_state)).map((r, i) => ({
      kind: r.s.needs_approval ? 'approval' : 'next', priority: i, session_id: r.s.session_id, name: r.s.display_name,
      where: r.s.folder_label, did: (r.s.session_state || {}).did || '', insight: (r.s.session_state || {}).insight || '',
      next_step: (r.s.session_state || {}).next_step_user || '', has_structured: true, live_feed: true, repo: r.s.folder_label,
      folder_label: r.s.folder_label, folder_path: r.s.folder_path, mtime: r.s.mtime, engine: r.s.engine, turns: [],
    }));
    return { ok: true, scope: 'all', recent_only: false, window_hours: null, items, shown: items.length, total: items.length, turns_enriched: 0 };
  }
  function conversation(id, q) {
    const rec = byId.get(id);
    if (!rec) return { events: [], last_line: 0 };
    let events = rec.events;
    if (q.get('after') !== null) events = events.filter(e => e.line > Number(q.get('after')));
    if (q.get('tail') !== null) events = events.slice(-Number(q.get('tail')));
    return { events, last_line: rec.line, total_lines: rec.line, session_id: id };
  }
  function liveActivity() {
    const out = {};
    for (const { s } of sessions) {
      out[s.session_id] = { is_live: true, state: s.state, last_event_type: s.last_event_type, mtime: s.mtime, modified: s.modified,
        sidecar_status: s.sidecar_status, sidecar_in_flight: s.sidecar_in_flight, sidecar_tool: s.sidecar_tool,
        sidecar_file: s.sidecar_file, sidecar_ts: s.sidecar_ts || 0, sidecar_has_writes: s.sidecar_has_writes,
        needs_approval: s.needs_approval, needs_approval_message: s.needs_approval_message || '', question_waiting: false };
    }
    return { sessions: out, kill_events: [], prewarm_events: [], interrupt_asks: [], recent_spawns: [] };
  }
  function sessionStatus(id) {
    const rec = byId.get(id);
    if (!rec) return { sessions: [] };
    const s = rec.s;
    return { live: s.state === 'working' || s.needs_approval, status: s.sidecar_status, sidecar_tool: s.sidecar_tool,
      sidecar_file: s.sidecar_file, sidecar_in_flight: s.sidecar_in_flight, needs_approval: s.needs_approval,
      needs_approval_message: s.needs_approval_message || '', approval_hook_parked: s.needs_approval,
      headless_present: s.state === 'working' };
  }
  function answer(path, q, method, body) {
    if (method !== 'GET') return post(path, body);
    if (path === '/api/conversations/list' || path === '/api/conversations/all') {
      const rows = snapshot();
      return { ok: true, conversations: rows, count: rows.length, total_count: rows.length, window: q.get('window') || 'all',
        fields: [], queue_health: { unfinished_turns: 0, queued_items: 0, stale_queued_items: 0, stale_queued_wait_s: 0 }, cached: false };
    }
    if (path === '/api/sessions') return snapshot();
    if (path === '/api/attention') return attention();
    if (path === '/api/sessions/live-activity') return liveActivity();
    if (path === '/api/session-status') return sessionStatus(q.get('session_id'));
    let m = path.match(/^\/api\/conversations\/([^/]+)$/);
    if (m) return conversation(decodeURIComponent(m[1]), q);
    if (/^\/api\/conversations\/[^/]+\/files$/.test(path)) return { ok: true, files: [] };
    if (/^\/api\/conversations\/[^/]+\/(tokeneconomizer|transcript-path)$/.test(path)) return { ok: false };
    if (/^\/api\/session\/[^/]+\/(usage|spawn-info|workspace)$/.test(path)) return { ok: false };
    if (/^\/api\/session\/[^/]+\/timeline$/.test(path)) return { ok: true, events: [] };
    if (/\/spawn-[a-z]+\/availability$/.test(path)) return { available: true, bin: 'demo', model: '', source: 'demo' };
    if (Object.prototype.hasOwnProperty.call(STATIC, path)) return STATIC[path];
    misses['GET ' + path] = (misses['GET ' + path] || 0) + 1;
    return { ok: false, demo: true };
  }
  function post(path, body) {
    if (SILENT_POSTS.has(path)) return { ok: true };
    if (path === '/api/inject-input') {
      const rec = byId.get(body && body.session_id);
      if (!rec) return { ok: false, error: 'Unknown demo session' };
      deliver(rec, String(body.text || ''));
      api.taps = (api.taps || 0) + 1;
      return { ok: true, queued: false, delivered: true, delivery_id: 'demo-' + Date.now() };
    }
    if (path === '/api/claude/permission') {
      const rec = byId.get(body && body.session_id);
      if (!rec) return { ok: false, error: 'Unknown demo session' };
      api.taps = (api.taps || 0) + 1;
      deliver(rec, body.decision === 'decline' ? 'Denied. Do not run that.' : 'Approved.');
      return { ok: true };
    }
    const archive = path.match(/^\/api\/conversations\/([^/]+)\/archive$/);
    if (archive && byId.has(decodeURIComponent(archive[1]))) {
      const s = byId.get(decodeURIComponent(archive[1])).s;
      s.archived = !(body && body.archived === false);
      return { ok: true, archived: s.archived };
    }
    misses['POST ' + path] = (misses['POST ' + path] || 0) + 1;
    if (window.__tryDemoToast) window.__tryDemoToast(INSTALL_NOTE);
    if (/\/spawn/.test(path)) return { ok: false, error: INSTALL_NOTE };
    return { ok: true, demo: true };
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = function demoBackendFetch(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    let u;
    try { u = new URL(url, location.href); } catch (_) { return realFetch(input, init); }
    if (u.origin !== location.origin || !u.pathname.includes('/api/')) return realFetch(input, init);
    const path = u.pathname.slice(u.pathname.indexOf('/api/'));
    const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    let body = null;
    try { body = init && init.body ? JSON.parse(init.body) : null; } catch (_) {}
    const result = answer(path, u.searchParams, method, body);
    return new Promise(res => setTimeout(() => res(new Response(JSON.stringify(result), {
      status: 200, headers: { 'content-type': 'application/json' } })), 15));
  };

  class FakeEventSource {
    constructor(url) {
      this.url = String(url); this.readyState = 1; this.listeners = {};
      this.onmessage = null; this.onopen = null; this.onerror = null;
      const m = this.url.match(/\/api\/conversations\/([^/]+)\/stream\?after=(-?\d+)/);
      this.sid = m ? decodeURIComponent(m[1]) : null;
      this.after = m ? Number(m[2]) : 0;
      if (this.sid) subscribers.add(this);
      this.keepalive = setInterval(() => this.emit('keepalive', {}), 5000);
      setTimeout(() => { if (this.onopen) this.onopen({}); this.catchUp(); }, 0);
    }
    emit(type, ev) { (this.listeners[type] || []).forEach(fn => fn(ev)); }
    catchUp() {
      const rec = this.sid && byId.get(this.sid);
      if (!rec) return;
      const fresh = rec.events.filter(e => e.line > this.after);
      if (fresh.length) this.send(fresh, rec.line);
    }
    deliver(id, ev) { if (id === this.sid) this.send([ev], ev.line); }
    send(events, last) {
      this.after = last;
      if (this.onmessage) this.onmessage({ data: JSON.stringify({ events, last_line: last }) });
    }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter(f => f !== fn); }
    close() { this.readyState = 2; subscribers.delete(this); clearInterval(this.keepalive); }
  }
  FakeEventSource.CONNECTING = 0; FakeEventSource.OPEN = 1; FakeEventSource.CLOSED = 2;
  window.EventSource = FakeEventSource;

  // No backend means no service worker to install, and root-absolute history
  // URLs would walk the visitor out of a project-path deployment.
  try {
    if (navigator.serviceWorker) navigator.serviceWorker.register = () => Promise.resolve({});
    const base = location.pathname.replace(/[^/]*$/, '');
    ['pushState', 'replaceState'].forEach(name => {
      const real = history[name].bind(history);
      history[name] = (st, title, url) => real(st, title, url === '/' ? base : url);
    });
  } catch (_) {}
})();
