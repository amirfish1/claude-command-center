/* Thin left sidebar shared across /, /morning, /morning/kanban, and
 * /morning/goals/*. Injected at page load. Highlights the current page
 * so the user always knows which surface they're on.
 */
(function () {
  "use strict";

  const STYLE_ID = "ccc-side-nav-style";
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    .ccc-side-nav {
      position: fixed; top: 0; left: 0; bottom: 0; width: 52px;
      background: #0f1115; border-right: 1px solid #20252c;
      display: flex; flex-direction: column; align-items: center;
      padding: 12px 0 8px; gap: 8px; z-index: 1000;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .ccc-side-nav a {
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      width: 40px; padding: 8px 0; border-radius: 6px;
      color: #888; text-decoration: none;
      font-size: 9px; text-transform: uppercase; letter-spacing: 0.5px;
      transition: background 0.1s, color 0.1s;
      text-align: center; line-height: 1.2;
    }
    .ccc-side-nav a .icon {
      font-size: 18px; margin-bottom: 2px; display: block;
    }
    .ccc-side-nav a:hover { background: #1a1d23; color: #ccc; }
    .ccc-side-nav a.active { background: #23272e; color: #5ac8fa; }

    body { padding-left: 52px; box-sizing: border-box; }
    .mv-wrap, .mk-wrap { padding-left: 20px !important; }
  `;
  document.head.appendChild(style);

  // Detect current page for active highlighting.
  const path = window.location.pathname;
  let activeKey = "";
  if (path === "/" || path === "") activeKey = "dev";
  else if (path === "/morning/kanban") activeKey = "mkanban";
  else if (path.startsWith("/morning")) activeKey = "morning";

  const allLinks = [
    { key: "dev",     href: "/",                icon: "🛠️", label: "Dev" },
    { key: "morning", href: "/morning",         icon: "☀️",  label: "Morning", morning: true },
    { key: "mkanban", href: "/morning/kanban",  icon: "📋", label: "Board", morning: true },
  ];

  // Probe whether the Morning view is enabled before rendering its links.
  // /api/morning/state returns 404 when CCC_ENABLE_MORNING is unset, in which
  // case we silently drop the Morning entries from the nav.
  function build(links) {
    const nav = document.createElement("nav");
    nav.className = "ccc-side-nav";
    for (const l of links) {
      const a = document.createElement("a");
      a.href = l.href;
      if (l.key === activeKey) a.className = "active";
      const iconSpan = document.createElement("span");
      iconSpan.className = "icon";
      iconSpan.textContent = l.icon;
      a.appendChild(iconSpan);
      a.appendChild(document.createTextNode(l.label));
      nav.appendChild(a);
    }
    document.body.appendChild(nav);
  }

  fetch("/api/features")
    .then(r => r.ok ? r.json() : {})
    .catch(() => ({}))
    .then(features => {
      const morningOn = !!(features && features.morning);
      const links = allLinks.filter(l => morningOn || !l.morning);
      build(links);
    });
})();
