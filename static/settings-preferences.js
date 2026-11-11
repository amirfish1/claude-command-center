/* Settings Workspace > Preferences. Standalone, browser-only integration.
 * Include after app.js. Auto-mounts when #settingsRail/#settingsPane exist;
 * window.cccSettingsPreferences exposes mount() and refresh() for hosts.
 * Reads only GET /api/settings/preferences/registry. Successful local applies
 * dispatch document event ccc:preferences-applied with {operation, keys}.
 * Host reloads the dashboard on that event so startup consumers reread storage.
 * Profiles use the Python ledger schema but accept ONLY supported browser keys.
 * No arbitrary localStorage enumeration or free-form values in export/history.
 * localStorage writes are not a database transaction: failures attempt rollback.
 */
(() => {
  'use strict';
  if (window.cccSettingsPreferences) return;
  const HISTORY_KEY = 'ccc-preferences-history-v1';
  const LIMIT = 20;
  const MAX_BYTES = 65536;
  const FORMAT = 'ccc-preferences';
  // Fixed client boundary: a future registry entry cannot widen local access.
  const SUPPORTED = Object.freeze({
    theme: ['ccc-theme', 'Theme', ['dark', 'light', 'system']],
    font: ['ccc-font', 'Font', ['system', 'mono']],
    sounds_enabled: ['ccc-sounds-enabled', 'Completion sounds', true],
    compact_rows: ['ccc-compact-rows', 'Compact rows', true],
    wrap_titles: ['ccc-wrap-titles', 'Wrap titles', false],
    separate_tabs: ['ccc-separate-tabs', 'Separate issue and queue tabs', false],
    queue_rhs_list: ['ccc-queue-rhs-list', 'Queue health list', false],
    voice_mobile_button: ['ccc-voice-mobile-btn', 'Mobile voice button', true],
    chips_mode: ['ccc-chips-mode', 'Chip colors', ['color', 'level', 'muted']],
    coding_density: ['ccc-coding-density', 'Coding row density', ['compact', 'cozy', 'detailed']],
    workers_density: ['ccc-workers-density', 'Worker row density', ['compact', 'cozy', 'detailed']],
    rows_spacing: ['ccc-rows-spacing', 'Row spacing', ['cozy', 'roomy', 'airy']],
  });
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const exact = (object, keys) => plain(object) && Object.keys(object).length === keys.length && keys.every(key => own(object, key));
  const copy = value => JSON.parse(JSON.stringify(value));
  const canonical = value => JSON.stringify(value, Object.keys(value).sort());
  const message = text => { throw new Error(text); };
  let panel, tab, specs, defaults, preview, loading;
  let refreshGeneration = 0;

  function valid(key, value) {
    const supported = SUPPORTED[key];
    if (!supported || !own(specs, key)) return false;
    return Array.isArray(supported[2])
      ? typeof value === 'string' && supported[2].includes(value) && specs[key].choices.includes(value)
      : typeof value === 'boolean';
  }
  function values(object) {
    if (!plain(object) || Object.keys(object).some(key => !own(SUPPORTED, key) || !valid(key, object[key]))) {
      message('Use only supported browser preferences with valid typed values.');
    }
    return copy(object);
  }
  function encode(key, value) {
    if (typeof value !== 'boolean') return value;
    return ['separate_tabs', 'queue_rhs_list'].includes(key) ? (value ? 'on' : 'off') : (value ? '1' : '0');
  }
  function decode(key, raw) {
    if (raw === null) return defaults[key];
    if (Array.isArray(SUPPORTED[key][2])) return valid(key, raw) ? raw : defaults[key];
    if (['separate_tabs', 'queue_rhs_list'].includes(key)) return raw === 'on';
    return SUPPORTED[key][2] ? raw !== '0' : raw === '1';
  }
  function parse(raw) {
    if (typeof raw !== 'string' || new Blob([raw]).size > MAX_BYTES) message('Profile or history is too large (maximum 64 KB).');
    let result;
    try { result = JSON.parse(raw); } catch (_) { message('Enter a valid JSON profile.'); }
    // JSON.parse accepts duplicate fields. Reject that ambiguity, including
    // escaped spellings of the same field, before trusting profile/history data.
    const stack = [];
    for (const token of raw.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]/g) || []) {
      const current = stack.at(-1);
      if (token === '{' || token === '[') stack.push({object: token === '{', key: true, seen: new Set()});
      else if (token === '}' || token === ']') stack.pop();
      else if (token === ',' && current?.object) current.key = true;
      else if (token.startsWith('"') && current?.object && current.key) {
        const key = JSON.parse(token);
        if (current.seen.has(key)) message('Duplicate JSON fields are not supported.');
        current.seen.add(key); current.key = false;
      }
    }
    return result;
  }
  function readHistory(raw) {
    if (raw === null) return {version: 1, sequence: 0, entries: []};
    const data = parse(raw);
    if (!exact(data, ['version', 'sequence', 'entries']) || data.version !== 1 || !Number.isSafeInteger(data.sequence) || data.sequence < 0 || !Array.isArray(data.entries) || data.entries.length > LIMIT) {
      message('Local preference history is invalid. No changes were applied.');
    }
    let previous;
    for (const entry of data.entries) {
      if (!exact(entry, ['sequence', 'operation', 'before', 'after']) || !['import', 'reset', 'undo'].includes(entry.operation) || !Number.isSafeInteger(entry.sequence) || entry.sequence < 1 || entry.sequence > data.sequence) {
        message('Local preference history is invalid. No changes were applied.');
      }
      values(entry.before); values(entry.after);
      // Existing Settings controls can legitimately change values between
      // workbench transactions. Sequence order remains strict, not adjacency.
      if (previous && entry.sequence !== previous.sequence + 1) {
        message('Local preference history is inconsistent. No changes were applied.');
      }
      previous = entry;
    }
    if ((!previous && data.sequence !== 0) || (previous && previous.sequence !== data.sequence)) message('Local preference history is inconsistent.');
    return data;
  }
  function snapshot() {
    const raw = {}, effective = {}, overrides = {}, noncanonical = [];
    for (const key of Object.keys(specs)) {
      raw[key] = localStorage.getItem(SUPPORTED[key][0]);
      effective[key] = decode(key, raw[key]);
      if (raw[key] !== null) {
        overrides[key] = effective[key];
        if (raw[key] !== encode(key, effective[key])) noncanonical.push(key);
      }
    }
    const historyRaw = localStorage.getItem(HISTORY_KEY);
    return {raw, effective, overrides, noncanonical, historyRaw, history: readHistory(historyRaw),
      // Raw values stay in memory only, never in exported profiles or history.
      revision: JSON.stringify([raw, historyRaw, defaults])};
  }
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function status(text, error = false) {
    const target = panel.querySelector('[data-pref-status]');
    target.textContent = text;
    target.classList.toggle('is-error', error);
  }
  function showValue(value) { return typeof value === 'boolean' ? (value ? 'On' : 'Off') : value; }
  function table(rows, headings) {
    const result = node('table');
    const head = node('thead'), header = node('tr');
    headings.forEach(text => { const cell = node('th', text); cell.scope = 'col'; header.append(cell); });
    head.append(header); result.append(head);
    const body = node('tbody');
    rows.forEach(row => {
      const tr = node('tr');
      row.forEach((text, index) => { const cell = node(index ? 'td' : 'th', text); if (!index) cell.scope = 'row'; tr.append(cell); });
      body.append(tr);
    });
    result.append(body); return result;
  }
  function clearPreview() {
    preview = null;
    const host = panel.querySelector('[data-pref-preview]');
    host.hidden = true; host.replaceChildren();
  }
  function render() {
    const state = snapshot();
    const changed = Object.keys(specs).filter(key => state.effective[key] !== defaults[key]);
    const host = panel.querySelector('[data-pref-changed]');
    host.replaceChildren(node('h3', 'Changed from defaults'));
    host.append(changed.length ? table(changed.map(key => [SUPPORTED[key][1], showValue(state.effective[key]), showValue(defaults[key])]), ['Preference', 'Current', 'Default']) : node('p', 'Supported preferences match their defaults.'));
    if (state.noncanonical.length) host.append(node('p', 'Some saved values use an unsupported encoding. Profiles use their effective values; reset removes the saved overrides. Undo restores supported encodings.'));
    panel.querySelector('[data-pref-history]').textContent = `${state.history.entries.length} retained changes in this browser (latest ${LIMIT}). Undo restores the latest workbench change; undoing undo acts as redo.`;
    panel.querySelector('[data-pref-action="undo"]').disabled = !state.history.entries.length;
  }
  function makePreview(operation, target) {
    const state = snapshot();
    const after = operation === 'import' ? {...state.overrides, ...values(target)} : values(target);
    const changes = Object.keys(specs).filter(key => own(state.overrides, key) !== own(after, key) || state.overrides[key] !== after[key] || (own(target, key) && state.raw[key] !== encode(key, after[key])) || (operation === 'reset' && state.raw[key] !== null));
    preview = {operation, revision: state.revision, before: state.overrides, after, keys: changes};
    const host = panel.querySelector('[data-pref-preview]'); host.hidden = false; host.replaceChildren();
    const title = {import: 'Import preview', reset: 'Reset preview', undo: 'Latest undo preview'}[operation];
    host.append(node('h3', title));
    if (changes.length) host.append(table(changes.map(key => [SUPPORTED[key][1], showValue(state.effective[key]) + (own(state.overrides, key) ? ' (saved)' : ' (default)'), showValue(own(after, key) ? after[key] : defaults[key]) + (own(after, key) ? ' (saved)' : ' (default)')]), ['Preference', 'Before', 'After']));
    else host.append(node('p', 'No supported preferences would change.'));
    if (changes.length) host.append(node('p', 'Applying saves these preferences and reloads the dashboard.'));
    const apply = node('button', `Apply ${operation} and reload`, 'settings-action-btn eng-primary'); apply.type = 'button'; apply.dataset.prefAction = 'apply'; apply.disabled = !changes.length;
    const cancel = node('button', 'Cancel', 'settings-action-btn'); cancel.type = 'button'; cancel.dataset.prefAction = 'cancel';
    host.append(apply, cancel);
    status(changes.length ? 'Review the preview. Applying saves your changes, then reloads the dashboard.' : 'No changes to apply. The dashboard will not reload.');
    host.focus();
  }
  function importProfile() {
    const profile = parse(panel.querySelector('#settingsPreferencesProfile').value);
    if (!exact(profile, ['format', 'version', 'values']) || profile.format !== FORMAT || profile.version !== 1) message('Use a version 1 CCC preference profile without extra metadata.');
    makePreview('import', values(profile.values));
  }
  function undo() {
    const state = snapshot();
    const latest = state.history.entries.at(-1);
    if (!latest) message('No retained workbench change to undo.');
    if (canonical(state.overrides) !== canonical(latest.after) || state.noncanonical.length) message('Preferences changed outside this workbench. Latest undo is unavailable; review current values.');
    makePreview('undo', latest.before);
  }
  function exportProfile() {
    const state = snapshot();
    const text = JSON.stringify({format: FORMAT, version: 1, values: state.effective}, null, 2);
    panel.querySelector('#settingsPreferencesProfile').value = text;
    const blobURL = URL.createObjectURL(new Blob([text + '\n'], {type: 'application/json'}));
    const link = node('a'); link.href = blobURL; link.download = 'ccc-browser-preferences.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(blobURL), 1000);
    status('Exported supported browser preferences. The JSON is also available below.');
  }
  async function apply() {
    const selected = preview;
    if (!selected || !selected.keys.length) return;
    const commit = () => {
      const state = snapshot();
      if (state.revision !== selected.revision) message('Preferences changed since this preview. Preview again before applying.');
      values(selected.after);
      if (!Number.isSafeInteger(state.history.sequence + 1)) message('Local preference history has reached its revision limit.');
      const history = {version: 1, sequence: state.history.sequence + 1, entries: [...state.history.entries,
        {sequence: state.history.sequence + 1, operation: selected.operation, before: selected.before, after: selected.after}].slice(-LIMIT)};
      const historyJSON = JSON.stringify(history);
      if (new Blob([historyJSON]).size > MAX_BYTES) message('Local preference history is too large. No changes were applied.');
      const written = [];
      try {
        for (const key of selected.keys) {
          const storageKey = SUPPORTED[key][0];
          if (own(selected.after, key)) localStorage.setItem(storageKey, encode(key, selected.after[key]));
          else localStorage.removeItem(storageKey);
          written.push(key);
        }
        localStorage.setItem(HISTORY_KEY, historyJSON);
      } catch (_) {
        let recovered = true;
        for (const key of written.reverse()) {
          try {
            if (state.raw[key] === null) localStorage.removeItem(SUPPORTED[key][0]);
            else localStorage.setItem(SUPPORTED[key][0], state.raw[key]);
          } catch (_) { recovered = false; }
        }
        message(recovered ? 'Save failed. Changes were rolled back; check browser storage and try again.' : 'Save failed and rollback was incomplete. Review browser preferences before trying again.');
      }
      clearPreview(); render();
      status(`Applied ${selected.operation} to ${selected.keys.length} supported preferences in this browser. Reloading the dashboard…`);
      // The host may reload immediately. All synchronous preference/history
      // writes and local UI work must finish before this final notification.
      document.dispatchEvent(new CustomEvent('ccc:preferences-applied', {bubbles: true, detail: {operation: selected.operation, keys: [...selected.keys]}}));
    };
    if (navigator.locks) await navigator.locks.request('ccc-browser-preferences', commit);
    else commit();
  }
  function protect(action) {
    return Promise.resolve().then(action).catch(error => {
      clearPreview(); status(error instanceof Error ? error.message : 'Could not update browser preferences.', true);
    });
  }
  async function refresh() {
    if (!panel) return;
    const generation = ++refreshGeneration;
    clearPreview();
    panel.querySelectorAll('[data-pref-action]').forEach(button => { button.disabled = true; });
    status('Loading supported preferences…');
    try {
      const response = await fetch('/api/settings/preferences/registry', {headers: {Accept: 'application/json'}, cache: 'no-store'});
      if (!response.ok) message('Could not load supported preferences. Try Refresh.');
      const data = await response.json();
      if (!plain(data.registry) || !plain(data.defaults)) message('The preference registry is unavailable. Try Refresh.');
      const next = {}, nextDefaults = {};
      for (const key of Object.keys(SUPPORTED)) {
        const spec = data.registry[key];
        if (!spec) continue;
        const expected = SUPPORTED[key];
        if (spec.scope !== 'browser' || spec.portable !== true || spec.source_key !== expected[0] || spec.type !== (Array.isArray(expected[2]) ? 'enum' : 'boolean') || (Array.isArray(expected[2]) && (!Array.isArray(spec.choices) || spec.choices.some(value => !expected[2].includes(value))))) {
          message('The supported browser registry is incompatible. No storage was changed.');
        }
        next[key] = spec; nextDefaults[key] = data.defaults[key];
      }
      if (!Object.keys(next).length) message('No supported browser preferences are available.');
      if (generation !== refreshGeneration) return;
      specs = next; defaults = nextDefaults;
      Object.keys(specs).forEach(key => { if (!valid(key, defaults[key])) message('The browser defaults are invalid. No storage was changed.'); });
      const list = panel.querySelector('[data-pref-supported]');
      list.textContent = Object.keys(specs).map(key => SUPPORTED[key][1]).join(', ') + '.';
      render();
      panel.querySelectorAll('[data-pref-action]').forEach(button => { button.disabled = false; });
      panel.querySelector('[data-pref-action="undo"]').disabled = !snapshot().history.entries.length;
      status('Ready. Profiles and history contain only the supported preferences listed below.');
    } catch (_) {
      if (generation !== refreshGeneration) return;
      specs = null; defaults = null;
      status('Could not load valid preferences or local history. No changes were made. Check browser storage or try Refresh.', true);
    } finally {
      if (generation === refreshGeneration) panel.querySelector('[data-pref-action="refresh"]').disabled = false;
    }
  }
  function mount() {
    if (panel) return panel;
    const rail = document.getElementById('settingsRail'), pane = document.getElementById('settingsPane');
    if (!rail || !pane) return null;
    tab = node('button', 'Preferences', 'settings-rail-item'); tab.type = 'button'; tab.id = 'settingsRailTab-preferences'; tab.dataset.sectionTarget = 'preferences'; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', 'settingsSection-preferences'); tab.setAttribute('aria-selected', 'false');
    const position = () => {
      const anchor = ['voice', 'notifications', 'layout', 'appearance'].map(id => rail.querySelector(`[data-section-target="${id}"]`)).find(Boolean);
      if (anchor && anchor.nextElementSibling !== tab) anchor.after(tab);
      else if (!tab.isConnected) rail.append(tab);
    };
    position(); new MutationObserver(position).observe(rail, {childList: true});
    panel = node('section', undefined, 'settings-section ccc-prefs'); panel.id = 'settingsSection-preferences'; panel.dataset.sectionId = 'preferences'; panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', tab.id); panel.setAttribute('aria-hidden', 'true');
    panel.innerHTML = `
      <h2 class="settings-section-eyebrow">Preferences</h2>
      <p class="settings-section-note"><strong>This browser.</strong> Review, transfer or reset the supported preferences below. Server settings, credentials and session data are excluded.</p>
      <div class="settings-row ccc-prefs-row"><div class="settings-row-main"><div class="settings-row-label">Preferences</div></div><div class="ccc-prefs-actions settings-row-control"><button type="button" class="settings-action-btn" data-pref-action="export" disabled>Export</button><button type="button" class="settings-action-btn" data-pref-action="pick">Import</button><button type="button" class="settings-action-btn" data-pref-action="refresh">Refresh</button><button type="button" class="settings-action-btn" data-pref-action="reset" disabled>Preview reset</button><button type="button" class="settings-action-btn" data-pref-action="undo" disabled>Preview latest undo</button></div></div>
      <p data-pref-status role="status" aria-live="polite"></p>
      <div data-pref-changed></div>
      <section><h4>Supported preferences</h4><p data-pref-supported></p></section>
      <div class="ccc-prefs-import settings-row"><div class="settings-row-main"><label for="settingsPreferencesProfile">Browser preference profile (JSON)</label><p id="settingsPreferencesProfileHelp">Import merges supplied preferences. Reset clears all supported saved overrides. Applying an import, reset or undo saves the changes and reloads the dashboard.</p><textarea id="settingsPreferencesProfile" rows="6" spellcheck="false" aria-describedby="settingsPreferencesProfileHelp" placeholder='{"format":"ccc-preferences","version":1,"values":{"theme":"light"}}'></textarea><label for="settingsPreferencesFile">Choose a JSON profile</label><input id="settingsPreferencesFile" type="file" accept="application/json,.json"><button type="button" class="settings-action-btn" data-pref-action="import" disabled>Preview import</button></div></div>
      <div data-pref-preview tabindex="-1" aria-label="Preference change preview" hidden></div>
      <p data-pref-history></p>`;
    const style = node('style'); style.textContent = `
      .ccc-prefs {color:var(--text,#e6edf3);font:13px/1.5 var(--font-ui,system-ui);min-width:0}
      .ccc-prefs h2 {font-size:20px;margin:0 0 12px}.ccc-prefs h3 {font-size:14px;margin:24px 0 10px}
      .ccc-prefs p {line-height:1.5;max-width:72ch;overflow-wrap:anywhere}
      .ccc-prefs-actions {display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}
      .ccc-prefs button {cursor:pointer;min-height:36px}.ccc-prefs button:disabled {cursor:default;opacity:.5}
      .ccc-prefs table {width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed}
      .ccc-prefs th,.ccc-prefs td {text-align:left;vertical-align:top;padding:10px 8px;border-bottom:1px solid var(--border,#30363d);overflow-wrap:anywhere}
      .ccc-prefs th:first-child {width:44%;padding-left:0}.ccc-prefs thead {color:var(--text-muted,#aeb8c4)}
      .ccc-prefs details {margin:18px 0}.ccc-prefs summary {cursor:pointer}.ccc-prefs label {display:block;font-weight:500;margin:12px 0 6px}
      .ccc-prefs textarea {box-sizing:border-box;width:100%;resize:vertical;border:1px solid var(--border,#30363d);border-radius:6px;padding:10px;background:var(--bg,#161b22);color:inherit;font:13px/1.5 monospace}
      .ccc-prefs input[type=file] {display:block;max-width:100%;margin:8px 0 12px}
      .ccc-prefs [data-pref-preview] {margin-top:20px;border-top:1px solid var(--border,#30363d);padding-bottom:12px}
      .ccc-prefs [data-pref-preview] button {margin:12px 8px 0 0}.ccc-prefs [hidden] {display:none}
      .ccc-prefs [data-pref-status].is-error {color:var(--red,#ff7b72)}
      .ccc-prefs :is(button,textarea,input,summary):focus-visible {outline:2px solid var(--accent,#58a6ff);outline-offset:3px}
      @media(max-width:500px){.ccc-prefs th,.ccc-prefs td{padding:8px 4px}.ccc-prefs-actions button{flex:1 1 44%}}
    `;
    panel.prepend(style); pane.append(panel);
    panel.addEventListener('click', event => {
      const button = event.target.closest('[data-pref-action]');
      if (!button || button.disabled) return;
      const actions = {refresh, export: exportProfile, pick: () => { const file = panel.querySelector('#settingsPreferencesFile'); file.value = ''; file.click(); }, import: importProfile, reset: () => makePreview('reset', {}), undo, apply, cancel: clearPreview};
      protect(actions[button.dataset.prefAction]);
    });
    panel.querySelector('#settingsPreferencesFile').addEventListener('change', event => protect(async () => {
      clearPreview(); const file = event.target.files[0]; if (!file) return;
      if (file.size > MAX_BYTES) message('Profile is too large (maximum 64 KB).');
      panel.querySelector('#settingsPreferencesProfile').value = await file.text();
      importProfile();
    }));
    panel.querySelector('#settingsPreferencesProfile').addEventListener('input', clearPreview);
    tab.addEventListener('click', () => protect(() => { if (specs) render(); }));
    window.addEventListener('storage', event => {
      if (!specs || (event.key !== null && event.key !== HISTORY_KEY && !Object.keys(specs).some(key => SUPPORTED[key][0] === event.key))) return;
      protect(render); // Keep an open preview so Apply can explicitly reject staleness.
    });
    loading = refresh(); return panel;
  }
  window.cccSettingsPreferences = Object.freeze({mount, refresh: () => panel ? refresh() : (mount(), loading)});
  if (!mount()) {
    const observer = new MutationObserver(() => { if (mount()) observer.disconnect(); });
    observer.observe(document.documentElement, {childList: true, subtree: true});
  }
})();
