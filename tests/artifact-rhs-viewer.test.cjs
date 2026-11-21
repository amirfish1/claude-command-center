const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const chromeJs = fs.readFileSync(require.resolve('../static/conversation-chrome.js'), 'utf8');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const sampleSvg = 'data:image/svg+xml,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800"><rect width="1200" height="800" fill="#1f6feb"/><text x="40" y="100" fill="#fff" font-size="48">Artifact preview</text></svg>'
);

test('Artifacts open in the RHS status rail on desktop and fall back to lightbox on mobile', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 900 });
    await page.setRequestInterception(true);
    page.on('request', (req) => {
      if (req.url() === 'http://ccc-artifact.test/') {
        req.respond({
          status: 200,
          contentType: 'text/html',
          body: `<!doctype html>
            <style>${css}
              body { margin: 0; display: flex; height: 100vh; }
              #sessionLeft { flex: 1; min-width: 260px; padding: 16px; }
              #statusRail { width: var(--status-rail-width, 280px); display: flex; flex-direction: column; border-left: 1px solid #30363d; }
            </style>
            <body class="status-pos-right">
              <main id="sessionLeft">Active session</main>
              <aside id="statusRail" class="status-rail">
                <div id="statusRailResizer"></div>
                <div id="statusRailShell">Rail tabs</div>
                <div class="status-rail-file-viewer" id="statusRailFileViewer" style="display: none;">
                  <div class="file-viewer-header">
                    <div class="file-viewer-title-group">
                      <span class="file-viewer-title" id="fileViewerTitle">File Preview</span>
                      <span class="file-viewer-filename" id="fileViewerFilename"></span>
                    </div>
                    <button id="fileViewerRefreshBtn" class="file-viewer-refresh" type="button">R</button>
                    <a id="fileViewerDownloadBtn" class="file-viewer-action" download hidden>D</a>
                    <button id="fileViewerFullscreenBtn" class="file-viewer-action" type="button" hidden>F</button>
                    <button id="fileViewerOpenExternalBtn" class="file-viewer-open-external" type="button">Open</button>
                    <button id="fileViewerCloseBtn" class="file-viewer-close" type="button">&times;</button>
                  </div>
                  <div class="file-viewer-body markdown-body assistant-text" id="fileViewerBody"></div>
                </div>
              </aside>
            </body>`,
        });
      } else {
        req.continue();
      }
    });

    await page.goto('http://ccc-artifact.test/');
    await page.addScriptTag({ content: chromeJs });
    await page.addScriptTag({
      content: `
        window._mockMobile = false;
        window._expandedCalls = 0;
        window._cccExpandStatusRailForFileViewer = () => {
          window._expandedCalls++;
          document.documentElement.style.setProperty('--status-rail-width', '640px');
        };
        const state = {
          _imgLightbox: null,
          _lightboxZoom: 0,
          _lightboxHistoryPushed: false,
          sessionIdByConv: {},
          currentConversation: '',
          currentSession: null,
          escapeAttr: (s) => String(s || '').replace(/"/g, '&quot;'),
          isMobile: () => window._mockMobile,
        };
        window.ctrl = window.CCCConversationChrome.createController({ state });
      `,
    });

    // 1. Image opens in RHS bar, not in lightbox modal.
    await page.evaluate((src) => {
      window.ctrl.openArtifactViewer(src, { kind: 'image', title: 'diagram.png', filePath: '/tmp/diagram.png' });
    }, sampleSvg);
    await page.waitForFunction(() => Boolean(document.querySelector('#fileViewerBody img')?.dataset.scale));

    const imgCheck = await page.evaluate(() => {
      const rail = document.getElementById('statusRail');
      const left = document.getElementById('sessionLeft').getBoundingClientRect();
      const right = rail.getBoundingClientRect();
      const lb = document.querySelector('.img-lightbox');
      return {
        railActive: rail.classList.contains('file-viewer-active'),
        lightboxOpen: Boolean(lb && lb.classList.contains('open')),
        title: document.getElementById('fileViewerTitle').textContent,
        filename: document.getElementById('fileViewerFilename').textContent,
        dlHidden: document.getElementById('fileViewerDownloadBtn').hidden,
        fsHidden: document.getElementById('fileViewerFullscreenBtn').hidden,
        extHidden: document.getElementById('fileViewerOpenExternalBtn').hidden,
        sideBySide: left.width >= 260 && right.width >= 600 && left.right <= right.left + 1,
        expandedCalls: window._expandedCalls,
      };
    });
    assert.equal(imgCheck.railActive, true, 'RHS file viewer should be active');
    assert.equal(imgCheck.lightboxOpen, false, 'Lightbox modal should NOT open on desktop');
    assert.equal(imgCheck.title, 'Image Preview');
    assert.equal(imgCheck.filename, 'diagram.png');
    assert.equal(imgCheck.dlHidden, false);
    assert.equal(imgCheck.fsHidden, false);
    assert.equal(imgCheck.extHidden, false);
    assert.equal(imgCheck.sideBySide, true, 'Session stays on the left while artifact shows on the right');
    assert.equal(imgCheck.expandedCalls, 1);

    // 2. HTML / PDF frame opens in RHS bar with iframe.
    await page.evaluate(() => {
      window.ctrl.openArtifactViewer('about:blank', { kind: 'frame', title: 'report.pdf', filePath: '/tmp/report.pdf' });
    });
    const frameCheck = await page.evaluate(() => ({
      hasIframe: Boolean(document.querySelector('#fileViewerBody iframe.file-viewer-frame')),
      title: document.getElementById('fileViewerTitle').textContent,
      filename: document.getElementById('fileViewerFilename').textContent,
      viewerKind: document.getElementById('statusRailFileViewer').getAttribute('data-viewer-kind'),
    }));
    assert.equal(frameCheck.hasIframe, true);
    assert.equal(frameCheck.title, 'PDF Preview');
    assert.equal(frameCheck.filename, 'report.pdf');
    assert.equal(frameCheck.viewerKind, 'frame');

    // 3. Video opens in RHS bar with <video>.
    await page.evaluate(() => {
      window.ctrl.openArtifactViewer('about:blank', { kind: 'video', title: 'demo.mp4' });
    });
    const videoCheck = await page.evaluate(() => ({
      hasVideo: Boolean(document.querySelector('#fileViewerBody video.file-viewer-video')),
      title: document.getElementById('fileViewerTitle').textContent,
      extHidden: document.getElementById('fileViewerOpenExternalBtn').hidden,
    }));
    assert.equal(videoCheck.hasVideo, true);
    assert.equal(videoCheck.title, 'Video Preview');
    assert.equal(videoCheck.extHidden, true, 'Open external hides when no local filePath is present');

    // 4. Mobile falls back to lightbox modal.
    await page.evaluate((src) => {
      window._mockMobile = true;
      window.ctrl.openArtifactViewer(src, { kind: 'image', title: 'mobile.png' });
    }, sampleSvg);
    const mobileCheck = await page.evaluate(() => ({
      lightboxOpen: Boolean(document.querySelector('.img-lightbox')?.classList.contains('open')),
    }));
    assert.equal(mobileCheck.lightboxOpen, true, 'Mobile should fall back to lightbox modal');
  } finally {
    await browser.close();
  }
});
