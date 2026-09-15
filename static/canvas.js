/* Pipeline Canvas — the fleet-topology node graph.
 *
 * Spec: docs/superpowers/specs/2026-09-15-pipeline-canvas-design.md.
 *
 * One canvas, two node populations:
 *   - runtime nodes: every WatchTower queue, rendered from /api/canvas/state
 *     (queue-config.json + live health). The canvas owns no queue facts.
 *   - designed nodes: archetype instances dragged from the library — design
 *     intent only ("not materialized"), persisted in the layout document.
 *
 * The layout document (positions, viewport, designed nodes, user edges) is
 * the only state this page owns; it saves to /api/canvas/layout (debounced).
 * This page never mutates a queue, a ticket, or wt.
 */
(function () {
  "use strict";

  /* ── constants ─────────────────────────────────────────────────────── */

  var GRID = 24;               // world px between grid dots
  var SNAP = 12;               // node drag snap (world px)
  var NODE_W = 232;
  var ZOOM_MIN = 0.15;
  var ZOOM_MAX = 3.0;
  var REFRESH_MS = 30000;
  var SAVE_DEBOUNCE_MS = 800;
  var HISTORY_CAP = 50;

  var GATE_ID = "gate:decision-inbox";

  var ARCH = {
    planner:  { letter: "P", name: "Planner",  desc: "Design-only worker queue. Deliverable is a spec; ends at a human gate." },
    executor: { letter: "E", name: "Executor", desc: "Claim-based drain queue. Engine + model per queue, reconciled staffing." },
    reviewer: { letter: "R", name: "Visual reviewer", desc: "Independent lane: drives a real browser, verdicts VERIFIED / WRONG-STATE." },
    stream:   { letter: "S", name: "Stream filer", desc: "Scheduled producer. Turns signals into deduped tickets." },
    gate:     { letter: "H", name: "Human gate", desc: "Option cards parked for a human. The Decision Inbox." }
  };

  var TEMPLATES = [
    {
      id: "feature-factory",
      name: "Feature factory",
      desc: "Design → build → independent visual review → human sign-off.",
      flow: "planner → executor → reviewer → gate",
      nodes: [
        { key: "planner",  archetype: "planner",  label: "DESIGN",        x: 0,   y: 40,
          config: { engine: "claude", model: "claude-opus-5", effort: "high", desired_workers: 1, auto_drain: true } },
        { key: "executor", archetype: "executor", label: "BUILD",         x: 330, y: 40,
          config: { engine: "claude", model: "claude-sonnet-5", effort: "high", desired_workers: 2, auto_drain: true } },
        { key: "reviewer", archetype: "reviewer", label: "VERIFY",        x: 660, y: 40,
          config: { engine: "claude", model: "claude-sonnet-5", desired_workers: 1, auto_drain: true } },
        { key: "gate",     archetype: "gate",     label: "HUMAN GATE",    x: 990, y: 40, config: {} }
      ],
      edges: [
        { from: "planner", to: "executor", label: "files builds to" },
        { from: "executor", to: "reviewer", label: "requests verification" },
        { from: "reviewer", to: "gate", label: "VERIFIED / WRONG-STATE" },
        { from: "planner", to: "gate", label: "design sign-off" }
      ]
    },
    {
      id: "posthog-watchdog",
      name: "PostHog watchdog",
      desc: "Session signals become deduped tickets; hard cases park for a human.",
      flow: "stream → executor → gate",
      nodes: [
        { key: "stream",   archetype: "stream",   label: "SESSION WATCH", x: 0,   y: 40, config: {} },
        { key: "executor", archetype: "executor", label: "TRIAGE",        x: 330, y: 40,
          config: { engine: "claude", model: "claude-sonnet-5", desired_workers: 1, auto_drain: true } },
        { key: "gate",     archetype: "gate",     label: "HUMAN GATE",    x: 660, y: 40, config: {} }
      ],
      edges: [
        { from: "stream", to: "executor", label: "files deduped issues" },
        { from: "executor", to: "gate", label: "hard cases" }
      ]
    },
    {
      id: "quality-loop",
      name: "The quality loop",
      desc: "An auditor sweep grades quiet conversations; bad grades become fixes; systemic ones escalate to a planner.",
      flow: "stream → executor → planner → gate",
      nodes: [
        { key: "stream",   archetype: "stream",   label: "AUDIT SWEEP",   x: 0,   y: 40, config: {} },
        { key: "executor", archetype: "executor", label: "FIX",           x: 330, y: 40,
          config: { engine: "claude", model: "claude-sonnet-5", desired_workers: 2, auto_drain: true } },
        { key: "planner",  archetype: "planner",  label: "REDESIGN",      x: 330, y: 260,
          config: { engine: "claude", model: "claude-opus-5", effort: "high", desired_workers: 1, auto_drain: true } },
        { key: "gate",     archetype: "gate",     label: "HUMAN GATE",    x: 660, y: 150, config: {} }
      ],
      edges: [
        { from: "stream", to: "executor", label: "files graded failures" },
        { from: "executor", to: "planner", label: "systemic issues" },
        { from: "planner", to: "gate", label: "redesign sign-off" },
        { from: "executor", to: "gate", label: "hard cases" }
      ]
    }
  ];

  /* ── dom helpers ───────────────────────────────────────────────────── */

  function $(id) { return document.getElementById(id); }
  var stage = $("pcStage");
  var world = $("pcWorld");
  var edgesSvg = $("pcEdges");
  var inspector = $("pcInspector");
  var inspectorBody = $("pcInspectorBody");
  var inspectorHead = $("pcInspectorHead");
  var library = $("pcLibrary");
  var emptyState = $("pcEmpty");
  var hint = $("pcHint");
  var toastEl = $("pcToast");
  var edgeTip = $("pcEdgeTip");
  var banner = $("pcBanner");
  var modalScrim = $("pcModalScrim");
  var minimapEl = $("pcMinimap");
  var minimapCanvas = $("pcMinimapCanvas");

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  var SVGNS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs) {
    var n = document.createElementNS(SVGNS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }
  var reducedMotion = false;
  try { reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) {}

  /* ── state ─────────────────────────────────────────────────────────── */

  // node: {id, kind ('queue'|'designed'|'gate'), archetype, label, x, y,
  //        data (runtime truth), config (designed sketch), el}
  var nodes = new Map();
  // edge: {id, source, target, kind ('convention'|'gate'|'user'), label, contract}
  var edges = new Map();
  var view = { x: 0, y: 0, zoom: 1 };
  var mode = "runtime";            // 'runtime' | 'design'
  var selection = null;            // {type:'node'|'edge', id}
  var history = { undo: [], redo: [] };
  var runtimeSeen = false;         // first state payload arrived
  var lastSyncAt = 0;
  var syncOk = false;
  var saveTimer = null;
  var refreshTimer = null;
  var syncTickTimer = null;
  var hintTimer = null;
  var designedSeq = 0;

  /* ── toast / hint ──────────────────────────────────────────────────── */

  var toastTimer = null;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove("is-visible"); }, 2600);
  }
  function showHint(html, ms) {
    hint.innerHTML = html;
    hint.classList.add("is-visible");
    clearTimeout(hintTimer);
    hintTimer = setTimeout(function () { hint.classList.remove("is-visible"); }, ms || 6000);
  }

  /* ── view transform ────────────────────────────────────────────────── */

  function applyView() {
    world.style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.zoom + ")";
    // Keep the dot grid glued to world space.
    var size = GRID * view.zoom;
    stage.style.backgroundSize = size + "px " + size + "px";
    stage.style.backgroundPosition = view.x + "px " + view.y + "px";
    $("pcZoomLevel").textContent = Math.round(view.zoom * 100) + "%";
    drawMinimap();
  }
  function toWorld(sx, sy) {
    var r = stage.getBoundingClientRect();
    return { x: (sx - r.left - view.x) / view.zoom, y: (sy - r.top - view.y) / view.zoom };
  }
  function clampView() {
    view.zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, view.zoom));
  }
  function zoomAt(sx, sy, factor) {
    var r = stage.getBoundingClientRect();
    var cx = sx - r.left, cy = sy - r.top;
    var wx = (cx - view.x) / view.zoom;
    var wy = (cy - view.y) / view.zoom;
    view.zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, view.zoom * factor));
    view.x = cx - wx * view.zoom;
    view.y = cy - wy * view.zoom;
    applyView();
    scheduleSave();
  }
  function zoomStep(factor) {
    var r = stage.getBoundingClientRect();
    zoomAt(r.left + r.width / 2, r.top + r.height / 2, factor);
  }
  function fitView() {
    if (!nodes.size) return;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodes.forEach(function (n) {
      var h = n.el ? n.el.offsetHeight : 110;
      minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x + NODE_W); maxY = Math.max(maxY, n.y + h);
    });
    var r = stage.getBoundingClientRect();
    var pad = 90;
    var zw = (r.width - pad * 2) / Math.max(1, maxX - minX);
    var zh = (r.height - pad * 2) / Math.max(1, maxY - minY);
    view.zoom = Math.min(1.25, Math.max(ZOOM_MIN, Math.min(zw, zh)));
    view.x = r.width / 2 - (minX + maxX) / 2 * view.zoom;
    view.y = r.height / 2 - (minY + maxY) / 2 * view.zoom;
    clampView();
    applyView();
    scheduleSave();
  }

  /* ── history (snapshot-based) ──────────────────────────────────────── */

  function snapshotLayout() {
    var designed = [];
    var positions = {};
    nodes.forEach(function (n) {
      positions[n.id] = { x: n.x, y: n.y };
      if (n.kind === "designed") {
        designed.push({ id: n.id, archetype: n.archetype, label: n.label,
                        config: n.config || {}, x: n.x, y: n.y });
      }
    });
    var userEdges = [];
    edges.forEach(function (e) {
      if (e.kind === "user") userEdges.push({ id: e.id, source: e.source, target: e.target, label: e.label || "" });
    });
    return { positions: positions, designed: designed, userEdges: userEdges };
  }
  function pushHistory() {
    history.undo.push(JSON.stringify(snapshotLayout()));
    if (history.undo.length > HISTORY_CAP) history.undo.shift();
    history.redo.length = 0;
  }
  function restoreSnapshot(json) {
    var snap;
    try { snap = JSON.parse(json); } catch (e) { return; }
    // Remove designed nodes and user edges not in the snapshot.
    var keepDesigned = {};
    snap.designed.forEach(function (d) { keepDesigned[d.id] = true; });
    Array.from(nodes.values()).forEach(function (n) {
      if (n.kind === "designed" && !keepDesigned[n.id]) removeNodeEl(n.id);
    });
    snap.designed.forEach(function (d) {
      var n = nodes.get(d.id);
      if (!n) {
        n = { id: d.id, kind: "designed", archetype: d.archetype,
              label: d.label, config: d.config || {}, x: d.x, y: d.y };
        addNodeEl(n);
      }
      n.archetype = d.archetype; n.label = d.label; n.config = d.config || {};
      n.x = d.x; n.y = d.y;
      placeNodeEl(n);
      fillNodeEl(n);
    });
    Array.from(edges.values()).forEach(function (e) {
      if (e.kind === "user") edges.delete(e.id);
    });
    snap.userEdges.forEach(function (e) { edges.set(e.id, e); });
    nodes.forEach(function (n) {
      var p = snap.positions[n.id];
      if (p && n.kind !== "designed") { n.x = p.x; n.y = p.y; placeNodeEl(n); }
    });
    clearSelection();
    renderEdges();
    refreshEmptyState();
    drawMinimap();
  }
  function undo() {
    if (!history.undo.length) { toast("Nothing to undo"); return; }
    history.redo.push(JSON.stringify(snapshotLayout()));
    restoreSnapshot(history.undo.pop());
    scheduleSave();
  }
  function redo() {
    if (!history.redo.length) { toast("Nothing to redo"); return; }
    history.undo.push(JSON.stringify(snapshotLayout()));
    restoreSnapshot(history.redo.pop());
    scheduleSave();
  }

  /* ── node model ────────────────────────────────────────────────────── */

  function healthOf(n) {
    if (n.kind === "gate") return { tone: "gate", label: "human gate" };
    if (n.kind === "designed") return { tone: "draft", label: "design intent" };
    var d = n.data || {};
    if (!d.configured) return { tone: "muted", label: "not configured" };
    if (d.stuck) return { tone: "bad", label: "stuck" };
    if (d.staffing_alarm) return { tone: "bad", label: "staffing alarm" };
    if (d.state === "draining") {
      if (d.workers > 0) return { tone: "ok", label: "draining · " + d.workers + (d.workers === 1 ? " worker" : " workers") };
      return { tone: "ok", label: "drain on" };
    }
    if (d.state === "backlog") return { tone: "warn", label: "backlog" };
    // Graveyard/idle queues (no open work, no workers) recede visually so
    // the live fleet pops. Still honest: the inspector shows everything.
    if ((d.depth || 0) === 0 && (d.workers || 0) === 0 && (d.in_progress || 0) === 0) {
      return { tone: "muted", label: d.auto_drain === false ? "quiet · drain off" : "quiet" };
    }
    if (d.auto_drain === false) return { tone: "parked", label: "drain off — parked by choice" };
    if (d.state) return { tone: "muted", label: d.state };
    return { tone: "muted", label: "idle" };
  }

  function shortModel(model) {
    if (!model) return "";
    return model.replace(/^claude-/, "");
  }

  function fmtAgo(seconds) {
    if (seconds == null) return "";
    if (seconds < 90) return "just now";
    if (seconds < 3600) return Math.round(seconds / 60) + "m ago";
    if (seconds < 86400) return Math.round(seconds / 3600) + "h ago";
    return Math.round(seconds / 86400) + "d ago";
  }

  /* ── node elements ─────────────────────────────────────────────────── */

  function addNodeEl(n) {
    var elNode = el("div", "pc-node");
    elNode.dataset.id = n.id;
    elNode.dataset.archetype = n.archetype;
    if (n.kind === "designed") elNode.classList.add("is-designed");
    elNode.style.width = NODE_W + "px";
    n.el = elNode;
    fillNodeEl(n);

    // Ports.
    var portIn = el("div", "pc-port pc-port-in");
    portIn.title = "Connect into " + n.label;
    var portOut = el("div", "pc-port pc-port-out");
    portOut.title = "Connect out of " + n.label;
    elNode.appendChild(portIn);
    elNode.appendChild(portOut);

    elNode.addEventListener("pointerdown", onNodePointerDown);
    portOut.addEventListener("pointerdown", function (e) { startEdgeDraw(e, n); });
    world.appendChild(elNode);
    placeNodeEl(n);
    return elNode;
  }

  function fillNodeEl(n) {
    var health = healthOf(n);
    n.el.dataset.health = health.tone;
    // Keep ports (last children) — rebuild only the content before them.
    while (n.el.firstChild && !n.el.firstChild.classList.contains("pc-port")) {
      n.el.removeChild(n.el.firstChild);
    }
    var portIn = n.el.querySelector(".pc-port-in");
    var frag = document.createDocumentFragment();

    var head = el("div", "pc-node-head");
    head.appendChild(el("span", "pc-node-icon", (ARCH[n.archetype] || ARCH.executor).letter));
    var title = el("div", "pc-node-title");
    var nameEl = el("div", "pc-node-name", n.label);
    nameEl.title = n.label;
    title.appendChild(nameEl);
    title.appendChild(el("div", "pc-node-arch", (ARCH[n.archetype] || {}).name || n.archetype));
    head.appendChild(title);
    head.appendChild(el("span", "pc-status"));
    frag.appendChild(head);

    var body = el("div", "pc-node-body");

    if (n.kind === "designed") {
      body.appendChild(el("span", "pc-chip-draft", "not materialized"));
      var badges = el("div", "pc-badge-row");
      var c = n.config || {};
      if (c.engine) badges.appendChild(el("span", "pc-badge", c.engine + (c.model ? " · " + shortModel(c.model) : "")));
      if (c.desired_workers) badges.appendChild(el("span", "pc-badge pc-badge-dim", c.desired_workers + (c.desired_workers === 1 ? " worker" : " workers")));
      if (badges.childNodes.length) body.appendChild(badges);
    } else if (n.kind === "gate") {
      var gb = el("div", "pc-badge-row");
      gb.appendChild(el("span", "pc-badge", "Decision Inbox"));
      body.appendChild(gb);
      var gc = el("div", "pc-counts");
      var open = n.data && n.data.open_cards;
      gc.innerHTML = open != null ? "<b>" + esc(open) + "</b> open cards" : "parked decisions";
      body.appendChild(gc);
    } else {
      var d = n.data || {};
      var badgeRow = el("div", "pc-badge-row");
      if (d.engine) {
        badgeRow.appendChild(el("span", "pc-badge", d.engine + (d.model ? " · " + shortModel(d.model) : "")));
        if (d.engine_source && d.engine_source !== "queue") {
          badgeRow.appendChild(el("span", "pc-badge pc-badge-dim", "default"));
        }
      }
      if (badgeRow.childNodes.length) body.appendChild(badgeRow);

      var counts = el("div", "pc-counts");
      var parts = [];
      if (d.depth != null) parts.push("<b>" + esc(d.depth) + "</b> open");
      if (d.claimable) parts.push("<b>" + esc(d.claimable) + "</b> claimable");
      if (d.in_progress) parts.push("<b>" + esc(d.in_progress) + "</b> running");
      if (d.workers) parts.push("<b>" + esc(d.workers) + "</b> worker" + (d.workers === 1 ? "" : "s"));
      counts.innerHTML = parts.join(" · ") || "no tickets yet";
      body.appendChild(counts);

      if (d.total) {
        var prog = el("div", "pc-progress");
        var bar = document.createElement("i");
        bar.style.width = Math.min(100, Math.round(100 * (d.closed || 0) / d.total)) + "%";
        prog.appendChild(bar);
        prog.title = (d.closed || 0) + " closed of " + d.total;
        body.appendChild(prog);
      }
    }

    var stateRow = el("div", "pc-node-state", health.label);
    if (n.kind === "queue" && n.data && n.data.last_activity_seconds != null) {
      stateRow.textContent = health.label + " · active " + fmtAgo(n.data.last_activity_seconds);
    }
    body.appendChild(stateRow);

    frag.appendChild(body);
    n.el.insertBefore(frag, portIn);
  }

  function placeNodeEl(n) {
    n.el.style.left = n.x + "px";
    n.el.style.top = n.y + "px";
  }

  function removeNodeEl(id) {
    var n = nodes.get(id);
    if (!n) return;
    if (n.el && n.el.parentNode) n.el.parentNode.removeChild(n.el);
    nodes.delete(id);
    Array.from(edges.values()).forEach(function (e) {
      if (e.source === id || e.target === id) edges.delete(e.id);
    });
  }

  /* ── edges ─────────────────────────────────────────────────────────── */

  function edgePathD(e) {
    var s = nodes.get(e.source);
    var t = nodes.get(e.target);
    if (!s || !t) return null;
    var sh = s.el ? s.el.offsetHeight : 110;
    var th = t.el ? t.el.offsetHeight : 110;
    var x1 = s.x + NODE_W, y1 = s.y + sh / 2;
    var x2 = t.x, y2 = t.y + th / 2;
    // If the target sits behind the source, route with wider handles.
    var dx = Math.max(60, Math.abs(x2 - x1) * 0.45);
    return "M " + x1 + " " + y1 +
           " C " + (x1 + dx) + " " + y1 + ", " + (x2 - dx) + " " + y2 + ", " + x2 + " " + y2;
  }

  function renderEdges() {
    edgesSvg.textContent = "";
    edges.forEach(function (e) {
      var d = edgePathD(e);
      if (!d) return;
      var g = svgEl("g", { "class": "pc-edge-group is-" + e.kind, "data-edge": e.id });
      var hit = svgEl("path", { "class": "pc-edge-hit", d: d });
      hit.addEventListener("pointerdown", function (ev) {
        ev.stopPropagation();
        if (e.kind === "user") selectEdge(e.id);
      });
      hit.addEventListener("mouseenter", function (ev) { showEdgeTip(e, ev); });
      hit.addEventListener("mousemove", function (ev) { moveEdgeTip(ev); });
      hit.addEventListener("mouseleave", hideEdgeTip);
      g.appendChild(hit);
      g.appendChild(svgEl("path", { "class": "pc-edge-line", d: d }));
      if (!reducedMotion) g.appendChild(svgEl("path", { "class": "pc-edge-flow", d: d }));
      if (selection && selection.type === "edge" && selection.id === e.id) {
        g.classList.add("is-selected");
      }
      edgesSvg.appendChild(g);
    });
  }

  function showEdgeTip(e, ev) {
    var html = "<div class='t'>" + esc(e.label || (e.kind === "user" ? "custom edge" : "convention")) + "</div>";
    if (e.contract) html += "<div class='c'>" + esc(e.contract) + "</div>";
    if (e.kind === "user") html += "<div class='c'>Design intent — no tickets flow until materialized.</div>";
    if (e.filing_label) html += "<span class='l'>" + esc(e.filing_label) + "</span>";
    edgeTip.innerHTML = html;
    moveEdgeTip(ev);
    edgeTip.classList.add("is-visible");
  }
  function moveEdgeTip(ev) {
    var x = Math.min(window.innerWidth - 300, ev.clientX + 14);
    var y = Math.min(window.innerHeight - 110, ev.clientY + 16);
    edgeTip.style.left = x + "px";
    edgeTip.style.top = y + "px";
  }
  function hideEdgeTip() { edgeTip.classList.remove("is-visible"); }

  /* ── selection + inspector ─────────────────────────────────────────── */

  function clearSelection() {
    if (selection && selection.type === "node") {
      var n = nodes.get(selection.id);
      if (n && n.el) n.el.classList.remove("is-selected");
    }
    selection = null;
    inspector.hidden = true;
    renderEdges();
  }
  function selectNode(id) {
    clearSelectionSilent();
    var n = nodes.get(id);
    if (!n) return;
    selection = { type: "node", id: id };
    n.el.classList.add("is-selected");
    renderInspector(n);
    renderEdges();
  }
  function selectEdge(id) {
    clearSelectionSilent();
    var e = edges.get(id);
    if (!e) return;
    selection = { type: "edge", id: id };
    inspector.hidden = true;
    renderEdges();
    showHint("Edge selected — <kbd>Delete</kbd> removes it", 3500);
  }
  function clearSelectionSilent() {
    if (selection && selection.type === "node") {
      var n = nodes.get(selection.id);
      if (n && n.el) n.el.classList.remove("is-selected");
    }
    selection = null;
  }

  function kv(k, v, cls) {
    var row = el("div", "pc-kv");
    row.appendChild(el("span", "k", k));
    var val = el("span", "v" + (cls ? " " + cls : ""), v);
    row.appendChild(val);
    return row;
  }
  function section(title) {
    var s = el("div", "pc-insp-section");
    s.appendChild(el("div", "pc-insp-h", title));
    return s;
  }

  function renderInspector(n) {
    inspectorHead.textContent = "Inspector";
    inspectorBody.textContent = "";
    var title = el("div", "pc-insp-title");
    title.appendChild(el("span", "pc-node-icon", (ARCH[n.archetype] || ARCH.executor).letter));
    title.firstChild.style.background = "var(--arch-" + n.archetype + ", var(--accent))";
    var tt = el("div");
    tt.appendChild(el("div", "pc-insp-name", n.label));
    tt.appendChild(el("div", "pc-insp-sub", ((ARCH[n.archetype] || {}).name || n.archetype) +
      (n.kind === "designed" ? " · design intent" : n.kind === "gate" ? " · human gate" : " · live queue")));
    title.appendChild(tt);
    inspectorBody.appendChild(title);

    if (n.kind === "gate") {
      var gs = section("The human end of every pipeline");
      gs.appendChild(kv("Open cards", n.data && n.data.open_cards != null ? String(n.data.open_cards) : "—"));
      var gl = el("div", "pc-insp-links");
      var ga = el("a", null, "Open Decision Inbox ↗");
      ga.href = "/decision-inbox.html";
      gl.appendChild(ga);
      gs.appendChild(gl);
      inspectorBody.appendChild(gs);
    } else if (n.kind === "designed") {
      renderDesignedInspector(n);
    } else {
      renderQueueInspector(n);
    }
    inspector.hidden = false;
  }

  function renderQueueInspector(n) {
    var d = n.data || {};
    var health = healthOf(n);

    var live = section("Live");
    var toneCls = health.tone === "ok" ? "pc-v-good" : health.tone === "bad" ? "pc-v-bad" : health.tone === "warn" || health.tone === "parked" ? "pc-v-warn" : "";
    live.appendChild(kv("State", health.label, toneCls));
    if (d.depth != null) live.appendChild(kv("Open", String(d.depth)));
    if (d.claimable != null) live.appendChild(kv("Claimable", String(d.claimable)));
    if (d.in_progress != null) live.appendChild(kv("In progress", String(d.in_progress)));
    if (d.total != null) live.appendChild(kv("Closed / total", (d.closed || 0) + " / " + d.total));
    if (d.workers != null) live.appendChild(kv("Workers live", d.workers + (d.effective_workers != null ? " (" + d.effective_workers + " effective)" : "")));
    if (d.last_activity_seconds != null) live.appendChild(kv("Last activity", fmtAgo(d.last_activity_seconds)));
    inspectorBody.appendChild(live);

    var cfg = section("Queue config (truth)");
    cfg.appendChild(kv("Engine", d.engine || "—"));
    cfg.appendChild(kv("Model", d.model || "engine default"));
    cfg.appendChild(kv("Effort", d.effort || "engine default"));
    cfg.appendChild(kv("Auto drain", d.auto_drain ? "on" : "off", d.auto_drain ? "pc-v-good" : "pc-v-warn"));
    cfg.appendChild(kv("Desired workers", String(d.desired_workers != null ? d.desired_workers : 1)));
    if (d.backend) cfg.appendChild(kv("Backend", d.backend));
    if (d.github_repo) cfg.appendChild(kv("GitHub repo", d.github_repo));
    if (d.repo_path) cfg.appendChild(kv("Repo path", d.repo_path));
    if (!d.configured) {
      var note = el("div", "pc-insp-note",
        "This queue has tickets but no queue-config.json entry — it renders, but the daemon does not staff it.");
      cfg.appendChild(note);
    }
    inspectorBody.appendChild(cfg);

    var links = el("div", "pc-insp-links");
    var q = el("a", null, "Open in Queues ↗");
    q.href = "/q2.html";
    links.appendChild(q);
    inspectorBody.appendChild(links);
  }

  function renderDesignedInspector(n) {
    var c = n.config || {};

    var fs = section("Design intent");
    var fLabel = el("div", "pc-field");
    var lLab = el("label", null, "Queue name");
    var lIn = el("input");
    lIn.type = "text";
    lIn.value = n.label;
    lIn.addEventListener("change", function () {
      pushHistory();
      n.label = lIn.value.trim().toUpperCase() || n.label;
      lIn.value = n.label;
      fillNodeEl(n);
      scheduleSave();
    });
    fLabel.appendChild(lLab); fLabel.appendChild(lIn);
    fs.appendChild(fLabel);

    function field(labelText, key, placeholder) {
      var f = el("div", "pc-field");
      var lab = el("label", null, labelText);
      var inp = el("input");
      inp.type = "text";
      inp.placeholder = placeholder || "";
      inp.value = c[key] || "";
      inp.addEventListener("change", function () {
        pushHistory();
        var v = inp.value.trim();
        if (v) c[key] = v; else delete c[key];
        n.config = c;
        fillNodeEl(n);
        scheduleSave();
      });
      f.appendChild(lab); f.appendChild(inp);
      return f;
    }
    if (n.archetype !== "stream" && n.archetype !== "gate") {
      fs.appendChild(field("Engine", "engine", "claude"));
      fs.appendChild(field("Model", "model", "claude-sonnet-5"));
      fs.appendChild(field("Effort", "effort", "high"));
      var wf = el("div", "pc-field");
      var wLab = el("label", null, "Desired workers");
      var wIn = el("input");
      wIn.type = "number"; wIn.min = "1"; wIn.max = "8";
      wIn.value = c.desired_workers || 1;
      wIn.addEventListener("change", function () {
        pushHistory();
        c.desired_workers = Math.max(1, Math.min(8, parseInt(wIn.value, 10) || 1));
        wIn.value = c.desired_workers;
        n.config = c;
        fillNodeEl(n);
        scheduleSave();
      });
      wf.appendChild(wLab); wf.appendChild(wIn);
      fs.appendChild(wf);
    }
    inspectorBody.appendChild(fs);

    if (n.archetype === "stream") {
      var sn = el("div", "pc-insp-note",
        "Stream filers are scheduled producers (launchd, cron) that file deduped tickets into a queue. They live outside queue-config — materializing one means scheduling the producer yourself.");
      inspectorBody.appendChild(sn);
    }

    var actions = el("div", "pc-insp-links");
    var del = el("button", "pc-btn pc-btn-danger", "Delete node");
    del.type = "button";
    del.addEventListener("click", function () {
      pushHistory();
      removeNodeEl(n.id);
      clearSelection();
      renderEdges();
      refreshEmptyState();
      scheduleSave();
      toast("Removed " + n.label);
    });
    actions.appendChild(del);
    inspectorBody.appendChild(actions);
  }

  /* ── interactions: pan / zoom ──────────────────────────────────────── */

  var spaceDown = false;
  var panState = null;

  stage.addEventListener("pointerdown", function (e) {
    if (e.target !== stage && e.target !== world && e.target !== edgesSvg) return;
    stage.focus();
    clearSelection();
    panState = { sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y, id: e.pointerId };
    try { stage.setPointerCapture(e.pointerId); } catch (err) {}
    stage.classList.add("is-panning");
  });
  stage.addEventListener("pointermove", function (e) {
    if (!panState) return;
    view.x = panState.vx + (e.clientX - panState.sx);
    view.y = panState.vy + (e.clientY - panState.sy);
    applyView();
  });
  function endPan(e) {
    if (!panState) return;
    panState = null;
    stage.classList.remove("is-panning");
    scheduleSave();
  }
  stage.addEventListener("pointerup", endPan);
  stage.addEventListener("pointercancel", endPan);

  stage.addEventListener("wheel", function (e) {
    e.preventDefault();
    // Trackpad pinch arrives as ctrlKey+wheel; plain wheel zooms too — the
    // n8n convention (scroll = zoom on canvas).
    var delta = e.ctrlKey ? -e.deltaY * 0.012 : -e.deltaY * 0.0016;
    zoomAt(e.clientX, e.clientY, Math.exp(delta));
  }, { passive: false });

  $("pcZoomIn").addEventListener("click", function () { zoomStep(1.25); });
  $("pcZoomOut").addEventListener("click", function () { zoomStep(0.8); });
  $("pcZoomFit").addEventListener("click", fitView);
  $("pcZoomLevel").addEventListener("click", function () {
    var r = stage.getBoundingClientRect();
    var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    zoomAt(cx, cy, 1 / view.zoom);
  });

  /* ── interactions: node drag ───────────────────────────────────────── */

  var dragState = null;

  function onNodePointerDown(e) {
    if (e.button !== 0) return;
    if (e.target.classList.contains("pc-port")) return;
    e.stopPropagation();
    var id = e.currentTarget.dataset.id;
    var n = nodes.get(id);
    if (!n) return;
    selectNode(id);
    var w = toWorld(e.clientX, e.clientY);
    dragState = { id: id, dx: w.x - n.x, dy: w.y - n.y, moved: false, pointerId: e.pointerId, startX: n.x, startY: n.y };
    try { n.el.setPointerCapture(e.pointerId); } catch (err) {}
    n.el.classList.add("is-dragging");
  }

  document.addEventListener("pointermove", function (e) {
    if (dragState) {
      var n = nodes.get(dragState.id);
      if (!n) { dragState = null; return; }
      var w = toWorld(e.clientX, e.clientY);
      var nx = Math.round((w.x - dragState.dx) / SNAP) * SNAP;
      var ny = Math.round((w.y - dragState.dy) / SNAP) * SNAP;
      if (nx !== n.x || ny !== n.y) {
        n.x = nx; n.y = ny;
        dragState.moved = true;
        placeNodeEl(n);
        renderEdges();
        drawMinimap();
      }
    }
    if (edgeDraw) updateEdgeDraw(e);
    if (paletteDrag) updatePaletteDrag(e);
  });
  document.addEventListener("pointerup", function (e) {
    if (dragState) {
      var n = nodes.get(dragState.id);
      if (n) n.el.classList.remove("is-dragging");
      if (dragState.moved) {
        pushHistoryAt(dragState.startX, dragState.startY);
        scheduleSave();
      }
      dragState = null;
    }
    if (edgeDraw) finishEdgeDraw(e);
    if (paletteDrag) finishPaletteDrag(e);
  });

  // For node moves we want the history entry to capture the PRE-move
  // position; simplest correct approach: capture a snapshot lazily at drag
  // start instead. We stash it here.
  var preDragSnapshot = null;
  function pushHistoryAt() {
    if (preDragSnapshot) {
      history.undo.push(preDragSnapshot);
      if (history.undo.length > HISTORY_CAP) history.undo.shift();
      history.redo.length = 0;
      preDragSnapshot = null;
    } else {
      pushHistory();
    }
  }
  document.addEventListener("pointerdown", function (e) {
    var nodeEl = e.target.closest && e.target.closest(".pc-node");
    if (nodeEl && !e.target.classList.contains("pc-port")) {
      preDragSnapshot = JSON.stringify(snapshotLayout());
    }
  }, true);

  /* ── interactions: edge drawing ────────────────────────────────────── */

  var edgeDraw = null; // {source, tempG, cur}

  function startEdgeDraw(e, n) {
    e.stopPropagation();
    e.preventDefault();
    var tempG = svgEl("g", { "class": "pc-edge-group is-pending" });
    tempG.appendChild(svgEl("path", { "class": "pc-edge-line", d: "M0 0" }));
    edgesSvg.appendChild(tempG);
    edgeDraw = { source: n.id, tempG: tempG };
  }
  function updateEdgeDraw(e) {
    var s = nodes.get(edgeDraw.source);
    if (!s) return;
    var w = toWorld(e.clientX, e.clientY);
    var fake = { source: edgeDraw.source, target: "__cursor__" };
    nodes.set("__cursor__", { id: "__cursor__", x: w.x, y: w.y - 1, el: null });
    var d = edgePathD(fake);
    nodes.delete("__cursor__");
    if (d) edgeDraw.tempG.firstChild.setAttribute("d", d);
    // Highlight the hovered drop target.
    nodes.forEach(function (n) { n.el.classList.remove("is-edge-target"); });
    var over = document.elementFromPoint(e.clientX, e.clientY);
    var overNode = over && over.closest ? over.closest(".pc-node") : null;
    if (overNode && overNode.dataset.id !== edgeDraw.source) {
      var tn = nodes.get(overNode.dataset.id);
      if (tn && tn.archetype !== "stream") tn.el.classList.add("is-edge-target");
    }
  }
  function finishEdgeDraw(e) {
    var draw = edgeDraw;
    edgeDraw = null;
    if (draw.tempG.parentNode) draw.tempG.parentNode.removeChild(draw.tempG);
    nodes.forEach(function (n) { n.el.classList.remove("is-edge-target"); });
    var over = document.elementFromPoint(e.clientX, e.clientY);
    var overNode = over && over.closest ? over.closest(".pc-node") : null;
    if (!overNode) return;
    var targetId = overNode.dataset.id;
    if (!targetId || targetId === draw.source) return;
    var tn = nodes.get(targetId);
    if (!tn || tn.archetype === "stream") return;
    // Dedupe: same pair already connected.
    var dup = false;
    edges.forEach(function (ed) {
      if (ed.source === draw.source && ed.target === targetId) dup = true;
    });
    if (dup) { toast("Those two are already connected"); return; }
    pushHistory();
    var id = "user:" + draw.source + "->" + targetId;
    edges.set(id, { id: id, source: draw.source, target: targetId, kind: "user", label: "files to" });
    renderEdges();
    scheduleSave();
    toast("Connected — edit the filing contract in a later step");
  }

  /* ── interactions: palette drag ────────────────────────────────────── */

  var paletteDrag = null; // {archetype, ghost}

  function startPaletteDrag(e, archetype) {
    e.preventDefault();
    var ghost = el("div", "pc-ghost");
    var card = el("div", "pc-node is-designed");
    card.dataset.archetype = archetype;
    card.style.width = NODE_W + "px";
    var head = el("div", "pc-node-head");
    head.appendChild(el("span", "pc-node-icon", ARCH[archetype].letter));
    var t = el("div", "pc-node-title");
    t.appendChild(el("div", "pc-node-name", ARCH[archetype].name));
    t.appendChild(el("div", "pc-node-arch", "drag onto canvas"));
    head.appendChild(t);
    card.appendChild(head);
    ghost.appendChild(card);
    document.body.appendChild(ghost);
    paletteDrag = { archetype: archetype, ghost: ghost };
    updatePaletteDrag(e);
  }
  function updatePaletteDrag(e) {
    paletteDrag.ghost.style.left = e.clientX + "px";
    paletteDrag.ghost.style.top = e.clientY + "px";
  }
  function finishPaletteDrag(e) {
    var drag = paletteDrag;
    paletteDrag = null;
    drag.ghost.remove();
    var r = stage.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) return;
    var w = toWorld(e.clientX, e.clientY);
    addDesignedNode(drag.archetype, null, w.x - NODE_W / 2, w.y - 30);
  }

  function addDesignedNode(archetype, label, x, y, config) {
    pushHistory();
    designedSeq += 1;
    var id = "designed:" + Date.now().toString(36) + designedSeq;
    var n = {
      id: id, kind: "designed", archetype: archetype,
      label: label || defaultDesignedLabel(archetype),
      config: config || defaultDesignedConfig(archetype),
      x: Math.round(x / SNAP) * SNAP,
      y: Math.round(y / SNAP) * SNAP
    };
    nodes.set(id, n);
    addNodeEl(n);
    if (!reducedMotion) n.el.classList.add("pc-enter");
    selectNode(id);
    renderEdges();
    refreshEmptyState();
    scheduleSave();
    drawMinimap();
    return n;
  }
  function defaultDesignedLabel(archetype) {
    var base = { planner: "PLANNER", executor: "EXECUTOR", reviewer: "REVIEWER", stream: "STREAM", gate: "HUMAN GATE" }[archetype] || "NODE";
    var taken = {};
    nodes.forEach(function (n) { taken[n.label] = true; });
    if (!taken[base]) return base;
    for (var i = 2; i < 50; i++) if (!taken[base + " " + i]) return base + " " + i;
    return base + " " + Date.now() % 100;
  }
  function defaultDesignedConfig(archetype) {
    if (archetype === "planner") return { engine: "claude", model: "claude-opus-5", effort: "high", desired_workers: 1, auto_drain: true };
    if (archetype === "executor") return { engine: "claude", model: "claude-sonnet-5", effort: "high", desired_workers: 1, auto_drain: true };
    if (archetype === "reviewer") return { engine: "claude", model: "claude-sonnet-5", desired_workers: 1, auto_drain: true };
    return {};
  }

  /* ── library + templates ───────────────────────────────────────────── */

  function buildLibrary() {
    var list = $("pcLibraryList");
    list.textContent = "";
    ["planner", "executor", "reviewer", "stream", "gate"].forEach(function (archetype) {
      var item = el("div", "pc-lib-item");
      item.dataset.archetype = archetype;
      item.appendChild(el("span", "pc-node-icon", ARCH[archetype].letter));
      var txt = el("div");
      txt.appendChild(el("div", "pc-lib-name", ARCH[archetype].name));
      txt.appendChild(el("div", "pc-lib-desc", ARCH[archetype].desc));
      item.appendChild(txt);
      item.addEventListener("pointerdown", function (e) { startPaletteDrag(e, archetype); });
      list.appendChild(item);
    });

    var tlist = $("pcTemplateList");
    tlist.textContent = "";
    TEMPLATES.forEach(function (t) {
      var card = el("button", "pc-template");
      card.type = "button";
      var name = el("div", "pc-template-name", "✦ " + t.name);
      card.appendChild(name);
      card.appendChild(el("div", "pc-template-desc", t.desc));
      card.appendChild(el("div", "pc-template-flow", t.flow));
      card.addEventListener("click", function () { applyTemplate(t); });
      tlist.appendChild(card);
    });
  }

  var lastTemplate = null;
  function applyTemplate(t) {
    pushHistory();
    // Place the cluster in free space BELOW everything already on the
    // canvas — a design should never land on top of the live fleet.
    var r = stage.getBoundingClientRect();
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    t.nodes.forEach(function (n) {
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
      minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
    });
    var floor = -Infinity, left = Infinity, right = -Infinity;
    nodes.forEach(function (n) {
      var nh = n.el ? n.el.offsetHeight : 110;
      floor = Math.max(floor, n.y + nh);
      left = Math.min(left, n.x);
      right = Math.max(right, n.x + NODE_W);
    });
    var offX, offY;
    if (floor === -Infinity) {
      // Empty canvas: center the cluster in the viewport.
      var center = toWorld(r.left + r.width / 2, r.top + r.height / 2);
      offX = center.x - (minX + maxX + NODE_W) / 2;
      offY = center.y - (minY + maxY + 120) / 2;
    } else {
      offX = (left + right) / 2 - (minX + maxX + NODE_W) / 2;
      offY = floor + 90 - minY;
    }
    var keyToId = {};
    t.nodes.forEach(function (spec) {
      var n = addDesignedNodeQuiet(spec.archetype, spec.label, spec.x + offX, spec.y + offY, spec.config);
      keyToId[spec.key] = n.id;
    });
    t.edges.forEach(function (e) {
      var id = "user:" + keyToId[e.from] + "->" + keyToId[e.to];
      edges.set(id, { id: id, source: keyToId[e.from], target: keyToId[e.to], kind: "user", label: e.label });
    });
    lastTemplate = { template: t, keyToId: keyToId };
    renderEdges();
    refreshEmptyState();
    scheduleSave();
    drawMinimap();
    fitView();
    banner.hidden = false;
    $("pcBannerText").textContent = "“" + t.name + "” laid out as design nodes — nothing real yet.";
    showHint("Drag nodes to arrange · <kbd>Delete</kbd> removes · <kbd>⌘Z</kbd> undoes", 6500);
  }
  function addDesignedNodeQuiet(archetype, label, x, y, config) {
    designedSeq += 1;
    var id = "designed:" + Date.now().toString(36) + designedSeq;
    var n = {
      id: id, kind: "designed", archetype: archetype,
      label: label || defaultDesignedLabel(archetype),
      config: JSON.parse(JSON.stringify(config || defaultDesignedConfig(archetype))),
      x: Math.round(x / SNAP) * SNAP,
      y: Math.round(y / SNAP) * SNAP
    };
    nodes.set(id, n);
    addNodeEl(n);
    if (!reducedMotion) n.el.classList.add("pc-enter");
    return n;
  }

  $("pcBannerDismiss").addEventListener("click", function () { banner.hidden = true; });
  $("pcBannerPreview").addEventListener("click", function () {
    if (lastTemplate) openMaterializeModal(lastTemplate);
  });

  /* ── materialization preview ───────────────────────────────────────── */

  function slugQueueName(label) {
    var s = String(label || "").trim().toUpperCase().replace(/[^A-Z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    return s || "NEW-QUEUE";
  }

  function materializationPreview(cluster) {
    var entries = {};
    var streams = [];
    cluster.template.nodes.forEach(function (spec) {
      if (spec.archetype === "gate") return; // the Decision Inbox already exists
      var name = slugQueueName(spec.label);
      if (spec.archetype === "stream") { streams.push(name); return; }
      var c = spec.config || {};
      var entry = { auto_drain: c.auto_drain !== false, repo_path: "/path/to/repo" };
      if (c.engine) entry.engine = c.engine;
      if (c.model) entry.model = c.model;
      if (c.effort) entry.effort = c.effort;
      entry.desired_workers = c.desired_workers || 1;
      entries[name] = entry;
    });
    return { entries: entries, streams: streams };
  }

  function openMaterializeModal(cluster) {
    var prev = materializationPreview(cluster);
    var body = $("pcModalBody");
    body.textContent = "";
    $("pcModalTitle").textContent = "Preview materialization — “" + cluster.template.name + "”";

    var note = el("p", "pc-modal-note");
    note.innerHTML = "This is exactly what would bring the design to life. " +
      "<b>v1 never writes it for you.</b> The WatchTower daemon reads " +
      "queue-config.json live — paste these entries there and it reconciles within a minute.";
    body.appendChild(note);

    body.appendChild(el("h3", null, "Add to queue-config.json (the daemon reads it live):"));
    body.appendChild(el("pre", null, JSON.stringify(prev.entries, null, 2)));

    var cmds = [];
    Object.keys(prev.entries).forEach(function (name) {
      cmds.push("wt ls -q " + name + "        # verify the queue registered");
    });
    if (prev.streams.length) {
      body.appendChild(el("h3", null, "Stream filers — scheduled producers (no queue entry):"));
      body.appendChild(el("pre", null,
        prev.streams.map(function (s) {
          return "# " + s + ": schedule the producer yourself (launchd/cron).\n" +
                 "# It files deduped tickets into its target queue on a cadence.";
        }).join("\n\n")));
    }
    body.appendChild(el("h3", null, "Then verify:"));
    body.appendChild(el("pre", null, cmds.join("\n") +
      "\n\n# Filing conventions the design implies:" +
      cluster.template.edges.map(function (e) {
        var from = slugQueueName((cluster.template.nodes.find(function (n) { return n.key === e.from; }) || {}).label);
        var to = slugQueueName((cluster.template.nodes.find(function (n) { return n.key === e.to; }) || {}).label);
        return "\n#   " + from + " --" + e.label + "--> " + to;
      }).join("")));

    modalScrim.hidden = false;
    modalScrim.dataset.preview = JSON.stringify(prev.entries, null, 2);
  }
  function closeModal() { modalScrim.hidden = true; }
  $("pcModalClose").addEventListener("click", closeModal);
  modalScrim.addEventListener("click", function (e) { if (e.target === modalScrim) closeModal(); });
  $("pcModalCopy").addEventListener("click", function () {
    var text = modalScrim.dataset.preview || "";
    function done() { toast("Config entries copied"); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { toast("Copy failed"); });
    } else {
      var ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); done(); } catch (e) { toast("Copy failed"); }
      ta.remove();
    }
  });

  /* ── minimap ───────────────────────────────────────────────────────── */

  function drawMinimap() {
    var dpr = window.devicePixelRatio || 1;
    var w = minimapEl.clientWidth, h = minimapEl.clientHeight;
    if (!w || !h) return;
    if (minimapCanvas.width !== w * dpr) { minimapCanvas.width = w * dpr; minimapCanvas.height = h * dpr; }
    var ctx = minimapCanvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!nodes.size) return;

    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodes.forEach(function (n) {
      var nh = n.el ? n.el.offsetHeight : 110;
      minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x + NODE_W); maxY = Math.max(maxY, n.y + nh);
    });
    // Include the current viewport in the world bounds so it's always visible.
    var r = stage.getBoundingClientRect();
    var vwx = -view.x / view.zoom, vwy = -view.y / view.zoom;
    var vww = r.width / view.zoom, vwh = r.height / view.zoom;
    minX = Math.min(minX, vwx); minY = Math.min(minY, vwy);
    maxX = Math.max(maxX, vwx + vww); maxY = Math.max(maxY, vwy + vwh);

    var pad = 10;
    var scale = Math.min((w - pad * 2) / Math.max(1, maxX - minX), (h - pad * 2) / Math.max(1, maxY - minY));
    var ox = pad + ((w - pad * 2) - (maxX - minX) * scale) / 2 - minX * scale;
    var oy = pad + ((h - pad * 2) - (maxY - minY) * scale) / 2 - minY * scale;
    minimapCanvas._map = { scale: scale, ox: ox, oy: oy };

    // Edges first (thin lines under node rects).
    ctx.strokeStyle = "rgba(90, 110, 135, 0.35)";
    ctx.lineWidth = 1;
    edges.forEach(function (e) {
      var s = nodes.get(e.source), t = nodes.get(e.target);
      if (!s || !t) return;
      ctx.beginPath();
      ctx.moveTo(s.x * scale + ox + NODE_W * scale, (s.y + 55) * scale + oy);
      ctx.lineTo(t.x * scale + ox, (t.y + 55) * scale + oy);
      ctx.stroke();
    });

    var colors = { ok: "#3fb950", bad: "#f85149", warn: "#d29922", parked: "#ffb340",
                   gate: "#ffb340", muted: "#4a5462", draft: "#7a6a9e" };
    nodes.forEach(function (n) {
      var tone = healthOf(n).tone;
      var nh = n.el ? n.el.offsetHeight : 110;
      ctx.fillStyle = colors[tone] || colors.muted;
      ctx.globalAlpha = n.kind === "designed" ? 0.55 : 0.9;
      var rx = n.x * scale + ox, ry = n.y * scale + oy;
      var rw = Math.max(3, NODE_W * scale), rh = Math.max(2, nh * scale);
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(rx, ry, rw, rh, 1.5); else ctx.rect(rx, ry, rw, rh);
      ctx.fill();
      ctx.globalAlpha = 1;
    });

    // Viewport rect.
    ctx.strokeStyle = "rgba(88, 166, 255, 0.85)";
    ctx.lineWidth = 1.2;
    ctx.strokeRect(vwx * scale + ox, vwy * scale + oy, vww * scale, vwh * scale);
  }

  var minimapDrag = false;
  function minimapJump(e) {
    var map = minimapCanvas._map;
    if (!map) return;
    var r = minimapCanvas.getBoundingClientRect();
    var wx = (e.clientX - r.left - map.ox) / map.scale;
    var wy = (e.clientY - r.top - map.oy) / map.scale;
    var sr = stage.getBoundingClientRect();
    view.x = sr.width / 2 - wx * view.zoom;
    view.y = sr.height / 2 - wy * view.zoom;
    applyView();
    scheduleSave();
  }
  minimapCanvas.addEventListener("pointerdown", function (e) {
    minimapDrag = true;
    minimapCanvas.setPointerCapture(e.pointerId);
    minimapJump(e);
  });
  minimapCanvas.addEventListener("pointermove", function (e) { if (minimapDrag) minimapJump(e); });
  minimapCanvas.addEventListener("pointerup", function () { minimapDrag = false; });

  /* ── modes ─────────────────────────────────────────────────────────── */

  function setMode(next) {
    mode = next;
    $("pcModeRuntime").classList.toggle("is-active", mode === "runtime");
    $("pcModeDesign").classList.toggle("is-active", mode === "design");
    $("pcModeRuntime").setAttribute("aria-selected", mode === "runtime" ? "true" : "false");
    $("pcModeDesign").setAttribute("aria-selected", mode === "design" ? "true" : "false");
    library.hidden = mode !== "design";
    if (mode === "design") {
      showHint("Drag a component onto the canvas — or lay out a template in one click", 7000);
    }
  }
  $("pcModeRuntime").addEventListener("click", function () { setMode("runtime"); });
  $("pcModeDesign").addEventListener("click", function () { setMode("design"); });

  /* ── empty state ───────────────────────────────────────────────────── */

  function refreshEmptyState() {
    emptyState.hidden = nodes.size > 0;
  }

  /* ── data: runtime state ───────────────────────────────────────────── */

  function setSync(ok, when) {
    syncOk = ok;
    if (when) lastSyncAt = when;
    var sync = $("pcSync");
    sync.classList.toggle("is-stale", !ok);
    tickSyncText();
  }
  function tickSyncText() {
    var txt = $("pcSyncText");
    if (!syncOk && !lastSyncAt) { txt.textContent = "connecting…"; return; }
    if (!syncOk) { txt.textContent = "offline — retrying"; return; }
    var s = Math.max(0, Math.round((Date.now() - lastSyncAt) / 1000));
    txt.textContent = s < 5 ? "live · just updated" : "live · updated " + s + "s ago";
  }

  // Auto-layout: runtime nodes without a saved position flow into a grid —
  // planners, then executors, then reviewers (sorted by name inside each
  // lane), LAYOUT_ROWS per column; the gate gets its own column at the far
  // right. New nodes appearing later append after the highest used slot.
  var LAYOUT_COL_W = NODE_W + 68;
  var LAYOUT_ROW_H = 170;
  var LAYOUT_ROWS = 8;
  var LAYOUT_ORIGIN = { x: 60, y: 60 };
  var gridSlotsUsed = 0;

  function archRank(n) {
    return { planner: 0, executor: 1, reviewer: 2, stream: 3, gate: 4 }[n.archetype] != null
      ? { planner: 0, executor: 1, reviewer: 2, stream: 3, gate: 4 }[n.archetype] : 1;
  }
  function gridPos(slot) {
    return {
      x: LAYOUT_ORIGIN.x + Math.floor(slot / LAYOUT_ROWS) * LAYOUT_COL_W,
      y: LAYOUT_ORIGIN.y + (slot % LAYOUT_ROWS) * LAYOUT_ROW_H
    };
  }
  function gridLayout(needPlace) {
    needPlace.sort(function (a, b) {
      var r = archRank(a) - archRank(b);
      return r !== 0 ? r : (a.label < b.label ? -1 : a.label > b.label ? 1 : 0);
    });
    var regular = needPlace.filter(function (n) { return n.kind !== "gate"; });
    var gates = needPlace.filter(function (n) { return n.kind === "gate"; });
    regular.forEach(function (n, i) {
      var p = gridPos(gridSlotsUsed + i);
      n.x = p.x; n.y = p.y;
      n._grid = true;
      placeNodeEl(n);
    });
    gridSlotsUsed += regular.length;
    // Gate: its own column right of everything placed so far, top row.
    gates.forEach(function (n, i) {
      var col = Math.max(1, Math.ceil(gridSlotsUsed / LAYOUT_ROWS)) + i;
      n.x = LAYOUT_ORIGIN.x + col * LAYOUT_COL_W;
      n.y = LAYOUT_ORIGIN.y;
      n._grid = true;
      placeNodeEl(n);
    });
  }

  function applyRuntimeState(payload) {
    var incoming = payload.nodes || [];
    var seen = {};
    var needPlace = [];
    incoming.forEach(function (d) {
      seen[d.id] = true;
      var n = nodes.get(d.id);
      if (!n) {
        n = {
          id: d.id,
          kind: d.kind === "gate" ? "gate" : "queue",
          archetype: d.archetype || "executor",
          label: d.kind === "gate" ? (d.label || "Decision Inbox") : d.queue,
          x: 0, y: 0
        };
        nodes.set(d.id, n);
        addNodeEl(n);
        var saved = savedPositions[d.id];
        if (saved && typeof saved.x === "number" && typeof saved.y === "number") {
          n.x = saved.x; n.y = saved.y;
          placeNodeEl(n);
        } else {
          needPlace.push(n);
        }
        if (!reducedMotion && runtimeSeen) n.el.classList.add("pc-enter");
      }
      n.data = d;
      if (n.kind === "gate") n.label = d.label || "Decision Inbox";
      fillNodeEl(n);
    });
    if (needPlace.length) gridLayout(needPlace);
    // Queues that vanished from truth disappear from the canvas (their saved
    // position is simply unused if they ever return).
    Array.from(nodes.values()).forEach(function (n) {
      if ((n.kind === "queue" || n.kind === "gate") && !seen[n.id]) {
        removeNodeEl(n.id);
        if (selection && selection.type === "node" && selection.id === n.id) clearSelection();
      }
    });
    // Convention edges replace their kind wholesale; user edges survive.
    Array.from(edges.values()).forEach(function (e) {
      if (e.kind !== "user") edges.delete(e.id);
    });
    (payload.edges || []).forEach(function (e) {
      edges.set(e.id, e);
    });
    renderEdges();
    refreshEmptyState();
    drawMinimap();
    if (selection && selection.type === "node") {
      var sel = nodes.get(selection.id);
      if (sel && sel.kind === "queue") renderInspector(sel);
    }
  }

  function fetchState() {
    return fetch("/api/canvas/state")
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)); })
      .then(function (payload) {
        if (!payload || payload.ok === false) throw new Error((payload && payload.error) || "bad payload");
        applyRuntimeState(payload);
        runtimeSeen = true;
        setSync(true, Date.now());
      })
      .catch(function () { setSync(false); });
  }

  /* ── data: layout persistence ──────────────────────────────────────── */

  function layoutDocument() {
    var doc = { version: 1, nodes: {}, edges: [], viewport: { x: view.x, y: view.y, zoom: view.zoom } };
    nodes.forEach(function (n) {
      var entry = { x: n.x, y: n.y, kind: n.kind };
      if (n.kind === "designed") {
        entry.archetype = n.archetype;
        entry.label = n.label;
        if (n.config && Object.keys(n.config).length) entry.config = n.config;
      }
      doc.nodes[n.id] = entry;
    });
    edges.forEach(function (e) {
      if (e.kind === "user") doc.edges.push({ id: e.id, source: e.source, target: e.target, label: e.label || "" });
    });
    return doc;
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveLayout, SAVE_DEBOUNCE_MS);
  }
  function saveLayout() {
    var doc = layoutDocument();
    fetch("/api/canvas/layout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layout: doc })
    }).then(function (r) {
      if (!r.ok) toast("Layout save failed (" + r.status + ")");
    }).catch(function () { toast("Layout save failed — offline"); });
  }

  function loadLayout() {
    return fetch("/api/canvas/layout")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (payload) {
        var doc = payload && payload.layout;
        if (!doc) return;
        if (doc.viewport && typeof doc.viewport.x === "number") {
          view.x = doc.viewport.x; view.y = doc.viewport.y; view.zoom = doc.viewport.zoom || 1;
          clampView();
        }
        (doc.edges || []).forEach(function (e) {
          if (e && e.source && e.target) {
            edges.set(e.id || ("user:" + e.source + "->" + e.target),
                      { id: e.id || ("user:" + e.source + "->" + e.target),
                        source: e.source, target: e.target, kind: "user", label: e.label || "" });
          }
        });
        // Designed nodes come from the layout itself (they have no truth).
        Object.keys(doc.nodes || {}).forEach(function (id) {
          var p = doc.nodes[id];
          if (p && p.kind === "designed" && p.archetype) {
            var n = { id: id, kind: "designed", archetype: p.archetype,
                      label: p.label || "NODE", config: p.config || {},
                      x: p.x || 0, y: p.y || 0 };
            nodes.set(id, n);
            addNodeEl(n);
          }
        });
        // Queue/gate positions apply once state arrives.
        savedPositions = doc.nodes || {};
      })
      .catch(function () { /* offline: start from an empty layout */ });
  }
  var savedPositions = {};

  function applySavedPositions() {
    nodes.forEach(function (n) {
      if (n.kind === "designed") return;
      var p = savedPositions[n.id];
      if (p && typeof p.x === "number" && typeof p.y === "number") {
        n.x = p.x; n.y = p.y;
        placeNodeEl(n);
      }
    });
    renderEdges();
    drawMinimap();
  }

  /* ── keyboard ──────────────────────────────────────────────────────── */

  document.addEventListener("keydown", function (e) {
    var inField = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || "") || (e.target && e.target.isContentEditable);
    if (e.key === " " && !inField) {
      spaceDown = true;
      stage.classList.add("is-space-pan");
      e.preventDefault();
      return;
    }
    if (inField) return;
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && (e.key === "z" || e.key === "Z")) {
      e.preventDefault(); undo(); return;
    }
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === "z" || e.key === "Z")) {
      e.preventDefault(); redo(); return;
    }
    if (e.key === "Delete" || e.key === "Backspace") {
      if (!selection) return;
      e.preventDefault();
      if (selection.type === "edge") {
        var ed = edges.get(selection.id);
        if (ed && ed.kind === "user") {
          pushHistory();
          edges.delete(selection.id);
          clearSelection();
          renderEdges();
          scheduleSave();
          toast("Edge removed");
        } else {
          toast("Convention edges come from the fleet — they can't be deleted here");
        }
      } else if (selection.type === "node") {
        var n = nodes.get(selection.id);
        if (n && n.kind === "designed") {
          pushHistory();
          removeNodeEl(n.id);
          clearSelection();
          renderEdges();
          refreshEmptyState();
          scheduleSave();
          toast("Removed " + n.label);
        } else {
          toast("Runtime queues are truth — hide them in queue-config, not here");
        }
      }
      return;
    }
    if (e.key === "f" || e.key === "F") { fitView(); return; }
    if (e.key === "+" || e.key === "=") { zoomStep(1.25); return; }
    if (e.key === "-" || e.key === "_") { zoomStep(0.8); return; }
    if (e.key === "Escape") {
      if (!modalScrim.hidden) { closeModal(); return; }
      if (!banner.hidden) { banner.hidden = true; return; }
      clearSelection();
    }
  });
  document.addEventListener("keyup", function (e) {
    if (e.key === " ") {
      spaceDown = false;
      stage.classList.remove("is-space-pan");
    }
  });

  /* ── boot ──────────────────────────────────────────────────────────── */

  buildLibrary();
  refreshEmptyState();
  if (!reducedMotion) stage.classList.add("pc-enter");

  loadLayout().then(function () {
    return fetchState();
  }).then(function () {
    applySavedPositions();
    // First paint: honor a saved viewport; otherwise frame the fleet.
    var hasSavedViewport = savedPositions && Object.keys(savedPositions).length &&
                           view && (view.x !== 0 || view.y !== 0 || view.zoom !== 1);
    if (!hasSavedViewport && nodes.size) fitView(); else applyView();
    refreshEmptyState();
    if (nodes.size && !reducedMotion) {
      var i = 0;
      nodes.forEach(function (n) {
        n.el.classList.add("pc-enter");
        n.el.style.animationDelay = Math.min(i * 30, 750) + "ms";
        i += 1;
      });
      setTimeout(function () {
        nodes.forEach(function (n) { n.el.style.animationDelay = ""; n.el.classList.remove("pc-enter"); });
      }, 750 + 600);
    }
  });

  refreshTimer = setInterval(fetchState, REFRESH_MS);
  syncTickTimer = setInterval(tickSyncText, 5000);
})();
