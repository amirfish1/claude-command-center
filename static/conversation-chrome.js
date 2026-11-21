/* Shared conversation chrome with explicit live-state and host adapters. */
(function (root) {
  function createController({ state: sharedState, helpers: hostServices }) {
  function ffcInvalidate(convId) {
    if (convId) sharedState._ffcCache.delete(convId);
  }

  function _subagentClustersExpandedSet() {
    try { return new Set(JSON.parse(localStorage.getItem(sharedState.SUBAGENT_CLUSTERS_EXPANDED_KEY) || '[]')); }
    catch (_) { return new Set(); }
  }

  function _subagentClustersSaveExpandedSet(s) {
    try { localStorage.setItem(sharedState.SUBAGENT_CLUSTERS_EXPANDED_KEY, JSON.stringify([...s])); } catch (_) {}
  }

  function _subagentClustersCollapsedSet() {
    try { return new Set(JSON.parse(localStorage.getItem(sharedState.SUBAGENT_CLUSTERS_COLLAPSED_KEY) || '[]')); }
    catch (_) { return new Set(); }
  }

  function _subagentClustersSaveCollapsedSet(s) {
    try { localStorage.setItem(sharedState.SUBAGENT_CLUSTERS_COLLAPSED_KEY, JSON.stringify([...s])); } catch (_) {}
  }

  function rememberSidebarFamilyParents(tree) {
    let changed = false;
    const seen = new Set();
    const walk = (node, parent) => {
      const sid = String(node && node.session_id || '').trim();
      if (!sid || seen.has(sid)) return;
      seen.add(sid);
      if (parent && sharedState._sidebarFamilyParents.get(sid) !== parent) {
        sharedState._sidebarFamilyParents.set(sid, parent);
        changed = true;
      }
      (Array.isArray(node.children) ? node.children : []).forEach(child => walk(child, sid));
    };
    walk(tree, '');
    return changed;
  }

  async function copyTextValue(text) {
    const value = String(text || '');
    if (!value) return false;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (_) {}
    }
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) {}
    ta.remove();
    return ok;
  }

  function _annotationsForSession(sessionId) {
    try {
      const raw = JSON.parse(localStorage.getItem(sharedState.ANNOTATIONS_STORAGE_KEY) || '{}');
      return (raw && typeof raw === 'object' && Array.isArray(raw[sessionId])) ? raw[sessionId] : [];
    } catch (_) {
      return [];
    }
  }

  function _wrapRangeInAnnotationMark(range, id) {
    const mark = document.createElement('span');
    mark.className = 'ccc-annotation-mark';
    mark.dataset.annotationId = id;
    mark.appendChild(range.extractContents());
    range.insertNode(mark);
    return mark;
  }

  function _annotationBlockAnchor(node) {
    let cur = node;
    while (cur && cur.parentElement) {
      if (window.getComputedStyle(cur).display !== 'inline') return cur;
      cur = cur.parentElement;
    }
    return cur;
  }

  function _insertAnnotationNote(markEl, note, id, sessionId) {
    const anchor = _annotationBlockAnchor(markEl);
    const noteEl = document.createElement('div');
    noteEl.className = 'ccc-annotation-note';
    noteEl.dataset.annotationId = id;
    noteEl.dataset.sessionId = sessionId;
    const textEl = document.createElement('span');
    textEl.className = 'ccc-annotation-note-text';
    textEl.textContent = note;
    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'ccc-annotation-note-delete';
    delBtn.setAttribute('aria-label', 'Delete annotation');
    delBtn.textContent = '×';
    noteEl.appendChild(textEl);
    noteEl.appendChild(delBtn);
    anchor.parentNode.insertBefore(noteEl, anchor.nextSibling);
    return noteEl;
  }

  function _findTextRangeInElement(roots, needle) {
    const clean = needle ? needle.replace(/…$/, '') : '';
    if (!clean) return null;
    const list = Array.isArray(roots) ? roots : [roots];
    for (const root of list) {
      if (!root) continue;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
      let node;
      while ((node = walker.nextNode())) {
        const idx = node.nodeValue.indexOf(clean);
        if (idx !== -1) {
          const range = document.createRange();
          range.setStart(node, idx);
          range.setEnd(node, idx + clean.length);
          return range;
        }
      }
    }
    return null;
  }

  function _annotationSearchRoots(eventEl) {
    if (!eventEl.classList.contains('kimi-marker')) return eventEl;
    const roots = [];
    let sib = eventEl.nextElementSibling;
    while (sib && !(sib.classList && sib.classList.contains('event'))) {
      roots.push(sib);
      sib = sib.nextElementSibling;
    }
    return roots;
  }

  function _reconcileAnnotationsForPane(paneId) {
    const pane = (0, sharedState.paneByPaneId)(paneId);
    if (!pane) return;
    const sessionId = sharedState.sessionIdByConv[pane.conversationId] || pane.conversationId;
    if (!sessionId) return;
    const list = _annotationsForSession(sessionId);
    if (!list.length) return;
    const $view = (0, sharedState.getConvViewForPane)(paneId);
    if (!$view) return;
    for (const ann of list) {
      const escId = (window.CSS && CSS.escape) ? CSS.escape(ann.id) : ann.id;
      if ($view.querySelector('.ccc-annotation-mark[data-annotation-id="' + escId + '"]')) continue;
      const escLine = (window.CSS && CSS.escape) ? CSS.escape(String(ann.jsonlLine)) : String(ann.jsonlLine);
      const eventEl = $view.querySelector('.event[data-jsonl-line="' + escLine + '"]');
      if (!eventEl) continue;
      const found = _findTextRangeInElement(_annotationSearchRoots(eventEl), ann.excerpt);
      if (!found) continue;
      const markEl = _wrapRangeInAnnotationMark(found, ann.id);
      _insertAnnotationNote(markEl, ann.note, ann.id, sessionId);
    }
  }

  function setMediaLightboxZoom(scale) {
    if (!sharedState._imgLightbox || !sharedState._imgLightbox.classList.contains('open')) return;
    const stage = sharedState._imgLightbox.querySelector('.img-lightbox-stage');
    const img = stage.querySelector('img');
    if (!img || !img.naturalWidth) return;
    const fit = Math.min(1, (stage.clientWidth - 24) / img.naturalWidth,
      (stage.clientHeight - 24) / img.naturalHeight);
    const next = scale === 0 ? fit : Math.max(fit, Math.min(8, scale));
    const centerX = stage.scrollWidth > stage.clientWidth ? (stage.scrollLeft + stage.clientWidth / 2) / stage.scrollWidth : 0.5;
    const centerY = stage.scrollHeight > stage.clientHeight ? (stage.scrollTop + stage.clientHeight / 2) / stage.scrollHeight : 0.5;
    sharedState._lightboxZoom = scale === 0 ? 0 : next;
    img.style.width = img.naturalWidth * next + 'px';
    img.style.height = img.naturalHeight * next + 'px';
    img.dataset.scale = String(next);
    sharedState._imgLightbox.querySelector('[data-image-zoom="actual"]').textContent = Math.round(next * 100) + '%';
    sharedState._imgLightbox.querySelector('[data-image-zoom="out"]').disabled = next <= fit + 0.001;
    sharedState._imgLightbox.querySelector('[data-image-zoom="in"]').disabled = next >= 8;
    stage.scrollLeft = scale === 0 ? 0 : centerX * stage.scrollWidth - stage.clientWidth / 2;
    stage.scrollTop = scale === 0 ? 0 : centerY * stage.scrollHeight - stage.clientHeight / 2;
  }

  function _ensureMediaLightbox() {
    if (sharedState._imgLightbox) return sharedState._imgLightbox;
    sharedState._imgLightbox = document.createElement('div');
    sharedState._imgLightbox.className = 'img-lightbox';
    sharedState._imgLightbox.innerHTML =
      '<div class="img-lightbox-bar">'
      + '<button type="button" class="img-lightbox-back" data-lightbox-close aria-label="Back to main view">\u2190 Back</button>'
      + '<span class="img-lightbox-title"></span>'
      + '<button type="button" class="img-lightbox-full" hidden title="Load the original image">Full size</button>'
      + '<a class="img-lightbox-download" download aria-label="Download file">Download</a>'
      + '<button type="button" class="img-lightbox-close" data-lightbox-close aria-label="Close">\u2715</button>'
      + '</div>'
      + '<div class="img-lightbox-zoom" role="group" aria-label="Image zoom">'
      + '<button type="button" data-image-zoom="out" aria-label="Zoom out">−</button>'
      + '<button type="button" data-image-zoom="actual" aria-label="Actual size (100%)" title="Actual size (100%)">100%</button>'
      + '<button type="button" data-image-zoom="in" aria-label="Zoom in">+</button>'
      + '<button type="button" data-image-zoom="fit">Fit</button>'
      + '</div>'
      + '<div class="img-lightbox-stage"><img alt=""></div>';
    sharedState._imgLightbox.addEventListener('click', (ev) => {
      const full = ev.target.closest('.img-lightbox-full');
      if (full) {
        const img = sharedState._imgLightbox.querySelector('.img-lightbox-stage img');
        const fullSrc = sharedState._imgLightbox.dataset.fullSrc || '';
        if (img && fullSrc) {
          full.disabled = true;
          full.textContent = 'Loading\u2026';
          img.addEventListener('load', () => { full.hidden = true; }, { once: true });
          img.addEventListener('error', () => { full.disabled = false; full.textContent = 'Full size'; }, { once: true });
          img.src = fullSrc;
        }
        return;
      }
      const zoom = ev.target.closest('[data-image-zoom]');
      if (zoom) {
        const action = zoom.dataset.imageZoom;
        const img = sharedState._imgLightbox.querySelector('.img-lightbox-stage img');
        const scale = Number(img && img.dataset.scale) || 1;
        setMediaLightboxZoom(action === 'fit' ? 0 : action === 'actual' ? 1 : scale * (action === 'in' ? 1.5 : 1 / 1.5));
        return;
      }
      if (ev.target.closest('[data-lightbox-close]')) {
        ev.preventDefault();
        closeMediaLightbox();
        return;
      }
      if (ev.target.closest('.img-lightbox-bar, .img-lightbox-zoom, .img-lightbox-stage.is-image, .img-lightbox-video, .img-lightbox-frame')) {
        return;
      }
      closeMediaLightbox();
    });
    document.body.appendChild(sharedState._imgLightbox);
    return sharedState._imgLightbox;
  }

  function closeMediaLightbox(opts) {
    if (!sharedState._imgLightbox || !sharedState._imgLightbox.classList.contains('open')) return;
    sharedState._imgLightbox.classList.remove('open');
    const vid = sharedState._imgLightbox.querySelector('video');
    if (vid) {
      try { vid.pause(); vid.removeAttribute('src'); vid.load(); } catch (_) {}
    }
    const frame = sharedState._imgLightbox.querySelector('iframe');
    if (frame) {
      try { frame.src = 'about:blank'; } catch (_) {}
    }
    if (sharedState._lightboxHistoryPushed) {
      if (!(opts && opts.fromPopState)) {
        // Keep ownership until the asynchronous popstate arrives, even when
        // the toolbar or Escape has already hidden the viewer.
        try { history.back(); } catch (_) { sharedState._lightboxHistoryPushed = false; }
      }
    }
  }

  function _mediaPreviewUrl(href) {
    try {
      const u = new URL(href, window.location.origin);
      if (u.pathname !== '/api/media') return '';
      u.searchParams.set('preview', '1');
      return u.href;
    } catch (_) { return ''; }
  }

  function openMediaLightbox(src, opts) {
    if (!src) return;
    if (!(opts && opts.forceModal)
        && typeof _canUseStatusRailViewer === 'function'
        && typeof openArtifactInStatusRail === 'function'
        && _canUseStatusRailViewer()) {
      openArtifactInStatusRail(src, opts);
      return;
    }
    const kind = (opts && opts.kind) || 'image';
    const title = (opts && opts.title) || '';
    const lb = _ensureMediaLightbox();
    const $title = lb.querySelector('.img-lightbox-title');
    const $dl = lb.querySelector('.img-lightbox-download');
    const $stage = lb.querySelector('.img-lightbox-stage');
    const isImage = kind !== 'video' && kind !== 'frame';
    const fullSrc = src;
    if (isImage && !(opts && opts.noPreview)) src = _mediaPreviewUrl(src) || src;
    const $full = lb.querySelector('.img-lightbox-full');
    lb.dataset.fullSrc = src !== fullSrc ? fullSrc : '';
    lb.dataset.origSrc = fullSrc;
    if ($full) {
      $full.hidden = src === fullSrc;
      $full.disabled = false;
      $full.textContent = 'Full size';
    }
    lb.querySelector('.img-lightbox-zoom').hidden = !isImage;
    $stage.classList.toggle('is-image', isImage);
    $stage.scrollTop = $stage.scrollLeft = 0;
    sharedState._lightboxZoom = 0;
    if ($title) $title.textContent = title;
    if ($dl) {
      $dl.href = fullSrc;
      if (title) $dl.setAttribute('download', title);
      else $dl.setAttribute('download', '');
    }
    if (kind === 'video') {
      $stage.innerHTML = '<video class="img-lightbox-video" controls autoplay playsinline></video>';
      $stage.querySelector('video').src = src;
    } else if (kind === 'frame') {
      $stage.innerHTML = '<iframe class="img-lightbox-frame" title="' + (0, sharedState.escapeAttr)(title || 'Preview') + '"></iframe>';
      $stage.querySelector('iframe').src = src;
    } else {
      $stage.innerHTML = '<img alt="' + (0, sharedState.escapeAttr)(title) + '">';
      const img = $stage.querySelector('img');
      img.addEventListener('load', () => {
        if (img === $stage.querySelector('img')) setMediaLightboxZoom(0);
      });
      img.src = src;
    }
    const wasOpen = lb.classList.contains('open');
    lb.classList.add('open');
    if (isImage) setMediaLightboxZoom(0);
    if (!wasOpen && !sharedState._lightboxHistoryPushed) {
      try {
        history.pushState({ ...(history.state || {}), cccMediaLightbox: true }, '');
        sharedState._lightboxHistoryPushed = true;
      } catch (_) {}
    }
  }

  function _isMarkdownPath(p) {
    const clean = String(p || '').split(/[?#]/)[0].replace(/:\d+(?::\d+)?$/, '');
    return /\.(?:md|mdx)$/i.test(clean);
  }

  function _isVideoPath(p) {
    const clean = String(p || '').split(/[?#]/)[0].replace(/:\d+(?::\d+)?$/, '');
    return /\.(?:mp4|mov|webm|avi|mkv|m4v)$/i.test(clean);
  }

  function _isImageOrPdfPath(p) {
    const clean = String(p || '').split(/[?#]/)[0].replace(/:\d+(?::\d+)?$/, '');
    return /\.(?:png|jpe?g|gif|webp|svg|pdf)$/i.test(clean);
  }

  function _isPdfPath(p) {
    const clean = String(p || '').split(/[?#]/)[0].replace(/:\d+(?::\d+)?$/, '');
    return /\.pdf$/i.test(clean);
  }

  function _isHtmlDocPath(p) {
    const clean = String(p || '').split(/[?#]/)[0].replace(/:\d+(?::\d+)?$/, '');
    return /\.(?:html|htm)$/i.test(clean);
  }

  function _canUseStatusRailViewer() {
    const rail = document.getElementById('statusRail');
    const viewer = document.getElementById('statusRailFileViewer');
    const bodyEl = document.getElementById('fileViewerBody');
    const body = document.body;
    if (!rail || !viewer || !bodyEl || !body) return false;
    if (!body.classList.contains('status-pos-right')) return false;
    if (body.classList.contains('ccc-app-embed')) return false;
    const forced = body.classList.contains('popout-rail-forced');
    if (!forced && window.innerWidth <= 1200) return false;
    if (!forced && typeof sharedState.isMobile === 'function' && (0, sharedState.isMobile)()) return false;
    const pane = rail.closest('.conv-pane');
    if (pane) {
      const rect = pane.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return false;
    }
    return true;
  }

  function _cleanupFileViewerMedia(bodyEl) {
    if (!bodyEl) return;
    if (bodyEl._fvResizeObserver) {
      try { bodyEl._fvResizeObserver.disconnect(); } catch (_) {}
      bodyEl._fvResizeObserver = null;
    }
    const vid = bodyEl.querySelector('video');
    if (vid) {
      try { vid.pause(); vid.removeAttribute('src'); vid.load(); } catch (_) {}
    }
    const frame = bodyEl.querySelector('iframe');
    if (frame) {
      try { frame.src = 'about:blank'; } catch (_) {}
    }
  }

  function _setFileViewerImageZoom(bodyEl, scale) {
    if (!bodyEl) return;
    const stage = bodyEl.querySelector('.file-viewer-media-stage.is-image');
    const img = stage && stage.querySelector('img');
    if (!stage || !img || !img.naturalWidth) return;
    const availW = Math.max(80, stage.clientWidth - 20);
    const availH = Math.max(80, stage.clientHeight - 20);
    const fit = Math.min(1, availW / img.naturalWidth, availH / img.naturalHeight);
    const next = scale === 0 ? fit : Math.max(fit, Math.min(8, scale));
    const centerX = stage.scrollWidth > stage.clientWidth ? (stage.scrollLeft + stage.clientWidth / 2) / stage.scrollWidth : 0.5;
    const centerY = stage.scrollHeight > stage.clientHeight ? (stage.scrollTop + stage.clientHeight / 2) / stage.scrollHeight : 0.5;
    stage.dataset.fvZoom = scale === 0 ? '0' : String(next);
    img.style.width = Math.round(img.naturalWidth * next) + 'px';
    img.style.height = Math.round(img.naturalHeight * next) + 'px';
    img.dataset.scale = String(next);
    const actualBtn = bodyEl.querySelector('[data-fv-zoom="actual"]');
    const outBtn = bodyEl.querySelector('[data-fv-zoom="out"]');
    const inBtn = bodyEl.querySelector('[data-fv-zoom="in"]');
    if (actualBtn) actualBtn.textContent = Math.round(next * 100) + '%';
    if (outBtn) outBtn.disabled = next <= fit + 0.001;
    if (inBtn) inBtn.disabled = next >= 8;
    stage.scrollLeft = scale === 0 ? 0 : Math.max(0, centerX * stage.scrollWidth - stage.clientWidth / 2);
    stage.scrollTop = scale === 0 ? 0 : Math.max(0, centerY * stage.scrollHeight - stage.clientHeight / 2);
  }

  function openArtifactInStatusRail(src, opts) {
    if (!src) return false;
    const rail = document.getElementById('statusRail');
    const viewer = document.getElementById('statusRailFileViewer');
    const bodyEl = document.getElementById('fileViewerBody');
    if (!rail || !viewer || !bodyEl) return false;

    const kind = (opts && opts.kind) || 'image';
    const title = (opts && opts.title) || '';
    const filePath = (opts && opts.filePath) || '';
    const noPreview = Boolean(opts && opts.noPreview);
    const isImage = kind !== 'video' && kind !== 'frame';
    const fullSrc = src;
    const displaySrc = (isImage && !noPreview) ? (_mediaPreviewUrl(src) || src) : src;

    _cleanupFileViewerMedia(bodyEl);

    const titleEl = document.getElementById('fileViewerTitle');
    const filenameEl = document.getElementById('fileViewerFilename');
    const dlBtn = document.getElementById('fileViewerDownloadBtn');
    const fsBtn = document.getElementById('fileViewerFullscreenBtn');
    const extBtn = document.getElementById('fileViewerOpenExternalBtn');

    if (titleEl) {
      if (kind === 'video') titleEl.textContent = 'Video Preview';
      else if (kind === 'frame') titleEl.textContent = _isPdfPath(filePath || title) ? 'PDF Preview' : 'Artifact Preview';
      else titleEl.textContent = 'Image Preview';
    }
    if (filenameEl) filenameEl.textContent = title || filePath.split('/').pop() || 'Artifact';

    viewer.setAttribute('data-viewer-kind', kind);
    viewer.setAttribute('data-current-src', fullSrc);
    viewer.setAttribute('data-viewer-no-preview', noPreview ? '1' : '');
    if (filePath) viewer.setAttribute('data-current-path', filePath);
    else viewer.removeAttribute('data-current-path');

    if (dlBtn) {
      dlBtn.href = fullSrc;
      dlBtn.setAttribute('download', title || '');
      dlBtn.hidden = false;
    }
    if (fsBtn) fsBtn.hidden = false;
    if (extBtn) extBtn.hidden = !filePath;

    bodyEl.className = 'file-viewer-body is-media-viewer is-media-' + kind;

    if (kind === 'video') {
      bodyEl.innerHTML = '<div class="file-viewer-media-stage is-video"><video class="file-viewer-video" controls autoplay playsinline></video></div>';
      const vid = bodyEl.querySelector('video');
      if (vid) vid.src = displaySrc;
    } else if (kind === 'frame') {
      bodyEl.innerHTML = '<iframe class="file-viewer-frame" title="' + (0, sharedState.escapeAttr)(title || 'Artifact preview') + '"></iframe>';
      const frame = bodyEl.querySelector('iframe');
      if (frame) frame.src = displaySrc;
    } else {
      const hasFullUpgrade = displaySrc !== fullSrc;
      bodyEl.innerHTML =
        '<div class="file-viewer-media-bar" role="group" aria-label="Image zoom">'
        + '<button type="button" class="file-viewer-zoom-btn" data-fv-zoom="out" aria-label="Zoom out">\u2212</button>'
        + '<button type="button" class="file-viewer-zoom-btn" data-fv-zoom="actual" aria-label="Actual size (100%)" title="Actual size (100%)">100%</button>'
        + '<button type="button" class="file-viewer-zoom-btn" data-fv-zoom="in" aria-label="Zoom in">+</button>'
        + '<button type="button" class="file-viewer-zoom-btn" data-fv-zoom="fit">Fit</button>'
        + '<button type="button" class="file-viewer-zoom-btn file-viewer-full-btn"' + (hasFullUpgrade ? '' : ' hidden') + ' title="Load the original image">Full size</button>'
        + '</div>'
        + '<div class="file-viewer-media-stage is-image"><img alt="' + (0, sharedState.escapeAttr)(title || 'Image preview') + '"></div>';
      const stage = bodyEl.querySelector('.file-viewer-media-stage');
      if (stage) stage.dataset.fvZoom = '0';
      const img = stage && stage.querySelector('img');
      const fullBtn = bodyEl.querySelector('.file-viewer-full-btn');
      if (img) {
        img.addEventListener('load', () => {
          if (img.isConnected) _setFileViewerImageZoom(bodyEl, 0);
        });
        img.src = displaySrc;
      }
      if (stage && typeof ResizeObserver === 'function') {
        const ro = new ResizeObserver(() => {
          if (stage.isConnected && stage.dataset.fvZoom === '0') _setFileViewerImageZoom(bodyEl, 0);
        });
        ro.observe(stage);
        bodyEl._fvResizeObserver = ro;
      }
      if (fullBtn && img) {
        fullBtn.addEventListener('click', () => {
          fullBtn.disabled = true;
          fullBtn.textContent = 'Loading\u2026';
          img.addEventListener('load', () => {
            fullBtn.hidden = true;
            viewer.setAttribute('data-viewer-no-preview', '1');
            _setFileViewerImageZoom(bodyEl, 0);
          }, { once: true });
          img.addEventListener('error', () => { fullBtn.disabled = false; fullBtn.textContent = 'Full size'; }, { once: true });
          img.src = fullSrc;
        });
      }
      bodyEl.querySelectorAll('[data-fv-zoom]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const action = btn.getAttribute('data-fv-zoom');
          const scale = Number(img && img.dataset.scale) || 1;
          _setFileViewerImageZoom(bodyEl, action === 'fit' ? 0 : action === 'actual' ? 1 : scale * (action === 'in' ? 1.5 : 1 / 1.5));
        });
      });
    }

    if (typeof window._cccExpandStatusRailForFileViewer === 'function') window._cccExpandStatusRailForFileViewer();
    rail.classList.add('file-viewer-active');
    if (isImage) {
      requestAnimationFrame(() => _setFileViewerImageZoom(bodyEl, 0));
    }
    return true;
  }

  async function loadTextIntoFileViewer(url, label, filePath) {
    const r = await fetch(url, { cache: 'no-store', cccPriority: 'user' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const text = await r.text();
    const rail = document.getElementById('statusRail');
    const bodyEl = document.getElementById('fileViewerBody');
    const titleEl = document.getElementById('fileViewerTitle');
    const filenameEl = document.getElementById('fileViewerFilename');
    const viewer = document.getElementById('statusRailFileViewer');
    const dlBtn = document.getElementById('fileViewerDownloadBtn');
    const fsBtn = document.getElementById('fileViewerFullscreenBtn');
    const extBtn = document.getElementById('fileViewerOpenExternalBtn');
    if (bodyEl) {
      _cleanupFileViewerMedia(bodyEl);
      bodyEl.className = 'file-viewer-body is-text-viewer';
      const pre = document.createElement('pre');
      pre.className = 'file-viewer-code';
      pre.textContent = text;
      bodyEl.replaceChildren(pre);
    }
    if (titleEl) titleEl.textContent = 'File Preview';
    if (filenameEl) filenameEl.textContent = label || (filePath ? filePath.split('/').pop() : 'File');
    if (viewer) {
      viewer.setAttribute('data-viewer-kind', 'text');
      viewer.setAttribute('data-current-src', url);
      viewer.removeAttribute('data-viewer-no-preview');
      if (filePath) viewer.setAttribute('data-current-path', filePath);
      else viewer.removeAttribute('data-current-path');
    }
    if (dlBtn) {
      dlBtn.href = url;
      dlBtn.setAttribute('download', label || '');
      dlBtn.hidden = false;
    }
    if (fsBtn) fsBtn.hidden = true;
    if (extBtn) extBtn.hidden = !filePath;
    if (typeof window._cccExpandStatusRailForFileViewer === 'function') window._cccExpandStatusRailForFileViewer();
    if (rail) rail.classList.add('file-viewer-active');
  }

  function openArtifactViewer(src, opts) {
    if (!src) return;
    if (!(opts && opts.forceModal) && _canUseStatusRailViewer()) {
      if (openArtifactInStatusRail(src, opts)) return;
    }
    openMediaLightbox(src, opts);
  }

  function openFilePathInMediaLightbox(filePath, label, ctx, opts) {
    const isCccPasted = /\/\.claude\/(?:command-center\/)?pasted-images\/paste-/.test(filePath);
    let href;
    if (isCccPasted) {
      const sid = (ctx && ctx.id) || sharedState.sessionIdByConv[sharedState.currentConversation] || (sharedState.currentSession && sharedState.currentSession.id) || '';
      href = '/api/pasted-image?path=' + encodeURIComponent(filePath) + (sid ? '&session_id=' + encodeURIComponent(sid) : '');
    } else {
      const url = new URL('/api/media', window.location.origin);
      url.searchParams.set('path', filePath);
      const sid = (ctx && ctx.id)
        || (typeof sharedState.sessionIdByConv !== 'undefined' && sharedState.currentConversation && sharedState.sessionIdByConv[sharedState.currentConversation])
        || (typeof sharedState.currentConversation === 'string' ? sharedState.currentConversation : '');
      if (sid) url.searchParams.set('session_id', sid);
      if (ctx && ctx.cwd) url.searchParams.set('cwd', ctx.cwd);
      const repoPath = (ctx && (ctx.repoPath || ctx.repo_path)) || (typeof sharedState.activeConvRepoPath === 'function' && (0, sharedState.activeConvRepoPath)()) || '';
      if (repoPath) url.searchParams.set('repo_path', repoPath);
      href = url.href;
    }
    const kind = _isVideoPath(filePath)
      ? 'video'
      : (_isHtmlDocPath(filePath) || _isPdfPath(filePath))
        ? 'frame'
        : 'image';
    const cleanName = label || String(filePath).split(/[?#]/)[0].replace(/:\d+(?::\d+)?$/, '').split('/').pop() || filePath;
    openArtifactViewer(href, { ...(opts || {}), kind, title: cleanName, filePath });
  }

  function mobileShowMain(on) {
    document.body.classList.toggle('mobile-show-main', !!on);
  }

  function mobileShowConv(on) {
    document.body.classList.toggle('mobile-conv-open', !!on);
  }

  function mobileShowForCurrentMode() {
    if (!(0, sharedState.isMobile)()) return;
    if (document.body.classList.contains('kanban-split')) mobileShowConv(true);
    else mobileShowMain(true);
  }

  function getUiMode() {
    // Flow owns this window's presentation; keep the saved parent mode intact.
    if (sharedState.FLOW_POPOUT_MODE) return 'advanced';
    try {
      const stored = localStorage.getItem('ccc-ui-mode');
      if (stored === 'simple' || stored === 'advanced') return stored;
    } catch (_) {}
    return 'advanced';
  }

  function isSimpleMode() { return getUiMode() === 'simple'; }

  function _syncSimpleHomeVisibility() {
    const home = document.getElementById('simpleHome');
    if (!home) return;
    const simpleOn = isSimpleMode();
    const show = simpleOn && !!sharedState._simpleHomeShowing && !sharedState._simpleScreen;
    document.body.classList.toggle('ccc-simple-home-open', show);
    home.style.display = show ? '' : 'none';
    // Depth-3 screens: one visible at a time, same sidebar layer as home.
    const screenOpen = simpleOn && !!sharedState._simpleScreen;
    document.body.classList.toggle('ccc-simple-screen-open', screenOpen);
    // _SIMPLE_SCREENS may still be undefined on the early-boot call from
    // _syncUiModeBodyClass (var hoists the binding, not the value).
    (sharedState._SIMPLE_SCREENS || []).forEach(name => {
      const el = document.getElementById(sharedState._SIMPLE_SCREEN_IDS[name]);
      if (el) el.hidden = !(screenOpen && sharedState._simpleScreen === name);
      document.body.classList.toggle('ccc-simple-screen-' + name, screenOpen && sharedState._simpleScreen === name);
    });
    // Depth-2: a conversation is open under simple chrome when neither home
    // nor a depth-3 screen is showing. The CSS keyed on this class hides the
    // advanced chrome (toolbar buttons, pane header, status rail, composer
    // selectors) and re-styles the transcript as plain chat bubbles.
    document.body.classList.toggle('ccc-simple-conv-open',
      simpleOn && !sharedState._simpleHomeShowing && !sharedState._simpleScreen);
    if (show || screenOpen) {
      // Home and the depth-3 screens live in the sidebar layer; .main
      // (off-canvas fixed at the mobile breakpoint) must not cover them.
      document.body.classList.remove('mobile-show-main');
    }
    if (show) {
      try { _simpleHomeRefresh(); } catch (_) {}
      if (!sharedState._simpleHomeRefreshTimer) {
        sharedState._simpleHomeRefreshTimer = setInterval(() => {
          if (isSimpleMode() && sharedState._simpleHomeShowing
              && document.body.classList.contains('ccc-simple-home-open')) {
            try { _simpleHomeRefresh(); } catch (_) {}
          }
        }, 45000);
      }
    }
    if (screenOpen) {
      try { _simpleRefreshScreen(sharedState._simpleScreen); } catch (_) {}
    }
  }

  function _simpleHideHome() {
    sharedState._simpleHomeShowing = false;
    _syncSimpleHomeVisibility();
  }

  function _simpleRefreshScreen(name) {
    if (name === 'history') _simpleRenderHistory();
    else if (name === 'automations') _simpleRenderAutomations();
    else if (name === 'automation-detail') _simpleRenderAutomationDetail();
    else if (name === 'settings') _simpleSyncSettingsScreen();
  }

  function _simpleOnConversationOpen(id) {
    try {
      if (!isSimpleMode()) return;
      if (typeof sharedState._qfBootRestoreDepth === 'undefined' || sharedState._qfBootRestoreDepth <= 0) {
        sharedState._simpleReturnScreen = sharedState._simpleScreen;
        sharedState._simpleScreen = null;
        _simpleHideHome();
      }
      _simpleUpdateUsageLine(id);
      _simpleUpdateConvTitle(id);
      _simpleSyncTechStrip(id);
    } catch (_) {}
  }

  function _simpleUpdateConvTitle(id) {
    const el = document.getElementById('simpleConvTitle');
    if (!el) return;
    let name = '', driverHtml = '';
    try {
      const row = (sharedState.conversationsData || []).find(x => x.id === id)
        || (Array.isArray(sharedState.archiveData) ? sharedState.archiveData.find(x => (x.id || x.session_id) === id) : null);
      name = String((row && (row.display_name || row.first_message || row.status_rail_title)) || '').trim();
      driverHtml = (0, sharedState.sessionDriverHtml)(row, undefined, true);
    } catch (_) {}
    el.innerHTML = '<span class="mobile-conv-name">' + (0, sharedState.escapeHtml)(name.length > 60 ? name.slice(0, 60) + '…' : (name || 'This task')) + '</span>' + driverHtml;
  }

  function _simpleSyncTechStrip(id) {
    sharedState._simpleTechCurrentId = id;
    const revealed = !!id && sharedState._simpleTechRevealedSessions.has(id);
    document.body.classList.toggle('ccc-simple-tech-open', revealed);
  }

  function _simpleNyaKindLabel(kind) {
    return sharedState._SIMPLE_NYA_KINDS[kind] || 'Waiting for you';
  }

  function _simpleNyaCardHtml(item) {
    const sid = String(item.session_id || '');
    const mtime = String(item.mtime || '');
    const title = _simpleNyaKindLabel(String(item.kind || ''));
    const task = String(item.name || '').trim();
    let need = String(item.question_text || item.next_step || item.where || '').trim();
    const _nyaAgeS = Number(item.stale_tool_age_s) || 0;
    if (_nyaAgeS && need.indexOf('{{stale_age}}') !== -1) {
      const _nyaAgeMin = Math.max(1, Math.round(_nyaAgeS / 60));
      need = need.replace(/\{\{stale_age\}\}/g, ' for about ' + _nyaAgeMin + 'm');
    }
    const options = Array.isArray(item.question_options) ? item.question_options : [];
    // Swipe wrapper: a "Not now" action sits behind the card, revealed by
    // dragging the card left (see _wireSimpleNyaSwipe). Dismissing is
    // self-healing, not permanent — see _simpleDismissNya.
    let html = '<div class="simple-nya-swipe" data-simple-nya-swipe>'
      + '<button type="button" class="simple-nya-dismiss-btn" data-dismiss-nya="' + (0, sharedState.escapeAttr)(sid) + '" data-dismiss-mtime="' + (0, sharedState.escapeAttr)(mtime) + '">Not now</button>'
      + '<div class="simple-card simple-nya-card" data-session-id="' + (0, sharedState.escapeAttr)(sid) + '" data-mtime="' + (0, sharedState.escapeAttr)(mtime) + '">'
      + '<div class="simple-nya-title">' + (0, sharedState.escapeHtml)(title) + '</div>'
      + (task ? '<div class="simple-card-name">' + (0, sharedState.escapeHtml)(task.length > 110 ? task.slice(0, 110) + '…' : task) + '</div>' : '')
      + (need ? '<div class="simple-nya-need">' + (0, sharedState.escapeHtml)(need) + '</div>' : '');
    if (options.length) {
      html += '<div class="simple-nya-options">' + options.map((opt, i) => {
        const text = typeof opt === 'string' ? opt : String((opt && (opt.label || opt.text || opt.title)) || 'Option ' + (i + 1));
        return '<button type="button" class="simple-chip simple-nya-option" data-answer-option="' + i + '">'
          + (0, sharedState.escapeHtml)(text) + '</button>';
      }).join('') + '</div>';
    }
    html += '<div class="simple-nya-answer-row">'
      + '<input type="text" class="simple-nya-answer-input" placeholder="Type your answer…" aria-label="Your answer">'
      + '<button type="button" class="simple-nya-send" data-answer-send>Send</button>'
      + '</div></div></div>';
    return html;
  }

  function _simpleLoadNyaDismissed() {
    try {
      const obj = JSON.parse(localStorage.getItem(sharedState._SIMPLE_NYA_DISMISSED_KEY) || '{}');
      return (obj && typeof obj === 'object') ? obj : {};
    } catch (_) { return {}; }
  }

  function _simpleIsNyaDismissed(item) {
    const map = _simpleLoadNyaDismissed();
    const sid = String(item.session_id || '');
    if (!sid || !(sid in map)) return false;
    return String(map[sid]) === String(item.mtime || '');
  }

  function _simpleMemoryPercent(row) {
    if (!row) return null;
    const direct = Number(row.live_context_percent);
    if (direct > 0) return direct;
    const limit = Number(row.live_context_limit) || 0;
    const tokens = Number(row.live_context_tokens) || 0;
    if (limit > 0 && tokens > 0) return (tokens / limit) * 100;
    return null;
  }

  function _simpleMemoryPhrase(p) {
    if (p >= 90) return 'start a fresh task for better results';
    if (p >= 70) return 'getting full';
    return 'plenty of room left';
  }

  function _simpleMemoryLine(row) {
    const pct = _simpleMemoryPercent(row);
    if (pct === null) return 'Memory: not reported yet';
    const p = Math.round(pct);
    return 'Memory ' + p + '% full - ' + _simpleMemoryPhrase(p);
  }

  function _simpleBaseName(p) {
    const s = String(p || '');
    const i = s.replace(/\\/g, '/').lastIndexOf('/');
    return i === -1 ? s : s.slice(i + 1);
  }

  function _simpleWorkingStatusLine(row) {
    if (row.question_waiting) return 'Waiting for you';
    const live = !!(row.is_live || row.sidecar_in_flight
      || ['running', 'working', 'active'].indexOf(String(row.status || row.session_state || '')) !== -1);
    let line = live ? 'Working' : 'Recently active';
    const file = row.pending_file || row.sidecar_file || '';
    if (live && file) line += ' - editing ' + _simpleBaseName(file);
    const folder = row.folder_label || _simpleBaseName(row.folder_path || '');
    if (folder) line += ' · ' + folder;
    return line;
  }

  function _simpleLoadSeenMtimes() {
    try {
      const obj = JSON.parse(localStorage.getItem(sharedState._SIMPLE_SEEN_KEY) || '{}');
      return (obj && typeof obj === 'object') ? obj : {};
    } catch (_) { return {}; }
  }

  function _simpleIsUnseenFinished(row) {
    const sid = String(row.id || row.session_id || '');
    if (!sid) return false;
    const mtime = String(row.mtime || row.modified || '');
    const seenMtime = _simpleLoadSeenMtimes()[sid];
    return seenMtime == null || String(seenMtime) !== mtime;
  }

  function _simpleTaskCardHtml(row, statusLine, isRunning) {
    const sid = String(row.id || row.session_id || '');
    sharedState._simpleTaskRowsById.set(sid, row);
    const archived = !!(row.explicitly_archived ?? row.idea_ticket?.explicitly_archived ?? row.archived);
    const mtime = String(row.mtime || row.modified || '');
    const name = String(row.display_name || row.first_message || row.status_rail_title || 'Untitled task').trim();
    // isRunning is caller-supplied where the caller already knows for sure
    // (the Working/Finished sections are built from a definitive is-working
    // filter) — falling back to _simpleIsWorkingRow()'s "touched in the last
    // 6 hours" heuristic only where no authoritative flag exists (History).
    // Recomputing the heuristic unconditionally here previously put the
    // "running" color on cards already filed under Finished, just because
    // the user had recently opened them.
    const working = (isRunning !== undefined) ? isRunning : _simpleIsWorkingRow(row);
    // "Running" and "unseen finished" are mutually exclusive, plain-color
    // signals: blue means it's actively working right now, amber means it
    // finished and you haven't looked yet. A seen/finished card gets neither.
    const unseen = !working && _simpleIsUnseenFinished(row);
    const stateClass = working ? ' is-running' : (unseen ? ' is-unseen-finished' : '');
    // The color bar alone tested as unclear ("one is blue, another has a
    // yellow tint" — not obvious what either meant); a plain-word badge
    // next to the name carries the same signal without relying on color.
    const badge = working
      ? '<span class="simple-card-badge simple-card-badge-running">Working…</span>'
      : (unseen ? '<span class="simple-card-badge simple-card-badge-unseen">New</span>' : '');
    // The copy-reference icon is a real <button>, which means the card's
    // own root element CANNOT be a <button> too — nested interactive
    // content is invalid HTML and the parser silently auto-closes the
    // outer <button> the moment it hits the inner one, detaching everything
    // after it from the card (this broke the icon's rendering entirely on
    // first attempt). Root is a div[role=button] instead, matching the
    // existing simple-nya-card pattern; click-to-open is delegated (doesn't
    // care about tag), and a keydown listener below restores Enter/Space
    // activation that a real <button> would have given for free.
    const copyRefBtn = sid
      ? '<button type="button" class="simple-card-copy-ref-btn" data-simple-copy-ref="' + (0, sharedState.escapeAttr)(sid) + '"'
        + ' title="Copy task reference" aria-label="Copy task reference">'
        + '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        + '<rect x="8" y="2" width="8" height="4" rx="1"></rect>'
        + '<path d="M8 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2"></path>'
        + '</svg></button>'
      : '';
    return '<div role="button" tabindex="0" class="simple-card simple-task-card' + stateClass + '"'
      + ' data-session-id="' + (0, sharedState.escapeAttr)(sid) + '" data-mtime="' + (0, sharedState.escapeAttr)(mtime) + '">'
      + '<span class="simple-card-title-row">' + (0, sharedState.sessionEngineIconHtml)(row, { context: 'pane' })
      + '<span class="simple-card-name">' + (0, sharedState.escapeHtml)(name.length > 90 ? name.slice(0, 90) + '…' : name) + '</span>'
      + badge + copyRefBtn
      + (sid && !row.trashed ? '<button type="button" class="conv-archive-btn simple-card-archive-btn" data-simple-archive="' + (0, sharedState.escapeAttr)(sid)
        + '"' + (sharedState._simpleLifecyclePending.has(sid) ? ' disabled' : '') + ' data-archived="' + (!archived) + '" aria-label="' + (archived ? 'Restore' : 'Archive')
        + '" title="' + (archived ? 'Restore' : 'Archive') + '"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">'
        + (archived ? '<path d="m8 4-4 4 4 4M4 8h10a6 6 0 0 1 0 12h-4"/>'
          : '<rect x="3" y="3" width="18" height="4" rx="1"/><path d="M5 7v13h14V7m-10 4h6"/>') + '</svg></button>' : '') + '</span>'
      + '<span class="simple-status-line">' + (0, sharedState.escapeHtml)(statusLine) + '</span>'
      + '<span class="simple-memory-line">' + (0, sharedState.escapeHtml)(_simpleMemoryLine(row)) + '</span>'
      + '</div>';
  }

  function _simpleIsWorkingRow(row) {
    if (!row || row.archived || row.trashed) return false;
    if (row.id && String(row.id).indexOf('spawning-') === 0) return false;
    if (row.is_live || row.question_waiting || row.sidecar_in_flight) return true;
    const st = String(row.status || row.session_state || '');
    if (['running', 'working', 'active', 'idle'].indexOf(st) !== -1 && row.is_live) return true;
    // Nothing reports live? Fall back to "touched in the last 6 hours" so the
    // section still answers "what is happening now" for recent work.
    const mtime = Number(row.mtime || row.modified) || 0;
    return mtime > 0 && (Date.now() / 1000 - mtime) < 6 * 3600;
  }

  async function _simpleHomeRefresh() {
    const home = document.getElementById('simpleHome');
    if (!home || !isSimpleMode() || (0, sharedState.isSidebarDragInProgress)()) return;
    // First load has nothing cached yet to show while these fetches are in
    // flight — show a spinner instead of leaving the section blank.
    const tasksEl0 = document.getElementById('simpleTasks');
    if (tasksEl0 && !tasksEl0.innerHTML.trim()) {
      tasksEl0.innerHTML = '<div class="simple-loading"><span class="simple-spinner" aria-hidden="true"></span>'
        + '<div><div>Loading your tasks…</div><div class="simple-loading-detail" id="simpleTasksProgress"></div></div></div>';
      _simpleStartArchiveProgress('simpleTasksProgress');
    }
    let attention = null, archive = null;
    try {
      const results = await Promise.all([
        fetch('/api/attention', { cache: 'no-store' }).then(r => r.json()).catch(() => null),
        (0, sharedState.loadArchiveAll)({ staleOk: true, window: 'all' }).catch(() => null),
      ]);
      attention = results[0];
      // loadArchiveAll resolves the conversations array directly (already
      // ETag-cached/deduped against Advanced mode's same in-flight fetch),
      // not a {conversations: [...]} envelope like the raw endpoint.
      archive = Array.isArray(results[1]) ? { conversations: results[1] } : null;
    } catch (_) { return; }

    if ((0, sharedState.isSidebarDragInProgress)()) return;
    // Needs you: sessions waiting on the user. (R4's stuck-queue alerts are
    // fetched separately below, not awaited here — /api/queue/status was
    // briefly bundled into this same Promise.all, which meant a slow queue
    // response delayed EVERY section, not just the alert; a real-device
    // repro showed the whole home screen going blank while it hung. Keep
    // this render on the fast/reliable path and let alerts arrive later.)
    const nyaEl = document.getElementById('simpleNeedsYou');
    if (nyaEl) {
      const items = (attention && Array.isArray(attention.items) ? attention.items : [])
        .filter(it => it && it.session_id
          && Object.prototype.hasOwnProperty.call(sharedState._SIMPLE_NYA_KINDS, String(it.kind || ''))
          && !_simpleIsNyaDismissed(it));
      nyaEl.innerHTML = items.length
        ? items.slice(0, 5).map(_simpleNyaCardHtml).join('')
        : '<div class="simple-empty">Nothing needs you right now.</div>';
    }
    _simpleRefreshQueueAlerts();

    // Your conversations: working + finished merged into one list ordered
    // strictly by last activity (was two separate sections; split them out
    // and users lost the ability to answer "what happened most recently").
    // Each card's own color/badge (_simpleTaskCardHtml) still says whether
    // it's running or finished-and-unseen.
    const tasksEl = document.getElementById('simpleTasks');
    const convRows = (archive && Array.isArray(archive.conversations)) ? archive.conversations : [];
    applyOptimisticOverrides(convRows);
    const sessionRows = convRows.filter(row => !row.trashed && !(row.explicitly_archived ?? row.idea_ticket?.explicitly_archived ?? row.archived));
    const workingRows = sessionRows.filter(_simpleIsWorkingRow);
    const workingIds = {};
    workingRows.forEach(r => { workingIds[String(r.id || r.session_id || '')] = true; });
    const finishedRows = sessionRows.filter(r => r && !workingIds[String(r.id || r.session_id || '')]);
    const merged = workingRows.concat(finishedRows)
      .sort((a, b) => (Number(b.mtime || b.modified) || 0) - (Number(a.mtime || a.modified) || 0))
      .slice(0, 10);
    // A cold first scan outlives loadArchiveAll's fetch timeout (null). Keep
    // the spinner and its progress line up instead of claiming the list is
    // empty while the server says the scan is still running; the 45s home
    // refresh asks again.
    let stillScanning = false;
    if (!archive && tasksEl && tasksEl.querySelector('.simple-loading')) {
      const snap = await _simpleFetchArchiveStatus();
      stillScanning = !!(snap && snap.active);
    }
    if (tasksEl && !stillScanning) {
      tasksEl.innerHTML = merged.length
        ? merged.map(r => {
            const working = !!workingIds[String(r.id || r.session_id || '')];
            return _simpleTaskCardHtml(r, working ? _simpleWorkingStatusLine(r) : 'Past task', working);
          }).join('')
        : '<div class="simple-empty">Your conversations will show up here.</div>';
    }
  }

  async function _simpleRefreshQueueAlerts() {
    const nyaEl = document.getElementById('simpleNeedsYou');
    if (!nyaEl || !isSimpleMode()) return;
    let queueStatus = null;
    try {
      const res = await fetch('/api/queue/status', { cache: 'no-store' });
      queueStatus = await res.json().catch(() => null);
    } catch (_) { return; }
    if (!isSimpleMode()) return;   // mode may have changed while this was in flight
    nyaEl.querySelectorAll('.simple-queue-alert-card').forEach(el => el.remove());
    const stuckQueues = (queueStatus && Array.isArray(queueStatus.queues) ? queueStatus.queues : [])
      .filter(q => q && q.stuck);
    if (!stuckQueues.length) return;
    const emptyState = nyaEl.querySelector('.simple-empty');
    if (emptyState) emptyState.remove();
    nyaEl.insertAdjacentHTML('afterbegin', stuckQueues.map(_simpleQueueAlertCardHtml).join(''));
  }

  async function _simpleUpdateUsageLine(sid) {
    const el = document.getElementById('simpleUsageLine');
    if (!el) return;
    if (!isSimpleMode() || !sid || sid === '__new__' || String(sid).indexOf('spawning-') === 0
        || !_simpleMemoryBannerEnabled()) {
      el.classList.remove('has-data');
      el.textContent = '';
      return;
    }
    const render = (pct, cost) => {
      let text;
      if (pct === null) text = 'Memory: not measured yet.';
      else {
        const p = Math.round(pct);
        text = 'Memory: ' + p + '% full - ' + _simpleMemoryPhrase(p) + '.';
      }
      if (cost !== null) text += ' About $' + cost.toFixed(2) + ' spent so far.';
      el.textContent = text;
      el.classList.add('has-data');
    };
    // Render the best-known value immediately — the /usage fetch can be slow,
    // and the line must not stay invisible (it only displays with .has-data).
    // Fall back to the list row the sidebar already computed for this conv.
    let fallbackPct = null;
    try {
      const row = (sharedState.conversationsData || []).find(x => x.id === sid)
        || (Array.isArray(sharedState.archiveData) ? sharedState.archiveData.find(x => (x.id || x.session_id) === sid) : null);
      fallbackPct = _simpleMemoryPercent(row);
    } catch (_) {}
    render(fallbackPct, null);
    // Then refine from the live usage endpoint.
    let pct = null;
    let cost = null;
    try {
      const res = await fetch('/api/session/' + encodeURIComponent(sid) + '/usage', { cache: 'no-store' });
      const d = await res.json().catch(() => null);
      if (d) {
        // Live-only, same reasoning as _simpleMemoryPercent above: never fall
        // back to the guessed context_limit/latest_input_tokens fields.
        const limit = Number(d.live_context_limit) || 0;
        const tokens = Number(d.live_context_tokens) || 0;
        if (Number(d.live_context_percent) > 0) pct = Number(d.live_context_percent);
        else if (limit > 0 && tokens > 0) pct = (tokens / limit) * 100;
        if (typeof d.cost_usd === 'number' && d.cost_usd > 0) cost = d.cost_usd;
      }
    } catch (_) {}
    if (pct === null && cost === null) return; // keep the fallback render
    render(pct === null ? fallbackPct : pct, cost);
  }

  function _simplePlainAge(seconds) {
    const s = Number(seconds);
    if (!(s >= 0)) return 'not yet';
    if (s < 90) return 'just now';
    const m = Math.round(s / 60);
    if (m < 60) return m + (m === 1 ? ' minute ago' : ' minutes ago');
    const h = Math.round(m / 60);
    if (h < 48) return h + (h === 1 ? ' hour ago' : ' hours ago');
    const d = Math.round(h / 24);
    if (d < 30) return d + (d === 1 ? ' day ago' : ' days ago');
    const w = Math.round(d / 7);
    if (w < 9) return w + (w === 1 ? ' week ago' : ' weeks ago');
    const mo = Math.round(d / 30);
    return mo + (mo === 1 ? ' month ago' : ' months ago');
  }

  function _simpleHistoryApply() {
    const list = document.getElementById('simpleHistoryList');
    if (!list || (0, sharedState.isSidebarDragInProgress)()) return;
    const query = String((document.getElementById('simpleHistorySearch') || {}).value || '')
      .trim().toLowerCase();
    let rows = (sharedState._simpleHistoryRows || []);
    if (query) {
      rows = rows.filter(r => {
        const hay = [r.display_name, r.first_message, r.status_rail_title, r.folder_label, r.folder_path]
          .map(x => String(x || '').toLowerCase()).join(' ');
        return hay.indexOf(query) !== -1;
      });
    }
    const limited = rows.slice(0, 60);
    list.innerHTML = limited.length
      ? limited.map(r => _simpleTaskCardHtml(r, _simpleHistoryStatusLine(r))).join('')
      : '<div class="simple-empty">'
        + (query ? 'Nothing matches that search.' : 'Past tasks will show up here once you start some.')
        + '</div>';
  }

  async function _simpleRenderHistory() {
    // First open (no cached rows yet) has nothing to show while the fetch
    // is in flight — show a spinner instead of a silent blank screen.
    // Later opens already have _simpleHistoryRows from last time, so keep
    // showing that immediately and just refresh it in the background.
    const list = document.getElementById('simpleHistoryList');
    const hasCache = Array.isArray(sharedState._simpleHistoryRows) && sharedState._simpleHistoryRows.length > 0;
    if (!hasCache && list) {
      list.innerHTML = '<div class="simple-loading"><span class="simple-spinner" aria-hidden="true"></span>'
        + '<div><div>Loading your past tasks…</div><div class="simple-loading-detail" id="simpleHistoryProgress"></div></div></div>';
      _simpleStartArchiveProgress('simpleHistoryProgress');
    }
    try {
      // A cold archive build on a fresh server can take minutes, longer than
      // loadArchiveAll's fetch timeout (null result). While the server says
      // the scan is still running, ask again instead of falling through to
      // the "nothing here yet" empty state.
      for (let attempt = 0; attempt < 10; attempt++) {
        const rows = await (0, sharedState.loadArchiveAll)({ staleOk: true, window: 'all' });
        if (Array.isArray(rows)) {
          sharedState._simpleHistoryRows = rows
            .filter(r => r && (r.id || r.session_id))
            .sort((a, b) => (Number(b.mtime || b.modified) || 0) - (Number(a.mtime || a.modified) || 0));
          break;
        }
        if (hasCache || sharedState._simpleScreen !== 'history') break;
        const snap = await _simpleFetchArchiveStatus();
        if (!snap || !snap.active) break;
      }
    } catch (_) { /* keep the previous cache */ }
    _simpleStopArchiveProgress('simpleHistoryProgress');
    _simpleHistoryApply();
  }

  async function _simpleFetchArchiveStatus() {
    try {
      const r = await (0, sharedState.backgroundApiFetch)('/api/archive/loading-status');
      return r.ok ? await r.json() : null;
    } catch (_) { return null; }
  }

  function _simpleStartArchiveProgress(elId) {
    if (sharedState._simpleArchiveProgressIds[elId]) return;
    const t0 = Date.now();
    const tick = async () => {
      const el = document.getElementById(elId);
      if (!el) { _simpleStopArchiveProgress(elId); return; }
      const snap = await _simpleFetchArchiveStatus();
      const secs = Math.round((Date.now() - t0) / 1000);
      let line = '';
      if (snap && snap.active) {
        // Every scan stage flips to running at once, so prefer the one
        // that carries a live count (transcripts N of M).
        const running = (snap.steps || []).filter(s => s.state === 'running');
        const step = running.find(s => typeof s.count === 'number' && typeof s.total === 'number')
          || running[0];
        if (step) {
          line = step.label;
          if (typeof step.count === 'number' && typeof step.total === 'number') {
            line += ' (' + step.count + ' of ' + step.total + ')';
          }
        }
      }
      if (secs >= 3) {
        line = (line ? line + ' · ' : '') + secs + 's';
        if (secs >= 20) line += '. The first load can take a few minutes.';
      }
      el.textContent = line;
    };
    tick();
    sharedState._simpleArchiveProgressIds[elId] = setInterval(tick, 1000);
  }

  function _simpleStopArchiveProgress(elId) {
    if (sharedState._simpleArchiveProgressIds[elId]) {
      clearInterval(sharedState._simpleArchiveProgressIds[elId]);
      delete sharedState._simpleArchiveProgressIds[elId];
    }
  }

  function _simpleHistoryStatusLine(row) {
    const age = Number(row.mtime || row.modified) || 0;
    return age > 0 ? 'Last worked on ' + _simplePlainAge(Date.now() / 1000 - age) : 'Past task';
  }

  function _simpleAutomationStatusLine(q) {
    const depth = Number(q.depth) || 0;
    const workers = Number(q.workers) || 0;
    if (q.stuck) return 'Needs attention - something looks stuck';
    if (workers > 0) return 'Working on it now (' + workers + (workers === 1 ? ' helper' : ' helpers') + ')';
    if (depth > 0 && q.auto_drain) return depth + ' waiting - a helper will pick them up';
    if (depth > 0) return depth + (depth === 1 ? ' job waiting' : ' jobs waiting') + ' - nobody on it yet';
    return 'Nothing waiting';
  }

  function _simplePlainAgeIso(iso) {
    const ms = Date.parse(String(iso || ''));
    if (!(ms > 0)) return '';
    return _simplePlainAge((Date.now() - ms) / 1000);
  }

  function _simpleQueueItemCardHtml(it, isOpen) {
    const text = String(it.note || it.text || it.title || it.ref || 'Untitled job');
    const short = text.length > 110 ? text.slice(0, 110) + '…' : text;
    const sid = String(it.claimed_session_id || '').trim();
    let statusLine;
    if (isOpen) {
      statusLine = it.claimed_by
        ? 'Someone is already on it' + (it.claimed_at ? ' - since ' + _simplePlainAgeIso(it.claimed_at) : '')
        : 'Waiting' + (it.created_at ? ' since ' + _simplePlainAgeIso(it.created_at) : '');
    } else {
      statusLine = 'Finished' + (it.closed_at ? ' ' + _simplePlainAgeIso(it.closed_at) : '');
    }
    const tag = sid ? 'button' : 'div';
    return '<' + tag + (sid ? ' type="button"' : '') + ' class="simple-card simple-task-card simple-queue-item"'
      + (sid ? ' data-session-id="' + (0, sharedState.escapeAttr)(sid) + '"' : '')
      + '>'
      + '<span class="simple-card-name">' + (0, sharedState.escapeHtml)(short) + '</span>'
      + '<span class="simple-status-line">' + (0, sharedState.escapeHtml)(statusLine) + '</span>'
      + '</' + tag + '>';
  }

  function _simpleQueueAlertCardHtml(q) {
    const name = String(q.queue || 'Helper queue');
    return '<div class="simple-card simple-nya-card simple-queue-alert-card" data-simple-queue-alert="' + (0, sharedState.escapeAttr)(name) + '">'
      + '<div class="simple-nya-title">Needs attention</div>'
      + '<div class="simple-card-name">' + (0, sharedState.escapeHtml)(name.charAt(0) + name.slice(1).toLowerCase()) + '</div>'
      + '<div class="simple-nya-need">Jobs are waiting but nobody’s on them - tap to see what’s stuck.</div>'
      + '</div>';
  }

  function _simpleAutomationCardHtml(q) {
    const name = String(q.queue || 'Helper queue');
    return '<button type="button" class="simple-card simple-task-card simple-automation-card"'
      + ' data-simple-queue="' + (0, sharedState.escapeAttr)(name) + '">'
      + '<span class="simple-card-name">' + (0, sharedState.escapeHtml)(name.charAt(0) + name.slice(1).toLowerCase()) + '</span>'
      + '<span class="simple-status-line">' + (0, sharedState.escapeHtml)(_simpleAutomationStatusLine(q)) + '</span>'
      + '<span class="simple-memory-line">'
      + (0, sharedState.escapeHtml)(q.last_activity_seconds != null
        ? 'Last activity ' + _simplePlainAge(q.last_activity_seconds)
        : 'No activity yet')
      + '</span></button>';
  }

  async function _simpleRenderAutomations() {
    const list = document.getElementById('simpleAutomationsList');
    if (!list) return;
    let queues = [];
    try {
      const res = await fetch('/api/queue/status', { cache: 'no-store' });
      const data = await res.json().catch(() => ({}));
      queues = Array.isArray(data && data.queues) ? data.queues : [];
    } catch (_) { /* keep empty */ }
    list.innerHTML = queues.length
      ? queues.map(_simpleAutomationCardHtml).join('')
      : '<div class="simple-empty">No automations set up yet.</div>';
  }

  async function _simpleRenderAutomationDetail() {
    const nameEl = document.getElementById('simpleAutomationDetailName');
    const body = document.getElementById('simpleAutomationDetailBody');
    if (!nameEl || !body) return;
    const queue = sharedState._simpleAutomationQueue;
    nameEl.textContent = queue ? (queue.charAt(0) + queue.slice(1).toLowerCase()) : 'Helper queue';
    let q = null, items = [];
    try {
      const res = await fetch('/api/queue/status', { cache: 'no-store' });
      const data = await res.json().catch(() => ({}));
      const queues = Array.isArray(data && data.queues) ? data.queues : [];
      q = queues.find(x => String(x.queue || '').toUpperCase() === String(queue).toUpperCase()) || null;
    } catch (_) {}
    try {
      const res = await fetch('/api/queue/list?slim=1', { cache: 'no-store' });
      const data = await res.json().catch(() => ({}));
      items = (Array.isArray(data && data.items) ? data.items : [])
        .filter(it => String(it.project || '').toUpperCase() === String(queue).toUpperCase());
    } catch (_) {}
    const open = items.filter(it => String(it.status || '') === 'open')
      .sort((a, b) => (Date.parse(a.created_at || '') || 0) - (Date.parse(b.created_at || '') || 0));
    const done = items.filter(it => String(it.status || '') === 'closed')
      .sort((a, b) => (Date.parse(b.closed_at || '') || 0) - (Date.parse(a.closed_at || '') || 0));
    // Remembered for _simpleSpawnQueueHelper() (R3) — the spawn-worker
    // endpoint is queue-scoped (it can't target one specific ticket), so
    // this is one "get help now" action for the whole list, distinct from
    // the recurring auto-drain toggle above it.
    sharedState._simpleAutomationRepoPath = (q && q.repo_path) || '';
    let html = '<div class="simple-card">'
      + '<div class="simple-settings-label">How it is doing</div>'
      + '<div class="simple-settings-desc">' + (0, sharedState.escapeHtml)(q ? _simpleAutomationStatusLine(q) : 'Status unknown') + '</div>'
      + '<div class="simple-memory-line">'
      + (0, sharedState.escapeHtml)(q && q.last_activity_seconds != null
        ? 'Last activity ' + _simplePlainAge(q.last_activity_seconds)
        : 'No activity yet')
      + '</div>'
      + '<div class="simple-memory-line">' + (0, sharedState.escapeHtml)(
        open.length + (open.length === 1 ? ' job waiting' : ' jobs waiting')
        + ' · ' + done.length + ' finished') + '</div>'
      + '</div>'
      + '<button type="button" class="simple-start-btn" id="simpleAutomationDrainBtn">'
      + (0, sharedState.escapeHtml)(q && q.auto_drain ? 'Stop working on these automatically' : 'Work on these automatically')
      + '</button>';
    if (open.length && sharedState._simpleAutomationRepoPath) {
      html += '<button type="button" class="simple-chip simple-automation-help-btn" id="simpleAutomationHelpBtn">'
        + 'Get extra help on this right now</button>';
    }
    if (open.length) {
      html += '<h3 class="simple-h3">Waiting jobs</h3><div class="simple-card-list">'
        + open.slice(0, 20).map(it => _simpleQueueItemCardHtml(it, true)).join('') + '</div>';
    }
    if (done.length) {
      html += '<h3 class="simple-h3">Recently finished</h3><div class="simple-card-list">'
        + done.slice(0, 10).map(it => _simpleQueueItemCardHtml(it, false)).join('') + '</div>';
    }
    body.innerHTML = html;
  }

  function _simpleMemoryBannerEnabled() {
    try { return localStorage.getItem('ccc-simple-memory-banner') !== 'off'; } catch (_) { return true; }
  }

  function _simpleSyncSettingsScreen() {
    let theme = 'dark';
    try { theme = localStorage.getItem('ccc-theme') || 'dark'; } catch (_) {}
    document.querySelectorAll('#simpleThemeRow [data-simple-theme]').forEach(btn => {
      const sel = btn.getAttribute('data-simple-theme') === theme;
      btn.classList.toggle('is-selected', sel);
      btn.setAttribute('aria-pressed', sel ? 'true' : 'false');
    });
    const bannerOn = _simpleMemoryBannerEnabled();
    document.querySelectorAll('#simpleMemoryBannerRow [data-simple-memory-banner]').forEach(btn => {
      const sel = (btn.getAttribute('data-simple-memory-banner') === 'on') === bannerOn;
      btn.classList.toggle('is-selected', sel);
      btn.setAttribute('aria-pressed', sel ? 'true' : 'false');
    });
    const voiceMobileOn = getVoiceMobileBtnPref();
    document.querySelectorAll('#simpleVoiceMobileRow [data-simple-voice-mobile]').forEach(btn => {
      const sel = (btn.getAttribute('data-simple-voice-mobile') === 'on') === voiceMobileOn;
      btn.classList.toggle('is-selected', sel);
      btn.setAttribute('aria-pressed', sel ? 'true' : 'false');
    });
  }

  function syncConversationNavigationFocus(convId, otherIds) {
    if (sharedState.$convList) {
      sharedState.$convList.querySelectorAll('.conv-item').forEach(el => {
        const isActive = el.dataset.id === convId;
        el.classList.toggle('active', isActive);
        el.classList.toggle('in-other-pane', !isActive && otherIds.has(el.dataset.id));
        el.classList.remove('mobile-tapped');
      });
    }
    if (sharedState.$kanbanBoard) {
      sharedState.$kanbanBoard.querySelectorAll('.kanban-card').forEach(el => {
        el.classList.toggle('active', el.dataset.id === convId);
      });
    }
    if (sharedState.$kanbanBoardSplit) {
      sharedState.$kanbanBoardSplit.querySelectorAll('.kanban-card').forEach(el => {
        el.classList.toggle('active', el.dataset.id === convId);
      });
    }
    if (sharedState.$flowBoard) {
      sharedState.$flowBoard.querySelectorAll('.flow-node-session').forEach(el => {
        el.classList.toggle('active', el.dataset.id === convId);
      });
    }
  }

  function updateSubagentsPanel(convId) {
    const panel = document.getElementById('subagentsPanel');
    if (!panel) return;
    const countEl = document.getElementById('subagentsCount');
    const listEl = document.getElementById('subagentsList');
    if (!listEl || !countEl) return;
    const row = (sharedState.conversationsData || []).find(c => c.id === convId);
    const recent = row && Array.isArray(row.subagent_recent) ? row.subagent_recent : [];
    const total = row ? Number(row.subagent_count || 0) : 0;
    const inFlight = row ? Number(row.subagent_in_flight_count || 0) : 0;
    const workflows = row && Array.isArray(row.workflows) ? row.workflows : [];
    if (!recent.length && !workflows.length) {
      panel.style.display = 'none';
      listEl.innerHTML = '';
      countEl.textContent = '';
      return;
    }
    panel.style.display = '';
    countEl.textContent = inFlight > 0 ? (inFlight + ' / ' + total) : String(total);
    countEl.title = inFlight > 0
      ? (inFlight + ' running, ' + total + ' total spawned this session')
      : (total + ' subagent' + (total === 1 ? '' : 's') + ' spawned this session');
    let html = recent.map(t => {
      const status = (t && t.status) === 'in-flight' ? 'in-flight' : 'done';
      const desc = (0, sharedState.escapeHtml)(String((t && t.description) || '(unnamed task)'));
      const type = t && t.subagent_type
        ? '<span class="subagent-type">' + (0, sharedState.escapeHtml)(t.subagent_type) + '</span>'
        : '';
      return '<div class="subagent-row is-' + status + '" title="' + (0, sharedState.escapeAttr)(desc) + '">'
        + '<span class="subagent-status">' + (status === 'in-flight' ? '▶' : '✓') + '</span>'
        + '<span class="subagent-desc">' + desc + '</span>'
        + type
        + '</div>';
    }).join('');
    if (workflows.length) {
      html += workflows.map(run => {
        const agents = Array.isArray(run.agents) ? run.agents : [];
        const doneN = agents.filter(a => a && a.status === 'done').length;
        const runStatus = ['running', 'done', 'failed', 'interrupted'].includes(run.status) ? run.status : 'done';
        const runTip = 'Workflow run ' + run.run_id + ' · ' + doneN + ' of ' + agents.length + ' agents done · ' + runStatus;
        let runHtml = '<div class="workflow-run is-' + runStatus + '">'
          + '<div class="workflow-run-header" title="' + (0, sharedState.escapeAttr)(runTip) + '">'
          + '<span class="subagent-status">' + (sharedState.WF_AGENT_GLYPH[runStatus] || '✓') + '</span>'
          + '<span class="workflow-run-id">' + (0, sharedState.escapeHtml)(String(run.run_id || '')) + '</span>'
          + '<span class="workflow-run-count">' + doneN + ' / ' + agents.length + '</span>'
          + '</div>';
        runHtml += agents.map(a => {
          const aStatus = ['running', 'done', 'failed', 'interrupted'].includes(a.status) ? a.status : 'done';
          const aDesc = String(a.description || a.id || '');
          const aTip = aDesc + ' · ' + aStatus + ' · click to open transcript';
          return '<button type="button" class="subagent-row wf-agent is-' + aStatus + '"'
            + ' data-wf-conv="' + (0, sharedState.escapeAttr)(String(a.conv_id || '')) + '"'
            + ' title="' + (0, sharedState.escapeAttr)(aTip) + '">'
            + '<span class="subagent-status">' + (sharedState.WF_AGENT_GLYPH[aStatus] || '✓') + '</span>'
            + '<span class="subagent-desc">' + (0, sharedState.escapeHtml)(aDesc || '(unnamed agent)') + '</span>'
            + '</button>';
        }).join('');
        return runHtml + '</div>';
      }).join('');
    }
    listEl.innerHTML = html;
  }

  function applyOptimisticOverrides(list) {
    if (!sharedState._optimisticOverrides.size) return;
    const now = Date.now();
    for (const [sid, entry] of sharedState._optimisticOverrides) {
      if (now - entry.ts > sharedState._OPTIMISTIC_TTL_MS) sharedState._optimisticOverrides.delete(sid);
    }
    for (const c of list) {
      const ov = sharedState._optimisticOverrides.get(c.session_id);
      if (!ov) continue;
      let allMatch = true;
      if ('archived' in ov) {
        if (c.archived !== ov.archived) { c.archived = ov.archived; allMatch = false; }
      }
      if ('explicitly_archived' in ov) {
        if (c.explicitly_archived !== ov.explicitly_archived) { c.explicitly_archived = ov.explicitly_archived; allMatch = false; }
      }
      if ('trashed' in ov) {
        if (c.trashed !== ov.trashed) { c.trashed = ov.trashed; allMatch = false; }
      }
      if ('verified' in ov) {
        if (c.verified !== ov.verified) { c.verified = ov.verified; allMatch = false; }
      }
      if ('pinned' in ov) {
        if (c.pinned !== ov.pinned) { c.pinned = ov.pinned; allMatch = false; }
      }
      if ('pin_rank' in ov) {
        if (c.pin_rank !== ov.pin_rank) { c.pin_rank = ov.pin_rank; allMatch = false; }
      }
      // Local renders also pass through here, so agreement is not proof a
      // server read began after the action. Retain explicit lifecycle intent
      // for the bounded TTL to shield delayed pre-action polls as well.
      if (allMatch && !('explicitly_archived' in ov)) sharedState._optimisticOverrides.delete(c.session_id);
    }
  }

  function renderNewSessionObjectContext() {
    // CCC-950: the OBJECT picker in the new-session composer is hidden —
    // this stays a no-op rather than a full removal, since
    // getNewSessionSelectedObject()/ensureNewSessionDefaultObject() etc. are
    // still used by the post-spawn assignment path.
    const wrap = document.getElementById('newSessionObjectContext');
    if (!wrap) return;
    wrap.innerHTML = '';
    wrap.style.display = 'none';
  }

  function mountNewSessionChrome(fallbackPaneId) {
    const home = document.getElementById('convInputContext');
    if (!home) return;
    const newPid = (0, sharedState.newSessionPaneId)();
    const targetPid = newPid || 'p1';
    const target = (targetPid === 'p1' ? home : (0, sharedState.getInputContextSlot)(targetPid)) || home;
    const pieces = [
      document.getElementById('newSessionObjectContext'),
      document.getElementById('spawnCwdQuickChips'),
      (document.getElementById('spawnCwdPicker') || { closest: () => null }).closest('.spawn-cwd-row'),
      (document.getElementById('inlineWorktreeToggle') || { closest: () => null }).closest('.spawn-worktree-row'),
    ].filter(Boolean);
    const anchor = target.querySelector(':scope > [data-workspace]');
    let after = anchor;
    pieces.forEach(el => {
      if (el.parentElement === target && (!after || el.previousElementSibling === after)) { after = el; return; }
      target.insertBefore(el, after ? after.nextSibling : target.firstChild);
      after = el;
    });
    document.querySelectorAll('#convSplit > .conv-pane > .conv-input-context[data-role="input-context"]').forEach(slot => {
      const on = !!newPid && slot === target;
      if (slot.classList.contains('is-new-session') !== on) slot.classList.toggle('is-new-session', on);
      if (typeof sharedState.syncInputContextVisibility === 'function') (0, sharedState.syncInputContextVisibility)(slot);
    });
    const pickerStrip = document.getElementById('convModelPickerStrip');
    const stripPane = document.querySelector(`.conv-pane[data-pane-id="${newPid || fallbackPaneId || (0, sharedState.activePaneId)() || 'p1'}"]`);
    if (pickerStrip && stripPane && pickerStrip.parentElement !== stripPane) stripPane.appendChild(pickerStrip);
    if (pickerStrip && newPid) {
      pickerStrip.style.display = 'flex';
      pickerStrip.classList.add('is-new-session');
    }
  }

  async function ffcFetch(convId) {
    if (!convId || convId === '__new__' || convId.startsWith('backlog-') || convId.startsWith('pkood-') || convId.startsWith('issue-')) {
      return null;
    }
    if (sharedState._ffcCache.has(convId)) {
      return sharedState._ffcCache.get(convId);
    }
    const pending = sharedState._ffcInFlight.get(convId);
    if (pending) return pending;
    const run = (async () => {
      try {
        const r = await fetch('/api/conversations/' + encodeURIComponent(convId) + '/files', { signal: (0, sharedState.convScopeSignal)() });
        if (!r.ok) {
          sharedState._ffcCache.set(convId, {count: 0, truncated: false, groups: {}});
          return sharedState._ffcCache.get(convId);
        }
        const data = await r.json();
        sharedState._ffcCache.set(convId, data);
        return data;
      } catch (e) {
        // Conversation switched underneath the walk: nothing to cache.
        if (e && e.name === 'AbortError') return null;
        // Network / parse failure — silent. Pill just stays hidden.
        sharedState._ffcCache.set(convId, {count: 0, truncated: false, groups: {}});
        return sharedState._ffcCache.get(convId);
      } finally {
        sharedState._ffcInFlight.delete(convId);
      }
    })();
    sharedState._ffcInFlight.set(convId, run);
    return run;
  }

  function ffcEnsurePill(stickyEl, data) {
    if (!stickyEl) return;
    // The pill lives inside `.csh-col-activity`. In right-rail mode that
    // column has been moved out of the sticky into `#statusRail`, so a
    // sticky-scoped query misses it and we'd fall back to inserting the
    // pill at the top of the sticky (showing as a stray "Files" panel
    // above earlier-ask). Search the document so the pill follows the
    // activity column wherever it currently lives.
    const activity = document.querySelector('.csh-col-activity');
    let pill = (activity && activity.querySelector('.ffc-pill'))
            || stickyEl.querySelector('.ffc-pill');
    const hasFiles = data && data.count > 0;
    if (!hasFiles) {
      if (pill) pill.hidden = true;
      return;
    }
    if (!pill) {
      pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'ffc-pill';
      pill.title = 'Files mentioned in this conversation';
      pill.innerHTML = '📎 Files <span class="ffc-pill-count"></span>';
      pill.addEventListener('click', () => {
        const cur = sharedState.currentConversation;
        const cached = cur ? sharedState._ffcCache.get(cur) : null;
        if (cached && typeof openFfcModal === 'function') openFfcModal(cached);
      });
    }
    // Always reparent to the live activity column (handles toggle moves).
    const host = activity || stickyEl;
    if (pill.parentElement !== host) {
      host.insertBefore(pill, host.firstChild || null);
    }
    pill.hidden = false;
    pill.querySelector('.ffc-pill-count').textContent = '(' + data.count + ')';
  }

  async function ffcRefreshForCurrent() {
    const cur = sharedState.currentConversation;
    if (!cur) return;
    const data = await ffcFetch(cur);
    // The conversation may have switched while we were fetching. Bail
    // if so — the new conversation's renderer will trigger its own.
    if (sharedState.currentConversation !== cur) return;
    const sticky = document.querySelector('.conversations-view .conv-sticky-header');
    ffcEnsurePill(sticky, data);
    ffcUpdateSidebar(data);
  }

  function _syncFilesPanelChrome() {
    const $panel = document.getElementById('filesPanel');
    const $files = document.getElementById('sidebarFilesList');
    const $search = document.getElementById('filesSearchInput');
    const $label = $panel && $panel.querySelector('.files-title-label');
    if (!$panel || !$files) return false;
    $files.style.display = '';
    if ($search) $search.style.display = '';
    if ($label) $label.textContent = 'Files';
    (0, hostServices._renderQueuePanel)();
  }

  function ffcUpdateSidebar(data) {
    sharedState._ffcLastSidebarData = data;
    const $panel = document.getElementById('filesPanel');
    const $count = document.getElementById('filesCount');
    const $list = document.getElementById('sidebarFilesList');
    if (!$panel || !$count || !$list) return;
    _syncFilesPanelChrome();
    // Files tab label count + empty state (CCC-1228).
    const $tabCount = document.getElementById('filesTabCount');
    const $empty = document.getElementById('filesTabEmpty');
    const n = (data && data.count) || 0;
    if ($tabCount) $tabCount.textContent = n ? String(n) : '';
    if ($empty) $empty.hidden = !!n;

    if (!n) {
      $panel.style.display = 'none';
      $count.textContent = '';
      $list.innerHTML = '';
      return;
    }

    $panel.style.display = '';
    $count.textContent = data.count;
    $list.innerHTML = '';

    // Flatten all categories
    const allFiles = [];
    for (const cat of sharedState.FFC_CATEGORY_ORDER) {
      const rows = (data.groups || {})[cat.key];
      if (rows && rows.length) {
        for (const row of rows) {
          allFiles.push(Object.assign({}, row, { category: cat.key, categoryIcon: cat.icon }));
        }
      }
    }

    // Sort reverse chronologically by the latest mention of each file.
    allFiles.sort((a, b) => _ffcRecencyLine(b) - _ffcRecencyLine(a));

    // Render each file
    for (const row of allFiles) {
      $list.appendChild(renderSidebarFileRow(row));
    }

    // Apply any active search filter
    const $searchInput = document.getElementById('filesSearchInput');
    if ($searchInput && $searchInput.value) {
      $searchInput.dispatchEvent(new Event('input'));
    }
  }

  function _ffcRecencyLine(row) {
    const last = Number(row && row.last_line);
    if (Number.isFinite(last) && last > 0) return last;
    const first = Number(row && row.first_line);
    return Number.isFinite(first) && first > 0 ? first : 0;
  }

  async function loadMarkdownIntoFileViewer(target, label) {
    const sid = sharedState.sessionIdByConv[sharedState.currentConversation] || (sharedState.currentSession && sharedState.currentSession.id) || '';
    const r = await fetch('/api/read-file', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: target, session_id: sid}),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({error: 'load failed'}));
      throw new Error(j.error || ('HTTP ' + r.status));
    }
    const res = await r.json();
    if (!res.ok) throw new Error(res.error || 'load failed');
    const rail = document.getElementById('statusRail');
    const bodyEl = document.getElementById('fileViewerBody');
    const titleEl = document.getElementById('fileViewerTitle');
    const filenameEl = document.getElementById('fileViewerFilename');
    const viewer = document.getElementById('statusRailFileViewer');
    const dlBtn = document.getElementById('fileViewerDownloadBtn');
    const fsBtn = document.getElementById('fileViewerFullscreenBtn');
    const extBtn = document.getElementById('fileViewerOpenExternalBtn');
    if (bodyEl) {
      _cleanupFileViewerMedia(bodyEl);
      bodyEl.className = 'file-viewer-body markdown-body assistant-text';
      bodyEl.innerHTML = typeof sharedState.renderMarkdown === 'function' ? (0, sharedState.renderMarkdown)(res.content) : res.content;
      // Rewrite <img src=…> to route through /api/local-image so relative
      // paths (./screenshot.png), bare names, and absolute filesystem paths
      // all resolve against the markdown file's directory, not the CCC origin.
      const _mdDir = target.replace(/\/[^/]*$/, '');
      bodyEl.querySelectorAll('img').forEach((img) => {
        const raw = img.getAttribute('src') || '';
        if (!raw) return;
        if (/^(https?:|data:|blob:|file:|\/api\/)/i.test(raw)) return;
        let abs;
        if (raw.startsWith('/')) abs = raw;
        else if (raw.startsWith('~/')) abs = raw;
        else abs = _mdDir + '/' + raw;
        img.src = '/api/local-image?path=' + encodeURIComponent(abs);
      });
    }
    if (titleEl) titleEl.textContent = 'File Preview';
    if (filenameEl && label != null) filenameEl.textContent = label;
    if (viewer) {
      viewer.setAttribute('data-viewer-kind', 'markdown');
      viewer.setAttribute('data-current-path', target);
      viewer.removeAttribute('data-current-src');
      viewer.removeAttribute('data-viewer-no-preview');
    }
    if (dlBtn) dlBtn.hidden = true;
    if (fsBtn) fsBtn.hidden = true;
    if (extBtn) extBtn.hidden = false;
    if (typeof window._cccExpandStatusRailForFileViewer === 'function') window._cccExpandStatusRailForFileViewer();
    if (rail) rail.classList.add('file-viewer-active');
  }

  function renderSidebarFileRow(row) {
    const rowEl = document.createElement('div');
    rowEl.className = 'sidebar-file-row';
    rowEl.title = 'Click to open';

    // Thumbnail / Icon placeholder
    if (row.category === 'images') {
      const target = row.target || '';
      const sid = sharedState.sessionIdByConv[sharedState.currentConversation] || (sharedState.currentSession && sharedState.currentSession.id) || '';
      const isHttp = /^https?:\/\//i.test(target);
      // Local conversation artifacts use the same image endpoint as the
      // Markdown viewer; the pasted-image proxy only accepts upload paths.
      const isCccPasted = /\/\.claude\/(?:command-center\/)?pasted-images\/paste-/.test(target);
      if (target) {
        const img = document.createElement('img');
        img.className = 'sidebar-file-thumb';
        img.alt = row.label;
        img.loading = 'lazy';
        img.decoding = 'async';
        img.src = isHttp
          ? target
          : isCccPasted
            ? '/api/pasted-image?path=' + encodeURIComponent(target) + (sid ? '&session_id=' + encodeURIComponent(sid) : '')
            : '/api/local-image?path=' + encodeURIComponent(target);
        img.onerror = () => {
          const placeholder = document.createElement('div');
          placeholder.className = 'sidebar-file-icon-placeholder';
          placeholder.textContent = row.categoryIcon || '📷';
          if (img.parentNode) {
            img.parentNode.replaceChild(placeholder, img);
          }
        };
        rowEl.appendChild(img);
      } else {
        const placeholder = document.createElement('div');
        placeholder.className = 'sidebar-file-icon-placeholder';
        placeholder.textContent = row.categoryIcon || '📷';
        rowEl.appendChild(placeholder);
      }
    } else {
      const placeholder = document.createElement('div');
      placeholder.className = 'sidebar-file-icon-placeholder';
      placeholder.textContent = row.categoryIcon || '📄';
      rowEl.appendChild(placeholder);
    }

    // Info containing filename and path
    const info = document.createElement('div');
    info.className = 'sidebar-file-info';

    const name = document.createElement('div');
    name.className = 'sidebar-file-name';
    name.textContent = row.label;
    info.appendChild(name);

    const pathEl = document.createElement('div');
    pathEl.className = 'sidebar-file-path';
    pathEl.textContent = row.target;
    pathEl.title = 'Click to copy path';
    pathEl.addEventListener('click', (e) => {
      e.stopPropagation();
      navigator.clipboard.writeText(row.target).then(() => {
        const orig = pathEl.textContent;
        pathEl.textContent = 'copied';
        const origColor = pathEl.style.color;
        pathEl.style.color = 'var(--accent, #58a6ff)';
        setTimeout(() => {
          pathEl.textContent = orig;
          pathEl.style.color = origColor;
        }, 900);
      }).catch(() => {});
    });
    info.appendChild(pathEl);

    rowEl.appendChild(info);

    // Row click handles reveal-file or URL redirect
    if (row.kind === 'url') {
      rowEl.addEventListener('click', (e) => {
        if (e.target.closest('.sidebar-file-path')) return;
        window.open(row.target, '_blank', 'noopener,noreferrer');
      });
    } else {
      rowEl.addEventListener('click', async (e) => {
        if (e.target.closest('.sidebar-file-path')) return;

        const isMarkdown = row.category === 'markdown' || (typeof _isMarkdownPath === 'function' && _isMarkdownPath(row.target));
        if (isMarkdown) {
          try {
            await loadMarkdownIntoFileViewer(row.target, row.label);
          } catch (err) {
            sidebarShowFileToast(rowEl, err.message || 'load failed');
          }
          return;
        }
        if (_isVideoPath(row.target) || _isHtmlDocPath(row.target) || _isImageOrPdfPath(row.target)) {
          openFilePathInMediaLightbox(row.target, row.label, sharedState.currentSession);
          return;
        }

        try {
          const sid = sharedState.sessionIdByConv[sharedState.currentConversation] || (sharedState.currentSession && sharedState.currentSession.id) || '';
          const r = await fetch('/api/reveal-file', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: row.target, session_id: sid}),
          });
          if (!r.ok) {
            const j = await r.json().catch(() => ({error: 'open failed'}));
            sidebarShowFileToast(rowEl, j.error || ('HTTP ' + r.status));
          }
        } catch (err) {
          sidebarShowFileToast(rowEl, 'network error');
        }
      });
    }

    return rowEl;
  }

  function sidebarShowFileToast(rowEl, msg) {
    let info = rowEl.querySelector('.sidebar-file-info');
    if (!info) return;
    let toast = info.querySelector('.sidebar-file-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.className = 'sidebar-file-toast';
      info.appendChild(toast);
    }
    toast.textContent = msg;
    setTimeout(() => {
      if (toast.parentNode === info) info.removeChild(toast);
    }, 3000);
  }

  function ffcEscapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function ffcRenderRow(row, icon) {
    const div = document.createElement('div');
    div.className = 'ffc-row';

    const labelEl = document.createElement('div');
    labelEl.className = 'ffc-row-label';
    labelEl.innerHTML = '<span class="ffc-row-icon">' + icon + '</span>' +
                       '<span>' + ffcEscapeHtml(row.label) + '</span>';
    if (row.kind === 'url') {
      const a = document.createElement('a');
      a.href = row.target;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.style.color = 'inherit';
      a.style.textDecoration = 'none';
      a.appendChild(labelEl);
      div.appendChild(a);
    } else {
      div.appendChild(labelEl);
      div.addEventListener('click', async (e) => {
        if (e.target.classList.contains('ffc-row-target')) return; // don't double-trigger on path-copy
        if (_isMarkdownPath(row.target) && _canUseStatusRailViewer()) {
          try {
            closeFfcModal();
            await loadMarkdownIntoFileViewer(row.target, row.label);
          } catch (err) {
            ffcShowRowToast(div, err.message || 'load failed');
          }
          return;
        }
        if (_isVideoPath(row.target) || _isHtmlDocPath(row.target) || _isImageOrPdfPath(row.target)) {
          if (_canUseStatusRailViewer()) closeFfcModal();
          openFilePathInMediaLightbox(row.target, row.label, sharedState.currentSession);
          return;
        }
        try {
          const r = await fetch('/api/reveal-file', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: row.target, session_id: sharedState.currentSession && sharedState.currentSession.id}),
          });
          if (!r.ok) {
            const j = await r.json().catch(() => ({error: 'open failed'}));
            ffcShowRowToast(div, j.error || ('HTTP ' + r.status));
          }
        } catch (err) {
          ffcShowRowToast(div, 'network error');
        }
      });
    }

    const tgt = document.createElement('div');
    tgt.className = 'ffc-row-target';
    tgt.textContent = row.target;
    tgt.title = 'Click to copy';
    tgt.addEventListener('click', (e) => {
      e.stopPropagation();
      navigator.clipboard.writeText(row.target).then(() => {
        const orig = tgt.textContent;
        tgt.textContent = 'copied';
        setTimeout(() => { tgt.textContent = orig; }, 900);
      }).catch(() => {});
    });
    div.appendChild(tgt);

    return div;
  }

  function ffcShowRowToast(rowEl, msg) {
    let toast = rowEl.querySelector('.ffc-row-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.className = 'ffc-row-toast';
      rowEl.appendChild(toast);
    }
    toast.textContent = msg;
    setTimeout(() => {
      if (toast.parentNode === rowEl) rowEl.removeChild(toast);
    }, 3000);
  }

  function openFfcModal(data) {
    const overlay = document.getElementById('ffcOverlay');
    const body    = document.getElementById('ffcBody');
    const footer  = document.getElementById('ffcFooter');
    if (!overlay || !body || !footer) return;
    body.innerHTML = '';

    if (!data || !data.count) {
      const empty = document.createElement('div');
      empty.className = 'ffc-empty';
      empty.textContent = 'No files mentioned in this conversation.';
      body.appendChild(empty);
    } else {
      for (const cat of sharedState.FFC_CATEGORY_ORDER) {
        const rows = (data.groups || {})[cat.key];
        if (!rows || !rows.length) continue;
        const section = document.createElement('div');
        section.className = 'ffc-section';
        const title = document.createElement('div');
        title.className = 'ffc-section-title';
        title.textContent = cat.label + ' (' + rows.length + ')';
        section.appendChild(title);
        for (const row of rows) {
          section.appendChild(ffcRenderRow(row, cat.icon));
        }
        body.appendChild(section);
      }
    }

    if (data && data.truncated) {
      footer.hidden = false;
      footer.textContent = 'Showing first 500 - conversation contains more.';
    } else {
      footer.hidden = true;
      footer.textContent = '';
    }

    overlay.hidden = false;
    document.addEventListener('keydown', _ffcEscHandler);
  }

  function closeFfcModal() {
    const overlay = document.getElementById('ffcOverlay');
    if (overlay) overlay.hidden = true;
    document.removeEventListener('keydown', _ffcEscHandler);
  }

  function _ffcEscHandler(e) {
    if ((0, sharedState.isImeKey)(e)) return;
    if (e.key === 'Escape') closeFfcModal();
  }

  async function renderSpawnStatsPanel(sid, $view, opts) {
    if (!sid || !$view || !(0, sharedState.spawnStatsEnabled)()) return;
    let data = null;
    try {
      const r = await fetch('/api/session/spawn-timeline?session_id=' + encodeURIComponent(sid),
        { cache: 'no-store', signal: (0, sharedState.convScopeSignal)(), cccPriority: sharedState.spawnStatsT0.has(sid) ? 'user' : undefined });
      data = await r.json();
    } catch (_) { return; }
    if (!data || !data.ok || !data.timeline || !$view.isConnected) return;
    const marks = Object.assign({}, data.timeline.marks || {});
    const local = sharedState.spawnStatsLocalMarks.get(sid);
    if (local) Object.assign(marks, local);
    if (!Object.keys(marks).length) return;
    const antigravity = data.timeline.engine === 'antigravity';
    const statRows = antigravity ? [
      ['process_started', 'AGY process started'],
      ['spawn_response_sent', 'CCC answered the browser'],
      ['client_response_received', 'Browser got the response'],
      ['antigravity_id_observed', 'CCC found the AGY conversation'],
      ['antigravity_activity_read', 'CCC read the first AGY activity'],
      ['client_first_visible_activity', 'First AGY activity painted'],
      ['antigravity_text_read', 'CCC read the first AGY reply text'],
      ['client_first_visible_output', 'First assistant text painted'],
      ['client_first_paint', 'Transcript first painted'],
      ['client_row_resolved', 'Row left "spawning"'],
    ] : data.timeline.engine === 'claude'
      ? sharedState.CLAUDE_SPAWN_STAT_ROWS : sharedState.SPAWN_STAT_ROWS;
    const rows = statRows
      .filter(([k]) => marks[k] != null)
      .map(([k, label]) => '<div class="spawn-stat-row"><span class="spawn-stat-label">'
        + (0, sharedState.escapeHtml)(label) + '</span><span class="spawn-stat-val">'
        + (0, sharedState.fmtStatMs)(marks[k]) + '</span></div>').join('');
    // "Visible" is the honest headline: the number the user actually felt.
    const visible = (antigravity ? marks.client_first_visible_activity : marks.client_first_visible_output) ?? null;
    const visibleLabel = antigravity ? 'first visible activity' : 'first visible output';
    const hiddenMs = antigravity ? marks.client_first_visible_activity_hidden_ms : marks.client_first_visible_output_hidden_ms;
    const unselectedMs = antigravity ? marks.client_first_visible_activity_unselected_ms : marks.client_first_visible_output_unselected_ms;
    const existing = $view.querySelector('.spawn-stats');
    const forceOpen = !!(opts && opts.open) || ((antigravity || data.timeline.engine === 'codex') && visible == null);
    const html = '<details class="spawn-stats"' + ((forceOpen || (existing && existing.open)) ? ' open' : '') + '>'
      + '<summary class="spawn-stats-summary">&#9201; Session start: '
      + (visible == null ? '<strong>Waiting for ' + visibleLabel + '</strong>'
        : '<strong>' + (0, sharedState.fmtStatMs)(visible) + '</strong> to ' + visibleLabel)
      + '<span class="spawn-stats-sub"> &middot; accepted in '
      + (0, sharedState.fmtStatMs)(marks.spawn_response_sent) + '</span>'
      + '<button type="button" class="spawn-stats-copy" title="Copy timeline">&#128203;</button></summary>'
      + '<div class="spawn-stats-body">' + rows
      + '<div class="spawn-stats-note">Times are from the browser submit action. '
      + (hiddenMs || unselectedMs
        ? 'Includes ' + (0, sharedState.fmtStatMs)(hiddenMs || 0) + ' with the tab hidden and '
          + (0, sharedState.fmtStatMs)(unselectedMs || 0) + ' viewing another session. ' : '')
      + 'Hide with <code>localStorage[\'ccc-spawn-stats\']=\'0\'</code>.</div></div></details>';
    if (existing) {
      existing.outerHTML = html;
    } else {
      $view.insertAdjacentHTML('afterbegin', html);
    }
    const $panel = $view.querySelector('.spawn-stats');
    const $copyBtn = $panel && $panel.querySelector('.spawn-stats-copy');
    if ($copyBtn) {
      $copyBtn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const lines = statRows
          .filter(([k]) => marks[k] != null)
          .map(([k, label]) => label + ': ' + (0, sharedState.fmtStatMs)(marks[k]));
        const text = 'Session start: ' + (0, sharedState.fmtStatMs)(visible) + ' to ' + visibleLabel + ' '
          + '(accepted in ' + (0, sharedState.fmtStatMs)(marks.spawn_response_sent) + ')\n' + lines.join('\n');
        const ok = await copyTextValue(text);
        (0, sharedState.showOpToast)(ok ? 'Timeline copied' : 'Copy failed', ok ? 'success' : 'error');
      });
    }
  }

  function presentationFeatureEnabled() {
    return (0, sharedState.ff)('presentation');
  }

  function normalizePresentationMode(mode) {
    const value = String(mode == null ? '' : mode).toLowerCase();
    if (value === '3' || value === 'mode3' || value === 'mode-3') return '3';
    return value === '1' || value === '2' || value === 'present' ? '2' : 'off';
  }

  function presentationModeState() {
    try {
      const parsed = JSON.parse(localStorage.getItem(sharedState.PRESENTATION_MODE_BY_SESSION_KEY) || 'null');
      if (!parsed || parsed.version !== 1 || !parsed.modes || typeof parsed.modes !== 'object') {
        return { version: 1, modes: {} };
      }
      const modes = {};
      Object.keys(parsed.modes).forEach(sessionId => {
        if (!sessionId) return;
        const raw = String(parsed.modes[sessionId] == null ? '' : parsed.modes[sessionId]).toLowerCase();
        if (raw === 'off' || raw === '1' || raw === '2' || raw === '3' || raw === 'present'
            || raw === 'mode3' || raw === 'mode-3') {
          modes[sessionId] = normalizePresentationMode(raw);
        }
      });
      return { version: 1, modes };
    } catch (_) {
      return { version: 1, modes: {} };
    }
  }

  function presentationModeForSession(sessionId) {
    const id = String(sessionId || '');
    if (!id) return 'off';
    const state = presentationModeState();
    return Object.prototype.hasOwnProperty.call(state.modes, id)
      ? normalizePresentationMode(state.modes[id])
      : 'off';
  }

  function presentationConversationIdForPane(paneId) {
    const paneState = (0, sharedState.paneByPaneId)(paneId || (0, sharedState.activePaneId)());
    return String((paneState && paneState.conversationId) || '');
  }

  function presentationSessionIdForPane(paneId) {
    const paneState = (0, sharedState.paneByPaneId)(paneId || (0, sharedState.activePaneId)());
    const conversationId = String((paneState && paneState.conversationId) || '');
    return String(
      (paneState && paneState.currentSession && paneState.currentSession.id)
      || sharedState.sessionIdByConv[conversationId]
      || conversationId
      || ''
    );
  }

  function paginatePresentationItems(items, budget) {
    const source = Array.isArray(items) ? items : [];
    const limit = Math.max(1, Number(budget) || 1);
    const pages = [];
    let page = [];
    let pageWeight = 0;
    const flush = () => {
      if (!page.length) return;
      pages.push(page);
      page = [];
      pageWeight = 0;
    };
    for (let i = 0; i < source.length; i++) {
      const item = source[i];
      const group = [item];
      let groupWeight = Math.max(1, Number(item && item.weight) || 1);
      if (item && item.keepWithNext && i + 1 < source.length) {
        const next = source[++i];
        group.push(next);
        groupWeight += Math.max(1, Number(next && next.weight) || 1);
      }
      const breakBefore = group.some(groupItem => groupItem && groupItem.breakBefore);
      if (page.length && breakBefore) flush();
      if (page.length && pageWeight + groupWeight > limit) flush();
      page.push(...group);
      pageWeight += groupWeight;
      if (groupWeight >= limit) flush();
    }
    flush();
    return pages;
  }

  function presentationItemGroups(items) {
    const source = Array.isArray(items) ? items : [];
    const groups = [];
    for (let i = 0; i < source.length; i++) {
      const group = [source[i]];
      if (source[i] && source[i].keepWithNext && i + 1 < source.length) {
        group.push(source[++i]);
      }
      groups.push(group);
    }
    return groups;
  }

  function paginatePresentationGroups(groups, fits, fitsFinal) {
    const pages = [];
    let pageGroups = [];
    (Array.isArray(groups) ? groups : []).forEach(group => {
      const candidate = pageGroups.concat([group]);
      const items = candidate.flat();
      if (pageGroups.length && !fits(items, pages.length)) {
        pages.push(pageGroups);
        pageGroups = [group];
      } else {
        pageGroups = candidate;
      }
    });
    if (pageGroups.length) pages.push(pageGroups);
    if (typeof fitsFinal === 'function' && pages.length) {
      while (pages[pages.length - 1].length > 1
          && !fitsFinal(pages[pages.length - 1].flat(), pages.length - 1)) {
        const moved = pages[pages.length - 1].pop();
        pages.push([moved]);
      }
    }
    return pages.map(page => page.flat());
  }

  function presentationBlockWeight(node) {
    if (!node) return 1;
    const tag = String(node.tagName || '').toLowerCase();
    const text = String(node.textContent || '').trim();
    if (tag === 'table' || (node.matches && node.matches('.md-table-wrap'))) {
      return Math.max(8, 4 + node.querySelectorAll('tr').length * 2);
    }
    if (tag === 'pre' || (node.matches && node.matches('.code-block,.mermaid-block'))) {
      return Math.max(8, 4 + text.split('\n').length);
    }
    if (node.querySelector && node.querySelector('img,.msg-image,.mermaid-svg')) return 12;
    if (tag === 'ul' || tag === 'ol') return Math.max(5, 2 + node.querySelectorAll('li').length * 2);
    if (/^h[1-6]$/.test(tag)) return 2;
    if (tag === 'blockquote') return Math.max(4, Math.ceil(text.length / 65) + 2);
    return Math.max(2, Math.ceil(text.length / 65) + 1);
  }

  function presentationPageBudget(view) {
    const height = view && view.clientHeight ? view.clientHeight : 620;
    return Math.max(9, Math.min(18, Math.floor((height - 210) / 42)));
  }

  function presentationClone(node) {
    const clone = node.cloneNode(true);
    if (clone.removeAttribute) clone.removeAttribute('id');
    if (clone.querySelectorAll) clone.querySelectorAll('[id]').forEach(el => el.removeAttribute('id'));
    return clone;
  }

  function presentationSourceRoot(view, node) {
    let current = node && (node.nodeType === 3 ? node.parentElement : node);
    if (!view || !current || current === view) return null;
    while (current && current.parentElement !== view) current = current.parentElement;
    if (!current || current.parentElement !== view) return null;
    if (current.classList && current.classList.contains('conv-presentation-stage')) return null;
    return current;
  }

  function presentationCloneForProjection(source, prefix) {
    const clone = source.cloneNode(true);
    const sourceElements = [source].concat(Array.from(source.querySelectorAll ? source.querySelectorAll('*') : []));
    const cloneElements = [clone].concat(Array.from(clone.querySelectorAll ? clone.querySelectorAll('*') : []));
    const idMap = new Map();
    const safePrefix = String(prefix || 'presentation-projection').replace(/[^a-zA-Z0-9_-]/g, '-');
    sourceElements.forEach((sourceElement, index) => {
      const cloneElement = cloneElements[index];
      if (!cloneElement) return;
      ['value', 'checked', 'indeterminate', 'selectedIndex', 'open', 'scrollTop'].forEach(property => {
        if (!(property in sourceElement) || !(property in cloneElement)) return;
        try { cloneElement[property] = sourceElement[property]; } catch (_) {}
      });
      const sourceId = sourceElement.getAttribute && sourceElement.getAttribute('id');
      if (sourceId) {
        const projectedId = safePrefix + '-' + sourceId;
        idMap.set(sourceId, projectedId);
        cloneElement.setAttribute('id', projectedId);
      }
    });
    const rewriteTokens = (value) => String(value || '').split(/\s+/).filter(Boolean)
      .map(token => idMap.get(token) || token).join(' ');
    cloneElements.forEach(element => {
      ['for', 'aria-labelledby', 'aria-describedby', 'aria-controls'].forEach(attribute => {
        if (!element.hasAttribute || !element.hasAttribute(attribute)) return;
        element.setAttribute(attribute, rewriteTokens(element.getAttribute(attribute)));
      });
      ['href', 'xlink:href'].forEach(attribute => {
        if (!element.hasAttribute || !element.hasAttribute(attribute)) return;
        const value = element.getAttribute(attribute) || '';
        if (value.startsWith('#') && idMap.has(value.slice(1))) {
          element.setAttribute(attribute, '#' + idMap.get(value.slice(1)));
        }
      });
    });
    return clone;
  }

  function presentationRootsAfterLatestAnswer(view) {
    const directChildren = Array.from((view && view.children) || [])
      .filter(node => !(node.classList && node.classList.contains('conv-presentation-stage')));
    let boundary = -1;
    directChildren.forEach((node, index) => {
      if (node.matches && node.matches('.event.assistant')
          && !node.classList.contains('tool-only')
          && node.querySelector('.assistant-text')) boundary = index;
    });
    return directChildren.slice(boundary + 1);
  }

  function presentationListItems(list) {
    const tag = String((list && list.tagName) || '').toLowerCase();
    const ordered = tag === 'ol';
    const baseStart = ordered ? Math.max(1, Number(list.getAttribute('start')) || 1) : 1;
    return Array.from((list && list.children) || [])
      .filter(item => String(item.tagName || '').toLowerCase() === 'li')
      .map((item, index) => {
        const wrapper = list.cloneNode(false);
        wrapper.removeAttribute('id');
        wrapper.classList.add('conv-presentation-list-fragment');
        if (ordered) {
          const explicitValue = Number(item.getAttribute('value'));
          wrapper.setAttribute('start', String(Number.isFinite(explicitValue) && explicitValue > 0
            ? explicitValue : baseStart + index));
        }
        wrapper.appendChild(presentationClone(item));
        return {
          node: wrapper,
          weight: Math.max(3, presentationBlockWeight(item) + 1),
          // A numbered point is a slide-sized idea. Unordered lists stay
          // together when they fit, but the list itself never dangles after
          // unrelated prose at the bottom of a slide.
          breakBefore: index === 0,
        };
      });
  }

  function presentationAnswerBlocks(eventEl) {
    const blocks = [];
    const containers = eventEl && eventEl.querySelectorAll
      ? Array.from(eventEl.querySelectorAll(':scope > .assistant-text')) : [];
    containers.forEach(container => {
      const children = Array.from(container.children || []);
      if (!children.length && String(container.textContent || '').trim()) {
        const fallback = document.createElement('div');
        fallback.textContent = container.textContent;
        children.push(fallback);
      }
      children.forEach(child => {
        const tag = String(child.tagName || '').toLowerCase();
        if (tag === 'ul' || tag === 'ol') {
          blocks.push(...presentationListItems(child));
          return;
        }
        blocks.push({
          node: presentationClone(child),
          weight: presentationBlockWeight(child),
          keepWithNext: /^h[1-6]$/.test(tag),
        });
      });
    });
    blocks.forEach((block, index) => { block.key = 'item-' + index; });
    return blocks;
  }

  function presentationPromptText(eventEl) {
    if (!eventEl || !eventEl.querySelector) return '';
    const message = eventEl.querySelector('.user-msg');
    if (!message) return '';
    return String(message.getAttribute('data-raw-text') || message.textContent || '')
      .replace(/\s+/g, ' ').trim();
  }

  function presentationTurns(view) {
    const turns = [];
    let prompt = '';
    let pendingDetails = [];
    let answerNumber = 0;
    const children = Array.from((view && view.children) || []);
    children.forEach(node => {
      if (!node.classList || node.classList.contains('conv-presentation-stage')) return;
      if (node.matches('.event.user_text:not(.task-notification-event)')) {
        const nextPrompt = presentationPromptText(node);
        if (nextPrompt) prompt = nextPrompt;
        pendingDetails = [];
        return;
      }
      if (node.matches('.tool-call-group')) {
        pendingDetails.push(node);
        return;
      }
      if (node.matches('.event.assistant')) {
        const blocks = presentationAnswerBlocks(node);
        if (!blocks.length || node.classList.contains('is-noop-ack')) {
          if (node.classList.contains('tool-only')) pendingDetails.push(node);
          return;
        }
        answerNumber += 1;
        const extras = Array.from(node.children || []).filter(child =>
          !child.classList.contains('assistant-text')
          && !child.classList.contains('line-num')
          && !child.classList.contains('msg-ts')
          && !child.classList.contains('assistant-message-actions')
          && !child.classList.contains('event-model-meta')
          && !child.classList.contains('event-token-chips')
        );
        turns.push({
          answerNumber,
          key: node.dataset.jsonlLine || node.dataset.msgId || ('answer-' + answerNumber),
          prompt,
          blocks,
          details: pendingDetails.concat(extras),
          presentationArtifact: node._presentationArtifact || null,
          presentationArtifactError: node.dataset.presentationArtifactError || '',
          live: false,
        });
        pendingDetails = [];
        return;
      }
      if (node.matches('.stream-bubble:not(.stream-bubble-subagent)')) {
        const streamBlocks = [];
        node.querySelectorAll('.assistant-text').forEach(container => {
          const children = Array.from(container.children || []);
          children.forEach(child => streamBlocks.push({
            node: presentationClone(child),
            weight: presentationBlockWeight(child),
            keepWithNext: /^h[1-6]$/i.test(child.tagName || ''),
          }));
        });
        streamBlocks.forEach((block, index) => { block.key = 'item-' + index; });
        if (streamBlocks.length) {
          answerNumber += 1;
          turns.push({
            answerNumber,
            key: 'live-' + (node.dataset.msgId || answerNumber),
            prompt,
            blocks: streamBlocks,
            details: [],
            live: true,
          });
        }
      }
    });
    return turns;
  }

  function buildPresentationDetails(turn) {
    if (!turn.details || !turn.details.length) return null;
    const details = document.createElement('details');
    details.className = 'conv-presentation-details';
    details.open = (0, sharedState.convVerboseOn)();
    const summary = document.createElement('summary');
    summary.textContent = 'Details · ' + turn.details.length;
    details.appendChild(summary);
    const body = document.createElement('div');
    body.className = 'conv-presentation-details-body';
    turn.details.forEach(node => body.appendChild(presentationClone(node)));
    details.appendChild(body);
    return details;
  }

  function buildPresentationSlide(turn, items, partIndex, partCount, mode) {
    const slide = document.createElement('article');
    slide.className = 'conv-presentation-slide' + (turn.live ? ' is-live' : '');
    slide.dataset.presentationKey = turn.key + ':' + partIndex;
    slide.dataset.answerKey = turn.key;
    slide.dataset.answerIndex = String(turn.answerNumber);
    slide.dataset.partIndex = String(partIndex);
    slide.dataset.partCount = String(partCount);
    slide.dataset.presentationItemKeys = items
      .map(item => String(turn.key || '') + ':' + String((item && item.key) || ''))
      .filter(Boolean)
      .join(',');

    const header = document.createElement('header');
    header.className = 'conv-presentation-slide-header';
    const eyebrow = document.createElement('span');
    eyebrow.className = 'conv-presentation-eyebrow';
    eyebrow.textContent = turn.live
      ? 'Answer ' + turn.answerNumber + ' · Live'
      : ('Answer ' + turn.answerNumber + (mode === '2' ? ' · ' + (partIndex + 1) + ' of ' + partCount : ''));
    header.appendChild(eyebrow);
    if (turn.live) {
      const live = document.createElement('span');
      live.className = 'conv-presentation-live';
      live.textContent = 'streaming';
      header.appendChild(live);
    }
    slide.appendChild(header);

    if (partIndex === 0 && turn.prompt) {
      const prompt = document.createElement('div');
      prompt.className = 'conv-presentation-prompt';
      const label = document.createElement('span');
      label.textContent = 'Prompt';
      const text = document.createElement('p');
      text.textContent = turn.prompt;
      prompt.append(label, text);
      slide.appendChild(prompt);
    }

    const body = document.createElement('div');
    body.className = 'conv-presentation-body assistant-text';
    items.forEach(item => body.appendChild(presentationClone(item.node)));
    slide.appendChild(body);

    if (partIndex === partCount - 1) {
      const details = buildPresentationDetails(turn);
      if (details) slide.appendChild(details);
    }
    return slide;
  }

  function mode3Text(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = String(value == null ? '' : value);
    return node;
  }

  function mode3StringList(items, className) {
    const list = document.createElement('ul');
    list.className = className || 'conv-mode3-list';
    (Array.isArray(items) ? items : []).forEach(value => {
      list.appendChild(mode3Text('li', '', value));
    });
    return list;
  }

  function buildMode3Slide(turn, artifact, source, slideIndex, slideCount) {
    const slide = document.createElement('article');
    const layout = String(source.layout || 'statement');
    slide.className = 'conv-presentation-slide conv-mode3-slide layout-' + layout;
    slide.dataset.presentationKey = turn.key + ':mode3:' + source.id;
    slide.dataset.answerKey = turn.key;
    slide.dataset.artifactSlideId = String(source.id || '');
    slide.dataset.answerIndex = String(turn.answerNumber);
    slide.dataset.partIndex = String(slideIndex);
    slide.dataset.partCount = String(slideCount);
    slide.dataset.mode3Theme = String(artifact.theme || 'neutral');

    const header = document.createElement('header');
    header.className = 'conv-mode3-header';
    const heading = document.createElement('div');
    heading.className = 'conv-mode3-heading';
    if (source.eyebrow) heading.appendChild(mode3Text('span', 'conv-presentation-eyebrow', source.eyebrow));
    heading.appendChild(mode3Text('h2', 'conv-mode3-title', source.title));
    if (source.subtitle) heading.appendChild(mode3Text('p', 'conv-mode3-subtitle', source.subtitle));
    header.appendChild(heading);
    header.appendChild(mode3Text('span', 'conv-mode3-deck-label',
      String(artifact.deck_title || ('Answer ' + turn.answerNumber))));
    slide.appendChild(header);

    const body = document.createElement('div');
    body.className = 'conv-mode3-body';
    if (layout === 'statement') {
      body.appendChild(mode3Text('p', 'conv-mode3-statement', source.statement));
    } else if (layout === 'bullets') {
      body.appendChild(mode3StringList(source.items, 'conv-mode3-list'));
    } else if (layout === 'steps') {
      const list = document.createElement('ol');
      list.className = 'conv-mode3-steps';
      (source.items || []).forEach(item => {
        const row = document.createElement('li');
        row.append(
          mode3Text('strong', 'conv-mode3-step-label', item.label),
          mode3Text('span', 'conv-mode3-step-text', item.text),
        );
        list.appendChild(row);
      });
      body.appendChild(list);
    } else if (layout === 'comparison') {
      const comparison = document.createElement('div');
      comparison.className = 'conv-mode3-comparison';
      ['left', 'right'].forEach(sideName => {
        const side = document.createElement('section');
        side.className = 'conv-mode3-comparison-side is-' + sideName;
        side.append(
          mode3Text('h3', '', source[sideName].title),
          mode3StringList(source[sideName].items, 'conv-mode3-list'),
        );
        comparison.appendChild(side);
      });
      body.appendChild(comparison);
    } else if (layout === 'metrics') {
      const metrics = document.createElement('div');
      metrics.className = 'conv-mode3-metrics';
      (source.items || []).forEach(item => {
        const metric = document.createElement('div');
        metric.className = 'conv-mode3-metric';
        metric.append(
          mode3Text('strong', 'conv-mode3-metric-value', item.value),
          mode3Text('span', 'conv-mode3-metric-label', item.label),
        );
        metrics.appendChild(metric);
      });
      body.appendChild(metrics);
    } else if (layout === 'quote') {
      const quote = document.createElement('blockquote');
      quote.className = 'conv-mode3-quote';
      quote.appendChild(mode3Text('p', '', source.quote));
      if (source.attribution) quote.appendChild(mode3Text('cite', '', source.attribution));
      body.appendChild(quote);
    } else if (layout === 'code') {
      const pre = document.createElement('pre');
      pre.className = 'conv-mode3-code';
      pre.appendChild(mode3Text('code', '', source.code));
      body.appendChild(pre);
      if (source.caption) body.appendChild(mode3Text('p', 'conv-mode3-caption', source.caption));
    } else if (layout === 'summary') {
      body.appendChild(mode3Text('p', 'conv-mode3-takeaway', source.takeaway));
      if (source.actions && source.actions.length) {
        body.appendChild(mode3StringList(source.actions, 'conv-mode3-actions'));
      }
    }
    slide.appendChild(body);
    return slide;
  }

  function mode3SlidesForTurn(turn) {
    const artifact = turn && turn.presentationArtifact;
    if (!artifact || !Array.isArray(artifact.slides) || !artifact.slides.length) return [];
    return artifact.slides.map((source, index) => (
      buildMode3Slide(turn, artifact, source, index, artifact.slides.length)
    ));
  }

  function ensurePresentationMeasureSurface(stage) {
    let surface = stage.querySelector(':scope > .conv-presentation-measure');
    if (surface) return surface;
    surface = document.createElement('div');
    surface.className = 'conv-presentation-measure';
    surface.setAttribute('aria-hidden', 'true');
    stage.appendChild(surface);
    return surface;
  }

  function presentationCandidateFits(view, turn, items, pageIndex, includeDetails) {
    const stage = (0, sharedState.ensurePresentationStage)(view);
    const slot = stage.querySelector(':scope > .conv-presentation-slide-slot');
    const width = slot && slot.clientWidth;
    const height = slot && slot.clientHeight;
    if (!width || !height) return null;
    const surface = ensurePresentationMeasureSurface(stage);
    surface.style.width = width + 'px';
    surface.style.height = height + 'px';
    const partCount = includeDetails ? pageIndex + 1 : pageIndex + 2;
    const slide = buildPresentationSlide(turn, items, pageIndex, partCount, '2');
    surface.replaceChildren(slide);
    const body = slide.querySelector('.conv-presentation-body');
    const fits = !!body && body.scrollHeight <= body.clientHeight + 2;
    surface.replaceChildren();
    return fits;
  }

  function paginatePresentationItemsMeasured(view, turn) {
    const stage = (0, sharedState.ensurePresentationStage)(view);
    const slot = stage.querySelector(':scope > .conv-presentation-slide-slot');
    if (!slot || slot.clientWidth < 1 || slot.clientHeight < 1) return null;
    const groups = presentationItemGroups(turn.blocks);
    return paginatePresentationGroups(
      groups,
      (items, pageIndex) => presentationCandidateFits(
        view, turn, items, pageIndex, false
      ),
      (items, pageIndex) => presentationCandidateFits(
        view, turn, items, pageIndex, true
      ),
    );
  }

  function buildPresentationDeck(view, mode) {
    const deck = [];
    const budget = presentationPageBudget(view);
    presentationTurns(view).forEach(turn => {
      if (mode === '3' && !turn.live) {
        const authored = mode3SlidesForTurn(turn);
        if (authored.length) {
          deck.push(...authored);
          return;
        }
      }
      let pages;
      try {
        if ((mode === '2' || mode === '3') && !turn.live) {
          pages = paginatePresentationItemsMeasured(view, turn)
            || paginatePresentationItems(turn.blocks, budget);
        } else {
          pages = [turn.blocks];
        }
        if (!pages.length) pages = [turn.blocks];
      } catch (_) {
        // One malformed answer must not break the rest of the deck. Its
        // fallback is a single internally-scrollable slide.
        pages = [turn.blocks];
      }
      pages.forEach((items, index) => {
        const fallback = buildPresentationSlide(turn, items, index, pages.length, '2');
        if (mode === '3') {
          fallback.classList.add('is-mode3-fallback');
          fallback.dataset.presentationFallback = '1';
          const eyebrow = fallback.querySelector('.conv-presentation-eyebrow');
          if (eyebrow) eyebrow.textContent += ' · Transcript slides · AI deck unavailable';
        }
        deck.push(fallback);
      });
    });
    return deck;
  }

  function renderMode3BootstrapState(paneId, conversationId) {
    const pane = (0, sharedState.presentationPaneElement)(paneId);
    const view = (0, sharedState.getConvViewForPane)(paneId);
    if (!pane || !view) return;
    const stage = (0, sharedState.ensurePresentationStage)(view);
    let notice = stage.querySelector(':scope > .conv-mode3-status');
    if (!notice) {
      notice = document.createElement('div');
      notice.className = 'conv-mode3-status';
      notice.setAttribute('role', 'status');
      stage.appendChild(notice);
    }
    const mode = normalizePresentationMode(pane.dataset.presentationMode);
    const answer = (0, sharedState.latestCompletedPresentationAnswer)(view);
    if (mode !== '3' || !answer || answer._presentationArtifact) {
      notice.hidden = true;
      notice.replaceChildren();
      return;
    }
    const key = (0, sharedState.mode3BootstrapAnswerKey)(conversationId, answer);
    const entry = sharedState._mode3BootstrapByAnswer.get(key);
    notice.hidden = false;
    notice.replaceChildren();
    if (entry && entry.status === 'pending') {
      notice.classList.add('is-pending');
      notice.classList.remove('is-failed');
      const pulse = document.createElement('span');
      pulse.className = 'conv-mode3-status-pulse';
      pulse.setAttribute('aria-hidden', 'true');
      const text = document.createElement('span');
      text.textContent = 'Designing AI deck…';
      notice.append(pulse, text);
      return;
    }
    notice.classList.remove('is-pending');
    notice.classList.add('is-failed');
    const text = document.createElement('span');
    text.textContent = 'AI deck unavailable';
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.setAttribute('data-mode3-retry', '');
    retry.textContent = 'Retry';
    retry.addEventListener('click', () => {
      sharedState._mode3BootstrapByAnswer.delete(key);
      requestMode3Bootstrap(paneId, conversationId);
    });
    notice.append(text, document.createTextNode(' · '), retry);
  }

  async function requestMode3Bootstrap(paneId, conversationId) {
    const targetPaneId = paneId || (0, sharedState.activePaneId)();
    const pane = (0, sharedState.presentationPaneElement)(targetPaneId);
    const paneState = (0, sharedState.paneByPaneId)(targetPaneId);
    const view = (0, sharedState.getConvViewForPane)(targetPaneId);
    const id = String(conversationId || (paneState && paneState.conversationId) || '');
    if (!pane || !view || !id) return;
    const answer = (0, sharedState.latestCompletedPresentationAnswer)(view);
    if (!answer) return;
    const key = (0, sharedState.mode3BootstrapAnswerKey)(id, answer);
    if (answer._presentationArtifact) {
      sharedState._mode3BootstrapByAnswer.delete(key);
      renderMode3BootstrapState(targetPaneId, id);
      return;
    }
    const existing = sharedState._mode3BootstrapByAnswer.get(key);
    if (existing && existing.status === 'pending') return;
    sharedState._mode3BootstrapByAnswer.set(key, { status: 'pending' });
    renderMode3BootstrapState(targetPaneId, id);
    const sid = String(
      (paneState && paneState.currentSession && paneState.currentSession.id)
      || sharedState.sessionIdByConv[id]
      || id
    );
    try {
      const response = await fetch('/api/inject-input', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sid,
          text: '',
          mode: 'send',
          presentation_bootstrap: true,
        }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) {
        throw new Error(result.error || ('HTTP ' + response.status));
      }
    } catch (error) {
      sharedState._mode3BootstrapByAnswer.set(key, {
        status: 'failed',
        error: String((error && error.message) || error || 'Mode 3 bootstrap failed'),
      });
      renderMode3BootstrapState(targetPaneId, id);
    }
  }

  function syncPresentationToolbar(pane, mode, hasAnswers) {
    const toolbar = pane && pane.querySelector('[data-role="presentation-toolbar"]');
    if (!toolbar) return;
    const presentationEnabled = presentationFeatureEnabled();
    toolbar.classList.toggle('is-reader-only', !presentationEnabled);
    toolbar.hidden = !hasAnswers;
    const audioDock = pane.querySelector('.conv-reader-audio');
    if (audioDock) audioDock.hidden = !hasAnswers;
    toolbar.querySelectorAll('[data-presentation-mode]').forEach(button => {
      const active = button.dataset.presentationMode === mode;
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
      button.classList.toggle('is-active', active);
    });
    const cost = toolbar.querySelector('[data-role="presentation-cost"]');
    if (cost) cost.textContent = mode === '3' ? 'agent-authored deck' : '0 extra tokens';
    const progress = toolbar.querySelector('[data-role="presentation-progress"]');
    if (progress) progress.hidden = mode === 'off' || !hasAnswers;
    // Mobile: reflect the active mode in the collapsed toggle's label so
    // it's readable without opening the picker.
    const mobileToggle = toolbar.querySelector('[data-role="mobile-view-toggle"]');
    if (mobileToggle) {
      const label = mode === '2' ? 'Slides' : (mode === '3' ? 'Slides, agent-designed' : 'Chat');
      mobileToggle.textContent = 'View: ' + label;
    }
  }

  function presentationRootRefreshesDeck(root) {
    return !!(root && root.matches
      && root.matches('.event.assistant, .stream-bubble:not(.stream-bubble-subagent)'));
  }

  function presentationProjectionSignature(view) {
    return Array.from((view && view.children) || [])
      .filter(node => !(node.classList && node.classList.contains('conv-presentation-stage')))
      .map(node => node.outerHTML || node.textContent || '').join('\n');
  }

  function removePresentationProjectionEntry(state, id) {
    const entry = state && state.entries.get(id);
    if (!entry) return;
    if (entry.mirror && entry.mirror.remove) entry.mirror.remove();
    state.entries.delete(id);
  }

  function schedulePresentationProjectionFlush(view, roots, refreshDeck) {
    const state = view && view._presentationProjection;
    if (!state) return;
    const sourceRoots = Array.isArray(roots) ? roots : (roots ? [roots] : []);
    sourceRoots.forEach(root => {
      if (root && root.parentElement === view
          && !(root.classList && root.classList.contains('conv-presentation-stage'))) {
        state.dirtyRoots.add(root);
      }
      if (presentationRootRefreshesDeck(root)) state.refreshDeck = true;
    });
    if (refreshDeck) state.refreshDeck = true;
    if (state.raf) return;
    state.raf = requestAnimationFrame(() => flushPresentationProjection(view));
  }

  function flushPresentationProjection(view) {
    const state = view && view._presentationProjection;
    if (!state) return;
    state.raf = 0;
    const pane = (0, sharedState.presentationPaneElement)(state.paneId);
    if (!pane || normalizePresentationMode(pane.dataset.presentationMode) === 'off') return;
    const stage = (0, sharedState.ensurePresentationStage)(view);
    const region = (0, sharedState.ensurePresentationLiveRegion)(stage);
    const list = region.querySelector('.conv-presentation-live-list');
    const wasPinned = list.scrollHeight - list.scrollTop - list.clientHeight < 8;

    state.entries.forEach((entry, id) => {
      if (!view.contains(entry.source)) removePresentationProjectionEntry(state, id);
    });

    if (state.refreshDeck) {
      const currentTail = new Set(presentationRootsAfterLatestAnswer(view));
      state.entries.forEach((entry, id) => {
        if (!currentTail.has(entry.source)) removePresentationProjectionEntry(state, id);
      });
    }

    const dirtyRoots = Array.from(state.dirtyRoots);
    state.dirtyRoots.clear();
    dirtyRoots.forEach(source => {
      if (!view.contains(source)) return;
      let id = state.sourceIds.get(source);
      if (!id) {
        id = 'live-' + state.nextId++;
        state.sourceIds.set(source, id);
      }
      let entry = state.entries.get(id);
      let mirror;
      try {
        mirror = presentationCloneForProjection(source, 'presentation-' + state.paneId + '-' + id);
      } catch (_) {
        mirror = document.createElement('div');
        mirror.className = 'conv-presentation-projection-fallback';
        mirror.dataset.sourceClass = String(source.className || '');
        mirror.textContent = String(source.innerText || source.textContent || 'Update');
      }
      let wrapper = entry && entry.mirror;
      if (!wrapper) {
        wrapper = document.createElement('div');
        wrapper.className = 'conv-presentation-live-item';
        wrapper.dataset.presentationProjectionId = id;
      }
      wrapper.replaceChildren(mirror);
      list.appendChild(wrapper);
      state.entries.set(id, { source, mirror: wrapper });
    });

    region.hidden = state.entries.size === 0;
    const count = region.querySelector('[data-presentation-live-count]');
    if (count) count.textContent = state.entries.size ? String(state.entries.size) : '';
    if (wasPinned) list.scrollTop = list.scrollHeight;

    const refreshDeck = state.refreshDeck;
    state.refreshDeck = false;
    if (refreshDeck) refreshPresentationForPane(state.paneId, { followTail: true });
  }

  function ensurePresentationProjection(view, paneId) {
    if (!view) return null;
    const existing = view._presentationProjection;
    if (existing && existing.paneId === paneId) return existing;
    if (existing) disconnectPresentationProjection(view);
    const state = {
      paneId,
      sourceIds: new WeakMap(),
      entries: new Map(),
      dirtyRoots: new Set(),
      nextId: 1,
      raf: 0,
      refreshDeck: false,
      observer: null,
      poll: null,
      signature: presentationProjectionSignature(view),
    };
    view._presentationProjection = state;
    if (typeof MutationObserver === 'function') {
      state.observer = new MutationObserver(records => {
        let needsFlush = false;
        records.forEach(record => {
          const targetElement = record.target && (record.target.nodeType === 3
            ? record.target.parentElement : record.target);
          if (targetElement && targetElement.closest
              && targetElement.closest('.conv-presentation-stage')) return;
          const root = presentationSourceRoot(view, record.target);
          if (root) {
            state.dirtyRoots.add(root);
            if (presentationRootRefreshesDeck(root)) state.refreshDeck = true;
          }
          if (record.type === 'childList') {
            Array.from(record.addedNodes || []).forEach(node => {
              const addedRoot = presentationSourceRoot(view, node);
              if (!addedRoot) return;
              state.dirtyRoots.add(addedRoot);
              if (presentationRootRefreshesDeck(addedRoot)) state.refreshDeck = true;
            });
          }
          needsFlush = true;
        });
        if (needsFlush) schedulePresentationProjectionFlush(view);
      });
      state.observer.observe(view, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
      });
    } else {
      state.poll = setInterval(() => {
        const signature = presentationProjectionSignature(view);
        if (signature === state.signature) return;
        state.signature = signature;
        schedulePresentationProjectionFlush(view, Array.from(view.children || []), true);
      }, 250);
    }
    schedulePresentationProjectionFlush(view, presentationRootsAfterLatestAnswer(view));
    return state;
  }

  function disconnectPresentationProjection(view) {
    const state = view && view._presentationProjection;
    if (!state) return;
    if (state.observer) state.observer.disconnect();
    if (state.poll) clearInterval(state.poll);
    if (state.raf) cancelAnimationFrame(state.raf);
    state.entries.forEach((entry, id) => removePresentationProjectionEntry(state, id));
    view._presentationProjection = null;
  }

  function presentationRefreshIndex(deck, previousDeck, previousIndex) {
    const slides = Array.isArray(deck) ? deck : [];
    const oldSlides = Array.isArray(previousDeck) ? previousDeck : [];
    if (!slides.length) return 0;
    if (!oldSlides.length) return slides.length - 1;
    const oldIndex = Math.max(0, Math.min(oldSlides.length - 1, Number(previousIndex) || 0));
    const oldSlide = oldSlides[oldIndex] || {};
    const oldData = oldSlide.dataset || {};
    const oldLastData = (oldSlides[oldSlides.length - 1] || {}).dataset || {};
    const newLastData = (slides[slides.length - 1] || {}).dataset || {};
    if (oldIndex === oldSlides.length - 1
        && String(oldLastData.answerKey || '') !== String(newLastData.answerKey || '')) {
      const newAnswer = String(newLastData.answerKey || '');
      const first = slides.findIndex(item => String(((item || {}).dataset || {}).answerKey || '') === newAnswer);
      return first >= 0 ? first : slides.length - 1;
    }
    const artifactId = String(oldData.artifactSlideId || '');
    if (artifactId) {
      const sameArtifact = slides.findIndex(item => {
        const data = ((item || {}).dataset || {});
        return String(data.answerKey || '') === String(oldData.answerKey || '')
          && String(data.artifactSlideId || '') === artifactId;
      });
      if (sameArtifact >= 0) return sameArtifact;
    }
    const itemKey = String(oldData.presentationItemKeys || '').split(',')[0];
    if (itemKey) {
      const containing = slides.findIndex(item => String((((item || {}).dataset || {}).presentationItemKeys || ''))
        .split(',').includes(itemKey));
      if (containing >= 0) return containing;
    }
    const exactKey = String(oldData.presentationKey || '');
    if (exactKey) {
      const exact = slides.findIndex(item => String((((item || {}).dataset || {}).presentationKey || '')) === exactKey);
      if (exact >= 0) return exact;
    }
    return Math.max(0, Math.min(slides.length - 1, oldIndex));
  }

  function firstSlideOfLatestPresentationTurn(deck) {
    const slides = Array.isArray(deck) ? deck : [];
    if (!slides.length) return 0;
    const latestAnswer = String((slides[slides.length - 1].dataset || {}).answerIndex || '');
    const index = slides.findIndex(slide => (
      String(((slide && slide.dataset) || {}).answerIndex || '') === latestAnswer
    ));
    return index >= 0 ? index : slides.length - 1;
  }

  function disconnectPresentationResizeObserver(view) {
    if (!view) return;
    if (view._presentationResizeObserver) view._presentationResizeObserver.disconnect();
    if (view._presentationResizeTimer) clearTimeout(view._presentationResizeTimer);
    view._presentationResizeObserver = null;
    view._presentationResizeTimer = null;
    view._presentationResizeSlot = null;
    view._presentationResizeSize = null;
  }

  function ensurePresentationResizeObserver(view, paneId) {
    if (!view || typeof ResizeObserver !== 'function') return;
    const stage = (0, sharedState.ensurePresentationStage)(view);
    const slot = stage.querySelector(':scope > .conv-presentation-slide-slot');
    if (!slot || view._presentationResizeSlot === slot) return;
    disconnectPresentationResizeObserver(view);
    view._presentationResizeSlot = slot;
    view._presentationResizeObserver = new ResizeObserver(entries => {
      const rect = entries[0] && entries[0].contentRect;
      if (!rect) return;
      const width = Math.round(rect.width);
      const height = Math.round(rect.height);
      const previous = view._presentationResizeSize;
      view._presentationResizeSize = { width, height };
      if (!previous) return;
      if (Math.abs(width - previous.width) < 4
          && Math.abs(height - previous.height) < 4) return;
      if (view._presentationResizeTimer) clearTimeout(view._presentationResizeTimer);
      view._presentationResizeTimer = setTimeout(() => {
        const pane = (0, sharedState.presentationPaneElement)(paneId);
        if (!pane || normalizePresentationMode(pane.dataset.presentationMode) !== '2') return;
        refreshPresentationForPane(paneId, { preserveCursor: true });
      }, 100);
    });
    view._presentationResizeObserver.observe(slot);
  }

  function refreshPresentationForPane(paneId, opts) {
    const targetPaneId = paneId || (0, sharedState.activePaneId)();
    const pane = (0, sharedState.presentationPaneElement)(targetPaneId);
    const view = (0, sharedState.getConvViewForPane)(targetPaneId);
    if (!pane || !view) return;
    const sessionId = presentationSessionIdForPane(targetPaneId);
    const conversationId = presentationConversationIdForPane(targetPaneId);
    if (pane.dataset.presentationSessionId !== sessionId) {
      pane.dataset.presentationSessionId = sessionId;
      pane.dataset.presentationMode = presentationModeForSession(sessionId);
    }
    const mode = presentationFeatureEnabled()
      ? normalizePresentationMode(pane.dataset.presentationMode || presentationModeForSession(sessionId))
      : 'off';
    pane.dataset.presentationMode = mode;
    const hasAnswers = !!view.querySelector('.event.assistant .assistant-text, .stream-bubble .assistant-text, [data-read-assistant-message], [data-read-agent-answer]');
    syncPresentationToolbar(pane, mode, hasAnswers);
    if (mode === 'off' || !hasAnswers) {
      disconnectPresentationProjection(view);
      disconnectPresentationResizeObserver(view);
      view.classList.remove('is-presentation-mode', 'is-presentation-mode-2', 'is-presentation-mode-3');
      const stage = view.querySelector(':scope > .conv-presentation-stage');
      if (stage) stage.remove();
      view._presentationDeck = null;
      view._presentationIndex = 0;
      return;
    }

    view.classList.add('is-presentation-mode', 'is-presentation-mode-' + mode);
    view.classList.toggle('is-presentation-mode-2', mode === '2');
    view.classList.toggle('is-presentation-mode-3', mode === '3');
    (0, sharedState.ensurePresentationStage)(view);

    const previousDeck = view._presentationDeck || [];
    const previousIndex = Number(view._presentationIndex) || 0;
    const previousSlide = previousDeck[previousIndex];
    const deck = buildPresentationDeck(view, mode);
    if (!deck.length) return;
    let index = presentationRefreshIndex(
      deck, previousDeck, previousIndex,
    );
    if (opts && opts.startAtLatestTurn) {
      index = firstSlideOfLatestPresentationTurn(deck);
    }
    view._presentationDeck = deck;
    view._presentationIndex = index;
    (0, sharedState.renderPresentationCursor)(targetPaneId, { animate: !previousDeck.length });
    ensurePresentationProjection(view, targetPaneId);
    renderMode3BootstrapState(targetPaneId, conversationId);
    if (mode === '2') ensurePresentationResizeObserver(view, targetPaneId);
    else disconnectPresentationResizeObserver(view);
  }

  function _tokenEconomizerAnnotationHtml(report) {
    if (!report || !report.observed) return '';
    const actions = Array.isArray(report.actions) ? report.actions : [];
    const label = 'TokenEconomizer · ' + (actions.length ? actions.join(' · ') : 'observed');
    const detail = actions.length
      ? 'Recorded automatic actions for this conversation. This is not a measurement of token savings.'
      : 'Hooks recorded this conversation; no optimization action is recorded yet. This does not imply token savings.';
    return '<span class="te-conversation-chip" title="' + (0, sharedState.escapeAttr)(detail) + '">'
      + (0, sharedState.escapeHtml)(label) + '</span>';
  }

  function _paintTokenEconomizerAnnotation(paneId, report) {
    const view = (0, sharedState.getConvViewForPane)(paneId);
    if (!view) return;
    let annotation = view.querySelector(':scope > .te-conversation-annotation');
    const html = _tokenEconomizerAnnotationHtml(report);
    if (!html) { if (annotation) annotation.remove(); }
    else {
      if (!annotation) {
        annotation = document.createElement('div');
        annotation.className = 'te-conversation-annotation';
        view.prepend(annotation);
      }
      if (annotation.innerHTML !== html) annotation.innerHTML = html;
    }
  }

  async function _refreshTokenEconomizerAnnotation(paneId) {
    const pane = (0, sharedState.paneByPaneId)(paneId);
    if (!pane || !pane.conversationId) return;
    const sid = pane.conversationId;
    const now = Date.now();
    if (pane._teFetchedSid === sid) _paintTokenEconomizerAnnotation(paneId, pane._teReport);
    if (pane._teRequestSid === sid || (pane._teFetchedSid === sid && now - pane._teFetchedAt < 15000)) return;
    pane._teRequestSid = sid;
    try {
      // The first annotation belongs to the conversation open. Background
      // status reads can wait behind long-lived streams in the slot gate.
      // Later refreshes share the background pool rather than taking a user slot.
      const priority = pane._teFetchedSid !== sid ? { cccPriority: 'user' } : undefined;
      const response = await fetch('/api/conversations/' + encodeURIComponent(sid) + '/tokeneconomizer', priority);
      if (!response.ok) return;
      const report = await response.json();
      if ((0, sharedState.paneByPaneId)(paneId) !== pane || pane.conversationId !== sid) return;
      _paintTokenEconomizerAnnotation(paneId, report);
      pane._teReport = report;
      pane._teFetchedSid = sid;
      pane._teFetchedAt = now;
    } catch (_) { /* Optional plugin state must never block the transcript. */ }
    finally { if (pane._teRequestSid === sid) pane._teRequestSid = null; }
  }

  function orchContext() {
    const convId = sharedState.currentConversation || '';
    const row = convId ? (sharedState.conversationsData || []).find(c => c && c.id === convId) : null;
    const sid = (row && row.session_id) || sharedState.sessionIdByConv[convId] || (sharedState.currentSession && sharedState.currentSession.id) || '';
    const repo = (row && (row.session_cwd || row.folder_path)) || (sharedState.currentSession && sharedState.currentSession.cwd) || '';
    const root = orchResolveRoot(row, sid);
    return { convId, row, sid, repo, cccUrl: location.origin, rootRow: root.row, rootSid: root.sid, hiddenParentSid: root.hiddenParentSid };
  }

  function orchResolveRoot(row, sid) {
    const rows = sharedState.conversationsData || [];
    const bySid = new Map();
    rows.forEach(r => { if (r && r.session_id) bySid.set(r.session_id, r); });
    const spawnBySid = new Map();
    (sharedState._orchSpawnedRegistry || []).forEach(sp => { if (sp && sp.session_id) spawnBySid.set(sp.session_id, sp); });
    let curRow = row || bySid.get(sid) || null;
    let curSid = sid;
    let hiddenParentSid = '';
    for (let depth = 0; depth < 6; depth++) {
      const sp = spawnBySid.get(curSid);
      const pid = String((curRow && (curRow.parent_session_id || curRow.hermes_parent_session_id)) || (sp && sp.parent_session_id) || '');
      if (!pid || pid === curSid) break;
      // Don't walk past a parent we can't see — root at the highest
      // visible ancestor instead of an invisible grandparent. Remember it
      // so the map can hint "there's more above" instead of silently
      // presenting this node as the true top of the tree.
      if (!bySid.has(pid) && !spawnBySid.has(pid)) { hiddenParentSid = pid; break; }
      curSid = pid;
      curRow = bySid.get(pid) || null;
    }
    return { row: curRow, sid: curSid, hiddenParentSid };
  }

  function orchSpawnExited(spawn) {
    if (!spawn) return false;
    if (spawn.running === false) return true;
    if (spawn.exit_code !== undefined && spawn.exit_code !== null) return true;
    return /^(exited|done|finished|stopped|killed|failed)$/i.test(String(spawn.status || ''));
  }

  function orchLiveActivityForLane(row, born) {
    const sid = row && row.session_id;
    if (!sid || !sharedState._liveSessionsActivityFetchedAt) return null;
    const sessions = (sharedState._liveSessionsActivityLast && sharedState._liveSessionsActivityLast.sessions) || {};
    if (Object.prototype.hasOwnProperty.call(sessions, sid)) return sessions[sid];
    // A new lane can take a poll cycle to appear in the live snapshot. Keep
    // the existing boot grace, but once it has elapsed an omitted row is
    // definitively ended rather than a stale archive-row "working" state.
    const age = born ? Date.now() / 1000 - born : Infinity;
    if (age < sharedState.ORCH_BOOT_GRACE_S) return null;
    return { is_live: false, state: 'ended' };
  }

  function orchLaneStatus(row, spawn, born) {
    if (row) {
      const fresh = orchLiveActivityForLane(row, born);
      if (fresh) row = Object.assign({}, row, fresh);
      if (row.state === 'ended' || row.is_live === false) return 'done';
      if (row.question_waiting || row.needs_approval) return 'waiting';
      if (row.state === 'working' || row.sidecar_in_flight) return 'working';
      // Devin is also live while merely steerable over CCC's shared ACP
      // conn; acp_status 'idle' there means the run finished (CCC-1203).
      if (row.is_live && sharedState.ORCH_ONE_SHOT_ENGINES.has(row.engine) && row.acp_status !== 'idle') return 'working';
      const age = born ? Date.now() / 1000 - born : Infinity;
      if (age < sharedState.ORCH_BOOT_GRACE_S && !orchSpawnExited(spawn)) return 'working';   // booting: hooks not yet reporting
      return 'done';
    }
    // Registry-only (row not in the list yet): a just-born lane is working
    // until its transcript shows up and the row takes over.
    if (spawn) return orchSpawnExited(spawn) ? 'done' : 'working';
    return 'idle';
  }

  function orchLaneName(row, spawn, sid) {
    const clean = (v) => String(v || '').replace(/[`*_]+/g, '').trim();
    if (row && row.name_overridden && clean(row.display_name)) return clean(row.display_name);
    return clean(spawn && spawn.name) || clean(row && row.display_name) || sid.slice(0, 8);
  }

  function orchLane(row, spawn) {
    const sid = (row && row.session_id) || (spawn && spawn.session_id) || '';
    const born = orchParseSpawnedAt(spawn && spawn.spawned_at) || orchParseSpawnedAt(row && row.timestamp) || orchLaneMetaGet(sid).born || 0;
    return {
      id: sid,
      convId: (row && row.id) || sid,
      name: orchLaneName(row, spawn, sid),
      engine: (row && row.engine) || (spawn && spawn.engine) || 'claude',
      model: (row && row.model) || (spawn && spawn.model) || '',
      status: orchLaneStatus(row, spawn, born),
      // List rows carry `modified` (epoch seconds); detail rows `mtime`.
      mtime: Number((row && (row.modified || row.mtime)) || 0),
      // Replay timeline (epoch seconds): when the lane was spawned and
      // when its transcript last moved, i.e. when it landed. The spawn
      // registry is pruned once a lane's process exits, so a lane meta
      // cache (written whenever the registry did know the lane) keeps
      // spawned_at for later replays.
      born,
      landedAt: Number((row && (row.modified || row.mtime)) || 0),
    };
  }

  function orchLaneMetaAll() {
    if (sharedState._orchLaneMeta) return sharedState._orchLaneMeta;
    try { sharedState._orchLaneMeta = JSON.parse(localStorage.getItem(sharedState.ORCH_LANE_META_KEY) || '{}') || {}; }
    catch (_) { sharedState._orchLaneMeta = {}; }
    return sharedState._orchLaneMeta;
  }

  function orchLaneMetaGet(sid) {
    return (sid && orchLaneMetaAll()[sid]) || {};
  }

  function orchLaneMetaRemember(entries) {
    let dirty = false;
    const all = orchLaneMetaAll();
    (entries || []).forEach(sp => {
      if (!sp || !sp.session_id) return;
      const born = orchParseSpawnedAt(sp.spawned_at);
      if (!born) return;
      const cur = all[sp.session_id];
      if (cur && cur.born === born && cur.name === (sp.name || cur.name)) return;
      all[sp.session_id] = {
        born,
        name: sp.name || (cur && cur.name) || '',
        model: sp.model || (cur && cur.model) || '',
        engine: sp.engine || (cur && cur.engine) || '',
        parent: sp.parent_session_id || (cur && cur.parent) || '',
      };
      dirty = true;
    });
    if (!dirty) return;
    const ids = Object.keys(all);
    if (ids.length > sharedState.ORCH_LANE_META_MAX) {
      ids.sort((a, b) => (all[a].born || 0) - (all[b].born || 0)).slice(0, ids.length - sharedState.ORCH_LANE_META_MAX).forEach(id => { delete all[id]; });
    }
    try { localStorage.setItem(sharedState.ORCH_LANE_META_KEY, JSON.stringify(all)); } catch (_) {}
  }

  function orchParseSpawnedAt(v) {
    if (!v) return 0;
    const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$/.exec(String(v));
    if (m) return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime() / 1000;
    const t = Date.parse(v);
    return isNaN(t) ? 0 : t / 1000;
  }

  function orchReplayClock() {
    try {
      if (!sharedState._convReplayActive) return null;
      const idx = Math.min(sharedState._convReplayMsgIndex, sharedState._convReplayEvents.length) - 1;
      if (idx < 0) return 0;
      const ts = Number(sharedState._convReplayEvents[idx].ts);
      return ts > 0 ? ts / 1000 : 0;
    } catch (_) { return null; }
  }

  function orchReportTimes() {
    const out = [];
    try {
      (sharedState._convReplayEvents || []).forEach(it => {
        if (!it || !it.el || !it.el.classList || !it.el.classList.contains('user_text')) return;
        const m = /announced from:\s*([^\n]+)/i.exec(it.el.textContent || '');
        if (m && it.ts > 0) out.push({ key: m[1].toLowerCase().replace(/[^a-z0-9]+/g, ''), ts: it.ts / 1000 });
      });
    } catch (_) {}
    return out;
  }

  function orchSpawnTimeFor(sid) {
    if (!sid) return 0;
    const events = sharedState._convReplayEvents || [];
    if (sharedState._orchSpawnIdx.events !== events) sharedState._orchSpawnIdx = { events, at: new Map() };
    if (sharedState._orchSpawnIdx.at.has(sid)) return sharedState._orchSpawnIdx.at.get(sid);
    let found = 0;
    try {
      const short = String(sid).replace(/^session_/, '').slice(0, 8);   // codex ids carry a prefix
      for (let i = 0; i < events.length; i++) {
        const it = events[i];
        if (!it || !it.el || !(it.ts > 0)) continue;
        const txt = it.el.textContent || '';
        if (txt.indexOf(short) !== -1) { found = it.ts / 1000; break; }
      }
    } catch (_) {}
    sharedState._orchSpawnIdx.at.set(sid, found);
    return found;
  }

  function orchLanesAt(lanes, clock) {
    const now = Date.now();
    let pending = false;
    const reports = orchReportTimes();
    const landedFor = (l, born) => {
      const key = String(l.name || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
      const hit = reports.find(r => r.key && key && r.ts >= (born || 0) && (key.indexOf(r.key) === 0 || r.key.indexOf(key) === 0));
      return hit && (!l.landedAt || hit.ts < l.landedAt) ? hit.ts : l.landedAt;
    };
    const out = lanes
      .map(l => {
        const spawnTs = l.born || orchSpawnTimeFor(l.id);
        const landedAt = landedFor(l, spawnTs);
        const born = spawnTs || landedAt;
        if (!born || born > clock) return null;
        let status = landedAt && landedAt <= clock ? 'done' : 'working';
        const first = sharedState._orchReplaySeen.get(l.id);
        if (first === undefined) sharedState._orchReplaySeen.set(l.id, now);
        if (status === 'done' && now - (first === undefined ? now : first) < sharedState.ORCH_REPLAY_STAGE_MS) { status = 'working'; pending = true; }
        return Object.assign({}, l, { status });
      })
      .filter(Boolean);
    if (pending) {
      clearTimeout(sharedState._orchReplayStageTimer);
      sharedState._orchReplayStageTimer = setTimeout(() => { sharedState._orchReplayStageTimer = null; updateOrchestrationPane(sharedState.currentConversation); }, sharedState.ORCH_REPLAY_STAGE_MS + 100);
    }
    return out;
  }

  function orchFlattenTree(tree, rootSid) {
    if (!tree || typeof tree !== 'object') return [];
    const rows = sharedState.conversationsData || [];
    const byId = new Map();
    rows.forEach(r => { if (r && r.session_id) byId.set(r.session_id, r); });
    const spawnById = new Map();
    (sharedState._orchSpawnedRegistry || []).forEach(sp => { if (sp && sp.session_id) spawnById.set(sp.session_id, sp); });
    const out = [];
    const seen = new Set();
    function walk(node, depth, parentSid) {
      if (!node || !node.session_id) return;
      const sid = node.session_id;
      if (sid === rootSid || seen.has(sid)) return;
      seen.add(sid);
      const row = byId.get(sid);
      const spawn = spawnById.get(sid);
      const meta = orchLaneMetaGet(sid);
      const isTaskSubagent = !node.resumable && (
        node.source === 'claude-task-tool' || node.source === 'kimi-subagent'
      );
      // For non-resumable subagents (Claude Task-tool transcripts, Kimi
      // Code Agent subagents) there's no row or spawn entry — build a minimal
      // lane from the tree node itself.
      if (isTaskSubagent) {
        // Claude Task-tool subagents open through the existing composite-id
        // popout convention ("<parent>:agent-<id>", CCC-112 — same one the
        // task-notification "agent transcript" button already uses). Kimi
        // subagents use a different synthetic id (`kimi-subagent:...`) with
        // no viewer built for it yet, so they stay unclickable for now.
        const canOpen = node.source === 'claude-task-tool' && sid.startsWith('agent-') && parentSid;
        // Liveness from the transcript itself (server-side tail check): an
        // `active` node whose file moved recently is still working. The
        // mtime freshness bound keeps a crashed lane (tail forever mid-tool,
        // file never written again) from reading "working" indefinitely.
        const nmt = Number(node.mtime || 0);
        const working = !!node.active && nmt > 0 && (Date.now() / 1000 - nmt) < 600;
        const born = orchParseSpawnedAt(node.spawned_at) || (nmt ? (nmt - 1) : 0);
        out.push({
          id: sid,
          convId: canOpen ? (parentSid + ':' + sid) : '',
          name: node.name || sid.slice(0, 16),
          engine: node.engine || 'claude',
          model: node.model || '',
          status: working ? 'working' : 'done',
          mtime: nmt,
          born,
          landedAt: nmt,
          depth,
          isTaskSubagent: true,
          source: node.source || '',
          nodeName: node.node_name || '',
        });
      } else {
        const lane = orchLane(row, spawn || { session_id: sid, name: node.name, model: node.model, engine: node.engine, spawned_at: null });
        lane.depth = depth;
        lane.isTaskSubagent = false;
        lane.source = node.source || '';
        lane.nodeName = node.node_name || '';
        out.push(lane);
      }
      (node.children || []).forEach(c => walk(c, depth + 1, sid));
    }
    // /api/sessions/family answers with the whole family from its topmost
    // ancestor. When the map is rooted lower (the real top is hidden: other
    // repo), walking from the top drew the orchestrator's siblings as its
    // lanes, and with no row here they read "working" forever (CCC-1212).
    // Walk only the map root's own subtree.
    function findRoot(node, n) {
      if (!node || n > 12) return null;
      if (node.session_id === rootSid) return node;
      for (const c of (node.children || [])) {
        const hit = findRoot(c, n + 1);
        if (hit) return hit;
      }
      return null;
    }
    const top = findRoot(tree, 0);
    if (!top) return [];
    (top.children || []).forEach(c => walk(c, 0, rootSid));
    // Newest first within each depth level.
    out.sort((a, b) => (a.depth - b.depth) || (b.mtime - a.mtime));
    return out;
  }

  function orchAppendManualLanes(parentSid, lanes) {
    const rows = sharedState.conversationsData || [];
    const byId = new Map();
    rows.forEach(row => { if (row && row.session_id) byId.set(row.session_id, row); });
    const spawnById = new Map();
    (sharedState._orchSpawnedRegistry || []).forEach(spawn => {
      if (spawn && spawn.session_id) spawnById.set(spawn.session_id, spawn);
    });
    const seen = new Set((lanes || []).map(lane => lane.id));
    rows.forEach(row => {
      const id = String((row && row.session_id) || '');
      if (!id || seen.has(id) || (0, sharedState.manualSubsessionParentId)(id) !== parentSid) return;
      const lane = orchLane(row, spawnById.get(id) || null);
      lane.depth = 0;
      lane.isTaskSubagent = false;
      lane.source = 'manual-subsession';
      seen.add(id);
      lanes.push(lane);
    });
    lanes.sort((a, b) => ((a.depth || 0) - (b.depth || 0)) || (b.mtime - a.mtime));
    return lanes;
  }

  function orchCollectLanes(sid) {
    if (sharedState._orchSim) return sharedState._orchSim.lanes.slice();
    if (!sid) return [];
    // Start with the unified SessionGraph when available: it contributes
    // multi-depth descendants and non-resumable Task-tool subagents. It is
    // not a replacement for the live direct-child sources, though. Family
    // indexing can lag a fresh spawn, so returning early here previously hid
    // a child the conversation list already knew about.
    const treeLanes = (sharedState._orchFamilyTree && sharedState._orchFamilyTreeSid === sid)
      ? orchFlattenTree(sharedState._orchFamilyTree, sid)
      : [];
    const lanes = treeLanes.slice();
    // Merge direct children from rows, the spawned registry, and the lane
    // meta cache. `seen` preserves the richer tree record when sources agree.
    const rows = sharedState.conversationsData || [];
    const byId = new Map();
    rows.forEach(r => { if (r && r.session_id) byId.set(r.session_id, r); });
    const spawnById = new Map();
    (sharedState._orchSpawnedRegistry || []).forEach(sp => { if (sp && sp.session_id) spawnById.set(sp.session_id, sp); });
    const seen = new Set(lanes.map(lane => lane.id));
    rows.forEach(r => {
      const pid = String((r && (r.parent_session_id || r.hermes_parent_session_id)) || '');
      if (pid !== sid || !r.session_id || seen.has(r.session_id)) return;
      seen.add(r.session_id);
      lanes.push(orchLane(r, spawnById.get(r.session_id) || null));
    });
    (sharedState._orchSpawnedRegistry || []).forEach(sp => {
      if (String(sp.parent_session_id || '') !== sid || !sp.session_id || seen.has(sp.session_id)) return;
      seen.add(sp.session_id);
      lanes.push(orchLane(byId.get(sp.session_id) || null, sp));
    });
    const meta = orchLaneMetaAll();
    Object.keys(meta).forEach(id => {
      const m = meta[id];
      if (!m || String(m.parent || '') !== sid || seen.has(id)) return;
      const row = byId.get(id);
      if (!row) return;   // no transcript on this machine any more: nothing to show or open
      seen.add(id);
      lanes.push(orchLane(row, { session_id: id, name: m.name, model: m.model, engine: m.engine, spawned_at: null }));
    });
    // Newest first inside each band so a fresh lane enters on the left.
    lanes.sort((a, b) => b.mtime - a.mtime);
    return orchAppendManualLanes(sid, lanes);
  }

  async function orchRefreshRegistry(force, rootSid, selectedSid) {
    if (sharedState._orchRegistryInFlight) return;
    const familySid = selectedSid || rootSid;
    const familyKey = (rootSid || '') + '|' + (familySid || '');
    const sidChanged = rootSid && familyKey !== sharedState._orchFamilyRequestKey;
    if (!force && !sidChanged && Date.now() - sharedState._orchRegistryFetchedAt < 5000) return;
    sharedState._orchRegistryInFlight = true;
    sharedState._orchFamilyRequestKey = familyKey;
    try {
      const [r1, r2, r3] = await Promise.all([
        fetch('/api/sessions/spawned', { signal: (0, sharedState.convScopeSignal)() }),
        rootSid ? fetch('/api/sessions/children?parent=' + encodeURIComponent(rootSid), { signal: (0, sharedState.convScopeSignal)() }) : Promise.resolve(null),
        rootSid ? fetch('/api/sessions/family?sid=' + encodeURIComponent(familySid), { signal: (0, sharedState.convScopeSignal)() }) : Promise.resolve(null),
      ]);
      const live = r1 && r1.ok ? await r1.json() : [];
      let kids = [];
      if (r2 && r2.ok) {
        const d = await r2.json();
        kids = (d && Array.isArray(d.children)) ? d.children : [];
        sharedState._orchChildrenSid = rootSid;
        sharedState._orchChildren = kids;
      } else if (rootSid === sharedState._orchChildrenSid) {
        kids = sharedState._orchChildren;
      }
      // Family tree from the unified SessionGraph — full multi-depth tree
      // including Claude Task-tool subagents and Codex native subagents.
      if (r3 && r3.ok) {
        const d = await r3.json();
        if (d && d.ok && d.tree) {
          sharedState._orchFamilyTreeSid = rootSid;
          sharedState._orchFamilyTree = d.tree;
          sharedState._orchFamilyContinuation = d.continuation || null;
          if (rememberSidebarFamilyParents(d.tree)) (0, hostServices.renderSidebar)(sharedState.conversationsData);
        }
      } else if (rootSid === sharedState._orchFamilyTreeSid) {
        // keep cached tree
      } else {
        sharedState._orchFamilyTree = null;
        sharedState._orchFamilyContinuation = null;
      }
      const seen = new Set();
      const merged = [];
      (Array.isArray(live) ? live : []).forEach(sp => { if (sp && sp.session_id) { seen.add(sp.session_id); merged.push(sp); } });
      kids.forEach(sp => { if (sp && sp.session_id && !seen.has(sp.session_id)) { seen.add(sp.session_id); merged.push(sp); } });
      sharedState._orchSpawnedRegistry = merged;
      orchLaneMetaRemember(merged);
    } catch (_) {}
    sharedState._orchRegistryFetchedAt = Date.now();
    sharedState._orchRegistryInFlight = false;
    if ((0, sharedState.orchPaneVisible)()) {
      updateOrchestrationPane(sharedState.currentConversation);
    }
  }

  function orchPaintFamilyRows(ctx) {
    const list = document.getElementById('convList');
    if (!list) return;
    let root = '', laneIds = [], count = 0, details = [];
    if (ctx && ctx.convId && !sharedState._orchSim) {
      const lanes = orchCollectLanes(ctx.rootSid || ctx.sid);
      if (lanes.length) {
        root = (ctx.rootRow && ctx.rootRow.id) || (ctx.rootSid === ctx.sid ? ctx.convId : '');
        laneIds = lanes.map(l => l.convId).filter(Boolean);
        count = lanes.length;
        details = lanes;
      }
    }
    sharedState._orchFamily = { root, lanes: laneIds, count, details };
    orchApplyFamilyRows();
  }

  function orchSyncFamilyDisclosure(el, lanes) {
    const sid = el.getAttribute('data-id') || '';
    const signature = JSON.stringify(lanes.map(l => [l.id, l.convId, l.name, l.isTaskSubagent]));
    const targets = [el.querySelector('.conv-main-row .conv-meta-col') || el.querySelector('.conv-main-row'),
      el.querySelector('.wa-body')].filter(Boolean);
    targets.forEach(target => {
      let disclosure = target.querySelector('.conv-orch-family-details');
      if (!disclosure) {
        disclosure = document.createElement('details');
        disclosure.className = 'conv-family-details conv-orch-family-details';
        disclosure.setAttribute('data-family-key', sid + ':lanes');
        disclosure.open = _subagentClustersExpandedSet().has(sid)
          && !_subagentClustersCollapsedSet().has(sid);
        disclosure.addEventListener('toggle', () => {
          if (!disclosure.isConnected) return;
          const expanded = disclosure.open;
          const expSet = _subagentClustersExpandedSet();
          const colSet = _subagentClustersCollapsedSet();
          if (expanded) { expSet.add(sid); colSet.delete(sid); }
          else { expSet.delete(sid); colSet.add(sid); }
          _subagentClustersSaveExpandedSet(expSet);
          _subagentClustersSaveCollapsedSet(colSet);
          el.querySelectorAll('.conv-orch-family-details').forEach(other => {
            if (other.open !== expanded) other.open = expanded;
          });
        });
        target.appendChild(disclosure);
      }
      if (disclosure.dataset.lanes === signature) return;
      disclosure.dataset.lanes = signature;
      disclosure.innerHTML = '<summary class="conv-orch-lanes-chip">'
        + (0, sharedState.escapeHtml)('\u21b3 ' + lanes.length + (lanes.length === 1 ? ' lane' : ' lanes')) + '</summary>'
        + '<div class="conv-family-list">' + lanes.map(lane => (
          '<button type="button" class="conv-family-link"'
          + (lane.convId ? ' data-parent-sid="' + (0, sharedState.escapeAttr)(lane.convId) + '"' : ' disabled')
          + (lane.isTaskSubagent ? ' data-family-task' : '') + '>'
          + (0, sharedState.escapeHtml)(lane.name || lane.id) + '</button>'
        )).join('') + '</div>';
    });
  }

  function orchApplyFamilyRows() {
    const list = document.getElementById('convList');
    if (!list) return;
    const { root, lanes, details } = sharedState._orchFamily;
    const laneSet = new Set(lanes);
    list.querySelectorAll('.conv-item.is-orch-root, .conv-item.is-orch-lane').forEach(el => {
      const id = el.getAttribute('data-id');
      if (id !== root && !laneSet.has(id)) {
        el.classList.remove('is-orch-root', 'is-orch-lane');
        el.querySelectorAll('.conv-orch-family-details').forEach(d => d.remove());
        const chip = el.querySelector('.conv-orch-lanes-chip:not(.conv-subagent-cluster-toggle)');
        if (chip) chip.remove();
      }
    });
    if (!root && !lanes.length) return;
    list.querySelectorAll('.conv-item[data-id]').forEach(el => {
      const id = el.getAttribute('data-id');
      const isRoot = id === root, isLane = laneSet.has(id);
      el.classList.toggle('is-orch-root', isRoot);
      el.classList.toggle('is-orch-lane', isLane);
      const mainRow = isRoot ? el.querySelector('.conv-main-row') : null;
      let chip = el.querySelector('.conv-orch-lanes-chip');
      if (isRoot && mainRow) {
        if (el.querySelector('.conv-subagent-cluster-toggle, .wa-lanes')) {
          el.querySelectorAll('.conv-orch-family-details').forEach(d => d.remove());
          if (chip && !chip.classList.contains('conv-subagent-cluster-toggle')) chip.remove();
          return;
        }
        if (chip && !chip.closest('.conv-orch-family-details')) chip.remove();
        orchSyncFamilyDisclosure(el, details || []);
      } else {
        el.querySelectorAll('.conv-orch-family-details').forEach(d => d.remove());
        if (chip && !chip.classList.contains('conv-subagent-cluster-toggle')) chip.remove();
      }
    });
  }

  function orchNodeHtml(lane, compact) {
    const glyph = sharedState.ORCH_ENGINE_GLYPH[lane.engine] || '?';
    const statusWord = { working: 'working', waiting: 'needs you', idle: 'idle', done: 'landed' }[lane.status] || lane.status;
    const depth = lane.depth || 0;
    const indent = depth > 0 ? '<span class="orch-node-indent" style="margin-left:' + (depth * 14) + 'px"></span>' : '';
    const taskBadgeTitle = lane.isTaskSubagent
      ? (lane.source === 'kimi-subagent'
         ? 'Kimi Code Agent subagent (read-only transcript)'
         : 'Claude Task-tool subagent (read-only transcript)')
      : '';
    const taskBadge = lane.isTaskSubagent ? '<span class="orch-node-task-badge" title="' + (0, sharedState.escapeAttr)(taskBadgeTitle) + '">transcript</span>' : '';
    return indent
      + '<span class="orch-node-glyph orch-glyph orch-glyph-' + (0, sharedState.escapeAttr)(lane.engine) + '">' + glyph + '</span>'
      + '<span class="orch-node-body">'
      + '<span class="orch-node-name">' + (0, sharedState.escapeHtml)((lane.nodeName ? '[' + lane.nodeName + '] ' : '') + lane.name) + '</span>'
      + (compact ? '' : '<span class="orch-node-model">' + (0, sharedState.escapeHtml)(lane.model || lane.engine) + '</span>')
      + '</span>'
      + taskBadge
      + '<span class="orch-node-status"><span class="orch-node-dot"></span>' + (0, sharedState.escapeHtml)(statusWord) + '</span>';
  }

  function orchContinuationHtml(data) {
    if (!data) return '';
    const names = data.node_names || {};
    const labelRef = ref => {
      const parts = String(ref || '').split(':');
      return parts.length === 2 ? '[' + (names[parts[0]] || parts[0].slice(0, 8)) + '] ' + parts[1].slice(0, 8) : String(ref || '').slice(0, 8);
    };
    const link = (edge, role) => {
      if (!edge) return '';
      let label = edge.node_name ? '[' + edge.node_name + '] ' + (edge.title || edge.session_id.slice(0, 8)) : labelRef(edge.ref || edge.session_id);
      if (edge.dangling) label += ' · unresolved';
      if (edge.ambiguous) label += ' · multiple copies: ' + (edge.candidates || []).map(labelRef).join(', ');
      return '<div class="orch-continuation-link" title="' + (0, sharedState.escapeAttr)(edge.ref || edge.session_id || '') + '">'
        + (0, sharedState.escapeHtml)(role + ' ' + label) + '</div>';
    };
    const links = link(data.predecessor, 'Continued from') + (data.successors || []).map(s => link(s, 'Continued as')).join('');
    if (!links) return data.indexing ? '<div class="orch-continuation-status">Continuation index is warming</div>' : '';
    const statuses = (data.continuation_status || []).filter(n => n.status !== 'ok' || n.stale)
      .map(n => '[' + n.name + '] ' + (n.stale ? 'cached answer' : String(n.status).replace(/_/g, ' ')));
    return links + (statuses.length ? '<div class="orch-continuation-status">' + (0, sharedState.escapeHtml)(statuses.join(' · ')) + '</div>' : '');
  }

  function updateOrchestrationPane(convId) {
    const pane = document.getElementById('statusRailOrchestrationPane');
    const rootEl = document.getElementById('orchMapRoot');
    const ancestorEl = document.getElementById('orchMapAncestor');
    const lanesEl = document.getElementById('orchMapLanes');
    const doneEl = document.getElementById('orchMapDone');
    const doneLabel = document.getElementById('orchMapDoneLabel');
    const countEl = document.getElementById('orchMapCount');
    const emptyEl = document.getElementById('orchEmpty');
    const playbooksEl = document.getElementById('orchPlaybooks');
    if (!pane || !rootEl || !lanesEl || !doneEl) return;
    if (!playbooksEl.childElementCount) (0, sharedState.renderOrchPlaybooks)();
    const ctx = orchContext();
    const hasSession = !!ctx.convId;
    if (emptyEl) emptyEl.hidden = hasSession;
    playbooksEl.classList.toggle('is-disabled', !hasSession);
    orchPaintFamilyRows(ctx);
    if (pane.hidden) return;   // nothing visible to measure — skip the work
    if (hasSession) orchRefreshRegistry(false, ctx.rootSid || ctx.sid, ctx.sid);

    const sid = ctx.rootSid || ctx.sid;
    const hereSid = ctx.sid;
    const continuation = !sharedState._orchSim && sharedState._orchFamilyContinuation && sharedState._orchFamilyContinuation.session_id === hereSid
      ? sharedState._orchFamilyContinuation : null;
    const continuationEl = document.getElementById('orchMapContinuation');
    if (continuationEl) {
      continuationEl.innerHTML = orchContinuationHtml(continuation);
      continuationEl.hidden = !continuationEl.innerHTML;
    }
    const atRoot = !ctx.rootSid || ctx.rootSid === ctx.sid;
    if (sid !== sharedState._orchLastSid) { sharedState._orchLastKeys = new Set(); sharedState._orchLastSid = sid; }
    const clock = sharedState._orchSim ? null : orchReplayClock();
    let lanes = orchCollectLanes(sid);
    if (clock !== null) lanes = orchLanesAt(lanes, clock);
    const active = lanes.filter(l => l.status !== 'done');
    const landed = lanes.filter(l => l.status === 'done');
    const working = active.filter(l => l.status === 'working').length;
    const waiting = active.filter(l => l.status === 'waiting').length;

    // Root (orchestrator) node.
    const rootRow = ctx.rootRow || ctx.row || (sharedState._orchSim ? sharedState._orchSim.root : null);
    const rootName = sharedState._orchSim ? sharedState._orchSim.root.name : ((rootRow && rootRow.display_name) || (atRoot ? 'This session' : sid.slice(0, 8)));
    const rootEngine = sharedState._orchSim ? sharedState._orchSim.root.engine : ((rootRow && rootRow.engine) || 'claude');
    const rootModel = sharedState._orchSim ? sharedState._orchSim.root.model : ((rootRow && rootRow.model) || '');
    const rootConv = (rootRow && rootRow.id) || '';
    const rootNodeName = (() => {
      if (sharedState._orchSim || sharedState._orchFamilyTreeSid !== sid) return '';
      const find = (node, depth) => {
        if (!node || depth > 12) return '';
        if (node.session_id === sid) return node.node_name || '';
        for (const child of (node.children || [])) {
          const name = find(child, depth + 1);
          if (name) return name;
        }
        return '';
      };
      return find(sharedState._orchFamilyTree, 0);
    })();
    rootEl.innerHTML = hasSession || sharedState._orchSim
      ? '<button type="button" class="orch-node orch-node-root' + (working ? ' is-fanning' : '') + (atRoot ? ' is-here' : '') + '"'
        + (rootConv && !atRoot ? ' data-conv-id="' + (0, sharedState.escapeAttr)(rootConv) + '" title="Back to the orchestrator"' : '') + '>'
        + '<span class="orch-node-glyph orch-glyph orch-glyph-' + (0, sharedState.escapeAttr)(rootEngine) + '">' + (sharedState.ORCH_ENGINE_GLYPH[rootEngine] || '?') + '</span>'
        + '<span class="orch-node-body">'
        + '<span class="orch-node-role">Orchestrator</span>'
        + '<span class="orch-node-name">' + (0, sharedState.escapeHtml)((rootNodeName ? '[' + rootNodeName + '] ' : '') + rootName) + '</span>'
        + '<span class="orch-node-model">' + (0, sharedState.escapeHtml)(rootModel || rootEngine) + '</span>'
        + '</span></button>'
      : '';
    if (ancestorEl) {
      const hiddenParentSid = hasSession && !sharedState._orchSim ? ctx.hiddenParentSid : '';
      ancestorEl.hidden = !hiddenParentSid;
      if (hiddenParentSid) ancestorEl.textContent = '↑ spawned by ' + hiddenParentSid.slice(0, 8) + ' (different repo or not visible here)';
    }
    if (countEl) {
      const total = clock !== null ? orchCollectLanes(sid).length : 0;
      countEl.textContent = !lanes.length
        ? (clock !== null ? (total ? 'Replay · ' + total + (total === 1 ? ' lane' : ' lanes') + ' ahead' : 'Replay · this session spawned no lanes') : '')
        : (clock !== null ? 'Replay · ' : '') + lanes.length + (lanes.length === 1 ? ' lane' : ' lanes')
          + (working ? ' · ' + working + ' working' : '')
          + (waiting ? ' · ' + waiting + ' waiting' : '')
          + (landed.length ? ' · ' + landed.length + ' landed' : '');
      countEl.classList.toggle('is-preview', !!sharedState._orchSim || clock !== null);
    }

    // FLIP: remember where every existing node is before we touch the DOM.
    const before = new Map();
    if (lanes.length) pane.querySelectorAll('.orch-node[data-lane-id]').forEach(el => {
      before.set(el.getAttribute('data-lane-id'), el.getBoundingClientRect());
    });
    const rootRectBefore = lanes.length ? rootEl.getBoundingClientRect() : null;

    const paint = (host, list, compact) => {
      const keep = new Set();
      list.forEach(lane => {
        keep.add(lane.id);
        let el = pane.querySelector('.orch-node[data-lane-id="' + CSS.escape(lane.id) + '"]');
        if (!el) {
          el = document.createElement('button');
          el.type = 'button';
          el.className = 'orch-node orch-node-lane';
          el.setAttribute('data-lane-id', lane.id);
          el.setAttribute('data-conv-id', lane.convId);
        }
        el.classList.toggle('is-compact', !!compact);
        el.classList.toggle('is-here', !!lane.id && lane.id === hereSid);
        el.classList.toggle('is-task-subagent', !!lane.isTaskSubagent);
        el.className = el.className.replace(/\bis-(working|waiting|idle|done)\b/g, '').trim() + ' is-' + lane.status;
        el.title = lane.name + ' · ' + (lane.model || lane.engine) + ' · ' + lane.status + (lane.isTaskSubagent ? ' · transcript only' : ' · click to open');
        const html = orchNodeHtml(lane, compact);
        if (el.innerHTML !== html) el.innerHTML = html;
        if (el.parentNode !== host) host.appendChild(el);
      });
      // Keep DOM order = list order so a new lane enters leftmost.
      list.forEach(lane => {
        const el = host.querySelector('.orch-node[data-lane-id="' + CSS.escape(lane.id) + '"]');
        if (el) host.appendChild(el);
      });
      Array.from(host.children).forEach(el => {
        const id = el.getAttribute('data-lane-id');
        if (!keep.has(id) && !(el.parentNode !== host)) {
          // Node left this band; if it is not in the other band either, drop it.
          if (!lanes.some(l => l.id === id)) el.remove();
        }
      });
    };
    paint(lanesEl, active, false);
    paint(doneEl, landed, true);
    if (doneLabel) doneLabel.hidden = !landed.length;
    lanesEl.classList.toggle('is-empty', !active.length);
    lanesEl.setAttribute('data-empty-text', hasSession
      ? (lanes.length ? 'All lanes landed.' : 'Lanes appear here when the orchestrator spawns them. Tap Delegate.')
      : '');

    // Animate: moved nodes glide (FLIP); brand-new nodes are born out of
    // the orchestrator node.
    const rootNode = rootEl.querySelector('.orch-node-root');
    const rootRect = lanes.length && rootNode ? rootNode.getBoundingClientRect() : rootRectBefore;
    const firstPaintForSession = sharedState._orchLastKeys.size === 0 && !sharedState._orchSim && clock === null;
    pane.querySelectorAll('.orch-node[data-lane-id]').forEach(el => {
      const id = el.getAttribute('data-lane-id');
      const after = el.getBoundingClientRect();
      const prev = before.get(id);
      el.style.transition = 'none';
      if (prev) {
        const dx = prev.left - after.left, dy = prev.top - after.top;
        if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
          el.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
        }
      } else if (!firstPaintForSession || sharedState._orchSim) {
        const dx = (rootRect.left + rootRect.width / 2) - (after.left + after.width / 2);
        const dy = (rootRect.top + rootRect.height / 2) - (after.top + after.height / 2);
        el.style.transform = 'translate(' + dx + 'px,' + dy + 'px) scale(0.3)';
        el.style.opacity = '0';
        el.classList.add('is-born');
        el.addEventListener('animationend', () => el.classList.remove('is-born'), { once: true });
      }
    });
    const bornNow = lanes.some(l => !before.has(l.id)) && !firstPaintForSession;
    sharedState._orchLastKeys = new Set(lanes.map(l => l.id));
    if (bornNow) {
      const wrap = document.getElementById('orchMapWrap');
      if (wrap && wrap.scrollIntoView) wrap.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
    requestAnimationFrame(() => {
      pane.querySelectorAll('.orch-node[data-lane-id]').forEach(el => {
        el.style.transition = '';
        el.style.transform = '';
        el.style.opacity = '';
        setTimeout(() => el.classList.remove('is-born'), 950);
      });
      orchDrawEdges();
      setTimeout(orchDrawEdges, 520);   // once the FLIP transition settles
    });
  }

  function orchDrawEdges() {
    const map = document.getElementById('orchMap');
    const svg = document.getElementById('orchMapEdges');
    const rootNode = map && map.querySelector('.orch-node-root');
    if (!map || !svg) return;
    // With no selected session there are no edges or nodes to measure.
    // Geometry here would flush the entire freshly rendered dashboard.
    if (!rootNode) { svg.innerHTML = ''; return; }
    const mapRect = map.getBoundingClientRect();
    const narrow = map.clientWidth < 360;
    map.classList.toggle('is-narrow', narrow);
    svg.setAttribute('width', map.clientWidth);
    svg.setAttribute('height', map.scrollHeight);
    svg.setAttribute('viewBox', '0 0 ' + map.clientWidth + ' ' + map.scrollHeight);
    const r = rootNode.getBoundingClientRect();
    // Wide rail: fan out from the root's bottom centre. Narrow rail (the
    // common case): org-chart elbows off a spine that drops from the
    // root's left side, so every lane visibly hangs off the orchestrator
    // even when they stack in one column.
    const x0 = narrow ? (r.left + 22 - mapRect.left) : (r.left + r.width / 2 - mapRect.left);
    const y0 = r.bottom - mapRect.top + map.scrollTop;
    const parts = [];
    map.querySelectorAll('#orchMapLanes .orch-node-lane, #orchMapDone .orch-node-lane').forEach(el => {
      const n = el.getBoundingClientRect();
      const status = (el.className.match(/\bis-(working|waiting|idle|done)\b/) || [])[1] || 'idle';
      let d;
      if (narrow) {
        const x1 = n.left - mapRect.left;
        const y1 = n.top + n.height / 2 - mapRect.top + map.scrollTop;
        const rad = 8;
        d = 'M' + x0.toFixed(1) + ',' + y0.toFixed(1)
          + ' L' + x0.toFixed(1) + ',' + (y1 - rad).toFixed(1)
          + ' Q' + x0.toFixed(1) + ',' + y1.toFixed(1) + ' ' + (x0 + rad).toFixed(1) + ',' + y1.toFixed(1)
          + ' L' + x1.toFixed(1) + ',' + y1.toFixed(1);
      } else {
        const x1 = n.left + n.width / 2 - mapRect.left;
        const y1 = n.top - mapRect.top + map.scrollTop;
        const my = (y0 + y1) / 2;
        d = 'M' + x0.toFixed(1) + ',' + y0.toFixed(1)
          + ' C' + x0.toFixed(1) + ',' + my.toFixed(1) + ' ' + x1.toFixed(1) + ',' + my.toFixed(1) + ' ' + x1.toFixed(1) + ',' + y1.toFixed(1);
      }
      parts.push('<path class="orch-edge orch-edge-' + status + '" d="' + d + '"></path>');
      if (status === 'working') parts.push('<path class="orch-edge orch-edge-flow" d="' + d + '"></path>');
    });
    svg.innerHTML = parts.join('');
  }

  function _localhostCtxKeyOf(ctx) {
    return ctx ? [ctx.repoPath || '', ctx.cwd || '', ctx.sessionId || ''].join('') : '';
  }

  function localhostContext() {
    const convId = (typeof sharedState.currentConversation !== 'undefined') ? sharedState.currentConversation : '';
    const row = convId ? ((sharedState.conversationsData || []).find(x => x.id === convId) || null) : null;
    // Take it from the conversation first: rowRepoPath() scans repo_path /
    // folder_path / spawn_cwd / session_cwd / cwd for the first ABSOLUTE path,
    // so Codex/Gemini/external-volume sessions resolve the same as Claude ones.
    // Only fall back to the sidebar selection when the open conversation has no
    // usable path of its own. (Previously a non-absolute sidebar value shadowed
    // a valid conversation path and then got nulled → false "no-repo".)
    const rowRepo = row ? (0, sharedState.rowRepoPath)(row) : '';
    // The open session id, computed up front because it gates every fallback
    // below. Only a real UUID-ish id, never a popout project-dir slug.
    const sessionId = (typeof convId === 'string' && /^[0-9a-f-]{8,}$/i.test(convId)) ? convId : '';
    // currentSession is the record of the session setCurrentSession ran for
    // LAST — it is the open session's record only while its id matches the open
    // conversation. Across a conversation switch it still holds the PREVIOUS
    // session's repo/cwd until the new row loads, so trusting it unconditionally
    // mis-binds the localhost pill to the previously-open repo ("it remembers
    // the previous conversation"). Gate it on the id match. When it matches it
    // still resolves every engine (Claude/Codex/Gemini) without a shaped row.
    const sessRaw = (typeof sharedState.currentSession !== 'undefined' && sharedState.currentSession) ? sharedState.currentSession : null;
    const sess = (sessRaw && (!sessionId || sessRaw.id === sessionId)) ? sessRaw : null;
    const sessRepo = sess ? (sess.repoPath || sess.cwd || '') : '';
    // The sidebar repo filter describes the LIST, not the open conversation —
    // only use it when no conversation is open.
    const selectedRepo = sessionId ? '' : (0, sharedState.popoutRepoPath)();
    let repoPath = ((0, sharedState._isAbsoluteLocalPath)(rowRepo) ? rowRepo : '')
                || ((0, sharedState._isAbsoluteLocalPath)(sessRepo) ? sessRepo : '')
                || ((0, sharedState._isAbsoluteLocalPath)(selectedRepo) ? selectedRepo : '');
    const mapCwd = (row && typeof sharedState.sessionCwdByConv !== 'undefined') ? (sharedState.sessionCwdByConv[row.id] || '') : '';
    let cwd = mapCwd || (row && (row.session_cwd || row.spawn_cwd || row.cwd))
            || (sess && sess.cwd) || '';
    // Only an absolute filesystem path is a valid repo_path/cwd for the server's
    // require_repo_context. A dash-encoded project-dir name (e.g. leaked popout
    // state like "-Users-amirfish-GStack-test") 400s every 15s — drop it and let
    // the pill fall back to "no-repo" instead of spamming failed calls.
    if (cwd && !cwd.startsWith('/')) cwd = '';
    if (repoPath && cwd && cwd !== repoPath) {
      const root = repoPath.replace(/\/+$/, '');
      if (cwd !== root && !cwd.startsWith(root + '/')) cwd = '';
    }
    // When a conversation is open but we could not derive its repo from the open
    // row or a matching currentSession, do NOT send a repo_path/cwd borrowed
    // from another context — the server prefers repo_path over session_id, so a
    // stale one wins and binds the pill to the wrong repo. Send session_id alone
    // and let the server resolve the session's own cwd (works for every engine).
    if (!repoPath && sessionId) cwd = '';
    return {
      repoPath,
      cwd: cwd && cwd !== repoPath ? cwd : '',
      sessionId,
    };
  }

  function localhostUrl(path, ctx) {
    const u = new URL(path, window.location.href);
    if (ctx && ctx.repoPath) u.searchParams.set('repo_path', ctx.repoPath);
    if (ctx && ctx.cwd) u.searchParams.set('cwd', ctx.cwd);
    if (ctx && ctx.sessionId) u.searchParams.set('session_id', ctx.sessionId);
    return u.pathname + u.search;
  }

  function localhostCommand(d) {
    return (d && (d.launch_cmd || d.cmd)) || sharedState._localhostLastCommand || '';
  }

  function setLocalhostPill({ dotClass, label, title, href, busy }) {
    if (sharedState.$localhostDot) sharedState.$localhostDot.className = 'deploy-dot' + (dotClass ? ' ' + dotClass : '');
    if (sharedState.$localhostLabel) sharedState.$localhostLabel.textContent = label;
    if (!sharedState.$localhostPill) return;
    sharedState.$localhostPill.title = title || '';
    if (href) {
      sharedState.$localhostPill.setAttribute('href', href);
      sharedState.$localhostPill.setAttribute('target', '_blank');
      sharedState.$localhostPill.style.cursor = 'pointer';
    } else {
      sharedState.$localhostPill.removeAttribute('href');
      sharedState.$localhostPill.removeAttribute('target');
      // Always pointer (except when busy) so it's obvious the pill is
      // interactive even when there's no actionable state — the click
      // handler always responds (alert, retry, etc.).
      sharedState.$localhostPill.style.cursor = busy ? 'wait' : 'pointer';
    }
  }

  async function pollLocalhost() {
    if (!sharedState.$localhostPill) return;
    const ctx = localhostContext();
    // If the resolved context changed since the last poll, drop the previous
    // conversation's cached target/command before deriving any UI from them —
    // otherwise an early return below (no-repo / unreachable / 400) shows the
    // old repo path in the title and click alert (CCC-137).
    const ctxKey = _localhostCtxKeyOf(ctx);
    if (ctxKey !== sharedState._localhostCtxKey) {
      sharedState._localhostTargetPath = '';
      sharedState._localhostLastCommand = '';
      sharedState._localhostCtxKey = ctxKey;
    }
    if (!ctx.repoPath && !ctx.cwd && !ctx.sessionId) {
      sharedState._localhostState = 'no-repo';
      setLocalhostPill({
        dotClass: '',
        label: 'localhost',
        title: 'Pick a repo first - the localhost pill needs to know which directory to look in.',
        href: '',
      });
      return;
    }
    let res;
    try {
      res = await (0, sharedState.backgroundApiFetch)(localhostUrl('/api/nextjs/status', ctx));
    } catch (e) {
      sharedState._localhostState = 'unreachable';
      setLocalhostPill({
        dotClass: 'error',
        label: 'localhost: offline',
        title: 'Could not reach the CCC server: ' + (e && e.message || e),
        href: '',
      });
      return;
    }
    if (!res.ok) {
      // A 400 with a structured repo-context error (code: repo_required /
      // not_a_repo / …) means the ROUTE works — we just didn't hand it a
      // resolvable repo (e.g. a session on a path CCC doesn't treat as a
      // repo). That's a no-repo state, NOT a stale-server condition. Only a
      // genuinely missing route (404) warrants the "restart ./run.sh" nudge.
      let body = null;
      try { body = await res.clone().json(); } catch (_) {}
      if (res.status === 400 && body && body.code) {
        sharedState._localhostState = 'no-repo';
        setLocalhostPill({
          dotClass: '',
          label: 'localhost',
          title: 'No dev-server repo here (' + body.code + '). ' +
                 'Pick a repo with a package.json to use the localhost pill.',
          href: '',
        });
        return;
      }
      sharedState._localhostState = 'unreachable';
      setLocalhostPill({
        dotClass: 'error',
        label: 'localhost: needs restart',
        title: '/api/nextjs/status returned ' + res.status +
               '. Restart the CCC server (./run.sh) to pick up the new endpoint.',
        href: '',
      });
      return;
    }
    let d;
    try {
      d = await res.json();
    } catch (_e) {
      sharedState._localhostState = 'unreachable';
      setLocalhostPill({
        dotClass: 'error',
        label: 'localhost: bad response',
        title: 'CCC server returned non-JSON for /api/nextjs/status - likely an old build. Restart it.',
        href: '',
      });
      return;
    }
    sharedState._localhostLastCommand = localhostCommand(d);
    sharedState._localhostTargetPath = d.target_path || '';
    if (!d.detected) {
      sharedState._localhostState = 'no-nextjs';
      setLocalhostPill({
        dotClass: '',
        label: 'No dev server',
        title: 'No dev server in ' + (sharedState._localhostTargetPath || 'this folder') +
               ' - needs a `dev` script in package.json (Vite/Next/CRA/Astro/…) ' +
               'or a next.config.*. Click for details.',
        href: '',
      });
      return;
    }
    if (d.running && d.port) {
      sharedState._localhostState = 'running';
      const url = 'http://localhost:' + d.port;
      const age = d.started_at ? (0, sharedState.timeAgo)(d.started_at * 1000) : '';
      setLocalhostPill({
        dotClass: 'ready',
        label: 'localhost:' + d.port,
        title: 'Next.js dev server running' + (age ? ' · started ' + age : '') +
               (d.cmd ? ' · ' + d.cmd : '') +
               (d.target_path ? '\napp: ' + d.target_path : '') +
               (d.cwd ? '\nin: ' + d.cwd : '') +
               ' · click to open',
        href: url,
      });
      return;
    }
    if (d.running && !d.port) {
      sharedState._localhostState = 'stuck';
      const cmd = localhostCommand(d) || 'dev command';
      setLocalhostPill({
        dotClass: 'building',
        label: cmd,
        title: 'Waiting on: ' + cmd +
               (d.target_path ? '\napp: ' + d.target_path : '') +
               (d.cmd && d.cmd !== cmd ? '\nmatched process: ' + d.cmd : '') +
               (d.log_path ? '\nTail the log: ' + d.log_path : '') +
               '\nClick to restart it.',
        href: '',
      });
      return;
    }
    if (d.last_exit) {
      sharedState._localhostState = 'failed';
      const rc = d.last_exit.returncode;
      const tail = (d.last_exit.log_tail || '').slice(-800);
      setLocalhostPill({
        dotClass: 'error',
        label: 'Start failed',
        title: 'Last `' + (d.last_exit.cmd || 'dev') + '` exited' +
               (rc !== null && rc !== undefined ? ' with code ' + rc : '') +
               ' · click to retry · log tail:\n' + tail,
        href: '',
      });
      return;
    }
    sharedState._localhostState = 'idle';
    setLocalhostPill({
      dotClass: '',
      label: '▶ Start localhost',
      title: 'Click to run: ' + (sharedState._localhostLastCommand || 'the detected dev command'),
      href: '',
    });
  }

  function syncAntigravityAccountUi() {
    const row = document.getElementById('antigravityAccountRow');
    const select = document.getElementById('antigravityAccountSelect');
    const badge = document.getElementById('antigravitySessionAccount');
    if (!row || !select || !badge) return;
    const isNew = sharedState.currentConversation === '__new__';
    row.hidden = !(isNew && (0, sharedState.getSpawnEngine)() === 'antigravity');
    if (select.dataset.userPicked !== '1') select.value = sharedState.spawnDefaultsState.antigravity_account_id || 'primary';
    const sid = !isNew && sharedState.currentSession && sharedState.currentSession.source === 'antigravity' ? String(sharedState.currentConversation || '') : '';
    badge.hidden = !sid;
    if (sharedState._antigravityAccountSession === sid) return;
    sharedState._antigravityAccountSession = sid;
    if (!sid) return;
    badge.textContent = 'Antigravity account…';
    fetch('/api/antigravity/accounts?session_id=' + encodeURIComponent(sid)).then(r => {
      if (!r.ok) throw new Error('Account unavailable');
      return r.json();
    }).then(data => {
      if (!data.ok) throw new Error('Account unavailable');
      if (sharedState._antigravityAccountSession === sid) badge.textContent = 'Account: ' + data.session_account;
    }).catch(() => {
      if (sharedState._antigravityAccountSession === sid) {
        badge.textContent = 'Account unavailable';
        sharedState._antigravityAccountSession = '';
      }
    });
  }

  function syncCodexAccountUi() {
    const row = document.getElementById('codexAccountRow');
    const select = document.getElementById('codexAccountSelect');
    const badge = document.getElementById('codexSessionAccount');
    if (!row || !select || !badge) return;
    const isNew = sharedState.currentConversation === '__new__';
    row.hidden = !(isNew && (0, sharedState.getSpawnEngine)() === 'codex');
    const sid = !isNew && sharedState.currentSession && sharedState.currentSession.source === 'codex' ? String(sharedState.currentConversation || '') : '';
    badge.hidden = !sid;
    if (sid && sharedState._codexAccountSession !== sid) {
      sharedState._codexAccountSession = sid;
      badge.textContent = 'Codex account…';
      fetch('/api/codex/accounts?session_id=' + encodeURIComponent(sid)).then(r => r.json()).then(data => {
        if (sharedState._codexAccountSession === sid) {
          const label = data.session_account === 'Codex · current account' ? 'Primary' : data.session_account;
          badge.textContent = label;
        }
      }).catch(() => { if (sharedState._codexAccountSession === sid) badge.textContent = 'Account unavailable'; });
    }
    if (row.hidden || sharedState._codexAccountsLoading || Date.now() - sharedState._codexAccountsLoadedAt < 30000) return;
    sharedState._codexAccountsLoading = true;
    fetch('/api/codex/accounts', { cccPriority: 'user' }).then(r => r.json()).then(data => {
      if (!data.ok) throw new Error('Accounts unavailable');
      // Until the user picks one, start on the saved default account.
      const selected = select.dataset.userPicked === '1' ? select.value : (sharedState.spawnDefaultsState.codex_account_id || '__current__');
      select.replaceChildren(new Option('Primary', '__current__'));
      const secondaryAccounts = (data.accounts || []).filter(account => !account.current);
      secondaryAccounts.forEach((account, index) => {
        const label = secondaryAccounts.length === 1 ? 'Secondary' : (account.label || 'Secondary ' + (index + 1));
        const option = new Option(label, account.id);
        option.title = account.label || label;
        select.add(option);
      });
      if (selected && !Array.from(select.options).some(o => o.value === selected)) {
        select.add(new Option('Selected account unavailable: reconnect in Allowances', selected));
      }
      select.value = selected;
      sharedState._codexAccountsLoadedAt = Date.now();
    }).catch(() => { sharedState._codexAccountsLoadedAt = 0; }).finally(() => { sharedState._codexAccountsLoading = false; });
  }

  function _restoreCwdControlsToInputBar() {
    const cic = document.getElementById('convInputContext');
    if (!cic || !cic.classList.contains('cwd-controls-adopted')) return;
    const slot = document.getElementById('nsCwdSlot');
    const row = slot && slot.querySelector('.spawn-cwd-row');
    const wt = slot && slot.querySelector('.spawn-worktree-row');
    const usage = cic.querySelector('.wp-usage');
    if (row) cic.insertBefore(row, usage || null);
    if (wt) cic.insertBefore(wt, usage || null);
    cic.classList.remove('cwd-controls-adopted');
    const bar = document.getElementById('convInputBar');
    if (bar && sharedState._adoptedComposerHome && sharedState._adoptedComposerHome.parent
        && bar.parentElement !== sharedState._adoptedComposerHome.parent) {
      bar.classList.remove('is-centered-stage');
      sharedState._adoptedComposerHome.parent.insertBefore(bar, sharedState._adoptedComposerHome.next || null);
    }
    sharedState._adoptedComposerHome = null;
  }

  function getVoiceMobileBtnPref() {
    // Hidden unless deliberately enabled. Two saved keys exist for history;
    // every control reads this one answer and writes both (setter below), so
    // no switch can say "on" while the button stays hidden.
    try {
      return localStorage.getItem(sharedState.VOICE_MOBILE_BTN_STORAGE_KEY) !== '0'
        && localStorage.getItem('ccc-voice-call-button') === '1';
    } catch (_) { return false; }
  }
  function _bodyHasAny(cls, test) {
    const list = sharedState._bodyHasLive[cls] || (sharedState._bodyHasLive[cls] = document.getElementsByClassName(cls));
    for (let i = 0; i < list.length; i++) if (test(list[i])) return true;
    return false;
  }
    return { ffcInvalidate, syncConversationNavigationFocus, mountNewSessionChrome, updateSubagentsPanel, updateOrchestrationPane, _restoreCwdControlsToInputBar, mobileShowForCurrentMode, _simpleOnConversationOpen, syncCodexAccountUi, syncAntigravityAccountUi, renderNewSessionObjectContext, _refreshTokenEconomizerAnnotation, ffcRefreshForCurrent, ffcUpdateSidebar, refreshPresentationForPane, _reconcileAnnotationsForPane, renderSpawnStatsPanel, pollLocalhost, orchContext, orchPaintFamilyRows, orchRefreshRegistry, orchContinuationHtml, orchReplayClock, orchCollectLanes, orchLanesAt, orchNodeHtml, orchDrawEdges, mobileShowConv, mobileShowMain, isSimpleMode, _simpleHideHome, _simpleUpdateUsageLine, _simpleUpdateConvTitle, _simpleSyncTechStrip, _paintTokenEconomizerAnnotation, ffcFetch, ffcEnsurePill, _syncFilesPanelChrome, _ffcRecencyLine, renderSidebarFileRow, presentationSessionIdForPane, presentationConversationIdForPane, presentationModeForSession, presentationFeatureEnabled, normalizePresentationMode, syncPresentationToolbar, disconnectPresentationProjection, disconnectPresentationResizeObserver, buildPresentationDeck, presentationRefreshIndex, firstSlideOfLatestPresentationTurn, ensurePresentationProjection, renderMode3BootstrapState, ensurePresentationResizeObserver, _annotationsForSession, _findTextRangeInElement, _annotationSearchRoots, _wrapRangeInAnnotationMark, _insertAnnotationNote, copyTextValue, localhostContext, _localhostCtxKeyOf, setLocalhostPill, localhostUrl, localhostCommand, orchResolveRoot, orchApplyFamilyRows, rememberSidebarFamilyParents, orchLaneMetaRemember, orchFlattenTree, orchLane, orchLaneMetaAll, orchAppendManualLanes, orchReportTimes, orchSpawnTimeFor, getUiMode, _syncSimpleHomeVisibility, _simpleMemoryBannerEnabled, _simpleMemoryPhrase, _simpleMemoryPercent, _tokenEconomizerAnnotationHtml, openFfcModal, _isMarkdownPath, loadMarkdownIntoFileViewer, loadTextIntoFileViewer, sidebarShowFileToast, _isVideoPath, _isHtmlDocPath, _isImageOrPdfPath, openFilePathInMediaLightbox, openArtifactViewer, openArtifactInStatusRail, _canUseStatusRailViewer, presentationModeState, removePresentationProjectionEntry, presentationPageBudget, presentationTurns, mode3SlidesForTurn, paginatePresentationItemsMeasured, paginatePresentationItems, buildPresentationSlide, presentationProjectionSignature, presentationSourceRoot, presentationRootRefreshesDeck, schedulePresentationProjectionFlush, presentationRootsAfterLatestAnswer, requestMode3Bootstrap, _annotationBlockAnchor, orchSyncFamilyDisclosure, orchParseSpawnedAt, orchLaneMetaGet, orchLaneName, orchLaneStatus, _simpleHomeRefresh, _simpleRefreshScreen, ffcRenderRow, _ffcEscHandler, _isPdfPath, openMediaLightbox, presentationPromptText, presentationAnswerBlocks, presentationClone, presentationBlockWeight, buildMode3Slide, presentationItemGroups, paginatePresentationGroups, presentationCandidateFits, buildPresentationDetails, flushPresentationProjection, _subagentClustersExpandedSet, _subagentClustersCollapsedSet, _subagentClustersSaveExpandedSet, _subagentClustersSaveCollapsedSet, orchLiveActivityForLane, orchSpawnExited, _simpleStartArchiveProgress, _simpleIsNyaDismissed, _simpleNyaCardHtml, _simpleRefreshQueueAlerts, applyOptimisticOverrides, _simpleIsWorkingRow, _simpleFetchArchiveStatus, _simpleTaskCardHtml, _simpleWorkingStatusLine, _simpleRenderHistory, _simpleRenderAutomations, _simpleRenderAutomationDetail, _simpleSyncSettingsScreen, ffcEscapeHtml, ffcShowRowToast, closeFfcModal, _ensureMediaLightbox, _mediaPreviewUrl, setMediaLightboxZoom, presentationListItems, mode3Text, mode3StringList, ensurePresentationMeasureSurface, presentationCloneForProjection, _simpleStopArchiveProgress, _simpleLoadNyaDismissed, _simpleNyaKindLabel, _simpleQueueAlertCardHtml, _simpleIsUnseenFinished, _simpleMemoryLine, _simpleBaseName, _simpleHistoryApply, _simpleAutomationCardHtml, _simpleAutomationStatusLine, _simplePlainAge, _simpleQueueItemCardHtml, getVoiceMobileBtnPref, closeMediaLightbox, _simpleLoadSeenMtimes, _simpleHistoryStatusLine, _simplePlainAgeIso, _bodyHasAny };
  }
  root.CCCConversationChrome = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
