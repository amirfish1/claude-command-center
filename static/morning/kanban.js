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
    const card = el("div", { class: "mk-card selectable", style: { "--accent": morning.goal_accent || "#5ac8fa" } },
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
    card.addEventListener("click", (e) => {
      if (e.target.tagName === "BUTTON" || e.target.tagName === "A") return;
      openPane(sess);
      document.querySelectorAll(".mk-card.selected").forEach(c => c.classList.remove("selected"));
      card.classList.add("selected");
    });
    return card;
  }

  // ---------------------------------------------------------------------
  // Right-side transcript pane
  // ---------------------------------------------------------------------

  let paneSession = null;
  let paneAfter = 0;
  let panePollTimer = null;

  function openPane(sess) {
    paneSession = sess;
    paneAfter = 0;
    document.getElementById("mk-pane").hidden = false;
    document.getElementById("mk-wrap").classList.add("pane-open");
    const m = sess.morning || {};
    document.getElementById("mk-pane-title").textContent =
      (m.goal_name || m.goal_slug || "") + " · " + (m.strategy_text || m.strategy_id || "");
    document.getElementById("mk-pane-meta").textContent =
      "session " + (sess.session_id || "").slice(0, 8) +
      (sess.is_live ? " · alive" : " · dormant") +
      (sess.modified_human ? " · " + sess.modified_human : "");
    document.getElementById("mk-pane-transcript").innerHTML = "";
    document.getElementById("mk-pane-transcript").appendChild(
      el("div", { class: "mk-empty" }, "Loading transcript…")
    );
    loadTranscript(true);
    if (panePollTimer) clearInterval(panePollTimer);
    panePollTimer = setInterval(() => loadTranscript(false), 4000);
  }

  function closePane() {
    document.getElementById("mk-pane").hidden = true;
    document.getElementById("mk-wrap").classList.remove("pane-open");
    paneSession = null;
    paneAfter = 0;
    if (panePollTimer) {
      clearInterval(panePollTimer);
      panePollTimer = null;
    }
    document.querySelectorAll(".mk-card.selected").forEach(c => c.classList.remove("selected"));
  }

  async function loadTranscript(replace) {
    if (!paneSession) return;
    let data;
    try {
      const r = await fetch(`/api/morning/conversation/${encodeURIComponent(paneSession.session_id)}?after=${paneAfter}`);
      if (!r.ok) throw new Error("HTTP " + r.status);
      data = await r.json();
    } catch (e) {
      return;
    }
    const host = document.getElementById("mk-pane-transcript");
    if (replace) host.innerHTML = "";
    for (const ev of (data.events || [])) {
      host.appendChild(renderEvent(ev));
    }
    if (!data.events || !data.events.length) {
      if (replace) {
        host.appendChild(el("div", { class: "mk-empty" }, "No transcript events yet."));
      }
    }
    paneAfter = data.last_line || paneAfter;
    host.scrollTop = host.scrollHeight;
  }

  function renderEvent(ev) {
    if (ev.type === "user_text") {
      return el("div", { class: "mk-ev user" },
        el("div", { class: "role" }, "you"),
        el("div", { class: "body" }, ev.text || "")
      );
    }
    if (ev.type === "tool_result") {
      return el("div", { class: "mk-ev tool_result" },
        el("div", { class: "role" }, "tool result")
      );
    }
    if (ev.type === "assistant") {
      const host = el("div", { class: "mk-ev assistant" },
        el("div", { class: "role" }, "claude")
      );
      for (const b of (ev.blocks || [])) {
        if (b.kind === "text") {
          host.appendChild(el("div", { class: "blk text" }, b.text || ""));
        } else if (b.kind === "tool_use") {
          host.appendChild(el("div", { class: "blk tool_use" },
            el("span", { class: "tool-name" }, b.name || "?"),
            el("span", {}, b.detail ? " · " + b.detail : "")
          ));
        } else if (b.kind === "thinking") {
          host.appendChild(el("div", { class: "blk thinking" }, b.text || ""));
        }
      }
      return host;
    }
    if (ev.type === "result") {
      return el("div", { class: "mk-ev result" },
        el("div", { class: "role" }, "— turn complete —")
      );
    }
    return el("div", {});
  }

  async function sendPaneMessage() {
    if (!paneSession) return;
    const textarea = document.getElementById("mk-pane-msg");
    const msg = textarea.value.trim();
    if (!msg) return;
    const m = paneSession.morning || {};
    if (!m.goal_slug || !m.strategy_id) return;
    const sendBtn = document.getElementById("mk-pane-send");
    sendBtn.disabled = true;
    sendBtn.textContent = "sending…";
    try {
      const r = await fetch("/api/morning/launch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          goal_slug: m.goal_slug,
          strategy_id: m.strategy_id,
          message: msg,
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) throw new Error(d.error || "HTTP " + r.status);
      textarea.value = "";
      sendBtn.textContent = d.action === "resumed" ? "✓ sent" : "✓";
      setTimeout(() => { sendBtn.textContent = "Send"; sendBtn.disabled = false; }, 1500);
      setTimeout(() => loadTranscript(false), 1000);
    } catch (e) {
      sendBtn.textContent = "error";
      alert("Send failed: " + e.message);
      setTimeout(() => { sendBtn.textContent = "Send"; sendBtn.disabled = false; }, 2500);
    }
  }

  document.getElementById("mk-pane-close").addEventListener("click", closePane);
  document.getElementById("mk-pane-send").addEventListener("click", sendPaneMessage);
  document.getElementById("mk-pane-msg").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") sendPaneMessage();
  });

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
    // board toggle summary
    const summary = `${backlog.length} backlog · ${active.length} active · ${dormant.length} dormant`;
    const mkBoardCount = document.getElementById("mk-board-count");
    if (mkBoardCount) mkBoardCount.textContent = summary;
    document.getElementById("refresh-meta").textContent =
      "last refreshed " + new Date().toLocaleTimeString();
  }

  // ---------------------------------------------------------------------
  // Morning hero — greeting + brain dump analysis
  // ---------------------------------------------------------------------

  function setGreeting() {
    const now = new Date();
    const hour = now.getHours();
    let period = "morning";
    if (hour >= 12 && hour < 17) period = "afternoon";
    else if (hour >= 17 || hour < 5) period = "evening";
    const months = ["January","February","March","April","May","June",
                    "July","August","September","October","November","December"];
    const dateStr = months[now.getMonth()] + " " + now.getDate() + ", " +
      now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    document.getElementById("mk-greeting").textContent =
      "Good " + period + ", Amir.";
    document.getElementById("mk-subgreeting").textContent =
      "It's " + dateStr + ". What's on your mind?";
  }

  function renderAnalysisItem(item) {
    const cls = (item.classification || "NEW").toUpperCase();
    const host = el("div", { class: "mk-ana-item " + cls },
      el("span", { class: "mk-ana-badge" }, cls),
      el("div", { class: "mk-ana-body" },
        el("div", { class: "mk-ana-text" }, item.original_text || ""),
        el("div", { class: "mk-ana-meta" },
          item.notes || "",
          item.matched_existing
            ? el("span", {}, " · matches: ", el("em", {}, item.matched_existing))
            : null,
          item.suggested_goal
            ? el("span", {}, " · ", el("span", { class: "mk-ana-goal" }, item.suggested_goal))
            : null
        )
      )
    );
    return host;
  }

  async function analyzeDump() {
    const textarea = document.getElementById("mk-dump");
    const text = textarea.value.trim();
    if (!text) return;
    const btn = document.getElementById("mk-analyze");
    const status = document.getElementById("mk-analyze-status");
    btn.disabled = true;
    status.textContent = "analyzing (takes ~15s)…";
    try {
      const r = await fetch("/api/morning/braindump", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) throw new Error(d.error || "HTTP " + r.status);
      const host = document.getElementById("mk-analysis");
      host.hidden = false;
      host.innerHTML = "";
      const items = d.items || [];
      if (!items.length) {
        host.appendChild(el("div", { class: "muted" }, "No items found."));
      } else {
        for (const it of items) host.appendChild(renderAnalysisItem(it));
      }
      status.textContent = items.length + " items · "
        + items.filter(i => (i.classification || "").toUpperCase() === "NEW").length + " new · "
        + items.filter(i => (i.classification || "").toUpperCase() === "EXISTING").length + " existing";
    } catch (e) {
      status.textContent = "failed: " + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  document.getElementById("mk-analyze").addEventListener("click", analyzeDump);
  document.getElementById("mk-dump").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") analyzeDump();
  });

  // Collapsible kanban board — hidden by default so the hero has the stage.
  const boardToggle = document.getElementById("mk-board-toggle");
  boardToggle.addEventListener("click", () => {
    const board = document.getElementById("mk-board");
    const isHidden = board.hidden;
    board.hidden = !isHidden;
    // Flip the triangle on the button's label.
    boardToggle.innerHTML = boardToggle.innerHTML.replace(/^[▸▾]/, isHidden ? "▾" : "▸");
  });

  setGreeting();
  document.getElementById("refresh-now").addEventListener("click", load);
  load();
})();
