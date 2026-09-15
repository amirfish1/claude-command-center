/* Pipeline Canvas — component registry (data-driven).
 *
 * Every component the library can place is ONE entry here. Adding a
 * component is adding an object to COMPONENT_REGISTRY — canvas.js renders
 * library cards, nodes, inspector fields, edge contracts, and port rules
 * entirely from this data, and tests/canvas-component-registry.test.cjs
 * validates the registry's integrity (anchor, contract, category, ports).
 *
 * Entry format:
 *   id        unique slug, persisted on designed nodes
 *   name      human name
 *   cat       category: sources | workers | gates | sinks | utilities
 *   letter    monogram glyph on the node icon (per-category unique)
 *   desc      one-line description for the library card
 *   anchor    the proven real-world implementation ("Pattern:" in inspector)
 *   pattern   how the anchor works, one line
 *   files     edge contract this component produces: {what, label?}
 *   consumes  edge contract this component expects in (free text, optional)
 *   config    inspector sketch fields: [{key, label, ph?, type?}] —
 *             sketch only; nothing here is ever written anywhere real
 *
 * Port rules are categorical: sources emit, sinks and gates terminate,
 * workers and utilities flow through.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CCC_CANVAS_COMPONENTS = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var CATEGORIES = {
    sources:   { name: 'Sources & streams', hue: 'var(--mint)',   ports: { inp: false, out: true } },
    workers:   { name: 'Workers',           hue: 'var(--accent)', ports: { inp: true,  out: true } },
    gates:     { name: 'Gates & review',    hue: 'var(--amber)',  ports: { inp: true,  out: false } },
    sinks:     { name: 'Sinks & outputs',   hue: 'var(--purple)', ports: { inp: true,  out: false } },
    utilities: { name: 'Utilities',         hue: 'var(--cyan)',   ports: { inp: true,  out: true } }
  };

  function workerConfig(engine, model, effort, workers) {
    return [
      { key: 'engine', label: 'Engine', ph: engine || 'claude' },
      { key: 'model', label: 'Model', ph: model || 'claude-sonnet-5' },
      { key: 'effort', label: 'Effort', ph: effort || 'high' },
      { key: 'desired_workers', label: 'Desired workers', type: 'number', ph: String(workers || 1) }
    ];
  }

  var COMPONENT_REGISTRY = [
    /* ── Sources & streams ─────────────────────────────────────────── */
    {
      id: 'posthog-watcher', name: 'PostHog session watcher', cat: 'sources', letter: 'P',
      desc: 'Watches product sessions, files deduped issues.',
      anchor: '~/Apps/stramp-posthog-watch',
      pattern: 'PostHog sessions → deduped GitHub issues on a schedule.',
      files: { what: 'deduped session issues', label: 'watchtower:STRAMP' },
      config: [{ key: 'cadence', label: 'Cadence', ph: '*/15 * * * *' },
               { key: 'max_per_run', label: 'Max issues / run', type: 'number', ph: '5' }]
    },
    {
      id: 'auditor-sweep', name: 'Conversation auditor sweep', cat: 'sources', letter: 'A',
      desc: 'Grades quiet conversations on a cron; files the failures.',
      anchor: 'BYM scripts/becky-auditor (15-min Vercel cron)',
      pattern: 'Every quiet conversation gets graded; went_bad / stuck_loop become issues.',
      files: { what: 'graded failures', label: 'watchtower:BECKY' },
      config: [{ key: 'cadence', label: 'Cadence', ph: '*/15 * * * *' },
               { key: 'grader', label: 'Grader model', ph: 'claude-sonnet-5' }]
    },
    {
      id: 'ops-digest', name: 'Studio ops digest', cat: 'sources', letter: 'D',
      desc: 'Scheduled ops rollup; each finding filed once.',
      anchor: '~/Apps/bym-studio-digest (twice daily)',
      pattern: 'Rollup runs twice a day; digest-finding-id markers dedupe filings.',
      files: { what: 'digest findings', label: 'watchtower:BYMOPS' },
      config: [{ key: 'cadence', label: 'Cadence', ph: '0 7,19 * * *' }]
    },
    {
      id: 'email-parsers', name: 'Email payment parser', cat: 'sources', letter: '@',
      desc: 'Payment emails become payment events.',
      anchor: 'BYM packages/email-parsers',
      pattern: 'Gmail / Venmo / PayPal emails parsed into payment events.',
      files: { what: 'payment events', label: 'payments' },
      config: [{ key: 'mailbox', label: 'Mailbox', ph: 'payments@studio.com' }]
    },
    {
      id: 'sms-webhook', name: 'Inbound SMS/WhatsApp', cat: 'sources', letter: 'S',
      desc: 'Inbound texts become conversation tickets.',
      anchor: 'BYM Twilio webhook routes',
      pattern: 'Twilio inbound webhook → normalized message → ticket.',
      files: { what: 'inbound messages' },
      config: [{ key: 'endpoint', label: 'Webhook path', ph: '/api/webhooks/twilio' }]
    },
    {
      id: 'booking-webhooks', name: 'Calendly/Stripe webhooks', cat: 'sources', letter: 'W',
      desc: 'Booking + payment events, pushed as they happen.',
      anchor: 'BYM webhook routes',
      pattern: 'Calendly invitee + Stripe payment webhooks → events.',
      files: { what: 'booking/payment events' },
      config: [{ key: 'endpoint', label: 'Webhook path', ph: '/api/webhooks/...' }]
    },
    {
      id: 'annotate-widget', name: '⚑ Annotate widget', cat: 'sources', letter: '⚑',
      desc: 'Click any element on a page → files it to a queue.',
      anchor: 'CCC add-annotate-widget skill',
      pattern: 'Drop-in ⚑ button; element + note lands as a ticket.',
      files: { what: 'element annotations', label: 'watchtower:CCC' },
      config: [{ key: 'queue', label: 'Target queue', ph: 'CCC' }]
    },
    {
      id: 'scheduler', name: 'Scheduler / trigger', cat: 'sources', letter: 'T',
      desc: 'The generic clock: launchd, systemd, cron, Vercel cron.',
      anchor: 'launchd / systemd timers / Vercel cron',
      pattern: 'Fires on a cadence; whatever it triggers is the pipeline.',
      files: { what: 'ticks' },
      config: [{ key: 'cadence', label: 'Cadence', ph: '*/30 * * * *' }]
    },

    /* ── Workers ───────────────────────────────────────────────────── */
    {
      id: 'planner', name: 'Planner', cat: 'workers', letter: 'P',
      desc: 'Design-only worker queue. Deliverable is a spec.',
      anchor: 'BECKY-DESIGN queue',
      pattern: 'Opus worker forbidden from implementing; the design ends at a wt block human gate.',
      files: { what: 'design specs' },
      consumes: 'build requests',
      config: workerConfig('claude', 'claude-opus-5', 'high', 1)
    },
    {
      id: 'deep-design', name: 'Deep-design thinker', cat: 'workers', letter: 'D',
      desc: 'Opus-grade architecture pass before anything gets built.',
      anchor: 'planner variant for architecture',
      pattern: 'Slow, expensive, worth it: trade-offs written down before code exists.',
      files: { what: 'architecture decisions' },
      consumes: 'hard problems',
      config: workerConfig('claude', 'claude-opus-5', 'high', 1)
    },
    {
      id: 'executor-quick', name: 'Quick fixer', cat: 'workers', letter: 'F',
      desc: 'Tactical claim-based worker for bugs and small fixes.',
      anchor: 'BECKY queue',
      pattern: 'Sonnet/flash worker draining a bug queue ticket by ticket.',
      files: { what: 'fixes' },
      consumes: 'bug tickets',
      config: workerConfig('claude', 'claude-sonnet-5', 'high', 2)
    },
    {
      id: 'executor-product', name: 'Product builder', cat: 'workers', letter: 'B',
      desc: 'Feature-build worker for designed, spec\u2019d work.',
      anchor: 'BYMPROD queue',
      pattern: 'Opus worker building features from approved specs.',
      files: { what: 'features' },
      consumes: 'approved specs',
      config: workerConfig('claude', 'claude-opus-5', 'high', 1)
    },
    {
      id: 'visual-verifier', name: 'Visual verifier', cat: 'workers', letter: 'V',
      desc: 'Drives a real browser; verdicts VERIFIED / WRONG-STATE.',
      anchor: 'skills/fleet-verify.md',
      pattern: 'Independent lane: loads the real page, screenshots it, verdicts with evidence.',
      files: { what: 'VERIFIED / WRONG-STATE + screenshot' },
      consumes: 'verification requests',
      config: workerConfig('claude', 'claude-sonnet-5', null, 1)
    },
    {
      id: 'code-reviewer', name: 'Pre-commit code reviewer', cat: 'workers', letter: 'C',
      desc: 'Static gates + auto-fix before anything lands.',
      anchor: '~/.hermes/skills/requesting-code-review',
      pattern: 'Review skill runs static gates, auto-fixes what it can, blocks the rest.',
      files: { what: 'review verdicts' },
      consumes: 'diffs',
      config: workerConfig('claude', 'claude-sonnet-5', null, 1)
    },
    {
      id: 'replay-simulator', name: 'Replay simulator', cat: 'workers', letter: 'R',
      desc: 'Replays the real engine before anyone claims "fixed".',
      anchor: 'becky-replay toolkits',
      pattern: 'The actual decision engine replays the failing conversation against the fix.',
      files: { what: 'replay receipts' },
      consumes: 'fix candidates',
      config: workerConfig('claude', 'claude-sonnet-5', null, 1)
    },
    {
      id: 'db-investigator', name: 'Data/DB investigator', cat: 'workers', letter: '?',
      desc: 'Read-only database truth-finding.',
      anchor: 'read-only psql probe pattern (BECKY playbook)',
      pattern: 'Read-only probes answer "what does the data actually say" — never writes.',
      files: { what: 'findings' },
      consumes: 'data questions',
      config: workerConfig('claude', 'claude-sonnet-5', null, 1)
    },
    {
      id: 'docs-writer', name: 'Docs/knowledge writer', cat: 'workers', letter: 'K',
      desc: 'Turns resolved work into playbooks and runbooks.',
      anchor: 'playbook/runbook update pattern',
      pattern: 'Every closed ticket is a lesson; the writer files it where the fleet reads it.',
      files: { what: 'docs updates' },
      consumes: 'resolved work',
      config: workerConfig('claude', 'claude-sonnet-5', null, 1)
    },
    {
      id: 'test-worker', name: 'Test/TDD worker', cat: 'workers', letter: '✓',
      desc: 'Red-green-refactor as a queue worker.',
      anchor: 'TDD loop pattern',
      pattern: 'Writes the failing test first, then the fix, then refactors under green.',
      files: { what: 'tested fixes' },
      consumes: 'bug tickets',
      config: workerConfig('claude', 'claude-sonnet-5', null, 1)
    },

    /* ── Gates & review ────────────────────────────────────────────── */
    {
      id: 'decision-inbox', name: 'Decision Inbox', cat: 'gates', letter: 'H',
      desc: 'Option cards parked for a human. The terminal gate.',
      anchor: 'ccc_server/decision_inbox.py',
      pattern: 'Stalled work becomes a card with options + one recommendation; the owner clicks.',
      consumes: 'blocked work, hard cases'
    },
    {
      id: 'product-gate', name: 'product_gate', cat: 'gates', letter: 'G',
      desc: 'Ack/Nack before finished work may close.',
      anchor: 'wt queue.py close enforcement',
      pattern: 'A queue with product_gate refuses to close until a human acks the result.',
      files: { what: 'acked work' },
      consumes: 'finished work',
      ports: { inp: true, out: true }
    },
    {
      id: 'wt-block', name: 'wt block', cat: 'gates', letter: '⏸',
      desc: 'Park-for-human end state on a ticket.',
      anchor: 'wt block / needs_input',
      pattern: 'The worker stops and the ticket waits for a human answer to resume.',
      consumes: 'questions'
    },
    {
      id: 'senior-reviewer', name: 'Senior reviewer', cat: 'gates', letter: 'S',
      desc: 'An LLM reviews the queues themselves, 4× a day.',
      anchor: 'bym-studio-digest src/senior-review.mjs',
      pattern: 'A senior pass over queue health; systemic problems escalate instead of festering.',
      files: { what: 'review findings' },
      consumes: 'queue state',
      ports: { inp: true, out: true }
    },
    {
      id: 'closure-verifier', name: 'Closure verifier', cat: 'gates', letter: 'V',
      desc: 'Weekly adversarial regrade of closed tickets.',
      anchor: 'verify-closures.mjs',
      pattern: 'Closes without evidence get reopened. Trust, but regrade.',
      files: { what: 'reopens' },
      consumes: 'closed tickets',
      ports: { inp: true, out: true }
    },

    /* ── Sinks & outputs ───────────────────────────────────────────── */
    {
      id: 'github-issue', name: 'GitHub issue', cat: 'sinks', letter: 'G',
      desc: 'The ticket itself; its label is its queue membership.',
      anchor: 'watchtower:<QUEUE> label convention',
      pattern: 'An issue labeled watchtower:BECKY IS a BECKY ticket — the label is the queue.',
      consumes: 'filings'
    },
    {
      id: 'email-digest', name: 'Email digest', cat: 'sinks', letter: 'M',
      desc: 'A human-readable rollup in the inbox.',
      anchor: 'Resend sender (bym-studio-digest)',
      pattern: 'The day\u2019s findings as one skimmable email, sent via Resend.',
      consumes: 'findings'
    },
    {
      id: 'sms-notify', name: 'SMS/WhatsApp notify', cat: 'sinks', letter: 'N',
      desc: 'A text when something needs eyes now.',
      anchor: 'BYM notification_logs',
      pattern: 'Non-blocking notify; failures logged, never fatal to the pipeline.',
      consumes: 'alerts'
    },
    {
      id: 'status-page', name: 'Dashboard / status page', cat: 'sinks', letter: 'D',
      desc: 'Live pipeline health on a page, shareable redacted.',
      anchor: '/superadmin/becky/pipeline + /becky-status',
      pattern: 'Both drain hosts heartbeat every 5 min; a stale heartbeat reads red on purpose.',
      consumes: 'heartbeats'
    },
    {
      id: 'commit-pr', name: 'Commit / PR', cat: 'sinks', letter: '⎇',
      desc: 'Explicit paths, lean commit, no push unless told.',
      anchor: 'lean-commit convention',
      pattern: 'Small, explicit-path commits on main; the subject is the narrative.',
      consumes: 'finished changes'
    },
    {
      id: 'deploy', name: 'Deploy', cat: 'sinks', letter: '▲',
      desc: 'Ship it — gated on a human push.',
      anchor: 'Vercel deploys',
      pattern: 'Deploys happen on an explicit push, never as a side effect.',
      consumes: 'merged work'
    },

    /* ── Utilities ─────────────────────────────────────────────────── */
    {
      id: 'deduplicator', name: 'Deduplicator', cat: 'utilities', letter: '≡',
      desc: 'Same signal twice files once.',
      anchor: 'digest-finding-id markers + findingId sha1',
      pattern: 'A stable fingerprint per finding; seen-before fingerprints never re-file.',
      files: { what: 'unique findings' },
      consumes: 'raw signals'
    },
    {
      id: 'budget-guard', name: 'Budget guard', cat: 'utilities', letter: '$',
      desc: 'Caps on filings, tokens, and dollars per run.',
      anchor: 'MAX_FILED_ISSUES_PER_RUN + SENIOR_REVIEW_* envs',
      pattern: 'Hard caps stop a broken producer from filing 500 tickets overnight.',
      files: { what: 'throttled flow' },
      consumes: 'anything expensive'
    },
    {
      id: 'rate-limiter', name: 'Rate limiter / backoff', cat: 'utilities', letter: '∿',
      desc: 'Retries with backoff when APIs push back.',
      anchor: 'phJson 429 retry + GraphQL quota handling',
      pattern: '429s and quota walls get exponential backoff, not a crash loop.',
      files: { what: 'paced calls' },
      consumes: 'API calls'
    },
    {
      id: 'queue-memory', name: 'Queue memory / learnings', cat: 'utilities', letter: 'M',
      desc: 'The per-queue brief every worker reads first.',
      anchor: '~/.watchtower/learnings/<QUEUE>.md',
      pattern: 'The 60-line brief: what this queue is for, what bites, what worked.',
      files: { what: 'context' },
      consumes: 'lessons'
    }
  ];

  var BY_ID = {};
  COMPONENT_REGISTRY.forEach(function (c) { BY_ID[c.id] = c; });

  /* Runtime archetypes → registry component (for the inspector's Pattern
   * section on live queue nodes). */
  var ARCHETYPE_COMPONENT = {
    planner: 'planner',
    executor: 'executor-quick',
    reviewer: 'visual-verifier',
    stream: 'scheduler',
    gate: 'decision-inbox'
  };
  var ARCHETYPE_CATEGORY = {
    planner: 'workers', executor: 'workers', reviewer: 'workers',
    stream: 'sources', gate: 'gates'
  };
  var ARCHETYPE_LETTER = {
    planner: 'P', executor: 'E', reviewer: 'R', stream: 'S', gate: 'H'
  };
  var ARCHETYPE_NAME = {
    planner: 'Planner', executor: 'Executor', reviewer: 'Visual reviewer',
    stream: 'Stream filer', gate: 'Human gate'
  };

  function portsFor(comp) {
    if (!comp) return CATEGORIES.workers.ports;
    if (comp.ports) return comp.ports;
    return (CATEGORIES[comp.cat] || CATEGORIES.workers).ports;
  }
  function canConnect(sourceComp, targetComp) {
    /* sourceComp/targetComp: registry entries or {cat} shims. */
    if (!sourceComp || !targetComp) return false;
    if (!portsFor(sourceComp).out) return false;
    if (!portsFor(targetComp).inp) return false;
    return true;
  }

  return {
    CATEGORIES: CATEGORIES,
    COMPONENT_REGISTRY: COMPONENT_REGISTRY,
    BY_ID: BY_ID,
    ARCHETYPE_COMPONENT: ARCHETYPE_COMPONENT,
    ARCHETYPE_CATEGORY: ARCHETYPE_CATEGORY,
    ARCHETYPE_LETTER: ARCHETYPE_LETTER,
    ARCHETYPE_NAME: ARCHETYPE_NAME,
    portsFor: portsFor,
    canConnect: canConnect
  };
});
