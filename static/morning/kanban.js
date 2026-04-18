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

  function renderNeverStarted(item) {
    const card = el("div", { class: "mk-card", style: { "--accent": item.goal_accent || "#5ac8fa" } },
      el("div", { class: "goal" }, item.goal_name || item.goal_slug),
      el("div", { class: "text" }, item.strategy_text || item.strategy_id),
      el("div", { class: "meta" }, "no session yet")
    );
    const launchBtn = el("button", { class: "primary" }, "▶ Start");
    launchBtn.addEventListener("click", async () => {
      launchBtn.disabled = true;
      launchBtn.textContent = "launching…";
      try {
        const r = await fetch("/api/morning/launch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ goal_slug: item.goal_slug, strategy_id: item.strategy_id }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || !d.ok) throw new Error(d.error || "HTTP " + r.status);
        launchBtn.textContent = "✓ spawned";
        setTimeout(load, 2000);
      } catch (e) {
        launchBtn.textContent = "error";
        alert("Launch failed: " + e.message);
        launchBtn.disabled = false;
      }
    });
    const detailLink = el("a", {
      class: "btn",
      href: "/morning/goals/" + encodeURIComponent(item.goal_slug),
    }, "goal →");
    card.appendChild(el("div", { class: "actions" }, launchBtn, detailLink));
    return card;
  }

  function renderSession(sess) {
    const morning = sess.morning || {};
    const card = el("div", { class: "mk-card", style: { "--accent": morning.goal_accent || "#5ac8fa" } },
      el("div", { class: "goal" }, morning.goal_name || morning.goal_slug || ""),
      el("div", { class: "text" }, morning.strategy_text || sess.display_name || sess.first_message || "(untitled)"),
      el("div", { class: "meta" },
        "session " + (sess.session_id || "").slice(0, 8) +
        (sess.is_live ? " · alive" : "") +
        (sess.modified_human ? " · " + sess.modified_human : "")
      )
    );
    const actions = el("div", { class: "actions" });
    if (sess.is_live) {
      const injectBtn = el("button", { class: "primary" }, "▶ Inject");
      injectBtn.addEventListener("click", () => launchAction(injectBtn, morning));
      actions.appendChild(injectBtn);
    } else {
      const resumeBtn = el("button", { class: "warn" }, "▶ Resume");
      resumeBtn.addEventListener("click", () => launchAction(resumeBtn, morning));
      actions.appendChild(resumeBtn);
    }
    const detailLink = el("a", {
      class: "btn",
      href: "/morning/goals/" + encodeURIComponent(morning.goal_slug || ""),
    }, "goal →");
    actions.appendChild(detailLink);
    card.appendChild(actions);
    return card;
  }

  async function launchAction(btn, morning) {
    if (!morning.goal_slug || !morning.strategy_id) return;
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "…";
    try {
      const r = await fetch("/api/morning/launch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal_slug: morning.goal_slug, strategy_id: morning.strategy_id }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) throw new Error(d.error || "HTTP " + r.status);
      btn.textContent = d.action === "resumed" ? "✓ resumed" : (d.action === "spawned" ? "✓ spawned" : "✓");
      setTimeout(load, 1500);
    } catch (e) {
      btn.textContent = "error";
      alert(e.message);
      setTimeout(() => { btn.textContent = label; btn.disabled = false; }, 2500);
    }
  }

  async function load() {
    let state;
    try {
      const r = await fetch("/api/morning/sessions");
      if (!r.ok) throw new Error("HTTP " + r.status);
      state = await r.json();
    } catch (e) {
      document.getElementById("refresh-meta").textContent = "load failed: " + e.message;
      return;
    }

    const active = (state.sessions || []).filter(s => s.is_live);
    const dormant = (state.sessions || []).filter(s => !s.is_live);
    const backlog = state.never_started || [];

    const paint = (listId, items, renderer, emptyText) => {
      const host = document.getElementById(listId);
      host.innerHTML = "";
      if (!items.length) {
        host.appendChild(el("div", { class: "mk-empty" }, emptyText));
        return;
      }
      for (const it of items) host.appendChild(renderer(it));
    };
    paint("col-backlog", backlog, renderNeverStarted, "all strategies have sessions");
    paint("col-active", active, renderSession, "nothing running right now");
    paint("col-dormant", dormant, renderSession, "no dormant sessions");

    document.getElementById("count-backlog").textContent = backlog.length;
    document.getElementById("count-active").textContent = active.length;
    document.getElementById("count-dormant").textContent = dormant.length;
    document.getElementById("refresh-meta").textContent =
      "last refreshed " + new Date().toLocaleTimeString();
  }

  document.getElementById("refresh-now").addEventListener("click", load);
  load();
})();
