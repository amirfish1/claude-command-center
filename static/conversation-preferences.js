/* Shared conversation preferences with explicit live-state and host adapters. */
(function (root) {
  function createController({ state: sharedState, helpers: hostServices }) {
  function _readPersistedTtsRate() {
    try {
      const raw = parseFloat(localStorage.getItem('ccc-tts-rate') || '');
      if (Number.isFinite(raw) && raw >= sharedState._TTS_RATE_MIN && raw <= sharedState._TTS_RATE_MAX) return raw;
    } catch (_) {}
    return sharedState._TTS_RATE_DEFAULT;
  }

  function normalizeSidebarViewMode(value) {
    if (value === 'flow') return sharedState.FLOW_POPOUT_MODE ? 'flow' : 'list';
    if (value === 'list') return 'list';
    if (value === 'board' || value === 'kanban') return 'board';
    return 'list';
  }

  function readSidebarViewMode() {
    if (sharedState.FLOW_POPOUT_MODE) return 'flow';
    if (sharedState.KANBAN_URL_MODE) return 'board';
    try {
      const saved = localStorage.getItem('ccc-session-view');
      if (saved) return normalizeSidebarViewMode(saved);
      return localStorage.getItem('ccc-kanban-view') === 'true' ? 'board' : 'list';
    } catch (_) {
      return 'list';
    }
  }

  function normalizeRecencyFilter(value) {
    if (value === '1' || value === '10h') return '1d';
    if (value === '1d' || value === '7d') return value;
    return '';
  }

  function _kimiSvg(inner) {
    return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
      + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
      + inner + '</svg>';
  }

  function readLegacySpawnEnginePref() {
    try { return (0, sharedState.normalizeSpawnDefaultEngine)(localStorage.getItem('ccc.spawnEngine')); }
    catch (_) { return 'claude'; }
  }
    return { _readPersistedTtsRate, readSidebarViewMode, normalizeRecencyFilter, _kimiSvg, readLegacySpawnEnginePref, normalizeSidebarViewMode };
  }
  root.CCCConversationPreferences = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
