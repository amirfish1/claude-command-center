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
      return new Date(t).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' });
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
    if (status === 'closed') return null;
    if (status === 'in_review') return opts.explain ? make('review', 'neutral', 'awaiting checks / review',
      'Implementation has been submitted; required checks or review must pass before closure.') : null;
    var question = str(it.block_question);

    // Duplicate followers wait for the original's completion and checks.
    if (it.duplicate_of) {
      var original = str(it.duplicate_of);
      return make('dependency', 'neutral', 'waiting on ' + original,
        'Duplicate of ' + original + '. Waiting for its fix and required verification; no separate worker is needed.',
        { blocked: true, actor: original, depRef: original });
    }

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

    // Current scheduling state takes precedence over past answer delivery.
    var until = Date.parse(it.deferred_until || '');
    var now = opts.now != null ? opts.now : Date.now();
    if (status === 'open' && isFinite(until) && until > now) {
      var minutes = Math.max(1, Math.ceil((until - now) / 60000));
      var remaining = minutes < 60 ? minutes + 'm'
        : minutes < 1440 ? Math.ceil(minutes / 60) + 'h' : Math.ceil(minutes / 1440) + 'd';
      return make('deferred', 'neutral', 'deferred · in ' + remaining,
        'Deferred until ' + shortDate(it.deferred_until) + '\n'
          + new Date(until).toISOString()
          + (it.defer_reason ? '\n' + it.defer_reason : '') + '\nNot ready for a worker yet.');
    }
    if (opts.explain) {
      var deps = Array.isArray(it.waiting_on) ? it.waiting_on : [];
      if (deps.length) return make('dependency', 'neutral', 'waiting on ' + deps.join(', '),
        'These dependencies must complete before a worker can claim this ticket: ' + deps.join(', '),
        { blocked: true, depRef: str(deps[0]) });
      var ready = str(it.readiness);
      var readinessLabels = { 'needs-spec': 'needs specification', 'needs-shaping': 'needs shaping', 'needs-rationale': 'needs rationale' };
      if (status === 'open' && readinessLabels[ready]) return make('readiness', 'neutral', readinessLabels[ready],
        'Build workers cannot claim this ticket until ' + readinessLabels[ready].replace('needs ', '')
        + ' is completed. This is preparation work, not automatically a question for you.', { blocked: true });
    }
    // Legacy blocked rows do not necessarily request an owner decision.
    if (status === 'blocked') {
      return make('blocked', 'neutral', opts.explain && !question ? 'block reason missing' : 'blocked',
        question || 'Blocked without a recorded reason. This needs investigation; no owner decision is recorded.', { blocked: true });
    }

    var live = opts.liveClaim !== undefined ? !!opts.liveClaim : status === 'in_progress';
    var pa = it.pending_answer;
    if (pa && typeof pa === 'object' && pa.state) {
      // Receipt-backed only: 'delivered' is the delivery receipt; an
      // affinity/handed_off answer travels with the claim that bound it.
      var claimedAfter = lastEventAt(it, ['claim']) > str(pa.state_at || '');
      var received = pa.state === 'delivered'
        || ((pa.state === 'affinity' || pa.state === 'handed_off') && claimedAfter);
      if (received && status === 'in_progress' && live) {
        return make('answer-working', 'answer', 'working on answer',
          'Your answer was delivered and a live worker is on it.');
      }
      if (received && status === 'in_progress') {
        return make('answer-delivered', 'answer', 'delivered · awaiting worker',
          'Your answer was delivered, but no live worker is verified on this ticket. Delivery confirmation is historical, not proof of an active worker.');
      }
      // queued/delivering keep status in_progress (WatchTower E6-E8): the
      // answer is still in flight whatever the claim's liveness.
      var paLabel = {
        routing: ['answer queued', 'Your answer is recorded and queued for delivery to the worker.'],
        queued: ['answer queued', 'Your answer is queued; the worker session has not received it yet.'],
        delivering: ['answer sending', 'Your answer is being delivered; no receipt yet.'],
        delivered: ['awaiting worker', 'Your answer was received earlier, but this ticket is not currently being worked. Delivery confirmation is historical, not proof of an active worker.'],
        affinity: ['answer waiting', 'Your answer is reserved for the worker that asked; it has not resumed yet.'],
        handed_off: ['answer handed off', 'The asking session could not take your answer; the next worker gets it with the question.'],
      }[pa.state];
      if (paLabel) {
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

    if (opts.explain) {
      if (status === 'in_progress') {
        // Amber (tone 'answer') is reserved for real answer/delivery state;
        // plain live work and plain open work explain themselves neutrally.
        if (opts.liveClaim === true) return make('working', 'neutral', 'working', 'A live worker matches this ticket’s claim.');
        return make('unverified-claim', 'neutral', 'worker not verified',
          'Claimed by ' + str(it.claimed_by || 'an unknown worker')
          + (it.claimed_machine ? ' on ' + str(it.claimed_machine) : '')
          + '. This dashboard has not verified that worker is alive; remote workers may be outside its local roster.');
      }
      var q = opts.queue || {};
      var stage = it.stages && it.stages.current;
      if (stage && ['plan', 'plan_review', 'checks', 'verify'].indexOf(stage) >= 0) {
        var stageLabel = { plan: 'planning', plan_review: 'plan review', checks: 'checks', verify: 'verification' }[stage];
        return make('stage', 'neutral', 'awaiting ' + stageLabel,
          'The ticket is in the ' + stageLabel + ' stage; implementation workers must wait for that stage to complete.');
      }
      if (q.auto_drain === false && !it.run_requested) return make('paused', 'neutral', 'automatic work paused',
        'Automatic draining is disabled for this queue. Request a run or enable automatic draining.');
      if (q.desired_workers === 0) return make('capacity', 'neutral', 'no worker slots configured',
        'This queue’s worker budget is zero. A worker slot must be configured before it can run.');
      if (it.no_auto_drain === true && !it.run_requested) return make('manual', 'neutral', 'manual run required',
        'This ticket is excluded from automatic draining; it needs an explicit run request.');
      if (it.claimable === false || it.watchtower_runnable === false) return make('unexplained', 'neutral', 'eligibility needs checking',
        'The scheduler marks this ticket unavailable but supplies no specific reason here. This needs investigation; it is not a confirmed request for your input.');
      if (status === 'open') return make('ready', 'neutral', 'awaiting worker',
        'No current blocker is recorded. Waiting for the scheduler to assign an available worker.');
      return make('unexplained', 'neutral', 'state needs checking',
        'Recorded state: ' + status + '. No actionable waiting reason is supplied; this needs investigation.');
    }
    return null;
  }

  // Deferred work stays together at the top, soonest return first.
  // Zero leaves the caller's existing ordering intact for every other row.
  function compareDeferred(a, b, now) {
    now = now == null ? Date.now() : now;
    function due(it) {
      var t = Date.parse(it && it.deferred_until || '');
      return it && it.status === 'open' && !it.needs_input && isFinite(t) && t > now ? t : 0;
    }
    var at = due(a), bt = due(b);
    return at && bt ? at - bt : at ? -1 : bt ? 1 : 0;
  }

  // The "needs you" predicate for counts and filters.
  function needsOwner(it, opts) {
    var a = project(it, opts);
    return !!(a && a.owner);
  }

  return { compareDeferred: compareDeferred, project: project, needsOwner: needsOwner, currentWait: currentWait, commentsSinceBlock: commentsSinceBlock, WAIT_ON: WAIT_ON };
});
