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
    card.draggable = true;
    card.dataset.sourceCol = "backlog";
    card.dataset.goalSlug = item.goal_slug || "";
    card.dataset.strategyId = item.strategy_id || "";
    wireCardDrag(card);
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
    const col = sess.is_live ? "active" : "dormant";
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
    card.draggable = true;
    card.dataset.sourceCol = col;
    card.dataset.goalSlug = morning.goal_slug || "";
    card.dataset.strategyId = morning.strategy_id || "";
    card.dataset.sessionId = sess.session_id || "";
    wireCardDrag(card);
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
    const sendBtn = document.getElementById("mk-pane-send");
    sendBtn.disabled = true;
    sendBtn.textContent = "sending…";
    let url, body;
    if (m.user_tactical_id) {
      // Task-bound session: route through the task launch endpoint so the
      // resume message uses the task framing (not the strategy framing).
      url = "/api/morning/today/launch";
      body = { id: m.user_tactical_id, message: msg };
    } else if (m.goal_slug && m.strategy_id) {
      url = "/api/morning/launch";
      body = { goal_slug: m.goal_slug, strategy_id: m.strategy_id, message: msg };
    } else {
      sendBtn.textContent = "no target";
      setTimeout(() => { sendBtn.textContent = "Send"; sendBtn.disabled = false; }, 1500);
      return;
    }
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
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

  const LS_PANE_WIDTH_KEY = "ccc-morning-pane-width";

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
    // Task-bound sessions (user_tactical_id + no strategy_id) have no
    // entry in goal.md — they route through the task launch endpoint so
    // /api/morning/launch's "unknown strategy" check doesn't trip.
    const isTask = morning.user_tactical_id && !morning.strategy_id;
    if (!isTask && (!morning.goal_slug || !morning.strategy_id)) return;
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "…";
    try {
      const url = isTask ? "/api/morning/today/launch" : "/api/morning/launch";
      const body = isTask
        ? { id: morning.user_tactical_id }
        : { goal_slug: morning.goal_slug, strategy_id: morning.strategy_id };
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
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
      lastCompletedData = stateBody.completed || [];
    }

    lastSessionsData = sState;
    renderGoalsRow();
    renderBoard();
    document.getElementById("refresh-meta").textContent =
      "last refreshed " + new Date().toLocaleTimeString();
  }

  // Dev-kanban-style derived classification. Takes a session record (with the
  // stage fields served by /api/morning/sessions) and returns the column it
  // belongs to: "active" / "dormant" / "review". Columns are derived from
  // signals — the user doesn't hand-place sessions, same as the dev kanban.
  function classifyKanbanColumn(sess) {
    const m = sess.morning || {};
    const isTask = !!m.user_tactical_id && !m.strategy_id;
    const live = !!sess.is_live;
    const has_edit = !!sess.has_edit;
    const has_commit = !!sess.has_commit;
    const has_push = !!sess.has_push;
    const last_event = sess.last_event_type;
    const sidecar_status = sess.sidecar_status;
    const sidecar_has_writes = !!sess.sidecar_has_writes;

    // Live sessions with sidecar: trust sidecar over stage history.
    if (live && sidecar_status) {
      if (sidecar_status === "waiting" || sidecar_has_writes) return "active";
      return "active";  // planning-style live — morning collapses into active
    }
    // Pushed or committed (dead or live-without-sidecar) → review.
    if (has_push || has_commit) return "review";
    // Dormant + edits + last turn was assistant = work waiting for human review.
    if (!live && has_edit && last_event === "assistant") return "review";
    // Live without sidecar, any stage → active.
    if (live) return "active";
    // Task-bound sessions fall through to dormant naturally.
    if (isTask) return "dormant";
    return "dormant";
  }

  function renderBoard() {
    if (!lastSessionsData) return;
    const state = lastSessionsData;
    const matches = (slug) => !activeGoalFilter || slug === activeGoalFilter;
    const allSessions = state.sessions || [];
    // Sessions classify derivedly — same model as the dev kanban. Run each
    // session through classifyKanbanColumn and bucket. Goal-filter still
    // applies on top of the classifier result.
    const active = [];
    const dormant = [];
    const review = [];
    for (const s of allSessions) {
      if (!matches((s.morning || {}).goal_slug)) continue;
      const col = classifyKanbanColumn(s);
      if (col === "active") active.push(s);
      else if (col === "review") review.push(s);
      else dormant.push(s);
    }
    // Corresponding: tasks with a session are represented by their session
    // card in Active/Dormant, so hide them from the Today column.
    const sessionOwnedTaskIds = new Set(
      allSessions.map(s => (s.morning || {}).user_tactical_id).filter(Boolean)
    );
    const backlog = (state.never_started || []).filter(n => matches(n.goal_slug));
    const todayFiltered = (lastTodayData || [])
      .filter(t => matches(t.goal_slug))
      .filter(t => !sessionOwnedTaskIds.has(t.user_tactical_id));

    const paint = (listId, items, renderer, emptyText) => {
      const host = document.getElementById(listId);
      host.innerHTML = "";
      if (!items.length) {
        host.appendChild(el("div", { class: "mk-empty" }, emptyText));
        return;
      }
      for (const it of items) host.appendChild(renderer(it));
    };
    const completedData = (lastCompletedData || []).filter(t => matches(t.goal_slug));
    paint("col-today", todayFiltered, renderTodayCard, activeGoalFilter ? "no tasks for this goal" : "no tasks yet — braindump above");
    paint("col-backlog", backlog, renderNeverStarted, activeGoalFilter ? "no backlog for this goal" : "all strategies have sessions");
    paint("col-active", active, renderSession, activeGoalFilter ? "no active sessions for this goal" : "nothing running right now");
    paint("col-dormant", dormant, renderSession, activeGoalFilter ? "no dormant sessions for this goal" : "no dormant sessions");
    paint("col-review", review, renderSession, activeGoalFilter ? "no review items for this goal" : "no sessions waiting on review");
    paint("col-completed", completedData, renderCompletedCard, activeGoalFilter ? "no completed tasks for this goal" : "drag tasks here to complete");

    document.getElementById("count-today").textContent = todayFiltered.length;
    document.getElementById("count-backlog").textContent = backlog.length;
    document.getElementById("count-active").textContent = active.length;
    document.getElementById("count-dormant").textContent = dormant.length;
    document.getElementById("count-review").textContent = review.length;
    document.getElementById("count-completed").textContent = completedData.length;

    renderAttention();
  }

  let lastCompletedData = [];

  // Derived "Needs your attention" strip. Same pattern as the dev kanban's
  // NYA panel: iterate sessions, match each against a signal predicate, emit
  // a row. No server call — predicates run over `lastSessionsData` which is
  // already loaded. Click a row to open the session's pane.
  function renderAttention() {
    const section = document.getElementById("mk-attention-section");
    const list = document.getElementById("mk-attention-list");
    const countEl = document.getElementById("mk-attention-count");
    if (!section || !list) return;
    const sessions = (lastSessionsData && lastSessionsData.sessions) || [];
    const rows = [];
    for (const s of sessions) {
      const m = s.morning || {};
      if (activeGoalFilter && m.goal_slug !== activeGoalFilter) continue;
      let kind = null, detail = "";
      if (s.is_live && s.pending_tool) {
        kind = "pending_tool";
        detail = "tool awaiting approval: " + (s.pending_tool || "");
      } else if (s.is_live && s.sidecar_status === "waiting") {
        kind = "sidecar_waiting";
        detail = "session waiting for input";
      } else if (!s.is_live && s.has_edit && !s.has_commit) {
        kind = "uncommitted_edits";
        detail = "edits on disk, no commit yet";
      } else if (!s.is_live && s.has_commit && !s.has_push) {
        kind = "committed_not_pushed";
        detail = "commits waiting to push";
      }
      if (kind) rows.push({ sess: s, kind, detail });
    }
    countEl.textContent = rows.length ? String(rows.length) : "";
    list.innerHTML = "";
    if (!rows.length) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    for (const r of rows) {
      const m = r.sess.morning || {};
      const row = el("div", { class: "mk-attention-row" },
        el("span", { class: "mk-attention-kind " + r.kind }, r.kind.replace(/_/g, " ")),
        el("span", { class: "mk-attention-text" }, m.strategy_text || r.sess.display_name || "(untitled)"),
        el("span", { class: "mk-attention-where" },
          (m.goal_name || m.goal_slug || "") + " · " + r.detail),
      );
      row.addEventListener("click", () => openPane(r.sess));
      list.appendChild(row);
    }
  }

  // ---------------------------------------------------------------------
  // Drag & drop
  // ---------------------------------------------------------------------

  let _dragState = null;  // { taskId, sourceCol, sourceEl }

  function wireCardDrag(card) {
    card.addEventListener("dragstart", (e) => {
      _dragState = {
        taskId: card.dataset.taskId || "",
        sourceCol: card.dataset.sourceCol,
        goalSlug: card.dataset.goalSlug || "",
        strategyId: card.dataset.strategyId || "",
        sessionId: card.dataset.sessionId || "",
        sourceEl: card,
      };
      card.classList.add("mk-dragging-card");
      try { e.dataTransfer.effectAllowed = "move"; } catch (_) {}
    });
    card.addEventListener("dragend", () => {
      card.classList.remove("mk-dragging-card");
      document.querySelectorAll(".mk-list.mk-drop-hot").forEach(n => n.classList.remove("mk-drop-hot"));
      document.querySelectorAll(".mk-drop-indicator").forEach(n => n.remove());
      _dragState = null;
    });
  }

  // Given a mouse clientY and a list container, return the DOM node the new
  // card should be inserted BEFORE (null = append to end).
  function childToInsertBefore(list, clientY) {
    const cards = Array.from(list.querySelectorAll(".mk-card:not(.mk-dragging-card)"));
    for (const c of cards) {
      const r = c.getBoundingClientRect();
      if (clientY < r.top + r.height / 2) return c;
    }
    return null;
  }

  function wireColDrop(list, colKey) {
    list.addEventListener("dragover", (e) => {
      if (!_dragState) return;
      // Accept any source for reorder within-column; only `today` drops
      // onto `completed` are wired to the dismiss semantics right now.
      e.preventDefault();
      try { e.dataTransfer.dropEffect = "move"; } catch (_) {}
      list.classList.add("mk-drop-hot");
      // Visual indicator for insertion point (reorder within Today).
      if (_dragState.sourceCol === "today" && colKey === "today") {
        document.querySelectorAll(".mk-drop-indicator").forEach(n => n.remove());
        const before = childToInsertBefore(list, e.clientY);
        const ind = el("div", { class: "mk-drop-indicator" });
        if (before) list.insertBefore(ind, before);
        else list.appendChild(ind);
      }
    });
    list.addEventListener("dragleave", (e) => {
      if (e.target === list) list.classList.remove("mk-drop-hot");
    });
    list.addEventListener("drop", async (e) => {
      if (!_dragState) return;
      e.preventDefault();
      list.classList.remove("mk-drop-hot");
      document.querySelectorAll(".mk-drop-indicator").forEach(n => n.remove());
      const ds = _dragState;
      _dragState = null;
      if (ds.sourceCol === "today" && colKey === "today") {
        await reorderTodayTask(ds.taskId, e.clientY, list);
        return;
      }
      await genericMove(ds, colKey);
    });
  }

  async function genericMove(ds, targetCol) {
    const payload = {
      source_col: ds.sourceCol,
      target_col: targetCol,
      user_tactical_id: ds.taskId,
      goal_slug: ds.goalSlug,
      strategy_id: ds.strategyId,
      session_id: ds.sessionId,
      card_id: ds.taskId || ds.sessionId || (ds.goalSlug + "/" + ds.strategyId),
    };
    try {
      const r = await fetch("/api/morning/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) {
        alert("Move " + ds.sourceCol + " → " + targetCol + " failed: " + (d.error || "HTTP " + r.status));
      }
    } catch (e) {
      alert("Move request failed: " + e.message);
    }
    // Full reload to pick up authoritative state — goal.md mutations, new
    // strategies, detached sessions etc. all affect multiple columns.
    await load();
  }

  async function undismissTask(taskId) {
    try {
      const r = await fetch("/api/morning/today/undismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: taskId }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) throw new Error(d.error || "HTTP " + r.status);
      // Move locally: pop from completed, push to end of today.
      const idx = lastCompletedData.findIndex(x => x.user_tactical_id === taskId);
      if (idx >= 0) {
        const moved = lastCompletedData.splice(idx, 1)[0];
        delete moved.dismissed_at;
        lastTodayData.push(moved);
      }
      renderBoard();
    } catch (err) {
      alert("Un-dismiss failed: " + err.message);
    }
  }

  async function dismissTodayTask(taskId) {
    try {
      const r = await fetch("/api/morning/today/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: taskId }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) throw new Error(d.error || "HTTP " + r.status);
      // Move locally: pop from today, push to completed.
      const idx = lastTodayData.findIndex(x => x.user_tactical_id === taskId);
      if (idx >= 0) {
        const moved = lastTodayData.splice(idx, 1)[0];
        moved.dismissed_at = new Date().toISOString().replace(/\.\d+Z$/, "Z");
        lastCompletedData.unshift(moved);
      }
      renderBoard();
    } catch (err) {
      alert("Dismiss failed: " + err.message);
    }
  }

  async function reorderTodayTask(taskId, clientY, list) {
    const currentIds = lastTodayData.map(t => t.user_tactical_id);
    const moving = lastTodayData.find(t => t.user_tactical_id === taskId);
    if (!moving) return;
    // Compute target index by examining current DOM (which excludes the
    // dragging card because of the filter in childToInsertBefore).
    const before = childToInsertBefore(list, clientY);
    let targetIdx;
    if (!before) targetIdx = currentIds.length;  // end
    else targetIdx = currentIds.indexOf(before.dataset.taskId);
    const fromIdx = currentIds.indexOf(taskId);
    if (fromIdx === targetIdx || fromIdx === targetIdx - 1) return;
    const next = lastTodayData.slice();
    next.splice(fromIdx, 1);
    const insertAt = targetIdx > fromIdx ? targetIdx - 1 : targetIdx;
    next.splice(insertAt, 0, moving);
    lastTodayData = next;
    renderBoard();
    // Persist — fire-and-forget; server accepts full id list.
    fetch("/api/morning/today/reorder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: next.map(t => t.user_tactical_id) }),
    }).catch(() => {});
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
    // Personalize the greeting via window.CCC_USER_NAME (set in HTML head from
    // a server-injected env var). Falls back to a name-less greeting.
    const userName = (typeof window !== "undefined" && window.CCC_USER_NAME) || "";
    document.getElementById("mk-greeting").textContent =
      "Good " + period + (userName ? ", " + userName : "") + ".";
    document.getElementById("mk-subgreeting").textContent =
      "It's " + dateStr + ". What's on your mind?";
  }

  let knownGoalSlugs = [];       // populated by load() below
  let knownGoals = [];           // [{slug, name, accent, life_area, ribbon}] — authoritative from /api/morning/state
  let activeGoalFilter = null;   // goal slug the board is filtered to, or null for "all"
  let lastSessionsData = null;   // cached /api/morning/sessions response so filter re-renders don't refetch
  const LS_ANALYSIS_KEY = "ccc-morning-braindump-last-analysis";
  const LS_DUMP_KEY = "ccc-morning-braindump-last-dump";

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
    );

    // Click-to-edit task text.
    const textEl = el("div", { class: "text mk-editable" }, t.text || "(no text)");
    textEl.title = "click to edit";
    wireInlineEdit(textEl, t.text || "", "text", t.user_tactical_id);
    card.appendChild(textEl);

    // Freeform status line — always present so the user can see the affordance.
    const statusEl = el("div", { class: "mk-card-status mk-editable" });
    if (t.status) statusEl.textContent = "status: " + t.status;
    else { statusEl.textContent = "+ status"; statusEl.classList.add("mk-placeholder"); }
    statusEl.title = "click to set status";
    wireInlineEdit(statusEl, t.status || "", "status", t.user_tactical_id);
    card.appendChild(statusEl);

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

    // Claude session binding: ▶ Start if none; ▶ Resume/Inject once bound.
    if (t.user_tactical_id) {
      const actions = el("div", { class: "actions" });
      const sid = t.claude_session_id;
      const label = sid ? "▶ Resume" : "▶ Start";
      const btn = el("button", { class: "primary" }, label);
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        launchTask(t.user_tactical_id, btn);
      });
      actions.appendChild(btn);
      if (sid) {
        const sidChip = el("span", { class: "meta", style: { marginLeft: "6px" } },
          "session " + sid.slice(0, 8));
        actions.appendChild(sidChip);
      }
      card.appendChild(actions);

      // Drag-to-reorder within Today, drag-to-Completed to dismiss.
      card.draggable = true;
      card.dataset.taskId = t.user_tactical_id;
      card.dataset.sourceCol = "today";
      wireCardDrag(card);
    }
    return card;
  }

  // Swaps a text node for a textarea on click; saves on blur/Enter, reverts
  // on Escape. `field` maps to the /api/morning/today/update payload key.
  function wireInlineEdit(displayEl, initialValue, field, taskId) {
    displayEl.addEventListener("click", (e) => {
      e.stopPropagation();
      if (displayEl.classList.contains("mk-editing")) return;
      const textarea = document.createElement("textarea");
      textarea.className = "mk-inline-editor";
      textarea.value = initialValue || "";
      textarea.rows = 2;
      displayEl.classList.add("mk-editing");
      displayEl.replaceChildren(textarea);
      textarea.focus();
      textarea.select();
      const restore = (finalVal, isPlaceholder) => {
        displayEl.classList.remove("mk-editing");
        displayEl.classList.toggle("mk-placeholder", isPlaceholder);
        displayEl.textContent = finalVal;
      };
      const save = async () => {
        const v = textarea.value.trim();
        if (v === (initialValue || "")) {
          const display = field === "status"
            ? (v ? "status: " + v : "+ status")
            : (v || "(no text)");
          restore(display, field === "status" && !v);
          return;
        }
        try {
          const r = await fetch("/api/morning/today/update", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: taskId, [field]: v }),
          });
          const d = await r.json().catch(() => ({}));
          if (!r.ok || !d.ok) throw new Error(d.error || "HTTP " + r.status);
          // Sync in-memory cache so renderBoard matches server.
          const idx = lastTodayData.findIndex(x => x.user_tactical_id === taskId);
          if (idx >= 0) lastTodayData[idx][field] = v;
          const display = field === "status"
            ? (v ? "status: " + v : "+ status")
            : (v || "(no text)");
          restore(display, field === "status" && !v);
        } catch (err) {
          alert("Save failed: " + err.message);
          restore(initialValue || "", field === "status" && !initialValue);
        }
      };
      textarea.addEventListener("blur", save, { once: true });
      textarea.addEventListener("keydown", (k) => {
        if (k.key === "Enter" && !k.shiftKey) { k.preventDefault(); textarea.blur(); }
        else if (k.key === "Escape") {
          textarea.removeEventListener("blur", save);
          const display = field === "status"
            ? (initialValue ? "status: " + initialValue : "+ status")
            : (initialValue || "(no text)");
          restore(display, field === "status" && !initialValue);
        }
      });
    });
  }

  async function launchTask(taskId, btn) {
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "…";
    try {
      const r = await fetch("/api/morning/today/launch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: taskId }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) throw new Error(d.error || "HTTP " + r.status);
      btn.textContent = d.action === "resumed" ? "✓ resumed" : "✓ spawned";
      // Open the conversation pane on the newly-bound session so the user
      // can watch the spawn/resume actually land — otherwise the spawn feels
      // silent. If we got a session_id back from the server, use it; else
      // re-fetch the task after a short delay to pick up the resolved sid.
      const task = lastTodayData.find(x => x.user_tactical_id === taskId);
      const goal = task && knownGoals.find(g => g.slug === task.goal_slug);
      const openPaneFor = (sid) => {
        if (!sid) return;
        openPane({
          session_id: sid,
          is_live: true,
          modified_human: "just now",
          morning: {
            goal_slug: task ? task.goal_slug : null,
            goal_name: goal ? goal.name : (task && task.goal_slug) || "",
            goal_accent: goal ? goal.accent : "#5ac8fa",
            strategy_id: null,
            strategy_text: task ? task.text : "",
            user_tactical_id: taskId,
          },
        });
      };
      if (d.session_id) {
        openPaneFor(d.session_id);
      } else {
        // Spawn's log parser sometimes needs an extra moment to resolve the
        // session_id. Wait for the task refresh then open the pane.
        setTimeout(async () => {
          await load();
          const t2 = lastTodayData.find(x => x.user_tactical_id === taskId);
          if (t2 && t2.claude_session_id) openPaneFor(t2.claude_session_id);
        }, 1500);
      }
      setTimeout(load, 1500);
    } catch (e) {
      btn.textContent = "error";
      alert("Launch failed: " + e.message);
      setTimeout(() => { btn.textContent = originalLabel; btn.disabled = false; }, 2500);
    }
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
    if (t.user_tactical_id) {
      card.draggable = true;
      card.dataset.taskId = t.user_tactical_id;
      card.dataset.sourceCol = "completed";
      wireCardDrag(card);
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
  // Wire each column's list as a drop target once — renderBoard repaints
  // the inner cards but preserves the list element identity, so listeners
  // stay attached across re-renders.
  for (const [colKey, listId] of [
    ["today", "col-today"],
    ["backlog", "col-backlog"],
    ["active", "col-active"],
    ["dormant", "col-dormant"],
    ["review", "col-review"],
    ["completed", "col-completed"],
  ]) {
    const list = document.getElementById(listId);
    if (list) wireColDrop(list, colKey);
  }
  // Populate knownGoals first, then restore. Otherwise restored cards that
  // need the manual goal picker render with an empty <select>.
  (async () => {
    await load();
    restorePreviousAnalysis();
  })();
})();
