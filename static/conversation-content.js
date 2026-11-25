/* Shared transcript content helpers, independent of dashboard state. */
(function (root) {
  'use strict';
  function createHelpers(text) {
    const { escapeHtml, _escapeHtmlChar, escapeAttr, unescapeHtml, truncate, normalizeMarkdownLinkTarget, isUnavailableMarkdownImageTarget, CODEX_INLINE_VIS_RE, linkifyCodexInlineVisuals, PASTED_IMG_RE, PASTED_IMG_MD_LINK_RE, ANNOTATION_IMG_RE, pastedImageTag, linkifyPastedImages, sessionQuickAction, decisionQuickActions, sessionQuickActions, ssbChipLabel, NEEDS_YOU_SAY_RE, needsYouSayPhrases, NEEDS_YOU_CMD_RE, NEEDS_YOU_REPLY_RE, needsYouActions, needsYouActionsHtml, renderSessionStateBlock, normalizeTaskNotificationField, taskNotificationTagText, parseTaskNotificationBlock, splitTaskNotificationSummary, taskNotificationPlainText, renderTaskNotificationBlock, renderNestedBulletList, renderDevinSummaryBlock, MD_RENDER_MAX_CHARS, renderMarkdown, _CB_LANG_ALIAS, _CB_LANG_PATTERNS, _linkifyEscapedUrls, INLINE_CODE_PATH_RE, _isPlaceholderPathToken, _isInternalApiPathToken, _shouldLinkifyInlineCodePath, highlightCode, renderCodeBlock, renderInline, linkifySessionRefs, WATCHTOWER_TICKET_REF_RE, linkifyWatchtowerTicketRefs, linkifyPath } = text;
  const SESSION_STATE_INSTRUCTION = "\n\nBefore your final reply, end with a block formatted EXACTLY like this (the Command Center dashboard parses it):\n<session-state>\nDID: <one sentence - what you actually changed/learned>\nINSIGHT: <one sentence - the main finding, root cause, or surprise>\nNEXT_STEP_USER: <one sentence - the exact next thing the user should do>\n</session-state>";

  const SIBLING_PROMPT_PREFIX_LC = 'you are a sibling claude code session';

  const SIBLING_HEADING_RE = /^##\s+(?:Feature|Task|Goal|Bug|Fix|Spec)\s*:\s*.+$/im;

  function extractSiblingTaskBody(s) {
    if (!s) return null;
    const head = String(s).replace(/^\s+/, '').slice(0, 80).toLowerCase();
    if (!head.startsWith(SIBLING_PROMPT_PREFIX_LC)) return null;
    const m = SIBLING_HEADING_RE.exec(s);
    if (!m) return null;
    // Slice from the heading to end-of-string. The body below the heading
    // is the actual task — keep it. The boilerplate before it is dropped.
    return String(s).slice(m.index).trim();
  }

  function cleanIssuePrompt(s) {
    if (!s) return s;
    let out = String(s);
    // Sibling-orchestrator spawn: strip the preamble down to the heading.
    // Done first so the rest of the cleanup applies to the real task body.
    const siblingBody = extractSiblingTaskBody(out);
    if (siblingBody) out = siblingBody;
    // Strip the session-state instruction older CCC builds appended to
    // spawn prompts. Use the constant so legacy cleanup follows template
    // edits automatically.
    if (SESSION_STATE_INSTRUCTION && out.indexOf(SESSION_STATE_INSTRUCTION) !== -1) {
      out = out.split(SESSION_STATE_INSTRUCTION).join('');
    }
    // Generic fallback: strip a "Before your final reply…<session-state>
    // …</session-state>" trailer even if the constant has shifted slightly
    // (whitespace, wording tweaks). Anchored to "Before your final reply"
    // so it can't eat unrelated user text.
    out = out.replace(/\n*Before your final reply[\s\S]*?<\/session-state>(?:\s*-->)?\s*$/i, '');
    return out
      // Old template
      .replace(/^\s*Fix GitHub issue #\d+:\s*/i, '')
      .replace(/\.?\s*Read the issue first with[^.]*?,\s*then implement the fix\.?\s*$/i, '')
      // Current template: "Fix issue #N — {title}\n\nRun `gh issue view N` …"
      .replace(/^\s*Fix issue #\d+\s*(?:-|-)\s*/i, '')
      .replace(/\n+Run `gh issue view \d+`[^\n]*(title may be truncated\)\.?)?\s*$/i, '')
      // Slash-command markup Claude Code injects into user messages
      // (`<command-name>/foo</command-name> <command-message>foo</command-message>
      // <command-args>bar</command-args>`). Collapse to `/foo bar` so the
      // sticky-header earlier-ask slot doesn't show raw XML-like tags.
      // Also handle the missing-args variant: when the user runs a bare
      // command like `/compact` (no args), the `<command-args>` tag is
      // omitted entirely. The strict-three-tag regex below didn't match
      // and the bare command got stripped by the catch-all, leaving an
      // EMPTY user_text — which read as "lots of text lost between
      // yesterday's last message and today's compact". Match either
      // shape so the typed command stays visible as a tiny user event.
      .replace(
        /<command-name>([^<]*)<\/command-name>\s*<command-message>[^<]*<\/command-message>(?:\s*<command-args>([^<]*)<\/command-args>)?/g,
        (_, name, args) => (name + (args ? ' ' + args : '')).trim()
      )
      // Drop the local-command-caveat / stdout wrappers Claude Code adds
      // around slash-command output — they're verbose plumbing the user
      // doesn't need to read inline. Keep their inner text (if any), but
      // strip the wrapping tags.
      .replace(/<local-command-(?:caveat|stdout)>[\s\S]*?<\/local-command-(?:caveat|stdout)>/g, '')
      .replace(/<command-(?:name|message|args)>[\s\S]*?<\/command-(?:name|message|args)>/g, '')
      .trim();
  }

  function eventTextString(value) {
    if (typeof value === 'string') return value;
    if (value == null) return '';
    const blockText = (item) => {
      if (typeof item === 'string') return item;
      if (item == null) return '';
      if (typeof item !== 'object') return String(item);
      if (typeof item.text === 'string') return item.text;
      const blockType = item.type || item.kind || '';
      if (blockType === 'input_image' || blockType === 'image') return '[image]';
      if (blockType) return '[' + String(blockType).replace(/_/g, ' ') + ']';
      try { return JSON.stringify(item); } catch (_) { return String(item); }
    };
    if (Array.isArray(value)) {
      return value.map(blockText).filter(Boolean).join('\n');
    }
    if (typeof value.text === 'string') return value.text;
    const valueType = value.type || value.kind || '';
    if (valueType === 'input_image' || valueType === 'image') return '[image]';
    if (valueType) return '[' + String(valueType).replace(/_/g, ' ') + ']';
    try { return JSON.stringify(value, null, 2); } catch (_) { return String(value); }
  }

  function splitFirstSentence(text) {
    if (!text) return ['', ''];
    const s = String(text).trim();
    // Split only on real sentence terminators (.!?) followed by whitespace.
    // A bare newline is NOT a split point — the user's first sentence often
    // wraps mid-clause (e.g. `btw the "Recap\nlatest assistant turn"`).
    const m = s.match(/^([\s\S]*?[.!?])\s+([\s\S]+)$/);
    if (!m) return [s, ''];
    return [m[1].trim(), m[2].trim()];
  }

  const SESSION_ENGINE_LABELS = {
    claude: 'Claude', codex: 'Codex', gemini: 'Gemini', cursor: 'Cursor',
    antigravity: 'Antigravity', hermes: 'Hermes', kimi: 'Kimi',
    copilot: 'Copilot', grok: 'Grok', copilotchat: 'Copilot Chat',
    devin: 'Devin', 'devin-cli': 'Devin',
    opencode: 'OpenCode',
    // sessionIconEngine never returns these two, but the pending-spawn card
    // looks labels up by raw source and they are real spawn targets.
    kilo: 'Kilo', pkood: 'pkood',
  };

  function rowReasoningEffort(row) {
    if (!row) return '';
    return String(row.reasoning_effort || row.effort || '').trim();
  }

  function formatModelEffort(model, effort, opts) {
    const o = opts || {};
    const modelText = String(model == null ? '' : model).trim() || String(o.fallback || '');
    const effortText = String(effort == null ? '' : effort).trim();
    if (!effortText) return modelText;
    if (o.style === 'verbose') {
      return modelText ? modelText + ' · effort ' + effortText : 'effort ' + effortText;
    }
    return modelText ? modelText + ' (' + effortText + ')' : effortText;
  }

  function clearLiveGeneratingIndicator($view) {
    ($view || document).querySelectorAll('.conv-live-tool-inline.is-generating').forEach(n => n.remove());
  }

  function _anchorWakeBreakdown($view) {
    if (!$view) return;
    const optimistic = $view.querySelector('.conv-live-tool-inline.optimistic');
    const breakdown = $view.querySelector('.conv-live-tool-inline.wake-breakdown');
    if (!breakdown) return;
    if (optimistic) {
      if (breakdown.previousElementSibling !== optimistic) {
        optimistic.parentNode.insertBefore(breakdown, optimistic.nextSibling);
      }
      return;
    }
    // No optimistic pill to anchor next to (already resolved into the card
    // itself). Once the user's own message lands as a durable transcript row
    // it re-renders at $view's true tail, which otherwise left this card
    // stranded above it — a jarring reorder mid-render. Reclaim the tail on
    // every poll tick so the card stays pinned below the newest content.
    // The Codex status pill (anchorCodexStateBadge) also claims the very
    // last slot, so sit just above it: two nodes both taking the tail
    // swapped places on every poll (CCC-222 flicker).
    const badge = $view.querySelector(':scope > .conv-codex-state');
    const target = badge ? badge.previousElementSibling : $view.lastElementChild;
    if (breakdown !== target) {
      $view.insertBefore(breakdown, badge || null);
    }
  }

  function isCommandActivityTool(tool) {
    const name = toolDisplayName(String(tool || ''));
    return name === 'Bash'
      || name === 'exec' // devin-cli
      || name === 'exec_command'
      || name === 'shell_command'
      || name === 'run_shell_command'
      || name === 'run_command';
  }

  function anchorCodexStateBadge($view) {
    if (!$view) return;
    const badges = $view.querySelectorAll(':scope > .conv-codex-state');
    const badge = badges[0];
    // A transcript refresh can preserve an older status alongside a new one.
    for (let i = 1; i < badges.length; i++) badges[i].remove();
    if (badge && badge !== $view.lastElementChild) $view.appendChild(badge);
  }

  function _reanchorToTailUnlessOnlyTransientBetween($view, el) {
    anchorCodexStateBadge($view);
    if (!el || el.parentNode !== $view || el === $view.lastElementChild) return;
    for (let n = el.nextElementSibling; n; n = n.nextElementSibling) {
      if (!n.classList || !(n.classList.contains('stream-bubble')
        || n.classList.contains('conv-live-tool-inline')
        || n.classList.contains('conv-live-tool-strip')
        || n.classList.contains('compact-run-card')
        || n.classList.contains('conv-codex-state'))) {
        $view.insertBefore(el, $view.querySelector(':scope > .conv-codex-state'));
        return;
      }
    }
  }

  function assistantMessageActionsHtml(ev) {
    const hasAssistantText = Array.isArray(ev && ev.blocks)
      && ev.blocks.some((b) => b && b.kind === 'text' && String(b.text || '').trim());
    if (!hasAssistantText) return '';
    return '<span class="assistant-message-actions" data-role="assistant-message-actions">'
      + '<button type="button" class="assistant-message-action" data-read-assistant-message title="Read assistant message aloud" aria-label="Read assistant message aloud">&#128266;</button>'
      + '<button type="button" class="assistant-message-action" data-copy-assistant-message title="Copy assistant message" aria-label="Copy assistant message">&#128203;</button>'
      + '<button type="button" class="assistant-message-action" data-open-assistant-md title="Copy and open as MD file" aria-label="Copy and open as MD file">&#128221;</button>'
      + '</span>';
  }

  function queuedRowDeliveryId(el) {
    if (!el) return '';
    if (el.dataset && el.dataset.deliveryId) return String(el.dataset.deliveryId);
    const entry = el._pendingRef && el._pendingRef.entry;
    return (entry && entry.deliveryId) ? String(entry.deliveryId) : '';
  }

  function pastedImageThumbsHtml(text) {
    if (!text) return '';
    const seen = new Set();
    for (const re of [PASTED_IMG_RE, ANNOTATION_IMG_RE]) {
      const rx = new RegExp(re.source, 'gi');
      let m;
      while ((m = rx.exec(String(text)))) seen.add(m[1]);
    }
    if (!seen.size) return '';
    return Array.from(seen, p => '<a href="/api/pasted-image?path=' + encodeURIComponent(p)
      + '" target="_blank" rel="noopener">' + pastedImageTag(p) + '</a>').join('');
  }

  function renderCodexUserText(text) {
    const parts = String(text || '').split(/((?:\/|~\/)[^\s<>"']*\/command-center\/attachments\/[^\s<>"']+)/g);
    return parts.map((part, index) => {
      if (index % 2 === 0) return linkifyPastedImages(escapeHtml(part));
      const filename = part.split('/').pop();
      const extension = (filename.match(/\.([a-z0-9]{1,10})$/i) || [])[1];
      const label = /^attachment-[a-f0-9]{16,}\./i.test(filename)
        ? (extension ? extension.toUpperCase() + ' attachment' : 'Attachment') : filename;
      return '<a role="button" tabindex="0" class="path-link codex-attachment" data-path="'
        + escapeAttr(part) + '" title="' + escapeAttr(part) + '">'
        + '<span aria-hidden="true">▤</span> ' + escapeHtml(label) + '</a>';
    }).join('');
  }

  function tsSpan(ts) {
    if (!ts) return '';
    const d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    const diffHour = Math.floor(diffMin / 60);
    const pad = n => String(n).padStart(2, '0');
    const hhmm = pad(d.getHours()) + ':' + pad(d.getMinutes());

    let label;
    if (diffMs < 60000) {
      // Also catches small negative skews — treat as "just now" rather than showing "N minutes ago" in the future.
      label = 'just now';
    } else if (diffMin < 60) {
      label = diffMin === 1 ? '1 minute ago' : diffMin + ' minutes ago';
    } else if (diffHour < 5) {
      label = diffHour === 1 ? '1 hour ago' : diffHour + ' hours ago';
    } else {
      const sameDay = d.getFullYear() === now.getFullYear()
        && d.getMonth() === now.getMonth()
        && d.getDate() === now.getDate();
      if (sameDay) {
        label = hhmm;
      } else {
        // Yesterday check — compare against a date object rolled back one day
        // rather than (now - 24h), so DST transitions don't misclassify.
        const yest = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
        const isYesterday = d.getFullYear() === yest.getFullYear()
          && d.getMonth() === yest.getMonth()
          && d.getDate() === yest.getDate();
        if (isYesterday) {
          label = 'Yesterday · ' + hhmm;
        } else {
          const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
          label = months[d.getMonth()] + ' ' + d.getDate() + ' · ' + hhmm;
        }
      }
    }
    const iso = d.toLocaleString();
    // data-ts carries the raw timestamp so refreshMsgTimestamps() can
    // recompute the label later — these spans are baked into innerHTML once
    // at render time and otherwise freeze at whatever tier they started in
    // (e.g. "just now" forever) as real time moves on.
    return '<span class="msg-ts" data-ts="' + escapeAttr(ts) + '" title="' + escapeHtml(iso) + '">' + escapeHtml(label) + '</span>';
  }

  function parseCompactResumeBlock(text) {
    const src = String(text || '');
    if (!/^This session is being continued from a previous conversation that ran out of context\b/.test(src)) {
      return null;
    }
    const firstParaEnd = src.indexOf('\n\n');
    const intro = (firstParaEnd > 0 ? src.slice(0, firstParaEnd) : src).trim();
    const body = (firstParaEnd > 0 ? src.slice(firstParaEnd + 2) : '').trim();
    return { intro, body };
  }

  function _compactEngineLabel(source) {
    if (typeof SESSION_ENGINE_LABELS !== 'undefined' && SESSION_ENGINE_LABELS[source]) {
      return SESSION_ENGINE_LABELS[source];
    }
    return 'Claude';
  }

  function _compactTokenLabel(n) {
    if (typeof _formatTokens === 'function') return _formatTokens(n);
    return String(n || 0);
  }

  function _compactElapsedLabel(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    const m = Math.floor(s / 60);
    return m + ':' + String(s % 60).padStart(2, '0');
  }

  const _COMPACT_FOLD_RADIUS = 6;

  function _compactRowIsFoldable(el) {
    if (!el || !el.classList) return null;
    if (el.classList.contains('compact-boundary-row')) return 'boundary';
    if (el.classList.contains('compact-resume-event')) return null;
    if (el.classList.contains('user_text')) {
      const um = el.querySelector('.user-msg');
      const t = ((um && (um.getAttribute('data-raw-text') || um.textContent)) || '').trim();
      // The `/compact` command marker and the empty local-command-stdout row
      // beside it — plumbing the card already represents.
      if (!t || /^\/compact\b/i.test(t)) return 'marker';
    }
    return null;
  }

  function _foldCompactRecords($view) {
    if (!$view) return;
    $view.querySelectorAll('.compact-resume-event').forEach((summary) => {
      if (summary.classList.contains('compact-absorbed-by-run')) return;
      if (summary.classList.contains('compact-record-hidden')) return;
      if (summary.dataset.compactFolded === '1') return;
      const eaten = [];
      let boundary = null;
      for (const dir of ['previousElementSibling', 'nextElementSibling']) {
        let n = summary[dir];
        for (let i = 0; n && i < _COMPACT_FOLD_RADIUS; i++, n = n[dir]) {
          const kind = _compactRowIsFoldable(n);
          if (!kind) break;
          if (kind === 'boundary') boundary = n;
          eaten.push(n);
        }
      }
      summary.dataset.compactFolded = '1';
      const pre = boundary ? Number(boundary.dataset.compactPre || 0) : 0;
      const post = boundary ? Number(boundary.dataset.compactPost || 0) : 0;
      const ms = boundary ? Number(boundary.dataset.compactDuration || 0) : 0;
      const trigger = (boundary && boundary.dataset.compactTrigger) || 'manual';
      const card = document.createElement('div');
      card.className = 'compact-run-card is-record';
      card.dataset.stage = 'done';
      const bits = [];
      if (pre && post) {
        const freed = Math.max(0, pre - post);
        const pct = pre ? Math.round((freed / pre) * 100) : 0;
        bits.push('<span class="compact-run-delta"><b>' + escapeHtml(_compactTokenLabel(pre)) + '</b>'
          + ' <span class="compact-run-arrow">→</span> <b>' + escapeHtml(_compactTokenLabel(post))
          + '</b> tokens</span>');
        if (freed) {
          bits.push('<span class="compact-run-freed">' + escapeHtml(_compactTokenLabel(freed))
            + ' freed <span class="compact-run-pct">(−' + pct + '%)</span></span>');
        }
      }
      if (ms > 0) bits.push('<span class="compact-run-took">took ' + escapeHtml(_compactElapsedLabel(ms)) + '</span>');
      const summaryInner = summary.querySelector('.compact-resume-card');
      card.innerHTML =
        '<div class="compact-run-head">'
        +   '<span class="compact-run-glyph">✓</span>'
        +   '<span class="compact-run-title">Context compacted'
        +     (trigger === 'auto' ? ' automatically' : '') + '</span>'
        + '</div>'
        + (bits.length ? '<div class="compact-run-result">' + bits.join('<span class="compact-run-sep">·</span>') + '</div>' : '')
        + '<div class="compact-run-note">'
        +   (trigger === 'auto'
              ? 'The conversation neared its context limit, so the earlier turns were replaced with a summary.'
              : 'The earlier turns were replaced with a summary.')
        +   ' Everything below this point is the conversation that was kept.'
        +   ' <span class="compact-run-quiet">The full transcript on disk is untouched.</span>'
        + '</div>'
        + '<div class="compact-run-summary"></div>';
      if (summaryInner) card.querySelector('.compact-run-summary').appendChild(summaryInner);
      // Anchor at the EARLIEST folded row so the card sits where the
      // compaction actually happened, not wherever the summary landed.
      let anchor = summary;
      for (const el of eaten) {
        if (el.compareDocumentPosition(anchor) & Node.DOCUMENT_POSITION_FOLLOWING) anchor = el;
      }
      anchor.parentNode.insertBefore(card, anchor);
      summary.classList.add('compact-record-hidden');
      eaten.forEach((el) => el.classList.add('compact-record-hidden'));
    });
  }

  function renderCompactResumeCard(text) {
    const parsed = parseCompactResumeBlock(text);
    if (!parsed) return null;
    const introHtml = escapeHtml(parsed.intro);
    const bodyHtml = parsed.body
      ? renderMarkdown(parsed.body)
      : '<em class="compact-resume-empty">(no detail captured)</em>';
    return '<div class="compact-resume-card" data-collapsed="1">'
      + '<button type="button" class="compact-resume-toggle" aria-expanded="false">'
      +   '<span class="compact-resume-icon">▸</span>'
      +   '<span class="compact-resume-label">Resumed from /compact summary</span>'
      +   '<span class="compact-resume-hint">click to expand</span>'
      + '</button>'
      + '<div class="compact-resume-intro">' + introHtml + '</div>'
      + '<div class="compact-resume-body">' + bodyHtml + '</div>'
      + '</div>';
  }

  function renderDevinHandoffCard(ev) {
    const title = String((ev && ev.handoff_title) || '').trim();
    return '<details class="devin-handoff-card">'
      + '<summary>'
      +   '<span class="devin-handoff-label">➤ Handoff to sidekick</span>'
      +   (title ? '<span class="devin-handoff-title">' + escapeHtml(title) + '</span>' : '')
      + '</summary>'
      + '<div class="devin-handoff-body">' + renderMarkdown(String(ev.text || '')) + '</div>'
      + '</details>';
  }

  function renderSkillLoadedCard(ev) {
    const name = String((ev && ev.skill) || 'skill').trim();
    return '<details class="skill-loaded-card">'
      + '<summary>'
      +   '<span class="skill-loaded-label">\u25b8 Skill loaded</span>'
      +   '<span class="skill-loaded-title">' + escapeHtml(name) + '</span>'
      + '</summary>'
      + '<div class="skill-loaded-body">' + renderMarkdown(String(ev.text || '')) + '</div>'
      + '</details>';
  }

  function renderCodexSkillLoadedCard(b) {
    const name = String((b && b.name) || 'skill').trim();
    const path = String((b && b.path) || '').trim();
    return '<details class="skill-loaded-card codex-skill-loaded-card">'
      + '<summary>'
      +   '<span class="skill-loaded-label">\u25b8 Skill loaded</span>'
      +   '<span class="skill-loaded-title">' + escapeHtml(name) + '</span>'
      + '</summary>'
      + (path ? '<div class="skill-loaded-body">Read ' + linkifyPath(path) + '</div>' : '')
      + '</details>';
  }

  function _convPaneHost() {
    return document.getElementById('convSplit') || document;
  }

  function convPaneElById(pid) {
    return _convPaneHost().querySelector(`.conv-pane[data-pane-id="${pid}"]`);
  }

  function getConvViewForPane(pid) {
    const pane = convPaneElById(pid);
    return pane ? pane.querySelector('.conversations-view') : null;
  }

  const CONV_BOTTOM_TOLERANCE = 80;

  function conversationDistanceFromBottom(view) {
    if (!view) return 0;
    return Math.max(0, view.scrollHeight - view.scrollTop - view.clientHeight);
  }

  function isConversationAtBottom(view) {
    // An upward gesture opts out of following even inside the tolerance.
    // A cleared/short view still counts as bottom on a fresh open.
    if (view && view._pinnedToBottom === false
        && view.scrollHeight > view.clientHeight) return false;
    return conversationDistanceFromBottom(view) <= CONV_BOTTOM_TOLERANCE;
  }

  function nowStamp() {
    const d = new Date();
    const p2 = (n) => (n < 10 ? '0' + n : '' + n);
    const p3 = (n) => (n < 10 ? '00' + n : n < 100 ? '0' + n : '' + n);
    return p2(d.getHours()) + ':' + p2(d.getMinutes()) + ':' + p2(d.getSeconds()) + '.' + p3(d.getMilliseconds());
  }

  function eventStamp(isoTs) {
    if (!isoTs) return null;
    const d = new Date(isoTs);
    if (isNaN(d.getTime())) return null;
    const p2 = (n) => (n < 10 ? '0' + n : '' + n);
    const p3 = (n) => (n < 10 ? '00' + n : n < 100 ? '0' + n : '' + n);
    return p2(d.getHours()) + ':' + p2(d.getMinutes()) + ':' + p2(d.getSeconds()) + '.' + p3(d.getMilliseconds());
  }

  function _freeServedText(info) {
    if (!info || !info.provider) return '';
    const bits = ['Used free (\u00240)', info.provider, info.model];
    if (info.latency_ms) bits.push((info.latency_ms / 1000).toFixed(1) + 's');
    if (info.failed) bits.push(info.failed + ' failed attempt' + (info.failed > 1 ? 's' : '') + ' before');
    return bits.join(' \u00b7 ');
  }

  function _formatTokens(n) {
    if (!n) return '0';
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
    if (n >= 1_000) return Math.round(n / 1_000) + 'k';
    return String(n);
  }

  function _formatCompactDuration(ms) {
    ms = Number(ms) || 0;
    if (!ms) return '';
    const seconds = Math.round(ms / 1000);
    if (seconds < 60) return seconds + 's';
    const minutes = Math.floor(seconds / 60);
    const rem = seconds % 60;
    return minutes + 'm' + (rem ? ' ' + rem + 's' : '');
  }

  function _formatTokensAntigravity(n) {
    n = Number(n) || 0;
    if (n < 1000) return String(n);
    // Truncate to one decimal (e.g. 1199 -> 1.1k, not 1.2k).
    return (Math.floor(n / 100) / 10).toFixed(1) + 'k';
  }

  function _formatAntigravityTokenChips(tIn, tOut, tThinking, tCached) {
    tIn = Number(tIn) || 0;
    tOut = Number(tOut) || 0;
    tThinking = Number(tThinking) || 0;
    tCached = Number(tCached) || 0;
    const parts = [];
    if (tIn) parts.push(_formatTokensAntigravity(tIn) + ' in');
    if (tCached) parts.push(_formatTokensAntigravity(tCached) + ' cached');
    if (tOut) parts.push(_formatTokensAntigravity(tOut) + ' out');
    if (tThinking >= 100) parts.push(_formatTokensAntigravity(tThinking) + ' thinking');
    return parts.join(' | ');
  }

  const CACHE_MISS_CALLOUT_MIN_SHARE = 0.70;

  function _cacheMissCallout(tIn, tCached) {
    const total = Number(tIn) || 0;
    const missed = Math.max(0, total - (Number(tCached) || 0));
    if (!missed || !total) return '';
    if (missed / total <= CACHE_MISS_CALLOUT_MIN_SHARE) return '';
    return 'CACHE MISS: ' + _formatTokensAntigravity(missed) + ' input tokens uncached';
  }

  const CACHE_READ_TOKEN_WEIGHT = 0.10;

  function _cacheReadTokenWeight(model) {
    return /^(?:claude-)?fable-5[-.][12](?:$|\[|-[0-9]{8}$)/i.test(String(model || ''))
      ? 0.025 : CACHE_READ_TOKEN_WEIGHT;
  }

  function _cacheAdjustedTurnTokens(tIn, tOut, tCached, model) {
    const weight = _cacheReadTokenWeight(model);
    const total = Number(tIn) || 0;
    const cached = Math.min(Number(tCached) || 0, total);
    const fresh = total - cached;
    return Math.round(fresh + cached * weight + (Number(tOut) || 0));
  }

  function _cacheAdjustedTurnTitle(tIn, tOut, tCached, model) {
    const weight = _cacheReadTokenWeight(model);
    const total = Number(tIn) || 0;
    const cached = Math.min(Number(tCached) || 0, total);
    const fresh = total - cached;
    const out = Number(tOut) || 0;
    const cachedWeighted = Math.round(cached * weight);
    const grand = fresh + cachedWeighted + out;
    return 'Cache-read input tokens are discounted to ' + (weight * 100) + '% of a fresh input token before summing with output.\n'
      + fresh.toLocaleString() + ' fresh in + ' + cachedWeighted.toLocaleString() + ' (' + cached.toLocaleString() + ' cached × ' + (weight * 100) + '%) + ' + out.toLocaleString() + ' out = ' + grand.toLocaleString();
  }

  function _cacheAdjustedInlineHtml(tokensIn, tokensOut, cached, model) {
    const n = _cacheAdjustedTurnTokens(tokensIn, tokensOut, cached, model);
    return '<span class="cache-adjusted-num" title="'
      + escapeAttr('Cached-adjusted tokens this turn\n' + _cacheAdjustedTurnTitle(tokensIn, tokensOut, cached, model))
      + '"> | ' + n.toLocaleString() + '</span>';
  }

  function toolCallName(toolCall) {
    const nameEl = toolCall ? toolCall.querySelector('.tool-name') : null;
    return (nameEl?.dataset?.toolName || nameEl?.textContent || '').trim();
  }

  const KNOWN_TOOL_SOURCE_BY_NAME = {
    click: 'Computer Use',
    click_at: 'Computer Use',
    double_click: 'Computer Use',
    drag: 'Computer Use',
    get_app_state: 'Computer Use',
    hotkey: 'Computer Use',
    list_apps: 'Computer Use',
    move_mouse: 'Computer Use',
    press_key: 'Computer Use',
    screenshot: 'Computer Use',
    scroll: 'Computer Use',
    type_text: 'Computer Use',
  };

  function splitMcpToolName(rawName) {
    const value = String(rawName || '').trim();
    if (!value.startsWith('mcp__')) return null;
    const rest = value.slice(5);
    const idx = rest.indexOf('__');
    if (idx <= 0) return null;
    return { namespace: rest.slice(0, idx), name: rest.slice(idx + 2) };
  }

  function toolDisplayName(rawName) {
    let value = String(rawName || '').trim();
    const mcp = splitMcpToolName(value);
    if (mcp) value = mcp.name;
    const dot = value.lastIndexOf('.');
    if (dot >= 0) value = value.slice(dot + 1);
    return value || 'tool';
  }

  function toolNamespaceLabel(rawNamespace) {
    const key = String(rawNamespace || '').trim().toLowerCase().replace(/[-\s]+/g, '_');
    return ({
      browser: 'Browser',
      chrome: 'Chrome',
      computer_use: 'Computer Use',
      github: 'GitHub',
      gmail: 'Gmail',
      google_calendar: 'Google Calendar',
      google_drive: 'Google Drive',
      image_gen: 'Image Generation',
      outlook_calendar: 'Outlook Calendar',
      outlook_email: 'Outlook Email',
      slack: 'Slack',
      teams: 'Teams',
      tool_search: 'Tool Search',
      zoom: 'Zoom',
    })[key] || '';
  }

  function inferredToolSource(rawName) {
    const value = String(rawName || '').trim();
    const mcp = splitMcpToolName(value);
    if (mcp) return toolNamespaceLabel(mcp.namespace);
    const dot = value.lastIndexOf('.');
    if (dot > 0) {
      const source = toolNamespaceLabel(value.slice(0, dot));
      if (source) return source;
    }
    return KNOWN_TOOL_SOURCE_BY_NAME[toolDisplayName(value)] || '';
  }

  function toolBlockSource(block) {
    if (!block || typeof block !== 'object') return '';
    const explicit = block.tool_source || block.toolSource || block.provider || block.namespace || '';
    if (explicit) return String(toolNamespaceLabel(explicit) || explicit).trim();
    return inferredToolSource(block.name).trim();
  }

  function toolCallSource(toolCall) {
    if (!toolCall) return '';
    return (toolCall.dataset.toolSource || inferredToolSource(toolCallName(toolCall))).trim();
  }

  function toolCallDetailText(toolCall) {
    if (!toolCall) return '';
    return (toolCall.dataset.toolDetail || toolCall.querySelector('.tool-detail')?.textContent || '').trim();
  }

  function toolCallForResult(group, toolUseId) {
    if (!group) return null;
    const calls = Array.from(group.querySelectorAll('.tool-call'));
    if (!calls.length) return null;
    if (toolUseId) {
      const match = calls.find(tc => (tc.dataset.toolUseId || '') === toolUseId);
      if (match) return match;
    }
    return calls[calls.length - 1];
  }

  function toolResultOutputLabel(toolCall, isError) {
    if (isCommandActivityTool(toolCallName(toolCall))) {
      return isError ? '⚠ Command failed' : 'Command result';
    }
    return isError ? '⚠ Tool failed' : 'Tool result';
  }

  function isEditToolName(name) {
    const base = toolDisplayName(name);
    // Lowercase entries are devin-cli tool names.
    return base === 'Edit' || base === 'MultiEdit' || base === 'Write' || base === 'NotebookEdit'
      || base === 'edit' || base === 'write' || base === 'notebook_edit';
  }

  function isFileToolName(name) {
    const base = toolDisplayName(name);
    return isEditToolName(base) || base === 'Read' || base === 'read';
  }

  function compactPathDetail(detail) {
    const value = String(detail || '').trim();
    if (!value || !value.includes('/')) return value;
    const parts = value.split('/').filter(Boolean);
    if (parts.length <= 2) return value;
    return parts.slice(-2).join('/');
  }

  function _isImagePathDetail(p) {
    return /\.(?:png|jpe?g|gif|webp|bmp|svg)$/i.test(String(p || '').trim());
  }

  function inlineToolImageHtml(detail) {
    const full = String((detail && detail.full) || '').trim();
    if (!full || !_isImagePathDetail(full)) return '';
    const src = '/api/local-image?path=' + encodeURIComponent(full);
    return '<div class="tool-inline-image">'
      + '<img class="msg-image" src="' + escapeAttr(src) + '"'
      + ' alt="' + escapeAttr(compactPathDetail(full)) + '" loading="lazy"'
      + ' onerror="var p=this.closest(&quot;.tool-inline-image&quot;); if(p) p.remove();"></div>';
  }

  function formatToolCallDetail(name, detail) {
    const baseName = toolDisplayName(name);
    const full = String(detail || '').trim();
    if (!full) return { display: '', full: '', className: '' };
    if (isCommandActivityTool(baseName)) {
      return { display: full, full, className: ' tool-command' };
    }
    if (isFileToolName(baseName)) {
      return { display: compactPathDetail(full), full, className: ' tool-file' };
    }
    return { display: full, full, className: '' };
  }

  const CONV_VIEW_MODES = ['compact', 'normal', 'verbose'];

  function convViewMode() {
    try {
      const m = localStorage.getItem('ccc-conv-view');
      if (CONV_VIEW_MODES.includes(m)) return m;
      return localStorage.getItem('ccc-conv-verbose') === '1' ? 'verbose' : 'normal';
    } catch (_) { return 'normal'; }
  }

  function convVerboseOn() { return convViewMode() === 'verbose'; }

  function renderToolCommandDisclosure(block, detail) {
    const command = String((block && block.command) || '').trim();
    if (!command) return '';
    const visible = String((detail && detail.full) || '').trim();
    const commandFlat = command.replace(/\s+/g, ' ').trim();
    const visibleFlat = visible.replace(/\s+/g, ' ').trim();
    if (commandFlat === visibleFlat && command.length <= 160 && command.indexOf('\n') === -1) {
      return '';
    }
    const kind = String((block && block.command_kind) || '').trim();
    const label = /argument/i.test(kind) ? 'View arguments' : (/script/i.test(kind) ? 'View script' : 'View command');
    const kindHtml = kind
      ? '<span class="tool-command-kind">' + escapeHtml(kind) + '</span>'
      : '';
    return '<details class="tool-command-disclosure"' + (convVerboseOn() ? ' open' : '') + '>'
      + '<summary><span>' + escapeHtml(label) + '</span>' + kindHtml + '</summary>'
      + '<pre>' + escapeHtml(command) + '</pre>'
      + '</details>';
  }

  function renderToolInputDisclosure(block, conversationId, line) {
    if (!block || !block.has_input || !conversationId || !line) return '';
    const toolUseId = String(block.id || block.tool_use_id || '').trim();
    const structured = toolDisplayName(block.name) === 'StructuredOutput';
    return '<details class="tool-command-disclosure tool-input-disclosure'
      + (structured ? ' tool-structured-output' : '') + '"'
      + ' data-conversation-id="' + escapeAttr(conversationId) + '"'
      + ' data-jsonl-line="' + escapeAttr(line) + '"'
      + ' data-tool-use-id="' + escapeAttr(toolUseId) + '"'
      + (convVerboseOn() ? ' open' : '') + '>'
      + '<summary><span>' + (structured ? 'View structured output' : 'Input') + '</span></summary>'
      + '<pre class="tool-input-payload">Loading…</pre>'
      + '</details>';
  }

  async function loadToolInputDisclosure(details) {
    if (!details || details.dataset.loaded === '1' || details.dataset.loading === '1') return;
    const payload = details.querySelector('.tool-input-payload');
    if (!payload) return;
    details.dataset.loading = '1';
    payload.textContent = 'Loading…';
    try {
      const convId = details.dataset.conversationId || '';
      const line = details.dataset.jsonlLine || '';
      const toolUseId = details.dataset.toolUseId || '';
      const url = '/api/conversations/' + encodeURIComponent(convId)
        + '/tool-input?line=' + encodeURIComponent(line)
        + '&tool_use_id=' + encodeURIComponent(toolUseId);
      const res = await fetch(url, { cache: 'no-store', cccPriority: 'user' });
      const data = await res.json();
      if (!res.ok || !data.ok || typeof data.input !== 'string') {
        throw new Error(data.error || 'Tool input unavailable');
      }
      payload.textContent = data.input;
      details.dataset.loaded = '1';
    } catch (err) {
      payload.textContent = 'Input unavailable.';
    } finally {
      delete details.dataset.loading;
    }
  }

  function renderEditDisclosure(b) {
    const editInput = b && b.edit_input;
    if (!editInput) return '';
    const name = toolDisplayName(b.name || '');
    let body = '';
    if ((name === 'Edit' || name === 'edit') && (editInput.old_string != null || editInput.new_string != null)) {
      body = '<div class="tool-edit-section tool-edit-old"><span class="tool-edit-label">Before</span><pre>' + escapeHtml(String(editInput.old_string || '')) + '</pre></div>'
           + '<div class="tool-edit-section tool-edit-new"><span class="tool-edit-label">After</span><pre>' + escapeHtml(String(editInput.new_string || '')) + '</pre></div>';
    } else if ((name === 'Write' || name === 'write') && editInput.content != null) {
      body = '<div class="tool-edit-section tool-edit-new"><span class="tool-edit-label">Content</span><pre>' + escapeHtml(String(editInput.content || '')) + '</pre></div>';
    } else if (name === 'MultiEdit' && Array.isArray(editInput.edits)) {
      body = editInput.edits.map(function (e, i) {
        return '<div class="tool-edit-multi-item">'
          + '<div class="tool-edit-section tool-edit-old"><span class="tool-edit-label">Before ' + (editInput.edits.length > 1 ? (i + 1) : '') + '</span><pre>' + escapeHtml(String(e.old_string || '')) + '</pre></div>'
          + '<div class="tool-edit-section tool-edit-new"><span class="tool-edit-label">After ' + (editInput.edits.length > 1 ? (i + 1) : '') + '</span><pre>' + escapeHtml(String(e.new_string || '')) + '</pre></div>'
          + '</div>';
      }).join('');
    }
    if (!body) return '';
    const truncNote = editInput.truncated
      ? '<div class="tool-edit-truncated">… content truncated</div>'
      : '';
    return '<details class="tool-command-disclosure tool-edit-disclosure"' + (convVerboseOn() ? ' open' : '') + '>'
      + '<summary><span>View edit</span></summary>'
      + '<div class="tool-edit-body">' + body + truncNote + '</div>'
      + '</details>';
  }

  function summarizeToolCall(div) {
    const tc = div.querySelector('.tool-call');
    if (!tc) return 'Ran 1 command';
    const name = toolCallName(tc);
    const displayName = toolDisplayName(name);
    const source = toolCallSource(tc);
    const detail = toolCallDetailText(tc);
    const basename = (s) => {
      if (!s) return '';
      const cleaned = s.replace(/^[\"\'\s]+|[\"\'\s]+$/g, '');
      const parts = cleaned.split('/');
      return parts[parts.length - 1] || cleaned;
    };
    const trunc = (s, n) => s.length > n ? s.slice(0, n - 1) + '…' : s;
    const codeRead = (displayName === 'Bash' || displayName === 'exec_command') ? parseCodeReadCommand(detail) : null;
    if (codeRead) {
      const loc = codeRead.start ? ':' + codeRead.start + (codeRead.end && codeRead.end !== codeRead.start ? '-' + codeRead.end : '') : '';
      return 'Viewed ' + _pathBase(codeRead.path) + loc;
    }
    if (isCommandActivityTool(displayName)) {
      const full = fullCommandSummaryLabel(detail);
      const verb = tc.classList.contains('tool-call-ok') ? '' : 'Ran ';
      return full ? verb + full : (verb || 'Ran ') + 'shell command';
    }
    if (source) {
      return detail
        ? 'Used ' + source + ': ' + displayName + ' ' + trunc(detail, 40)
        : 'Used ' + source + ': ' + displayName;
    }
    switch (displayName) {
      case 'Read':         return detail ? 'Read ' + basename(detail) : 'Ran Read';
      case 'Edit':         return detail ? 'Edited ' + basename(detail) : 'Ran Edit';
      case 'Write':        return detail ? 'Wrote ' + basename(detail) : 'Ran Write';
      case 'NotebookEdit': return detail ? 'Edited ' + basename(detail) : 'Ran NotebookEdit';
      case 'Bash':         return detail ? 'Ran ' + trunc(detail, 60) : 'Ran Bash';
      case 'Grep':         return detail ? 'Searched ' + trunc(detail, 50) : 'Ran Grep';
      case 'Glob':         return detail ? 'Globbed ' + trunc(detail, 50) : 'Ran Glob';
      case 'WebFetch':     return detail ? 'Fetched ' + trunc(detail, 60) : 'Ran WebFetch';
      case 'WebSearch':    return detail ? 'Searched the web for ' + trunc(detail, 50) : 'Ran WebSearch';
      case 'TodoWrite':    return 'Updated todos';
      case 'Task':         return detail ? 'Spawned subagent: ' + trunc(detail, 50) : 'Spawned subagent';
      case 'sidekick':     return detail ? 'Delegated to sidekick: ' + trunc(detail, 50) : 'Delegated to sidekick';
      case 'run_subagent': return detail ? 'Spawned subagent: ' + trunc(detail, 50) : 'Spawned subagent';
      case 'TaskCreate':   return 'Created task';
      case 'TaskUpdate':   return 'Updated task';
      case 'AskUserQuestion': return detail ? 'Question: ' + trunc(detail, 70) : 'Asked a question';
      case 'ExitPlanMode': return 'Exited plan mode';
      default:             return detail ? 'Ran ' + displayName + ': ' + trunc(detail, 40) : 'Ran ' + (displayName || 'tool');
    }
  }

  function toolCallCarriesConversationContext(toolCall) {
    const key = normalizedToolKey(toolCallName(toolCall));
    // sidekick/run_subagent handoffs are conversation context — the row is
    // where the lead's work visibly leaves for the worker lane.
    return key === 'ask_user_question' || key === 'askuserquestion' || key === 'sidekick'
      || key === 'run_subagent' || key.startsWith('kanban_');
  }

  function toolGroupCarriesConversationContext(group) {
    if (!group) return false;
    return Array.from(group.querySelectorAll('.tool-call')).some(toolCallCarriesConversationContext);
  }

  function splitShellWords(cmd) {
    const words = [];
    let cur = '';
    let quote = '';
    let esc = false;
    for (const ch of String(cmd || '')) {
      if (esc) {
        cur += ch;
        esc = false;
        continue;
      }
      if (ch === '\\' && quote !== "'") {
        esc = true;
        continue;
      }
      if (quote) {
        if (ch === quote) quote = '';
        else cur += ch;
        continue;
      }
      if (ch === '"' || ch === "'") {
        quote = ch;
        continue;
      }
      if (/\s/.test(ch)) {
        if (cur) {
          words.push(cur);
          cur = '';
        }
        continue;
      }
      cur += ch;
    }
    if (cur) words.push(cur);
    return words;
  }

  function shellSearchTerm(words) {
    const valueFlags = new Set([
      '-e', '-A', '-B', '-C', '-m', '-d', '-f', '-g',
      '--include', '--exclude', '--include-dir', '--exclude-dir',
      '--glob', '--type', '-t',
    ]);
    for (let k = 1; k < words.length; k++) {
      const w = words[k];
      if (w.startsWith('-')) {
        if (valueFlags.has(w)) k++;   // skip the flag's value too
        continue;
      }
      return w;                       // first bare arg = the search term
    }
    return '';
  }

  function _stripCdWrapperPrefix(raw) {
    let out = raw;
    for (let guard = 0; guard < 6; guard++) {
      const m = out.match(/^(?:cd|pushd|export|source|set|unset)\b[^&;|]*(?:&&|;)\s*/);
      if (!m) break;
      const rest = out.slice(m[0].length).trim();
      if (!rest) break;
      out = rest;
    }
    return out;
  }

  function fullCommandSummaryLabel(command) {
    let raw = String(command || '').replace(/\s+/g, ' ').trim();
    if (!raw) return '';
    raw = raw.replace(/^Shell command:\s*/i, '').trim();
    raw = _stripCdWrapperPrefix(raw);
    return raw;
  }

  function compactShellCommandLabel(command) {
    let raw = String(command || '').replace(/\s+/g, ' ').trim();
    if (!raw) return '';
    raw = raw.replace(/^Shell command:\s*/i, '').trim();
    raw = _stripCdWrapperPrefix(raw);
    const words = splitShellWords(raw);
    if (!words.length) return raw.length > 40 ? raw.slice(0, 39) + '…' : raw;
    const cmd = _pathBase(words[0]);
    const sub = words[1] && !words[1].startsWith('-') ? words[1] : '';
    const _short = (s) => s.length > 28 ? s.slice(0, 27) + '…' : s;
    // Search tools: the pattern is the meaningful part, not the bare verb.
    if (['grep', 'egrep', 'fgrep', 'rg', 'ripgrep', 'ag', 'ack'].includes(cmd)) {
      const term = shellSearchTerm(words);
      return term ? cmd + ' ' + _short(term) : cmd;
    }
    if (cmd === 'find') {
      const ni = words.indexOf('-name');
      const pat = (ni >= 0 && words[ni + 1]) ? words[ni + 1] : sub;
      return pat ? 'find ' + _short(pat) : cmd;
    }
    if (cmd === 'git' && sub) return 'git ' + sub;
    if (cmd === 'gh' && sub) return 'gh ' + sub;
    if (cmd === 'npx' && sub) {
      const third = words[2] && !words[2].startsWith('-') ? words[2] : '';
      return third ? 'npx ' + sub + ' ' + third : 'npx ' + sub;
    }
    if ((cmd === 'npm' || cmd === 'pnpm' || cmd === 'yarn') && sub) return cmd + ' ' + sub;
    if ((cmd === 'python' || cmd === 'python3') && sub) return sub === '-m' && words[2] ? cmd + ' -m ' + words[2] : cmd + ' ' + sub;
    if (cmd === 'ssh' || cmd === 'scp' || cmd === 'sftp') {
      // Find host: first non-flag, non-consumed-by-flag arg.
      // ssh flags that consume the next argument: -b -c -D -E -e -F -I -i -J -l -L -m -N -o -p -Q -R -S -w
      let host = '', remoteFirst = '';
      for (let _i = 1; _i < words.length; _i++) {
        const _w = words[_i];
        if (/^-[bcDEeFIiJlLmNopQRSw]$/.test(_w)) { _i++; continue; }
        if (_w.startsWith('-')) continue;
        if (!host) { host = _w; continue; }
        // next non-flag after host = remote command (already shell-split)
        remoteFirst = splitShellWords(_w)[0] || '';
        break;
      }
      if (host) {
        const suffix = remoteFirst ? ': ' + _short(remoteFirst) : '';
        return cmd + ' ' + _short(host) + suffix;
      }
    }
    if (cmd) return cmd;
    return raw.length > 40 ? raw.slice(0, 39) + '…' : raw;
  }

  function joinCompactList(items, maxItems) {
    const unique = Array.from(new Set((items || []).filter(Boolean)));
    const max = Math.max(1, Number(maxItems) || 3);
    const visible = unique.slice(0, max);
    const extra = unique.length - visible.length;
    if (!visible.length) return '';
    let text = '';
    if (visible.length === 1) text = visible[0];
    else if (visible.length === 2) text = visible[0] + ' and ' + visible[1];
    else text = visible.slice(0, -1).join(', ') + ', and ' + visible[visible.length - 1];
    return extra > 0 ? text + ' and ' + extra + ' more' : text;
  }

  function normalizedToolKey(name) {
    return toolDisplayName(name)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '');
  }

  function toolActionKind(name) {
    const key = normalizedToolKey(name);
    if ([
      'read', 'view_file', 'read_file', 'open_file',
    ].includes(key)) return 'file-read';
    if ([
      'grep', 'grep_search', 'search', 'search_files', 'search_file_content',
      'ripgrep', 'find_text',
    ].includes(key)) return 'search';
    if (['glob', 'glob_search', 'find_files', 'file_search'].includes(key)) return 'glob';
    if (['list_dir', 'list_directory', 'ls', 'directory_list'].includes(key)) return 'list-dir';
    if (['webfetch', 'web_fetch', 'fetch_url'].includes(key)) return 'web-fetch';
    if (['websearch', 'web_search', 'search_web'].includes(key)) return 'web-search';
    if (['todowrite', 'todo_write', 'update_todo', 'update_todos'].includes(key)) return 'todo';
    if (['askuserquestion', 'ask_user_question'].includes(key)) return 'question';
    if (['exitplanmode', 'exit_plan_mode'].includes(key)) return 'plan';
    return '';
  }

  function uniqueToolDetails(calls) {
    return Array.from(new Set((calls || []).map(tc => toolCallDetailText(tc)).filter(Boolean)));
  }

  function detailLooksLikePath(detail) {
    return /[\/\\]/.test(String(detail || ''));
  }

  function detailLabel(detail) {
    const value = String(detail || '').trim();
    if (!value) return '';
    return detailLooksLikePath(value) ? _pathBase(value) : value;
  }

  function summarizeToolActionCalls(kind, calls) {
    const count = calls.length;
    const details = uniqueToolDetails(calls);
    const namedDetails = details.map(detailLabel).filter(Boolean);
    const namedList = joinCompactList(namedDetails, 2);
    if (kind === 'file-read') {
      if (details.length === 1) return 'viewed ' + detailLabel(details[0]) + (count > 1 ? ' ' + count + ' times' : '');
      if (details.length > 1) return 'viewed ' + details.length + ' files';
      return count === 1 ? 'viewed a file' : 'viewed ' + count + ' files';
    }
    if (kind === 'search') {
      if (details.length === 1) return 'searched ' + detailLabel(details[0]) + (count > 1 ? ' ' + count + ' times' : '');
      return count === 1 ? 'searched code' : 'searched code ' + count + ' times';
    }
    if (kind === 'glob') {
      return count === 1 ? 'matched files' : 'matched files ' + count + ' times';
    }
    if (kind === 'list-dir') {
      if (namedList) return 'listed ' + namedList;
      return count === 1 ? 'listed a folder' : 'listed ' + count + ' folders';
    }
    if (kind === 'web-fetch') {
      return count === 1 ? 'fetched a URL' : 'fetched ' + count + ' URLs';
    }
    if (kind === 'web-search') {
      return count === 1 ? 'searched the web' : 'searched the web ' + count + ' times';
    }
    if (kind === 'todo') return 'updated todos';
    if (kind === 'question') return count === 1 ? 'asked a question' : 'asked ' + count + ' questions';
    if (kind === 'plan') return 'updated plan state';
    return '';
  }

  function readableToolName(name) {
    return toolDisplayName(name).replace(/_/g, ' ');
  }

  function summarizeNamedToolCalls(calls) {
    const counts = {};
    for (const tc of calls || []) {
      const name = readableToolName(toolCallName(tc));
      if (!name) continue;
      counts[name] = (counts[name] || 0) + 1;
    }
    const labels = Object.keys(counts).sort().map(name => {
      const n = counts[name];
      return n > 1 ? String(n) + ' ' + name + ' calls' : name;
    });
    return labels.length ? 'used ' + joinCompactList(labels, 3) : '';
  }

  function _codeLangForPath(path) {
    const clean = String(path || '').split(/[?#]/)[0].toLowerCase();
    const m = /\.([a-z0-9]+)$/.exec(clean);
    if (!m) return '';
    return ({
      js: 'ts', jsx: 'tsx', ts: 'ts', tsx: 'tsx',
      py: 'py',
      sh: 'bash', bash: 'bash', zsh: 'bash',
      json: 'json',
      css: 'css',
      html: 'html', htm: 'html',
      md: 'markdown', mdx: 'markdown',
      yaml: 'yaml', yml: 'yaml',
    })[m[1]] || m[1];
  }

  function _pathBase(path) {
    const s = String(path || '');
    const parts = s.split('/');
    return parts[parts.length - 1] || s;
  }

  function parseCodeReadCommand(command) {
    const raw = String(command || '').trim();
    if (!raw) return null;
    if (/[|;&<>`]/.test(raw)) return null;
    const words = splitShellWords(raw);
    if (!words.length) return null;
    const cmd = _pathBase(words[0]);
    const pathLooksUseful = (p) => !!String(p || '').match(/\.(?:md|mdx|txt|py|js|jsx|ts|tsx|json|ya?ml|css|html?|sh|bash|zsh|toml|ini|cfg|conf|sql|prisma|go|rs|java|kt|swift|c|cc|cpp|h|hpp|rb|php)$/i);
    const cleanPath = (p) => String(p || '').replace(/^--$/, '');

    if (cmd === 'sed') {
      let expr = '';
      let path = '';
      for (let i = 1; i < words.length; i++) {
        const w = words[i];
        if (w === '-n' || w === '-E' || w === '-r') continue;
        if ((w === '-e' || w === '-f') && i + 1 < words.length) {
          if (w === '-e' && !expr) expr = words[++i];
          else i += 1;
          continue;
        }
        if (!expr) {
          expr = w;
          continue;
        }
        if (w === '--') continue;
        path = cleanPath(w);
        break;
      }
      const range = /^(\d+)(?:,(\d+)|,\+(\d+))?p$/.exec(expr || '');
      if (range && path) {
        const start = Number(range[1]);
        const end = range[2] ? Number(range[2]) : (range[3] ? start + Number(range[3]) : start);
        return { kind: 'excerpt', path, start, end, lang: _codeLangForPath(path) };
      }
    }

    if (cmd === 'cat') {
      const files = words.slice(1).filter(w => w !== '--' && !/^-/.test(w));
      if (files.length === 1 && pathLooksUseful(files[0])) {
        return { kind: 'file', path: files[0], start: null, end: null, lang: _codeLangForPath(files[0]) };
      }
    }

    if (cmd === 'nl') {
      const files = words.slice(1).filter(w => w !== '--' && !/^-/.test(w) && !/^\d+$/.test(w));
      const path = files[files.length - 1] || '';
      if (pathLooksUseful(path)) {
        return { kind: 'file', path, start: null, end: null, lang: _codeLangForPath(path) };
      }
    }

    return null;
  }

  function toolCallCommandInfo(toolCall) {
    if (!toolCall) return null;
    const name = toolDisplayName(toolCallName(toolCall));
    const detail = toolCallDetailText(toolCall);
    if (name !== 'exec_command' && name !== 'Bash') return null;
    return parseCodeReadCommand(detail);
  }

  function toolCallLooksLikeCodeRead(div) {
    return !!toolCallCommandInfo(div && div.querySelector ? div.querySelector('.tool-call') : null);
  }

  function toolCallResultSnippet(tc) {
    if (!tc) return '';
    const out = tc.querySelector('.tool-result-output');
    if (out) {
      const raw = out.textContent.replace(/^\[J[^\]]*\]\s{0,2}/, '');
      const line = raw.split('\n').map(l => l.trim()).find(Boolean);
      if (!line) return 'no output';
      return line;
    }
    if (tc.querySelector('.tool-result-code-preview')) return '';
    return '';
  }

  function updateToolGroupLabel(group) {
    if (!group) return;
    const label = group.querySelector('.tcg-label');
    if (!label) return;
    const count = Number(group.dataset.toolCount || 0);
    const codeReads = Number(group.dataset.codeReadCount || 0);
    const calls = Array.from(group.querySelectorAll('.tool-call'));
    const resultEl = group.querySelector('.tcg-result');
    // Native Claude groups need the same command chips as merged tool
    // groups, including a lone group with no preceding narration.
    const header = label.parentElement;
    const orch = _ccOrchLabels([{ el: group }]);
    const chipLabels = orch.slice(0, 4).concat(orch.length > 4 ? ['+' + (orch.length - 4)] : []);
    let chips = header.querySelector('.tcg-orch');
    if (chipLabels.length) {
      if (!chips) {
        chips = document.createElement('span');
        chips.className = 'tcg-orch kimi-tg-orch';
        label.before(chips);
      }
      if (chips.dataset.labels !== chipLabels.join('|')) {
        chips.replaceChildren(...chipLabels.map(text => {
          const chip = document.createElement('span');
          chip.className = 'kimi-tg-orch-chip';
          chip.dataset.ccOrch = text;
          return chip;
        }));
        chips.dataset.labels = chipLabels.join('|');
      }
    } else if (chips) chips.remove();
    // Kimi-web parity (KIMI-FIXES-16): webui panes get the ToolGroup.vue
    // header shape — "N tool calls · <aggregate status>" — instead of the
    // command-summary sentence. Aggregate from per-row terminal classes.
    if (count >= 1 && group.closest('.conv-pane.is-webui-session')) {
      let state = 'done';
      if (calls.some(tc => tc.classList.contains('tool-call-fail'))) state = 'error';
      else if (calls.some(tc => !tc.classList.contains('tool-call-ok'))) state = 'running';
      label.textContent = count + ' tool call' + (count === 1 ? '' : 's') + ' · ' + state;
      label.title = '';
      group.dataset.kimiGroupState = state;
      return;
    }
    if (count === 1) {
      const only = group.querySelector('.tool-call-group-body > .event.tool-only');
      label.textContent = only ? summarizeToolCall(only) : 'Ran 1 command';
      const onlyCall = only ? only.querySelector('.tool-call') : null;
      label.title = onlyCall ? toolCallDetailText(onlyCall) : '';
      if (resultEl) resultEl.textContent = onlyCall ? toolCallResultSnippet(onlyCall) : '';
    } else if (count > 1 && codeReads === count) {
      label.title = '';
      if (resultEl) resultEl.textContent = '';
      label.textContent = 'Viewed ' + count + ' file excerpts';
    } else if (calls.length) {
      const edits = calls.filter(tc => isEditToolName(toolCallName(tc)));
      const commands = calls.filter(tc => isCommandActivityTool(toolCallName(tc)));
      const parts = [];
      if (edits.length) {
        const files = Array.from(new Set(edits.map(tc => toolCallDetailText(tc)).filter(Boolean)));
        if (files.length === 1) {
          parts.push('Edited ' + _pathBase(files[0]) + (edits.length > 1 ? ' ' + edits.length + ' times' : ''));
        } else if (files.length > 1) {
          parts.push('Edited ' + files.length + ' files');
        } else {
          parts.push('Edited ' + edits.length + ' time' + (edits.length === 1 ? '' : 's'));
        }
      }
      if (commands.length) {
        const commandList = joinCompactList(commands.map(tc => compactShellCommandLabel(toolCallDetailText(tc))), 3);
        if (commandList) {
          const allSucceeded = commands.every(tc => tc.classList.contains('tool-call-ok'));
          parts.push((allSucceeded ? 'completed ' : 'ran ') + commandList);
        } else {
          parts.push(commands.length === 1 ? 'ran shell command' : 'ran ' + commands.length + ' shell commands');
        }
      }
      const sourcedTools = calls.filter(tc =>
        !isEditToolName(toolCallName(tc))
        && !isCommandActivityTool(toolCallName(tc))
        && toolCallSource(tc)
      );
      const sourceCounts = {};
      for (const tc of sourcedTools) {
        const source = toolCallSource(tc);
        sourceCounts[source] = (sourceCounts[source] || 0) + 1;
      }
      for (const source of Object.keys(sourceCounts).sort()) {
        const n = sourceCounts[source];
        parts.push('used ' + n + ' ' + source + ' tool' + (n === 1 ? '' : 's'));
      }
      const categorizedTools = calls.filter(tc =>
        !isEditToolName(toolCallName(tc))
        && !isCommandActivityTool(toolCallName(tc))
        && !toolCallSource(tc)
      );
      const kindBuckets = {};
      const namedRemainder = [];
      for (const tc of categorizedTools) {
        const kind = toolActionKind(toolCallName(tc));
        if (kind) {
          if (!kindBuckets[kind]) kindBuckets[kind] = [];
          kindBuckets[kind].push(tc);
        } else {
          namedRemainder.push(tc);
        }
      }
      for (const kind of Object.keys(kindBuckets).sort()) {
        const summary = summarizeToolActionCalls(kind, kindBuckets[kind]);
        if (summary) parts.push(summary);
      }
      const namedSummary = summarizeNamedToolCalls(namedRemainder);
      if (namedSummary) parts.push(namedSummary);
      const text = parts.length ? parts.join('; ') : 'Ran ' + count + ' commands';
      label.textContent = text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
      label.title = '';
      if (resultEl) resultEl.textContent = '';
    } else {
      label.textContent = 'Ran ' + count + ' commands';
      label.title = '';
      if (resultEl) resultEl.textContent = '';
    }
  }

  function isRoutineSuccessfulToolResult(toolCall, text) {
    const name = toolCallName(toolCall);
    if (!isEditToolName(name)) return false;
    const t = String(text || '');
    return /has been (?:updated|created) successfully/i.test(t)
      || /file state is current/i.test(t)
      || /no need to Read it back/i.test(t);
  }

  function isSuccessfulCommandToolResult(toolCall, text) {
    if (!isCommandActivityTool(toolCallName(toolCall))) return false;
    const t = String(text || '');
    if (/exit code:\s*[1-9]\d*/i.test(t) || /Process exited with code\s+[1-9]\d*/i.test(t)) return false;
    if (/Tool is running as a background task/i.test(t)) return false;
    return /The command completed successfully/i.test(t)
      || /finished,\s*exit code:\s*0/i.test(t)
      || /Process exited with code\s+0/i.test(t)
      || !!t.trim();
  }

  function stripToolOutputEnvelope(text) {
    const raw = String(text || '').replace(/\r\n/g, '\n');
    const lines = raw.split('\n');
    const outIdx = lines.findIndex(line => line.trim() === 'Output:');
    if (outIdx < 0) return raw;
    const exit = raw.match(/^Process exited with code\s+(-?\d+)/m);
    if (exit && exit[1] !== '0') return raw;
    let body = lines.slice(outIdx + 1);
    if (body[0] && /^Total output lines:\s+\d+\s*$/.test(body[0].trim())) body = body.slice(1);
    return body.join('\n').replace(/\n+$/, '');
  }

  function renderToolCodePreview(toolCall, text) {
    const info = toolCallCommandInfo(toolCall);
    if (!info) return null;
    const code = stripToolOutputEnvelope(text);
    if (!code.trim()) return null;
    const loc = info.start ? ':' + info.start + (info.end && info.end !== info.start ? '-' + info.end : '') : '';
    const label = info.path + loc;
    const lang = info.lang || '';
    const langLabel = lang
      ? '<span class="cb-lang">' + escapeHtml(lang) + '</span>'
      : '<span class="cb-lang cb-lang-plain">code</span>';
    const wrap = document.createElement('div');
    wrap.className = 'tool-result-code-preview cb-wrap';
    wrap.innerHTML =
      '<div class="tool-result-code-title">'
        + '<span>File excerpt</span>'
        + '<code title="' + escapeAttr(label) + '">' + escapeHtml(_pathBase(info.path) + loc) + '</code>'
      + '</div>'
      + '<div class="cb-head">' + langLabel
        + '<button class="cb-copy" type="button" title="Copy">Copy</button>'
      + '</div>'
      + '<pre class="cb"><code>' + _linkifyEscapedUrls(highlightCode(code, lang)) + '</code></pre>';
    return wrap;
  }

  function _isNoopAckText(s) {
    if (!s) return false;
    const t = String(s).trim().toLowerCase().replace(/\s+/g, ' ');
    return /^(no response (requested|needed|required)|nothing (to (do|add|report)|further( to (do|add))?)|acknowledged|no action (needed|required)|standing by|continuing|will continue)\.?$/.test(t);
  }

  function _resultOutcomeInfo(ev) {
    if (!ev) return null;
    if (ev.no_agent_output) {
      // CCC-424: the resumable rollout.jsonl never records *why* a turn
      // produced no text — the server correlates CCC's raw process capture
      // to find the actual turn.failed error (network blip, dropped stream,
      // etc). When it finds one, show that instead of the generic message.
      if (ev.turn_failed_error) {
        return {
          kind: 'error',
          label: 'Stopped - turn failed',
          detail: String(ev.turn_failed_error).trim()
            + ' The turn did not complete; use the wake/follow-up box below to retry.',
        };
      }
      return {
        kind: 'silent',
        label: 'No visible response',
        detail: 'Codex completed this turn without assistant text; this is not a live stuck process. Use the wake/follow-up box below if you expected an answer.',
      };
    }
    const sub = String(ev.subtype || '').toLowerCase();
    const errText = String(ev.error || '').trim();
    const isError = !!ev.is_error || /error|failed|aborted|interrupt/.test(sub);
    if (!isError && !errText) return null;
    const blob = (sub + ' ' + errText).toLowerCase();
    const exhausted = /(rate.?limit|usage.?limit|quota|exhaust|insufficient.?quota|resource_exhausted|no tokens?\b|out of (tokens|credit)|credit balance|reached your[^.]*limit|429|too many requests|subscription)/.test(blob);
    if (exhausted) {
      return {
        kind: 'exhausted',
        label: 'Stopped - no tokens remaining',
        detail: (errText ? errText.slice(0, 200) : 'Token / subscription limit reached.')
          + ' Top up or wait for the limit to reset, then resume.',
      };
    }
    if (isError) {
      return {
        kind: 'error',
        label: 'Stopped - error',
        detail: errText.slice(0, 240) || (sub ? sub.replace(/_/g, ' ') : ''),
      };
    }
    return null;
  }

  function _planEntriesHtml(entries) {
    const rows = (Array.isArray(entries) ? entries : [])
      .filter(e => e && String(e.content || '').trim())
      .map(e => {
        const status = String(e.status || 'pending').toLowerCase();
        const glyph = status === 'completed' ? '✓' : (status === 'in_progress' ? '●' : '○');
        const cls = status === 'completed' ? ' is-done' : (status === 'in_progress' ? ' is-active' : '');
        return '<div class="plan-row' + cls + '"><span class="plan-glyph">' + glyph + '</span> '
          + escapeHtml(String(e.content)) + '</div>';
      });
    if (!rows.length) return '';
    return '<div class="stream-block-plan plan-card">' + rows.join('') + '</div>';
  }

  function _diffLinesHtml(diffText) {
    return String(diffText || '').split('\n').map(line => {
      const cls = line.startsWith('@@') ? ' diff-hunk'
        : (line.startsWith('+') && !line.startsWith('+++')) ? ' diff-add'
        : (line.startsWith('-') && !line.startsWith('---')) ? ' diff-delete' : '';
      return '<span class="diff-line' + cls + '">' + escapeHtml(line) + '</span>';
    }).join('\n');
  }

  function _turnDiffBlockHtml(b) {
    const files = Array.isArray(b && b.files) ? b.files : null;
    let body = '';
    if (files && files.length) {
      body = files.map(file => {
        if (!file || typeof file !== 'object') return '';
        const filePath = String(file.path || file.filePath || file.file_path || 'Change');
        const fileDiff = file.diff || file.unified_diff || file.patch;
        const kind = file.kind || file.type || file.status;
        const kindHtml = kind ? '<span class="turn-diff-file-badge">' + escapeHtml(String(kind)) + '</span>' : '';
        return '<div class="turn-diff-file">'
          + '<div class="turn-diff-file-head"><strong>' + escapeHtml(filePath) + '</strong>' + kindHtml + '</div>'
          + (fileDiff ? '<pre class="turn-diff">' + _diffLinesHtml(fileDiff) + '</pre>' : '')
          + '</div>';
      }).join('');
    } else {
      const rawDiff = b && (b.diff || b.patch);
      if (rawDiff) body = '<pre class="turn-diff">' + _diffLinesHtml(rawDiff) + '</pre>';
    }
    if (!body) return '';
    return '<div class="stream-block-diff turn-diff-card"><div class="turn-diff-card-head">Turn diff</div>' + body + '</div>';
  }

  function _toolResultImageKey(img) {
    return String(img && img.line != null ? img.line : '') + ':' + String(img && img.idx != null ? img.idx : 0);
  }

  function _toolResultImagesCardHtml(ev, convId) {
    const imgs = (ev && Array.isArray(ev.images)) ? ev.images : [];
    if (!imgs.length) return '';
    let items = '';
    for (const img of imgs) {
      let src = '';
      if (img && img.kind === 'base64' && img.line != null && img.data == null && convId) {
        src = '/api/conv-image?conversation_id=' + encodeURIComponent(convId)
          + '&line=' + encodeURIComponent(img.line)
          + '&idx=' + encodeURIComponent(img.idx != null ? img.idx : 0);
      } else if (img && img.kind === 'base64' && img.data) {
        src = 'data:' + (img.media_type || 'image/png') + ';base64,' + img.data;
      } else if (img && img.kind === 'path' && img.session_id && img.filename) {
        src = '/image-cache/' + encodeURIComponent(img.session_id) + '/' + encodeURIComponent(img.filename);
      }
      if (!src) continue;
      const ext = String((img.media_type || 'image/png').split('/')[1] || 'png').replace(/[^a-z0-9]/gi, '') || 'png';
      const fname = 'tool-image-L' + (img.line != null ? img.line : 'x') + '-' + (img.idx != null ? img.idx : 0) + '.' + ext;
      items += '<figure class="tool-result-image" data-tool-image-key="' + escapeAttr(_toolResultImageKey(img))
        + '" data-image-tool-use-id="' + escapeAttr(String((ev && ev.tool_use_id) || '')) + '">'
        + '<button type="button" class="tool-result-image-zoom" data-zoom-src="' + escapeAttr(src) + '"'
        + ' data-zoom-title="' + escapeAttr(fname) + '" title="Enlarge" aria-label="Enlarge image">+</button>'
        + '<a href="' + escapeAttr(src) + '" target="_blank" rel="noopener" title="Open full size">'
        + '<img class="msg-image" src="' + escapeAttr(src) + '" alt="Image returned by tool" loading="lazy"'
        + ' onerror="this.closest(\'figure\').classList.add(\'is-missing\')"></a>'
        + '<figcaption><a href="' + escapeAttr(src) + '" target="_blank" rel="noopener">Open</a>'
        + ' · <a href="' + escapeAttr(src) + '" download="' + escapeAttr(fname) + '">Download</a>'
        + '<span class="tool-result-image-missing"> · image unavailable</span></figcaption>'
        + '</figure>';
    }
    if (!items) return '';
    return '<div class="stream-block-image-gen image-gen-card tool-result-images" data-tool-use-id="'
      + escapeAttr(String((ev && ev.tool_use_id) || '')) + '">'
      + '<div class="image-gen-card-head"><span class="image-gen-icon" aria-hidden="true">◫</span> '
      + (imgs.length === 1 ? 'Image from tool' : imgs.length + ' images from tool') + '</div>'
      + '<div class="image-gen-media">' + items + '</div>'
      + '</div>';
  }

  function _codexRequestBodyHtml(ev) {
    const method = String((ev && ev.method) || '');
    const params = (ev && ev.params) || {};
    const isApproval = /approval|requestApproval/i.test(method);
    const isQuestion = /requestUserInput/i.test(method);
    const isElicitation = /elicitation/i.test(method);
    const isPermission = /permission/i.test(method) && !isApproval;
    let title = 'Codex has a question';
    if (isApproval) title = 'Approval needed';
    else if (isPermission) title = 'Permission requested';
    else if (isElicitation) title = 'More information needed';
    const reason = params.reason || params.message || params.description || '';
    let body = reason ? '<div class="codex-request-reason">' + escapeHtml(String(reason)) + '</div>' : '';
    if (isApproval) {
      const command = params.command || params.cmd;
      if (command) {
        body += '<pre class="codex-request-command">'
          + escapeHtml(Array.isArray(command) ? command.join(' ') : String(command)) + '</pre>';
      }
      if (params.cwd) {
        body += '<div class="codex-request-row"><strong>Workspace</strong><span>' + escapeHtml(String(params.cwd)) + '</span></div>';
      }
      body += '<div class="codex-request-actions">'
        + '<button type="button" class="codex-request-btn is-primary" data-decision="accept">Approve</button>'
        + '<button type="button" class="codex-request-btn" data-decision="acceptForSession">Approve session</button>'
        + '<button type="button" class="codex-request-btn is-negative" data-decision="decline">Deny</button>'
        + '<button type="button" class="codex-request-btn is-negative" data-decision="cancel">Cancel</button>'
        + '</div>';
    } else if (isQuestion) {
      const questionsRaw = params.questions || params.items || params.question || [];
      const questions = Array.isArray(questionsRaw) ? questionsRaw : [questionsRaw];
      let lastQid = '0';
      questions.forEach((q, qi) => {
        if (!q || typeof q !== 'object') return;
        const qid = String(q.id || q.questionId || qi);
        lastQid = qid;
        const header = q.header ? '<div class="codex-request-q-header">' + escapeHtml(String(q.header)) + '</div>' : '';
        const text = q.question ? '<div class="codex-request-q-text">' + escapeHtml(String(q.question)) + '</div>' : '';
        const options = Array.isArray(q.options || q.choices) ? (q.options || q.choices) : [];
        const optsHtml = options.length
          ? '<div class="codex-request-q-options">' + options.map(o => {
              const isObj = o && typeof o === 'object';
              const value = isObj ? (o.value || o.label || o.id || '') : o;
              const label = isObj ? (o.label || o.value || '') : o;
              return '<button type="button" class="codex-request-btn codex-request-opt"'
                + ' data-question-id="' + escapeAttr(qid) + '"'
                + ' data-answer="' + escapeAttr(String(value)) + '">' + escapeHtml(String(label)) + '</button>';
            }).join('') + '</div>'
          : '';
        body += '<div class="codex-request-question">' + header + text + optsHtml + '</div>';
      });
      body += '<div class="codex-request-actions">'
        + '<input type="text" class="codex-request-free-text" data-question-id="' + escapeAttr(lastQid) + '" placeholder="Type an answer">'
        + '<button type="button" class="codex-request-btn is-primary" data-decision="send-answer">Send</button>'
        + '<button type="button" class="codex-request-btn is-negative" data-decision="cancel">Cancel</button>'
        + '</div>';
    } else if (isPermission) {
      const requested = params.permissions || {};
      body += '<div class="codex-request-permissions">' + Object.keys(requested).map(key =>
        '<label class="codex-request-perm"><input type="checkbox" checked data-perm-key="' + escapeAttr(key) + '"> ' + escapeHtml(key) + '</label>'
      ).join('') + '</div>';
      body += '<div class="codex-request-actions">'
        + '<button type="button" class="codex-request-btn is-primary" data-decision="allow-selected">Allow selected</button>'
        + '<button type="button" class="codex-request-btn is-negative" data-decision="decline">Decline</button>'
        + '</div>';
    } else if (isElicitation) {
      const url = params.url;
      const link = url
        ? '<a class="codex-request-link" href="' + escapeAttr(String(url)) + '" target="_blank" rel="noopener noreferrer">Open secure sign-in</a>'
        : '';
      body += link + '<div class="codex-request-actions">'
        + '<button type="button" class="codex-request-btn is-negative" data-decision="cancel">Cancel</button>'
        + '<button type="button" class="codex-request-btn is-negative" data-decision="decline">Decline</button>'
        + '<button type="button" class="codex-request-btn is-primary" data-decision="accept">' + (url ? 'I completed it' : 'Continue') + '</button>'
        + '</div>';
    } else {
      const rawDecisions = params.availableDecisions || params.decisions || ['accept', 'decline', 'cancel'];
      const decisions = Array.isArray(rawDecisions) ? rawDecisions : Object.keys(rawDecisions);
      body += '<div class="codex-request-actions">' + decisions.map(d => {
        const decision = (d && typeof d === 'object') ? Object.keys(d)[0] : d;
        if (!decision) return '';
        const cls = /accept|approve/i.test(decision) ? ' is-primary' : ' is-negative';
        return '<button type="button" class="codex-request-btn' + cls + '" data-decision="' + escapeAttr(String(decision)) + '">' + escapeHtml(String(decision)) + '</button>';
      }).join('') + '</div>';
    }
    return { title, body };
  }

  function _codexRequestCardHtml(ev, threadId) {
    const fields = _codexRequestBodyHtml(ev);
    const requestId = String((ev && (ev.request_id || ev.key)) || '');
    return '<div class="codex-request-card"'
      + ' data-request-key="' + escapeAttr(String((ev && ev.key) || '')) + '"'
      + ' data-request-id="' + escapeAttr(requestId) + '"'
      + ' data-request-method="' + escapeAttr(String((ev && ev.method) || '')) + '"'
      + ' data-thread-id="' + escapeAttr(String(threadId || '')) + '"'
      + ' data-request-generation="' + escapeAttr(String(ev && ev.generation != null ? ev.generation : '')) + '">'
      + '<div class="codex-request-title">' + escapeHtml(fields.title) + '</div>'
      + fields.body
      + '</div>';
  }

  function _viewIsWebUiPane($view) {
    return !!($view && $view.closest && $view.closest('.conv-pane.is-webui-session'));
  }

  function _stripKimiControlXml(text) {
    let out = String(text || '');
    if (out.indexOf('<') === -1) return out;
    // Paired wrappers (attribute-tolerant), then unclosed-truncated tails.
    out = out
      .replace(/<system-reminder\b[^>]*>[\s\S]*?<\/system-reminder\s*>/g, '')
      .replace(/<kimi-skill-loaded\b[^>]*>[\s\S]*?<\/kimi-skill-loaded\s*>/g, '')
      .replace(/<command-(name|message|args)\b[^>]*>[\s\S]*?<\/command-\1\s*>/g, '')
      .replace(/<system-reminder\b[^>]*>[\s\S]*$/g, '')
      .replace(/<kimi-skill-loaded\b[^>]*>[\s\S]*$/g, '')
      .replace(/<\/?local-command-[a-z-]+\s*>/g, '');
    return out;
  }

  const _KIMI_TOOL_PEEK_LINES = 3;

  const _KIMI_TOOL_PEEK_COLS = 160;

  function _kimiToolPeekHtml(outputText) {
    let text = String(outputText || '');
    // Codex wraps every exec result in "Script completed / Wall time N
    // seconds / Output:" boilerplate; it's noise in a peek, so drop it
    // (the expanded body still shows the raw output).
    text = text.replace(/^\s*Script completed\s*\n\s*Wall time[^\n]*\n\s*Output:[ \t]*\n?/i, '');
    text = text.replace(/\s+$/, '');
    if (!text) return '';
    const lines = text.split('\n');
    const shown = lines.slice(0, _KIMI_TOOL_PEEK_LINES).map(function (l) {
      l = l.replace(/\s+$/, '');
      return l.length > _KIMI_TOOL_PEEK_COLS ? l.slice(0, _KIMI_TOOL_PEEK_COLS - 1) + '…' : l;
    });
    const more = lines.length - shown.length;
    return '<div class="kimi-tool-peek">' + escapeHtml(shown.join('\n'))
      + (more > 0 ? '<span class="kimi-tool-peek-more">(' + more + ' more line' + (more === 1 ? '' : 's') + ')</span>' : '')
      + '</div>';
  }

  function _kimiThinkingHtml(text, renderAsMarkdown) {
    const full = String(text || '');
    if (!full.trim()) return '';
    const paragraphs = full.split(/\n{2,}/).filter(p => p.trim().length > 0);
    const renderBody = value => renderAsMarkdown ? renderMarkdown(value) : escapeHtml(value);
    const bodyTag = renderAsMarkdown ? 'div' : 'pre';
    if (paragraphs.length <= 1) {
      return '<div class="kimi-thinking is-single"><' + bodyTag + ' class="kimi-thinking-full">' + renderBody(full) + '</' + bodyTag + '></div>';
    }
    const teaser = paragraphs[paragraphs.length - 1];
    return '<div class="kimi-thinking" onclick="this.classList.toggle(\'open\')" title="Click to show the full thinking">'
      + '<' + bodyTag + ' class="kimi-thinking-teaser">' + renderBody(teaser) + '</' + bodyTag + '>'
      + '<' + bodyTag + ' class="kimi-thinking-full">' + renderBody(full) + '</' + bodyTag + '>'
      + '</div>';
  }

  async function _loadCompleteToolOutput(button) {
    if (button.disabled || button.dataset.loaded) return;
    button.disabled = true;
    button.textContent = 'Loading full output…';
    try {
      const response = await fetch(button.dataset.outputUrl, { cccPriority: 'user' });
      const data = await response.json();
      if (!response.ok || !data.ok || typeof data.output !== 'string') {
        throw new Error(data.error || 'Could not load full output');
      }
      const output = button.parentElement.querySelector('.kimi-tool-io.is-output');
      if (output) output.textContent = data.output;
      button.dataset.loaded = '1';
      button.remove();
    } catch (error) {
      button.disabled = false;
      button.textContent = 'Retry loading full output';
      button.title = error.message || 'Could not load full output';
    }
  }

  function _kimiUpdateToolGroupHead(grp) {
    const rows = Array.from(grp.querySelectorAll('.kimi-tool-group-body > .kimi-tool'));
    const count = rows.length;
    let state = 'done';
    if (rows.some(r => r.querySelector('.kimi-tool-status.running'))) state = 'running';
    else if (rows.some(r => r.querySelector('.kimi-tool-status.error'))) state = 'failed';
    grp.dataset.kimiGroupState = state;
    const title = grp.querySelector('.kimi-tg-title');
    if (title) {
      const names = [];
      for (const r of rows) {
        const label = r.querySelector('.kimi-tool-name')?.textContent.trim();
        if (label && !names.includes(label)) names.push(label);
      }
      const namesSuffix = names.length ? ' (' + names.join(', ') + ')' : '';
      title.textContent = count + ' tool call' + (count === 1 ? '' : 's') + namesSuffix;
    }
    // CCC-254: purple wt / ccc / CCC API chips in the header, so a collapsed
    // group still shows its orchestration use outside compact view too.
    const orch = [];
    for (const r of rows) {
      (r.dataset.ccOrch || '').split('|').filter(Boolean).forEach(l => { if (!orch.includes(l)) orch.push(l); });
    }
    const want = orch.slice(0, 4).concat(orch.length > 4 ? ['+' + (orch.length - 4)] : []);
    let orchEl = grp.querySelector(':scope > .kimi-tool-group-head > .kimi-tg-orch');
    if (want.length && !orchEl && title) {
      orchEl = document.createElement('span');
      orchEl.className = 'kimi-tg-orch';
      title.insertAdjacentElement('afterend', orchEl);
    }
    if (orchEl && orchEl.dataset.orch !== want.join('|')) {
      orchEl.dataset.orch = want.join('|');
      orchEl.innerHTML = want.map(l => '<span class="kimi-tg-orch-chip" data-cc-orch="' + escapeAttr(l) + '"></span>').join('');
    }
    const meta = grp.querySelector('.kimi-tg-meta');
    if (meta) meta.textContent = '· ' + state;
  }

  function _lastRealTranscriptChild($view) {
    let node = $view.lastElementChild;
    while (node && (
      node.classList.contains('conv-live-tool-strip')
      || node.classList.contains('conv-live-tool-inline')
      || node.classList.contains('stream-bubble')
    )) {
      node = node.previousElementSibling;
    }
    return node;
  }

  function isRoutineCodexCoordination(ev) {
    // Activity observations belong in the live indicator, not durable chat
    // rows. Keep delivery, recovery and unfamiliar events visible.
    return !!ev && ev.type === 'system' && ev.subtype === 'codex_coordination'
      && ['external_turn_started', 'external_turn_ended', 'ccc_turn_started', 'ccc_turn_completed'].includes(ev.kind);
  }

  function _turnOrdinalBefore(view, turnId) {
    if (!view || turnId == null) return 0;
    const esc = (window.CSS && CSS.escape) ? CSS.escape(String(turnId)) : String(turnId);
    return view.querySelectorAll('.event[data-jsonl-line][data-turn-id="' + esc + '"]').length;
  }

  function _normalizeEventText(s) {
    return String(s || '').replace(/\s+/g, ' ').trim();
  }

  function _lastRenderedRowAnchor(view) {
    const children = view.children;
    for (let i = children.length - 1; i >= 0; i--) {
      const child = children[i];
      const isRow = (el) => el.matches && (el.matches('.event[data-jsonl-line]') || el.matches('.event[data-live-key]'));
      if (isRow(child)) return child;
      if (child.querySelector && child.querySelector('.event[data-jsonl-line], .event[data-live-key]')) return child;
    }
    return null;
  }

  function _upsertProvisionalNode(view, newDiv, existingNode) {
    if (existingNode) {
      if (existingNode.innerHTML === newDiv.innerHTML) return existingNode;
      existingNode.replaceWith(newDiv);
      return newDiv;
    }
    const anchor = _lastRenderedRowAnchor(view);
    if (anchor) view.insertBefore(newDiv, anchor.nextSibling);
    else view.appendChild(newDiv);
    return newDiv;
  }

  function _findMatchingProvisionalNode(view, ev, div) {
    const provisionalNodes = view.querySelectorAll('.event[data-live-key]');
    const sameType = node => !ev.type || node.classList.contains(ev.type);
    if (!provisionalNodes.length) return null;
    // 1) Tools: rollout call_id equals the live item id. Per the plan's
    // Slice-1 findings this is effectively dead in practice (app-server item
    // ids don't equal rollout call_ids), but it's cheap to try first.
    const callId = ev.tool_use_id || ev.call_id;
    if (callId) {
      for (const node of provisionalNodes) {
        const liveKey = node.dataset.liveKey || '';
        const itemId = liveKey.slice(liveKey.indexOf(':') + 1);
        if (itemId && itemId === String(callId)) return node;
      }
    }
    // 2) Turn: turn_id plus ordinal position within the turn.
    if (ev.turn_id != null) {
      const turnId = String(ev.turn_id);
      const ordinal = String(_turnOrdinalBefore(view, turnId));
      for (const node of provisionalNodes) {
        if (sameType(node) && node.dataset.turnId === turnId && node.dataset.turnOrdinal === ordinal) return node;
      }
    }
    // 3) Fallback: normalized text equality.
    const text = _normalizeEventText(div.textContent);
    if (text) {
      for (const node of provisionalNodes) {
        if (sameType(node) && _normalizeEventText(node.textContent) === text) return node;
      }
    }
    return null;
  }

  function _confirmedResultForTurn(view, turnId) {
    if (!view || turnId == null) return false;
    const esc = (window.CSS && CSS.escape) ? CSS.escape(String(turnId)) : String(turnId);
    return !!view.querySelector('.event.result[data-jsonl-line][data-turn-id="' + esc + '"]');
  }

  function refreshActivityTimers(view) {
    const timers = view._activityTimers;
    if (!timers?.[0]?.isConnected) return;
    const now = Date.now();
    for (const timer of timers) {
      if (timer.hidden) continue;
      const seconds = Math.max(0, Math.floor((now - Number(timer.dataset.since)) / 1000));
      const text = seconds < 60 ? seconds + 's'
        : seconds < 3600 ? Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's'
          : Math.floor(seconds / 3600) + 'h ' + (Math.floor(seconds / 60) % 60) + 'm';
      if (timer.textContent !== text) timer.textContent = text;
    }
  }

  // Keep the transcript sources in place for reconciliation, paging and tool
  // result routing. Only their collapsed presentation is replaced by a slot.
  // One turn reads as one "Working" block: narration that is followed by more
  // activity folds into it and shows as its live note, replaced in place. Text
  // after the last activity (the answer, or narration not yet followed by a
  // tool) stays visible below the block. User messages end the block.
  function _syncActivityRuns(view) {
    const stale = new Set(view.querySelectorAll('.activity-run'));
    const sources = new Set(view.querySelectorAll('.activity-run-source'));
    let members = [];
    let pendingText = [];
    let tailActivity = null;
    const flush = () => {
      if (!members.length) return;
      const first = members[0];
      let slot = first.previousElementSibling;
      if (!slot?.classList.contains('activity-run')) {
        slot = document.createElement('div');
        slot.className = 'activity-run';
        slot.innerHTML = '<div class="activity-run-header"><button type="button" class="activity-run-toggle" aria-expanded="false">Working</button>'
          + '<span class="activity-run-orchestration"></span></div>'
          + '<div class="activity-run-message"><div class="activity-run-note" dir="auto"></div>'
          + '<span class="activity-run-timer" role="timer" aria-label="Time since status update" hidden></span></div>'
          + '<div class="activity-run-message"><div class="activity-run-preview" dir="auto"></div>'
          + '<span class="activity-run-timer" role="timer" aria-label="Time since tool update" hidden></span></div>';
        slot.querySelector('button').addEventListener('click', () => {
          const open = slot.classList.toggle('is-expanded');
          slot.querySelector('button').setAttribute('aria-expanded', String(open));
          for (const member of slot._activityMembers) {
            member.classList.toggle('activity-run-open', open);
            // Opening history shows the thinking text in full; tool calls stay
            // collapsed (they open one by one on demand).
            if (open && member.matches('.thinking-run') && !member.classList.contains('is-expanded')) {
              member.classList.add('is-expanded');
              member.querySelector('button')?.setAttribute('aria-expanded', 'true');
              const body = member.querySelector('.t-body');
              const teaser = member.querySelector('.thinking-run-preview');
              if (body) body.hidden = false;
              if (teaser) teaser.hidden = true;
            }
            if (open && member.matches('.tool-call-group, .kimi-tool-group')) member.classList.add('collapsed');
          }
        });
        first.before(slot);
      }
      stale.delete(slot);
      slot._activityMembers = members;
      for (const member of members) {
        sources.delete(member);
        if (member.matches('.thinking-run')) {
          // Thinking stays visible (in full) even while Working is collapsed;
          // only tool calls fold away.
          member.classList.remove('activity-run-source', 'activity-run-open');
          if (!member._thinkingAutoOpened) {
            member._thinkingAutoOpened = true;
            member.classList.add('is-expanded');
            member.querySelector('button')?.setAttribute('aria-expanded', 'true');
            const body = member.querySelector('.t-body');
            const teaser = member.querySelector('.thinking-run-preview');
            if (body) body.hidden = false;
            if (teaser) teaser.hidden = true;
          }
          continue;
        }
        member.classList.add('activity-run-source');
        member.classList.toggle('activity-run-open', slot.classList.contains('is-expanded'));
      }
      const narration = members.filter(member => member.matches('.assistant-text'));
      const activity = members.filter(member => !member.matches('.assistant-text'));
      const toolActivity = activity.filter(member => !member.matches('.thinking-run'));
      const lastNote = narration[narration.length - 1];
      const noteText = lastNote ? (lastNote.textContent || '').trim().split(/\n\s*\n/).filter(Boolean).pop() || '' : '';
      const note = slot.querySelector('.activity-run-note');
      if (note.textContent !== noteText) note.textContent = noteText;
      // Thinking is shown on its own; the preview line tracks the latest tool.
      const latest = toolActivity[toolActivity.length - 1] || activity[activity.length - 1];
      const calls = latest.matches('.tool-call, .kimi-tool') ? [latest] : Array.from(latest.querySelectorAll('.tool-call, .kimi-tool'));
      const lastCall = calls[calls.length - 1];
      const detail = latest.matches('.thinking-run') ? latest.querySelector('.thinking-run-preview')
        : (lastCall || latest).querySelector('.tool-detail, .kimi-tool-arg, .tcg-label, .kimi-tg-label');
      const preview = slot.querySelector('.activity-run-preview');
      const failed = members.some(member => member.matches('.tool-call-fail, .kimi-tool.err')
        || member.querySelector('.tool-call-fail, .kimi-tool-status.error'));
      const text = toolActivity.length ? (detail?.textContent || latest.textContent || '').trim() : '';
      const value = (failed ? 'Failed: ' : '') + (text ? text.replace(/[.…]+$/u, '') + '...' : '');
      if (preview.textContent !== value) preview.textContent = value;
      tailActivity = { slot, note: noteText ? lastNote : null, tool: text ? lastCall || latest : null };
      slot.classList.toggle('has-error', failed);
      const title = failed ? 'Tool failed — show activity history' : 'Show activity history';
      if (slot.querySelector('button').title !== title) slot.querySelector('button').title = title;
      const labels = _ccOrchLabels(activity.map(el => ({ el })));
      const chips = slot.querySelector('.activity-run-orchestration');
      const visibleLabels = labels.slice(0, 4);
      if (labels.length > 4) visibleLabels.push('+' + (labels.length - 4));
      const key = visibleLabels.join('|');
      if ((chips.dataset.labels || '') !== key) {
        chips.dataset.labels = key;
        chips.innerHTML = visibleLabels.map(label =>
          '<span class="cc-orch-chip" data-cc-orch="' + escapeAttr(label) + '" aria-label="' + escapeAttr(label) + '"></span>'
        ).join('');
      }
      members = [];
    };
    const visit = (el) => {
      // Empty reasoning entries have no visible content and must not split
      // consecutive commands into separate Working disclosures.
      if (el.matches('.activity-run, .thinking-run-source, .thinking-block-silent, .line-num, .msg-ts, .actor-chip, .event-model-meta, .event-token-chips, .assistant-message-actions, .kimi-marker, .kimi-answer-meta')) return;
      // Questions, approvals, handoffs and returned images must remain visible.
      const needsAttention = el.matches('.ccc-video-cleared, .acp-needs-approval, .codex-subagent')
        || el.querySelector('.acp-needs-approval, .tool-result-images, .tool-inline-image, .codex-subagent')
        || Array.from(el.querySelectorAll('.kimi-tool-name')).some(node => /^(?:Question|AskUserQuestion|sidekick|run_subagent)$/.test(node.dataset.toolName || node.textContent.trim()))
        || (el.matches('.tool-call') && toolCallCarriesConversationContext(el))
        || Array.from(el.querySelectorAll('.tool-call')).some(toolCallCarriesConversationContext);
      if (needsAttention && el.matches('.tool-call-group, .kimi-tool-group')) {
        el.classList.remove('collapsed');
        el.querySelector(':scope > .kimi-tool-group-head')?.setAttribute('aria-expanded', 'true');
      }
      if (!needsAttention && el.matches('.thinking-run, .tool-call-group, .tool-call, .kimi-tool-group, .kimi-tool')) {
        members.push(...pendingText, el);
        pendingText = [];
      } else if (el.matches('.assistant-text') && !el.closest('.ccc-video-cleared')) {
        // Held until we know whether more activity follows in this turn.
        pendingText.push(el);
      } else {
        flush();
        pendingText = [];
      }
    };
    for (const event of Array.from(view.children)) {
      if (event.matches('.activity-run, .conv-codex-state, .conv-live-tool-inline, .conv-live-tool-strip, .spawn-stats')) continue;
      if (event.matches('.event.assistant, .kimi-turn') && !event.classList.contains('ccc-video-cleared')) {
        for (const child of Array.from(event.children)) visit(child);
      } else visit(event);
      if (event.matches('.event.user_text, .event.result')) tailActivity = null;
    }
    flush();
    for (const source of sources) source.classList.remove('activity-run-source', 'activity-run-open');
    for (const slot of stale) slot.remove();

    const timers = tailActivity ? tailActivity.slot.querySelectorAll('.activity-run-timer') : [];
    // Only the latest Working block in the unfinished turn has ticking ages.
    if (view._activityTimers?.[0] !== timers[0]) {
      for (const timer of view._activityTimers || []) timer.hidden = true;
    }
    view._activityTimers = timers;
    if (tailActivity) {
      [tailActivity.note, tailActivity.tool].forEach((source, index) => {
        const timer = timers[index];
        const epoch = source?.closest('[data-ts-epoch]')?.dataset.tsEpoch;
        timer.hidden = !(Number(epoch) > 0);
        if (!timer.hidden && timer.dataset.since !== epoch) timer.dataset.since = epoch;
      });
      refreshActivityTimers(view);
    }
  }

  function _syncThinkingRuns(view) {
    const stale = new Set(view.querySelectorAll('.thinking-run'));
    const chrome = '.line-num, .msg-ts, .actor-chip, .event-model-meta, .event-token-chips, .assistant-message-actions';
    let run = null;
    const visit = (source) => {
      if (!source.classList.contains('thinking-run-source')) source.classList.add('thinking-run-source');
      if (!run) {
        run = source.previousElementSibling;
        if (!run?.classList.contains('thinking-run')) {
          run = document.createElement('div');
          run.className = 'thinking-block thinking-run';
          run.innerHTML = '<button type="button" class="thinking-toggle" aria-expanded="false" title="Show full latest thought">Thinking</button>'
            + '<div class="thinking-run-preview" dir="auto"></div><div class="t-body" dir="auto" hidden></div>';
          const slot = run;
          // The whole block toggles, not just the small label: on a phone the
          // preview text is the obvious tap target. Links and text selections
          // inside the expanded thought pass through.
          slot.addEventListener('click', (ev) => {
            if (ev.target.closest('a') || (!ev.target.closest('button') && String(window.getSelection?.() || '').trim())) return;
            const open = slot.classList.toggle('is-expanded');
            slot.querySelector('button').setAttribute('aria-expanded', String(open));
            slot.querySelector('button').title = open ? 'Show latest thought preview' : 'Show full latest thought';
            slot.querySelector('.t-body').hidden = !open;
            slot.querySelector('.thinking-run-preview').hidden = open;
          });
          source.before(run);
        }
      }
      stale.delete(run);
      run._latestThinkingSource = source;
    };
    for (const event of view.children) {
      if (event.matches('.activity-run, .conv-codex-state, .conv-live-tool-inline, .conv-live-tool-strip')) continue;
      if (!event.matches('.event.assistant, .kimi-turn') || event.classList.contains('ccc-video-cleared')) {
        run = null;
        continue;
      }
      let thoughtsOnly = true;
      let hasThought = false;
      for (const block of Array.from(event.children)) {
        if (block.matches('.activity-run, .thinking-run, .kimi-marker, .kimi-answer-meta, ' + chrome)) continue;
        if (block.matches('.thinking-block:not(.thinking-block-silent), .kimi-thinking')) {
          visit(block);
          hasThought = true;
        } else {
          run = null;
          thoughtsOnly = false;
        }
      }
      if (event.matches('.event.assistant')) event.classList.toggle('thinking-run-event', hasThought && thoughtsOnly);
    }
    for (const slot of view.querySelectorAll('.thinking-run')) {
      if (stale.has(slot)) { slot.remove(); continue; }
      // Read only the last source in each run, not every older thought on
      // every poll. Both renderers already escaped/rendered this safely.
      const source = slot._latestThinkingSource;
      const sourceBody = source.querySelector('.t-body, .kimi-thinking-full') || source;
      const html = sourceBody.innerHTML;
      const text = Array.from(sourceBody.childNodes).map(node => node.textContent || '').join('\n\n');
      const body = slot.querySelector('.t-body');
      if (slot._thinkingHtml !== html) { body.innerHTML = html; slot._thinkingHtml = html; }
      // A growing streamed item may contain earlier paragraphs: keep the
      // newest paragraph in the preview while expansion retains the whole item.
      const preview = text.trim().split(/\n\s*\n/).filter(Boolean).pop() || '';
      const teaser = slot.querySelector('.thinking-run-preview');
      if (teaser.textContent !== preview) teaser.textContent = preview;
    }
    _syncActivityRuns(view);
  }

  function _ccOrchResultLabels(labels, output) {
    // add/claim/take learn their ticket only when the result arrives. Do
    // not infer one from arbitrary prose, list output, or ambiguous batches.
    const allocating = labels.filter(l => /^wt (?:add|claim|take)(?: · [\w.-]+)?$/.test(l));
    if (allocating.length !== 1 || / · [A-Z][A-Z0-9_]*-\d+$/.test(allocating[0])) return labels;
    const refs = new Set();
    const text = String(output || '').replace(/\\"/g, '"');
    const re = /(?:["']ref["']\s*:\s*["']|\b(?:FILED|CLAIMED|HANDED BACK):\s*)([A-Z][A-Z0-9_]*-\d+)\b/g;
    let m;
    while ((m = re.exec(text))) refs.add(m[1]);
    if (refs.size !== 1) return labels;
    const ref = Array.from(refs)[0];
    const queue = allocating[0].split(' · ')[1];
    if (queue && ref.slice(0, ref.lastIndexOf('-')) !== queue) return labels;
    return labels.map(l => l === allocating[0] ? l.split(' · ')[0] + ' · ' + ref : l);
  }

  function _ccOrchLabels(units) {
    const seen = new Set();
    for (const { el } of units) {
      const calls = el.matches('.tool-call, .kimi-tool') ? [el] : el.querySelectorAll('.tool-call, .kimi-tool');
      for (const tc of calls) {
        (tc.dataset.ccOrch || '').split('|').filter(Boolean).forEach(l => seen.add(l));
      }
    }
    return Array.from(seen);
  }

  const DEBUG_MODE_STORAGE_KEY = 'ccc-debug-mode';

  function debugModeEnabled() {
    try { return localStorage.getItem(DEBUG_MODE_STORAGE_KEY) === '1'; }
    catch (_) { return false; }
  }
    return {
      SESSION_STATE_INSTRUCTION,
      SIBLING_PROMPT_PREFIX_LC,
      SIBLING_HEADING_RE,
      extractSiblingTaskBody,
      cleanIssuePrompt,
      eventTextString,
      splitFirstSentence,
      SESSION_ENGINE_LABELS,
      rowReasoningEffort,
      formatModelEffort,
      clearLiveGeneratingIndicator,
      _anchorWakeBreakdown,
      isCommandActivityTool,
      anchorCodexStateBadge,
      _reanchorToTailUnlessOnlyTransientBetween,
      assistantMessageActionsHtml,
      queuedRowDeliveryId,
      pastedImageThumbsHtml,
      renderCodexUserText,
      tsSpan,
      parseCompactResumeBlock,
      _compactEngineLabel,
      _compactTokenLabel,
      _compactElapsedLabel,
      _COMPACT_FOLD_RADIUS,
      _compactRowIsFoldable,
      _foldCompactRecords,
      renderCompactResumeCard,
      renderDevinHandoffCard,
      renderSkillLoadedCard,
      renderCodexSkillLoadedCard,
      _convPaneHost,
      convPaneElById,
      getConvViewForPane,
      CONV_BOTTOM_TOLERANCE,
      conversationDistanceFromBottom,
      isConversationAtBottom,
      nowStamp,
      eventStamp,
      _freeServedText,
      _formatTokens,
      _formatCompactDuration,
      _formatTokensAntigravity,
      _formatAntigravityTokenChips,
      CACHE_MISS_CALLOUT_MIN_SHARE,
      _cacheMissCallout,
      CACHE_READ_TOKEN_WEIGHT,
      _cacheReadTokenWeight,
      _cacheAdjustedTurnTokens,
      _cacheAdjustedTurnTitle,
      _cacheAdjustedInlineHtml,
      toolCallName,
      KNOWN_TOOL_SOURCE_BY_NAME,
      splitMcpToolName,
      toolDisplayName,
      toolNamespaceLabel,
      inferredToolSource,
      toolBlockSource,
      toolCallSource,
      toolCallDetailText,
      toolCallForResult,
      toolResultOutputLabel,
      isEditToolName,
      isFileToolName,
      compactPathDetail,
      _isImagePathDetail,
      inlineToolImageHtml,
      formatToolCallDetail,
      CONV_VIEW_MODES,
      convViewMode,
      convVerboseOn,
      renderToolCommandDisclosure,
      renderToolInputDisclosure,
      loadToolInputDisclosure,
      renderEditDisclosure,
      summarizeToolCall,
      toolCallCarriesConversationContext,
      toolGroupCarriesConversationContext,
      splitShellWords,
      shellSearchTerm,
      _stripCdWrapperPrefix,
      fullCommandSummaryLabel,
      compactShellCommandLabel,
      joinCompactList,
      normalizedToolKey,
      toolActionKind,
      uniqueToolDetails,
      detailLooksLikePath,
      detailLabel,
      summarizeToolActionCalls,
      readableToolName,
      summarizeNamedToolCalls,
      _codeLangForPath,
      _pathBase,
      parseCodeReadCommand,
      toolCallCommandInfo,
      toolCallLooksLikeCodeRead,
      toolCallResultSnippet,
      updateToolGroupLabel,
      isRoutineSuccessfulToolResult,
      isSuccessfulCommandToolResult,
      stripToolOutputEnvelope,
      renderToolCodePreview,
      _isNoopAckText,
      _resultOutcomeInfo,
      _planEntriesHtml,
      _diffLinesHtml,
      _turnDiffBlockHtml,
      _toolResultImageKey,
      _toolResultImagesCardHtml,
      _codexRequestBodyHtml,
      _codexRequestCardHtml,
      _viewIsWebUiPane,
      _stripKimiControlXml,
      _KIMI_TOOL_PEEK_LINES,
      _KIMI_TOOL_PEEK_COLS,
      _kimiToolPeekHtml,
      _kimiThinkingHtml,
      _loadCompleteToolOutput,
      _kimiUpdateToolGroupHead,
      _lastRealTranscriptChild,
      isRoutineCodexCoordination,
      _turnOrdinalBefore,
      _normalizeEventText,
      _lastRenderedRowAnchor,
      _upsertProvisionalNode,
      _findMatchingProvisionalNode,
      _confirmedResultForTurn,
      _syncThinkingRuns,
      refreshActivityTimers,
      _ccOrchResultLabels,
      _ccOrchLabels,
      DEBUG_MODE_STORAGE_KEY,
      debugModeEnabled
    };
  }
  root.CCCConversationContent = Object.freeze({ createHelpers });
})(typeof window === 'undefined' ? globalThis : window);
