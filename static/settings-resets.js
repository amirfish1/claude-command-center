/* Selected operational resets. Browser storage/reset remains a separate scope. */
(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const make = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; return n; };
  let host, view, pending, busy = false, generation = 0;
  const show = value => value.redacted ? 'Unsupported local value hidden' : !value.present ? 'Not stored · follow native defaults' : value.value === '' ? 'Blank (native engine/shared default)' : typeof value.value === 'object' ? JSON.stringify(value.value) : typeof value.value === 'boolean' ? (value.value ? 'On' : 'Off') : String(value.value);
  const status = text => { get('settingsResetStatus').textContent = text; };
  function focus(id) { const n = get(id); if (!n) return; n.scrollIntoView({block: 'center'}); requestAnimationFrame(() => n.isConnected && n.focus({preventScroll: true})); }
  function button(label, id, handler) {
    const n = make('button', label); n.id = id; n.type = 'button'; n.className = 'settings-action-btn';
    n.addEventListener('click', e => { e.stopPropagation(); handler(); }); return n;
  }
  function clear() { pending = null; get('settingsResetPreview').hidden = true; get('settingsResetPreview').replaceChildren(); }
  function fields() { return Array.from(host.querySelectorAll('#settingsResetFields input:checked'), n => n.dataset.key); }
  function lock(on) {
    busy = on;
    host.querySelectorAll('button,input,select').forEach(n => { n.disabled = on || n.dataset.unavailable === 'true'; });
    get('settingsResetPreviewButton').disabled = on || !view || !fields().length;
    const apply = get('settingsResetApply'); if (apply) apply.disabled = on || !pending?.changes.length;
  }
  async function api(body, query = '') {
    const response = await fetch('/api/settings/resets' + query, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {});
    const result = await response.json(); if (!response.ok || !result.ok) throw Error(result.error || 'Reset sources unavailable'); return result;
  }
  function scope() { return JSON.parse(get('settingsResetScope').value || '["machine",""]'); }
  async function refresh(returnFocus = false) {
    if (busy) return;
    const ticket = ++generation; clear(); lock(true); view = null;
    get('settingsResetFields').replaceChildren();
    get('settingsResetRefreshError').hidden = true;
    try {
      const [owner, target] = scope();
      const next = await api(null, '?scope=' + encodeURIComponent(owner) + '&target=' + encodeURIComponent(target));
      if (ticket !== generation) return;
      view = next; const select = get('settingsResetScope'); select.replaceChildren();
      next.scopes.forEach(s => { const option = make('option', s.label); option.value = JSON.stringify([s.scope, s.target]); option.selected = s.scope === next.scope && s.target === next.target; select.append(option); });
      get('settingsResetCoverage').textContent = next.coverage;
      const list = get('settingsResetFields');
      next.fields.forEach(field => {
        const row = make('label'); row.className = 'settings-reset-field';
        const check = make('input'); check.type = 'checkbox'; check.dataset.key = field.key; check.dataset.unavailable = String(!field.available);
        check.addEventListener('change', () => { clear(); lock(false); status('Selection changed. Preview before applying.'); });
        const text = make('span'); text.append(make('strong', field.label), make('span', show(field.current) + ' → ' + show(field.after)), make('small', field.available ? field.effect : field.reason));
        row.append(check, text); list.append(row);
      });
      status(next.fields.some(f => f.available) ? 'Choose saved fields to review. Nothing changes until Apply.' : 'No supported saved overrides to reset in this scope.');
    } catch (e) {
      status('Reset sources unavailable. Refresh to retry; no all-default claim can be made.');
      get('settingsResetRefreshError').textContent = e.message;
      get('settingsResetRefreshError').hidden = false;
      returnFocus = true;
    } finally { lock(false); if (returnFocus) focus('settingsResetRefresh'); }
  }
  async function preview() {
    if (busy || !view) return;
    clear(); lock(true);
    const body = {action: 'preview', scope: view.scope, target: view.target, fields: fields(), expected_revision: view.revision};
    try {
      const result = await api(body); pending = {...result, request: body};
      const panel = get('settingsResetPreview'); panel.hidden = false;
      panel.append(make('h4', 'Reset preview · ' + (result.scope === 'machine' ? 'This CCC host' : 'Queue ' + result.target)));
      panel.append(make('p', 'Saved policy for future matching launches only. Running work is unchanged. Browser preferences are separate; there is no cross-scope transaction.'));
      if (result.changes.length) {
        const list = make('ul'); result.changes.forEach(change => list.append(make('li', change.label + (change.selected ? '' : ' (dependent change)') + ': ' + show(change.before) + ' → ' + show(change.after)))); panel.append(list);
      } else panel.append(make('p', 'No change: selected values already match their replacement. Nothing will be written.'));
      if (result.configured_after) {
        const details = make('section'); details.append(make('h3', 'Queue policy after reset'));
        for (const name of ['engine', 'model', 'effort']) {
          const item = result.configured_after[name];
          if (item) details.append(make('p', name + ': ' + (item.known ? (item.value || 'Blank · native runtime default') : 'Unknown · resolved at launch') + ' · ' + item.source));
        }
        const f = result.configured_after.fallback;
        details.append(make('p', 'Failure fallback: ' + (f.enabled ? 'On' : 'Off') + ' · ' + f.source + '; headroom: ' + (f.headroom ? 'On' : 'Off') + ' · ' + f.headroom_source), make('p', result.configured_after.note));
        panel.append(details);
      }
      panel.append(make('p', result.undo_supported ? 'This change can be recovered in Configuration history while its sources remain compatible.' : 'Undo is not available for this selection: local metadata, unsupported history fields or current native compatibility rules prevent safe restoration. Use the canonical editor if recovery is required.'));
      const actions = make('div'); actions.className = 'settings-reset-actions';
      actions.append(button('Apply selected reset', 'settingsResetApply', apply), button('Cancel', 'settingsResetCancel', () => { clear(); status('Reset canceled. No configuration was written.'); focus('settingsResetPreviewButton'); })); panel.append(actions);
      status('Preview ready. Check the exact replacements and undo coverage.');
    } catch (e) { clear(); status('Preview unavailable. ' + e.message); }
    finally { lock(false); focus(pending?.changes.length ? 'settingsResetApply' : pending ? 'settingsResetCancel' : 'settingsResetPreviewButton'); }
  }
  async function apply() {
    if (busy || !pending?.changes.length) return;
    const selection = pending; lock(true);
    try {
      const result = await api({...selection.request, action: 'apply', preview_token: selection.preview_token});
      clear();
      if (!result.applied) { status('No change; nothing was written.'); return; }
      // Publication/journal completes before consumers are notified. This is not a browser reset.
      let refreshed = true;
      try { refreshed = await window.cccConfigurationHistory?.refreshConsumers(result); } catch (_) { refreshed = false; }
      if (result.scope === 'machine') window.dispatchEvent(new CustomEvent('ccc:configuration-saved', {detail: result}));
      else window.dispatchEvent(new CustomEvent('ccc:queue-policy-changed', {detail: {queue: result.target}}));
      window.cccConfigurationHistory?.refresh(); window.cccSettingsChanges?.refresh();
      view = null; get('settingsResetFields').replaceChildren();
      status('Reset saved. ' + (result.configuration_history?.state === 'recorded' ? 'Configuration history recorded.' : 'History status: ' + (result.configuration_history?.state || 'unconfirmed') + '; refresh history before relying on undo.') + (refreshed === false ? ' Some controls could not refresh; reopen their editor.' : '') + ' Refresh to review current overrides.');
    } catch (e) { clear(); status('Save was not confirmed. ' + e.message + ' Refresh configuration and history before retrying.'); }
    finally { lock(false); focus(view ? 'settingsResetPreviewButton' : 'settingsResetRefresh'); }
  }
  function mount() {
    const parent = get('settingsSection-preferences'); if (!parent || host) return;
    host = make('section'); host.id = 'settingsOperationalResets'; host.className = 'settings-operational-resets';
    host.append(make('h3', 'Reset selected operational overrides'));
    host.append(make('p', 'Review known host defaults or one queue’s exceptions before replacing them. This does not reset credentials, services or arbitrary configuration. Browser reset remains separate above.'));
    const where = make('section'); where.append(make('h3', 'Where this applies'), make('p', 'Owner: this CCC host, shared by its dashboard clients; or the explicitly selected queue, including manual queues. Applies to future matching launches. No restart or running-work change is performed. Host reset does not remove queue pins.')); host.append(where);
    const controls = make('div'); controls.className = 'settings-reset-actions';
    const label = make('label', 'Reset scope '); const select = make('select'); select.id = 'settingsResetScope'; select.setAttribute('aria-label', 'Reset scope'); const option = make('option', 'This CCC host'); option.value = '["machine",""]'; select.append(option); label.append(select);
    select.addEventListener('change', () => refresh()); controls.append(label, button('Refresh overrides', 'settingsResetRefresh', () => refresh(true))); host.append(controls);
    const error = make('p'); error.id = 'settingsResetRefreshError'; error.hidden = true; error.setAttribute('role', 'alert'); host.append(error);
    const coverage = make('p'); coverage.id = 'settingsResetCoverage'; host.append(coverage);
    const list = make('fieldset'); list.id = 'settingsResetFields'; list.setAttribute('aria-label', 'Saved fields eligible for reset'); host.append(list);
    host.append(button('Preview selected reset', 'settingsResetPreviewButton', preview));
    const state = make('p'); state.id = 'settingsResetStatus'; state.setAttribute('role', 'status'); host.append(state);
    const panel = make('div'); panel.id = 'settingsResetPreview'; panel.hidden = true; host.append(panel); parent.append(host);
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape' || !pending || busy || !host.open || !host.getClientRects().length || get('settingsModal')?.hidden) return;
      e.preventDefault(); e.stopImmediatePropagation(); clear(); status('Reset canceled. No configuration was written.'); focus('settingsResetPreviewButton');
    }, true);
    refresh();
  }
  window.cccSettingsResets = {mount, refresh};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
  new MutationObserver(mount).observe(document.documentElement, {childList: true, subtree: true});
})();
