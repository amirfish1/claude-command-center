// Shared prep for CCC film flows against the live demo server: dismiss
// first-run prompts and hide the WatchTower-daemon alert (the demo HOME runs
// no daemon on purpose, so nothing drains the fake queues).
'use strict';

async function prep(ctx) {
  await ctx.eval(() => {
    const st = document.createElement('style');
    st.textContent = '#queueAlertStrip{display:none!important}';
    document.head.appendChild(st);
  });
  for (let i = 0; i < 3; i++) {
    const hit = await ctx.eval(() => {
      const b = [...document.querySelectorAll('button')]
        .find(x => /^\s*(Not now|Skip|Dismiss)\s*$/.test(x.textContent) && x.offsetParent);
      if (b) { b.click(); return true; }
      return false;
    });
    if (!hit) break;
    await ctx.pause(300);
  }
}

// Center of the visible element whose own text is exactly `text`.
async function ptOf(page, text, { maxX = 99999 } = {}) {
  return page.evaluate((t, mx) => {
    const el = [...document.querySelectorAll('div,span,a,li,button')]
      .filter(e => e.offsetParent && e.children.length < 3 && e.textContent.trim() === t
        && e.getBoundingClientRect().left < mx)[0];
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left + Math.min(r.width / 2, 160), y: r.top + r.height / 2 };
  }, text, maxX);
}

// Step caption: a numbered pill at the bottom of the frame, so rough cuts
// read as a walkthrough before there is narration. Replaces the previous one.
async function caption(ctx, text, { hold = 0 } = {}) {
  await ctx.eval((t) => {
    let el = document.getElementById('__filmCap');
    if (!el) {
      el = document.createElement('div');
      el.id = '__filmCap';
      el.style.cssText = 'position:fixed;left:50%;bottom:34px;transform:translateX(-50%);'
        + 'z-index:2147483646;background:rgba(12,14,20,.92);color:#fff;padding:14px 26px;'
        + 'border-radius:14px;font:600 22px/1.3 -apple-system,system-ui,sans-serif;'
        + 'box-shadow:0 8px 30px rgba(0,0,0,.45);border:1px solid rgba(255,255,255,.14);'
        + 'max-width:1100px;text-align:center;transition:opacity .35s;opacity:0;pointer-events:none';
      document.body.appendChild(el);
    }
    el.style.opacity = '0';
    setTimeout(() => { el.textContent = t; el.style.opacity = t ? '1' : '0'; }, t ? 200 : 0);
  }, text);
  if (hold) await ctx.pause(hold);
}

// Soft highlight ring around an element (selector or {x,y,w,h} box).
async function highlight(ctx, target, { pad = 6 } = {}) {
  await ctx.eval((tg, p) => {
    let box = tg;
    if (typeof tg === 'string') {
      const el = [...document.querySelectorAll(tg)].find(e => e.offsetParent);
      if (!el) return;
      const r = el.getBoundingClientRect();
      box = { x: r.left, y: r.top, w: r.width, h: r.height };
    }
    let h = document.getElementById('__filmHi');
    if (!h) {
      h = document.createElement('div');
      h.id = '__filmHi';
      h.style.cssText = 'position:fixed;z-index:2147483645;border:3px solid #ffd54a;'
        + 'border-radius:10px;box-shadow:0 0 0 9999px rgba(0,0,0,.28),0 0 18px #ffd54a;'
        + 'pointer-events:none;transition:all .45s ease';
      document.body.appendChild(h);
    }
    h.style.display = 'block';
    Object.assign(h.style, { left: (box.x - p) + 'px', top: (box.y - p) + 'px',
      width: (box.w + 2 * p) + 'px', height: (box.h + 2 * p) + 'px' });
  }, target, pad);
}

async function unhighlight(ctx) {
  await ctx.eval(() => { const h = document.getElementById('__filmHi'); if (h) h.style.display = 'none'; });
}

// Box of the visible element whose own text includes `text` (first match).
async function boxOf(page, text, { sel = 'div,span,a,li,button', exact = false } = {}) {
  return page.evaluate((t, s, ex) => {
    const el = [...document.querySelectorAll(s)].filter(e => e.offsetParent && e.children.length < 4
      && (ex ? e.textContent.trim() === t : e.textContent.trim().includes(t)))
      .sort((a, b) => a.textContent.length - b.textContent.length)[0];
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height,
             cx: r.left + Math.min(r.width / 2, 160), cy: r.top + r.height / 2 };
  }, text, sel, exact);
}

// Move to and click the element with that text; returns false if missing.
async function clickText(ctx, text, opts = {}) {
  const b = await boxOf(ctx.page, text, opts);
  if (!b) { console.log('[film] missing:', text); return false; }
  await ctx.click({ x: b.cx, y: b.cy }, { duration: opts.duration || 1100 });
  return true;
}

module.exports = { prep, ptOf, caption, highlight, unhighlight, boxOf, clickText };
