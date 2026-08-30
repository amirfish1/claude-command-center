/* Applications rail — the resizable column of app icons on the left edge.
 *
 * Replaces the old Queues/Sessions chip toggle. Every CCC surface is an app:
 * built-ins (Sessions, Queues, Morning) come from the server, and the user's
 * own apps come from the `apps` array in
 * ~/.claude/command-center/custom-links.json, and their pages live in
 * ~/.claude/command-center/apps/<id>/ (outside the repo checkout, so a
 * reinstall never wipes them).
 *
 * Three kinds of app, all of which are just a URL from the rail's point of
 * view:
 *   1. core view      — Sessions, Queues. Ship in the repo.
 *   2. CCC-hosted page — index.html served at /view/<app>, resolved from the
 *                        user apps dir first and the repo's static/ second.
 *                        Being same-origin, it can call every /api/* route.
 *   3. external embed  — an http(s) URL, or a local companion dashboard
 *                        reached through /proxy/<name>/*.
 *
 * Navigation is plain <a href> page navigation for user apps. Sessions ↔
 * Queues is intercepted on the dashboard (cccSwitchCoreApp) so the session
 * list stays mounted; other apps still navigate. The Mac app treats any
 * URL on the dashboard port as in-app (scripts/macapp/main.swift).
 *
 * Include with:  <script src="/static/app-rail.js" defer></script>
 */
