'use strict';
// Apps home: a grid of app cards with a one-line status; clicking a card
// opens the app in a resizable side panel.
(function () {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const getJSON = async u => { try { const r = await fetch(u, {cache:'no-store', signal:AbortSignal.timeout(15000)}); return r.ok ? await r.json() : null; } catch { return null; } };
  const ago = t => { const s = Math.max(0, (Date.now() - t) / 1000); if (s < 90) return 'just now'; if (s < 5400) return Math.round(s / 60) + ' min ago'; if (s < 129600) return Math.round(s / 3600) + ' h ago'; return Math.round(s / 86400) + ' d ago'; };
  const working = s => s.state === 'working';

  // One line of status per app, from the routines overview, its sessions and
  // the freshness of its data file.
  async function statFor(app, sessions, over) {
    if (app.id === 'sessions') {
      const real = sessions.filter(s => !s.helper && !s.archived && !s.trashed);
      const w = real.filter(working).length;
      return (w ? `<span class="dot"></span><b>${w} working</b> · ` : '') + `${real.length} sessions today`;
    }
    if (!String(app.nav).startsWith('/view/')) return '';
    const [meta, data] = await Promise.all([getJSON(`/app-static/${app.id}/app.json`), getJSON(`/app-static/${app.id}/data.json`)]);
    const bits = [];
    const topic = meta && meta.topic;
    if (topic) {
      const ids = new Set((topic.sessions || []).map(s => s.id));
      for (const s of sessions) if (topic.agent_prefix && String(s.name || '').toLowerCase().startsWith(topic.agent_prefix) && !s.helper && !s.trashed) ids.add(s.session_id);
      const w = sessions.filter(s => ids.has(s.session_id) && working(s)).length;
      if (w) bits.push(`<span class="dot"></span><b>${w} agent${w > 1 ? 's' : ''} working</b>`);
      else if (ids.size) bits.push(`${ids.size} agent${ids.size > 1 ? 's' : ''}`);
      const parts = topic.parts || [];
      if (parts.length) bits.push(`${parts.filter(p => p.status === 'live').length} of ${parts.length} parts live`);
    }
    const o = over && over[app.id];
    if (o) {
      const a = o.agent && sessions.find(s => s.session_id === o.agent);
      if (a && working(a) && !bits.length) bits.push('<span class="dot"></span><b>agent working</b>');
      if (o.alerts) bits.unshift(`<b style="color:var(--bad)">${o.alerts} need${o.alerts > 1 ? '' : 's'} you</b>`);
      if (o.routines) bits.push(`${o.on} routine${o.on === 1 ? '' : 's'} on`);
    }
    const stamp = data && Date.parse(data.generated_at || data.updated_at || '');
    if (stamp) bits.push('data updated ' + ago(stamp));
    return bits.join(' · ');
  }

  // Drag the divider to resize; the width is remembered on this browser.
  function grip(el, box, cssVar, key) {
    const set = w => { w = Math.max(320, Math.min(box.clientWidth * 0.75, w)); box.style.setProperty(cssVar, w + 'px'); return w; };
    try { const saved = +localStorage.getItem(key); if (saved) set(saved); } catch (_) {}
    el.addEventListener('pointerdown', e => {
      e.preventDefault(); el.setPointerCapture(e.pointerId); document.body.classList.add('dragging');
      const move = ev => { const w = set(box.getBoundingClientRect().right - ev.clientX); try { localStorage.setItem(key, String(Math.round(w))); } catch (_) {} };
      const up = () => { document.body.classList.remove('dragging'); el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up); el.removeEventListener('pointercancel', up); };
      el.addEventListener('pointermove', move); el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up);
    });
  }

  // The card picture: preview.png if the app has one, else a live thumbnail
  // for light /view/ pages, else the big icon.
  function shotFor(a) {
    const big = `<div class="big">${esc(a.icon)}</div>`;
    if (a.id === 'sessions' || a.id === 'queues' || !String(a.nav).startsWith('/')) return big;
    const live = String(a.nav).startsWith('/view/') ? `<iframe src="${esc(a.nav)}#thumb" loading="lazy" tabindex="-1" aria-hidden="true"></iframe>` : big;
    return `<img src="/app-static/${encodeURIComponent(a.id)}/preview.png" alt="" loading="lazy" data-fallback="${esc(live)}">`;
  }

  async function load() {
    const [appsRes, census, rout, concierge] = await Promise.all([
      getJSON('/api/apps'), getJSON('/api/sessions/census'), getJSON('/api/apps/routines'),
      fetch('/static/app-concierge.js').then(r => r.ok).catch(() => false)]);
    const over = (rout && rout.apps) || {};
    const apps = ((appsRes && appsRes.apps) || []).filter(a => a.id !== 'home' && a.id !== 'apps');
    const sessions = (census && census.sessions) || [];
    document.getElementById('sub').textContent = `${apps.length} apps on this machine`;
    const grid = document.getElementById('grid');
    grid.innerHTML = apps.map(a =>
      `<a class="card" data-app="${esc(a.id)}" href="${esc(a.nav)}"><div class="shot">${shotFor(a)}</div><div class="meta"><div class="name"><span>${esc(a.icon)}</span>${esc(a.label)}</div><div class="stat" data-stat="${esc(a.id)}"></div></div></a>`
    ).join('') + `<a class="card new" id="newapp" href="/applications#add"><div class="plus">+</div><div>New app</div></a>`;
    grid.querySelectorAll('img[data-fallback]').forEach(img => img.addEventListener('error', () => {
      img.insertAdjacentHTML('afterend', img.dataset.fallback); img.remove();
    }));
    if (concierge) {
      const s = document.createElement('script'); s.src = '/static/app-concierge.js';
      s.onload = () => {
        const n = document.getElementById('newapp');
        if (!n || !window.cccConcierge) return;
        const box = document.createElement('div');
        box.className = 'card new'; box.id = 'newapp';
        box.innerHTML = '<div class="plus">+</div><div>New app</div><div data-ccc-concierge style="padding:0 14px 14px;width:100%"></div>';
        n.replaceWith(box);
        window.cccConcierge.mount(box.querySelector('[data-ccc-concierge]'));
      };
      document.body.appendChild(s);
    }
    const open = id => {
      const a = apps.find(x => x.id === id);
      document.getElementById('shell').classList.toggle('open', !!a);
      grid.querySelectorAll('.card').forEach(c => c.classList.toggle('sel', !!a && c.dataset.app === id));
      if (!a) { history.replaceState(null, '', location.pathname); return; }
      document.getElementById('ptitle').textContent = a.icon + ' ' + a.label;
      document.getElementById('pfull').href = a.nav;
      const box = document.getElementById('pframes');
      let f = [...box.children].find(x => x.dataset.app === id);
      if (!f) { f = document.createElement('iframe'); f.title = a.label; f.dataset.app = id; f.src = a.nav; if (box.children.length >= 4) box.firstElementChild.remove(); }
      box.appendChild(f);
      [...box.children].forEach(x => x.hidden = x !== f);
      history.replaceState(null, '', '#' + id);
    };
    grid.querySelectorAll('.card[data-app]').forEach(c => c.addEventListener('click', e => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
      e.preventDefault(); open(c.dataset.app);
    }));
    document.getElementById('pclose').onclick = () => open(null);
    grip(document.getElementById('pgrip'), document.getElementById('shell'), '--panel-w', 'apps-panel-w');
    if (location.hash.length > 1) open(decodeURIComponent(location.hash.slice(1)));
    for (const a of apps) statFor(a, sessions, over).then(html => { const el = grid.querySelector(`[data-stat="${CSS.escape(a.id)}"]`); if (el) el.innerHTML = html; });
  }
  load();
})();
