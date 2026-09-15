/* Pipeline Canvas — template gallery (data-driven).
 *
 * Each template is a small graph of registry component ids with layout
 * offsets and edge contracts. canvas.js renders gallery cards (with
 * mini-graph previews) and instantiates them as designed nodes; the
 * registry test validates that every referenced component exists and every
 * edge is port-legal.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CCC_CANVAS_TEMPLATES = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var TEMPLATES = [
    {
      id: 'feature-factory',
      name: 'Feature factory',
      desc: 'Design → build → independent visual review → human sign-off.',
      flow: 'planner → executor → verifier → gate',
      nodes: [
        { key: 'planner',  comp: 'planner',          label: 'DESIGN',     x: 0,   y: 40 },
        { key: 'executor', comp: 'executor-product', label: 'BUILD',      x: 330, y: 40 },
        { key: 'reviewer', comp: 'visual-verifier',  label: 'VERIFY',     x: 660, y: 40 },
        { key: 'gate',     comp: 'decision-inbox',   label: 'HUMAN GATE', x: 990, y: 40 }
      ],
      edges: [
        { from: 'planner', to: 'executor', label: 'files builds to' },
        { from: 'executor', to: 'reviewer', label: 'requests verification' },
        { from: 'reviewer', to: 'gate', label: 'VERIFIED / WRONG-STATE' },
        { from: 'planner', to: 'gate', label: 'design sign-off' }
      ]
    },
    {
      id: 'posthog-watchdog',
      name: 'PostHog watchdog',
      desc: 'Session signals become deduped tickets; hard cases park for a human.',
      flow: 'watcher → dedup → fixer → gate',
      nodes: [
        { key: 'stream', comp: 'posthog-watcher', label: 'SESSION WATCH', x: 0,   y: 40 },
        { key: 'dedup',  comp: 'deduplicator',    label: 'DEDUP',         x: 330, y: 40 },
        { key: 'fixer',  comp: 'executor-quick',  label: 'TRIAGE',        x: 660, y: 40 },
        { key: 'gate',   comp: 'decision-inbox',  label: 'HUMAN GATE',    x: 990, y: 40 }
      ],
      edges: [
        { from: 'stream', to: 'dedup', label: 'raw signals' },
        { from: 'dedup', to: 'fixer', label: 'files deduped issues' },
        { from: 'fixer', to: 'gate', label: 'hard cases' }
      ]
    },
    {
      id: 'quality-loop',
      name: 'The quality loop',
      desc: 'An auditor grades quiet conversations; failures become fixes; systemic ones escalate to a planner.',
      flow: 'auditor → fixer → planner → gate',
      nodes: [
        { key: 'stream',   comp: 'auditor-sweep',   label: 'AUDIT SWEEP', x: 0,   y: 40 },
        { key: 'executor', comp: 'executor-quick',  label: 'FIX',         x: 330, y: 40 },
        { key: 'planner',  comp: 'planner',         label: 'REDESIGN',    x: 330, y: 260 },
        { key: 'gate',     comp: 'decision-inbox',  label: 'HUMAN GATE',  x: 660, y: 150 }
      ],
      edges: [
        { from: 'stream', to: 'executor', label: 'files graded failures' },
        { from: 'executor', to: 'planner', label: 'systemic issues' },
        { from: 'planner', to: 'gate', label: 'redesign sign-off' },
        { from: 'executor', to: 'gate', label: 'hard cases' }
      ]
    },
    {
      id: 'payment-watchdog',
      name: 'Payment watchdog',
      desc: 'Payment emails parsed, deduped, reconciled — mismatches park for a human.',
      flow: 'parser → dedup → fixer → gate',
      nodes: [
        { key: 'parser', comp: 'email-parsers',   label: 'PAYMENT MAIL', x: 0,   y: 40 },
        { key: 'dedup',  comp: 'deduplicator',    label: 'DEDUP',        x: 330, y: 40 },
        { key: 'fixer',  comp: 'db-investigator', label: 'RECONCILE',    x: 660, y: 40 },
        { key: 'gate',   comp: 'decision-inbox',  label: 'HUMAN GATE',   x: 990, y: 40 }
      ],
      edges: [
        { from: 'parser', to: 'dedup', label: 'payment events' },
        { from: 'dedup', to: 'fixer', label: 'unique payments' },
        { from: 'fixer', to: 'gate', label: 'mismatches' }
      ]
    },
    {
      id: 'pr-gauntlet',
      name: 'PR gauntlet',
      desc: 'Every change passes static review and a real-browser verdict before a human sees it.',
      flow: 'trigger → review → verify → gate',
      nodes: [
        { key: 'trigger',  comp: 'scheduler',       label: 'PR POLL',     x: 0,   y: 40 },
        { key: 'review',   comp: 'code-reviewer',   label: 'CODE REVIEW', x: 330, y: 40 },
        { key: 'verify',   comp: 'visual-verifier', label: 'VERIFY',      x: 660, y: 40 },
        { key: 'gate',     comp: 'decision-inbox',  label: 'HUMAN GATE',  x: 990, y: 40 }
      ],
      edges: [
        { from: 'trigger', to: 'review', label: 'new PRs' },
        { from: 'review', to: 'verify', label: 'passes static gates' },
        { from: 'verify', to: 'gate', label: 'VERIFIED + screenshot' }
      ]
    },
    {
      id: 'self-healing-ops',
      name: 'Self-healing ops',
      desc: 'Audit → fix → senior review → closure regrade, with bad closes looping back for rework.',
      flow: 'auditor → fixer → review ⇄ verifier',
      nodes: [
        { key: 'auditor',  comp: 'auditor-sweep',    label: 'AUDIT',        x: 0,   y: 150 },
        { key: 'fixer',    comp: 'executor-quick',   label: 'FIX',          x: 330, y: 150 },
        { key: 'senior',   comp: 'senior-reviewer',  label: 'SENIOR REVIEW', x: 660, y: 40 },
        { key: 'verifier', comp: 'closure-verifier', label: 'REGRADE',      x: 660, y: 260 }
      ],
      edges: [
        { from: 'auditor', to: 'fixer', label: 'files failures' },
        { from: 'fixer', to: 'senior', label: 'systemic patterns' },
        { from: 'fixer', to: 'verifier', label: 'closed tickets' },
        { from: 'verifier', to: 'fixer', label: 'reopens evidence-free closes' }
      ]
    },
    {
      id: 'research-spec-build',
      name: 'Research → Spec → Build',
      desc: 'Deep thinking first, a spec second, the build third — a human signs off at the end.',
      flow: 'thinker → planner → builder → gate',
      nodes: [
        { key: 'thinker', comp: 'deep-design',      label: 'RESEARCH',    x: 0,   y: 40 },
        { key: 'planner', comp: 'planner',          label: 'SPEC',        x: 330, y: 40 },
        { key: 'builder', comp: 'executor-product', label: 'BUILD',       x: 660, y: 40 },
        { key: 'gate',    comp: 'decision-inbox',   label: 'HUMAN GATE',  x: 990, y: 40 }
      ],
      edges: [
        { from: 'thinker', to: 'planner', label: 'architecture decisions' },
        { from: 'planner', to: 'builder', label: 'approved spec' },
        { from: 'builder', to: 'gate', label: 'ready for sign-off' }
      ]
    }
  ];

  return { TEMPLATES: TEMPLATES };
});
