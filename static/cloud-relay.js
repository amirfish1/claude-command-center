/* CCC Cloud (outbound relay) settings section.
 *
 * Injected into the Network access modal by app.js (networkOpen calls
 * window.cccCloudRelay.open()). Optional, outbound-only device pairing with a
 * hosted relay. It does NOT change local or Tailscale access. Every
 * /api/cloud-relay/* endpoint is localhost-only on the server
 * (ccc_server/cloud_relay.py, docs/cloud-relay/PROTOCOL.md).
 */
(function () {
  'use strict';
  if (window.cccCloudRelay) return;

  // No baked-in service URL: the user types it, or sets CCC_CLOUD_SERVICE_URL
  // (surfaced by the status endpoint as default_relay_url).
  const el = {};
  let built = false;
  let pendingMask = '';

  function build() {
    if (built) return;
    const modal = document.getElementById('networkModal');
    const dialog = modal && modal.querySelector('.upd-dialog');
    const anchor = document.getElementById('networkError');
    if (!dialog || !anchor) return;
    const wrap = document.createElement('div');
    wrap.className = 'cloud-relay-section';
    wrap.innerHTML =
      '<div class="cloud-relay-title">CCC Cloud</div>' +
      '<div class="cloud-relay-copy">Optional. Outbound-only. Does not change ' +
        'local or Tailscale access. No account required for local use.</div>' +
      '<div class="cloud-relay-status" id="cloudRelayStatus">Loading...</div>' +
      '<div id="cloudRelayPair" class="cloud-relay-pair" style="display:none;">' +
        '<div class="cloud-relay-copy" id="cloudRelaySignup" style="display:none;"></div>' +
        '<label class="bug-label" for="cloudRelayCode">Pairing code</label>' +
        '<input type="text" id="cloudRelayCode" class="bug-input" autocomplete="off" ' +
          'placeholder="8-character code from CCC Cloud">' +
        '<label class="bug-label" for="cloudRelayUrl" style="margin-top:8px;">Relay URL</label>' +
        '<input type="text" id="cloudRelayUrl" class="bug-input" autocomplete="off" ' +
          'placeholder="https://relay.example.com">' +
        '<div class="cloud-relay-actions">' +
          '<button type="button" class="upd-btn upd-primary" id="cloudRelayPairBtn">Pair...</button>' +
        '</div>' +
      '</div>' +
      '<div id="cloudRelayConfirm" class="cloud-relay-confirm" style="display:none;">' +
        '<div id="cloudRelayConfirmMsg" class="cloud-relay-copy"></div>' +
        '<div class="cloud-relay-actions">' +
          '<button type="button" class="upd-btn upd-primary" id="cloudRelayConfirmBtn">Confirm</button>' +
          '<button type="button" class="upd-btn" id="cloudRelayCancelBtn">Cancel</button>' +
        '</div>' +
      '</div>' +
      '<div id="cloudRelayPaired" class="cloud-relay-paired" style="display:none;">' +
        '<label style="display:flex;align-items:center;gap:8px;cursor:pointer;margin-top:8px;">' +
          '<input type="checkbox" id="cloudRelayEnabled"><span>Enabled</span></label>' +
        '<label style="display:flex;align-items:flex-start;gap:8px;cursor:pointer;margin-top:8px;">' +
          '<input type="checkbox" id="cloudRelayShareTitles" style="margin-top:3px;"><span>' +
          'Share session titles with CCC Cloud (redacted). Off sends opaque labels.</span></label>' +
        '<div class="cloud-relay-actions">' +
          '<button type="button" class="upd-btn" id="cloudRelayUnpairBtn">Unpair</button>' +
        '</div>' +
      '</div>' +
      '<div class="cloud-relay-error" id="cloudRelayError"></div>';
    dialog.insertBefore(wrap, anchor);
    const q = (id) => wrap.querySelector('#' + id);
    el.status = q('cloudRelayStatus');
    el.pair = q('cloudRelayPair');
    el.signup = q('cloudRelaySignup');
    el.code = q('cloudRelayCode');
    el.url = q('cloudRelayUrl');
    el.pairBtn = q('cloudRelayPairBtn');
    el.confirm = q('cloudRelayConfirm');
    el.confirmMsg = q('cloudRelayConfirmMsg');
    el.confirmBtn = q('cloudRelayConfirmBtn');
    el.cancelBtn = q('cloudRelayCancelBtn');
    el.paired = q('cloudRelayPaired');
    el.enabled = q('cloudRelayEnabled');
    el.share = q('cloudRelayShareTitles');
    el.unpairBtn = q('cloudRelayUnpairBtn');
    el.error = q('cloudRelayError');
    el.pairBtn.addEventListener('click', doPair);
    el.confirmBtn.addEventListener('click', doConfirm);
    el.cancelBtn.addEventListener('click', doAbandon);
    el.unpairBtn.addEventListener('click', doUnpair);
    el.enabled.addEventListener('change', () => postConfig({ enabled: el.enabled.checked }));
    el.share.addEventListener('change', () => postConfig({ share_titles: el.share.checked }));
    built = true;
  }

  function showError(msg) {
    if (!el.error) return;
    el.error.textContent = msg || '';
    el.error.style.display = msg ? 'block' : 'none';
  }

  async function post(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok, data };
  }

  function loopLabel(state) {
    return ({ online: 'Online', offline: 'Offline', error: 'Error',
      connecting: 'Connecting...', disabled: 'Disabled', off: 'Offline' }[state]) || state || '';
  }

  // Why this machine is not paired, in words the user can act on. A
  // service-side disconnect already wiped the dead credential, so the
  // pairing form below is the way back.
  const DISCONNECT_COPY = {
    device_revoked: 'This computer was disconnected from your CCC Cloud account. ' +
      'Pair again with a new code to reconnect.',
    account_deleted: 'The CCC Cloud account this computer was paired with was deleted.',
    credentials_rejected: 'CCC Cloud no longer accepts this computer\'s credentials. ' +
      'Pair again with a new code to reconnect.',
    unpaired: 'Not paired. Local CCC keeps working without an account.',
  };
  function disconnectLine(reason) {
    if (!reason) return 'Not paired. Local CCC keeps working without an account.';
    return DISCONNECT_COPY[reason] || ('Not paired. (' + reason + ')');
  }

  // Where to get an account and a pairing code. DOM nodes, no innerHTML.
  function renderSignup(url) {
    if (!el.signup) return;
    el.signup.textContent = '';
    if (!url) { el.signup.style.display = 'none'; return; }
    el.signup.appendChild(document.createTextNode('New to CCC Cloud? '));
    const a = document.createElement('a');
    a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer';
    a.textContent = 'Create an account or sign in';
    el.signup.appendChild(a);
    el.signup.appendChild(document.createTextNode(
      ', then generate a pairing code and enter it here.'));
    el.signup.style.display = '';
  }

  const PAIR_ERRORS = {
    relay_url_insecure: 'the relay URL must start with https:// (plain http only for localhost).',
    relay_url_required: 'enter the relay URL of your CCC Cloud service.',
    plan_limit_machines: 'your CCC Cloud plan has reached its machine limit. ' +
      'Disconnect a machine from your CCC Cloud account first.',
    pair_code_invalid: 'that pairing code is not valid.',
    pair_code_expired: 'that pairing code expired. Generate a new one.',
    rate_limited: 'too many attempts. Wait a minute and try again.',
  };

  function render(s) {
    const paired = !!s.paired;
    el.pair.style.display = (!paired && !pendingMask) ? '' : 'none';
    el.confirm.style.display = pendingMask ? '' : 'none';
    el.paired.style.display = (paired && !pendingMask) ? '' : 'none';
    if (pendingMask) el.confirmMsg.textContent = 'Pair this computer with ' + pendingMask + '?';
    if (!el.url.value) el.url.value = s.relay_url || s.default_relay_url || '';
    if (paired) {
      el.enabled.checked = !!s.enabled;
      el.share.checked = s.share_titles !== false;
    }
    let line;
    if (s.env_disabled) {
      line = 'Disabled by CCC_CLOUD_DISABLED for this run.';
    } else if (pendingMask) {
      line = 'Pairing: confirm below.';
    } else if (!paired) {
      line = disconnectLine(s.disabled_reason);
    } else {
      const who = s.account_email_masked || 'this account';
      const ls = (s.loop && s.loop.state) || (s.enabled ? 'connecting' : 'off');
      line = 'Paired as ' + who + ': ' + (s.enabled ? loopLabel(ls) : 'Disabled');
      if (s.loop && s.loop.last_error && ls === 'error') line += ' (' + s.loop.last_error + ')';
      if (s.enabled && ls === 'off' && s.disabled_reason) line += ' (' + s.disabled_reason + ')';
    }
    el.status.textContent = line;
    renderSignup(!paired && !pendingMask ? (s.signup_url || '') : '');
  }

  async function refresh() {
    if (!built) return;
    showError('');
    try {
      const r = await fetch('/api/cloud-relay/status', { cache: 'no-store' });
      const s = await r.json();
      if (!r.ok) { el.status.textContent = s.error || 'CCC Cloud status unavailable.'; return; }
      render(s || {});
      // The rail's account avatar repaints on this event instead of waiting
      // for its poll, so pair/confirm/unpair/config changes show at once.
      try { window.dispatchEvent(new CustomEvent('ccc-cloud-relay-change')); } catch (_) {}
    } catch (_) {
      el.status.textContent = 'CCC Cloud status unavailable.';
    }
  }

  async function doPair() {
    showError('');
    const code = (el.code.value || '').trim();
    const url = (el.url.value || '').trim();
    if (!code) { showError('Enter the pairing code from CCC Cloud.'); return; }
    el.pairBtn.disabled = true;
    const { ok, data } = await post('/api/cloud-relay/pair', { pair_code: code, relay_url: url });
    el.pairBtn.disabled = false;
    if (!ok || !data.ok) {
      const why = PAIR_ERRORS[data.error] || data.error || 'unknown';
      showError('Pairing failed: ' + why);
      return;
    }
    pendingMask = data.masked_email || '(unknown email)';
    refresh();
  }

  async function doConfirm() {
    showError('');
    el.confirmBtn.disabled = true;
    const { ok, data } = await post('/api/cloud-relay/pair/confirm', {});
    el.confirmBtn.disabled = false;
    if (!ok || !data.ok) { showError('Confirm failed: ' + (data.error || 'unknown')); return; }
    pendingMask = '';
    el.code.value = '';
    refresh();
  }

  async function doAbandon() {
    await post('/api/cloud-relay/pair/abandon', {});
    pendingMask = '';
    refresh();
  }

  async function doUnpair() {
    showError('');
    const { ok, data } = await post('/api/cloud-relay/unpair', {});
    if (!ok || !data.ok) { showError('Unpair failed: ' + (data.error || 'unknown')); return; }
    refresh();
  }

  async function postConfig(patch) {
    showError('');
    const { ok, data } = await post('/api/cloud-relay/config', patch);
    if (!ok || !data.ok) {
      showError('Could not save: ' + (data.error === 'not_paired' ? 'pair first.' : (data.error || 'unknown')));
    }
    refresh();
  }

  window.cccCloudRelay = {
    open() { build(); refresh(); },
  };
})();
