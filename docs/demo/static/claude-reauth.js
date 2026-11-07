/* One-click Claude Code re-authentication (preview flag `claude_reauth`).
 *
 * Shared by the dashboard (index.html: per-session "Re-authenticate" chip)
 * and the Fleet page (fleet.html: per-node button in the health strip).
 * Backend: /api/claude-auth/* (ccc_server/claude_auth.py). A node_id routes
 * the whole flow to that paired peer, which runs `claude auth login` in tmux
 * as its own user; this page only ever sees the authorize URL.
 *
 * The one-time code is sent once, in a POST body, and never stored,
 * logged, or rendered back.
 */
(function () {
  'use strict';
  if (window.cccClaudeReauth) return;

  const FLAG = 'claude_reauth';
  let flagsLoaded = !!window.__CCC_FLAGS__;
  let flagsPromise = null;

  function flagOn() {
    try { return !!(window.__CCC_FLAGS__ || {})[FLAG]; } catch (_) { return false; }
  }

  // index.html bootstraps __CCC_FLAGS__; fleet.html does not, so load them.
  function ensureFlags() {
    if (flagsLoaded) return Promise.resolve(flagOn());
    if (!flagsPromise) {
      flagsPromise = fetch('/api/features', { cache: 'no-store' })
        .then((r) => r.json())
        .then((f) => {
          window.__CCC_FLAGS__ = Object.assign({}, (f && f.preview) || {}, window.__CCC_FLAGS__ || {});
          try {
            new URLSearchParams(window.location.search || '').getAll('ff')
              .forEach((n) => { if (n) window.__CCC_FLAGS__[n] = true; });
          } catch (_) {}
          flagsLoaded = true;
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

  async function post(sub, body) {
    const r = await fetch('/api/claude-auth/' + sub, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const d = await r.json().catch(() => ({}));
    return d || {};
  }

  function injectStyle() {
    if (document.getElementById('ccc-reauth-style')) return;
    const st = document.createElement('style');
    st.id = 'ccc-reauth-style';
    st.textContent = [
      '.ccc-reauth-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:10050;display:flex;align-items:center;justify-content:center}',
      '.ccc-reauth-box{background:var(--bg-elev,#1b1e24);color:var(--text,#e6e6e6);border:1px solid var(--border,#333);border-radius:10px;padding:18px 20px;width:min(480px,92vw);font:13px/1.45 system-ui,-apple-system,sans-serif;box-shadow:0 12px 40px rgba(0,0,0,.4)}',
      '.ccc-reauth-box h3{margin:0 0 8px;font-size:15px}',
      '.ccc-reauth-box p{margin:6px 0;color:var(--text-dim,#aab)}',
      '.ccc-reauth-box input{width:100%;box-sizing:border-box;margin:8px 0;padding:7px 9px;border-radius:6px;border:1px solid var(--border,#444);background:var(--bg,#111);color:inherit;font:12px ui-monospace,monospace}',
      '.ccc-reauth-row{display:flex;gap:8px;justify-content:flex-end;margin-top:12px;flex-wrap:wrap}',
      '.ccc-reauth-row button{padding:6px 12px;border-radius:6px;border:1px solid var(--border,#444);background:transparent;color:inherit;cursor:pointer}',
      '.ccc-reauth-row button.primary{background:var(--accent,#d97757);border-color:var(--accent,#d97757);color:#fff}',
      '.ccc-reauth-row button:disabled{opacity:.5;cursor:default}',
      '.ccc-reauth-msg{margin-top:8px;min-height:1em}',
      '.ccc-reauth-msg.err{color:var(--danger,#f07070)}',
      '.ccc-reauth-msg.ok{color:var(--success,#6fcf97)}',
      '.ccc-reauth-box a{color:var(--accent,#d97757);word-break:break-all}',
      '.conv-reauth-chip,.fleet-reauth-btn{font-size:11px;padding:1px 7px;border-radius:9px;border:1px solid var(--danger,#f07070);color:var(--danger,#f07070);background:transparent;cursor:pointer;white-space:nowrap;margin-left:4px}',
      '.conv-reauth-chip:hover,.fleet-reauth-btn:hover{background:var(--danger,#f07070);color:#fff}',
    ].join('\n');
    document.head.appendChild(st);
  }

  /* open({nodeId, nodeName, sessionIds}) — nodeId '' means this node. */
  function open(opts) {
    opts = opts || {};
    injectStyle();
    const nodeId = opts.nodeId || '';
    const nodeName = opts.nodeName || (nodeId ? nodeId.slice(0, 8) : 'this machine');
    const sessionIds = Array.from(new Set((opts.sessionIds || []).filter(Boolean)));
    let attemptId = null;
    let busy = false;

    const bd = document.createElement('div');
    bd.className = 'ccc-reauth-backdrop';
    bd.innerHTML = '<div class="ccc-reauth-box" role="dialog" aria-modal="true" aria-label="Re-authenticate Claude Code">'
      + '<h3>Re-authenticate Claude Code on ' + esc(nodeName) + '</h3>'
      + '<p>Claude Code on this node lost its login ("Failed to authenticate"). '
      + 'CCC runs <code>claude auth login</code> there; you approve in your browser and paste the code back here.</p>'
      + (sessionIds.length ? '<p>' + sessionIds.length + ' stuck session' + (sessionIds.length === 1 ? '' : 's')
        + ' will be told to retry once it works.</p>' : '')
      + '<div data-step="start"><div class="ccc-reauth-row">'
      + '<button type="button" data-act="close">Cancel</button>'
      + '<button type="button" class="primary" data-act="start">Start sign-in</button></div></div>'
      + '<div data-step="code" hidden>'
      + '<p>1. Approve in the sign-in tab (<a data-role="url" target="_blank" rel="noopener noreferrer">open it again</a>).<br>'
      + '2. Copy the code the page shows and paste it here:</p>'
      + '<input type="text" data-role="code" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="code#state">'
      + '<div class="ccc-reauth-row">'
      + '<button type="button" data-act="cancel">Cancel</button>'
      + '<button type="button" class="primary" data-act="submit">Submit code</button></div></div>'
      + '<div data-step="done" hidden><div class="ccc-reauth-row">'
      + '<button type="button" data-act="restart" hidden>Start over</button>'
      + '<button type="button" class="primary" data-act="close">Close</button></div></div>'
      + '<div class="ccc-reauth-msg" data-role="msg" aria-live="polite"></div>'
      + '</div>';
    document.body.appendChild(bd);

    const $ = (sel) => bd.querySelector(sel);
    const msg = (text, kind) => {
      const el = $('[data-role="msg"]');
      el.className = 'ccc-reauth-msg' + (kind ? ' ' + kind : '');
      el.textContent = text || '';
    };
    const step = (name) => {
      bd.querySelectorAll('[data-step]').forEach((el) => { el.hidden = el.getAttribute('data-step') !== name; });
    };
    const close = () => { bd.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = (e) => { if (e.key === 'Escape' && !busy) { e.stopPropagation(); cancelAndClose(); } };
    document.addEventListener('keydown', onKey, true);

    async function cancelAndClose() {
      if (attemptId) post('cancel', { node_id: nodeId }).catch(() => {});
      close();
    }

    async function start() {
      if (busy) return;
      busy = true;
      // Open the tab inside the click so popup blockers allow it; point it at
      // the URL once the node has printed one.
      let tab = null;
      try { tab = window.open('', '_blank'); } catch (_) { tab = null; }
      msg('Starting claude auth login on ' + nodeName + '…');
      $('[data-act="start"]').disabled = true;
      const d = await post('start', { node_id: nodeId }).catch((e) => ({ ok: false, detail: String(e) }));
      busy = false;
      if (!d.ok || !d.url) {
        if (tab) try { tab.close(); } catch (_) {}
        $('[data-act="start"]').disabled = false;
        msg('Could not start sign-in: ' + (d.detail || d.error || 'unknown error'), 'err');
        return;
      }
      attemptId = d.attempt_id;
      if (tab) {
        try { tab.opener = null; tab.location.href = d.url; } catch (_) { tab = null; }
      }
      $('[data-role="url"]').href = d.url;
      step('code');
      msg(tab ? 'Sign-in page opened in a new tab.' : 'Popup blocked: use "open it again" above.');
      $('[data-role="code"]').focus();
    }

    async function submit() {
      if (busy) return;
      const input = $('[data-role="code"]');
      const code = (input.value || '').trim();
      if (!code) { msg('Paste the code from the sign-in page first.', 'err'); return; }
      busy = true;
      $('[data-act="submit"]').disabled = true;
      msg('Verifying on ' + nodeName + ' (claude auth status, then a test prompt; up to a minute)…');
      const d = await post('submit', { node_id: nodeId, attempt_id: attemptId, code: code })
        .catch((e) => ({ ok: false, detail: String(e) }));
      input.value = '';
      busy = false;
      $('[data-act="submit"]').disabled = false;
      if (d.error === 'bad_code') { msg(d.detail || 'That code does not look right.', 'err'); return; }
      step('done');
      if (!d.ok) {
        attemptId = null;
        $('[data-act="restart"]').hidden = false;
        msg('Sign-in failed: ' + (d.detail || d.error || 'unknown error') + ' Start over for a fresh code.', 'err');
        return;
      }
      let text = 'Logged in' + (d.email ? ' as ' + d.email : '') + '.';
      if (d.smoke) text += d.smoke.ok ? ' Test prompt answered.' : ' Test prompt failed: ' + (d.smoke.detail || '') + '.';
      attemptId = null;
      if (sessionIds.length) {
        const n = await post('nudge', { node_id: nodeId, session_ids: sessionIds }).catch(() => ({}));
        text += ' Told ' + (n.nudged || 0) + ' of ' + sessionIds.length + ' stuck session'
          + (sessionIds.length === 1 ? '' : 's') + ' to retry.';
      }
      msg(text, d.smoke && !d.smoke.ok ? 'err' : 'ok');
      try { window.dispatchEvent(new CustomEvent('ccc:claude-reauth', { detail: { nodeId: nodeId, ok: true } })); } catch (_) {}
    }

    bd.addEventListener('click', (e) => {
      if (e.target === bd && !busy) { cancelAndClose(); return; }
      const act = e.target && e.target.getAttribute && e.target.getAttribute('data-act');
      if (!act) return;
      if (act === 'start') start();
      else if (act === 'submit') submit();
      else if (act === 'cancel') cancelAndClose();
      else if (act === 'restart') { $('[data-act="restart"]').hidden = true; step('start'); $('[data-act="start"]').disabled = false; msg(''); }
      else if (act === 'close') close();
    });
    $('[data-role="code"]').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
  }

  // ── Dashboard row chip ─────────────────────────────────────────────────
  function rowChipHtml(row) {
    if (!row || !row.claude_auth_failed || !flagOn()) return '';
    return '<button type="button" class="conv-reauth-chip" data-role="claude-reauth"'
      + ' data-session-id="' + esc(row.session_id || row.id || '') + '"'
      + ' title="Claude Code is not logged in on this machine. Re-authenticate and tell stuck sessions to retry.">'
      + 'Re-authenticate</button>';
  }

  // Capture phase: the row's own click handler would otherwise open the
  // conversation. Every flagged row on the page is nudged, not just this one.
  document.addEventListener('click', (e) => {
    const chip = e.target && e.target.closest && e.target.closest('[data-role="claude-reauth"]');
    if (!chip) return;
    e.preventDefault();
    e.stopPropagation();
    const ids = Array.from(document.querySelectorAll('[data-role="claude-reauth"][data-session-id]'))
      .map((el) => el.getAttribute('data-session-id'));
    open({ nodeId: chip.getAttribute('data-node-id') || '', nodeName: chip.getAttribute('data-node-name') || '', sessionIds: ids });
  }, true);

  // ── Fleet health strip: node-level button ──────────────────────────────
  async function decorateFleetNodes(stripEl) {
    if (!stripEl || !(await ensureFlags())) return;
    injectStyle();
    let data;
    try {
      const r = await fetch('/api/sessions?federated=1&limit=500', { cache: 'no-store' });
      data = await r.json();
    } catch (_) { return; }
    const byNode = {};
    ((data && data.sessions) || []).forEach((s) => {
      if (!s.claude_auth_failed || !s.node_id) return;
      (byNode[s.node_id] = byNode[s.node_id] || []).push(s.session_id);
    });
    const selfId = ((data && data.nodes) || []).filter((n) => n.self).map((n) => n.node_id)[0] || '';
    stripEl.querySelectorAll('[data-fleet-node-id]').forEach((chip) => {
      const nid = chip.getAttribute('data-fleet-node-id');
      const old = chip.querySelector('.fleet-reauth-btn');
      if (old) old.remove();
      const ids = byNode[nid] || [];
      if (!ids.length) return;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'fleet-reauth-btn';
      btn.textContent = 'Re-authenticate (' + ids.length + ')';
      btn.title = ids.length + ' session' + (ids.length === 1 ? '' : 's') + ' on this node stopped with a Claude Code auth failure';
      btn.addEventListener('click', () => open({
        nodeId: nid === selfId ? '' : nid,
        nodeName: chip.getAttribute('data-fleet-node-name') || '',
        sessionIds: ids,
      }));
      chip.appendChild(btn);
    });
  }

  window.cccClaudeReauth = { open: open, rowChipHtml: rowChipHtml, decorateFleetNodes: decorateFleetNodes, ensureFlags: ensureFlags };
})();
