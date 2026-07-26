#!/usr/bin/env node

const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const baseUrl = process.env.CCC_WHATS_NEW_NOTICE_URL || 'http://127.0.0.1:8090';
const screenshotPath = process.env.CCC_WHATS_NEW_NOTICE_SCREENSHOT
  || '/tmp/ccc-whats-new-notice.png';
const timeout = Number(process.env.CCC_WHATS_NEW_NOTICE_TIMEOUT_MS) || 90000;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: findChromePath(),
    args: ['--no-sandbox'],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 800 });
    await page.setCacheEnabled(false);
    await page.evaluateOnNewDocument(() => {
      localStorage.setItem('ccc-tour-done', 'skipped');
      localStorage.setItem('ccc-pwa-install-dismissed', String(Date.now()));
      localStorage.removeItem('ccc-last-seen-version');
      localStorage.removeItem('ccc-whats-new-dismissed-version');
    });

    const waitForActionableNotice = async () => {
      await page.waitForFunction(() => {
        const notice = document.getElementById('whatsNewNotice');
        const loading = document.getElementById('cccLoadingOverlay');
        return notice?.classList.contains('visible')
          && loading?.classList.contains('gone')
          && !document.body.classList.contains('resizing');
      }, { timeout });
    };

    await page.goto(baseUrl, { waitUntil: 'load', timeout });
    await waitForActionableNotice();

    const startup = await page.evaluate(() => {
      const button = document.getElementById('sidebarNewGroupChatBtn');
      const rect = button.getBoundingClientRect();
      const hit = document.elementFromPoint(
        rect.left + rect.width / 2,
        rect.top + rect.height / 2,
      );
      return {
        modalOpen: document.getElementById('whatsNewModal').classList.contains('open'),
        noticeVisible: document.getElementById('whatsNewNotice').classList.contains('visible'),
        groupChatHitTarget: hit?.closest('#sidebarNewGroupChatBtn')?.id || '',
      };
    });
    assert(startup.noticeVisible, 'version mismatch did not show the compact notice');
    assert(!startup.modalOpen, 'startup opened the blocking What’s New modal');
    assert(
      startup.groupChatHitTarget === 'sidebarNewGroupChatBtn',
      'New Group chat is not the first-click hit target',
    );

    await page.screenshot({ path: screenshotPath });

    await page.evaluate(() => {
      window.__cccGroupChatPromptCount = 0;
      window.prompt = () => {
        window.__cccGroupChatPromptCount += 1;
        return null;
      };
    });
    await page.click('#sidebarNewGroupChatBtn');
    await page.waitForFunction(
      () => window.__cccGroupChatPromptCount === 1,
      { timeout: 5000 },
    );

    await page.click('#whatsNewNoticeOpen');
    await page.waitForFunction(
      () => document.getElementById('whatsNewModal').classList.contains('open')
        && !document.getElementById('whatsNewNotice').classList.contains('visible'),
      { timeout: 5000 },
    );
    // The backdrop fills the viewport but its geometric center is covered by
    // the dialog. Click a visible edge coordinate to exercise real hit-testing.
    const backdropAtEdge = await page.evaluate(
      () => document.elementFromPoint(4, 400)?.matches('#whatsNewBackdrop'),
    );
    assert(backdropAtEdge, 'modal backdrop is not the edge hit target');
    await page.mouse.click(4, 400);
    await page.waitForFunction(
      () => !document.getElementById('whatsNewModal').classList.contains('open'),
      { timeout: 5000 },
    );

    await page.evaluate(() => localStorage.removeItem('ccc-last-seen-version'));
    await page.reload({ waitUntil: 'load', timeout });
    await waitForActionableNotice();
    await page.click('#whatsNewNoticeDismiss');
    const dismissal = await page.evaluate(() => ({
      noticeVisible: document.getElementById('whatsNewNotice').classList.contains('visible'),
      lastSeen: localStorage.getItem('ccc-last-seen-version'),
    }));
    assert(!dismissal.noticeVisible, 'dismiss button left the notice visible');
    assert(dismissal.lastSeen, 'dismiss button did not persist the seen version');

    process.stdout.write(`VERIFIED What’s New notice; screenshot: ${screenshotPath}\n`);
  } catch (error) {
    process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
