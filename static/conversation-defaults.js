/* Canonical conversation state initialization for dashboard and app hosts. */
(function (root) {
  function createInitializers({ state: sharedState, helpers: hostServices }) {
    return {
      _PAUSE_WHEN_HIDDEN() {
        const _PAUSE_WHEN_HIDDEN = new Set([
    'liveStatus', 'liveToolStrip', 'sessionsList', 'archiveRecovery', 'gcActive', 'issues',
    'vercelDeploy', 'localhost', 'worktreesBadge', 'archiveTimes',
    'uxFixesQueueMeta', 'stuckSessions', 'modelCatalog',
  ]);
        return _PAUSE_WHEN_HIDDEN;
      },
      _clientLogLast() {
        const _clientLogLast = {};
        return _clientLogLast;
      },
      _cccBootId() {
        const _cccBootId = Math.random().toString(36).slice(2, 8);
        return _cccBootId;
      },
      _perfConvOpen() {
        const _perfConvOpen = null;
        return _perfConvOpen;
      },
      _perfHiddenSegs() {
        const _perfHiddenSegs = [];
        return _perfHiddenSegs;
      },
      _perfHiddenSince() {
        const _perfHiddenSince = null;
        return _perfHiddenSince;
      },
      _POLLER_META() {
        const _POLLER_META = {
    liveToolStrip:  { ms: 1000,  label: 'tools',   surface: 'Open conversation pane - inline tool activity', desc: 'Live tool-activity indicator at the bottom of the open transcript.' },
    gcReader:       { ms: 3000,  label: 'gc-read', surface: 'Open conversation pane - group-chat transcript', desc: 'Group-chat transcript reader (open chat only).' },
    hiStatus:       { ms: 4000,  label: 'hist-ix', surface: 'Top bar - history/search index pill',         desc: 'History-index progress (4s while indexing, else 60s).' },
    liveStatus:     { ms: 5000,  label: 'status',  surface: 'Sidebar - conversation row status',           desc: 'Session statuses - the live row dots + state.' },
    issues:         { ms: 10000, label: 'issues',  surface: 'Sidebar - GitHub Issues section',             desc: 'GitHub issues for the active repo.' },
    sessionsList:   { ms: 60000, label: 'sessions',surface: 'Sidebar - session list',                      desc: 'Archive/session refresh (~3MB, stale-cache).' },
    archiveRecovery:{ ms: 300000,label: 'recovery', surface: 'Sidebar - external transcript recovery',    desc: 'Slow recovery floor for changes made outside CCC.' },
    archiveTimes:   { ms: 90000, label: 'archive-t',surface: 'Sidebar - archive row times',                  desc: 'Refresh archive last_interacted/modified so row times stay live.' },
    uxFixesQueueMeta:{ ms: 15000, label: 'uxq',     surface: 'Sidebar - UX-fixes queue row',                 desc: 'UX-fixes queue current/last-number badge.' },
    gcActive:       { ms: 15000, label: 'gc-live', surface: 'Sidebar - active-group-chat footer pill',     desc: 'Active group-chat coordinations badge.' },
    vercelDeploy:   { ms: 15000, label: 'vercel',  surface: 'Top bar - Vercel deploy badge',               desc: 'Latest Vercel deploy status.' },
    localhost:      { ms: 15000, label: 'localhost',surface: 'Top bar - localhost pill',                   desc: 'Localhost dev-server reachability probe.' },
    worktreesBadge: { ms: 60000, label: 'worktrees',surface: 'Top bar - worktrees badge',                  desc: 'Git worktree count badge.' },
    peer:           { ms: null,  label: 'peer',    surface: 'Sidebar - repo/peer picker',                  desc: 'Peer-session registry (picker open).' },
    codexLog:       { ms: null,  label: 'codex',   surface: 'Open conversation pane - codex log',          desc: 'Codex session log (open codex convo only).' },
    archiveProgress:{ ms: 250,   label: 'archive', surface: 'Sidebar - archive loading bar',               desc: 'Archive load progress bar (transient, self-clears).' },
    cccHealth:      { ms: 5000,  label: 'health',  surface: 'Sidebar - bottom-left CCC health bar',         desc: 'CCC self-health: server CPU%, live-activity build latency, recent errors.' },
    stuckSessions:  { ms: 60000, label: 'stuck',   surface: 'Sidebar - bottom-left stuck-session count',    desc: 'Recent Codex sessions currently labeled Stuck by the stale-transcript heuristic.' },
    modelCatalog:   { ms: 3600000, label: 'models', surface: 'Model picker - engine catalogs',               desc: 'Refresh exact engine model choices from the server-owned catalog.' },
    codexLiveOverlay:{ ms: null,  label: 'codex-live', surface: 'Open conversation pane - Codex live overlay', desc: 'Live app-server overlay poll (static/codex-live-source.js; only while a turn is running or a request is pending).' },
  };
        return _POLLER_META;
      },
      _pollerStats() {
        const _pollerStats = {};
        return _pollerStats;
      },
      _onPollerTick() {
        const _onPollerTick = null;
        return _onPollerTick;
      },
      _pollerInflight() {
        const _pollerInflight = {};
        return _pollerInflight;
      },
      _perfLongTasks() {
        const _perfLongTasks = [];
        return _perfLongTasks;
      },
      _convDirect() {
        const _convDirect = { base: null, downUntil: 0, probing: false, checkedAt: 0 };
        return _convDirect;
      },
      _CCC_SOUNDS_LS() {
        const _CCC_SOUNDS_LS = 'ccc-sounds-enabled';
        return _CCC_SOUNDS_LS;
      },
      _audioCtx() {
        const _audioCtx = null;
        return _audioCtx;
      },
      _CCC_GLOW_MS() {
        const _CCC_GLOW_MS = 6000;
        return _CCC_GLOW_MS;
      },
      _cccGlowUntil() {
        const _cccGlowUntil = new Map();
        return _cccGlowUntil;
      },
      _cccGlowTicker() {
        const _cccGlowTicker = null;
        return _cccGlowTicker;
      },
      _sysHealthTimer() {
        const _sysHealthTimer = null;
        return _sysHealthTimer;
      },
      _sysProcessesTimer() {
        const _sysProcessesTimer = null;
        return _sysProcessesTimer;
      },
      _sysJobsTimer() {
        const _sysJobsTimer = null;
        return _sysJobsTimer;
      },
      _cccLocalSpawns() {
        const _cccLocalSpawns = { ids: new Set(), inFlight: 0 };
        return _cccLocalSpawns;
      },
      BACKGROUND_API_READ_LIMIT() {
        const BACKGROUND_API_READ_LIMIT = 4;
        return BACKGROUND_API_READ_LIMIT;
      },
      _backgroundApiBaseFetch() {
        const _backgroundApiBaseFetch = window.fetch.bind(window);
        return _backgroundApiBaseFetch;
      },
      _backgroundApiReadControllers() {
        const _backgroundApiReadControllers = new Set();
        return _backgroundApiReadControllers;
      },
      _backgroundApiReadQueue() {
        const _backgroundApiReadQueue = [];
        return _backgroundApiReadQueue;
      },
      _backgroundApiReadActive() {
        const _backgroundApiReadActive = 0;
        return _backgroundApiReadActive;
      },
      _archiveBootstrapWindow() {
        const _archiveBootstrapWindow = '7d';
        return _archiveBootstrapWindow;
      },
      _startupDeferredApiReads() {
        const _startupDeferredApiReads = [];
        return _startupDeferredApiReads;
      },
      _startupBaseFetch() {
        const _startupBaseFetch = window.fetch.bind(window);
        return _startupBaseFetch;
      },
      _startupApiReadsReleased() {
        const _startupApiReadsReleased = false;
        return _startupApiReadsReleased;
      },
      _startupApiReleaseTimer() {
        const _startupApiReleaseTimer = null;
        return _startupApiReleaseTimer;
      },
      _archiveBootstrapUrl() {
        const _archiveBootstrapUrl = '/api/conversations/list?window=' + encodeURIComponent(sharedState._archiveBootstrapWindow) + '&stale_ok=1';
        return _archiveBootstrapUrl;
      },
      _archiveBootstrapFetchPromise() {
        const _archiveBootstrapFetchPromise = Promise.resolve().then(async () => {
    if (window.__CCC_CONVERSATION_POPOUT__) return null;
    await (0, sharedState._whenDashboardUncovered)();
    const _archiveBootstrapController = new AbortController();
    const _archiveBootstrapTimeout = setTimeout(() => {
      try { _archiveBootstrapController.abort(); } catch (_) {}
    }, 45 * 1000);
    try {
      return await (0, sharedState.backgroundApiFetch)(sharedState._archiveBootstrapUrl, { signal: _archiveBootstrapController.signal });
    } catch (_) {
      return null;
    } finally {
      clearTimeout(_archiveBootstrapTimeout);
    }
  });
        return _archiveBootstrapFetchPromise;
      },
      _archiveBootstrapConsumed() {
        const _archiveBootstrapConsumed = false;
        return _archiveBootstrapConsumed;
      },
      _startupDeferrableBeacons() {
        const _startupDeferrableBeacons = ['/api/client-log'];
        return _startupDeferrableBeacons;
      },
      _startupDirectReplayPaths() {
        const _startupDirectReplayPaths = ['/api/engines/models', '/api/spawn-defaults', '/api/model-picker/picks', '/api/repo/list'];
        return _startupDirectReplayPaths;
      },
      APP_EMBED_MODE() {
        const APP_EMBED_MODE = !!window.__CCC_APP_EMBED__;
        return APP_EMBED_MODE;
      },
      _appEmbedSkipPrefixes() {
        const _appEmbedSkipPrefixes = [
    '/api/settings', '/api/savings', '/api/headroom', '/api/ci', '/api/free-failover',
    '/api/queue/list', '/api/queue/import-doc', '/api/config-consent', '/api/telemetry/status',
    '/api/throughput', '/api/objects', '/api/codex/stuck-summary', '/api/health',
    '/api/cloud-relay/indicator', '/api/phone-access/status', '/api/car-mode/status',
    '/api/version', '/api/decision-inbox', '/api/engines/installed', '/api/free-runtime/status',
    '/api/today-stars', '/api/group-chats', '/api/watchtower/alerts', '/api/repo/list',
    '/api/system/anomalies',
  ];
        return _appEmbedSkipPrefixes;
      },
      STARTUP_OPEN_HOLD_MAX_MS() {
        const STARTUP_OPEN_HOLD_MAX_MS = 5000;
        return STARTUP_OPEN_HOLD_MAX_MS;
      },
      _startupOpenFetches() {
        const _startupOpenFetches = new Set();
        return _startupOpenFetches;
      },
      _startupReleaseRequestedAt() {
        const _startupReleaseRequestedAt = 0;
        return _startupReleaseRequestedAt;
      },
      _lastTextKeyTs() {
        const _lastTextKeyTs = 0;
        return _lastTextKeyTs;
      },
      _convListScrollQuietUntil() {
        const _convListScrollQuietUntil = 0;
        return _convListScrollQuietUntil;
      },
      _dashboardUncoverWaiters() {
        const _dashboardUncoverWaiters = [];
        return _dashboardUncoverWaiters;
      },
      APP_CONFIG() {
        const APP_CONFIG = {
    app_name: 'Command Center for Claude, Codex, Cursor, and Anti-Gravity',
    title_strip: [],
    repo: '',
    vercel_enabled: false,
    vercel_project: '',
    pkood_enabled: false,
    gh_enabled: true,
  };
        return APP_CONFIG;
      },
      _bootUrlParams() {
        const _bootUrlParams = new URLSearchParams(window.location.search || '');
        return _bootUrlParams;
      },
      CONV_POPOUT_MODE() {
        const CONV_POPOUT_MODE = sharedState._bootUrlParams.get('ccc_popout') === 'conversation'
    || sharedState._bootUrlParams.get('popout') === 'conversation';
        return CONV_POPOUT_MODE;
      },
      CONV_POPOUT_TARGET() {
        const CONV_POPOUT_TARGET = (
    sharedState._bootUrlParams.get('conv')
    || sharedState._bootUrlParams.get('conversation')
    || sharedState._bootUrlParams.get('session_id')
    || ''
  ).trim();
        return CONV_POPOUT_TARGET;
      },
      CONV_POPOUT_REPO_PATH() {
        const CONV_POPOUT_REPO_PATH = (sharedState._bootUrlParams.get('repo_path') || '').trim();
        return CONV_POPOUT_REPO_PATH;
      },
      CONV_POPOUT_BG() {
        const CONV_POPOUT_BG = (() => {
    const v = (sharedState._bootUrlParams.get('conv_bg') || '').trim();
    return sharedState.CONV_POPOUT_MODE && /^(#[0-9a-f]{6}|[a-z]{2,20})$/i.test(v) ? v : '';
  })();
        return CONV_POPOUT_BG;
      },
      FLOW_POPOUT_MODE() {
        const FLOW_POPOUT_MODE = sharedState._bootUrlParams.get('ccc_popout') === 'flow'
    || sharedState._bootUrlParams.get('popout') === 'flow';
        return FLOW_POPOUT_MODE;
      },
      GROUPCHAT_POPOUT_MODE() {
        const GROUPCHAT_POPOUT_MODE = sharedState._bootUrlParams.get('ccc_popout') === 'groupchat'
    || sharedState._bootUrlParams.get('popout') === 'groupchat';
        return GROUPCHAT_POPOUT_MODE;
      },
      READER_ONLY_POPOUT() {
        const READER_ONLY_POPOUT = sharedState.CONV_POPOUT_MODE || sharedState.GROUPCHAT_POPOUT_MODE;
        return READER_ONLY_POPOUT;
      },
      _titleStripRe() {
        const _titleStripRe = null;
        return _titleStripRe;
      },
      LEADING_PATH_OR_URL_RE() {
        const LEADING_PATH_OR_URL_RE = /^(?:https?:\/\/\S+|file:\/\/\S+|\S*\/\S*\.[A-Za-z0-9]{1,8})\s+(?=\S)/;
        return LEADING_PATH_OR_URL_RE;
      },
      LEADING_CCC_PASTED_IMAGE_PATH_RE() {
        const LEADING_CCC_PASTED_IMAGE_PATH_RE = /^(?:file:\/\/)?(?:\/[^\n]*?\/)?\.claude\/(?:command[- ]center\/)?pasted[ -]images\/paste[- ][\w.-]+?\.(?:png|jpe?g|gif|webp)\s+(?=\S)/i;
        return LEADING_CCC_PASTED_IMAGE_PATH_RE;
      },
      ORIGINAL_ASK_EXPAND_BTN() {
        const ORIGINAL_ASK_EXPAND_BTN = '<button type="button" class="original-ask-expand" data-role="original-ask-expand"'
    + ' title="Open the original ask in a larger, easier-to-read view" aria-label="Open original ask in a reading view">+</button>';
        return ORIGINAL_ASK_EXPAND_BTN;
      },
      ARCHIVE_WINDOW_KEY() {
        const ARCHIVE_WINDOW_KEY = 'ccc-archive-window';
        return ARCHIVE_WINDOW_KEY;
      },
      IP_NYA_DETAILS_KEY() {
        const IP_NYA_DETAILS_KEY = 'ccc-inprogress-nya-details';
        return IP_NYA_DETAILS_KEY;
      },
      _ultraFresh() {
        const _ultraFresh = { entries: new Map(), recent: new Map(), source: null, ids: '', generation: 0, queued: false, connectTimer: null };
        return _ultraFresh;
      },
      ULTRA_FRESH_CONNECT_SETTLE_MS() {
        const ULTRA_FRESH_CONNECT_SETTLE_MS = 1200;
        return ULTRA_FRESH_CONNECT_SETTLE_MS;
      },
      _ATTN_NONE_RE() {
        const _ATTN_NONE_RE = /^(?:none|nothing|no action(?: is)?(?: needed| required)?|n\/?a|not applicable)?\s*[.!]?\s*(?:[—–(:-][\s\S]*)?$/i;
        return _ATTN_NONE_RE;
      },
      _ATTN_OWNER_RE() {
        const _ATTN_OWNER_RE = /\bowner\s*[:=]\s*(agent|user|external|amir|human)\b|^\s*\(?(agent|user|external|amir|human)\)?\s*[:—–-]/i;
        return _ATTN_OWNER_RE;
      },
      _handoffOptimistic() {
        const _handoffOptimistic = new Map();
        return _handoffOptimistic;
      },
      ROWS_SPACING_KEY() {
        const ROWS_SPACING_KEY = 'ccc-rows-spacing';
        return ROWS_SPACING_KEY;
      },
      _nyaItemsBySid() {
        const _nyaItemsBySid = new Map();
        return _nyaItemsBySid;
      },
      SUBAGENT_CLUSTERS_EXPANDED_KEY() {
        const SUBAGENT_CLUSTERS_EXPANDED_KEY = 'ccc-subagent-clusters-expanded';
        return SUBAGENT_CLUSTERS_EXPANDED_KEY;
      },
      SUBAGENT_CLUSTERS_COLLAPSED_KEY() {
        const SUBAGENT_CLUSTERS_COLLAPSED_KEY = 'ccc-subagent-clusters-collapsed';
        return SUBAGENT_CLUSTERS_COLLAPSED_KEY;
      },
      repoListState() {
        const repoListState = { repos: [], current: '', recent: [] };
        return repoListState;
      },
      sessionIdByConv() {
        const sessionIdByConv = {};
        return sessionIdByConv;
      },
      sessionCwdByConv() {
        const sessionCwdByConv = {};
        return sessionCwdByConv;
      },
      sessionCwdExistsByConv() {
        const sessionCwdExistsByConv = {};
        return sessionCwdExistsByConv;
      },
      sessionSourceByConv() {
        const sessionSourceByConv = {};
        return sessionSourceByConv;
      },
      sessionSpawnPidByConv() {
        const sessionSpawnPidByConv = {};
        return sessionSpawnPidByConv;
      },
      liveStatus() {
        const liveStatus = { forSessionId: null, live: false, pid: null, tty: null, terminalApp: null, sidecarTool: null, sidecarFile: null, sidecarStatus: null, sidecarTs: 0, sidecarInFlight: false, staleToolCall: false, staleToolAgeS: 0, needsApproval: false, needsApprovalMessage: '', acpPendingPermission: null, questionWaiting: false, questionText: '', questionHeader: '', questionPreamble: '', questionOptions: [], questionOptionDetails: [], codexAppServer: false, codexAppServerTransport: null, codexManagedAppServer: false, codexAppServerEventSeq: 0, codexAppServerLastActivityAt: 0, codexAppServerLastItemId: '' };
        return liveStatus;
      },
      liveStatusTimer() {
        const liveStatusTimer = null;
        return liveStatusTimer;
      },
      _liveStatusPrev() {
        const _liveStatusPrev = null;
        return _liveStatusPrev;
      },
      _codexWorkingHoldCount() {
        const _codexWorkingHoldCount = 0;
        return _codexWorkingHoldCount;
      },
      _liveStatusFailHoldCount() {
        const _liveStatusFailHoldCount = 0;
        return _liveStatusFailHoldCount;
      },
      CODEX_WAKE_TEXT() {
        const CODEX_WAKE_TEXT = 'Status check: your last tool call has not returned for a while. If you are stuck, say what you were waiting on, stop polling that command, and continue with the next concrete step.';
        return CODEX_WAKE_TEXT;
      },
      liveStatusRenderTicker() {
        const liveStatusRenderTicker = null;
        return liveStatusRenderTicker;
      },
      _msgTsTick() {
        const _msgTsTick = 0;
        return _msgTsTick;
      },
      _sessionLiveOverlay() {
        const _sessionLiveOverlay = new Map();
        return _sessionLiveOverlay;
      },
      _LIVE_OVERLAY_KEYS() {
        const _LIVE_OVERLAY_KEYS = [
    'is_live', 'state', 'ended_blocked', 'sidecar_status', 'sidecar_has_writes', 'sidecar_tool', 'sidecar_file',
    'sidecar_ts', 'sidecar_in_flight', 'is_compacting', 'compacting_trigger', 'recently_interrupted', 'pending_tool', 'pending_file', 'last_event_type',
    'needs_approval', 'needs_approval_message', 'question_waiting', 'question_text',
    'question_header', 'question_preamble', 'question_options', 'question_option_details',
    'codex_state', 'codex_fresh', 'codex_state_reason',
    'codex_writer', 'codex_desktop_attached',
    'transcript_mtime', 'last_event_ts', 'pending_tool_ts', 'codex_app_server_last_activity_at',
    'working_since',
  ];
        return _LIVE_OVERLAY_KEYS;
      },
      F2_TOKEN_THRESHOLD() {
        const F2_TOKEN_THRESHOLD = 100000;
        return F2_TOKEN_THRESHOLD;
      },
      F2_STALE_MINUTES() {
        const F2_STALE_MINUTES = 60;
        return F2_STALE_MINUTES;
      },
      F2_STALE_MINUTES_CODEX() {
        const F2_STALE_MINUTES_CODEX = 45;
        return F2_STALE_MINUTES_CODEX;
      },
      F2_STALE_MINUTES_KIMI() {
        const F2_STALE_MINUTES_KIMI = 35;
        return F2_STALE_MINUTES_KIMI;
      },
      F2_ENGINE_CACHE() {
        const F2_ENGINE_CACHE = {
    claude: {
      staleMinutes: sharedState.F2_STALE_MINUTES,
      decay: 'cliff',
      railNote: 'cache likely cold',
      // Past the cliff ~90% of reads miss. "Likely gone cold" understated it.
      verdictNote: 'past ' + sharedState.F2_STALE_MINUTES + ' min idle roughly 9 in 10 cache reads miss, so expect to pay for nearly all of it again',
    },
    codex: {
      staleMinutes: sharedState.F2_STALE_MINUTES_CODEX,
      decay: 'graded',
      railNote: 'cache likely partial',
      // No cliff to point at — the honest statement is "some of it", scaling
      // with idle time, not "all of it".
      verdictNote: 'Codex caches decay gradually rather than expiring, so a growing share of that reload is no longer cached',
    },
    kimi: {
      staleMinutes: sharedState.F2_STALE_MINUTES_KIMI,
      decay: 'graded',
      railNote: 'cache likely partial',
      // Measured: retention is >99% under ~30 min, then variable; by 40 min
      // roughly half of samples retain <50%. The warning hedges because the
      // decay is not a clean cliff.
      verdictNote: 'after ' + sharedState.F2_STALE_MINUTES_KIMI + ' min idle a material share of Kimi context cache reads may miss, and the miss rate grows with idle time',
    },
  };
        return F2_ENGINE_CACHE;
      },
      F2_ENGINE_ALIASES() {
        const F2_ENGINE_ALIASES = { interactive: 'claude' };
        return F2_ENGINE_ALIASES;
      },
      F2_LAUNCH_ENGINES() {
        const F2_LAUNCH_ENGINES = [
    { id: 'claude', label: 'Claude', fallback: [{ id: 'sonnet-5', label: 'sonnet-5' }] },
    { id: 'codex',  label: 'Codex',  fallback: [{ id: 'gpt-6-astra', label: '6 Astra' }] },
    { id: 'kimi',   label: 'Kimi',   fallback: [{ id: 'kimi-code/k3', label: 'K3' }] },
  ];
        return F2_LAUNCH_ENGINES;
      },
      F2_LAUNCH_EFFORTS() {
        const F2_LAUNCH_EFFORTS = [
    { id: 'low',    label: 'Light' },
    { id: 'medium', label: 'Medium' },
    { id: 'high',   label: 'High' },
    { id: 'xhigh',  label: 'Extra High' },
  ];
        return F2_LAUNCH_EFFORTS;
      },
      f2PaneState() {
        const f2PaneState = new Map();
        return f2PaneState;
      },
      f2ManualPanes() {
        const f2ManualPanes = new Map();
        return f2ManualPanes;
      },
      f2DismissedPanes() {
        const f2DismissedPanes = new Map();
        return f2DismissedPanes;
      },
      F2_ENGINE_LOCATE() {
        const F2_ENGINE_LOCATE = {
    claude: {
      label: 'Claude',
      note: 'Its transcript is a JSONL under ~/.claude/projects/. Locate it with:',
      cmd: sid => '  ls ~/.claude/projects/*/' + sid + '.jsonl',
    },
    codex: {
      label: 'Codex',
      note: 'Its transcript is a rollout JSONL under ~/.codex/sessions/. Locate it with:',
      cmd: sid => '  find ~/.codex/sessions -name "*' + sid + '.jsonl"',
    },
    kimi: {
      label: 'Kimi',
      note: 'Its transcript path is recorded in ~/.kimi-code/session_index.jsonl. Locate it with:',
      cmd: sid => '  grep ' + sid + ' ~/.kimi-code/session_index.jsonl',
    },
  };
        return F2_ENGINE_LOCATE;
      },
      MANUAL_SUBSESSION_KEY() {
        const MANUAL_SUBSESSION_KEY = 'ccc-manual-subsession-edges';
        return MANUAL_SUBSESSION_KEY;
      },
      _manualSubsessionEdgesCache() {
        const _manualSubsessionEdgesCache = { raw: null, edges: {} };
        return _manualSubsessionEdgesCache;
      },
      _CONTINUATION_ORIGIN_RE() {
        const _CONTINUATION_ORIGIN_RE = /Origin session id: ([A-Za-z0-9][A-Za-z0-9_.-]{7,127})/;
        return _CONTINUATION_ORIGIN_RE;
      },
      _CONTINUATION_BOILERPLATE_RE() {
        const _CONTINUATION_BOILERPLATE_RE = /^\s*(?:[⤴↱]︎?️?\s*)*(?:Continue\s+)?You are continuing a task from an earlier\b/i;
        return _CONTINUATION_BOILERPLATE_RE;
      },
      _f2ContinuationEdgesCache() {
        const _f2ContinuationEdgesCache = { raw: null, edges: {} };
        return _f2ContinuationEdgesCache;
      },
      _sidebarFamilyParents() {
        const _sidebarFamilyParents = new Map();
        return _sidebarFamilyParents;
      },
      _f2OriginIndexCache() {
        const _f2OriginIndexCache = { rows: null, len: -1, byId: null, successors: null };
        return _f2OriginIndexCache;
      },
      _contSuccIdxCache() {
        const _contSuccIdxCache = { convs: null, arch: null, clen: -1, alen: -1, succ: null };
        return _contSuccIdxCache;
      },
      F2_ROUTES() {
        const F2_ROUTES = {
    continue: {
      glyph: '→',
      short: 'Continue new',
      name: 'Continue in a new session',
      desc: 'Carries your text and this session’s id. Tails the transcript for recent state, greps for anything older.',
      launches: true,
    },
  };
        return F2_ROUTES;
      },
      _liveSessionsActivityPromise() {
        const _liveSessionsActivityPromise = null;
        return _liveSessionsActivityPromise;
      },
      _liveSessionsActivityLast() {
        const _liveSessionsActivityLast = { sessions: {} };
        return _liveSessionsActivityLast;
      },
      _liveSessionsActivityFetchedAt() {
        const _liveSessionsActivityFetchedAt = 0;
        return _liveSessionsActivityFetchedAt;
      },
      _lastKillEventId() {
        const _lastKillEventId = '';
        return _lastKillEventId;
      },
      _lastPrewarmEventId() {
        const _lastPrewarmEventId = '';
        return _lastPrewarmEventId;
      },
      _interruptAskLogged() {
        const _interruptAskLogged = new Set();
        return _interruptAskLogged;
      },
      _interruptAskAutoDismissPending() {
        const _interruptAskAutoDismissPending = new Set();
        return _interruptAskAutoDismissPending;
      },
      _activityLogLines() {
        const _activityLogLines = [];
        return _activityLogLines;
      },
      _activityLogActive() {
        const _activityLogActive = false;
        return _activityLogActive;
      },
      _activityLogRenderPending() {
        const _activityLogRenderPending = false;
        return _activityLogRenderPending;
      },
      _activityLogRenderedIds() {
        const _activityLogRenderedIds = new Set();
        return _activityLogRenderedIds;
      },
      _ACTIVITY_LOG_MAX() {
        const _ACTIVITY_LOG_MAX = 50;
        return _ACTIVITY_LOG_MAX;
      },
      $jumpBtnConv() {
        const $jumpBtnConv = document.getElementById('jumpBtnConv');
        return $jumpBtnConv;
      },
      $launchWrapConv() {
        const $launchWrapConv = document.getElementById('launchWrapConv');
        return $launchWrapConv;
      },
      $launchBtnConv() {
        const $launchBtnConv = document.getElementById('launchBtnConv');
        return $launchBtnConv;
      },
      $launchChoiceBtnConv() {
        const $launchChoiceBtnConv = document.getElementById('launchChoiceBtnConv');
        return $launchChoiceBtnConv;
      },
      $launchChoiceMenuConv() {
        const $launchChoiceMenuConv = document.getElementById('launchChoiceMenuConv');
        return $launchChoiceMenuConv;
      },
      $announceBtnConv() {
        const $announceBtnConv = document.getElementById('announceBtnConv');
        return $announceBtnConv;
      },
      $pkoodKillBtn() {
        const $pkoodKillBtn = document.getElementById('pkoodKillBtn');
        return $pkoodKillBtn;
      },
      $convReplayBtn() {
        const $convReplayBtn = document.getElementById('convReplayBtn');
        return $convReplayBtn;
      },
      pkoodTailPoller() {
        const pkoodTailPoller = null;
        return pkoodTailPoller;
      },
      codexLogPoller() {
        const codexLogPoller = null;
        return codexLogPoller;
      },
      $codexHandoverBtn() {
        const $codexHandoverBtn = document.getElementById('codexHandoverBtn');
        return $codexHandoverBtn;
      },
      codexHandoverWait() {
        const codexHandoverWait = null;
        return codexHandoverWait;
      },
      $convOverflowBtn() {
        const $convOverflowBtn = document.getElementById('convOverflowBtn');
        return $convOverflowBtn;
      },
      $convOverflowMenu() {
        const $convOverflowMenu = document.getElementById('convOverflowMenu');
        return $convOverflowMenu;
      },
      _lastSeenSpawnCount() {
        const _lastSeenSpawnCount = null;
        return _lastSeenSpawnCount;
      },
      _lastStatusCheckedAt() {
        const _lastStatusCheckedAt = 0;
        return _lastStatusCheckedAt;
      },
      _liveStatusFetchingKey() {
        const _liveStatusFetchingKey = '';
        return _liveStatusFetchingKey;
      },
      _codexAppServerStatusKeys() {
        const _codexAppServerStatusKeys = new Map();
        return _codexAppServerStatusKeys;
      },
      _acpWireMtimeKeys() {
        const _acpWireMtimeKeys = new Map();
        return _acpWireMtimeKeys;
      },
      _relayedQuestionState() {
        const _relayedQuestionState = {
    sessionId: null,     // session the open modal belongs to
    nonce: null,         // server nonce for the open question batch
    fetching: false,     // GET /api/question in flight
    submitting: false,   // POST /api/answer-question in flight
    questions: null,     // questions array currently rendered
    selections: null,    // per-question state: { picked:Set<int>, custom:string }
  };
        return _relayedQuestionState;
      },
      _lastAnsweredQuestionNonce() {
        const _lastAnsweredQuestionNonce = null;
        return _lastAnsweredQuestionNonce;
      },
      _afterConvTailQueue() {
        const _afterConvTailQueue = [];
        return _afterConvTailQueue;
      },
      _afterConvTailTimer() {
        const _afterConvTailTimer = 0;
        return _afterConvTailTimer;
      },
      _sendingSessions() {
        const _sendingSessions = new Map();
        return _sendingSessions;
      },
      _optimisticSessionTouches() {
        const _optimisticSessionTouches = new Map();
        return _optimisticSessionTouches;
      },
      _SENDING_TIMEOUT_MS() {
        const _SENDING_TIMEOUT_MS = 5 * 60 * 1000;
        return _SENDING_TIMEOUT_MS;
      },
      _PENDING_SEND_ECHO_MAX_MS() {
        const _PENDING_SEND_ECHO_MAX_MS = sharedState._SENDING_TIMEOUT_MS + 15000;
        return _PENDING_SEND_ECHO_MAX_MS;
      },
      _preSendPreview() {
        const _preSendPreview = new Map();
        return _preSendPreview;
      },
      _earlySendRefreshTimer() {
        const _earlySendRefreshTimer = null;
        return _earlySendRefreshTimer;
      },
      _turnEndRefreshTimer() {
        const _turnEndRefreshTimer = null;
        return _turnEndRefreshTimer;
      },
      _turnEndRefreshLast() {
        const _turnEndRefreshLast = 0;
        return _turnEndRefreshLast;
      },
      _workersHoistState() {
        const _workersHoistState = { engine: '', tier: '' };
        return _workersHoistState;
      },
      _optimisticAgentTimer() {
        const _optimisticAgentTimer = null;
        return _optimisticAgentTimer;
      },
      _optimisticAgentTick() {
        const _optimisticAgentTick = null;
        return _optimisticAgentTick;
      },
      _optimisticAgentStart() {
        const _optimisticAgentStart = 0;
        return _optimisticAgentStart;
      },
      _optimisticAgentStartKey() {
        const _optimisticAgentStartKey = '';
        return _optimisticAgentStartKey;
      },
      _optimisticAgentMisses() {
        const _optimisticAgentMisses = 0;
        return _optimisticAgentMisses;
      },
      _codexWakePoll() {
        const _codexWakePoll = null;
        return _codexWakePoll;
      },
      _codexWakePollSid() {
        const _codexWakePollSid = null;
        return _codexWakePollSid;
      },
      _codexWakeStageKey() {
        const _codexWakeStageKey = '';
        return _codexWakeStageKey;
      },
      _codexWakeStageChangedAt() {
        const _codexWakeStageChangedAt = 0;
        return _codexWakeStageChangedAt;
      },
      WAKE_STAGE_DETAIL_DELAY_MS() {
        const WAKE_STAGE_DETAIL_DELAY_MS = 1000;
        return WAKE_STAGE_DETAIL_DELAY_MS;
      },
      _uxNudgeLast() {
        const _uxNudgeLast = {};
        return _uxNudgeLast;
      },
      _UX_NUDGE_THROTTLE_MS() {
        const _UX_NUDGE_THROTTLE_MS = 5 * 60 * 1000;
        return _UX_NUDGE_THROTTLE_MS;
      },
      _liveStripShown() {
        const _liveStripShown = false;
        return _liveStripShown;
      },
      _liveStripLastLiveTime() {
        const _liveStripLastLiveTime = 0;
        return _liveStripLastLiveTime;
      },
      _liveStripLastKeyTs() {
        const _liveStripLastKeyTs = 0;
        return _liveStripLastKeyTs;
      },
      $convSessionId() {
        const $convSessionId = document.getElementById('convSessionId');
        return $convSessionId;
      },
      RAIL_SPAWNED_VIA_LABELS() {
        const RAIL_SPAWNED_VIA_LABELS = {
    cli: ['CLI', 'Spawned from the ccc CLI.'],
    ui: ['UI', 'Spawned from the CCC dashboard UI.'],
    'ui-automated': ['UI · automated', 'Spawned from a browser tab under automation control (navigator.webdriver) - e.g. a Playwright/Puppeteer-driven session, not necessarily a human click.'],
    api: ['API', 'Spawned via a direct API call with no browser Origin/Referer.'],
    terminal: ['Terminal', 'Started from an interactive terminal session outside CCC.'],
    subagent: ['Subagent', 'Spawned as a subagent by another session.'],
    watchtower: ['WatchTower', 'Spawned by the WatchTower queue runner.'],
    'ccc-ask': ['ccc-ask', 'Spawned via the ccc-ask assistant.'],
    resumed: ['Resumed', 'Resumed from an earlier session.'],
    resume: ['Resumed', 'Resumed from an earlier session.'],
    external: ['External', 'Spawned by an external tool or script.'],
    'external-tool': ['External tool', 'Spawned by an external tool.'],
  };
        return RAIL_SPAWNED_VIA_LABELS;
      },
      _ttsHoverTimer() {
        const _ttsHoverTimer = null;
        return _ttsHoverTimer;
      },
      $convInputBar() {
        const $convInputBar = document.getElementById('convInputBar');
        return $convInputBar;
      },
      $convInput() {
        const $convInput = document.getElementById('convInput');
        return $convInput;
      },
      $convSendBtn() {
        const $convSendBtn = document.getElementById('convSendBtn');
        return $convSendBtn;
      },
      $convSubmitPlusBtn() {
        const $convSubmitPlusBtn = document.getElementById('convSubmitPlusBtn');
        return $convSubmitPlusBtn;
      },
      $convSendQueueBtn() {
        const $convSendQueueBtn = document.getElementById('convSendQueueBtn');
        return $convSendQueueBtn;
      },
      $convSteerBtn() {
        const $convSteerBtn = document.getElementById('convSteerBtn');
        return $convSteerBtn;
      },
      $convCompactBtn() {
        const $convCompactBtn = document.getElementById('convCompactBtn');
        return $convCompactBtn;
      },
      $convAnnouncedFrom() {
        const $convAnnouncedFrom = document.getElementById('convAnnouncedFrom');
        return $convAnnouncedFrom;
      },
      $convAnnouncedFromControl() {
        const $convAnnouncedFromControl = sharedState.$convAnnouncedFrom && sharedState.$convAnnouncedFrom.closest('.conv-announced-from');
        return $convAnnouncedFromControl;
      },
      $convTtsBtn() {
        const $convTtsBtn = document.getElementById('convTtsBtn');
        return $convTtsBtn;
      },
      $convMicBtn() {
        const $convMicBtn = document.getElementById('convMicBtn');
        return $convMicBtn;
      },
      $convEscBtn() {
        const $convEscBtn = document.getElementById('convEscBtn');
        return $convEscBtn;
      },
      $convTtyLabel() {
        const $convTtyLabel = document.getElementById('convTtyLabel') || document.createElement('span');
        return $convTtyLabel;
      },
      $convCodexAppSrv() {
        const $convCodexAppSrv = document.getElementById('convCodexAppSrv');
        return $convCodexAppSrv;
      },
      $convCodexAppSrvLabel() {
        const $convCodexAppSrvLabel = document.getElementById('convCodexAppSrvLabel');
        return $convCodexAppSrvLabel;
      },
      _compactInFlight() {
        const _compactInFlight = false;
        return _compactInFlight;
      },
      _autoHandoverToggleInFlight() {
        const _autoHandoverToggleInFlight = false;
        return _autoHandoverToggleInFlight;
      },
      AUTO_HANDOVER_MODE_CYCLE() {
        const AUTO_HANDOVER_MODE_CYCLE = ['compact', 'mdfile', 'both', 'off'];
        return AUTO_HANDOVER_MODE_CYCLE;
      },
      AUTO_HANDOVER_MODE_LABELS() {
        const AUTO_HANDOVER_MODE_LABELS = { compact: 'compact', mdfile: 'md', both: 'both', off: 'OFF' };
        return AUTO_HANDOVER_MODE_LABELS;
      },
      _AUTO_HANDOVER_CYCLE_DEBOUNCE_MS() {
        const _AUTO_HANDOVER_CYCLE_DEBOUNCE_MS = 450;
        return _AUTO_HANDOVER_CYCLE_DEBOUNCE_MS;
      },
      _autoHandoverCycleTimers() {
        const _autoHandoverCycleTimers = {};
        return _autoHandoverCycleTimers;
      },
      _autoHandoverConfirmedState() {
        const _autoHandoverConfirmedState = {};
        return _autoHandoverConfirmedState;
      },
      _recentlyClearedGoals() {
        const _recentlyClearedGoals = new Map();
        return _recentlyClearedGoals;
      },
      SLASH_FALLBACK_COMMANDS() {
        const SLASH_FALLBACK_COMMANDS = [
    { name: '/compact', description: 'Compact conversation context' },
    { name: '/context', description: 'Show context usage' },
    { name: '/cost', description: 'Show current session cost' },
    { name: '/help', description: 'Show available commands' },
    { name: '/mcp', description: 'Manage MCP servers' },
    { name: '/model', description: 'Select or change model' },
    { name: '/status', description: 'Show session status' },
  ];
        return SLASH_FALLBACK_COMMANDS;
      },
      CODEX_SLASH_FALLBACK_COMMANDS() {
        const CODEX_SLASH_FALLBACK_COMMANDS = [
    { name: '/approve', description: 'Approve one retry of a recent auto-review denial' },
    { name: '/apps', description: 'Browse apps and insert them into the prompt' },
    { name: '/clear', description: 'Clear the terminal and start a new chat' },
    { name: '/compact', description: 'Summarize the visible conversation to free tokens' },
    { name: '/copy', description: 'Copy the latest completed Codex output' },
    { name: '/diff', description: 'Show the current Git diff' },
    { name: '/exit', description: 'Exit the Codex CLI' },
    { name: '/fast', description: 'Toggle the current model fast tier' },
    { name: '/fork', description: 'Fork the current conversation' },
    { name: '/goal', description: 'Set or view a task goal' },
    { name: '/hooks', description: 'Review lifecycle hooks' },
    { name: '/ide', description: 'Include IDE context' },
    { name: '/init', description: 'Generate an AGENTS.md scaffold' },
    { name: '/logout', description: 'Sign out of Codex' },
    { name: '/mcp', description: 'List configured MCP tools' },
    { name: '/memories', description: 'Configure memory use and generation' },
    { name: '/mention', description: 'Attach a file to the conversation' },
    { name: '/model', description: 'Choose the active model' },
    { name: '/new', description: 'Start a new conversation in the same CLI' },
    { name: '/permissions', description: 'Set approval and permission behavior' },
    { name: '/personality', description: 'Choose a communication style' },
    { name: '/plan', description: 'Switch to plan mode' },
    { name: '/plugins', description: 'Browse installed and discoverable plugins' },
    { name: '/quit', description: 'Exit the Codex CLI' },
    { name: '/raw', description: 'Toggle raw scrollback mode' },
    { name: '/resume', description: 'Resume a saved conversation' },
    { name: '/review', description: 'Ask Codex to review the working tree' },
    { name: '/sandbox-add-read-dir', description: 'Grant sandbox read access to a directory' },
    { name: '/skills', description: 'Browse and use skills' },
    { name: '/status', description: 'Display session configuration and token usage' },
    { name: '/statusline', description: 'Configure TUI status-line fields' },
    { name: '/stop', description: 'Stop background terminals' },
    { name: '/theme', description: 'Choose a syntax-highlighting theme' },
    { name: '/title', description: 'Configure terminal title items' },
    { name: '/vim', description: 'Toggle Vim mode' },
  ];
        return CODEX_SLASH_FALLBACK_COMMANDS;
      },
      _slashCommandCache() {
        const _slashCommandCache = new Map();
        return _slashCommandCache;
      },
      _slashMenuEl() {
        const _slashMenuEl = null;
        return _slashMenuEl;
      },
      _slashMenuInput() {
        const _slashMenuInput = null;
        return _slashMenuInput;
      },
      _slashMenuItems() {
        const _slashMenuItems = [];
        return _slashMenuItems;
      },
      _slashMenuIndex() {
        const _slashMenuIndex = 0;
        return _slashMenuIndex;
      },
      _slashMenuReq() {
        const _slashMenuReq = 0;
        return _slashMenuReq;
      },
      _wtReceiptPolls() {
        const _wtReceiptPolls = {};
        return _wtReceiptPolls;
      },
      PHONE_MODE_SUFFIX() {
        const PHONE_MODE_SUFFIX = "\n\nReply as if you're in a phone conversation with the user - response needs to be conversational and short. "
    + "Break a complex message into multiple turns of conversation. "
    + "Do not include the session-state summary block at the end of this reply.";
        return PHONE_MODE_SUFFIX;
      },
      _phoneModePending() {
        const _phoneModePending = null;
        return _phoneModePending;
      },
      _phoneModeTimer() {
        const _phoneModeTimer = null;
        return _phoneModeTimer;
      },
      _phoneModeSettleTimer() {
        const _phoneModeSettleTimer = null;
        return _phoneModeSettleTimer;
      },
      _phoneModePollTimer() {
        const _phoneModePollTimer = null;
        return _phoneModePollTimer;
      },
      TTS_TEXT_MAX_CHARS() {
        const TTS_TEXT_MAX_CHARS = 12000;
        return TTS_TEXT_MAX_CHARS;
      },
      TTS_CHUNK_MAX_CHARS() {
        const TTS_CHUNK_MAX_CHARS = 1600;
        return TTS_CHUNK_MAX_CHARS;
      },
      _ttsActive() {
        const _ttsActive = false;
        return _ttsActive;
      },
      _ttsActivePaneId() {
        const _ttsActivePaneId = null;
        return _ttsActivePaneId;
      },
      _ttsCapturedRange() {
        const _ttsCapturedRange = null;
        return _ttsCapturedRange;
      },
      _ttsCapturedAt() {
        const _ttsCapturedAt = 0;
        return _ttsCapturedAt;
      },
      _ttsSelectionReadText() {
        const _ttsSelectionReadText = '';
        return _ttsSelectionReadText;
      },
      _annotateCapturedRange() {
        const _annotateCapturedRange = null;
        return _annotateCapturedRange;
      },
      _annotateCapturedEventEl() {
        const _annotateCapturedEventEl = null;
        return _annotateCapturedEventEl;
      },
      _annotateTriggerEl() {
        const _annotateTriggerEl = null;
        return _annotateTriggerEl;
      },
      _annotatePopoverEl() {
        const _annotatePopoverEl = null;
        return _annotatePopoverEl;
      },
      ANNOTATIONS_STORAGE_KEY() {
        const ANNOTATIONS_STORAGE_KEY = 'ccc-annotations';
        return ANNOTATIONS_STORAGE_KEY;
      },
      _ttsActiveConvId() {
        const _ttsActiveConvId = null;
        return _ttsActiveConvId;
      },
      _sttRecognition() {
        const _sttRecognition = null;
        return _sttRecognition;
      },
      _sttRecording() {
        const _sttRecording = false;
        return _sttRecording;
      },
      _sttActivePaneId() {
        const _sttActivePaneId = null;
        return _sttActivePaneId;
      },
      _sttActiveConvId() {
        const _sttActiveConvId = null;
        return _sttActiveConvId;
      },
      _sttPreText() {
        const _sttPreText = '';
        return _sttPreText;
      },
      _sttPostText() {
        const _sttPostText = '';
        return _sttPostText;
      },
      _sttCommittedIndex() {
        const _sttCommittedIndex = 0;
        return _sttCommittedIndex;
      },
      _sttResultsLength() {
        const _sttResultsLength = 0;
        return _sttResultsLength;
      },
      _sttUpdatingText() {
        const _sttUpdatingText = false;
        return _sttUpdatingText;
      },
      _nativeSttListener() {
        const _nativeSttListener = null;
        return _nativeSttListener;
      },
      _ttsUtterance() {
        const _ttsUtterance = null;
        return _ttsUtterance;
      },
      _ttsTextMapping() {
        const _ttsTextMapping = [];
        return _ttsTextMapping;
      },
      _ttsChunkState() {
        const _ttsChunkState = null;
        return _ttsChunkState;
      },
      _TTS_RATE_MIN() {
        const _TTS_RATE_MIN = 0.8;
        return _TTS_RATE_MIN;
      },
      _TTS_RATE_MAX() {
        const _TTS_RATE_MAX = 1.8;
        return _TTS_RATE_MAX;
      },
      _TTS_RATE_DEFAULT() {
        const _TTS_RATE_DEFAULT = 1.25;
        return _TTS_RATE_DEFAULT;
      },
      _ttsRate() {
        const _ttsRate = (0, hostServices._readPersistedTtsRate)();
        return _ttsRate;
      },
      _ttsLastCharIndex() {
        const _ttsLastCharIndex = 0;
        return _ttsLastCharIndex;
      },
      _ttsPaused() {
        const _ttsPaused = false;
        return _ttsPaused;
      },
      _ttsBoundUtteranceText() {
        const _ttsBoundUtteranceText = '';
        return _ttsBoundUtteranceText;
      },
      _ttsRateRestartTimer() {
        const _ttsRateRestartTimer = null;
        return _ttsRateRestartTimer;
      },
      _ttsDirectBtn() {
        const _ttsDirectBtn = null;
        return _ttsDirectBtn;
      },
      _ttsStreaming() {
        const _ttsStreaming = false;
        return _ttsStreaming;
      },
      _ttsAudio() {
        const _ttsAudio = null;
        return _ttsAudio;
      },
      _ttsNeural() {
        const _ttsNeural = null;
        return _ttsNeural;
      },
      _ttsNeuralToken() {
        const _ttsNeuralToken = 0;
        return _ttsNeuralToken;
      },
      _ttsNeuralCooldownUntil() {
        const _ttsNeuralCooldownUntil = 0;
        return _ttsNeuralCooldownUntil;
      },
      _ttsBrowserPlayback() {
        const _ttsBrowserPlayback = null;
        return _ttsBrowserPlayback;
      },
      _ttsPriorityClient() {
        const _ttsPriorityClient = Array.from(crypto.getRandomValues(new Uint8Array(16)),
    n => n.toString(16).padStart(2, '0')).join('');
        return _ttsPriorityClient;
      },
      _ttsPrioritySeq() {
        const _ttsPrioritySeq = 0;
        return _ttsPrioritySeq;
      },
      _ttsPriorityTimer() {
        const _ttsPriorityTimer = null;
        return _ttsPriorityTimer;
      },
      _ttsPriorityStarted() {
        const _ttsPriorityStarted = false;
        return _ttsPriorityStarted;
      },
      _ttsKeepVoice() {
        const _ttsKeepVoice = '';
        return _ttsKeepVoice;
      },
      _ttsNeuralCooldownWhy() {
        const _ttsNeuralCooldownWhy = '';
        return _ttsNeuralCooldownWhy;
      },
      _ttsStartWhy() {
        const _ttsStartWhy = '';
        return _ttsStartWhy;
      },
      _TTS_FAIL_COOL_MS() {
        const _TTS_FAIL_COOL_MS = 20000;
        return _TTS_FAIL_COOL_MS;
      },
      _ttsCoolMs() {
        const _ttsCoolMs = (status) => status === 429 ? 60000 : status === 0 ? 15000 : sharedState._TTS_FAIL_COOL_MS;
        return _ttsCoolMs;
      },
      _TTS_HEAD_CHARS() {
        const _TTS_HEAD_CHARS = 80;
        return _TTS_HEAD_CHARS;
      },
      _ttsHeadCache() {
        const _ttsHeadCache = new Map();
        return _ttsHeadCache;
      },
      _ttsPrefetchTimer() {
        const _ttsPrefetchTimer = null;
        return _ttsPrefetchTimer;
      },
      _ttsPrefetchTarget() {
        const _ttsPrefetchTarget = null;
        return _ttsPrefetchTarget;
      },
      _ttsCacheDebug() {
        const _ttsCacheDebug = false;
        return _ttsCacheDebug;
      },
      _ttsCacheDebugFrame() {
        const _ttsCacheDebugFrame = null;
        return _ttsCacheDebugFrame;
      },
      _ttsCacheDebugObserver() {
        const _ttsCacheDebugObserver = null;
        return _ttsCacheDebugObserver;
      },
      _ttsCacheDebugResizeObserver() {
        const _ttsCacheDebugResizeObserver = null;
        return _ttsCacheDebugResizeObserver;
      },
      _ttsDebugJobs() {
        const _ttsDebugJobs = [];
        return _ttsDebugJobs;
      },
      _ttsPrefetchTargets() {
        const _ttsPrefetchTargets = new Map();
        return _ttsPrefetchTargets;
      },
      _ttsChainTokens() {
        const _ttsChainTokens = {};
        return _ttsChainTokens;
      },
      TTS_QUEUE_POS_KEY() {
        const TTS_QUEUE_POS_KEY = 'ccc-tts-queue-pos';
        return TTS_QUEUE_POS_KEY;
      },
      _ttsEnabled() {
        const _ttsEnabled = true;
        return _ttsEnabled;
      },
      _ttsTogglePending() {
        const _ttsTogglePending = false;
        return _ttsTogglePending;
      },
      _ttsInFlightAborts() {
        const _ttsInFlightAborts = new Set();
        return _ttsInFlightAborts;
      },
      _ttsServerQueueSnap() {
        const _ttsServerQueueSnap = { at: 0, extraInFlight: 0, sessionWaiting: 0, active: 0, fg: 0, bg: 0 };
        return _ttsServerQueueSnap;
      },
      _TTS_SECOND_CHARS() {
        const _TTS_SECOND_CHARS = 160;
        return _TTS_SECOND_CHARS;
      },
      _ttsCacheState() {
        const _ttsCacheState = new Map();
        return _ttsCacheState;
      },
      _TTS_OFFSCREEN_BODY_CLASSES() {
        const _TTS_OFFSCREEN_BODY_CLASSES = [
    'ccc-core-app-open',
    'ccc-mobile-app-open',
    'ccc-simple-home-open',
    'ccc-simple-screen-open',
    'mobile-ask-open',
  ];
        return _TTS_OFFSCREEN_BODY_CLASSES;
      },
      _ttsLastScreenVisible() {
        const _ttsLastScreenVisible = null;
        return _ttsLastScreenVisible;
      },
      _ttsScreenSyncTimer() {
        const _ttsScreenSyncTimer = null;
        return _ttsScreenSyncTimer;
      },
      _ttsHoverBtn() {
        const _ttsHoverBtn = null;
        return _ttsHoverBtn;
      },
      _ttsChainQueue() {
        const _ttsChainQueue = Promise.resolve();
        return _ttsChainQueue;
      },
      _TTS_PREFETCH_CLIPS() {
        const _TTS_PREFETCH_CLIPS = 4;
        return _TTS_PREFETCH_CLIPS;
      },
      _TTS_PREFETCH_CLIPS_QUIET() {
        const _TTS_PREFETCH_CLIPS_QUIET = 1;
        return _TTS_PREFETCH_CLIPS_QUIET;
      },
      _ttsChainRunning() {
        const _ttsChainRunning = new Set();
        return _ttsChainRunning;
      },
      _TTS_PREFETCH_WIDTH() {
        const _TTS_PREFETCH_WIDTH = 1;
        return _TTS_PREFETCH_WIDTH;
      },
      _ttsCanStream() {
        const _ttsCanStream = () => typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported('audio/mpeg');
        return _ttsCanStream;
      },
      _TTS_CHAIN_AHEAD_S() {
        const _TTS_CHAIN_AHEAD_S = 40;
        return _TTS_CHAIN_AHEAD_S;
      },
      _ttsChainBroken() {
        const _ttsChainBroken = false;
        return _ttsChainBroken;
      },
      _ttsPrimed() {
        const _ttsPrimed = false;
        return _ttsPrimed;
      },
      _ttsPerfTarget() {
        const _ttsPerfTarget = '';
        return _ttsPerfTarget;
      },
      _ttsPerfState() {
        const _ttsPerfState = null;
        return _ttsPerfState;
      },
      _ttsFgAbort() {
        const _ttsFgAbort = null;
        return _ttsFgAbort;
      },
      $convTtsRate() {
        const $convTtsRate = document.getElementById('convTtsRate');
        return $convTtsRate;
      },
      _ttsForceBridge() {
        const _ttsForceBridge = false;
        return _ttsForceBridge;
      },
      $cpLaunchChoiceBtnGlobal() {
        const $cpLaunchChoiceBtnGlobal = document.getElementById('cpLaunchChoiceBtn');
        return $cpLaunchChoiceBtnGlobal;
      },
      $cpLaunchChoiceMenuGlobal() {
        const $cpLaunchChoiceMenuGlobal = document.getElementById('cpLaunchChoiceMenu');
        return $cpLaunchChoiceMenuGlobal;
      },
      PEER_LABEL_RE() {
        const PEER_LABEL_RE = /^([A-Z][A-Z0-9 _/-]{1,30}):[ \t]*(.*)$/;
        return PEER_LABEL_RE;
      },
      _rowQuickActionSending() {
        const _rowQuickActionSending = new Set();
        return _rowQuickActionSending;
      },
      _rowQuickActionFeedback() {
        const _rowQuickActionFeedback = new Map();
        return _rowQuickActionFeedback;
      },
      _rowInvestigationChecks() {
        const _rowInvestigationChecks = new Map();
        return _rowInvestigationChecks;
      },
      _rowInvestigationChecksInFlight() {
        const _rowInvestigationChecksInFlight = 0;
        return _rowInvestigationChecksInFlight;
      },
      _rowDecisionReportSending() {
        const _rowDecisionReportSending = new Set();
        return _rowDecisionReportSending;
      },
      _rowDecisionReportEditor() {
        const _rowDecisionReportEditor = null;
        return _rowDecisionReportEditor;
      },
      _rowDecisionReportPending() {
        const _rowDecisionReportPending = new Map();
        return _rowDecisionReportPending;
      },
      _rowDecisionReplies() {
        const _rowDecisionReplies = new Map();
        return _rowDecisionReplies;
      },
      _COMPACT_STALL_MS() {
        const _COMPACT_STALL_MS = 4 * 60 * 1000;
        return _COMPACT_STALL_MS;
      },
      _COMPACT_TYPICAL_MS() {
        const _COMPACT_TYPICAL_MS = 90 * 1000;
        return _COMPACT_TYPICAL_MS;
      },
      _COMPACT_STEPS() {
        const _COMPACT_STEPS = [
    { key: 'ask',   label: 'Handing the transcript over' },
    { key: 'write', label: 'Writing the summary' },
    { key: 'swap',  label: 'Rebuilding the context window' },
  ];
        return _COMPACT_STEPS;
      },
      _compactRun() {
        const _compactRun = null;
        return _compactRun;
      },
      _compactRunTimer() {
        const _compactRunTimer = null;
        return _compactRunTimer;
      },
      _COMPACT_ACP_IDLE_SETTLE_MS() {
        const _COMPACT_ACP_IDLE_SETTLE_MS = 10 * 1000;
        return _COMPACT_ACP_IDLE_SETTLE_MS;
      },
      _COMPACT_ACP_NEVER_RAN_MS() {
        const _COMPACT_ACP_NEVER_RAN_MS = 20 * 1000;
        return _COMPACT_ACP_NEVER_RAN_MS;
      },
      RTL_TARGET_SEL() {
        const RTL_TARGET_SEL = '.assistant-text,.user-msg,.gc-message-body,.gc-chat-doc';
        return RTL_TARGET_SEL;
      },
      RTL_BLOCK_TAGS() {
        const RTL_BLOCK_TAGS = 'p,li,blockquote,h1,h2,h3,h4,h5,h6,td,th,dt,dd';
        return RTL_BLOCK_TAGS;
      },
      _RTL_CHAR_RE() {
        const _RTL_CHAR_RE = /[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]/;
        return _RTL_CHAR_RE;
      },
      _LTR_CHAR_RE() {
        const _LTR_CHAR_RE = /[A-Za-z]/;
        return _LTR_CHAR_RE;
      },
      _mermaidLoadPromise() {
        const _mermaidLoadPromise = null;
        return _mermaidLoadPromise;
      },
      _mermaidIdSeq() {
        const _mermaidIdSeq = 0;
        return _mermaidIdSeq;
      },
      _imgLightbox() {
        const _imgLightbox = null;
        return _imgLightbox;
      },
      _lightboxHistoryPushed() {
        const _lightboxHistoryPushed = false;
        return _lightboxHistoryPushed;
      },
      _lightboxZoom() {
        const _lightboxZoom = 0;
        return _lightboxZoom;
      },
      $conversationsView() {
        const $conversationsView = document.getElementById('conversationsView');
        return $conversationsView;
      },
      $convList() {
        const $convList = document.getElementById('convList');
        return $convList;
      },
      $convSearch() {
        const $convSearch = document.getElementById('convSearch');
        return $convSearch;
      },
      $kanbanBoard() {
        const $kanbanBoard = document.getElementById('kanbanBoard');
        return $kanbanBoard;
      },
      $flowBoard() {
        const $flowBoard = document.getElementById('flowBoard');
        return $flowBoard;
      },
      KANBAN_URL_MODE() {
        const KANBAN_URL_MODE = ['kanban', 'board'].includes(sharedState._bootUrlParams.get('ccc_mode'));
        return KANBAN_URL_MODE;
      },
      sidebarViewMode() {
        const sidebarViewMode = (0, hostServices.readSidebarViewMode)();
        return sidebarViewMode;
      },
      kanbanView() {
        const kanbanView = sharedState.sidebarViewMode === 'board';
        return kanbanView;
      },
      activeTab() {
        const activeTab = 'sessions';
        return activeTab;
      },
      $kanbanLayout() {
        const $kanbanLayout = document.getElementById('kanbanLayout');
        return $kanbanLayout;
      },
      $kanbanBoardSplit() {
        const $kanbanBoardSplit = document.getElementById('kanbanBoardSplit');
        return $kanbanBoardSplit;
      },
      $convPanelView() {
        const $convPanelView = document.getElementById('convPanelView');
        return $convPanelView;
      },
      $convPanelInput() {
        const $convPanelInput = document.getElementById('convPanelInput');
        return $convPanelInput;
      },
      $cpInput() {
        const $cpInput = document.getElementById('cpInput');
        return $cpInput;
      },
      $cpTtyLabel() {
        const $cpTtyLabel = document.getElementById('cpTtyLabel');
        return $cpTtyLabel;
      },
      $cpSessionId() {
        const $cpSessionId = document.getElementById('cpSessionId');
        return $cpSessionId;
      },
      $splitResizer() {
        const $splitResizer = document.getElementById('splitResizer');
        return $splitResizer;
      },
      $kanbanPanel() {
        const $kanbanPanel = sharedState.$kanbanLayout ? sharedState.$kanbanLayout.querySelector('.kanban-panel') : null;
        return $kanbanPanel;
      },
      $convPanel() {
        const $convPanel = sharedState.$kanbanLayout ? sharedState.$kanbanLayout.querySelector('.conv-panel') : null;
        return $convPanel;
      },
      convPanelOpen() {
        const convPanelOpen = true;
        return convPanelOpen;
      },
      $kptOpenConvBtn() {
        const $kptOpenConvBtn = document.getElementById('kptOpenConvBtn');
        return $kptOpenConvBtn;
      },
      savedKanbanWidth() {
        const savedKanbanWidth = '';
        return savedKanbanWidth;
      },
      _mobileMQ() {
        const _mobileMQ = window.matchMedia('(max-width: 1200px)');
        return _mobileMQ;
      },
      _coarsePointerMQ() {
        const _coarsePointerMQ = window.matchMedia('(pointer: coarse)');
        return _coarsePointerMQ;
      },
      _anyCoarsePointerMQ() {
        const _anyCoarsePointerMQ = window.matchMedia('(any-pointer: coarse)');
        return _anyCoarsePointerMQ;
      },
      $mobileBackBtn() {
        const $mobileBackBtn = document.getElementById('mobileBackBtn');
        return $mobileBackBtn;
      },
      $mobileOriginalAsk() {
        const $mobileOriginalAsk = document.getElementById('mobileOriginalAsk');
        return $mobileOriginalAsk;
      },
      $mobileOriginalAskText() {
        const $mobileOriginalAskText = document.getElementById('mobileOriginalAskText');
        return $mobileOriginalAskText;
      },
      _simpleHomeShowing() {
        const _simpleHomeShowing = true;
        return _simpleHomeShowing;
      },
      _simpleHomeRefreshTimer() {
        const _simpleHomeRefreshTimer = null;
        return _simpleHomeRefreshTimer;
      },
      _simpleScreen() {
        const _simpleScreen = null;
        return _simpleScreen;
      },
      _simpleReturnScreen() {
        const _simpleReturnScreen = null;
        return _simpleReturnScreen;
      },
      _simpleAutomationQueue() {
        const _simpleAutomationQueue = '';
        return _simpleAutomationQueue;
      },
      _simpleAutomationRepoPath() {
        const _simpleAutomationRepoPath = '';
        return _simpleAutomationRepoPath;
      },
      _SIMPLE_SCREENS() {
        const _SIMPLE_SCREENS = ['history', 'automations', 'automation-detail', 'settings'];
        return _SIMPLE_SCREENS;
      },
      _SIMPLE_SCREEN_IDS() {
        const _SIMPLE_SCREEN_IDS = {
    'history': 'simpleHistory',
    'automations': 'simpleAutomations',
    'automation-detail': 'simpleAutomationDetail',
    'settings': 'simpleSettings',
  };
        return _SIMPLE_SCREEN_IDS;
      },
      _simpleTechRevealedSessions() {
        const _simpleTechRevealedSessions = new Set();
        return _simpleTechRevealedSessions;
      },
      _simpleTechCurrentId() {
        const _simpleTechCurrentId = null;
        return _simpleTechCurrentId;
      },
      _SIMPLE_NYA_KINDS() {
        const _SIMPLE_NYA_KINDS = {
    needs_input: 'Waiting for your answer',
    question: 'Waiting for your answer',
    pending_tool: 'Needs your approval',
    stale_tool_call: 'Looks stuck',
    approval: 'Needs your approval',
  };
        return _SIMPLE_NYA_KINDS;
      },
      _SIMPLE_NYA_DISMISSED_KEY() {
        const _SIMPLE_NYA_DISMISSED_KEY = 'ccc-simple-nya-dismissed';
        return _SIMPLE_NYA_DISMISSED_KEY;
      },
      _SIMPLE_SEEN_KEY() {
        const _SIMPLE_SEEN_KEY = 'ccc-simple-seen-mtimes';
        return _SIMPLE_SEEN_KEY;
      },
      _simpleTaskRowsById() {
        const _simpleTaskRowsById = new Map();
        return _simpleTaskRowsById;
      },
      _simpleLifecyclePending() {
        const _simpleLifecyclePending = new Set();
        return _simpleLifecyclePending;
      },
      _simpleHistoryRows() {
        const _simpleHistoryRows = null;
        return _simpleHistoryRows;
      },
      _simpleArchiveProgressIds() {
        const _simpleArchiveProgressIds = {};
        return _simpleArchiveProgressIds;
      },
      SWIPE_RECENT_CAP() {
        const SWIPE_RECENT_CAP = 4;
        return SWIPE_RECENT_CAP;
      },
      SWIPE_RECENT_KEY() {
        const SWIPE_RECENT_KEY = 'ccc-recent-convs';
        return SWIPE_RECENT_KEY;
      },
      _swipeRecent() {
        const _swipeRecent = [];
        return _swipeRecent;
      },
      _swipeRotating() {
        const _swipeRotating = false;
        return _swipeRotating;
      },
      convFontScale() {
        const convFontScale = parseFloat(localStorage.getItem('ccc-conv-font-scale') || '1');
        return convFontScale;
      },
      $fontMinus() {
        const $fontMinus = document.getElementById('fontMinus');
        return $fontMinus;
      },
      $fontPlus() {
        const $fontPlus = document.getElementById('fontPlus');
        return $fontPlus;
      },
      conversationsData() {
        const conversationsData = [];
        return conversationsData;
      },
      conversationsLoaded() {
        const conversationsLoaded = false;
        return conversationsLoaded;
      },
      currentRepoBacklogData() {
        const currentRepoBacklogData = [];
        return currentRepoBacklogData;
      },
      _newPaneState() {
        const _newPaneState = window.CCCConversationState.createPane;
        return _newPaneState;
      },
      splitState() {
        const splitState = window.CCCConversationState.createWorkspace();
        return splitState;
      },
      _qfBootRestoreDepth() {
        const _qfBootRestoreDepth = 0;
        return _qfBootRestoreDepth;
      },
      _deepLinkedSession() {
        const _deepLinkedSession = false;
        return _deepLinkedSession;
      },
      WF_AGENT_GLYPH() {
        const WF_AGENT_GLYPH = { running: '▶', done: '✓', failed: '✕', interrupted: '⚠' };
        return WF_AGENT_GLYPH;
      },
      _firstSeenSessions() {
        const _firstSeenSessions = new Map();
        return _firstSeenSessions;
      },
      _convInitialLoadDone() {
        const _convInitialLoadDone = false;
        return _convInitialLoadDone;
      },
      _stickyInitialCol() {
        const _stickyInitialCol = new Map();
        return _stickyInitialCol;
      },
      _popoutSelectInFlight() {
        const _popoutSelectInFlight = false;
        return _popoutSelectInFlight;
      },
      _popoutMissingShown() {
        const _popoutMissingShown = false;
        return _popoutMissingShown;
      },
      _appEmbedReadySent() {
        const _appEmbedReadySent = false;
        return _appEmbedReadySent;
      },
      _optimisticOverrides() {
        const _optimisticOverrides = new Map();
        return _optimisticOverrides;
      },
      _OPTIMISTIC_TTL_MS() {
        const _OPTIMISTIC_TTL_MS = 30000;
        return _OPTIMISTIC_TTL_MS;
      },
      pendingSpawns() {
        const pendingSpawns = new Map();
        return pendingSpawns;
      },
      claudeSpawnAwaitingFirstPaint() {
        const claudeSpawnAwaitingFirstPaint = new Set();
        return claudeSpawnAwaitingFirstPaint;
      },
      codexSpawnPreviews() {
        const codexSpawnPreviews = new Map();
        return codexSpawnPreviews;
      },
      _resolvedPlaceholderTargets() {
        const _resolvedPlaceholderTargets = new Map();
        return _resolvedPlaceholderTargets;
      },
      newSessionComposerRevision() {
        const newSessionComposerRevision = 0;
        return newSessionComposerRevision;
      },
      _externalSpawnSeen() {
        const _externalSpawnSeen = new Map();
        return _externalSpawnSeen;
      },
      EXTERNAL_SPAWN_MAX_AGE_S() {
        const EXTERNAL_SPAWN_MAX_AGE_S = 120;
        return EXTERNAL_SPAWN_MAX_AGE_S;
      },
      EXTERNAL_SPAWN_MAX_PER_TICK() {
        const EXTERNAL_SPAWN_MAX_PER_TICK = 4;
        return EXTERNAL_SPAWN_MAX_PER_TICK;
      },
      EXTERNAL_SPAWN_SEEN_TTL_MS() {
        const EXTERNAL_SPAWN_SEEN_TTL_MS = 30 * 60 * 1000;
        return EXTERNAL_SPAWN_SEEN_TTL_MS;
      },
      _firstSessionsResolve() {
        const _firstSessionsResolve = undefined;
        return _firstSessionsResolve;
      },
      _nyaShowAll() {
        const _nyaShowAll = false;
        return _nyaShowAll;
      },
      _NYA_POLL_MIN_MS() {
        const _NYA_POLL_MIN_MS = 15000;
        return _NYA_POLL_MIN_MS;
      },
      _NYA_POLL_MAX_MS() {
        const _NYA_POLL_MAX_MS = 120000;
        return _NYA_POLL_MAX_MS;
      },
      _nyaPollGapMs() {
        const _nyaPollGapMs = sharedState._NYA_POLL_MIN_MS;
        return _nyaPollGapMs;
      },
      _nyaNextFetchAt() {
        const _nyaNextFetchAt = 0;
        return _nyaNextFetchAt;
      },
      _nyaLastSig() {
        const _nyaLastSig = null;
        return _nyaLastSig;
      },
      _WA_CATEGORIES() {
        const _WA_CATEGORIES = [
    { e: '💰', label: 'Money', bg: ['#3fae6a', '#24804a'],
      re: /\b(pric\w*|stripe|payments?|billing|bill|invoices?|charge|refund|reimburs\w*|fsa|payroll|cost|spend|tokens?|usage|budget|quota|revenue|\$\d+)/ },
    { e: '📊', label: 'Data & evals', bg: ['#2fa7b8', '#1f7c8c'],
      re: /\b(evals?|replay|benchmark|grad(e|ing)|scorecard|analytics|metrics?|report|dashboard|investigat\w*|research|audit|analy[sz]\w*)\b/ },
    { e: '📣', label: 'Customers & growth', bg: ['#e0894a', '#b9652c'],
      re: /\b(lead|leads|outreach|marketing|seo|growth|campaign|customers?|clients?|studios?|email|gmail|inbox|sms|twilio|whatsapp|calendar|booking|bookings|classes|onboarding)\b/ },
    { e: '🎨', label: 'Product & UI', bg: ['#8a6be0', '#6649b8'],
      re: /\b(ui|ux|sidebar|tabs?|avatars?|layout|css|button|screen|page|view|mobile|phone|iphone|ios|voice|tts|design|feature|menu|rows?)\b/ },
    { e: '🛠️', label: 'Build & ops', bg: ['#5a7dbf', '#3d5c99'],
      re: /\b(ci|pytest|tests?|flaky|release|deploy\w*|ship|publish|git|github|commit|push|merge|branch|pr|worktree|server|restart|vm|infra|ssh|ops|cron|perf|slow|cpu|memory|database|db|supabase|sql|migration|queues?|tickets?|wt|watchtower|workers?|agents?|claude|codex|model|prompt|auth|login|security|bug|fix|crash)\b/ },
  ];
        return _WA_CATEGORIES;
      },
      _WA_CATEGORY_DEFAULT() {
        const _WA_CATEGORY_DEFAULT = { e: '💭', label: 'General', bg: ['#6b7280', '#4b5260'] };
        return _WA_CATEGORY_DEFAULT;
      },
      _waCategoryCache() {
        const _waCategoryCache = new Map();
        return _waCategoryCache;
      },
      _lastWrittenState() {
        const _lastWrittenState = null;
        return _lastWrittenState;
      },
      _sidebarDragInProgress() {
        const _sidebarDragInProgress = false;
        return _sidebarDragInProgress;
      },
      _sidebarRenderPendingAfterDrag() {
        const _sidebarRenderPendingAfterDrag = false;
        return _sidebarRenderPendingAfterDrag;
      },
      flowNodePositions() {
        const flowNodePositions = {};
        return flowNodePositions;
      },
      flowNodeParents() {
        const flowNodeParents = {};
        return flowNodeParents;
      },
      flowCustomObjects() {
        const flowCustomObjects = [];
        return flowCustomObjects;
      },
      FLOW_DRAFT_DELETED_KEY() {
        const FLOW_DRAFT_DELETED_KEY = 'ccc-flow-draft-deleted-ids';
        return FLOW_DRAFT_DELETED_KEY;
      },
      flowDraftSessions() {
        const flowDraftSessions = [];
        return flowDraftSessions;
      },
      _objectsSyncTimer() {
        const _objectsSyncTimer = null;
        return _objectsSyncTimer;
      },
      _objectsSyncInFlight() {
        const _objectsSyncInFlight = false;
        return _objectsSyncInFlight;
      },
      _objectsSyncQueued() {
        const _objectsSyncQueued = false;
        return _objectsSyncQueued;
      },
      NEW_SESSION_DEFAULT_OBJECT_ID() {
        const NEW_SESSION_DEFAULT_OBJECT_ID = 'new-session-inbox';
        return NEW_SESSION_DEFAULT_OBJECT_ID;
      },
      NEW_SESSION_DEFAULT_OBJECT_TITLE() {
        const NEW_SESSION_DEFAULT_OBJECT_TITLE = 'Inbox';
        return NEW_SESSION_DEFAULT_OBJECT_TITLE;
      },
      NEW_SESSION_PENDING_OBJECT_KEY() {
        const NEW_SESSION_PENDING_OBJECT_KEY = 'ccc-pending-new-session-object-assignments';
        return NEW_SESSION_PENDING_OBJECT_KEY;
      },
      NEW_SESSION_OBJECT_BY_CWD_KEY() {
        const NEW_SESSION_OBJECT_BY_CWD_KEY = 'ccc-new-session-object-by-cwd';
        return NEW_SESSION_OBJECT_BY_CWD_KEY;
      },
      _flowInspectorState() {
        const _flowInspectorState = null;
        return _flowInspectorState;
      },
      _flowInspectorHiddenInputEls() {
        const _flowInspectorHiddenInputEls = [];
        return _flowInspectorHiddenInputEls;
      },
      _sidebarRenderRaf() {
        const _sidebarRenderRaf = 0;
        return _sidebarRenderRaf;
      },
      _sidebarLifecycleRenderPending() {
        const _sidebarLifecycleRenderPending = false;
        return _sidebarLifecycleRenderPending;
      },
      _sidebarRenderPendingWhilePaused() {
        const _sidebarRenderPendingWhilePaused = false;
        return _sidebarRenderPendingWhilePaused;
      },
      _convRowActionHoverUntil() {
        const _convRowActionHoverUntil = 0;
        return _convRowActionHoverUntil;
      },
      _lastPolledSidebarRenderAt() {
        const _lastPolledSidebarRenderAt = 0;
        return _lastPolledSidebarRenderAt;
      },
      columnOverrides() {
        const columnOverrides = {};
        return columnOverrides;
      },
      _archivedBacklogIds() {
        const _archivedBacklogIds = new Set();
        return _archivedBacklogIds;
      },
      _gcReaderInterval() {
        const _gcReaderInterval = null;
        return _gcReaderInterval;
      },
      _gcReaderPath() {
        const _gcReaderPath = null;
        return _gcReaderPath;
      },
      _gcReaderId() {
        const _gcReaderId = null;
        return _gcReaderId;
      },
      _gcReaderTopic() {
        const _gcReaderTopic = '';
        return _gcReaderTopic;
      },
      _gcReaderMode() {
        const _gcReaderMode = 'topic';
        return _gcReaderMode;
      },
      _gcLastMtime() {
        const _gcLastMtime = null;
        return _gcLastMtime;
      },
      _gcUnchangedPolls() {
        const _gcUnchangedPolls = 0;
        return _gcUnchangedPolls;
      },
      _gcReaderGeneration() {
        const _gcReaderGeneration = 0;
        return _gcReaderGeneration;
      },
      _gcPollFailCount() {
        const _gcPollFailCount = 0;
        return _gcPollFailCount;
      },
      _gcLastNudgeTime() {
        const _gcLastNudgeTime = 0;
        return _gcLastNudgeTime;
      },
      _suppressRestoreLastView() {
        const _suppressRestoreLastView = false;
        return _suppressRestoreLastView;
      },
      _gcReaderNameMap() {
        const _gcReaderNameMap = {};
        return _gcReaderNameMap;
      },
      _gcReaderSessionIds() {
        const _gcReaderSessionIds = [];
        return _gcReaderSessionIds;
      },
      _gcAgentFallbackNames() {
        const _gcAgentFallbackNames = {};
        return _gcAgentFallbackNames;
      },
      _gcReplayData() {
        const _gcReplayData = null;
        return _gcReplayData;
      },
      _gcReplayActive() {
        const _gcReplayActive = false;
        return _gcReplayActive;
      },
      _gcReplaySpeed() {
        const _gcReplaySpeed = 1;
        return _gcReplaySpeed;
      },
      _gcReplayPaused() {
        const _gcReplayPaused = false;
        return _gcReplayPaused;
      },
      _gcReplayTimeout() {
        const _gcReplayTimeout = null;
        return _gcReplayTimeout;
      },
      _gcReplayMsgIndex() {
        const _gcReplayMsgIndex = 0;
        return _gcReplayMsgIndex;
      },
      _gcReplayWordIndex() {
        const _gcReplayWordIndex = 0;
        return _gcReplayWordIndex;
      },
      _gcReplayMessages() {
        const _gcReplayMessages = [];
        return _gcReplayMessages;
      },
      _gcReplayAllNonSys() {
        const _gcReplayAllNonSys = [];
        return _gcReplayAllNonSys;
      },
      _gcReplaySpeakersAfter() {
        const _gcReplaySpeakersAfter = [];
        return _gcReplaySpeakersAfter;
      },
      _onReplayKeyDownRef() {
        const _onReplayKeyDownRef = null;
        return _onReplayKeyDownRef;
      },
      _gcReplayHero() {
        const _gcReplayHero = null;
        return _gcReplayHero;
      },
      _gcReplayHumanTypeIndex() {
        const _gcReplayHumanTypeIndex = 0;
        return _gcReplayHumanTypeIndex;
      },
      _gcReplayHumanSendPending() {
        const _gcReplayHumanSendPending = false;
        return _gcReplayHumanSendPending;
      },
      _gcReplayHumanSent() {
        const _gcReplayHumanSent = false;
        return _gcReplayHumanSent;
      },
      _GC_QUEUE_EVENT_META() {
        const _GC_QUEUE_EVENT_META = {
    created:  { icon: '▲', verb: 'appeared in queue' },
    claimed:  { icon: '◆', verb: 'claimed' },
    resolved: { icon: '✓', verb: 'resolved' },
  };
        return _GC_QUEUE_EVENT_META;
      },
      _convReplayActive() {
        const _convReplayActive = false;
        return _convReplayActive;
      },
      _convReplayPaneId() {
        const _convReplayPaneId = null;
        return _convReplayPaneId;
      },
      _convReplayPaneIds() {
        const _convReplayPaneIds = [];
        return _convReplayPaneIds;
      },
      _convReplayPaused() {
        const _convReplayPaused = false;
        return _convReplayPaused;
      },
      _convReplaySpeed() {
        const _convReplaySpeed = 1;
        return _convReplaySpeed;
      },
      _convReplayMsgIndex() {
        const _convReplayMsgIndex = 0;
        return _convReplayMsgIndex;
      },
      _convReplayEvents() {
        const _convReplayEvents = [];
        return _convReplayEvents;
      },
      _convReplaySeenEls() {
        const _convReplaySeenEls = null;
        return _convReplaySeenEls;
      },
      _convReplayTimeout() {
        const _convReplayTimeout = null;
        return _convReplayTimeout;
      },
      _onConvReplayKeyRef() {
        const _onConvReplayKeyRef = null;
        return _onConvReplayKeyRef;
      },
      _onConvReplayInputRef() {
        const _onConvReplayInputRef = null;
        return _onConvReplayInputRef;
      },
      _convReplayWordItem() {
        const _convReplayWordItem = null;
        return _convReplayWordItem;
      },
      _convReplayWordContainer() {
        const _convReplayWordContainer = null;
        return _convReplayWordContainer;
      },
      _convReplayWordIdx() {
        const _convReplayWordIdx = 0;
        return _convReplayWordIdx;
      },
      _convReplayHumanTypeIndex() {
        const _convReplayHumanTypeIndex = 0;
        return _convReplayHumanTypeIndex;
      },
      _convReplayHumanSendPending() {
        const _convReplayHumanSendPending = false;
        return _convReplayHumanSendPending;
      },
      _convReplayHumanSent() {
        const _convReplayHumanSent = false;
        return _convReplayHumanSent;
      },
      _convReplayTypedByPane() {
        const _convReplayTypedByPane = {};
        return _convReplayTypedByPane;
      },
      _convReplayRunId() {
        const _convReplayRunId = 0;
        return _convReplayRunId;
      },
      _convReplayTypedLoading() {
        const _convReplayTypedLoading = false;
        return _convReplayTypedLoading;
      },
      CONV_LIVE_REVEAL_BOTTOM_EPSILON() {
        const CONV_LIVE_REVEAL_BOTTOM_EPSILON = 2;
        return CONV_LIVE_REVEAL_BOTTOM_EPSILON;
      },
      _gcMentionMenuEl() {
        const _gcMentionMenuEl = null;
        return _gcMentionMenuEl;
      },
      _gcMentionItems() {
        const _gcMentionItems = [];
        return _gcMentionItems;
      },
      _gcMentionIndex() {
        const _gcMentionIndex = 0;
        return _gcMentionIndex;
      },
      _gcMentionInput() {
        const _gcMentionInput = null;
        return _gcMentionInput;
      },
      _gcMentionTokenStart() {
        const _gcMentionTokenStart = -1;
        return _gcMentionTokenStart;
      },
      _gcReaderHiddenInputBar() {
        const _gcReaderHiddenInputBar = false;
        return _gcReaderHiddenInputBar;
      },
      _gcReaderHiddenRailItems() {
        const _gcReaderHiddenRailItems = false;
        return _gcReaderHiddenRailItems;
      },
      _FFV_ICON() {
        const _FFV_ICON = { images: '\uD83D\uDDBC', videos: '\uD83C\uDFAC', pdfs: '\uD83D\uDCC4', html: '\uD83C\uDF10', markdown: '\uD83D\uDCDD' };
        return _FFV_ICON;
      },
      COMPACT_UNSUPPORTED_SOURCES() {
        const COMPACT_UNSUPPORTED_SOURCES = new Set(['cursor', 'gemini', 'antigravity', 'pkood', 'kimi']);
        return COMPACT_UNSUPPORTED_SOURCES;
      },
      NON_CLAUDE_SOURCES() {
        const NON_CLAUDE_SOURCES = new Set(['codex', 'gemini', 'cursor', 'antigravity', 'pkood', 'kimi', 'grok']);
        return NON_CLAUDE_SOURCES;
      },
      _convListRenderSig() {
        const _convListRenderSig = null;
        return _convListRenderSig;
      },
      _WT_LAUNCH_PROMPT_RE() {
        const _WT_LAUNCH_PROMPT_RE = /^(?:drain the [a-z0-9_]+(?:-[a-z0-9_]+)* watchtower queue\b|fix ticket \S+ on the \S+ watchtower queue\b|you are the planner for watchtower ticket |you are an independent (?:plan reviewer|verifier) for watchtower ticket |you are the independent post-fix assessor for the bug ticket |you are the bounded senior reviewer for )/;
        return _WT_LAUNCH_PROMPT_RE;
      },
      dragSourceId() {
        const dragSourceId = null;
        return dragSourceId;
      },
      convSortMode() {
        const convSortMode = 'recent';
        return convSortMode;
      },
      convAlphaSort() {
        const convAlphaSort = localStorage.getItem('ccc-conv-alpha-sort') === '1';
        return convAlphaSort;
      },
      recencyFilter() {
        const recencyFilter = (function () {
    const raw = localStorage.getItem('ccc-show-recent') || '';
    return (0, hostServices.normalizeRecencyFilter)(raw);
  })();
        return recencyFilter;
      },
      RECENCY_WINDOWS() {
        const RECENCY_WINDOWS = { '1d': 24 * 3600, '7d': 7 * 24 * 3600 };
        return RECENCY_WINDOWS;
      },
      showRecentOnly() {
        const showRecentOnly = !!sharedState.recencyFilter;
        return showRecentOnly;
      },
      _ROW_ORDER_HYSTERESIS_S() {
        const _ROW_ORDER_HYSTERESIS_S = 5 * 60;
        return _ROW_ORDER_HYSTERESIS_S;
      },
      _ROW_ORDER_KEY() {
        const _ROW_ORDER_KEY = 'ccc-row-stable-order';
        return _ROW_ORDER_KEY;
      },
      _statusRailActiveRow() {
        const _statusRailActiveRow = null;
        return _statusRailActiveRow;
      },
      _convScrollByConv() {
        const _convScrollByConv = Object.create(null);
        return _convScrollByConv;
      },
      CONV_USER_MESSAGE_SELECTOR() {
        const CONV_USER_MESSAGE_SELECTOR = '.event.user_text:not(.task-notification-event):not(.peer-message):not(.skill-loaded-event):not(.compact-resume-event):not(.session-interrupted-event):not([data-live-key])';
        return CONV_USER_MESSAGE_SELECTOR;
      },
      SPEAK_BTN_POS_KEY() {
        const SPEAK_BTN_POS_KEY = 'ccc-speak-btn-pos';
        return SPEAK_BTN_POS_KEY;
      },
      INCOMPLETE_QUIET_MS() {
        const INCOMPLETE_QUIET_MS = 90000;
        return INCOMPLETE_QUIET_MS;
      },
      SILENT_INVESTIGATE_MS() {
        const SILENT_INVESTIGATE_MS = 300000;
        return SILENT_INVESTIGATE_MS;
      },
      silentNudgeAttempts() {
        const silentNudgeAttempts = new Map();
        return silentNudgeAttempts;
      },
      TAPBACK_KEY() {
        const TAPBACK_KEY = 'ccc-tapbacks-pending';
        return TAPBACK_KEY;
      },
      TAPBACK_EMOJI() {
        const TAPBACK_EMOJI = {up: '👍', down: '👎'};
        return TAPBACK_EMOJI;
      },
      TAPBACK_LABEL() {
        const TAPBACK_LABEL = {up: 'Thumbs up', down: 'Thumbs down'};
        return TAPBACK_LABEL;
      },
      _TEXT_FOCUS_SEL() {
        const _TEXT_FOCUS_SEL = 'textarea, input[type="text"], input[type="search"], input[type="email"], input[type="url"], input[type="tel"], input[type="password"], input:not([type])';
        return _TEXT_FOCUS_SEL;
      },
      _bodyHasLive() {
        const _bodyHasLive = {};
        return _bodyHasLive;
      },
      _BODY_HAS_FLAGS() {
        const _BODY_HAS_FLAGS = [
    ['has-f2c-config', () => (0, hostServices._bodyHasAny)('f2c-config', () => true)],
    ['has-img-lightbox-open', () => (0, hostServices._bodyHasAny)('img-lightbox', (el) => el.classList.contains('open'))],
    ['has-settings-modal-open', () => !!document.getElementById('settingsModal')?.classList.contains('open')],
    ['has-start-view', () => !!document.getElementById('cccStartView')],
    ['has-upd-overlay-open', () => (0, hostServices._bodyHasAny)('upd-overlay', (el) => el.classList.contains('open'))],
    ['has-mobile-nav-menu-open', () => (0, hostServices._bodyHasAny)('mobile-nav-menu',
      (el) => !el.hidden && !!el.closest('#mobileBottomNav'))],
    ['has-new-session-composer', () => (0, hostServices._bodyHasAny)('conv-input-context',
      (el) => el.classList.contains('is-new-session'))],
  ];
        return _BODY_HAS_FLAGS;
      },
      _BODY_HAS_ATTR_TARGET() {
        const _BODY_HAS_ATTR_TARGET = /(^|\s)(f2c-config|img-lightbox|upd-overlay|mobile-nav-menu|conv-input-context)(\s|$)/;
        return _BODY_HAS_ATTR_TARGET;
      },
      _convToastTimer() {
        const _convToastTimer = null;
        return _convToastTimer;
      },
      conversationPaneLoading() {
        const conversationPaneLoading = false;
        return conversationPaneLoading;
      },
      conversationPaneLoadToken() {
        const conversationPaneLoadToken = 0;
        return conversationPaneLoadToken;
      },
      _sessionStripCache() {
        const _sessionStripCache = new Map();
        return _sessionStripCache;
      },
      spawnEventSource() {
        const spawnEventSource = null;
        return spawnEventSource;
      },
      _streamingBubble() {
        const _streamingBubble = null;
        return _streamingBubble;
      },
      _streamingMsgId() {
        const _streamingMsgId = null;
        return _streamingMsgId;
      },
      _spawnLiveSid() {
        const _spawnLiveSid = null;
        return _spawnLiveSid;
      },
      _ffcCache() {
        const _ffcCache = new Map();
        return _ffcCache;
      },
      _ffcInFlight() {
        const _ffcInFlight = new Map();
        return _ffcInFlight;
      },
      _sharedQueuePanelHost() {
        const _sharedQueuePanelHost = (() => {
    try { return window.CCCNavigation.getTab() === 'queues' ? 'sidebar' : 'rail'; }
    catch (_) { return 'rail'; }
  })();
        return _sharedQueuePanelHost;
      },
      _uxqItemsCache() {
        const _uxqItemsCache = { ts: 0, items: [], syncedAt: 0 };
        return _uxqItemsCache;
      },
      _uxqItemsVersion() {
        const _uxqItemsVersion = 0;
        return _uxqItemsVersion;
      },
      _uxqRunBusyRefs() {
        const _uxqRunBusyRefs = new Set();
        return _uxqRunBusyRefs;
      },
      _ffcLastSidebarData() {
        const _ffcLastSidebarData = null;
        return _ffcLastSidebarData;
      },
      _queueListMirror() {
        const _queueListMirror = { version: '', order: null, items: null };
        return _queueListMirror;
      },
      _queueListMirrorPromise() {
        const _queueListMirrorPromise = null;
        return _queueListMirrorPromise;
      },
      _uxqHealthCache() {
        const _uxqHealthCache = { ts: 0, rows: [], wt_workers: [], queues: [], worker_session_ids: [], worker_session_map: {}, past_workers: [], github_sync: null };
        return _uxqHealthCache;
      },
      _sysServicesCache() {
        const _sysServicesCache = { ts: 0, payload: null };
        return _sysServicesCache;
      },
      _sysServicesPromise() {
        const _sysServicesPromise = null;
        return _sysServicesPromise;
      },
      _sysServicesFailures() {
        const _sysServicesFailures = 0;
        return _sysServicesFailures;
      },
      _SYS_SERVICES_DEDUPE_MS() {
        const _SYS_SERVICES_DEDUPE_MS = 3000;
        return _SYS_SERVICES_DEDUPE_MS;
      },
      _wtLogTimer() {
        const _wtLogTimer = null;
        return _wtLogTimer;
      },
      _UXQ_PROJECT_ALIASES() {
        const _UXQ_PROJECT_ALIASES = { CC: 'CCC' };
        return _UXQ_PROJECT_ALIASES;
      },
      _UXQ_SCOPE_LS() {
        const _UXQ_SCOPE_LS = 'ccc-uxq-scope';
        return _UXQ_SCOPE_LS;
      },
      _UXQ_SELECTED_SCOPE_LS() {
        const _UXQ_SELECTED_SCOPE_LS = 'ccc-uxq-selected-scope';
        return _UXQ_SELECTED_SCOPE_LS;
      },
      _uxqHistoryPage() {
        const _uxqHistoryPage = 0;
        return _uxqHistoryPage;
      },
      _workersFallbackTickets() {
        const _workersFallbackTickets = [];
        return _workersFallbackTickets;
      },
      _workersTicketSessions() {
        const _workersTicketSessions = new Map();
        return _workersTicketSessions;
      },
      _workersAnswersPending() {
        const _workersAnswersPending = new Set();
        return _workersAnswersPending;
      },
      _uxqNoteModalEl() {
        const _uxqNoteModalEl = null;
        return _uxqNoteModalEl;
      },
      _uxqNoteModalKeydownHandler() {
        const _uxqNoteModalKeydownHandler = null;
        return _uxqNoteModalKeydownHandler;
      },
      UXQ_TD_ZOOM_KEY() {
        const UXQ_TD_ZOOM_KEY = 'ccc-ticket-font-zoom';
        return UXQ_TD_ZOOM_KEY;
      },
      UXQ_TD_ZOOM_MIN() {
        const UXQ_TD_ZOOM_MIN = 0.7;
        return UXQ_TD_ZOOM_MIN;
      },
      UXQ_TD_ZOOM_MAX() {
        const UXQ_TD_ZOOM_MAX = 1.6;
        return UXQ_TD_ZOOM_MAX;
      },
      UXQ_TD_ZOOM_STEP() {
        const UXQ_TD_ZOOM_STEP = 0.1;
        return UXQ_TD_ZOOM_STEP;
      },
      _uxqAnswerPending() {
        const _uxqAnswerPending = new Map();
        return _uxqAnswerPending;
      },
      _uxqAnswerDrafts() {
        const _uxqAnswerDrafts = new Map();
        return _uxqAnswerDrafts;
      },
      UXQ_ANSWER_DRAFT_PREFIX() {
        const UXQ_ANSWER_DRAFT_PREFIX = 'ccc-ticket-answer:';
        return UXQ_ANSWER_DRAFT_PREFIX;
      },
      _UXQ_SESSION_CREATED_QUEUE_LS() {
        const _UXQ_SESSION_CREATED_QUEUE_LS = 'ccc-uxq-session-created-queue';
        return _UXQ_SESSION_CREATED_QUEUE_LS;
      },
      _UXQ_NAME_STOPWORDS() {
        const _UXQ_NAME_STOPWORDS = new Set(('a an the and or of to for in on at with from by is it this that '
    + 'my our please can you we i let lets fix add make into about new session').split(' '));
        return _UXQ_NAME_STOPWORDS;
      },
      FFC_CATEGORY_ORDER() {
        const FFC_CATEGORY_ORDER = [
    {key: 'images',        label: 'Images',        icon: '📷'},
    {key: 'pdfs',          label: 'PDFs',          icon: '📕'},
    {key: 'docs',          label: 'Docs',          icon: '📄'},
    {key: 'presentations', label: 'Presentations', icon: '📊'},
    {key: 'videos',        label: 'Videos',        icon: '🎬'},
    {key: 'markdown',      label: 'Markdown',      icon: '📝'},
    {key: 'html',          label: 'HTML',          icon: '🌐'},
  ];
        return FFC_CATEGORY_ORDER;
      },
      _streamingBubbleLingerTimer() {
        const _streamingBubbleLingerTimer = null;
        return _streamingBubbleLingerTimer;
      },
      _staleBubbleClearTimer() {
        const _staleBubbleClearTimer = null;
        return _staleBubbleClearTimer;
      },
      SUBAGENT_TAB_AUTOCLOSE_MS() {
        const SUBAGENT_TAB_AUTOCLOSE_MS = 30 * 1000;
        return SUBAGENT_TAB_AUTOCLOSE_MS;
      },
      STALE_BUBBLE_CLEAR_MS() {
        const STALE_BUBBLE_CLEAR_MS = 8000;
        return STALE_BUBBLE_CLEAR_MS;
      },
      MAX_STREAM_HANDOFF_MARKERS() {
        const MAX_STREAM_HANDOFF_MARKERS = 256;
        return MAX_STREAM_HANDOFF_MARKERS;
      },
      _scheduledStreamTextEls() {
        const _scheduledStreamTextEls = new Set();
        return _scheduledStreamTextEls;
      },
      _ENGINE_SVG_KEYS() {
        const _ENGINE_SVG_KEYS = ['claude', 'codex', 'hermes', 'cursor', 'kimi', 'copilot', 'grok', 'copilotchat', 'devin', 'opencode'];
        return _ENGINE_SVG_KEYS;
      },
      _engineSvgSpriteReady() {
        const _engineSvgSpriteReady = false;
        return _engineSvgSpriteReady;
      },
      _ENGINE_SVG_SPRITE_ID() {
        const _ENGINE_SVG_SPRITE_ID = 'cccEngineSvgSprite';
        return _ENGINE_SVG_SPRITE_ID;
      },
      _convTailPrefetches() {
        const _convTailPrefetches = new Map();
        return _convTailPrefetches;
      },
      _convScopeCtrl() {
        const _convScopeCtrl = null;
        return _convScopeCtrl;
      },
      CONV_TAIL_PREFETCH_MAX() {
        const CONV_TAIL_PREFETCH_MAX = 4;
        return CONV_TAIL_PREFETCH_MAX;
      },
      CONV_TAIL_PREFETCH_TTL_MS() {
        const CONV_TAIL_PREFETCH_TTL_MS = 30000;
        return CONV_TAIL_PREFETCH_TTL_MS;
      },
      CONV_CACHE_DB() {
        const CONV_CACHE_DB = 'ccc-conv-cache';
        return CONV_CACHE_DB;
      },
      CONV_CACHE_MAX_ENTRIES() {
        const CONV_CACHE_MAX_ENTRIES = 60;
        return CONV_CACHE_MAX_ENTRIES;
      },
      CONV_CACHE_MAX_BYTES() {
        const CONV_CACHE_MAX_BYTES = 1500000;
        return CONV_CACHE_MAX_BYTES;
      },
      CONV_CACHE_READ_BUDGET_MS() {
        const CONV_CACHE_READ_BUDGET_MS = 250;
        return CONV_CACHE_READ_BUDGET_MS;
      },
      CONV_CACHE_PAINT_HOLD_MS() {
        const CONV_CACHE_PAINT_HOLD_MS = 150;
        return CONV_CACHE_PAINT_HOLD_MS;
      },
      _convCacheDbP() {
        const _convCacheDbP = null;
        return _convCacheDbP;
      },
      CONV_TAIL_LINES() {
        const CONV_TAIL_LINES = 400;
        return CONV_TAIL_LINES;
      },
      CONV_TAIL_FIRST_LINES() {
        const CONV_TAIL_FIRST_LINES = 120;
        return CONV_TAIL_FIRST_LINES;
      },
      CONV_TAIL_LINES_MOBILE() {
        const CONV_TAIL_LINES_MOBILE = 120;
        return CONV_TAIL_LINES_MOBILE;
      },
      spawnStatsT0() {
        const spawnStatsT0 = new Map();
        return spawnStatsT0;
      },
      spawnStatsLocalMarks() {
        const spawnStatsLocalMarks = new Map();
        return spawnStatsLocalMarks;
      },
      spawnStatsSent() {
        const spawnStatsSent = new Map();
        return spawnStatsSent;
      },
      SPAWN_STAT_ROWS() {
        const SPAWN_STAT_ROWS = [
    ['thread_start_done',        'Codex created the thread'],
    ['turn_accepted',            'Turn accepted by Codex'],
    ['codex_first_notification', 'Codex emitted its first event'],
    ['codex_first_reasoning',    'Codex began streaming reasoning'],
    ['codex_first_text_delta',   'Codex emitted its first text'],
    ['client_first_visible_output', 'First assistant text painted'],
    ['spawn_response_sent',      'CCC answered the browser'],
    ['client_response_received', 'Browser got the response'],
    ['rollout_file_created',     'Transcript file created'],
    ['rollout_first_bytes',      'Transcript first bytes'],
    ['client_row_rendered',      'Row rendered in sidebar'],
    ['client_row_resolved',      'Row left "spawning"'],
    ['rollout_has_user_message', 'Your prompt durable on disk'],
    ['client_first_paint',       'Transcript first painted'],
    ['confirm_finished',         'Durability check done'],
    ['name_set_finished',        'Thread named'],
    ['finalize_done',            'Background finalize done'],
  ];
        return SPAWN_STAT_ROWS;
      },
      CLAUDE_SPAWN_STAT_ROWS() {
        const CLAUDE_SPAWN_STAT_ROWS = [
    ['process_started',             'Claude process started'],
    ['prewarm_claimed',             'Warm Claude process claimed'],
    ['initial_prompt_written',      'Prompt written to Claude'],
    ['spawn_response_sent',         'CCC answered the browser'],
    ['client_response_received',    'Browser got the response'],
    ['claude_system_init',          'Claude initialized the session'],
    ['claude_first_stream_event',   'Claude emitted its first stream event'],
    ['claude_first_text_delta',     'Claude emitted its first text'],
    ['client_first_visible_output', 'First assistant text painted'],
    ['claude_transcript_created',   'Claude transcript created'],
    ['client_row_rendered',         'Row rendered in sidebar'],
    ['client_row_resolved',         'Row left "spawning"'],
    ['client_first_paint',          'Canonical transcript painted'],
  ];
        return CLAUDE_SPAWN_STAT_ROWS;
      },
      _dynamicAskState() {
        const _dynamicAskState = null;
        return _dynamicAskState;
      },
      _normSend() {
        const _normSend = (s) => (0, sharedState.eventTextString)(s).replace(/\s+/g, ' ').trim();
        return _normSend;
      },
      _workspaceSessionIdByPane() {
        const _workspaceSessionIdByPane = {};
        return _workspaceSessionIdByPane;
      },
      _workspaceDataByPane() {
        const _workspaceDataByPane = {};
        return _workspaceDataByPane;
      },
      _inputContextFitRaf() {
        const _inputContextFitRaf = 0;
        return _inputContextFitRaf;
      },
      _codexDiagSessionIdByPane() {
        const _codexDiagSessionIdByPane = Object.create(null);
        return _codexDiagSessionIdByPane;
      },
      _codexDiagTimerByPane() {
        const _codexDiagTimerByPane = Object.create(null);
        return _codexDiagTimerByPane;
      },
      _usageSessionIdByPane() {
        const _usageSessionIdByPane = {};
        return _usageSessionIdByPane;
      },
      _usageDataByPane() {
        const _usageDataByPane = {};
        return _usageDataByPane;
      },
      _usageRequestByPane() {
        const _usageRequestByPane = {};
        return _usageRequestByPane;
      },
      _usageRequestGenerationByPane() {
        const _usageRequestGenerationByPane = {};
        return _usageRequestGenerationByPane;
      },
      _usageLastFetchByPane() {
        const _usageLastFetchByPane = {};
        return _usageLastFetchByPane;
      },
      SESSION_USAGE_REFRESH_MIN_MS() {
        const SESSION_USAGE_REFRESH_MIN_MS = 5000;
        return SESSION_USAGE_REFRESH_MIN_MS;
      },
      _deferredStreamBatches() {
        const _deferredStreamBatches = [];
        return _deferredStreamBatches;
      },
      _deferredUsageSid() {
        const _deferredUsageSid = null;
        return _deferredUsageSid;
      },
      _deferredUsagePaneId() {
        const _deferredUsagePaneId = '';
        return _deferredUsagePaneId;
      },
      _freeServedBySid() {
        const _freeServedBySid = {};
        return _freeServedBySid;
      },
      CCC_MONTHLY_SUBSCRIPTION_PRICE_KEY() {
        const CCC_MONTHLY_SUBSCRIPTION_PRICE_KEY = 'ccc-monthly-claude-plan-usd';
        return CCC_MONTHLY_SUBSCRIPTION_PRICE_KEY;
      },
      CCC_MONTHLY_CODEX_PLAN_PRICE_KEY() {
        const CCC_MONTHLY_CODEX_PLAN_PRICE_KEY = 'ccc-monthly-codex-plan-usd';
        return CCC_MONTHLY_CODEX_PLAN_PRICE_KEY;
      },
      RAIL_TURN_GRAPH_MAX() {
        const RAIL_TURN_GRAPH_MAX = 30;
        return RAIL_TURN_GRAPH_MAX;
      },
      QUOTA_COST_CALIBRATION_TTL_MS() {
        const QUOTA_COST_CALIBRATION_TTL_MS = 60_000;
        return QUOTA_COST_CALIBRATION_TTL_MS;
      },
      _quotaCostCalibration() {
        const _quotaCostCalibration = null;
        return _quotaCostCalibration;
      },
      _quotaCostCalibrationRequest() {
        const _quotaCostCalibrationRequest = null;
        return _quotaCostCalibrationRequest;
      },
      _quotaCostCalibrationLoadedAt() {
        const _quotaCostCalibrationLoadedAt = 0;
        return _quotaCostCalibrationLoadedAt;
      },
      _cacheMissPrevTs() {
        const _cacheMissPrevTs = new Map();
        return _cacheMissPrevTs;
      },
      MODEL_OPTIONS_BY_ENGINE() {
        const MODEL_OPTIONS_BY_ENGINE = {
    claude: [
      { id: 'fable-5',   label: 'fable-5',   oneM: true },
      { id: 'opus-5',    label: 'opus-5',    oneM: true },
      { id: 'sonnet-5',  label: 'sonnet-5',  oneM: true },
      { id: 'opus-4-8',  label: 'opus-4-8',  oneM: true },
      { id: 'haiku-4-5', label: 'haiku-4-5', oneM: false },
    ],
    codex: [
      { id: 'gpt-6-astra',          label: '6 Astra' },
      { id: 'gpt-5.5',              label: '5.5' },
      { id: 'gpt-5.6-sol',          label: '5.6 Sol' },
      { id: 'gpt-5.6-terra',        label: '5.6 Terra' },
      { id: 'gpt-5.6-luna',         label: '5.6 Luna' },
      { id: 'gpt-5.4',              label: '5.4' },
      { id: 'gpt-5.4-mini',         label: '5.4 Mini' },
      { id: 'gpt-5.3-codex-spark',  label: '5.3 Codex Spark' },
    ],
    cursor: [
      { id: 'opus-5-5',  label: 'opus-5-5',  oneM: true },
      { id: 'auto',                          label: 'Auto (default)' },
      { id: 'composer-2.5-fast',             label: 'composer-2.5-fast' },
      { id: 'gpt-5.3-codex',                 label: 'gpt-5.3-codex' },
      { id: 'gpt-5.3-codex-high',            label: 'gpt-5.3-codex-high' },
      { id: 'claude-opus-4-8-thinking-high', label: 'claude-opus-4-8-thinking-high' },
      { id: 'composer-2.5',                  label: 'composer-2.5' },
    ],
    gemini: [
      { id: 'gemini-3.5-pro',   label: 'gemini-3.5-pro' },
      { id: 'gemini-3.5-flash', label: 'gemini-3.5-flash' },
      { id: 'gemini-3.1-pro',   label: 'gemini-3.1-pro' },
      { id: 'gemini-2.5-pro',   label: 'gemini-2.5-pro' },
      { id: 'gemini-2.5-flash', label: 'gemini-2.5-flash' },
    ],
    antigravity: [
      { id: 'Gemini 4 Carbon (High)', label: 'Gemini 4 Carbon (High)' },
      { id: 'Gemini 4 Carbon (Medium)', label: 'Gemini 4 Carbon (Medium)' },
      { id: 'Gemini 4 Carbon (Low)', label: 'Gemini 4 Carbon (Low)' },
      { id: 'Gemini 4 Argon (High)', label: 'Gemini 4 Argon (High)' },
      { id: 'Gemini 4 Argon (Medium)', label: 'Gemini 4 Argon (Medium)' },
      { id: 'Gemini 4 Argon (Low)', label: 'Gemini 4 Argon (Low)' },
      { id: 'Gemini 3.8 Flash (High)', label: 'Gemini 3.8 Flash (High)' },
      { id: 'Gemini 3.8 Flash (Medium)', label: 'Gemini 3.8 Flash (Medium)' },
      { id: 'Gemini 3.8 Flash (Low)', label: 'Gemini 3.8 Flash (Low)' },
      { id: 'Gemini 3.1 Pro (High)', label: 'Gemini 3.1 Pro (High)' },
      { id: 'Gemini 3.1 Pro (Low)', label: 'Gemini 3.1 Pro (Low)' },
      { id: 'Claude Sonnet 4.6 (Thinking)', label: 'Claude Sonnet 4.6 (Thinking)' },
      { id: 'Claude Opus 4.6 (Thinking)', label: 'Claude Opus 4.6 (Thinking)' },
      { id: 'GPT-OSS 120B (Medium)', label: 'GPT-OSS 120B (Medium)' },
    ],
    kilo: [
      { id: 'kilo/stepfun/step-3.8-flash:free', label: 'step-3.8-flash (free)' },
      { id: 'kilo/stepfun/step-3.7-flash:free', label: 'step-3.7-flash (free, default)' },
      { id: 'kilo/anthropic/claude-sonnet-4.8', label: 'claude-sonnet-4.8' },
      { id: 'kilo/anthropic/claude-opus-4.8',   label: 'claude-opus-4.8' },
      { id: 'kilo/anthropic/claude-sonnet-4.6', label: 'claude-sonnet-4.6' },
      { id: 'kilo/openai/gpt-6.0',              label: 'gpt-6.0' },
      { id: 'kilo/openai/gpt-5.5',              label: 'gpt-5.5' },
    ],
    hermes: [
      { id: 'auto',                          label: 'Auto (default)' },
      { id: 'hermes-3-llama-3.1-405b',       label: 'hermes-3-llama-3.1-405b' },
      { id: 'hermes-3-llama-3.1-70b',        label: 'hermes-3-llama-3.1-70b' },
      { id: 'hermes-2-pro-llama-3-8b',       label: 'hermes-2-pro-llama-3-8b' },
    ],
    kimi: [
      { id: 'kimi-code/k3',                          label: 'K3' },
      { id: 'kimi-code/kimi-for-coding',             label: 'K2.8 Preview' },
      { id: 'kimi-code/kimi-for-coding-highspeed',   label: 'K2.7 Coding Highspeed' },
    ],
    opencode: [
      { id: 'openrouter/anthropic/claude-sonnet-4.5', label: 'claude-sonnet-4.5 (default)' },
      { id: 'openrouter/anthropic/claude-opus-4.1',   label: 'claude-opus-4.1' },
      { id: 'openrouter/openai/gpt-5',                label: 'gpt-5' },
    ],
    devin: [
      { id: 'adaptive',          label: 'Adaptive (default)' },
      { id: 'claude-opus-5-5',  label: 'Claude Opus 5.5' },
      { id: 'claude-opus-5',    label: 'Claude Opus 5' },
      { id: 'claude-fable-5',  label: 'Claude Fable 5' },
      { id: 'claude-sonnet-5', label: 'Claude Sonnet 5' },
      { id: 'gpt-5.6-sol',     label: 'GPT-5.6 Sol' },
      { id: 'gpt-5.6-luna',    label: 'GPT-5.6 Luna' },
      { id: 'glm-5.2',         label: 'GLM 5.2' },
      { id: 'kimi-k3',         label: 'Kimi K3' },
      { id: 'swe-1.7',         label: 'SWE-1.7' },
      { id: 'swe-2',           label: 'SWE-2' },
    ],
    grok: [
      { id: 'grok-4.7', label: 'Grok 4.7' },
      { id: 'grok-4.6', label: 'Grok 4.6' },
      { id: 'grok-4.5', label: 'Grok 4.5' },
    ],
    droid: [
      { id: 'claude-opus-5-5',  label: 'Claude Opus 5.5' },
      { id: 'claude-opus-5',    label: 'Claude Opus 5' },
      { id: 'claude-sonnet-5',  label: 'Claude Sonnet 5' },
      { id: 'gpt-5.4',          label: 'GPT-5.4' },
      { id: 'gemini-3.5-flash', label: 'Gemini 3.5 Flash' },
      { id: 'grok-4.7',         label: 'Grok 4.7' },
      { id: 'grok-4.5',         label: 'Grok 4.5' },
      { id: 'glm-5.2',          label: 'GLM 5.2' },
      { id: 'deepseek-v4-pro',  label: 'DeepSeek V4 Pro' },
      { id: 'minimax-m3',       label: 'MiniMax M3' },
      { id: 'kimi-k3',          label: 'Kimi K3' },
    ],
    aider: [],
    pi: [],
  };
        return MODEL_OPTIONS_BY_ENGINE;
      },
      ENGINE_SUPPORTS_CUSTOM_MODEL() {
        const ENGINE_SUPPORTS_CUSTOM_MODEL = {
    claude: true,
    codex: false,
    cursor: true,
    gemini: true,
    antigravity: true,
    kilo: true,
    hermes: true,
    kimi: true,
    opencode: true,
    devin: true,
    droid: true,
    grok: true,
    aider: true,
    pi: true,
  };
        return ENGINE_SUPPORTS_CUSTOM_MODEL;
      },
      CODEX_REASONING_LEVELS() {
        const CODEX_REASONING_LEVELS = [
    { id: 'low',    label: 'Light' },
    { id: 'medium', label: 'Medium' },
    { id: 'high',   label: 'High' },
    { id: 'xhigh',  label: 'Extra High' },
  ];
        return CODEX_REASONING_LEVELS;
      },
      CLAUDE_REASONING_LEVELS() {
        const CLAUDE_REASONING_LEVELS = sharedState.CODEX_REASONING_LEVELS.concat([
    { id: 'max', label: 'Max' },
  ]);
        return CLAUDE_REASONING_LEVELS;
      },
      REASONING_LEVELS_BY_ENGINE() {
        const REASONING_LEVELS_BY_ENGINE = {
    claude: sharedState.CLAUDE_REASONING_LEVELS,
    codex: sharedState.CODEX_REASONING_LEVELS,
    kimi: sharedState.CLAUDE_REASONING_LEVELS,
  };
        return REASONING_LEVELS_BY_ENGINE;
      },
      REASONING_LEVEL_LABELS() {
        const REASONING_LEVEL_LABELS = {
    low: 'Light', medium: 'Medium', high: 'High', xhigh: 'Extra High', max: 'Max',
  };
        return REASONING_LEVEL_LABELS;
      },
      _modelPickerEl() {
        const _modelPickerEl = null;
        return _modelPickerEl;
      },
      _modelPickerCloseHandler() {
        const _modelPickerCloseHandler = null;
        return _modelPickerCloseHandler;
      },
      _planUsagePopEl() {
        const _planUsagePopEl = null;
        return _planUsagePopEl;
      },
      _planUsageCloseHandler() {
        const _planUsageCloseHandler = null;
        return _planUsageCloseHandler;
      },
      _timelineSessionId() {
        const _timelineSessionId = null;
        return _timelineSessionId;
      },
      _timelineData() {
        const _timelineData = null;
        return _timelineData;
      },
      _steerInFlightTexts() {
        const _steerInFlightTexts = new Map();
        return _steerInFlightTexts;
      },
      _queuedSteerErrors() {
        const _queuedSteerErrors = new Map();
        return _queuedSteerErrors;
      },
      PRESENTATION_MODE_BY_SESSION_KEY() {
        const PRESENTATION_MODE_BY_SESSION_KEY = 'ccc-conv-presentation-mode-by-session';
        return PRESENTATION_MODE_BY_SESSION_KEY;
      },
      _mode3BootstrapByAnswer() {
        const _mode3BootstrapByAnswer = new Map();
        return _mode3BootstrapByAnswer;
      },
      _presentationResizeTimer() {
        const _presentationResizeTimer = null;
        return _presentationResizeTimer;
      },
      _KIMI_TOOL_NAME_ALIASES() {
        const _KIMI_TOOL_NAME_ALIASES = {
    multiedit: 'multi_edit', shell: 'bash', run: 'bash', exec: 'bash', execute: 'bash',
    ripgrep: 'grep', rg: 'grep', find: 'glob',
    fetch: 'web_fetch', webfetch: 'web_fetch', url_fetch: 'web_fetch', urlfetch: 'web_fetch',
    list: 'ls', listdir: 'ls', list_dir: 'ls',
    todowrite: 'todo', todo_write: 'todo', todoread: 'todo', todolist: 'todo', todo_list: 'todo',
    agent: 'task', subagent: 'task', websearch: 'search', web_search: 'search',
    exec_command: 'bash', write_stdin: 'bash', apply_patch: 'edit',
    update_plan: 'todo', view_image: 'read', view_file: 'read',
    spawn_agent: 'task', wait_agent: 'task', list_agents: 'task',
    followup_task: 'task', update_goal: 'todo',
  };
        return _KIMI_TOOL_NAME_ALIASES;
      },
      _WEBUI_TOOL_DISPLAY_NAMES() {
        const _WEBUI_TOOL_DISPLAY_NAMES = {
    exec_command: 'Shell', shell: 'Shell', write_stdin: 'Stdin',
    apply_patch: 'Apply patch', update_plan: 'Update plan',
    view_image: 'View image', view_file: 'View file',
    spawn_agent: 'Spawn agent', wait_agent: 'Wait for agent', wait: 'Wait',
    list_agents: 'List agents', followup_task: 'Follow-up task',
    update_goal: 'Update goal', web_search: 'Web search',
  };
        return _WEBUI_TOOL_DISPLAY_NAMES;
      },
      _KIMI_GLYPHS() {
        const _KIMI_GLYPHS = {
    'file-text': (0, hostServices._kimiSvg)('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>'),
    'terminal': (0, hostServices._kimiSvg)('<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>'),
    'pencil': (0, hostServices._kimiSvg)('<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/>'),
    'file-plus': (0, hostServices._kimiSvg)('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><line x1="9" y1="15" x2="15" y2="15"/>'),
    'search': (0, hostServices._kimiSvg)('<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>'),
    'globe': (0, hostServices._kimiSvg)('<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'),
    'folder': (0, hostServices._kimiSvg)('<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'),
    'check-list': (0, hostServices._kimiSvg)('<polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'),
    'sparkles': (0, hostServices._kimiSvg)('<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/>'),
    'tool': (0, hostServices._kimiSvg)('<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>'),
    'list': (0, hostServices._kimiSvg)('<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>'),
    'chevron-right': (0, hostServices._kimiSvg)('<polyline points="9 18 15 12 9 6"/>'),
    'check': (0, hostServices._kimiSvg)('<polyline points="20 6 9 17 4 12"/>'),
    'close': (0, hostServices._kimiSvg)('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'),
  };
        return _KIMI_GLYPHS;
      },
      _KIMI_TOOL_GLYPH() {
        const _KIMI_TOOL_GLYPH = {
    read: 'file-text', bash: 'terminal', edit: 'pencil', multi_edit: 'pencil',
    write: 'file-plus', grep: 'search', search: 'search', glob: 'search',
    ls: 'folder', web_fetch: 'globe', todo: 'check-list', task: 'sparkles',
  };
        return _KIMI_TOOL_GLYPH;
      },
      _activityLogModalEl() {
        const _activityLogModalEl = null;
        return _activityLogModalEl;
      },
      _activityLogModalKeydownHandler() {
        const _activityLogModalKeydownHandler = null;
        return _activityLogModalKeydownHandler;
      },
      _historyState() {
        const _historyState = { query: '', map: new Map(), repoRows: [], matchedRepo: null };
        return _historyState;
      },
      _hiPollTimer() {
        const _hiPollTimer = null;
        return _hiPollTimer;
      },
      _INJECT_SOURCE_LABELS() {
        const _INJECT_SOURCE_LABELS = {
    composer: 'Dashboard composer', api: 'API', wt: 'WatchTower',
    announced_from: 'Another session', ask: 'Ask', 'fleet-ping': 'Fleet ping',
    'archive-bulk': 'Archive bulk send', 'annotate-queue-approved': 'Annotation queue',
  };
        return _INJECT_SOURCE_LABELS;
      },
      _Q_HELD_REASONS() {
        const _Q_HELD_REASONS = {
    orphaned_spawn: 'session is from before the last CCC restart',
    headless_turn: 'session is mid-turn',
    tty_busy: 'terminal session is busy',
    tool_child_blocks_inject: 'session is running a tool',
    bg_not_ready: 'background agent is not ready yet',
    bg_pty_recent_failure: 'last delivery to this background agent failed',
  };
        return _Q_HELD_REASONS;
      },
      RAIL_LOG_POLL_MS() {
        const RAIL_LOG_POLL_MS = 5000;
        return RAIL_LOG_POLL_MS;
      },
      _railLogEvents() {
        const _railLogEvents = [];
        return _railLogEvents;
      },
      _railLogScope() {
        const _railLogScope = 'all';
        return _railLogScope;
      },
      _railLogFilter() {
        const _railLogFilter = '';
        return _railLogFilter;
      },
      _railLogRequest() {
        const _railLogRequest = null;
        return _railLogRequest;
      },
      _railLogContext() {
        const _railLogContext = null;
        return _railLogContext;
      },
      _railLogEmptyMessage() {
        const _railLogEmptyMessage = 'No activity-log entries yet.';
        return _railLogEmptyMessage;
      },
      _railLogLoadError() {
        const _railLogLoadError = false;
        return _railLogLoadError;
      },
      _railLogShowHeartbeats() {
        const _railLogShowHeartbeats = false;
        return _railLogShowHeartbeats;
      },
      _railLogShowInjects() {
        const _railLogShowInjects = true;
        return _railLogShowInjects;
      },
      _railLogOnlyAttention() {
        const _railLogOnlyAttention = false;
        return _railLogOnlyAttention;
      },
      _logSessionIndex() {
        const _logSessionIndex = { src: null, map: new Map() };
        return _logSessionIndex;
      },
      ORCH_EXECUTORS() {
        const ORCH_EXECUTORS = [
    { id: 'gpt-6-astra',   engine: 'codex',       model: 'gpt-6-astra',           label: '6 Astra',        vendor: 'Codex',       family: 'openai' },
    { id: 'sonnet-5',      engine: 'claude',      model: 'sonnet-5',              label: 'Sonnet 5',       vendor: 'Claude',      family: 'anthropic' },
    { id: 'opus-5-5',      engine: 'claude',      model: 'opus-5-5',              label: 'Opus 5.5',       vendor: 'Claude',      family: 'anthropic' },
    { id: 'opus-5',        engine: 'claude',      model: 'opus-5',                label: 'Opus 5',         vendor: 'Claude',      family: 'anthropic' },
    { id: 'gpt-5.6-terra', engine: 'codex',       model: 'gpt-5.6-terra',         label: '5.6 Terra',      vendor: 'Codex',       family: 'openai' },
    { id: 'grok-4.7',      engine: 'grok',        model: 'grok-4.7',              label: 'Grok 4.7',       vendor: 'Grok',        family: 'xai' },
    { id: 'grok-4.6',      engine: 'grok',        model: 'grok-4.6',              label: 'Grok 4.6',       vendor: 'Grok',        family: 'xai' },
    { id: 'glm-5.2',       engine: 'devin',       model: 'glm-5.2',               label: 'GLM 5.2',        vendor: 'Devin',       family: 'zhipu', via: true },
    { id: 'gemini-3.5',    engine: 'antigravity', model: 'Gemini 3.5 Pro (High)', label: 'Gemini 3.5 Pro', vendor: 'Antigravity', family: 'google' },
    { id: 'kimi-k3',       engine: 'kimi',        model: 'kimi-code/k3',          label: 'Kimi K3',        vendor: 'Kimi',        family: 'moonshot' },
  ];
        return ORCH_EXECUTORS;
      },
      ORCH_PLAYBOOKS() {
        const ORCH_PLAYBOOKS = [
    { id: 'delegate', title: 'Delegate',          sub: 'Execution runs in a separate lane.' },
    { id: 'spawn',    title: 'Spawn a sub-agent', sub: 'Pick a model, tap, edit the task in the composer, send.', tier: true, draft: true },
    { id: 'critique', title: 'Critique',          sub: 'Two outside reviewers, blunt.' },
    { id: 'verify',   title: 'Verify',            sub: 'A different model family checks the work.', more: true },
    { id: 'status',   title: 'Check status',      sub: 'Optional. Where does each lane stand?', optional: true, more: true },
  ];
        return ORCH_PLAYBOOKS;
      },
      ORCH_SPAWN_PLACEHOLDER() {
        const ORCH_SPAWN_PLACEHOLDER = '[describe the task]';
        return ORCH_SPAWN_PLACEHOLDER;
      },
      ORCH_ENGINE_GLYPH() {
        const ORCH_ENGINE_GLYPH = {
    claude: 'C', codex: 'X', antigravity: 'A', gemini: 'A', grok: 'G', devin: 'D',
    kimi: 'K', cursor: 'U', hermes: 'H', kilo: 'L', opencode: 'O', copilot: 'P',
  };
        return ORCH_ENGINE_GLYPH;
      },
      _orchSpawnedRegistry() {
        const _orchSpawnedRegistry = [];
        return _orchSpawnedRegistry;
      },
      _orchRegistryFetchedAt() {
        const _orchRegistryFetchedAt = 0;
        return _orchRegistryFetchedAt;
      },
      _orchRegistryInFlight() {
        const _orchRegistryInFlight = false;
        return _orchRegistryInFlight;
      },
      _orchLastSid() {
        const _orchLastSid = '';
        return _orchLastSid;
      },
      _orchLastKeys() {
        const _orchLastKeys = new Set();
        return _orchLastKeys;
      },
      _orchSim() {
        const _orchSim = null;
        return _orchSim;
      },
      _orchSimTimers() {
        const _orchSimTimers = [];
        return _orchSimTimers;
      },
      _orchPollTimer() {
        const _orchPollTimer = null;
        return _orchPollTimer;
      },
      _orchEnginesProbed() {
        const _orchEnginesProbed = false;
        return _orchEnginesProbed;
      },
      ORCH_BOOT_GRACE_S() {
        const ORCH_BOOT_GRACE_S = 45;
        return ORCH_BOOT_GRACE_S;
      },
      ORCH_ONE_SHOT_ENGINES() {
        const ORCH_ONE_SHOT_ENGINES = new Set(['devin', 'cursor', 'gemini', 'grok', 'kilo', 'opencode', 'copilot']);
        return ORCH_ONE_SHOT_ENGINES;
      },
      ORCH_LANE_META_KEY() {
        const ORCH_LANE_META_KEY = 'ccc-orch-lane-meta';
        return ORCH_LANE_META_KEY;
      },
      ORCH_LANE_META_MAX() {
        const ORCH_LANE_META_MAX = 400;
        return ORCH_LANE_META_MAX;
      },
      _orchLaneMeta() {
        const _orchLaneMeta = null;
        return _orchLaneMeta;
      },
      _orchReplaySeen() {
        const _orchReplaySeen = new Map();
        return _orchReplaySeen;
      },
      _orchReplayStageTimer() {
        const _orchReplayStageTimer = null;
        return _orchReplayStageTimer;
      },
      ORCH_REPLAY_STAGE_MS() {
        const ORCH_REPLAY_STAGE_MS = 1500;
        return ORCH_REPLAY_STAGE_MS;
      },
      _orchSpawnIdx() {
        const _orchSpawnIdx = { events: null, at: new Map() };
        return _orchSpawnIdx;
      },
      _orchChildrenSid() {
        const _orchChildrenSid = '';
        return _orchChildrenSid;
      },
      _orchChildren() {
        const _orchChildren = [];
        return _orchChildren;
      },
      _orchFamilyTreeSid() {
        const _orchFamilyTreeSid = '';
        return _orchFamilyTreeSid;
      },
      _orchFamilyTree() {
        const _orchFamilyTree = null;
        return _orchFamilyTree;
      },
      _orchFamilyContinuation() {
        const _orchFamilyContinuation = null;
        return _orchFamilyContinuation;
      },
      _orchFamilyRequestKey() {
        const _orchFamilyRequestKey = '';
        return _orchFamilyRequestKey;
      },
      _orchFamily() {
        const _orchFamily = { root: '', lanes: [], count: 0, details: [] };
        return _orchFamily;
      },
      _orchFamilyRaf() {
        const _orchFamilyRaf = 0;
        return _orchFamilyRaf;
      },
      ASK_HISTORY_KEY() {
        const ASK_HISTORY_KEY = 'ccc-ask-history';
        return ASK_HISTORY_KEY;
      },
      ASK_HISTORY_TURNS() {
        const ASK_HISTORY_TURNS = 4;
        return ASK_HISTORY_TURNS;
      },
      ASK_RANGE_LABELS() {
        const ASK_RANGE_LABELS = { any: 'Any time', '24h': 'Last 24h', '7d': 'Last 7 days', '30d': 'Last 30 days' };
        return ASK_RANGE_LABELS;
      },
      ASK_RANGE_ORDER() {
        const ASK_RANGE_ORDER = ['any', '24h', '7d', '30d'];
        return ASK_RANGE_ORDER;
      },
      ASK_MONTHS() {
        const ASK_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        return ASK_MONTHS;
      },
      ASK_CONFIRM_MARKER_RE() {
        const ASK_CONFIRM_MARKER_RE = /\[\[action:confirm:[0-9A-Za-z_]{4,40}\]\]/g;
        return ASK_CONFIRM_MARKER_RE;
      },
      askWarmUp() {
        const askWarmUp = null;
        return askWarmUp;
      },
      $deployDot() {
        const $deployDot = document.getElementById('deployDot');
        return $deployDot;
      },
      $deployPill() {
        const $deployPill = document.getElementById('deployPill');
        return $deployPill;
      },
      $deployLabel() {
        const $deployLabel = document.getElementById('deployStateLabel');
        return $deployLabel;
      },
      _vercelDeployUrl() {
        const _vercelDeployUrl = '';
        return _vercelDeployUrl;
      },
      _vercelProgressRepo() {
        const _vercelProgressRepo = '';
        return _vercelProgressRepo;
      },
      _vercelProgressRequest() {
        const _vercelProgressRequest = 0;
        return _vercelProgressRequest;
      },
      _shipLogActive() {
        const _shipLogActive = false;
        return _shipLogActive;
      },
      $localhostPill() {
        const $localhostPill = document.getElementById('localhostPill');
        return $localhostPill;
      },
      $localhostDot() {
        const $localhostDot = document.getElementById('localhostDot');
        return $localhostDot;
      },
      $localhostLabel() {
        const $localhostLabel = document.getElementById('localhostLabel');
        return $localhostLabel;
      },
      _localhostState() {
        const _localhostState = 'idle';
        return _localhostState;
      },
      _localhostLastCommand() {
        const _localhostLastCommand = '';
        return _localhostLastCommand;
      },
      _localhostTargetPath() {
        const _localhostTargetPath = '';
        return _localhostTargetPath;
      },
      _localhostCtxKey() {
        const _localhostCtxKey = '';
        return _localhostCtxKey;
      },
      $gcActiveBtn() {
        const $gcActiveBtn = document.getElementById('gcActiveBtn');
        return $gcActiveBtn;
      },
      _activeGroupChatPillEl() {
        const _activeGroupChatPillEl = null;
        return _activeGroupChatPillEl;
      },
      GC_ACTIVE_TRIGGER_RECENT_MS() {
        const GC_ACTIVE_TRIGGER_RECENT_MS = 60 * 1000;
        return GC_ACTIVE_TRIGGER_RECENT_MS;
      },
      GC_ACTIVE_PILL_FRESHNESS_MS() {
        const GC_ACTIVE_PILL_FRESHNESS_MS = 10 * 60 * 1000;
        return GC_ACTIVE_PILL_FRESHNESS_MS;
      },
      _GC_ACTIVE_CACHE_KEY() {
        const _GC_ACTIVE_CACHE_KEY = 'ccc-gc-active-cache';
        return _GC_ACTIVE_CACHE_KEY;
      },
      _gcActiveChats() {
        const _gcActiveChats = (() => {
    try { const v = JSON.parse(localStorage.getItem(sharedState._GC_ACTIVE_CACHE_KEY) || '[]'); return Array.isArray(v) ? v.slice().sort((a, b) => (b.last_mtime || 0) - (a.last_mtime || 0)) : []; }
    catch (_) { return []; }
  })();
        return _gcActiveChats;
      },
      _gcLastLocalTrigger() {
        const _gcLastLocalTrigger = null;
        return _gcLastLocalTrigger;
      },
      _gcActivePollTimer() {
        const _gcActivePollTimer = null;
        return _gcActivePollTimer;
      },
      _gcActivePollPromise() {
        const _gcActivePollPromise = null;
        return _gcActivePollPromise;
      },
      _archivedGroupChats() {
        const _archivedGroupChats = [];
        return _archivedGroupChats;
      },
      archiveData() {
        const archiveData = [];
        return archiveData;
      },
      archiveLoaded() {
        const archiveLoaded = false;
        return archiveLoaded;
      },
      _archiveFromLocalCache() {
        const _archiveFromLocalCache = false;
        return _archiveFromLocalCache;
      },
      _archiveFirstLoadedResolve() {
        const _archiveFirstLoadedResolve = null;
        return _archiveFirstLoadedResolve;
      },
      archiveDataWindow() {
        const archiveDataWindow = null;
        return archiveDataWindow;
      },
      uxFixesQueueMeta() {
        const uxFixesQueueMeta = { total: 0, byClaimedSession: new Map() };
        return uxFixesQueueMeta;
      },
      _uxFixesQueueMetaPromise() {
        const _uxFixesQueueMetaPromise = null;
        return _uxFixesQueueMetaPromise;
      },
      _uxFixesQueueMetaLoadedAt() {
        const _uxFixesQueueMetaLoadedAt = 0;
        return _uxFixesQueueMetaLoadedAt;
      },
      UX_FIXES_QUEUE_META_TTL_MS() {
        const UX_FIXES_QUEUE_META_TTL_MS = 15 * 1000;
        return UX_FIXES_QUEUE_META_TTL_MS;
      },
      _dashboardSavedCursor() {
        const _dashboardSavedCursor = null;
        return _dashboardSavedCursor;
      },
      _dashboardEventState() {
        const _dashboardEventState = {
    bootId: sharedState._dashboardSavedCursor && sharedState._dashboardSavedCursor.boot_id || '',
    seq: Number(sharedState._dashboardSavedCursor && sharedState._dashboardSavedCursor.seq || 0),
    entityVersions: new Map(),
    invalidations: new Map(),
    pendingConversationPatches: new Map(),
    source: null,
    retryMs: 1000,
    retryTimer: null,
  };
        return _dashboardEventState;
      },
      _archiveUnknownPatchIds() {
        const _archiveUnknownPatchIds = new Set();
        return _archiveUnknownPatchIds;
      },
      DASHBOARD_ARCHIVE_INVALIDATION_DEBOUNCE_MS() {
        const DASHBOARD_ARCHIVE_INVALIDATION_DEBOUNCE_MS = 250;
        return DASHBOARD_ARCHIVE_INVALIDATION_DEBOUNCE_MS;
      },
      _dashboardArchiveRefreshTimer() {
        const _dashboardArchiveRefreshTimer = null;
        return _dashboardArchiveRefreshTimer;
      },
      UX_FIXES_ACTIVE_CLAIM_MS() {
        const UX_FIXES_ACTIVE_CLAIM_MS = 30 * 60 * 1000;
        return UX_FIXES_ACTIVE_CLAIM_MS;
      },
      WORKER_HELPER_ROLE_LABELS() {
        const WORKER_HELPER_ROLE_LABELS = {
    verifier: 'Independent verifier', planner: 'Planner',
    plan_reviewer: 'Plan reviewer', assessor: 'Post-fix assessor',
    probe: 'Probe', helper: 'Helper', adhoc: 'Helper',
    critic: 'Critique', monitor: 'Loop watch',
  };
        return WORKER_HELPER_ROLE_LABELS;
      },
      _WK_SELF_TICKET_RE() {
        const _WK_SELF_TICKET_RE = /^\s*(?:\u{1F9F5}\s*)?([A-Z][A-Z0-9_]*(?:-[A-Z0-9_]+)*)#(\d+)\b/u;
        return _WK_SELF_TICKET_RE;
      },
      _WK_TICKET_REF_RE() {
        const _WK_TICKET_REF_RE = /\b([A-Z][A-Z0-9_]*(?:-[A-Z0-9_]+)*)-(\d+)\b/;
        return _WK_TICKET_REF_RE;
      },
      _WK_GENERIC_TITLE_RE() {
        const _WK_GENERIC_TITLE_RE = /^(?:\(untitled\)|(?:[A-Z][A-Z0-9_-]*\s+)?(?:queue\s+)?worker(?:\s*·.*)?|drain the .*? watchtower queue\b.*|you are an independent\b.*|you are the (?:planner|independent post-fix assessor)\b.*|you monitor an ai coding agent\b.*|critique task\b.*|fix ticket \S+ on the \S+ watchtower queue\b.*)$/i;
        return _WK_GENERIC_TITLE_RE;
      },
      _uxFixesChipPaintedSig() {
        const _uxFixesChipPaintedSig = '';
        return _uxFixesChipPaintedSig;
      },
      _recentRenameOverrides() {
        const _recentRenameOverrides = new Map();
        return _recentRenameOverrides;
      },
      RENAME_OVERRIDE_MAX_AGE_MS() {
        const RENAME_OVERRIDE_MAX_AGE_MS = 6 * 60 * 1000;
        return RENAME_OVERRIDE_MAX_AGE_MS;
      },
      ARCHIVE_SIDEBAR_CACHE_KEY() {
        const ARCHIVE_SIDEBAR_CACHE_KEY = 'ccc-sidebar-snapshot-v1';
        return ARCHIVE_SIDEBAR_CACHE_KEY;
      },
      ARCHIVE_SIDEBAR_CACHE_MAX_ROWS() {
        const ARCHIVE_SIDEBAR_CACHE_MAX_ROWS = 500;
        return ARCHIVE_SIDEBAR_CACHE_MAX_ROWS;
      },
      ARCHIVE_SIDEBAR_CACHE_MAX_CHARS() {
        const ARCHIVE_SIDEBAR_CACHE_MAX_CHARS = 256 * 1024;
        return ARCHIVE_SIDEBAR_CACHE_MAX_CHARS;
      },
      ARCHIVE_SIDEBAR_CACHE_FIELDS() {
        const ARCHIVE_SIDEBAR_CACHE_FIELDS = [
    'id', 'session_id', 'source', 'engine', 'source_platform', 'thread_source',
    'folder_label', 'folder_path', 'slug', 'pinned_repo', 'session_cwd',
    'session_cwd_exists', 'session_cwd_is_worktree', 'mtime', 'modified',
    'last_interacted', 'size', 'first_message', 'ai_title', 'display_name',
    'name_overridden', 'auto_titled', 'spawn_named', 'archived', 'trashed', 'verified',
    'pinned', 'pin_rank', 'all_lane_override', 'parent_session_id',
    'continued_from_session_id', 'hermes_parent_session_id', 'hermes_continued_from',
    'hermes_source', 'hermes_profile', 'hermes_chat_type', 'spawned_via',
    'model', 'runtime', 'git_branch', 'effective_branch', 'effective_kind',
    'worktree_label', 'explicitly_archived', 'spawned_kind', 'codex_cloud',
  ];
        return ARCHIVE_SIDEBAR_CACHE_FIELDS;
      },
      _archiveSidebarCachePending() {
        const _archiveSidebarCachePending = null;
        return _archiveSidebarCachePending;
      },
      _archiveSidebarCacheScheduled() {
        const _archiveSidebarCacheScheduled = false;
        return _archiveSidebarCacheScheduled;
      },
      _archiveAllInflight() {
        const _archiveAllInflight = new Map();
        return _archiveAllInflight;
      },
      _archiveAllEtag() {
        const _archiveAllEtag = new Map();
        return _archiveAllEtag;
      },
      _archiveAllData() {
        const _archiveAllData = new Map();
        return _archiveAllData;
      },
      _archiveAllDelta() {
        const _archiveAllDelta = new Map();
        return _archiveAllDelta;
      },
      ARCHIVE_ALL_FETCH_TIMEOUT_MS() {
        const ARCHIVE_ALL_FETCH_TIMEOUT_MS = 45 * 1000;
        return ARCHIVE_ALL_FETCH_TIMEOUT_MS;
      },
      _lastArchiveListHttpStatus() {
        const _lastArchiveListHttpStatus = null;
        return _lastArchiveListHttpStatus;
      },
      crossRepoIssuesData() {
        const crossRepoIssuesData = [];
        return crossRepoIssuesData;
      },
      _archiveMasterRows() {
        const _archiveMasterRows = [];
        return _archiveMasterRows;
      },
      _archiveProgressPollId() {
        const _archiveProgressPollId = null;
        return _archiveProgressPollId;
      },
      _archiveSideDataPromise() {
        const _archiveSideDataPromise = null;
        return _archiveSideDataPromise;
      },
      _archiveStaleRetryId() {
        const _archiveStaleRetryId = null;
        return _archiveStaleRetryId;
      },
      _archiveSideDataHydratedAt() {
        const _archiveSideDataHydratedAt = 0;
        return _archiveSideDataHydratedAt;
      },
      ARCHIVE_STALE_RETRY_MS() {
        const ARCHIVE_STALE_RETRY_MS = 30 * 1000;
        return ARCHIVE_STALE_RETRY_MS;
      },
      ARCHIVE_HYDRATE_TTL_MS() {
        const ARCHIVE_HYDRATE_TTL_MS = 5 * 60 * 1000;
        return ARCHIVE_HYDRATE_TTL_MS;
      },
      _archiveStuckRenderRecoveryPromise() {
        const _archiveStuckRenderRecoveryPromise = null;
        return _archiveStuckRenderRecoveryPromise;
      },
      _archiveStuckRetryId() {
        const _archiveStuckRetryId = null;
        return _archiveStuckRetryId;
      },
      _archiveStuckRetryCount() {
        const _archiveStuckRetryCount = 0;
        return _archiveStuckRetryCount;
      },
      ARCHIVE_STUCK_RETRY_MAX() {
        const ARCHIVE_STUCK_RETRY_MAX = 120;
        return ARCHIVE_STUCK_RETRY_MAX;
      },
      ARCHIVE_STUCK_RETRY_MS() {
        const ARCHIVE_STUCK_RETRY_MS = 5 * 1000;
        return ARCHIVE_STUCK_RETRY_MS;
      },
      _archiveRecoveryRendered() {
        const _archiveRecoveryRendered = null;
        return _archiveRecoveryRendered;
      },
      _archiveRefreshPromise() {
        const _archiveRefreshPromise = null;
        return _archiveRefreshPromise;
      },
      _archiveRefreshAfterInflight() {
        const _archiveRefreshAfterInflight = false;
        return _archiveRefreshAfterInflight;
      },
      SPAWN_DEFAULT_ENGINES() {
        const SPAWN_DEFAULT_ENGINES = ['claude', 'codex', 'cursor', 'antigravity', 'kilo', 'hermes', 'kimi', 'opencode', 'devin', 'grok'];
        return SPAWN_DEFAULT_ENGINES;
      },
      SPAWN_DEFAULT_OTHER() {
        const SPAWN_DEFAULT_OTHER = '__other__';
        return SPAWN_DEFAULT_OTHER;
      },
      _defaultModelsByEngine() {
        const _defaultModelsByEngine = { claude: '', codex: 'gpt-5.6-terra', cursor: 'auto', antigravity: '', kilo: 'kilo/stepfun/step-3.7-flash:free', hermes: 'auto', kimi: 'kimi-code/k3', opencode: 'openrouter/anthropic/claude-sonnet-4.5', grok: 'grok-4.7' };
        return _defaultModelsByEngine;
      },
      _spawnDefaultsLoaded() {
        const _spawnDefaultsLoaded = false;
        return _spawnDefaultsLoaded;
      },
      spawnDefaultsState() {
        const spawnDefaultsState = {
    engine: (0, hostServices.readLegacySpawnEnginePref)(),
    models: Object.assign({}, sharedState._defaultModelsByEngine),
    reasoning_effort: '',
    auto_compact_k: 250,
    worker_inherit_session: false,
    worker_engine: '',
    worker_model: '',
    worker_reasoning_effort: '',
    worker_auto_compact_k: 250,
    worker_fallback: { enabled: false, models: [] },
    model_profiles: {},
    worker_fallback_engines: ['claude', 'codex', 'kimi', 'grok'],
    model_profile_engines: ['claude'],
    codex_context_1m: false,
    codex_context_tokens: 250000,
    blockedModels: new Set(),
  };
        return spawnDefaultsState;
      },
      $convInputEngineSelect() {
        const $convInputEngineSelect = document.getElementById('convInputEngineSelect');
        return $convInputEngineSelect;
      },
      $convInputModelSelect() {
        const $convInputModelSelect = document.getElementById('convInputModelSelect');
        return $convInputModelSelect;
      },
      $convInputEffortSelect() {
        const $convInputEffortSelect = document.getElementById('convInputEffortSelect');
        return $convInputEffortSelect;
      },
      spawnEffortChoiceDirty() {
        const spawnEffortChoiceDirty = false;
        return spawnEffortChoiceDirty;
      },
      nsAdvisor() {
        const nsAdvisor = { data: null, error: '', loading: false, loadedAt: 0, manual: false, applying: false, editing: false, draft: null, depth: 'routine', applied: null };
        return nsAdvisor;
      },
      _spawnEngineChosenByUser() {
        const _spawnEngineChosenByUser = false;
        return _spawnEngineChosenByUser;
      },
      _spawnModelChosenByUserForEngine() {
        const _spawnModelChosenByUserForEngine = new Set();
        return _spawnModelChosenByUserForEngine;
      },
      $kptToolbarEngineSelect() {
        const $kptToolbarEngineSelect = document.getElementById('kptToolbarEngineSelect');
        return $kptToolbarEngineSelect;
      },
      _codexAccountsLoadedAt() {
        const _codexAccountsLoadedAt = 0;
        return _codexAccountsLoadedAt;
      },
      _codexAccountsLoading() {
        const _codexAccountsLoading = false;
        return _codexAccountsLoading;
      },
      _codexAccountSession() {
        const _codexAccountSession = '';
        return _codexAccountSession;
      },
      _antigravityAccountSession() {
        const _antigravityAccountSession = '';
        return _antigravityAccountSession;
      },
      _EFFORT_SUFFIX_RE() {
        const _EFFORT_SUFFIX_RE = /^-(none|low|medium|high|xhigh|max)$/;
        return _EFFORT_SUFFIX_RE;
      },
      _emnObservedAt() {
        const _emnObservedAt = null;
        return _emnObservedAt;
      },
      _emnInstalled() {
        const _emnInstalled = null;
        return _emnInstalled;
      },
      _emnForced() {
        const _emnForced = '';
        return _emnForced;
      },
      _emnBusy() {
        const _emnBusy = '';
        return _emnBusy;
      },
      $sysModal() {
        const $sysModal = document.getElementById('sysModal');
        return $sysModal;
      },
      $sysDialog() {
        const $sysDialog = document.getElementById('sysDialog');
        return $sysDialog;
      },
      $sysChip() {
        const $sysChip = document.getElementById('cccServerStatusChip');
        return $sysChip;
      },
      $sysChipVerdict() {
        const $sysChipVerdict = document.getElementById('cccServerStatusVerdict');
        return $sysChipVerdict;
      },
      $sysChipCount() {
        const $sysChipCount = document.getElementById('cccServerStatusCount');
        return $sysChipCount;
      },
      $sysRollup() {
        const $sysRollup = document.getElementById('sysRollup');
        return $sysRollup;
      },
      $sysRollupText() {
        const $sysRollupText = document.getElementById('sysRollupText');
        return $sysRollupText;
      },
      $sysLiveRegion() {
        const $sysLiveRegion = document.getElementById('sysLiveRegion');
        return $sysLiveRegion;
      },
      WORKER_RESTART_CONFIRM() {
        const WORKER_RESTART_CONFIRM = 'Restart the execution worker?\n\n'
    + 'CCC hands off ownership of any live sessions first and reconciles the '
        + 'queue automatically once the worker answers again. No manual Reconcile '
    + 'needed. A turn already mid-response when the worker dies is still '
    + 'interrupted (draining stops new dispatch, it does not wait one out).';
        return WORKER_RESTART_CONFIRM;
      },
      SYS_ROWS() {
        const SYS_ROWS = {
    dashboard: {
      label: 'Dashboard',
      row: 'sysRowDash', state: 'sysDashState', started: 'sysDashStarted',
      busy: 'sysDashBusy', result: 'sysDashResult', btn: 'sysRestartDashBtn',
      meta: 'sysDashMeta', detail: 'sysDashDetail',
    },
    worker: {
      label: 'Execution worker',
      row: 'sysRowWorker', state: 'sysWorkerState', started: 'sysWorkerStarted',
      busy: 'sysWorkerBusy', result: 'sysWorkerResult', btn: 'sysRestartWorkerBtn',
      meta: 'sysWorkerMeta', detail: 'sysWorkerDetail',
    },
    watchtower: {
      label: 'WatchTower server',
      row: 'sysRowWt', state: 'sysWtState', started: 'sysWtStarted',
      busy: 'sysWtBusy', result: 'sysWtResult', btn: 'sysRestartWtBtn',
      meta: 'sysWtMeta', detail: 'sysWtDetail',
    },
  };
        return SYS_ROWS;
      },
      _sysPollTimer() {
        const _sysPollTimer = null;
        return _sysPollTimer;
      },
      _sysSpawnedList() {
        const _sysSpawnedList = [];
        return _sysSpawnedList;
      },
      _sysSpawnedTickTimer() {
        const _sysSpawnedTickTimer = null;
        return _sysSpawnedTickTimer;
      },
      _sysReturnFocus() {
        const _sysReturnFocus = null;
        return _sysReturnFocus;
      },
      _sysRestartInFlight() {
        const _sysRestartInFlight = null;
        return _sysRestartInFlight;
      },
      _sysArmedRestart() {
        const _sysArmedRestart = null;
        return _sysArmedRestart;
      },
      _sysFirstAttemptAt() {
        const _sysFirstAttemptAt = 0;
        return _sysFirstAttemptAt;
      },
      _sysLastRollupLevel() {
        const _sysLastRollupLevel = '';
        return _sysLastRollupLevel;
      },
      SYS_STATE_PILL() {
        const SYS_STATE_PILL = {
    online: 'online', degraded: 'degraded', offline: 'offline',
    idle: 'idle', unknown: 'checking',
  };
        return SYS_STATE_PILL;
      },
      $sysRestartAllBtn() {
        const $sysRestartAllBtn = document.getElementById('sysRestartAllBtn');
        return $sysRestartAllBtn;
      },
      _sysDashArmTimer() {
        const _sysDashArmTimer = null;
        return _sysDashArmTimer;
      },
      _wtReleasedRender() {
        const _wtReleasedRender = null;
        return _wtReleasedRender;
      },
      $restartServerBtn() {
        const $restartServerBtn = document.getElementById('restartServerBtn');
        return $restartServerBtn;
      },
      $restartServerLabel() {
        const $restartServerLabel = document.getElementById('restartServerLabel');
        return $restartServerLabel;
      },
      $controlPlaneStatus() {
        const $controlPlaneStatus = document.getElementById('controlPlaneStatus');
        return $controlPlaneStatus;
      },
      $controlPlaneDrainBtn() {
        const $controlPlaneDrainBtn = document.getElementById('controlPlaneDrainBtn');
        return $controlPlaneDrainBtn;
      },
      $controlPlaneReconcileBtn() {
        const $controlPlaneReconcileBtn = document.getElementById('controlPlaneReconcileBtn');
        return $controlPlaneReconcileBtn;
      },
      $workerBadge() {
        const $workerBadge = document.getElementById('cccWorkerBadge');
        return $workerBadge;
      },
      $workerWord() {
        const $workerWord = document.getElementById('cccWorkerWord');
        return $workerWord;
      },
      $workerCount() {
        const $workerCount = document.getElementById('cccWorkerCount');
        return $workerCount;
      },
      $drainPausedBanner() {
        const $drainPausedBanner = document.getElementById('drainPausedBanner');
        return $drainPausedBanner;
      },
      $drainPausedBannerText() {
        const $drainPausedBannerText = document.getElementById('drainPausedBannerText');
        return $drainPausedBannerText;
      },
      $drainPausedResumeBtn() {
        const $drainPausedResumeBtn = document.getElementById('drainPausedResumeBtn');
        return $drainPausedResumeBtn;
      },
      restartServerPort() {
        const restartServerPort = '';
        return restartServerPort;
      },
      controlPlaneDraining() {
        const controlPlaneDraining = false;
        return controlPlaneDraining;
      },
      controlPlaneDrainReason() {
        const controlPlaneDrainReason = '';
        return controlPlaneDrainReason;
      },
      controlPlaneDrainQueued() {
        const controlPlaneDrainQueued = 0;
        return controlPlaneDrainQueued;
      },
      controlPlaneOnline() {
        const controlPlaneOnline = false;
        return controlPlaneOnline;
      },
      watchtowerServiceUrl() {
        const watchtowerServiceUrl = '';
        return watchtowerServiceUrl;
      },
      $annotationStartBtn() {
        const $annotationStartBtn = document.getElementById('annotationStartBtn');
        return $annotationStartBtn;
      },
      annotationState() {
        const annotationState = null;
        return annotationState;
      },
      ANN_DOM_CAPTURE_MAX_NODES() {
        const ANN_DOM_CAPTURE_MAX_NODES = 1500;
        return ANN_DOM_CAPTURE_MAX_NODES;
      },
      ANN_GITHUB_ISSUES_URL() {
        const ANN_GITHUB_ISSUES_URL = 'https://github.com/amirfish1/claude-command-center/issues/new';
        return ANN_GITHUB_ISSUES_URL;
      },
      $statusRailAnnotateBtn() {
        const $statusRailAnnotateBtn = document.getElementById('statusRailAnnotateBtn');
        return $statusRailAnnotateBtn;
      },
      $statusRailTitleRenameBtn() {
        const $statusRailTitleRenameBtn = document.getElementById('statusRailTitleRenameBtn');
        return $statusRailTitleRenameBtn;
      },
      $annotationFabBtn() {
        const $annotationFabBtn = document.getElementById('annotationFabBtn');
        return $annotationFabBtn;
      },
      _ccRunSeq() {
        const _ccRunSeq = 0;
        return _ccRunSeq;
      },
      _CC_ORCH_RES() {
        const _CC_ORCH_RES = [
    [/(?:^|[\s;&|(`"'])((?:[^\s"'`]*tokeneconomizer[^\s"'`]*\/)?te)["']?\s+(?:(?:--host|--cwd)\s+(?:"[^"]*"|'[^']*'|[^\s;|)]+)\s+)*(status|microskills|facts|toolprune|untool|config|fold|judge|doctor|repos|repomap|probe)\b/gi,
      (m) => 'TokenEconomizer ' + m[2].toLowerCase()],
    [/(?:^|[\s;&|(`"'])((?:[^\s"'`]*\/)?wt)["']?\s+([a-z][\w-]*)/g, (m) => (0, sharedState._ccWtCommandLabel)(m)],
    [/(?:^|[\s;&|(`])ccc\s+([a-z][\w-]*)/g, (m) => 'ccc ' + m[1]],
    [/(?:localhost|127\.0\.0\.1|\$\{?CCC[A-Z_]*\}?)(?::809[01])?(\/api\/[\w\-./]*)/g,
      (m) => 'CCC API ' + m[1].replace(/\/[0-9a-f]{8}-[0-9a-f-]{27}/gi, '/<id>').replace(/\/$/, '')],
  ];
        return _CC_ORCH_RES;
      },
      _ccRunsRaf() {
        const _ccRunsRaf = 0;
        return _ccRunsRaf;
      },
      _ccRunsObserver() {
        const _ccRunsObserver = new MutationObserver((records) => {
    for (const r of records) {
      const t = r.target;
      if (t && t.closest && t.closest('.conversations-view')) { (0, sharedState._ccScheduleRuns)(); return; }
      if (t && t.querySelector && t.querySelector('.conversations-view')) { (0, sharedState._ccScheduleRuns)(); return; }
    }
  });
        return _ccRunsObserver;
      },
      $bugModal() {
        const $bugModal = document.getElementById('bugReportModal');
        return $bugModal;
      },
      $bugDescInput() {
        const $bugDescInput = document.getElementById('bugReportDescInput');
        return $bugDescInput;
      },
      $bugMeta() {
        const $bugMeta = document.getElementById('bugReportMeta');
        return $bugMeta;
      },
      $bugError() {
        const $bugError = document.getElementById('bugReportError');
        return $bugError;
      },
      $bugSuccess() {
        const $bugSuccess = document.getElementById('bugReportSuccess');
        return $bugSuccess;
      },
      $bugFallback() {
        const $bugFallback = document.getElementById('bugReportFallback');
        return $bugFallback;
      },
      $bugCancelBtn() {
        const $bugCancelBtn = document.getElementById('bugReportCancelBtn');
        return $bugCancelBtn;
      },
      $bugSubmitBtn() {
        const $bugSubmitBtn = document.getElementById('bugReportSubmitBtn');
        return $bugSubmitBtn;
      },
      $bugCopyBtn() {
        const $bugCopyBtn = document.getElementById('bugReportCopyBtn');
        return $bugCopyBtn;
      },
      $bugShotBtn() {
        const $bugShotBtn = document.getElementById('bugReportShotBtn');
        return $bugShotBtn;
      },
      $bugShotBtnLabel() {
        const $bugShotBtnLabel = document.getElementById('bugReportShotBtnLabel');
        return $bugShotBtnLabel;
      },
      $bugShotPreview() {
        const $bugShotPreview = document.getElementById('bugReportShotPreview');
        return $bugShotPreview;
      },
      $bugShotImg() {
        const $bugShotImg = document.getElementById('bugReportShotImg');
        return $bugShotImg;
      },
      $bugName() {
        const $bugName = document.getElementById('bugName');
        return $bugName;
      },
      $bugContact() {
        const $bugContact = document.getElementById('bugContact');
        return $bugContact;
      },
      $bugPreview() {
        const $bugPreview = document.getElementById('bugPreview');
        return $bugPreview;
      },
      $bugAutoBanner() {
        const $bugAutoBanner = document.getElementById('bugAutoBanner');
        return $bugAutoBanner;
      },
      $bugAutoOptOut() {
        const $bugAutoOptOut = document.getElementById('bugAutoOptOut');
        return $bugAutoOptOut;
      },
      $bugShotSection() {
        const $bugShotSection = document.getElementById('bugReportScreenshotSection');
        return $bugShotSection;
      },
      $bugShotEmailNote() {
        const $bugShotEmailNote = document.getElementById('bugShotEmailNote');
        return $bugShotEmailNote;
      },
      $bugDiagInclude() {
        const $bugDiagInclude = document.getElementById('bugDiagInclude');
        return $bugDiagInclude;
      },
      $bugDiagStatus() {
        const $bugDiagStatus = document.getElementById('bugDiagStatus');
        return $bugDiagStatus;
      },
      BUG_DIAG_HOURS() {
        const BUG_DIAG_HOURS = 168;
        return BUG_DIAG_HOURS;
      },
      BUG_DIAG_NOTE() {
        const BUG_DIAG_NOTE = 'Timings, counts, and versions only. No prompts, paths, session names, or ticket text.';
        return BUG_DIAG_NOTE;
      },
      bugDiag() {
        const bugDiag = { private: null, github: null };
        return bugDiag;
      },
      bugReportFrom() {
        const bugReportFrom = '';
        return bugReportFrom;
      },
      bugCachedVersion() {
        const bugCachedVersion = null;
        return bugCachedVersion;
      },
      bugFallbackMarkdown() {
        const bugFallbackMarkdown = '';
        return bugFallbackMarkdown;
      },
      bugShotB64() {
        const bugShotB64 = '';
        return bugShotB64;
      },
      bugDiagnosticMode() {
        const bugDiagnosticMode = false;
        return bugDiagnosticMode;
      },
      bugDiagnosticRequestId() {
        const bugDiagnosticRequestId = '';
        return bugDiagnosticRequestId;
      },
      bugDiagnosticSections() {
        const bugDiagnosticSections = [
    'bugReportDestinationSection', 'bugReportIdentitySection',
    'bugReportDiagSection',
    'bugReportScreenshotSection', 'bugReportContextSection',
    'bugReportPreviewSection', 'bugShotEmailNote',
  ].map((id) => document.getElementById(id));
        return bugDiagnosticSections;
      },
      BUG_AUTO_OPTOUT_KEY() {
        const BUG_AUTO_OPTOUT_KEY = 'ccc-bug-report-auto-optout';
        return BUG_AUTO_OPTOUT_KEY;
      },
      $handoffModal() {
        const $handoffModal = document.getElementById('handoffModal');
        return $handoffModal;
      },
      $handoffSubtitle() {
        const $handoffSubtitle = document.getElementById('handoffSubtitle');
        return $handoffSubtitle;
      },
      $handoffDest() {
        const $handoffDest = document.getElementById('handoffDest');
        return $handoffDest;
      },
      $handoffPreflightBtn() {
        const $handoffPreflightBtn = document.getElementById('handoffPreflightBtn');
        return $handoffPreflightBtn;
      },
      $handoffNoPeers() {
        const $handoffNoPeers = document.getElementById('handoffNoPeers');
        return $handoffNoPeers;
      },
      $handoffPlan() {
        const $handoffPlan = document.getElementById('handoffPlan');
        return $handoffPlan;
      },
      $handoffError() {
        const $handoffError = document.getElementById('handoffError');
        return $handoffError;
      },
      $handoffRecheckBtn() {
        const $handoffRecheckBtn = document.getElementById('handoffRecheckBtn');
        return $handoffRecheckBtn;
      },
      $handoffConfirmBtn() {
        const $handoffConfirmBtn = document.getElementById('handoffConfirmBtn');
        return $handoffConfirmBtn;
      },
      handoffSessionId() {
        const handoffSessionId = null;
        return handoffSessionId;
      },
      handoffPeers() {
        const handoffPeers = [];
        return handoffPeers;
      },
      handoffReady() {
        const handoffReady = false;
        return handoffReady;
      },
      SPAWN_CWD_KEY() {
        const SPAWN_CWD_KEY = 'ccc-spawn-cwd';
        return SPAWN_CWD_KEY;
      },
      SPAWN_CWD_CHIP_LIMIT() {
        const SPAWN_CWD_CHIP_LIMIT = 10;
        return SPAWN_CWD_CHIP_LIMIT;
      },
      spawnCwdOptions() {
        const spawnCwdOptions = [];
        return spawnCwdOptions;
      },
      spawnCwdAutoDefault() {
        const spawnCwdAutoDefault = '';
        return spawnCwdAutoDefault;
      },
      repoGuessSeq() {
        const repoGuessSeq = 0;
        return repoGuessSeq;
      },
      repoGuessTimer() {
        const repoGuessTimer = null;
        return repoGuessTimer;
      },
      repoGuessInflight() {
        const repoGuessInflight = null;
        return repoGuessInflight;
      },
      repoGuessLastPrompt() {
        const repoGuessLastPrompt = '';
        return repoGuessLastPrompt;
      },
      repoGuessUserPicked() {
        const repoGuessUserPicked = false;
        return repoGuessUserPicked;
      },
      repoGuessApplying() {
        const repoGuessApplying = false;
        return repoGuessApplying;
      },
      repoGuessAuto() {
        const repoGuessAuto = null;
        return repoGuessAuto;
      },
      repoGuessSuggestion() {
        const repoGuessSuggestion = '';
        return repoGuessSuggestion;
      },
      spawnCwdMissing() {
        const spawnCwdMissing = new Set();
        return spawnCwdMissing;
      },
      spawnCwdChecked() {
        const spawnCwdChecked = new Set();
        return spawnCwdChecked;
      },
      spawnCwdExpanded() {
        const spawnCwdExpanded = false;
        return spawnCwdExpanded;
      },
      folderPickerEls() {
        const folderPickerEls = {
    backdrop: document.getElementById('folderPickerBackdrop'),
    note: document.getElementById('folderPickerNote'),
    up: document.getElementById('folderPickerUp'),
    path: document.getElementById('folderPickerPath'),
    list: document.getElementById('folderPickerList'),
    error: document.getElementById('folderPickerError'),
    cancel: document.getElementById('folderPickerCancel'),
    select: document.getElementById('folderPickerSelect'),
  };
        return folderPickerEls;
      },
      folderPickerOnPick() {
        const folderPickerOnPick = null;
        return folderPickerOnPick;
      },
      folderPickerKeyHandler() {
        const folderPickerKeyHandler = null;
        return folderPickerKeyHandler;
      },
      smartRepoConfigured() {
        const smartRepoConfigured = null;
        return smartRepoConfigured;
      },
      _claudePrewarmKey() {
        const _claudePrewarmKey = '';
        return _claudePrewarmKey;
      },
      _claudePrewarmPromise() {
        const _claudePrewarmPromise = null;
        return _claudePrewarmPromise;
      },
      _claudePrewarmDebounce() {
        const _claudePrewarmDebounce = null;
        return _claudePrewarmDebounce;
      },
      _claudePrewarmClientId() {
        const _claudePrewarmClientId = (window.crypto && window.crypto.randomUUID)
    ? window.crypto.randomUUID()
    : ('tab-' + Date.now() + '-' + Math.random().toString(16).slice(2));
        return _claudePrewarmClientId;
      },
      _adoptedComposerHome() {
        const _adoptedComposerHome = null;
        return _adoptedComposerHome;
      },
      NS_FUN_PROJECT_NAMES() {
        const NS_FUN_PROJECT_NAMES = [
    'turbo-walrus', 'quantum-falafel', 'midnight-mango', 'electric-hummus', 'rocket-pita',
    'neon-cactus', 'disco-octopus', 'cosmic-bagel', 'turbo-pickle', 'laser-llama',
    'funky-matzah', 'atomic-tahini', 'groovy-gecko', 'plasma-penguin', 'ninja-noodle',
    'bionic-burrito', 'stealth-sabra', 'mega-meerkat', 'hyper-hoopoe', 'pixel-pomegranate',
    'sneaky-shakshuka', 'dancing-dolphin', 'flying-falcon', 'silent-sphinx', 'rapid-raccoon',
    'golden-golem', 'witty-wombat', 'zesty-zebra', 'cunning-camel', 'brave-burekas',
    'lucky-lemur', 'magic-malabi', 'swift-swallow', 'bold-baklava', 'clever-couscous',
    'mighty-mole', 'jolly-jackal', 'shiny-shekel', 'happy-halva', 'dapper-dingo',
    'frozen-falcon', 'crimson-koala', 'velvet-viper', 'amber-antelope', 'cobalt-cricket',
    'scarlet-sloth', 'indigo-ibex', 'emerald-emu', 'turquoise-tapir', 'magenta-mongoose',
    'iron-dome-jr', 'startup-nation', 'desert-bloom', 'dead-sea-surfer', 'negev-ninja',
    'galilee-glider', 'carmel-comet', 'jaffa-jet', 'eilat-eagle', 'masada-mainframe',
    'silicon-shuk', 'hummus-engine', 'krembo-cloud', 'bamba-bot', 'bisli-blaster',
    'sufganiyah-server', 'arak-attack', 'tahini-tornado', 'zaatar-zeppelin', 'labneh-launcher',
    'moonshot-mule', 'gravity-goose', 'orbit-otter', 'comet-cobra', 'nebula-newt',
    'stellar-stork', 'lunar-lobster', 'solar-salmon', 'astro-armadillo', 'galaxy-gibbon',
    'warp-weasel', 'photon-phoenix', 'quark-quail', 'tensor-toucan', 'vector-vole',
    'binary-bison', 'kernel-kiwi', 'syntax-squid', 'cache-cheetah', 'lambda-leopard',
    'docker-duck', 'regex-rhino', 'pixel-panther', 'crypto-crab', 'neural-narwhal',
    'fusion-ferret', 'matrix-marmot', 'cyber-seagull', 'data-dromedary', 'logic-lynx',
  ];
        return NS_FUN_PROJECT_NAMES;
      },
      _nsExtensionTemplates() {
        const _nsExtensionTemplates = null;
        return _nsExtensionTemplates;
      },
      MODEL_PICKER_ENGINE_GLYPHS() {
        const MODEL_PICKER_ENGINE_GLYPHS = {
    claude: 'C', codex: 'X', antigravity: 'A', gemini: 'A', grok: 'G', devin: 'D',
    kimi: 'K', cursor: 'U', hermes: 'H', kilo: 'L', opencode: 'O', copilot: 'P',
  };
        return MODEL_PICKER_ENGINE_GLYPHS;
      },
      _cachedServerModelPicks() {
        const _cachedServerModelPicks = (() => {
    try {
      const raw = localStorage.getItem('ccc-model-picker-picks');
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed) && parsed.length > 0) return parsed;
      }
    } catch (_) {}
    return null;
  })();
        return _cachedServerModelPicks;
      },
      _fetchServerModelPicksPromise() {
        const _fetchServerModelPicksPromise = null;
        return _fetchServerModelPicksPromise;
      },
      _nsModelComparisonOpen() {
        const _nsModelComparisonOpen = false;
        return _nsModelComparisonOpen;
      },
      $settingsBtn() {
        const $settingsBtn = document.getElementById('settingsBtn');
        return $settingsBtn;
      },
      CONV_BG_STORAGE_KEY() {
        const CONV_BG_STORAGE_KEY = 'ccc-conv-bg-by-conversation';
        return CONV_BG_STORAGE_KEY;
      },
      CONV_BG_DEFAULT() {
        const CONV_BG_DEFAULT = 'stitch';
        return CONV_BG_DEFAULT;
      },
      CONV_BG_DEFAULT_LIGHT() {
        const CONV_BG_DEFAULT_LIGHT = 'paper';
        return CONV_BG_DEFAULT_LIGHT;
      },
      CONV_BG_PALETTE() {
        const CONV_BG_PALETTE = [
    { id: 'stitch', label: 'Stitch', bg: '#0a0a0b' },
    { id: 'charcoal', label: 'Charcoal', bg: '#0d1117' },
    { id: 'midnight', label: 'Midnight', bg: '#101827' },
    { id: 'slate', label: 'Slate', bg: '#1b263b' },
    { id: 'ocean', label: 'Ocean', bg: '#0d2438' },
    { id: 'cobalt', label: 'Cobalt', bg: '#172554' },
    { id: 'indigo', label: 'Indigo', bg: '#171a33' },
    { id: 'plum', label: 'Plum', bg: '#2a1824' },
    { id: 'wine', label: 'Wine', bg: '#351b24' },
    { id: 'sepia', label: 'Sepia', bg: '#2a2118' },
    { id: 'olive', label: 'Olive', bg: '#232b18' },
    { id: 'pine', label: 'Pine', bg: '#14301f' },
    { id: 'teal', label: 'Deep teal', bg: '#102c2f' },
    { id: 'paper', label: 'Paper', bg: '#f7f2e8' },
    { id: 'parchment', label: 'Parchment', bg: '#f3ead8' },
    { id: 'sand', label: 'Sand', bg: '#f4efe2' },
    { id: 'clay', label: 'Clay', bg: '#f4e9e0' },
    { id: 'peach', label: 'Peach', bg: '#fff1e8' },
    { id: 'rose', label: 'Rose', bg: '#fff3f6' },
    { id: 'lilac', label: 'Lilac', bg: '#f3edff' },
    { id: 'mist', label: 'Mist', bg: '#eef4ff' },
    { id: 'sky', label: 'Sky', bg: '#e7f0fb' },
    { id: 'ice', label: 'Ice', bg: '#edf8fb' },
    { id: 'mint', label: 'Mint', bg: '#e9f7ee' },
    { id: 'sage', label: 'Sage', bg: '#edf7f2' },
  ];
        return CONV_BG_PALETTE;
      },
      TTS_VOICE_DEFAULT_KEY() {
        const TTS_VOICE_DEFAULT_KEY = 'ccc-tts-voice-default';
        return TTS_VOICE_DEFAULT_KEY;
      },
      TTS_VOICE_BY_SESSION_KEY() {
        const TTS_VOICE_BY_SESSION_KEY = 'ccc-tts-voice-by-session';
        return TTS_VOICE_BY_SESSION_KEY;
      },
      _ttsVoiceOptions() {
        const _ttsVoiceOptions = [];
        return _ttsVoiceOptions;
      },
      CONV_ROWSTYLE_STORAGE_KEY() {
        const CONV_ROWSTYLE_STORAGE_KEY = 'ccc-conv-rowstyle';
        return CONV_ROWSTYLE_STORAGE_KEY;
      },
      CONV_ROWSTYLE_VARIANTS() {
        const CONV_ROWSTYLE_VARIANTS = [
    // kind 'lines' previews as a 3-bar glyph (density / brightness);
    // kind 'font' previews as "Aa" set in the typeface it applies.
    // Small caps, Compact, Mono, and Bold were tried and rejected by the
    // user (2026-08-28) — don't re-add them to this list. Same day: Default,
    // Active, Contrast, Serif, Humanist, Geometric, Condensed, and Typewriter
    // were dropped too. Slab and Grotesk (plus their XL siblings) were
    // dropped in a follow-up round. Bright, Large, and base Rounded were
    // narrowed away in a final round, leaving Large Bright (now the
    // fallback/default) and Rounded XL as the only two survivors. Comfortable
    // moved out of this radio group entirely (now the "Details" toggle next
    // to Wrap in the sidebar toolbar), and Airy became a separate 3-step
    // spacing control (CONV_ROWSTYLE_SPACING) — neither is an alternative to
    // a font/color choice.
    { id: 'large-bright', label: 'Large Bright', kind: 'lines', op: 100, h: 3, gap: 4 },
    { id: 'rounded-xl',   label: 'Rounded XL',   kind: 'font', font: 'ui-rounded, "SF Pro Rounded", "Avenir Next", system-ui', weight: 500 },
  ];
        return CONV_ROWSTYLE_VARIANTS;
      },
      CONV_ROWSTYLE_SPACING() {
        const CONV_ROWSTYLE_SPACING = [
    { id: 'cozy',  label: 'Cozy',  title: 'Default row spacing', op: 70, h: 2, gap: 2 },
    { id: 'roomy', label: 'Roomy', title: 'A bit more breathing room between rows', op: 70, h: 2, gap: 3.5 },
    { id: 'airy',  label: 'Airy',  title: 'Maximum breathing room between rows', op: 70, h: 2, gap: 5 },
  ];
        return CONV_ROWSTYLE_SPACING;
      },
      SHOW_ROWSTYLE_FONT_AND_SPACING() {
        const SHOW_ROWSTYLE_FONT_AND_SPACING = false;
        return SHOW_ROWSTYLE_FONT_AND_SPACING;
      },
      ROW_DELINEATION_KEY() {
        const ROW_DELINEATION_KEY = 'ccc-row-delineation';
        return ROW_DELINEATION_KEY;
      },
      CONV_ROWSTYLE_DELINEATION() {
        const CONV_ROWSTYLE_DELINEATION = [
    { id: 'divider', label: 'Divider', title: 'Thin hairline between rows', op: 70, h: 2, gap: 2 },
    { id: 'zebra',   label: 'Zebra',   title: 'Faint alternating row tint', op: 70, h: 2, gap: 2 },
    { id: 'cards',   label: 'Cards',   title: 'Each row as a separated card', op: 70, h: 2, gap: 2 },
  ];
        return CONV_ROWSTYLE_DELINEATION;
      },
      VOICE_MOBILE_BTN_STORAGE_KEY() {
        const VOICE_MOBILE_BTN_STORAGE_KEY = 'ccc-voice-mobile-btn';
        return VOICE_MOBILE_BTN_STORAGE_KEY;
      },
      ENGINE_HUB_META() {
        const ENGINE_HUB_META = {
    claude: { mono: 'Cl', cmd: 'claude', label: 'Claude Code', vendor: 'Anthropic', hue: 24, docs: 'https://docs.claude.com/en/docs/claude-code' },
    codex: { mono: 'Cx', cmd: 'codex', label: 'Codex', vendor: 'OpenAI', hue: 160, docs: 'https://developers.openai.com/codex/cli' },
    cursor: { mono: 'Cu', cmd: 'cursor-agent', label: 'Cursor Agent', vendor: 'Cursor', hue: 220, docs: 'https://cursor.com/cli' },
    antigravity: { mono: 'Ag', cmd: 'agy', label: 'Antigravity', vendor: 'Google', hue: 265, docs: 'https://antigravity.google' },
    kimi: { mono: 'Km', cmd: 'kimi', label: 'Kimi Code', vendor: 'Moonshot', hue: 200, docs: 'https://www.kimi.com/code/docs/en/third-party-tools/other-coding-agents.html',
      links: [['Membership', 'https://www.kimi.com/code/docs/en/kimi-code/membership.html']] },
    opencode: { mono: 'Oc', cmd: 'opencode', label: 'OpenCode', vendor: 'Any provider', hue: 45, docs: 'https://opencode.ai/docs',
      install: 'curl -fsSL https://opencode.ai/install | bash', login: 'opencode auth login' },
    kilo: { mono: 'Kl', cmd: 'kilo', label: 'Kilo Code', vendor: 'Any provider', hue: 50, docs: 'https://kilo.ai/docs/cli', install: 'npm install -g @kilocode/cli' },
    hermes: { mono: 'He', cmd: 'hermes', label: 'Hermes', vendor: 'Nous Research', hue: 300, install: 'curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash', docs: 'https://github.com/NousResearch/hermes-agent' },
    devin: { mono: 'Dv', cmd: 'devin', label: 'Devin', vendor: 'Cognition', hue: 185, docs: 'https://docs.devin.ai/cli',
      install: 'curl -fsSL https://cli.devin.ai/install.sh | bash', login: 'devin auth login' },
    grok: { mono: 'Gk', cmd: 'grok', label: 'Grok', vendor: 'xAI', hue: 0, docs: 'https://x.ai/cli',
      install: 'curl -fsSL https://x.ai/cli/install.sh | bash', login: 'grok login' },
    droid: { mono: 'Dr', cmd: 'droid', label: 'Droid', vendor: 'Factory', hue: 15, docs: 'https://docs.factory.ai/cli/getting-started/quickstart',
      install: 'curl -fsSL https://app.factory.ai/cli | sh' },
    aider: { mono: 'Ai', cmd: 'aider', label: 'Aider', vendor: 'Any provider', hue: 120, docs: 'https://aider.chat/docs/install.html',
      install: 'python -m pip install aider-install && aider-install' },
    pi: { mono: 'Pi', cmd: 'pi', label: 'Pi', vendor: 'Any provider', hue: 330, install: 'npm install -g @mariozechner/pi-coding-agent', docs: 'https://github.com/badlogic/pi-mono' },
  };
        return ENGINE_HUB_META;
      },
      $settingsModal() {
        const $settingsModal = document.getElementById('settingsModal');
        return $settingsModal;
      },
      _settingsModalPrevFocus() {
        const _settingsModalPrevFocus = null;
        return _settingsModalPrevFocus;
      },
      _settingsCurrentSection() {
        const _settingsCurrentSection = 'appearance';
        return _settingsCurrentSection;
      },
      choiceEligibilityReturn() {
        const choiceEligibilityReturn = undefined;
        return choiceEligibilityReturn;
      },
      ENGINES_FIRST_RUN_KEY() {
        const ENGINES_FIRST_RUN_KEY = 'ccc-engines-first-run-done';
        return ENGINES_FIRST_RUN_KEY;
      },
      TAILSCALE_STEP_KEY() {
        const TAILSCALE_STEP_KEY = 'ccc-tailscale-step-done';
        return TAILSCALE_STEP_KEY;
      },
      _etsTimer() {
        const _etsTimer = null;
        return _etsTimer;
      },
      $cmdkModal() {
        const $cmdkModal = document.getElementById('cmdkModal');
        return $cmdkModal;
      },
      $cmdkInput() {
        const $cmdkInput = document.getElementById('cmdkInput');
        return $cmdkInput;
      },
      $cmdkList() {
        const $cmdkList = document.getElementById('cmdkList');
        return $cmdkList;
      },
      _cmdkItems() {
        const _cmdkItems = [];
        return _cmdkItems;
      },
      _cmdkIndex() {
        const _cmdkIndex = 0;
        return _cmdkIndex;
      },
      refreshChatFind() {
        const refreshChatFind = function() {};
        return refreshChatFind;
      },
      $chatFindInput() {
        const $chatFindInput = document.getElementById('chatFindInput');
        return $chatFindInput;
      },
      $chatFindNext() {
        const $chatFindNext = document.getElementById('chatFindNext');
        return $chatFindNext;
      },
      $chatFindPrev() {
        const $chatFindPrev = document.getElementById('chatFindPrev');
        return $chatFindPrev;
      },
      $chatFindClose() {
        const $chatFindClose = document.getElementById('chatFindClose');
        return $chatFindClose;
      },
      FFT_DONE_KEY() {
        const FFT_DONE_KEY = 'ccc-tour-done';
        return FFT_DONE_KEY;
      }
    };
  }
  root.CCCConversationDefaults = Object.freeze({ createInitializers });
})(typeof window === 'undefined' ? globalThis : window);
