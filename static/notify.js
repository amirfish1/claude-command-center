/* CCC notifications: one pipeline, three doors.
 *
 * Server items (POST /api/notify, task-complete watcher, 6pm digest, savings
 * milestones) arrive over the /api/events SSE stream as "notify.request"
 * patches — app.js hands them to window.cccNotify._deliver. /api/notify/pending
 * is the 60s catch-up poll for anything missed while the stream was down.
 *
 * Presentation, in order of preference:
 *   1. macOS app native bridge (window.webkit.messageHandlers.cccNotify, L10)
 *   2. Web Notification via the registered service worker (page hidden)
 *   3. In-app toast (always available; the visible-page default)
 *
 * localStorage prefs: ccc-notify-enabled (master), ccc-notify-tasks,
 * ccc-notify-digest, ccc-notify-milestones. All default on. Permission asks
 * are capped at 2 total, 7 days apart (ccc-notify-ask-count /
 * ccc-notify-snooze-until).
 */
(function () {
  'use strict';

  var LS_ENABLED = 'ccc-notify-enabled';
  var LS_TASKS = 'ccc-notify-tasks';
  var LS_DIGEST = 'ccc-notify-digest';
  var LS_MILESTONES = 'ccc-notify-milestones';
  var LS_CURSOR = 'ccc-notify-cursor';
  var LS_ASK_COUNT = 'ccc-notify-ask-count';
  var LS_SNOOZE = 'ccc-notify-snooze-until';

  var MAX_ASKS = 2;
  var SNOOZE_MS = 7 * 24 * 3600 * 1000;
  var MAX_TOASTS = 3;
  var SEEN_CAP = 500;
  // Anything older than this on arrival is history, not a ping.
  var AGE_LIMITS = {
    task: 10 * 60 * 1000,
    needs_input: 10 * 60 * 1000,
    milestone: 12 * 3600 * 1000,
    digest: 12 * 3600 * 1000,
    info: 30 * 60 * 1000,
    success: 30 * 60 * 1000,
    error: 30 * 60 * 1000,
  };

  var _seen = [];           // bounded ring of item ids (Set order for dedupe)
  var _seenSet = {};
  var _toastQueue = [];
  var _permissionAsked = false;
  var _stylesInjected = false;

  function _lsGet(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }
  function _lsSet(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }

  function pref(name) {
    // 'enabled' | 'tasks' | 'digest' | 'milestones' — default true.
    var key = 'ccc-notify-' + name;
    return _lsGet(key) !== '0';
  }
  function setPref(name, on) {
    _lsSet('ccc-notify-' + name, on ? '1' : '0');
    refreshSettings();
  }
  function enabled() { return pref('enabled'); }

  function _reducedMotion() {
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; }
    catch (_) { return false; }
  }

  // ── styles ────────────────────────────────────────────────────────────

  function _injectStyles() {
    if (_stylesInjected) return;
    _stylesInjected = true;
    var css = ''
      + '#cccNotifyStack{position:fixed;right:14px;bottom:14px;z-index:2147483000;'
      + 'display:flex;flex-direction:column;gap:8px;max-width:min(360px,calc(100vw - 28px));'
      + 'font-family:var(--font-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif);}'
      + '.ccc-toast{pointer-events:auto;display:flex;gap:10px;align-items:flex-start;'
      + 'background:var(--bg-elevated,#1e2130);color:var(--text-primary,#e8eaf0);'
      + 'border:1px solid var(--border-subtle,rgba(255,255,255,.09));border-radius:10px;'
      + 'padding:10px 12px 10px 10px;box-shadow:0 8px 28px rgba(0,0,0,.35);'
      + 'cursor:default;position:relative;overflow:hidden;}'
      + '.ccc-toast.ccc-toast-in{animation:cccToastIn .22s ease-out;}'
      + '.ccc-toast.ccc-toast-out{opacity:0;transform:translateX(12px);transition:opacity .18s,transform .18s;}'
      + '@keyframes cccToastIn{from{opacity:0;transform:translateY(8px) scale(.98)}to{opacity:1;transform:none}}'
      + '@media (prefers-reduced-motion:reduce){.ccc-toast.ccc-toast-in{animation:none}.ccc-toast.ccc-toast-out{transition:none}}'
      + '.ccc-toast-bar{flex:0 0 3px;align-self:stretch;border-radius:2px;background:var(--text-muted,#888);}'
      + '.ccc-toast-kind-task .ccc-toast-bar{background:#3fb950;}'
      + '.ccc-toast-kind-success .ccc-toast-bar{background:#3fb950;}'
      + '.ccc-toast-kind-needs_input .ccc-toast-bar{background:#d29922;}'
      + '.ccc-toast-kind-milestone .ccc-toast-bar{background:linear-gradient(180deg,#a371f7,#58a6ff);}'
      + '.ccc-toast-kind-digest .ccc-toast-bar{background:#58a6ff;}'
      + '.ccc-toast-kind-error .ccc-toast-bar{background:#f85149;}'
      + '.ccc-toast-body{flex:1 1 auto;min-width:0;}'
      + '.ccc-toast-title{font-weight:600;font-size:12.5px;line-height:1.35;margin:0 0 2px;}'
      + '.ccc-toast-text{font-size:12px;line-height:1.4;color:var(--text-secondary,#a6adbb);margin:0;'
      + 'overflow-wrap:anywhere;}'
      + '.ccc-toast-actions{display:flex;gap:6px;margin-top:7px;}'
      + '.ccc-toast-btn{font:inherit;font-size:11.5px;font-weight:600;padding:4px 10px;border-radius:6px;'
      + 'border:1px solid var(--border-subtle,rgba(255,255,255,.14));background:transparent;'
      + 'color:var(--text-primary,#e8eaf0);cursor:pointer;}'
      + '.ccc-toast-btn.ccc-toast-btn-primary{background:var(--accent,#2f81f7);border-color:transparent;color:#fff;}'
      + '.ccc-toast-btn:hover{filter:brightness(1.15);}'
      + '.ccc-toast-close{flex:0 0 auto;border:0;background:transparent;color:var(--text-muted,#7d8590);'
      + 'font-size:14px;line-height:1;padding:2px;cursor:pointer;border-radius:4px;}'
      + '.ccc-toast-close:hover{color:var(--text-primary,#e8eaf0);}'
      + '.ccc-toast-life{position:absolute;left:0;bottom:0;height:2px;background:currentColor;opacity:.25;'
      + 'transform-origin:left;animation:cccToastLife linear forwards;}'
      + '@keyframes cccToastLife{from{transform:scaleX(1)}to{transform:scaleX(0)}}';
    var el = document.createElement('style');
    el.id = 'cccNotifyStyles';
    el.textContent = css;
    document.head.appendChild(el);
  }

  function _stack() {
    var el = document.getElementById('cccNotifyStack');
    if (!el) {
      el = document.createElement('div');
      el.id = 'cccNotifyStack';
      el.setAttribute('aria-live', 'polite');
      el.setAttribute('aria-label', 'Notifications');
      document.body.appendChild(el);
    }
    return el;
  }

  // ── toasts ────────────────────────────────────────────────────────────

  var _KIND_ICON = {
    task: '✓', success: '✓', needs_input: '?', milestone: '★',
    digest: '☰', error: '!', info: 'i',
  };

  function _openItem(item) {
    var sid = item && item.session_id;
    if (sid && typeof window.cccOpenSession === 'function') {
      try { window.cccOpenSession(sid); return; } catch (_) {}
    }
    var url = (item && item.url) || '';
    if (url.startsWith('/')) {
      try { window.location.href = url; } catch (_) {}
    } else if (/^https?:\/\//.test(url)) {
      try { window.open(url, '_blank', 'noopener'); } catch (_) {}
    }
  }

  function toast(item) {
    _injectStyles();
    var stack = _stack();
    var t = document.createElement('div');
    var kind = String(item.kind || 'info');
    t.className = 'ccc-toast ccc-toast-kind-' + kind + (_reducedMotion() ? '' : ' ccc-toast-in');
    t.setAttribute('role', 'status');

    var bar = document.createElement('div');
    bar.className = 'ccc-toast-bar';
    t.appendChild(bar);

    var body = document.createElement('div');
    body.className = 'ccc-toast-body';
    var title = document.createElement('p');
    title.className = 'ccc-toast-title';
    var icon = _KIND_ICON[kind] || 'i';
    title.textContent = icon + '  ' + String(item.title || 'Notification');
    body.appendChild(title);
    if (item.body) {
      var text = document.createElement('p');
      text.className = 'ccc-toast-text';
      text.textContent = String(item.body);
      body.appendChild(text);
    }
    if (item.url || item.session_id) {
      var actions = document.createElement('div');
      actions.className = 'ccc-toast-actions';
      var view = document.createElement('button');
      view.type = 'button';
      view.className = 'ccc-toast-btn ccc-toast-btn-primary';
      view.textContent = 'View';
      view.addEventListener('click', function (ev) {
        ev.stopPropagation();
        _dismiss(t);
        _openItem(item);
      });
      actions.appendChild(view);
      body.appendChild(actions);
    }
    t.appendChild(body);

    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'ccc-toast-close';
    close.setAttribute('aria-label', 'Dismiss notification');
    close.textContent = '×';
    close.addEventListener('click', function (ev) {
      ev.stopPropagation();
      _dismiss(t);
    });
    t.appendChild(close);

    t.addEventListener('click', function () {
      if (item.url || item.session_id) { _dismiss(t); _openItem(item); }
    });

    var life = null;
    var ttl = (kind === 'digest' || kind === 'milestone') ? 14000 : 9000;
    var remaining = ttl;
    var armAt = Date.now();
    function arm() {
      armAt = Date.now();
      if (life) life.remove();
      if (_reducedMotion()) return;
      life = document.createElement('div');
      life.className = 'ccc-toast-life';
      life.style.animationDuration = remaining + 'ms';
      t.appendChild(life);
    }
    var timer = setTimeout(function tick() { _dismiss(t); }, ttl);
    function _dismiss(el) {
      if (!el.parentNode) return;
      clearTimeout(timer);
      el.classList.add('ccc-toast-out');
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 200);
      _drainQueue();
    }
    t.addEventListener('mouseenter', function () {
      remaining = Math.max(500, remaining - (Date.now() - armAt));
      clearTimeout(timer);
      if (life) life.style.animationPlayState = 'paused';
    });
    t.addEventListener('mouseleave', function () {
      timer = setTimeout(function () { _dismiss(t); }, remaining);
      if (life) life.style.animationPlayState = 'running';
    });
    arm();

    stack.appendChild(t);
    while (stack.children.length > MAX_TOASTS) {
      var first = stack.children[0];
      first.parentNode.removeChild(first);
    }
    return t;
  }

  function _drainQueue() {
    var stack = document.getElementById('cccNotifyStack');
    var visible = stack ? stack.children.length : 0;
    while (_toastQueue.length && visible < MAX_TOASTS) {
      toast(_toastQueue.shift());
      visible += 1;
    }
  }

  // ── channels ──────────────────────────────────────────────────────────

  function _nativeBridge(item) {
    try {
      var bridge = window.webkit && window.webkit.messageHandlers &&
        window.webkit.messageHandlers.cccNotify;
      if (bridge && typeof bridge.postMessage === 'function') {
        bridge.postMessage({
          title: String(item.title || ''),
          body: String(item.body || ''),
          url: String(item.url || ''),
          kind: String(item.kind || 'info'),
        });
        return true;
      }
    } catch (_) {}
    return false;
  }

  function _webNotification(item) {
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return false;
    var opts = {
      body: String(item.body || ''),
      icon: '/static/icon.svg',
      tag: String(item.id || ('ccc-' + Date.now())),
      data: { url: item.url || '/', session_id: item.session_id || '' },
      // System banners already auto-dismiss; requireInteraction for "needs
      // you" would be pushy — keep everything standard.
    };
    try {
      if (navigator.serviceWorker) {
        navigator.serviceWorker.ready.then(function (reg) {
          reg.showNotification(String(item.title || 'Command Center'), opts);
        }).catch(function () {
          new Notification(String(item.title || 'Command Center'), opts);
        });
      } else {
        var n = new Notification(String(item.title || 'Command Center'), opts);
        n.onclick = function () {
          try { window.focus(); } catch (_) {}
          _openItem(item);
        };
      }
      return true;
    } catch (_) {
      return false;
    }
  }

  // ── delivery ──────────────────────────────────────────────────────────

  function _seenBefore(id) {
    if (!id) return false;
    if (_seenSet[id]) return true;
    _seenSet[id] = true;
    _seen.push(id);
    if (_seen.length > SEEN_CAP) {
      var drop = _seen.splice(0, _seen.length - SEEN_CAP);
      drop.forEach(function (d) { delete _seenSet[d]; });
    }
    return false;
  }

  function _kindAllowed(kind) {
    if (kind === 'task' || kind === 'needs_input') return pref('tasks');
    if (kind === 'digest') return pref('digest');
    if (kind === 'milestone') return pref('milestones');
    return true;
  }

  function _pageCalm() {
    return document.hidden || !document.hasFocus();
  }

  function _deliver(item) {
    if (!item || typeof item !== 'object') return false;
    if (!item.title) return false;
    var id = String(item.id || '');
    if (id && _seenBefore(id)) return false;
    var kind = String(item.kind || 'info');
    var age = Date.now() - (Number(item.ts) || Date.now() / 1000) * 1000;
    var limit = AGE_LIMITS[kind] || AGE_LIMITS.info;
    if (age > limit) return false;
    if (!enabled()) return false;
    if (!_kindAllowed(kind)) return false;
    // Unapproved pop-ups stay silent (static/popups.js); the explicit
    // "Send test notification" button is not a pop-up.
    if (id.indexOf('ntf_test_') !== 0 &&
        !(window.cccPopups && window.cccPopups.notifyAllowed(kind))) return false;

    var channel = 'toast';
    if (_pageCalm()) {
      if (_nativeBridge(item)) channel = 'native';
      else if (_webNotification(item)) channel = 'notification';
    }
    // The toast is the persistent, clickable record — always render it too
    // for actionable kinds so a glance at the dashboard shows what fired.
    if (channel === 'toast' || kind === 'needs_input' || kind === 'milestone' || kind === 'digest') {
      var stack = document.getElementById('cccNotifyStack');
      if (stack && stack.children.length >= MAX_TOASTS) _toastQueue.push(item);
      else toast(item);
    }
    if (kind === 'milestone') {
      try { if (window.cccFx && window.cccFx.confetti) window.cccFx.confetti(); } catch (_) {}
      try { if (window.cccFx && window.cccFx.play) window.cccFx.play('coin'); } catch (_) {}
    }
    // Every successful delivery is a success moment — a natural, friendly
    // time to offer real system notifications once.
    maybeAskPermission();
    return true;
  }

  // ── permission ask ────────────────────────────────────────────────────

  function _permissionState() {
    if (typeof Notification === 'undefined') return 'unsupported';
    return Notification.permission; // 'default' | 'granted' | 'denied'
  }

  function maybeAskPermission() {
    if (!(window.cccPopups && window.cccPopups.allowed('notify-permission'))) return;
    var state = _permissionState();
    if (state !== 'default' || _permissionAsked) return;
    var count = parseInt(_lsGet(LS_ASK_COUNT) || '0', 10) || 0;
    if (count >= MAX_ASKS) return;
    var snoozeUntil = parseInt(_lsGet(LS_SNOOZE) || '0', 10) || 0;
    if (Date.now() < snoozeUntil) return;
    _permissionAsked = true;
    _lsSet(LS_ASK_COUNT, String(count + 1));
    _lsSet(LS_SNOOZE, String(Date.now() + SNOOZE_MS));
    _showPermissionCard();
  }

  function _showPermissionCard() {
    _injectStyles();
    var stack = _stack();
    var t = document.createElement('div');
    t.className = 'ccc-toast ccc-toast-kind-digest' + (_reducedMotion() ? '' : ' ccc-toast-in');
    t.setAttribute('role', 'dialog');
    t.setAttribute('aria-label', 'Turn on notifications');

    var bar = document.createElement('div');
    bar.className = 'ccc-toast-bar';
    t.appendChild(bar);

    var body = document.createElement('div');
    body.className = 'ccc-toast-body';
    var title = document.createElement('p');
    title.className = 'ccc-toast-title';
    title.textContent = '☰  Get a ping when agents finish';
    body.appendChild(title);
    var text = document.createElement('p');
    text.className = 'ccc-toast-text';
    text.textContent = 'Step away while your agents work. We will tell you the moment something finishes or needs you.';
    body.appendChild(text);
    var actions = document.createElement('div');
    actions.className = 'ccc-toast-actions';
    var yes = document.createElement('button');
    yes.type = 'button';
    yes.className = 'ccc-toast-btn ccc-toast-btn-primary';
    yes.textContent = 'Turn on notifications';
    var no = document.createElement('button');
    no.type = 'button';
    no.className = 'ccc-toast-btn';
    no.textContent = 'Not now';
    actions.appendChild(yes);
    actions.appendChild(no);
    body.appendChild(actions);
    t.appendChild(body);

    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'ccc-toast-close';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '×';
    t.appendChild(close);

    function drop() { if (t.parentNode) t.parentNode.removeChild(t); }
    close.addEventListener('click', drop);
    no.addEventListener('click', drop);
    yes.addEventListener('click', function () {
      drop();
      requestPermission();
    });
    stack.appendChild(t);
  }

  function requestPermission() {
    var state = _permissionState();
    if (state !== 'default') {
      refreshSettings();
      return Promise.resolve(state);
    }
    return Notification.requestPermission().then(function (result) {
      if (result === 'granted') {
        toast({
          kind: 'success',
          title: 'Notifications on',
          body: 'We will ping you when an agent finishes or needs you.',
        });
      }
      refreshSettings();
      return result;
    }).catch(function () {
      refreshSettings();
      return _permissionState();
    });
  }

  // ── pending poll (SSE catch-up) ───────────────────────────────────────

  var _pollInFlight = false;
  function pollPending() {
    if (_pollInFlight) return;
    _pollInFlight = true;
    var cursor = _lsGet(LS_CURSOR) || '';
    fetch('/api/notify/pending' + (cursor ? ('?since=' + encodeURIComponent(cursor)) : ''))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        (data.items || []).forEach(function (item) { _deliver(item); });
        if (data.latest) _lsSet(LS_CURSOR, String(data.latest));
      })
      .catch(function () {})
      .finally(function () { _pollInFlight = false; });
  }

  // ── settings wiring ───────────────────────────────────────────────────

  var _PREFS = ['enabled', 'tasks', 'digest', 'milestones'];

  function refreshSettings() {
    _PREFS.forEach(function (name) {
      var btn = document.querySelector('[data-notify-pref="' + name + '"]');
      if (!btn) return;
      var on = pref(name);
      btn.classList.toggle('is-on', on);
      btn.setAttribute('aria-checked', String(on));
    });
    var status = document.getElementById('notifyBrowserPermStatus');
    if (status) {
      var state = _permissionState();
      status.textContent =
        state === 'granted' ? 'On in this browser' :
        state === 'denied' ? 'Blocked by the browser' :
        state === 'unsupported' ? 'Not supported here' :
        'Not asked yet';
    }
    var permBtn = document.getElementById('notifyBrowserPermBtn');
    if (permBtn) {
      var st = _permissionState();
      permBtn.textContent = st === 'granted' ? 'Send a test' : 'Turn on';
      permBtn.disabled = st === 'denied' || st === 'unsupported';
      permBtn.title = st === 'denied'
        ? 'Notifications are blocked — allow them for this site in your browser settings.'
        : '';
    }
  }

  function _bindSettings() {
    // One delegated listener covers the whole modal — no app.js wiring needed.
    document.addEventListener('click', function (e) {
      var prefBtn = e.target.closest('[data-notify-pref]');
      if (prefBtn) {
        var name = prefBtn.getAttribute('data-notify-pref');
        setPref(name, !pref(name));
        return;
      }
      var permBtn = e.target.closest('#notifyBrowserPermBtn');
      if (permBtn) {
        if (_permissionState() === 'granted') {
          _deliver({
            id: 'ntf_test_' + Date.now(), kind: 'success',
            title: 'Test notification',
            body: 'This is how agent pings will look.',
          });
        } else {
          requestPermission();
        }
      }
    }, true);
    // Sync the toggles whenever the settings modal becomes visible.
    var modal = document.getElementById('settingsModal');
    if (modal && typeof MutationObserver === 'function') {
      new MutationObserver(function () {
        if (!modal.hidden) refreshSettings();
      }).observe(modal, { attributes: true, attributeFilter: ['hidden'] });
    }
  }

  // ── public API + boot ─────────────────────────────────────────────────

  function show(item) {
    if (typeof item === 'string') item = { title: item, body: arguments[1] || '', kind: arguments[2] || 'info' };
    item = Object.assign({ kind: 'info', ts: Date.now() / 1000 }, item || {});
    if (!item.id) item.id = 'ntf_local_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    return _deliver(item);
  }

  window.cccNotify = Object.assign(show, {
    show: show,
    toast: toast,
    enabled: enabled,
    pref: pref,
    setPref: setPref,
    requestPermission: requestPermission,
    maybeAskPermission: maybeAskPermission,
    permissionState: _permissionState,
    status: function () {
      return {
        enabled: enabled(),
        permission: _permissionState(),
        serviceWorker: !!navigator.serviceWorker,
        nativeBridge: !!(window.webkit && window.webkit.messageHandlers &&
          window.webkit.messageHandlers.cccNotify),
      };
    },
    refreshSettings: refreshSettings,
    pollPending: pollPending,
    _deliver: _deliver,
    _toast: toast,
  });

  function _boot() {
    _bindSettings();
    refreshSettings();
    // Catch up on anything that fired while this tab was closed, then keep a
    // slow poll as the SSE fallback. First poll waits a beat so the SSE
    // replay (which is usually faster) wins the dedupe race.
    setTimeout(pollPending, 4000);
    setInterval(pollPending, 60000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _boot);
  } else {
    _boot();
  }
})();
