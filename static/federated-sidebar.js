// Federated sidebar: sessions from paired peers (other machines) shown in the
// conversation list, opened embedded from the peer's own CCC.
//
// Peer rows come from GET /api/sessions?federated=1&peers_only=1, fetched on
// its own timer so the local list never waits on the network (the server caches
// one call per peer per TTL). Each row is identified by its global ref
// (<node_id>:<session_id>). Clicking a row embeds the peer's single-conversation
// popout (<web_url>/?ccc_popout=conversation&conv=<session_id>) over the main
// pane. A peer without a browser-reachable address shows why and is not openable.
(function () {
  'use strict';
  var params = new URLSearchParams(window.location.search || '');
  if (params.get('ccc_popout') || params.get('popout')) return; // never nest inside a popout frame

  var POLL_MS = 15000;      // matches the server's ~10s per-peer cache
  var IDLE_POLL_MS = 60000; // nothing paired: keep the interval long
  var PER_NODE_CAP = 5;
  var FETCH_TIMEOUT_MS = 25000;
  var ROW_BG_KEY = 'ccc-fed-row-bg';
  var ROW_BG_DEFAULT = 'plum';   // conversation-background palette id
  var COLLAPSE_KEY = 'ccc-fed-collapsed';

  var state = { nodes: [], rows: [] };
  var expanded = false;
  var collapsed = false;
  try { collapsed = localStorage.getItem(COLLAPSE_KEY) === '1'; } catch (_) {}
  var openRef = '';
  var lastSig = null;
  var sectionEl = null;   // built once; re-attached (never rebuilt) when the local list redraws
  var pollTimer = null;
  var polling = false;

  function $(id) { return document.getElementById(id); }

  function rowEpoch(r) {
    var t = r && r.timestamp;
    if (typeof t === 'number') return t;
    if (typeof t === 'string' && t) {
      var n = Number(t);
      if (isFinite(n)) return n;
      var d = Date.parse(t);
      if (isFinite(d)) return d / 1000;
    }
    return 0;
  }

  function ago(epoch) {
    if (!epoch) return '';
    var s = Math.max(0, Date.now() / 1000 - epoch);
    if (s < 90) return 'now';
    if (s < 3600) return Math.round(s / 60) + 'm';
    if (s < 86400) return Math.round(s / 3600) + 'h';
    return Math.round(s / 86400) + 'd';
  }

  function nodeById(id) {
    for (var i = 0; i < state.nodes.length; i++) if (state.nodes[i].node_id === id) return state.nodes[i];
    return null;
  }

  // Why a row can't be opened, or '' when it can.
  function blockedReason(node) {
    if (!node) return 'unknown machine';
    var st = node.web_url_state;
    if (st === 'ok' && node.web_url) return '';
    if (st === 'pin') return 'PIN-protected: cannot be embedded';
    if (st === 'unknown') return 'looking up web address…';
    return 'no web address for this machine';
  }

  function nodeNote(node) {
    if (!node) return '';
    if (node.ok === false) return 'offline';
    if (node.stale) return 'stale';
    return '';
  }

  function buildEmbedUrl(node, row) {
    var u = new URL(node.web_url);
    u.searchParams.set('ccc_popout', 'conversation');
    u.searchParams.set('conv', row.session_id);
    u.searchParams.set('conv_bg', rowBgId());
    return u.toString();
  }

  function searchValue() {
    var el = $('convSearch');
    return ((el && el.value) || '').trim().toLowerCase();
  }

  // Gate on the COMMITTED tab (.conv-tab.is-active), not raw localStorage:
  // the key is shared across same-origin windows and transiently written by
  // the mobile bottom-nav correction, so reading it here flashed the section
  // in for one render pass while the list body stayed on another tab.
  function sidebarTabIsCoding() {
    var list = $('convList');
    var active = list && list.querySelector('.conv-tab.is-active');
    var t = active ? active.getAttribute('data-conv-tab') : null;
    if (t == null) {
      try { t = localStorage.getItem('ccc-sidebar-tab'); } catch (_) {}
    }
    return !t || t === 'coding';
  }

  function rowTitle(r) {
    return r.display_name || r.first_message || String(r.session_id || '').slice(0, 8);
  }

  function visibleRows() {
    var q = searchValue();
    var rows = state.rows.filter(function (r) {
      if (!r.ref || !r.session_id) return false;
      if (!q) return true;
      return (rowTitle(r) + ' ' + (r.first_message || '') + ' ' + (r.cwd || '') + ' '
        + (r.node_name || '') + ' ' + r.session_id).toLowerCase().indexOf(q) !== -1;
    });
    if (expanded || q) return rows;
    var seen = {};
    return rows.filter(function (r) {
      seen[r.node_id] = (seen[r.node_id] || 0) + 1;
      return seen[r.node_id] <= PER_NODE_CAP;
    });
  }

  // Peer rows (and the embedded conversation) get their own background so they
  // never read as local sessions. The pick comes from the same palette as the
  // conversation background (window.cccConvBg, set by app.js), per browser.
  function palette() {
    return (window.cccConvBg && window.cccConvBg.palette) || [];
  }

  function rowBgId() {
    var id = '';
    try { id = localStorage.getItem(ROW_BG_KEY) || ''; } catch (_) {}
    var pal = palette();
    if (pal.length) return pal.some(function (p) { return p.id === id; }) ? id : ROW_BG_DEFAULT;
    return /^[a-z]{2,20}$/.test(id) ? id : ROW_BG_DEFAULT;
  }

  function applyRowBg() {
    var v = window.cccConvBg ? window.cccConvBg.vars(rowBgId()) : null;
    var root = document.documentElement.style;
    if (!v) return;
    root.setProperty('--fed-row-bg', v.bg);
    root.setProperty('--fed-row-text', v.text);
    root.setProperty('--fed-row-muted', v.muted);
    root.setProperty('--fed-row-chip', v.surface2);
    root.setProperty('--fed-row-border', v.border);
  }

  function closePalette() {
    var pop = $('fedBgPalette');
    if (pop) pop.remove();
    document.removeEventListener('mousedown', onOutside, true);
    document.removeEventListener('keydown', onPaletteKey, true);
  }

  function onOutside(ev) {
    var pop = $('fedBgPalette');
    if (pop && !pop.contains(ev.target) && !(ev.target.closest && ev.target.closest('.fed-color'))) closePalette();
  }

  function onPaletteKey(ev) { if (ev.key === 'Escape') closePalette(); }

  // Lives on <body>, not in the list, so list redraws can never close it.
  function openPalette(anchor) {
    if ($('fedBgPalette')) { closePalette(); return; }
    var pop = el('div', 'conv-bg-palette fed-bg-popover');
    pop.id = 'fedBgPalette';
    pop.setAttribute('role', 'radiogroup');
    pop.setAttribute('aria-label', 'Background for sessions on other machines');
    var current = rowBgId();
    palette().forEach(function (item) {
      var b = el('button', 'conv-bg-swatch' + (item.id === current ? ' active' : ''));
      b.type = 'button';
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', String(item.id === current));
      b.setAttribute('aria-label', item.label);
      b.title = item.label;
      b.style.setProperty('--swatch-color', item.bg);
      b.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        try { localStorage.setItem(ROW_BG_KEY, item.id); } catch (_) {}
        applyRowBg();
        closePalette();
        resetFrames();   // the color is a URL param: reload the kept frames
        if (openRef) openRow(openRef);
      });
      pop.appendChild(b);
    });
    document.body.appendChild(pop);
    var r = anchor.getBoundingClientRect();
    pop.style.top = Math.round(r.bottom + 6) + 'px';
    pop.style.left = Math.max(8, Math.round(r.right - pop.offsetWidth)) + 'px';
    document.addEventListener('mousedown', onOutside, true);
    document.addEventListener('keydown', onPaletteKey, true);
  }

  function buildSection() {
    var sec = el('div', 'fed-peer-section');
    sec.setAttribute('data-role', 'fed-peer-section');
    var head = el('div', 'fed-head');
    head.setAttribute('data-role', 'fed-toggle');
    head.setAttribute('role', 'button');
    head.tabIndex = 0;
    head.appendChild(el('span', 'fed-caret'));
    head.appendChild(el('span', 'fed-head-label', 'Other machines'));
    head.appendChild(el('span', 'fed-head-count'));
    var pick = el('button', 'fed-color');
    pick.type = 'button';
    pick.title = 'Background color for sessions on other machines';
    pick.setAttribute('aria-label', pick.title);
    head.appendChild(pick);
    sec.appendChild(head);
    sec.appendChild(el('div', 'fed-body'));
    return sec;
  }

  function updateHead(count) {
    var head = sectionEl.querySelector('.fed-head');
    head.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    head.title = collapsed ? 'Show sessions on other machines' : 'Hide sessions on other machines';
    head.querySelector('.fed-caret').textContent = collapsed ? '\u25b8' : '\u25be';
    var c = head.querySelector('.fed-head-count');
    c.textContent = collapsed && count ? String(count) : '';
    c.hidden = !(collapsed && count);
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function buildRow(r) {
    var node = nodeById(r.node_id);
    var reason = blockedReason(node);
    var note = nodeNote(node);
    var row = el('div', 'fed-row' + (note ? ' is-offline' : '') + (reason ? ' is-blocked' : '')
      + (openRef === r.ref ? ' active' : ''));
    row.setAttribute('data-id', r.ref);
    row.setAttribute('data-node-id', r.node_id || '');
    row.setAttribute('role', 'button');
    row.tabIndex = 0;
    row.title = reason || ('Open on ' + (r.node_name || 'peer'));
    row.appendChild(el('span', 'fed-dot' + (r.is_live ? ' is-live' : '')));
    var main = el('div', 'fed-main');
    main.appendChild(el('div', 'fed-title', rowTitle(r)));
    var sub = el('div', 'fed-sub');
    sub.appendChild(el('span', 'fed-chip', r.node_name || (node && node.name) || 'peer'));
    if (note) sub.appendChild(el('span', 'fed-note', note));
    if (reason) sub.appendChild(el('span', 'fed-note', reason));
    else if (r.cwd) sub.appendChild(el('span', 'fed-cwd', String(r.cwd).replace(/\/+$/, '').split('/').pop()));
    main.appendChild(sub);
    row.appendChild(main);
    row.appendChild(el('span', 'fed-time', ago(rowEpoch(r))));
    return row;
  }

  // A peer that answered with no sessions, or is down, still says so.
  function buildNodeRow(node) {
    var reason = node.ok === false ? 'offline' : '';
    if (!reason) return null;
    var row = el('div', 'fed-row is-offline is-blocked');
    row.setAttribute('data-id', 'node:' + node.node_id);
    row.appendChild(el('span', 'fed-dot'));
    var main = el('div', 'fed-main');
    main.appendChild(el('div', 'fed-title', 'No sessions available'));
    var sub = el('div', 'fed-sub');
    sub.appendChild(el('span', 'fed-chip', node.name || 'peer'));
    sub.appendChild(el('span', 'fed-note', reason));
    main.appendChild(sub);
    row.appendChild(main);
    return row;
  }

  function render() {
    var list = $('convList');
    if (!list) return;
    var peers = state.nodes.filter(function (n) { return !n.self; });
    var rows = (peers.length && sidebarTabIsCoding()) ? visibleRows() : [];
    var offlineOnly = peers.filter(function (n) {
      return n.ok === false && !state.rows.some(function (r) { return r.node_id === n.node_id; });
    });
    var hasContent = rows.length || (offlineOnly.length && !searchValue());
    if (!hasContent) {
      if (sectionEl && sectionEl.parentNode) sectionEl.remove();
      return;
    }
    if (!sectionEl) sectionEl = buildSection();
    var totalHidden = state.rows.length - rows.length;
    var sig = JSON.stringify([
      rows.map(function (r) { return [r.ref, rowTitle(r), ago(rowEpoch(r)), !!r.is_live, r.node_name, r.cwd]; }),
      peers.map(function (n) { return [n.node_id, n.ok, n.stale, n.web_url_state, n.web_url]; }),
      openRef, expanded, collapsed, totalHidden > 0 && !searchValue()]);
    if (sig !== lastSig) {
      lastSig = sig;
      updateHead(state.rows.length);
      var body = [];
      if (!collapsed) rows.forEach(function (r) { body.push(buildRow(r)); });
      if (!collapsed && !searchValue()) {
        offlineOnly.forEach(function (n) { var b = buildNodeRow(n); if (b) body.push(b); });
      }
      if (!collapsed && !searchValue() && (totalHidden > 0 || expanded) && state.rows.length > PER_NODE_CAP) {
        var more = el('button', 'fed-more', expanded ? 'Show fewer' : 'Show ' + totalHidden + ' more');
        more.type = 'button';
        more.setAttribute('data-role', 'fed-more');
        body.push(more);
      }
      sectionEl.querySelector('.fed-body').replaceChildren.apply(sectionEl.querySelector('.fed-body'), body);
    }
    // The local list redraws wholesale; put the SAME element back (no rebuild,
    // so nothing flickers) right after the tab bar.
    var tabBar = list.querySelector(':scope > .conv-tab-bar');
    var placed = sectionEl.parentNode === list
      && (tabBar ? sectionEl.previousElementSibling === tabBar : list.firstElementChild === sectionEl);
    if (placed) return;
    if (tabBar) tabBar.insertAdjacentElement('afterend', sectionEl);
    else list.insertBefore(sectionEl, list.firstChild);
  }

  // ---- embed ---------------------------------------------------------------

  // One frame per machine, kept alive while hidden: booting the peer's app
  // costs 1.5-3s, so after the first open a switch is a postMessage asking the
  // same frame to show another conversation. A peer whose CCC predates that
  // never answers the hello and keeps getting a full reload per click.
  var frames = {};   // node_id -> { frame, origin, live }

  function embedShowing() {
    var host = $('fedEmbed');
    return !!(host && !host.hidden);
  }

  function mobileNav() {
    var nav = window.cccMobileNav;
    return nav && nav.isMobile() ? nav : null;
  }

  // Desktop: over the main pane. Phone: local conversations open in the
  // slide-up .conv-panel (split mode), so peer ones must too or they land in
  // the off-screen main area.
  function embedContainer() {
    if (mobileNav() && document.body.classList.contains('kanban-split')) {
      var panel = document.querySelector('.conv-panel');
      if (panel) return panel;
    }
    return document.querySelector('.main');
  }

  // userClose: the embed's own Close button. On a phone it also slides back to
  // the list; a local row opening (which closes the embed) must not.
  function closeEmbed(userClose) {
    var host = $('fedEmbed');
    var wasShowing = embedShowing();
    if (host) host.hidden = true;
    if (host && host.parentElement) host.parentElement.classList.remove('fed-embed-host');
    openRef = '';
    var nav = mobileNav();
    if (nav && wasShowing && userClose === true) nav.back();
    render();
  }

  // Drop every kept frame (e.g. after a color change: the color rides the URL).
  function resetFrames() {
    Object.keys(frames).forEach(function (k) { frames[k].frame.remove(); });
    frames = {};
  }

  function ensureHost(main) {
    var host = $('fedEmbed');
    if (host) {
      if (host.parentElement !== main) main.appendChild(host); // viewport crossed the phone breakpoint
      return host;
    }
    host = el('div', 'fed-embed');
    host.id = 'fedEmbed';
    var bar = el('div', 'fed-embed-bar');
    bar.appendChild(el('span', 'fed-chip'));
    bar.appendChild(el('span', 'fed-embed-title'));
    var close = el('button', 'fed-embed-close', 'Close');
    close.type = 'button';
    close.setAttribute('data-role', 'fed-embed-close');
    close.addEventListener('click', function () { closeEmbed(true); });
    bar.appendChild(close);
    host.appendChild(bar);
    main.appendChild(host);
    return host;
  }

  function frameFor(host, node, url) {
    var entry = frames[node.node_id];
    if (entry && entry.origin === new URL(url).origin) return entry;
    if (entry) entry.frame.remove();
    var frame = document.createElement('iframe');
    frame.className = 'fed-embed-frame';
    frame.setAttribute('allow', 'clipboard-read; clipboard-write');
    entry = { frame: frame, origin: new URL(url).origin, live: false };
    frame.addEventListener('load', function () {
      entry.live = false;
      try { frame.contentWindow.postMessage({ type: 'ccc:embed-hello' }, entry.origin); } catch (_) {}
    });
    host.appendChild(frame);
    frames[node.node_id] = entry;
    return entry;
  }

  function onFrameMessage(ev) {
    var d = ev.data;
    if (!d || typeof d !== 'object' || d.type !== 'ccc:embed-hello') return;
    Object.keys(frames).forEach(function (k) {
      var e = frames[k];
      if (e.frame.contentWindow === ev.source && e.origin === ev.origin) e.live = true;
    });
  }

  function openRow(ref) {
    var row = null;
    for (var i = 0; i < state.rows.length; i++) if (state.rows[i].ref === ref) row = state.rows[i];
    if (!row) return;
    var node = nodeById(row.node_id);
    if (blockedReason(node)) return;
    var main = embedContainer();
    if (!main) return;
    var host = ensureHost(main);
    host.hidden = false;
    main.classList.add('fed-embed-host');
    host.querySelector('.fed-chip').textContent = row.node_name || node.name || 'peer';
    host.querySelector('.fed-embed-title').textContent = rowTitle(row);
    var url = buildEmbedUrl(node, row);
    var entry = frameFor(host, node, url);
    var f = entry.frame;
    Object.keys(frames).forEach(function (k) { frames[k].frame.hidden = frames[k] !== entry; });
    f.title = 'Conversation on ' + (row.node_name || 'peer');
    if (entry.live && f.contentWindow) {
      f.contentWindow.postMessage({ type: 'ccc:embed-open', conv: row.session_id }, entry.origin);
      f.setAttribute('data-conv', row.session_id);
    } else if (f.getAttribute('data-conv') !== row.session_id || !f.getAttribute('src')) {
      f.setAttribute('data-conv', row.session_id);
      f.setAttribute('src', url);
    }
    openRef = row.ref;
    var nav = mobileNav();
    if (nav) nav.show();
    render();
  }

  function toggleCollapsed() {
    collapsed = !collapsed;
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0'); } catch (_) {}
    render();
  }

  function wire() {
    var list = $('convList');
    if (!list) return;
    list.addEventListener('click', function (ev) {
      var swatch = ev.target.closest && ev.target.closest('.fed-color');
      if (swatch) { ev.stopPropagation(); openPalette(swatch); return; }
      if (ev.target.closest && ev.target.closest('[data-role="fed-toggle"]')) { toggleCollapsed(); return; }
      var more = ev.target.closest && ev.target.closest('[data-role="fed-more"]');
      if (more) { expanded = !expanded; render(); return; }
      var row = ev.target.closest && ev.target.closest('.fed-row[data-node-id]');
      if (row) openRow(row.getAttribute('data-id'));
    });
    list.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      if (ev.target.getAttribute && ev.target.getAttribute('data-role') === 'fed-toggle') {
        ev.preventDefault(); toggleCollapsed(); return;
      }
      var row = ev.target.closest && ev.target.closest('.fed-row[data-node-id]');
      if (row) { ev.preventDefault(); openRow(row.getAttribute('data-id')); }
    });
    // The local list re-renders wholesale (innerHTML); put the section back.
    new MutationObserver(function () { render(); }).observe(list, { childList: true });
    var search = $('convSearch');
    if (search) search.addEventListener('input', render);
    // Opening any local conversation dismisses the embed.
    window.addEventListener('ccc:conversation-selected', function () { if (embedShowing()) closeEmbed(); });
    window.addEventListener('message', onFrameMessage);
  }

  // ---- polling -------------------------------------------------------------

  function schedule(ms) {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, ms);
  }

  function poll() {
    if (polling) return;
    if (document.hidden) { schedule(POLL_MS); return; }
    polling = true;
    var ctl = new AbortController();
    var to = setTimeout(function () { ctl.abort(); }, FETCH_TIMEOUT_MS);
    fetch('/api/sessions?federated=1&peers_only=1&limit=60', { cache: 'no-store', signal: ctl.signal })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && Array.isArray(d.nodes)) {
          state.nodes = d.nodes;
          // Peer rows only: the local list already carries this machine's own.
          state.rows = (d.sessions || []).filter(function (r) {
            var n = nodeById(r.node_id);
            return n && !n.self;
          });
          render();
        }
        var hasPeers = state.nodes.some(function (n) { return !n.self; });
        schedule(hasPeers ? POLL_MS : IDLE_POLL_MS);
      })
      .catch(function () { schedule(POLL_MS); })
      .then(function () { clearTimeout(to); polling = false; });
  }

  function boot() {
    applyRowBg();
    wire();
    poll();
    document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
    window.addEventListener('storage', function (e) { if (e.key === 'ccc-sidebar-tab') render(); });
    window.cccFederatedSidebar = { render: render, state: function () { return state; }, open: openRow, close: closeEmbed, frames: function () { return frames; } };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
