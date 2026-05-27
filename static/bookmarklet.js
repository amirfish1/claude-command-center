/* CCC annotation bookmarklet — injected into any page via loader bookmark.
 * Lets you point at any element, captures DOM context, copies a Claude-ready
 * annotation block to the clipboard. Re-runnable; second invocation closes
 * an active overlay.
 */
(function () {
  'use strict';

  if (window.__cccBookmarkletActive) {
    window.__cccBookmarkletCancel && window.__cccBookmarkletCancel();
    return;
  }
  window.__cccBookmarkletActive = true;

  var STYLE_ID = 'ccc-bookmarklet-style';
  if (!document.getElementById(STYLE_ID)) {
    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent =
      '.ccc-bm-overlay{position:fixed;inset:0;z-index:2147483646;cursor:crosshair;background:rgba(15,23,42,0.08);}' +
      '.ccc-bm-hud{position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:2147483647;background:#0f172a;color:#f8fafc;font:600 13px -apple-system,system-ui,sans-serif;padding:8px 14px;border-radius:999px;box-shadow:0 8px 24px rgba(0,0,0,0.25);display:flex;gap:12px;align-items:center;}' +
      '.ccc-bm-hud button{background:transparent;color:#f8fafc;border:1px solid rgba(248,250,252,0.4);border-radius:6px;font:500 12px inherit;padding:3px 8px;cursor:pointer;}' +
      '.ccc-bm-hud button:hover{background:rgba(248,250,252,0.12);}' +
      '.ccc-bm-hover{position:fixed;pointer-events:none;z-index:2147483645;border:2px solid #38bdf8;background:rgba(56,189,248,0.12);border-radius:4px;transition:all 60ms ease;}' +
      '.ccc-bm-label{position:fixed;pointer-events:none;z-index:2147483647;background:#0f172a;color:#f8fafc;font:500 11px -apple-system,system-ui,sans-serif;padding:3px 6px;border-radius:4px;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}' +
      '.ccc-bm-toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:2147483647;background:#16a34a;color:#fff;font:600 13px -apple-system,system-ui,sans-serif;padding:10px 16px;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.25);animation:ccc-bm-fade 2.4s ease forwards;}' +
      '.ccc-bm-toast.err{background:#dc2626;}' +
      '@keyframes ccc-bm-fade{0%{opacity:0;transform:translate(-50%,8px);}10%{opacity:1;transform:translate(-50%,0);}80%{opacity:1;}100%{opacity:0;transform:translate(-50%,-8px);}}';
    document.head.appendChild(style);
  }

  var overlay = document.createElement('div');
  overlay.className = 'ccc-bm-overlay';
  var hover = document.createElement('div');
  hover.className = 'ccc-bm-hover';
  hover.style.display = 'none';
  var label = document.createElement('div');
  label.className = 'ccc-bm-label';
  label.style.display = 'none';
  var hud = document.createElement('div');
  hud.className = 'ccc-bm-hud';
  hud.innerHTML =
    '<span>Click any element. Esc cancels.</span>' +
    '<button type="button" data-bm-cancel>Cancel</button>';

  document.body.appendChild(overlay);
  document.body.appendChild(hover);
  document.body.appendChild(label);
  document.body.appendChild(hud);

  function cleanup() {
    window.__cccBookmarkletActive = false;
    window.__cccBookmarkletCancel = null;
    overlay.remove();
    hover.remove();
    label.remove();
    hud.remove();
    document.removeEventListener('keydown', onKey, true);
  }
  window.__cccBookmarkletCancel = cleanup;

  function onKey(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      cleanup();
    }
  }
  document.addEventListener('keydown', onKey, true);
  hud.querySelector('[data-bm-cancel]').addEventListener('click', cleanup);

  function targetAt(x, y) {
    overlay.style.pointerEvents = 'none';
    hover.style.pointerEvents = 'none';
    label.style.pointerEvents = 'none';
    var el = document.elementFromPoint(x, y);
    overlay.style.pointerEvents = '';
    if (!el || el === overlay || el === hover || el === label) return null;
    if (el.closest && el.closest('.ccc-bm-hud')) return null;
    return el;
  }

  function rectOf(el) {
    var r = el.getBoundingClientRect();
    return { x: r.left, y: r.top, width: r.width, height: r.height };
  }

  function setHover(el) {
    if (!el) {
      hover.style.display = 'none';
      label.style.display = 'none';
      return;
    }
    var r = rectOf(el);
    hover.style.display = 'block';
    hover.style.left = r.x + 'px';
    hover.style.top = r.y + 'px';
    hover.style.width = r.width + 'px';
    hover.style.height = r.height + 'px';
    label.style.display = 'block';
    label.textContent = summarize(el);
    var lx = r.x;
    var ly = r.y - 22;
    if (ly < 4) ly = r.y + r.height + 4;
    if (lx + 280 > window.innerWidth) lx = Math.max(4, window.innerWidth - 284);
    label.style.left = lx + 'px';
    label.style.top = ly + 'px';
  }

  overlay.addEventListener('pointermove', function (e) {
    var el = targetAt(e.clientX, e.clientY);
    setHover(el);
  });

  overlay.addEventListener('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    var el = targetAt(e.clientX, e.clientY);
    if (!el) return;
    capture(el);
  });

  function summarize(el) {
    var tag = el.tagName.toLowerCase();
    var id = el.id ? '#' + el.id : '';
    var cls = el.className && typeof el.className === 'string'
      ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.')
      : '';
    var text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    return tag + id + cls + (text ? ' — "' + text + '"' : '');
  }

  function buildSelector(el) {
    if (!el) return '';
    if (el.id) return '#' + CSS.escape(el.id);
    var parts = [];
    var node = el;
    var depth = 0;
    while (node && node.nodeType === 1 && node !== document.body && depth < 6) {
      var seg = node.tagName.toLowerCase();
      if (node.id) {
        parts.unshift('#' + CSS.escape(node.id));
        break;
      }
      var classList = node.className && typeof node.className === 'string'
        ? node.className.trim().split(/\s+/).filter(Boolean).slice(0, 2)
        : [];
      if (classList.length) {
        seg += '.' + classList.map(function (c) { return CSS.escape(c); }).join('.');
      } else if (node.parentNode) {
        var idx = 1;
        var sib = node.previousElementSibling;
        while (sib) {
          if (sib.tagName === node.tagName) idx++;
          sib = sib.previousElementSibling;
        }
        seg += ':nth-of-type(' + idx + ')';
      }
      parts.unshift(seg);
      node = node.parentNode;
      depth++;
    }
    return parts.join(' > ');
  }

  function showToast(msg, isErr) {
    var t = document.createElement('div');
    t.className = 'ccc-bm-toast' + (isErr ? ' err' : '');
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2600);
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy') ? resolve() : reject(new Error('copy failed'));
      } catch (err) {
        reject(err);
      }
      ta.remove();
    });
  }

  function capture(el) {
    var note = window.prompt('Annotation note (what should we fix?):', '');
    if (note === null) return;
    var sel = buildSelector(el);
    var lines = [
      'Annotation: ' + (note || '(no note)').slice(0, 500),
      '',
      'Anchors:',
      '- URL: ' + window.location.href.slice(0, 240),
      '- Selector: ' + sel.slice(0, 200),
      '- Element: ' + summarize(el).slice(0, 160),
    ];
    var text = lines.join('\n');
    copyText(text).then(function () {
      showToast('Annotation copied. Paste into CCC chat.');
      cleanup();
    }).catch(function (err) {
      showToast('Copy failed: ' + (err && err.message || 'unknown'), true);
    });
  }
})();
