(function () {
  'use strict';

  var accounts = [], tasks = [], repo = '', generation = 0, selectedId = '', opened = false;
  var busy = {}, outcomes = {}, signature = '', headroomState = 'loading', pollBusy = false;
  var labels = { claude: 'Claude', codex: 'Codex', kimi: 'Kimi' };

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
  function summary(row) {
    var engine = labels[row.engine] || row.label;
    var reset = row.hours_to_reset >= 1 ? Math.round(row.hours_to_reset) + 'h' : Math.ceil(row.hours_to_reset * 60) + 'm';
    return hasDollars(row)
      ? 'You have about $' + row.expiring_usd_estimate.toFixed(2) + ' of ' + engine + ' work left that resets in ' + reset + '. Put it to work?'
      : 'About ' + row.projected_expiring_pct.toFixed(0) + '% of your ' + engine + ' allowance may go unused before it resets in ' + reset + '. Put it to work?';
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
      return res.json().catch(function () { return {}; }).then(function (data) { return { status: res.status, data: data }; });
    }).finally(function () { clearTimeout(timeout); });
  }
  function syncToggle() {
    var toggle = el('loEnabled');
    if (!toggle) return;
    toggle.classList.toggle('is-on', enabled());
    toggle.setAttribute('aria-checked', String(enabled()));
  }
  function savedKey(task, row) { return 'ccc-leftover-started:' + task.repo_path + ':' + task.id + ':' + row.engine; }
  function renderTasks() {
    var host = el('loTasks'), row = selected();
    if (!host) return;
    var markup = tasks.map(function (task) {
      var outcome = outcomes[task.id];
      var done = row && get(savedKey(task, row)) === '1';
      var running = busy[task.id];
      return '<article class="lo-task"><div class="lo-task-copy"><span class="lo-task-source">' + esc(task.source_label) + '</span>'
        + '<h3>' + esc(task.title) + '</h3><details><summary>Review task</summary><pre>' + esc(task.prompt) + '</pre></details></div>'
        + '<div class="lo-task-actions"><button type="button" class="lo-start" data-lo-start="' + esc(task.id) + '"'
        + ((!row || running || done) ? ' disabled' : '') + '>'
        + (running ? 'Starting…' : done ? 'Already started' : 'Start with ' + esc(row ? labels[row.engine] : 'your plan')) + '</button>'
        + (outcome ? '<div class="lo-outcome" role="status">' + esc(outcome.text) + '</div>' : '')
        + (outcome && outcome.session ? '<button type="button" class="lo-link" data-lo-open="' + esc(outcome.session) + '">Open session</button>' : '') + '</div></article>';
    }).join('');
    if (markup !== signature) { host.innerHTML = markup; signature = markup; }
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
    el('loPlan').textContent = row ? 'Uses your ' + labels[row.engine] + ' plan in a separate worktree. No task starts by itself.' : 'Tasks become available when a fresh estimate says your allowance may expire.';
    renderTasks();
    syncToggle();
  }
  function renderOffer() {
    var row = selected(), card = el('cccLeftoverOffer');
    if (!enabled() || !row || !window.cccPopups || !window.cccPopups.allowed('leftover-offer') || get('ccc-leftover-snooze') === row.id + ':' + row.resets_at) {
      if (card) card.hidden = true;
      return;
    }
    if (!card) {
      card = document.createElement('aside');
      card.id = 'cccLeftoverOffer'; card.className = 'lo-offer';
      card.innerHTML = '<strong id="loOfferTitle"></strong><p>Nothing starts without your approval.</p><button type="button" data-lo-choose>Choose a task</button><button type="button" class="lo-link" data-lo-dismiss>Not now</button>';
      document.body.appendChild(card);
    }
    el('loOfferTitle').textContent = summary(row);
    card.hidden = false;
  }
  function activeRepo() {
    return (window.activeConvRepoPath && window.activeConvRepoPath()) || (window.popoutRepoPath && window.popoutRepoPath()) || '';
  }
  function panelVisible() {
    var modal = el('settingsModal'), panel = el('settingsSection-leftover');
    return !!(modal && !modal.hidden && panel && panel.classList.contains('is-active-section'));
  }
  function autoAllowed() {
    return !!(enabled() && window.cccPopups && (window.cccPopups.allowed('leftover-offer') || window.cccPopups.allowed('leftover-notification')));
  }
  function poll() {
    if (document.hidden || pollBusy || (!panelVisible() && !autoAllowed())) return;
    pollBusy = true;
    return request('/api/headroom').then(function (res) {
      var valid = res.status === 200 && res.data.ok === true && Array.isArray(res.data.rows);
      accounts = valid ? res.data.rows : [];
      headroomState = res.status === 404 ? 'missing' : valid ? 'ready' : 'error';
      renderPanel(); renderOffer();
      var row = selected();
      if (row) notifyCandidate(row);
      if (panelVisible() && repo) loadTasks(repo, generation, Date.now() + 25000);
    }).catch(function () {
      accounts = []; headroomState = 'error'; renderPanel(); renderOffer();
    }).finally(function () { pollBusy = false; });
  }
  function loadTasks(folder, version, deadline) {
    if (!panelVisible() || !folder || version !== generation) return;
    el('loTaskStatus').textContent = tasks.length ? 'Suggestions update automatically.' : 'Looking for tasks in this folder…';
    request('/api/leftover/proposals?repo_path=' + encodeURIComponent(folder)).then(function (res) {
      if (version !== generation || folder !== repo) return;
      if (res.status !== 200 || res.data.ok !== true) {
        tasks = []; renderTasks();
        el('loTaskStatus').textContent = res.data.message || res.data.error || 'Could not find tasks. Check the folder and try another one.';
        return;
      }
      tasks = (res.data.proposals || []).filter(function (item) { return item && item.repo_path === folder && item.id && item.title && item.prompt; }).slice(0, 5);
      renderTasks();
      el('loSources').textContent = (res.data.sources || []).map(function (source) { return source.detail; }).join(' ');
      el('loTaskStatus').textContent = tasks.length ? 'Choose one task. Each click starts only that task.' : res.data.loading ? 'Looking for tasks in this folder…' : 'No ready tasks found. Add a GitHub issue or a TODO in your code.';
      if (res.data.loading && Date.now() < deadline) setTimeout(function () { loadTasks(folder, version, deadline); }, 1000);
      else if (res.data.loading) el('loTaskStatus').textContent = 'Task discovery is taking longer than usual. It will update automatically.';
    }).catch(function () {
      if (version === generation) { tasks = []; renderTasks(); el('loTaskStatus').textContent = 'Could not reach CCC. Suggestions will retry automatically.'; }
    });
  }
  function changeRepo(value) {
    repo = value.trim(); generation += 1; tasks = []; signature = ''; outcomes = {};
    el('loSources').textContent = ''; renderTasks();
    el('loTaskStatus').textContent = repo ? 'Looking for tasks in this folder…' : 'Choose a folder to find tasks.';
    if (repo) loadTasks(repo, generation, Date.now() + 25000);
  }
  function activate() {
    if (!panelVisible()) return;
    if (!opened) {
      opened = true;
      var current = activeRepo();
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
    if (settings && el('settingsModal').hidden) settings.click();
    if (tab) tab.click();
    activate();
  }
  function startTask(id) {
    var task = tasks.find(function (item) { return item.id === id; }), row = selected();
    if (!task || !row || busy[id] || task.repo_path !== repo || get(savedKey(task, row)) === '1') return;
    if (!resetIsCurrent(row)) {
      outcomes[id] = { text: 'This estimate has reset. Wait for the next usage update.' }; renderTasks(); poll(); return;
    }
    var version = generation;
    busy[id] = true; delete outcomes[id]; renderTasks();
    fetch('/api/sessions/spawn', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CCC-Automated': navigator.webdriver ? 'true' : 'false' }, body: JSON.stringify(spawnBody(task, row)) })
      .then(function (res) { return res.json().catch(function () { return {}; }).then(function (data) { return { data: data, ok: res.ok && data.ok === true }; }); })
      .then(function (res) {
        if (res.ok) put(savedKey(task, row), '1');
        if (version !== generation) return;
        outcomes[id] = res.ok ? { text: res.data.existing ? 'This task already has a session.' : 'Started with ' + labels[row.engine] + '.', session: res.data.session_id }
          : { text: res.data.error || 'Could not start this task. Try again.' };
      }).catch(function () {
        if (version === generation) outcomes[id] = { text: 'Could not confirm the start. Try again to check the same task.' };
      }).finally(function () { busy[id] = false; if (version === generation) renderTasks(); });
  }
  function boot() {
    var rail = el('settingsRail'), pane = el('settingsPane');
    if (!rail || !pane) return;
    var css = document.createElement('link'); css.rel = 'stylesheet'; css.href = '/static/leftover.css'; document.head.appendChild(css);
    var tab = document.createElement('button');
    tab.type = 'button'; tab.className = 'settings-rail-item'; tab.id = 'settingsRailTab-leftover';
    tab.setAttribute('data-section-target', 'leftover'); tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', 'false'); tab.setAttribute('aria-controls', 'settingsSection-leftover');
    tab.innerHTML = '<span>Leftover Mode</span>'; rail.appendChild(tab);
    var panel = document.createElement('section');
    panel.id = 'settingsSection-leftover'; panel.className = 'settings-section'; panel.setAttribute('data-section-id', 'leftover'); panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', tab.id); panel.setAttribute('aria-hidden', 'true');
    panel.innerHTML = '<div class="settings-section-eyebrow">Leftover Mode</div><p class="settings-section-note">Choose a task to use your plan before it resets. Nothing starts until you approve it.</p>'
      + '<div class="settings-row" data-keywords="leftover unused allowance reset reminders"><div class="settings-row-main"><div class="settings-row-label">Leftover reminders</div><div class="settings-row-desc">Remind me when at least 20% may expire within a day. Automatic reminders stay off until approved for this build.</div></div><div class="settings-row-control"><button type="button" class="settings-toggle" id="loEnabled" role="switch" aria-checked="true" aria-label="Leftover reminders"><span class="settings-toggle-track"><span class="settings-toggle-thumb"></span></span></button></div></div>'
      + '<div class="lo-hero"><h2 id="loHeadline" aria-live="polite">Checking what is left on your plan…</h2><p id="loEstimate"></p><select id="loAccount" aria-label="Plan to use" hidden></select></div>'
      + '<label class="lo-folder" for="loRepoInput">Project folder<input id="loRepoInput" type="text" list="loRepos" autocomplete="off" placeholder="Choose or paste a repository folder"><datalist id="loRepos"></datalist></label>'
      + '<p id="loPlan" class="lo-plan"></p><p id="loTaskStatus" role="status">Choose a folder to find tasks.</p><div id="loTasks"></div><p id="loSources" class="lo-sources"></p>';
    pane.appendChild(panel);
    el('loEnabled').addEventListener('click', function () { setEnabled(!enabled()); poll(); });
    el('loAccount').addEventListener('change', function () { selectedId = this.value; renderPanel(); });
    el('loRepoInput').addEventListener('change', function () { changeRepo(this.value); });
    document.addEventListener('click', function (event) {
      var target = event.target.closest('[data-lo-start], [data-lo-open], [data-lo-choose], [data-lo-dismiss]');
      if (!target) return;
      if (target.hasAttribute('data-lo-start')) startTask(target.getAttribute('data-lo-start'));
      else if (target.hasAttribute('data-lo-open') && window.cccOpenSession) window.cccOpenSession(target.getAttribute('data-lo-open'));
      else if (target.hasAttribute('data-lo-choose')) openPanel();
      else if (target.hasAttribute('data-lo-dismiss')) { var row = selected(); if (row) put('ccc-leftover-snooze', row.id + ':' + row.resets_at); renderOffer(); }
    });
    new MutationObserver(activate).observe(panel, { attributes: true, attributeFilter: ['class'] });
    new MutationObserver(activate).observe(el('settingsModal'), { attributes: true, attributeFilter: ['hidden'] });
    window.addEventListener('storage', function () { syncToggle(); renderOffer(); renderTasks(); });
    document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
    syncToggle();
    setInterval(poll, 60000);
    if (new URLSearchParams(location.search).get('ccc_settings') === 'leftover') setTimeout(openPanel, 30);
    else poll();
  }
  window.cccLeftover = { isCandidate: isCandidate, resetIsCurrent: resetIsCurrent, summary: summary, spawnBody: spawnBody, enabled: enabled, setEnabled: setEnabled, notifyCandidate: notifyCandidate, open: openPanel, refresh: poll };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
