/* On-call home (preview flag `oncall`).
 *
 * With On-call on, a desktop window opens on one quiet screen: what needs
 * you, what is being worked on, what got fixed this week. It is the phone's
 * three answers, bigger. The full dashboard is one click away and a rail
 * button brings this screen back.
 *
 * Nothing here runs until the flag is on: no element, no request. All data
 * comes from GET /api/oncall/home and is redrawn while the screen is open, so
 * there is nothing to refresh by hand. Text is set with textContent only.
 *
 * Opening rules: only a plain dashboard URL on a wide window opens it by
 * itself (phones and narrow windows keep their own layout, deep links keep
 * their target). `?oncall-home=1` opens it anywhere. "Open the full
 * dashboard" closes it for this tab; the Settings switch closes it for good.
 */
(() => {
  'use strict';

  const PREF_KEY = 'ccc-oncall-home';          // 'off' = never open by itself
  const CLOSED_KEY = 'ccc-oncall-home-closed'; // this tab chose the full dashboard
  const SEEN_KEY = 'ccc-oncall-home-seen';     // it opened here last time, so open at once
  const OPEN_MS = 10000, IDLE_MS = 60000, WIDE_PX = 1200;
  const CHANNELS = {email: 'email', telegram: 'Telegram', ntfy: 'your phone (ntfy)', imessage: 'iMessage',
    sms: 'text message', whatsapp: 'WhatsApp', command: 'your own command', slack: 'Slack',
    discord: 'Discord', matrix: 'Matrix', mattermost: 'Mattermost', call: 'a phone call'};
  const SOURCES = {github: 'GitHub issues', ci: 'failed builds', sentry: 'Sentry', posthog: 'PostHog',
    http: 'a URL check', command: 'a command check', docker: 'containers', logs: 'log files',
    alertmanager: 'Alertmanager', glitchtip: 'GlitchTip'};

  let root = null, parts = null, data = null, timer = null, loading = false, railButton = null;
  const sending = new Set();
  const drafts = new Map(), cardNotes = new Map();

  const flagOn = () => { try { return !!(window.__CCC_FLAGS__ || {}).oncall; } catch (_) { return false; } };
  const flagsKnown = () => typeof window.__CCC_FLAGS__ === 'object' && window.__CCC_FLAGS__ !== null;
  const local = () => ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);
  const stored = (store, key) => { try { return store.getItem(key); } catch (_) { return null; } };
  const store = (target, key, value) => {
    try { if (value === null) target.removeItem(key); else target.setItem(key, value); } catch (_) { /* private mode */ }
  };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  async function api(method, name, body) {
    const response = await fetch('/api/oncall/' + name, {
      method, headers: {'Content-Type': 'application/json'},
      body: method === 'POST' ? JSON.stringify(body || {}) : undefined,
    });
    let result = {};
    try { result = await response.json(); } catch (_) { /* a non-JSON error page */ }
    if (!response.ok || result.ok === false) throw new Error(result.error || 'CCC answered HTTP ' + response.status);
    return result;
  }

  // ── words ─────────────────────────────────────────────────────────────
  function clock(seconds) {
    const when = new Date(seconds * 1000), now = new Date();
    const time = when.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
    if (when.toDateString() === now.toDateString()) return time;
    return when.toLocaleDateString([], {weekday: 'short'}) + ' ' + time;
  }

  function day(seconds) {
    const when = new Date(seconds * 1000), now = new Date();
    if (when.toDateString() === now.toDateString()) return 'today';
    return when.toLocaleDateString([], {weekday: 'long'});
  }

  function span(seconds) {
    const minutes = Math.max(1, Math.round(seconds / 60));
    if (minutes < 90) return minutes + (minutes === 1 ? ' minute' : ' minutes');
    const hours = Math.round(minutes / 60);
    if (hours < 36) return hours + ' hours';
    return Math.round(hours / 24) + ' days';
  }

  function hour(text) {
    const m = /^(\d{1,2}):(\d{2})$/.exec(text || '');
    if (!m) return text || '';
    const h = Number(m[1]), suffix = h < 12 ? 'AM' : 'PM';
    return (h % 12 || 12) + (m[2] === '00' ? '' : ':' + m[2]) + ' ' + suffix;
  }

  const names = (keys, table) => keys.map(key => table[key] || key).join(', ');

  // ── the screen ────────────────────────────────────────────────────────
  function column(title) {
    const node = el('section', 'oncall-home-col');
    const head = el('h2', 'oncall-home-h', title);
    const count = el('span', 'oncall-home-count');
    head.append(count);
    const list = el('div', 'oncall-home-list');
    node.append(head, list);
    return {node, count, list};
  }

  function build() {
    const node = el('div', 'oncall-home');
    node.id = 'oncallHome';
    node.hidden = true;
    node.setAttribute('role', 'region');
    node.setAttribute('aria-label', 'On-call');
    const p = {};

    const top = el('header', 'oncall-home-top');
    p.state = el('span', 'oncall-home-state');
    p.full = el('button', 'oncall-home-full', 'Open the full dashboard →');
    p.full.type = 'button';
    p.full.addEventListener('click', () => close(true));
    top.append(el('span', 'oncall-home-brand', 'Command Center'), p.state, p.full);

    p.note = el('div', 'oncall-home-note');
    p.note.setAttribute('role', 'status');

    const cols = el('div', 'oncall-home-cols');
    p.needs = column('Needs you');
    p.working = column('Being worked on');
    p.fixed = column('Fixed this week');
    cols.append(p.needs.node, p.working.node, p.fixed.node);

    p.foot = el('footer', 'oncall-home-foot');
    const inner = el('div', 'oncall-home-inner');
    inner.append(top, p.note, cols, p.foot);
    node.append(inner);
    return {node, p};
  }

  function fact(label, value) {
    const node = el('span', 'oncall-home-fact', label + ' ');
    node.append(el('b', '', value));
    return node;
  }

  function empty(list, text) { list.append(el('div', 'oncall-home-empty', text)); }

  function needCard(item) {
    const card = el('div', 'oncall-home-card is-you');
    card.dataset.alert = String(item.id);
    card.append(el('div', 'oncall-home-who', item.headline));
    if (item.detail) {
      // The first paragraph is the question; anything after it is background.
      const cut = item.detail.indexOf('\n\n');
      card.append(el('div', 'oncall-home-ask', cut < 0 ? item.detail : item.detail.slice(0, cut)));
      if (cut >= 0) card.append(el('div', 'oncall-home-more', item.detail.slice(cut + 2)));
    }
    if (item.reply_help) card.append(el('div', 'oncall-home-more', item.reply_help));
    const busy = sending.has(item.id);
    const say = el('div', 'oncall-home-said');
    say.setAttribute('role', 'status');
    const previous = cardNotes.get(item.id);
    if (previous) { say.textContent = previous; say.classList.add('is-error'); }
    const send = async text => {
      if (sending.has(item.id)) return;
      if (!text) {
        say.textContent = 'Type an answer first.';
        say.classList.add('is-error');
        cardNotes.set(item.id, say.textContent);
        const input = card.querySelector('input');
        if (input) { input.setAttribute('aria-invalid', 'true'); input.focus(); }
        return;
      }
      cardNotes.delete(item.id);
      say.textContent = '';
      sending.add(item.id);
      card.classList.add('is-sending');
      card.querySelectorAll('button, input').forEach(control => { control.disabled = true; });
      try {
        const result = await api('POST', 'answer', {id: item.id, text});
        drafts.delete(item.id);
        note(result.message || 'Sent to the agent.');
        sending.delete(item.id);
        await load();
      } catch (error) {
        sending.delete(item.id);
        card.classList.remove('is-sending');
        card.querySelectorAll('button, input').forEach(control => { control.disabled = false; });
        say.textContent = error.message;
        say.classList.add('is-error');
        cardNotes.set(item.id, error.message);
      }
    };
    if (item.session_only) {
      const open = el('button', 'oncall-home-btn is-main', 'Open the session');
      open.type = 'button';
      open.addEventListener('click', () => {
        close(false);
        if (item.session_id && typeof window.cccOpenSession === 'function') window.cccOpenSession(item.session_id);
      });
      const acts = el('div', 'oncall-home-acts');
      acts.append(open);
      card.append(acts);
    } else {
      if (item.choices.length) {
        const acts = el('div', 'oncall-home-acts');
        item.choices.forEach((choice, index) => {
          const button = el('button', 'oncall-home-btn' + (index === 0 ? ' is-main' : ''), choice.label);
          button.type = 'button';
          button.disabled = busy;
          button.addEventListener('click', () => send(choice.reply));
          acts.append(button);
        });
        card.append(acts);
      }
      if (item.free_text) {
        const form = el('form', 'oncall-home-type');
        const input = el('input', 'oncall-home-input');
        input.type = 'text';
        input.placeholder = item.choices.length ? 'Or type your answer' : 'Type your answer';
        input.setAttribute('aria-label', 'Your answer');
        input.value = drafts.get(item.id) || '';
        input.addEventListener('input', () => {
          if (input.value) drafts.set(item.id, input.value); else drafts.delete(item.id);
          input.removeAttribute('aria-invalid');
        });
        input.disabled = busy;
        const go = el('button', 'oncall-home-btn', 'Send');
        go.type = 'submit';
        go.disabled = busy;
        form.append(input, go);
        form.addEventListener('submit', event => { event.preventDefault(); send(input.value.trim()); });
        card.append(form);
      }
    }
    if (!item.session_only && item.session_id && typeof window.cccOpenSession === 'function') {
      const look = el('button', 'oncall-home-look', 'Open the session');
      look.type = 'button';
      look.addEventListener('click', () => { close(false); window.cccOpenSession(item.session_id); });
      card.append(look);
    }
    card.append(say);
    return card;
  }

  function note(text) {
    if (!parts) return;
    parts.note.textContent = text || '';
    if (text) setTimeout(() => { if (parts.note.textContent === text) parts.note.textContent = ''; }, 6000);
  }

  // A refresh must not take keyboard focus from an answer control. Drafts
  // also survive refresh after focus moves elsewhere on the screen.
  const interacting = () => !!parts && parts.needs.list.contains(document.activeElement);

  function draw() {
    if (!parts || !data) return;
    const p = parts;
    p.state.textContent = data.paused ? 'On-call is paused' : 'On-call is on';
    p.state.classList.toggle('is-paused', !!data.paused);

    if (!interacting() && !sending.size) {
      p.needs.list.textContent = '';
      p.needs.count.textContent = data.needs_you.length ? String(data.needs_you.length) : '';
      if (!data.needs_you.length) empty(p.needs.list, 'Nothing needs you.');
      data.needs_you.forEach(item => p.needs.list.append(needCard(item)));
    }

    p.working.list.textContent = '';
    p.working.count.textContent = data.working_count ? String(data.working_count) : '';
    if (!data.working.length) empty(p.working.list, 'Nothing in progress.');
    data.working.forEach(item => {
      const card = el('div', 'oncall-home-card');
      card.append(el('div', 'oncall-home-title', item.title));
      card.append(el('div', 'oncall-home-sub', item.origin + ', ' + clock(item.created_at)
        + (item.waiting_on_you ? ' · waiting for your answer' : '')));
      p.working.list.append(card);
    });
    if (data.working_count > data.working.length) {
      empty(p.working.list, (data.working_count - data.working.length) + ' more');
    }

    p.fixed.list.textContent = '';
    p.fixed.count.textContent = data.fixed_count ? String(data.fixed_count) : '';
    if (!data.fixed.length) empty(p.fixed.list, 'Nothing yet this week.');
    data.fixed.forEach(item => {
      const card = el('div', 'oncall-home-card is-done');
      card.append(el('div', 'oncall-home-title', item.title));
      const what = (item.code_change ? 'Fixed ' : 'Closed without a code change ') + day(item.at);
      card.append(el('div', 'oncall-home-sub', what
        + (item.took !== null && item.took !== undefined ? ', ' + span(item.took) + ' after the report' : '')));
      p.fixed.list.append(card);
    });
    if (data.fixed_count > data.fixed.length) {
      empty(p.fixed.list, (data.fixed_count - data.fixed.length) + ' more this week');
    }

    p.foot.textContent = '';
    if (data.channels.length) p.foot.append(fact('Alerts go to', names(data.channels, CHANNELS)));
    if (data.quiet) p.foot.append(fact('Quiet', hour(data.quiet.start) + ' to ' + hour(data.quiet.end)));
    if (data.sources.length) p.foot.append(fact('Reports come from', names(data.sources, SOURCES)));
    if (local()) {
      const change = el('a', 'oncall-home-change', 'On-call settings');
      change.href = '/?ccc_settings=oncall';
      p.foot.append(change);
    }
  }

  // ── open, close, keep current ─────────────────────────────────────────
  const isOpen = () => !!root && !root.hidden;
  const available = () => flagOn() && !!data && data.enabled === true;

  function wantsToOpen() {
    const params = new URLSearchParams(window.location.search || '');
    if (params.get('oncall-home') === '1') return true;
    if (stored(localStorage, PREF_KEY) === 'off' || stored(sessionStorage, CLOSED_KEY) === '1') return false;
    if (window.innerWidth <= WIDE_PX || window.location.pathname !== '/') return false;
    for (const key of params.keys()) if (key !== 'ff') return false;   // a deep link keeps its target
    try { if (window.self !== window.top) return false; } catch (_) { return false; }
    return true;
  }

  function mount() {
    if (root) return;
    if (!document.getElementById('oncallHomeCss')) {
      const link = el('link');
      link.id = 'oncallHomeCss'; link.rel = 'stylesheet'; link.href = '/static/oncall-home.css';
      document.head.append(link);
    }
    const built = build();
    root = built.node; parts = built.p;
    document.body.append(root);
  }

  function show() {
    mount();
    root.hidden = false;
    document.body.classList.add('ccc-oncall-home-open');
  }

  function open() {
    if (!available()) return;
    store(sessionStorage, CLOSED_KEY, null);
    store(localStorage, SEEN_KEY, '1');
    show();
    draw();
    schedule();
  }

  // Never mind: On-call turned out to be off, so forget it opened here.
  function retract() {
    store(localStorage, SEEN_KEY, null);
    if (isOpen()) close(false);
  }

  function close(remember) {
    if (remember) store(sessionStorage, CLOSED_KEY, '1');
    if (root) root.hidden = true;
    document.body.classList.remove('ccc-oncall-home-open');
    schedule();
  }

  // The rail rebuilds its links whenever the app list changes; put the
  // button back afterwards so it survives.
  function placeRailButton() {
    const nav = document.querySelector('.ccc-side-nav nav') || document.querySelector('.ccc-side-nav');
    if (!nav) return;
    if (!available()) { if (railButton) railButton.remove(); return; }
    if (!railButton) {
      railButton = el('a', 'oncall-home-rail');
      railButton.href = '/?oncall-home=1';
      railButton.title = 'On-call: what needs you, what is being worked on, what got fixed';
      railButton.append(el('span', 'icon', '\u{1F6CE}️'), document.createTextNode('On-call'));
      railButton.addEventListener('click', event => {
        if (event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        open();
      });
    }
    if (railButton.parentNode !== nav || nav.firstChild !== railButton) nav.prepend(railButton);
  }

  async function load() {
    if (loading || !flagOn()) return;
    loading = true;
    try {
      const fresh = await api('GET', 'home');
      data = fresh;
      const ids = new Set((data.needs_you || []).map(item => item.id));
      for (const cache of [drafts, cardNotes]) for (const id of cache.keys()) if (!ids.has(id)) cache.delete(id);
      if (!data.enabled) retract();
      placeRailButton();
      if (isOpen()) draw();
    } catch (error) {
      if (!data) retract(); else if (isOpen()) note(error.message);
    } finally {
      loading = false;
    }
  }

  function schedule() {
    if (timer) { clearInterval(timer); timer = null; }
    if (!flagOn()) return;
    timer = setInterval(() => { if (document.visibilityState === 'visible') load(); }, isOpen() ? OPEN_MS : IDLE_MS);
  }

  let started = false;
  async function sync() {
    if (!flagsKnown()) return;                 // the flags arrive with their own event
    if (!flagOn()) {
      if (timer) { clearInterval(timer); timer = null; }
      retract();
      if (railButton) railButton.remove();
      return;
    }
    await load();
    if (!started && available()) {
      started = true;
      if (wantsToOpen()) open();
    }
    schedule();
  }

  window.addEventListener('ccc:flags', sync);
  window.addEventListener('ccc:apps', () => setTimeout(placeRailButton, 0));
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && isOpen()) load(); });
  // It opened here last time: show it at once instead of flashing the full
  // dashboard while the flags and the lists load. They confirm or retract it.
  if (stored(localStorage, SEEN_KEY) === '1' && wantsToOpen() && document.body) show();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', sync); else sync();

  window.cccOncallHome = {
    open, close: () => close(true), refresh: load, available,
    opensByItself: () => stored(localStorage, PREF_KEY) !== 'off',
    setOpensByItself: on => {
      store(localStorage, PREF_KEY, on ? null : 'off');
      if (on) store(sessionStorage, CLOSED_KEY, null);
    },
  };
})();
