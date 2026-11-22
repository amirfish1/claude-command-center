/* Session launch evidence, shared by the Metadata rail and helper rows. */
(function (root) {
  'use strict';
  const absent = new Set(['', 'none', 'null', '-', 'not available', 'not-available', 'unknown']);
  const metadata = new Map();
  const labels = {ui: 'CCC dashboard', 'ui-automated': 'CCC dashboard automation',
    api: 'CCC API', cli: 'CCC CLI', terminal: 'Terminal', subagent: 'Another session',
    watchtower: 'WatchTower', resumed: 'Continued session', resume: 'Continued session',
    'ccc-ask': 'ccc-ask', external: 'External tool', 'external-tool': 'External tool'};
  const nativeClients = {'claude-command-center': 'CCC', watchtower_app_worker: 'WatchTower',
    codex_exec: 'Codex exec', 'codex-tui': 'Codex CLI', codex_cli_rs: 'Codex CLI'};
  const escape = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));

  function describe(row) {
    if (!row || row.source === 'backlog' || row.source === 'github_pr' || row.backlog_type === 'github') return null;
    const sid = String(row.session_id || row.id || '');
    const parent = String(row.parent_session_id || row.hermes_parent_session_id || '').trim();
    const continued = String(row.continued_from_session_id || '').trim();
    const cwd = String(row.session_cwd || row.folder_path || row.cwd || '').replace(/\/$/, '');
    const leaf = cwd ? cwd.split(/[\\/]/).pop() : String(row.slug || '');
    let via = String(row.spawned_via || '').trim().toLowerCase();
    let tool = '';
    const teDirectory = cwd ? /^te-side-[\w-]+$/.test(leaf) : /(?:^|-)te-side-[\w-]+$/.test(leaf);
    const wtDirectory = cwd ? /^wt-model-[\w-]+$/.test(leaf) : /(?:^|-)wt-model-[\w-]+$/.test(leaf);
    if (via === 'tokeneconomizer' || teDirectory) tool = 'tokeneconomizer';
    else if (via === 'watchtower-model' || wtDirectory) tool = 'watchtower-model';
    const caller = String(row.spawn_caller || '').trim();
    const nativeClient = String(row.launch_originator || '').trim();
    const kind = String(row.spawned_kind || '').trim();
    const prompt = String(row.first_message || '');
    const loopCheck = tool === 'tokeneconomizer' && (kind === 'loop-check'
      || prompt.startsWith('You monitor an AI coding agent that is partway through ONE task'));
    let launcher = 'Unknown', status = 'Unknown', evidence = 'No launch attribution was saved with this session.';
    if (tool) {
      via = tool;
      launcher = tool === 'tokeneconomizer' ? 'TokenEconomizer' : 'WatchTower model helper';
      status = 'Inferred';
      evidence = cwd && /(?:te-side|wt-model)-[\w-]+$/.test(leaf)
        ? 'Recognized helper working-directory convention.' : 'Saved helper attribution; original launch receipt unavailable.';
    } else if (caller) {
      launcher = caller;
      status = 'Recorded';
      evidence = 'Caller label saved in the session-start marker.';
    } else if (!absent.has(via)) {
      launcher = labels[via] || via;
      status = 'Recorded';
      evidence = 'Launch method saved in session metadata.';
      if (via === 'watchtower') {
        status = row.worker_origin || nativeClient === 'watchtower_app_worker' ? 'Recorded' : 'Inferred';
        evidence = row.worker_origin ? 'WatchTower session-origin record.'
          : nativeClient === 'watchtower_app_worker' ? 'Transcript records watchtower_app_worker as the creating client.'
            : 'WatchTower attribution on the session row; original launch receipt unavailable.';
      }
    } else if (nativeClient) {
      launcher = nativeClients[nativeClient] || nativeClient; status = 'Recorded';
      evidence = 'Transcript records ' + nativeClient + ' as the creating client.';
      if (nativeClient === 'codex_exec') evidence += ' Its calling tool is not identified.';
    } else if (parent && parent !== sid) {
      launcher = 'Another session'; status = 'Recorded'; evidence = 'A parent-session relationship was saved.';
    } else if (continued && continued !== sid) {
      launcher = 'Continued session'; status = 'Recorded'; evidence = 'A continuation relationship was saved.';
    }
    let purpose = '', purposeEvidence = '';
    if (loopCheck) {
      purpose = 'Check agent progress';
      purposeEvidence = kind === 'loop-check' ? 'Saved helper kind: loop-check.' : 'Inferred from the opening loop-check prompt.';
    } else if (kind) {
      purpose = kind.replace(/[-_]/g, ' ');
      purposeEvidence = 'Helper kind saved in session metadata.';
    } else if (row.worker_origin && row.worker_origin.role) {
      purpose = String(row.worker_origin.role).replace(/[-_]/g, ' ')
        + (row.worker_origin.ref ? ' · ' + row.worker_origin.ref : '');
      purposeEvidence = 'WatchTower session-origin record.';
    }
    const parentId = parent && parent !== sid ? parent : '';
    const continuedId = continued && continued !== sid ? continued : '';
    const note = tool && !parentId
      ? 'The helper is identifiable, but its requesting session was not saved. A task mentioned inside its prompt does not establish a parent link.'
      : status === 'Unknown'
        ? 'Older sessions and launches outside CCC may lack origin metadata. The engine or task title alone cannot identify the launcher.'
        : !parentId && !continuedId
          ? 'A parent link is only expected when another session launched this one. Direct launches may have no parent.' : '';
    return {sid, via, tool, launcher, status, evidence, purpose, purposeEvidence, parentId, continuedId, note,
      role: loopCheck ? 'loop check' : kind.replace(/[-_]/g, ' ')};
  }

  function sidebarHtml(row) {
    const origin = describe(row);
    if (!origin || !origin.tool) return '';
    const text = origin.launcher + (origin.role ? ' · ' + origin.role : ' · helper');
    return '<div class="conv-session-origin-summary" title="' + escape(origin.status + ': ' + origin.evidence)
      + '">' + escape(text) + '</div>';
  }

  function render(row, options) {
    const doc = options.document || document;
    const details = doc.getElementById('railSessionOrigin');
    if (!details) return false;
    const summary = doc.getElementById('railSpawnedVia');
    const body = doc.getElementById('railSessionOriginBody');
    const sid = String(row && (row.session_id || row.id) || '');
    details._originRow = row;
    details._originOptions = options;
    let saved = metadata.get(sid);
    if (row && sid && describe(row) && root.fetch && (!saved || (saved.done && Date.now() - saved.at > 60000))) {
      if (metadata.size >= 64) metadata.delete(metadata.keys().next().value);
      saved = {at: Date.now(), data: null};
      metadata.set(sid, saved);
      root.fetch('/api/conversations/' + encodeURIComponent(sid) + '/origin')
        .then(response => { if (!response.ok) throw new Error('Origin metadata unavailable'); return response.json(); })
        .then(data => { if (data && data.session_id === sid) saved.data = data; })
        .catch(() => { saved.failed = true; })
        .finally(() => {
          saved.done = true;
          if (details.dataset.sessionId === sid) render(details._originRow, details._originOptions);
        });
    }
    const origin = describe(saved && saved.data ? Object.assign({}, row, saved.data) : row);
    if (!origin) {
      details.hidden = true; details.open = false;
      details.dataset.sessionId = ''; delete details.dataset.content;
      summary.textContent = ''; body.replaceChildren(); return true;
    }
    if (details.dataset.sessionId !== origin.sid) details.open = false;
    details.dataset.sessionId = origin.sid;
    details.hidden = false;
    const parentId = origin.parentId || origin.continuedId;
    const parentRow = parentId && options.findSession && options.findSession(parentId);
    const parentTitle = parentRow && (parentRow.display_name || parentRow.ai_title) || (parentId ? parentId.slice(0, 8) : '');
    const pending = saved && !saved.done;
    const key = JSON.stringify([origin, parentTitle, !!pending, !!(saved && saved.failed)]);
    if (details.dataset.content === key) return true;
    details.dataset.content = key;
    summary.innerHTML = '<span class="session-origin-label">' + (pending && origin.status === 'Unknown'
      ? 'Checking origin…' : 'via: ' + escape(origin.launcher)) + '</span>'
      + (origin.status === 'Unknown' ? '' : '<span class="session-origin-basis">' + escape(origin.status.toLowerCase()) + '</span>');
    summary.title = 'Session origin · ' + origin.status + '. Click to view launcher, purpose and parent evidence.';
    const field = (name, value) => '<dt>' + escape(name) + '</dt><dd>' + value + '</dd>';
    const parentHtml = parentId
      ? '<button type="button" class="session-origin-parent" title="Open ' + escape(parentTitle) + '">'
        + escape(parentTitle) + '</button>'
      : '<span class="session-origin-missing">No parent recorded</span>';
    body.innerHTML = '<dl>'
      + field('Launcher', escape(origin.launcher))
      + field('Purpose', origin.purpose ? escape(origin.purpose) : '<span class="session-origin-missing">Not saved</span>')
      + field(origin.parentId ? 'Parent' : origin.continuedId ? 'Continued from' : 'Parent', parentHtml)
      + field('Evidence', escape(origin.evidence))
      + '</dl>'
      + (origin.purposeEvidence && origin.purposeEvidence !== origin.evidence
        ? '<p>' + escape(origin.purposeEvidence) + '</p>' : '')
      + (origin.note ? '<p>' + escape(origin.note) + '</p>' : '')
      + (saved && saved.failed ? '<p>Saved origin metadata could not be loaded. Showing the available session-row evidence.</p>' : '');
    const parentButton = body.querySelector('.session-origin-parent');
    if (parentButton) parentButton.onclick = () => options.openSession && options.openSession(parentId);
    return true;
  }
  root.CCCSessionOrigin = {describe, sidebarHtml, render};
})(typeof window !== 'undefined' ? window : globalThis);
