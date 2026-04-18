(function () {
  "use strict";

  const state = {
    leads: [],
  };

  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    for (const k in (attrs || {})) {
      if (k === "class") e.className = attrs[k];
      else if (k === "style" && typeof attrs[k] === "object") Object.assign(e.style, attrs[k]);
      else if (k.startsWith("on") && typeof attrs[k] === "function") e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] === true) e.setAttribute(k, "");
      else if (attrs[k] === false || attrs[k] == null) { /* skip */ }
      else e.setAttribute(k, attrs[k]);
    }
    for (const c of children) {
      if (c == null || c === false) continue;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return e;
  }

  function todayISO() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  function probabilityClass(pct) {
    if (pct >= 60) return "hot";
    if (pct >= 25) return "warm";
    return "";
  }

  function leadBorderClass(pct) {
    if (pct >= 60) return "closing-hot";
    if (pct >= 25) return "closing-warm";
    return "closing-cold";
  }

  function setSaveTag(cardEl, tag) {
    const span = cardEl.querySelector(".af-lead-save-tag");
    if (!span) return;
    span.className = "af-lead-save-tag " + tag;
    span.textContent =
      tag === "saving" ? "saving…" :
      tag === "saved" ? "saved ✓" :
      tag === "error" ? "error" : "";
    if (tag === "saved") {
      setTimeout(() => {
        if (span.classList.contains("saved")) {
          span.textContent = "";
          span.className = "af-lead-save-tag";
        }
      }, 1500);
    }
  }

  async function apiSave(lead, cardEl) {
    setSaveTag(cardEl, "saving");
    try {
      const r = await fetch("/api/affiliates/" + encodeURIComponent(lead.id), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lead),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || "save failed");
      // Replace local copy with server's canonical version
      Object.assign(lead, data.lead);
      setSaveTag(cardEl, "saved");
      // Update dependent visual bits: border + pct chip.
      refreshCardHeader(cardEl, lead);
    } catch (e) {
      setSaveTag(cardEl, "error");
      console.error("save failed", e);
    }
  }

  function refreshCardHeader(cardEl, lead) {
    cardEl.classList.remove("closing-hot", "closing-warm", "closing-cold");
    cardEl.classList.add(leadBorderClass(lead.probability_close_month_pct || 0));
    const title = cardEl.querySelector(".af-lead-title");
    if (title) {
      const studio = (lead.studio_name || "").trim();
      const leadName = (lead.lead_name || "").trim();
      const head = leadName || "(unnamed lead)";
      const tail = studio ? ` · ${studio}` : "";
      const extra = (lead.city_state || "").trim();
      title.innerHTML = "";
      title.appendChild(document.createTextNode(head + tail));
      if (extra) {
        const small = el("span", { class: "muted-inline" }, extra);
        title.appendChild(small);
      }
    }
    const pct = cardEl.querySelector(".af-lead-pct");
    if (pct) {
      const val = lead.probability_close_month_pct || 0;
      pct.className = "af-lead-pct " + probabilityClass(val);
      pct.textContent = val + "%";
    }
  }

  function debounce(fn, ms) {
    let t = null;
    return function (...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  function renderLead(lead) {
    const card = el("div", {
      class: "af-lead " + leadBorderClass(lead.probability_close_month_pct || 0),
      "data-id": lead.id,
    });

    // Scheduled autosave wrapper used by all inputs on this card.
    const scheduleSave = debounce(() => apiSave(lead, card), 450);

    function bindText(field, input) {
      input.addEventListener("input", () => {
        lead[field] = input.value;
        scheduleSave();
      });
    }
    function bindNum(field, input) {
      input.addEventListener("input", () => {
        const v = parseInt(input.value, 10);
        lead[field] = isNaN(v) ? 0 : Math.max(0, v);
        scheduleSave();
      });
    }
    function bindBool(field, input) {
      input.addEventListener("change", () => {
        lead[field] = !!input.checked;
        scheduleSave();
      });
    }

    // ---- Header ----
    const title = el("div", { class: "af-lead-title" });
    const pctChip = el("span", {
      class: "af-lead-pct " + probabilityClass(lead.probability_close_month_pct || 0),
    }, (lead.probability_close_month_pct || 0) + "%");
    const saveTag = el("span", { class: "af-lead-save-tag" });
    const deleteBtn = el("button", {
      class: "af-btn-icon danger",
      title: "Delete lead",
      onclick: async () => {
        if (!confirm("Delete this lead? This cannot be undone.")) return;
        try {
          const r = await fetch("/api/affiliates/" + encodeURIComponent(lead.id) + "/delete", { method: "POST" });
          if (!r.ok) throw new Error("HTTP " + r.status);
          card.remove();
          state.leads = state.leads.filter((l) => l.id !== lead.id);
          updateCount();
        } catch (e) {
          alert("Delete failed: " + e.message);
        }
      },
    }, "Delete");

    const head = el("div", { class: "af-lead-head" }, title, pctChip, saveTag, deleteBtn);
    card.appendChild(head);

    // ---- Core grid ----
    const leadNameInput = el("input", { type: "text", value: lead.lead_name || "", placeholder: "e.g. Joyce Lin" });
    bindText("lead_name", leadNameInput);
    const studioInput = el("input", { type: "text", value: lead.studio_name || "", placeholder: "e.g. LC Power Pilates" });
    bindText("studio_name", studioInput);
    const cityInput = el("input", { type: "text", value: lead.city_state || "", placeholder: "City, ST" });
    bindText("city_state", cityInput);
    const ownerInput = el("input", { type: "text", value: lead.owner || "", placeholder: "(optional) your name" });
    bindText("owner", ownerInput);
    const locInput = el("input", { type: "number", min: "0", value: String(lead.num_locations || 0) });
    bindNum("num_locations", locInput);
    const partInput = el("input", { type: "number", min: "0", value: String(lead.participants_per_location || 0) });
    bindNum("participants_per_location", partInput);

    const grid = el("div", { class: "af-grid" },
      el("div", { class: "af-field" }, el("label", {}, "Lead name"), leadNameInput),
      el("div", { class: "af-field" }, el("label", {}, "Studio name"), studioInput),
      el("div", { class: "af-field" }, el("label", {}, "City / state"), cityInput),
      el("div", { class: "af-field" }, el("label", {}, "Owner (submitting)"), ownerInput),
      el("div", { class: "af-field" }, el("label", {}, "# locations"), locInput),
      el("div", { class: "af-field" }, el("label", {}, "Participants / location"), partInput),
    );
    card.appendChild(grid);

    // ---- Outreach ----
    const reachedChk = el("input", { type: "checkbox" });
    reachedChk.checked = !!lead.reached_out;
    bindBool("reached_out", reachedChk);
    const reachedDateInput = el("input", { type: "date", value: lead.reached_out_date || "" });
    bindText("reached_out_date", reachedDateInput);
    // Auto-default date to today when user ticks "reached out" and no date is set.
    reachedChk.addEventListener("change", () => {
      if (reachedChk.checked && !reachedDateInput.value) {
        reachedDateInput.value = todayISO();
        lead.reached_out_date = reachedDateInput.value;
        scheduleSave();
      }
    });

    const discussedChk = el("input", { type: "checkbox" });
    discussedChk.checked = !!lead.discussed_with_amir;
    bindBool("discussed_with_amir", discussedChk);

    const probInput = el("input", {
      type: "number", min: "0", max: "100",
      value: String(lead.probability_close_month_pct || 0),
    });
    probInput.addEventListener("input", () => {
      let v = parseInt(probInput.value, 10);
      if (isNaN(v)) v = 0;
      v = Math.max(0, Math.min(100, v));
      lead.probability_close_month_pct = v;
      scheduleSave();
    });

    const outreachGrid = el("div", { class: "af-subsection" },
      el("h4", {}, "Outreach"),
      el("div", { class: "af-grid" },
        el("div", { class: "af-field" },
          el("label", {}, "Reached out?"),
          el("label", { class: "inline-checkbox" }, reachedChk, document.createTextNode("Yes — contact made")),
        ),
        el("div", { class: "af-field" },
          el("label", {}, "Date reached out"),
          reachedDateInput,
        ),
        el("div", { class: "af-field" },
          el("label", {}, "Discussed with Amir?"),
          el("label", { class: "inline-checkbox" }, discussedChk, document.createTextNode("Yes")),
        ),
        el("div", { class: "af-field" },
          el("label", {}, "Probability of closing by end of April (%)"),
          probInput,
        ),
      ),
    );
    card.appendChild(outreachGrid);

    // ---- Proposal ----
    const promoMonthlyInput = el("input", { type: "text", value: lead.proposed_promo_monthly || "", placeholder: "$ / month" });
    bindText("proposed_promo_monthly", promoMonthlyInput);
    const promoMonthsInput = el("input", { type: "number", min: "0", value: String(lead.proposed_promo_duration_months || 0) });
    bindNum("proposed_promo_duration_months", promoMonthsInput);
    const ongoingInput = el("input", { type: "text", value: lead.proposed_ongoing_monthly || "", placeholder: "$ / month" });
    bindText("proposed_ongoing_monthly", ongoingInput);

    const proposalBlock = el("div", { class: "af-subsection" },
      el("h4", {}, "Proposal (what we're offering)"),
      el("div", { class: "af-proposal" },
        el("div", { class: "af-field" }, el("label", {}, "Promo price / month"), promoMonthlyInput),
        el("div", { class: "af-field" }, el("label", {}, "Promo duration (months)"), promoMonthsInput),
        el("div", { class: "af-field" }, el("label", {}, "Ongoing price / month"), ongoingInput),
      ),
    );
    card.appendChild(proposalBlock);

    // ---- Interaction log ----
    const logBody = el("div", { class: "af-log-body" });

    function renderLogBody() {
      logBody.innerHTML = "";
      if (!lead.interaction_log || lead.interaction_log.length === 0) {
        logBody.appendChild(el("div", { class: "af-log-empty" }, "No interactions logged yet."));
        return;
      }
      lead.interaction_log.forEach((entry, idx) => {
        const dateInput = el("input", { type: "date", value: entry.date || "" });
        const noteInput = el("textarea", { placeholder: "What happened / what was said" });
        noteInput.value = entry.note || "";
        dateInput.addEventListener("input", () => {
          entry.date = dateInput.value;
          scheduleSave();
        });
        noteInput.addEventListener("input", () => {
          entry.note = noteInput.value;
          scheduleSave();
        });
        const removeBtn = el("button", {
          class: "af-btn-icon danger",
          title: "Remove entry",
          onclick: () => {
            lead.interaction_log.splice(idx, 1);
            renderLogBody();
            scheduleSave();
          },
        }, "×");
        logBody.appendChild(el("div", { class: "af-log-row" }, dateInput, noteInput, removeBtn));
      });
    }

    const addEntryBtn = el("button", {
      class: "af-btn-small",
      onclick: () => {
        if (!Array.isArray(lead.interaction_log)) lead.interaction_log = [];
        lead.interaction_log.push({ date: todayISO(), note: "" });
        renderLogBody();
        scheduleSave();
      },
    }, "+ Add entry");

    const logBlock = el("div", { class: "af-subsection" },
      el("h4", {}, "Interaction log", addEntryBtn),
      logBody,
    );
    card.appendChild(logBlock);
    renderLogBody();

    // ---- Next steps ----
    const nextStepsTa = el("textarea", { placeholder: "What's the next action? Who owns it? By when?" });
    nextStepsTa.value = lead.next_steps || "";
    bindText("next_steps", nextStepsTa);
    const nextBlock = el("div", { class: "af-subsection" },
      el("h4", {}, "Next steps"),
      el("div", { class: "af-field wide" }, nextStepsTa),
    );
    card.appendChild(nextBlock);

    // Initial header fill.
    refreshCardHeader(card, lead);
    return card;
  }

  function updateCount() {
    const n = state.leads.length;
    document.getElementById("lead-count").textContent = n === 0 ? "" : "— " + n + (n === 1 ? " lead" : " leads");
    document.getElementById("empty-state").style.display = n === 0 ? "" : "none";
  }

  function renderAll() {
    const list = document.getElementById("leads-list");
    list.innerHTML = "";
    for (const lead of state.leads) list.appendChild(renderLead(lead));
    updateCount();
  }

  async function load() {
    try {
      const r = await fetch("/api/affiliates");
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      state.leads = data.leads || [];
      renderAll();
      document.getElementById("status-meta").textContent = "loaded " + new Date().toLocaleTimeString();
    } catch (e) {
      document.getElementById("status-meta").textContent = "load failed: " + e.message;
    }
  }

  async function addLead() {
    try {
      const r = await fetch("/api/affiliates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || "create failed");
      state.leads.unshift(data.lead);
      const list = document.getElementById("leads-list");
      const node = renderLead(data.lead);
      list.insertBefore(node, list.firstChild);
      updateCount();
      // Focus the first field on the new card.
      const firstInput = node.querySelector("input[type=text]");
      if (firstInput) firstInput.focus();
    } catch (e) {
      alert("Could not add lead: " + e.message);
    }
  }

  document.getElementById("add-lead").addEventListener("click", addLead);
  load();
})();
