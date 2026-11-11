/* Settings, On-call (preview flag `oncall`).
 *
 * Nothing here runs until the flag is on: no section, no rail entry, no
 * request. With it on, this file adds one Settings page and keeps it current
 * from GET /api/oncall/status. Every form is drawn from the field lists the
 * server sends, so a new channel or source type needs no markup here.
 *
 * Settings rules this page follows: switches are plain on/off, adding is
 * inline, nothing opens a popup or leaves Settings, and there is no
 * explanation text. The only prose is status the person needs (an error, a
 * saved note). Secrets are write-only: typed once, sent to the local Vault,
 * never read back.
 */
(() => {
  'use strict';

  const CHANNEL_NAMES = {email: 'Email', telegram: 'Telegram', ntfy: 'ntfy', imessage: 'iMessage',
    sms: 'SMS', whatsapp: 'WhatsApp', command: 'Your own command'};
  const SOURCE_NAMES = {github: 'GitHub issues', sentry: 'Sentry', posthog: 'PostHog', http: 'URL check', command: 'Command check'};
  const INTAKE_TYPES = ['email', 'sms', 'whatsapp'];
  const ALERT_SWITCHES = [['decisions', 'An agent needs a decision'], ['stopped', 'An agent stopped'],
    ['stuck', 'A queue stopped moving'], ['fixed', 'A bug was fixed']];
  const CUSTOMER_SWITCHES = [['approve_sends', 'Approve each message to a customer'],
    ['hold_unknown', 'Hold reports from people not listed'],
    ['notify_fixed', 'Tell the reporter when it is fixed']];
  const REFRESH_MS = 10000;

  let section = null, parts = null, state = null, decisions = [], timer = null, loading = false, retries = 0;

  const flagOn = () => { try { return !!(window.__CCC_FLAGS__ || {}).oncall; } catch (_) { return false; } };

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
    let data = {};
    try { data = await response.json(); } catch (_) { /* a non-JSON error page */ }
    if (!response.ok || data.ok === false) {
      const error = new Error(data.error || 'CCC answered HTTP ' + response.status);
      error.data = data;
      throw error;
    }
    return data;
  }

  function say(target, text, bad) {
    if (!target) return;
    target.textContent = text || '';
    target.classList.toggle('is-error', !!bad);
  }

  // ── building blocks ───────────────────────────────────────────────────
  function row(label, keywords) {
    const node = el('div', 'settings-row');
    if (keywords) node.dataset.keywords = ('on-call oncall ' + keywords).toLowerCase();
    const main = el('div', 'settings-row-main');
    main.append(el('div', 'settings-row-label', label));
    const control = el('div', 'settings-row-control');
    node.append(main, control);
    return {node, main, control, status: null};
  }

  function withStatus(r) {
    r.status = el('div', 'settings-row-desc oncall-status');
    r.status.setAttribute('role', 'status');
    r.main.append(r.status);
    return r;
  }

  function toggle(label, onChange) {
    const button = el('button', 'settings-toggle');
    button.type = 'button';
    button.setAttribute('role', 'switch');
    button.setAttribute('aria-label', label);
    const track = el('span', 'settings-toggle-track');
    track.append(el('span', 'settings-toggle-thumb'));
    button.append(track);
    button.addEventListener('click', async () => {
      const next = button.getAttribute('aria-checked') !== 'true';
      button.disabled = true;
      try { await onChange(next); } finally { button.disabled = false; }
    });
    setToggle(button, false);
    return button;
  }

  function setToggle(button, on) {
    button.classList.toggle('is-on', !!on);
    button.setAttribute('aria-checked', on ? 'true' : 'false');
  }

  function button(label, onClick, danger) {
    const node = el('button', 'settings-action-btn' + (danger ? ' danger' : ''), label);
    node.type = 'button';
    node.addEventListener('click', async () => {
      node.disabled = true;
      try { await onClick(); } finally { node.disabled = false; }
    });
    return node;
  }

  function input(placeholder, type) {
    const node = el('input', 'settings-text-input');
    node.type = type || 'text';
    node.placeholder = placeholder;
    node.setAttribute('aria-label', placeholder);
    node.autocomplete = type === 'password' ? 'new-password' : 'off';
    node.spellcheck = false;
    return node;
  }

  // Set a field's value unless the person is typing in it right now.
  function fill(node, value) {
    if (document.activeElement !== node && node.value !== (value || '')) node.value = value || '';
  }

  // ── saving ────────────────────────────────────────────────────────────
  async function saveConfig(patch, status) {
    try {
      const result = await api('POST', 'config', patch);
      say(status, (result.notes || []).join(' ') || 'Saved.');
    } catch (error) {
      say(status, error.message, true);
    }
    await load();
  }

  async function post(name, body, status, done) {
    try {
      const result = await api('POST', name, body);
      say(status, done ? done(result) : (result.note || 'Saved.'));
      await load();
      return result;
    } catch (error) {
      say(status, error.message, true);
      await load();
      return null;
    }
  }

  // ── inline "add" forms, drawn from the server's field lists ───────────
  function addForm(kind, names, typesOf) {
    const wrap = withStatus(row(kind === 'channel' ? 'Add a channel' : 'Add a check',
      kind === 'channel' ? 'add channel email telegram ntfy imessage sms whatsapp message alert'
        : 'add monitoring github issues sentry posthog uptime url check command'));
    wrap.node.classList.add('oncall-add-row');
    const pick = el('select', 'settings-select');
    pick.setAttribute('aria-label', kind === 'channel' ? 'Channel type' : 'Check type');
    const fields = el('div', 'oncall-form');
    wrap.main.insertBefore(fields, wrap.status);
    wrap.control.append(pick);
    let inputs = [];

    function drawTypes() {
      const current = pick.value;
      pick.replaceChildren();
      const none = el('option', null, kind === 'channel' ? 'Choose a channel' : 'Choose a check');
      none.value = '';
      pick.append(none);
      for (const type of Object.keys(typesOf() || {})) {
        const option = el('option', null, names[type] || type);
        option.value = type;
        pick.append(option);
      }
      pick.value = current;
    }

    function drawFields() {
      fields.replaceChildren();
      inputs = [];
      const type = pick.value;
      if (!type) return;
      const list = ((typesOf() || {})[type] || []).slice();
      if (kind === 'source') list.push({name: 'queue', label: 'Queue', secret: false, optional: true, placeholder: ''});
      for (const field of list) {
        const node = input(field.label + (field.optional ? ' (optional)' : ''), field.secret ? 'password' : 'text');
        if (field.placeholder && !field.secret) node.title = field.placeholder;
        fields.append(node);
        inputs.push({field, node});
      }
      const add = button('+ Add', submit);
      add.classList.add('oncall-form-wide');
      fields.append(add);
    }

    async function submit() {
      const entry = {type: pick.value}, secrets = {};
      for (const {field, node} of inputs) {
        const value = node.value.trim();
        if (!value) continue;
        if (field.secret) secrets[field.name] = value; else entry[field.name] = value;
      }
      say(wrap.status, 'Checking…');
      const result = await post(kind, {[kind]: entry, secrets}, wrap.status,
        r => 'Added. ' + (r.note || ''));
      // Accepted: the form, and the secret typed into it, are gone from the page.
      // Refused: what was typed stays so the person can fix one field and retry.
      if (result) { pick.value = ''; drawFields(); }
    }

    pick.addEventListener('change', () => { say(wrap.status, ''); drawFields(); });
    return {node: wrap.node, drawTypes};
  }

  // ── the page ──────────────────────────────────────────────────────────
  function build() {
    const root = el('section', 'settings-section');
    root.id = 'settingsSection-oncall';
    root.dataset.sectionId = 'oncall';
    root.setAttribute('role', 'tabpanel');
    root.append(el('div', 'settings-section-eyebrow', 'On-call'));
    const p = {};

    p.master = withStatus(row('On-call', 'agents fix bugs alerts decisions preview'));
    p.masterToggle = toggle('On-call', next => saveConfig({enabled: next}, p.master.status));
    p.master.control.append(p.masterToggle);

    p.queue = withStatus(row('Queue for bug tickets', 'queue tickets bugs'));
    p.queueInput = input('SHOP');
    p.queueInput.addEventListener('change', () => saveConfig({queue: p.queueInput.value.trim()}, p.queue.status));
    p.queue.control.append(p.queueInput);

    p.repo = withStatus(row('Code folder', 'repo repository folder code path'));
    p.repoInput = input('~/projects/example');
    p.repoInput.addEventListener('change', () => saveConfig({repo: p.repoInput.value.trim()}, p.repo.status));
    p.repo.control.append(p.repoInput);

    p.minor = withStatus(row('Agents decide small reversible things', 'minor decisions reversible auto approve'));
    p.minorToggle = toggle('Agents decide small reversible things',
      next => saveConfig({minor_decisions: {enabled: next}}, p.minor.status));
    p.minor.control.append(p.minorToggle);
    p.decisions = el('div', 'oncall-list');

    p.waitingLabel = el('div', 'settings-subsection-label', 'Waiting on you');
    p.waiting = el('div', 'oncall-list');

    p.channelsLabel = el('div', 'settings-subsection-label', 'Message channels');
    p.channels = el('div', 'oncall-list');
    p.channelForm = addForm('channel', CHANNEL_NAMES, () => state && state.channel_types);

    p.sourcesLabel = el('div', 'settings-subsection-label', 'Monitoring');
    p.sources = el('div', 'oncall-list');
    p.sourceForm = addForm('source', SOURCE_NAMES, () => state && state.source_types);

    p.customersLabel = el('div', 'settings-subsection-label', 'Customers');
    p.customerToggles = {};
    const customerRows = CUSTOMER_SWITCHES.map(([key, label]) => {
      const r = withStatus(row(label, 'customers approve hold reporter ' + key));
      p.customerToggles[key] = toggle(label, next => saveConfig({customers: {[key]: next}}, r.status));
      r.control.append(p.customerToggles[key]);
      return r.node;
    });
    p.known = el('div', 'oncall-list');
    const addCustomer = withStatus(row('Add a customer', 'add customer known address email phone domain'));
    const address = input('dana@example.com or @example.com');
    const name = input('Name (optional)');
    addCustomer.control.append(address, name, button('+ Add', async () => {
      const result = await post('customer', {action: 'add', address: address.value.trim(), name: name.value.trim()},
        addCustomer.status, () => 'Added.');
      if (result) { address.value = ''; name.value = ''; }
    }));

    p.alertsLabel = el('div', 'settings-subsection-label', 'Message me when');
    p.alertToggles = {};
    const alertRows = ALERT_SWITCHES.map(([key, label]) => {
      const r = withStatus(row(label, 'alerts notify message ' + key));
      p.alertToggles[key] = toggle(label, next => saveConfig({alerts: {[key]: next}}, r.status));
      r.control.append(p.alertToggles[key]);
      return r.node;
    });
    p.summary = withStatus(row('Daily summary', 'daily summary morning'));
    p.summaryToggle = toggle('Daily summary',
      next => saveConfig({daily_summary: {enabled: next}}, p.summary.status));
    p.summaryTime = input('Time', 'time');
    p.summaryTime.setAttribute('aria-label', 'Daily summary time');
    p.summaryTime.addEventListener('change', () =>
      saveConfig({daily_summary: {time: p.summaryTime.value}}, p.summary.status));
    p.summary.control.append(p.summaryToggle, p.summaryTime);
    p.quiet = withStatus(row('Quiet hours', 'quiet hours night do not disturb'));
    p.quietStart = input('Start', 'time');
    p.quietEnd = input('End', 'time');
    const saveQuiet = () => {
      const start = p.quietStart.value, end = p.quietEnd.value;
      if (start && end) saveConfig({quiet_hours: {start, end}}, p.quiet.status);
      else if (!start && !end) saveConfig({quiet_hours: null}, p.quiet.status);
    };
    p.quietStart.addEventListener('change', saveQuiet);
    p.quietEnd.addEventListener('change', saveQuiet);
    p.quiet.control.append(p.quietStart, p.quietEnd);

    root.append(p.master.node, p.queue.node, p.repo.node, p.minor.node, p.decisions,
      p.waitingLabel, p.waiting,
      p.channelsLabel, p.channels, p.channelForm.node,
      p.sourcesLabel, p.sources, p.sourceForm.node,
      p.customersLabel, ...customerRows, p.known, addCustomer.node,
      p.alertsLabel, ...alertRows, p.summary.node, p.quiet.node);
    return {root, p};
  }

  function listRow(label, value, error) {
    const r = row(label);
    if (value) r.main.append(el('div', 'settings-row-desc', value));
    if (error) {
      const bad = el('div', 'settings-row-desc oncall-status is-error', error);
      r.main.append(bad);
    }
    r.status = el('div', 'settings-row-desc oncall-status');
    r.status.setAttribute('role', 'status');
    r.main.append(r.status);
    return r;
  }

  function drawChannels() {
    const p = parts;
    p.channels.replaceChildren();
    for (const channel of state.channels || []) {
      const r = listRow(CHANNEL_NAMES[channel.type] || channel.type, channel.owner, channel.error);
      const enabled = toggle((CHANNEL_NAMES[channel.type] || channel.type) + ' on',
        next => post('channel', {action: 'toggle', channel: {id: channel.id, enabled: next}}, r.status));
      setToggle(enabled, channel.enabled);
      r.control.append(
        button('Test', () => post('test', {channel: channel.id}, r.status,
          result => (result.results || []).map(x => x.ok ? 'Sent.' : 'Failed: ' + x.error).join(' '))),
        enabled,
        button('Remove', () => post('channel', {action: 'remove', channel: {id: channel.id}}, r.status), true));
      p.channels.append(r.node);
      if (INTAKE_TYPES.includes(channel.type)) {
        const intake = listRow('Read customer reports from ' + (channel.owner || channel.id));
        const sw = toggle('Read customer reports from ' + channel.id,
          next => post('channel', {action: 'toggle', channel: {id: channel.id, intake: next}}, intake.status));
        setToggle(sw, channel.intake);
        intake.control.append(sw);
        p.channels.append(intake.node);
      }
    }
  }

  function drawSources() {
    const p = parts;
    p.sources.replaceChildren();
    for (const source of state.sources || []) {
      const r = listRow(SOURCE_NAMES[source.type] || source.type,
        source.queue ? 'Files into ' + source.queue : '', source.error);
      const enabled = toggle((SOURCE_NAMES[source.type] || source.type) + ' on',
        next => post('source', {action: 'toggle', source: {id: source.id, enabled: next}}, r.status));
      setToggle(enabled, source.enabled);
      r.control.append(enabled,
        button('Remove', () => post('source', {action: 'remove', source: {id: source.id}}, r.status), true));
      p.sources.append(r.node);
    }
  }

  function drawLists() {
    const p = parts;
    const config = state.config || {};
    p.known.replaceChildren();
    for (const customer of (config.customers || {}).known || []) {
      const r = listRow(customer.name || customer.address, customer.name ? customer.address : '');
      r.control.append(button('Remove',
        () => post('customer', {action: 'remove', address: customer.address}, r.status), true));
      p.known.append(r.node);
    }
    const waiting = (state.waiting || []).filter(item => item.needs_reply);
    p.waitingLabel.hidden = !waiting.length;
    p.waiting.replaceChildren();
    for (const item of waiting) p.waiting.append(listRow('#' + item.id, item.line).node);
    p.decisions.replaceChildren();
    for (const decision of decisions) {
      const parts = decision.recommend
        ? ['Decided: ' + decision.recommend, decision.undo && 'Undo: ' + decision.undo]
        : [decision.state, decision.reason];
      p.decisions.append(listRow(decision.ref, parts.filter(Boolean).join(' · ')).node);
    }
  }

  function draw() {
    if (!parts || !state) return;
    const p = parts, config = state.config || {};
    setToggle(p.masterToggle, state.enabled);
    if (!state.wt) say(p.master.status, 'WatchTower is not installed, so no agent will pick tickets up.', true);
    else if (state.enabled && state.paused) say(p.master.status, 'Paused. Reply resume on any channel to continue.');
    else if (p.master.status.classList.contains('is-error') && state.wt) say(p.master.status, '');
    fill(p.queueInput, state.queue);
    fill(p.repoInput, config.repo);
    setToggle(p.minorToggle, state.minor_decisions);
    setToggle(p.summaryToggle, (config.daily_summary || {}).enabled);
    fill(p.summaryTime, (config.daily_summary || {}).time || "09:00");
    for (const [key] of ALERT_SWITCHES) setToggle(p.alertToggles[key], (config.alerts || {})[key] !== false);
    for (const [key] of CUSTOMER_SWITCHES) setToggle(p.customerToggles[key], (config.customers || {})[key] !== false);
    const quiet = config.quiet_hours || {};
    fill(p.quietStart, quiet.start);
    fill(p.quietEnd, quiet.end);
    p.channelForm.drawTypes();
    p.sourceForm.drawTypes();
    // Rebuilding a list drops focus, so wait while the person is on a control inside it.
    for (const [box, paint] of [[p.channels, drawChannels], [p.sources, drawSources]]) {
      if (!box.contains(document.activeElement)) paint();
    }
    if (![p.known, p.waiting, p.decisions].some(box => box.contains(document.activeElement))) drawLists();
  }

  async function load() {
    if (loading || !flagOn()) return;
    loading = true;
    try {
      state = await api('GET', 'status');
      decisions = state.minor_decisions ? ((await api('GET', 'decisions')).decisions || []) : [];
      draw();
    } catch (error) {
      if (parts) say(parts.master.status, error.message, true);
    } finally {
      loading = false;
    }
  }

  const visible = () => !!section && !section.hidden && section.offsetParent !== null;

  function mount() {
    const pane = document.getElementById('settingsPane');
    const destinations = window.cccSettingsDestinations;
    // The Settings pages are assembled by app.js; wait until they exist.
    if (!pane || !Array.isArray(destinations)) return false;
    if (!section) {
      if (!document.getElementById('oncallCss')) {
        const link = el('link');
        link.id = 'oncallCss'; link.rel = 'stylesheet'; link.href = '/static/oncall.css';
        document.head.append(link);
      }
      // Its own page at the end of the Settings rail, next to the other previews.
      if (!destinations.some(d => d[0] === 'oncall')) destinations.push(['oncall', 'On-call', ['oncall']]);
      const built = build();
      section = built.root; parts = built.p;
      pane.append(section);
    }
    return true;
  }

  function sync() {
    const on = flagOn();
    if (on && !mount()) {
      if (retries++ < 60) setTimeout(sync, 500);
      return;
    }
    if (section) section.hidden = !on;
    const nav = document.getElementById('settingsDestination-oncall');
    if (nav) nav.hidden = !on;
    if (on && !timer) {
      load();
      timer = setInterval(() => { if (visible()) load(); }, REFRESH_MS);
    } else if (!on && timer) {
      clearInterval(timer); timer = null;
    }
  }

  window.addEventListener('ccc:flags', sync);
  document.addEventListener('click', event => {
    if (flagOn() && event.target.closest && event.target.closest('#settingsDestination-oncall')) load();
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', sync); else sync();
  window.cccOncallSettings = {refresh: load};
})();
