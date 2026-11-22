/* Shared transcript event renderer with explicit live-state and control adapters. */
(function (root) {
  'use strict';
  function createRenderer({ state, text, helpers }) {
    const { escapeAttr, escapeHtml, linkifyPastedImages, truncate, parseTaskNotificationBlock, renderTaskNotificationBlock, renderMarkdown, renderDevinSummaryBlock, _linkifyEscapedUrls } = text;
    const { activePaneId, paneByPaneId, _ultraFreshFeed, getConvViewForPane, _refreshTokenEconomizerAnnotation, _viewIsWebUiPane, isConversationAtBottom, resetTtsOnNewTurn, formatModelEffort, rowReasoningEffort, _freeServedText, _kimiRegroupTools, isRoutineCodexCoordination, _confirmedResultForTurn, _removeStaleProvisionalsForTurn, queuedRowDeliveryId, _turnOrdinalBefore, eventStamp, nowStamp, _isOriginalAskText, convRowForPane, cleanIssuePrompt, originalAskTextForEvent, syncMobileOriginalAsk, _uxFixesHandledTicketsForRow, _uxFixesQueueProgressForRow, splitFirstSentence, renderImageDescriptors, breadcrumbSpawnedByChipHtml, pastedImageThumbsHtml, _applyStatusRailLayout, _dynAskSchedule, _updateStickyAskSlots, renderSessionTimelineIntoSticky, renderSessionWorkspaceIntoSticky, renderIssueInConvPane, repoPathForIssueNumber, tsSpan, _formatCompactDuration, _formatTokens, _compactEngineLabel, clearSessionSending, syncPendingSendsMapForConv, _stripKimiControlXml, syncPaneLastUserMessage, renderCompactResumeCard, completeCompactRun, _compactRunAnchorTo, userMessageSteerHtml, renderSkillLoadedCard, renderDevinHandoffCard, renderCodexUserText, formatPeerMessageHtml, debugModeEnabled, _convPaneSeedTaskInfo, _kimiToolRowHtml, _kimiThinkingHtml, renderCodexSkillLoadedCard, _planEntriesHtml, _turnDiffBlockHtml, _imageGenerationBlockHtml, assistantMessageActionsHtml, _formatAntigravityTokenChips, _cacheAdjustedInlineHtml, _kimiAppendAssistantEvent, attachPresentationArtifactToAssistant, toolDisplayName, toolBlockSource, formatToolCallDetail, renderToolInputDisclosure, renderToolCommandDisclosure, renderEditDisclosure, _ccOrchScan, isFileToolName, inlineToolImageHtml, _compactRunAdoptDevinSummary, _cacheMissCallout, _recordCacheMissLog, _isNoopAckText, loadToolInputDisclosure, _resultOutcomeInfo, _convPaneMarkTaskCompleted, eventTextString, _ccOrchResultLabels, _kimiUpdateToolGroupHead, _loadCompleteToolOutput, _kimiToolPeekHtml, _toolResultImageKey, _toolResultImagesCardHtml, toolCallForResult, isSuccessfulCommandToolResult, isRoutineSuccessfulToolResult, stampCurrentToolGroup, updateToolGroupLabel, renderToolCodePreview, toolResultOutputLabel, _codexRequestCardHtml, convVerboseOn, _lastRealTranscriptChild, toolGroupCarriesConversationContext, toolCallLooksLikeCodeRead, _upsertProvisionalNode, _findMatchingProvisionalNode, _convLiveRevealNewText, _kimiFlushRegroups, _convPaneSyncJsonlTaskTabs, updateLiveToolStrip, updateCodexStateBadge, clearOptimisticAgentIndicator, clearLiveGeneratingIndicator, fetchSessionUsage, _compactRunClaimSummary, _compactRunMount, _compactRunAdoptBoundary, _foldCompactRecords, _reanchorToTailUnlessOnlyTransientBetween, syncQueuedSteerTray, takeQueuedSendAcknowledgement, _anchorWakeBreakdown, updateSessionOutcomeBanner, _syncThinkingRuns, _ccMarkRuns, anchorCodexStateBadge, updateConversationEndAffordance, scrollConversationToEnd, ffcRefreshForCurrent, _firePhoneModeReadIfDone, _ensureFirstUserPinned, _syncLiveQuestionDuplicateHide, syncUserMessageSteerButtons, _convReplaySweepNewItems, refreshPresentationForPane, _reconcileAnnotationsForPane, markSpawnAssistantPaint, convPaneElById } = helpers;
  let resultOverflowObserver;
  function addResultOverflowToggles(outputs) {
    // ResizeObserver delivers after layout. Keep all reads ahead of the writes.
    const overflowing = outputs.filter(output => output.isConnected
      && output.scrollHeight > output.clientHeight + 1);
    for (const output of overflowing) {
      const expandBtn = document.createElement('button');
      expandBtn.type = 'button';
      expandBtn.className = 'tool-result-expand-toggle';
      expandBtn.setAttribute('data-toggle-tool-result', '');
      expandBtn.textContent = 'Show full result';
      output.insertAdjacentElement('afterend', expandBtn);
    }
  }
  function renderConversationEvents(events, paneId, opts) {
    if (!Array.isArray(events)) return true;  // defensive: backlog/unknown responses
    // Do not defer transcript rendering while the composer is focused.
    // Steer/send keeps focus in the input; deferring used to advance
    // lastLine without painting those events, so Codex replies vanished
    // until a full reselect (and could stay missing if blur never fired).
    paneId = paneId || activePaneId();
    opts = opts || {};
    const pane = paneByPaneId(paneId);
    const renderedConversationId = (pane && pane.conversationId) || state.currentConversation || '';
    // A saved-copy preview is old data: never feed it to live freshness.
    if (!opts.isTruncated && !opts.cachedPreview) _ultraFreshFeed((pane && pane.currentSession && pane.currentSession.id) || renderedConversationId, events);
    if (pane && opts.isTruncated) {
      pane.firstUserMsgRendered = true;
    }
    const $view = getConvViewForPane(paneId) || state.$conversationsView;
    // Real data replaces a saved-copy preview (CCC-143) wholesale -- e.g. a
    // stream/poll landing after the refresh fetch failed -- so stale rows
    // and the "saved copy" badge never mix with live ones.
    if (!opts.cachedPreview && $view.querySelector(':scope > .conv-cache-preview')) {
      $view.innerHTML = '';
    }
    if (!opts.cachedPreview) _refreshTokenEconomizerAnnotation(paneId);
    // Webui panes (kimi + codex) render kimi-web style: merged turns,
    // right-aligned user bubbles, ToolGroup cards. Gated here so the Claude
    // path below stays byte-for-byte the shared legacy renderer.
    const _kimiPane = _viewIsWebUiPane($view);
    const _codexPane = !!($view.closest && $view.closest('.conv-pane.is-codex-session'));
    // Stick-to-bottom only when the user is *already* near the bottom.
    // If they've scrolled up to read, leave the scroll position alone so
    // newly-streamed events don't yank them back down. 80px tolerance is
    // generous enough to absorb typical line-height jitter.
    // History paging renders into a temporarily empty view. That is not a
    // fresh open: bottom-follow and saved-open restores belong to the reader
    // already on screen, whose position the prepend restores after rendering.
    const wasAtBottom = !opts.prepending && isConversationAtBottom($view);
    // Measure tool output after layout, including history revealed later.
    // Reading scrollHeight while building the transcript forces expanded
    // tool layouts that the final folding pass immediately invalidates.
    const _overflowChecks = [];
    // Visual "Clear" watermark (CCC-474): events at or below this jsonl line
    // were wiped from the screen for recording — keep them hidden if a poll
    // or load-earlier re-renders them. Purely cosmetic; see
    // clearPaneScreenForVideo.
    const _videoClearLine = parseInt($view.dataset.videoClearLine || '0', 10) || 0;
    // Detect a NEW assistant turn arriving (one whose JSONL line isn't
    // already rendered). If found, reset TTS so the play/pause button
    // re-arms to read the new message on next click instead of resuming
    // the prior message mid-sentence.
    // Skip TTS rearm on initial load (view just cleared and re-populated on
    // conv select). We only want to stop playback when a genuinely NEW turn
    // streams in while the user is already watching — not when every event
    // "appears new" because the DOM was freshly wiped.
    if (!(opts && opts.initialLoad) && typeof resetTtsOnNewTurn === 'function') {
      for (const ev of events) {
        if (ev.type !== 'assistant' && ev.type !== 'result') continue;
        // Provisional (live_key) rows have no jsonl line, so the "already
        // rendered?" check below can't recognize a steady-state re-poll of
        // the same in-progress item as already-seen -- every overlay tick
        // would otherwise look like a brand-new turn and keep cutting off
        // playback. The eventual line-keyed rollout row for the same turn
        // still triggers a proper rearm once it's written.
        if (ev.live_key != null) continue;
        if (ev.line != null) {
          const escLineCheck = (window.CSS && CSS.escape) ? CSS.escape(String(ev.line)) : String(ev.line);
          if ($view.querySelector('.event[data-jsonl-line="' + escLineCheck + '"]')) continue;
        }
        // Scope the rearm to the conversation being rendered. A fresh turn in
        // a DIFFERENT conversation (e.g. the user navigated away while a read
        // is playing) must not cancel the in-progress utterance — that's the
        // whole point of decoupling playback from focus.
        resetTtsOnNewTurn(state.currentConversation);
        break;
      }
    }
    function whatsappBridgeSenderHtml(ev) {
      if (!ev || ev.subtype !== 'hermes_whatsapp_bridge') return '';
      const sender = String(ev.sender_name || ev.pushName || ev.sender_id || '').trim();
      if (!sender) return '';
      const title = ev.chat_id ? ('WhatsApp sender in ' + ev.chat_id) : 'WhatsApp sender';
      return '<div class="whatsapp-bridge-sender" title="' + escapeAttr(title) + '">' + escapeHtml(sender) + '</div>';
    }
    // Chip for messages that arrived over Claude Code's cross-session peer
    // mesh (SendMessage / uds sockets). Server tags these events with
    // ev.peer = {name, from, from_mode, msg_id}. from_mode is the sender's
    // attested permission mode; blank when the sender did not attest.
    function peerSenderHtml(ev) {
      const attr = ev && ev.attribution;
      if (attr) {
        const author = attr.author || {};
        if (author.kind === 'human' || author.kind === 'unknown') return '';
        const name = author.name_at_send || author.id || 'Unattributed sender';
        const via = [...new Set((attr.relay || []).map(h => h.via).filter(Boolean))].join(' → ');
        const basis = String(attr.basis || 'unknown');
        const node = ev.attribution_local ? '' : (' · node ' + (author.node || 'unknown'));
        const reply = attr.reply_to ? 'reply route available' : 'no reply path';
        return '<span class="peer-sender" title="' + escapeAttr('Attribution, not authentication · ' + reply + (attr.reply_to ? ' (' + (attr.reply_to.addr || attr.reply_to.route_token || '') + ')' : '')) + '">'
          + (ev.attribution_session_id ? '<a class="peer-sender-name" href="?session=' + encodeURIComponent(ev.attribution_session_id) + '">' + escapeHtml(name) + '</a>' : '<span class="peer-sender-name">' + escapeHtml(name) + '</span>')
          + '<span class="peer-sender-mode">' + escapeHtml([author.engine, basis].filter(Boolean).join(' · ') + node) + '</span>'
          + (via ? '<span class="peer-sender-mode">' + escapeHtml('via ' + via) + '</span>' : '')
          + '<span class="peer-sender-mode">' + escapeHtml(reply) + '</span>'
          + '</span>';
      }
      const peer = ev && ev.peer;
      if (!peer || typeof peer !== 'object') return '';
      const name = String(peer.name || '').trim() || String(peer.from || '').replace(/^uds:/, '') || 'Unattributed legacy sender';
      const mode = String(peer.from_mode || '').trim();
      const title = 'Sender-provided name; attribution unavailable for this legacy/native message'
        + (peer.from ? ' (' + peer.from + ')' : '')
        + (mode ? ', sender mode: ' + mode : '');
      return '<span class="peer-sender" title="' + escapeAttr(title) + '">'
        + '<span class="peer-sender-icon" aria-hidden="true">&#8644;</span>'
        + '<span class="peer-sender-mode">unattributed · legacy/native</span>'
        + '<span class="peer-sender-name">' + escapeHtml(name) + '</span>'
        + (mode ? '<span class="peer-sender-mode">' + escapeHtml(mode) + '</span>' : '')
        + '</span>';
    }
    function eventModelMetaHtml(ev, role) {
      if (!ev || !ev.model) return '';
      const bits = [];
      const prefix = role === 'assistant' ? 'Used' : 'Sent to';
      bits.push(prefix + ' ' + formatModelEffort(ev.model, rowReasoningEffort(ev), { style: 'verbose' }));
      const freeMap = role === 'assistant' && ev.tokens_in ? state._freeServedBySid[renderedConversationId] : null;
      const freeText = freeMap ? _freeServedText(freeMap[String(ev.tokens_in)]) : '';
      const text = freeText || bits.join(' · ');
      return '<div class="event-model-meta"' + (ev.tokens_in ? ' data-tin="' + escapeAttr(String(ev.tokens_in)) + '"' : '')
        + ' title="' + escapeAttr(text) + '">' + escapeHtml(text) + '</div>';
    }
    function ambientContextHtml(context) {
      if (!context || !context.text) return '';
      const source = String(context.source || 'in-app-browser');
      const label = source === 'ambient-ui-state' ? 'In-app browser context' : 'Injected context';
      return '<details class="codex-ambient-context">'
        + '<summary title="Context automatically supplied by Codex Desktop; it was not typed as part of the request.">'
        + '<span class="codex-ambient-context__label">' + escapeHtml(label) + '</span>'
        + '<span class="codex-ambient-context__source">' + escapeHtml(source) + '</span>'
        + '</summary>'
        + '<pre class="codex-ambient-context__body">' + escapeHtml(String(context.text)) + '</pre>'
        + '</details>';
    }
    const _ownsRegroupBatch = !!opts.initialLoad && !_kimiRegroupTools.batch;
    if (_ownsRegroupBatch) _kimiRegroupTools.batch = new Set();
    try {
    for (const sourceEvent of events) {
      // A transport hop does not make ordinary dashboard input a peer
      // message. Keep its evidence, but use normal input rendering unless
      // the envelope identifies an external session or service.
      const authorKind = sourceEvent.attribution && sourceEvent.attribution.author && sourceEvent.attribution.author.kind;
      const ordinaryInput = authorKind === 'human' || authorKind === 'unknown';
      // Old transcript rows can contain the silence check's delivery header
      // without separate sender metadata. Normalize this exact message for display.
      const legacySilenceCheck = sourceEvent.type === 'user_text'
        && authorKind !== 'human'
        && /^Announced from: CCC silence check\r?\n\r?\n\?\s*$/.test(sourceEvent.text || '');
      const ev = legacySilenceCheck
        ? { ...sourceEvent, text: '?', peer: sourceEvent.peer || { name: 'CCC silence check' } }
        : ordinaryInput ? { ...sourceEvent, peer: null } : sourceEvent;
      if (isRoutineCodexCoordination(ev)) continue;
      // Provisional (live_key) events never carry a real jsonl `line` — they
      // upsert in place (see _upsertProvisionalNode below) instead of taking
      // the confirmed-row dedupe-skip a line-keyed event gets.
      let _existingProvisionalNode = null;
      if (ev.live_key != null) {
        const escLiveKey = (window.CSS && CSS.escape) ? CSS.escape(String(ev.live_key)) : String(ev.live_key);
        _existingProvisionalNode = $view.querySelector('.event[data-live-key="' + escLiveKey + '"]');
        // Reverse of the confirmed-after-provisional reconcile below: the
        // overlay can still serve a turn whose completion the rollout has
        // already recorded (opening a stuck/ended session re-fetches the
        // overlay after the transcript rendered). Rendering those items
        // duplicates the finished turn as ghost rows, so drop them — and
        // sweep any copies a previous pass already inserted for the same
        // stale turn.
        const _overlayTurnId = ev.turn_id != null ? String(ev.turn_id)
          : (String(ev.live_key).indexOf(':') !== -1 ? String(ev.live_key).split(':')[0] : null);
        if (_overlayTurnId != null && _confirmedResultForTurn($view, _overlayTurnId)) {
          _removeStaleProvisionalsForTurn($view, _overlayTurnId);
          continue;
        }
      } else if (ev.line != null) {
        const escLine = (window.CSS && CSS.escape) ? CSS.escape(String(ev.line)) : String(ev.line);
        if ($view.querySelector('.event[data-jsonl-line="' + escLine + '"]')) continue;
      } else if (ev.pending && ev.type === 'user_text') {
        // Queue-overlay rows carry neither line nor live_key, so the keyed
        // dedupe above cannot see them — the SAME parked queue entry would
        // append a duplicate row on every poll. Dedupe by normalized text
        // against both the transcript and the queued-steer tray (the server
        // queue itself dedupes by text, so identical words are one entry).
        // A queue-snapshot render (see the syncQueuedSteerTray call below)
        // drops every server card already in the tray as stale, so skipping
        // this row against the tray emptied it on every other poll (CCC-246).
        // Render it fresh then; the sync swaps it in for the old card.
        const _queuedText = state._normSend(ev.text);
        const _queuedId = String(ev.delivery_id || '');
        const _queuedScope = $view.closest('.conv-pane');
        const _replacesTray = !opts.provisionalOverlay && !opts.prepending
          && !!(opts.initialLoad || opts.queueSnapshot !== false);
        const _queuedDup = [!_replacesTray && _queuedScope && _queuedScope.querySelector('.queued-steer-tray'), $view]
          .some(scope => scope && [...scope.querySelectorAll('.event.user_text[data-queued-steer-server]')]
            .some(el => {
              // Two queued sends with the same words are two messages once
              // the server names them; only id-less rows fall back to text.
              const elId = queuedRowDeliveryId(el);
              if (_queuedId && elId) return elId === _queuedId;
              const msg = el.querySelector('.user-msg');
              return msg && state._normSend(msg.getAttribute('data-raw-text') || msg.textContent) === _queuedText;
            }));
        if (_queuedDup) continue;
      }
      if (ev.type === 'chat_bridge_row') continue; // Only Share reads this row.
      const div = document.createElement('div');
      div.className = 'event ' + ev.type + (ev.pending ? ' pending' : '');
      // Fusion (devin-cli) events carry actor 'lead'|'sidekick' — the lane
      // class tints the turn and drives the actor chip below.
      if (ev.actor === 'sidekick' || ev.actor === 'lead') {
        div.classList.add('event-actor-' + ev.actor);
      }
      if (ev.pending) {
        div.dataset.queuedSteerServer = 'true';
        // A durable server-queue row is "queued", not "sending": the
        // message is safe and will go out when the engine's running turn
        // ends. Say so, with the engine's own reason when it gave one.
        div.classList.add('server-queued');
        if (ev.queued_reason) div.dataset.queuedReason = String(ev.queued_reason);
        if (ev.delivery_id) div.dataset.deliveryId = String(ev.delivery_id);
        // Not typed in the composer: another session, WatchTower or a
        // service sent it. The tray labels and borders these differently.
        if (ev.queued_from && typeof ev.queued_from === 'object') {
          div._queuedFrom = ev.queued_from;
          div.classList.add('queued-external');
        }
      }
      if (ev.line != null) div.dataset.jsonlLine = String(ev.line);
      if (ev.diagnostic) div.dataset.diagnostic = '1';
      if (ev.turn_id != null) div.dataset.turnId = String(ev.turn_id);
      if (ev.codex_item_id != null) div.dataset.codexItemId = String(ev.codex_item_id);
      if (ev.live_key != null) {
        div.dataset.liveKey = String(ev.live_key);
        div.dataset.provisional = 'true';
        div.classList.add('provisional');
        if (ev.turn_id != null) div.dataset.turnOrdinal = String(_turnOrdinalBefore($view, String(ev.turn_id)));
      }
      if (_videoClearLine && ev.line != null && Number(ev.line) <= _videoClearLine) {
        div.classList.add('ccc-video-cleared');
      }

      // Use the event's own JSONL timestamp (when it was actually written)
      // rather than render time; fall back to render time only when the
      // event has no ts (synthetic / very old logs).
      div.dataset.renderTs = eventStamp(ev.ts) || nowStamp();
      // Epoch of the event's own JSONL timestamp — lets the outcome banner
      // tell a genuinely-stalled session from one that's just between turns
      // (the freshest epoch is how long ago the transcript last moved).
      const _evEpoch = ev.ts ? Date.parse(ev.ts) : NaN;
      if (!isNaN(_evEpoch)) div.dataset.tsEpoch = String(_evEpoch);
      // Tag assistant rows with the source message_id so the streaming
      // bubble can detect "JSONL already won" and hand off cleanly. Remember
      // that handoff so the authoritative row appears fully formed instead
      // of replaying text the user already read in the streaming bubble.
      let handedOffStreamingBubble = false;
      if (ev.type === 'assistant' && ev.message_id) {
        // .has(), not .delete(): "already shown live" must survive this
        // row being rebuilt later (pane refresh, resync, scroll-reload of
        // history) — a one-time consume protected only the FIRST render
        // pass, so any later rebuild re-triggered the word-by-word reveal
        // on text the user already read live. Bounded by
        // MAX_STREAM_HANDOFF_MARKERS / conv-switch clear, so retaining
        // entries here doesn't leak.
        if ($view._streamedAssistantMessageIds
            && $view._streamedAssistantMessageIds.has(ev.message_id)) {
          handedOffStreamingBubble = true;
        }
        div.dataset.msgId = ev.message_id;
        const escId = (window.CSS && CSS.escape) ? CSS.escape(ev.message_id) : ev.message_id;
        const liveBubble = $view.querySelector('.stream-bubble[data-msg-id="' + escId + '"]');
        if (liveBubble) {
          handedOffStreamingBubble = true;
          if (liveBubble.parentNode) liveBubble.parentNode.removeChild(liveBubble);
          if (state._streamingBubble === liveBubble) {
            state._streamingBubble = null;
            state._streamingMsgId = null;
          }
        }
      }

      // Make the first user message sticky at the top
      if (ev.type === 'user_text' && !legacySilenceCheck && !state._firstUserMsgRendered) {
        state._firstUserMsgRendered = true;
        // The first user message lives permanently in the sticky header as
        // "Original ask" — hide its in-conversation bubble so we don't show
        // the same text twice (sticky + bubble) when no scrolling has
        // happened yet. The dynamic-ask tracker still walks DOM and treats
        // index 0 as "viewing the original," so the hidden node is harmless.
        if (_isOriginalAskText(ev.text, paneId)) div.classList.add('is-pinned-in-sticky');
        let sticky = $view.querySelector('.conv-sticky-header');
        if (!sticky) {
          sticky = document.createElement('div');
          sticky.className = 'conv-sticky-header';
          sticky.style.position = sticky.style.position || 'sticky';
          const hidden = localStorage.getItem('hideToolCalls') === '1';
          if (hidden) $view.classList.add('hide-tools');
          // Resolve linked issue number for THIS pane's conversation (not the
          // globally-active one) so the issue link + resolve button stamp the
          // right session/issue in split panes and mid-switch.
          const conv = convRowForPane(paneId) || {};
          let issueNum = conv.linked_issue || conv.issue_number || '';
          if (!issueNum) {
            const dm = /^issue-(\d+)$/.exec(conv.display_name || '');
            if (dm) issueNum = dm[1];
          }
          const issueBtn = issueNum
            ? '<button class="tools-toggle issue-link-btn" data-issue="' + escapeHtml(String(issueNum)) + '" title="View GitHub issue #' + escapeHtml(String(issueNum)) + '" style="right:120px;color:var(--green);border-color:rgba(63,185,80,0.4);">Issue #' + escapeHtml(String(issueNum)) + ' &#x2197;</button>'
            : '';
          const resolveBtn = issueNum
            ? '<button class="tools-toggle resolve-btn" data-issue="' + escapeHtml(String(issueNum)) + '" data-session="' + escapeHtml(conv.session_id || '') + '" title="Commit changes and close issue #' + escapeHtml(String(issueNum)) + '" style="right:240px;color:#fff;background:var(--green);border-color:var(--green);">Commit &amp; resolve</button>'
            : '';
          // The "Close & announce" button used to live as an absolute
          // overlay in this sticky header; it's now a real button in
          // convToolbar (see #announceBtnConv) so it stays in reach
          // without having to scroll up.
          // Always start in the default (170px) state when entering a
          // conversation. The chevron toggles to `.is-expanded` (50vh)
          // and back; that state is ephemeral, scoped to this sticky DOM
          // node, and resets every time the user switches conversations.
          const mobileOriginalAskText = cleanIssuePrompt(originalAskTextForEvent(ev, paneId));
          if (paneId === activePaneId()) syncMobileOriginalAsk(mobileOriginalAskText);
          // Pop-out lives HERE — on the pane's own top bar — not in the
          // status rail. The rail can be collapsed (or repositioned) and
          // the pop-out action must survive that; it is pane chrome, not
          // rail chrome. Click handling is delegated at boot via
          // [data-role="ccc-breadcrumb-popout"], so the button needs no
          // per-conversation wiring and survives sticky rebuilds.
          const stickyPopoutBtn = state.CONV_POPOUT_MODE ? ''
            : '<button type="button" class="conv-sticky-header__popout" data-role="ccc-breadcrumb-popout"'
              + ' title="Pop this conversation out to its own window" aria-label="Pop out conversation">'
              + '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">'
              +   '<path d="M5 1H2a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V7" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>'
              +   '<path d="M7 1h4v4" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>'
              +   '<path d="M11 1L6 6" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>'
              + '</svg>'
              + '<span>Pop out</span>'
            + '</button>';
          sticky.innerHTML = resolveBtn + issueBtn
            + stickyPopoutBtn
            + '<button type="button" class="conv-sticky-header__close" data-csh-close title="Hide this panel completely">×</button>'
            + '<div class="csh-row">'
            +   '<div class="csh-col csh-col-ask">'
            +     (function () {
                      // CCC-785: a long-running WatchTower drain worker's
                      // "Original ask" is forever the queue-drain instruction
                      // it was launched with — stale the moment it claims its
                      // first ticket. Swap to the most recent ticket it
                      // claimed/closed, plus a list of every ticket it has
                      // handled this session, when that data is available.
                      // Matches via `conv.session_id` against each queue
                      // item's `claimed_session_id` (permanent — survives the
                      // worker process ending, unlike resolving a `wt
                      // --worker <id>` string through the live-workers cache).
                      const handled = (typeof _uxFixesHandledTicketsForRow === 'function')
                        ? _uxFixesHandledTicketsForRow(conv) : [];
                      const current = (typeof _uxFixesQueueProgressForRow === 'function')
                        ? _uxFixesQueueProgressForRow(conv) : null;
                      const mostRecent = (current && current.kind === 'working')
                        ? { ref: current.ref, title: current.title, note: current.note }
                        : (handled[0] || null);
                      if (!mostRecent || !mostRecent.ref) {
                        const parts = splitFirstSentence(mobileOriginalAskText);
                        const imagesHtml = renderImageDescriptors(ev.images);
                        // CCC-1026: "who spawned this session" is the first
                        // thing a reader asks when the ask itself is an opaque
                        // task template (e.g. a WatchTower worker prompt) —
                        // put the answer right next to it, not buried in a
                        // sidebar hover chip.
                        let h = '<div class="csh-ask-original"><div class="label">Original ask' + state.ORIGINAL_ASK_EXPAND_BTN + '</div>';
                        h += breadcrumbSpawnedByChipHtml(conv);
                        h += '<div class="user-msg" dir="auto" data-raw-text="' + escapeAttr(mobileOriginalAskText) + '">';
                        h += '<span class="ask-first">' + linkifyPastedImages(escapeHtml(parts[0])) + '</span>';
                        h += '<span class="ask-rest"' + (parts[1] ? '' : ' style="display:none"') + '>' + linkifyPastedImages(escapeHtml(parts[1] || '')) + '</span>';
                        h += imagesHtml;
                        h += '</div></div>';
                        return h;
                      }
                      const recentLabel = (current && current.kind === 'working') ? 'Working on' : 'Most recent ticket';
                      // Prefer the close resolution (what actually got fixed)
                      // over the raw complaint text; `title` on older queue
                      // items is just the generic page title captured at
                      // screenshot time, not a real ticket title — last resort.
                      const _truncate = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
                      const recentText = _truncate(mostRecent.summary || mostRecent.note || mostRecent.title || '', 220);
                      let h = '<div class="csh-ask-original is-worker-summary">';
                      h += '<div class="label">' + escapeHtml(recentLabel) + '</div>';
                      h += breadcrumbSpawnedByChipHtml(conv);
                      h += '<div class="user-msg" dir="auto">';
                      h += '<span class="ux-ticket-ref">' + escapeHtml(mostRecent.ref) + '</span> ';
                      h += '<span class="ask-first">' + escapeHtml(recentText) + '</span>';
                      h += pastedImageThumbsHtml(mostRecent.note || mostRecent.title || '');
                      h += '</div>';
                      if (handled.length) {
                        h += '<details class="csh-tickets-handled">';
                        h += '<summary>Tickets handled this session (' + handled.length + ')</summary>';
                        h += '<ul>';
                        for (const t of handled) {
                          const label = _truncate(t.summary || t.note || t.title || '', 120);
                          h += '<li><span class="ux-ticket-ref">' + escapeHtml(t.ref) + '</span> ' + escapeHtml(label) + '</li>';
                        }
                        h += '</ul></details>';
                      }
                      h += '</div>';
                      return h;
                    })()
            +     '<div class="csh-ask-earlier" data-earlier-block>'
            +       '<div class="label">Earlier ask</div>'
            +       '<div class="user-msg" dir="auto"><span class="earlier-first" data-earlier-first></span></div>'
            +     '</div>'
            +   '</div>'
            +   '<div class="csh-col csh-col-activity">'
            +     '<div class="session-timeline" data-timeline></div>'
            +   '</div>'
            + '</div>'
            + '<button type="button" class="conv-sticky-header__collapse" data-csh-collapse '
            +   'title="Expand to half-screen / shrink back" aria-label="Toggle expanded sticky panel">'
            +   '<span class="conv-sticky-header__collapse-icon">▴</span>'
            + '</button>';
          $view.insertBefore(sticky, $view.firstChild);
          // Apply the right-rail layout if it's active. The sticky was
          // just built with everything inside .csh-row; in right-rail
          // mode the original-ask and activity nodes need to be moved
          // into #statusRail. Idempotent — safe to call when not active.
          _applyStatusRailLayout();
          // Wire the chevron toggle. Two states:
          //   default (170px) → click → expanded (50vh)
          //   expanded (50vh) → click → default (170px)
          // State is ephemeral; switching conversations rebuilds the
          // sticky in the default state.
          const collapseBtn = sticky.querySelector('[data-csh-collapse]');
          if (collapseBtn) {
            collapseBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              const nowExpanded = !sticky.classList.contains('is-expanded');
              sticky.classList.toggle('is-expanded', nowExpanded);
              const icon = collapseBtn.querySelector('.conv-sticky-header__collapse-icon');
              if (icon) icon.textContent = nowExpanded ? '▾' : '▴';
            });
          }
          // The sticky ask slots keep a stable height while their message
          // bodies scroll internally; no resize handle or stored height.
          // Wire the dynamic-ask scroll tracker. The helpers (_dynAskItems,
          // _dynAskApply, _dynAskUpdate, _dynAskSchedule) only do work when
          // _dynamicAskState is populated and a scroll listener fires
          // _dynAskSchedule. Without this block they sit dormant and the
          // sticky never swaps to "Earlier ask".
          {
            state._dynamicAskState = {
              view: $view,
              sticky,
              earlierBox: sticky.querySelector('[data-earlier-block]'),
              earlierFirst: sticky.querySelector('[data-earlier-first]'),
              currentIdx: 0,
              rafPending: false,
            };
            if (!$view._dynAskListenerAttached) {
              $view._dynAskListenerAttached = true;
              $view.addEventListener('scroll', _dynAskSchedule, { passive: true });
            }
            _dynAskSchedule();  // settle initial state in case view mounts pre-scrolled
            // Decide initial slot layout (earlier collapsed / activity hidden)
            // before the timeline fetch lands; the timeline render will
            // rerun the coordinator once events arrive.
            _updateStickyAskSlots();
          }
          // Populate the timeline + workspace panels if the fetches already
          // landed; otherwise the fetch handlers will populate them.
          renderSessionTimelineIntoSticky();
          renderSessionWorkspaceIntoSticky();
          const closeBtn = sticky.querySelector('[data-csh-close]');
          if (closeBtn) {
            closeBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              // Use the semantic hidden state instead of an inline
              // `display:none`. The mobile right-rail fallback restores the
              // sticky with `display:block !important`, which otherwise wins
              // over the inline declaration and makes this button appear to
              // do nothing. Once the duplicate panel is gone, put the first
              // user turn back in the transcript so the original request is
              // still readable.
              sticky.hidden = true;
              const firstUser = $view.querySelector('.event.user_text.is-pinned-in-sticky');
              if (firstUser) firstUser.classList.remove('is-pinned-in-sticky');
              if (state._dynamicAskState && state._dynamicAskState.sticky === sticky) {
                state._dynamicAskState = null;
              }
            });
          }
          const resolveClickBtn = sticky.querySelector('.resolve-btn');
          if (resolveClickBtn) {
            resolveClickBtn.addEventListener('click', async (e) => {
              e.stopPropagation();
              const n = resolveClickBtn.dataset.issue;
              const sid = resolveClickBtn.dataset.session;
              resolveClickBtn.disabled = true;
              resolveClickBtn.textContent = 'Sending...';
              const msg = 'Please commit all your changes with a clear commit message referencing issue #' + n + ', then push. Use /commit if available. Once pushed, the issue will be closed automatically.';
              try {
                await fetch('/api/inject-input', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({ session_id: sid, text: msg }),
                });
                resolveClickBtn.textContent = 'Sent ✓';
                resolveClickBtn.style.opacity = '0.6';
              } catch (err) {
                resolveClickBtn.textContent = 'Failed';
                setTimeout(() => { resolveClickBtn.textContent = 'Commit & resolve'; resolveClickBtn.disabled = false; }, 2000);
              }
            });
          }
          const issueLinkBtn = sticky.querySelector('.issue-link-btn');
          if (issueLinkBtn) {
            issueLinkBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              const n = issueLinkBtn.dataset.issue;
              if (e.metaKey || e.ctrlKey || e.shiftKey) {
                renderIssueInConvPane(n, repoPathForIssueNumber(n));
              } else {
                renderIssueInConvPane(n, repoPathForIssueNumber(n));
              }
            });
          }
        }
      }

      if (ev.type === 'system') {
        if (ev.subtype === 'stop_held_inputs') {
          // CCC-237: queued after-turn input an explicit Stop parked. It never
          // sends on its own; Release re-sends (still after any running turn),
          // Cancel discards. Each store revision is a new block that replaces
          // the previous one; an emptied record renders hidden to clear it.
          const _shSid = String(ev.session_id || '');
          $view.querySelectorAll('.event.stop-held-block').forEach(el => {
            if (el.dataset.sessionId === _shSid) el.remove();
          });
          const _shItems = Array.isArray(ev.items) ? ev.items : [];
          div.classList.add('stop-held-block');
          div.dataset.sessionId = _shSid;
          if (!_shItems.length) {
            div.hidden = true;
          } else {
            const _shAttr = 'data-session-id="' + escapeAttr(_shSid) + '"';
            div.innerHTML = '<div class="stop-held-head"><span class="label">Held after Stop</span>'
              + '<span class="stop-held-sub">' + _shItems.length + ' queued message'
              + (_shItems.length === 1 ? '' : 's') + ' will not send until you release '
              + (_shItems.length === 1 ? 'it' : 'them') + '</span>'
              + (_shItems.length > 1
                ? '<button type="button" class="stop-held-btn" data-stop-held-action="release" data-stop-held-id="all" ' + _shAttr + '>Release all</button>'
                  + '<button type="button" class="stop-held-btn" data-stop-held-action="cancel" data-stop-held-id="all" ' + _shAttr + '>Cancel all</button>'
                : '')
              + '</div>'
              + _shItems.map(item => '<div class="stop-held-item" data-stop-held-item="' + escapeAttr(String(item.id || '')) + '">'
                + '<span class="stop-held-text" data-raw-text="' + escapeAttr(String(item.text || '')) + '">'
                + escapeHtml(truncate(String(item.text || ''), 400)) + '</span>'
                + '<button type="button" class="stop-held-btn" data-stop-held-action="release" data-stop-held-id="' + escapeAttr(String(item.id || '')) + '" ' + _shAttr
                + ' title="Send this message (after the current turn, if one is running)">Release</button>'
                + '<button type="button" class="stop-held-btn" data-stop-held-action="cancel" data-stop-held-id="' + escapeAttr(String(item.id || '')) + '" ' + _shAttr
                + ' title="Discard this message">Cancel</button>'
                + '</div>').join('');
          }
        } else if (ev.subtype === 'peer_held') {
          // A teammate's peer message was held by Claude's mode-parity rule
          // and never reached the model. Muted, one line, expandable.
          const who = String(ev.name || '').trim() || String(ev.from || '').replace(/^uds:/, '') || 'peer';
          div.classList.add('system-compact', 'peer-held');
          div.innerHTML = '<span class="label">Peer</span>'
            + '<span class="line-num">L' + ev.line + '</span>'
            + tsSpan(ev.ts)
            + '<span class="system-compact-text" title="' + escapeAttr(String(ev.text || '')) + '">'
            + 'Held message from ' + escapeHtml(who) + ' (not delivered)'
            + (ev.preview ? ': <span class="peer-held-preview">' + escapeHtml(String(ev.preview)) + '</span>' : '')
            + '</span>';
        } else if (ev.subtype === 'hermes_turn_summary') {
          // At-a-glance session health, emitted at the top of the transcript:
          // how many turns ran and how many failed upstream. Failed turns live
          // only in request_dump files, so a session can look fine in the list
          // while most of its turns errored — this surfaces that immediately.
          const _tsTurns = Number(ev.turns || 0);
          const _tsFailed = Number(ev.failed || 0);
          const _tsOk = Number(ev.succeeded || 0);
          const bits = [];
          if (ev.source_platform) bits.push('<span class="hts-platform">' + escapeHtml(String(ev.source_platform)) + '</span>');
          bits.push(escapeHtml(_tsTurns + (_tsTurns === 1 ? ' turn' : ' turns')));
          if (_tsFailed > 0) {
            bits.push('<span class="hts-ok">' + escapeHtml(String(_tsOk)) + ' ✓</span>');
            bits.push('<span class="hts-failed">' + escapeHtml(_tsFailed + ' failed') + '</span>');
          }
          if (ev.model) bits.push('<span class="hts-model">' + escapeHtml(formatModelEffort(ev.model, rowReasoningEffort(ev))) + '</span>');
          div.classList.add('system-compact', 'system-hermes', 'hermes-turn-summary');
          if (_tsFailed > 0) div.classList.add('has-failures');
          div.innerHTML = '<span class="label">Hermes</span>'
            + '<span class="line-num">L' + ev.line + '</span>'
            + tsSpan(ev.ts)
            + '<span class="system-compact-text">' + bits.join(' · ') + '</span>';
        } else if (ev.subtype === 'hermes_system_prompt') {
          // The system prompt Hermes injected into this conversation
          // (persona + skills + memory + per-conversation context). Read-only,
          // collapsed by default via native <details> so it doesn't dominate
          // the transcript.
          const _spText = String(ev.text || '');
          const _spChars = Number(ev.char_count || _spText.length) || 0;
          const _spTip = 'The system prompt Hermes injected into this session - '
            + 'persona, skills, memory and per-conversation context. Read-only.';
          div.classList.add('system-hermes', 'system-hermes-prompt');
          div.innerHTML = '<details class="hermes-sysprompt-details">'
            + '<summary class="hermes-sysprompt-summary" title="' + escapeAttr(_spTip) + '">'
            +   '<span class="label">Hermes</span>'
            +   '<span class="line-num">L' + ev.line + '</span>'
            +   tsSpan(ev.ts)
            +   '<span class="system-compact-text">Injected system prompt · '
            +     escapeHtml(_spChars.toLocaleString()) + ' chars'
            +     ' <span class="system-compact-help" aria-hidden="true">?</span></span>'
            + '</summary>'
            + '<pre class="hermes-sysprompt-body">' + escapeHtml(_spText) + '</pre>'
            + '</details>';
        } else if (ev.subtype === 'hermes_lineage') {
          const lineage = Array.isArray(ev.lineage_session_ids) ? ev.lineage_session_ids : [];
          const parent = ev.parent_session_id || '';
          const shortSession = ev.session ? String(ev.session).slice(0, 19) : '';
          const bits = [];
          if (ev.source_platform) bits.push(String(ev.source_platform));
          if (ev.model) bits.push(formatModelEffort(ev.model, rowReasoningEffort(ev)));
          const meta = bits.length ? ' · ' + escapeHtml(bits.join(' · ')) : '';
          const tip = 'Hermes compression created a continuation session.'
            + (parent ? '\nContinued from: ' + parent : '')
            + (lineage.length ? '\nLineage: ' + lineage.join(' -> ') : '');
          const countText = lineage.length > 1
            ? (lineage.length + ' linked sessions')
            : 'linked session';
          div.classList.add('system-compact', 'system-hermes');
          div.innerHTML = '<span class="label">Hermes</span>'
            + '<span class="line-num">L' + ev.line + '</span>'
            + tsSpan(ev.ts)
            + '<span class="system-compact-text" title="' + escapeAttr(tip) + '">Continuation history'
            + (shortSession ? ': ' + escapeHtml(shortSession) : '')
            + ' · ' + escapeHtml(countText)
            + meta
            + ' <span class="system-compact-help" aria-hidden="true">?</span></span>';
        } else if (ev.subtype === 'ccc_segment') {
          // Seam between two CCC continuation segments in the merged view:
          // messages ABOVE came from the earlier session, messages BELOW are
          // the session that continued from it. The divider doubles as the
          // jump target for "end of each session" navigation (wired in
          // attachConversationEndAffordance's delegated click handler).
          const _csChild = String(ev.child_title || '').trim();
          const _csParent = String(ev.parent_title || '').trim();
          const _csTip = 'Messages above were recorded in the earlier session'
            + (_csParent ? ' “' + _csParent + '”' : '') + '.'
            + ' The session below continued from it.'
            + '\nClick the label to jump to the start of the continued session; “end ⇡” jumps to the end of the earlier one.';
          div.classList.add('conv-session-boundary');
          // Engine / model / effort of the new session; a chip is
          // highlighted when it differs from the session above.
          const _csC = ev.child_cfg || null;
          const _csP = ev.parent_cfg || null;
          let _csCfgHtml = '';
          if (_csC) {
            const _chip = (txt, changed, what) => txt
              ? '<span class="csb-cfg' + (changed ? ' is-changed' : '') + '" title="'
                + escapeAttr(what + (changed ? ' changed from the earlier session' : '')) + '">'
                + escapeHtml(txt) + '</span>'
              : '';
            _csCfgHtml = _chip(_csC.engine, _csP && _csP.engine !== _csC.engine, 'Engine')
              + _chip(_csC.model, _csP && _csP.model !== _csC.model, 'Model')
              + _chip(_csC.effort ? 'effort ' + _csC.effort : '',
                _csP && _csP.effort !== _csC.effort, 'Effort');
            if (_csP && (_csP.engine !== _csC.engine || _csP.model !== _csC.model
                || _csP.effort !== _csC.effort)) {
              div.classList.add('is-config-changed');
            }
          }
          if (ev.child_sid) div.dataset.boundaryChildSid = String(ev.child_sid);
          if (ev.parent_sid) div.dataset.boundaryParentSid = String(ev.parent_sid);
          div.innerHTML = '<span class="csb-line" aria-hidden="true"></span>'
            + '<span class="csb-label" role="button" tabindex="0" title="' + escapeAttr(_csTip) + '">'
            + '⤴︎ New session' + (_csChild ? ' &ldquo;' + escapeHtml(_csChild) + '&rdquo;' : '') + '</span>'
            + _csCfgHtml
            + '<button type="button" class="csb-jump" title="Jump to the end of the earlier session">end ⇡</button>'
            + '<span class="csb-line" aria-hidden="true"></span>';
        } else if (ev.subtype === 'hermes_segment') {
          const parent = ev.parent_session_id || '';
          const bits = [];
          if (ev.source_platform) bits.push(String(ev.source_platform));
          if (ev.model) bits.push(formatModelEffort(ev.model, rowReasoningEffort(ev)));
          const meta = bits.length ? ' · ' + escapeHtml(bits.join(' · ')) : '';
          const tip = 'Messages below were recorded in a Hermes continuation segment.'
            + (parent ? '\nContinued from: ' + parent : '');
          div.classList.add('system-compact', 'system-hermes');
          div.innerHTML = '<span class="label">Hermes</span>'
            + '<span class="line-num">L' + ev.line + '</span>'
            + tsSpan(ev.ts)
            + '<span class="system-compact-text" title="' + escapeAttr(tip) + '">Continued session'
            + (ev.session ? ': ' + escapeHtml(String(ev.session).slice(0, 19)) : '')
            + meta
            + ' <span class="system-compact-help" aria-hidden="true">?</span></span>';
        } else if (ev.subtype === 'compact_boundary') {
          const compact = ev.compact || {};
          const preTokens = Number(compact.pre_tokens || 0);
          const postTokens = Number(compact.post_tokens || 0);
          const duration = _formatCompactDuration(compact.duration_ms);
          const trigger = compact.trigger ? String(compact.trigger) : 'manual';
          const triggerLabel = trigger === 'auto' ? 'automatic' : trigger;
          let compactText = 'Compacted context';
          if (preTokens || postTokens) {
            compactText += ': ' + _formatTokens(preTokens) + ' -> ' + _formatTokens(postTokens);
          }
          compactText += ' (' + triggerLabel + (duration ? ', ' + duration : '') + ')';
          // Codex emits this row too now, so the copy names whichever engine
          // actually wrote it instead of always saying Claude.
          const compactEngine = _compactEngineLabel(
            ev.engine || (state.currentSession && state.currentSession.source) || 'claude');
          const compactTip = trigger === 'auto'
            ? 'The conversation neared its context-window limit, so ' + compactEngine
              + ' automatically summarized the older history into a short recap and continued from there. Nothing is lost from the transcript on disk - only the model’s working memory was condensed.'
            : 'The conversation history was summarized into a short recap to free up context-window space (/compact). The full transcript on disk is untouched - only the model’s working memory was condensed.';
          div.classList.add('system-compact', 'compact-boundary-row');
          // The engine's OWN measurements. The lifecycle card prefers these
          // over its client-side before/after estimate, and the fold pass
          // below rebuilds a record card from them on a reopened session.
          div.dataset.compactPre = String(preTokens || 0);
          div.dataset.compactPost = String(postTokens || 0);
          div.dataset.compactDuration = String(compact.duration_ms || 0);
          div.dataset.compactTrigger = trigger;
          div.innerHTML = '<span class="label">System</span>'
            + '<span class="line-num">L' + ev.line + '</span>'
            + tsSpan(ev.ts)
            + '<span class="system-compact-text" title="' + escapeAttr(compactTip) + '">' + escapeHtml(compactText)
            + ' <span class="system-compact-help" aria-hidden="true">?</span></span>';
        } else if (ev.subtype === 'hermes_failed_turn') {
          // A turn whose upstream API call failed. Hermes persists only
          // successful turns to the DB; failures live in request_dump files,
          // so this is the only place the error (bad model id, out of usage,
          // rate limit, ...) surfaces in the transcript. Render it as a clear
          // error block right after the prompt it belongs to.
          const _ftMsg = String(ev.text || '').trim();
          const _ftBits = [];
          // Effort belongs here more than anywhere: an effort the model does
          // not accept is exactly the failure this block exists to explain.
          if (ev.model) _ftBits.push(formatModelEffort(ev.model, rowReasoningEffort(ev)));
          if (ev.status_code) _ftBits.push('HTTP ' + escapeHtml(String(ev.status_code)));
          if (ev.error_type) _ftBits.push(String(ev.error_type));
          else if (ev.reason) _ftBits.push(String(ev.reason).replace(/_/g, ' '));
          const _ftMeta = _ftBits.length ? escapeHtml(_ftBits.join(' · ')) : '';
          const _ftReq = String(ev.request_id || '').trim();
          div.classList.add('system-hermes', 'hermes-failed-turn');
          div.innerHTML = '<span class="label">Failed turn</span>'
            + '<span class="line-num">L' + ev.line + '</span>'
            + tsSpan(ev.ts)
            + (_ftMeta ? '<span class="hermes-failed-meta">' + _ftMeta + '</span>' : '')
            + (_ftMsg ? '<div class="hermes-failed-detail">' + escapeHtml(_ftMsg) + '</div>' : '')
            + (_ftReq ? '<div class="hermes-failed-reqid" title="Anthropic request id - quote this to support">'
                + escapeHtml(_ftReq) + '</div>' : '');
        } else if (ev.subtype === 'codex_coordination') {
          // Durable desktop↔CCC coordination note (external turn started/ended,
          // input queued, CCC turn started/completed). Meta, not transcript —
          // render a quiet single-line row. `line` here is a synthetic string id,
          // so no L-number span.
          const _coordIcons = {
            external_turn_started: '🖥',
            external_turn_ended: '✓',
            input_queued: '⏸',
            ccc_turn_started: '▶',
            ccc_turn_completed: '✓',
          };
          const _coordKind = String(ev.kind || '').replace(/[^a-z0-9_-]/gi, '');
          const _coordIcon = _coordIcons[ev.kind] || '•';
          div.classList.add('system-compact', 'codex-coordination');
          if (_coordKind) div.classList.add('coord-' + _coordKind);
          div.innerHTML = '<span class="ccoord-icon" aria-hidden="true">' + _coordIcon + '</span>'
            + '<span class="ccoord-text">' + escapeHtml(ev.text || '') + '</span>'
            + tsSpan(ev.ts);
        } else if (ev.subtype === 'codex_app_server_item') {
          const _tool = String(ev.tool || ev.item_type || 'Codex');
          const _status = String(ev.status || '').toLowerCase();
          const _needsApproval = !!ev.needs_approval;
          const _isRunning = !!ev.in_flight || _status === 'inprogress' || _status === 'running';
          const _isError = !!ev.is_error || _status === 'failed' || _status === 'errored';
          const _icon = _needsApproval ? '!' : (_isError ? '!' : (_isRunning ? '>' : '+'));
          const _detail = String(ev.approval_message || ev.detail || ev.output || ev.text || '').trim();
          const _label = _needsApproval
            ? ('Codex app-server · ' + _tool + ' needs approval')
            : (ev.item_type === 'agentMessage' ? 'Codex message' : ('Codex app-server · ' + _tool));
          div.classList.add('system-compact', 'codex-app-item');
          if (_isRunning) div.classList.add('is-running');
          if (_isError) div.classList.add('is-error');
          if (_needsApproval) div.classList.add('needs-approval');
          div.innerHTML = '<span class="ccoord-icon" aria-hidden="true">' + _icon + '</span>'
            + '<span class="ccoord-text"><span class="codex-app-item-label">' + escapeHtml(_label) + '</span>'
            + (_detail ? ' <span class="codex-app-item-detail">' + escapeHtml(truncate(_detail, 260)) + '</span>' : '')
            + '</span>'
            + tsSpan(ev.ts);
        } else if (ev.subtype === 'codex_subagent') {
          // Codex multi-agent mode: the root thread spawned/messaged its own
          // sub-agent threads. Each sub-agent is a real, independently
          // addressable Codex thread (its own rollout file) - this row is
          // just the "something happened between agents" marker; opening the
          // sub-agent's own session shows its actual transcript.
          const _kind = String(ev.kind || '').replace(/[^a-z0-9_-]/gi, '');
          const _isCollab = _kind.indexOf('collab_') === 0;
          const _icon = _isCollab ? '↔' : '⊕';
          div.classList.add('system-compact', 'codex-subagent');
          if (_kind) div.classList.add('subagent-' + _kind);
          div.innerHTML = '<span class="ccoord-icon" aria-hidden="true">' + _icon + '</span>'
            + '<span class="ccoord-text">' + escapeHtml(ev.text || '') + '</span>'
            + tsSpan(ev.ts);
        } else if (ev.subtype === 'cloud_unavailable') {
          // Codex cloud thread whose transcript could not be fetched — show
          // the typed reason instead of an empty pane.
          div.classList.add('system-compact', 'codex-cloud-note');
          div.innerHTML = '<span class="ccoord-icon" aria-hidden="true">☁</span>'
            + '<span class="ccoord-text">' + escapeHtml(ev.text || 'cloud thread, transcript unavailable') + '</span>'
            + tsSpan(ev.ts);
        } else if (ev.subtype === 'ccc_free_runtime') {
          // Limit-hit failover marker written by the server (see
          // ccc_server/free_failover.py): "continued on a free model" /
          // "switched back" / "resumed automatically at reset". A quiet
          // green row so the transcript explains the $0 stretch.
          const _frText = String(ev.text || '').trim()
            || 'Ran on a free model for a stretch.';
          div.classList.add('system-compact', 'ccc-free-runtime');
          div.innerHTML = '<span class="ccoord-icon" aria-hidden="true">⚡</span>'
            + '<span class="ccoord-text">' + escapeHtml(_frText) + '</span>'
            + tsSpan(ev.ts);
        } else if (String(ev.subtype || '').indexOf('grok_') === 0) {
          // Grok ACP status updates (failed hook runs, image-dropped notes,
          // retries, plan/task/subagent/compaction lifecycle) are meta, not
          // model text — render as a compact Grok row so the conversation
          // view shows the same lifecycle the terminal does.
          const _grokText = String(ev.text || '').trim();
          div.classList.add('system-compact', 'system-grok');
          div.innerHTML = '<span class="label">Grok</span>'
            + '<span class="line-num">L' + ev.line + '</span>'
            + tsSpan(ev.ts)
            + '<span class="system-compact-text" title="' + escapeAttr(_grokText) + '">' + escapeHtml(_grokText) + '</span>';
        } else {
          div.innerHTML = '<span class="label">System</span>'
            + '<span class="line-num">L' + ev.line + '</span>'
            + tsSpan(ev.ts)
            + '<span>' + escapeHtml(ev.subtype || '') + (ev.model ? ' &middot; ' + escapeHtml(formatModelEffort(ev.model, rowReasoningEffort(ev))) : '') + (ev.session ? ' &middot; ' + escapeHtml(ev.session) : '') + '</span>';
        }
      } else if (ev.type === 'user_text') {
        // If this matches an outstanding optimistic send, drop the pending stub
        // (the real event lands in its place below).
        const normed = state._normSend(ev.text);
        let _reconciledExact = false;
        let _acknowledgedSendText = '';
        // App-server live-overlay userMessage items are provisional. They can
        // appear before the rollout JSONL or queue state confirms delivery,
        // then disappear when the live snapshot clears. Only a durable
        // transcript event may acknowledge and remove the injected echo.
        const _authoritativeUserEvent = !ev.peer && !ev.skill && !ev.pending && !ev.provisional;
        const sendEventEpoch = ev.ts ? Date.parse(ev.ts) : NaN;
        // devin-cli truncates event timestamps to the whole second, so the
        // event that delivers a send can read up to ~1s before the echo's
        // Date.now() stamp; without slack that echo never clears (CCC-205).
        const sendAckClockSlackMs = 2000;
        const canAcknowledgeSend = pending => !Number.isFinite(sendEventEpoch)
          || !Number(pending && pending.ts)
          || sendEventEpoch + sendAckClockSlackMs >= Number(pending.ts);
        // Peer (cross-session) messages are not the operator's own sends:
        // never let one clear a pending echo, exact or FIFO.
        // A synthetic queue overlay is still awaiting delivery. It must not
        // acknowledge an echo or remove a card being optimistically steered.
        const pIdx = !_authoritativeUserEvent ? -1 : state._pendingSends.findIndex(p =>
          state._normSend(p.text) === normed && canAcknowledgeSend(p));
        if (pIdx >= 0) {
          const p = state._pendingSends[pIdx];
          p.element?._pendingRef?.resolveQueueReady?.(false);
          if (p.element && p.element.parentNode) p.element.parentNode.removeChild(p.element);
          if (p.timer) clearTimeout(p.timer);
          if (p.sid) clearSessionSending(p.sid);
          state._pendingSends.splice(pIdx, 1);
          _reconciledExact = true;
          _acknowledgedSendText = state._normSend(p.text);
          const pane = paneByPaneId(paneId);
          if (pane && state.currentConversation) syncPendingSendsMapForConv(pane, state.currentConversation);
        }
        // Self-heal: clear ANY optimistic-echo state whose text matches the
        // real event that just arrived — not only `.pending`. A queued echo
        // (`.send-queued`) has the `pending` class REMOVED, so the old
        // pending-only scan never saw it; it relied solely on the _pendingSends
        // array, and once that entry was lost (pane re-render / conv switch) or
        // the delivered text differed, the "⏳ Queued…" note stuck on the row
        // forever even though the message had landed. Match send-queued /
        // send-delivered / not-acknowledged too so the durable event always
        // wins. Real events never carry these classes, so this can't remove a
        // genuine transcript row.
        const echoScope = $view.closest('.conv-pane') || $view;
        const echoDivs = (!_authoritativeUserEvent || _reconciledExact) ? [] : echoScope.querySelectorAll(
          '.event.user_text.pending, .event.user_text.send-queued,'
          + ' .event.user_text.send-delivered, .event.user_text.not-acknowledged');
        for (const pDiv of echoDivs) {
          const echoEntry = pDiv._pendingRef && pDiv._pendingRef.entry;
          if (!canAcknowledgeSend(echoEntry || { ts: pDiv.dataset.tsEpoch })) continue;
          const userMsgDiv = pDiv.querySelector('.user-msg');
          if (userMsgDiv) {
            const rawText = userMsgDiv.getAttribute('data-raw-text') || userMsgDiv.textContent;
            if (state._normSend(rawText) === normed) {
              if (pDiv.parentNode) pDiv.parentNode.removeChild(pDiv);
              _reconciledExact = true;
              _acknowledgedSendText = state._normSend(rawText);
              // One durable message acknowledges one occurrence, even when
              // the user has queued the same words more than once.
              break;
            }
          }
        }
        // FIFO fallback: user messages deliver in send order, so a real
        // user_text event with no exact-text match still proves the OLDEST
        // outstanding optimistic echo was delivered — just recorded with
        // slightly different text (common with voice transcription, where the
        // echoed words and the saved transcript differ). Reconcile it instead
        // of leaving the message stuck on "· sending…" for the full 5-minute
        // timeout (the "why is this still being pushed?" symptom). Worst case
        // the echo clears a beat early — the message still renders below, so
        // nothing is lost. Skip synthetic continuation events so they never
        // consume a genuine pending echo.
        const _isContinuation = /^This session is being continued from a previous conversation\b/.test(ev.text || '');
        if (_authoritativeUserEvent && !_reconciledExact && !_isContinuation) {
          if (state._pendingSends.length && canAcknowledgeSend(state._pendingSends[0])) {
            const oldest = state._pendingSends.shift();
            if (oldest) {
              _reconciledExact = true;
              _acknowledgedSendText = state._normSend(oldest.text);
              if (oldest.element && oldest.element.parentNode) oldest.element.parentNode.removeChild(oldest.element);
              if (oldest.timer) clearTimeout(oldest.timer);
              if (oldest.sid) clearSessionSending(oldest.sid);
              const pane = paneByPaneId(paneId);
              if (pane && state.currentConversation) syncPendingSendsMapForConv(pane, state.currentConversation);
            }
          }
          // Orphan sweep: the durable event still acknowledges ONE delivery
          // even when the echo's array entry is already gone (pane re-render,
          // conv switch) or its raw text drifted from the persisted wording —
          // e.g. devin-cli stores pasted-image paths inline mid-message while
          // the echo kept them appended, so the exact match above never fires
          // and a "✓ Delivered" card would sit under its own transcript bubble
          // forever. Remove the oldest surviving echo row in that case.
          if (!_reconciledExact) {
            const _orphans = echoScope.querySelectorAll(
              '.event.user_text.pending, .event.user_text.send-queued,'
              + ' .event.user_text.send-delivered, .event.user_text.not-acknowledged');
            for (const _o of _orphans) {
              const _oEntry = _o._pendingRef && _o._pendingRef.entry;
              if (!canAcknowledgeSend(_oEntry || { ts: _o.dataset.tsEpoch })) continue;
              if (_o.parentNode) _o.parentNode.removeChild(_o);
              _reconciledExact = true;
              const _orphanMsg = _o.querySelector('.user-msg');
              _acknowledgedSendText = state._normSend(_orphanMsg && (_orphanMsg.getAttribute('data-raw-text') || _orphanMsg.textContent));
              break;
            }
          }
        }
        // The render-tail and tray sweeps see this same durable row next.
        // They must not reuse its receipt to clear a second identical send.
        if (_reconciledExact) {
          div.dataset.sendAcknowledged = 'true';
          div.dataset.sendAcknowledgedText = _acknowledgedSendText;
        }
        // Webui panes: collapse a durable user bubble that repeats the most
        // recent durable user bubble when that bubble was NEVER ANSWERED.
        // The Codex queue pump can persist the same queued text several
        // times while a busy turn rejects delivery, producing alternating
        // bubble/banner repeats with no assistant turn in between — render
        // the first copy only. A repeat the user genuinely re-sent comes
        // AFTER an assistant reply (a `.kimi-turn` sits between the bubbles),
        // so it always renders. (Pending-echo reconciliation above has
        // already run, so the optimistic echo is still cleared.)
        if (_kimiPane && normed && _authoritativeUserEvent) {
          const _userRows = $view.querySelectorAll(
            '.event.user_text:not(.pending):not(.send-queued):not(.send-delivered):not(.not-acknowledged):not(.provisional)');
          const _lastUserRow = _userRows.length ? _userRows[_userRows.length - 1] : null;
          const _lastUserMsg = _lastUserRow && _lastUserRow.querySelector('.user-msg');
          if (_lastUserMsg
              && state._normSend(_lastUserMsg.getAttribute('data-raw-text') || _lastUserMsg.textContent) === normed) {
            const _turns = $view.querySelectorAll('.kimi-turn');
            const _lastTurn = _turns.length ? _turns[_turns.length - 1] : null;
            const _answered = !!(_lastTurn
              && (_lastUserRow.compareDocumentPosition(_lastTurn) & Node.DOCUMENT_POSITION_FOLLOWING));
            if (!_answered) continue;
          }
        }
        const imagesHtml = renderImageDescriptors(ev.images);
        // Safety net: if cleanIssuePrompt strips a user's typed message
        // down to empty (it's matching a spawn-prompt boilerplate
        // pattern that overshot), fall back to the raw text so the
        // user's words never disappear from their own conv view. The
        // user reported a sent message being swallowed because the
        // pending echo got dedup'd against the real event but the real
        // event rendered with empty body.
        const _rawText = (ev.text || '').trim();
        let cleanedText = cleanIssuePrompt(ev.text || '');
        if (_rawText && !String(cleanedText || '').trim()) {
          cleanedText = ev.text;
        }
        // Kimi: defensively strip control-XML wrappers (system reminders,
        // skill-loaded blocks, command envelopes) before they reach a
        // bubble. A message that becomes empty keeps its (hidden) event div
        // so data-jsonl-line dedupe still anchors it.
        if (_kimiPane) {
          cleanedText = _stripKimiControlXml(cleanedText);
          if (!String(cleanedText || '').trim()) div.classList.add('kimi-xml-only');
        }
        if (legacySilenceCheck) div.classList.add('legacy-silence-check');
        const bridgeSenderHtml = whatsappBridgeSenderHtml(ev)
          + peerSenderHtml(legacySilenceCheck && ordinaryInput ? { peer: ev.peer } : ev);
        if (ev.peer) div.classList.add('peer-message');
        if (ev.skill) div.classList.add('skill-loaded-event');
        const notification = parseTaskNotificationBlock(cleanedText);
        if (notification) div.classList.add('task-notification-event');
        if (!notification && !ev.peer && !ev.skill && cleanedText) syncPaneLastUserMessage(paneId, cleanedText);
        // Collapse /compact-resume blocks into a styled card — they're
        // multi-page walls of text that drown the actual conversation.
        // The card shows just the intro line + a click-to-expand toggle;
        // the full body renders inside (still readable, just out of the
        // way until requested). Also tears down the compact-in-progress
        // banner the send handler put up: the boundary just arrived.
        const compactCardHtml = renderCompactResumeCard(cleanedText);
        if (compactCardHtml) {
          div.classList.add('compact-resume-event');
          // The boundary just landed: this IS the compaction's result. Absorb
          // the summary into the lifecycle card (one element, no sibling to
          // order wrongly) and land the card on its before/after numbers.
          if (typeof completeCompactRun === 'function' && state._compactRun
              && state._compactRun.sid === (state.currentSession && state.currentSession.id)
              && state._compactRun.stage !== 'failed') {
            state._compactRun.summaryHtml = compactCardHtml;
            div.classList.add('compact-absorbed-by-run');
            completeCompactRun(state._compactRun.sid);
            _compactRunAnchorTo(div);
          }
        }
        const userSteerHtml = userMessageSteerHtml(cleanedText, notification, compactCardHtml, ev);
        if (userSteerHtml) div.classList.add('has-user-steer');
        // data-raw-text preserves the original prose so _dynAskApply can pin
        // the same wording in the "Earlier ask" sticky — reading textContent
        // back would lose any pasted-image path that's been replaced with <img>.
        const _isPinned = div.classList.contains('is-pinned-in-sticky');
        // Queue launch instructions are machine boilerplate, often several
        // screens long. Keep the complete prompt available without making
        // the reader scroll through it before reaching the actual work.
        const workerQueue = !ev.peer && !ev.skill
          && /^\s*Drain the ([A-Za-z0-9_-]+) WatchTower queue\b/i.exec(cleanedText);
        const workerInstructions = workerQueue
          && /\bYour worker id is [A-Za-z0-9_-]+\b/i.test(cleanedText);
        let textHtml;
        if (notification) {
          textHtml = renderTaskNotificationBlock(notification, cleanedText, true);
        } else if (ev.skill) {
          textHtml = '<div class="user-msg" dir="auto" data-raw-text="' + escapeAttr(cleanedText) + '">' + renderSkillLoadedCard(ev) + '</div>';
        } else if (ev.handoff) {
          div.classList.add('devin-handoff-event');
          textHtml = '<div class="user-msg" dir="auto" data-raw-text="' + escapeAttr(cleanedText) + '">' + bridgeSenderHtml + renderDevinHandoffCard(ev) + '</div>';
        } else if (compactCardHtml) {
          textHtml = '<div class="user-msg" dir="auto" data-raw-text="' + escapeAttr(cleanedText) + '">' + bridgeSenderHtml + compactCardHtml + '</div>';
        } else if (workerInstructions) {
          div.classList.add('worker-instructions-event');
          textHtml = '<div class="user-msg" dir="auto" data-raw-text="' + escapeAttr(cleanedText) + '">'
            + '<details class="worker-instructions-card"><summary>'
            + escapeHtml(workerQueue[1]) + ' worker instructions</summary>'
            + '<div class="worker-instructions-body">'
            + (_codexPane ? renderCodexUserText(cleanedText) : linkifyPastedImages(escapeHtml(cleanedText)))
            + '</div></details>' + (_isPinned ? imagesHtml : '') + '</div>';
        } else if (cleanedText) {
          if (_isPinned) {
            // For the pinned original-ask bubble: split into first sentence + rest so
            // CSS can size the opener larger and render the body smaller/quieter.
            const _parts = splitFirstSentence(cleanedText);
            const _imagesHtml = renderImageDescriptors(ev.images);
            textHtml = '<div class="user-msg" dir="auto" data-raw-text="' + escapeAttr(cleanedText) + '">'
              + state.ORIGINAL_ASK_EXPAND_BTN
              + bridgeSenderHtml
              + '<span class="ask-first">' + (_codexPane ? renderCodexUserText(_parts[0]) : linkifyPastedImages(escapeHtml(_parts[0]))) + '</span>'
              + (_parts[1] ? '<span class="ask-rest">' + (_codexPane ? renderCodexUserText(_parts[1]) : linkifyPastedImages(escapeHtml(_parts[1]))) + '</span>' : '')
              + _imagesHtml
              + '</div>';
          } else {
            const _peerHtml = ev.peer ? formatPeerMessageHtml(cleanedText) : '';
            textHtml = '<div class="user-msg" dir="auto" data-raw-text="' + escapeAttr(cleanedText) + '">' + bridgeSenderHtml + (_peerHtml || (_codexPane ? renderCodexUserText(cleanedText) : linkifyPastedImages(escapeHtml(cleanedText)))) + '</div>';
          }
        } else {
          textHtml = bridgeSenderHtml ? '<div class="user-msg" dir="auto" data-raw-text="">' + bridgeSenderHtml + '</div>' : '';
        }
        // Claude writes "[Request interrupted by user]" into the transcript as
        // a USER message when its turn is cut off (CCC kill, an approved
        // interrupt-ask, or a real Esc). Rendering it as a normal user bubble
        // reads as though the human typed those words, and the turn just looks
        // like it quietly ended ("Done"). Render it as an explicit interrupted
        // marker instead, so the conversation itself says what happened.
        if (/^\[Request interrupted by user/.test(_rawText) && debugModeEnabled()) {
          div.classList.add('session-interrupted-event');
          div.innerHTML = '<span class="label">Session</span>'
            + (ev.line != null ? '<span class="line-num">L' + ev.line + '</span>' : '')
            + tsSpan(ev.ts)
            + '<div class="session-interrupted-msg">'
            + '<span class="session-interrupted-title">⏸ Session interrupted</span>'
            + '<span class="session-interrupted-sub">Claude\'s turn was cut off mid-response'
            + ' (a kill, an approved interrupt, or Esc) - this reflects the transcript itself,'
            + ' not a hook. Send a message to pick it back up.</span>'
            + '</div>';
        } else {
        div.innerHTML = '<span class="label">' + (ev.peer ? 'Peer' : (ev.skill ? 'Skill' : 'User')) + '</span>'
          + (ev.line != null ? '<span class="line-num">L' + ev.line + '</span>' : '')
          + tsSpan(ev.ts)
          + ambientContextHtml(ev.ambient_context)
          + textHtml
          + eventModelMetaHtml(ev, 'user')
          + (_isPinned ? '' : imagesHtml)
          + userSteerHtml;
        }

      } else if (ev.type === 'assistant') {
        if (_kimiPane) {
          // kimi-web turn rendering: consecutive assistant events merge into
          // ONE `.kimi-turn`; blocks render in original order (thinking,
          // text, plan, tool rows), tool runs of >=2 fuse into a ToolGroup
          // card. The event div stays as a hidden `.kimi-marker` so
          // data-jsonl-line dedupe + the msg_id stream hand-off keep working.
          div.classList.add('kimi-marker');
          const kimiBlockEls = [];
          const kimiAnswerParts = [];
          const kimiBlocks = Array.isArray(ev.blocks)
            ? ev.blocks
            : (String(ev.text || '').trim() ? [{ kind: 'text', text: String(ev.text || '') }] : []);
          const _kimiHolder = document.createElement('div');
          for (const b of kimiBlocks) {
            if (!b || !b.kind) continue;
            if (b.kind === 'tool_use') {
              // Seed the subagent-tab label (same as the shared path).
              if (b.name === 'Task' && b.id && $view) {
                const _info = (b.detail && (b.detail.description || b.detail.prompt))
                  || (typeof b.detail === 'string' ? b.detail : '');
                _convPaneSeedTaskInfo($view, b.id, {
                  description: typeof _info === 'string' ? _info : (_info && _info.description) || '',
                  name: 'Task',
                });
              }
              _kimiHolder.innerHTML = _kimiToolRowHtml(b);
              if (_kimiHolder.firstElementChild) kimiBlockEls.push(_kimiHolder.firstElementChild);
            } else if (b.kind === 'text') {
              const txt = _stripKimiControlXml(String(b.text || ''));
              if (!txt.trim()) continue;
              kimiAnswerParts.push(txt);
              const t = document.createElement('div');
              t.className = 'assistant-text';
              t.dir = 'auto';
              t.innerHTML = renderMarkdown(txt);
              kimiBlockEls.push(t);
            } else if (b.kind === 'thinking') {
              if (b.signature_only || !b.text) continue;
              // Thinking is NOT XML-stripped: the model quoting a wrapper
              // while reasoning about it is legitimate content (kimi-web
              // doesn't strip thinking either).
              _kimiHolder.innerHTML = _kimiThinkingHtml(String(b.text), _codexPane);
              if (_kimiHolder.firstElementChild) kimiBlockEls.push(_kimiHolder.firstElementChild);
            } else if (b.kind === 'skill_loaded') {
              _kimiHolder.innerHTML = renderCodexSkillLoadedCard(b);
              if (_kimiHolder.firstElementChild) kimiBlockEls.push(_kimiHolder.firstElementChild);
            } else if (b.kind === 'plan') {
              const planHtml = _planEntriesHtml(b.entries);
              if (planHtml) {
                _kimiHolder.innerHTML = planHtml;
                if (_kimiHolder.firstElementChild) kimiBlockEls.push(_kimiHolder.firstElementChild);
              }
            } else if (b.kind === 'diff') {
              const diffHtml = _turnDiffBlockHtml(b);
              if (diffHtml) {
                _kimiHolder.innerHTML = diffHtml;
                if (_kimiHolder.firstElementChild) kimiBlockEls.push(_kimiHolder.firstElementChild);
              }
            } else if (b.kind === 'image_generation') {
              const imgGenHtml = _imageGenerationBlockHtml(b, ev);
              if (imgGenHtml) {
                _kimiHolder.innerHTML = imgGenHtml;
                if (_kimiHolder.firstElementChild) kimiBlockEls.push(_kimiHolder.firstElementChild);
              }
            }
          }
          const _kimiAnswerText = kimiAnswerParts.join('\n\n').trim();
          div._agentAnswerText = _kimiAnswerText;
          // Claude's assistant turns get a turn number, a timestamp, and
          // speak/copy actions (assistantMessageActionsHtml); kimi turns
          // merge into `.kimi-turn` and skipped all of that (the event div
          // that would normally carry it is the hidden `.kimi-marker`,
          // CCC-755). Surface the same metadata on a small visible row —
          // `_agentAnswerText` lives on IT (not the hidden marker) so the
          // copy/speak click handlers below can find it via `.closest`.
          if (_kimiAnswerText) {
            const metaEl = document.createElement('div');
            metaEl.className = 'kimi-answer-meta';
            metaEl._agentAnswerText = _kimiAnswerText;
            if (ev.live_key != null) metaEl.dataset.liveKey = String(ev.live_key);
            // Live (provisional) events carry no jsonl line — omit the tag
            // instead of rendering a literal "Lundefined".
            metaEl.innerHTML = (ev.line != null ? '<span class="line-num">L' + ev.line + '</span>' : '')
              + tsSpan(ev.ts) + assistantMessageActionsHtml(ev);
            // CCC-929: wire_tail._attach_kimi_wire_usage already stamps
            // tokens_in/tokens_out/tokens_cached on each Kimi assistant
            // event server-side — the generic (Claude/Antigravity) branch
            // below renders a chip from the same fields, but the kimi
            // branch `continue`s before reaching it, so this row never
            // got one. Reuse the same chip formatter for parity.
            if (ev.tokens_in || ev.tokens_out || ev.tokens_cached) {
              const _kimiChipCached = Number(ev.tokens_cached
                || (ev.token_usage && (ev.token_usage.cache_read_input_tokens || ev.token_usage.cached_input_tokens)) || 0);
              const _kimiChipText = _formatAntigravityTokenChips(ev.tokens_in, ev.tokens_out, ev.tokens_thinking, _kimiChipCached);
              if (_kimiChipText) {
                const _kimiChipTitle = 'Input:    ' + (Number(ev.tokens_in) || 0).toLocaleString() + ' tokens'
                  + '\nCached input:    ' + _kimiChipCached.toLocaleString() + ' tokens'
                  + '\nOutput:   ' + (Number(ev.tokens_out) || 0).toLocaleString() + ' tokens';
                metaEl.innerHTML += '<span class="event-token-chips is-merged" title="'
                  + escapeAttr(_kimiChipTitle) + '">' + escapeHtml(_kimiChipText)
                  + _cacheAdjustedInlineHtml(ev.tokens_in, ev.tokens_out, _kimiChipCached, ev.model) + '</span>';
              }
            }
            kimiBlockEls.push(metaEl);
          }
          _kimiAppendAssistantEvent($view, div, kimiBlockEls, _existingProvisionalNode);
          // Codex mode-3 presentation artifacts attach to the assistant
          // event — the shared tail below is skipped by this branch.
          attachPresentationArtifactToAssistant($view, div, ev, renderedConversationId);
          continue;
        }
        const _actorChip = (ev.actor === 'sidekick' || ev.actor === 'lead')
          ? '<span class="actor-chip actor-' + ev.actor + '" title="'
            + escapeAttr(ev.actor === 'sidekick'
              ? (ev.subagent_report ? 'Sidekick subagent - completion report' : 'Sidekick subagent turn (Fusion)')
              : 'Lead model turn (Fusion)')
            + '">' + escapeHtml(ev.subagent_report ? 'sidekick ▸ report' : ev.actor) + '</span>'
          : '';
        let html = assistantMessageActionsHtml(ev)
          + '<span class="line-num">L' + ev.line + '</span>'
          + tsSpan(ev.ts)
          + _actorChip;
        let hasNonTool = false;
        // Collect block HTML in parts so the per-turn token chip can be merged
        // into the end of the last tool-call (CCC-30) instead of floating as
        // its own line below the turn.
        const blockParts = [];
        const agentAnswerParts = [];
        let lastToolPartIdx = -1;
        const bridgeSenderHtml = whatsappBridgeSenderHtml(ev);
        if (bridgeSenderHtml) blockParts.push(bridgeSenderHtml);
        const assistantBlocks = Array.isArray(ev.blocks)
          ? ev.blocks
          : (String(ev.text || '').trim() ? [{ kind: 'text', text: String(ev.text || '') }] : []);
        for (const b of assistantBlocks) {
          if (b.kind === 'tool_use') {
            // Seed the subagent-tab label when a Task tool_use lands in
            // the parent JSONL — gives the tab a real name (description
            // or subagent_type) instead of the fallback "Task <id>".
            if (b.name === 'Task' && b.id && $view) {
              const _info = (b.detail && (b.detail.description || b.detail.prompt))
                || (typeof b.detail === 'string' ? b.detail : '');
              _convPaneSeedTaskInfo($view, b.id, {
                description: typeof _info === 'string' ? _info : (_info && _info.description) || '',
                name: 'Task',
              });
            }
            const baseName = toolDisplayName(b.name);
            const displayName = (baseName === 'AskUserQuestion' || baseName === 'ask_user_question') ? 'Question' : baseName;
            const source = toolBlockSource(b);
            const sourceHtml = source
              ? '<span class="tool-source" title="Tool source">' + escapeHtml(source) + '</span> '
              : '';
            const toolClass = baseName === 'AskUserQuestion' ? ' ask-user-question' : '';
            const detail = formatToolCallDetail(b.name, b.detail);
            // AskUserQuestion may carry up to 4 questions in one tool call.
            // Accept the new {questions:[...]} shape and the older single-
            // question shape so older transcripts still render.
            let askQuestions = null;
            if ((baseName === 'AskUserQuestion' || baseName === 'ask_user_question') && b.question && typeof b.question === 'object') {
              if (Array.isArray(b.question.questions) && b.question.questions.length) {
                askQuestions = b.question.questions;
              } else if (b.question.header || b.question.question || (Array.isArray(b.question.options) && b.question.options.length)) {
                askQuestions = [b.question];
              }
            }
            let askBody = '';
            if (askQuestions) {
              // CCC-161: for a SINGLE-question ask, render options as clickable
              // buttons so the user can answer straight from the transcript —
              // a hook-independent fallback for when the relayed answer card
              // doesn't mount. The click handler (delegated) gates on the live
              // session's latest waiting question and POSTs /api/answer-question
              // (the same path the card uses). Multi-question asks keep static
              // options and rely on the relay card.
              const _askPickable = askQuestions.length === 1;
              askBody = askQuestions.map(function (askQ, qIdx) {
                const headerHtml = askQ.header
                  ? '<div class="ask-user-header">' + escapeHtml(askQ.header) + '</div>'
                  : '';
                const questionHtml = askQ.question
                  ? '<div class="ask-user-question-text">' + escapeHtml(askQ.question) + '</div>'
                  : '';
                const opts = Array.isArray(askQ.options) ? askQ.options : [];
                const optsHtml = opts.length
                  ? '<ul class="ask-user-options' + (_askPickable ? ' is-pickable' : '') + '">' + opts.map(function (o, oIdx) {
                      const lbl = (o && typeof o === 'object') ? (o.label || '') : String(o || '');
                      const desc = (o && typeof o === 'object') ? (o.description || '') : '';
                      const inner = '<span class="ask-user-option-label">' + escapeHtml(lbl) + '</span>'
                        + (desc ? '<span class="ask-user-option-desc"> - ' + escapeHtml(desc) + '</span>' : '');
                      if (_askPickable) {
                        return '<li><button type="button" class="ask-user-option-pick"'
                          + ' data-ask-q="' + qIdx + '" data-ask-opt="' + oIdx + '"'
                          + ' title="Answer with this option">' + inner + '</button></li>';
                      }
                      return '<li>' + inner + '</li>';
                    }).join('') + '</ul>'
                  : '';
                return '<div class="ask-user-block">' + headerHtml + questionHtml + optsHtml + '</div>';
              }).join('');
            }
            const inputDisclosure = renderToolInputDisclosure(b, renderedConversationId, ev.line);
            const commandDisclosure = renderToolCommandDisclosure(b, detail);
            const editDisclosure = renderEditDisclosure(b);
            const commandClass = (commandDisclosure || editDisclosure) ? ' has-command-disclosure' : '';
            const toolUseId = String(b.id || b.tool_use_id || '').trim();
            // ACP (kimi) permission requests carry their options on the block
            // — render them as inline buttons; the delegated click handler
            // POSTs /api/acp/approval with the chosen optionId.
            const acpPermOpts = (b.approval_required && Array.isArray(b.acp_options) && b.acp_options.length)
              ? '<span class="acp-perm-options">' + b.acp_options.map(function (o) {
                  const oid = String((o && (o.optionId || o.id)) || '');
                  const oname = String((o && (o.name || o.title || o.optionId || o.id)) || 'Choose');
                  return '<button type="button" class="acp-perm-opt"'
                    + ' data-acp-harness="' + escapeAttr(String(b.acp_harness || 'kimi')) + '"'
                    + ' data-acp-req="' + escapeAttr(String(b.acp_request_id)) + '"'
                    + ' data-acp-opt="' + escapeAttr(oid) + '"'
                    + ' title="Respond to this permission request">' + escapeHtml(oname) + '</button>';
                }).join('') + '</span>'
              : '';
            const acpPermClass = (b.approval_required && b.acp_request_id != null) ? ' acp-needs-approval' : '';
            // ACP tool blocks carry a terminal tool_status (completed/failed)
            // — map it to a class so group headers can aggregate run state.
            const toolStatusClass = b.tool_status === 'completed' ? ' tool-call-ok'
              : (b.tool_status === 'failed' ? ' tool-call-fail' : '');
            // A permission prompt must never collapse into the "Ran N commands"
            // group — the option buttons are the only way to answer it.
            if (b.approval_required) hasNonTool = true;
            blockParts.push('<div class="tool-call' + toolClass + detail.className + commandClass + acpPermClass + toolStatusClass + '" data-tool-detail="' + escapeAttr(detail.full) + '" data-cc-orch="' + escapeAttr(Array.from(_ccOrchScan(b.command || detail.full)).join('|')) + '" data-tool-source="' + escapeAttr(source) + '" data-tool-use-id="' + escapeAttr(toolUseId) + '">'
              + '<span class="arrow">-></span> '
              + sourceHtml
              + '<span class="tool-name" data-tool-name="' + escapeAttr(b.name || '') + '">' + escapeHtml(displayName) + '</span>'
              + (askBody
                  ? askBody
                  : (detail.display ? ' <span class="tool-detail" title="' + escapeAttr(detail.full) + '">' + escapeHtml(detail.display) + '</span>' : ''))
              + inputDisclosure
              + commandDisclosure
              + editDisclosure
              + acpPermOpts
              + '</div>');
            lastToolPartIdx = blockParts.length - 1;
            // CCC-50: render the image inline under file tool-calls that point
            // at one. Pushed as a separate part AFTER lastToolPartIdx so the
            // per-turn token chip still merges into the tool-call line, not the
            // image.
            // CCC-122: Codex views images through its own `view_image` tool
            // (and antigravity reads them via view_file) — same inline render.
            if (isFileToolName(b.name) || /^view_(?:image|file)$/i.test(String(b.name || ''))) {
              const imgHtml = inlineToolImageHtml(detail);
              if (imgHtml) blockParts.push(imgHtml);
            }
          } else if (b.kind === 'text') {
            if (b.text) agentAnswerParts.push(String(b.text));
            // A structured (JSON-mode) reply carries a distilled one-liner —
            // the router/classifier decision — shown above the raw JSON so the
            // gist is legible without parsing the object by eye.
            if (b.summary) {
              blockParts.push('<div class="assistant-decision" title="Distilled from the structured reply below">' + escapeHtml(String(b.summary)) + '</div>');
            }
            blockParts.push('<div class="assistant-text" dir="auto">' + renderMarkdown(b.text) + '</div>');
            hasNonTool = true;
          } else if (b.kind === 'devin_summary') {
            // CCC-1174: Devin's compactor drops a "Request and Intent / …
            // / Current state" context handoff into the transcript as an
            // ordinary assistant message every few turns — a multi-screen
            // wall each time. Render it as ONE collapsed <details>; the
            // CCC-913 per-section split stays available once expanded.
            // Not pushed to agentAnswerParts: it is a checkpoint, not the
            // reply (also keeps it out of read-aloud/copy).
            const _sumText = String(b.text || '');
            if (_sumText.trim()) {
              blockParts.push('<details class="ctx-usage-details devin-summary-details devin-context-summary">'
                + '<summary title="' + escapeAttr('Auto-generated context checkpoint from the Devin compactor - not a reply.') + '">'
                + 'Context summary</summary>'
                + renderDevinSummaryBlock(_sumText)
                + '</details>');
              hasNonTool = true;
              if (state._compactRun) _compactRunAdoptDevinSummary(ev);
            }
          } else if (b.kind === 'skill_loaded') {
            blockParts.push(renderCodexSkillLoadedCard(b));
            hasNonTool = true;
          } else if (b.kind === 'plan') {
            // ACP plan snapshot (kimi TodoList): compact todo card.
            const planHtml = _planEntriesHtml(b.entries);
            if (planHtml) {
              blockParts.push(planHtml);
              hasNonTool = true;
            }
          } else if (b.kind === 'diff') {
            const diffHtml = _turnDiffBlockHtml(b);
            if (diffHtml) {
              blockParts.push(diffHtml);
              hasNonTool = true;
            }
          } else if (b.kind === 'image_generation') {
            const imgGenHtml = _imageGenerationBlockHtml(b, ev);
            if (imgGenHtml) {
              blockParts.push(imgGenHtml);
              hasNonTool = true;
            }
          } else if (b.kind === 'thinking') {
            if (b.signature_only || !b.text) {
              // Signature-only: reasoning text not persisted (only signature survived
              // a resume, or the model emitted encrypted thinking). Show a compact
              // non-expandable marker so the turn stays visible as a record that
              // thinking occurred — with a hint explaining why there's no body
              // (CCC-454: a bare "thought" read as a rendering bug).
              blockParts.push('<div class="thinking-block thinking-block-silent" title="The model thought here, but the reasoning text was not saved to the transcript (only an encrypted signature) - there are no details to show."><span class="thinking-toggle">💭 thought</span> <span class="thinking-silent-note">(reasoning not saved to transcript)</span></div>');
              // Deliberately NOT hasNonTool = true here: a silent-thought
              // marker carries zero real information (no reasoning text, no
              // tool, no answer) — it should NOT block a turn that also has
              // a tool call from being classified `tool-only` and fused into
              // the compact "Ran N commands" group like a normal tool-only
              // turn. Before this, EVERY silent-thought+single-tool turn
              // rendered as its own full-height block (header + silent
              // marker + standalone token line + raw tool-call line) instead
              // of collapsing into the group's one-line "Completed X"
              // summary — the "lots of wasted space" a whole run of these
              // produces back-to-back.
            } else {
              // Text present: visible block with an expandable body. Short
              // thinking stays expanded regardless of Verbose (CCC-942);
              // LONG thinking clamps to a two-line teaser — a single click
              // (on the label or the teaser itself) unfolds it. Devin/ACP
              // models routinely emit multi-page reasoning, and always-open
              // bodies drowned the actual transcript.
              const _tText = String(b.text || '');
              const _tLong = _tText.length > 280;
              blockParts.push('<div class="thinking-block"><span class="thinking-toggle" title="Click to expand / collapse"'
                + ' onclick="this.parentElement.querySelector(\'.t-body\').classList.toggle(\'is-clamped\')">💭 Thinking</span>'
                + '<div class="t-body' + (_tLong ? ' is-clamped' : '') + '"'
                + (_tLong ? ' onclick="this.classList.remove(\'is-clamped\')"' : '')
                + '>' + escapeHtml(_tText) + '</div></div>');
              hasNonTool = true;
            }
          }
        }
        // Per-turn token chips. Set for Antigravity (from the trajectory's
        // modelUsage) and for Claude (from each assistant message's
        // message.usage, server-side). Tier 2 now carries the live "working"
        // signal, so a standalone token line below the turn is just noise
        // (CCC-30). Merge the count into the END of the last tool-call line
        // when there is one; only fall back to a standalone line for turns
        // with no tool call to hang it on.
        // Did this turn render anything visible? A turn whose only block was
        // signature-only ("silent") thinking now renders nothing — hide it
        // rather than leave an empty row (or a lone token line).
        const hadTool = lastToolPartIdx >= 0;
        const renderedSomething = hadTool || hasNonTool;
        if (renderedSomething && (ev.tokens_in || ev.tokens_out || ev.tokens_thinking || ev.tokens_cached)) {
          const chipCached = Number(ev.tokens_cached || (ev.token_usage && (ev.token_usage.cache_read_input_tokens || ev.token_usage.cached_input_tokens)) || 0);
          const chipText = _formatAntigravityTokenChips(ev.tokens_in, ev.tokens_out, ev.tokens_thinking, chipCached);
          // The cache-adjusted total rides at the end of the same row as just
          // its number (title explains it) instead of a second, louder line.
          const adjHtml = (Number(ev.tokens_in) || Number(ev.tokens_out))
            ? _cacheAdjustedInlineHtml(ev.tokens_in, ev.tokens_out, chipCached, ev.model) : '';
          if (chipText) {
            const chipTitle = 'Input:    ' + (Number(ev.tokens_in) || 0).toLocaleString() + ' tokens'
              + '\nCached input:    ' + chipCached.toLocaleString() + ' tokens'
              + '\nOutput:   ' + (Number(ev.tokens_out) || 0).toLocaleString() + ' tokens'
              + '\nThinking: ' + (Number(ev.tokens_thinking) || 0).toLocaleString() + ' tokens';
            if (lastToolPartIdx >= 0) {
              const mergedChip = '<span class="event-token-chips is-merged" title="' + escapeAttr(chipTitle) + '">' + escapeHtml(chipText) + adjHtml + '</span>';
              blockParts[lastToolPartIdx] = blockParts[lastToolPartIdx].replace(/<\/div>\s*$/, mergedChip + '</div>');
            } else {
              blockParts.push('<div class="event-token-chips" title="' + escapeAttr(chipTitle) + '">' + escapeHtml(chipText) + adjHtml + '</div>');
            }
          } else if (adjHtml) {
            blockParts.push('<div class="event-token-chips">' + adjHtml + '</div>');
          }
          const cacheMiss = _cacheMissCallout(ev.tokens_in, chipCached);
          if (cacheMiss) {
            blockParts.push('<div class="event-token-chips cache-miss" title="' + escapeAttr(
              'Cache read covered ' + chipCached.toLocaleString() + ' of ' + (Number(ev.tokens_in) || 0).toLocaleString() + ' input tokens.'
            ) + '">' + escapeHtml(cacheMiss) + '</div>');
          }
          _recordCacheMissLog(renderedConversationId, ev, ev.tokens_in, chipCached);
        }
        html += blockParts.join('');
        if (renderedSomething) html += eventModelMetaHtml(ev, 'assistant');
        if (!hasNonTool) div.classList.add('tool-only');
        if (!renderedSomething) div.classList.add('is-silent-thinking-only');
        // Hide bare no-op acknowledgments ("No response requested." etc.):
        // filler an agent emits when the Claude Code harness resume-nudges an
        // idle session ("Continue from where you left off"). Zero signal, and
        // confusing in the transcript. Only hide when the WHOLE turn is the
        // filler phrase and carries no tool call.
        const _ackBlocks = Array.isArray(ev.blocks) ? ev.blocks : [];
        const _ackText = _ackBlocks.filter(b => b && b.kind === 'text').map(b => b.text || '').join(' ');
        const _ackHasTool = _ackBlocks.some(b => b && b.kind === 'tool_use');
        if (!_ackHasTool && _isNoopAckText(_ackText)) div.classList.add('is-noop-ack');
        div.innerHTML = html;
        div.querySelectorAll('details.tool-input-disclosure').forEach(function (details) {
          details.addEventListener('toggle', function () {
            if (details.open) loadToolInputDisclosure(details);
          });
          if (details.open) loadToolInputDisclosure(details);
        });
        div._agentAnswerText = agentAnswerParts.join('\n\n').trim();
      } else if (ev.type === 'result') {
        const dur = typeof ev.duration_ms === 'number' ? (ev.duration_ms / 1000).toFixed(1) + 's' : ev.duration_ms;
        let statsHtml = '';
        if (ev.token_usage && typeof ev.token_usage === 'object') {
          const u = ev.token_usage;
          const input = Number(u.input_tokens || 0);
          const cached = Number(u.cached_input_tokens || 0);
          const output = Number(u.output_tokens || 0);
          const reasoning = Number(u.reasoning_output_tokens || 0);
          const total = Number(u.total_tokens || 0);
          const cachedText = cached ? ' (' + _formatTokens(cached) + ' cached)' : '';
          const reasoningText = reasoning ? ' (' + _formatTokens(reasoning) + ' reasoning)' : '';
          const tokenTip = 'Input:           ' + input.toLocaleString() + ' tokens\n'
            + 'Cached input:    ' + cached.toLocaleString() + ' tokens\n'
            + 'Output:          ' + output.toLocaleString() + ' tokens\n'
            + 'Reasoning:       ' + reasoning.toLocaleString() + ' tokens'
            + (total ? '\nTotal:           ' + total.toLocaleString() + ' tokens' : '');
          statsHtml += '<span title="' + escapeHtml(tokenTip) + '">Tokens: in ' + escapeHtml(_formatTokens(input) + cachedText)
            + ' · out ' + escapeHtml(_formatTokens(output) + reasoningText) + '</span>';
        } else if (ev.cost_usd != null && ev.cost_usd !== '') {
          const cost = typeof ev.cost_usd === 'number' ? '$' + ev.cost_usd.toFixed(4) : ev.cost_usd;
          // simple-tech-cost: gated behind the Simple-mode technical strip
          // (see app.css .ccc-simple-tech-open) — cost is "technical", not
          // shown by default in Simple mode.
          statsHtml += '<span class="simple-tech-cost">Cost: ' + escapeHtml(String(cost)) + '</span>';
        }
        if (dur !== undefined && dur !== null && dur !== '') {
          statsHtml += '<span>Duration: ' + escapeHtml(String(dur)) + '</span>';
        }
        // A turn can end in error — most importantly a token/quota/subscription
        // limit. Don't mislabel that as "Done": say what actually happened so
        // the user knows it stopped because it's out of tokens, not because it
        // finished.
        const _outcome = _resultOutcomeInfo(ev);
        if (_outcome) {
          // Webui panes: a repeated failed delivery (e.g. the Codex queue
          // pump retrying against a busy turn) lands one identical error
          // footer per attempt. Collapse consecutive identical outcomes —
          // a genuinely stopped session keeps exactly one banner.
          if (_kimiPane && _outcome.kind !== 'silent') {
            const _sig = _outcome.kind + '|' + _outcome.label + '|' + String(_outcome.detail || '');
            const _prevErrs = $view.querySelectorAll('.event.result.result-error');
            const _prevErr = _prevErrs.length ? _prevErrs[_prevErrs.length - 1] : null;
            if (_prevErr && _prevErr.dataset.resultSig === _sig) continue;
            div.dataset.resultSig = _sig;
          }
          if (_outcome.kind === 'silent') {
            div.classList.add('result-silent');
          } else {
            div.classList.add('result-error', 'result-' + _outcome.kind);
          }
          div.innerHTML = '<span class="label">' + escapeHtml(_outcome.label) + '</span>'
            + '<span class="line-num">L' + ev.line + '</span>'
            + tsSpan(ev.ts)
            + (_outcome.detail ? '<div class="result-error-detail">' + escapeHtml(_outcome.detail) + '</div>' : '')
            + '<div class="stats">' + statsHtml + '</div>';
        } else {
          // kimi result events carry no duration/token stats — an empty
          // "Done" footer is pure noise, so hide it (kimi-web only shows a
          // muted footer when there's real meta to show).
          if (_kimiPane && !statsHtml) div.classList.add('kimi-result-empty');
          div.innerHTML = '<span class="label">Done</span>'
            + '<button type="button" class="result-copy-agent-answer" data-read-agent-answer title="Read agent answer aloud" aria-label="Read agent answer aloud">&#128266;</button>'
            + '<button type="button" class="result-copy-agent-answer" data-copy-agent-answer title="Copy agent answer" aria-label="Copy agent answer">&#128203;</button>'
            + '<span class="line-num">L' + ev.line + '</span>'
            + tsSpan(ev.ts)
            + '<div class="stats">' + statsHtml + '</div>';
        }
      } else if (ev.type === 'tool_result') {
        // If this result closes out a Task delegate (its tool_use_id matches
        // an active subagent tab), mark the tab completed so the auto-close
        // watchdog can fire when the user navigates away.
        if (ev.tool_use_id && $view) {
          const _state = $view._tabState;
          if (_state && _state.tasks && _state.tasks[ev.tool_use_id]) {
            _convPaneMarkTaskCompleted($view, ev.tool_use_id);
          }
        }
        const text = eventTextString(ev.text).trim();
        const _answerConfirm = /^User answered the question/.test(text);
        const _isErr = !!ev.is_error && !_answerConfirm;
        const shouldRenderResult = !!text || _isErr;
        if (text && ev.tool_use_id) {
          const escId = (window.CSS && CSS.escape) ? CSS.escape(String(ev.tool_use_id)) : String(ev.tool_use_id);
          const row = $view.querySelector('.tool-call[data-tool-use-id="' + escId + '"], .kimi-tool[data-tool-use-id="' + escId + '"]');
          if (row) {
            row.dataset.ccOrch = _ccOrchResultLabels((row.dataset.ccOrch || '').split('|').filter(Boolean), text).join('|');
            const group = row.closest('.kimi-tool-group');
            if (group) _kimiUpdateToolGroupHead(group);
          }
        }
        if (_kimiPane) {
          // Webui panes: fold the result into the matching `.kimi-tool` row
          // (codex emits tool_result as a separate event keyed by call_id;
          // kimi rows usually carry output_preview from the server already,
          // in which case this is a no-op). The event div stays unappended —
          // empty .event.tool_result rows are hidden by CSS anyway.
          if (shouldRenderResult && ev.tool_use_id) {
            const escId = (window.CSS && CSS.escape) ? CSS.escape(String(ev.tool_use_id)) : String(ev.tool_use_id);
            const row = $view.querySelector('.kimi-tool[data-tool-use-id="' + escId + '"]');
            if (row && !row.querySelector('.kimi-tool-io.is-output')) {
              let body = row.querySelector('.kimi-tool-body');
              if (!body) {
                const head = row.querySelector('.kimi-tool-head');
                if (head && !head.getAttribute('onclick')) {
                  head.setAttribute('onclick', "this.parentElement.classList.toggle('open')");
                  const car = document.createElement('span');
                  car.className = 'kimi-tool-car';
                  car.innerHTML = state._KIMI_GLYPHS['chevron-right'];
                  head.appendChild(car);
                }
                body = document.createElement('div');
                body.className = 'kimi-tool-body';
                body.innerHTML = '<div class="kimi-tool-body-pad"></div>';
                row.appendChild(body);
              }
              const pad = body.querySelector('.kimi-tool-body-pad') || body;
              const out = document.createElement('pre');
              out.className = 'kimi-tool-io is-output';
              out.textContent = text || 'No error details returned.';
              pad.appendChild(out);
              if (ev.output_truncated) {
                const load = document.createElement('button');
                load.type = 'button';
                load.className = 'tool-result-expand-toggle';
                load.textContent = 'Show full output';
                if (ev.line != null) {
                  load.dataset.outputUrl = '/api/conversations/' + encodeURIComponent(renderedConversationId)
                    + '/tool-output?line=' + encodeURIComponent(ev.line)
                    + '&tool_use_id=' + encodeURIComponent(ev.tool_use_id);
                } else {
                  const context = window.CCCCodexClientContext($view.closest('.conv-pane'));
                  load.dataset.outputUrl = '/api/codex/client/tool-output?thread_id=' + encodeURIComponent(context.threadId)
                    + '&repo_path=' + encodeURIComponent(context.repoPath)
                    + '&environment_id=' + encodeURIComponent(context.environmentId || '')
                    + '&turn_id=' + encodeURIComponent(ev.turn_id || '')
                    + '&item_id=' + encodeURIComponent(ev.tool_use_id);
                }
                pad.appendChild(load);
                if (row.classList.contains('open')) _loadCompleteToolOutput(load);
              }
              // Late-landing output (codex call_id results, wire-tail
              // tool_result folds) gets the same collapsed-row peek a
              // server-rendered output_preview would have had.
              if (!row.querySelector(':scope > .kimi-tool-peek')) {
                const peekHtml = _kimiToolPeekHtml(text);
                if (peekHtml) body.insertAdjacentHTML('beforebegin', peekHtml);
              }
              if (_isErr) {
                row.classList.add('err');
                const st = row.querySelector('.kimi-tool-status');
                if (st) {
                  st.className = 'kimi-tool-status error';
                  st.setAttribute('aria-label', 'failed');
                  st.innerHTML = state._KIMI_GLYPHS['close'];
                }
              }
              const grp = row.parentElement && row.parentElement.closest('.kimi-tool-group');
              if (grp) _kimiUpdateToolGroupHead(grp);
            }
          }
          // Images stay visible outside collapsed tools. Regrouping bundles
          // cards separated only by tools into a shared thumbnail gallery. Keyed by
          // (line, idx) so replays, reloads and repeat polls never duplicate.
          if (Array.isArray(ev.images) && ev.images.length && ev.tool_use_id) {
            const escId = (window.CSS && CSS.escape) ? CSS.escape(String(ev.tool_use_id)) : String(ev.tool_use_id);
            const row = $view.querySelector('.kimi-tool[data-tool-use-id="' + escId + '"]');
            const fresh = ev.images.filter(img => {
              const k = _toolResultImageKey(img);
              const esc = (window.CSS && CSS.escape) ? CSS.escape(k) : k;
              return !$view.querySelector('[data-tool-image-key="' + esc + '"]');
            });
            const cardHtml = row && fresh.length
              ? _toolResultImagesCardHtml(Object.assign({}, ev, { images: fresh }), renderedConversationId) : '';
            if (cardHtml) {
              row.insertAdjacentHTML('afterend', cardHtml);
              const turn = row.closest('.kimi-turn');
              if (turn) _kimiRegroupTools(turn);
            }
          }
          continue;
        }
        // Inline the result text under the most recent tool_call in the
        // current group. If we can't find one, drop the marker silently —
        // empty .event.tool_result rows are hidden by CSS anyway.
        if (shouldRenderResult && state._currentToolGroup) {
          const last = toolCallForResult(state._currentToolGroup, ev.tool_use_id || '');
          if (last) last.dataset.ccOrch = _ccOrchResultLabels((last.dataset.ccOrch || '').split('|').filter(Boolean), text).join('|');
          if (last && !last.querySelector('.tool-result-output, .tool-result-code-preview')) {
            // Stamp this output with the tool_result event's own ts (when
            // the result actually landed in the JSONL), not render time.
            // The parent .event.tool_result row is display:none, so the
            // CSS ::before never renders. Plain outputs bake the prefix into
            // their <pre>; structured code previews rely on the group stamp.
            const _toolTs = eventStamp(ev.ts) || nowStamp();
            // CCC-46: a relayed AskUserQuestion answer comes back through a
            // PreToolUse `deny` reason ("User answered the question…"), which
            // Claude Code records as an is_error tool_result. It's a SUCCESS,
            // not an error — don't paint it red / "Tool error".
            const resultText = text || 'No error details returned.';
            const commandSucceeded = !_isErr && isSuccessfulCommandToolResult(last, text);
            if (commandSucceeded) last.classList.add('tool-call-ok');
            if (!_isErr && isRoutineSuccessfulToolResult(last, text)) {
              last.classList.add('tool-call-ok');
              stampCurrentToolGroup(_toolTs);
              updateToolGroupLabel(state._currentToolGroup);
              continue;
            }
            const codePreview = (_isErr || _answerConfirm) ? null : renderToolCodePreview(last, resultText);
            const out = codePreview || document.createElement('pre');
            if (codePreview) {
              out.dataset.renderTs = _toolTs;
            } else {
              out.className = 'tool-result-output' + (_isErr ? ' is-error' : '')
                + (_answerConfirm ? ' is-answer-confirm' : '');
              out.dataset.resultLabel = _answerConfirm ? 'Answer sent' : toolResultOutputLabel(last, _isErr);
              out.dataset.renderTs = _toolTs;
              // textContent for safety, then if the text has URLs swap to
              // an escaped+linkified innerHTML so they're one-click. The
              // `[J <ts>]` prefix is also preserved in escaped form.
              const _prefix = '[J ' + _toolTs + ']  ';
              if (resultText.indexOf('http') !== -1) {
                out.innerHTML = _linkifyEscapedUrls(escapeHtml(_prefix + resultText));
              } else {
                out.textContent = _prefix + resultText;
              }
            }
            // Bump the group's header stamp too so it reflects the most
            // recent activity (tool_use OR tool_result), not just tool_use.
            // CSS ::before reads attr() from its own element, so the header
            // node needs the attribute set, not just the parent group.
            stampCurrentToolGroup(_toolTs);
            last.appendChild(out);
            // CCC-500: plain-text results are capped at max-height:200px with
            // only a scrollbar as a hint they're clipped — add an explicit
            // toggle when the content actually overflows that box. Deferred
            // to the batched read pass after the loop (no layout read here).
            if (!codePreview) _overflowChecks.push(out);
            // CCC-501: refresh the group header even on failure/non-command
            // results so the collapsed row's result snippet reflects what
            // actually came back, not just successful shell commands.
            updateToolGroupLabel(state._currentToolGroup);
          }
        }
        continue;
      } else if (ev.type === 'codex_request') {
        // Codex single-renderer merge, slice 3: a pending approval/question/
        // permission/elicitation request, rendered inline instead of only in
        // the live strip. Falls through to the shared non-tool-only append
        // logic below like any other event `div`. Answers route through the
        // same `/api/codex/client/respond` RPC action the native app-server
        // pane already uses (see the delegated click handler near the other
        // `.ask-user-option-pick`/`.acp-perm-opt` handlers).
        div.classList.add('codex-request-event');
        div.innerHTML = _codexRequestCardHtml(ev, renderedConversationId);
      } else if (ev.type === 'attachment') {
        const badge = ev.hook_name || ev.hook_event || 'hook';
        const rawText = String(ev.text || ev.stdout || ev.content || ev.stderr || '').trim();
        const firstLine = (rawText.split('\n')[0] || '').trim();
        const preview = truncate(firstLine, 120);
        const bodyHtml = rawText.indexOf('http') !== -1
          ? _linkifyEscapedUrls(escapeHtml(rawText))
          : escapeHtml(rawText);
        div.classList.add('attachment-event');
        // Compact mode hides routine hook output; failures stay visible.
        if (ev.stderr || (ev.exit_code != null && Number(ev.exit_code) !== 0)) div.classList.add('hook-failed');
        div.innerHTML = '<details class="hook-attachment-details"' + (convVerboseOn() ? ' open' : '') + '>'
          + '<summary class="hook-attachment-summary">'
          + '<span class="hook-arrow" aria-hidden="true">▶</span>'
          + '<span class="label">Hook</span>'
          + '<span class="hook-attachment-badge">' + escapeHtml(badge) + '</span>'
          + (preview ? '<span class="hook-attachment-preview">' + escapeHtml(preview) + '</span>' : '')
          + (ev.line != null ? '<span class="line-num">L' + ev.line + '</span>' : '')
          + tsSpan(ev.ts)
          + '</summary>'
          + '<pre class="hook-attachment-body">' + bodyHtml + '</pre>'
          + '</details>';
      }

      if (ev.type === 'assistant') {
        attachPresentationArtifactToAssistant($view, div, ev, renderedConversationId);
      }

      // Route tool-only assistant events into a fused group so the chat
      // pane reads "Ran N commands ▾" instead of one row per Bash/Read/Edit.
      // CCC-148: a signature-only-thinking turn is `tool-only` but carries NO
      // command — it's a `display:none` empty turn. Grouping it produced a
      // misleading "Ran 1 command" header the user could expand into nothing.
      // Exclude it so only real tool calls form command groups.
      const isToolOnly = div.classList.contains('tool-only')
        && !div.classList.contains('is-silent-thinking-only');
      if (isToolOnly) {
        if (!state._currentToolGroup || state._currentToolGroup !== _lastRealTranscriptChild($view)) {
          // No open group, or another event closed it — start a new one.
          const grp = document.createElement('div');
          grp.className = 'tool-call-group' + (convVerboseOn() ? '' : ' collapsed');
          const _grpTs = eventStamp(ev.ts) || nowStamp();
          grp.dataset.renderTs = _grpTs;
          grp.innerHTML =
              '<div class="tool-call-group-header" data-render-ts="' + _grpTs + '">'
            + '<span class="tcg-arrow">▶</span> <span class="tcg-label">Ran 1 command</span>'
            + '<span class="tcg-result"></span>'
            + '<span class="tcg-meta"></span>'
            + '</div>'
            + '<div class="tool-call-group-body"></div>';
          $view.appendChild(grp);
          grp.querySelector('.tool-call-group-header')
             .addEventListener('click', () => grp.classList.toggle('collapsed'));
          state._currentToolGroup = grp;
          state._currentToolCount = 0;
        }
        state._currentToolGroup.querySelector('.tool-call-group-body').appendChild(div);
        // AskUserQuestion and Kanban state writes carry conversation/control-
        // plane context. Keep those readable instead of hiding them behind a
        // compact tool batch summary.
        if (toolGroupCarriesConversationContext(state._currentToolGroup)) {
          state._currentToolGroup.classList.remove('collapsed');
          var _qArrow = state._currentToolGroup.querySelector('.tcg-arrow');
          if (_qArrow) _qArrow.textContent = '▼';
        }
        // Bump both stamps to this tool's own ts so the visible group
        // header reflects when the latest tool actually ran. The header's
        // own data-render-ts is what the CSS ::before reads.
        const _ts = eventStamp(ev.ts) || nowStamp();
        state._currentToolGroup.dataset.renderTs = _ts;
        const _tcgHeader = state._currentToolGroup.querySelector('.tool-call-group-header');
        _tcgHeader.dataset.renderTs = _ts;
        // Real user-facing time + line number on the far right — collapsed
        // groups previously showed neither (only a debug-mode ::before), so
        // a whole run of "▶ Completed X" rows read as anonymous, unlabeled
        // noise with no way to tell when/where each one happened.
        const _tcgMeta = _tcgHeader.querySelector('.tcg-meta');
        if (_tcgMeta) {
          _tcgMeta.innerHTML = tsSpan(ev.ts) + (ev.line != null ? '<span class="line-num">L' + ev.line + '</span>' : '');
        }
        state._currentToolCount += 1;
        state._currentToolGroup.dataset.toolCount = String(state._currentToolCount);
        const prevCodeReads = Number(state._currentToolGroup.dataset.codeReadCount || 0);
        state._currentToolGroup.dataset.codeReadCount = String(prevCodeReads + (toolCallLooksLikeCodeRead(div) ? 1 : 0));
        // Update the header label. Single-command groups get a smart
        // tool-specific summary ("Read foo.py"); multi-command groups of
        // source-file reads say what they are instead of generic commands.
        updateToolGroupLabel(state._currentToolGroup);
      } else {
        // Any non-tool-only event closes the current group.
        state._currentToolGroup = null;
        state._currentToolCount = 0;
        if (ev.live_key != null) {
          _upsertProvisionalNode($view, div, _existingProvisionalNode);
        } else {
          if (ev.line != null) {
            // Reconcile: a confirmed rollout row supersedes any provisional
            // row still standing in for it. A `task_complete` row (ev.type
            // === 'result') additionally clears every remaining provisional
            // row for that turn — the turn is over, the rollout is now the
            // sole authoritative record of it.
            const _staleProvisional = _findMatchingProvisionalNode($view, ev, div);
            if (_staleProvisional) _staleProvisional.remove();
            if (ev.type === 'result' && ev.turn_id != null) _removeStaleProvisionalsForTurn($view, ev.turn_id);
          }
          // The live answer may precede the rollout's first prompt flush.
          // Insert that prompt before its own provisional turn, not after it.
          const livePromptTurn = ev.type === 'user_text' && ev.turn_id != null
            ? Array.from($view.querySelectorAll('.event[data-live-key]'))
              .find(node => node.dataset.turnId === String(ev.turn_id))?.closest('.kimi-turn') : null;
          if (livePromptTurn) $view.insertBefore(div, livePromptTurn);
          else $view.appendChild(div);
        }
        if (ev.type === 'assistant' && !handedOffStreamingBubble) _convLiveRevealNewText(div, paneId, opts);
      }
    }
    } finally {
      if (_ownsRegroupBatch) _kimiFlushRegroups();
    }
    // Older browsers retain the original synchronous expansion check.
    if (_overflowChecks.length && typeof ResizeObserver !== 'function') addResultOverflowToggles(_overflowChecks);
    // Defensive sweep: a tool group whose body has no events renders as a
    // "Ran 1 command" header over an empty box (CCC-80 — seen after live-
    // stream races). It carries no information; drop it.
    $view.querySelectorAll('.tool-call-group').forEach(g => {
      if (!g.querySelector('.tool-call-group-body .event')) {
        if (g === state._currentToolGroup) { state._currentToolGroup = null; state._currentToolCount = 0; }
        g.remove();
      }
    });
    // Visual "Clear" watermark (CCC-474): a group whose events are ALL below
    // the watermark must hide as a whole — its header is not an .event and
    // would otherwise survive a re-render as an orphan "Ran N commands" row.
    if (_videoClearLine) {
      $view.querySelectorAll('.tool-call-group').forEach(g => {
        const evs = g.querySelectorAll('.tool-call-group-body .event');
        const hidden = g.querySelectorAll('.tool-call-group-body .event.ccc-video-cleared');
        g.classList.toggle('ccc-video-cleared', evs.length > 0 && evs.length === hidden.length);
      });
    }
    // Surface a tab per task-notification rendered from the JSONL — these
    // subagents never pass through the spawn stream that normally seeds tabs.
    try { _convPaneSyncJsonlTaskTabs($view); } catch (_) {}
    // Re-anchor the inline live-tool indicator at the bottom; new events
    // just appended would otherwise push past it.
    if (typeof updateLiveToolStrip === 'function') updateLiveToolStrip();
    if (typeof updateCodexStateBadge === 'function') updateCodexStateBadge();
    const foregroundRun = state._compactRun?.sid === state.currentSession?.id ? state._compactRun : null;
    const compactBoundary = events.find(e => e && e.type === 'system' && e.subtype === 'compact_boundary'
      && (foregroundRun?.source !== 'codex' || Date.parse(e.ts) >= foregroundRun.requestedAt));
    if (compactBoundary) {
      // The compact boundary proves the pending compact turn is over.
      clearOptimisticAgentIndicator($view);
      clearLiveGeneratingIndicator($view);
      if (state.currentSession.id) clearSessionSending(state.currentSession.id);
      const sid = compactBoundary.session || (state.currentSession && state.currentSession.id) || '';
      if (sid && state.currentSession && state.currentSession.id === sid) fetchSessionUsage(sid);
      // Authoritative "it happened" signal — it can land before the resume
      // summary does, so the card lands on its result here too.
      if (typeof completeCompactRun === 'function') completeCompactRun(sid);
    }
    // Keep the lifecycle card in the DOM across re-renders, anchored to the
    // summary it absorbed when there is one (chronological slot) and to the
    // tail while the run is still in flight.
    if (state._compactRun && state.currentSession && state._compactRun.sid === state.currentSession.id) {
      try { _compactRunClaimSummary($view); } catch (_) {}
      const _absorbed = $view.querySelector('.compact-resume-event.compact-absorbed-by-run');
      if (_absorbed) _compactRunAnchorTo(_absorbed);
      else if (!state._compactRun.anchored) _compactRunMount($view);
      _compactRunAdoptBoundary($view);
    }
    // Every compaction already on disk gets the same one-card treatment.
    try { _foldCompactRecords($view); } catch (_) {}
    // Same story for the spawn-log streaming bubble — keep it pinned to
    // the tail so it doesn't end up sandwiched between older JSONL events
    // and newer ones.
    if (state._streamingBubble && state._streamingBubble.parentNode === $view) {
      _reanchorToTailUnlessOnlyTransientBetween($view, state._streamingBubble);
    }
    // The app-server live overlay is agent-output-only. Queue/input lifecycle
    // belongs to the durable transcript and pending-input APIs; running these
    // reconciliation passes on a 900ms provisional snapshot can erase input
    // and steer cards that snapshot does not own.
    if (!opts.provisionalOverlay) {
      // Keep queued steer candidates at the composer, including durable server
      // queue events (which are not represented in `_pendingSends`).
      // Every server fetch includes the current durable pending overlay. If a
      // former server card is absent, that absence is authoritative.
      // Older history (load-earlier, first-prompt pin) carries no queue
      // overlay, so its absence there proves nothing (CCC-246).
      syncQueuedSteerTray($view, paneId,
        !opts.prepending && !!(opts.initialLoad || opts.queueSnapshot !== false));
      // Self-heal optimistic echoes only against durable user rows.
      if (state._pendingSends.length) {
        const _durableUserRows = [];
        for (const el of $view.querySelectorAll(
            '.event.user_text:not(.pending):not(.send-queued)'
            + ':not(.send-delivered):not(.not-acknowledged):not(.provisional)'
            + ':not(.peer-message):not(.steering-optimistic)')) {
          _durableUserRows.push(el);
        }
        if (_durableUserRows.length) {
          for (const p of state._pendingSends.slice()) {
            if (!p || !p.text) continue;
            if (!takeQueuedSendAcknowledgement(_durableUserRows, state._normSend(p.text), Number(p.ts || 0))) continue;
            p.element?._pendingRef?.resolveQueueReady?.(false);
            if (p.element && p.element.parentNode) p.element.parentNode.removeChild(p.element);
            if (p.timer) clearTimeout(p.timer);
            if (p.sid) clearSessionSending(p.sid);
            const _i = state._pendingSends.indexOf(p);
            if (_i >= 0) state._pendingSends.splice(_i, 1);
            const _pane = paneByPaneId(paneId);
            if (_pane && state.currentConversation) syncPendingSendsMapForConv(_pane, state.currentConversation);
          }
        }
      }
    }
    if (!opts.provisionalOverlay) {
      // Echoes follow new transcript content but precede the live status.
      // Appending them after Generating on every refresh made the status
      // ticker move back below them a second later. Walk backwards so several
      // pending sends keep their order without moving on unchanged refreshes.
      let anchor = null;
      for (let node = $view.lastElementChild; node && node.matches(
          '.conv-live-tool-inline, .conv-live-tool-strip, .conv-codex-state');
          node = node.previousElementSibling) anchor = node;
      for (let i = state._pendingSends.length - 1; i >= 0; i--) {
        const el = state._pendingSends[i].element;
        if (!el || el.parentNode !== $view) continue;
        if (el.nextElementSibling !== anchor) $view.insertBefore(el, anchor);
        anchor = el;
      }
    }
    // The optimistic "Sending…" pill needs two things:
    //   1) If the agent has already produced a real reply (assistant /
    //      tool_use / result event in this batch), kill it — "starting up"
    //      is over. Otherwise it sticks forever when the agent answers
    //      before the 5s sidecar poll catches a tool in flight.
    //   2) Otherwise (still waiting), re-anchor it to the tail so the
    //      newly-appended user_text bubble doesn't end up below the pill.
    const _agentReplied = events.some(e =>
      e && (e.type === 'assistant' || e.type === 'result' || e.type === 'tool_result')
    );
    if (_agentReplied) {
      clearOptimisticAgentIndicator($view);
      if (!opts.provisionalOverlay) {
        // A reply chunk proves the send started, not that the turn ended.
        // updateLiveToolStrip owns the live indicator; removing it here
        // makes active turns flicker between transcript and status ticks.
        if (state.currentSession.id) clearSessionSending(state.currentSession.id);
      }
    } else {
      const _optimistic = $view.querySelector('.conv-live-tool-inline.optimistic');
      if (_optimistic && _optimistic !== $view.lastElementChild) {
        $view.appendChild(_optimistic);
      }
      _anchorWakeBreakdown($view);
    }
    // Re-evaluate the end-of-session outcome banner now that the latest
    // events are painted (covers error tool-results and mid-action stops).
    updateSessionOutcomeBanner($view);
    // Fold before measuring or scrolling the transcript. Deferring this to
    // the mutation observer measures expanded tools, then shrinks the view
    // on the next frame and visibly jumps the reader during live updates.
    _syncThinkingRuns($view);
    if (document.body.classList.contains('conv-compact')) _ccMarkRuns($view);
    anchorCodexStateBadge($view);
    // Fold first so hidden tool history does not force an expanded layout.
    // Its observer measures the output if the reader later opens the history.
    if (_overflowChecks.length && typeof ResizeObserver === 'function') {
      if (!resultOverflowObserver) resultOverflowObserver = new ResizeObserver(entries => {
        const measurable = entries.filter(entry => !entry.target.isConnected || entry.contentRect.height > 0);
        for (const entry of measurable) resultOverflowObserver.unobserve(entry.target);
        addResultOverflowToggles(measurable.map(entry => entry.target));
      });
      for (const output of _overflowChecks) resultOverflowObserver.observe(output);
    }
    // When a compact-resume event just landed, scroll the boundary
    // INTO VIEW (top of the resume card) instead of the very bottom.
    // The collapsed card sits at the top of the user's gaze; the
    // pre-compact history is above and the post-resume conversation
    // (currently nothing) is below. Without this, scrollToEnd left
    // the user looking at "yesterday's last message" with the compact
    // boundary far below the fold — reading as "the log went back a
    // day" even though the new boundary was already rendered.
    const _newCompactCard = $view.querySelector('.compact-resume-event:last-child');
    const _hasNewCompactEvent = events.some(e => {
      if (!e || e.type !== 'user_text') return false;
      return /^This session is being continued from a previous conversation that ran out of context\b/.test(e.text || '');
    });
    if (!opts.prepending && _hasNewCompactEvent && _newCompactCard && typeof _newCompactCard.scrollIntoView === 'function') {
      _newCompactCard.scrollIntoView({ block: 'start', behavior: 'auto' });
      updateConversationEndAffordance($view);
    } else if (events.length > 0 && wasAtBottom) {
      // CCC-131: on a fresh (re)open, prefer the saved scroll position over a
      // forced bottom-scroll. wasAtBottom is always true here (the view was
      // just cleared), so without this every reopen jumped to the bottom/top.
      // Defer to a double-rAF so the write lands after markdown/code reflow,
      // and clamp (a tail-only reload may be shorter than when we left).
      const _saved = (opts && opts.initialLoad) ? state._convScrollByConv[state.currentConversation] : null;
      if (_saved && !_saved.atBottom) {
        const _top = _saved.top;
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const _max = Math.max(0, $view.scrollHeight - $view.clientHeight);
          $view.scrollTop = Math.min(_top, _max);
          updateConversationEndAffordance($view);
        }));
      } else {
        scrollConversationToEnd($view);
      }
    } else {
      updateConversationEndAffordance($view);
    }
    // Prepare after painting, including provisional live messages. Playback
    // rearm above deliberately ignores provisional rows, but caching must not.
    if (paneId === activePaneId() && (opts.initialLoad || events.some(ev =>
        ev && (ev.type === 'assistant' || ev.type === 'result')))) ;
    ffcRefreshForCurrent();
    updateSessionOutcomeBanner($view);
    _firePhoneModeReadIfDone(events, paneId);
    _ensureFirstUserPinned($view);
    // Re-hide the inline duplicate of a pending question after the transcript
    // rebuilds (it re-creates the .ask-user-block without the hide class).
    try { _syncLiveQuestionDuplicateHide(); } catch (_) {}
    try { syncUserMessageSteerButtons($view); } catch (_) {}
    // Conversation/control-plane tools otherwise collapse into the fused
    // "Ran N commands ▶" group. Their details are important context, so keep
    // those groups expanded after any transcript rebuild.
    try {
      $view.querySelectorAll('.tool-call-group.collapsed').forEach(function (g) {
        if (toolGroupCarriesConversationContext(g)) {
          g.classList.remove('collapsed');
          var _arrow = g.querySelector('.tcg-arrow');
          if (_arrow) _arrow.textContent = '▼';
        }
      });
    } catch (_) {}
    // Show Replay button whenever there are meaningful events and no replay is
    // active. The topbar button represents "the active pane" (it's hidden
    // outright in split mode via CSS, migrated into the rail in right-rail
    // mode) — gate on paneId === activePaneId() so a background render for
    // the OTHER split pane can't toggle it based on the wrong pane's content.
    try {
      if (state.$convReplayBtn && !state._convReplayActive && paneId === activePaneId()) {
        const debugOn = typeof debugModeEnabled === 'function' && debugModeEnabled();
        const hasMeaningful = !!$view.querySelector('.event.user_text:not(.is-pinned-in-sticky), .event.assistant .assistant-text');
        state.$convReplayBtn.style.display = (debugOn && hasMeaningful) ? 'inline-flex' : 'none';
      }
      // A replay in progress on THIS pane (or its synced partner pane) may
      // just have received new live-streamed content (SSE/poll) — sweep it
      // into the hidden queue instead of letting it show up ahead of the
      // reveal cursor.
      _convReplaySweepNewItems($view, paneId);
    } catch (_) {}
    try {
      refreshPresentationForPane(paneId, {
        followTail: _agentReplied && !(opts && (opts.initialLoad || opts.prepending)),
      });
    } catch (_) {}
    try {
      _reconcileAnnotationsForPane(paneId);
    } catch (_) {
      // Best-effort re-apply of stored annotations — never blocks rendering.
    }
    if (_codexPane || state.sessionSourceByConv?.[renderedConversationId] === 'antigravity') {
      markSpawnAssistantPaint(renderedConversationId, $view, paneId);
    }
    if (_codexPane) {
      queueMicrotask(() => {
        if (paneByPaneId(paneId)?.conversationId !== renderedConversationId) return;
        const paneEl = convPaneElById(paneId);
        // This is the main trigger point: it fires on every rollout render
        // for a Codex pane (initial open and every poll tick alike), which
        // is exactly the cadence a freshly-started or resumed turn needs.
        // Cloud threads have no local app-server overlay, so skip them.
        const _liveRow = (state.conversationsData || []).find(x => x && (x.id === renderedConversationId || x.session_id === renderedConversationId))
          || (Array.isArray(state.archiveData) ? state.archiveData.find(x => x && (x.id === renderedConversationId || x.session_id === renderedConversationId)) : null);
        if (_liveRow && _liveRow.codex_cloud) return;
        window.CCCCodexLiveSource?.start(paneEl);
      });
    }
    return true;
  }
    return { renderConversationEvents };
  }
  root.CCCConversationEvents = Object.freeze({ createRenderer });
})(typeof window === 'undefined' ? globalThis : window);
