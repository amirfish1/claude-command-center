/* Free engine (freellmapi) client library.
 *
 * window.cccFreeRouter is the shared front-door every lane talks through:
 * onboarding (L07), the key wizard (L03), the settings panel (L17) and the
 * $0 spawn path all consume the same endpoints. It is a thin fetch wrapper
 * plus one job poller — no DOM, no deps, safe to load anywhere.
 */
(function () {
  "use strict";

  async function api(path, opts) {
    const res = await fetch(path, Object.assign({
      headers: { "Content-Type": "application/json" },
    }, opts || {}));
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON error body */ }
    if (data === null) data = {};
    if (!res.ok && !data.error) data.error = `HTTP ${res.status}`;
    data._status = res.status;
    return data;
  }

  function post(path) {
    return api(path, { method: "POST", body: "{}" });
  }

  /* Poll a job until it leaves "running". onLine(text) fires once per new
   * log line, onUpdate(job) once per poll. Resolves with the final job. */
  function pollJob(jobId, opts) {
    const o = opts || {};
    const interval = o.interval || 900;
    let seen = 0;
    return new Promise((resolve) => {
      async function tick() {
        let job;
        try {
          job = await api(`/api/free-router/jobs/${jobId}`);
        } catch (e) {
          job = { status: "error", error: String(e) };
        }
        const lines = job.lines || [];
        for (; seen < lines.length; seen++) {
          if (o.onLine) o.onLine(lines[seen]);
        }
        if (o.onUpdate) o.onUpdate(job);
        if (job.status && job.status !== "running") {
          resolve(job);
          return;
        }
        setTimeout(tick, interval);
      }
      tick();
    });
  }

  /* One friendly {tone, headline, detail} per router state so every surface
   * words it the same way. tone: idle|busy|ok|warn|error. */
  function describe(st) {
    if (!st || !st.installed) {
      return {
        tone: "idle",
        headline: "Not set up yet",
        detail: "One click installs your free engine. It runs only on this machine.",
      };
    }
    switch (st.state) {
      case "stopped":
        return { tone: "warn", headline: "Installed but off", detail: "Start it to run agents for $0." };
      case "starting":
        return { tone: "busy", headline: "Warming up", detail: "The engine is starting. Give it a few seconds." };
      case "needs_setup":
        return { tone: "warn", headline: "Almost there", detail: "Your free key is not minted yet. Reinstall to finish setup." };
      case "needs_key":
        return { tone: "warn", headline: "Needs a free provider", detail: "Add one free provider key and your agents can run for $0." };
      case "degraded":
        return { tone: "warn", headline: "Taking a breather", detail: "All free providers are cooling down. Try again soon." };
      case "ready":
        return { tone: "ok", headline: "Ready", detail: "Your agents can run for $0." };
      default:
        return { tone: "warn", headline: "Check engine", detail: "Something looks off. Try restarting it." };
    }
  }

  window.cccFreeRouter = {
    status: () => api("/api/free-router/status"),
    spawnReady: () => api("/api/free-router/spawn-ready"),
    install: () => post("/api/free-router/install"),
    start: () => post("/api/free-router/start"),
    stop: () => post("/api/free-router/stop"),
    uninstall: () => post("/api/free-router/uninstall"),
    job: (id) => api(`/api/free-router/jobs/${id}`),
    logs: () => api("/api/free-router/logs"),
    pollJob,
    describe,
  };
})();
