// Collapse WatchTower reconciler log bursts into single summary rows.
//
// The reconciler writes one "thought" per idle evaluation as a burst of
// IDLE_CANDIDATE + IDLE_SIGNAL×N + IDLE_DECISION lines sharing an
// evaluation_id, and a GC sweep as one GC_RELEASED line per reaped worker
// (all in one same-timestamp write). The raw log keeps the full audit trail;
// these helpers fold each burst into one display row — a single line of the
// reconciler's thinking — while the member lines stay reachable behind a UI
// toggle. Shared by the main dashboard's activity-log panel (app.js) and the
// q2 log bar (q2.js); loaded before both.
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.WtLogBursts = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // "2026-07-27 01:02:52 UTC  CCC   SPAWN  CCC-666 — text" — tolerant of a
  // 'T' date/time separator so one parser serves both log renderers.
  var LINE_RE = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\s*UTC\s+(\S+)\s+(\S+)\s*(.*)$/;

  function parseLine(line) {
    var m = String(line || '').match(LINE_RE);
    if (!m) return null;
    return {
      line: String(line),
      date: m[1],
      time: m[2],
      queue: m[3],
      verb: m[4],
      detail: m[5],
      utcMs: Date.parse(m[1] + 'T' + m[2] + 'Z'),
    };
  }

  var IDLE_VERBS = { IDLE_CANDIDATE: 1, IDLE_SIGNAL: 1, IDLE_DECISION: 1 };

  // Burst identity for one parsed line: every member of an idle evaluation
  // shares its evaluation_id; a GC sweep's lines share queue+timestamp.
  // '' means the line is never part of a collapsible burst.
  function burstKey(e) {
    if (!e) return '';
    if (IDLE_VERBS[e.verb]) {
      var m = e.detail.match(/\bevaluation_id=(\S+)/);
      return m ? 'idle:' + m[1] : '';
    }
    if (e.verb === 'GC_RELEASED') {
      return 'gc:' + e.queue + ':' + e.date + ' ' + e.time;
    }
    return '';
  }

  // Pull one key=value field out of an _audit_detail string; handles bare,
  // "quoted", and [list] values.
  function kv(detail, key) {
    var m = String(detail || '').match(
      new RegExp('(?:^|\\s)' + key + '=("(?:[^"\\\\]|\\\\.)*"|\\[[^\\]]*\\]|\\S+)'));
    if (!m) return '';
    var v = m[1];
    if (v.length > 1 && v.charAt(0) === '"' && v.charAt(v.length - 1) === '"') {
      return v.slice(1, -1);
    }
    return v;
  }

  function fmtSecs(s) {
    s = Number(s);
    if (!isFinite(s) || s < 0) return '';
    if (s < 60) return Math.round(s) + 's';
    if (s < 3600) return Math.round(s / 60) + 'm';
    if (s < 86400) return Math.round(s / 3600) + 'h';
    return Math.round(s / 86400) + 'd';
  }

  // Fold consecutive same-key members into burst items, order preserved.
  // Each item is {raw} | {entry} | {key, members}. A lone member stays a
  // plain entry, so a burst clipped by the log tail renders its surviving
  // line normally.
  function collapse(lines) {
    var items = [];
    (lines || []).forEach(function (line) {
      var e = parseLine(line);
      if (!e) { items.push({ raw: String(line) }); return; }
      var key = burstKey(e);
      var last = items[items.length - 1];
      if (key && last && last.key === key) {
        last.members.push(e);
      } else if (key) {
        items.push({ key: key, members: [e] });
      } else {
        items.push({ entry: e });
      }
    });
    items.forEach(function (it) {
      if (it.key && it.members.length < 2) {
        it.entry = it.members[0];
        delete it.key;
        delete it.members;
      }
    });
    return items;
  }

  // One-line digest of a collapsed burst — worker, idle age vs floor, the
  // decision and why — so the stream shows one line per reconciler thought.
  // `worker` is returned separately for renderers with a worker column.
  function summary(members) {
    if (!members.length || members[0].verb === 'GC_RELEASED') {
      var acts = members.map(function (e) {
        var m = e.detail.match(/^worker (\S+) pid \d+ (\S+)/);
        return m ? m[1] + ' ' + m[2] : e.detail;
      });
      return { verb: 'GC_RELEASED', worker: '', text: acts.join(' · ') };
    }
    var cand = null;
    var dec = null;
    members.forEach(function (e) {
      if (e.verb === 'IDLE_CANDIDATE' && !cand) cand = e;
      if (e.verb === 'IDLE_DECISION') dec = e;
    });
    var src = dec || cand || members[members.length - 1];
    var worker = cand ? kv(cand.detail, 'worker_id') : '';
    var evalId = kv(src.detail, 'evaluation_id');
    var parts = [];
    var ageText = cand ? fmtSecs(kv(cand.detail, 'effective_age_s')) : '';
    var floorText = cand ? fmtSecs(kv(cand.detail, 'floor_s')) : '';
    if (ageText) parts.push('idle ' + ageText + (floorText ? ' ≥ floor ' + floorText : ''));
    parts.push('→ ' + (dec ? kv(dec.detail, 'decision') || 'evaluating' : 'evaluating'));
    var reasons = dec ? kv(dec.detail, 'reasons').replace(/[\[\]"]/g, '') : '';
    if (reasons) parts.push(reasons.split(',').join(', '));
    var rel = dec ? kv(dec.detail, 'release_id') : '';
    if (rel) parts.push(rel);
    if (evalId) parts.push(evalId);
    return { verb: 'IDLE', worker: worker, text: parts.join(' · ') };
  }

  return {
    parseLine: parseLine,
    burstKey: burstKey,
    collapse: collapse,
    summary: summary,
    kv: kv,
    fmtSecs: fmtSecs,
  };
});
