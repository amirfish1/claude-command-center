/* Fleet — the repo × node matrix, as a standalone page.
 *
 * Moved out of app.js/index.html deliberately. A fleet scan touches every
 * mapped repo on every paired node; on a real machine that is tens of repos
 * and hundreds of git subprocesses. As a dashboard modal it blocked the
 * whole UI behind a spinner for minutes, so it lives on its own page now
 * (opened from the dashboard's Fleet pill, like /throughput.html).
 *
 * Loading is two-phase, because the dimensions have wildly different costs:
 *
 *   pass 1  git only (prs=0 deploy=0 sessions=0)  — seconds, paints the matrix
 *   pass 2  everything                            — `gh` + deploy provider +
 *                                                   the all-rows archive cache
 *
 * Pass 2 runs in the background and patches the rendered cells. Dimensions
 * it has not answered for yet render as an explicit "checking…" placeholder
 * rather than as a zero, so the page never shows an absence as a fact.
 *
 * Self-contained on purpose: it borrows app.css for the .fleet-/.upd-/.fed-
 * component classes but shares no JavaScript with app.js.
 */
(function () {
  'use strict';

  // ── Small local helpers (deliberate duplicates of app.js's) ──
  function fedEsc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function fedShortId(id) {
    const s = String(id || '');
    return s.length > 8 ? s.slice(0, 8) : s;
  }
  function relativeTime(ts) {
    const n = Number(ts);
    if (!isFinite(n) || n <= 0) return 'unknown';
    const secs = Math.max(0, Math.floor(Date.now() / 1000 - n));
    if (secs < 45) return 'just now';
    if (secs < 90) return '1 min ago';
    if (secs < 3600) return Math.round(secs / 60) + ' min ago';
    if (secs < 7200) return '1 hour ago';
    if (secs < 86400) return Math.round(secs / 3600) + ' hours ago';
    if (secs < 172800) return 'yesterday';
    return Math.round(secs / 86400) + ' days ago';
  }
  let toastTimer = null;
  function showOpToast(msg, kind) {
    let el = document.getElementById('fleetToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'fleetToast';
      el.className = 'fleet-toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.className = 'fleet-toast is-' + (kind || 'ok') + ' is-visible';
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = 'fleet-toast'; }, 4000);
  }

  const $fleetRefreshBtn = document.getElementById('fleetRefreshBtn');
  const $fleetRefreshSpinner = document.getElementById('fleetRefreshSpinner');
  const $fleetRefreshTime = document.getElementById('fleetRefreshTime');
  const $fleetResolveBtn = document.getElementById('fleetResolveBtn');
  const $fleetHealthStrip = document.getElementById('fleetHealthStrip');
  const $fleetMatrix = document.getElementById('fleetMatrix');
  const $fleetError = document.getElementById('fleetError');
  const $fleetProgress = document.getElementById('fleetProgress');
  let fleetInventory = null;
  let fleetLoading = false;
  // True while pass 2 is still in flight — drives the "checking…" chips.
  let fleetEnriching = false;

  function fleetSetError(msg) {
    if (!$fleetError) return;
    $fleetError.textContent = msg || '';
    $fleetError.classList.toggle('visible', !!msg);
  }
  // The spinner-with-no-explanation was the original complaint: a bare
  // "Loading…" for two minutes is indistinguishable from a hang. Every phase
  // says what it is doing and over how many repos.
  function fleetSetProgress(msg, busy) {
    if (!$fleetProgress) return;
    $fleetProgress.innerHTML = msg
      ? (busy ? '<span class="fleet-spinner"></span>' : '') + '<span>' + fedEsc(msg) + '</span>'
      : '';
    $fleetProgress.classList.toggle('visible', !!msg);
  }
  function fleetRel(ts) {
    const n = Number(ts);
    return (isFinite(n) && n > 0) ? relativeTime(n) : 'unknown';
  }
  function fleetObsTitle(label, ts) {
    return (label ? label + ' - ' : '') + 'observed ' + fleetRel(ts);
  }
  function fleetBase(p) {
    const parts = String(p || '').split('/');
    return parts[parts.length - 1] || String(p || '');
  }
  async function fleetGet(path) {
    const r = await fetch(path, { cache: 'no-store' });
    const d = await r.json().catch(() => ({}));
    return { status: r.status, data: d || {} };
  }
  async function fleetPost(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const d = await r.json().catch(() => ({}));
    return { status: r.status, data: d || {} };
  }

  // ── Node health strip ──
  function renderFleetHealth(nodes) {
    if (!$fleetHealthStrip) return;
    if (!nodes || !nodes.length) {
      $fleetHealthStrip.innerHTML = '<div class="fed-empty">No nodes.</div>';
      return;
    }
    $fleetHealthStrip.innerHTML = nodes.map((n) => {
      const name = n.name || fedShortId(n.node_id);
      const ok = n.ok !== false && !n.error;
      let cls = 'fleet-node-chip ' + (ok ? 'is-ok' : 'is-err');
      if (n.stale) cls += ' is-stale';
      let html = '<span class="fleet-node-name" title="' + fedEsc(n.node_id) + '">' + fedEsc(name) + '</span>';
      if (n.self) html += '<span class="fleet-self-marker" title="This node">self</span>';
      if (ok) html += '<span class="fleet-node-status ok" title="reachable">✓</span>';
      else html += '<span class="fleet-node-status err" title="' + fedEsc(n.detail || n.error || 'unreachable') + '">✗ ' + fedEsc(n.error || 'error') + '</span>';
      if (n.stale) html += '<span class="fleet-stale-badge" title="' + fedEsc(fleetObsTitle('', n.observed_at)) + '">stale: ' + fedEsc(fleetRel(n.observed_at)) + '</span>';
      return '<div class="' + cls + '">' + html + '</div>';
    }).join('');
  }

  // ── Repo matrix ──
  function renderFleetMatrix(repos, nodes) {
    if (!$fleetMatrix) return;
    if (!repos || !repos.length) {
      $fleetMatrix.innerHTML = '<div class="fed-empty">No repositories mapped yet - open a repo to auto-map it, or add a mapping under Nodes &amp; peers.</div>';
      return;
    }
    const nodesById = {};
    (nodes || []).forEach((n) => { nodesById[n.node_id] = n; });
    $fleetMatrix.innerHTML = repos.map((repo) => {
      const nodeIds = Object.keys(repo.nodes || {});
      const cells = nodeIds.map((nid) =>
        renderFleetNodeCell(repo, nid, repo.nodes[nid], nodesById[nid] || { node_id: nid })
      ).join('');
      return '<div class="fleet-repo-card">'
        + '<div class="fleet-repo-head">'
        + (repo.pinned ? '<span class="fleet-pin" title="Pinned">📌</span>' : '')
        + '<span class="fleet-repo-title">' + fedEsc(repo.identity) + '</span>'
        + (repo.kind ? '<span class="fleet-repo-kind">' + fedEsc(repo.kind) + '</span>' : '')
        + '</div>'
        + '<div class="fleet-repo-nodes">' + cells + '</div>'
        + '</div>';
    }).join('');
  }

  function renderFleetNodeCell(repo, nodeId, entry, node) {
    const nodeName = (node && node.name) || (entry && entry.node_name) || fedShortId(nodeId);
    const stale = !!(entry && entry.stale);
    const staleBadge = stale
      ? '<span class="fleet-stale-badge" title="' + fedEsc(fleetObsTitle('', entry.observed_at)) + '">stale: ' + fedEsc(fleetRel(entry.observed_at)) + '</span>'
      : '';
    const head = '<div class="fleet-cell-head">'
      + '<span class="fleet-cell-node" title="' + fedEsc(nodeId) + '">' + fedEsc(nodeName) + '</span>'
      + (node && node.self ? '<span class="fleet-self-marker" title="This node">self</span>' : '')
      + staleBadge
      + '</div>';
    if (!entry || entry.ok === false) {
      const detail = (entry && (entry.detail || entry.error)) || 'inventory failed';
      return '<div class="fleet-cell is-err' + (stale ? ' is-stale' : '') + '">' + head
        + '<div class="fleet-dim"><span class="fleet-chip chip-red" title="' + fedEsc(detail) + '">✗ ' + fedEsc((entry && entry.error) || 'error') + '</span></div>'
        + '<div class="fleet-cell-detail">' + fedEsc(detail) + '</div>'
        + '</div>';
    }
    const body = renderFleetWorktreesDim(repo, nodeId, entry)
      + renderFleetBranchDim(entry)
      + renderFleetPrsDim(entry)
      + renderFleetDeployDim(entry)
      + renderFleetSessionsDim(entry);
    return '<div class="fleet-cell' + (stale ? ' is-stale' : '') + '">' + head + body + '</div>';
  }

  function fleetDim(label, chipsHtml, extraHtml) {
    return '<div class="fleet-dim"><span class="fleet-dim-label">' + label + '</span>'
      + '<div class="fleet-chip-row">' + chipsHtml + '</div>' + (extraHtml || '') + '</div>';
  }
  // A dimension the fast pass deliberately skipped. Never render it as "0",
  // which would be a claim we have not earned yet.
  function fleetPendingChip(what) {
    return '<span class="fleet-chip chip-muted is-pending" title="'
      + fedEsc(what + ' is still being fetched') + '">checking…</span>';
  }

  function renderFleetWorktreesDim(repo, nodeId, entry) {
    const wts = Array.isArray(entry.worktrees) ? entry.worktrees : [];
    const obs = fleetObsTitle('worktrees', entry.observed_at);
    let dirtyFiles = 0, unpublished = 0, cleanable = 0;
    wts.forEach((wt) => {
      if (wt.dirty) dirtyFiles += Number(wt.dirty_files || 0);
      if (wt.unpublished_commits) unpublished += Number(wt.unpublished_commits || 0);
      if (!wt.is_primary_clone && !wt.dirty && wt.merged_into_default === true && !wt.unpublished_commits) cleanable++;
    });
    let chips = '<span class="fleet-chip" title="' + fedEsc(obs) + '">' + wts.length + ' worktree' + (wts.length === 1 ? '' : 's') + '</span>';
    if (dirtyFiles) chips += '<span class="fleet-chip chip-red" title="' + fedEsc(obs) + '">' + dirtyFiles + ' dirty</span>';
    if (unpublished) chips += '<span class="fleet-chip chip-orange" title="' + fedEsc(obs) + '">' + unpublished + ' unpublished</span>';
    if (cleanable) chips += '<span class="fleet-chip chip-green" title="' + fedEsc(obs) + '">' + cleanable + ' cleanable</span>';
    let rows = '';
    const dirty = wts.filter((wt) => wt.dirty);
    if (dirty.length) {
      rows = '<div class="fleet-wt-rows">' + dirty.map((wt) =>
        '<div class="fleet-wt-row">'
        + '<span class="fleet-wt-name" title="' + fedEsc(wt.path || '') + '">' + fedEsc(fleetBase(wt.path)) + (wt.branch ? ' · ' + fedEsc(wt.branch) : '') + '</span>'
        + '<span class="fleet-wt-dirty">' + Number(wt.dirty_files || 0) + ' dirty</span>'
        + '<button type="button" class="upd-btn fleet-mini-btn" data-fleet-who="1"'
        + ' data-fleet-identity="' + fedEsc(repo.identity) + '" data-fleet-node="' + fedEsc(nodeId) + '"'
        + ' data-fleet-path="' + fedEsc(wt.path || '') + '">Who?</button>'
        + '</div>'
      ).join('') + '</div>';
    }
    return fleetDim('Worktrees', chips, rows);
  }

  function renderFleetBranchDim(entry) {
    const db = entry.default_branch || {};
    const obs = fleetObsTitle('default branch', entry.observed_at);
    const sha = db.sha ? String(db.sha).slice(0, 8) : '-';
    let chips = '<span class="fleet-chip" title="' + fedEsc(obs) + '">' + fedEsc(db.branch || '-') + ' @ <span class="fleet-sha">' + fedEsc(sha) + '</span></span>';
    const primary = (entry.worktrees || []).find((wt) => wt.is_primary_clone);
    if (primary && db.sha && primary.head_sha && primary.head_sha !== db.sha
        && primary.branch === db.branch && !primary.unpublished_commits) {
      chips += '<span class="fleet-chip chip-orange" title="local ' + fedEsc(String(primary.head_sha).slice(0, 8))
        + ' vs origin ' + fedEsc(sha) + '">behind origin</span>';
    }
    return fleetDim('Default branch', chips);
  }

  function renderFleetPrsDim(entry) {
    const prs = entry.prs || {};
    const obs = fleetObsTitle('PRs', prs.observed_at || entry.observed_at);
    let chips;
    if (prs.skipped === 'excluded' || entry.prs_skipped) {
      chips = fleetPendingChip('PR data');
    } else if (prs.error) {
      chips = '<span class="fleet-chip chip-warn" title="' + fedEsc(String(prs.error)) + '">PR data unavailable</span>';
    } else if (prs.skipped) {
      chips = '<span class="fleet-chip chip-muted" title="' + fedEsc(String(prs.skipped)) + '">no remote</span>';
    } else {
      const open = Array.isArray(prs.open) ? prs.open : [];
      let failing = 0, drafts = 0;
      open.forEach((p) => {
        if (Array.isArray(p.checks_failing) && p.checks_failing.length) failing++;
        if (p.draft) drafts++;
      });
      chips = '<span class="fleet-chip" title="' + fedEsc(obs) + '">' + open.length + ' open</span>';
      if (failing) chips += '<span class="fleet-chip chip-red" title="' + fedEsc(obs) + '">' + failing + ' failing</span>';
      if (drafts) chips += '<span class="fleet-chip chip-muted" title="' + fedEsc(obs) + '">' + drafts + ' draft' + (drafts === 1 ? '' : 's') + '</span>';
    }
    return fleetDim('PRs', chips);
  }

  function renderFleetDeployDim(entry) {
    const dep = entry.deployment || {};
    const obs = fleetObsTitle('deployment', dep.observed_at || entry.observed_at);
    let chip;
    if (dep.skipped === 'excluded') {
      chip = fleetPendingChip('Deployment state');
    } else if (dep.error) {
      chip = '<span class="fleet-chip chip-warn" title="' + fedEsc(String(dep.error)) + '">deploy error</span>';
    } else if (dep.skipped) {
      chip = '<span class="fleet-chip chip-muted" title="' + fedEsc(String(dep.skipped)) + '">no deploy</span>';
    } else if (dep.state) {
      const st = String(dep.state).toUpperCase();
      let c = 'chip-muted';
      if (st === 'READY') c = 'chip-green';
      else if (st === 'ERROR') c = 'chip-red';
      else if (st === 'BUILDING' || st === 'QUEUED' || st === 'INITIALIZING') c = 'chip-orange';
      const title = obs + (dep.commit_sha ? ' · ' + String(dep.commit_sha).slice(0, 8) : '');
      chip = '<span class="fleet-chip ' + c + '" title="' + fedEsc(title) + '">' + fedEsc(dep.state) + '</span>';
    } else {
      chip = '<span class="fleet-chip chip-muted" title="' + fedEsc(obs) + '">no data</span>';
    }
    return fleetDim('Deploy', chip);
  }

  function renderFleetSessionsDim(entry) {
    if (entry.sessions_skipped) return fleetDim('Sessions', fleetPendingChip('Session ownership'));
    const ss = Array.isArray(entry.sessions) ? entry.sessions : [];
    const obs = fleetObsTitle('sessions', entry.observed_at);
    const live = ss.filter((s) => s.is_live).length;
    let chips = '<span class="fleet-chip" title="' + fedEsc(obs) + '">' + ss.length + ' session' + (ss.length === 1 ? '' : 's') + (live ? ' · ' + live + ' live' : '') + '</span>';
    let badges = '';
    if (ss.length) {
      badges = '<div class="fleet-session-badges">' + ss.slice(0, 8).map((s) =>
        '<span class="fleet-session-badge' + (s.is_live ? ' is-live' : '') + '" title="' + fedEsc(s.ref || s.session_id || '') + '">'
        + fedEsc(s.display_name || fedShortId(s.session_id)) + '</span>'
      ).join('') + (ss.length > 8 ? '<span class="fleet-session-badge more">+' + (ss.length - 8) + '</span>' : '') + '</div>';
    }
    return fleetDim('Sessions', chips, badges);
  }

  function renderFleetView(data) {
    fleetInventory = data;
    renderFleetHealth((data && data.nodes) || []);
    renderFleetMatrix((data && data.repos) || [], (data && data.nodes) || []);
    if ($fleetRefreshTime) $fleetRefreshTime.textContent = 'Updated ' + fleetRel(data && data.observed_at);
  }

  function fleetRepoCount(data) {
    return ((data && data.repos) || []).length;
  }

  /* Two-phase load.
   *
   * Pass 1 asks for git facts only and paints. Pass 2 asks for everything and
   * repaints. Pass 2 is slow and network-bound (it can take minutes when
   * GitHub is having a bad day), but the matrix is already usable by then, so
   * it never blocks the page — only the progress line reflects it.
   */
  async function loadFleetInventory(fetchRemote) {
    if (fleetLoading) return;
    fleetLoading = true;
    fleetEnriching = false;
    fleetSetError('');
    if ($fleetRefreshBtn) $fleetRefreshBtn.disabled = true;
    if ($fleetRefreshSpinner) $fleetRefreshSpinner.hidden = false;
    fleetSetProgress('Scanning repos (git state)…', true);

    const q = '&fetch=' + (fetchRemote ? '1' : '0');
    let fastOk = false;
    try {
      const { status, data } = await fleetGet(
        '/api/fleet/inventory?prs=0&deploy=0&sessions=0' + q);
      if (status !== 200 || !data || data.ok === false) {
        fleetSetError('Could not load the fleet: ' + ((data && (data.detail || data.error)) || ('HTTP ' + status)));
      } else {
        fastOk = true;
        renderFleetView(data);
      }
    } catch (e) {
      fleetSetError('Could not load the fleet: ' + ((e && e.message) || 'unknown'));
    }

    if ($fleetRefreshSpinner) $fleetRefreshSpinner.hidden = true;
    if ($fleetRefreshBtn) $fleetRefreshBtn.disabled = false;
    fleetLoading = false;
    if (!fastOk) { fleetSetProgress('', false); return; }

    // Pass 2 — deliberately not awaited by the caller.
    fleetEnriching = true;
    const n = fleetRepoCount(fleetInventory);
    fleetSetProgress('Matrix ready. Fetching PRs, deployments and sessions for '
      + n + ' repo' + (n === 1 ? '' : 's') + ' (this can take a minute)…', true);
    try {
      const { status, data } = await fleetGet('/api/fleet/inventory?' + q.slice(1));
      if (status === 200 && data && data.ok !== false) {
        renderFleetView(data);
        fleetSetProgress('', false);
      } else {
        fleetSetProgress('Git state shown. PRs / deployments / sessions unavailable: '
          + ((data && (data.detail || data.error)) || ('HTTP ' + status)), false);
      }
    } catch (e) {
      fleetSetProgress('Git state shown. PRs / deployments / sessions unavailable: '
        + ((e && e.message) || 'unknown'), false);
    }
    fleetEnriching = false;
  }

  // ── "Who?" attribution popover ──
  // A floating popover appended to <body>, positioned under the clicked
  // button. Lists candidate sessions with confidence + evidence kinds, each
  // offering a "Ping to commit".
  let $fleetAttrPop = null;
  function fleetEnsureAttrPopover() {
    if ($fleetAttrPop) return $fleetAttrPop;
    const el = document.createElement('div');
    el.className = 'fleet-attr-popover';
    el.hidden = true;
    document.body.appendChild(el);
    $fleetAttrPop = el;
    document.addEventListener('click', (e) => {
      if (!$fleetAttrPop || $fleetAttrPop.hidden) return;
      if ($fleetAttrPop.contains(e.target)) return;
      if (e.target.closest('[data-fleet-who]')) return;
      fleetHideAttrPopover();
    });
    return el;
  }
  function fleetHideAttrPopover() {
    if ($fleetAttrPop) { $fleetAttrPop.hidden = true; $fleetAttrPop.innerHTML = ''; }
  }
  function fleetPositionPopover(btn) {
    const el = $fleetAttrPop;
    if (!el || !btn) return;
    const r = btn.getBoundingClientRect();
    el.style.top = (r.bottom + 6) + 'px';
    el.style.left = r.left + 'px';
    const rect = el.getBoundingClientRect();
    if (rect.right > window.innerWidth - 8) {
      el.style.left = Math.max(8, window.innerWidth - 8 - rect.width) + 'px';
    }
    if (rect.bottom > window.innerHeight - 8) {
      el.style.top = Math.max(8, r.top - rect.height - 6) + 'px';
    }
  }
  async function fleetShowWho(btn) {
    const el = fleetEnsureAttrPopover();
    const identity = btn.getAttribute('data-fleet-identity') || '';
    const nodeId = btn.getAttribute('data-fleet-node') || '';
    const path = btn.getAttribute('data-fleet-path') || '';
    el.hidden = false;
    el.innerHTML = '<div class="fleet-attr-loading">Attributing…</div>';
    fleetPositionPopover(btn);
    const body = { path };
    if (identity) body.identity = identity;
    if (nodeId) body.node_id = nodeId;
    let res;
    try {
      res = await fleetPost('/api/fleet/attribute', body);
    } catch (e) {
      el.innerHTML = '<div class="fleet-attr-empty">Attribution failed: ' + fedEsc((e && e.message) || 'error') + '</div>';
      fleetPositionPopover(btn);
      return;
    }
    const data = res.data || {};
    if (res.status !== 200 || data.ok === false) {
      el.innerHTML = '<div class="fleet-attr-empty">' + fedEsc(data.detail || data.error || 'attribution failed') + '</div>';
      fleetPositionPopover(btn);
      return;
    }
    const cands = Array.isArray(data.candidates) ? data.candidates : [];
    let html = '<div class="fleet-attr-head" title="' + fedEsc(path) + '">Who touched ' + fedEsc(fleetBase(path)) + '?</div>';
    if (!cands.length) {
      html += '<div class="fleet-attr-empty">unknown - no evidence</div>';
    } else {
      html += cands.map((c) => {
        const evk = Array.isArray(c.evidence) ? c.evidence.map((e) => e.kind).filter(Boolean) : [];
        const uniqEv = evk.filter((v, i) => evk.indexOf(v) === i);
        return '<div class="fleet-attr-cand">'
          + '<div class="fleet-attr-cand-head">'
          + '<span class="fleet-attr-name">' + fedEsc(c.display_name || fedShortId(c.session_id)) + '</span>'
          + '<span class="fleet-attr-conf conf-' + fedEsc(c.confidence || 'low') + '">' + fedEsc(c.confidence || 'low') + '</span>'
          + (c.is_live ? '<span class="fleet-attr-live">live</span>' : '')
          + '</div>'
          + '<div class="fleet-attr-ev" title="' + fedEsc(uniqEv.join(', ')) + '">' + fedEsc(uniqEv.join(', ') || 'correlated') + '</div>'
          + '<button type="button" class="upd-btn fleet-mini-btn" data-fleet-ping="1"'
          + ' data-fleet-ref="' + fedEsc(c.ref || c.session_id || '') + '"'
          + ' data-fleet-target="' + fedEsc(path) + '">Ping to commit</button>'
          + '</div>';
      }).join('');
    }
    el.innerHTML = html;
    fleetPositionPopover(btn);
  }
  async function fleetPing(btn) {
    const ref = btn.getAttribute('data-fleet-ref') || '';
    const target = btn.getAttribute('data-fleet-target') || '';
    if (!ref) return;
    btn.disabled = true;
    btn.textContent = 'Pinging…';
    try {
      const { status, data } = await fleetPost('/api/fleet/ping-session', { ref, kind: 'commit', target });
      if (status === 200 && data.ok !== false) {
        showOpToast('Pinged the session to commit', 'ok');
        btn.textContent = 'Pinged ✓';
      } else {
        showOpToast('Ping failed: ' + ((data && (data.detail || data.error)) || 'error'), 'error');
        btn.disabled = false;
        btn.textContent = 'Ping to commit';
      }
    } catch (e) {
      showOpToast('Ping failed: ' + ((e && e.message) || 'error'), 'error');
      btn.disabled = false;
      btn.textContent = 'Ping to commit';
    }
  }

  // ── Resolve-all: plan review + job progress ──
  const $fleetPlanModal = document.getElementById('fleetPlanModal');
  const $fleetPlanBackdrop = document.getElementById('fleetPlanBackdrop');
  const $fleetPlanCancel = document.getElementById('fleetPlanCancel');
  const $fleetPlanConfirm = document.getElementById('fleetPlanConfirm');
  const $fleetPlanBody = document.getElementById('fleetPlanBody');
  const $fleetPlanError = document.getElementById('fleetPlanError');
  const $fleetPlanSubtitle = document.getElementById('fleetPlanSubtitle');
  let fleetPlan = null;
  let fleetJobPlanId = null;
  let fleetJobPollTimer = null;

  function fleetPlanSetError(msg) {
    if (!$fleetPlanError) return;
    $fleetPlanError.textContent = msg || '';
    $fleetPlanError.classList.toggle('visible', !!msg);
  }
  function fleetPlanStopPolling() {
    if (fleetJobPollTimer) { clearTimeout(fleetJobPollTimer); fleetJobPollTimer = null; }
  }
  function fleetPlanClose() {
    fleetPlanStopPolling();
    if ($fleetPlanModal) $fleetPlanModal.classList.remove('open');
  }

  function fleetEvidenceSummary(ev) {
    if (!ev || typeof ev !== 'object') return '';
    const parts = [];
    if (ev.dirty_files != null) parts.push(ev.dirty_files + ' dirty');
    if (ev.unpublished_commits != null) parts.push(ev.unpublished_commits + ' unpublished');
    if (ev.head_sha) parts.push('head ' + String(ev.head_sha).slice(0, 8));
    if (ev.origin_head) parts.push('origin ' + String(ev.origin_head).slice(0, 8));
    if (ev.local_head) parts.push('local ' + String(ev.local_head).slice(0, 8));
    if (ev.merged_into_default === true) parts.push('merged into default');
    if (ev.attribution) parts.push(String(ev.attribution));
    if (ev.pr && ev.pr.number) parts.push('PR #' + ev.pr.number);
    if (Array.isArray(ev.candidate_sessions) && ev.candidate_sessions.length) {
      const names = ev.candidate_sessions.map((s) => s.display_name || fedShortId(s.session_id)).filter(Boolean).slice(0, 3);
      if (names.length) parts.push('candidates: ' + names.join(', '));
    }
    if (ev.deployment && ev.deployment.state) parts.push('deploy ' + ev.deployment.state);
    if (Array.isArray(ev.failing) && ev.failing.length) parts.push('failing: ' + ev.failing.slice(0, 3).join(', '));
    if (!parts.length) return '';
    return fedEsc(parts.join(' · '));
  }

  function renderFleetPlanAction(a) {
    const status = a.status || ((a.blockers && a.blockers.length) ? 'blocked' : 'proposed');
    const selectable = status === 'proposed';
    let cls = 'fleet-action';
    if (a.destructive) cls += ' is-destructive';
    if (!selectable) cls += ' is-locked';
    let html = '<label class="' + cls + '" data-fleet-action-id="' + fedEsc(a.id) + '">';
    html += '<input type="checkbox" class="fleet-action-check" ' + (selectable ? 'checked' : 'disabled')
      + ' data-fleet-action-check="' + fedEsc(a.id) + '">';
    html += '<div class="fleet-action-main">';
    html += '<div class="fleet-action-top">'
      + '<span class="fleet-kind-badge kind-' + fedEsc(a.kind) + (a.destructive ? ' is-destructive' : '') + '">' + fedEsc(a.kind) + '</span>'
      + (a.destructive ? '<span class="fleet-destructive-tag" title="Destructive action">destructive</span>' : '')
      + (status !== 'proposed' ? '<span class="fleet-status-tag status-' + fedEsc(status) + '">' + fedEsc(status) + '</span>' : '')
      + (a.target ? '<span class="fleet-action-target" title="' + fedEsc(a.target) + '">' + fedEsc(a.target) + '</span>' : '')
      + '</div>';
    if (a.reason) html += '<div class="fleet-action-reason">' + fedEsc(a.reason) + '</div>';
    if (a.command_intent) html += '<div class="fleet-action-cmd' + (a.destructive ? ' is-destructive' : '') + '"><code>' + fedEsc(a.command_intent) + '</code></div>';
    const ev = fleetEvidenceSummary(a.evidence);
    if (ev) html += '<div class="fleet-action-ev">' + ev + '</div>';
    const blockers = Array.isArray(a.blockers) ? a.blockers : [];
    if (blockers.length) {
      html += '<div class="fleet-action-blockers">' + blockers.map((b) =>
        '<span class="fleet-blocker" title="' + fedEsc(b.detail || '') + '"><span class="fleet-blocker-code">'
        + fedEsc(b.code || 'blocked') + '</span>' + (b.detail ? ' ' + fedEsc(b.detail) : '') + '</span>'
      ).join('') + '</div>';
    } else if (status === 'manual') {
      html += '<div class="fleet-action-blockers"><span class="fleet-blocker"><span class="fleet-blocker-code">manual</span> needs a human - not auto-executable</span></div>';
    }
    html += '</div></label>';
    return html;
  }

  function renderFleetPlan(plan) {
    if (!$fleetPlanBody) return;
    const actions = Array.isArray(plan.actions) ? plan.actions : [];
    if (!actions.length) {
      $fleetPlanBody.innerHTML = '<div class="fed-empty">Nothing to resolve - every repo is clean and published.</div>';
      if ($fleetPlanConfirm) $fleetPlanConfirm.hidden = true;
      return;
    }
    const byRepo = {};
    actions.forEach((a) => {
      const k = a.repo_identity || '(repo)';
      (byRepo[k] = byRepo[k] || []).push(a);
    });
    let html = '';
    Object.keys(byRepo).sort().forEach((repoKey) => {
      html += '<div class="fleet-plan-repo"><div class="fleet-plan-repo-title">' + fedEsc(repoKey) + '</div>';
      const byNode = {};
      byRepo[repoKey].forEach((a) => {
        const nk = a.node_name || fedShortId(a.node_id) || 'this node';
        (byNode[nk] = byNode[nk] || []).push(a);
      });
      Object.keys(byNode).forEach((nodeKey) => {
        html += '<div class="fleet-plan-node"><div class="fleet-plan-node-title">' + fedEsc(nodeKey) + '</div>';
        byNode[nodeKey].forEach((a) => { html += renderFleetPlanAction(a); });
        html += '</div>';
      });
      html += '</div>';
    });
    $fleetPlanBody.innerHTML = html;
    fleetUpdatePlanConfirm();
  }

  function fleetUpdatePlanConfirm() {
    if (!$fleetPlanConfirm || !$fleetPlanBody) return;
    const n = $fleetPlanBody.querySelectorAll('.fleet-action-check:checked:not(:disabled)').length;
    $fleetPlanConfirm.textContent = n ? ('Confirm ' + n + ' action' + (n === 1 ? '' : 's')) : 'Confirm';
    $fleetPlanConfirm.disabled = n === 0;
  }

  async function openFleetPlan() {
    if (!$fleetPlanModal) return;
    fleetPlanStopPolling();
    fleetPlanSetError('');
    fleetJobPlanId = null;
    if ($fleetPlanSubtitle) {
      $fleetPlanSubtitle.hidden = false;
      $fleetPlanSubtitle.textContent = 'Review the proposed actions. Destructive steps are outlined in red. Nothing runs until you confirm.';
    }
    if ($fleetPlanBody) $fleetPlanBody.innerHTML = '<div class="fed-empty">Building plan…</div>';
    if ($fleetPlanConfirm) { $fleetPlanConfirm.hidden = false; $fleetPlanConfirm.disabled = true; $fleetPlanConfirm.textContent = 'Confirm'; }
    if ($fleetPlanCancel) $fleetPlanCancel.textContent = 'Cancel';
    $fleetPlanModal.classList.add('open');
    try {
      const { status, data } = await fleetPost('/api/fleet/plan', {});
      if (status !== 200 || !data || data.ok === false || !data.plan) {
        fleetPlanSetError('Could not build a plan: ' + ((data && (data.detail || data.error)) || ('HTTP ' + status)));
        if ($fleetPlanBody) $fleetPlanBody.innerHTML = '';
        if ($fleetPlanConfirm) $fleetPlanConfirm.hidden = true;
        return;
      }
      fleetPlan = data.plan;
      renderFleetPlan(fleetPlan);
    } catch (e) {
      fleetPlanSetError('Could not build a plan: ' + ((e && e.message) || 'unknown'));
      if ($fleetPlanBody) $fleetPlanBody.innerHTML = '';
      if ($fleetPlanConfirm) $fleetPlanConfirm.hidden = true;
    }
  }

  async function fleetConfirmPlan() {
    if (!fleetPlan || !$fleetPlanBody) return;
    const ids = Array.from($fleetPlanBody.querySelectorAll('.fleet-action-check:checked:not(:disabled)'))
      .map((c) => c.getAttribute('data-fleet-action-check'));
    if (!ids.length) { fleetPlanSetError('Select at least one action.'); return; }
    fleetPlanSetError('');
    if ($fleetPlanConfirm) { $fleetPlanConfirm.disabled = true; $fleetPlanConfirm.textContent = 'Starting…'; }
    try {
      const { status, data } = await fleetPost('/api/fleet/execute', { plan_id: fleetPlan.plan_id, selected: ids });
      if (status !== 200 || data.ok === false) {
        fleetPlanSetError('Could not start: ' + ((data && (data.detail || data.error)) || ('HTTP ' + status)));
        if ($fleetPlanConfirm) { $fleetPlanConfirm.disabled = false; fleetUpdatePlanConfirm(); }
        return;
      }
      fleetJobPlanId = fleetPlan.plan_id;
      if ($fleetPlanSubtitle) $fleetPlanSubtitle.textContent = 'Running ' + data.selected + ' action' + (data.selected === 1 ? '' : 's') + '…';
      if ($fleetPlanConfirm) $fleetPlanConfirm.hidden = true;
      if ($fleetPlanCancel) $fleetPlanCancel.textContent = 'Close';
      fleetPollJob();
    } catch (e) {
      fleetPlanSetError('Could not start: ' + ((e && e.message) || 'unknown'));
      if ($fleetPlanConfirm) { $fleetPlanConfirm.disabled = false; fleetUpdatePlanConfirm(); }
    }
  }

  function renderFleetJob(job) {
    if (!$fleetPlanBody) return;
    const actions = Array.isArray(job.actions) ? job.actions : [];
    const active = actions.filter((a) => a.status && a.status !== 'skipped' && a.status !== 'proposed');
    let html = '<div class="fleet-job-status">Job ' + fedEsc(job.status || '') + '</div>';
    html += '<div class="fleet-job-actions">' + active.map((a) => {
      const st = a.status || '';
      let icon = '', cls = '';
      if (st === 'running') { icon = '<span class="fleet-spinner"></span>'; cls = 'is-running'; }
      else if (st === 'done') { icon = '✓'; cls = 'is-done'; }
      else if (st === 'failed') { icon = '✗'; cls = 'is-failed'; }
      else if (st === 'stopped_stale') { icon = '⚠'; cls = 'is-stale'; }
      else { icon = '…'; cls = 'is-pending'; }
      const detail = a.result && (a.result.detail || a.result.error);
      return '<div class="fleet-job-action ' + cls + '">'
        + '<span class="fleet-job-icon">' + icon + '</span>'
        + '<span class="fleet-job-kind">' + fedEsc(a.kind) + '</span>'
        + '<span class="fleet-job-target" title="' + fedEsc(a.target || '') + '">' + fedEsc(a.target || '') + '</span>'
        + (detail ? '<span class="fleet-job-detail">' + fedEsc(detail) + '</span>' : '')
        + '</div>';
    }).join('') + '</div>';
    const log = Array.isArray(job.log) ? job.log : [];
    if (log.length) {
      html += '<div class="fleet-job-log">' + log.slice(-60).map((l) =>
        '<div class="fleet-log-line level-' + fedEsc(l.level || 'info') + '">'
        + '<span class="fleet-log-t">' + fedEsc(fleetRel(l.t)) + '</span> ' + fedEsc(l.text || '') + '</div>'
      ).join('') + '</div>';
    }
    $fleetPlanBody.innerHTML = html;
  }

  async function fleetPollJob() {
    if (!fleetJobPlanId) return;
    let done = false;
    try {
      const { status, data } = await fleetGet('/api/fleet/job?id=' + encodeURIComponent(fleetJobPlanId));
      if (status === 200 && data.ok !== false && data.job) {
        renderFleetJob(data.job);
        done = data.job.status === 'finished';
      }
    } catch (_) { /* transient - keep polling */ }
    if (done) {
      fleetPlanStopPolling();
      loadFleetInventory(false);
      return;
    }
    fleetJobPollTimer = setTimeout(fleetPollJob, 1500);
  }

  // ── Event wiring ──
  if ($fleetRefreshBtn) $fleetRefreshBtn.addEventListener('click', () => loadFleetInventory(true));
  if ($fleetResolveBtn) $fleetResolveBtn.addEventListener('click', openFleetPlan);
  if ($fleetMatrix) $fleetMatrix.addEventListener('click', (e) => {
    const who = e.target.closest('[data-fleet-who]');
    if (who) { e.preventDefault(); e.stopPropagation(); fleetShowWho(who); }
  });
  document.addEventListener('click', (e) => {
    const ping = e.target.closest('[data-fleet-ping]');
    if (ping) { e.preventDefault(); fleetPing(ping); }
  });
  if ($fleetPlanBackdrop) $fleetPlanBackdrop.addEventListener('click', fleetPlanClose);
  if ($fleetPlanCancel) $fleetPlanCancel.addEventListener('click', fleetPlanClose);
  if ($fleetPlanConfirm) $fleetPlanConfirm.addEventListener('click', fleetConfirmPlan);
  if ($fleetPlanBody) $fleetPlanBody.addEventListener('change', (e) => {
    if (e.target.closest('.fleet-action-check')) fleetUpdatePlanConfirm();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if ($fleetPlanModal && $fleetPlanModal.classList.contains('open')) fleetPlanClose();
  });

  // Match the dashboard's theme choice so the page does not flash a
  // different scheme than the window it was opened from.
  try {
    const theme = localStorage.getItem('ccc-theme');
    if (theme) document.documentElement.setAttribute('data-theme', theme);
    const font = localStorage.getItem('ccc-font');
    if (font) document.documentElement.setAttribute('data-font', font);
  } catch (_) {}

  loadFleetInventory(false);
})();
