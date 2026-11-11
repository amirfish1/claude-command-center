/* Preferences-only Settings: relocate existing action nodes, never clone their
 * editors/listeners. Load before app.js; its final mount supplies closure hooks.
 * Tool/help entry points live in the existing More menu, operations in System
 * status, and starting-model evidence in Stats. No network or service actions
 * are performed by this module. Legacy IDs and section URLs redirect safely.
 */
(() => {
  'use strict';
  let hooks, mounted = false, openUsage;
  const get = id => document.getElementById(id);
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const visible = element => !element.disabled && element.getClientRects().length && !element.closest('[hidden]') && ![...element.closest('#sidebarMoreMenu')?.querySelectorAll('details:not([open])') || []].some(details => details.contains(element) && details.querySelector('summary') !== element);
  function closeMenu() {
    get('sidebarMoreMenu').hidden = true;
    get('sidebarMoreBtn').setAttribute('aria-expanded', 'false');
  }
  function openMenu(help = false) {
    hooks.closeSettings();
    const menu = get('sidebarMoreMenu'), button = get('sidebarMoreBtn');
    if (menu.hidden) button.click();
    const details = get('settingsActionsHelp');
    if (help && details) details.open = true;
    (help ? details?.querySelector('summary') : get('settingsCmdkBtn'))?.focus();
  }
  function openSystem(focusId) {
    hooks.closeSettings(); closeMenu();
    hooks.openSystem(get('sidebarMoreBtn'));
    if (focusId) get(focusId)?.focus();
  }
  function redirect(section) {
    if (!mounted) return false;
    if (section === 'tools' || section === 'help') { openMenu(section === 'help'); return true; }
    if (['stats', 'usage', 'model-usage'].includes(section)) {
      hooks.closeSettings(); closeMenu(); openUsage?.();
      const report = get('settingsActionsUsage');
      if (report) { report.open = true; report.querySelector('summary').focus(); }
      return true;
    }
    if (['services', 'system-status'].includes(section)) { openSystem(); return true; }
    return false;
  }
  function item(element, label) {
    if (!element) return null;
    element.classList.add('sh-more-item');
    element.classList.remove('topbar-btn', 'settings-action-btn', 'danger');
    element.setAttribute('role', 'menuitem');
    if (label) { element.textContent = label; element.setAttribute('aria-label', label); }
    return element;
  }
  function mount(integration) {
    if (mounted) return;
    const menu = get('sidebarMoreMenu'), pane = get('settingsPane');
    if (!menu || !pane || !get('sysRowWorker') || !get('statsDialog')) return;
    hooks = integration; mounted = true;
    const style = node('style'); style.textContent = `
      #sidebarMoreMenu {overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain}
      #sidebarMoreMenu .settings-actions-tools {display:contents}
      #sidebarMoreMenu .settings-actions-tools .sh-more-item {display:flex!important;width:100%;box-sizing:border-box;white-space:normal;min-height:36px;text-align:left;margin:0;justify-content:flex-start}
      #sidebarMoreMenu .settings-actions-help-title {padding:9px 12px;cursor:pointer;font-size:12.5px;color:var(--text)}
      #sidebarMoreMenu .settings-actions-help-title:hover {background:var(--surface-2)}
      #sidebarMoreMenu details > .sh-more-item {padding-left:24px;box-sizing:border-box;white-space:normal}
      @media(max-width:600px) {#sidebarMoreMenu .settings-actions-help-title {padding:14px 12px;font-size:16px}}
      #sidebarMoreMenu #settingsActionsHelp[open] {border-top:1px solid var(--border);margin-top:4px}
      #sidebarMoreMenu :is(button,a,summary,[role=menuitem]):focus-visible {outline:2px solid var(--accent);outline-offset:-2px}
      #sidebarMoreMenu .settings-actions-help-title:focus-visible {outline:2px solid var(--accent);outline-offset:-2px}
      #sidebarMoreMenu .sh-action-label {display:inline}
      .settings-actions-controls {display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:8px 0;font-size:12px}
      .settings-actions-controls .settings-row-desc {flex-basis:100%}
      #settingsActionsUsage {padding:10px 20px;border-bottom:1px solid var(--border);font-size:13px;flex:0 0 auto;max-height:35vh;overflow-y:auto}
      #settingsActionsUsage > summary {cursor:pointer;font-weight:600}
      #settingsActionsUsage .settings-row {display:block}
      #settingsActionsUsage .settings-action-btn {margin-top:8px}
      #statsDialog [hidden],#sysModal [hidden] {display:none!important}
      html #statsModal.open {display:flex!important}
      #statsTitle {font-size:15px;margin:0;padding:14px 20px 0}
      @media(max-width:600px) {
        #sysDialog .sys-tabs {display:flex;flex-wrap:wrap}
        #sysDialog .sys-name {flex-wrap:wrap;gap:4px}
      }
    `; document.head.append(style);

    // Originals moved at boot by app.js remain the real interactive nodes.
    const tools = node('div', null, 'settings-actions-tools'); tools.id = 'settingsActionsTools';
    tools.setAttribute('role', 'group'); tools.setAttribute('aria-label', 'Workspace tools');
    menu.prepend(tools);
    for (const [id, label] of [
      ['settingsCmdkBtn', 'Session search…'], ['termToggleBtn'], ['historyStatusPill'],
      ['kptWorktreesBtn'], ['statsBtn', 'Stats & usage…'], ['todayToggleBtn'],
      ['annotationNotesBtn'], ['fleetViewBtn', 'Fleet overview…'], ['settingsFleetPulseBtn', 'Fleet Pulse…'],
    ]) {
      const button = item(get(id), label);
      if (button) {
        const oldRow = button.closest('.settings-row');
        tools.append(button);
        if (id === 'settingsFleetPulseBtn') button.addEventListener('click', () => { hooks.closeSettings(); window._cccOpenFleetPulse?.(); });
        // A moved launcher leaves no action-only row in a preferences section.
        if (oldRow && ['fleetViewBtn', 'settingsFleetPulseBtn'].includes(id)) oldRow.remove();
      }
    }
    // Preserve the status LED while replacing the old proxy with its live node.
    const oldStatus = menu.querySelector('[data-proxy="cccServerStatusChip"]') || get('settingsAboutStrip')?.querySelector('[data-proxy="cccServerStatusChip"]');
    const system = item(get('systemStatusBtn'), 'System status…');
    if (system) {
      const led = get('sidebarMoreStatusLed'); if (led) system.prepend(led);
      if (oldStatus) oldStatus.replaceWith(system); else tools.append(system);
      // Its existing direct handler already opens the native status dialog.
      system.addEventListener('click', () => hooks.closeSettings(), {capture: true});
    }
    const about = get('settingsAboutStrip');
    if (about) { while (about.firstChild) menu.append(about.firstChild); about.remove(); }

    const help = node('details'); help.id = 'settingsActionsHelp';
    const summary = node('summary', 'Help & setup', 'settings-actions-help-title'); summary.setAttribute('role', 'menuitem');
    help.append(summary); menu.append(help);
    for (const [id, label] of [['runOnboardingBtn', 'Guided tour'], ['momentZeroBtn', 'Welcome & free setup…']]) {
      const button = item(get(id), label); if (button) help.append(button);
    }
    const docs = pane.querySelector('#settingsSection-help a[href]');
    if (docs) help.append(item(docs, 'Documentation & setup help ↗'));
    // Keep the first level short; move the original nodes so their handlers,
    // status updates and responsive ownership remain intact.
    function group(id, title, parent) {
      const details = node('details'); details.id = id;
      const heading = node('summary', title, 'settings-actions-help-title');
      heading.setAttribute('role', 'menuitem'); details.append(heading); parent.append(details);
      return details;
    }
    const filters = get('sidebarMoreFilters');
    if (filters) {
      const listOptions = group('settingsActionsList', 'List options', menu);
      filters.before(listOptions); listOptions.append(filters);
      const syncListVisibility = () => { listOptions.hidden = filters.hidden; };
      new MutationObserver(syncListVisibility).observe(filters, {attributes:true, attributeFilter:['hidden']});
      syncListVisibility();
    }
    const workspace = group('settingsActionsWorkspace', 'Workspace tools', tools);
    for (const id of ['historyStatusPill', 'kptWorktreesBtn', 'todayToggleBtn', 'annotationNotesBtn', 'fleetViewBtn', 'settingsFleetPulseBtn']) {
      const button = get(id); if (button) workspace.append(button);
    }
    const view = group('settingsActionsView', 'Group chats & view', menu);
    for (const selector of ['[data-proxy="sidebarNewGroupChatBtn"]', '[data-proxy="sidebarGroupChatLiveBtn"]', '.sh-more-check']) {
      const control = menu.querySelector(selector); if (control) view.append(control);
    }
    // Help also owns setup, release information and support.
    for (const selector of ['#cccWhatsNewLink', '#cccCheckUpdatesLink', '[data-proxy="bugReportLink"]', 'a[href*="discord.gg"]', '.sh-more-info']) {
      const control = menu.querySelector(selector); if (control) help.append(control);
    }
    menu.append(view, help);
    menu.querySelectorAll(':scope > .sh-more-divider').forEach(divider => divider.remove());
    // Voice setup registers after this module; route those late arrivals too.
    const organizeSetup = () => {
      for (const id of ['voiceSettingsBtn', 'carModeMenuBtn']) {
        const control = get(id);
        if (control?.parentElement === tools) help.append(control);
      }
    };
    new MutationObserver(organizeSetup).observe(tools, {childList:true});
    organizeSetup();

    // Take-the-tour is still called by the welcome flow. Keep its native ID and
    // listener as a hidden alias instead of the old consolidation deleting it.
    const aliases = node('div'); aliases.hidden = true; aliases.id = 'settingsActionsLegacy'; menu.append(aliases);
    if (get('takeTourBtn')) aliases.append(get('takeTourBtn'));

    function relocateControls(rowId, ids) {
      const destination = get(rowId); if (!destination) return;
      const controls = node('div', null, 'settings-actions-controls');
      for (const id of ids) { const element = get(id); if (element) controls.append(element); }
      destination.append(controls);
    }
    relocateControls('sysRowWorker', ['controlPlaneStatus', 'controlPlaneRestartNote', 'controlPlaneReconcileBtn', 'controlPlaneDrainBtn']);
    relocateControls('sysRowWt', ['watchtowerServiceStatus', 'watchtowerServiceOpenBtn']);
    // Legacy destructive buttons now navigate to the canonical operation and
    // focus it; they never bypass the native status panel's confirmation flow.
    for (const [oldId, canonicalId] of [['restartServerBtn', 'sysRestartDashBtn'], ['controlPlaneRestartBtn', 'sysRestartWorkerBtn'], ['watchtowerServiceActionBtn', 'sysRestartWtBtn']]) {
      const button = get(oldId); if (!button) continue;
      aliases.append(button);
      button.addEventListener('click', event => {
        event.preventDefault(); event.stopImmediatePropagation(); openSystem(canonicalId);
      }, {capture: true});
    }
    // Remove emptied operation rows, retaining config consent/debug preferences.
    for (const row of [...pane.querySelectorAll('#settingsSection-maintenance .settings-row')]) {
      if (!row.querySelector('button,input,select,textarea,a')) row.remove();
    }
    const maintenance = get('settingsSection-maintenance');
    const heading = maintenance?.querySelector('.settings-section-eyebrow'); if (heading) heading.textContent = 'Diagnostics & config access';
    const note = maintenance?.querySelector(':scope > .settings-section-note'); if (note) note.textContent = 'Choose diagnostic display options and review agent configuration access.';

    const report = get('settingsModelUsage')?.closest('.settings-usage-disclosure') || get('settingsModelUsage')?.closest('.settings-row');
    if (!get('statsTitle')) {
      const title = node('h2', 'Stats & usage'); title.id = 'statsTitle'; get('statsDialog').prepend(title);
    }
    if (report) {
      let disclosure = report;
      if (report.tagName !== 'DETAILS') { disclosure = node('details'); disclosure.append(node('summary', 'Starting models · last 7 days'), report); }
      disclosure.id = 'settingsActionsUsage'; disclosure.classList.remove('settings-disclosure');
      get('statsBody').before(disclosure);
      disclosure.addEventListener('toggle', () => { if (disclosure.open) hooks.refreshUsage(); });
    }
    for (const sectionId of ['tools', 'help']) {
      const adapter = get('settingsRailTab-' + sectionId);
      if (adapter) { aliases.append(adapter); adapter.addEventListener('click', () => redirect(sectionId)); }
      get('settingsSection-' + sectionId)?.remove();
    }
    // Update aria-controls after retiring sections; the other preferences stay.
    document.querySelectorAll('#settingsRail [aria-controls]').forEach(button => {
      button.setAttribute('aria-controls', button.getAttribute('aria-controls').split(/\s+/).filter(id => get(id)).join(' '));
    });

    const focusableItems = () => [...menu.querySelectorAll('[role=menuitem], [role=menuitemcheckbox]')]
      .map(element => element.matches('[role=menuitemcheckbox]') ? element.querySelector('input') || element : element).filter(visible);
    get('sidebarMoreBtn').addEventListener('click', () => { if (!menu.hidden) focusableItems()[0]?.focus(); });
    menu.addEventListener('keydown', event => {
      if (event.isComposing) return;
      const items = focusableItems(), index = items.indexOf(document.activeElement);
      let next;
      if (event.key === 'ArrowDown') next = items[(index + 1) % items.length];
      if (event.key === 'ArrowUp') next = items[(index - 1 + items.length) % items.length];
      if (event.key === 'Home') next = items[0];
      if (event.key === 'End') next = items.at(-1);
      if (next) { event.preventDefault(); next.focus(); }
      if (event.key === 'Escape') { event.preventDefault(); closeMenu(); get('sidebarMoreBtn').focus(); }
      if (!event.defaultPrevented && (event.key === 'Enter' || event.key === ' ') && document.activeElement?.getAttribute('role') === 'menuitem' && !document.activeElement.matches('button,a,summary')) {
        event.preventDefault(); document.activeElement.click();
      }
    });
    // Stats' renderer replaces #statsBody, so its report lives beside that body.
    // Keep a keyboard entrance/return for the existing overlay, not a new modal.
    const stats = get('statsModal');
    new MutationObserver(() => {
      if (stats.classList.contains('open')) get('statsTabOverview')?.focus();
      else if (stats.contains(document.activeElement)) get('sidebarMoreBtn').focus();
    }).observe(stats, {attributes: true, attributeFilter: ['class']});
    stats.addEventListener('keydown', event => {
      if (event.key !== 'Tab' || !stats.classList.contains('open')) return;
      const items = [...stats.querySelectorAll('button,a,input,summary,[tabindex="0"]')].filter(visible);
      const first = items[0], last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    });
    const hashRoute = () => { const match = /^#settingsSection-(tools|help)$/.exec(location.hash); if (match) redirect(match[1]); };
    window.addEventListener('hashchange', hashRoute); hashRoute();
  }
  window.cccSettingsActions = Object.freeze({mount, redirect, setUsageOpener: opener => { openUsage = opener; }});
})();
