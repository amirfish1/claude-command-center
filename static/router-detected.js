/* Existing-router card (L20).
 *
 * GET /api/free-router/detected finds routers the user already runs
 * (freellmapi, 9router/OmniRoute, free-claude-code, Ollama, LM Studio) plus
 * stored OpenRouter keys. This component offers "use yours instead" so a
 * user with a working router never installs a second one.
 *
 * Two surfaces:
 *   1. Embedded — the onboarding shell (L07) calls
 *      window.cccRouterDetect.renderCard(container) to drop the card into a
 *      step. Returns null when nothing is detected so the step can skip.
 *   2. Auto — on the plain dashboard a small floating card appears once per
 *      router set, dismissible, and reappears only when a NEW router id
 *      shows up.
 *
 * Degrades silently when the endpoints 404 (older server or unmerged lanes)
 * and when window.cccFx / window.cccOnboarding don't exist.
 */
(function () {
  "use strict";

  var DISMISS_KEY = "ccc-router-detect-dismissed";
  var _card = null;

  function api(path, opts) {
    return fetch(path, opts || {}).then(function (r) {
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    });
  }

  function detect(force) {
    return api("/api/free-router/detected" + (force ? "?fresh=1" : ""));
  }

  function prefer(id, key) {
    var body = { id: id };
    if (key) body.key = key;
    return api("/api/free-router/detected/prefer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function engineSetup(engine) {
    return api("/api/free-router/engine-setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ engine: engine }),
    });
  }

  function dismissedIds() {
    try {
      var raw = localStorage.getItem(DISMISS_KEY);
      var arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (e) {
      return [];
    }
  }

  function dismiss(ids) {
    try {
      var seen = dismissedIds();
      (ids || []).forEach(function (id) {
        if (seen.indexOf(id) === -1) seen.push(id);
      });
      localStorage.setItem(DISMISS_KEY, JSON.stringify(seen.slice(-40)));
    } catch (e) { /* storage unavailable — card just won't persist dismissal */ }
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function statusPill(r) {
    if (r.kind === "key") return el("span", "ccc-rd-pill ccc-rd-pill-key", "key saved");
    if (r.kind === "env") return el("span", "ccc-rd-pill ccc-rd-pill-key", "env");
    if (r.status === "running") return el("span", "ccc-rd-pill", "running");
    return el("span", "ccc-rd-pill ccc-rd-pill-idle", r.status || "found");
  }

  function successBlock(card, router, close) {
    card.innerHTML = "";
    var done = el("div", "ccc-rd-done");
    done.appendChild(el("span", "ccc-rd-check", "\u2713"));
    var copy = el("div");
    copy.appendChild(el("div", null,
      "Nice. Free runs will use " + (router.name || "your router") + " now."));
    var note = el("div", "ccc-rd-engines", "Sessions marked Free will route there at $0.");
    copy.appendChild(note);
    done.appendChild(copy);
    card.appendChild(done);
    // Codex can't take per-session env — offer the one-click profile write.
    api("/api/engines/installed").then(function (data) {
      var installed = {};
      ((data && data.engines) || []).forEach(function (e) {
        if (e && e.installed) installed[e.engine] = true;
      });
      if (!installed.codex) return;
      var btn = el("button", "ccc-rd-setup", "Also set up Codex to use it");
      btn.addEventListener("click", function () {
        btn.disabled = true;
        btn.textContent = "Setting up…";
        engineSetup("codex").then(function (res) {
          if (!res || !res.ok) throw new Error((res && res.error) || "failed");
          btn.textContent = "Done";
          var run = el("div", "ccc-rd-setup-note");
          var cmd = el("code", null, res.run || "codex --profile ccc-free");
          run.appendChild(document.createTextNode("Terminal Codex: "));
          run.appendChild(cmd);
          note.appendChild(run);
        }).catch(function () {
          btn.textContent = "Set up Codex";
          btn.disabled = false;
        });
      });
      note.appendChild(btn);
    }).catch(function () { /* engines list unavailable — skip Codex offer */ });
    var dismissBtn = el("button", "ccc-rd-dismiss", "Done");
    dismissBtn.addEventListener("click", function () { close(); });
    card.appendChild(dismissBtn);
  }

  function buildCard(routers, opts) {
    opts = opts || {};
    var running = routers.filter(function (r) { return r.status === "running" || r.status === "configured"; });
    var resting = routers.filter(function (r) { return r.status === "installed"; });
    var list = running.concat(resting);
    if (!list.length) return null;

    var card = el("div", "ccc-rd-card" + (opts.embedded ? " ccc-rd-embedded" : ""));
    var title = el("div", "ccc-rd-title");
    title.appendChild(el("span", "ccc-rd-spark", "\u2726"));
    title.appendChild(document.createTextNode("Free AI is already on this Mac"));
    card.appendChild(title);
    card.appendChild(el("p", "ccc-rd-sub",
      "We found " +
      (list.length === 1 ? "a router" : "routers") +
      " you already run. Your agents can use it for $0 — nothing new to install."));

    function close() {
      if (card.parentNode) card.parentNode.removeChild(card);
      if (_card === card) _card = null;
    }

    list.forEach(function (r) {
      var row = el("div", "ccc-rd-row");
      var info = el("div", "ccc-rd-row-info");
      var nameRow = el("div", "ccc-rd-row-name");
      nameRow.appendChild(document.createTextNode(r.name || r.id));
      nameRow.appendChild(statusPill(r));
      info.appendChild(nameRow);
      info.appendChild(el("div", "ccc-rd-row-detail", r.detail || ""));
      row.appendChild(info);
      var usable = r.status !== "installed";
      if (usable) {
        var use = el("button", "ccc-rd-use", "Use it");
        use.addEventListener("click", function () {
          use.disabled = true;
          use.textContent = "…";
          prefer(r.id).then(function (res) {
            if (!res || !res.ok) throw new Error("prefer failed");
            try {
              if (window.cccFx && typeof window.cccFx.play === "function") {
                window.cccFx.play("success");
              }
            } catch (e) { /* fx kit is another lane — optional */ }
            dismiss([r.id]);
            successBlock(card, r, close);
          }).catch(function () {
            use.disabled = false;
            use.textContent = "Use it";
          });
        });
        row.appendChild(use);
      }
      card.appendChild(row);
    });

    if (!opts.embedded) {
      var notNow = el("button", "ccc-rd-dismiss", "Not now");
      notNow.addEventListener("click", function () {
        dismiss(list.map(function (r) { return r.id; }));
        close();
      });
      card.appendChild(notNow);
    }
    return card;
  }

  function onboardingOpen() {
    // L07's full-screen shell owns the moment — never float over it.
    try {
      if (window.cccOnboarding && window.cccOnboarding.isOpen &&
          window.cccOnboarding.isOpen()) return true;
      if (document.querySelector(".ccc-onboarding, #onboarding, [data-onboarding-open]")) return true;
      if (new URLSearchParams(location.search).get("onboarding") === "1") return true;
    } catch (e) { /* fall through */ }
    return false;
  }

  function maybeAutoShow() {
    if (onboardingOpen()) return;
    if (!(window.cccPopups && window.cccPopups.allowed("router-detected"))) return;
    detect(false).then(function (data) {
      var routers = (data && data.routers) || [];
      var fresh = routers.filter(function (r) {
        return r && r.id && dismissedIds().indexOf(r.id) === -1;
      });
      if (!fresh.length) return;
      var card = buildCard(fresh, {});
      if (!card) return;
      card.id = "ccc-router-detected";
      document.body.appendChild(card);
      _card = card;
    }).catch(function () { /* endpoint missing/old server — stay quiet */ });
  }

  window.cccRouterDetect = {
    detect: detect,
    prefer: prefer,
    engineSetup: engineSetup,
    /* renderCard(container): embeddable card for the onboarding step.
       Returns the element or null when nothing is detected. */
    renderCard: function (container) {
      return detect(false).then(function (data) {
        var card = buildCard((data && data.routers) || [], { embedded: true });
        if (card && container) container.appendChild(card);
        return card;
      });
    },
    open: maybeAutoShow,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      setTimeout(maybeAutoShow, 2500);
    });
  } else {
    setTimeout(maybeAutoShow, 2500);
  }
})();
