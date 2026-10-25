/* Read-only checks against the latest transcript window. No model calls. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.cccRowAudit = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const none = value => !String(value || '').trim() || /^(?:none|nothing|no action(?: is)? needed|n\/a|not applicable)[.!]?(?:\s+[—–-]\s+.*)?$/i.test(String(value).trim());
  const norm = value => String(value || '').replace(/[*_`#>]+/g, '').replace(/\s+/g, ' ').trim();
  function latest(events) {
    let answer = '', index = -1;
    for (let i = events.length - 1; i >= 0; i--) {
      const event = events[i];
      if (event.type !== 'assistant' || !Array.isArray(event.blocks)) continue;
      const text = event.blocks.filter(b => b.kind === 'text').map(b => b.text || '').join('\n\n').trim();
      if (text) { answer = text; index = i; break; }
    }
    const fields = {};
    const footers = [...answer.matchAll(/<session-state>([\s\S]*?)<\/session-state>/gi)];
    if (footers.length) {
      const body = footers[footers.length - 1][1];
      const re = /^(DID|INSIGHT|NEXT_STEP_USER|NEXT_ACTION|COMPLETION):\s*([\s\S]*?)(?=^(?:DID|INSIGHT|NEXT_STEP_USER|NEXT_ACTION|COMPLETION):|$\s*(?![\s\S]))/gm;
      for (const match of body.matchAll(re)) fields[match[1]] = match[2].trim();
    }
    const prose = answer.replace(/<session-state>[\s\S]*$/i, '').trim();
    return {answer, fields, prose, paragraph: prose.split(/\n\s*\n/).filter(Boolean).pop() || '',
      userAfter: index >= 0 && events.slice(index + 1).some(e => !e.bookkeeping && ['user', 'user_text'].includes(e.type))};
  }
  function closeReasons(c) {
    if (!c || c.version !== 1) return ['No completion assessment.'];
    const reasons = [];
    if (!c.scope || typeof c.scope.closure_met !== 'boolean' || !c.scope.goal || !c.scope.done_when || !c.scope.evidence) reasons.push('Topic closure criteria are missing.');
    else if (!c.scope.closure_met) reasons.push('Topic goal is still open.');
    for (const [key, minimum] of [['resolution', .9], ['verification', .8], ['delivery', 1]]) {
      if (key === 'delivery' && c.delivery?.applicable === false && c.delivery.evidence) continue;
      const axis = c[key];
      if (!axis || !Number.isFinite(axis.score) || axis.score < minimum || axis.score > 1 || !axis.evidence) reasons.push(key + ' gate is not met.');
    }
    if (c.decision_dependency?.score !== 0 || !c.decision_dependency?.evidence) reasons.push('A decision remains or is unassessed.');
    if (!Array.isArray(c.caveats) || c.caveats.some(x => typeof x.blocking !== 'boolean' || !x.text || x.blocking)) reasons.push('Blocking or invalid caveats.');
    if (!Array.isArray(c.remaining_actions) || c.remaining_actions.length) reasons.push('Unfinished or unassessed actions.');
    return reasons;
  }
  function inspect(row, events, rendered = {}) {
    const source = latest(Array.isArray(events) ? events : []);
    const findings = [];
    const add = (kind, text, code) => findings.push({kind, text, ...(code ? {code} : {})});
    const working = !!rendered.working || ['working', 'running'].includes(row.state) || row.codex_state === 'working' || !!row.pending_spawn || source.userAfter;
    let completion = null, action = null;
    try { completion = JSON.parse(source.fields.COMPLETION || 'null'); } catch (_) {}
    try { action = JSON.parse(source.fields.NEXT_ACTION || 'null'); } catch (_) {}
    if (source.answer && !working && (!completion || completion.version !== 1)) add('unknown', 'The latest answer has no valid completion assessment; Close remains unassessed.');
    const questions = [...new Map([...String(source.fields.NEXT_STEP_USER || source.prose).matchAll(/\[Q(\d+):([a-z0-9-]+)\]/g)].map(m => [m[1], {n: +m[1], tag: m[0]}])).values()];
    if (!source.answer) add('unknown', 'No assistant answer in this transcript window; freshness is unassessed.');
    else if (working) add('pending', 'A reply is in progress or a newer user message is waiting; check again after the turn ends.');
    else if (!Object.keys(source.fields).length) add('unknown', 'The latest answer has no session-state footer; action and completion claims need review.');
    else {
      const state = row.session_state || {};
      if (['DID', 'NEXT_STEP_USER'].some(key => norm(source.fields[key]) !== norm(state[key.toLowerCase()]))) add('warning', 'The row summary differs from the latest answer.');
      else add('pass', 'The row summary matches the latest answer footer.');
      const expected = !none(source.fields.NEXT_STEP_USER) ? source.fields.NEXT_STEP_USER : source.fields.DID;
      if (rendered.text && expected && !norm(expected).startsWith(norm(rendered.text))) add('warning', 'The visible row text differs from the latest next step or update.');
      const hasAction = action && typeof action.label === 'string' && action.label.trim() && action.label.length <= 24
        && typeof action.reply === 'string' && action.reply.trim() && action.reply.length <= 400;
      if (!none(source.fields.NEXT_STEP_USER)) {
        // NEXT_ACTION is null by contract for check/review/choice steps, so its absence is a
        // review hint, not an incident (CCC-187: every hourly ticket was this false positive).
        if (!hasAction && !questions.length) add('warning', 'A user step is stated, but no model-authored button or question details are supplied.');
        if (hasAction && (!rendered.action || rendered.action.label !== action.label || rendered.action.reply !== action.reply)) add('warning', 'The latest action button is missing or differs from the model-authored reply.', 'missing_action');
        if (questions.length && !rendered.questions) add('warning', 'The next step references a question without a visible details chip.', 'missing_question');
        if (hasAction || questions.length) add('pass', 'The latest answer supplies an action or a question to inspect.');
      } else if (completion?.decision_dependency?.score > 0) add('warning', 'The assessment says a decision remains, but Needs you is empty.');
      if (completion?.scope?.closure_met === false && !completion?.remaining_actions?.length) add('warning', 'The topic is open but has no concrete remaining plan.');
    }
    const reasons = closeReasons(completion);
    if (working) reasons.unshift('The current turn is not finished.');
    if (rendered.close && reasons.length) add('warning', 'Close is shown despite: ' + reasons.join(' '));
    else if (rendered.checked && !working && !reasons.length && none(source.fields.NEXT_STEP_USER) && !rendered.close) add('warning', 'All explicit Close gates pass, but the rendered Close button is missing.', 'missing_close');
    else add(reasons.length ? 'info' : 'pass', reasons.length ? 'Keep open: ' + reasons.join(' ') : 'All explicit Close gates pass; the evidence is still self-reported.');
    return {findings, source, questions, completion, action, checkedAt: Date.now(),
      status: findings.some(x => x.kind === 'warning') ? 'warning' : findings.some(x => x.kind === 'unknown' || x.kind === 'pending') ? 'unknown' : 'consistent'};
  }
  return {inspect, latest, closeReasons};
});
