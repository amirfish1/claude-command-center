/* Shared conversation annotations with explicit live-state and host adapters. */
(function (root) {
  function createController({ state: sharedState, helpers: hostServices }) {
  function _uxFixesWorkerProjectFromName(value) {
    const text = (0, hostServices._uxFixesLooseWords)(value);
    if (!text) return '';
    let m = text.match(/^([a-z0-9]+) ux fixes queue$/);
    if (m) return m[1].toUpperCase();
    m = text.match(/^ux fixes queue ([a-z0-9]+)$/);
    if (m) return m[1].toUpperCase();
    m = text.match(/^([a-z0-9]+) ux worker$/);
    if (m) return m[1].toUpperCase();
    m = text.match(/^ux worker ([a-z0-9]+)$/);
    if (m) return m[1].toUpperCase();
    // Watchtower worker sessions: "CCC queue worker", "OPS queue worker"
    m = text.match(/^([a-z0-9]+) queue worker$/);
    if (m) return m[1].toUpperCase();
    return '';
  }

  function _uxFixesWorkerProjectForRow(c) {
    if (!c) return '';
    for (const v of [c.display_name, c.ai_title, c.title, c.name]) {
      const project = _uxFixesWorkerProjectFromName(v);
      if (project) return project;
    }
    return '';
  }

  function _uxFixesHandledTicketsForRow(c, limit = Infinity) {
    return (0, sharedState._uxFixesWorkerTicketsForRow)(c).filter(t => t.status === 'closed').slice(0, limit);
  }

  function _uxFixesQueueProgressForRow(c) {
    if (!sharedState.uxFixesQueueMeta) return null;
    // Match the row on any of its identities (UUID or name) so a worker that
    // claimed with its made-up name still lights up its row, not just one that
    // claimed with its raw session id.
    const keys = (0, sharedState._uxFixesRowIdentityKeys)(c);
    if (!keys.length) return null;
    const projectMaxSeq = sharedState.uxFixesQueueMeta.projectMaxSeq || new Map();
    const byClaimed = sharedState.uxFixesQueueMeta.byClaimedSession || new Map();
    // 1) A FRESH in-progress claim wins — "currently working".
    for (const key of keys) {
      const cur = byClaimed.get(key);
      if (!cur) continue;
      // Stale claim → parked/between tickets. `continue`, NOT `break`: a row
      // can carry several identities (UUID + made-up name), and an orphaned
      // claim under one (e.g. a 6-day-old in_progress CCC-10 under the UUID)
      // must not abort the search for a FRESH claim under another identity.
      // If every identity is stale the loop falls through to the last-fix chip.
      if (cur.claimedAt && (Date.now() - cur.claimedAt) > sharedState.UX_FIXES_ACTIVE_CLAIM_MS) continue;
      const total = Number(projectMaxSeq.get(cur.project) || 0);
      if (!Number.isFinite(total) || total <= 0) continue;
      return { kind: 'working', current: cur.seq, total, lane: cur.lane || 'normal', project: cur.project, ref: cur.ref };
    }
    // 1b) Project-named worker rows ("CCC UX fixes queue", "UX worker · CCC")
    // are valid queue workers even when the claim/close identity was recorded
    // under a CLI alias. Fall back to this project-level progress so those
    // rows still show "xx/xx" instead of looking like ordinary sessions.
    const projectHint = _uxFixesWorkerProjectForRow(c);
    const projectActive = sharedState.uxFixesQueueMeta.activeByProject || new Map();
    if (projectHint) {
      const cur = projectActive.get(projectHint);
      if (cur && !(cur.claimedAt && (Date.now() - cur.claimedAt) > sharedState.UX_FIXES_ACTIVE_CLAIM_MS)) {
        const total = Number(projectMaxSeq.get(cur.project) || 0);
        if (Number.isFinite(total) && total > 0) {
          return { kind: 'working', current: cur.seq, total, lane: cur.lane || 'normal', project: cur.project, ref: cur.ref };
        }
      }
    }
    // 2) Project-named worker rows should show the latest project close, even
    // when this visual row's own session id last closed an older ticket. Queue
    // draining can move across worker identities, but the project worker row is
    // the thing the user scans for up-to-date project progress.
    const projectLastFix = sharedState.uxFixesQueueMeta.lastFixByProject || new Map();
    if (projectHint) {
      const done = projectLastFix.get(projectHint);
      if (done) {
        const total = Number(projectMaxSeq.get(done.project) || 0);
        if (Number.isFinite(total) && total > 0) {
          return { kind: 'done', current: done.seq, total, lane: 'normal', project: done.project, ref: done.ref };
        }
      }
    }
    // 3) Otherwise, credit the session that closed this project's latest ticket
    // attributable to this row — so an idle non-project worker row still shows
    // what it personally finished.
    const lastFix = sharedState.uxFixesQueueMeta.lastFixBySession || new Map();
    for (const key of keys) {
      const done = lastFix.get(key);
      if (!done) continue;
      const total = Number(projectMaxSeq.get(done.project) || 0);
      if (!Number.isFinite(total) || total <= 0) break;
      return { kind: 'done', current: done.seq, total, lane: 'normal', project: done.project, ref: done.ref };
    }
    return null;
  }

  function _ccWtCommandLabel(match) {
    // Read shell words only to this command's boundary. Quoted prose may
    // mention other tickets; only whole arguments can identify the target.
    const args = match.input.slice(match.index + match[0].length);
    const word = /(?:[^\s;&|()`"'\\]+|\\[\s\S]|"(?:\\[\s\S]|[^"\\])*"|'[^']*')+/y;
    const tokens = [];
    let offset = 0;
    while (offset < args.length) {
      const space = /^[ \t]+/.exec(args.slice(offset));
      if (space) offset += space[0].length;
      if (args.slice(offset, offset + 2) === '\\\n') { offset += 2; continue; }
      word.lastIndex = offset;
      const token = word.exec(args);
      if (!token) break;
      tokens.push(token[0].replace(/^(["'])([\s\S]*)\1$/, '$2'));
      offset = word.lastIndex;
    }
    const refs = [];
    let queue = '';
    const proseFlag = /^--(?:note|summary|progress|reason|title|question|text|recommend|undo|caveat|follow-up|unresolved|accept|detail)$/;
    for (let i = 0; i < tokens.length; i++) {
      const token = tokens[i];
      if (proseFlag.test(token.split('=')[0])) { if (!token.includes('=')) i++; continue; }
      if (token === '-q' || token === '--queue') { queue = tokens[++i] || ''; continue; }
      if (/^--queue=/.test(token)) { queue = token.slice(8); continue; }
      const ref = /^(?:--[\w-]+=)?([A-Z][A-Z0-9_]*-\d+)$/.exec(token);
      if (ref && !refs.includes(ref[1])) refs.push(ref[1]);
    }
    const context = refs.length ? refs.join(', ') : (/^[\w.-]+$/.test(queue) ? queue : '');
    return 'wt ' + match[2] + (context ? ' · ' + context : '');
  }

  function _ccOrchScan(text, seen) {
    seen = seen || new Set();
    if (!text) return seen;
    for (const [re, fmt] of sharedState._CC_ORCH_RES) {
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(text))) seen.add(fmt(m));
    }
    return seen;
  }

  function _ccChipHost(text) {
    let host = text;
    while (host.lastElementChild && /^(P|UL|OL|LI|BLOCKQUOTE|DIV)$/.test(host.lastElementChild.tagName)
      && !host.lastElementChild.classList.contains('cc-run-chip')) {
      host = host.lastElementChild;
    }
    return host;
  }

  function _ccMarkRuns(view) {
    const stale = new Set(view.querySelectorAll('[data-cc-run]'));
    const staleChips = new Set(view.querySelectorAll('.cc-run-chip'));
    const staleFolds = new Set(view.querySelectorAll('.cc-run-fold, .cc-run-tools, .cc-run-preview'));
    const staleOrch = new Set(view.querySelectorAll('.cc-orch-chip[data-cc-orch-for]'));
    const staleThumbs = new Set(view.querySelectorAll('.cc-run-thumbs'));
    const staleFinals = new Set(view.querySelectorAll('.assistant-text.cc-final'));
    // The last text of each turn (right before the next user message, or
    // the end of the view) is the turn's summary: compact keeps it as a card.
    const markFinal = (t) => {
      if (!t) return;
      staleFinals.delete(t);
      if (!t.classList.contains('cc-final')) t.classList.add('cc-final');
    };
    let run = [];
    let prevText = null;
    let turnText = null;
    const markPreview = (head, active) => {
      const latest = active[active.length - 1];
      const tool = latest.kind === 'tool' ? latest.el : null;
      const calls = tool?.querySelectorAll('.tool-call, .kimi-tool');
      const call = calls?.length ? calls[calls.length - 1] : tool;
      const detail = call?.querySelector('.tool-detail, .kimi-tool-arg')?.textContent.trim();
      // Retain the reasoning teaser when there is no newer tool description.
      const firstThought = active.find(u => u.kind === 'think')?.el;
      const thoughtBody = firstThought?.querySelector('.t-body, .kimi-thinking-full') || firstThought;
      const previewText = detail || (thoughtBody && Array.from(thoughtBody.childNodes)
        .filter(el => !el.matches?.('.cc-run-preview, .cc-run-fold, .cc-run-tools, .cc-orch-chip, .cc-run-thumbs'))
        .map(el => el.textContent).join('').trim());
      if (!previewText) return;
      let preview = head.querySelector(':scope > .cc-run-preview');
      if (!preview) {
        preview = document.createElement('div');
        preview.className = 'cc-run-preview';
        preview.dir = 'auto';
        head.appendChild(preview);
      }
      const text = detail ? detail.replace(/[.…]+$/u, '') + '...'
        : previewText.slice(0, 600) + (previewText.length > 600 ? '…' : '');
      if (preview.textContent !== text) preview.textContent = text;
      preview.classList.toggle('is-tool-preview', !!detail);
      staleFolds.delete(preview);
    };
    const flush = () => {
      const active = run.filter(u => u.kind !== 'passive');
      // A chip costs no row, so with a preceding text even one step folds;
      // Legacy standalone rows fold at two steps; merged tools/thinking
      // also fold alone so output previews never dominate compact mode.
      // A standalone tool group already has its own disclosure.
      if (active.length >= 2 || (active.length && prevText) || active[0]?.el.matches('.kimi-tool, .kimi-thinking')) {
        const head = active[0].el;
        const calls = active.reduce((n, u) => n + (u.calls || 0), 0);
        const thoughts = active.filter(u => u.kind === 'think').length;
        const open = run.some(u => u.el.classList.contains('cc-run-open'));
        const thinking = run.some(u => u.el.classList.contains('cc-run-thinking'));
        const id = head.dataset.ccRun || String(++sharedState._ccRunSeq);
        run.forEach(({ el, kind }) => {
          stale.delete(el);
          el.dataset.ccRun = id;
          el.classList.toggle('cc-run-head', el === head);
          el.classList.toggle('cc-run-tail', el !== head);
          el.classList.toggle('cc-run-open', open);
          el.classList.toggle('cc-run-thinking', thinking);
          el.classList.toggle('cc-run-thought', kind === 'think');
        });
        const label = [
          calls ? calls + ' tool call' + (calls === 1 ? '' : 's') : '',
          thoughts ? thoughts + ' thought' + (thoughts === 1 ? '' : 's') : '',
        ].filter(Boolean).join(' \u00b7 ');
        const labelEl = head.classList.contains('tool-call-group')
          ? head.querySelector(':scope > .tool-call-group-header') : head;
        if (labelEl && labelEl.dataset.ccRunLabel !== label) labelEl.dataset.ccRunLabel = label;
        // The first Claude group's arrow belongs to that group, just like
        // later arrows. Give the enclosing run its own collapse control.
        if (labelEl) {
          let fold = labelEl.querySelector('.cc-run-fold');
          if (!fold) {
            fold = document.createElement('button');
            fold.type = 'button';
            fold.className = 'cc-run-fold';
            fold.setAttribute('aria-label', 'Collapse tool run');
            labelEl.appendChild(fold);
          }
          const foldLabel = 'Fold ' + label;
          if (fold.textContent !== foldLabel) fold.textContent = foldLabel;
          staleFolds.delete(fold);
          let tools = labelEl.querySelector('.cc-run-tools');
          if (calls && thoughts) {
            if (!tools) {
              tools = document.createElement('button');
              tools.type = 'button';
              tools.className = 'cc-run-tools';
              labelEl.appendChild(tools);
            }
            const toolsLabel = thinking ? 'Show tools' : 'Hide tools';
            if (tools.textContent !== toolsLabel) tools.textContent = toolsLabel;
            tools.setAttribute('aria-expanded', String(open && !thinking));
            staleFolds.delete(tools);
          }
        }
        markPreview(head, active);
        // Chip at the end of the preceding text, so the fold line doesn't
        // take a row between sentences (CSS exempts chip-bearing text from
        // the two-line clamp so the chip is never cut off). No preceding
        // text (run follows a user turn): the standalone head line stays.
        let chip = null;
        if (prevText) {
          chip = prevText.querySelector('.cc-run-chip[data-cc-chip-for="' + id + '"]');
          if (!chip) {
            chip = document.createElement('span');
            chip.className = 'cc-run-chip';
            chip.dataset.ccChipFor = id;
            _ccChipHost(prevText).appendChild(chip);
          }
        }
        if (chip) {
          staleChips.delete(chip);
          if (chip.dataset.ccRunLabel !== label) chip.dataset.ccRunLabel = label;
          chip.classList.toggle('cc-run-open', open);
        }
        head.classList.toggle('cc-run-chipped', !!chip);
        // Purple orchestration chips ride next to the run chip (or in the
        // standalone head line when no text precedes the run).
        const orch = (0, sharedState._ccOrchLabels)(active);
        const orchHost = chip ? chip.parentNode : (head.classList.contains('tool-call-group')
          ? head.querySelector(':scope > .tool-call-group-header') : head);
        if (orchHost) {
          const sel = '.cc-orch-chip[data-cc-orch-for="' + id + '"]';
          const have = Array.from(orchHost.querySelectorAll(sel));
          const want = orch.slice(0, 4).concat(orch.length > 4 ? ['+' + (orch.length - 4)] : []);
          if (have.map(c => c.dataset.ccOrch).join('|') !== want.join('|')) {
            have.forEach(c => c.remove());
            want.forEach(l => {
              const c = document.createElement('span');
              c.className = 'cc-orch-chip';
              c.dataset.ccOrchFor = id;
              c.dataset.ccOrch = l;
              if (chip) chip.insertAdjacentElement('afterend', c);
              else orchHost.appendChild(c);
            });
          }
          orchHost.querySelectorAll(sel).forEach(c => staleOrch.delete(c));
        }
        // CCC-333: images the folded calls returned (Read of a screenshot,
        // tool-result images) stay visible as small thumbs next to the chips.
        // Clicking one opens the full image in the lightbox.
        if (orchHost) {
          const srcs = [];
          for (const { el } of active) {
            el.querySelectorAll('.tool-inline-image img.msg-image, .tool-result-image img.msg-image').forEach(im => {
              const src = im.getAttribute('src');
              if (src && !srcs.includes(src)) srcs.push(src);
            });
          }
          const tsel = '.cc-run-thumbs[data-cc-thumbs-for="' + id + '"]';
          let strip = orchHost.querySelector(tsel);
          if (srcs.length) {
            const key = srcs.join('|');
            if (!strip) {
              strip = document.createElement('span');
              strip.className = 'cc-run-thumbs';
              strip.dataset.ccThumbsFor = id;
              const lastChip = Array.from(orchHost.querySelectorAll('.cc-orch-chip[data-cc-orch-for="' + id + '"]')).pop() || chip;
              if (lastChip) lastChip.insertAdjacentElement('afterend', strip);
              else orchHost.appendChild(strip);
            }
            if (strip.dataset.ccThumbKey !== key) {
              strip.dataset.ccThumbKey = key;
              strip.textContent = '';
              srcs.slice(0, 4).forEach(src => {
                const im = document.createElement('img');
                im.className = 'msg-image cc-run-thumb';
                im.src = src;
                im.alt = 'Image from folded tool call';
                im.title = 'Enlarge';
                im.loading = 'lazy';
                im.onerror = () => im.remove();
                strip.appendChild(im);
              });
              if (srcs.length > 4) {
                const more = document.createElement('span');
                more.className = 'cc-run-thumbs-more';
                more.textContent = '+' + (srcs.length - 4);
                strip.appendChild(more);
              }
            }
            staleThumbs.delete(strip);
          }
        }
      } else if (active[0]?.el.matches('.tool-call-group, .kimi-tool-group')) {
        markPreview(active[0].el, active);
      }
      run = [];
    };
    for (const u of (0, hostServices._ccUnitsOf)(view)) {
      if (u.kind === 'break') {
        flush();
        if (u.el.classList.contains('user_text')) { markFinal(turnText); turnText = null; }
        prevText = u.el.classList.contains('assistant-text') ? u.el
          : (u.el.classList.contains('event') && u.el.classList.contains('assistant')
            ? u.el.querySelector(':scope > .assistant-text') : null);
        if (prevText) turnText = prevText;
      } else {
        run.push(u);
      }
    }
    flush();
    markFinal(turnText);
    staleFinals.forEach(t => t.classList.remove('cc-final'));
    stale.forEach(el => {
      el.classList.remove('cc-run-head', 'cc-run-tail', 'cc-run-open', 'cc-run-chipped', 'cc-run-thinking', 'cc-run-thought');
      delete el.dataset.ccRun;
    });
    staleChips.forEach(c => c.remove());
    staleFolds.forEach(c => c.remove());
    staleOrch.forEach(c => c.remove());
    staleThumbs.forEach(c => c.remove());
  }
  function _uxFixesLooseWords(value) {
    return String(value || '').trim().toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function _ccUnitsOf(view) {
    // -> array of { el, kind: 'tool'|'think'|'passive'|'break', calls }
    const units = [];
    for (const el of view.children) {
      // The shared disclosure owns this history and its orchestration chips.
      if (el.matches('.activity-run')) { units.push({ el, kind: 'break' }); continue; }
      if (el.matches('.activity-run-source, .conv-codex-state, .conv-live-tool-inline, .conv-live-tool-strip')) continue;
      const cl = el.classList;
      if (cl.contains('tool-call-group')) {
        units.push({ el, kind: 'tool', calls: Math.max(1, el.querySelectorAll('.tool-call').length) });
      } else if (el.matches('details.spawn-stats, .codex-subagent')) {
        units.push({ el, kind: 'passive' });
      } else if (cl.contains('kimi-turn')) {
        // Codex/Kimi merge events into turns. Walk their visible blocks;
        // hidden reconciliation markers and answer chrome are transparent.
        for (const c of el.children) {
          if (c.matches('.activity-run')) { units.push({ el: c, kind: 'break' }); continue; }
          if (c.matches('.activity-run-source, .kimi-marker, .kimi-answer-meta, .thinking-run-source')) continue;
          if (c.matches('.acp-needs-approval, :has(.acp-needs-approval)')) {
            units.push({ el: c, kind: 'break' });
          } else if (c.matches('.kimi-tool-group, .kimi-tool')) {
            units.push({ el: c, kind: 'tool', calls: Math.max(1, c.querySelectorAll('.kimi-tool').length) });
          } else if (c.matches('.thinking-run')) {
            units.push({ el: c, kind: 'break' });
          } else if (c.matches('.kimi-thinking')) {
            units.push({ el: c, kind: 'think' });
          } else {
            units.push({ el: c, kind: 'break' });
          }
        }
      } else if (cl.contains('event') && cl.contains('assistant')) {
        // Silent-thinking-only turns are display:none (no saved reasoning);
        // counting them made "3 thoughts" expand to one visible thought.
        if (cl.contains('is-silent-thinking-only')) { units.push({ el, kind: 'passive' }); continue; }
        let found = false;
        for (const c of el.children) {
          if (c.matches('.activity-run')) { found = true; units.push({ el: c, kind: 'break' }); continue; }
          if (c.matches('.activity-run-source')) continue;
          if (c.classList.contains('thinking-run-source')) continue;
          const cc = c.classList;
          if (cc.contains('tool-call-group')) { found = true; units.push({ el: c, kind: 'tool', calls: Math.max(1, c.querySelectorAll('.tool-call').length) }); }
          else if (cc.contains('tool-call')) { found = true; units.push({ el: c, kind: 'tool', calls: 1 }); }
          else if (cc.contains('thinking-run')) { found = true; units.push({ el: c, kind: 'break' }); }
          else if (cc.contains('thinking-block')) { found = true; units.push({ el: c, kind: cc.contains('thinking-block-silent') ? 'passive' : 'think' }); }
          else if (cc.contains('assistant-text')) { found = true; units.push({ el: c, kind: 'break' }); }
        }
        if (!found) units.push({ el, kind: 'passive' });
      } else {
        units.push({ el, kind: 'break' });
      }
    }
    return units;
  }
    return { _ccOrchScan, _ccWtCommandLabel, _ccChipHost, _ccMarkRuns, _uxFixesQueueProgressForRow, _uxFixesHandledTicketsForRow, _uxFixesWorkerProjectForRow, _uxFixesWorkerProjectFromName, _uxFixesLooseWords, _ccUnitsOf };
  }
  root.CCCConversationAnnotations = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
