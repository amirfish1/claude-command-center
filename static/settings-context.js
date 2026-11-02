/* Contextual preference homes. Loaded before app.js; mount receives the same
 * native preference handler used by Settings. Nodes, IDs and storage adapters
 * stay intact, including across list renders, reader splits and phone menus.
 */
(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const make = (tag, text) => { const el = document.createElement(tag); if (text) el.textContent = text; return el; };
  let hooks, list, branding, reader, parking, mounted = false, brandingOpened = false, brandingTarget;
  const phone = () => matchMedia('(max-width:1200px)').matches || document.documentElement.classList.contains('ccc-mobile-list-view');
  const routes = {
    'list-presentation': 'list', 'folder-chips': 'list', 'chips': 'list',
    'settingsGhIssuesToggle': 'list', 'settingsSeparateTabsToggle': 'list', 'ccc-view-gh': 'list', 'ccc-separate-tabs': 'list',
    reader: 'reader', 'text-size': 'reader', 'fontMinus': 'reader', 'fontPlus': 'reader', 'settingsModalFontSlot': 'reader',
    'repository-branding': 'repo', 'repo-avatar': 'repo', 'repo-avatars': 'repo', 'repoAvatarSelect': 'repo',
    'repoAvatarUpload': 'repo', 'repoAvatarReset': 'repo',
    'queue-view': 'queue', 'settingsQueueRhsListToggle': 'queue', 'ccc-queue-rhs-list': 'queue',
  };
  function syncList() {
    if (!mounted) return;
    const menu = document.querySelector('#convList .conv-archived-overflow-menu');
    const target = (phone() ? get('sidebarMoreFilters') : menu) || parking;
    if (target && list.parentElement !== target) target.append(list);
    if (phone() && target) target.hidden = false;
  }
  function syncReader(pane) {
    const mobileSlot = document.querySelector('#convSimpleActionsMenu [data-context-phone-reader]');
    const usePhone = phone() && mobileSlot && !document.body.classList.contains('conversation-popout');
    const target = usePhone ? mobileSlot : (pane || document.querySelector('.conv-pane.is-active') || document.querySelector('.conv-pane'))?.querySelector('[data-context-reader-slot]');
    if (target && reader.parentElement !== target) target.append(reader);
    if (mobileSlot) mobileSlot.hidden = !usePhone;
  }
  function closeOtherMenus(kind) {
    if (kind !== 'repo') { branding.open = false; brandingOpened = false; }
    if (kind !== 'reader') {
      document.querySelectorAll('.conv-pane-more[open]').forEach(details => { details.open = false; });
      if (get('convSimpleActionsMenu')) get('convSimpleActionsMenu').hidden = true;
      get('convSimpleActionsBtn')?.setAttribute('aria-expanded', 'false');
    }
    if (kind !== 'list') {
      const menu = get('sidebarMoreMenu'); if (menu) menu.hidden = true;
      get('sidebarMoreBtn')?.setAttribute('aria-expanded', 'false');
      const details = list.closest('details'); if (details) details.open = false;
    }
    if (kind !== 'queue') {
      get('queueMoreMenu')?.classList.remove('open'); get('queueMoreMenu')?.setAttribute('aria-hidden', 'true');
      get('queueMoreBtn')?.setAttribute('aria-expanded', 'false');
    }
  }
  function clampList() {
    const menu = list?.closest('.conv-archived-overflow-menu');
    if (!menu || !menu.closest('details')?.open) return;
    menu.style.transform = '';
    const rect = menu.getBoundingClientRect();
    const delta = Math.max(8 - rect.left, Math.min(0, innerWidth - 8 - rect.right));
    menu.style.transform = 'translateX(' + delta + 'px)';
  }
  function openBranding(repo, anchor) {
    if (!mounted) return;
    hooks.closeSettings(); closeOtherMenus('repo');
    if (anchor) {
      const header = anchor.closest('.conv-folder-group-header, .flow-inspector-toolbar');
      header?.after(branding);
    }
    else {
      hooks.openRepoPicker();
      const host = document.querySelector('.spawn-cwd-row');
      // Composer initialization can collapse the folder row asynchronously.
      // Keep its appearance editor beside that row, outside the collapsed area.
      if (host) host.after(branding);
    }
    brandingOpened = true; brandingTarget = repo || '';
    branding.open = true;
    void hooks.refreshBranding(brandingTarget);
    // Focus immediately; later discovery must not steal focus back.
    if (branding.open && branding.isConnected) { branding.scrollIntoView({block: 'nearest'}); get('repoAvatarSelect')?.focus(); }
  }
  function redirect(route) {
    const kind = routes[route]; if (!mounted || !kind) return false;
    hooks.closeSettings(); closeOtherMenus(kind);
    if (kind === 'repo') { openBranding(); return true; }
    if (kind === 'reader') {
      hooks.openReader(); syncReader();
      if (reader.closest('#convSimpleActionsMenu') && get('convSimpleActionsMenu').hidden) get('convSimpleActionsBtn')?.click();
      const details = reader.closest('details'); if (details) details.open = true;
      get('fontPlus')?.focus(); return true;
    }
    if (kind === 'queue') {
      hooks.openQueue();
      const menu = get('queueMoreMenu');
      if (menu && !menu.classList.contains('open')) get('queueMoreBtn')?.click();
      clampQueue(); get('settingsQueueRhsListToggle')?.focus(); return true;
    }
    hooks.openList(); syncList();
    if (phone()) {
      if (get('sidebarMoreMenu')?.hidden) get('sidebarMoreBtn')?.click();
    } else {
      const details = list.closest('details'); if (details) details.open = true; clampList();
    }
    get('settingsGhIssuesToggle')?.focus(); return true;
  }
  function clampQueue() {
    const menu = get('queueMoreMenu'); if (!menu?.classList.contains('open')) return;
    const rect = menu.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(rect.left, innerWidth - rect.width - 8)) + 'px';
    menu.style.maxHeight = Math.max(80, innerHeight - rect.top - 8) + 'px';
  }
  function mount(integration) {
    if (mounted || !get('settingsPane') || !get('repoAvatarSelect') || !get('settingsModalFontSlot')) return;
    hooks = integration; mounted = true;
    parking = make('div'); parking.hidden = true; parking.id = 'contextPreferenceParking'; document.body.append(parking);
    const style = make('style'); style.textContent = `
      [data-context-preferences] {text-transform:none;letter-spacing:normal;box-sizing:border-box;min-width:0;color:var(--text);font:12px/1.5 var(--font-ui,system-ui);white-space:normal}
      [data-context-preferences] .settings-row {display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 0;border-bottom:1px solid var(--border)}
      [data-context-preferences] .settings-row-main {flex:1 1 140px;min-width:0}
      [data-context-preferences] .settings-row-label {font-weight:600}
      [data-context-preferences] .settings-row-desc {font-size:12px;color:var(--text-muted);overflow-wrap:anywhere}
      [data-context-preferences] .settings-row-control {display:flex;align-items:center;flex-wrap:wrap;gap:6px}
      [data-context-preferences] .settings-toggle {flex-shrink:0}
      [data-context-preferences] button:focus-visible,[data-context-preferences] select:focus-visible {outline:2px solid var(--accent);outline-offset:2px}
      #contextListPreferences h3,#contextQueuePreferences h3 {font-size:12px;margin:6px 0}
      #contextListPreferences {width:100%;padding:4px}
      #contextQueuePreferences {padding:8px 12px;width:min(300px,calc(100vw - 32px))}
      #queueMoreMenu {z-index:1500;max-width:calc(100vw - 16px);overflow-y:auto;overscroll-behavior:contain}
      #queuePanel .files-header:has(#queueMoreMenu.open) {z-index:1500}
      #contextQueuePreferences .settings-toggle {display:inline-flex;width:36px;padding:0;min-height:32px}
      #contextRepoPreferences {width:100%;grid-column:1 / -1;flex-basis:100%;border:1px solid var(--border);border-radius:8px;padding:10px;margin:8px 0;background:var(--bg)}
      body #convInputContext.visible.is-new-session:has(> #contextRepoPreferences) {grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"object object" "chips chips" "cwd worktree" "branding branding"}
      #convInputContext > #contextRepoPreferences {grid-area:branding}
      body #convInputContext:has(> #contextRepoPreferences) .spawn-cwd-row {grid-area:cwd}
      @media(max-width:1200px) {
        body #convInputContext.visible.is-new-session:has(> #contextRepoPreferences) {grid-template-columns:minmax(0,1fr);grid-template-areas:"object" "chips" "cwd" "worktree" "branding"}
      }
      #contextRepoPreferences > summary {cursor:pointer;font-size:13px;font-weight:600;color:var(--text)}
      #contextRepoPreferences .settings-row {border:0;display:block}
      #contextRepoPreferences select {box-sizing:border-box;max-width:100%;color:var(--text);background:var(--surface);margin:8px 0}
      #contextRepoPreferences .settings-row-control {margin-top:8px}
      html:not(.ccc-debug-mode) .conv-pane-more:has([data-context-reader-slot]) {display:block!important}
      .conv-pane-more-menu:has([data-context-reader-slot]) {min-width:220px;max-width:calc(100vw - 24px)}
      .conv-pane-more-menu:has([data-context-reader-slot]) .conv-view-seg {max-width:100%;flex-wrap:wrap;overflow:visible}
      #settingsModalFontSlot {padding:8px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
      #settingsModalFontSlot .font-size-controls {display:flex!important}
      #settingsModalFontSlot p {flex-basis:100%;margin:0;color:var(--text-muted);font-size:12px}
      [data-context-singleton][hidden] {display:none!important}
      #convList .conv-archived-overflow-menu {width:min(340px,calc(100vw - 16px));min-width:0;max-width:calc(100vw - 16px);max-height:70vh;overflow-y:auto;overscroll-behavior:contain}
      [data-context-preferences] .settings-segmented {flex-wrap:wrap}
      #settingsPane [data-settings-context] {max-width:100%;min-width:0;white-space:normal;overflow-wrap:anywhere;box-sizing:border-box;text-align:left}
      @media(max-width:1200px) {
        [data-context-preferences] button {min-height:36px}
        #settingsModalFontSlot .font-size-btn {min-width:44px;min-height:44px}
        #settingsPane [data-settings-context] {min-height:44px}
      }
    `; document.head.append(style);
    const singleton = (element, id) => { if (id) element.id = id; element.dataset.contextSingleton = ''; element.dataset.contextPreferences = ''; return element; };
    const row = id => get(id)?.closest('.settings-row');
    const link = (oldRow, text, route) => {
      if (!oldRow) return;
      const entry = make('div'); entry.className = 'settings-row'; entry.dataset.keywords = oldRow.dataset.keywords || '';
      const button = make('button', text); button.type = 'button'; button.className = 'settings-action-btn'; button.dataset.settingsContext = route;
      entry.append(button); oldRow.before(entry);
    };
    const repoRow = row('repoAvatarSelect'); link(repoRow, 'Repository appearance → Folder picker or repository header', 'repository-branding');
    branding = singleton(make('details'), 'contextRepoPreferences'); branding.append(make('summary', 'Repository appearance'));
    // Keep the editor out of the composer until an appearance action opens it.
    branding.append(repoRow); parking.append(branding);
    branding.addEventListener('toggle', () => {
      if (!branding.open) { brandingOpened = false; parking.append(branding); return; }
      if (!brandingOpened) { brandingOpened = true; brandingTarget = get('spawnCwdPicker')?.value; hooks.refreshBranding(brandingTarget); }
    });

    list = singleton(make('section'), 'contextListPreferences');
    list.setAttribute('aria-label', 'List presentation · This browser'); list.append(make('h3', 'List presentation · This browser'));
    const chipRow = document.querySelector('[data-segmented-group="chips"]')?.closest('.settings-row');
    link(chipRow, 'List presentation → View options', 'list-presentation');
    if (chipRow) list.append(chipRow);
    for (const id of ['settingsGhIssuesToggle', 'settingsSeparateTabsToggle']) { const element = row(id); if (element) {
      const description = element.querySelector('.settings-row-desc');
      if (description) description.textContent = id === 'settingsGhIssuesToggle' ? 'Include GitHub issues in the session list.' : 'Show Issues and Queues tabs instead of Coding and Workers.';
      list.append(element);
    } }
    const cycle = get('chipsColorToggle'); if (cycle) { cycle.hidden = true; list.append(cycle); }
    const listReset = make('button', 'Reset list presentation'); listReset.type = 'button'; listReset.className = 'settings-action-btn';
    listReset.addEventListener('click', () => hooks.resetList()); list.append(listReset);
    list.addEventListener('click', hooks.preferenceClick);

    const queueRow = row('settingsQueueRhsListToggle'); link(queueRow, 'Queue sidebar → Queue view options', 'queue-view');
    const queue = singleton(make('section'), 'contextQueuePreferences'); queue.append(make('h3', 'Queue sidebar · This browser'));
    if (queueRow) {
      queueRow.querySelector('.settings-row-label').textContent = 'Show full queue list';
      queueRow.querySelector('.settings-row-desc').textContent = 'Display all queues instead of the selected queue’s compact status strip.';
      queue.append(queueRow);
    }
    get('queueMoreMenu')?.append(queue);
    queue.addEventListener('click', hooks.preferenceClick);
    get('queueMoreBtn')?.addEventListener('click', clampQueue);

    reader = singleton(get('settingsModalFontSlot'));
    const phoneReader = document.querySelector('#convSimpleActionsMenu .mobile-actions-row[aria-label="Text size"]');
    if (phoneReader) { phoneReader.replaceChildren(); phoneReader.dataset.contextPhoneReader = ''; }
    const readerRow = reader.closest('.settings-row'); link(readerRow, 'Conversation text size → Reader actions', 'text-size');
    // The toolbar's delayed setup used to move these controls into Settings.
    // Move the real stepper now, before removing its old row.
    const stepper = reader.querySelector('.font-size-controls') || get('fontMinus')?.closest('.font-size-controls');
    if (stepper) reader.append(stepper);
    reader.prepend(make('p', 'Text size · This browser'));
    const reset = make('button', 'Reset'); reset.type = 'button'; reset.className = 'font-size-btn'; reset.style.width = 'auto'; reset.setAttribute('aria-label', 'Reset conversation text size');
    reset.addEventListener('click', hooks.resetReader); reader.append(reset); syncReader(); readerRow?.remove();
    // One canonical stepper follows the reader menu; pane cloning excludes it.
    document.addEventListener('click', event => {
      const repoButton = event.target.closest('[data-context-repo]');
      if (repoButton) { event.preventDefault(); event.stopPropagation(); openBranding(repoButton.dataset.contextRepo, repoButton); return; }
      const context = event.target.closest('[data-settings-context]');
      if (context) { event.preventDefault(); event.stopPropagation(); redirect(context.dataset.settingsContext); return; }
      const readerButton = event.target.closest('[data-role="pane-more"]');
      if (readerButton) syncReader(readerButton.closest('.conv-pane'));
    }, true);
    function keyboard(event) {
      if (event.isComposing) return;
      if (event.target.closest('[data-context-repo]') && ['Enter', ' '].includes(event.key)) { event.stopPropagation(); return; }
      const block = event.target.closest('[data-context-preferences]');
      if (block && !event.target.matches('select,input,textarea')) {
        const chips = event.target.closest('[data-chips-mode]');
        if (chips && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
          const options = [...block.querySelectorAll('[data-chips-mode]')];
          const next = options[(options.indexOf(chips) + (event.key === 'ArrowRight' ? 1 : options.length - 1)) % options.length];
          event.preventDefault(); event.stopPropagation(); next.focus(); next.click(); return;
        }
        if (['ArrowUp', 'ArrowDown'].includes(event.key)) {
          const options = [...block.querySelectorAll('button,select,input')].filter(el => !el.disabled && el.getClientRects().length && !el.hidden);
          const index = options.indexOf(event.target);
          if (index >= 0) {
            event.preventDefault(); event.stopPropagation();
            options[(index + (event.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length].focus(); return;
          }
        }
      }
      if (event.key !== 'Escape') return;
      if (reader.contains(event.target) && reader.closest('#convSimpleActionsMenu')) {
        event.preventDefault(); event.stopPropagation();
        get('convSimpleActionsMenu').hidden = true;
        get('convSimpleActionsBtn').setAttribute('aria-expanded', 'false');
        get('convSimpleActionsBtn').focus(); return;
      }
      if (list.contains(event.target) && phone()) {
        event.preventDefault(); event.stopPropagation(); get('sidebarMoreMenu').hidden = true;
        get('sidebarMoreBtn').setAttribute('aria-expanded', 'false'); get('sidebarMoreBtn').focus(); return;
      }
      const details = event.target.closest('details[open]');
      if (details && (details === branding || details.contains(reader) || details.contains(list))) {
        event.preventDefault(); event.stopPropagation(); details.open = false; details.querySelector('summary')?.focus();
      }
      if (get('queueMoreMenu')?.contains(event.target)) { get('queueMoreBtn')?.focus(); }
    }
    document.addEventListener('keydown', keyboard, true);
    const listHost = get('convList');
    if (listHost) new MutationObserver(() => {
      syncList();
      if (!branding.isConnected) { branding.open = false; parking.append(branding); }
    }).observe(listHost, {childList: true, subtree: true});
    const split = get('convSplit');
    if (split) new MutationObserver(() => { if (!reader.isConnected) syncReader(); if (!branding.isConnected) { branding.open = false; parking.append(branding); } }).observe(split, {childList: true, subtree: true});
    window.addEventListener('resize', () => { syncList(); syncReader(); clampQueue(); clampList(); });
    document.addEventListener('toggle', event => { if (event.target.contains(list)) clampList(); }, true);
    const hashRoute = () => { try { redirect(decodeURIComponent(location.hash.slice(1))); } catch (_) {} };
    window.addEventListener('hashchange', hashRoute);
    hooks.refresh(); syncList(); hashRoute();
  }
  window.cccSettingsContext = Object.freeze({mount, syncList, redirect, openBranding});
})();
