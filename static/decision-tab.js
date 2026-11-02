/* Keep one Decisions document alive across the sidebar's frequent rebuilds. */
(function () {
  'use strict';
  let frame, host, count = null;
  const style = document.createElement('style');
  style.textContent = `
    body.ccc-decisions-tab #pwaInstallBanner { display:none !important; }
    #convList:has(> .decisions-host) { display:flex; flex-direction:column; overflow:hidden !important; }
    #convList:has(> .decisions-host) > .conv-tab-bar { flex:0 0 auto; }
    .decisions-host { flex:1 1 auto; min-height:0; }
    #cccDecisionTabFrame { position:fixed; border:0; z-index:2; background:var(--bg,#0f1115); }
    #cccDecisionTabFrame[hidden] { display:none; }
    body.ccc-core-app-open #cccDecisionTabFrame, body.ccc-mobile-app-open #cccDecisionTabFrame { display:none; }
  `;
  document.head.append(style);
  const resize = new ResizeObserver(position);
  function position() {
    if (!frame || !host || !host.isConnected) { if (frame) frame.hidden = true; return; }
    const rect = host.getBoundingClientRect();
    frame.hidden = rect.width < 1 || rect.height < 1;
    Object.assign(frame.style, {left:rect.left + 'px', top:rect.top + 'px', width:rect.width + 'px', height:rect.height + 'px'});
  }
  function badge() {
    const tab = document.querySelector('[data-conv-tab="decisions"]');
    if (!tab) return;
    let value = tab.querySelector('.conv-tab-count');
    if (count === null) { if (value) value.remove(); tab.title = 'Owner questions; current source count unavailable'; return; }
    if (!value) { value = document.createElement('span'); value.className = 'conv-tab-count'; tab.append(value); }
    value.textContent = String(count);
    tab.title = count + ' owner questions need an answer in supported sources';
  }
  function mount() {
    badge();
    const target = document.getElementById('sidebarDecisionHost');
    document.body.classList.toggle('ccc-decisions-tab', !!target);
    if (host !== target) { resize.disconnect(); host = target; if (host) resize.observe(host); }
    if (host && !frame) {
      frame = document.createElement('iframe'); frame.id = 'cccDecisionTabFrame';
      frame.title = 'Decisions'; frame.src = '/decision-inbox.html'; document.body.append(frame);
    }
    position();
  }
  window.CCCDecisionTab = {mount};
  window.addEventListener('resize', position);
  window.addEventListener('message', event => {
    const mobileFrame = document.getElementById('cccMobileAppFrame');
    if (event.origin === location.origin && event.data && event.data.type === 'ccc:open-ticket'
        && ((frame && event.source === frame.contentWindow)
            || (mobileFrame && event.source === mobileFrame.contentWindow))
        && typeof window.cccOpenTicket === 'function') {
      window.cccOpenTicket(event.data.ref); return;
    }
    if (!frame || event.source !== frame.contentWindow || event.origin !== location.origin
        || !event.data || event.data.type !== 'ccc:decision-count') return;
    const value = event.data.count;
    count = Number.isInteger(value) && value >= 0 ? value : null; badge();
  });
  const list = document.getElementById('convList');
  if (list) new MutationObserver(mount).observe(list, {childList:true});
  mount();
})();