(function () {
  "use strict";

  // Shared with the older gitignored static/morning/side-nav.js so the two
  // never double-render on a Morning page — whichever loads first wins.
  var STYLE_ID = "ccc-side-nav-style";
  if (document.getElementById(STYLE_ID)) return;
  if (document.querySelector(".ccc-side-nav")) return;
  // Framed pages (the Applications popup, an embedded app) get no rail of
  // their own — the parent window already has one.
  try { if (window.self !== window.top) return; } catch (e) { return; }

  // Width is a user preference: emoji-plus-label wants more room than a bare
  // icon strip, and how much depends on how long your app names are.
  var RAIL_DEFAULT = 76;
  var RAIL_MIN = 56;
  var RAIL_MAX = 160;
  var RAIL_KEY = "ccc-rail-width";

  function storedWidth() {
    var n = parseInt(localStorage.getItem(RAIL_KEY) || "", 10);
    if (!n || isNaN(n)) return RAIL_DEFAULT;
    return Math.min(RAIL_MAX, Math.max(RAIL_MIN, n));
  }

  var railW = storedWidth();

  function applyWidth(px) {
    railW = Math.min(RAIL_MAX, Math.max(RAIL_MIN, Math.round(px)));
    document.documentElement.style.setProperty("--ccc-rail-w", railW + "px");
  }

  // Rendered before /api/apps answers, and kept if it never does. The rail is
  // the only navigation once the chip toggle is gone, so it must not depend on
  // a fetch succeeding.
  var FALLBACK = [
    { id: "sessions", label: "Sessions", icon: "💬", url: "/", builtin: true },
    { id: "queues", label: "Queues", icon: "📋", url: "/q2.html", builtin: true }
  ];

  var style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = [
    ".ccc-side-nav {",
    "  position: fixed; top: 0; left: 0; bottom: 0;",
    "  width: var(--ccc-rail-w, " + RAIL_DEFAULT + "px);",
    "  background: #0f1115; border-right: 1px solid #20252c;",
    "  display: flex; flex-direction: column; align-items: center;",
    "  padding: 12px 0 8px; gap: 6px; z-index: 1000; overflow-y: auto;",
    "  overflow-x: hidden; scrollbar-width: none;",
    "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;",
    "}",
    ".ccc-side-nav::-webkit-scrollbar { display: none; }",
    ".ccc-side-nav a {",
    "  display: flex; flex-direction: column; align-items: center;",
    "  justify-content: center; padding: 8px 3px; border-radius: 7px;",
    "  width: calc(var(--ccc-rail-w, " + RAIL_DEFAULT + "px) - 12px);",
    "  color: #888; text-decoration: none; flex-shrink: 0;",
    "  font-size: 9px; text-transform: uppercase; letter-spacing: 0.2px;",
    "  transition: background 0.1s, color 0.1s; text-align: center;",
    "  line-height: 1.2; white-space: nowrap; overflow: hidden;",
"  text-overflow: ellipsis; max-width: 100%;",
    "}",
    ".ccc-side-nav a .icon { font-size: 20px; margin-bottom: 3px; display: block; }",
    ".ccc-side-nav a:hover { background: #1a1d23; color: #ccc; }",
    ".ccc-side-nav a.active { background: #23272e; color: #5ac8fa; }",
    ".ccc-side-nav .add {",
    "  width: 34px; height: 34px; flex-shrink: 0; border-radius: 8px;",
    "  border: 1px dashed #333a44; background: none; color: #5f6772;",
    "  font-size: 18px; line-height: 1; cursor: pointer; display: grid;",
    "  place-items: center; padding: 0; margin-top: 2px;",
    "}",
    ".ccc-side-nav .add:hover { border-color: #5ac8fa; color: #5ac8fa; }",
    /* Applications popup: keeps you on the page you were already on. */
    ".ccc-apps-scrim {",
    "  position: fixed; inset: 0; background: rgba(0,0,0,.55); z-index: 4000;",
    "  display: grid; place-items: center; padding: 28px;",
    "}",
    ".ccc-apps-modal {",
    "  width: 940px; max-width: 100%; height: 82vh; background: #0b0d11;",
    "  border: 1px solid #232830; border-radius: 13px; overflow: hidden;",
    "  box-shadow: 0 24px 70px rgba(0,0,0,.55); display: flex;",
    "  flex-direction: column; position: relative;",
    "}",
    ".ccc-apps-modal iframe { flex: 1; border: 0; width: 100%; }",
    ".ccc-apps-close {",
    "  position: absolute; top: 10px; right: 12px; z-index: 2; width: 28px;",
    "  height: 28px; border-radius: 7px; border: 1px solid #232830;",
    "  background: #171b21; color: #8a929e; font-size: 15px; cursor: pointer;",
    "  line-height: 1; display: grid; place-items: center; padding: 0;",
    "}",
    ".ccc-apps-close:hover { color: #e6e9ee; border-color: #39414d; }",
    "body { padding-left: var(--ccc-rail-w, " + RAIL_DEFAULT + "px);",
    "       box-sizing: border-box; }",
    /* Drag handle straddling the rail's right edge. */
    ".ccc-rail-resizer {",
    "  position: fixed; top: 0; bottom: 0; width: 7px; z-index: 1001;",
    "  left: calc(var(--ccc-rail-w, " + RAIL_DEFAULT + "px) - 3px);",
    "  cursor: col-resize;",
    "}",
    ".ccc-rail-resizer::before {",
    "  content: ''; position: absolute; inset: 0 3px; background: transparent;",
    "  transition: background .12s;",
    "}",
    ".ccc-rail-resizer:hover::before,",
    ".ccc-rail-resizer.dragging::before { background: #5ac8fa; }",
    "body.ccc-rail-dragging { cursor: col-resize; user-select: none; }",
    /* Morning's own wrappers hug the old nav; keep them off the rail. */
    ".mv-wrap, .mk-wrap { padding-left: 20px !important; }",
    /* Phones and the PWA have no room for the gutter. */
    "@media (max-width: 700px) {",
    "  .ccc-side-nav, .ccc-rail-resizer { display: none; }",
    "  body { padding-left: 0; }",
    "}"
  ].join("\n");
  document.head.appendChild(style);

  /* Which app owns the current page. Longest matching URL wins so that
   * /view/reddit beats the "/" of Sessions. */
  function activeIdFor(apps) {
    var here = window.location.pathname;
    var best = null;
    var bestLen = -1;
    for (var i = 0; i < apps.length; i++) {
      var u = apps[i].nav || apps[i].url;
      if (u.charAt(0) !== "/") continue;
      var hit = (u === "/") ? (here === "/" || here === "") : (here.indexOf(u) === 0);
      if (hit && u.length > bestLen) { best = apps[i].id; bestLen = u.length; }
    }
    return best;
  }

  var nav = document.createElement("nav");
  nav.className = "ccc-side-nav";
  nav.setAttribute("aria-label", "Applications");
  // Sessions ↔ Queues: let the dashboard keep the conversation list
  // mounted (iframe overlay) instead of a full navigation. Other apps
  // and modified-clicks (cmd-click, middle-click) still follow the href.
  // On standalone q2.html, cccSwitchCoreApp is undefined and this is a no-op.
  nav.addEventListener("click", function (e) {
    if (e.defaultPrevented) return;
    if (e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest("a[data-app-id]");
    if (!a || !nav.contains(a)) return;
    var id = a.getAttribute("data-app-id");
    if (id !== "sessions" && id !== "queues") return;
    if (typeof window.cccSwitchCoreApp === "function" && window.cccSwitchCoreApp(id)) {
      e.preventDefault();
    }
  });

  function markRailActive(id) {
    var links = nav.querySelectorAll("a[data-app-id]");
    for (var i = 0; i < links.length; i++) {
      var on = links[i].getAttribute("data-app-id") === id;
      links[i].classList.toggle("active", on);
      if (on) links[i].setAttribute("aria-current", "page");
      else links[i].removeAttribute("aria-current");
    }
  }
  window.cccMarkRailActive = markRailActive;

  function render(apps) {
    var activeId = activeIdFor(apps);
    nav.textContent = "";
    for (var i = 0; i < apps.length; i++) {
      var app = apps[i];
      var a = document.createElement("a");
      a.href = app.nav || app.url;
      a.title = app.label;
      a.setAttribute("data-app-id", app.id);
      if (app.id === activeId) {
        a.className = "active";
        a.setAttribute("aria-current", "page");
      }
      // Everything stays inside the CCC window. An absolute URL is wrapped
      // by /app/<id>, which frames it and offers a way out if the site
      // refuses to be embedded.
      a.href = app.nav || app.url;
      var icon = document.createElement("span");
      icon.className = "icon";
      icon.textContent = app.icon || "•";
      a.appendChild(icon);
      a.appendChild(document.createTextNode(app.label));
      nav.appendChild(a);
    }
    // Adding an app is a rail action, so the affordance belongs in the rail
    // rather than buried in dashboard settings. It opens a popup instead of
    // navigating: you should not lose the session you were reading.
    var add = document.createElement("button");
    add.className = "add";
    add.type = "button";
    add.title = "Add an app";
    add.setAttribute("aria-label", "Add an app");
    add.textContent = "+";
    add.onclick = openApps;
    nav.appendChild(add);
  }

  function refresh() {
    return fetch("/api/apps")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && Array.isArray(d.apps) && d.apps.length) render(d.apps);
      })
      .catch(function () { /* keep whatever is already rendered */ });
  }

  function mountResizer() {
    var grip = document.createElement("div");
    grip.className = "ccc-rail-resizer";
    grip.title = "Drag to resize \u00b7 double-click to reset";
    grip.addEventListener("mousedown", function (e) {
      e.preventDefault();
      grip.classList.add("dragging");
      document.body.classList.add("ccc-rail-dragging");
      function move(ev) { applyWidth(ev.clientX); }
      function up() {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        grip.classList.remove("dragging");
        document.body.classList.remove("ccc-rail-dragging");
        try { localStorage.setItem(RAIL_KEY, String(railW)); } catch (err) {}
      }
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
    grip.addEventListener("dblclick", function () {
      applyWidth(RAIL_DEFAULT);
      try { localStorage.setItem(RAIL_KEY, String(railW)); } catch (err) {}
    });
    document.body.appendChild(grip);
  }

  function mount() {
    if (!document.body) return;
    applyWidth(railW);
    render(FALLBACK);
    document.body.appendChild(nav);
    mountResizer();
    refresh();
  }

  // The Applications settings page calls this after a toggle, reorder, or
  // removal so the rail beside it stops disagreeing with the list.
  window.cccRefreshRail = refresh;

  function openApps() {
    var scrim = document.createElement("div");
    scrim.className = "ccc-apps-scrim";
    var modal = document.createElement("div");
    modal.className = "ccc-apps-modal";
    var close = document.createElement("button");
    close.className = "ccc-apps-close";
    close.type = "button";
    close.textContent = "\u00d7";
    close.setAttribute("aria-label", "Close");
    var frame = document.createElement("iframe");
    frame.src = "/applications#add";
    frame.title = "Applications";
    modal.appendChild(close);
    modal.appendChild(frame);
    scrim.appendChild(modal);

    function shut() {
      document.removeEventListener("keydown", onKey);
      scrim.remove();
      refresh();          // apps may have been added, renamed, or removed
    }
    function onKey(e) { if (e.key === "Escape") shut(); }
    close.onclick = shut;
    scrim.onclick = function (e) { if (e.target === scrim) shut(); };
    document.addEventListener("keydown", onKey);
    // The settings page focuses its own name field, so Escape lands inside
    // the frame and never reaches the listener above. Same-origin, so we can
    // listen in there too.
    frame.addEventListener("load", function () {
      try { frame.contentDocument.addEventListener("keydown", onKey); }
      catch (e) { /* cross-origin: parent listener is all we get */ }
    });
    document.body.appendChild(scrim);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
