(function () {
  "use strict";

  function age(days) {
    if (days === 0) return "today";
    if (days === 1) return "1d";
    return days + "d";
  }

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

  // Active filter — which goal slug we're narrowing the strategic/tactical
  // lists to. null means "show all". Click a goal card to set; click the same
  // card again (or the Clear link) to reset.
  let activeFilter = null;

  function renderGoal(goal) {
    const isActive = activeFilter === goal.slug;
    const openLink = el("a", {
      class: "mv-goal-open",
      href: "/morning/goals/" + encodeURIComponent(goal.slug),
    }, "details →");
    openLink.addEventListener("click", (e) => e.stopPropagation());
    const card = el("div", {
      class: "mv-goal" + (isActive ? " active" : (activeFilter ? " dim" : "")),
      style: { "--accent": goal.accent || "#5ac8fa" },
    },
      el("div", { class: "cat" }, goal.life_area),
      el("div", { class: "name" }, goal.name),
      el("div", { class: "ribbon", style: { "border-left-color": goal.accent || "#5ac8fa" } },
        el("span", { class: "date" }, (goal.ribbon && goal.ribbon.date || "") + (goal.ribbon && goal.ribbon.source ? " (" + goal.ribbon.source + ")" : "")),
        (goal.ribbon && goal.ribbon.text) || ""
      ),
      openLink
    );
    card.style.setProperty("--accent", goal.accent || "#5ac8fa");
    card.addEventListener("click", () => {
      activeFilter = (activeFilter === goal.slug) ? null : goal.slug;
      render();  // re-render with new filter
    });
    return card;
  }

  function renderTaskRow(task) {
    return el("div", { class: "mv-task" },
      el("span", { class: "pri " + task.priority }, task.priority),
      task.goal_slug ? el("span", { class: "goal-chip" }, task.goal_slug + " ›") : null,
      el("span", { class: "text" }, task.text),
      el("span", { class: "src" }, task.source),
      el("span", { class: "ago" }, age(task.age_days))
    );
  }

  async function promoteInboxItem(item, btn, row, goalSlugs) {
    // Minimal picker via prompt() — upgrade later to a styled popover.
    const choices = goalSlugs.join(" / ");
    const slug = window.prompt(`Promote to which goal?\n\n${choices}`, goalSlugs[0] || "");
    if (!slug) return;
    if (!goalSlugs.includes(slug)) {
      alert(`Unknown goal slug: ${slug}`);
      return;
    }
    const as = window.prompt("Promote as: tactical / strategy / context", "tactical");
    if (!as) return;
    btn.disabled = true;
    btn.textContent = "promoting…";
    try {
      const r = await fetch("/api/morning/inbox/promote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: item.id, goal_slug: slug, as }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) throw new Error(data.error || "HTTP " + r.status);
      row.style.opacity = "0.4";
      btn.textContent = `✓ → ${slug}`;
      setTimeout(load, 1200);
    } catch (e) {
      btn.textContent = "error";
      alert("Promote failed: " + e.message);
      btn.disabled = false;
    }
  }

  async function dismissInboxItem(item, btn, row) {
    btn.disabled = true;
    btn.textContent = "dismissing…";
    try {
      const r = await fetch("/api/morning/inbox/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: item.id }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) throw new Error(data.error || "HTTP " + r.status);
      row.style.opacity = "0.3";
      btn.textContent = "✓ dismissed";
      setTimeout(load, 800);
    } catch (e) {
      btn.textContent = "error";
      alert("Dismiss failed: " + e.message);
      btn.disabled = false;
    }
  }

  function renderInboxItem(item, goalSlugs) {
    const promoteBtn = el("button", { class: "promote" }, "promote →");
    const dismissBtn = el("button", {}, "dismiss");
    const row = el("div", { class: "mv-inbox-item" },
      el("span", { class: "src" }, (item.source || "") + "\n" + age(item.age_days || 0) + " ago"),
      el("span", { style: { flex: "1" } }, item.text || ""),
      el("span", { class: "actions" }, promoteBtn, dismissBtn)
    );
    if (!item.id) {
      promoteBtn.disabled = true;
      promoteBtn.title = "candidate missing id — can't promote";
      dismissBtn.disabled = true;
    } else {
      promoteBtn.addEventListener("click", () => promoteInboxItem(item, promoteBtn, row, goalSlugs));
      dismissBtn.addEventListener("click", () => dismissInboxItem(item, dismissBtn, row));
    }
    return row;
  }

  // Cached state so the filter toggle can re-render without refetching.
  let lastState = null;

  function render() {
    if (!lastState) return;
    const state = lastState;

    const goalsRow = document.getElementById("goals-row");
    goalsRow.innerHTML = "";
    for (const g of state.goals) goalsRow.appendChild(renderGoal(g));

    // Filter banner (only shown when a goal is selected)
    const filterNote = document.getElementById("filter-note");
    if (filterNote) filterNote.remove();
    if (activeFilter) {
      const activeGoal = state.goals.find(g => g.slug === activeFilter);
      const banner = el("div", { id: "filter-note", class: "filter-note" },
        "Filtered to ",
        el("strong", {}, activeGoal ? activeGoal.name : activeFilter),
        " — "
      );
      const clear = el("a", { href: "#" }, "clear");
      clear.addEventListener("click", (e) => { e.preventDefault(); activeFilter = null; render(); });
      banner.appendChild(clear);
      goalsRow.insertAdjacentElement("afterend", banner);
    }

    const matches = (row) => !activeFilter || row.goal_slug === activeFilter;

    const strat = document.getElementById("strategic-list");
    strat.innerHTML = "";
    const stratFiltered = state.strategic.filter(matches);
    for (const t of stratFiltered) strat.appendChild(renderTaskRow(t));
    if (activeFilter && !stratFiltered.length) {
      strat.appendChild(el("div", { class: "muted" }, "no strategic rows for this goal"));
    }

    const tact = document.getElementById("tactical-list");
    tact.innerHTML = "";
    const tactFiltered = state.tactical.filter(matches);
    for (const t of tactFiltered) tact.appendChild(renderTaskRow(t));
    if (activeFilter && !tactFiltered.length) {
      tact.appendChild(el("div", { class: "muted" }, "no tactical items tagged to this goal"));
    }

    const inb = document.getElementById("inbox-list");
    inb.innerHTML = "";
    const goalSlugs = state.goals.map(g => g.slug);
    // Inbox is not filtered (it's always "needs triage for any goal"),
    // but if filtering we still render it so promote flows stay available.
    for (const i of state.inbox) inb.appendChild(renderInboxItem(i, goalSlugs));
    document.getElementById("inbox-count").textContent =
      "— " + state.inbox.length + " candidates from free-form sources";

    const ts = state.last_refreshed ? new Date(state.last_refreshed) : null;
    document.getElementById("refresh-meta").textContent =
      ts ? ("last refreshed " + ts.toLocaleTimeString()) : "";
  }

  async function load() {
    try {
      const r = await fetch("/api/morning/state");
      if (!r.ok) throw new Error("HTTP " + r.status);
      lastState = await r.json();
    } catch (e) {
      document.getElementById("refresh-meta").textContent = "load failed: " + e.message;
      return;
    }
    render();
  }

  async function scanNow() {
    const btn = document.getElementById("scan-now");
    btn.disabled = true;
    const originalLabel = btn.textContent;
    btn.textContent = "ingesting…";
    try {
      // Fire the Apple Notes extractor in background (non-blocking on the
      // server side — this call returns immediately). Then reload state so
      // the rest of the page refreshes.
      await fetch("/api/morning/ingest/run", { method: "POST" });
    } catch (e) {
      // Non-fatal; state refresh still worth doing.
    }
    await load();
    btn.textContent = originalLabel;
    btn.disabled = false;
  }

  document.getElementById("scan-now").addEventListener("click", scanNow);
  load();
})();
