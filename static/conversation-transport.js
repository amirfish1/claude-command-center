/* Shared conversation transport with explicit live-state and host adapters. */
(function (root) {
  'use strict';
  function createController({ state, helpers }) {
    const { activePaneId, paneByPaneId, _refreshTokenEconomizerAnnotation, paneIndexByPaneId, ffcInvalidate, fetchSessionUsage, getConvViewForPane, _renderBacklogDetail, _renderGithubPrDetail, resolvedSpawnConversationId, isSpawnLogPlaceholderSource, loadCodexLog, stopCodexLogPoller, _pollerOff, _pollerTick, renderPendingSpawnConversation, _contEnsureSegments, _contNextParentForPane, convTailLines, convFirstOpenLines, _takePrefetchedConversationTail, _convCacheEligible, _convCacheGet, _fetchConvJsonTimed, convPaneElById, _markConvCachePreview, noteAppEmbedDrawn, _perfEvent, _perfActiveElapsed, _flushAfterConvTailAfterPaint, updateInputBar, _contSegmentTitle, _contSegmentConfig, _prependConversationEvents, _convCachePutSoon, _renderFirstPromptPin, _convOpenPhases, _convOpenAfterPaint, _insertLoadEarlierBanner, _scheduleConvOpenBackfill, restorePendingSendEchoes, optimisticPromptForSession, spawnStatsMark, markSpawnAssistantPaint, markClaudeFirstVisibleOutput, renderSpawnStatsPanel } = helpers;
  function startConvStream(paneId) {
    const streamPaneId = paneId || activePaneId();
    const streamPane = paneByPaneId(streamPaneId);
    if (!streamPane || !streamPane.conversationId) return;
    stopConvStream(streamPaneId);
    // Snapshot the pane and conv id at stream-start time so the SSE
    // event-handler closures always target THIS pane's stream even if
    // the user switches the active pane between SSE events.
    const streamConvId = streamPane.conversationId;
    const url = '/api/conversations/' + streamConvId + '/stream?after=' + streamPane.lastLine
      + (streamPane.antigravityProvisional ? '&antigravity_provisional=1' : '');
    const source = new EventSource(url);
    streamPane.eventSource = source;
    // Watchdog: WKWebView (and Chromium on flaky links) sometimes lets an
    // EventSource silently die without firing onerror, so the stream goes
    // quiet and the conv pane stops updating even though the terminal is
    // clearly running. Server sends `event: keepalive` every 5s — if we
    // hear nothing (no data, no keepalive) for 15s, force a reconnect
    // ourselves. Reconnect uses the freshly-advanced streamPane.lastLine
    // so we catch up on any events that landed during the silent gap.
    const WATCHDOG_MS = 15000;
    const ownsStream = () => paneByPaneId(streamPaneId) === streamPane
      && streamPane.conversationId === streamConvId && streamPane.eventSource === source;
    let retryTimer;
    const reconnect = (delay) => {
      if (retryTimer || !ownsStream()) return;
      clearTimeout(watchdog);
      retryTimer = setTimeout(() => {
        if (ownsStream()) startConvStream(streamPaneId);
      }, delay);
      source.close();
    };
    let watchdog;
    const bumpWatchdog = () => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => reconnect(500), WATCHDOG_MS);
    };
    bumpWatchdog();
    streamPane._eventSourceCleanup = () => {
      clearTimeout(watchdog);
      clearTimeout(retryTimer);
    };
    source.addEventListener('keepalive', () => {
      if (retryTimer || !ownsStream()) return;
      bumpWatchdog();
      _refreshTokenEconomizerAnnotation(streamPaneId);
    });
    source.addEventListener('source_reset', () => {
      if (retryTimer || !ownsStream()) return;
      stopConvStream(streamPaneId);
      (0, state.selectConversation)(streamConvId, streamPaneId);
    });
    source.onmessage = (ev) => {
      if (retryTimer || !ownsStream()) return;
      bumpWatchdog();
      try {
        const data = JSON.parse(ev.data);
        if (!data.events?.length) return;
        const savedIdx = state.splitState.activeIndex;
        state.splitState.activeIndex = paneIndexByPaneId(streamPaneId);
        try {
          ffcInvalidate(state.currentConversation);
          // Stream batches are deltas. Keep parked cards until an HTTP
          // queue snapshot or a durable delivery receipt retires them.
          if ((0, state.renderConversationEvents)(data.events, streamPaneId, { queueSnapshot: false }) === false) {
            reconnect(2000);
            return;
          }
          streamPane.antigravityProvisional = !!data.antigravity_provisional;
          if (Number.isFinite(data.last_line)) {
            streamPane.lastLine = Math.max(streamPane.lastLine, data.last_line);
          }
        } finally {
          state.splitState.activeIndex = savedIdx;
        }
      } catch (_) {
        // The server has already sent this batch. Replay from the last
        // applied cursor before accepting any subsequent batch.
        reconnect(2000);
        return;
      }
      try {
        const sid = (state.conversationsData.find(x => x.id === streamConvId) || {}).session_id || streamConvId;
        fetchSessionUsage(sid);
      } catch (_) {}
    };
    source.onerror = () => reconnect(2000);
  }

  function stopConvStream(paneId) {
    const pane = paneByPaneId(paneId || activePaneId());
    if (!pane) return;
    pane._eventSourceCleanup?.();
    pane._eventSourceCleanup = null;
    pane.eventSource?.close();
    pane.eventSource = null;
  }

  async function fetchConversationEvents(paneId) {
    if (paneId) {
      const idx = paneIndexByPaneId(paneId);
      if (idx >= 0) state.splitState.activeIndex = idx;
    }
    const fetchPaneId = activePaneId();
    if (!state.currentConversation) return;
    const id = state.currentConversation;
    const $view = getConvViewForPane(fetchPaneId) || state.$conversationsView;
    // Backlog cards (open GH issues + TODO/PARKING/native-task) have no
    // session JSONL — /api/conversations/<id> returns 404. Render the
    // issue body directly from the card's already-loaded fields so the
    // detail pane isn't a permanent "Loading…".
    const backlogCard = (state.conversationsData || []).find(x => x.id === id && x.source === 'backlog');
    if (backlogCard || id.startsWith('backlog-')) {
      const c = backlogCard || (state.conversationsData || []).find(x => x.id === id);
      if (c) {
        const html = await _renderBacklogDetail(c);
        if (state.currentConversation === id) $view.innerHTML = html;
        return;
      }
      // A backlog id whose row hasn't loaded yet (switch/load window). Defer to
      // the next poll — which renders it — instead of falling through to
      // /api/conversations/<backlog-id>, which 404s and wedges the pane on
      // "Loading…".
      return;
    }
    // github_pr rows are synthetic (no real session transcript).
    // Render an in-pane PR detail card; the ↗ chip on the row opens GitHub.
    const githubPrCard = (state.conversationsData || []).find(x => x.id === id && x.source === 'github_pr');
    if (githubPrCard) {
      const html = await _renderGithubPrDetail(githubPrCard);
      if (state.currentConversation === id) $view.innerHTML = html;
      return;
    }
    // Pending spawn placeholders render the submitted prompt immediately.
    // Fire-and-watch engines switch to their spawn log once the POST returns
    // a real pid; Claude placeholders stay on the optimistic "Sending..." pane
    // until /api/sessions swaps in the durable transcript row.
    if (id.startsWith('spawning-')) {
      const c = (state.conversationsData || []).find(x => x.id === id);
      const resolvedId = resolvedSpawnConversationId(id);
      if (resolvedId !== id) {
        await (0, state.selectConversation)(resolvedId, fetchPaneId);
        return;
      }
      if (!c) return;
      const hasLogPid = typeof c.spawn_pid === 'number'
        || (c.source === 'antigravity' && /^[1-9]\d*$/.test(String(c.spawn_pid)));
      if (isSpawnLogPlaceholderSource(c.source) && hasLogPid) {
        await loadCodexLog(c);
        if (state.currentConversation !== id || c._landedResolved || c.spawn_failed) return;
        stopCodexLogPoller();
        if (c.source === 'antigravity') {
          // The focused composer pauses background intervals. This probe
          // follows the session the user just opened, like its live stream.
          const retry = async () => {
            const timer = state.codexLogPoller;
            if (state.currentConversation !== id) { stopCodexLogPoller(); return; }
            if (!_pollerOff('codexLog')) {
              _pollerTick('codexLog');
              await loadCodexLog(c);
            }
            if (state.codexLogPoller === timer && !c._landedResolved && !c.spawn_failed) {
              state.codexLogPoller = setTimeout(retry, 1500);
            }
          };
          state.codexLogPoller = setTimeout(retry, 1500);
          return;
        }
        state.codexLogPoller = setInterval(() => { if (_pollerOff('codexLog')) return;
          if (state.currentConversation !== id) { stopCodexLogPoller(); return; }
          _pollerTick('codexLog');
          loadCodexLog(c);
        }, 1500);
        return;
      }
      if (c && (c.pending_spawn || c.spawn_failed)) {
        renderPendingSpawnConversation(c, fetchPaneId);
        return;
      }
    }
    // Set once a saved copy (IndexedDB) is on screen for this open; the
    // catch below keeps it, marked, instead of blanking the pane.
    let _cachedPreview = null;
    let historyRequest = false;
    try {
      const _pane0 = paneByPaneId(fetchPaneId);
      const _wantFull = !!(_pane0 && _pane0.wantFull);
      const _loadBefore = _pane0 ? Number(_pane0.loadBeforeLine || 0) : 0;
      const _loadBeforeTail = _pane0 ? Number(_pane0.loadBeforeTail || 0) : 0;
      const _hop = !!(_pane0 && _pane0.loadAncestorHop);
      if (_pane0) _pane0.wantFull = false;   // one-shot: consumed on this fetch
      if (_pane0) _pane0.loadBeforeLine = 0; // one-shot: consumed on this fetch
      if (_pane0) _pane0.loadBeforeTail = 0; // one-shot: consumed on this fetch
      if (_pane0) _pane0.loadAncestorHop = 0; // one-shot: consumed on this fetch
      const _freshOpen = state.convLastLine === 0;
      if (_freshOpen && _pane0) _pane0.contSegments = null; // re-init chain on reopen
      const _segs = _pane0 ? _contEnsureSegments(_pane0, id) : [];
      const _hopSid = _hop ? _contNextParentForPane(_pane0, id) : '';
      const _loadingEarlier = !!_loadBefore || !!_hopSid;
      historyRequest = _loadingEarlier;
      // Earlier-window fetches walk the continuation chain: within the top
      // segment's own file first, then (hop) the previous session's tail.
      const _fetchSid = _hopSid || (_loadBefore && _segs.length ? String(_segs[0].sid) : id);
      const _url = _hopSid
        ? '/api/conversations/' + encodeURIComponent(_fetchSid) + '?tail=' + convTailLines()
        : _loadBefore
        ? '/api/conversations/' + encodeURIComponent(_fetchSid) + '?before=' + encodeURIComponent(_loadBefore) + '&tail=' + (_loadBeforeTail || convTailLines())
        : (_freshOpen && !_wantFull)
        ? '/api/conversations/' + id + '?tail=' + convFirstOpenLines()
        : '/api/conversations/' + id + '?after=' + state.convLastLine;
      let data = null;
      // Perf metric 2 detail: did this open serve from the pre-warmed tail
      // (kicked in selectConversation) or fall through to a fresh fetch?
      let _perfPrefetchHit = false;
      let _netP = null;
      let _cacheP = null;
      let _cacheEligible = false;
      if (_freshOpen && !_wantFull && !_loadingEarlier) {
        const _prefetchRow = (state.conversationsData || []).find(x => x.id === id)
          || (Array.isArray(state.archiveData) ? state.archiveData.find(x => (x.id || x.session_id) === id) : null);
        const _prefetchSource = state.sessionSourceByConv[id] || (_prefetchRow && _prefetchRow.source) || '';
        const prefetched = _takePrefetchedConversationTail(id);
        if (prefetched && _prefetchSource !== 'hermes') {
          _netP = prefetched;
          _perfPrefetchHit = true;
        }
        _cacheEligible = _convCacheEligible(id, _prefetchSource);
        if (_cacheEligible) _cacheP = _convCacheGet(id);
      }
      const _loadNet = async () => {
        if (_freshOpen && !_loadingEarlier) {
          return _fetchConvJsonTimed(_url, { cccPriority: 'user' }, true);
        }
        const res = await fetch(_url, _loadingEarlier ? {
          cccPriority: 'user',
          signal: AbortSignal.timeout(20000),
        } : undefined);
        if (!res.ok) throw new Error('Conversation request failed (' + res.status + ')');
        const payload = await res.json();
        if (payload.error) throw new Error(payload.error);
        return payload;
      };
      _netP = _netP ? _netP.then(d => d || _loadNet()) : _loadNet();
      if (_cacheP) {
        // Paint the saved copy only if it beats the network; the fresh
        // response below always replaces it.
        let _netDone = false;
        _netP = _netP.then(d => { _netDone = true; return d; }, e => { _netDone = true; throw e; });
        const cached = await Promise.race([
          _cacheP,
          _netP.then(() => null, () => null),
          new Promise(r => setTimeout(() => r(null), state.CONV_CACHE_READ_BUDGET_MS)),
        ]);
        const _pvHold = state._perfConvOpen
          ? state.CONV_CACHE_PAINT_HOLD_MS - (performance.now() - state._perfConvOpen.t0) : 0;
        if (cached && !_netDone && _pvHold > 0) {
          await Promise.race([
            _netP.then(() => null, () => null),
            new Promise(r => setTimeout(r, _pvHold)),
          ]);
        }
        const _pv = paneByPaneId(fetchPaneId);
        if (cached && !_netDone && state.convLastLine === 0 && _pv && _pv.conversationId === id
            && Array.isArray(cached.events) && cached.events.length) {
          const savedIdxPv = state.splitState.activeIndex;
          state.splitState.activeIndex = paneIndexByPaneId(fetchPaneId);
          try {
            if (cached.engine) {
              const _pvEl = convPaneElById(fetchPaneId);
              if (_pvEl) {
                _pvEl.classList.toggle('is-codex-session', cached.engine === 'codex');
                _pvEl.classList.toggle('is-webui-session', cached.engine === 'kimi' || cached.engine === 'codex');
              }
            }
            $view.innerHTML = '';
            const _pvOk = (0, state.renderConversationEvents)(cached.events, fetchPaneId, {
              initialLoad: true, isTruncated: cached.truncated_before, cachedPreview: true,
            }) !== false;
            if (_pvOk) {
              _markConvCachePreview($view, 'refreshing', cached.saved_at);
              noteAppEmbedDrawn(id, true);
              _cachedPreview = { saved_at: cached.saved_at };
              if (state._perfConvOpen && state._perfConvOpen.id === id && state._perfConvOpen.paneId === fetchPaneId
                  && !state._perfConvOpen.cachedPainted) {
                state._perfConvOpen.cachedPainted = true;
                const _pvEnd = performance.now();
                _cachedPreview.ms = Math.round(_pvEnd - state._perfConvOpen.t0);
                _perfEvent('conv_open_cached', _perfActiveElapsed(state._perfConvOpen.t0, _pvEnd), {
                  conv_id: id,
                  detail: {
                    events: cached.events.length,
                    engine: cached.engine || state.sessionSourceByConv[id] || '',
                    age_ms: Math.max(0, Date.now() - (cached.saved_at || Date.now())),
                    wall_ms: _cachedPreview.ms,
                  },
                });
              }
            }
          } catch (_) {
            $view.innerHTML = '';
          } finally {
            state.splitState.activeIndex = savedIdxPv;
          }
        }
      }
      data = await _netP;
      if (!data || data.error) throw new Error((data && data.error) || 'Conversation response unavailable');
      const _perfDataAt = performance.now();
      // Tail is in hand: release the parked select-time fetches once this
      // render has painted (see _afterConvTail).
      if (typeof _flushAfterConvTailAfterPaint === 'function') _flushAfterConvTailAfterPaint();
      // Guard: if the pane's conv id shifted (e.g. user navigated away
      // while the fetch was in-flight), discard the stale response.
      const currentPane = paneByPaneId(fetchPaneId);
      if (!currentPane || currentPane.conversationId !== id) return;
      if (!_loadingEarlier && !_freshOpen && currentPane.antigravityProvisional
          && !data.antigravity_provisional) {
        return (0, state.selectConversation)(id, fetchPaneId);
      }
      // The server authoritatively detects the engine while parsing. View
      // paths that opened the conversation WITHOUT a list row (popout URLs
      // missing `source`, synthetic rows defaulting to 'interactive') set
      // is-webui-session wrong at select time — correct it here, before the
      // first render picks a renderer.
      if (data && typeof data.engine === 'string' && data.engine) {
        const _enginePaneEl = convPaneElById(fetchPaneId);
        if (_enginePaneEl) {
          _enginePaneEl.classList.toggle('is-codex-session', data.engine === 'codex');
          _enginePaneEl.classList.toggle('is-webui-session', data.engine === 'kimi' || data.engine === 'codex');
        }
      }
      // The server's cloud marker is authoritative too: a popout/URL-opened
      // cloud thread may not have a list row behind it, so its composer flag
      // arrives here with the transcript payload instead of at select time.
      if (data && data.codex_cloud === true && state.currentSession && state.currentSession.id === id
          && !state.currentSession.codex_cloud) {
        state.currentSession.codex_cloud = true;
        try { updateInputBar(); } catch (_) {}
      }
      // Re-anchor activeIndex to fetchPaneId — the user may have clicked
      // another conv (in either pane) while this fetch was in-flight, shifting
      // splitState.activeIndex away. Mirror the savedIdx/try/finally pattern
      // used in startConvStream's onmessage handler.
      const savedIdx = state.splitState.activeIndex;
      state.splitState.activeIndex = paneIndexByPaneId(fetchPaneId);
      try {
        if (state.convLastLine === 0 && !state.codexSpawnPreviews.has(id)) {
          $view.innerHTML = '';
        }
        // Remember the fetched segment's own continuation parent (from the
        // transcript marker, server-side) so the next scroll-up can hop to
        // it — works even for sessions absent from every loaded list.
        if (_segs.length && data && typeof data.continued_from_session_id === 'string'
            && data.continued_from_session_id) {
          const _seg = _segs.find(s => String(s.sid) === String(_fetchSid));
          if (_seg) _seg.nextParent = data.continued_from_session_id;
        }
        if (_hopSid) {
          // Stitch the previous session above, with a seam marker between
          // the two segments.
          const _childSid = _segs.length ? String(_segs[0].sid) : '';
          _segs.unshift({
            sid: _hopSid,
            nextParent: String((data && data.continued_from_session_id) || ''),
          });
          if (Array.isArray(data.events)) {
            data.events.push({
              // Negative line: real JSONL lines start at 1, and each seam
              // needs a unique value so the data-jsonl-line dedup above
              // never swallows a second boundary.
              type: 'system', subtype: 'ccc_segment', line: -(1000 + _segs.length), ts: '',
              parent_sid: _hopSid, child_sid: _childSid,
              parent_title: _contSegmentTitle(_hopSid),
              child_title: _contSegmentTitle(_childSid),
              parent_cfg: _contSegmentConfig(_hopSid),
              child_cfg: _contSegmentConfig(_childSid),
            });
          }
        }
        if (_loadingEarlier) {
          // A before= window reports the file's full length, including
          // unseen new lines. Only an initial or live read can commit it.
          _prependConversationEvents(data.events, fetchPaneId);
          const pane = paneByPaneId(fetchPaneId);
          if (pane) {
            const emptyEvents = !Array.isArray(data.events) || data.events.length === 0;
            if (emptyEvents && !_hopSid) {
              // No renderable events remain in this segment. Pin firstLine
              // to 1 so the next Load-earlier click (or the same-click hop
              // in loadEarlier) fetches the continuation parent instead of
              // walking before=N-1 on empty windows.
              pane.firstLine = 1;
            } else {
              pane.firstLine = data.first_line || pane.firstLine || 0;
            }
          }
        } else {
          const _perfRenderAt = performance.now();
          const _renderOk = (0, state.renderConversationEvents)(data.events, fetchPaneId, { initialLoad: _freshOpen, isTruncated: data.truncated_before }) !== false;
          if (_renderOk) {
            state.convLastLine = Math.max(state.convLastLine, data.last_line || 0);
            currentPane.antigravityProvisional = !!data.antigravity_provisional;
          }
          if (_renderOk && _freshOpen && !data.error) noteAppEmbedDrawn(id, false);
          if (_renderOk && _cacheEligible) _convCachePutSoon(id, data);
          if (_renderOk && _freshOpen && data.truncated_before && data.first_prompt) {
            _renderFirstPromptPin($view, data.first_prompt, fetchPaneId);
          }
          // Perf metric 2 end (conv_open): only the initialLoad render for the
          // click still pending in _perfConvOpen fires — a poll/backfill
          // re-render of the same pane (opts.initialLoad false, or a click
          // that already fired/was superseded) does not.
          if (_freshOpen && _renderOk && state._perfConvOpen
              && state._perfConvOpen.id === id && state._perfConvOpen.paneId === fetchPaneId) {
            try {
              const _perfEnd = performance.now();
              const _perfWall = _perfEnd - state._perfConvOpen.t0;
              const _perfActive = _perfActiveElapsed(state._perfConvOpen.t0, _perfEnd);
              const _perfOpenEvent = {
                conv_id: id,
                detail: {
                  events: Array.isArray(data.events) ? data.events.length : 0,
                  prefetch_hit: _perfPrefetchHit,
                  // conv_open stays click -> FRESH render; the saved-copy
                  // paint is its own kind (conv_open_cached).
                  cached_preview_ms: _cachedPreview ? (_cachedPreview.ms ?? null) : null,
                  truncated: !!data.truncated_before,
                  wall_ms: Math.round(_perfWall),
                  hidden_ms: Math.round(_perfWall - _perfActive),
                  // Per-phase split (CCC-143). Offsets are ms from the click;
                  // fetch_at < 0 means a hover/restore prefetch started first.
                  ..._convOpenPhases(data, state._perfConvOpen.t0, _perfDataAt, _perfRenderAt, _perfEnd, id),
                },
              };
              _convOpenAfterPaint(state._perfConvOpen.t0, (paintMs) => {
                _perfOpenEvent.detail.paint_ms = paintMs;
                _perfEvent('conv_open', _perfActive, _perfOpenEvent);
              });
              if (typeof window.__cccPerfRow === 'function') {
                window.__cccPerfRow('open', {
                  at: Date.now(),
                  text: '✓ ' + Math.round(_perfWall) + 'ms ' + String(id).slice(0, 8),
                  ms: Math.round(_perfWall),
                  title: 'click→render ' + Math.round(_perfWall) + 'ms wall, '
                    + Math.round(_perfActive) + 'ms active'
                    + (_perfPrefetchHit ? ' (prefetch hit)' : '')
                    + ' · ' + id,
                });
                if (window.__cccPerfClick) window.__cccPerfClick.delete(String(id));
              }
            } catch (_) {}
            state._perfConvOpen = null;
          } else if (_freshOpen && _renderOk
              && window.__cccPerfClick && window.__cccPerfClick.has(String(id))) {
            // The strict conv_open metric didn't match this open (superseded
            // click, programmatic open, an entry point that never set
            // _perfConvOpen) but a fresh-load render DID land — the delegated
            // click timer still closes it so the pane never stays silent.
            try {
              const _ct = window.__cccPerfClick.get(String(id));
              window.__cccPerfClick.delete(String(id));
              if (typeof window.__cccPerfRow === 'function' && Number.isFinite(_ct)) {
                const _ms = Math.round(performance.now() - _ct);
                if (_ms >= 0 && _ms < 5 * 60 * 1000) {
                  window.__cccPerfRow('open', {
                    at: Date.now(),
                    text: '✓ ' + _ms + 'ms ' + String(id).slice(0, 8),
                    ms: _ms,
                    title: 'click→render ' + _ms + 'ms · ' + id,
                  });
                }
              }
            } catch (_) {}
          }
        }
        // Live after= polls must not clobber the history cursor. Their
        // first_line is the start of the new tail slice (or absent), which
        // would rewind Load-earlier back into the already-loaded window.
        if (!_loadingEarlier && _freshOpen) {
          const pane = paneByPaneId(fetchPaneId);
          if (pane) pane.firstLine = data.first_line || pane.firstLine || 0;
        }
        // The banner stays available while EITHER the top segment has more
        // of its own file OR a continuation parent remains to hop to.
        const _contMore = !!_contNextParentForPane(_pane0, id);
        if (_freshOpen && !_wantFull && data && (data.truncated_before || _contMore)) {
          _insertLoadEarlierBanner($view, id, fetchPaneId);
        } else if (_loadingEarlier && data && (data.truncated_before || _contMore)) {
          _insertLoadEarlierBanner($view, id, fetchPaneId);
        }
        // Two-stage open: the first window was CONV_TAIL_FIRST_LINES; fill
        // the rest of the usual window in above it once this paint is out.
        if (_freshOpen && !_wantFull && data && data.truncated_before
            && convFirstOpenLines() < convTailLines()) {
          _scheduleConvOpenBackfill(id, fetchPaneId, convTailLines() - convFirstOpenLines());
        }
        restorePendingSendEchoes(id, fetchPaneId);
        // Codex writes its rollout lazily: measured, the user's own message
        // does not reach disk for ~15s after the turn is accepted, and CCC
        // renders the transcript FROM that file. So a freshly spawned session
        // showed an empty pane (just "Thinking...") even though CCC has held
        // the prompt since the moment it was submitted. Echo it optimistically
        // until the real transcript arrives -- the next poll replaces this.
        if ((!Array.isArray(data.events) || !data.events.length) && state.convLastLine === 0
            && !$view.querySelector('.event.user_text.pending, [data-live-key]')) {
          const echo = optimisticPromptForSession(id);
          if (echo) {
            $view.innerHTML = '<div class="event user_text pending">'
              + '<span class="label">User</span>'
              + '<div class="user-msg" dir="auto" data-raw-text="' + (0, state.escapeAttr)(echo) + '">'
              + (0, state.linkifyPastedImages)((0, state.escapeHtml)(echo)) + '</div>'
              + '<div class="optimistic-echo-note">Sent. Waiting for the engine to write its transcript&hellip;</div>'
              + '</div>';
          }
        }
        // First real content on screen for this session -- the moment the
        // wait actually ends from the user's point of view.
        if (Array.isArray(data.events) && data.events.length) {
          spawnStatsMark(id, 'first_paint');
          markSpawnAssistantPaint(id, $view, fetchPaneId);
          const renderedPane = paneByPaneId(fetchPaneId);
          const renderedSid = (renderedPane && renderedPane.currentSession
            && renderedPane.currentSession.id) || id;
          if (state.claudeSpawnAwaitingFirstPaint.has(renderedSid)
              && $view.querySelector('.event.assistant .assistant-text')) {
            markClaudeFirstVisibleOutput(renderedSid);
          }
        }
        renderSpawnStatsPanel(id, $view);
      } finally {
        state.splitState.activeIndex = savedIdx;
      }
      // Re-fetch session usage whenever new events landed. Usage rollups
      // arrive sporadically (in some session paths only the final
      // assistant event of each turn carries the `usage` block — many
      // delta events in between have none), so the initial selection-
      // time fetch can come back empty and the user sees no context %
      // until they reselect. Tying this to event arrival means the pill
      // updates as soon as the next turn lands.
      if (data.events && data.events.length > 0) {
        const sid = (state.conversationsData.find(x => x.id === id) || {}).session_id || id;
        fetchSessionUsage(sid);
      }
    } catch (err) {
      if (historyRequest) throw err;
      // A rejected old request has the same ownership boundary as a
      // successful response: it must not replace the newly selected pane.
      const failedPane = paneByPaneId(fetchPaneId);
      if (!failedPane || failedPane.conversationId !== id) return;
      if (_cachedPreview && state.convLastLine === 0 && $view.querySelector(':scope > .conv-cache-preview')) {
        _markConvCachePreview($view, 'failed', _cachedPreview.saved_at);
      } else if (state.convLastLine === 0) {
        $view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Failed to load conversation: ' + (0, state.escapeHtml)(err.message) + '</div>';
      }
    }
  }
    return { fetchConversationEvents, startConvStream, stopConvStream };
  }
  root.CCCConversationTransport = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
