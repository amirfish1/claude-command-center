/* Read-only launch explanation on existing Settings surfaces. */
(() => {
  'use strict';
  let mounted = false, sequence = 0, timer, observedAt, plans = [];
  const cards = [];
  const sources = {session_default:'Session default', worker_default:'Stored worker default', queue_pin:'Queue pin',
    session_model_default:'Shared model for this engine', watchtower_default:'WatchTower availability fallback',
    engine_default:'Engine default', unresolved:'Unresolved', model_policy:'Model policy substitution',
    consumer_safeguard:'Launch safeguard', consumer_validation:'Launch validation'};
  const el = (tag, text, cls) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; };
  function render(card) {
    const selected = card.pendingSelection || card.select.value, focused = document.activeElement === card.select;
    card.pendingSelection = null;
    card.select.replaceChildren();
    for (const plan of plans) { const option = el('option', plan.label); option.value = plan.id; card.select.append(option); }
    if (selected && !plans.some(plan => plan.id === selected)) {
      const missing = el('option', selected + ' · no longer available'); missing.value = selected; missing.disabled = true; card.select.append(missing);
    }
    card.select.value = selected || (card.kind === 'queues'
      ? (plans.find(plan => plan.id.startsWith('queue:'))?.id || 'worker') : 'session');
    if (focused) card.select.focus({preventScroll:true});
    const plan = plans.find(plan => plan.id === card.select.value);
    card.output.replaceChildren();
    if (!plan) { card.output.append(el('p','This context is no longer available. Choose another context; no launch result is inferred.')); return; }
    card.output.append(el('p', plan.basis === 'consumer' ? 'Resolved by launch consumers · ' + observedAt : 'Configured only · consumer unavailable · ' + observedAt, 'settings-launch-basis'));
    const details = el('details', null, 'settings-launch-details');
    details.append(el('summary', 'Sources & runtime details'));
    const fields = el('dl', null, 'settings-launch-fields');
    for (const [key, label] of [['engine','Engine'],['model','Model'],['effort','Effort']]) {
      const field = plan.fields[key], row = el('div'); row.dataset.launchField = key;
      row.append(el('dt', label));
      const value = field.value || (field.certainty === 'ambient' ? 'Engine decides at launch' : 'Unresolved');
      const dd = el('dd'); dd.append(el('strong', value + (field.value && field.certainty === 'ambient' ? ' · runtime selection' : '')));
      dd.append(el('span', sources[field.source] || field.source, 'settings-source-badge'));
      details.append(el('p', label + ': ' + field.explanation)); row.append(dd); fields.append(row);
    }
    card.output.append(fields);
    if (Object.keys(plan.pins || {}).length) {
      details.append(el('p', 'Stored queue pins: ' + Object.entries(plan.pins).map(([key,value]) => key + ' = ' + value).join(' · ')));
    }
    const fallback = plan.fallback;
    card.output.append(el('h4', 'Failure fallback · ' + (fallback.enabled ? 'On' : 'Off') + (fallback.source ? ' · ' + (fallback.source === 'queue_pin' ? 'Queue override' : fallback.legacy ? 'Legacy default' : 'Worker policy') : '')));
    details.append(el('p', fallback.explanation));
    if (fallback.routes.length) {
      details.append(el('p', (fallback.verified ? 'Consumer-resolved' : 'Configured, unverified') + ' candidate order (not a selected replacement):'));
      const order = el('ol');
      for (const route of fallback.routes) order.append(el('li', [route.engine, route.model || 'Engine default model', route.effort || 'No effort override'].join(' · ') + (route.same_engine ? ' — current engine; skipped after its failure' : '')));
      details.append(order);
    } else if (plan.id !== 'session') {
      card.output.append(el('p', fallback.enabled ? 'No verified replacement candidates. A failure may park the queue.' : 'No replacement selected.'));
    }
    if (plan.id !== 'session') card.output.append(el('p', 'Headroom dispatch: ' + (fallback.headroom ? 'On · decision deferred until dispatch' : 'Off') + ' · ' + (fallback.headroom_source === 'queue_pin' ? 'Queue override' : 'Worker policy')));
    for (const warning of plan.warnings || []) card.output.append(el('p', warning, 'settings-launch-warning'));
    for (const note of plan.notes) details.append(el('p', note, 'settings-row-desc'));
    card.output.append(details);
  }
  function accept(data) {
    if (!data?.launch_preview?.plans) return false;
    sequence++; // Discard any earlier in-flight snapshot.
    plans = data.launch_preview.plans;
    observedAt = new Date(data.launch_preview.observed_at || Date.now()).toLocaleTimeString();
    cards.forEach(card => { card.loading = false; card.select.disabled = false; render(card); if (card.focusOnRead) { card.focusOnRead = false; card.select.focus({preventScroll:true}); card.host.scrollIntoView({block:'nearest'}); } });
    return true;
  }
  async function refresh() {
    const request = ++sequence;
    cards.forEach(card => { card.loading = true; card.output.textContent = 'Reading saved launch routing…'; });
    try {
      const response = await fetch('/api/settings/queue-engines', {cache:'no-store'});
      const data = await response.json();
      if (request !== sequence) return;
      if (!response.ok || data.ok === false) throw new Error(data.error || 'Unable to read launch routing');
      if (!accept(data)) throw new Error('Launch preview unavailable from this server');
    } catch (error) {
      if (request !== sequence) return;
      cards.forEach(card => { card.loading = false; card.select.disabled = true; card.output.textContent = 'Preview unavailable: ' + error.message + '. Refresh to retry; no settings changed.'; });
    }
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(() => { if (cards.some(card => card.host.getClientRects().length)) refresh(); }, 50);
  }
  function selectQueue(name) {
    const card = cards.find(item => item.kind === 'queues') || cards[0]; if (!card) return;
    const disclosure = card.host.closest('details'); if (disclosure) disclosure.open = true;
    if (card.loading || card.select.disabled || !plans.some(plan => plan.id === 'queue:' + name)) {
      card.pendingSelection = 'queue:' + name; card.focusOnRead = true; refresh(); return;
    }
    card.select.value = 'queue:' + name; render(card); card.select.focus(); card.host.scrollIntoView({block:'nearest'});
  }
  function mount() {
    if (mounted) return; mounted = true;
    document.querySelectorAll('[data-launch-preview]').forEach(host => {
      const suffix = host.dataset.launchPreview, title = el('h3','Next launch · saved routing');
      const intro = el('p','This machine · Saved arguments for a standard local session, worker or queue. Runtime routing can differ; nothing is launched or saved.','settings-row-desc');
      const toolbar = el('div',null,'settings-launch-toolbar');
      const label = el('label','Context'); label.htmlFor = 'settingsLaunchContext-' + suffix;
      const select = el('select'); select.id = label.htmlFor; select.disabled = true;
      const refreshButton = el('button','Refresh preview','settings-action-btn'); refreshButton.type = 'button'; refreshButton.addEventListener('click',refresh);
      const output = el('div','Open this section or refresh to read saved routing.','settings-launch-output'); output.setAttribute('role','status'); output.setAttribute('aria-live','polite');
      select.addEventListener('change',() => { if (!card.loading) render(card); });
      toolbar.append(label,select,refreshButton); host.append(title,intro,toolbar,output);
      const card = {host,select,output,kind:suffix}; cards.push(card);
      if ('IntersectionObserver' in window) new IntersectionObserver(entries => { if (!plans.length && entries.some(entry => entry.isIntersecting)) schedule(); }).observe(host);
    });
    document.addEventListener('click',event => {
      const button = event.target.closest('[data-launch-queue]'); if (button) selectQueue(button.dataset.launchQueue);
    });
    window.addEventListener('ccc:spawn-defaults-changed',schedule);
    schedule();
  }
  window.cccLaunchPreview = Object.freeze({mount,refresh,accept,selectQueue});
})();
