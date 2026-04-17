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

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  // Minimal safe markdown: **bold**, ## heading, paragraph breaks, line breaks.
  // Input is HTML-escaped first; only controlled tag substitutions happen after.
  function renderMarkdownSafe(src) {
    if (!src) return "";
    let h = escapeHtml(src);
    h = h.replace(/^## (.+)$/gm, "<h4 class=\"md-h\">$1</h4>");
    h = h.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    h = h.split(/\n\n+/).map(p => p.startsWith("<h4") ? p : "<p>" + p.replace(/\n/g, "<br>") + "</p>").join("");
    return h;
  }

  function launchLabel(state) {
    if (state === "alive") return "▶ Inject";
    if (state === "dormant") return "▶ Resume";
    if (state === "never") return "▶ Start";
    return "—";
  }

  async function launchStrategy(goalSlug, strategyId, btn) {
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "launching…";
    try {
      const r = await fetch("/api/morning/launch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal_slug: goalSlug, strategy_id: strategyId }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) {
        btn.textContent = "error";
        alert("Launch failed: " + (data.error || ("HTTP " + r.status)));
        setTimeout(() => { btn.textContent = originalLabel; btn.disabled = false; }, 2500);
        return;
      }
      const tag =
        data.action === "resumed" ? "✓ resumed"
        : data.action === "spawned" ? (data.session_id_saved ? "✓ spawned" : "✓ spawned*")
        : "✓ ok";
      btn.textContent = tag;
      // Reload so the strategy row picks up the new session_id / state.
      setTimeout(() => load(), 1500);
    } catch (e) {
      btn.textContent = "error";
      alert("Launch failed: " + e.message);
      setTimeout(() => { btn.textContent = originalLabel; btn.disabled = false; }, 2500);
    }
  }

  function renderStrategy(goalSlug, s) {
    const statusClass = s.status === "done" ? " done" : (s.status === "dropped" ? " dropped" : "");
    const dot = el("span", { class: "sess-dot " + s.session_state });
    let btn = null;
    if (s.session_state !== "dropped") {
      btn = el(
        "button",
        {
          class: "launch " + (
            s.session_state === "dormant" ? "dormant"
            : s.session_state === "never" ? "new"
            : ""
          ),
        },
        launchLabel(s.session_state)
      );
      btn.addEventListener("click", () => launchStrategy(goalSlug, s.id, btn));
    }
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
    document.getElementById("gd-intent").innerHTML = renderMarkdownSafe(detail.intent_markdown || "");
    document.title = "CCC · " + (detail.name || slug);

    const header = document.getElementById("gd-header");
    if (detail.accent) header.style.borderLeftColor = detail.accent;

    const strats = document.getElementById("gd-strategies");
    strats.innerHTML = "";
    for (const s of (detail.strategies || [])) strats.appendChild(renderStrategy(slug, s));

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
