/* Leftover Mode (M06): when /api/headroom says at least 20% of a plan will
 * expire unused within 24h, offer 3 to 5 real tasks (WatchTower, GitHub
 * issues, TODO/FIXME notes; see ccc_server/leftover.py). Every task needs one
 * click to start and goes through the normal /api/sessions/spawn API, except
 * WatchTower tickets: that click approves the ticket back to WatchTower
 * (POST /api/leftover/approve -> `wt run`), whose dispatcher claims it and
 * picks the engine. Nothing ever starts by itself.
 *
 * Gates: the floating offer card is pop-up id 'leftover-offer' and the daily
 * reminder is 'leftover-notification' (static/popups.js). Until they are
 * approved, only the Settings > Leftover Mode panel works (the user opens it
 * on purpose) and this file does not poll /api/headroom in the background.
 */
(function () {
  'use strict';

  var accounts = [], selectedId = '', opened = false, headroomState = 'loading', pollBusy = false;
  var busy = {}, outcomes = {};
  // Panel: the folder chosen in Settings. Offer: the open conversation's repo
  // (or the last folder picked in Settings).
  var panel = { repo: '', canonical: '', tasks: [], generation: 0, signature: '' };
  var offer = { repo: '', canonical: '', tasks: [], generation: 0, signature: '', fetchedAt: 0, loading: false, error: '' };
  var labels = { claude: 'Claude', codex: 'Codex', kimi: 'Kimi' };
  var OFFER_REFRESH_MS = 60000;

  function el(id) { return document.getElementById(id); }
  function get(key) { try { return localStorage.getItem(key); } catch (_) { return null; } }
  function put(key, value) { try { localStorage.setItem(key, String(value)); } catch (_) {} }
  function esc(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function enabled() { return get('ccc-leftover-enabled') !== '0'; }
  function setEnabled(on) {
    put('ccc-leftover-enabled', on ? '1' : '0');
    syncToggle();
    if (!on && el('cccLeftoverOffer')) el('cccLeftoverOffer').hidden = true;
  }
  function isCandidate(row) {
    return !!(row && row.available === true && row.stale === false && row.unlimited !== true && typeof row.projected_expiring_pct === 'number' && Number.isFinite(row.projected_expiring_pct) && row.projected_expiring_pct >= 20 && row.projected_expiring_pct <= 100 && typeof row.hours_to_reset === 'number' && Number.isFinite(row.hours_to_reset) && row.hours_to_reset >= 0 && row.hours_to_reset <= 24 && ['claude', 'codex', 'kimi'].indexOf(row.engine) !== -1);
  }
  function resetIsCurrent(row) { return typeof row.resets_at === 'number' && Number.isFinite(row.resets_at) && row.resets_at * 1000 > Date.now(); }
  function hasDollars(row) { return typeof row.expiring_usd_estimate === 'number' && Number.isFinite(row.expiring_usd_estimate) && row.expiring_usd_estimate >= 0; }
  function resetText(row) {
    return row.hours_to_reset >= 1 ? Math.round(row.hours_to_reset) + 'h' : Math.max(1, Math.ceil(row.hours_to_reset * 60)) + 'm';
  }
  function engineName(row) { return labels[row.engine] || row.label || 'your plan'; }
  function summary(row) {
    return hasDollars(row)
      ? 'You have about $' + row.expiring_usd_estimate.toFixed(2) + ' of ' + engineName(row) + ' left that resets in ' + resetText(row) + '. Put it to work?'
      : 'About ' + row.projected_expiring_pct.toFixed(0) + '% of your ' + engineName(row) + ' plan may go unused before it resets in ' + resetText(row) + '. Put it to work?';
  }
  function candidates() { return accounts.filter(isCandidate); }
  function selected() {
    var list = candidates();
    return list.find(function (row) { return row.id === selectedId; }) || list[0];
  }
  function spawnBody(task, row) {
    return { prompt: task.prompt, name: 'Leftover: ' + task.title.slice(0, 100), engine: row.engine,
      cwd: task.repo_path, repo_path: task.repo_path, task_key: 'leftover:' + task.id + ':' + row.engine, dedupe: true, worktree: true };
  }
  function notifyCandidate(row) {
    if (!enabled() || !isCandidate(row) || !window.cccPopups || !window.cccPopups.allowed('leftover-notification') || !window.cccPopups.notifyAllowed('leftover')) return;
    // Skip while the same offer is on screen, or after "Not now" for this reset.
    if (offerOnScreen() || snoozed(row)) return;
    var notify = window.cccNotify;
    if (!notify || typeof notify.show !== 'function' || (notify.enabled && !notify.enabled())) return;
    var now = Date.now(), last = Number(get('ccc-leftover-notified-at')) || 0;
    if (now - last < 86400000) return;
    if (notify.show({ id: 'leftover-' + Math.floor(now / 86400000), kind: 'leftover', title: 'Put your plan to work', body: summary(row), url: '/?ccc_settings=leftover' }) === true) put('ccc-leftover-notified-at', now);
  }
  function request(url) {
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 10000);
    return fetch(url, { signal: controller.signal, cache: 'no-store' }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) { return { status: res.status, data: data || {} }; });
    }).finally(function () { clearTimeout(timeout); });
  }
  // The server answers with the canonical folder (~ expanded, symlinks and
  // trailing slashes resolved), so match proposals against that, not the typed text.
  function validTasks(data) {
    var folder = data.repo_path;
    return (data.proposals || []).filter(function (item) {
      return item && folder && item.repo_path === folder && item.id && item.title && item.prompt;
    }).slice(0, 5);
  }
  function syncToggle() {
    var toggle = el('loEnabled');
    if (!toggle) return;
    toggle.classList.toggle('is-on', enabled());
    toggle.setAttribute('aria-checked', String(enabled()));
  }
  function savedKey(task, row) { return 'ccc-leftover-started:' + task.repo_path + ':' + task.id + ':' + row.engine; }
  function folderName(path) {
    var parts = String(path || '').split('/').filter(Boolean);
    return parts[parts.length - 1] || path;
  }
  function sourceMarkup(task) {
    return '<span class="lo-task-source">' + esc(task.source_label)
      + (task.reference ? '<span class="lo-task-ref">' + esc(task.reference) + '</span>' : '')
      + (task.task_repo && task.task_repo !== task.repo_path ? '<span class="lo-task-ref">' + esc(folderName(task.task_repo)) + '</span>' : '') + '</span>';
  }
  function startButton(task, row, compact) {
    var done = row && get(savedKey(task, row)) === '1';
    var running = busy[task.id];
    var queued = task.dispatch === 'watchtower';
    var label = queued ? (running ? 'Queuing…' : done ? 'Queued' : compact ? 'Queue' : 'Queue in WatchTower')
      : running ? 'Starting…' : done ? 'Started' : compact ? 'Start' : 'Start with ' + (row ? engineName(row) : 'your plan');
    return '<button type="button" class="lo-start" data-lo-start="' + esc(task.id) + '"'
      + (compact ? ' aria-label="Start: ' + esc(task.title) + '"' : '')
      + ((!row || running || done) ? ' disabled' : '') + '>' + esc(label) + '</button>';
  }
  function outcomeMarkup(task) {
    var outcome = outcomes[task.id];
    if (!outcome) return '';
    return '<div class="lo-outcome' + (outcome.error ? ' is-error' : '') + '" role="status">' + esc(outcome.text)
      + (outcome.session ? ' <button type="button" class="lo-link" data-lo-open="' + esc(outcome.session) + '">Open session</button>' : '') + '</div>';
  }
  function renderTasks() {
    var host = el('loTasks'), row = selected();
    if (!host) return;
    var markup = panel.tasks.map(function (task) {
      return '<article class="lo-task"><div class="lo-task-copy">' + sourceMarkup(task)
        + '<h3>' + esc(task.title) + '</h3><details><summary>Review what the agent will get</summary><pre>' + esc(task.prompt) + '</pre></details></div>'
        + '<div class="lo-task-actions">' + startButton(task, row, false) + outcomeMarkup(task) + '</div></article>';
    }).join('');
    if (markup !== panel.signature) { host.innerHTML = markup; panel.signature = markup; }
  }
  function renderPanel() {
    var headline = el('loHeadline');
    if (!headline) return;
    var row = selected(), list = candidates(), picker = el('loAccount');
    headline.textContent = row ? summary(row) : headroomState === 'missing' ? 'Usage estimates are not available in this build yet.'
      : headroomState === 'error' ? 'Could not read your usage. CCC will try again shortly.'
      : headroomState === 'loading' ? 'Checking what is left on your plan…' : 'Nothing is about to expire. Check back closer to your plan reset.';
    el('loEstimate').textContent = row && hasDollars(row) ? 'Estimated work at API prices, not cash or a refund.' : '';
    var options = list.map(function (item) { return '<option value="' + esc(item.id) + '">' + esc(item.label || labels[item.engine]) + '</option>'; }).join('');
    if (picker.innerHTML !== options) picker.innerHTML = options;
    picker.hidden = list.length < 2;
    if (row) { selectedId = row.id; picker.value = row.id; }
    el('loPlan').textContent = row ? 'Uses your ' + engineName(row) + ' plan in a separate worktree. No task starts by itself.' : 'Tasks become available when a fresh estimate says your plan may go unused.';
    renderTasks();
    syncToggle();
  }

  // ── floating offer card (pop-up id 'leftover-offer') ──────────────────

  function activeRepo() {
    return (window.activeConvRepoPath && window.activeConvRepoPath()) || (window.popoutRepoPath && window.popoutRepoPath()) || '';
  }
  function offerRepo() { return activeRepo() || get('ccc-leftover-repo') || ''; }
  function settingsOpen() { var modal = el('settingsModal'); return !!(modal && !modal.hidden); }
  // "Not now" lasts until this plan resets. Small drift in resets_at between
  // updates (under an hour) still counts as the same reset.
  function snoozed(row) {
    var saved = String(get('ccc-leftover-snooze') || ''), cut = saved.lastIndexOf(':');
    return cut > 0 && saved.slice(0, cut) === row.id && Math.abs(Number(saved.slice(cut + 1)) - row.resets_at) < 3600;
  }
  function offerShouldShow(row) {
    return !!(enabled() && row && window.cccPopups && window.cccPopups.allowed('leftover-offer') && !snoozed(row) && !settingsOpen());
  }
  function buildOffer() {
    var card = document.createElement('aside');
    card.id = 'cccLeftoverOffer'; card.className = 'lo-offer'; card.hidden = true;
    card.setAttribute('role', 'region'); card.setAttribute('aria-labelledby', 'loOfferTitle');
    card.innerHTML = '<div class="lo-offer-head"><span class="lo-offer-eyebrow"><span class="lo-offer-dot" aria-hidden="true"></span>Leftover Mode</span>'
      + '<button type="button" class="lo-offer-close" data-lo-dismiss aria-label="Not now">×</button></div>'
      + '<p class="lo-offer-title" id="loOfferTitle"></p>'
      + '<p class="lo-offer-meta" id="loOfferMeta"></p>'
      + '<div class="lo-offer-tasks" id="loOfferTasks" aria-live="polite"></div>'
      + '<div class="lo-offer-foot"><span>Nothing starts until you click.</span><button type="button" class="lo-link" data-lo-choose>More options</button></div>';
    document.body.appendChild(card);
    return card;
  }
  function renderOfferTasks(row) {
    var host = el('loOfferTasks'), meta = el('loOfferMeta');
    if (!host) return;
    var markup;
    if (!offer.repo) {
      meta.textContent = '';
      markup = '<div class="lo-offer-empty">Open a conversation or pick a folder to see tasks for it.<button type="button" class="lo-start" data-lo-choose>Pick a folder</button></div>';
    } else {
      meta.textContent = 'Tasks in ' + folderName(offer.repo) + ' · uses your ' + engineName(row) + ' plan';
      if (offer.tasks.length) {
        markup = '<ul class="lo-offer-list">' + offer.tasks.map(function (task) {
          return '<li class="lo-offer-task"><div class="lo-offer-copy">' + sourceMarkup(task)
            + '<span class="lo-offer-name" title="' + esc(task.title) + '">' + esc(task.title) + '</span>' + outcomeMarkup(task) + '</div>'
            + startButton(task, row, true) + '</li>';
        }).join('') + '</ul>';
      } else if (offer.loading) {
        markup = '<div class="lo-offer-empty" role="status">Finding tasks in ' + esc(folderName(offer.repo)) + '…</div>';
      } else {
        markup = '<div class="lo-offer-empty">' + esc(offer.error || 'No ready tasks here yet. Add a GitHub issue or a TODO note, or pick another folder.')
          + '<button type="button" class="lo-start" data-lo-choose>Pick a folder</button></div>';
      }
    }
    if (markup !== offer.signature) { host.innerHTML = markup; offer.signature = markup; }
  }
  function loadOfferTasks(folder, version, deadline) {
    if (version !== offer.generation) return;
    offer.loading = true;
    request('/api/leftover/proposals?repo_path=' + encodeURIComponent(folder)).then(function (res) {
      if (version !== offer.generation) return;
      if (res.status !== 200 || res.data.ok !== true) {
        offer.tasks = []; offer.loading = false;
        offer.error = res.data.message || res.data.error || 'Could not find tasks in this folder.';
      } else {
        offer.tasks = validTasks(res.data); offer.canonical = res.data.repo_path; offer.error = '';
        offer.loading = !!res.data.loading && Date.now() < deadline;
        if (offer.loading) setTimeout(function () { loadOfferTasks(folder, version, deadline); }, 1000);
      }
      renderOffer();
    }).catch(function () {
      if (version !== offer.generation) return;
      offer.loading = false; offer.error = 'Could not reach CCC. Tasks will retry shortly.'; renderOffer();
    });
  }
  // Same words as summary(), with the amount picked out.
  function setOfferTitle(row) {
    var title = el('loOfferTitle'), text = summary(row);
    var amount = hasDollars(row) ? '$' + row.expiring_usd_estimate.toFixed(2) : row.projected_expiring_pct.toFixed(0) + '%';
    var at = text.indexOf(amount);
    if (title.getAttribute('data-text') === text) return;
    title.setAttribute('data-text', text);
    title.textContent = '';
    if (at < 0) { title.textContent = text; return; }
    var strong = document.createElement('span');
    strong.className = 'lo-amount'; strong.textContent = amount;
    title.append(text.slice(0, at), strong, text.slice(at + amount.length));
  }
  function offerOnScreen() {
    var card = el('cccLeftoverOffer');
    return !!(card && !card.hidden && !document.hidden && (!document.hasFocus || document.hasFocus()));
  }
  function renderOffer() {
    var row = selected(), card = el('cccLeftoverOffer');
    if (!offerShouldShow(row)) {
      if (card) card.hidden = true;
      return;
    }
    if (!card) card = buildOffer();
    var folder = offerRepo();
    if (folder !== offer.repo || (folder && !offer.loading && Date.now() - offer.fetchedAt > OFFER_REFRESH_MS)) {
      if (folder !== offer.repo) { offer.tasks = []; offer.error = ''; }
      offer.repo = folder; offer.generation += 1; offer.fetchedAt = Date.now();
      if (folder) loadOfferTasks(folder, offer.generation, Date.now() + 25000);
    }
    setOfferTitle(row);
    renderOfferTasks(row);
    card.hidden = false;
  }

  // ── settings panel ────────────────────────────────────────────────────

  function panelVisible() {
    var section = el('settingsSection-leftover');
    return !!(settingsOpen() && section && section.classList.contains('is-active-section'));
  }
  function autoAllowed() {
    return !!(enabled() && window.cccPopups && (window.cccPopups.allowed('leftover-offer') || window.cccPopups.allowed('leftover-notification')));
  }
  function poll() {
    if (document.hidden || pollBusy || (!panelVisible() && !autoAllowed())) return Promise.resolve();
    pollBusy = true;
    return request('/api/headroom').then(function (res) {
      var valid = res.status === 200 && res.data.ok === true && Array.isArray(res.data.rows);
      accounts = valid ? res.data.rows : [];
      headroomState = res.status === 404 ? 'missing' : valid ? 'ready' : 'error';
      renderPanel(); renderOffer();
      var row = selected();
      if (row) notifyCandidate(row);
      if (panelVisible() && panel.repo) loadTasks(panel.repo, panel.generation, Date.now() + 25000);
    }).catch(function () {
      accounts = []; headroomState = 'error'; renderPanel(); renderOffer();
    }).finally(function () { pollBusy = false; });
  }
  function loadTasks(folder, version, deadline) {
    if (!panelVisible() || !folder || version !== panel.generation) return;
    el('loTaskStatus').textContent = panel.tasks.length ? 'Suggestions update automatically.' : 'Looking for tasks in this folder…';
    request('/api/leftover/proposals?repo_path=' + encodeURIComponent(folder)).then(function (res) {
      if (version !== panel.generation || folder !== panel.repo) return;
      if (res.status !== 200 || res.data.ok !== true) {
        panel.tasks = []; renderTasks();
        el('loTaskStatus').textContent = res.data.message || res.data.error || 'Could not find tasks. Check the folder and try another one.';
        return;
      }
      panel.tasks = validTasks(res.data); panel.canonical = res.data.repo_path;
      renderTasks();
      el('loSources').textContent = (res.data.sources || []).map(function (source) { return source.detail; }).join(' ');
      el('loTaskStatus').textContent = panel.tasks.length ? 'Choose one task. Each click starts only that task.' : res.data.loading ? 'Looking for tasks in this folder…' : 'No ready tasks found. Add a GitHub issue or a TODO in your code.';
      if (res.data.loading && Date.now() < deadline) setTimeout(function () { loadTasks(folder, version, deadline); }, 1000);
      else if (res.data.loading) el('loTaskStatus').textContent = 'Task discovery is taking longer than usual. It will update automatically.';
    }).catch(function () {
      if (version === panel.generation) { panel.tasks = []; renderTasks(); el('loTaskStatus').textContent = 'Could not reach CCC. Suggestions will retry automatically.'; }
    });
  }
  function changeRepo(value) {
    panel.repo = value.trim(); panel.generation += 1; panel.tasks = []; panel.signature = '';
    if (panel.repo) put('ccc-leftover-repo', panel.repo);
    el('loSources').textContent = ''; renderTasks();
    el('loTaskStatus').textContent = panel.repo ? 'Looking for tasks in this folder…' : 'Choose a folder to find tasks.';
    if (panel.repo) loadTasks(panel.repo, panel.generation, Date.now() + 25000);
  }
  function activate() {
    renderOffer();
    if (!panelVisible()) return;
    if (!opened) {
      opened = true;
      var current = activeRepo() || get('ccc-leftover-repo') || '';
      if (current) { el('loRepoInput').value = current; changeRepo(current); }
      request('/api/repo/list').then(function (res) {
        var options = (res.data.repos || []).concat(res.data.suggested || []);
        el('loRepos').innerHTML = options.map(function (item) { return '<option value="' + esc(item.path) + '">' + esc(item.label || item.path) + '</option>'; }).join('');
      }).catch(function () {});
    }
    poll();
  }
  function openPanel() {
    var settings = el('settingsBtn'), tab = el('settingsRailTab-leftover');
    if (settings && el('settingsModal') && el('settingsModal').hidden) settings.click();
    if (tab) tab.click();
    activate();
  }
  function findTask(id) {
    var inPanel = panel.tasks.find(function (item) { return item.id === id; });
    if (inPanel) return { task: inPanel, repo: panel.repo, generation: panel.generation, owner: panel };
    var inOffer = offer.tasks.find(function (item) { return item.id === id; });
    if (inOffer) return { task: inOffer, repo: offer.repo, generation: offer.generation, owner: offer };
    return null;
  }
  function renderAllTasks() { renderTasks(); var row = selected(); if (row && el('loOfferTasks')) renderOfferTasks(row); }
  // One user click = one spawn. Never called from a timer or a poll.
  function startTask(id) {
    var found = findTask(id), row = selected();
    if (!found || !row || busy[id]) return;
    var task = found.task;
    if (task.repo_path !== found.owner.canonical || get(savedKey(task, row)) === '1') return;
    if (!resetIsCurrent(row)) {
      outcomes[id] = { text: 'This estimate has reset. Wait for the next usage update.', error: true }; renderAllTasks(); poll(); return;
    }
    busy[id] = true; delete outcomes[id]; renderAllTasks();
    if (task.dispatch === 'watchtower') { approveTask(task, row); return; }
    fetch('/api/sessions/spawn', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CCC-Automated': navigator.webdriver ? 'true' : 'false' }, body: JSON.stringify(spawnBody(task, row)) })
      .then(function (res) { return res.json().catch(function () { return {}; }).then(function (data) { return { data: data || {}, ok: res.ok && data && data.ok === true }; }); })
      .then(function (res) {
        if (res.ok) put(savedKey(task, row), '1');
        outcomes[id] = res.ok ? { text: res.data.existing ? 'This task already has a session.' : 'Started with ' + engineName(row) + '.', session: res.data.session_id }
          : { text: res.data.error || 'Could not start this task. Try again.', error: true };
      }).catch(function () {
        outcomes[id] = { text: 'Could not confirm the start. Try again to check the same task.', error: true };
      }).finally(function () { busy[id] = false; renderAllTasks(); });
  }
  // WatchTower tickets go back to WatchTower: it claims the ticket (so no other
  // worker takes it too) and its dispatcher chooses the engine.
  function approveTask(task, row) {
    var id = task.id;
    fetch('/api/leftover/approve', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repo_path: task.repo_path, id: id }) })
      .then(function (res) { return res.json().catch(function () { return {}; }).then(function (data) { return { data: data || {}, ok: res.ok && data && data.ok === true }; }); })
      .then(function (res) {
        if (res.ok) put(savedKey(task, row), '1');
        outcomes[id] = res.ok ? { text: 'Queued ' + res.data.ref + ' in WatchTower' + (res.data.queue ? ' (' + res.data.queue + ')' : '') + '. A worker will pick it up.' }
          : { text: res.data.error || 'Could not queue this task. Try again.', error: true };
      }).catch(function () {
        outcomes[id] = { text: 'Could not confirm the request. Check WatchTower before trying again.', error: true };
      }).finally(function () { busy[id] = false; renderAllTasks(); });
  }
  function boot() {
    var rail = el('settingsRail'), pane = el('settingsPane');
    if (!rail || !pane) return;
    var css = document.createElement('link'); css.rel = 'stylesheet'; css.href = '/static/leftover.css'; document.head.appendChild(css);
    var tab = document.createElement('button');
    tab.type = 'button'; tab.className = 'settings-rail-item'; tab.id = 'settingsRailTab-leftover';
    tab.setAttribute('data-section-target', 'leftover'); tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', 'false'); tab.setAttribute('aria-controls', 'settingsSection-leftover');
    tab.innerHTML = '<span>Leftover Mode</span>'; rail.appendChild(tab);
    var section = document.createElement('section');
    section.id = 'settingsSection-leftover'; section.className = 'settings-section'; section.setAttribute('data-section-id', 'leftover'); section.setAttribute('role', 'tabpanel'); section.setAttribute('aria-labelledby', tab.id); section.setAttribute('aria-hidden', 'true');
    section.innerHTML = '<div class="settings-section-eyebrow">Leftover Mode</div><p class="settings-section-note">Use your plan before it resets. Pick a task and CCC starts it for you. Nothing starts until you click.</p>'
      + '<div class="settings-row" data-keywords="leftover unused allowance reset reminders"><div class="settings-row-main"><div class="settings-row-label">Leftover offers</div><div class="settings-row-desc">Let me know when at least 20% of my plan may go unused within a day. At most one reminder a day.</div></div><div class="settings-row-control"><button type="button" class="settings-toggle" id="loEnabled" role="switch" aria-checked="true" aria-label="Leftover offers"><span class="settings-toggle-track"><span class="settings-toggle-thumb"></span></span></button></div></div>'
      + '<div class="lo-hero"><h2 id="loHeadline" aria-live="polite">Checking what is left on your plan…</h2><p id="loEstimate"></p><select id="loAccount" aria-label="Plan to use" hidden></select></div>'
      + '<label class="lo-folder" for="loRepoInput">Project folder<input id="loRepoInput" type="text" list="loRepos" autocomplete="off" placeholder="Choose or paste a repository folder"><datalist id="loRepos"></datalist></label>'
      + '<p id="loPlan" class="lo-plan"></p><p id="loTaskStatus" role="status">Choose a folder to find tasks.</p><div id="loTasks"></div><p id="loSources" class="lo-sources"></p>';
    pane.appendChild(section);
    el('loEnabled').addEventListener('click', function () { setEnabled(!enabled()); poll(); });
    el('loAccount').addEventListener('change', function () { selectedId = this.value; renderPanel(); renderOffer(); });
    el('loRepoInput').addEventListener('change', function () { changeRepo(this.value); });
    document.addEventListener('click', function (event) {
      var target = event.target.closest && event.target.closest('[data-lo-start], [data-lo-open], [data-lo-choose], [data-lo-dismiss]');
      if (!target) return;
      if (target.hasAttribute('data-lo-start')) startTask(target.getAttribute('data-lo-start'));
      else if (target.hasAttribute('data-lo-open') && window.cccOpenSession) window.cccOpenSession(target.getAttribute('data-lo-open'));
      else if (target.hasAttribute('data-lo-choose')) openPanel();
      else if (target.hasAttribute('data-lo-dismiss')) { var row = selected(); if (row) put('ccc-leftover-snooze', row.id + ':' + row.resets_at); renderOffer(); }
    });
    new MutationObserver(activate).observe(section, { attributes: true, attributeFilter: ['class'] });
    if (el('settingsModal')) new MutationObserver(activate).observe(el('settingsModal'), { attributes: true, attributeFilter: ['hidden'] });
    window.addEventListener('storage', function () { syncToggle(); renderOffer(); renderTasks(); });
    document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
    syncToggle();
    setInterval(poll, 60000);
    if (new URLSearchParams(location.search).get('ccc_settings') === 'leftover') setTimeout(openPanel, 30);
    else poll();
  }
  window.cccLeftover = { isCandidate: isCandidate, resetIsCurrent: resetIsCurrent, summary: summary, spawnBody: spawnBody, validTasks: validTasks, enabled: enabled, setEnabled: setEnabled, notifyCandidate: notifyCandidate, open: openPanel, refresh: poll };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
