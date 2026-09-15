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

  /* The component ecosystem is data-driven: static/canvas-components.js
   * (registry + categories + port rules) and static/canvas-templates.js
   * (gallery graphs) attach these globals. canvas.js only renders them. */
  var REG = window.CCC_CANVAS_COMPONENTS;
  var CATS = REG.CATEGORIES;
  var COMPONENTS = REG.COMPONENT_REGISTRY;
  var COMP_BY_ID = REG.BY_ID;
  var ARCH_COMP = REG.ARCHETYPE_COMPONENT;
  var ARCH_CAT = REG.ARCHETYPE_CATEGORY;
  var ARCH_LETTER = REG.ARCHETYPE_LETTER;
  var ARCH_NAME = REG.ARCHETYPE_NAME;
  var TEMPLATES = window.CCC_CANVAS_TEMPLATES.TEMPLATES;

  function compOf(n) {
    /* The registry entry for a designed node, or a {cat} shim for runtime. */
    if (n.kind === "designed" && n.component && COMP_BY_ID[n.component]) {
      return COMP_BY_ID[n.component];
    }
    return { cat: categoryOf(n) };
  }
  function categoryOf(n) {
    if (n.kind === "designed" && n.category && CATS[n.category]) return n.category;
    if (n.kind === "designed" && n.component && COMP_BY_ID[n.component]) return COMP_BY_ID[n.component].cat;
    return ARCH_CAT[n.archetype] || "workers";
  }
  function letterOf(n) {
    var c = compOf(n);
    if (c.letter) return c.letter;
    return ARCH_LETTER[n.archetype] || "E";
  }
  function kindNameOf(n) {
    var c = compOf(n);
    if (c.name) return c.name;
    return ARCH_NAME[n.archetype] || n.archetype;
  }

  /* TEMPLATES come from static/canvas-templates.js (bound above). */

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

  /* Runtime activity filter: show only queues with ticket/worker activity
   * in the last 7 days. Default ON; the toggle persists in localStorage.
   * Gate nodes are always visible; design nodes are never affected. */
  var ACTIVITY_WINDOW_S = 7 * 86400;
  var ACTIVITY_LS_KEY = "ccc-canvas-activity-filter";
  var activityFilterOn = true;
  try { activityFilterOn = localStorage.getItem(ACTIVITY_LS_KEY) !== "0"; } catch (e) {}

  function activityVisible(n) {
    if (n.kind !== "queue") return true; // gates + designed nodes always show
    var d = n.data || {};
    return d.last_activity_seconds != null && d.last_activity_seconds <= ACTIVITY_WINDOW_S;
  }
  function visibleNodes() {
    var out = [];
    nodes.forEach(function (n) {
      if (!activityFilterOn || activityVisible(n)) out.push(n);
    });
    return out;
  }
  function applyVisibility() {
    var visible = 0, total = 0;
    nodes.forEach(function (n) {
      if (n.kind === "queue") {
        total += 1;
        var show = !activityFilterOn || activityVisible(n);
        if (show) visible += 1;
        n.filteredOut = !show;
        n.el.classList.toggle("is-filtered-out", !show);
      } else {
        n.filteredOut = false;
      }
    });
    // A filtered-out node must not stay selected.
    if (selection && selection.type === "node") {
      var sel = nodes.get(selection.id);
      if (sel && sel.filteredOut) clearSelection();
    }
    var toggle = $("pcActivityToggle");
    var count = $("pcActivityCount");
    toggle.classList.toggle("is-off", !activityFilterOn);
    toggle.textContent = activityFilterOn ? "Active · 7d" : "All queues";
    toggle.title = activityFilterOn
      ? "Showing only queues with activity in the last 7 days — click to show all"
      : "Showing every queue — click to filter to the last 7 days";
    count.textContent = activityFilterOn
      ? visible + " of " + total + " queues"
      : total + " queues";
    renderEdges();
    drawMinimap();
  }

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
    var set = visibleNodes();
    if (!set.length) return;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    set.forEach(function (n) {
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
                        component: n.component, category: n.category,
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
              component: d.component || null, category: d.category || null,
              label: d.label, config: d.config || {}, x: d.x, y: d.y };
        addNodeEl(n);
      }
      n.archetype = d.archetype; n.label = d.label; n.config = d.config || {};
      n.component = d.component || null; n.category = d.category || null;
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
    maybeCelebrate(); // undo/redo of the completing edge re-arms the shimmer
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
    elNode.dataset.category = categoryOf(n);
    if (n.kind === "designed") elNode.classList.add("is-designed");
    elNode.style.width = NODE_W + "px";
    n.el = elNode;
    fillNodeEl(n);

    // Ports — visibility follows the component's port rules (sources emit
    // only, sinks/gates terminate, workers/utilities flow through).
    var ports = REG.portsFor(compOf(n));
    var portIn = el("div", "pc-port pc-port-in");
    portIn.title = "Connect into " + n.label;
    if (!ports.inp) portIn.classList.add("pc-port-off");
    var portOut = el("div", "pc-port pc-port-out");
    portOut.title = "Connect out of " + n.label;
    if (!ports.out) portOut.classList.add("pc-port-off");
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
    n.el.dataset.category = categoryOf(n);
    // Live queues with fresh activity breathe (transform/opacity only).
    var d0 = n.data || {};
    var isLive = n.kind === "queue" &&
      ((d0.in_progress || 0) > 0 || (d0.workers || 0) > 0 ||
       (d0.last_activity_seconds != null && d0.last_activity_seconds < 900));
    n.el.classList.toggle("is-live", isLive && !reducedMotion);
    // Keep ports (last children) — rebuild only the content before them.
    while (n.el.firstChild && !n.el.firstChild.classList.contains("pc-port")) {
      n.el.removeChild(n.el.firstChild);
    }
    var portIn = n.el.querySelector(".pc-port-in");
    var frag = document.createDocumentFragment();

    var head = el("div", "pc-node-head");
    head.appendChild(el("span", "pc-node-icon", letterOf(n)));
    var title = el("div", "pc-node-title");
    var nameEl = el("div", "pc-node-name", n.label);
    nameEl.title = n.label;
    title.appendChild(nameEl);
    title.appendChild(el("div", "pc-node-arch", kindNameOf(n)));
    head.appendChild(title);
    head.appendChild(el("span", "pc-status"));
    frag.appendChild(head);

    var body = el("div", "pc-node-body");

    if (n.kind === "designed") {
      body.appendChild(el("span", "pc-chip-draft", "not materialized"));
      var badges = el("div", "pc-badge-row");
      var c = n.config || {};
      if (c.engine) badges.appendChild(el("span", "pc-badge", c.engine + (c.model ? " · " + shortModel(c.model) : "")));
      else {
        // Non-worker components: show up to two sketch values as chips.
        Object.keys(c).slice(0, 2).forEach(function (k) {
          if (c[k] && typeof c[k] === "string") badges.appendChild(el("span", "pc-badge pc-badge-dim", c[k]));
        });
      }
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
    maybeCelebrate(); // re-evaluates; resets the chip signature if the path broke
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
    var gapX = x2 - x1;
    var spanY = Math.abs(y2 - y1);
    // Long-haul edges (spanning several columns or rows, target ahead) take a
    // raised arc that lifts over the grid instead of slicing through it —
    // reads as a deliberate flyover, not a routing bug. The lift is capped so
    // the arc clears the top row without leaving the neighbourhood.
    if (gapX > 520 || (gapX > 140 && spanY > 420)) {
      var lift = Math.min(260, 80 + Math.abs(gapX) * 0.10);
      var cy = Math.min(y1, y2) - lift;
      var dxA = Math.max(140, Math.abs(gapX) * 0.30);
      return "M " + x1 + " " + y1 +
             " C " + (x1 + dxA) + " " + cy + ", " + (x2 - dxA) + " " + cy + ", " + x2 + " " + y2;
    }
    // If the target sits behind the source, route with wider handles.
    var dx = Math.max(60, Math.abs(gapX) * 0.45);
    return "M " + x1 + " " + y1 +
           " C " + (x1 + dx) + " " + y1 + ", " + (x2 - dx) + " " + y2 + ", " + x2 + " " + y2;
  }

  function renderEdges() {
    // Keep the in-progress edge-draw group: a mid-draw re-render (the 30s
    // refresh, ⌘Z, Delete) must not detach the line under the cursor.
    var pending = null;
    Array.from(edgesSvg.childNodes).forEach(function (child) {
      if (child.classList && child.classList.contains("is-pending")) pending = child;
      else edgesSvg.removeChild(child);
    });
    edges.forEach(function (e) {
      // Edges render only when both endpoints are visible.
      var sn = nodes.get(e.source), tn2 = nodes.get(e.target);
      if ((sn && sn.filteredOut) || (tn2 && tn2.filteredOut)) return;
      var d = edgePathD(e);
      if (!d) return;
      var srcNode = nodes.get(e.source);
      var g = svgEl("g", { "class": "pc-edge-group is-" + e.kind, "data-edge": e.id });
      // User edges inherit the source component's category hue.
      if (e.kind === "user" && srcNode) {
        g.classList.add("is-cat-" + categoryOf(srcNode));
      }
      g.appendChild(svgEl("path", { "class": "pc-edge-line", d: d }));
      if (!reducedMotion) g.appendChild(svgEl("path", { "class": "pc-edge-flow", d: d }));
      // The fat invisible hit path goes LAST: the visible line/flow paint
      // under it, so their center pixels are not a dead hover/click zone.
      var hit = svgEl("path", { "class": "pc-edge-hit", d: d });
      hit.addEventListener("pointerdown", function (ev) {
        ev.stopPropagation();
        if (e.kind === "user") selectEdge(e.id);
      });
      hit.addEventListener("mouseenter", function (ev) { showEdgeTip(e, ev); });
      hit.addEventListener("mousemove", function (ev) { moveEdgeTip(ev); });
      hit.addEventListener("mouseleave", hideEdgeTip);
      g.appendChild(hit);
      if (selection && selection.type === "edge" && selection.id === e.id) {
        g.classList.add("is-selected");
      }
      edgesSvg.insertBefore(g, pending); // the in-progress draw rides on top
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

  function patternSection(comp) {
    /* "Pattern:" — the proven real-world implementation this component is
     * modeled on, so a designed graph teaches the working fleet. */
    if (!comp || !comp.anchor) return null;
    var ps = section("Pattern");
    var pat = el("div", "pc-insp-pattern");
    pat.appendChild(el("div", "pc-insp-pattern-text", comp.pattern));
    var anc = el("div", "pc-insp-anchor", "⚓ " + comp.anchor);
    pat.appendChild(anc);
    ps.appendChild(pat);
    return ps;
  }

  function renderInspector(n) {
    inspectorHead.textContent = "Inspector";
    inspectorBody.textContent = "";
    var cat = categoryOf(n);
    var title = el("div", "pc-insp-title");
    title.appendChild(el("span", "pc-node-icon", letterOf(n)));
    title.firstChild.style.background = CATS[cat].hue;
    var tt = el("div");
    tt.appendChild(el("div", "pc-insp-name", n.label));
    tt.appendChild(el("div", "pc-insp-sub", kindNameOf(n) +
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
    cfg.appendChild(kv("Effort",
      d.effort ? d.effort + (d.effort_source && d.effort_source !== "queue" ? " (default)" : "") : "engine default"));
    cfg.appendChild(kv("Auto drain", d.auto_drain ? "on" : "off", d.auto_drain ? "pc-v-good" : "pc-v-warn"));
    cfg.appendChild(kv("Desired workers",
      String(d.desired_workers != null ? d.desired_workers : 1) +
      (d.desired_workers_source === "default" ? " (default)" : "")));
    if (d.backend) cfg.appendChild(kv("Backend", d.backend));
    if (d.github_repo) cfg.appendChild(kv("GitHub repo", d.github_repo));
    if (d.repo_path) cfg.appendChild(kv("Repo path", d.repo_path));
    if (!d.configured) {
      var note = el("div", "pc-insp-note",
        "This queue has tickets but no queue-config.json entry — it renders, but the daemon does not staff it.");
      cfg.appendChild(note);
    }
    inspectorBody.appendChild(cfg);

    var pattern = patternSection(COMP_BY_ID[ARCH_COMP[n.archetype]]);
    if (pattern) inspectorBody.appendChild(pattern);

    var links = el("div", "pc-insp-links");
    var q = el("a", null, "Open in Queues ↗");
    q.href = "/q2.html";
    links.appendChild(q);
    inspectorBody.appendChild(links);
  }

  function renderDesignedInspector(n) {
    var c = n.config || {};
    var comp = compOf(n);

    var pattern = patternSection(comp);
    if (pattern) inspectorBody.appendChild(pattern);

    if ((comp.files && comp.files.what) || comp.consumes) {
      var es = section("Edge contract");
      if (comp.files && comp.files.what) es.appendChild(kv("Files", comp.files.what));
      if (comp.files && comp.files.label) es.appendChild(kv("Label", comp.files.label));
      if (comp.consumes) es.appendChild(kv("Consumes", comp.consumes));
      inspectorBody.appendChild(es);
    }

    var fs = section("Config sketch");
    var fLabel = el("div", "pc-field");
    var lLab = el("label", null, "Name");
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

    (comp.config || []).forEach(function (spec) {
      var f = el("div", "pc-field");
      var lab = el("label", null, spec.label);
      var inp = el("input");
      inp.type = spec.type === "number" ? "number" : "text";
      if (spec.type === "number") { inp.min = "1"; inp.max = "32"; }
      inp.placeholder = spec.ph || "";
      inp.value = c[spec.key] != null ? c[spec.key] : "";
      inp.addEventListener("change", function () {
        pushHistory();
        var v = inp.value.trim();
        if (spec.type === "number") {
          var num = Math.max(1, Math.min(32, parseInt(v, 10) || 0));
          if (num) c[spec.key] = num; else delete c[spec.key];
          inp.value = c[spec.key] != null ? c[spec.key] : "";
        } else {
          if (v) c[spec.key] = v; else delete c[spec.key];
        }
        n.config = c;
        fillNodeEl(n);
        scheduleSave();
      });
      f.appendChild(lab); f.appendChild(inp);
      fs.appendChild(f);
    });
    inspectorBody.appendChild(fs);

    var note = el("div", "pc-insp-note",
      "A sketch, not a write: nothing here touches queue-config, wt, or any schedule. " +
      "Materialization stays a preview you apply yourself.");
    inspectorBody.appendChild(note);

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
  // pointercancel = aborted gesture (OS interrupt, touch→scroll steal):
  // clean up WITHOUT committing — no node move history, no edge, no node.
  function cancelInteractions() {
    if (dragState) {
      var n = nodes.get(dragState.id);
      if (n) {
        n.el.classList.remove("is-dragging");
        if (dragState.moved) { // snap back to the pre-drag slot
          n.x = dragState.startX; n.y = dragState.startY;
          placeNodeEl(n);
          renderEdges();
          drawMinimap();
        }
      }
      dragState = null;
    }
    if (edgeDraw) {
      if (edgeDraw.tempG.parentNode) edgeDraw.tempG.parentNode.removeChild(edgeDraw.tempG);
      edgeDraw = null;
      nodes.forEach(function (nd) {
        nd.el.classList.remove("is-edge-target");
        nd.el.classList.remove("is-edge-invalid");
      });
    }
    if (paletteDrag) {
      paletteDrag.ghost.remove();
      paletteDrag = null;
    }
    preDragSnapshot = null;
  }
  document.addEventListener("pointercancel", cancelInteractions);
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
    if (!REG.portsFor(compOf(n)).out) {
      toast(kindNameOf(n) + " is a terminal — nothing flows out of it");
      return;
    }
    var tempG = svgEl("g", { "class": "pc-edge-group is-pending" });
    tempG.appendChild(svgEl("path", { "class": "pc-edge-line", d: "M0 0" }));
    edgesSvg.appendChild(tempG);
    edgeDraw = { source: n.id, tempG: tempG, target: null };
  }
  function edgeTargetUnder(x, y) {
    var over = document.elementFromPoint(x, y);
    var overNode = over && over.closest ? over.closest(".pc-node") : null;
    if (!overNode) return null;
    var id = overNode.dataset.id;
    if (!id || id === edgeDraw.source) return null;
    return nodes.get(id) || null;
  }
  function updateEdgeDraw(e) {
    var s = nodes.get(edgeDraw.source);
    if (!s) return;
    var w = toWorld(e.clientX, e.clientY);
    // Magnetic snap: a valid target within range pulls the endpoint onto its
    // in-port and glows; an invalid one shakes red.
    var tn = edgeTargetUnder(e.clientX, e.clientY);
    var valid = tn ? REG.canConnect(compOf(s), compOf(tn)) : false;
    nodes.forEach(function (n) {
      n.el.classList.remove("is-edge-target");
      n.el.classList.remove("is-edge-invalid");
    });
    var end = w;
    edgeDraw.reason = null;
    if (tn) {
      var dup = edgeExists(s.id, tn.id);
      valid = valid && !dup;
      if (dup) edgeDraw.reason = "duplicate";
      else if (!valid) edgeDraw.reason = "invalid";
      tn.el.classList.add(valid ? "is-edge-target" : "is-edge-invalid");
      edgeDraw.target = valid ? tn.id : null;
      if (valid) {
        var th = tn.el ? tn.el.offsetHeight : 110;
        end = { x: tn.x, y: tn.y + th / 2 };
      }
    } else {
      edgeDraw.target = null;
    }
    var fake = { source: edgeDraw.source, target: "__cursor__" };
    nodes.set("__cursor__", { id: "__cursor__", x: end.x, y: end.y - 1, el: null });
    var d = edgePathD(fake);
    nodes.delete("__cursor__");
    if (d) edgeDraw.tempG.firstChild.setAttribute("d", d);
    edgeDraw.tempG.classList.toggle("is-invalid", !!(tn && !valid));
  }
  function edgeExists(sourceId, targetId) {
    var dup = false;
    edges.forEach(function (ed) {
      if (ed.source === sourceId && ed.target === targetId) dup = true;
    });
    return dup;
  }
  function finishEdgeDraw(e) {
    var draw = edgeDraw;
    edgeDraw = null;
    if (draw.tempG.parentNode) draw.tempG.parentNode.removeChild(draw.tempG);
    nodes.forEach(function (n) {
      n.el.classList.remove("is-edge-target");
      n.el.classList.remove("is-edge-invalid");
    });
    var s = nodes.get(draw.source);
    var tn = draw.target ? nodes.get(draw.target) : null;
    if (!s || !tn) {
      if (draw.reason === "duplicate") {
        toast("Those two are already connected");
      } else if (draw.reason === "invalid") {
        var over = edgeTargetUnder(e.clientX, e.clientY);
        toast("That connection doesn't make sense — " +
              (over && !REG.portsFor(compOf(over)).inp ? kindNameOf(over) + " takes no input" : "contract mismatch"));
      }
      return;
    }
    pushHistory();
    var id = "user:" + s.id + "->" + tn.id;
    // The filing contract comes from the source component's registry entry.
    var sc = compOf(s);
    var edge = { id: id, source: s.id, target: tn.id, kind: "user",
                 label: sc.files ? "files " + sc.files.what : "files to" };
    if (sc.files && sc.files.label) edge.filing_label = sc.files.label;
    edges.set(id, edge);
    renderEdges();
    pulseEdge(id);
    scheduleSave();
    maybeCelebrate();
    dismissCoach(true);
  }
  // A one-shot pulse traveling the freshly connected edge.
  function pulseEdge(edgeId) {
    if (reducedMotion) return;
    var e = edges.get(edgeId);
    var d = e && edgePathD(e);
    if (!d) return;
    var p = svgEl("path", { "class": "pc-edge-spark", d: d });
    edgesSvg.appendChild(p);
    setTimeout(function () { if (p.parentNode) p.parentNode.removeChild(p); }, 1400);
  }

  /* ── pipeline validation + celebration ─────────────────────────────── */

  // A designed graph is COMPLETE when a root (a designed node no user edge
  // points into — a source, or the first worker in line) reaches a terminal
  // (no-out-port: gate or sink) through user edges. Returns the path edges.
  var celebrateSig = "";
  function completePath() {
    var adj = {};
    var hasIncoming = {};
    edges.forEach(function (e) {
      if (e.kind !== "user") return;
      (adj[e.source] = adj[e.source] || []).push(e);
      hasIncoming[e.target] = true;
    });
    var starts = [], terminals = {};
    nodes.forEach(function (n) {
      if (n.kind !== "designed") return;
      if (!hasIncoming[n.id]) starts.push(n.id);
      if (!REG.portsFor(compOf(n)).out) terminals[n.id] = true;
    });
    if (!starts.length || !Object.keys(terminals).length) return null;
    // BFS keeping the edge trail.
    var queue = starts.map(function (id) { return { id: id, trail: [] }; });
    var seen = {};
    while (queue.length) {
      var cur = queue.shift();
      if (terminals[cur.id] && cur.trail.length) return cur.trail;
      if (seen[cur.id]) continue;
      seen[cur.id] = true;
      (adj[cur.id] || []).forEach(function (e) {
        queue.push({ id: e.target, trail: cur.trail.concat(e.id) });
      });
    }
    return null;
  }
  function maybeCelebrate(force) {
    var trail = completePath();
    var sig = trail ? trail.join("|") : "";
    if (!trail || (!force && sig === celebrateSig)) {
      if (!trail) celebrateSig = "";
      return;
    }
    celebrateSig = sig;
    if (reducedMotion) { toast("Pipeline complete ✓"); return; }
    // Shimmer along the path, edge by edge, then the chip.
    trail.forEach(function (edgeId, i) {
      setTimeout(function () { pulseEdge(edgeId); }, i * 180);
    });
    setTimeout(function () {
      var chip = $("pcComplete");
      chip.hidden = false;
      chip.classList.add("is-visible");
      setTimeout(function () {
        chip.classList.remove("is-visible");
        setTimeout(function () { chip.hidden = true; }, 400);
      }, 3200);
    }, trail.length * 180 + 150);
  }

  /* ── first-placement coach mark ────────────────────────────────────── */

  var coachEl = null;
  function maybeCoach(n) {
    try { if (localStorage.getItem("ccc-canvas-coached")) return; } catch (err) { return; }
    if (coachEl) return;
    coachEl = el("div", "pc-coach");
    coachEl.innerHTML = "<b>nice — now connect it to something</b>" +
      "<span>drag from the dot on its right edge into another node</span>";
    coachEl.addEventListener("click", function () { dismissCoach(false); });
    stage.appendChild(coachEl);
    positionCoach(n);
    setTimeout(function () { dismissCoach(false); }, 14000);
  }
  function positionCoach(n) {
    if (!coachEl) return;
    var sx = (n.x + NODE_W) * view.zoom + view.x;
    var sy = n.y * view.zoom + view.y;
    coachEl.style.left = Math.min(stage.clientWidth - 260, Math.max(10, sx + 16)) + "px";
    coachEl.style.top = Math.max(10, sy - 8) + "px";
  }
  function dismissCoach(permanent) {
    if (!coachEl) return;
    coachEl.remove();
    coachEl = null;
    if (permanent) {
      try { localStorage.setItem("ccc-canvas-coached", "1"); } catch (err) {}
    }
  }

  /* ── interactions: palette drag ────────────────────────────────────── */

  var paletteDrag = null; // {compId, ghost}

  function startPaletteDrag(e, compId) {
    e.preventDefault();
    var comp = COMP_BY_ID[compId];
    if (!comp) return;
    var ghost = el("div", "pc-ghost");
    var card = el("div", "pc-node is-designed");
    card.dataset.category = comp.cat;
    card.style.width = NODE_W + "px";
    var head = el("div", "pc-node-head");
    head.appendChild(el("span", "pc-node-icon", comp.letter));
    var t = el("div", "pc-node-title");
    t.appendChild(el("div", "pc-node-name", comp.name));
    t.appendChild(el("div", "pc-node-arch", "drag onto canvas"));
    head.appendChild(t);
    card.appendChild(head);
    ghost.appendChild(card);
    document.body.appendChild(ghost);
    paletteDrag = { compId: compId, ghost: ghost };
    updatePaletteDrag(e);
  }
  function updatePaletteDrag(e) {
    paletteDrag.ghost.style.left = e.clientX + "px";
    paletteDrag.ghost.style.top = e.clientY + "px";
  }
  // Nearest free grid slot: a drop that lands on an existing node cascades
  // right, then down, until it finds open space.
  function resolveDropCollision(x, y) {
    function overlaps(px, py) {
      var hit = false;
      nodes.forEach(function (n) {
        var nh = n.el ? n.el.offsetHeight : 110;
        if (px < n.x + NODE_W + 12 && px + NODE_W + 12 > n.x &&
            py < n.y + nh + 12 && py + nh + 12 > n.y) hit = true;
      });
      return hit;
    }
    var px = Math.round(x / SNAP) * SNAP;
    var py = Math.round(y / SNAP) * SNAP;
    for (var i = 0; i < 80 && overlaps(px, py); i++) {
      px += NODE_W + 36;
      if (i % 6 === 5) { px = Math.round(x / SNAP) * SNAP; py += 150; }
    }
    return { x: px, y: py };
  }

  function finishPaletteDrag(e) {
    var drag = paletteDrag;
    paletteDrag = null;
    drag.ghost.remove();
    var r = stage.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) return;
    var w = toWorld(e.clientX, e.clientY);
    var spot = resolveDropCollision(w.x - NODE_W / 2, w.y - 30);
    addDesignedNode(drag.compId, null, spot.x, spot.y);
  }

  function addDesignedNode(compId, label, x, y, config) {
    pushHistory();
    var n = makeDesignedNode(compId, label, x, y, config);
    addNodeEl(n);
    if (!reducedMotion) n.el.classList.add("pc-enter");
    selectNode(n.id);
    renderEdges();
    refreshEmptyState();
    scheduleSave();
    drawMinimap();
    maybeCoach(n);
    maybeCelebrate();
    return n;
  }
  function makeDesignedNode(compId, label, x, y, config) {
    designedSeq += 1;
    var comp = COMP_BY_ID[compId] || { cat: "workers", name: "Worker", letter: "E" };
    var n = {
      id: "designed:" + Date.now().toString(36) + designedSeq,
      kind: "designed",
      component: compId,
      category: comp.cat,
      // Keep archetype for backward compatibility with older layouts.
      archetype: { sources: "stream", gates: "gate", workers: "executor", sinks: "executor", utilities: "executor" }[comp.cat],
      label: label || defaultDesignedLabel(comp),
      config: config || defaultDesignedConfig(comp),
      x: Math.round(x / SNAP) * SNAP,
      y: Math.round(y / SNAP) * SNAP
    };
    nodes.set(n.id, n);
    return n;
  }
  function defaultDesignedLabel(comp) {
    var base = (comp.name || "NODE").toUpperCase().replace(/[^A-Z0-9 ⚑]+/g, "").trim() || "NODE";
    var taken = {};
    nodes.forEach(function (n) { taken[n.label] = true; });
    if (!taken[base]) return base;
    for (var i = 2; i < 50; i++) if (!taken[base + " " + i]) return base + " " + i;
    return base + " " + Date.now() % 100;
  }
  function defaultDesignedConfig(comp) {
    var c = {};
    (comp.config || []).forEach(function (spec) {
      if (spec.ph) c[spec.key] = spec.type === "number" ? (parseInt(spec.ph, 10) || 1) : spec.ph;
    });
    return c;
  }

  /* ── library + templates ───────────────────────────────────────────── */

  function updateLibraryFade() {
    var sc = $("pcLibraryScroll");
    if (!sc || library.hidden) return;
    var moreBelow = sc.scrollHeight - sc.clientHeight - sc.scrollTop > 4;
    library.classList.toggle("has-more", moreBelow);
  }

  /* Match: substring anywhere wins; word-initials ("phw" → PostHog watcher)
     as a fallback. Plain subsequence was too loose — "post" matched 21 of
     33 components and the search felt broken. */
  function fuzzyMatch(hay, needle) {
    hay = hay.toLowerCase();
    needle = needle.toLowerCase().trim();
    if (!needle) return true;
    if (hay.indexOf(needle) !== -1) return true;
    var initials = hay.split(/[^a-z0-9]+/).filter(Boolean).map(function (w) { return w[0]; }).join("");
    return initials.indexOf(needle) !== -1;
  }

  var searchIdx = -1;
  function renderLibraryItems() {
    var list = $("pcLibraryList");
    var query = $("pcLibSearch").value;
    list.textContent = "";
    searchIdx = -1;
    var anyVisible = false;
    ["sources", "workers", "gates", "sinks", "utilities"].forEach(function (cat) {
      var comps = COMPONENTS.filter(function (c) {
        return c.cat === cat &&
          fuzzyMatch(c.name + " " + c.desc + " " + c.cat + " " + (c.anchor || ""), query);
      });
      if (!comps.length) return;
      var head = el("div", "pc-lib-cat", CATS[cat].name);
      head.dataset.category = cat;
      list.appendChild(head);
      comps.forEach(function (comp) {
        anyVisible = true;
        var item = el("div", "pc-lib-item");
        item.dataset.component = comp.id;
        item.dataset.category = comp.cat;
        item.tabIndex = -1;
        item.appendChild(el("span", "pc-node-icon", comp.letter));
        var txt = el("div");
        txt.appendChild(el("div", "pc-lib-name", comp.name));
        txt.appendChild(el("div", "pc-lib-desc", comp.desc));
        item.appendChild(txt);
        item.addEventListener("pointerdown", function (e) {
          if (e.button === 0) startPaletteDrag(e, comp.id);
        });
        list.appendChild(item);
      });
    });
    if (!anyVisible) {
      list.appendChild(el("div", "pc-lib-none", "No components match “" + query.trim() + "”."));
    }
    updateLibraryFade();
  }

  function visibleLibItems() {
    return Array.from(library.querySelectorAll(".pc-lib-item"));
  }
  function moveSearchHighlight(delta) {
    var items = visibleLibItems();
    if (!items.length) return;
    searchIdx = (searchIdx + delta + items.length) % items.length;
    items.forEach(function (it, i) { it.classList.toggle("is-search-hit", i === searchIdx); });
    items[searchIdx].scrollIntoView({ block: "nearest" });
  }
  function placeSearchHighlight() {
    var items = visibleLibItems();
    var item = items[searchIdx >= 0 ? searchIdx : 0];
    if (!item) return;
    var r = stage.getBoundingClientRect();
    var center = toWorld(r.left + r.width / 2, r.top + r.height / 2);
    var spot = resolveDropCollision(center.x - NODE_W / 2, center.y - 60);
    addDesignedNode(item.dataset.component, null, spot.x, spot.y);
    $("pcLibSearch").value = "";
    renderLibraryItems();
  }

  function templatePreviewSvg(t) {
    /* Mini-graph: category-colored dots + contract lines, scaled to fit. */
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    t.nodes.forEach(function (n) {
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
      minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
    });
    var W = 208, H = 46, pad = 7;
    var sx = (W - pad * 2) / Math.max(1, maxX - minX);
    var sy = (H - pad * 2) / Math.max(1, maxY - minY);
    var s = Math.min(sx, sy, 0.19);
    function px(x) { return pad + (x - minX) * s; }
    function py(y) { return pad + (y - minY) * s; }
    var CAT_HEX = { sources: "#7ee0a3", workers: "#58a6ff", gates: "#ffb340", sinks: "#bc8cff", utilities: "#39d2c0" };
    var svg = svgEl("svg", { "class": "pc-template-graph", viewBox: "0 0 " + W + " " + H });
    t.edges.forEach(function (e) {
      var a = t.nodes.find(function (n) { return n.key === e.from; });
      var b = t.nodes.find(function (n) { return n.key === e.to; });
      if (!a || !b) return;
      svg.appendChild(svgEl("line", {
        x1: px(a.x), y1: py(a.y), x2: px(b.x), y2: py(b.y),
        "class": "pc-template-graph-edge"
      }));
    });
    t.nodes.forEach(function (n) {
      var comp = COMP_BY_ID[n.comp] || { cat: "workers" };
      svg.appendChild(svgEl("rect", {
        x: px(n.x) - 4, y: py(n.y) - 4, width: 8, height: 8, rx: 2.5,
        fill: CAT_HEX[comp.cat] || "#58a6ff"
      }));
    });
    return svg;
  }

  function buildLibrary() {
    renderLibraryItems();
    var search = $("pcLibSearch");
    search.addEventListener("input", renderLibraryItems);
    search.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); moveSearchHighlight(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); moveSearchHighlight(-1); }
      else if (e.key === "Enter") { e.preventDefault(); placeSearchHighlight(); }
      else if (e.key === "Escape") { search.value = ""; renderLibraryItems(); search.blur(); }
      e.stopPropagation();
    });

    var tlist = $("pcTemplateList");
    tlist.textContent = "";
    TEMPLATES.forEach(function (t) {
      var card = el("button", "pc-template");
      card.type = "button";
      card.appendChild(el("div", "pc-template-name", "✦ " + t.name));
      card.appendChild(templatePreviewSvg(t));
      card.appendChild(el("div", "pc-template-flow", t.flow));
      card.appendChild(el("div", "pc-template-desc", t.desc));
      card.addEventListener("click", function () { applyTemplate(t); });
      tlist.appendChild(card);
    });

    var sc = $("pcLibraryScroll");
    sc.addEventListener("scroll", updateLibraryFade);
    window.addEventListener("resize", updateLibraryFade);
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
      var n = makeDesignedNode(spec.comp, spec.label, spec.x + offX, spec.y + offY, null);
      addNodeEl(n);
      if (!reducedMotion) n.el.classList.add("pc-enter");
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
    maybeCelebrate(true);
    banner.hidden = false;
    $("pcBannerText").textContent = "“" + t.name + "” laid out as design nodes — nothing real yet.";
    showHint("Drag nodes to arrange · <kbd>Delete</kbd> removes · <kbd>⌘Z</kbd> undoes", 6500);
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
    /* Only workers become queue-config entries. Sources are scheduled
     * producers, gates/sinks/utilities are conventions and plumbing — all
     * surfaced as notes, never as queue entries. Config comes from the LIVE
     * placed nodes (the user may have edited the sketch in the inspector),
     * falling back to the component's placeholder defaults. */
    var entries = {};
    var others = [];
    cluster.template.nodes.forEach(function (spec) {
      var comp = COMP_BY_ID[spec.comp] || { cat: "workers" };
      if (spec.comp === "decision-inbox") return; // already exists
      var liveNode = cluster.keyToId && nodes.get(cluster.keyToId[spec.key]);
      var name = slugQueueName((liveNode && liveNode.label) || spec.label);
      if (comp.cat !== "workers") { others.push({ name: name, comp: comp }); return; }
      var c = {};
      (comp.config || []).forEach(function (f) { if (f.ph) c[f.key] = f.ph; });
      var live = (liveNode && liveNode.config) || {};
      Object.keys(live).forEach(function (k) { c[k] = live[k]; });
      var entry = { auto_drain: true, repo_path: "/path/to/repo" };
      if (c.engine) entry.engine = String(c.engine);
      if (c.model) entry.model = String(c.model);
      if (c.effort) entry.effort = String(c.effort);
      entry.desired_workers = parseInt(c.desired_workers, 10) || 1;
      entries[name] = entry;
    });
    return { entries: entries, others: others };
  }

  function openMaterializeModal(cluster) {
    var prev = materializationPreview(cluster);
    var body = $("pcModalBody");
    body.textContent = "";
    $("pcModalTitle").textContent = "Preview materialization — “" + cluster.template.name + "”";

    var note = el("p", "pc-modal-note");
    note.innerHTML = "This is exactly what would bring the design to life. " +
      "<b>The canvas never writes it for you.</b> The WatchTower daemon reads " +
      "queue-config.json live — paste these entries there and it reconciles within a minute.";
    body.appendChild(note);

    body.appendChild(el("h3", null, "Add to queue-config.json (the daemon reads it live):"));
    body.appendChild(el("pre", null, JSON.stringify(prev.entries, null, 2)));

    var cmds = [];
    Object.keys(prev.entries).forEach(function (name) {
      cmds.push("wt ls -q " + name + "        # verify the queue registered");
    });
    if (prev.others.length) {
      body.appendChild(el("h3", null, "Not queue entries — bring these to life yourself:"));
      body.appendChild(el("pre", null,
        prev.others.map(function (o) {
          var hints = {
            sources: "schedule the producer (launchd/cron/webhook) — it files into a queue on its cadence",
            gates: "a human checkpoint — park work with wt block / needs_input, or decide it in the Decision Inbox",
            sinks: "an output — it happens when the filing lands (label, email, text, page, commit, deploy)",
            utilities: "plumbing inside the producer/worker, not a queue — see its Pattern in the inspector"
          };
          return "# " + o.name + " (" + o.comp.name + ")\n#   " +
                 (hints[o.comp.cat] || hints.utilities) + "\n#   pattern: " + o.comp.anchor;
        }).join("\n\n")));
    }
    body.appendChild(el("h3", null, "Then verify:"));
    body.appendChild(el("pre", null, (cmds.join("\n") || "# (no worker queues in this design)") +
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
    var visSet = visibleNodes();
    if (!visSet.length) return;

    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    visSet.forEach(function (n) {
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
      if (!s || !t || s.filteredOut || t.filteredOut) return;
      ctx.beginPath();
      ctx.moveTo(s.x * scale + ox + NODE_W * scale, (s.y + 55) * scale + oy);
      ctx.lineTo(t.x * scale + ox, (t.y + 55) * scale + oy);
      ctx.stroke();
    });

    var colors = { ok: "#3fb950", bad: "#f85149", warn: "#d29922", parked: "#ffb340",
                   gate: "#ffb340", muted: "#4a5462", draft: "#7a6a9e" };
    visSet.forEach(function (n) {
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
    try { minimapCanvas.setPointerCapture(e.pointerId); } catch (err) {}
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
    $("pcActivity").hidden = mode !== "runtime"; // the filter is runtime-only
    refreshGhostHint();
    if (mode === "design") {
      showHint("Drag a component onto the canvas — or lay out a template in one click", 7000);
      requestAnimationFrame(updateLibraryFade);
    }
  }
  $("pcModeRuntime").addEventListener("click", function () { setMode("runtime"); });
  $("pcModeDesign").addEventListener("click", function () { setMode("design"); });

  $("pcActivityToggle").addEventListener("click", function () {
    activityFilterOn = !activityFilterOn;
    try { localStorage.setItem(ACTIVITY_LS_KEY, activityFilterOn ? "1" : "0"); } catch (e) {}
    applyVisibility();
    fitView(); // reframe on the new visible set
    toast(activityFilterOn ? "Showing queues active in the last 7 days" : "Showing all queues");
  });

  /* ── empty state ───────────────────────────────────────────────────── */

  function refreshEmptyState() {
    emptyState.hidden = nodes.size > 0;
    refreshGhostHint();
  }
  function refreshGhostHint() {
    var ghost = $("pcGhostHint");
    if (!ghost) return;
    var anyDesigned = false;
    nodes.forEach(function (n) { if (n.kind === "designed") anyDesigned = true; });
    ghost.hidden = !(mode === "design" && !anyDesigned && nodes.size > 0);
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
    // Gate: its own column right of everything placed so far, lifted ABOVE
    // the top row — the human sits above the machine fleet, and the
    // sign-off edges route over the grid instead of through it.
    gates.forEach(function (n, i) {
      var col = Math.max(1, Math.ceil(gridSlotsUsed / LAYOUT_ROWS)) + i;
      n.x = LAYOUT_ORIGIN.x + col * LAYOUT_COL_W;
      n.y = LAYOUT_ORIGIN.y - LAYOUT_ROW_H - 40;
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
    applyVisibility(); // also re-renders edges + minimap on the visible set
    refreshEmptyState();
    if (selection && selection.type === "node") {
      var sel = nodes.get(selection.id);
      if (sel && sel.kind === "queue" && !sel.filteredOut) renderInspector(sel);
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
        if (n.component) entry.component = n.component;
        if (n.category) entry.category = n.category;
        if (n.archetype) entry.archetype = n.archetype;
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
          viewportSaved = true;
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
          if (p && p.kind === "designed" && (p.component || p.archetype)) {
            var n = { id: id, kind: "designed",
                      component: p.component || null,
                      category: p.category || null,
                      archetype: p.archetype || "executor",
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
  var viewportSaved = false; // the layout carried a real viewport — honor it

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
          maybeCelebrate();
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

  // First impression: a LEGIBLE view of the active fleet — queues with open
  // work, running tickets, live workers, or alarms, plus the gate — framed at
  // 60–100% zoom. The full quiet fleet is one Fit away. Falls back to the
  // whole-fleet fit when nothing is active (or the active set IS the fleet).
  function frameInitialView() {
    var active = [];
    var visible = visibleNodes();
    visible.forEach(function (n) {
      if (n.kind === "gate") { active.push(n); return; }
      if (n.kind !== "queue") return;
      var d = n.data || {};
      if ((d.depth || 0) > 0 || (d.in_progress || 0) > 0 || (d.workers || 0) > 0 ||
          d.stuck || d.staffing_alarm) active.push(n);
    });
    if (!active.length || active.length >= visible.length) { fitView(); return; }
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    active.forEach(function (n) {
      var h = n.el ? n.el.offsetHeight : 110;
      minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x + NODE_W); maxY = Math.max(maxY, n.y + h);
    });
    var r = stage.getBoundingClientRect();
    var pad = 110;
    var zw = (r.width - pad * 2) / Math.max(1, maxX - minX);
    var zh = (r.height - pad * 2) / Math.max(1, maxY - minY);
    view.zoom = Math.min(1.0, Math.max(0.7, Math.min(zw, zh)));
    view.x = r.width / 2 - (minX + maxX) / 2 * view.zoom;
    view.y = r.height / 2 - (minY + maxY) / 2 * view.zoom;
    clampView();
    applyView();
  }

  buildLibrary();
  refreshEmptyState();
  if (!reducedMotion) stage.classList.add("pc-enter");

  loadLayout().then(function () {
    return fetchState();
  }).then(function () {
    applySavedPositions();
    // First paint: honor a saved viewport (even {0,0,1} — the user put it
    // there); otherwise frame the ACTIVE fleet at a legible zoom — Fit (F)
    // still shows the whole wall on demand.
    if (!viewportSaved && nodes.size) frameInitialView(); else applyView();
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
