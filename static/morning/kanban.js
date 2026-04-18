(function () {
  "use strict";

  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    for (const k in (attrs || {})) {
      if (k === "class") e.className = attrs[k];
      else if (k === "style" && typeof attrs[k] === "object") {
        // Object.assign doesn't touch CSS custom properties (e.g. "--accent")
        // — CSSStyleDeclaration only reacts to setProperty for those. Route
        // double-dashed keys through setProperty and normal keys through
        // direct assignment so callers can pass either in a single style obj.
        for (const sk in attrs[k]) {
          if (sk.startsWith("--")) e.style.setProperty(sk, attrs[k][sk]);
          else e.style[sk] = attrs[k][sk];
        }
      }
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

  // ---------------------------------------------------------------------
  // Draggable resize handle on the transcript pane
  // ---------------------------------------------------------------------

  const LS_PANE_WIDTH_KEY = "ccc.morning.pane.width";

  function applyPaneWidth(px) {
    document.documentElement.style.setProperty("--mk-pane-width", px + "px");
  }

  (function restorePaneWidth() {
    try {
      const raw = localStorage.getItem(LS_PANE_WIDTH_KEY);
      if (!raw) return;
      const n = parseInt(raw, 10);
      if (!isNaN(n) && n >= 320 && n <= 900) applyPaneWidth(n);
    } catch (e) { /* ignore */ }
  })();

  let paneDragging = false;

  function onPaneDragMove(e) {
    if (!paneDragging) return;
    // Pane is right-anchored, so its width = viewport width minus cursor X.
    const raw = window.innerWidth - e.clientX;
    const w = Math.max(320, Math.min(900, raw));
    applyPaneWidth(w);
  }

  function onPaneDragEnd() {
    if (!paneDragging) return;
    paneDragging = false;
    document.body.classList.remove("mk-dragging");
    document.removeEventListener("mousemove", onPaneDragMove);
    document.removeEventListener("mouseup", onPaneDragEnd);
    const cur = getComputedStyle(document.documentElement).getPropertyValue("--mk-pane-width").trim();
    const n = parseInt(cur, 10);
    if (!isNaN(n)) {
      try { localStorage.setItem(LS_PANE_WIDTH_KEY, String(n)); } catch (e) {}
    }
  }

  document.getElementById("mk-pane-handle").addEventListener("mousedown", (e) => {
    e.preventDefault();
    paneDragging = true;
    document.body.classList.add("mk-dragging");
    document.addEventListener("mousemove", onPaneDragMove);
    document.addEventListener("mouseup", onPaneDragEnd);
  });

  document.getElementById("mk-pane-close").addEventListener("click", closePane);
  document.getElementById("mk-pane-send").addEventListener("click", sendPaneMessage);
  document.getElementById("mk-pane-msg").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") sendPaneMessage();
  });
  // Escape as a backup close — if the X button misses for any reason (CSS
  // glitch, tiny hit area, stale listener) Esc always works.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !document.getElementById("mk-pane").hidden) {
      // Don't steal Escape while typing in the pane textarea unless it's empty
      const ta = document.getElementById("mk-pane-msg");
      if (document.activeElement === ta && ta.value) return;
      closePane();
    }
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
    // Fetch sessions + morning state in parallel — Today comes from state,
    // everything else from sessions. Paint once both land so the columns
    // appear together and share knownGoals for accent colors.
    let sState = null, stateBody = null;
    try {
      const [sResp, stResp] = await Promise.all([
        fetch("/api/morning/sessions"),
        fetch("/api/morning/state"),
      ]);
      if (!sResp.ok) throw new Error("sessions HTTP " + sResp.status);
      sState = await sResp.json();
      if (stResp.ok) stateBody = await stResp.json();
    } catch (e) {
      document.getElementById("refresh-meta").textContent = "load failed: " + e.message;
      return;
    }

    if (stateBody) {
      knownGoals = (stateBody.goals || []).map(g => ({
        slug: g.slug, name: g.name || g.slug, accent: g.accent || "#5ac8fa",
        life_area: g.life_area || "", ribbon: g.ribbon || null,
      }));
      knownGoalSlugs = knownGoals.map(g => g.slug);
      lastTodayData = stateBody.today || [];
    }

    lastSessionsData = sState;
    renderGoalsRow();
    renderBoard();
    document.getElementById("refresh-meta").textContent =
      "last refreshed " + new Date().toLocaleTimeString();
  }

  function renderBoard() {
    if (!lastSessionsData) return;
    const state = lastSessionsData;
    const matches = (slug) => !activeGoalFilter || slug === activeGoalFilter;
    const allSessions = state.sessions || [];
    const active = allSessions.filter(s => s.is_live && matches((s.morning || {}).goal_slug));
    const dormant = allSessions.filter(s => !s.is_live && matches((s.morning || {}).goal_slug));
    const backlog = (state.never_started || []).filter(n => matches(n.goal_slug));
    const todayFiltered = (lastTodayData || []).filter(t => matches(t.goal_slug));

    const paint = (listId, items, renderer, emptyText) => {
      const host = document.getElementById(listId);
      host.innerHTML = "";
      if (!items.length) {
        host.appendChild(el("div", { class: "mk-empty" }, emptyText));
        return;
      }
      for (const it of items) host.appendChild(renderer(it));
    };
    paint("col-today", todayFiltered, renderTodayCard, activeGoalFilter ? "no tasks for this goal" : "no tasks yet — braindump above");
    paint("col-backlog", backlog, renderNeverStarted, activeGoalFilter ? "no backlog for this goal" : "all strategies have sessions");
    paint("col-active", active, renderSession, activeGoalFilter ? "no active sessions for this goal" : "nothing running right now");
    paint("col-dormant", dormant, renderSession, activeGoalFilter ? "no dormant sessions for this goal" : "no dormant sessions");

    document.getElementById("count-today").textContent = todayFiltered.length;
    document.getElementById("count-backlog").textContent = backlog.length;
    document.getElementById("count-active").textContent = active.length;
    document.getElementById("count-dormant").textContent = dormant.length;
  }

  // Render the row of goal cards above the board. Clicking a card toggles the
  // board-wide goal filter — Today column, Backlog, Active, Dormant all
  // narrow to sessions/tasks tagged with that goal_slug.
  function renderGoalsRow() {
    const row = document.getElementById("goals-row");
    if (!row) return;
    row.innerHTML = "";
    for (const g of knownGoals) {
      const isActive = activeGoalFilter === g.slug;
      const dim = activeGoalFilter && !isActive;
      const card = el("div", {
        class: "mv-goal" + (isActive ? " active" : (dim ? " dim" : "")),
        style: { "--accent": g.accent },
      },
        el("div", { class: "cat" }, g.life_area || ""),
        el("div", { class: "name" }, g.name),
      );
      card.style.setProperty("--accent", g.accent);
      card.addEventListener("click", () => {
        activeGoalFilter = isActive ? null : g.slug;
        renderGoalsRow();
        renderBoard();
      });
      row.appendChild(card);
    }
    // Filter banner below the goals row
    const note = document.getElementById("goals-filter-note");
    note.innerHTML = "";
    if (activeGoalFilter) {
      const g = knownGoals.find(x => x.slug === activeGoalFilter);
      const clear = el("a", { href: "#" }, "clear");
      clear.addEventListener("click", (e) => {
        e.preventDefault();
        activeGoalFilter = null;
        renderGoalsRow();
        renderBoard();
      });
      note.appendChild(el("div", { class: "filter-note" },
        "Filtered to ", el("strong", {}, g ? g.name : activeGoalFilter), " — ", clear
      ));
    }
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

  let knownGoalSlugs = [];       // populated by load() below
  let knownGoals = [];           // [{slug, name, accent, life_area, ribbon}] — authoritative from /api/morning/state
  let activeGoalFilter = null;   // goal slug the board is filtered to, or null for "all"
  let lastSessionsData = null;   // cached /api/morning/sessions response so filter re-renders don't refetch
  const LS_ANALYSIS_KEY = "ccc.morning.braindump.lastAnalysis";
  const LS_DUMP_KEY = "ccc.morning.braindump.lastDump";

  function saveAnalysisToLS(items, dumpText) {
    try {
      localStorage.setItem(LS_ANALYSIS_KEY, JSON.stringify({
        items,
        savedAt: new Date().toISOString(),
      }));
      if (dumpText !== undefined) localStorage.setItem(LS_DUMP_KEY, dumpText);
    } catch (e) { /* quota / disabled — ignore */ }
  }

  function loadAnalysisFromLS() {
    try {
      const raw = localStorage.getItem(LS_ANALYSIS_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (e) { return null; }
  }

  function clearAnalysisLS() {
    try {
      localStorage.removeItem(LS_ANALYSIS_KEY);
      localStorage.removeItem(LS_DUMP_KEY);
    } catch (e) {}
  }

  async function acceptAnalysis(item, action, goalSlug, btn, row) {
    btn.disabled = true;
    const originalLabel = btn.textContent;
    btn.textContent = "…";
    try {
      const r = await fetch("/api/morning/braindump/accept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          goal_slug: goalSlug,
          action,
          text: item.original_text || "",
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) throw new Error(d.error || "HTTP " + r.status);
      btn.textContent = action === "tactical" ? "✓ added" : "✓ attached";
      row.style.opacity = "0.4";
      setTimeout(() => {
        row.remove();
        pruneFromLS(item);
        updateEmptyState();
      }, 600);
    } catch (e) {
      btn.textContent = "error";
      alert("Accept failed: " + e.message);
      setTimeout(() => { btn.textContent = originalLabel; btn.disabled = false; }, 2500);
    }
  }

  function dismissAnalysis(row, item) {
    row.style.transition = "opacity 0.2s";
    row.style.opacity = "0.25";
    setTimeout(() => { row.remove(); pruneFromLS(item); updateEmptyState(); }, 220);
  }

  function pruneFromLS(item) {
    const stored = loadAnalysisFromLS();
    if (!stored || !item) return;
    const key = (item.original_text || "") + "::" + (item.classification || "");
    stored.items = (stored.items || []).filter((it) => {
      return ((it.original_text || "") + "::" + (it.classification || "")) !== key;
    });
    if (!stored.items.length) {
      clearAnalysisLS();
    } else {
      saveAnalysisToLS(stored.items, undefined);
    }
  }

  function updateEmptyState() {
    const host = document.getElementById("mk-analysis");
    if (host.children.length === 0) host.hidden = true;
  }

  function renderAnalysisList(items) {
    const host = document.getElementById("mk-analysis");
    host.innerHTML = "";
    // Auto-accepted items live in the Today strip with their classification
    // and notes attached — no need for a duplicate card here. Only surface
    // items that couldn't auto-add (user still needs to pick a goal).
    const unresolved = items.filter(it => !it._autoAccepted);
    if (!unresolved.length) {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    for (const it of unresolved) host.appendChild(renderAnalysisItem(it));
  }

  function goalNameFor(slug) {
    const g = knownGoals.find(x => x.slug === slug);
    return g ? g.name : slug;
  }

  function renderAnalysisItem(item) {
    const cls = (item.classification || "NEW").toUpperCase();
    const actionsHost = el("div", { class: "mk-ana-actions" });

    // Status line on the card body reflects what already happened (auto-add)
    // versus what the user needs to decide. Typed items auto-land in Today —
    // the card is now a receipt, not a form.
    const statusLine = el("div", { class: "mk-ana-meta" });
    if (item._autoAccepted) {
      statusLine.appendChild(el("span", {},
        "✓ added to Today · goal: ",
        el("em", {}, goalNameFor(item._autoGoal))
      ));
    } else if (item._autoError) {
      statusLine.appendChild(el("span", {},
        "couldn't auto-add: " + item._autoError + " — pick a goal below"
      ));
    }

    const body = el("div", { class: "mk-ana-body" },
      el("div", { class: "mk-ana-text" }, item.original_text || ""),
      el("div", { class: "mk-ana-meta" },
        item.notes || "",
        item.matched_existing
          ? el("span", {}, " · matches: ", el("em", {}, item.matched_existing))
          : null
      ),
      statusLine,
    );

    // Goal picker only appears when we couldn't auto-add (no known goal
    // matched, or the request failed). Otherwise the card is purely a
    // receipt with an Undo button.
    let goalSelect = null;
    if (!item._autoAccepted) {
      goalSelect = el("select", { class: "mk-ana-goal-picker" });
      goalSelect.appendChild(el("option", { value: "" }, "(no goal)"));
      for (const g of knownGoals) {
        goalSelect.appendChild(el("option", { value: g.slug }, g.name));
      }
      if (item.suggested_goal && knownGoalSlugs.includes(item.suggested_goal)) {
        goalSelect.value = item.suggested_goal;
      }
      body.appendChild(el("div", { class: "mk-ana-controls" },
        el("label", { class: "mk-ana-label" }, "goal:"),
        goalSelect,
        actionsHost,
      ));
    } else {
      body.appendChild(el("div", { class: "mk-ana-controls" }, actionsHost));
    }

    const row = el("div", { class: "mk-ana-item " + cls },
      el("span", { class: "mk-ana-badge" }, cls),
      body,
    );

    if (!item._autoAccepted) {
      // Fallback manual flow: user picks a goal and clicks Add.
      const addTodayBtn = el("button", { class: "accept" }, "Add to today");
      addTodayBtn.addEventListener("click", () => {
        const slug = goalSelect.value;
        if (!slug) { alert("Pick a goal first (or use Dismiss)"); return; }
        acceptAnalysis(item, "tactical", slug, addTodayBtn, row);
      });
      actionsHost.appendChild(addTodayBtn);

      if (cls === "CONTEXT") {
        const attachBtn = el("button", {}, "Attach as note");
        attachBtn.addEventListener("click", () => {
          const slug = goalSelect.value;
          if (!slug) { alert("Pick a goal first (or use Dismiss)"); return; }
          acceptAnalysis(item, "context", slug, attachBtn, row);
        });
        actionsHost.appendChild(attachBtn);
      }
    } else if (cls === "CONTEXT") {
      // CONTEXT auto-adds to Today, but the user may also want to persist it
      // as a note on the goal. Offer that as a secondary action.
      const attachBtn = el("button", {}, "Also attach as note");
      attachBtn.addEventListener("click", () => {
        acceptAnalysis(item, "context", item._autoGoal, attachBtn, row);
      });
      actionsHost.appendChild(attachBtn);
    }

    const dismissBtn = el("button", {}, "Dismiss");
    dismissBtn.addEventListener("click", () => dismissAnalysis(row, item));
    actionsHost.appendChild(dismissBtn);

    return row;
  }

  async function analyzeDump() {
    const textarea = document.getElementById("mk-dump");
    const text = textarea.value.trim();
    if (!text) return;
    const btn = document.getElementById("mk-analyze");
    const status = document.getElementById("mk-analyze-status");
    btn.disabled = true;
    btn.textContent = "Analyzing…";

    // Elapsed-time ticker: forces the browser to repaint the status line
    // every second so the user sees it's actively working. Also doubles as a
    // guarantee that the initial "analyzing…" text is actually painted
    // before the blocking fetch starts (setInterval schedules past the
    // first animation frame).
    const start = Date.now();
    status.textContent = "analyzing · 0s";
    const tickerId = setInterval(() => {
      const secs = Math.round((Date.now() - start) / 1000);
      status.textContent = `analyzing · ${secs}s (typically 15–30s)`;
    }, 1000);

    // Yield a frame so the first "analyzing · 0s" paints before we issue
    // the blocking request.
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));

    try {
      const r = await fetch("/api/morning/braindump", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) throw new Error(d.error || "HTTP " + r.status);
      const items = d.items || [];

      // Auto-accept each item into Today. The user typed these — the app
      // shouldn't force them to click a button per item to confirm. We pick
      // the LLM's suggested_goal if it matches a known goal, else fall back
      // to the first known goal. Failed items fall back to the manual picker
      // automatically via renderAnalysisItem's _autoError branch.
      await autoAddToToday(items);
      saveAnalysisToLS(items, text);
      renderAnalysisList(items);
      renderTodayStrip();

      const elapsed = Math.round((Date.now() - start) / 1000);
      const added = items.filter(i => i._autoAccepted).length;
      status.textContent = `${items.length} items · ${added} auto-added to Today (${elapsed}s)`;
    } catch (e) {
      status.textContent = "failed: " + e.message;
    } finally {
      clearInterval(tickerId);
      btn.disabled = false;
      btn.textContent = "Analyze →";
    }
  }

  async function autoAddToToday(items) {
    if (!items.length) return;
    const fallbackGoal = knownGoalSlugs[0] || null;
    const jobs = items.map(async (item) => {
      let goal = item.suggested_goal && knownGoalSlugs.includes(item.suggested_goal)
        ? item.suggested_goal
        : fallbackGoal;
      if (!goal) {
        item._autoAccepted = false;
        item._autoError = "no goals configured";
        return;
      }
      try {
        const r = await fetch("/api/morning/braindump/accept", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            goal_slug: goal,
            action: "tactical",
            text: item.original_text || "",
            classification: item.classification || "",
            notes: item.notes || "",
            matched_existing: item.matched_existing || "",
          }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || !d.ok) {
          item._autoAccepted = false;
          item._autoError = d.error || ("HTTP " + r.status);
          return;
        }
        item._autoAccepted = true;
        item._autoGoal = goal;
      } catch (e) {
        item._autoAccepted = false;
        item._autoError = e.message;
      }
    });
    await Promise.all(jobs);
  }

  document.getElementById("mk-analyze").addEventListener("click", analyzeDump);
  document.getElementById("mk-dump").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") analyzeDump();
  });

  // Cached today items, paired with the sessions data. Populated by
  // refreshTodayCache() which fetches /api/morning/state once per load()
  // cycle. Kept separate from lastSessionsData because the "today" view
  // comes from user-tactical.jsonl, not from the sessions endpoint.
  let lastTodayData = [];

  async function refreshTodayCache() {
    try {
      const r = await fetch("/api/morning/state");
      if (r.ok) {
        const d = await r.json();
        lastTodayData = d.today || [];
      }
    } catch (e) { /* keep previous cache */ }
  }

  // Renders a Today task as an mk-card — same anatomy as renderNeverStarted
  // / renderSession so all four columns look identical: goal chip, text,
  // meta, actions. The classification badge is a corner decoration, not a
  // header variant — keeps the header line clean like other cards.
  function renderTodayCard(t) {
    const goal = knownGoals.find(g => g.slug === t.goal_slug);
    const accent = goal ? goal.accent : "#5ac8fa";
    const goalName = goal ? goal.name : (t.goal_slug || "—");
    const cls = (t.classification || "").toUpperCase();

    const card = el("div", { class: "mk-card", style: { "--accent": accent } },
      el("div", { class: "goal" }, goalName),
      el("div", { class: "text" }, t.text || ""),
    );
    if (t.notes || t.matched_existing) {
      const meta = el("div", { class: "meta" });
      if (t.notes) meta.appendChild(el("span", {}, t.notes));
      if (t.matched_existing) {
        meta.appendChild(el("span", {}, " · matches: "));
        meta.appendChild(el("em", {}, t.matched_existing));
      }
      card.appendChild(meta);
    }
    if (cls) {
      const badge = el("div", { class: "mk-card-badge " + cls }, cls);
      card.appendChild(badge);
    }
    if (t.user_tactical_id) {
      // Drag-to-reorder within Today, drag-to-Completed to dismiss. The
      // Done button is intentionally gone — completion is a drag gesture,
      // not a click, so the user signals it the same way across columns.
      card.draggable = true;
      card.dataset.taskId = t.user_tactical_id;
      card.dataset.sourceCol = "today";
      wireCardDrag(card);
    }
    return card;
  }

  function renderCompletedCard(t) {
    const goal = knownGoals.find(g => g.slug === t.goal_slug);
    const accent = goal ? goal.accent : "#5ac8fa";
    const goalName = goal ? goal.name : (t.goal_slug || "—");
    const card = el("div", { class: "mk-card", style: { "--accent": accent } },
      el("div", { class: "goal" }, goalName),
      el("div", { class: "text" }, t.text || ""),
    );
    if (t.dismissed_at) {
      card.appendChild(el("div", { class: "meta" }, "done " + t.dismissed_at.replace("T", " ").replace("Z", "")));
    }
    return card;
  }

  // Legacy name kept for callers (restorePreviousAnalysis, analyzeDump). Now
  // just triggers a full board repaint after refreshing the today cache —
  // keeps Today in lock-step with the other columns.
  async function renderTodayStrip() {
    await refreshTodayCache();
    renderBoard();
  }

  // Defensive fallback: document-level delegation so the pane-close
  // button still works if its direct listener gets clobbered by a
  // re-render.
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t && (t.id === "mk-pane-close" ||
              (t.closest && t.closest("#mk-pane-close")))) {
      closePane();
    }
  });

  // Restore the last braindump analysis so reloading the page doesn't wipe
  // the cards. Also restores the raw dump text so the user can see what
  // they typed.
  async function restorePreviousAnalysis() {
    const stored = loadAnalysisFromLS();
    if (stored && Array.isArray(stored.items) && stored.items.length) {
      // Cards stored before auto-accept shipped still have no `_autoAccepted`
      // flag. Push those through the same auto-add flow so the user isn't
      // left with a page of manual Add-to-today buttons after reload.
      const pending = stored.items.filter(i => !i._autoAccepted && !i._autoError);
      if (pending.length) {
        await autoAddToToday(pending);
        saveAnalysisToLS(stored.items, undefined);
      }
      renderAnalysisList(stored.items);
      renderTodayStrip();
      const status = document.getElementById("mk-analyze-status");
      if (status) {
        const savedStr = stored.savedAt ? new Date(stored.savedAt).toLocaleTimeString() : "";
        const added = stored.items.filter(i => i._autoAccepted).length;
        status.textContent = stored.items.length + " items from " + savedStr
          + " · " + added + " in Today";
      }
    }
    try {
      const lastDump = localStorage.getItem(LS_DUMP_KEY);
      if (lastDump) document.getElementById("mk-dump").value = lastDump;
    } catch (e) {}
  }

  setGreeting();
  document.getElementById("refresh-now").addEventListener("click", load);
  // Populate knownGoals first, then restore. Otherwise restored cards that
  // need the manual goal picker render with an empty <select>.
  (async () => {
    await load();
    restorePreviousAnalysis();
  })();
})();
