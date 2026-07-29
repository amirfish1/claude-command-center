/* WatchTower Annotate Widget — copy this into your dev build only.
   Config: window.WT_ANNOTATE = { queue: 'MYAPP', endpoint: 'http://127.0.0.1:8787' }
   must be set before this script runs (or the defaults below are used).
   IMPORTANT: guard this behind a dev-only check
   (e.g. if (process.env.NODE_ENV !== 'production') { ... })
*/
(function () {
  var cfg = window.WT_ANNOTATE || {};
  var QUEUE = cfg.queue || 'DEFAULT';
  var ENDPOINT = (cfg.endpoint || 'http://127.0.0.1:8787').replace(/\/$/, '');

  // ---- floating button
  var btn = document.createElement('button');
  btn.textContent = '⚑';
  btn.title = 'WatchTower: annotate element';
  btn.setAttribute('style', [
    'position:fixed', 'bottom:20px', 'right:20px', 'z-index:999999',
    'width:40px', 'height:40px', 'border-radius:50%', 'border:none',
    'background:#1e3a5f', 'color:#fff', 'font-size:20px', 'cursor:pointer',
    'box-shadow:0 2px 8px rgba(0,0,0,.4)', 'user-select:none',
    'display:flex', 'align-items:center', 'justify-content:center',
  ].join(';'));

  // draggable
  var dragging = false, ox = 0, oy = 0;
  btn.addEventListener('mousedown', function (e) {
    if (e.button !== 0) return;
    dragging = true; ox = e.clientX - btn.getBoundingClientRect().left;
    oy = e.clientY - btn.getBoundingClientRect().top;
    e.preventDefault();
  });
  document.addEventListener('mousemove', function (e) {
    if (!dragging) return;
    btn.style.left = (e.clientX - ox) + 'px';
    btn.style.top  = (e.clientY - oy) + 'px';
    btn.style.right = 'auto'; btn.style.bottom = 'auto';
  });
  document.addEventListener('mouseup', function () { dragging = false; });

  document.body.appendChild(btn);

  // ---- pick mode
  var pickMode = false;
  var pickedEl = null;

  function enterPick() {
    pickMode = true;
    document.body.style.cursor = 'crosshair';
    document.addEventListener('click', onPick, true);
    document.addEventListener('keydown', onEsc, true);
  }

  function exitPick() {
    pickMode = false;
    document.body.style.cursor = '';
    document.removeEventListener('click', onPick, true);
    document.removeEventListener('keydown', onEsc, true);
  }

  function onEsc(e) {
    if (e.key === 'Escape') { exitPick(); closeModal(); }
  }

  function onPick(e) {
    if (e.target === btn) return;
    e.preventDefault(); e.stopPropagation();
    exitPick();
    pickedEl = e.target;
    showModal(captureElement(pickedEl));
  }

  btn.addEventListener('click', function (e) {
    if (dragging) return;
    e.stopPropagation();
    enterPick();
  });

  // ---- element capture
  function bestSelector(el) {
    if (el.id) return '#' + el.id;
    var cls = Array.from(el.classList).slice(0, 2).join('.');
    if (cls) return el.tagName.toLowerCase() + '.' + cls;
    var parent = el.parentElement;
    if (parent) {
      var idx = Array.from(parent.children).indexOf(el) + 1;
      return el.tagName.toLowerCase() + ':nth-child(' + idx + ')';
    }
    return el.tagName.toLowerCase();
  }

  function captureElement(el) {
    var r = el.getBoundingClientRect();
    return {
      selector: bestSelector(el),
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      role: el.getAttribute('role') || '',
      textContent: (el.textContent || '').trim().slice(0, 80),
      boundingClientRect: { top: r.top, left: r.left, width: r.width, height: r.height },
      scrollX: window.scrollX, scrollY: window.scrollY,
      viewportWidth: window.innerWidth, viewportHeight: window.innerHeight,
      pageUrl: location.href, pageTitle: document.title,
    };
  }

  // ---- modal
  var modal = null;

  function showModal(info) {
    closeModal();
    modal = document.createElement('div');
    modal.setAttribute('style', [
      'position:fixed', 'top:50%', 'left:50%', 'transform:translate(-50%,-50%)',
      'z-index:1000000', 'background:#0c121e', 'color:#eaf1fb',
      'border:1px solid #25324a', 'border-radius:8px', 'padding:20px',
      'width:360px', 'box-shadow:0 8px 32px rgba(0,0,0,.6)',
      'font-family:system-ui,sans-serif', 'font-size:14px',
    ].join(';'));

    var ctx = document.createElement('div');
    ctx.setAttribute('style', 'margin-bottom:12px;color:#7e90ae;font-size:12px;');
    ctx.textContent = '<' + info.tag + '>' +
      (info.textContent ? ' — ' + info.textContent : '');
    modal.appendChild(ctx);

    var ta = document.createElement('textarea');
    ta.placeholder = 'Describe the issue…';
    ta.setAttribute('style', [
      'width:100%', 'height:80px', 'background:#141d2c', 'color:#eaf1fb',
      'border:1px solid #25324a', 'border-radius:4px', 'padding:8px',
      'font-size:13px', 'resize:vertical', 'box-sizing:border-box',
    ].join(';'));
    modal.appendChild(ta);

    var row = document.createElement('div');
    row.setAttribute('style', 'display:flex;gap:8px;margin-top:12px;justify-content:flex-end;');

    var cancel = document.createElement('button');
    cancel.textContent = 'Cancel';
    cancel.setAttribute('style', btnStyle('#25324a', '#eaf1fb'));
    cancel.addEventListener('click', closeModal);

    var submit = document.createElement('button');
    submit.textContent = 'Submit';
    submit.setAttribute('style', btnStyle('#1e3a5f', '#fff'));
    submit.addEventListener('click', function () { doSubmit(info, ta.value); });

    row.appendChild(cancel); row.appendChild(submit);
    modal.appendChild(row);

    document.addEventListener('keydown', onEsc, true);
    document.body.appendChild(modal);
    ta.focus();
  }

  function btnStyle(bg, color) {
    return 'background:' + bg + ';color:' + color + ';border:none;border-radius:4px;' +
      'padding:6px 14px;cursor:pointer;font-size:13px;';
  }

  function closeModal() {
    if (modal && modal.parentNode) modal.parentNode.removeChild(modal);
    modal = null;
    document.removeEventListener('keydown', onEsc, true);
  }

  // ---- submit
  function doSubmit(info, note) {
    note = (note || '').trim();
    if (!note) { alert('Please enter a note.'); return; }
    closeModal();
    var payload = JSON.stringify({
      note: note,
      url: info.pageUrl,
      title: info.pageTitle,
      selector: info.selector,
      source: 'annotate-widget',
    });
    var xhr = new XMLHttpRequest();
    xhr.open('POST', ENDPOINT + '/api/queue/' + QUEUE + '/add');
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.onload = function () {
      try {
        var res = JSON.parse(xhr.responseText);
        if (res.ok) { toast('Queued as ' + res.ref); return; }
      } catch (e) {}
      toast('Failed — copy note: ' + note, true);
    };
    xhr.onerror = function () { toast('Failed — copy note: ' + note, true); };
    xhr.send(payload);
  }

  // ---- toast
  function toast(msg, isErr) {
    var t = document.createElement('div');
    t.textContent = msg;
    t.setAttribute('style', [
      'position:fixed', 'bottom:70px', 'right:20px', 'z-index:1000001',
      'background:' + (isErr ? '#7a1c1c' : '#1e3a5f'),
      'color:#eaf1fb', 'border-radius:6px', 'padding:10px 16px',
      'font-family:system-ui,sans-serif', 'font-size:13px',
      'box-shadow:0 2px 8px rgba(0,0,0,.4)', 'max-width:300px',
    ].join(';'));
    document.body.appendChild(t);
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 3000);
  }
})();
