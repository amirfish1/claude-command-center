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

  function renderGoal(goal) {
    const card = el("div", {
      class: "mv-goal",
      style: { "--accent": goal.accent || "#5ac8fa" },
      onclick: () => { window.location.href = "/morning/goals/" + encodeURIComponent(goal.slug); }
    },
      el("div", { class: "cat" }, goal.life_area),
      el("div", { class: "name" }, goal.name),
      el("div", { class: "ribbon", style: { "border-left-color": goal.accent || "#5ac8fa" } },
        el("span", { class: "date" }, (goal.ribbon && goal.ribbon.date || "") + (goal.ribbon && goal.ribbon.source ? " (" + goal.ribbon.source + ")" : "")),
        (goal.ribbon && goal.ribbon.text) || ""
      )
    );
    // Inline color set via style object above isn't picking up CSS variable on all browsers
    // for border-left. Set the CSS variable directly:
    card.style.setProperty("--accent", goal.accent || "#5ac8fa");
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

  function renderInboxItem(item) {
    return el("div", { class: "mv-inbox-item" },
      el("span", { class: "src" }, item.source + "\n" + age(item.age_days) + " ago"),
      el("span", { style: { flex: "1" } }, item.text),
      el("span", { class: "actions" },
        el("button", { class: "promote" }, "promote →"),
        el("button", {}, "dismiss")
      )
    );
  }

  async function load() {
    let state;
    try {
      const r = await fetch("/api/morning/state");
      if (!r.ok) throw new Error("HTTP " + r.status);
      state = await r.json();
    } catch (e) {
      document.getElementById("refresh-meta").textContent = "load failed: " + e.message;
      return;
    }

    const goalsRow = document.getElementById("goals-row");
    goalsRow.innerHTML = "";
    for (const g of state.goals) goalsRow.appendChild(renderGoal(g));

    const strat = document.getElementById("strategic-list");
    strat.innerHTML = "";
    for (const t of state.strategic) strat.appendChild(renderTaskRow(t));

    const tact = document.getElementById("tactical-list");
    tact.innerHTML = "";
    for (const t of state.tactical) tact.appendChild(renderTaskRow(t));

    const inb = document.getElementById("inbox-list");
    inb.innerHTML = "";
    for (const i of state.inbox) inb.appendChild(renderInboxItem(i));
    document.getElementById("inbox-count").textContent =
      "— " + state.inbox.length + " candidates from free-form sources";

    const ts = state.last_refreshed ? new Date(state.last_refreshed) : null;
    document.getElementById("refresh-meta").textContent =
      ts ? ("last refreshed " + ts.toLocaleTimeString()) : "";
  }

  document.getElementById("scan-now").addEventListener("click", load);
  load();
})();
