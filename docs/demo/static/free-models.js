/* Free model leaderboard: races the router's catalog through five tiny
 * coding tasks and crowns the best free model. Talks to:
 *   GET  /api/free-router/models          -> [{id,platform,supports_tools,context,rank,score,ready,...}]
 *   GET  /api/free-router/eval/status     -> router + job info (additive)
 *   GET  /api/free-router/eval/<job_id>   -> job progress (same shape as /api/setup/jobs)
 *   POST /api/free-router/eval            -> {job_id}
 *   POST /api/free-router/prefer {model}  -> pin a default
 * window.cccFx (L08) is used when present and quietly skipped when not. */
(function () {
  'use strict';

  var els = {
    runBtn: document.getElementById('fmRunBtn'),
    runNote: document.getElementById('fmRunNote'),
    notice: document.getElementById('fmNotice'),
    race: document.getElementById('fmRace'),
    raceStep: document.getElementById('fmRaceStep'),
    raceCount: document.getElementById('fmRaceCount'),
    progress: document.getElementById('fmProgress'),
    raceLog: document.getElementById('fmRaceLog'),
    tasks: document.getElementById('fmTasks'),
    list: document.getElementById('fmList'),
    empty: document.getElementById('fmEmpty'),
    toast: document.getElementById('fmToast'),
    sub: document.getElementById('fmSub')
  };

  var state = {
    info: null,
    models: [],
    jobId: null,
    jobTimer: null,
    refreshTimer: null,
    toastTimer: null
  };

  function fx(name) {
    try {
      if (window.cccFx && typeof window.cccFx.play === 'function') window.cccFx.play(name);
    } catch (e) { /* sounds are decoration, never a reason to break */ }
  }

  function confetti(opts) {
    try {
      if (window.cccFx && typeof window.cccFx.confetti === 'function' &&
          !(window.cccFx.reducedMotion && window.cccFx.reducedMotion())) {
        window.cccFx.confetti(opts || {});
      }
    } catch (e) { /* ignore */ }
  }

  function jget(url) {
    return fetch(url, { headers: { 'X-CCC-Background': '1' } }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (body) {
        return { status: r.status, body: body };
      });
    }).catch(function () { return { status: 0, body: null }; });
  }

  function jpost(url, data) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data || {})
    }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (body) {
        return { status: r.status, body: body };
      });
    }).catch(function () { return { status: 0, body: null }; });
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function toast(text, isErr) {
    els.toast.textContent = text;
    els.toast.className = 'fm-toast show' + (isErr ? ' err' : '');
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(function () {
      els.toast.className = 'fm-toast';
    }, 3600);
  }

  function fmtCtx(ctx) {
    if (!ctx) return '';
    var n = Number(ctx);
    if (!isFinite(n) || n <= 0) return '';
    if (n >= 1000000) return (n / 1000000).toFixed(n % 1000000 ? 1 : 0) + 'M ctx';
    if (n >= 1000) return Math.round(n / 1000) + 'K ctx';
    return n + ' ctx';
  }

  function fmtMs(ms) {
    if (ms == null) return '';
    if (ms < 1000) return Math.round(ms) + 'ms';
    return (ms / 1000).toFixed(1) + 's';
  }

  function fmtAgo(iso) {
    if (!iso) return '';
    var then = Date.parse(iso);
    if (!isFinite(then)) return '';
    var s = Math.max(0, (Date.now() - then) / 1000);
    if (s < 90) return 'just now';
    if (s < 3600) return Math.round(s / 60) + 'm ago';
    if (s < 86400) return Math.round(s / 3600) + 'h ago';
    return Math.round(s / 86400) + 'd ago';
  }

  function statusPill(row) {
    if (row.pinned) return ''; // the "your default" crown already says it
    if (row.ready) return '<span class="fm-pill ok">ready</span>';
    if (row.execution_status === 'exhausted') return '<span class="fm-pill warn">cooling down</span>';
    if (!row.in_catalog) return '<span class="fm-pill off">left the catalog</span>';
    return '<span class="fm-pill warn">needs a key</span>';
  }

  function taskDots(row) {
    var dots = '';
    var per = row.per_task || [];
    for (var i = 0; i < per.length; i++) {
      var cls = per[i].passed ? 'pass' : 'fail';
      var tip = esc((per[i].task || '') + ': ' + (per[i].passed ? 'passed' : 'failed'));
      dots += '<span class="fm-dot ' + cls + '" title="' + tip + '"></span>';
    }
    if (!dots) return '';
    return '<span class="fm-dots" title="Task results">' + dots + '</span>';
  }

  function rankBadge(row) {
    if (row.rank === 1) return '<div class="fm-rank r1">1</div>';
    if (row.rank === 2) return '<div class="fm-rank r2">2</div>';
    if (row.rank === 3) return '<div class="fm-rank r3">3</div>';
    if (row.rank) return '<div class="fm-rank">' + row.rank + '</div>';
    return '<div class="fm-rank none">&mdash;</div>';
  }

  function scoreBlock(row) {
    if (!row.evaluated) {
      var canRace = row.ready;
      return '<div class="fm-score">' +
        '<div class="fm-score-sub">' + (canRace ? 'Not raced yet' : 'Cannot race right now') + '</div>' +
        preferButton(row) +
        '</div>';
    }
    var score = row.score == null ? 0 : row.score;
    var low = score < 50 ? ' low' : '';
    var sub = (row.passed || 0) + '/' + (row.tasks || 0) + ' tasks';
    if (row.median_request_ms) sub += ' · ' + fmtMs(row.median_request_ms) + ' a turn';
    if (row.evaluated_at) sub += ' · ' + fmtAgo(row.evaluated_at);
    return '<div class="fm-score">' +
      '<div class="fm-score-num">' + score.toFixed(score % 1 ? 1 : 0) + '<small> /100</small></div>' +
      '<div class="fm-score-bar"><div class="fm-score-fill' + low + '" style="width:' + Math.max(2, Math.min(100, score)) + '%"></div></div>' +
      '<div class="fm-score-sub">' + esc(sub) + '</div>' +
      preferButton(row) +
      '</div>';
  }

  function preferButton(row) {
    if (!row.id || row.pinned || !row.ready) return '';
    if (!row.evaluated || !(row.score > 0)) return '';
    return '<button type="button" class="fm-prefer-btn" data-model="' + esc(row.id) + '">Set as default</button>';
  }

  function renderModels() {
    var rows = state.models || [];
    if (!rows.length) {
      els.list.innerHTML = '';
      return;
    }
    var html = '';
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var top = row.rank === 1 && row.evaluated && (row.score || 0) > 0;
      var dim = !row.ready && !top;
      var crown = '';
      if (row.pinned) crown = '<span class="fm-crown pinned">your default</span>';
      else if (top) crown = '<span class="fm-crown">best pick</span>';
      var pills = statusPill(row);
      if (row.supports_tools) pills += '<span class="fm-pill ok">tools</span>';
      else pills += '<span class="fm-pill off">no tools</span>';
      var ctx = fmtCtx(row.context);
      if (ctx) pills += '<span class="fm-pill">' + esc(ctx) + '</span>';
      var plat = row.platform ? '<span class="fm-pill">' + esc(row.platform) + '</span>' : '';
      html +=
        '<div class="fm-card' + (top ? ' is-top' : '') + (dim ? ' is-dim' : '') + '" data-model="' + esc(row.id) + '">' +
          rankBadge(row) +
          '<div class="fm-main">' +
            '<div class="fm-name-row"><span class="fm-name" title="' + esc(row.id) + '">' + esc(row.name || row.id) + '</span>' + crown + '</div>' +
            '<div class="fm-id">' + esc(row.id) + '</div>' +
            '<div class="fm-meta">' + pills + plat + taskDots(row) + '</div>' +
          '</div>' +
          scoreBlock(row) +
        '</div>';
    }
    els.list.innerHTML = html;
  }

  function renderTasks() {
    var tasks = (state.info && state.info.tasks) || [];
    if (!tasks.length) { els.tasks.hidden = true; return; }
    els.tasks.hidden = false;
    els.tasks.innerHTML = tasks.map(function (t) {
      return '<span class="fm-task-chip">' + esc(t.title || t.id) + '</span>';
    }).join('');
  }

  function renderNotice() {
    var info = state.info || {};
    var html = '';
    var err = false;
    if (!info.router_configured) {
      err = true;
      html = '<div><strong>No free router found.</strong><br>' +
        'This machine has no freellmapi router yet. Once the free router is ' +
        'installed and running, its models show up here for the race.</div>';
    } else if (!info.has_unified_key) {
      err = true;
      html = '<div><strong>The router needs its key.</strong><br>' +
        'CCC could not find the router&#39;s unified key, so it cannot ask ' +
        'the router for its model list.</div>';
    } else if (info.catalog_error) {
      err = true;
      html = '<div><strong>The router is not answering.</strong><br>' +
        esc(info.catalog_error) + '</div>';
    }
    if (html) {
      els.notice.innerHTML = html;
      els.notice.className = 'fm-notice' + (err ? ' error' : '');
      els.notice.hidden = false;
    } else {
      els.notice.hidden = true;
    }
  }

  function renderRunButton() {
    var info = state.info || {};
    var running = !!(info.running_job || state.jobId);
    els.runBtn.disabled = running || !info.router_configured;
    els.runBtn.textContent = running ? 'Racing now…' : 'Run the race';
    var note = '';
    if (info.last_eval_at) {
      note = 'Last race ' + fmtAgo(info.last_eval_at);
      if (info.best && info.best.model) note += ' · winner: ' + info.best.model;
    } else if (info.router_configured) {
      note = 'Five tiny coding tasks. A few minutes. Still $0.';
    }
    els.runNote.textContent = note;
  }

  function renderEmpty() {
    var info = state.info || {};
    if ((state.models || []).length) { els.empty.hidden = true; return; }
    var text;
    if (!info.router_configured) {
      text = 'The leaderboard is waiting for its first router.';
    } else if (info.catalog_error) {
      text = 'The router is up but did not hand over a model list yet.';
    } else {
      text = 'No models to race yet. Add a provider key to the router and they will appear here.';
    }
    els.empty.textContent = text;
    els.empty.hidden = false;
  }

  function renderAll() {
    renderNotice();
    renderRunButton();
    renderTasks();
    renderModels();
    renderEmpty();
  }

  function refresh() {
    return Promise.all([
      jget('/api/free-router/eval/status'),
      jget('/api/free-router/models')
    ]).then(function (res) {
      var info = res[0].body, models = res[1].body;
      if (info && typeof info === 'object') state.info = info;
      if (Array.isArray(models)) state.models = models;
      // A race started elsewhere (onboarding, another tab): pick it up live.
      var running = state.info && state.info.running_job;
      if (running && !state.jobId) watchJob(running);
      renderAll();
    });
  }

  function startRace() {
    els.runBtn.disabled = true;
    els.runBtn.textContent = 'Starting…';
    jpost('/api/free-router/eval', {}).then(function (res) {
      if (res.status === 200 && res.body && res.body.job_id) {
        fx('whoosh');
        watchJob(res.body.job_id);
      } else {
        els.runBtn.disabled = false;
        els.runBtn.textContent = 'Run the race';
        var msg = (res.body && res.body.error) || 'Could not start the race.';
        if (res.body && res.body.job_id) {
          watchJob(res.body.job_id);
          return;
        }
        toast(msg, true);
        fx('error');
      }
    });
  }

  function watchJob(jobId) {
    state.jobId = jobId;
    els.race.hidden = false;
    els.raceLog.textContent = '';
    els.raceStep.textContent = 'The race is on';
    renderRunButton();
    clearInterval(state.jobTimer);
    pollJob();
    state.jobTimer = setInterval(pollJob, 800);
  }

  function pollJob() {
    if (!state.jobId) return;
    jget('/api/free-router/eval/' + encodeURIComponent(state.jobId)).then(function (res) {
      var job = res.body;
      if (!job || res.status === 404) {
        clearInterval(state.jobTimer);
        state.jobId = null;
        refresh();
        return;
      }
      if (job.step) els.raceStep.textContent = job.step === 'done' ? 'Race complete' : 'Racing: ' + job.step;
      var pct = Math.round((job.progress || 0) * 100);
      els.progress.style.width = pct + '%';
      els.raceCount.textContent = pct + '%';
      renderLines(job.lines || []);
      if (job.status === 'done' || job.status === 'error') {
        clearInterval(state.jobTimer);
        state.jobId = null;
        if (job.status === 'done') {
          var best = job.result && job.result.best && job.result.best.model;
          if (best) {
            toast(best + ' wins the race' + (job.result.best.pinned ? ' and is now your default free model.' : '.'));
            confetti({ particleCount: 140, spread: 80 });
            fx('success');
          } else {
            toast('Race done. No model passed enough tasks to win.', true);
            fx('error');
          }
        } else {
          toast(job.error || 'The race hit a snag.', true);
          fx('error');
        }
        refresh().then(function () {
          var top = document.querySelector('.fm-card.is-top');
          if (top) {
            top.classList.add('fm-glow');
            top.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          }
        });
      }
    });
  }

  function renderLines(lines) {
    var html = '';
    var start = Math.max(0, lines.length - 60);
    for (var i = start; i < lines.length; i++) {
      var line = String(lines[i]);
      var cls = '';
      if (/ PASS /.test(line) || /Winner:/.test(line) || /Pinned /.test(line)) cls = ' class="ok"';
      if (/ FAIL /.test(line) || /crashed/.test(line)) cls = ' class="bad"';
      if (/Winner:/.test(line)) cls = ' class="win"';
      html += '<span' + cls + '>' + esc(line) + '</span>\n';
    }
    var atBottom = els.raceLog.scrollTop + els.raceLog.clientHeight >= els.raceLog.scrollHeight - 24;
    els.raceLog.innerHTML = html;
    if (atBottom) els.raceLog.scrollTop = els.raceLog.scrollHeight;
  }

  function prefer(modelId) {
    jpost('/api/free-router/prefer', { model: modelId }).then(function (res) {
      if (res.status === 200 && res.body && res.body.ok) {
        toast(modelId + ' is now your default free model.');
        fx('success');
        refresh();
      } else {
        toast((res.body && res.body.error) || 'Could not set the default model.', true);
        fx('error');
      }
    });
  }

  els.runBtn.addEventListener('click', startRace);
  els.list.addEventListener('click', function (ev) {
    var btn = ev.target && ev.target.closest && ev.target.closest('.fm-prefer-btn');
    if (!btn) return;
    btn.disabled = true;
    prefer(btn.getAttribute('data-model'));
  });

  // Keep the board fresh while visible; a running job already polls faster.
  state.refreshTimer = setInterval(function () {
    if (!document.hidden && !state.jobId) refresh();
  }, 30000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) refresh();
  });

  refresh().then(function () {
    // /free-models?run=1 (onboarding deep link): start the race for them.
    var params = new URLSearchParams(location.search);
    if (params.get('run') === '1' && !state.jobId && !(state.info && state.info.running_job)) {
      if (state.info && state.info.router_configured && !state.info.catalog_error) {
        startRace();
      }
    }
  });
})();
