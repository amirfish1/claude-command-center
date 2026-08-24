/* Native element annotation for the standalone Q2 queue board. */
(function () {
  'use strict';

  function esc(value) {
    return String(value || '').replace(/[&<>"']/g, function (ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function selectorFor(el) {
    if (!el || !el.tagName) return '';
    if (el.id) return '#' + CSS.escape(el.id);
    var parts = [];
    var cur = el;
    while (cur && cur !== document.body && parts.length < 7) {
      var part = cur.tagName.toLowerCase();
      if (cur.classList && cur.classList.length) part += '.' + Array.from(cur.classList).slice(0, 2).map(CSS.escape).join('.');
      var parent = cur.parentElement;
      if (parent) {
        var peers = Array.from(parent.children).filter(function (child) { return child.tagName === cur.tagName; });
        if (peers.length > 1) part += ':nth-of-type(' + (peers.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      cur = parent;
    }
    return parts.join(' > ') || el.tagName.toLowerCase();
  }

  function elementInfo(el) {
    var rect = el.getBoundingClientRect();
    return {
      tag: el.tagName.toLowerCase(), id: el.id || '', role: el.getAttribute('role') || '',
      selector: selectorFor(el), text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 200),
      rect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height }
    };
  }

  function buildFixesQueueText(note, url, selector, elementText) {
    var anchors = ['- URL: ' + url, '- Selector: ' + selector];
    if (elementText) anchors.push('- Element: ' + elementText);
    return 'Fix the following UX issue based on this annotation:\n\n'
      + 'Annotation: ' + note + '\n\n'
      + 'Anchors:\n' + anchors.join('\n');
  }

  function addStyle() {
    if (document.getElementById('q2AnnotationStyle')) return;
    var style = document.createElement('style');
    style.id = 'q2AnnotationStyle';
    style.textContent = '.q2-ann-overlay{position:fixed;inset:0;z-index:10000;cursor:crosshair}.q2-ann-selection{position:fixed;border:2px solid #58a6ff;background:rgba(88,166,255,.12);pointer-events:none}.q2-ann-label{position:fixed;max-width:280px;padding:4px 7px;border-radius:4px;background:#58a6ff;color:#07111f;font:11px ui-monospace,monospace;pointer-events:none}.q2-ann-editor{position:fixed;z-index:10001;width:min(340px,calc(100vw - 24px));padding:12px;border:1px solid #30363d;border-radius:8px;background:#161b22;color:#e6edf3;box-shadow:0 8px 28px rgba(0,0,0,.55)}.q2-ann-editor textarea{box-sizing:border-box;width:100%;min-height:82px;margin:8px 0;background:#0d1117;color:inherit;border:1px solid #30363d;border-radius:4px;padding:7px}.q2-ann-actions{display:flex;gap:8px;justify-content:flex-end}.q2-ann-error{color:#ff7b72;font-size:12px}';
    document.head.appendChild(style);
  }

  window.Q2Annotation = {
    attach: function (button, options) {
      if (!button || button.dataset.q2AnnotationAttached) return;
      button.dataset.q2AnnotationAttached = 'true';
      addStyle();
      var state = null;

      function stop() {
        if (!state) return;
        document.removeEventListener('keydown', onKey, true);
        state.overlay.remove();
        if (state.editor) state.editor.remove();
        button.classList.remove('is-armed');
        state = null;
      }

      function targetAtPoint(x, y) {
        state.overlay.style.pointerEvents = 'none';
        try { return document.elementFromPoint(x, y); }
        finally { state.overlay.style.pointerEvents = 'auto'; }
      }

      function place(info) {
        state.selection.style.left = info.rect.x + 'px';
        state.selection.style.top = info.rect.y + 'px';
        state.selection.style.width = info.rect.width + 'px';
        state.selection.style.height = info.rect.height + 'px';
        state.label.textContent = info.selector;
        state.label.style.left = Math.max(8, Math.min(info.rect.x, window.innerWidth - 288)) + 'px';
        state.label.style.top = Math.max(8, info.rect.y - 28) + 'px';
      }

      function showEditor(info) {
        var editor = document.createElement('div');
        editor.className = 'q2-ann-editor';
        editor.innerHTML = '<strong>Annotate selected element</strong><div class="q2-ann-selected">' + esc(info.selector) + '</div><textarea placeholder="Describe the issue…"></textarea><div class="q2-ann-error" hidden></div><div class="q2-ann-actions"><button type="button" data-action="cancel">Cancel</button><button type="button" data-action="save">Save annotation</button></div>';
        editor.style.left = Math.max(12, Math.min(info.rect.x, window.innerWidth - 352)) + 'px';
        editor.style.top = Math.max(12, Math.min(info.rect.y + info.rect.height + 10, window.innerHeight - 230)) + 'px';
        document.body.appendChild(editor);
        state.editor = editor;
        var textarea = editor.querySelector('textarea');
        textarea.focus();
        editor.querySelector('[data-action="cancel"]').addEventListener('click', stop);
        editor.querySelector('[data-action="save"]').addEventListener('click', async function () {
          var note = textarea.value.trim();
          var error = editor.querySelector('.q2-ann-error');
          if (!note) { error.textContent = 'Describe the issue before saving.'; error.hidden = false; return; }
          var save = editor.querySelector('[data-action="save"]');
          save.disabled = true;
          var payload = { note: note, url: location.href, title: document.title, source: 'ccc', rect: info.rect, viewport_crop: info.rect, element: { tag: info.tag, id: info.id, role: info.role, selector: info.selector, text: info.text }, capture_screen: true };
          var noteEl = document.getElementById('q2Note');
          function showNote(text) {
            if (!noteEl) return;
            noteEl.textContent = text; noteEl.hidden = false;
            setTimeout(function () { noteEl.hidden = true; }, 3000);
          }
          try {
            var response = await fetch('/api/annotations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            if (!response.ok) throw new Error('Could not save annotation');
            var data = await response.json().catch(function () { return {}; });
            var saved = (data && data.annotation) || {};
            stop();
            try {
              var queueResponse = await fetch('/api/annotations/ux-fixes-queue', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  annotation_id: saved.id || '',
                  text: buildFixesQueueText(note, payload.url, info.selector, info.text),
                  note: note,
                  url: payload.url,
                  title: payload.title,
                  selector: info.selector,
                  screenshot_path: saved.screenshot_path || '',
                  source: 'ccc',
                }),
              });
              var queueData = await queueResponse.json().catch(function () { return {}; });
              if (!queueResponse.ok || !queueData.ok) throw new Error((queueData && queueData.error) || 'Could not file ticket');
              showNote('Filed as ' + ((queueData.item && queueData.item.ref) || 'ticket'));
              if (typeof window.q2Refresh === 'function') window.q2Refresh();
            } catch (queueErr) {
              showNote('Annotation saved, but could not file a ticket: ' + ((queueErr && queueErr.message) || 'unknown error'));
            }
          } catch (err) { error.textContent = err.message || 'Could not save annotation.'; error.hidden = false; save.disabled = false; }
        });
      }

      function onKey(event) { if (event.key === 'Escape') stop(); }
      button.addEventListener('click', function () {
        if (state) { stop(); return; }
        var overlay = document.createElement('div');
        overlay.className = 'q2-ann-overlay';
        var selection = document.createElement('div'); selection.className = 'q2-ann-selection';
        var label = document.createElement('div'); label.className = 'q2-ann-label';
        overlay.appendChild(selection); overlay.appendChild(label); document.body.appendChild(overlay);
        state = { overlay: overlay, selection: selection, label: label, editor: null };
        button.classList.add('is-armed');
        document.addEventListener('keydown', onKey, true);
        overlay.addEventListener('pointermove', function (event) { if (!state.editor) place(elementInfo(targetAtPoint(event.clientX, event.clientY))); });
        overlay.addEventListener('pointerdown', function (event) { event.preventDefault(); event.stopPropagation(); if (!state.editor) { var info = elementInfo(targetAtPoint(event.clientX, event.clientY)); place(info); showEditor(info); } });
      });
    }
  };
})();
