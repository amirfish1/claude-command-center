(() => {
  const playButton = document.getElementById("captainPlay");
  const replayButton = document.getElementById("captainReplay");
  const progress = document.getElementById("captainProgress");
  const status = document.getElementById("captainStatus");
  const time = document.getElementById("captainTime");
  const eventList = document.getElementById("captainEvents");
  const receipt = document.getElementById("captainReceipt");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  let events = [];
  let durationMs = 45000;
  let elapsedMs = 0;
  let startedAt = 0;
  let animationFrame = 0;
  let playing = false;

  function renderEvents(nextEvents) {
    events = nextEvents;
    eventList.replaceChildren();
    nextEvents.forEach((event) => {
      const row = document.createElement("li");
      row.className = "event-item";
      row.dataset.demoEvent = event.kind;

      const title = document.createElement("strong");
      title.textContent = event.title;
      const detail = document.createElement("span");
      detail.textContent = event.detail;
      row.append(title, detail);
      eventList.append(row);
    });
  }

  function renderAt(nextElapsedMs) {
    elapsedMs = Math.max(0, Math.min(durationMs, nextElapsedMs));
    const rows = Array.from(eventList.querySelectorAll("[data-demo-event]"));
    let activeEvent = null;

    events.forEach((event, index) => {
      const active = event.at_ms <= elapsedMs;
      rows[index]?.classList.toggle("is-active", active);
      document
        .querySelectorAll(`[data-terminal-event="${event.kind}"]`)
        .forEach((node) => node.classList.toggle("is-active", active));
      if (active) activeEvent = event;
    });

    const ratio = durationMs ? elapsedMs / durationMs : 0;
    progress.style.width = `${Math.min(100, ratio * 100)}%`;
    progress.parentElement.setAttribute("aria-valuenow", String(Math.round(ratio * 100)));
    time.textContent = `${Math.floor(elapsedMs / 1000)}s / ${Math.floor(durationMs / 1000)}s`;
    receipt.classList.toggle("is-visible", elapsedMs >= 41000);
    status.textContent = activeEvent ? activeEvent.title : "Ready to play";
  }

  function frame(now) {
    if (!playing) return;
    const nextElapsed = now - startedAt;
    renderAt(nextElapsed);
    if (nextElapsed >= durationMs) {
      pause();
      status.textContent = "Receipt returned";
      return;
    }
    animationFrame = requestAnimationFrame(frame);
  }

  function play() {
    if (!events.length || reduceMotion.matches) return;
    if (elapsedMs >= durationMs) renderAt(0);
    if (playing) {
      pause();
      return;
    }
    playing = true;
    startedAt = performance.now() - elapsedMs;
    playButton.textContent = "Pause";
    playButton.setAttribute("aria-pressed", "true");
    status.textContent = "Walkthrough playing";
    animationFrame = requestAnimationFrame(frame);
  }

  function pause() {
    playing = false;
    cancelAnimationFrame(animationFrame);
    animationFrame = 0;
    playButton.textContent = "Play";
    playButton.setAttribute("aria-pressed", "false");
  }

  function replay() {
    pause();
    renderAt(0);
    play();
  }

  async function load() {
    try {
      const response = await fetch("./events.json");
      if (!response.ok) throw new Error("event fixture unavailable");
      const payload = await response.json();
      durationMs = payload.duration_ms;
      renderEvents(payload.events);
      playButton.disabled = false;
      replayButton.disabled = false;
      if (reduceMotion.matches) {
        renderAt(durationMs);
        playButton.textContent = "Final state";
        playButton.disabled = true;
        status.textContent = "Reduced motion. Final receipt shown.";
      } else {
        renderAt(0);
      }
    } catch (_) {
      status.textContent = "Static walkthrough shown";
    }
  }

  playButton.addEventListener("click", play);
  replayButton.addEventListener("click", replay);
  reduceMotion.addEventListener("change", () => window.location.reload());

  window.CaptainPlayer = { play, pause, replay, renderAt };
  load();
})();
