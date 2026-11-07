/* Savings page (/savings.html) — renders GET /api/savings and drives the
 * editable plan cost via POST /api/savings/plan. Dependency-free; the page is
 * a standalone satellite (same pattern as spawn-ledger.html). */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var currentRange = "today";
  var inFlight = null;   // AbortController for the pending range fetch

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function fmtUsd(v, digits) {
    var n = Number(v);
    if (!isFinite(n)) return "$0.00";
    var d = digits == null ? (Math.abs(n) >= 1000 ? 0 : 2) : digits;
    return "$" + n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  }

  function fmtTokens(n) {
    n = Number(n) || 0;
    if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
    return String(Math.round(n));
  }

  function fmtRoi(x) {
    if (x == null || !isFinite(Number(x))) return "–";
    return Number(x).toLocaleString(undefined, { maximumFractionDigits: 1 }) + "×";
  }

  function shortDay(iso) {
    var d = new Date(iso + "T12:00:00");
    if (isNaN(d.getTime())) return iso.slice(5);
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  function showErr(msg) {
    var el = $("errPill");
    el.hidden = !msg;
    el.textContent = msg || "";
  }

  function renderRangeButtons() {
    var btns = $("ranges").querySelectorAll("button");
    for (var i = 0; i < btns.length; i++) {
      btns[i].classList.toggle("on", btns[i].dataset.range === currentRange);
    }
  }

  function renderHero(p) {
    $("apiValue").textContent = fmtUsd(p.api_value_usd);
    var sub = "priced at API list rates";
    if (p.estimated_share_usd > 0) {
      sub += " · " + fmtUsd(p.estimated_share_usd) + " estimated";
    }
    $("apiValueSub").textContent = sub;

    $("planCost").textContent = fmtUsd(p.plan_cost_usd);
    var planName = (p.plan && p.plan.name) || "plan";
    $("planCostSub").textContent = planName +
      (p.plan && p.plan.source === "default" ? " (default)" : "") +
      " · " + (p.label || "").toLowerCase();

    $("roi").textContent = fmtRoi(p.roi_x);
    $("roiSub").textContent = p.roi_x != null
      ? "each plan dollar did " + fmtUsd(p.plan_cost_usd > 0 ? p.api_value_usd / p.plan_cost_usd : 0) + " of work"
      : "add a plan cost to see ROI";

    $("freeSaved").textContent = fmtUsd(p.free_saved_usd);
    var bits = [];
    if (p.free_runs) bits.push(p.free_runs + " free run" + (p.free_runs === 1 ? "" : "s"));
    if (p.free_tokens) bits.push(fmtTokens(p.free_tokens) + " tokens");
    if (p.router_connected) bits.push("router connected");
    $("freeSavedSub").textContent = bits.length ? bits.join(" · ") : "no free runs yet";

    // Value vs plan bar: paid-blue = plan cost share, accent = extra value.
    var plan = Number(p.plan_cost_usd) || 0;
    var value = Number(p.api_value_usd) || 0;
    var free = Math.min(Number(p.free_saved_usd) || 0, value);
    var vs = $("vsBar");
    if (plan > 0 || value > 0) {
      vs.hidden = false;
      var total = Math.max(plan, value);
      // The track shows plan cost vs delivered value as two stacked shares:
      // blue = the plan's share of the bar, teal = the free-run portion inside
      // the delivered value.
      var paidPct = Math.min(plan / total, 1) * 100;
      var freePct = Math.min(free / total, 1) * 100;
      $("fillPaid").style.width = paidPct + "%";
      $("fillFree").style.width = freePct + "%";
      $("lgValue").textContent = fmtUsd(value);
      $("lgFree").textContent = fmtUsd(free);
      $("lgPlan").textContent = fmtUsd(plan);
    } else {
      vs.hidden = true;
    }
  }

  function renderDays(p) {
    var wrap = $("days");
    var days = p.by_day || [];
    if (!days.length) {
      wrap.innerHTML = "";
      $("daysEmpty").hidden = false;
      return;
    }
    $("daysEmpty").hidden = true;
    var max = 0;
    for (var i = 0; i < days.length; i++) {
      max = Math.max(max, days[i].api_value_usd || 0);
    }
    // Cap at 21 columns so a long window stays readable; newest days win.
    var shown = days.slice(-21);
    wrap.innerHTML = shown.map(function (d) {
      var h = max > 0 ? Math.max((d.api_value_usd / max) * 100, 2) : 0;
      var fh = d.api_value_usd > 0 ? Math.min((d.free_saved_usd || 0) / d.api_value_usd, 1) * 100 : 0;
      return '<div class="day" title="' + esc(d.day) + ": " + esc(fmtUsd(d.api_value_usd)) +
        " value" + (d.free_saved_usd > 0 ? ", " + esc(fmtUsd(d.free_saved_usd)) + " free" : "") + '">' +
        '<div class="bar" style="height:' + h.toFixed(1) + '%">' +
        (fh > 0 ? '<div class="free" style="height:' + fh.toFixed(0) + '%"></div>' : "") +
        '</div><div class="lbl">' + esc(shortDay(d.day)) + "</div></div>";
    }).join("");
  }

  function renderModels(p) {
    var models = p.models || [];
    $("modelsWrap").hidden = !models.length;
    $("modelsEmpty").hidden = !!models.length;
    $("modelsBody").innerHTML = models.map(function (m) {
      return "<tr><td>" + esc(m.label || m.model) + "</td><td class=\"num mono\">" +
        esc(fmtTokens(m.tokens)) + "</td><td class=\"num\">" + esc(fmtUsd(m.cost_usd)) + "</td></tr>";
    }).join("");
  }

  function renderPlan(p) {
    var plan = p.plan || {};
    var input = $("planInput");
    if (document.activeElement !== input) {
      input.value = plan.monthly_usd != null ? plan.monthly_usd : "";
    }
    var names = (plan.plans || []).map(function (pl) {
      return pl.name + " $" + pl.monthly_usd + "/mo";
    }).join(" + ");
    $("planNote").textContent = plan.source === "configured"
      ? "Configured plans: " + (names || plan.name) + ". Changing this only adjusts the math here; nothing is billed."
      : "Default: Claude Max at $200/mo. Change it to match what you actually pay; this only adjusts the math here.";
  }

  function render(p) {
    $("partialPill").hidden = !p.partial;
    renderHero(p);
    renderDays(p);
    renderModels(p);
    renderPlan(p);
    $("updated").textContent = p.updated_at
      ? "Counted " + (p.sessions || 0) + " session" + (p.sessions === 1 ? "" : "s") +
        " · updated " + new Date(p.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : "";
  }

  async function load(range, refresh) {
    if (inFlight) inFlight.abort();
    var ctl = new AbortController();
    inFlight = ctl;
    var url = "/api/savings?range=" + encodeURIComponent(range) + (refresh ? "&refresh=1" : "");
    try {
      var res = await fetch(url, { cache: "no-store", signal: ctl.signal });
      var data = await res.json();
      if (ctl !== inFlight) return;          // superseded by a newer click
      if (!res.ok || data.ok === false) {
        showErr(data.error || "savings endpoint returned " + res.status);
        return;
      }
      showErr(null);
      render(data);
    } catch (e) {
      if (e && e.name === "AbortError") return;
      if (ctl === inFlight) showErr("could not load savings");
    }
  }

  function setPlanMsg(text, ok) {
    var el = $("planMsg");
    el.textContent = text || "";
    el.className = "plan-msg " + (ok ? "ok" : "err");
  }

  async function postPlan(body) {
    setPlanMsg("saving…", true);
    try {
      var res = await fetch("/api/savings/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ range: currentRange }, body)),
      });
      var data = await res.json();
      if (!res.ok || data.ok === false) {
        setPlanMsg(data.error || "could not save", false);
        return;
      }
      setPlanMsg("saved", true);
      if (data.savings) render(data.savings);
    } catch (e) {
      setPlanMsg("could not save", false);
    }
  }

  $("ranges").addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-range]");
    if (!btn || btn.dataset.range === currentRange) return;
    currentRange = btn.dataset.range;
    renderRangeButtons();
    load(currentRange);
  });

  $("planSave").addEventListener("click", function () {
    var v = parseFloat($("planInput").value);
    if (!isFinite(v) || v < 0) {
      setPlanMsg("enter a monthly amount like 200", false);
      return;
    }
    postPlan({ monthly_usd: v });
  });

  $("planReset").addEventListener("click", function () {
    postPlan({ reset: true });
  });

  renderRangeButtons();
  load(currentRange);
}());
