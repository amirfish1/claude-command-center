/* Applications rail — the fixed 64px column of app icons on the left edge.
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
 * Navigation is plain <a href> page navigation, not an SPA router: that is
 * how the Queues/Sessions toggle already worked, and the Mac app treats any
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

  var RAIL_W = 64;

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
    "  position: fixed; top: 0; left: 0; bottom: 0; width: " + RAIL_W + "px;",
    "  background: #0f1115; border-right: 1px solid #20252c;",
    "  display: flex; flex-direction: column; align-items: center;",
    "  padding: 12px 0 8px; gap: 6px; z-index: 1000; overflow-y: auto;",
    "  overflow-x: hidden; scrollbar-width: none;",
    "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;",
    "}",
    ".ccc-side-nav::-webkit-scrollbar { display: none; }",
    ".ccc-side-nav a {",
    "  display: flex; flex-direction: column; align-items: center;",
    "  justify-content: center; width: 52px; padding: 8px 2px; border-radius: 7px;",
    "  color: #888; text-decoration: none; flex-shrink: 0;",
    "  font-size: 10px; text-transform: uppercase; letter-spacing: 0.4px;",
    "  transition: background 0.1s, color 0.1s; text-align: center;",
    "  line-height: 1.2; word-break: break-word;",
    "}",
    ".ccc-side-nav a .icon { font-size: 20px; margin-bottom: 3px; display: block; }",
    ".ccc-side-nav a:hover { background: #1a1d23; color: #ccc; }",
    ".ccc-side-nav a.active { background: #23272e; color: #5ac8fa; }",
    "body { padding-left: " + RAIL_W + "px; box-sizing: border-box; }",
    /* Morning's own wrappers hug the old nav; keep them off the rail. */
    ".mv-wrap, .mk-wrap { padding-left: 20px !important; }",
    /* Phones and the PWA have no room for the gutter. */
    "@media (max-width: 700px) {",
    "  .ccc-side-nav { display: none; }",
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
      var u = apps[i].url;
      if (u.charAt(0) !== "/") continue;         // external — never "current"
      var hit = (u === "/") ? (here === "/" || here === "") : (here.indexOf(u) === 0);
      if (hit && u.length > bestLen) { best = apps[i].id; bestLen = u.length; }
    }
    return best;
  }

  var nav = document.createElement("nav");
  nav.className = "ccc-side-nav";
  nav.setAttribute("aria-label", "Applications");

  function render(apps) {
    var activeId = activeIdFor(apps);
    nav.textContent = "";
    for (var i = 0; i < apps.length; i++) {
      var app = apps[i];
      var a = document.createElement("a");
      a.href = app.url;
      a.title = app.label;
      a.setAttribute("data-app-id", app.id);
      if (app.id === activeId) {
        a.className = "active";
        a.setAttribute("aria-current", "page");
      }
      if (app.url.charAt(0) !== "/") {
        // Absolute URLs leave the dashboard origin; give them their own tab
        // rather than replacing CCC.
        a.target = "_blank";
        a.rel = "noopener";
      }
      var icon = document.createElement("span");
      icon.className = "icon";
      icon.textContent = app.icon || "•";
      a.appendChild(icon);
      a.appendChild(document.createTextNode(app.label));
      nav.appendChild(a);
    }
  }

  function mount() {
    if (!document.body) return;
    render(FALLBACK);
    document.body.appendChild(nav);
    fetch("/api/apps")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && Array.isArray(d.apps) && d.apps.length) render(d.apps);
      })
      .catch(function () { /* keep the fallback rail */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
