/* First-run setup wizard (static/setup.js).
 *
 * window.cccSetup.mountInto(el, opts) renders the step machine into any
 * container — the standalone /setup page uses it, and the onboarding shell
 * mounts the same component inline. opts:
 *   onDone()      all runnable steps finished ok
 *   onError(err)  a job ended in error
 *   onStep(step)  a step changed state
 *   autoRefresh   seconds between plan polls when idle (default 15, 0 = off)
 *
 * Raw helpers for other shells: cccSetup.fetchPlan(), cccSetup.run(ids),
 * cccSetup.pollJob(id, cb) -> stop fn, cccSetup.cancelJob(id).
 * Sounds/confetti ride on window.cccFx when that kit is present.
 */
(function () {
  "use strict";

  var POLL_MS = 700;

  function reducedMotion() {
    if (window.cccFx && typeof window.cccFx.reducedMotion === "function") {
      return window.cccFx.reducedMotion();
    }
    return window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function fx(name) {
    try {
      if (window.cccFx && typeof window.cccFx.play === "function") {
        window.cccFx.play(name);
      }
    } catch (e) { /* sound is a bonus, never a blocker */ }
  }

  function confetti() {
    try {
      if (!reducedMotion() && window.cccFx &&
          typeof window.cccFx.confetti === "function") {
        window.cccFx.confetti();
      }
    } catch (e) { /* ignore */ }
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function fmtEta(seconds) {
    if (!seconds || seconds < 45) return "under a minute";
    var mins = Math.round(seconds / 60);
    if (mins < 60) return "about " + mins + " min";
    return "about " + (Math.round(mins / 6) / 10) + " hr";
  }

  function fetchPlan(force) {
    return fetch("/api/setup/plan" + (force ? "?refresh=1" : ""), {
      headers: { "Accept": "application/json" }
    }).then(function (r) { return r.json(); });
  }

  function runSteps(ids) {
    return fetch("/api/setup/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ steps: ids })
    }).then(function (r) { return r.json(); });
  }

  function fetchJob(id) {
    return fetch("/api/setup/jobs/" + encodeURIComponent(id), {
      headers: { "Accept": "application/json" }
    }).then(function (r) { return r.json(); });
  }

  function cancelJob(id) {
    return fetch("/api/setup/jobs/" + encodeURIComponent(id) + "/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    }).then(function (r) { return r.json(); });
  }

  function pollJob(id, cb) {
    var stopped = false;
    var timer = null;
    function tick() {
      if (stopped) return;
      fetchJob(id).then(function (job) {
        if (stopped) return;
        cb(job);
        if (job && job.status === "running") {
          timer = setTimeout(tick, POLL_MS);
        }
      }).catch(function () {
        if (!stopped) timer = setTimeout(tick, POLL_MS * 3);
      });
    }
    tick();
    return function () { stopped = true; if (timer) clearTimeout(timer); };
  }

  function badgeFor(state) {
    var badge = el("div", "ccc-setup-badge");
    if (state === "ok" || state === "done") {
      badge.classList.add("ok");
      badge.textContent = "✓";
    } else if (state === "error") {
      badge.classList.add("err");
      badge.textContent = "!";
    } else if (state === "running") {
      badge.classList.add("run");
      badge.appendChild(el("div", "ccc-setup-spinner"));
    } else if (state === "skipped" || state === "na") {
      badge.textContent = "–";
    } else {
      badge.classList.add("need");
      badge.textContent = "!";
    }
    return badge;
  }

  function mountInto(container, opts) {
    opts = opts || {};
    var state = {
      plan: null,
      job: null,
      jobId: null,
      stopPoll: null,
      refreshTimer: null,
      destroyed: false,
      consent: {}   // step id -> bool, default true for needed steps
    };

    var root = el("div", "ccc-setup");
    var card = el("div", "ccc-setup-card");
    root.appendChild(card);

    var title = el("h2", "ccc-setup-title", "Let's get you set up");
    var sub = el("p", "ccc-setup-sub",
      "A few quick installs and you're ready to build. One tap approves each step.");
    var progress = el("div", "ccc-setup-progress");
    var bar = el("i");
    progress.appendChild(bar);
    var banner = el("div");
    var stepsEl = el("div", "ccc-setup-steps");
    var actions = el("div", "ccc-setup-actions");
    var goBtn = el("button", "ccc-setup-btn", "Set up for me");
    var cancelBtn = el("button", "ccc-setup-btn ghost", "Cancel");
    cancelBtn.style.display = "none";
    var statusLine = el("span", "ccc-setup-status-line");
    actions.appendChild(goBtn);
    actions.appendChild(cancelBtn);
    actions.appendChild(statusLine);

    var logwrap = el("div", "ccc-setup-logwrap");
    var logtoggle = el("button", "ccc-setup-logtoggle", "See what's happening");
    var log = el("div", "ccc-setup-log");
    logwrap.appendChild(logtoggle);
    logwrap.appendChild(log);
    logtoggle.addEventListener("click", function () {
      logwrap.classList.toggle("open");
    });

    var foot = el("div", "ccc-setup-foot",
      "Everything installs to your user folder. No admin password needed.");

    card.appendChild(title);
    card.appendChild(sub);
    card.appendChild(progress);
    card.appendChild(banner);
    card.appendChild(stepsEl);
    card.appendChild(actions);
    card.appendChild(logwrap);
    card.appendChild(foot);
    container.appendChild(root);

    function neededSteps() {
      if (!state.plan) return [];
      return state.plan.steps.filter(function (s) {
        return s.status !== "ok" && s.runnable;
      });
    }

    function consentGiven(s) {
      if (!(s.id in state.consent)) return true;
      return state.consent[s.id];
    }

    function renderSteps() {
      stepsEl.innerHTML = "";
      if (!state.plan) return;
      var running = {};
      if (state.job && state.job.steps) {
        state.job.steps.forEach(function (js) { running[js.id] = js; });
      }
      state.plan.steps.forEach(function (s) {
        var jobStep = running[s.id];
        var st = jobStep ? jobStep.state
          : (s.applies === false ? "na"
          : (s.status === "ok" ? "ok" : "need"));
        var row = el("div", "ccc-setup-step");
        row.setAttribute("data-state",
          st === "need" ? "" : st);
        if (s.applies === false) row.classList.add("dim");
        row.appendChild(badgeFor(st === "need" ? "need" : st));
        var body = el("div", "ccc-setup-step-body");
        var name = el("div", "ccc-setup-step-name", s.label);
        if (s.optional) name.appendChild(el("span", "ccc-setup-pill opt", "Optional"));
        if (s.external) name.appendChild(el("span", "ccc-setup-pill ext", "Its own step"));
        body.appendChild(name);
        var whyText = s.detail || s.why || "";
        var est = (s.status !== "ok" && s.est_seconds)
          ? " · " + fmtEta(s.est_seconds) : "";
        body.appendChild(el("div", "ccc-setup-step-why",
          (s.why ? s.why + ". " : "") + (whyText !== s.why ? whyText : "") + est));
        if (s.needs_consent && s.status !== "ok" && s.runnable && !state.job) {
          var lab = el("label", "ccc-setup-consent");
          var cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = consentGiven(s);
          cb.addEventListener("change", function () {
            state.consent[s.id] = cb.checked;
            renderActions();
          });
          lab.appendChild(cb);
          lab.appendChild(document.createTextNode(
            "OK to install" + (s.consent_label ? " · " + s.consent_label : "")));
          body.appendChild(lab);
        }
        row.appendChild(body);
        stepsEl.appendChild(row);
      });
    }

    function renderBanner() {
      banner.innerHTML = "";
      banner.className = "";
      if (!state.job && state.plan && neededSteps().length === 0 && state.plan.steps.length) {
        var pendingExternal = state.plan.steps.some(function (s) {
          return s.external && s.status !== "ok";
        });
        banner.className = "ccc-setup-banner win";
        banner.appendChild(el("strong", null,
          pendingExternal ? "Your computer is ready." : "You're all set."));
        banner.appendChild(el("span", "small",
          pendingExternal
            ? "The rest happen on their own screens next."
            : "Every step is green. Onward."));
        return;
      }
      if (!state.job) return;
      if (state.job.status === "done") {
        banner.className = "ccc-setup-banner win";
        banner.appendChild(el("strong", null, "All set!"));
        banner.appendChild(el("span", "small",
          "Everything you approved is installed and ready."));
      } else if (state.job.status === "error") {
        banner.className = "ccc-setup-banner snag";
        banner.appendChild(el("strong", null,
          state.job.cancelled ? "Cancelled." : "We hit a snag."));
        banner.appendChild(el("span", "small",
          (state.job.error || "Something stopped early.") +
          " Nothing is broken · fix it and try again."));
      }
    }

    function renderActions() {
      var needed = neededSteps().filter(consentGiven);
      if (state.job && state.job.status === "running") {
        goBtn.disabled = true;
        goBtn.textContent = state.job.step_label
          ? "Installing " + state.job.step_label + "…"
          : "Setting up…";
        cancelBtn.style.display = "";
        statusLine.textContent = "";
        return;
      }
      cancelBtn.style.display = "none";
      goBtn.disabled = needed.length === 0;
      goBtn.textContent = needed.length
        ? "Set up for me (" + needed.length + ")"
        : "Set up for me";
      if (state.job && state.job.status === "error" && !state.job.cancelled) {
        goBtn.disabled = false;
        goBtn.textContent = "Try again";
      }
      var pendingExternal = state.plan && state.plan.steps.some(function (s) {
        return s.external && s.status !== "ok";
      });
      if (needed.length === 0 && pendingExternal) {
        statusLine.textContent = "The rest happens in the next screens.";
      } else if (needed.length) {
        var est = needed.reduce(function (a, s) { return a + (s.est_seconds || 0); }, 0);
        statusLine.textContent = "Takes " + fmtEta(est) + " total.";
      } else {
        statusLine.textContent = "";
      }
    }

    function renderJob(job) {
      state.job = job;
      if (job && job.id) state.jobId = job.id;
      root.classList.toggle("running", job.status === "running");
      bar.style.width = Math.round((job.progress || 0) * 100) + "%";
      if (job.lines && job.lines.length) {
        log.textContent = job.lines.join("\n");
        log.scrollTop = log.scrollHeight;
      }
      renderSteps();
      renderBanner();
      renderActions();
      if (job.status === "done") {
        confetti();
        fx("success");
        if (opts.onDone) opts.onDone(job);
        refresh(true);
      } else if (job.status === "error" && !job.cancelled) {
        fx("error");
        if (opts.onError) opts.onError(job);
      }
    }

    function render() {
      renderSteps();
      renderBanner();
      renderActions();
      var readyCount = state.plan ? state.plan.summary.ready : 0;
      var total = state.plan ? state.plan.summary.total : 1;
      if (!state.job) {
        bar.style.width = Math.round((readyCount / total) * 100) + "%";
      }
    }

    function refresh(force) {
      return fetchPlan(force).then(function (plan) {
        if (state.destroyed) return;
        state.plan = plan;
        if (state.job && state.job.status !== "running") {
          /* keep finished job visible until next refresh cycle settles */
        }
        render();
        if (opts.onStep) {
          plan.steps.forEach(function (s) { opts.onStep(s); });
        }
      }).catch(function () {
        statusLine.textContent = "Can't reach the setup service · retrying…";
      });
    }

    goBtn.addEventListener("click", function () {
      var ids = neededSteps().filter(consentGiven).map(function (s) { return s.id; });
      if (!ids.length) return;
      goBtn.disabled = true;
      fx("step");
      logwrap.classList.add("open");
      state.job = { status: "running", lines: [], steps: [], progress: 0 };
      renderActions();
      runSteps(ids).then(function (resp) {
        if (!resp.job_id) {
          state.job = null;
          statusLine.textContent = resp.error || "Couldn't start setup · try again.";
          render();
          return;
        }
        state.jobId = resp.job_id;
        state.stopPoll = pollJob(resp.job_id, renderJob);
      }).catch(function () {
        state.job = null;
        statusLine.textContent = "Couldn't reach the server · try again.";
        render();
      });
    });

    cancelBtn.addEventListener("click", function () {
      if (state.jobId) cancelJob(state.jobId);
    });

    refresh();
    var autoRefresh = opts.autoRefresh === undefined ? 15 : opts.autoRefresh;
    if (autoRefresh > 0) {
      state.refreshTimer = setInterval(function () {
        if (!state.job || state.job.status !== "running") refresh();
      }, autoRefresh * 1000);
    }

    return {
      el: root,
      refresh: refresh,
      start: function () { goBtn.click(); },
      destroy: function () {
        state.destroyed = true;
        if (state.stopPoll) state.stopPoll();
        if (state.refreshTimer) clearInterval(state.refreshTimer);
        if (root.parentNode) root.parentNode.removeChild(root);
      }
    };
  }

  window.cccSetup = {
    mountInto: mountInto,
    fetchPlan: fetchPlan,
    run: runSteps,
    pollJob: pollJob,
    cancelJob: cancelJob
  };
})();
