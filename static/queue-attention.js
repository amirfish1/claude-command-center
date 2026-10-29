// Who a queue ticket is waiting on (CCC-244). One projection shared by the
// sidebar queue panel, the ticket detail modal and the q2 board, so the chip,
// row tint, status dot, tooltip and "needs you" counts can never disagree.
//
// Red ("needs you") is reserved for a decision only the owner can make. The
// rest come from structured state, never from free text or from "someone
// commented after the question":
//   - item.wait (WatchTower `wt block --wait-on` / `wt wait-on`), honoured
//     only while its block_at matches the item's current blocked_at, so a
//     newer block generation always wins;
//   - block_kind 'awaiting-client' (a client conversation turn);
//   - pending_answer.state (local queues' answer router) and answer/claim
//     history events (GitHub queues), for answer handoff;
//   - deferred_until.
// A block with no wait record reads as an owner decision, the safe default.
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.QueueAttention = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var WAIT_ON = ['owner', 'external', 'dependency', 'worker'];

  function str(v) { return v == null ? '' : String(v); }

  function history(it) { return it && Array.isArray(it.history) ? it.history : []; }

  function lastEventAt(it, names) {
    var h = history(it);
    for (var i = h.length - 1; i >= 0; i--) {
      if (h[i] && names.indexOf(h[i].event) >= 0) return str(h[i].at);
    }
    return '';
  }

  function currentWait(it) {
    var w = it && it.wait;
    if (!it || !it.needs_input || !w || typeof w !== 'object') return null;
    if (WAIT_ON.indexOf(w.on) < 0) return null;
    if (str(w.block_at) !== str(it.blocked_at)) return null;
    return w;
  }

  // Comments after the latest question. Reported, never interpreted: agents
  // post through the owner's GitHub account too, so a "human" comment is not
  // proof that the owner answered.
  function commentsSinceBlock(it) {
    var blockAt = lastEventAt(it, ['block']);
    if (!blockAt) return 0;
    return history(it).filter(function (e) {
      return e && e.event === 'comment' && str(e.at) > blockAt;
    }).length;
  }

  function shortDate(iso) {
    var t = Date.parse(iso || '');
    if (!isFinite(t)) return str(iso);
    try {
      return new Date(t).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    } catch (_) { return new Date(t).toISOString().slice(0, 16).replace('T', ' '); }
  }

  function make(key, tone, label, title, extra) {
    var out = { key: key, tone: tone, label: label, title: title, owner: key === 'owner', blocked: false };
    if (extra) for (var k in extra) out[k] = extra[k];
    return out;
  }

  // opts.liveClaim: the caller's verdict that the claim has a live worker
  // (CCC's stale/unverified-claim checks); undefined = trust status.
  function project(it, opts) {
    if (!it) return null;
    opts = opts || {};
    var status = str(it.status || 'open');
    if (status === 'closed' || status === 'in_review') return null;
    var question = str(it.block_question);

    if (it.needs_input) {
      if (it.block_kind === 'rationale') {
        return make('gate', 'gate', 'gate', question || 'pitch awaiting decision', { blocked: true });
      }
      var n = commentsSinceBlock(it);
      var noteComments = n
        ? '\n\n' + n + (n === 1 ? ' comment' : ' comments') + ' since this question. A comment is not an answer; '
          + 'record the decision with wt answer (or wt wait-on if someone else must act).'
        : '';
      var w = currentWait(it);
      var on = w ? w.on : (it.block_kind === 'awaiting-client' ? 'external' : 'owner');
      var why = w && w.note ? '\n\n' + str(w.note) : '';
      if (on === 'external') {
        var actor = str(w && w.actor) || (it.block_kind === 'awaiting-client' ? 'client' : 'external');
        return make('external', 'external', 'waiting on ' + actor,
          'Waiting on ' + actor + ', not on you.' + why + (question ? '\n\n' + question : '') + noteComments,
          { blocked: true, actor: actor });
      }
      if (on === 'dependency') {
        var dep = str(w && (w.ref || w.actor)) || 'dependency';
        return make('dependency', 'neutral', 'waiting on ' + dep,
          'Waiting on ' + dep + ' (another ticket or handoff), not on you.' + why + (question ? '\n\n' + question : '') + noteComments,
          { blocked: true, actor: dep, depRef: str(w && w.ref) });
      }
      if (on === 'worker') {
        return make('worker', 'answer', 'awaiting worker',
          'Decision already made; waiting for a worker to pick it up.' + why + (question ? '\n\n' + question : '') + noteComments,
          { blocked: true });
      }
      return make('owner', 'owner', 'needs you',
        'Needs your decision.' + why + (question ? '\n\n' + question : '') + noteComments, { blocked: true });
    }

    var live = opts.liveClaim !== undefined ? !!opts.liveClaim : status === 'in_progress';
    var pa = it.pending_answer;
    if (pa && typeof pa === 'object' && pa.state) {
      var claimedAfter = lastEventAt(it, ['claim']) > str(pa.state_at || '');
      if (status === 'in_progress' && live && (claimedAfter || pa.state === 'delivered')) {
        return make('answer-working', 'answer', 'working on answer',
          'Your answer was delivered and a live worker is on it.');
      }
      var paLabel = {
        routing: ['answer queued', 'Your answer is recorded and queued for delivery to the worker.'],
        queued: ['answer queued', 'Your answer is queued; the worker session has not received it yet.'],
        delivering: ['answer sending', 'Your answer is being delivered; no receipt yet.'],
        delivered: ['answer delivered', 'Your answer reached the worker session; waiting for a worker to resume.'],
        affinity: ['answer waiting', 'Your answer is reserved for the worker that asked; it has not resumed yet.'],
        handed_off: ['answer handed off', 'The asking session could not take your answer; the next worker gets it with the question.'],
      }[pa.state];
      if (paLabel && status !== 'in_progress') {
        return make('answer-' + pa.state, 'answer', paLabel[0], paLabel[1]);
      }
    } else {
      // GitHub queues have no answer router: an answer event newer than the
      // latest question is the structured record.
      var answerAt = lastEventAt(it, ['answer']);
      var blockAt = lastEventAt(it, ['block']);
      if (answerAt && answerAt > blockAt) {
        if (status === 'in_progress' && live && lastEventAt(it, ['claim']) > answerAt) {
          return make('answer-working', 'answer', 'working on answer', 'A live worker resumed after your answer.');
        }
        if (status !== 'in_progress' || !live) {
          return make('answer-pending', 'answer', 'answered · awaiting worker',
            'You answered; no live worker has resumed it yet.');
        }
      }
    }

    var until = Date.parse(it.deferred_until || '');
    var now = opts.now != null ? opts.now : Date.now();
    if (isFinite(until) && until > now) {
      return make('deferred', 'neutral', 'deferred until ' + shortDate(it.deferred_until),
        'Deferred until ' + shortDate(it.deferred_until) + (it.defer_reason ? ': ' + it.defer_reason : '') + '. Not waiting on you.');
    }
    return null;
  }

  // The "needs you" predicate for counts and filters.
  function needsOwner(it, opts) {
    var a = project(it, opts);
    return !!(a && a.owner);
  }

  return { project: project, needsOwner: needsOwner, currentWait: currentWait, commentsSinceBlock: commentsSinceBlock, WAIT_ON: WAIT_ON };
});
