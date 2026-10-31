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
    "  padding: 12px 0 76px; gap: 6px; z-index: 1000; overflow-y: auto;",
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
    /* Report-a-problem flag rides in the rail flow between the app list and
     * the account avatar: the auto margins centre it in whatever free space
     * is left, so it lands mid-rail on short pages and simply scrolls with
     * the icons when the app list outgrows the window — never overlapping. */
    ".ccc-report-flag {",
    "  margin-top: auto; margin-bottom: auto; flex-shrink: 0;",
    "  width: 100%; display: flex; justify-content: center; padding: 4px 0;",
    "}",
    ".ccc-report-flag button {",
    "  position: relative; display: flex;",
    "  flex-direction: column; align-items: center; gap: 2px;",
    "  width: calc(var(--ccc-rail-w, " + RAIL_DEFAULT + "px) - 12px);",
    "  padding: 6px 3px; border-radius: 7px; cursor: pointer;",
    "  border: 1px solid rgba(240,180,60,.45); background: rgba(240,180,60,.10);",
    "  color: #f0b43c; font: 9px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;",
    "  text-transform: uppercase; letter-spacing: 0.2px; white-space: nowrap;",
    "}",
    ".ccc-report-flag button .icon { font-size: 17px; line-height: 1; }",
    ".ccc-report-flag button:hover { background: rgba(240,180,60,.2); color: #ffd27a; }",
    ".ccc-report-flag.hint button::after {",
    "  content: ''; position: absolute; top: 4px; right: 6px; width: 7px;",
    "  height: 7px; border-radius: 50%; background: #f85149;",
    "}",
    /* Account avatar: fixed at the viewport's bottom-left over the rail, so
     * a long app list can scroll underneath without pushing it off-screen.
     * The dot reports the CCC Cloud connection state — green only when
     * paired, enabled and online. */
    ".ccc-account-wrap {",
    "  position: fixed; left: 0; bottom: 8px; z-index: 1000;",
    "  width: var(--ccc-rail-w, " + RAIL_DEFAULT + "px);",
    "  display: flex; justify-content: center;",
    "}",
    ".ccc-account {",
    "  position: relative; flex-shrink: 0;",
    "  display: flex; flex-direction: column; align-items: center; gap: 3px;",
    "  width: calc(var(--ccc-rail-w, " + RAIL_DEFAULT + "px) - 12px);",
    "  padding: 6px 3px; border: 0; border-radius: 7px; background: none;",
    "  color: #888; cursor: pointer; text-align: center;",
    "  font: 9px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;",
    "  text-transform: uppercase; letter-spacing: 0.2px; line-height: 1.2;",
    "}",
    ".ccc-account:hover { background: #1a1d23; color: #ccc; }",
    ".ccc-account[aria-expanded=\"true\"] { background: #23272e; color: #ccc; }",
    ".ccc-account-ava {",
    "  position: relative; width: 34px; height: 34px; border-radius: 50%;",
    "  background: #23272e; border: 1px solid #333a44; color: #cfd4db;",
    "  display: grid; place-items: center; font-size: 15px; font-weight: 600;",
    "  text-transform: none; letter-spacing: 0; line-height: 1;",
    "}",
    ".ccc-account-ava svg { display: block; width: 18px; height: 18px; }",
    ".ccc-account-dot {",
    "  position: absolute; right: -2px; bottom: -2px; width: 11px; height: 11px;",
    "  border-radius: 50%; border: 2px solid #0f1115; background: #5f6772;",
    "}",
    ".ccc-account-dot.on { background: #3fb950; }",
    ".ccc-account-dot.warn { background: #f0b43c; }",
    /* Account popover: anchored just right of the rail's bottom corner. */
    ".ccc-account-pop {",
    "  position: fixed; left: calc(var(--ccc-rail-w, " + RAIL_DEFAULT + "px) + 10px);",
    "  bottom: 10px; z-index: 4500; width: 248px; padding: 13px;",
    "  background: #14171d; border: 1px solid #2a303a; border-radius: 11px;",
    "  box-shadow: 0 14px 44px rgba(0,0,0,.5); color: #cfd4db;",
    "  font: 12px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;",
    "}",
    ".ccc-account-pop .acc-head { display: flex; align-items: center; gap: 10px; }",
    ".ccc-account-pop .acc-ava { width: 32px; height: 32px; font-size: 14px; flex-shrink: 0; }",
    ".ccc-account-pop .acc-ava svg { width: 17px; height: 17px; }",
    ".ccc-account-pop .acc-who { font-weight: 600; color: #e6e9ee; word-break: break-all; }",
    ".ccc-account-pop .acc-state { color: #8a929e; font-size: 11px; margin-top: 2px; }",
    ".ccc-account-pop .acc-machine {",
    "  color: #69707a; font-size: 10.5px; margin-top: 9px; word-break: break-all;",
    "}",
    ".ccc-account-pop .acc-err { color: #f0883e; font-size: 11px; margin-top: 8px; }",
    ".ccc-account-pop .acc-actions {",
    "  display: flex; flex-direction: column; gap: 6px; margin-top: 11px;",
    "}",
    ".ccc-account-pop .acc-btn {",
    "  width: 100%; padding: 7px 10px; border-radius: 7px; cursor: pointer;",
    "  border: 1px solid #333a44; background: #1c2027; color: #cfd4db;",
    "  font: 12px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;",
    "  text-align: left;",
    "}",
    ".ccc-account-pop .acc-btn:hover { background: #23272e; color: #e6e9ee; }",
    ".ccc-account-pop .acc-btn.primary { background: #1f6feb; border-color: #1f6feb; color: #fff; }",
    ".ccc-account-pop .acc-btn.primary:hover { background: #388bfd; }",
    ".ccc-account-pop .acc-btn.danger { color: #f0a29b; }",
    ".ccc-account-pop .acc-btn.danger:hover { border-color: #8c3f39; }",
    ".ccc-account-pop .acc-btn:disabled { opacity: .55; cursor: default; }",
    /* Morning's own wrappers hug the old nav; keep them off the rail. */
    ".mv-wrap, .mk-wrap { padding-left: 20px !important; }",
    /* Phones and the PWA have no room for the gutter. */
    "@media (max-width: 700px) {",
    "  .ccc-side-nav, .ccc-rail-resizer, .ccc-report-flag, .ccc-account-wrap, .ccc-account-pop { display: none; }",
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

  // Last good /api/apps answer, so a page paints the user's real rail
  // (Decisions, Savings, their own apps) instantly instead of the two-app
  // fallback until the fetch lands. Without it, every full navigation shows
  // Sessions + Queues first and the rest only "sometimes" — whenever the
  // fetch is slow behind the page's other requests, or fails outright.
  var APPS_CACHE_KEY = "ccc-rail-apps-v1";
  var SWITCH_KEY = "ccc-rail-switch";

  function cachedApps() {
    try {
      var v = JSON.parse(localStorage.getItem(APPS_CACHE_KEY) || "null");
      return (Array.isArray(v) && v.length) ? v : null;
    } catch (e) { return null; }
  }

  // Fire-and-forget latency sample into the local perf sink (the same
  // /api/perf-event the dashboard uses). Every page carries the rail, so it
  // cannot rely on app.js's window._perfEvent being present.
  function perfEvent(kind, ms, detail) {
    try {
      fetch("/api/perf-event", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: kind, ms: Math.max(0, Math.round(ms)), detail: detail || {} }),
        keepalive: true
      }).catch(function () {});
    } catch (e) {}
  }

  function afterPaint(fn) {
    requestAnimationFrame(function () { requestAnimationFrame(fn); });
  }

  // Report the switch a rail click on the previous page started. Ends at
  // this page's load event + a painted frame; navigation timing splits it
  // into server response vs. parse/script time so "slow switch" can be
  // attributed. Stale or foreign entries (> 60s) are dropped.
  function reportArrival() {
    var rec = null;
    try {
      rec = JSON.parse(sessionStorage.getItem(SWITCH_KEY) || "null");
      sessionStorage.removeItem(SWITCH_KEY);
    } catch (e) { rec = null; }
    if (!rec || typeof rec.t !== "number") return;
    function done() {
      afterPaint(function () {
        var ms = Date.now() - rec.t;
        if (ms < 0 || ms > 60000) return;
        if (ms > SLOW_SWITCH_MS) hintReport("Opening this page took " + (ms / 1000).toFixed(1) + " s.");
        var detail = { from: String(rec.from || ""), to: String(rec.to || ""), mode: "navigate" };
        try {
          var n = performance.getEntriesByType("navigation")[0];
          if (n) {
            detail.response_ms = Math.round(n.responseEnd);
            detail.dom_ready_ms = Math.round(n.domContentLoadedEventEnd);
            detail.load_ms = Math.round(n.loadEventStart);
          }
        } catch (e) {}
        perfEvent("view_switch", ms, detail);
      });
    }
    if (document.readyState === "complete") done();
    else window.addEventListener("load", done, { once: true });
  }

  var lastApps = FALLBACK;

  // "Report a problem" lives on every page, so the moment something feels
  // wrong the report is one click away. It never pops up on its own; after
  // a slow switch or a failed rail load it only grows a dot.
  var SLOW_SWITCH_MS = 5000;
  var flag = document.createElement("div");
  flag.className = "ccc-report-flag";
  var flagBtn = document.createElement("button");
  flagBtn.type = "button";
  flagBtn.title = "Report a problem: sends a private report with the last 7 days of diagnostics";
  flagBtn.setAttribute("aria-label", "Report a problem");
  var flagIcon = document.createElement("span");
  flagIcon.className = "icon";
  flagIcon.textContent = "\u2691";
  flagBtn.appendChild(flagIcon);
  flagBtn.appendChild(document.createTextNode("Report"));
  flag.appendChild(flagBtn);

  function hintReport(why) {
    flag.classList.add("hint");
    flagBtn.title = why + " Report a problem?";
  }

  // The dashboard owns the report modal; other pages hand off to it with
  // ?report_problem=<page>, which opens the modal after the load.
  flagBtn.addEventListener("click", function () {
    var from = activeIdFor(lastApps) || "";
    flag.classList.remove("hint");
    if (typeof window.cccOpenReport === "function" && window.cccOpenReport(from)) return;
    window.location.href = "/?report_problem=" + encodeURIComponent(from || "page");
  });

  /* Account affordance: an avatar at the foot of the rail reporting this
   * machine's CCC Cloud state, with a click popover for identity and
   * sign in/out. All state comes from /api/cloud-relay/status, which is
   * localhost-only on the server — anything the endpoint cannot confirm is
   * shown as grey/amber and described honestly, never faked green. */
  var ACCOUNT_CACHE_KEY = "ccc-rail-account-v1";
  var ACCOUNT_POLL_MS = 30000;
  var NETWORK_HANDOFF_PARAM = "open_network";
  // Static icon for the signed-out avatar; identity text never goes through
  // innerHTML — the paired letter is written with textContent like the rest.
  var PERSON_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path fill="currentColor" d="M12 12a4.2 4.2 0 1 0-4.2-4.2A4.2 4.2 0 0 0 ' +
    '12 12Zm0 2.1c-3.2 0-6.3 1.6-6.3 4.2v1.2h12.6v-1.2c0-2.6-3.1-4.2-6.3-4.2Z"/>' +
    "</svg>";

  var accountBtn = document.createElement("button");
  accountBtn.className = "ccc-account";
  accountBtn.type = "button";
  accountBtn.title = "Account";
  accountBtn.setAttribute("aria-haspopup", "dialog");
  accountBtn.setAttribute("aria-expanded", "false");
  accountBtn.setAttribute("aria-controls", "ccc-account-pop");
  var accAva = document.createElement("span");
  accAva.className = "ccc-account-ava";
  accAva.setAttribute("aria-hidden", "true");
  var accGlyph = document.createElement("span");
  var accDot = document.createElement("span");
  accDot.className = "ccc-account-dot off";
  accAva.appendChild(accGlyph);
  accAva.appendChild(accDot);
  accountBtn.appendChild(accAva);
  var accLabel = document.createElement("span");
  accLabel.textContent = "Account";
  accountBtn.appendChild(accLabel);
  // Fixed wrapper keeps the avatar at the viewport bottom no matter how far
  // down the app list the nav has scrolled.
  var accWrap = document.createElement("div");
  accWrap.className = "ccc-account-wrap";
  accWrap.appendChild(accountBtn);

  var pop = document.createElement("div");
  pop.className = "ccc-account-pop";
  pop.id = "ccc-account-pop";
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-label", "Account");
  pop.style.display = "none";
  var popHead = document.createElement("div");
  popHead.className = "acc-head";
  var popAva = document.createElement("span");
  popAva.className = "ccc-account-ava acc-ava";
  popAva.setAttribute("aria-hidden", "true");
  var popGlyph = document.createElement("span");
  var popDot = document.createElement("span");
  popDot.className = "ccc-account-dot off";
  popAva.appendChild(popGlyph);
  popAva.appendChild(popDot);
  var popHeadText = document.createElement("div");
  var popWho = document.createElement("div");
  popWho.className = "acc-who";
  var popSub = document.createElement("div");
  popSub.className = "acc-state";
  popHeadText.appendChild(popWho);
  popHeadText.appendChild(popSub);
  popHead.appendChild(popAva);
  popHead.appendChild(popHeadText);
  var popMachine = document.createElement("div");
  popMachine.className = "acc-machine";
  var popErr = document.createElement("div");
  popErr.className = "acc-err";
  popErr.style.display = "none";
  var popActions = document.createElement("div");
  popActions.className = "acc-actions";
  // Stable action buttons: repaints update label/class/handler instead of
  // rebuilding nodes, so a focused button keeps focus across refreshes.
  var popPrimary = document.createElement("button");
  popPrimary.type = "button";
  var popSecondary = document.createElement("button");
  popSecondary.type = "button";
  popSecondary.className = "acc-btn";
  popSecondary.textContent = "Connection settings…";
  popActions.appendChild(popPrimary);
  popActions.appendChild(popSecondary);
  pop.appendChild(popHead);
  pop.appendChild(popMachine);
  pop.appendChild(popErr);
  pop.appendChild(popActions);

  var lastStatus = cachedStatus();
  var statusFresh = false;
  var lastFetchErr = null;
  var popOpen = false;
  var signingOut = false;

  function cachedStatus() {
    try {
      var v = JSON.parse(localStorage.getItem(ACCOUNT_CACHE_KEY) || "null");
      return (v && typeof v === "object") ? v : undefined;
    } catch (e) { return undefined; }
  }

  function letterFor(s) {
    var ch = (String((s && s.account_email_masked) || "").match(/[A-Za-z0-9]/) || [""])[0];
    return ch.toUpperCase();
  }

  // Pure status → presentation mapping, kept free of DOM so tests can
  // exercise every state. Green is only ever paired + enabled + loop online;
  // everything else is amber (transitional/error) or grey.
  function cccAccountState(s, fresh, err) {
    if (s === undefined || !fresh)
      return { key: "loading", dot: "off", letter: "", who: "Checking…",
               sub: "Reading connection status.", paired: false, machine: "" };
    if (s === null)
      return { key: "unavailable", dot: "warn", letter: "", who: "Status unavailable",
               sub: (err === "forbidden")
                 ? "This browser does not have access to the account status."
                 : "Could not read CCC Cloud status on this machine.",
               paired: false, machine: "" };
    var who = s.account_email_masked || "Paired device";
    var machine = String(s.device_id || "");
    if (s.env_disabled)
      return { key: "env_disabled", dot: "off", letter: letterFor(s), who: who,
               sub: "CCC Cloud is disabled for this run.", paired: !!s.paired,
               machine: machine };
    if (!s.paired)
      return { key: "unpaired", dot: "off", letter: "", who: "Not signed in",
               sub: "Local only. Sign in to reach this machine from CCC Cloud.",
               paired: false, machine: machine };
    if (!s.enabled)
      return { key: "disabled", dot: "off", letter: letterFor(s), who: who,
               sub: "Paired, cloud connection off.", paired: true, machine: machine };
    var loopState = (s.loop && s.loop.state) || "off";
    if (loopState === "online")
      return { key: "online", dot: "on", letter: letterFor(s), who: who,
               sub: "Connected", paired: true, machine: machine };
    if (loopState === "connecting")
      return { key: "connecting", dot: "warn", letter: letterFor(s), who: who,
               sub: "Connecting…", paired: true, machine: machine };
    if (loopState === "error")
      return { key: "error", dot: "warn", letter: letterFor(s), who: who,
               sub: "Connection error" +
                 (s.loop && s.loop.last_error ? ": " + s.loop.last_error : ""),
               paired: true, machine: machine };
    return { key: "offline", dot: "warn", letter: letterFor(s), who: who,
             sub: "Offline", paired: true, machine: machine };
  }

  function setGlyph(el, letter) {
    if (letter) { el.textContent = letter; return; }
    el.textContent = "";
    el.innerHTML = PERSON_SVG;
  }

  function paintAccount() {
    var st = cccAccountState(lastStatus, statusFresh, lastFetchErr);
    accDot.className = "ccc-account-dot " + st.dot;
    setGlyph(accGlyph, st.letter);
    accountBtn.title = "Account: " + st.sub;
    if (popOpen) renderPop();
  }

  function shortId(id) {
    id = String(id || "");
    return id.length > 10 ? id.slice(0, 8) + "…" : id;
  }

  function renderPop() {
    var st = cccAccountState(lastStatus, statusFresh, lastFetchErr);
    popWho.textContent = st.who;
    popSub.textContent = st.sub;
    popDot.className = "ccc-account-dot " + st.dot;
    setGlyph(popGlyph, st.letter);
    if (st.machine) {
      popMachine.textContent = "Machine " + shortId(st.machine);
      popMachine.title = "This machine's device id: " + st.machine;
      popMachine.style.display = "";
    } else {
      popMachine.textContent = "";
      popMachine.title = "";
      popMachine.style.display = "none";
    }
    if (st.paired) {
      popPrimary.className = "acc-btn danger";
      popPrimary.textContent = "Sign out of this machine";
      popPrimary.disabled = signingOut;
      popPrimary.onclick = doSignOut;
      popSecondary.style.display = "";
      popSecondary.onclick = openAccountSettings;
    } else {
      popPrimary.className = "acc-btn primary";
      popPrimary.textContent =
        (st.key === "unavailable") ? "Connection settings…" : "Sign in";
      popPrimary.disabled = signingOut;
      popPrimary.onclick = openAccountSettings;
      popSecondary.style.display = "none";
      popSecondary.onclick = null;
    }
  }

  var STATUS_TIMEOUT_MS = 8000;
  var statusSeq = 0;

  function fetchStatus() {
    // cccPriority "user" keeps this out of the dashboard's background-fetch
    // pool (static/app.js installConnectionSlotGate): the dot is visible
    // state, and a poll parked behind the streams would show stale state.
    var seq = ++statusSeq;
    var ctrl = (typeof AbortController !== "undefined") ? new AbortController() : null;
    var timer = ctrl
      ? setTimeout(function () { try { ctrl.abort(); } catch (e) {} }, STATUS_TIMEOUT_MS)
      : 0;
    var opts = { cache: "no-store", cccPriority: "user" };
    if (ctrl) opts.signal = ctrl.signal;
    // /api/cloud-relay/indicator is the trimmed read added for this dot;
    // fall back to the fuller /api/cloud-relay/status on servers that do not
    // have it yet. Other failures are honest "unavailable", not a fallback.
    function get(url) {
      return fetch(url, opts).then(function (r) {
        if (r.ok) return r.json();
        if (url === "/api/cloud-relay/indicator" && (r.status === 404 || r.status === 405))
          return get("/api/cloud-relay/status");
        var err = new Error("cloud-relay http " + r.status);
        err.code = r.status;
        throw err;
      });
    }
    return get("/api/cloud-relay/indicator")
      .then(function (d) { return { d: d }; })
      .catch(function (e) { return { err: e }; })
      .then(function (res) {
        if (timer) clearTimeout(timer);
        if (seq !== statusSeq) return;   // a newer request already owns the paint
        statusFresh = true;
        lastStatus = (res.d && typeof res.d === "object") ? res.d : null;
        // Access denial is not evidence that the machine is signed out.
        lastFetchErr =
          (!lastStatus && res.err && res.err.code === 403) ? "forbidden" : null;
        // The indicator answer carries `state`/`last_error` flat; normalize
        // to the status shape the state mapper reads.
        if (lastStatus && lastStatus.state !== undefined && !lastStatus.loop)
          lastStatus.loop = { state: lastStatus.state, last_error: lastStatus.last_error };
        if (lastStatus) {
          try { localStorage.setItem(ACCOUNT_CACHE_KEY, JSON.stringify(lastStatus)); } catch (e) {}
        } else {
          try { localStorage.removeItem(ACCOUNT_CACHE_KEY); } catch (e) {}
        }
        paintAccount();
      });
  }

  // Sign in always lands on the real Network access dialog: the dashboard
  // owns it (#networkAccessBtn → networkOpen, which also opens the CCC Cloud
  // pairing section). Other rail pages hand off with ?open_network=1, which
  // the rail itself consumes on the dashboard below.
  function openAccountSettings() {
    closePop(false);
    var btn = document.getElementById("networkAccessBtn");
    if (btn) { btn.click(); return; }
    window.location.href = "/?" + NETWORK_HANDOFF_PARAM + "=1";
  }

  function openNetworkFromLocation() {
    var url;
    try { url = new URL(window.location.href); } catch (e) { return; }
    if (!url.searchParams.has(NETWORK_HANDOFF_PARAM)) return;
    url.searchParams.delete(NETWORK_HANDOFF_PARAM);
    try { history.replaceState(history.state, "", url.pathname + url.search + url.hash); } catch (e) {}
    var tries = 0;
    (function attempt() {
      var btn = document.getElementById("networkAccessBtn");
      if (btn) { btn.click(); return; }
      if (++tries < 20) setTimeout(attempt, 250);
    })();
  }

  function doSignOut() {
    if (signingOut) return;
    signingOut = true;
    popErr.textContent = "";
    popErr.style.display = "none";
    renderPop();
    fetch("/api/cloud-relay/unpair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; })
          .then(function (d) { return { ok: !!(r.ok && d && d.ok), err: d && d.error }; });
      })
      .catch(function () { return { ok: false, err: "request failed" }; })
      .then(function (res) {
        signingOut = false;
        if (!res.ok) {
          popErr.textContent = "Sign out failed" + (res.err ? ": " + res.err : ".");
          popErr.style.display = "";
          renderPop();
          return;
        }
        try { localStorage.removeItem(ACCOUNT_CACHE_KEY); } catch (e) {}
        // Repaint from the server's own word, not an assumed outcome.
        fetchStatus();
      });
  }

  function openPop() {
    if (popOpen) return;
    popOpen = true;
    accountBtn.setAttribute("aria-expanded", "true");
    renderPop();
    pop.style.display = "";
    document.addEventListener("pointerdown", onDocPointer, true);
    document.addEventListener("keydown", onPopKey, true);
    fetchStatus();
    var first = pop.querySelector(".acc-btn");
    if (first) first.focus();
  }

  function closePop(restoreFocus) {
    if (!popOpen) return;
    popOpen = false;
    accountBtn.setAttribute("aria-expanded", "false");
    pop.style.display = "none";
    document.removeEventListener("pointerdown", onDocPointer, true);
    document.removeEventListener("keydown", onPopKey, true);
    if (restoreFocus !== false) accountBtn.focus();
  }

  function onDocPointer(e) {
    if (pop.contains(e.target) || accountBtn.contains(e.target)) return;
    closePop(false);
  }

  // Capture phase so Escape dismisses only the popover, not a modal that may
  // be open underneath it on the dashboard.
  function onPopKey(e) {
    if (e && (e.isComposing || e.keyCode === 229)) return;
    if (e.key === "Escape") {
      e.stopPropagation();
      closePop();
    }
  }

  accountBtn.addEventListener("click", function () {
    if (popOpen) closePop();
    else openPop();
  });
  // Pairing, unpairing and config saves in the Network dialog notify the
  // rail through this event (static/cloud-relay.js), so the avatar repaints
  // without waiting for the next poll.
  window.addEventListener("ccc-cloud-relay-change", function () { fetchStatus(); });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) fetchStatus();
  });
  setInterval(function () { if (!document.hidden) fetchStatus(); }, ACCOUNT_POLL_MS);

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
    var from = activeIdFor(lastApps) || "";
    if (a.target === "_blank") return;
    var t0 = Date.now();
    if (id !== "sessions" && id !== "queues") {
      // not a core app: falls through to a full navigation below
    } else if (typeof window.cccSwitchCoreApp === "function" && window.cccSwitchCoreApp(id)) {
      e.preventDefault();
      afterPaint(function () {
        perfEvent("view_switch", Date.now() - t0, { from: from, to: id, mode: "in_page" });
      });
      return;
    }
    // A full navigation: the next page's rail reports click -> its load.
    try {
      sessionStorage.setItem(SWITCH_KEY, JSON.stringify({ from: from, to: id, t: Date.now() }));
    } catch (err) {}
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
    lastApps = apps;
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
      // Canvas is deliberately a companion workspace: leave the dashboard
      // where it is and put the topology in its own same-origin window.
      // All other apps stay inside the CCC window; absolute URLs are wrapped
      // by /app/<id>, which frames them and offers a way out if needed.
      a.href = app.nav || app.url;
      if (app.id === "pipeline-canvas") {
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
    // The report flag centres itself in whatever space is left between the
    // app list and the rail's bottom padding — around the middle on short
    // pages, in the scroll flow on long ones. The account avatar is not a
    // nav child: it hangs off the viewport bottom via .ccc-account-wrap.
    nav.appendChild(flag);
  }

  // A failed or non-OK fetch retries with backoff instead of leaving the
  // fallback rail up for the life of the page.
  var RETRY_MS = [1500, 5000, 15000];

  function refresh(attempt) {
    attempt = attempt || 0;
    var t0 = Date.now();
    var hadCache = !!cachedApps();
    return fetch("/api/apps")
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (d) {
        var ok = !!(d && Array.isArray(d.apps) && d.apps.length);
        perfEvent("rail_apps", Date.now() - t0, { ok: ok, attempt: attempt, cached: hadCache });
        if (ok) {
          render(d.apps);
          try { localStorage.setItem(APPS_CACHE_KEY, JSON.stringify(d.apps)); } catch (e) {}
        } else if (attempt < RETRY_MS.length) {
          setTimeout(function () { refresh(attempt + 1); }, RETRY_MS[attempt]);
        } else {
          hintReport("The app list did not load.");
        }
      });
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
    render(cachedApps() || FALLBACK);
    document.body.appendChild(nav);
    document.body.appendChild(accWrap);
    document.body.appendChild(pop);
    mountResizer();
    refresh();
    reportArrival();
    paintAccount();
    fetchStatus();
    openNetworkFromLocation();
  }

  // The Applications settings page calls this after a toggle, reorder, or
  // removal so the rail beside it stops disagreeing with the list.
  window.cccRefreshRail = function () { return refresh(0); };

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
    function onKey(e) {
      if (e && (e.isComposing || e.keyCode === 229)) return;
      if (e.key === "Escape") shut();
    }
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
