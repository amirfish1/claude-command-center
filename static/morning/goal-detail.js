(function () {
  "use strict";

  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    for (const k in (attrs || {})) {
      if (k === "class") e.className = attrs[k];
      else if (k === "style" && typeof attrs[k] === "object") Object.assign(e.style, attrs[k]);
      else if (k.startsWith("on") && typeof attrs[k] === "function") e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    }
    for (const c of children) {
      if (c == null) continue;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return e;
  }

  function slugFromPath() {
    // /morning/goals/<slug>
    const parts = window.location.pathname.split("/").filter(Boolean);
    return parts.length >= 3 ? decodeURIComponent(parts[2]) : "";
  }

  function launchLabel(state) {
    if (state === "alive") return "▶ Inject";
    if (state === "dormant") return "▶ Resume";
    if (state === "never") return "▶ Start";
    return "—";
  }

  function renderStrategy(s) {
    const statusClass = s.status === "done" ? " done" : (s.status === "dropped" ? " dropped" : "");
    const dot = el("span", { class: "sess-dot " + s.session_state });
    const btn = s.session_state === "dropped"
      ? null
      : el("button", { class: "launch " + (s.session_state === "dormant" ? "dormant" : s.session_state === "never" ? "new" : "") }, launchLabel(s.session_state));
    return el("div", { class: "strat-row" + statusClass },
      dot,
      el("div", { class: "body" },
        el("div", { class: "text" }, s.text),
        el("div", { class: "sum" }, s.session_summary || "")
      ),
      btn
    );
  }

  async function load() {
    const slug = slugFromPath();
    if (!slug) {
      showError("No goal slug in URL.");
      return;
    }

    let detail;
    try {
      const r = await fetch("/api/morning/goals/" + encodeURIComponent(slug));
      if (r.status === 404) { showError("Goal not found: " + slug); return; }
      if (!r.ok) throw new Error("HTTP " + r.status);
      detail = await r.json();
    } catch (e) {
      showError("Load failed: " + e.message);
      return;
    }

    document.getElementById("gd-life-area").textContent = detail.life_area || "";
    document.getElementById("gd-name").textContent = detail.name || slug;
    document.getElementById("gd-intent").textContent = detail.intent_markdown || "";
    document.title = "CCC · " + (detail.name || slug);

    const header = document.getElementById("gd-header");
    if (detail.accent) header.style.borderLeftColor = detail.accent;

    const strats = document.getElementById("gd-strategies");
    strats.innerHTML = "";
    for (const s of (detail.strategies || [])) strats.appendChild(renderStrategy(s));

    const tact = document.getElementById("gd-tactical");
    tact.innerHTML = "";
    for (const t of (detail.tactical_tagged || [])) {
      tact.appendChild(el("div", { class: "mv-task" },
        el("span", { class: "text" }, t.text),
        el("span", { class: "src" }, t.source),
        t.strategy_id
          ? el("span", { class: "goal-chip" }, "→ " + t.strategy_id)
          : el("span", { class: "muted" }, "untagged")
      ));
    }

    const deliv = document.getElementById("gd-deliverables");
    deliv.innerHTML = "";
    for (const d of (detail.deliverables || [])) {
      deliv.appendChild(el("div", { class: "deliv-row" },
        el("span", { class: "type" }, d.type),
        el("span", { class: "label" }, d.label),
        el("span", { class: "src" }, d.source || "")
      ));
    }

    const ctx = document.getElementById("gd-context");
    ctx.innerHTML = "";
    if (!(detail.context_library || []).length) {
      ctx.appendChild(el("div", { class: "muted" }, "No attachments yet — Phase 4 wires this up."));
    } else {
      for (const c of detail.context_library) {
        ctx.appendChild(el("div", { class: "deliv-row" },
          el("span", { class: "type" }, c.type || "DOC"),
          el("span", { class: "label" }, c.label || c.path || ""),
          el("span", { class: "src" }, c.source || "")
        ));
      }
    }

    const sess = document.getElementById("gd-sessions");
    sess.innerHTML = "";
    for (const s of (detail.recent_sessions || [])) {
      sess.appendChild(el("div", { class: "sess-summary" },
        el("span", {}, s.summary),
        el("span", { class: "ago" }, " · " + s.when)
      ));
    }
  }

  function showError(msg) {
    const e = document.getElementById("gd-error");
    e.hidden = false;
    e.textContent = msg;
  }

  load();
})();
