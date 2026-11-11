/* Phone access wizard (preview flag `phone_access`).
 *
 * Shared by the dashboard (index.html: Settings > Fleet & Network > Phone
 * access…) and the Fleet page (fleet.html: one row per node with its phone
 * URL). Backend: /api/phone-access/* (ccc_server/phone_access.py). A nodeId
 * runs the same flow on that paired peer, over the federation route.
 *
 * The wizard re-polls status while open, so installing or logging in to
 * Tailscale in another window shows up here on its own.
 */
(function () {
  'use strict';
  if (window.cccPhoneAccess) return;

  const FLAG = 'phone_access';
  const POLL_MS = 4000;
  const LINKS = {
    download: 'https://tailscale.com/download',
    macAppStore: 'https://apps.apple.com/app/tailscale/id1475387142',
    ios: 'https://apps.apple.com/app/tailscale/id1470499037',
    android: 'https://play.google.com/store/apps/details?id=com.tailscale.ipn',
    acls: 'https://tailscale.com/kb/1018/acls',
    security: 'https://github.com/amirfish1/claude-command-center/blob/main/SECURITY.md#phone-access',
  };
  let flagsPromise = null;

  function flagOn() {
    try { return !!(window.__CCC_FLAGS__ || {})[FLAG]; } catch (_) { return false; }
  }
  function ensureFlags() {
    if (window.__CCC_FLAGS__ && FLAG in window.__CCC_FLAGS__) return Promise.resolve(flagOn());
    if (!flagsPromise) {
      flagsPromise = fetch('/api/features', { cache: 'no-store' })
        .then((r) => r.json())
        .then((f) => {
          window.__CCC_FLAGS__ = Object.assign({}, (f && f.preview) || {}, window.__CCC_FLAGS__ || {});
          try {
            new URLSearchParams(window.location.search || '').getAll('ff')
              .forEach((n) => { if (n) window.__CCC_FLAGS__[n] = true; });
          } catch (_) {}
          return flagOn();
        })
        .catch(() => false);
    }
    return flagsPromise;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  // Values below can come from a paired peer: only render a well-formed https
  // URL as a link and only an svg made of rect/path as the QR.
  function safeHttps(u) { return /^https:\/\/[A-Za-z0-9.-]+(:\d+)?\/?$/.test(String(u || '')) ? String(u) : ''; }
  function safeSvg(s) {
    s = String(s || '');
    return /^<svg [^<>]*>(<rect [^<>]*\/>)(<path [^<>]*\/>)<\/svg>$/.test(s) ? s : '';
  }
  function link(href, text) {
    return '<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + esc(text) + '</a>';
  }

  async function call(sub, body, nodeId) {
    const payload = Object.assign({}, body || {});
    if (nodeId) payload.node_id = nodeId;
    let r;
    if (sub === 'status') {
      const q = nodeId ? '?node_id=' + encodeURIComponent(nodeId) : '';
      r = await fetch('/api/phone-access/status' + q, { cache: 'no-store' });
    } else {
      r = await fetch('/api/phone-access/' + sub, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    }
    const d = await r.json().catch(() => ({}));
    if (!r.ok && d && d.ok === undefined) d.ok = false;
    return d || {};
  }

  function injectStyle() {
    if (document.getElementById('ccc-phone-style')) return;
    const st = document.createElement('style');
    st.id = 'ccc-phone-style';
    st.textContent = [
      '.ccc-phone-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:10050;display:flex;align-items:center;justify-content:center}',
      '.ccc-phone-box{background:var(--bg-elev,#1b1e24);color:var(--text,#e6e6e6);border:1px solid var(--border,#333);border-radius:10px;padding:18px 20px;width:min(620px,94vw);max-height:90vh;overflow-y:auto;font:13px/1.5 system-ui,-apple-system,sans-serif;box-shadow:0 12px 40px rgba(0,0,0,.4)}',
      '.ccc-phone-box h3{margin:0 0 4px;font-size:16px}',
      '.ccc-phone-box h4{margin:0 0 6px;font-size:13px;display:flex;gap:8px;align-items:center}',
      '.ccc-phone-box p{margin:4px 0;color:var(--text-dim,#aab)}',
      '.ccc-phone-box a{color:var(--accent,#d97757)}',
      '.ccc-phone-step{border:1px solid var(--border,#333);border-radius:8px;padding:10px 12px;margin-top:10px}',
      '.ccc-phone-step.is-off{opacity:.55}',
      '.ccc-phone-dot{width:18px;height:18px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11px;flex:none;border:1px solid var(--border,#555)}',
      '.ccc-phone-dot.ok{background:var(--success,#3fb950);border-color:var(--success,#3fb950);color:#fff}',
      '.ccc-phone-dot.err{background:var(--danger,#f07070);border-color:var(--danger,#f07070);color:#fff}',
      '.ccc-phone-checks{list-style:none;margin:4px 0;padding:0}',
      '.ccc-phone-checks li{margin:2px 0}',
      '.ccc-phone-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}',
      '.ccc-phone-row button{padding:6px 12px;border-radius:6px;border:1px solid var(--border,#444);background:transparent;color:inherit;cursor:pointer}',
      '.ccc-phone-row button.primary{background:var(--accent,#d97757);border-color:var(--accent,#d97757);color:#fff}',
      '.ccc-phone-row button:disabled{opacity:.5;cursor:default}',
      '.ccc-phone-row input{padding:6px 9px;border-radius:6px;border:1px solid var(--border,#444);background:var(--bg,#111);color:inherit;width:140px}',
      '.ccc-phone-url{font:12px ui-monospace,SFMono-Regular,monospace;background:var(--bg,#111);border:1px solid var(--border,#444);border-radius:6px;padding:6px 8px;word-break:break-all;flex:1;min-width:200px}',
      '.ccc-phone-qr{display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap}',
      '.ccc-phone-qr svg{width:180px;height:180px;border-radius:6px;flex:none}',
      '.ccc-phone-qr ol{margin:0;padding-left:18px;flex:1;min-width:220px}',
      '.ccc-phone-msg{margin-top:6px;min-height:1em}',
      '.ccc-phone-msg.err{color:var(--danger,#f07070)}',
      '.ccc-phone-msg.ok{color:var(--success,#3fb950)}',
      '.ccc-phone-warn{background:rgba(255,176,0,.08);border:1px solid rgba(255,176,0,.3);border-radius:8px;padding:8px 12px;margin-top:10px;font-size:12px}',
      '.ccc-phone-foot{display:flex;justify-content:flex-end;margin-top:12px}',
      '.fleet-phone-list{display:flex;flex-direction:column;gap:6px;margin:14px 0}',
      '.fleet-phone-list h3{margin:0 0 2px;font-size:13px}',
      '.fleet-phone-node{display:flex;gap:10px;align-items:center;flex-wrap:wrap;border:1px solid var(--border,#333);border-radius:8px;padding:6px 10px;font-size:12px}',
      '.fleet-phone-node .name{font-weight:600;min-width:120px}',
      '.fleet-phone-node .url{font-family:ui-monospace,monospace;word-break:break-all;flex:1;color:var(--accent,#58a6ff)}',
      '.fleet-phone-node .state.ok{color:var(--success,#3fb950)}',
      '.fleet-phone-node .state.err{color:var(--danger,#f07070)}',
      '.fleet-phone-node button{font-size:11px;padding:2px 9px;border-radius:9px;border:1px solid var(--border,#555);background:transparent;color:inherit;cursor:pointer}',
    ].join('\n');
    document.head.appendChild(st);
  }

  function dot(state) {
    if (state === true) return '<span class="ccc-phone-dot ok" aria-label="done">✓</span>';
    if (state === false) return '<span class="ccc-phone-dot err" aria-label="needs action">!</span>';
    return '<span class="ccc-phone-dot" aria-hidden="true"></span>';
  }

  function tailscaleStep(st) {
    const ts = st.tailscale || {};
    const ready = !!(ts.installed && ts.running && ts.hostname);
    const checks = [];
    checks.push('<li>' + (ts.installed ? '✓ Installed' : '✗ Not installed') + '</li>');
    if (ts.installed) {
      checks.push('<li>' + (ts.running ? '✓ Connected' : '✗ Not connected (state: ' + esc(ts.backend_state || 'stopped') + ')') + '</li>');
      if (ts.running) {
        checks.push('<li>✓ Signed in' + (ts.login_name ? ' as <strong>' + esc(ts.login_name) + '</strong>' : '') + '</li>');
        checks.push('<li>' + (ts.hostname ? '✓ This machine is <code>' + esc(ts.hostname) + '</code>' : '✗ No MagicDNS name: enable MagicDNS in the ' + link('https://login.tailscale.com/admin/dns', 'Tailscale admin console')) + '</li>');
      }
    }
    let help = '';
    if (!ts.installed) {
      help = '<p>Tailscale gives your devices a private network. Install it on this computer: '
        + link(LINKS.macAppStore, 'Mac App Store') + ' or ' + link(LINKS.download, 'tailscale.com/download')
        + ' (Linux: <code>curl -fsSL https://tailscale.com/install.sh | sh</code>). This dialog updates by itself once it is installed.</p>';
    } else if (!ts.running) {
      help = '<p>Open the Tailscale app and sign in (Linux: <code>sudo tailscale up</code>).'
        + (ts.auth_url ? ' Or finish signing in here: ' + link(ts.auth_url, ts.auth_url) : '') + '</p>';
    }
    return '<div class="ccc-phone-step"><h4>' + dot(ready) + '1. Tailscale on this computer</h4>'
      + '<ul class="ccc-phone-checks">' + checks.join('') + '</ul>' + help + '</div>';
  }

  function serveStep(st, ready) {
    const plan = (st.serve && st.serve.plan) || {};
    const conflicts = (plan.conflicts || []).map((c) =>
      'HTTPS port ' + esc(c.https_port) + ' already serves <code>' + esc(c.target) + '</code> (left alone)').join('; ');
    let body;
    if (st.enabled) {
      body = '<p>On. CCC is reachable at <code>' + esc(st.url) + '</code> from devices on your tailnet only (not the public internet).'
        + (st.created_by === 'existing' ? ' This serve entry existed before; turning off leaves it in place.' : '')
        + '</p>'
        + (st.hostname_changed ? '<p class="ccc-phone-msg err">This machine\'s Tailscale name changed since setup. Turn off and on again to use the new name.</p>' : '')
        + '<div class="ccc-phone-row"><button type="button" data-act="disable">Turn off</button></div>';
    } else {
      let what = '';
      if (plan.action === 'create') what = 'Runs <code>tailscale serve --bg --https=' + esc(plan.https_port) + ' ' + esc(plan.target) + '</code>.';
      else if (plan.action === 'reuse') what = 'A serve entry for CCC already exists on HTTPS port ' + esc(plan.https_port) + '; CCC will use it.';
      else if (plan.action === 'none') what = 'Every candidate HTTPS port is taken.';
      body = '<p>Shares this CCC on your tailnet over HTTPS and trusts that address right away (no restart). ' + what + '</p>'
        + (conflicts ? '<p>' + conflicts + '.</p>' : '')
        + '<div class="ccc-phone-row"><button type="button" class="primary" data-act="enable"' + (ready && plan.action !== 'none' ? '' : ' disabled') + '>Turn on phone access</button></div>';
    }
    return '<div class="ccc-phone-step' + (ready ? '' : ' is-off') + '"><h4>' + dot(st.enabled ? true : null) + '2. Share CCC on your tailnet</h4>'
      + body + '<div class="ccc-phone-msg" data-role="serve-msg" aria-live="polite"></div></div>';
  }

  function phoneStep(st) {
    if (!st.enabled) {
      return '<div class="ccc-phone-step is-off"><h4>' + dot(null) + '3. Open it on your phone</h4><p>Turn on step 2 first.</p></div>';
    }
    const login = (st.tailscale && st.tailscale.login_name) || '';
    return '<div class="ccc-phone-step"><h4>' + dot(null) + '3. Open it on your phone</h4>'
      + '<div class="ccc-phone-qr">' + safeSvg(st.qr_svg)
      + '<ol><li>Install Tailscale on your phone: ' + link(LINKS.ios, 'iPhone') + ' / ' + link(LINKS.android, 'Android') + '.</li>'
      + '<li>Sign in with the same account' + (login ? ' (<strong>' + esc(login) + '</strong>)' : '') + ' and switch the VPN on.</li>'
      + '<li>Scan this code with the camera, or open the link below.</li>'
      + '<li>Optional: Share &gt; Add to Home Screen for an app icon.</li></ol></div>'
      + '<div class="ccc-phone-row"><span class="ccc-phone-url" data-role="url">' + esc(st.url) + '</span>'
      + '<button type="button" data-act="copy">Copy</button></div></div>';
  }

  function testStep(st) {
    return '<div class="ccc-phone-step' + (st.enabled ? '' : ' is-off') + '"><h4>' + dot(null) + '4. Test</h4>'
      + '<p>Sends a real POST through the tailnet URL (the same path a phone uses) and reports what broke, if anything.</p>'
      + '<div class="ccc-phone-row"><button type="button" data-act="test"' + (st.enabled ? '' : ' disabled') + '>Run test</button></div>'
      + '<div class="ccc-phone-msg" data-role="test-msg" aria-live="polite"></div></div>';
  }

  function securityBox(st) {
    return '<div class="ccc-phone-warn"><strong>Who can use it:</strong> anyone signed in to your tailnet can open this URL and drive CCC as you: '
      + 'run sessions, send messages, run commands. CCC has no login of its own. '
      + 'If you share your tailnet with other people, restrict this machine with ' + link(LINKS.acls, 'tailnet ACLs') + ' and set a PIN. '
      + link(LINKS.security, 'Details in SECURITY.md') + '.'
      + '<div class="ccc-phone-row"><span>PIN for phones and other devices: <strong>' + (st.pin_set ? 'on' : 'off') + '</strong></span>'
      + '<input type="password" data-role="pin" inputmode="numeric" autocomplete="new-password" placeholder="' + (st.pin_set ? 'new PIN' : '6+ digits') + '">'
      + '<button type="button" data-act="pin">' + (st.pin_set ? 'Change PIN' : 'Set PIN') + '</button>'
      + (st.pin_set ? '<button type="button" data-act="pin-clear">Remove PIN</button>' : '')
      + '</div><div class="ccc-phone-msg" data-role="pin-msg" aria-live="polite"></div>'
      + '<p>This computer never needs the PIN. Changing it signs every phone out.</p></div>';
  }

  /* open({nodeId, nodeName}) — nodeId '' means this node. */
  function open(opts) {
    opts = opts || {};
    const inline = opts.container;
    const opener = document.activeElement;
    injectStyle();
    const nodeId = opts.nodeId || '';
    const nodeName = opts.nodeName || '';
    let status = null;
    let busy = false;
    let timer = null;
    let lastRender = '';

    const bd = document.createElement('div');
    bd.className = inline ? 'ccc-phone-inline' : 'ccc-phone-backdrop';
    bd.innerHTML = '<div class="ccc-phone-box" role="dialog" aria-modal="true" aria-label="Phone access">'
      + '<h3>Phone access' + (nodeName ? ' on ' + esc(nodeName) : '') + '</h3>'
      + '<p>Open this CCC on your phone over your private Tailscale network. About a minute, no port forwarding, nothing public.</p>'
      + '<div data-role="body"><p>Checking Tailscale…</p></div>'
      + '<div class="ccc-phone-foot ccc-phone-row"><button type="button" data-act="close">Close</button></div></div>';
    if(inline){bd.innerHTML='<div class="ccc-phone-content"><p>Open this CCC on your phone over your private Tailscale network.</p><div data-role="body"><p>Checking Tailscale…</p></div></div>';inline.append(bd);}else document.body.appendChild(bd);
    const $ = (sel) => bd.querySelector(sel);
    if(!inline)$('[data-act="close"]')?.focus();

    function restoreAction(action) {
      const next = action === 'enable' ? 'disable' : action === 'disable' ? 'enable' : action;
      const target = $('[data-act="' + action + '"]') || $('[data-act="' + next + '"]') || $('[data-act="close"]');
      if (target && !target.disabled) target.focus();
    }

    function setMsg(role, text, kind, html) {
      const el = $('[data-role="' + role + '"]');
      if (!el) return;
      el.className = 'ccc-phone-msg' + (kind ? ' ' + kind : '');
      if (html) el.innerHTML = html; else el.textContent = text || '';
    }

    function render() {
      if (!status) return;
      const focused = bd.contains(document.activeElement) ? document.activeElement : null;
      const action = focused?.getAttribute('data-act');
      const role = focused?.getAttribute('data-role');
      if (status.ok === false && !status.tailscale) {
        $('[data-role="body"]').innerHTML = '<p class="ccc-phone-msg err">' + esc(status.detail || status.error || 'Could not load status') + '</p>';
        if (focused && !focused.isConnected) $('[data-act="close"]')?.focus();
        return;
      }
      const ts = status.tailscale || {};
      const ready = !!(ts.installed && ts.running && ts.hostname);
      const html = tailscaleStep(status) + serveStep(status, ready) + phoneStep(status) + testStep(status) + securityBox(status);
      if (html === lastRender) return;  // keep typed PIN / messages across polls
      const pinVal = ($('[data-role="pin"]') || {}).value || '';
      lastRender = html;
      $('[data-role="body"]').innerHTML = html;
      const pin = $('[data-role="pin"]');
      if (pin && pinVal) pin.value = pinVal;
      if (focused && !focused.isConnected) {
        if (action) restoreAction(action);
        else if (role) $('[data-role="' + role + '"]')?.focus();
      }
    }

    async function refresh() {
      try { status = await call('status', null, nodeId); } catch (e) { status = { ok: false, detail: String(e && e.message || e) }; }
      render();
    }

    function schedule() {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        if (!document.body.contains(bd)) return;
        if (!busy && !document.hidden && (!inline || bd.getClientRects().length)) await refresh();
        schedule();
      }, POLL_MS);
    }

    function close() {
      clearTimeout(timer);
      bd.remove();
      document.removeEventListener('keydown', onKey, true);
      [opener, document.getElementById('settingsBtn'), document.querySelector('[data-mobile-nav="more"]'), document.querySelector('[data-mobile-nav-menu]')]
        .find(node => {
          if (!node || node === document.body || !node.isConnected || node.disabled || !node.getClientRects().length || getComputedStyle(node).visibility === 'hidden') return false;
          const rect = node.getBoundingClientRect();
          const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
          return top === node || node.contains(top);
        })?.focus();
    }
    function onKey(e) {
      if (e.isComposing) return;
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return; }
      if (e.key === 'Tab') {
        const nodes = [...bd.querySelectorAll('button,input,select,a[href],[tabindex]')].filter(node => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length);
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (!bd.contains(document.activeElement) || (!e.shiftKey && document.activeElement === last)) { e.preventDefault(); first?.focus(); }
        else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      }
    }
    if(!inline)document.addEventListener('keydown', onKey, true);

    function failureHtml(d) {
      let html = esc(d.detail || d.error || 'Failed');
      if (d.action_url) html += ' ' + link(d.action_url, 'Open');
      return html;
    }

    bd.addEventListener('click', async (e) => {
      if (!inline && e.target === bd) { close(); return; }
      const btn = e.target.closest('[data-act]');
      if (!btn || busy) return;
      const act = btn.getAttribute('data-act');
      if (act === 'close') { close(); return; }
      if (act === 'copy') {
        try { await navigator.clipboard.writeText(status.url); btn.textContent = 'Copied'; } catch (_) {
          const r = document.createRange(); r.selectNodeContents($('[data-role="url"]'));
          const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
        }
        return;
      }
      busy = true;
      const restoreFocus = document.activeElement === btn;
      btn.disabled = true;
      try {
        if (act === 'enable' || act === 'disable') {
          if (act === 'enable' && !nodeId && !await enableFlag()) {
            setMsg('serve-msg', 'Could not enable Phone access. Sharing is unchanged; retry.', 'err');
            return;
          }
          setMsg('serve-msg', act === 'enable' ? 'Setting up tailscale serve…' : 'Turning off…');
          const d = await call(act, {}, nodeId);
          if (d.status) status = d.status;
          lastRender = '';
          render();
          if (!d.ok) setMsg('serve-msg', '', 'err', failureHtml(d));
          else if (act === 'disable' && d.detail) setMsg('serve-msg', 'Off. Note: ' + d.detail, 'ok');
          else if (act === 'enable') setMsg('test-msg', 'Running the round-trip test…');
          if (act === 'enable' && d.ok) {
            const t = await call('test', {}, nodeId);
            setMsg('test-msg', '', t.ok ? 'ok' : 'err', t.ok ? esc(t.detail) + (t.warning ? ' ' + esc(t.warning) : '') : 'Test failed (' + esc(t.error) + '): ' + failureHtml(t));
          }
        } else if (act === 'test') {
          setMsg('test-msg', 'Testing…');
          const t = await call('test', {}, nodeId);
          setMsg('test-msg', '', t.ok ? 'ok' : 'err', t.ok ? esc(t.detail) + (t.warning ? ' ' + esc(t.warning) : '') : 'Test failed (' + esc(t.error) + '): ' + failureHtml(t));
        } else if (act === 'pin' || act === 'pin-clear') {
          const body = act === 'pin-clear' ? { clear: true } : { pin: ($('[data-role="pin"]') || {}).value || '' };
          const d = await call('pin', body, nodeId);
          if (!d.ok) { setMsg('pin-msg', d.detail || d.error || 'Failed', 'err'); }
          else {
            await refresh();
            setMsg('pin-msg', d.pin_set ? 'PIN set. Phones will ask for it once.' : 'PIN removed.', 'ok');
          }
        }
      } catch (err) {
        setMsg(act.startsWith('pin') ? 'pin-msg' : act === 'test' ? 'test-msg' : 'serve-msg', String(err && err.message || err), 'err');
      } finally {
        busy = false;
        btn.disabled = false;
        if (restoreFocus && document.activeElement === document.body && bd.isConnected) restoreAction(act);
      }
    });

    refresh().then(schedule);
    return { close };
  }

  /* Fleet page: one row per node (this one + paired peers). */
  async function renderFleet(container) {
    if (!container) return;
    if (!(await ensureFlags())) { container.hidden = true; return; }
    injectStyle();
    container.hidden = false;
    container.innerHTML = '<div class="fleet-phone-list"><h3>Phone access</h3><p class="fed-empty">Checking each node…</p></div>';
    let data;
    try {
      const r = await fetch('/api/phone-access/nodes', { cache: 'no-store' });
      data = await r.json();
    } catch (e) {
      container.innerHTML = '<div class="fleet-phone-list"><h3>Phone access</h3><p class="upd-error visible">' + esc(e && e.message || e) + '</p></div>';
      return;
    }
    const rows = (data && data.nodes) || [];
    container.innerHTML = '<div class="fleet-phone-list"><h3>Phone access</h3>' + rows.map((n) => {
      const name = n.node_name || (n.node_id || '').slice(0, 8);
      const ts = n.tailscale || {};
      let state;
      if (n.ok === false && !n.tailscale) state = '<span class="state err">' + esc(n.error || 'unreachable') + '</span>';
      else if (n.enabled) state = '<span class="state ok">on</span>' + (n.pin_set ? ' · PIN' : '');
      else if (!ts.installed) state = '<span class="state err">Tailscale not installed</span>';
      else if (!ts.running) state = '<span class="state err">Tailscale not connected</span>';
      else state = '<span class="state">off</span>';
      const url = n.enabled && safeHttps(n.url) ? '<a class="url" href="' + esc(n.url) + '" target="_blank" rel="noopener noreferrer">' + esc(n.url) + '</a>' : '<span class="url"></span>';
      return '<div class="fleet-phone-node"><span class="name">' + esc(name) + (n.self ? ' <span class="fleet-self-marker">self</span>' : '') + '</span>'
        + state + url
        + '<button type="button" data-phone-node="' + esc(n.self ? '' : n.node_id) + '" data-phone-name="' + esc(name) + '">' + (n.enabled ? 'Details…' : 'Set up…') + '</button></div>';
    }).join('') + '</div>';
    container.querySelectorAll('[data-phone-node]').forEach((b) => b.addEventListener('click', () => {
      const w = open({ nodeId: b.getAttribute('data-phone-node'), nodeName: b.getAttribute('data-phone-name') });
      // Repaint the list once the dialog closes, so a change shows up here.
      const obs = new MutationObserver(() => {
        if (!document.querySelector('.ccc-phone-backdrop')) { obs.disconnect(); renderFleet(container); }
      });
      obs.observe(document.body, { childList: true });
      return w;
    }));
  }

  /* Dashboard: reveal the Settings row when the flag is on. */
  function initSettingsRow() {
    const row = document.getElementById('phoneAccessRow');
    if (!row) return;
    // Viewing setup is read-only; the explicit Turn on action enables the flag.
    row.hidden = false;
  }

  // The wizard's server side (enable) is gated on the `phone_access` preview
  // flag. Only the explicit Turn on action switches it on.
  async function enableFlag() {
    if (await ensureFlags()) return true;
    try {
      const r = await fetch('/api/features/flag', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: FLAG, on: true }),
      });
      const d = await r.json();
      if (r.ok && d && d.ok) {
        window.__CCC_FLAGS__ = Object.assign({}, window.__CCC_FLAGS__ || {}, d.flags || {}, { [FLAG]: true });
        window.dispatchEvent(new CustomEvent('ccc:flags'));
        return true;
      }
    } catch (_) {}
    return false;
  }

  window.cccPhoneAccess = { mount(container){if(container&&!container.childElementCount)open({container});}, open, renderFleet, ensureFlags, enableFlag };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initSettingsRow);
  else initSettingsRow();
})();
