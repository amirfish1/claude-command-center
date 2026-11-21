/* Shared transcript text renderer. State is read through live lookup adapters. */
(function (root) {
  'use strict';
  function createRenderer(adapters) {
    const { cleanIssuePrompt, sidebarRowDisplayTitle, shortSessionId, sessionCompletionDetails } = adapters;


  // CCC-121: string replace, not a throwaway <div> per call. Every built sidebar
  // row calls this dozens of times, so createElement + textContent + innerHTML
  // serialization was the top self-time in window=all renders. Output matches
  // the old DOM round-trip exactly: null/undefined -> '', and text-node
  // serialization escapes only & < > and U+00A0 (quotes stay; see escapeAttr).
  // Self-contained (no module consts) so it is safe to call from anywhere and
  // tests can extract it by source range.
  function escapeHtml(s) {
    if (s == null) return '';
    const t = String(s);
    if (!/[&<>\u00a0]/.test(t)) return t;
    return t.replace(/[&<>\u00a0]/g, _escapeHtmlChar);
  }

  function _escapeHtmlChar(ch) {
    return ch === '&' ? '&amp;' : ch === '<' ? '&lt;' : ch === '>' ? '&gt;' : '&nbsp;';
  }


  function escapeAttr(s) {
    return escapeHtml(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }


  function unescapeHtml(s) {
    return String(s || '')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&#39;|&#x27;/g, "'")
      .replace(/&amp;/g, '&');
  }


  // Collapse whitespace and clip to `max` chars with a trailing ellipsis.
  // The throughput widget IIFE has its own copy; this is the main-app scope's.
  function truncate(text, max) {
    text = String(text || '').replace(/\s+/g, ' ').trim();
    if (!text) return '';
    return text.length > max ? text.slice(0, Math.max(0, max - 1)) + '...' : text;
  }


  function normalizeMarkdownLinkTarget(raw) {
    let target = unescapeHtml(String(raw || '').trim());
    const angle = /^<([\s\S]+)>(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?$/.exec(target);
    if (angle) return angle[1].trim();
    const titled = /^(\S+)(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))$/.exec(target);
    if (titled) return titled[1].trim();
    return target;
  }


  // Some agents describe a visual that the native client showed transiently
  // as `![label](attached above)`. That phrase is not a retrievable image URL;
  // rendering it as an <img> leaves a misleading broken-image icon.
  function isUnavailableMarkdownImageTarget(target) {
    return /^(?:attached|shown|image)\s+(?:above|below)$/i.test(String(target || '').trim());
  }


  // Codex uses this token to refer to a locally generated visual. It is not
  // Markdown, so turn it into the same session-aware file link used elsewhere
  // in the transcript rather than leaving its implementation syntax visible.
  const CODEX_INLINE_VIS_RE = /::codex-inline-vis\{\s*file=&quot;([\s\S]*?)&quot;\s*\}/g;

  function linkifyCodexInlineVisuals(escapedHtml) {
    if (!escapedHtml) return escapedHtml;
    return String(escapedHtml).replace(CODEX_INLINE_VIS_RE, (_match, rawFilename) => {
      const filename = normalizeMarkdownLinkTarget(rawFilename);
      return filename ? '<span class="codex-inline-vis">Visual: ' + linkifyPath(filename) + '</span>' : '';
    });
  }


  // Replace pasted-image references inside an already-escapeHtml'd string
  // with inline <img> tags. CCC now uploads to `.claude/command-center/
  // pasted-images/`; legacy `.claude/pasted-images/` paths still render.
  // The server's /api/pasted-image route performs the real path sandboxing.
  const PASTED_IMG_RE = /(?:file:\/\/)?(\/[^\s<>"']*?\/\.claude\/(?:command-center\/)?pasted-images\/paste-[\w.-]+?\.(?:png|jpe?g|gif|webp))/gi;

  const PASTED_IMG_MD_LINK_RE = /!?\[[^\]\n]*\]\((?:file:\/\/)?(\/[^\s<>"')]*?\/\.claude\/(?:command-center\/)?pasted-images\/paste-[\w.-]+?\.(?:png|jpe?g|gif|webp))(?:\s+(?:&quot;[^&]*&quot;|'[^']*'))?\)/gi;

  // Annotation screenshots forwarded via the ux-fixes-queue land in user
  // messages as absolute paths like /Users/<u>/.claude/command-center/
  // annotation-screenshots/ann-YYYYMMDD-HHMMSS-XXXXXX.png — render them
  // inline so the user can see what they annotated without leaving the chat.
  const ANNOTATION_IMG_RE = /(?:file:\/\/)?(\/[^\s<>"']*?\/\.claude\/command-center\/annotation-screenshots\/[\w.-]+?\.(?:png|jpe?g|gif|webp))/gi;

  function pastedImageTag(path) {
    const sid = adapters.activeSessionId() || '';
    return '<img class="msg-image pasted-image-inline" src="/api/pasted-image?path='
      + encodeURIComponent(path)
      + (sid ? '&session_id=' + encodeURIComponent(sid) : '')
      + '" alt="pasted image" loading="lazy">';
  }

  function linkifyPastedImages(escapedHtml) {
    if (!escapedHtml) return escapedHtml;
    return String(escapedHtml)
      .replace(PASTED_IMG_MD_LINK_RE, (_m, path) => pastedImageTag(path))
      .replace(PASTED_IMG_RE, (_m, path) => pastedImageTag(path))
      .replace(ANNOTATION_IMG_RE, (_m, path) => pastedImageTag(path));
  }

  function sessionQuickAction(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)
        || typeof value.label !== 'string' || value.label.length > 24 || !/^\p{L}+(?:,? \p{L}+){0,3}$/u.test(value.label)
        || typeof value.reply !== 'string' || !value.reply.trim() || value.reply.trim().length > 400
        || /[\x00-\x1f]/.test(value.reply)) return null;
    return { label: value.label, reply: value.reply.trim() };
  }

  // "Answer [Q12:slug]: keep all questions or use the shorter form." is a
  // decision, and a decision always gets one chip per option (CCC-276).
  // Footers written before NEXT_ACTION accepted arrays only carry the prose,
  // so derive the chips from its alternatives; the reply repeats the tag.
  function decisionQuickActions(next) {
    const s = String(next || '').trim();
    // A delivered artifact still needs an action when its author omitted
    // NEXT_ACTION. Open the literal review URL; never imply it was approved.
    const review = s.match(/^(watch|review|read|open|listen to)\s+(https?:\/\/\S+)\s+(.+)$/i);
    if (review && s.length <= 600 && !/[\x00-\x1f]/.test(s)
        && /\b(?:say|tell|give|share|provide)\b.*\b(?:whether|feedback|verdict|think|works|direction)\b/i.test(review[3])
        && /^https?:\/\/[a-z0-9.-]+(?::\d{1,5})?(?:[/?#][^\s<>"`\\]*)?$/i.test(review[2])) {
      const label = /^watch$/i.test(review[1]) ? 'Watch video' : 'Open review';
      return [{ label, href: review[2], derived: true }];
    }
    const m = s.match(/^(?:answer|choose|decide|pick)\b(.*?):\s+(.+)$/i);
    if (!m) {
      // Older answers often ask for a category without authoring chips.
      // Only copy short, explicit alternatives from a selection question;
      // never turn an offline task or inferred approval into a reply.
      const listed = s.match(/^([^()]+)\(([^()]+)\)/);
      if (!listed || s.length > 600 || /[\x00-\x1f]/.test(s)
          || !/^(?:say|tell me|choose|pick|select)\b/i.test(listed[1])
          || !/\b(?:which|what|type|one)\b/i.test(listed[1])
          || !/\sor\s/i.test(listed[2])) return [];
      const options = listed[2].split(/\s*,\s*or\s+|\s*,\s*|\s+or\s+/i).map(o => o.trim());
      if (options.length < 2 || options.length > 4
          || options.some(o => o.length > 24 || !/^\p{L}+(?:[ -]\p{L}+){0,2}$/u.test(o))
          || new Set(options.map(o => o.toLowerCase().replace(/-/g, ' '))).size !== options.length) return [];
      const tag = (listed[1].match(/\[Q\d+(?::[a-z0-9-]+)?\]|\bQ\d+\b/i) || [''])[0];
      return options.map(o => ({ label: o.replace(/-/g, ' '), reply: (tag ? tag + ' ' : '') + o + '.', derived: true }));
    }
    const tag = (m[1].match(/\[Q\d+(?::[a-z0-9-]+)?\]|\bQ\d+\b/i) || [''])[0];
    const body = m[2].replace(/[.?!]+$/, '').replace(/^(?:whether(?: to)?|between)\s+/i, '').trim();
    if (!/\sor\s/i.test(body)) return [];
    const options = body.split(/\s*,\s*or\s+|\s*,\s*|\s+or\s+/i).map(o => o.trim()).filter(Boolean);
    if (options.length < 2 || options.length > 4
        || options.some(o => o.split(/\s+/).length > 14 || /[\x00-\x1f]/.test(o))) return [];
    return options.map(o => {
      const text = o.charAt(0).toUpperCase() + o.slice(1);
      let label = text;
      if (label.length > 32) label = label.slice(0, 32).replace(/\s+\S*$/, '') + '…';
      return { label, reply: (tag ? tag + ' ' : '') + text + '.', derived: true };
    });
  }

  // Model-authored chips (NEXT_ACTION object or array), else chips derived
  // from a decision's alternatives. Always returns an array.
  function sessionQuickActions(state, next, assistantText) {
    state = state || {};
    const many = Array.isArray(state.next_actions) ? state.next_actions.map(sessionQuickAction) : [];
    if (many.length && many.every(Boolean)) return many.slice(0, 4);
    if (state.numbered_choice === true && state.next_actions?.length === 2
        && new Set(state.next_actions.map(action => action?.reply)).size === 2
        && state.next_actions.every(action => /^[12]$/.test(action?.reply)
          && typeof action.label === 'string' && action.label.length <= 48
          && /^\p{L}+(?: \p{L}+){0,6}$/u.test(action.label))) return state.next_actions;
    const one = sessionQuickAction(state.next_action);
    if (one) return [one];
    const step = String(next == null ? state.next_step_user || '' : next).trim();
    const decisions = decisionQuickActions(step);
    if (decisions.length) return decisions;
    // A review link may live in the final answer rather than its footer.
    // Require an explicit review request and one unambiguous labeled artifact;
    // opening it is navigation only, including when rollout still needs approval.
    if (/\breview\b/i.test(step) && step.length <= 600 && !/[\x00-\x1f]/.test(step)) {
      const prose = String(assistantText || '').split('<session-state>')[0];
      const links = [...prose.matchAll(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g)]
        .filter(m => /\b(?:PR|pull request|review)\b/i.test(m[1])
          && /^https?:\/\/[a-z0-9.-]+(?::\d{1,5})?(?:[/?#][^\s<>"`\\]*)?$/i.test(m[2]));
      const urls = [...new Set(links.map(m => m[2]))];
      if (urls.length === 1) return [{ label: 'Open review', href: urls[0], derived: true }];
    }
    // A delivered design can leave the build explicitly outside authorization.
    // Offer that choice to the user; rendering it never authorizes any work.
    const completion = state.completion;
    const planned = completion?.remaining_actions?.[0];
    if ((!step || /^(?:none|nothing|n\/a)[.!]?$/i.test(step))
        && completion?.valid === true && !completion.ready_to_close
        && completion.scope?.closure_met === false
        && /\bdesign[ -]only\b/i.test(completion.scope.evidence || '')
        && /\b(?:saved|delivered|recorded|verified)\b.*\bdesign\b|\bdesign\b.*\b(?:saved|delivered|recorded|verified)\b/i.test(state.did || '')
        && planned?.owner === 'agent' && planned.within_scope === false && planned.reversible === true
        && /\b(?:implement|implementation|build)\b/i.test(planned.text || '')) {
      return [{ label: 'Build', derived: true, build: true,
        reply: 'Build the implementation described in the saved design. Recheck the design and repository state first, then implement and verify locally. Inspect any unresolved delivery receipt before starting; do not resend an unknown delivery. This authorizes implementation only, not paid calls, spending, launch, deployment, or customer contact.' }];
    }
    return [];
  }


  // Chip labels bind their words with no-break spaces: a nowrap flex button
  // rendered "Yes do it" as "Yesdoit" for the owner (CCC-382).
  function ssbChipLabel(label) {
    return escapeHtml(String(label).replace(/ /g, '\u00a0'));
  }

  // CCC-1218: turn a "Needs you" ask into buttons. Built from what agents
  // actually write there: decide/confirm/tell/answer (-> Reply), reload or
  // hard-refresh CCC (-> Reload), a CCC launchctl kickstart (-> Restart CCC),
  // other `backticked` shell commands (-> Run in a visible terminal + Copy),
  // and URLs / file paths (-> Open).
  // CCC-1232: a quoted reply the agent asked for (Say "ship now" or "hold")
  // becomes a one-tap chip that sends that exact text.
  const NEEDS_YOU_SAY_RE = /\b(?:say|type|reply(?: with)?|respond(?: with)?|answer(?: with)?|send(?: me)?|tell me)\s*:?\s+((?:["\u201c'\u2018`][^"\u201d'\u2019`\n]{1,40}["\u201d'\u2019`](?:\s*(?:,|\/|or|and)\s*)?)+)/gi;

  function needsYouSayPhrases(src) {
    const out = [];
    let m;
    NEEDS_YOU_SAY_RE.lastIndex = 0;
    while ((m = NEEDS_YOU_SAY_RE.exec(src))) {
      const quoted = m[1].match(/["\u201c'\u2018`][^"\u201d'\u2019`\n]{1,40}["\u201d'\u2019`]/g) || [];
      for (const q of quoted) {
        const phrase = q.slice(1, -1).trim();
        if (phrase && !NEEDS_YOU_CMD_RE.test(phrase + ' ')) out.push(phrase);
      }
    }
    return out;
  }

  const NEEDS_YOU_CMD_RE = /^(?:sudo\s+)?(?:\.\/|~\/|launchctl|brew|git|gh|npm|npx|pnpm|yarn|bun|node|python3?|pip3?|uv|wt|ccc|curl|make|open|bash|sh|zsh|docker|kubectl|vercel|supabase|wrangler|cargo|go|ssh|trash|claude|codex|redditctl|ffmpeg|pytest)\s/;

  const NEEDS_YOU_REPLY_RE = /^(?:\(?\d+\)?\s*)?(?:decide|confirm|tell|say|answer|pick|choose|approve|reply|give|let me know|accept|send (?:me|your)|either)\b/i;

  function needsYouActions(text) {
    const actions = [];
    const seen = new Set();
    const add = (a) => {
      const key = a.act + '\u0000' + a.value;
      if (seen.has(key) || actions.length >= 6) return;
      seen.add(key);
      actions.push(a);
    };
    const src = String(text || '');
    for (const phrase of needsYouSayPhrases(src)) {
      add({ act: 'say', label: phrase, value: phrase, title: 'Send "' + phrase + '"' });
    }
    const codeSpans = [];
    src.replace(/`([^`\n]+)`/g, (m, inner) => { codeSpans.push(inner.trim()); return m; });
    for (const code of codeSpans) {
      if (/launchctl\s+kickstart\b[^`]*com\.github\.claude-command-center/.test(code)) {
        add({ act: 'restart', label: 'Restart CCC', value: 'all', title: 'Restart the dashboard and the worker, then reload' });
      } else if (NEEDS_YOU_CMD_RE.test(code + ' ')) {
        add({ act: 'run', label: 'Run', value: code, title: 'Run in a terminal: ' + code });
        add({ act: 'copy', label: 'Copy', value: code, title: 'Copy: ' + code });
      }
    }
    const urls = src.match(/https?:\/\/[^\s<>"'`)\]]+/g) || [];
    for (let u of urls) {
      u = u.replace(/[.,;:!?]+$/, '');
      let host = u;
      try { host = new URL(u).host; } catch (_) {}
      add({ act: 'url', label: 'Open ' + host, value: u, title: u });
    }
    const paths = src.match(/(?:^|[\s(`])((?:~\/|\/(?:Users|Volumes|Applications|private|tmp|opt|etc)\/)[^\s`'"<>)]+)/g) || [];
    for (let p of paths) {
      p = p.replace(/^[\s(`]/, '').replace(/[.,;:!?]+$/, '');
      const base = p.replace(/\/+$/, '').split('/').pop() || p;
      add({ act: 'path', label: 'Open ' + base, value: p, title: p });
    }
    if (!actions.some(a => a.act === 'restart')
        && /\b(?:hard[- ]?)?(?:reload|refresh)\b/i.test(src)
        && /\b(?:CCC|dashboard|page|browser|tab|UI)\b|^\s*(?:hard[- ]?)?(?:reload|refresh)\b/i.test(src)) {
      add({ act: 'reload', label: 'Reload', value: '1', title: 'Reload CCC' });
    }
    if (NEEDS_YOU_REPLY_RE.test(src.trim()) || /\?\s*$/.test(src.trim())) {
      add({ act: 'reply', label: 'Reply', value: '1', title: 'Answer in the composer' });
    }
    return actions;
  }

  function needsYouActionsHtml(text) {
    return needsYouActions(text).map(a => {
      if (a.act === 'url') {
        return '<a class="ssb-action" href="' + escapeAttr(a.value) + '" target="_blank" rel="noopener" title="' + escapeAttr(a.title) + '">' + escapeHtml(a.label) + '</a>';
      }
      if (a.act === 'path') {
        return '<a role="button" tabindex="0" class="ssb-action path-link" data-path="' + escapeAttr(a.value) + '" title="' + escapeAttr(a.title) + '">' + escapeHtml(a.label) + '</a>';
      }
      return '<button type="button" class="ssb-action ssb-act-' + a.act + '" data-ssb-act="' + a.act + '" data-ssb-value="' + escapeAttr(a.value) + '" title="' + escapeAttr(a.title) + '">' + escapeHtml(a.label) + '</button>';
    }).join('');
  }


  // Minimal markdown renderer for assistant text — tables, inline code, bold, headers
  function renderSessionStateBlock(body) {
    // Keep the machine-readable three-field footer, but render it as a
    // decision aid: lead with the user's action and keep only the reason.
    // DID becomes the primary line only when there is no user action.
    const lines = body.split('\n').map(s => s.trim()).filter(Boolean);
    const fields = {};
    const otherRows = [];
    const knownKeys = new Set(['DID', 'INSIGHT', 'NEXT_STEP_USER', 'NEXT_ACTION', 'COMPLETION']);
    for (const ln of lines) {
      // Skip the HTML comment markers that hide the machine JSON lines
      // (NEXT_ACTION/COMPLETION) in markdown-rendering terminal clients.
      if (ln === '<!--' || ln === '-->') continue;
      const m = ln.match(/^(?:<!--\s*)?([A-Z_]+):\s*(.*?)(?:\s*-->)?$/);
      if (m && knownKeys.has(m[1])) {
        fields[m[1]] = m[2].trim();
      } else if (m) {
        otherRows.push('<div class="ssb-row ssb-other"><span class="ssb-key">' + escapeHtml(m[1].replace(/_/g, ' ').toLowerCase()) + '</span>' + escapeHtml(m[2]) + '</div>');
      } else {
        otherRows.push('<div class="ssb-row ssb-other">' + escapeHtml(ln) + '</div>');
      }
    }

    const rows = [];
    let assessment;
    try { assessment = JSON.parse(fields.COMPLETION || 'null'); } catch (_) {}
    const { scope, validScope, openTopic, valid, reasons, axes } = sessionCompletionDetails(assessment);
    const verdict = !valid ? 'Unassessed · invalid assessment'
      : reasons.length ? (openTopic ? 'Open · ' : 'Next · ') + reasons.join('; ') : 'Close criteria met';

    let nextStep = fields.NEXT_STEP_USER || '';
    if (/^(?:none|nothing|no action(?: is)? needed|n\/a|not applicable)?[.!]?(?:\s+[—–-]\s+.*)?$/i.test(nextStep)
        && assessment && assessment.decision_dependency
        && typeof assessment.decision_dependency.score === 'number' && assessment.decision_dependency.score > 0
        && typeof assessment.decision_dependency.evidence === 'string' && assessment.decision_dependency.evidence.trim()) {
      nextStep = 'Decision required: ' + assessment.decision_dependency.evidence.trim();
    }
    const hasUserAction = !!nextStep && !/^(?:none|nothing|no action(?: is)? needed|n\/a|not applicable)[.!]?(?:\s+[—–-]\s+.*)?$/i.test(nextStep);
    if (hasUserAction) {
      let authored = null;
      try { authored = JSON.parse(fields.NEXT_ACTION || 'null'); } catch (_) {}
      const chips = sessionQuickActions(Array.isArray(authored) ? { next_actions: authored } : { next_action: authored }, nextStep);
      const actions = chips.length
        ? chips.map(action => '<button type="button" class="ssb-action ssb-act-say" data-ssb-act="say" data-ssb-value="' + escapeAttr(action.reply)
          + '" title="' + escapeAttr('Send to this session: ' + action.reply) + '">' + ssbChipLabel(action.label) + '</button>').join(' ')
        : needsYouActionsHtml(nextStep);
      rows.push('<div class="ssb-row ssb-primary ssb-next"><span class="ssb-key">Needs you</span>' + renderInline(nextStep) + (actions ? ' <span class="ssb-actions">' + actions + '</span>' : '') + '</div>');
    } else if (fields.DID) {
      rows.push('<div class="ssb-row ssb-primary ssb-done"><span class="ssb-key">' + (fields.COMPLETION && (!valid || reasons.length) ? 'Update' : 'Done') + '</span>' + escapeHtml(fields.DID) + '</div>');
    }
    if (!fields.COMPLETION) {
      if (!hasUserAction) rows.push('<div class="ssb-row ssb-no-action"><span class="ssb-key">Needs you</span>' + (nextStep ? 'None' : 'Not specified') + '</div>');
      if (fields.INSIGHT) rows.push('<div class="ssb-row ssb-reason"><span class="ssb-key">Why</span>' + escapeHtml(fields.INSIGHT) + '</div>');
    } else {
      const planned = valid && assessment.remaining_actions[0];
      if (planned && (!hasUserAction || planned.owner !== 'user')) {
        const label = planned.owner === 'agent' ? 'Doing next' : planned.owner === 'user' ? 'Your next step'
          : planned.owner === 'external' ? 'Waiting on' : 'Next step';
        rows.push('<div class="ssb-row ssb-plan"><span class="ssb-key">' + label + '</span>' + escapeHtml(planned.text) + '</div>');
      } else if (!hasUserAction && (!valid || reasons.length)) {
        // An open card with no plan is a dead end; offer one fixed reply that
        // asks the session for its step instead of guessing one for it.
        const ask = 'Your assessment is open (' + (valid ? reasons.join('; ') : 'invalid assessment')
          + ') but names no next step. If it is authorized agent work, do it now and verify it.'
          + ' Otherwise state it as the first remaining action, with an owner and a success check.';
        rows.push('<div class="ssb-row ssb-plan"><span class="ssb-key">Next step</span>Not stated. <span class="ssb-actions">'
          + '<button type="button" class="ssb-action ssb-act-say" data-ssb-act="say" data-ssb-value="' + escapeAttr(ask)
          + '" title="' + escapeAttr('Send to this session: ' + ask) + '">Ask for next step</button></span></div>');
      }
    }
    if (fields.COMPLETION) {
      let evidence = fields.INSIGHT ? '<div class="ssb-row ssb-reason"><span class="ssb-key">Why</span>' + escapeHtml(fields.INSIGHT) + '</div>' : '';
      if (assessment && assessment.version === 1) {
        if (validScope && scope) evidence += '<div class="ssb-row ssb-scope"><span class="ssb-key">Topic</span>'
          + escapeHtml(scope.goal) + '</div><div class="ssb-row"><span class="ssb-key">Done when</span>'
          + escapeHtml(scope.done_when) + '</div><div class="ssb-row"><span class="ssb-key">Topic evidence</span>'
          + escapeHtml(scope.evidence) + '</div>';
        for (const [key, label] of [['resolution', 'Resolution'], ['verification', 'Verification'],
            ['delivery', 'Delivery'], ['decision_dependency', 'Decision dependency']]) {
          const axis = assessment[key];
          if (!axis || typeof axis.score !== 'number' || !Number.isFinite(axis.score)
              || axis.score < 0 || axis.score > 1 || typeof axis.evidence !== 'string' || !axis.evidence.trim()) continue;
          const score = key === 'delivery' && axis.applicable === false ? 'N/A' : String(axis.score);
          evidence += '<div class="ssb-row"><span class="ssb-key">' + label + ' <span class="ssb-score">' + score + '</span>'
            + '</span>' + escapeHtml(axis.evidence) + '</div>';
        }
        if (Array.isArray(assessment.caveats)) for (const item of assessment.caveats) {
          if (item && typeof item.text === 'string') evidence += '<div class="ssb-row"><span class="ssb-key">'
            + (item.blocking ? 'Blocking caveat' : 'Caveat') + '</span>' + escapeHtml(item.text) + '</div>';
        }
        if (Array.isArray(assessment.remaining_actions)) for (const item of assessment.remaining_actions) {
          if (item && typeof item.text === 'string') evidence += '<div class="ssb-row"><span class="ssb-key">Remaining</span>'
            + escapeHtml(item.text) + ' · confidence ' + escapeHtml(String(item.confidence))
            + ' · downside ' + escapeHtml(String(item.downside)) + '</div>';
        }
      }
      const status = !valid ? 'Unassessed' : reasons.length ? 'Open' : 'Close criteria met';
      rows.push('<div class="ssb-row ssb-status"' + (valid && !reasons.length ? ' data-assessment-ready="true"' : '')
        + '><span class="ssb-key">Status</span>' + status + '</div>');
      const scores = valid ? '(' + axes.map(k => String(assessment[k].score)).join(', ') + ')' : '(unassessed)';
      rows.push('<details class="ssb-completion"><summary title="Expand for the topic goal, completion gates, scores, and evidence.">Details</summary>'
        + '<div class="ssb-row ssb-verdict"><span class="ssb-key">Assessment</span>' + escapeHtml(verdict) + '</div>'
        + '<div class="ssb-row ssb-scores"><span class="ssb-key">Scores</span>' + escapeHtml(scores) + '</div>'
        + (evidence || '<div class="ssb-row">Assessment unavailable or invalid.</div>') + '</details>');
    }
    return '<div class="session-state-block">' + rows.concat(otherRows).join('') + '</div>';
  }


  function normalizeTaskNotificationField(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }


  function taskNotificationTagText(root, tagName) {
    if (!root) return '';
    const el = root.getElementsByTagName(tagName)[0];
    return el ? normalizeTaskNotificationField(el.textContent || '') : '';
  }


  function parseTaskNotificationBlock(text) {
    const src = String(text || '').trim();
    if (!/^<task-notification\b/i.test(src) || !/<\/task-notification>\s*$/i.test(src)) return null;
    let parsed = { taskId: '', summary: '', event: '' };

    if (typeof DOMParser !== 'undefined') {
      try {
        const doc = new DOMParser().parseFromString(src, 'application/xml');
        const root = doc.getElementsByTagName('task-notification')[0];
        const hasParserError = doc.getElementsByTagName('parsererror').length > 0;
        if (root && !hasParserError) {
          parsed = {
            taskId: taskNotificationTagText(root, 'task-id'),
            summary: taskNotificationTagText(root, 'summary'),
            event: taskNotificationTagText(root, 'event'),
          };
        }
      } catch (_) {}
    }

    if (!parsed.taskId && !parsed.summary && !parsed.event) {
      const tagText = (tag) => {
        const re = new RegExp('<' + tag + '\\b[^>]*>([\\s\\S]*?)<\\/' + tag + '>', 'i');
        const m = src.match(re);
        return m ? normalizeTaskNotificationField(m[1]) : '';
      };
      parsed = {
        taskId: tagText('task-id'),
        summary: tagText('summary'),
        event: tagText('event'),
      };
    }

    if (!parsed.taskId && !parsed.summary && !parsed.event) return null;
    parsed.event = parsed.event.replace(/^\[([\s\S]*)\]$/, '$1').trim();
    return parsed;
  }


  function splitTaskNotificationSummary(summary) {
    const s = normalizeTaskNotificationField(summary);
    const m = s.match(/^([A-Za-z][A-Za-z0-9 _-]{1,32}):\s+([\s\S]+)$/);
    if (!m) return { kind: '', headline: s };
    return { kind: m[1].trim(), headline: m[2].trim() };
  }


  function taskNotificationPlainText(notification) {
    const n = notification || {};
    const lines = [];
    if (n.summary) lines.push(n.summary);
    if (n.event) lines.push(n.event);
    if (n.taskId) lines.push('Task ' + n.taskId);
    return lines.join('\n');
  }


  function renderTaskNotificationBlock(notification, rawText, asUserMessage) {
    const n = notification || {};
    const parts = splitTaskNotificationSummary(n.summary || '');
    const rawValue = asUserMessage ? taskNotificationPlainText(n) : rawText;
    const rawAttr = rawValue ? ' data-raw-text="' + escapeAttr(rawValue) + '"' : '';
    const cls = asUserMessage ? 'user-msg task-notification-card' : 'task-notification-card';
    // Full detail on hover — the one-line compact render hides the event/task-id
    // rows (see .task-notification-event CSS) to stay out of the reader's way.
    const tip = taskNotificationPlainText(n);
    const tipAttr = tip ? ' title="' + escapeAttr(tip) + '"' : '';
    let html = '<div class="' + cls + '"' + rawAttr + tipAttr + '>';
    html += '<div class="tn-kicker"><span>Task notification</span>'
      + (parts.kind ? '<span class="tn-kind">' + renderInline(parts.kind) + '</span>' : '')
      + '</div>';
    if (parts.headline) {
      html += '<div class="tn-summary">' + renderInline(parts.headline) + '</div>';
    }
    if (n.event) {
      html += '<div class="tn-event">' + renderInline(n.event) + '</div>';
    }
    if (n.taskId) {
      // Subagent transcripts live in <parent>/subagents/agent-<taskid>.jsonl;
      // the composite conv id "<parent>:agent-<taskid>" opens one read-only
      // in a popout through the normal conversation pipeline (CCC-112).
      html += '<div class="tn-meta"><span>Task <code>' + escapeHtml(n.taskId) + '</code></span>'
        + '<button type="button" class="tn-view-transcript" data-task-id="' + escapeAttr(n.taskId) + '"'
        + ' title="Open this agent\'s full transcript in a popout">↗ agent transcript</button></div>';
    }
    html += '</div>';
    return html;
  }


  // Renders a flat array of {indent, text} bullet items as a properly
  // nested <ul> tree based on leading-whitespace depth, so a bold "header"
  // bullet followed by indented sub-bullets (a common LLM output shape)
  // keeps its hierarchy instead of collapsing onto one flat list.
  function renderNestedBulletList(items, pos) {
    const baseIndent = items[pos.i].indent;
    let html = '<ul>';
    while (pos.i < items.length && items[pos.i].indent >= baseIndent) {
      if (items[pos.i].indent > baseIndent) break;
      html += '<li>' + renderInline(items[pos.i].text);
      pos.i++;
      if (pos.i < items.length && items[pos.i].indent > baseIndent) {
        html += renderNestedBulletList(items, pos);
      }
      html += '</li>';
    }
    html += '</ul>';
    return html;
  }


  // CCC-913: split a Devin CLI <summary> body into one <details> per
  // top-level markdown section (Overview / Key Details & Breadcrumbs /
  // Current State / …) instead of one giant collapse-everything block —
  // "Current state" ships open by default since that's the section people
  // actually want to see at a glance; the rest starts collapsed.
  function renderDevinSummaryBlock(body) {
    const lines = body.split('\n');
    const sections = [];
    let current = null;
    for (const line of lines) {
      const h = line.match(/^(#{1,6})\s+(.+)$/);
      if (h) {
        current = { title: h[2].trim(), lines: [] };
        sections.push(current);
      } else if (current) {
        current.lines.push(line);
      } else {
        if (!sections.length) sections.push({ title: '', lines: [] });
        sections[sections.length - 1].lines.push(line);
      }
    }
    const namedSections = sections.filter(s => s.title);
    if (namedSections.length < 2) {
      return '<details class="ctx-usage-details devin-summary-details"><summary>Summary</summary>'
        + renderMarkdown(body) + '</details>';
    }
    return namedSections.map(s => {
      const isCurrentState = /current\s+state/i.test(s.title);
      return '<details class="ctx-usage-details devin-summary-details"' + (isCurrentState ? ' open' : '') + '>'
        + '<summary>' + escapeHtml(s.title) + '</summary>'
        + renderMarkdown(s.lines.join('\n')) + '</details>';
    }).join('');
  }


  // CCC-403: a pasted multi-MB log in one assistant reply took the markdown
  // renderer 50-124s (5.2 MB, 12 events). Above the cap, render only the head
  // as markdown and tuck the rest into a collapsed plain-text <pre>.
  const MD_RENDER_MAX_CHARS = 40000;

  function renderMarkdown(text) {
    if (text.length > MD_RENDER_MAX_CHARS) {
      let cut = text.lastIndexOf('\n', MD_RENDER_MAX_CHARS);
      if (cut < MD_RENDER_MAX_CHARS / 2) cut = MD_RENDER_MAX_CHARS;
      const rest = text.slice(cut);
      // An odd fence count in the head would swallow the notice; close it.
      const head = text.slice(0, cut);
      const fenced = (head.match(/^\s*```/gm) || []).length % 2 ? '\n```' : '';
      return renderMarkdown(head + fenced)
        + '<details class="ctx-usage-details"><summary>Show remaining '
        + Math.round(rest.length / 1024).toLocaleString() + ' KB as plain text</summary>'
        + '<pre style="white-space:pre-wrap;word-break:break-word">' + escapeHtml(rest) + '</pre></details>';
    }
    const lines = text.split('\n');
    const out = [];
    let i = 0;
    // CCC-47: `/context` dumps a "## Context Usage" heading, the headline
    // "Tokens: X / 1m (Y%)" line, then two long tables (usage by category +
    // MCP tools). The user only cares about the headline number — collapse
    // every table that follows the heading into a <details> so the long
    // tables stop dominating the message.
    let inCtxReport = false;
    while (i < lines.length) {
      const line = lines[i];
      if (/^\s*<task-notification\b/i.test(line)) {
        const start = i;
        while (i < lines.length && !/^\s*<\/task-notification>\s*$/i.test(lines[i])) i++;
        if (i < lines.length) i++;
        const notification = parseTaskNotificationBlock(lines.slice(start, i).join('\n'));
        if (notification) {
          out.push(renderTaskNotificationBlock(notification, '', false));
          continue;
        }
        i = start;
      }
      // Session-state block emitted by the orchestration prompt:
      //   <session-state>
      //   DID: …
      //   INSIGHT: …
      //   NEXT_STEP_USER: …
      //   </session-state>
      // Render as a styled card instead of raw <session-state> tags + KEY:
      // pairs in the prose. Detect first so the block doesn't get partially
      // gobbled by the table or list logic below.
      if (/^\s*(?:<!--\s*)?<session-state>\s*$/.test(line)) {
        i++;
        const start = i;
        while (i < lines.length && !/^\s*<\/session-state>(?:\s*-->)?\s*$/.test(lines[i])) i++;
        const body = lines.slice(start, i).join('\n');
        if (i < lines.length) i++;  // skip closing tag
        out.push(renderSessionStateBlock(body));
        continue;
      }
      // CCC-874: Devin CLI sometimes wraps a whole reply body in a bare
      // <summary>...</summary> tag (no enclosing <details>). Rendered
      // literally that's a huge wall of text under a stray "<summary>"
      // line, so collapse it into a real <details> instead.
      if (/^\s*<summary>\s*$/i.test(line)) {
        i++;
        const start = i;
        while (i < lines.length && !/^\s*<\/summary>\s*$/i.test(lines[i])) i++;
        const body = lines.slice(start, i).join('\n');
        if (i < lines.length) i++;  // skip closing tag
        out.push(renderDevinSummaryBlock(body));
        continue;
      }
      // Fenced code block: ```lang\n...code...\n```
      // Runs BEFORE table/header detection so ``` inside code doesn't get
      // misinterpreted as markdown.
      const fence = line.match(/^\s*```\s*(\S*)\s*$/);
      if (fence) {
        const lang = fence[1] || '';
        i++;
        const start = i;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) i++;
        const code = lines.slice(start, i).join('\n');
        if (i < lines.length) i++;  // skip closing fence
        out.push(renderCodeBlock(code, lang));
        continue;
      }
      // Detect a table: header | sep | rows
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s\-:|]+\|\s*$/.test(lines[i + 1])) {
        const cells = (s) => s.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
        const header = cells(line);
        const rows = [];
        i += 2;
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
          rows.push(cells(lines[i]));
          i++;
        }
        let html = '<div class="md-table-wrap" tabindex="0" role="region" aria-label="Table">'
          + '<table class="md-table"><thead><tr>';
        for (const h of header) html += '<th>' + renderInline(h) + '</th>';
        html += '</tr></thead><tbody>';
        for (const r of rows) {
          html += '<tr>';
          for (const c of r) html += '<td>' + renderInline(c) + '</td>';
          html += '</tr>';
        }
        html += '</tbody></table></div>';
        if (inCtxReport) {
          // Collapsed by default; summary keeps the column names + row count
          // so the user knows what's tucked away without expanding.
          const summary = (header.join(' / ') || 'table') + ' · ' + rows.length + ' rows';
          out.push('<details class="ctx-usage-details"><summary>'
            + escapeHtml(summary) + '</summary>' + html + '</details>');
        } else {
          out.push(html);
        }
        continue;
      }
      // Headers
      const h = line.match(/^(#{1,6})\s+(.+)$/);
      if (h) {
        const level = h[1].length;
        if (/context\s+usage/i.test(h[2])) inCtxReport = true;
        out.push('<h' + level + ' class="md-h">' + renderInline(h[2]) + '</h' + level + '>');
        i++;
        continue;
      }
      // Pseudo-header heuristic: a line that is ENTIRELY `**…**` (bold from
      // first non-space to last) is the model's idiomatic "soft header" —
      // models trained on chat output frequently emit `**Section title**`
      // on its own line instead of `## Section title`. Render those as h3
      // so they get the same visual weight as a markdown header. Optionally
      // followed by a leading emoji + space *outside* the bold (e.g.
      // `📨 **Suggested send-out**`) — capture that prefix too.
      const pseudo = line.match(/^\s*((?:[^\sA-Za-z0-9*_]+(?:\s+))?)\*\*([^*]+?)\*\*\s*:?\s*$/);
      if (pseudo) {
        const prefix = pseudo[1] || '';
        const inner = pseudo[2];
        out.push('<h3 class="md-h md-h-pseudo">' + escapeHtml(prefix) + renderInline(inner) + '</h3>');
        i++;
        continue;
      }
      // Blockquote: consecutive lines starting with `>`. Empty `>` lines
      // are rendered as paragraph breaks inside the quote so multi-paragraph
      // quotes (suggested-message blocks etc.) read as proper structure.
      if (/^\s*>\s?/.test(line)) {
        const inner = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          inner.push(lines[i].replace(/^\s*>\s?/, ''));
          i++;
        }
        let html = '<blockquote>';
        let buf = [];
        const flushPara = () => {
          if (!buf.length) return;
          html += '<p>' + buf.map(l => renderInline(l)).join('<br>') + '</p>';
          buf = [];
        };
        for (const l of inner) {
          if (l.trim() === '') flushPara();
          else buf.push(l);
        }
        flushPara();
        html += '</blockquote>';
        out.push(html);
        continue;
      }
      // Numbered list: consecutive `N. text` lines.
      if (/^\s*\d+\.\s+/.test(line)) {
        const startMatch = line.match(/^\s*(\d+)\.\s+/);
        const startVal = startMatch ? parseInt(startMatch[1], 10) : 1;
        let html = startVal !== 1 ? '<ol start="' + startVal + '">' : '<ol>';
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          const item = lines[i].replace(/^\s*\d+\.\s+/, '');
          html += '<li>' + renderInline(item) + '</li>';
          i++;
        }
        html += '</ol>';
        out.push(html);
        continue;
      }
      // Bulleted list: consecutive `- text` or `* text` lines. The `\s+`
      // after the marker is what distinguishes a bullet from inline `*bold*`
      // (bold has no space) or a `---` rule (no space, multiple chars).
      if (/^\s*[-*]\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          const m = lines[i].match(/^(\s*)[-*]\s+(.*)$/);
          items.push({ indent: m[1].length, text: m[2] });
          i++;
        }
        out.push(renderNestedBulletList(items, { i: 0 }));
        continue;
      }
      // Regular text line (preserve as-is with <br>)
      if (line.trim() === '') {
        // Collapse consecutive blank lines: markdown semantics treat them as
        // a single paragraph break, but the old "always push <br>" rule
        // stacked one <br> per blank line — three blank lines became three
        // visual line-heights of empty space, which is what made the group-
        // chat transcript look broken when the orchestrator dumped a wad of
        // "system: removed X" entries with blank gaps between them.
        if (out.length && out[out.length - 1] !== '<br>') out.push('<br>');
      } else {
        out.push('<div>' + renderInline(line) + '</div>');
      }
      i++;
    }
    // Trim leading/trailing blank-line <br>s so a body that starts or ends
    // with whitespace doesn't render the gap. Inner runs are already
    // collapsed to a single <br> by the loop above.
    while (out.length && out[0] === '<br>') out.shift();
    while (out.length && out[out.length - 1] === '<br>') out.pop();
    // RTL: tag every block-level element with dir="auto" so each
    // paragraph picks its own direction from its first strong
    // directional character. `unicode-bidi: plaintext` in CSS handles
    // the bidi algorithm but doesn't flip text-align — only an actual
    // `dir` attribute does, and `dir="auto"` makes the browser detect
    // per element instead of inheriting from <html>. Skip tags we
    // never want to flip (pre/code stay LTR via CSS). A MutationObserver
    // below also tags blocks post-insertion as a belt-and-suspenders for
    // any markdown that bypasses this regex.
    return out.join('').replace(
      /<(p|li|blockquote|h[1-6]|td|th|dt|dd)(\s|>)/g,
      '<$1 dir="auto"$2'
    );
  }


  // ── Fenced-code-block rendering + tokenizer ─────────────────────────────
  // Basic language-aware syntax highlighting for the handful of langs that
  // actually show up in CCC conversation text. Not Prism-grade; deliberate
  // trade-off to keep the single-file "no-bundler" stance. Order of patterns
  // matters: comments/strings win over keywords, otherwise `// return`
  // would highlight `return` inside a comment.
  const _CB_LANG_ALIAS = {
    javascript: 'ts', js: 'ts', typescript: 'ts', tsx: 'ts', jsx: 'ts',
    python: 'py', py3: 'py',
    sh: 'bash', shell: 'bash', zsh: 'bash',
  };

  const _CB_LANG_PATTERNS = {
    ts: [
      { re: /\/\/[^\n]*|\/\*[\s\S]*?\*\//, cls: 'tok-comment' },
      { re: /`(?:\\.|[^`\\])*`|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/, cls: 'tok-string' },
      { re: /\b(?:const|let|var|function|return|if|else|for|while|do|switch|case|default|break|continue|class|extends|new|this|super|import|export|from|as|async|await|try|catch|finally|throw|typeof|instanceof|in|of|void|null|undefined|true|false|yield|enum|interface|type|public|private|protected|readonly|static|abstract|implements|namespace|declare|module|keyof|satisfies)\b/, cls: 'tok-keyword' },
      { re: /\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/, cls: 'tok-number' },
      { re: /\b[A-Z][A-Za-z0-9_]*\b/, cls: 'tok-type' },
      { re: /\b[a-zA-Z_][a-zA-Z0-9_]*(?=\s*\()/, cls: 'tok-function' },
    ],
    py: [
      { re: /#[^\n]*|'''[\s\S]*?'''|"""[\s\S]*?"""/, cls: 'tok-comment' },
      { re: /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/, cls: 'tok-string' },
      { re: /\b(?:def|return|if|elif|else|for|while|break|continue|class|import|from|as|async|await|try|except|finally|raise|with|lambda|yield|global|nonlocal|pass|in|is|not|and|or|True|False|None|self|cls)\b/, cls: 'tok-keyword' },
      { re: /\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/, cls: 'tok-number' },
      { re: /\b[a-zA-Z_][a-zA-Z0-9_]*(?=\s*\()/, cls: 'tok-function' },
    ],
    bash: [
      { re: /#[^\n]*/, cls: 'tok-comment' },
      { re: /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/, cls: 'tok-string' },
      { re: /\$\{[^}]+\}|\$[a-zA-Z_][a-zA-Z0-9_]*|\$[0-9@*#?!-]/, cls: 'tok-variable' },
      { re: /\b(?:if|then|else|elif|fi|for|do|done|while|until|case|esac|function|in|return|break|continue|export|local|readonly|declare|source|exit|trap|set|unset|eval|exec|shift)\b/, cls: 'tok-keyword' },
      { re: /\b\d+\b/, cls: 'tok-number' },
    ],
    json: [
      { re: /"(?:\\.|[^"\\])*"(?=\s*:)/, cls: 'tok-key' },
      { re: /"(?:\\.|[^"\\])*"/, cls: 'tok-string' },
      { re: /\b(?:true|false|null)\b/, cls: 'tok-keyword' },
      { re: /-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/, cls: 'tok-number' },
    ],
  };

  // Wrap http(s) URLs inside already-HTML-escaped output in anchor tags.
  // Used as a post-pass on highlightCode output and on inline code that
  // contains a URL alongside other text. Only matches URLs that aren't
  // already inside an existing <a …> tag (the prefix lookbehind via a
  // negative pattern is approximated by a check that we're not currently
  // inside an open anchor — easier just to require URL chars not contain
  // < or >, which are the anchor delimiters).
  function _linkifyEscapedUrls(html) {
    if (!html || html.indexOf('http') === -1) return html;
    return html.replace(
      /(https?:\/\/[^\s<>"'`]+)/g,
      '<a href="$1" target="_blank" rel="noopener">$1</a>'
    );
  }


  const INLINE_CODE_PATH_RE = /^(https?:\/\/\S+|~\/[\w./@#:\-+]*|\/[\w./@#:\-+]*|[\w./@#:\-+]+\.(md|ts|tsx|js|jsx|py|json|yaml|yml|css|html|sql|prisma|sh|png|jpe?g|gif|webp|svg|bmp|pdf|txt|csv|log|toml|xml|swift|rb|go|rs|java|c|h|cpp|mp4|mov|webm))$/;

  function _isPlaceholderPathToken(token) {
    return String(token || '').indexOf('...') !== -1;
  }

  function _isInternalApiPathToken(token) {
    const clean = normalizeMarkdownLinkTarget(token).split(/[?#]/)[0];
    return clean === '/api' || clean.startsWith('/api/');
  }

  const INLINE_CODE_WEB_HOST_RE = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|org|net|edu|gov|io|ai|co|app|dev|xyz|me|us|uk|ca|de|fr|au|jp|in|br|nl|se|ch|es|it|info|biz|tech|cloud|site|online|store|page|vercel\.app|pages\.dev|netlify\.app|fly\.dev|railway\.app|onrender\.com|ts\.net)(?::\d{1,5})?(?:\/[^\s<>"'`]*)?$/i;
  const INLINE_CODE_LOCALHOST_RE = /^(?:localhost|127\.0\.0\.1):\d{1,5}(?:\/[^\s<>"'`]*)?$/i;

  function _inlineCodeWebUrl(token) {
    const target = normalizeMarkdownLinkTarget(token);
    if (!target || _isPlaceholderPathToken(target) || target.indexOf('@') !== -1) return '';
    if (/^https?:\/\//i.test(target)) return '';
    if (INLINE_CODE_LOCALHOST_RE.test(target)) return 'http://' + target;
    if (INLINE_CODE_WEB_HOST_RE.test(target)) return 'https://' + target;
    return '';
  }

  function _shouldLinkifyInlineCodePath(token) {
    const target = normalizeMarkdownLinkTarget(token);
    if (!INLINE_CODE_PATH_RE.test(target)) return false;
    if (_isPlaceholderPathToken(target)) return false;
    if (_isInternalApiPathToken(target)) return false;
    // Don't linkify a bare filename with no directory separator (e.g.
    // `prospects-austin.csv`): it can't be resolved reliably — the name may be
    // mentioned in prose while the real file lives in another repo this session
    // never opened, so the click dead-ends on "file not found" (CCC-134). This
    // matches the bare-prose rule, which also requires a separator. URLs are
    // already accepted above; the ~/… and /… branches always contain a slash.
    if (!/^https?:\/\//i.test(target) && target.indexOf('/') === -1) return false;
    return true;
  }


  function highlightCode(code, lang) {
    const key = _CB_LANG_ALIAS[String(lang || '').toLowerCase()] || String(lang || '').toLowerCase();
    const patterns = _CB_LANG_PATTERNS[key];
    if (!patterns) return escapeHtml(code);
    let out = '';
    let idx = 0;
    while (idx < code.length) {
      let hit = null;
      let hitCls = '';
      for (const { re, cls } of patterns) {
        // Sticky regex match at current position only (no scanning ahead).
        const sticky = new RegExp(re.source, 'y');
        sticky.lastIndex = idx;
        const m = sticky.exec(code);
        if (m && m.index === idx) { hit = m[0]; hitCls = cls; break; }
      }
      if (hit) {
        out += '<span class="' + hitCls + '">' + escapeHtml(hit) + '</span>';
        idx += hit.length;
      } else {
        // Emit one char as-is; next iteration tries patterns from the next
        // position. Cheap; the patterns that matter are regex-fast and the
        // fall-through only runs on whitespace/punctuation.
        out += escapeHtml(code.charAt(idx));
        idx += 1;
      }
    }
    return out;
  }

  function renderCodeBlock(code, lang) {
    // Mermaid diagrams render as SVG via the lazy-loaded mermaid lib.
    // We emit a wrapper carrying the raw source so the post-insert
    // observer (see _renderMermaidBlocks) can find and convert it.
    // The <pre> inside is the offline/error fallback — if mermaid never
    // loads, the user still sees the diagram source as a code block.
    if ((lang || '').toLowerCase() === 'mermaid') {
      return (
        '<div class="mermaid-block" data-mermaid-pending>' +
          '<div class="cb-head">' +
            '<span class="cb-lang">mermaid</span>' +
            '<button class="cb-copy" type="button" title="Copy">Copy</button>' +
          '</div>' +
          '<pre class="mermaid-source">' + escapeHtml(code) + '</pre>' +
        '</div>'
      );
    }
    const langLabel = lang
      ? '<span class="cb-lang">' + escapeHtml(lang) + '</span>'
      : '<span class="cb-lang cb-lang-plain">code</span>';
    return (
      '<div class="cb-wrap">' +
        '<div class="cb-head">' + langLabel +
          '<button class="cb-copy" type="button" title="Copy">Copy</button>' +
        '</div>' +
        '<pre class="cb"><code>' + _linkifyEscapedUrls(highlightCode(code, lang)) + '</code></pre>' +
      '</div>'
    );
  }


  function renderInline(s) {
    // Escape HTML first
    s = escapeHtml(s);
    // Expand pasted images before other markdown formatting so their paths
    // don't get wrapped in backticks or a-tags by later rules.
    s = linkifyPastedImages(s);
    // Inline code `x` (also make paths and web domains inside code clickable)
    s = s.replace(/`([^`]+)`/g, (m, inner) => {
      if (_shouldLinkifyInlineCodePath(inner)) {
        return '<code class="md-code">' + linkifyPath(inner) + '</code>';
      }
      const webUrl = _inlineCodeWebUrl(inner);
      if (webUrl) {
        return '<code class="md-code"><a href="' + escapeAttr(webUrl) + '" target="_blank" rel="noopener" class="web-preview-link">' + inner + '</a></code>';
      }
      // Mixed inline content (e.g. `see http://… for details`) — link
      // any http(s) URLs inside. `inner` is the captured group from the
      // already-escaped `s`, so it's HTML-safe; we just wrap URL spans.
      if (inner.indexOf('http') !== -1) {
        return '<code class="md-code">' + _linkifyEscapedUrls(inner) + '</code>';
      }
      return '<code class="md-code">' + inner + '</code>';
    });
    // Bold **x**. Keep the following prose gap in its own non-collapsing
    // inline span: at a bold/plain-text boundary WebKit can visually swallow
    // the ordinary whitespace, making `**repo** so` read as "reposo".
    s = s.replace(/\*\*([^*]+)\*\*([ \t]+)(?=\S)/g, (m, inner, gap) =>
      '<strong>' + inner + '</strong><span class="md-bold-gap">'
        + gap.replace(/[ \t]/g, '&nbsp;') + '</span>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Images ![alt](url)
    s = s.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m, alt, url) => {
      const target = normalizeMarkdownLinkTarget(url);
      if (isUnavailableMarkdownImageTarget(target)) {
        return '<span class="unavailable-image" role="img" title="The source image was not saved in this transcript">'
          + escapeHtml(alt || 'Image') + ' · Image attachment unavailable in this transcript</span>';
      }
      return '<img src="' + escapeAttr(target) + '" alt="' + escapeHtml(alt) + '" class="msg-image" loading="lazy">';
    });
    // Markdown links [text](url)
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, text, url) => {
      const target = normalizeMarkdownLinkTarget(url);
      if (/^https?:\/\//i.test(target)) {
        return '<a href="' + escapeAttr(target) + '" target="_blank" rel="noopener">' + text + '</a>';
      }
      return '<a role="button" tabindex="0" class="path-link" data-path="' + escapeAttr(target) + '">' + text + '</a>';
    });
    // A bold label's space may already be wrapped in md-bold-gap. Treat
    // that wrapper as whitespace while skipping existing links and tags.
    s = s.replace(/<a\b[^>]*>[\s\S]*?<\/a>|(^|[\s(]|<span class="md-bold-gap">(?:&nbsp;)+<\/span>)(https?:\/\/[^\s<>"')]+)|<[^>]+>/g,
      (m, pre, url) => url ? pre + '<a href="' + url + '" target="_blank" rel="noopener">' + url + '</a>' : m);
    // Bare file paths (relative like docs/foo/bar.md, absolute /Users/..., or ~/...)
    s = s.replace(/(^|[\s(])((?:~\/|\/|(?:[\w.\-]+\/)+)[\w.\-/]+\.(?:md|ts|tsx|js|jsx|py|json|yaml|yml|css|html|sql|prisma|sh))\b/g,
      (m, pre, p) => pre + '<a role="button" tabindex="0" class="path-link" data-path="' + p + '">' + p + '</a>');
    return linkifySessionRefs(linkifyWatchtowerTicketRefs(linkifyCodexInlineVisuals(s)));
  }


  // Only known session identities become navigation; arbitrary UUIDs may be
  // request ids or other data. Preserve explicit links and HTML attributes.
  function linkifySessionRefs(html) {
    const protectedParts = [];
    const safe = String(html || '').replace(/<a\b[^>]*>[\s\S]*?<\/a>|<pre\b[^>]*>[\s\S]*?<\/pre>|<[^>]+>/gi, part => {
      const token = '\u0000SESSIONHTML' + protectedParts.length + '\u0000';
      protectedParts.push(part);
      return token;
    });
    const linked = safe.replace(/\b(?:(?:session_|devin-)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|devincli-[a-z0-9]+(?:-[a-z0-9]+)+)\b/gi, (sid, offset) => {
      // File paths, longer ids and fragments of URLs are not session refs.
      if (/[\w/.-]/.test(safe[offset - 1] || '') || /[\w/-]/.test(safe[offset + sid.length] || '')) return sid;
      const row = adapters.findSession(sid);
      if (!row) return sid;
      const name = sidebarRowDisplayTitle(row.display_name || row.ai_title || cleanIssuePrompt(row.first_message || '')) || shortSessionId(sid);
      const child = !!(row.parent_session_id || row.hermes_parent_session_id);
      return '<a class="session-ref-chip" href="?session=' + encodeURIComponent(sid) + '"'
        + ' data-session-ref="' + escapeAttr(sid) + '" title="' + escapeAttr('Open session: ' + name + '\n' + sid) + '">'
        + escapeHtml((child ? 'Child · ' : '') + truncate(name, 70)) + '</a>';
    });
    return linked.replace(/\u0000SESSIONHTML(\d+)\u0000/g, (match, index) => protectedParts[Number(index)] || match);
  }


  // A ticket ref is durable navigation, not merely a status label. Keep the
  // conversion after normal markdown links so an author's explicit link
  // remains authoritative instead of becoming a nested anchor. Protect tags
  // too: a ticket ref in an image URL or alt text must not corrupt its HTML.
  const WATCHTOWER_TICKET_REF_RE = /\b([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)-(\d+)\b/g;

  function linkifyWatchtowerTicketRefs(html) {
    const anchors = [];
    const protectedHtml = String(html || '').replace(/<a\b[^>]*>[\s\S]*?<\/a>|<[^>]+>/gi, match => {
      const token = '\u0000WTANCHOR' + anchors.length + '\u0000';
      anchors.push(match);
      return token;
    });
    const linked = protectedHtml.replace(WATCHTOWER_TICKET_REF_RE, (match, queue) =>
      '<a role="button" tabindex="0" class="watchtower-ticket-link"'
        + ' data-watchtower-ticket="' + escapeAttr(match) + '"'
        + ' data-watchtower-queue="' + escapeAttr(queue) + '">' + match + '</a>');
    return linked.replace(/\u0000WTANCHOR(\d+)\u0000/g, (match, index) => anchors[Number(index)] || match);
  }


  function linkifyPath(p) {
    const target = normalizeMarkdownLinkTarget(p);
    if (/^https?:\/\//i.test(target)) {
      return '<a href="' + escapeAttr(target) + '" target="_blank" rel="noopener">' + escapeHtml(target) + '</a>';
    }
    return '<a role="button" tabindex="0" class="path-link" data-path="' + escapeAttr(target) + '">' + escapeHtml(target) + '</a>';
  }

    return { escapeHtml,
    _escapeHtmlChar,
    escapeAttr,
    unescapeHtml,
    truncate,
    normalizeMarkdownLinkTarget,
    isUnavailableMarkdownImageTarget,
    CODEX_INLINE_VIS_RE,
    linkifyCodexInlineVisuals,
    PASTED_IMG_RE,
    PASTED_IMG_MD_LINK_RE,
    ANNOTATION_IMG_RE,
    pastedImageTag,
    linkifyPastedImages,
    sessionQuickAction,
    decisionQuickActions,
    sessionQuickActions,
    ssbChipLabel,
    NEEDS_YOU_SAY_RE,
    needsYouSayPhrases,
    NEEDS_YOU_CMD_RE,
    NEEDS_YOU_REPLY_RE,
    needsYouActions,
    needsYouActionsHtml,
    renderSessionStateBlock,
    normalizeTaskNotificationField,
    taskNotificationTagText,
    parseTaskNotificationBlock,
    splitTaskNotificationSummary,
    taskNotificationPlainText,
    renderTaskNotificationBlock,
    renderNestedBulletList,
    renderDevinSummaryBlock,
    MD_RENDER_MAX_CHARS,
    renderMarkdown,
    _CB_LANG_ALIAS,
    _CB_LANG_PATTERNS,
    _linkifyEscapedUrls,
    INLINE_CODE_PATH_RE,
    _isPlaceholderPathToken,
    _isInternalApiPathToken,
    _inlineCodeWebUrl,
    _shouldLinkifyInlineCodePath,
    highlightCode,
    renderCodeBlock,
    renderInline,
    linkifySessionRefs,
    WATCHTOWER_TICKET_REF_RE,
    linkifyWatchtowerTicketRefs,
    linkifyPath };
  }
  root.CCCConversationText = Object.freeze({ createRenderer });
})(typeof window === 'undefined' ? globalThis : window);
