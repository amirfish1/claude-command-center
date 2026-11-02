/* Keep mobile app navigation in the dashboard shell, using the rail catalog. */
(function () {
  'use strict';
  if (window.self !== window.top) return;
  const mobile = () => matchMedia('(max-width: 1200px)').matches || new URLSearchParams(location.search).get('mobile') === '1';
  let apps = window.cccRailApps || [], active = '', frame;
  const dashboard = !!document.getElementById('mobileBottomNav');
  const style = document.createElement('style');
  style.textContent = `
    #cccMobileAppFrame { position:fixed; top:calc(var(--app-vv-top, 0px) + env(safe-area-inset-top, 0px)); left:env(safe-area-inset-left, 0px); width:calc(100% - env(safe-area-inset-left, 0px) - env(safe-area-inset-right, 0px)); height:max(0px, calc(var(--app-vh, 100dvh) - env(safe-area-inset-top, 0px) - var(--ccc-mobile-app-nav-height, 70px))); border:0; background:var(--bg,#0f1115); z-index:900; }
    #cccMobileAppFrame[hidden] { display:none; }
    body.ccc-mobile-app-open #ideaTicketsIntroBanner, body.ccc-mobile-app-open #pwaInstallBanner { display:none !important; }
    body.has-mobile-bottom-nav #mobileBottomNav { bottom:auto; top:calc(var(--app-vv-top, 0px) + var(--app-vh, 100dvh)); transform:translateY(-100%); padding:4px max(4px, env(safe-area-inset-right, 0px)) max(2px, env(safe-area-inset-bottom, 0px)) max(4px, env(safe-area-inset-left, 0px)); }
    body.ccc-mobile-app-open #mobileBottomNav { display:flex !important; z-index:950; }
    body:has(#mobileBottomNav .mobile-nav-menu:not([hidden])) #pwaInstallBanner { display:none !important; }
    #mobileBottomNav .mobile-nav-menu { max-height:calc(100dvh - 100px); overflow-y:auto; overscroll-behavior:contain; min-width:220px; max-width:calc(100vw - 16px); }
    #mobileBottomNav .ccc-mobile-app-list { display:flex; flex-direction:column; border-top:1px solid var(--border); margin-top:6px; padding-top:6px; }
    #mobileBottomNav .ccc-mobile-app-list .mobile-nav-btn { flex:none; flex-direction:row; justify-content:flex-start; gap:12px; min-height:44px; padding:8px 12px; }
    #mobileBottomNav .ccc-mobile-app-list .mobile-nav-label { font-size:15px; }
    #mobileBottomNav .ccc-mobile-app-heading { padding:8px 12px; color:var(--text-muted); font-size:12px; }
    #mobileBottomNav[data-ccc-apps-open] .mobile-nav-menu:not([hidden]) { display:flex; }
    body.ccc-mobile-app-open #mobileBottomNav > [data-mobile-nav] { color:var(--text-muted); }
    body.ccc-mobile-app-open #mobileBottomNav > [data-mobile-nav-menu], body.ccc-mobile-app-open #mobileBottomNav > [data-mobile-nav="more"] { color:var(--green); }
  `;
  document.head.appendChild(style);

  function appURL(app) {
    // External apps already have a same-origin /app/<id> wrapper from /api/apps.
    try { const u = new URL(app.nav || app.url, location.origin); return u.origin === location.origin ? u : null; } catch (_) { return null; }
  }
  function setMenu(open) {
    const nav = document.getElementById('mobileBottomNav');
    if (!nav) return;
    nav.toggleAttribute('data-ccc-apps-open', open);
    nav.querySelector('.mobile-nav-menu').hidden = !open;
    nav.querySelectorAll('[data-mobile-nav-menu], :scope > [data-mobile-nav="more"]').forEach(b => b.setAttribute('aria-expanded', String(open)));
  }
  function mark() {
    document.querySelectorAll('[data-mobile-app]').forEach(el => {
      const on = el.dataset.mobileApp === active;
      el.classList.toggle('is-active', on);
      if (on) el.setAttribute('aria-current','page'); else el.removeAttribute('aria-current');
    });
  }
  function close(push) {
    if (!active) return;
    active = '';
    if (frame) frame.hidden = true;
    document.body.classList.remove('ccc-mobile-app-open');
    if (typeof window.cccSwitchCoreApp === 'function') window.cccSwitchCoreApp('sessions', {push:false,force:true});
    mark();
    if (push) history.pushState({}, '', '/');
  }
  function open(id, push) {
    const app = apps.find(a => a.id === id), url = app && appURL(app);
    if (!app || !url) return;
    setMenu(false);
    if (id === 'sessions') { close(push); if (typeof window.cccSwitchCoreApp === 'function') window.cccSwitchCoreApp('sessions'); return; }
    if (!mobile() && dashboard) { location.replace(url.href); return; }
    if (!dashboard) { location.href = '/?ccc_app=' + encodeURIComponent(id); return; }
    if (typeof window.cccSwitchCoreApp !== 'function') return;
    window.cccSwitchCoreApp('sessions', {push:false});
    document.body.classList.remove('mobile-ask-open');
    if (!frame) {
      frame = document.createElement('iframe'); frame.id = 'cccMobileAppFrame';
      document.body.appendChild(frame);
    }
    const target = url.pathname + url.search + url.hash;
    if (frame.dataset.appUrl !== target) {
      // Replacing the child location keeps app switching to one history entry.
      try { frame.contentWindow.location.replace(target); } catch (_) { frame.src = target; }
      frame.dataset.appUrl = target;
    }
    frame.title = app.label; frame.hidden = false; active = id;
    document.body.classList.add('ccc-mobile-app-open');
    document.title = app.label + ' · CCC';
    mark();
    if (push) history.pushState({cccMobileApp:id}, '', '/?ccc_app=' + encodeURIComponent(id));
  }
  function restore() {
    const id = new URLSearchParams(location.search).get('ccc_app');
    if (id) open(id, false); else close(false);
  }
  function render() {
    if (!dashboard) {
      if (!mobile()) return;
      const current = apps.filter(a => a.id !== 'sessions').find(a => {
        const u = appURL(a); return u && u.pathname === location.pathname;
      });
      if (current) location.replace('/?ccc_app=' + encodeURIComponent(current.id));
      return;
    }
    const nav = document.getElementById('mobileBottomNav'), menu = nav.querySelector('.mobile-nav-menu');
    if (!menu) return;
    let list = menu.querySelector('.ccc-mobile-app-list');
    if (!list) { list = document.createElement('div'); list.className = 'ccc-mobile-app-list'; list.setAttribute('role','group'); list.setAttribute('aria-label','Apps'); menu.appendChild(list); }
    list.replaceChildren();
    const heading = document.createElement('span'); heading.className = 'ccc-mobile-app-heading'; heading.textContent = 'Apps'; list.appendChild(heading);
    apps.filter(a => a.enabled !== false && appURL(a)).forEach(app => {
      const a = document.createElement('a'); a.className = 'mobile-nav-btn'; a.dataset.mobileApp = app.id;
      a.href = app.id === 'sessions' ? '/' : '/?ccc_app=' + encodeURIComponent(app.id); a.setAttribute('role','menuitem');
      const icon = document.createElement('span'); icon.className = 'mobile-nav-icon'; icon.textContent = app.icon || ''; icon.setAttribute('aria-hidden','true');
      const label = document.createElement('span'); label.className = 'mobile-nav-label'; label.textContent = app.label;
      a.append(icon,label); list.appendChild(a);
    });
    mark();
    if (document.readyState === 'complete') restore();
  }
  window.addEventListener('ccc:apps', e => { apps = e.detail; render(); });
  if (!dashboard) window.addEventListener('resize', render);
  window.addEventListener('popstate', restore);
  window.addEventListener('load', restore);
  if (dashboard) {
    const nav = document.getElementById('mobileBottomNav');
    const simpleMore = nav.querySelector(':scope > [data-mobile-nav="more"]');
    if (simpleMore) {
      simpleMore.setAttribute('aria-label','More'); simpleMore.title = 'More'; simpleMore.setAttribute('aria-haspopup','menu');
      simpleMore.querySelector('.mobile-nav-label').textContent = 'More';
      const settings = simpleMore.cloneNode(true); settings.removeAttribute('aria-haspopup'); settings.title = 'Settings'; settings.setAttribute('aria-label','Settings'); settings.setAttribute('role','menuitem'); settings.querySelector('.mobile-nav-label').textContent = 'Settings';
      nav.querySelector('.mobile-nav-menu').prepend(settings);
    }
    const measure = () => document.documentElement.style.setProperty('--ccc-mobile-app-nav-height', nav.getBoundingClientRect().height + 'px');
    new ResizeObserver(measure).observe(nav);
    nav.addEventListener('click', e => {
      const app = e.target.closest('[data-mobile-app]');
      if (app) { if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; e.preventDefault(); e.stopImmediatePropagation(); open(app.dataset.mobileApp, true); return; }
      if (e.target.closest('[data-mobile-nav-menu]') || e.target.closest('[data-mobile-nav]') === simpleMore) {
        e.preventDefault(); e.stopImmediatePropagation(); setMenu(nav.querySelector('.mobile-nav-menu').hidden); return;
      }
      if (e.target.closest('[data-mobile-nav]')) { close(true); setMenu(false); }
    }, true);
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && !nav.querySelector('.mobile-nav-menu').hidden) { setMenu(false); (document.body.classList.contains('ccc-simple-mode') ? simpleMore : nav.querySelector('[data-mobile-nav-menu]')).focus(); } });
    window.addEventListener('resize', () => { if (!mobile() && active) { const app = apps.find(a => a.id === active); if (app) location.assign(app.nav || app.url); } });
  }
  render();
})();
