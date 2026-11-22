// Mobile shortcut to the existing global ticket composer and its queue routing.
(() => {
  'use strict';
  const button = document.createElement('button');
  button.id = 'mobileTicketRecord';
  button.hidden = true;
  button.type = 'button';
  button.setAttribute('aria-label', 'Hold to record a ticket; drag to move');
  button.setAttribute('aria-pressed', 'false');
  button.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/></svg><span>Hold</span>';
  const status = document.createElement('div');
  status.id = 'mobileTicketRecordStatus';
  status.hidden = true;
  status.setAttribute('role', 'status');
  const style = document.createElement('style');
  style.textContent = `
    #mobileTicketRecord, #mobileTicketRecordStatus { display:none; }
    body.has-mobile-bottom-nav #mobileTicketRecord:not([hidden]) {
      display:flex; position:fixed; right:max(12px, env(safe-area-inset-right, 0px));
      top:calc(var(--app-vv-top, 0px) + var(--app-vh, 100dvh) - var(--ccc-mobile-app-nav-height, 70px) - 80px);
      z-index:9401; width:64px; height:64px; border:0; border-radius:50%;
      flex-direction:column; align-items:center; justify-content:center; gap:2px;
      background:var(--green, #8cdb9c); color:var(--bg, #101318);
      box-shadow:0 4px 12px #0004; font:600 11px/1.2 system-ui;
      touch-action:none; user-select:none; -webkit-user-select:none; -webkit-touch-callout:none;
    }
    #mobileTicketRecord:focus-visible { outline:2px solid var(--text); outline-offset:3px; }
    body.has-mobile-bottom-nav #mobileTicketRecord[data-state="recording"] { background:var(--red, #ff8585); }
    body.has-mobile-bottom-nav #mobileTicketRecordStatus:not([hidden]):not(:empty) {
      display:block; position:fixed; right:88px; max-width:calc(100vw - 112px);
      bottom:calc(var(--ccc-mobile-app-nav-height, 70px) + 20px); z-index:9401;
      padding:8px 12px; border-radius:8px; background:var(--bg); color:var(--text); font-size:14px;
    }
    body.has-upd-overlay-open #mobileTicketRecord:not([hidden]),
    body.has-upd-overlay-open #mobileTicketRecordStatus:not([hidden]):not(:empty) { display:none; }
  `;
  document.head.append(style);
  document.body.append(button, status);
  let active = null, pendingText = '', opening = false;
  const preferenceKey = 'ccc-mobile-ticket-record-enabled';
  const toggle = document.getElementById('settingsTicketRecordToggle');
  let enabled = false;
  function applyPreference() {
    enabled = false;
    try { enabled = localStorage.getItem(preferenceKey) === '1'; } catch (_) {}
    button.hidden = status.hidden = !enabled;
    if (toggle) {
      toggle.classList.toggle('is-on', enabled);
      toggle.setAttribute('aria-checked', String(enabled));
    }
    if (!enabled && active) { active.cancelled = true; finish(active, true); }
    if (enabled) restorePosition();
  }
  toggle?.addEventListener('click', () => {
    try { localStorage.setItem(preferenceKey, enabled ? '0' : '1'); } catch (_) {}
    applyPreference();
  });
  window.addEventListener('storage', event => {
    if (event.key === preferenceKey || event.key === null) applyPreference();
  });
  function state(label, message, recording = false) {
    button.querySelector('span').textContent = label;
    button.dataset.state = recording ? 'recording' : '';
    button.setAttribute('aria-pressed', String(recording));
    status.textContent = message;
  }
  function openDraft() {
    if (!pendingText || opening) return;
    // Use the existing entry point: it owns queue loading, the pop-up and submit.
    const opener = document.getElementById('sidebarNewTicketBtn');
    if (!opener || document.querySelector('.fq-ticket-composer')) {
      state('Retry', 'Finish the open ticket, then tap to use your recording.');
      return;
    }
    opening = true;
    state('Wait', 'Preparing ticket…');
    const observer = new MutationObserver(fill);
    const timeout = setTimeout(() => {
      observer.disconnect(); opening = false;
      state('Retry', 'Could not open the ticket. Tap to retry your saved words.');
    }, 15000);
    function fill() {
      const input = document.getElementById('fqTicketNote');
      if (!input) return;
      observer.disconnect(); clearTimeout(timeout);
      input.value = pendingText + input.value;
      input.dispatchEvent(new Event('input', { bubbles:true }));
      pendingText = ''; opening = false;
      state('Hold', '');
    }
    observer.observe(document.body, { childList:true, subtree:true });
    opener.click();
    fill();
  }
  async function finish(run, cancelled = false) {
    if (active !== run || run.finishing) return;
    run.finishing = true;
    run.cancelled = cancelled;
    state('Wait', cancelled ? '' : 'Finishing recording…');
    if (run.media) {
      clearTimeout(run.limitTimer);
      await run.ready;
      if (run.recorder?.state === 'recording') run.recorder.stop();
      else { run.stream?.getTracks().forEach(track => track.stop()); complete(run); }
    } else if (run.native) {
      // Await startup: releasing during a permission prompt must never leave
      // a microphone running after the finger has lifted.
      await run.ready;
      try { await run.native.stop(); } catch (_) {}
      if (run.listener) await run.listener.remove();
      complete(run);
    } else {
      try { run.recognition.stop(); } catch (_) { complete(run); }
      run.timer = setTimeout(() => {
        try { run.recognition.abort(); } catch (_) {}
        complete(run);
      }, 3000);
    }
  }
  function complete(run) {
    if (active !== run) return;
    clearTimeout(run.timer);
    active = null;
    if (run.cancelled) { state('Hold', ''); return; }
    pendingText = run.text.trim();
    if (pendingText) openDraft();
    else state('Hold', run.error || 'No speech heard. Hold to try again.');
  }
  async function startAudio(run) {
    try {
      if (!window.isSecureContext) throw new Error('Microphone recording requires the HTTPS dashboard address.');
      const statusResponse = await fetch('/api/ticket-audio/status');
      const availability = await statusResponse.json();
      if (!statusResponse.ok || !availability.ok || availability.available === false)
        throw new Error(availability.error || availability.reason || 'Local speech transcription is unavailable on the dashboard server.');
      if (run.finishing) return;
      run.stream = await navigator.mediaDevices.getUserMedia({audio:true});
      if (run.finishing) return; // finish() owns track cleanup after startup settles.
      const mimeType = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm']
        .find(type => MediaRecorder.isTypeSupported(type));
      run.recorder = new MediaRecorder(run.stream, mimeType ? {mimeType} : undefined);
      const chunks = [];
      let bytes = 0;
      run.recorder.ondataavailable = event => {
        if (event.data.size) { chunks.push(event.data); bytes += event.data.size; }
        if (bytes > 10 * 1024 * 1024) {
          run.error = 'Recording is too large. Try a shorter recording.';
          run.cancelled = true; finish(run, true);
        }
      };
      run.recorder.onerror = () => {
        run.error = 'Recording failed. Hold to try again.';
        finish(run);
      };
      run.recorder.onstop = async () => {
        clearTimeout(run.limitTimer);
        run.stream.getTracks().forEach(track => track.stop());
        if (run.cancelled || run.error) { complete(run); return; }
        const audio = new Blob(chunks, {type:run.recorder.mimeType || mimeType || 'audio/webm'});
        if (!audio.size) { complete(run); return; }
        state('Wait', 'Transcribing recording…');
        try {
          const response = await fetch('/api/ticket-audio/transcribe', {
            method:'POST', headers:{'Content-Type':audio.type}, body:audio,
          });
          const result = await response.json();
          if (!response.ok || !result.ok) throw new Error(result.error || 'Transcription failed. Hold to try again.');
          run.text = result.text || '';
        } catch (error) { run.error = error.message || 'Could not transcribe recording.'; }
        complete(run);
      };
      run.recorder.start(1000);
      state('Release', 'Listening… release to prepare ticket.', true);
      run.limitTimer = setTimeout(() => finish(run), 120000);
    } catch (error) {
      run.stream?.getTracks().forEach(track => track.stop());
      run.error = error.name === 'NotAllowedError'
        ? 'Microphone permission was denied. Reopen this app and hold again; choose Allow when iOS asks.'
        : error.message || 'Could not start recording.';
    }
  }
  function start() {
    if (!enabled || active || opening) return;
    if (pendingText) { openDraft(); return; }
    const native = /CCC-iOS/.test(navigator.userAgent) && window.Capacitor?.Plugins?.SpeechRecognition;
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const media = !native && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== 'undefined';
    if (!native && !media && !Recognition) {
      state('Hold', 'Voice input unavailable. Use a supported browser over HTTPS.'); return;
    }
    const run = { text:'', native, media, finishing:false };
    active = run;
    state('Wait', 'Allow microphone access, then speak while holding.');
    if (media) {
      run.ready = startAudio(run);
      run.ready.then(() => { if (run.error && !run.finishing) finish(run); });
      return;
    }
    if (native) {
      run.ready = (async () => {
        try {
          const permissions = await native.requestPermissions();
          if (permissions?.speechRecognition === 'denied' || permissions?.microphone === 'denied') throw new Error('Microphone access blocked. Allow it in Settings.');
          if (run.finishing) return;
          run.listener = await native.addListener('partialResults', data => { run.text = (data.matches || data.value || [])[0] || ''; });
          if (run.finishing) return;
          await native.start({ language:'en-US', maxResults:1, partialResults:true, popup:false });
          if (!run.finishing) state('Release', 'Listening… release to prepare ticket.', true);
        } catch (error) { run.error = error.message || 'Could not start recording.'; }
      })();
      run.ready.then(() => { if (run.error && !run.finishing) finish(run); });
      return;
    }
    const recognition = run.recognition = new Recognition();
    recognition.lang = 'en-US'; recognition.continuous = true; recognition.interimResults = true;
    recognition.onstart = () => {
      if (active !== run || run.finishing) { try { recognition.stop(); } catch (_) {} return; }
      state('Release', 'Listening… release to prepare ticket.', true);
    };
    recognition.onresult = event => { run.text = Array.from(event.results, result => result[0].transcript).join(' '); };
    recognition.onerror = event => {
      const homeScreenIOS = /iPhone|iPad|iPod/.test(navigator.userAgent)
        && (navigator.standalone || window.matchMedia('(display-mode: standalone)').matches);
      run.error = event.error === 'not-allowed'
        ? (homeScreenIOS
          ? 'Speech input was not allowed in this Home Screen app. Reopen the dashboard at its HTTPS address to enable direct microphone recording.'
          : 'Speech input was not allowed. Check browser microphone and speech permissions, then retry.')
        : 'Recording failed. Hold to try again.';
    };
    recognition.onend = () => complete(run);
    try { recognition.start(); } catch (_) { run.error = 'Could not start recording. Hold to retry.'; complete(run); }
  }
  const positionKey = 'ccc-mobile-ticket-record-position';
  let drag = null, savedPosition = null;
  function place(x, y) {
    const viewport = window.visualViewport;
    const width = viewport?.width || window.innerWidth;
    const height = viewport?.height || window.innerHeight;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const nav = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ccc-mobile-app-nav-height')) || 70;
    const maxX = Math.max(8, width - 72), maxY = Math.max(8, height - nav - 80);
    x = Math.max(8, Math.min(maxX, x - left));
    y = Math.max(8, Math.min(maxY, y - top));
    button.style.left = (left + x) + 'px'; button.style.top = (top + y) + 'px';
    button.style.right = 'auto';
    return {x:x / maxX, y:y / maxY};
  }
  function restorePosition() {
    if (!savedPosition || drag) return;
    const viewport = window.visualViewport;
    place((viewport?.offsetLeft || 0) + savedPosition.x * Math.max(8, (viewport?.width || innerWidth) - 72),
      (viewport?.offsetTop || 0) + savedPosition.y * Math.max(8, (viewport?.height || innerHeight)
        - (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ccc-mobile-app-nav-height')) || 70) - 80));
  }
  try {
    const value = JSON.parse(localStorage.getItem(positionKey));
    if (value && Number.isFinite(value.x) && Number.isFinite(value.y)) savedPosition = value;
  } catch (_) {}
  restorePosition();
  // Navigation measures itself after this script loads. Reapply the saved
  // ratios when that measurement arrives, not just on viewport resize.
  let measuredNav = document.documentElement.style.getPropertyValue('--ccc-mobile-app-nav-height');
  new MutationObserver(() => {
    const next = document.documentElement.style.getPropertyValue('--ccc-mobile-app-nav-height');
    if (next !== measuredNav) { measuredNav = next; restorePosition(); }
  }).observe(document.documentElement, {attributes:true, attributeFilter:['style']});
  window.addEventListener('resize', restorePosition);
  window.visualViewport?.addEventListener('resize', restorePosition);
  window.visualViewport?.addEventListener('scroll', restorePosition);
  button.addEventListener('pointerdown', event => {
    if (event.button !== 0 || active || opening || drag) return;
    event.preventDefault();
    const rect = button.getBoundingClientRect();
    drag = {id:event.pointerId, x:event.clientX, y:event.clientY,
      left:rect.left, top:rect.top, moved:false};
    button.setPointerCapture(event.pointerId);
    start();
    if (active) active.pointer = event.pointerId;
  });
  button.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 10) return;
    if (!drag.moved) {
      drag.moved = true;
      if (active) { active.cancelled = true; finish(active, true); }
    }
    savedPosition = place(drag.left + dx, drag.top + dy);
  });
  function endDrag(event, cancelled = false) {
    if (!drag || drag.id !== event.pointerId) return;
    const moved = drag.moved;
    drag = null;
    if (moved) {
      try { localStorage.setItem(positionKey, JSON.stringify(savedPosition)); } catch (_) {}
    }
    if (active?.pointer === event.pointerId && !active.finishing) finish(active, cancelled || moved);
  }
  button.addEventListener('pointerup', event => endDrag(event));
  button.addEventListener('pointercancel', event => endDrag(event, true));
  button.addEventListener('lostpointercapture', event => endDrag(event, true));
  button.addEventListener('contextmenu', event => event.preventDefault());
  button.addEventListener('keydown', event => {
    if (event.key === 'Escape' && active) finish(active, true);
    if ([' ', 'Enter'].includes(event.key)) { event.preventDefault(); if (!event.repeat) start(); }
  });
  button.addEventListener('keyup', event => { if ([' ', 'Enter'].includes(event.key) && active) finish(active); });
  // Assistive-technology activation uses click-to-start/click-to-finish.
  button.addEventListener('click', event => { if (event.detail === 0) { if (active) finish(active); else start(); } });
  document.addEventListener('visibilitychange', () => { if (document.hidden && active) finish(active, true); });
  window.addEventListener('blur', () => { if (active) finish(active, true); });
  applyPreference();
})();
