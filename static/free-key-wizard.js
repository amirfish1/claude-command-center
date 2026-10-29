/* Free-key wizard: guided provider setup for the $0 router.
 *
 * Shows each free provider as a card: what it is, where to sign up, and a
 * paste box that checks the key live through the managed router
 * (POST /api/free-router/keys). Kilo needs no key at all, just one consent
 * click (its free tier logs prompts for training, so the card says that out
 * loud above the button).
 *
 * Used embedded by the onboarding flow (L07 mounts it into its step) and
 * standalone via cccFreeKeyWizard.open() from Settings / anywhere.
 *
 * Backend: ccc_server/free_providers.py. Keys go to the router only; they
 * are never kept, logged, or rendered back by this page.
 */
(function () {
  'use strict';
  if (window.cccFreeKeyWizard) return;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function injectStyle() {
    if (document.getElementById('ccc-fkw-style')) return;
    const st = document.createElement('style');
    st.id = 'ccc-fkw-style';
    st.textContent = [
      '.ccc-fkw{font:13px/1.5 system-ui,-apple-system,sans-serif;color:var(--text,#e6e6e6)}',
      '.ccc-fkw *{box-sizing:border-box}',
      '.ccc-fkw-head{margin:0 0 4px;font-size:19px;font-weight:700;letter-spacing:-.01em}',
      '.ccc-fkw-sub{margin:0 0 14px;color:var(--text-dim,#aab);font-size:13px}',
      '.ccc-fkw-banner{display:flex;align-items:center;gap:8px;margin:0 0 12px;padding:9px 12px;border-radius:8px;font-size:12.5px;background:rgba(217,160,63,.12);border:1px solid rgba(217,160,63,.35);color:var(--text,#e6e6e6)}',
      '.ccc-fkw-banner[hidden]{display:none}',
      '.ccc-fkw-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}',
      '.fkw-card{position:relative;display:flex;flex-direction:column;gap:7px;padding:12px 13px;border-radius:10px;border:1px solid var(--border,#333);background:var(--bg-elev,#1b1e24);transition:border-color .18s ease,box-shadow .18s ease}',
      '.fkw-card:hover{border-color:var(--accent,#d97757)}',
      '.fkw-card.is-done{border-color:var(--success,#6fcf97);box-shadow:0 0 0 1px var(--success,#6fcf97) inset}',
      '.fkw-card.is-busy{pointer-events:none;opacity:.85}',
      '.fkw-top{display:flex;align-items:center;gap:7px;flex-wrap:wrap}',
      '.fkw-name{font-size:14px;font-weight:650}',
      '.fkw-badge{font-size:10px;font-weight:650;letter-spacing:.02em;padding:1.5px 7px;border-radius:99px;border:1px solid var(--border,#444);color:var(--text-dim,#aab);white-space:nowrap}',
      '.fkw-badge.free{color:var(--success,#6fcf97);border-color:var(--success,#6fcf97)}',
      '.fkw-badge.pick{color:#fff;background:var(--accent,#d97757);border-color:var(--accent,#d97757)}',
      '.fkw-tag{color:var(--text-dim,#aab);font-size:12px;margin:0}',
      '.fkw-tos{color:var(--text-dim,#aab);font-size:11.5px;margin:0}',
      '.fkw-tos strong{color:var(--warn,#e6b34d);font-weight:600}',
      '.fkw-row{display:flex;gap:6px;margin-top:auto;flex-wrap:wrap}',
      '.fkw-row input{flex:1 1 100%;min-width:0;padding:7px 9px;border-radius:7px;border:1px solid var(--border,#444);background:var(--bg,#111);color:inherit;font:12px ui-monospace,monospace}',
      '.fkw-row input:focus{outline:none;border-color:var(--accent,#d97757)}',
      '.fkw-row input.shake{border-color:var(--danger,#f07070)}',
      '.fkw-btn{padding:7px 11px;border-radius:7px;border:1px solid var(--border,#444);background:transparent;color:inherit;cursor:pointer;font-size:12px;font-weight:600;white-space:nowrap;transition:background .15s ease,border-color .15s ease}',
      '.fkw-btn:hover{border-color:var(--accent,#d97757)}',
      '.fkw-btn.primary{background:var(--accent,#d97757);border-color:var(--accent,#d97757);color:#fff}',
      '.fkw-btn.primary:hover{filter:brightness(1.08)}',
      '.fkw-btn.ghost{color:var(--accent,#d97757);border-color:transparent;text-decoration:none;display:inline-flex;align-items:center;gap:3px}',
      '.fkw-btn:disabled{opacity:.55;cursor:default;filter:none}',
      '.fkw-msg{min-height:1em;margin:0;font-size:11.5px}',
      '.fkw-msg.err{color:var(--danger,#f07070)}',
      '.fkw-msg.warn{color:var(--warn,#e6b34d)}',
      '.fkw-msg.ok{color:var(--success,#6fcf97)}',
      '.fkw-done{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--success,#6fcf97);font-weight:600}',
      '.fkw-done .dot{width:20px;height:20px;border-radius:50%;background:var(--success,#6fcf97);color:#0e1512;display:flex;align-items:center;justify-content:center;font-size:12px;flex:none}',
      '.fkw-done .masked{color:var(--text-dim,#aab);font:11px ui-monospace,monospace;font-weight:400}',
      '.fkw-replace{background:none;border:none;padding:0;color:var(--text-dim,#aab);font-size:11px;cursor:pointer;text-decoration:underline;margin-left:auto}',
      '.fkw-foot{display:flex;align-items:center;justify-content:space-between;margin-top:14px;gap:10px}',
      '.fkw-skip{background:none;border:none;color:var(--text-dim,#aab);font-size:12px;cursor:pointer;text-decoration:underline;padding:4px}',
      '.fkw-count{color:var(--text-dim,#aab);font-size:12px}',
      '.fkw-spin{display:inline-block;width:11px;height:11px;border:2px solid var(--text-dim,#aab);border-top-color:transparent;border-radius:50%;vertical-align:-1px;margin-right:5px;animation:fkw-spin .7s linear infinite}',
      '@keyframes fkw-spin{to{transform:rotate(360deg)}}',
      '.ccc-fkw-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:10060;display:flex;align-items:center;justify-content:center;padding:18px}',
      '.ccc-fkw-modal{background:var(--bg,#14161a);border:1px solid var(--border,#333);border-radius:14px;padding:22px 24px;width:min(860px,96vw);max-height:88vh;overflow-y:auto;box-shadow:0 18px 60px rgba(0,0,0,.5)}',
      '.ccc-fkw-close{position:sticky;float:right;top:0;background:none;border:none;color:var(--text-dim,#aab);font-size:20px;cursor:pointer;line-height:1;padding:2px 6px}',
      '@media (prefers-reduced-motion: reduce){.fkw-card,.fkw-btn{transition:none}.fkw-spin{animation:none}}',
    ].join('\n');
    document.head.appendChild(st);
  }

  async function fetchJSON(url, opts) {
    const r = await fetch(url, opts);
    const d = await r.json().catch(() => null);
    return { status: r.status, data: d };
  }

  function fx(name) {
    try {
      if (window.cccFx && typeof window.cccFx.play === 'function') window.cccFx.play(name);
    } catch (_) { /* sound kit optional */ }
  }
  function confetti() {
    try {
      if (window.cccFx && typeof window.cccFx.confetti === 'function') window.cccFx.confetti();
    } catch (_) { /* confetti optional */ }
  }

  const BADGE = { kilo: 'Easiest', google: 'Best pick' };

  // -------------------------------------------------------------------------
  // Card view
  // -------------------------------------------------------------------------

  function cardState(card) {
    return card._fkw || (card._fkw = { busy: false, done: false });
  }

  function setMsg(card, text, kind) {
    const el = card.querySelector('.fkw-msg');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'fkw-msg' + (kind ? ' ' + kind : '');
  }

  function setBusy(card, busy, label) {
    const st = cardState(card);
    st.busy = busy;
    card.classList.toggle('is-busy', busy);
    card.querySelectorAll('button,input').forEach((b) => { b.disabled = busy; });
    if (busy) setMsg(card, '', null);
    if (label) setMsg(card, label, null);
  }

  function markDone(card, masked) {
    const st = cardState(card);
    st.done = true;
    card.classList.add('is-done');
    const body = card.querySelector('.fkw-body');
    body.innerHTML = '';
    const row = document.createElement('div');
    row.className = 'fkw-done';
    row.innerHTML = '<span class="dot">&#10003;</span><span>Connected' +
      (masked ? ' <span class="masked">' + esc(masked) + '</span>' : '') + '</span>';
    const rep = document.createElement('button');
    rep.className = 'fkw-replace';
    rep.type = 'button';
    rep.textContent = 'replace key';
    rep.addEventListener('click', () => {
      st.done = false;
      card.classList.remove('is-done');
      renderBody(card);
    });
    row.appendChild(rep);
    body.appendChild(row);
    // Keep a live message slot under the done row: a saved-but-unconfirmed
    // submit still needs somewhere to say "checking" after markDone wipes
    // the body.
    const msg = document.createElement('p');
    msg.className = 'fkw-msg';
    msg.setAttribute('aria-live', 'polite');
    body.appendChild(msg);
  }

  function localFormatCheck(provider, key) {
    if (!key) return { ok: false, msg: 'Paste the key first.' };
    if (provider.key_regex) {
      try {
        if (!new RegExp(provider.key_regex).test(key)) {
          return { ok: false, msg: 'That does not look like a ' + provider.name + ' key.' };
        }
      } catch (_) { /* a bad pattern should never block a real key */ }
    }
    return { ok: true };
  }

  async function submit(provider, key, consent, card, ctl) {
    const res = await fetchJSON('/api/free-router/keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ platform: provider.platform, key: key || undefined, consent: consent || undefined }),
    });
    const d = res.data || {};
    if (d.ok && d.validated) {
      markDone(card, d.masked_key);
      setMsg(card, '', null);
      fx('success');
      confetti();
      ctl.noteSuccess(provider);
      return;
    }
    if (d.ok && !d.validated) {
      markDone(card, d.masked_key);
      setMsg(card, d.error || 'Saved. Still being checked.', 'warn');
      ctl.noteSuccess(provider);
      return;
    }
    if (d.code === 'router_unavailable') {
      setMsg(card, '', null);
      ctl.showBanner('Your free router is still starting up. Give it a few seconds, then try again.');
      return;
    }
    setMsg(card, d.error || 'Something went wrong. Try again.', 'err');
  }

  function renderBody(card) {
    const provider = card._provider;
    const ctl = card._ctl;
    const body = card.querySelector('.fkw-body');
    body.innerHTML = '';

    if (provider.keyless) {
      const tos = document.createElement('p');
      tos.className = 'fkw-tos';
      tos.innerHTML = '<strong>Heads up:</strong> ' + esc(provider.tos_note);
      body.appendChild(tos);
      const row = document.createElement('div');
      row.className = 'fkw-row';
      const btn = document.createElement('button');
      btn.className = 'fkw-btn primary';
      btn.type = 'button';
      btn.textContent = 'Turn on free models';
      btn.addEventListener('click', async () => {
        setBusy(card, true, 'Turning it on');
        try { await submit(provider, null, true, card, ctl); }
        finally { setBusy(card, false); }
      });
      row.appendChild(btn);
      body.appendChild(row);
    } else {
      const row = document.createElement('div');
      row.className = 'fkw-row';
      const link = document.createElement('a');
      link.className = 'fkw-btn ghost';
      link.href = provider.signup_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.innerHTML = 'Get a free key &#8599;';
      const input = document.createElement('input');
      input.type = 'password';
      input.placeholder = 'Paste key';
      input.autocomplete = 'off';
      input.spellcheck = false;
      input.setAttribute('aria-label', provider.name + ' API key');
      const btn = document.createElement('button');
      btn.className = 'fkw-btn primary';
      btn.type = 'button';
      btn.textContent = 'Save key';
      const go = async () => {
        const key = input.value.trim();
        const check = localFormatCheck(provider, key);
        if (!check.ok) {
          setMsg(card, check.msg, 'err');
          input.classList.add('shake');
          setTimeout(() => input.classList.remove('shake'), 1600);
          input.focus();
          return;
        }
        setBusy(card, true, 'Checking with ' + provider.name);
        try { await submit(provider, key, false, card, ctl); }
        finally { setBusy(card, false); }
      };
      btn.addEventListener('click', go);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
      row.appendChild(input);
      const row2 = document.createElement('div');
      row2.className = 'fkw-row';
      row2.style.justifyContent = 'space-between';
      row2.style.alignItems = 'center';
      row2.appendChild(link);
      row2.appendChild(btn);
      body.appendChild(row);
      body.appendChild(row2);
    }
    const msg = document.createElement('p');
    msg.className = 'fkw-msg';
    msg.setAttribute('aria-live', 'polite');
    body.appendChild(msg);
  }

  function buildCard(provider, ctl) {
    const card = document.createElement('div');
    card.className = 'fkw-card';
    card.dataset.platform = provider.platform;
    card._provider = provider;
    card._ctl = ctl;

    const top = document.createElement('div');
    top.className = 'fkw-top';
    const name = document.createElement('span');
    name.className = 'fkw-name';
    name.textContent = provider.name;
    top.appendChild(name);
    if (BADGE[provider.platform]) {
      const b = document.createElement('span');
      b.className = 'fkw-badge pick';
      b.textContent = BADGE[provider.platform];
      top.appendChild(b);
    }
    const free = document.createElement('span');
    free.className = 'fkw-badge free';
    free.textContent = provider.keyless ? 'No signup' : 'Free, no card';
    top.appendChild(free);
    card.appendChild(top);

    if (provider.tagline) {
      const tag = document.createElement('p');
      tag.className = 'fkw-tag';
      tag.textContent = provider.tagline;
      card.appendChild(tag);
    }
    if (!provider.keyless && provider.tos_note) {
      const tos = document.createElement('p');
      tos.className = 'fkw-tos';
      tos.textContent = provider.tos_note;
      card.appendChild(tos);
    }

    const body = document.createElement('div');
    body.className = 'fkw-body';
    card.appendChild(body);

    if (provider.has_key && provider.key_enabled) {
      markDone(card, provider.masked_key);
    } else {
      renderBody(card);
    }
    return card;
  }

  // -------------------------------------------------------------------------
  // Controller
  // -------------------------------------------------------------------------

  function mount(el, opts) {
    injectStyle();
    opts = opts || {};
    const root = document.createElement('div');
    root.className = 'ccc-fkw';
    el.innerHTML = '';
    el.appendChild(root);

    const ctl = {
      el: root,
      providers: [],
      successes: 0,
      opts,
      noteSuccess(provider) {
        this.successes += 1;
        const n = this.el.querySelector('.fkw-count');
        if (n) n.textContent = this.successes + ' connected';
        if (this.successes === 1 && typeof opts.onComplete === 'function') {
          try { opts.onComplete(provider); } catch (_) {}
        }
      },
      showBanner(text) {
        const b = this.el.querySelector('.ccc-fkw-banner');
        if (b) { b.textContent = text; b.hidden = false; }
      },
      refresh() { return load(this); },
    };

    function load(c) {
      root.innerHTML =
        '<h3 class="ccc-fkw-head">Get a free model</h3>' +
        '<p class="ccc-fkw-sub">Pick a provider. Free means free: no card, no trial clock. ' +
        'Your agent runs at $0 from here.</p>' +
        '<div class="ccc-fkw-banner" hidden></div>' +
        '<div class="ccc-fkw-grid"><p class="ccc-fkw-sub">Loading providers&hellip;</p></div>';
      return fetchJSON('/api/free-router/providers')
        .then((res) => {
          if (res.status !== 200 || !Array.isArray(res.data)) throw new Error('bad');
          c.providers = res.data;
          render(c);
        })
        .catch(() => {
          const grid = root.querySelector('.ccc-fkw-grid');
          grid.innerHTML = '';
          const p = document.createElement('p');
          p.className = 'ccc-fkw-sub';
          p.textContent = 'Providers did not load. Check your connection and try again.';
          const retry = document.createElement('button');
          retry.className = 'fkw-btn';
          retry.type = 'button';
          retry.textContent = 'Try again';
          retry.addEventListener('click', () => c.refresh());
          grid.appendChild(p);
          grid.appendChild(retry);
        });
    }

    function render(c) {
      const grid = root.querySelector('.ccc-fkw-grid');
      grid.innerHTML = '';
      c.providers.forEach((p) => grid.appendChild(buildCard(p, c)));
      const connected = c.providers.filter((p) => p.has_key && p.key_enabled).length;
      c.successes = connected;
      const foot = document.createElement('div');
      foot.className = 'fkw-foot';
      const count = document.createElement('span');
      count.className = 'fkw-count';
      count.textContent = connected ? connected + ' connected' : 'Connect at least one to go fully free';
      foot.appendChild(count);
      if (typeof opts.onSkip === 'function') {
        const skip = document.createElement('button');
        skip.className = 'fkw-skip';
        skip.type = 'button';
        skip.textContent = 'Skip for now';
        skip.addEventListener('click', () => { try { opts.onSkip(); } catch (_) {} });
        foot.appendChild(skip);
      }
      grid.after(foot);
      if (window.cccDomesticProviders) {
        const details = document.createElement('details');
        details.className = 'dp-wizard-details';
        const summary = document.createElement('summary');
        summary.textContent = 'Use your own paid key';
        const host = document.createElement('div');
        details.append(summary, host);
        details.addEventListener('toggle', () => {
          if (details.open) window.cccDomesticProviders.mount(host).refresh();
        });
        foot.after(details);
      }
    }

    load(ctl);
    return ctl;
  }

  function open(opts) {
    opts = opts || {};
    injectStyle();
    const backdrop = document.createElement('div');
    backdrop.className = 'ccc-fkw-backdrop';
    const box = document.createElement('div');
    box.className = 'ccc-fkw-modal';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-label', 'Get a free model');
    const close = document.createElement('button');
    close.className = 'ccc-fkw-close';
    close.type = 'button';
    close.setAttribute('aria-label', 'Close');
    close.innerHTML = '&times;';
    const host = document.createElement('div');
    box.appendChild(close);
    box.appendChild(host);
    backdrop.appendChild(box);
    document.body.appendChild(backdrop);

    function done() {
      backdrop.remove();
      document.removeEventListener('keydown', onKey);
    }
    function onKey(e) { if (e.key === 'Escape') done(); }
    document.addEventListener('keydown', onKey);
    close.addEventListener('click', done);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) done(); });

    mount(host, {
      onComplete(provider) {
        if (typeof opts.onComplete === 'function') {
          try { opts.onComplete(provider); } catch (_) {}
        }
        setTimeout(done, 1400);
      },
      onSkip() {
        done();
        if (typeof opts.onSkip === 'function') { try { opts.onSkip(); } catch (_) {} }
      },
    });
    return { close: done };
  }

  window.cccFreeKeyWizard = { mount: mount, open: open };
})();
