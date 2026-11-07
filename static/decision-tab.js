/* Keep each Decisions document alive across the sidebar's frequent rebuilds. */
(function () {
  'use strict';
  const views = [
    {tab:'decisions', hostId:'sidebarDecisionHost', frameId:'cccDecisionTabFrame', title:'Decisions', url:'/decision-inbox.html'},
    {tab:'decisions2', hostId:'sidebarDecisionGuideHost', frameId:'cccDecisionGuideFrame', title:'Decisions', url:'/decision-guide.html'}
  ];
  let count = null;
  const style = document.createElement('style');
  style.textContent = `
    body.ccc-decisions-tab #pwaInstallBanner { display:none !important; }
    #convList > .conv-tab-bar { min-width:0; max-width:100%; overflow-x:auto; scrollbar-width:thin; }
    #convList:has(> .decisions-host) { display:flex; flex-direction:column; overflow:hidden !important; }
    #convList:has(> .decisions-host) > .conv-tab-bar { flex:0 0 auto; }
    .decisions-host { flex:1 1 auto; min-height:0; }
    .ccc-decision-frame { position:fixed; border:0; z-index:2; background:var(--bg,#0f1115); }
    .ccc-decision-frame[hidden] { display:none; }
    body.ccc-core-app-open .ccc-decision-frame, body.ccc-mobile-app-open .ccc-decision-frame { display:none; }
  `;
  document.head.append(style);
  const resize = new ResizeObserver(position);
  function revealTab() {
    const tab = document.querySelector('[data-conv-tab="decisions"].is-active, [data-conv-tab="decisions2"].is-active');
    if (!tab) return;
    const strip = tab.parentElement, bounds = strip.getBoundingClientRect(), item = tab.getBoundingClientRect();
    if (item.right > bounds.right) strip.scrollLeft += item.right - bounds.right;
    else if (item.left < bounds.left) strip.scrollLeft -= bounds.left - item.left;
  }
  function position() {
    revealTab();
    for (const view of views) {
      const {frame, host} = view;
      if (!frame) continue;
      if (!host || !host.isConnected) { frame.hidden = true; view.visible = false; continue; }
      const rect = host.getBoundingClientRect();
      frame.hidden = rect.width < 1 || rect.height < 1;
      Object.assign(frame.style, {left:rect.left + 'px', top:rect.top + 'px', width:rect.width + 'px', height:rect.height + 'px'});
      if (!frame.hidden && !view.visible) frame.contentWindow.postMessage({type:'ccc:decision-refresh'}, location.origin);
      view.visible = !frame.hidden;
    }
  }
  function badge() {
    for (const view of views) {
      const tab = document.querySelector('[data-conv-tab="' + view.tab + '"]');
      if (!tab) continue;
      let value = tab.querySelector('.conv-tab-count');
      if (count === null) {
        if (value) value.remove();
        tab.title = 'Owner questions; current source count unavailable';
      } else {
        if (!value) { value = document.createElement('span'); value.className = 'conv-tab-count'; tab.append(value); }
        value.textContent = String(count);
        tab.title = count + ' owner questions need an answer in supported sources';
      }
    }
    revealTab();
  }
  function mount() {
    badge(); resize.disconnect();
    for (const view of views) {
      view.host = document.getElementById(view.hostId);
      if (view.host) {
        if (list) list.scrollLeft = 0;
        resize.observe(view.host);
        if (!view.frame) {
          view.frame = document.createElement('iframe'); view.frame.id = view.frameId;
          view.frame.className = 'ccc-decision-frame'; view.frame.title = view.title;
          view.frame.src = view.url; document.body.append(view.frame);
        }
      }
    }
    document.body.classList.toggle('ccc-decisions-tab', views.some(view => !!view.host));
    position();
  }
  window.CCCDecisionTab = {mount};
  window.addEventListener('resize', position);
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || !event.data) return;
    const native = views.some(view => view.frame && event.source === view.frame.contentWindow);
    const mobile = document.getElementById('cccMobileAppFrame');
    if (event.data.type === 'ccc:open-ticket' && (native || (mobile && event.source === mobile.contentWindow))
        && typeof window.cccOpenTicket === 'function') {
      window.cccOpenTicket(event.data.ref); return;
    }
    if (!native || event.data.type !== 'ccc:decision-count') return;
    const value = event.data.count;
    count = Number.isInteger(value) && value >= 0 ? value : null; badge();
    window.dispatchEvent(new CustomEvent('ccc:decision-count', {detail:{count}}));
  });
  const list = document.getElementById('convList');
  if (list) new MutationObserver(mount).observe(list, {childList:true});
  mount();
})();
