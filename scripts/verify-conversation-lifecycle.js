#!/usr/bin/env node

const puppeteer = require('../require-puppeteer.js');

const baseUrl = process.env.CCC_LIFECYCLE_URL || 'http://127.0.0.1:8090';
const timeout = Number(process.env.CCC_LIFECYCLE_TIMEOUT_MS) || 120000;

function pass(label) {
  process.stdout.write(`PASS ${label}\n`);
}

(async () => {
  const browser = await puppeteer.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage();
  let candidate = null;
  let workerCandidate = null;

  async function post(path, payload) {
    return page.evaluate(async ({ path, payload }) => {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      return response.json();
    }, { path, payload });
  }

  async function selectTab(name) {
    await page.waitForSelector(`[data-conv-tab="${name}"]`, { timeout });
    await page.evaluate(tabName => {
      const tab = document.querySelector(`[data-conv-tab="${tabName}"]`);
      if (!tab) throw new Error(`missing tab ${tabName}`);
      tab.click();
    }, name);
  }

  async function clickAction(container, sid, role) {
    await page.evaluate(({ container, sid, role }) => {
      const escaped = (window.CSS && CSS.escape) ? CSS.escape(sid) : sid.replace(/"/g, '\\"');
      const button = document.querySelector(
        `${container} .conv-item[data-session-id="${escaped}"] [data-role="${role}"]`,
      );
      if (!button) throw new Error(`missing ${role} action for ${sid}`);
      button.click();
    }, { container, sid, role });
  }

  async function selectAllLaneForRow(sid) {
    const hasRow = () => page.evaluate(sessionId => {
      const escaped = (window.CSS && CSS.escape) ? CSS.escape(sessionId) : sessionId.replace(/"/g, '\\"');
      return !!document.querySelector(`.conv-archived-list .conv-item[data-session-id="${escaped}"]`);
    }, sid);
    if (await hasRow()) return;
    for (const lane of ['coding', 'workers', 'messages']) {
      await page.evaluate(laneName => {
        const tab = document.querySelector(`[data-all-hermes-tab="${laneName}"]`);
        if (tab) tab.click();
      }, lane);
      try {
        await page.waitForFunction(sessionId => {
          const escaped = (window.CSS && CSS.escape) ? CSS.escape(sessionId) : sessionId.replace(/"/g, '\\"');
          return !!document.querySelector(`.conv-archived-list .conv-item[data-session-id="${escaped}"]`);
        }, { timeout: 5000 }, sid);
        return;
      } catch (_) {}
    }
  }

  async function selectAllLane(lane) {
    await page.waitForSelector(`[data-all-hermes-tab="${lane}"]`, { timeout });
    await page.evaluate(laneName => {
      const tab = document.querySelector(`[data-all-hermes-tab="${laneName}"]`);
      if (!tab) throw new Error(`missing All lane ${laneName}`);
      tab.click();
    }, lane);
  }

  async function waitForActions(container, sid, expected) {
    await page.waitForFunction(({ container, sid, expected }) => {
      const escaped = (window.CSS && CSS.escape) ? CSS.escape(sid) : sid.replace(/"/g, '\\"');
      const row = document.querySelector(`${container} .conv-item[data-session-id="${escaped}"]`);
      if (!row) return false;
      return Object.entries(expected).every(([role, count]) =>
        row.querySelectorAll(`[data-role="${role}"]`).length === count
      );
    }, { timeout }, { container, sid, expected });
  }

  try {
    await page.evaluateOnNewDocument(() => {
      localStorage.setItem('ccc-sidebar-tab', 'inprogress');
      localStorage.setItem('ccc-archive-window', 'all');
      localStorage.setItem('ccc-all-trash-collapsed', '0');
    });
    await page.goto(baseUrl, { waitUntil: 'load', timeout });
    await page.waitForSelector('[data-role="conv-tab-bar"]', { timeout });

    const rows = await page.evaluate(async () => {
      const response = await fetch('/api/conversations/all?stale_ok=1');
      const data = await response.json();
      return Array.isArray(data.conversations) ? data.conversations : [];
    });
    const visibleActive = new Set(await page.$$eval(
      '.conv-item[data-session-id]',
      elements => elements.map(el => el.dataset.sessionId).filter(Boolean),
    ));
    const eligible = row => row
      && (row.session_id || row.id)
      && !row.is_live
      && row.source !== 'backlog'
      && row.source !== 'github_pr'
      && !String(row.session_id || row.id).startsWith('pkood-');
    candidate = rows.find(row => eligible(row)
      && !row.archived
      && !row.all_lane_override
      && row.source !== 'hermes'
      && visibleActive.has(row.session_id || row.id))
      || rows.find(row => eligible(row) && row.archived && !row.trashed)
      || rows.find(eligible);
    if (!candidate) throw new Error('no dormant conversation is available for lifecycle verification');

    const sid = candidate.session_id || candidate.id;
    const convId = candidate.id || sid;
    process.stdout.write(`Using dormant conversation ${sid}\n`);
    const archivePath = `/api/conversations/${encodeURIComponent(convId)}/archive`;
    const trashPath = `/api/conversations/${encodeURIComponent(convId)}/trash`;

    const activeSetup = await post(archivePath, { session_id: sid, archived: false });
    if (!activeSetup.ok) throw new Error(activeSetup.error || 'could not prepare Active state');
    await page.reload({ waitUntil: 'load', timeout });
    await selectTab('inprogress');
    await waitForActions('#convList', sid, { archive: 1, trash: 0, untrash: 0 });
    pass('Active tab / Active has Pin + Archive lifecycle action');

    await clickAction('#convList', sid, 'archive');
    await selectTab('archived');
    await selectAllLaneForRow(sid);
    await waitForActions('.conv-archived-list', sid, { archive: 1, trash: 1, untrash: 0 });
    pass('All main / Archived has Move to Active + Trash');

    await clickAction('.conv-archived-list', sid, 'archive');
    await waitForActions('.conv-archived-list', sid, { archive: 0, trash: 1, untrash: 0 });
    pass('All main / Active has Trash and no Archive');

    await clickAction('.conv-archived-list', sid, 'trash');
    await waitForActions('.conv-trash-list', sid, { archive: 0, trash: 0, untrash: 1, pin: 0 });
    pass('Trash / Trashed has Untrash only');

    await clickAction('.conv-trash-list', sid, 'untrash');
    await selectAllLaneForRow(sid);
    await waitForActions('.conv-archived-list', sid, { archive: 1, trash: 1, untrash: 0 });
    pass('Untrash returns the conversation to Archived in All main');

    await selectAllLane('workers');
    const workerSids = new Set(await page.$$eval(
      '.conv-archived-list .conv-item[data-session-id]',
      elements => elements.map(el => el.dataset.sessionId).filter(Boolean),
    ));
    workerCandidate = rows.find(row => {
      const rowSid = row && (row.session_id || row.id);
      return rowSid && rowSid !== sid && workerSids.has(rowSid) && eligible(row) && !row.trashed;
    });
    if (!workerCandidate) throw new Error('no dormant Workers-lane conversation is available for Trash verification');
    const workerSid = workerCandidate.session_id || workerCandidate.id;
    await waitForActions('.conv-archived-list', workerSid, { trash: 1, untrash: 0 });
    const trashResponse = page.waitForResponse(response =>
      response.request().method() === 'POST'
      && response.url().includes(`/api/conversations/${encodeURIComponent(workerSid)}/trash`),
      { timeout },
    );
    await clickAction('.conv-archived-list', workerSid, 'trash');
    await trashResponse;
    await page.reload({ waitUntil: 'load', timeout });
    await selectTab('archived');
    await selectAllLane('workers');
    await waitForActions('.conv-trash-list', workerSid, { trash: 0, untrash: 1 });
    pass('All / Workers Trash survives refresh in the Trash bucket');
  } finally {
    if (workerCandidate) {
      const sid = workerCandidate.session_id || workerCandidate.id;
      const convId = workerCandidate.id || sid;
      const archivePath = `/api/conversations/${encodeURIComponent(convId)}/archive`;
      const trashPath = `/api/conversations/${encodeURIComponent(convId)}/trash`;
      try {
        if (workerCandidate.trashed) {
          await post(trashPath, { session_id: sid, trashed: true });
        } else if (workerCandidate.archived) {
          await post(trashPath, { session_id: sid, trashed: false });
          await post(archivePath, { session_id: sid, archived: true });
        } else {
          await post(trashPath, { session_id: sid, trashed: false });
          await post(archivePath, { session_id: sid, archived: false });
        }
      } catch (error) {
        process.stderr.write(`WARN could not restore worker ${sid}: ${error.message}\n`);
      }
    }
    if (candidate) {
      const sid = candidate.session_id || candidate.id;
      const convId = candidate.id || sid;
      const archivePath = `/api/conversations/${encodeURIComponent(convId)}/archive`;
      const trashPath = `/api/conversations/${encodeURIComponent(convId)}/trash`;
      try {
        if (candidate.trashed) {
          await post(trashPath, { session_id: sid, trashed: true });
        } else if (candidate.archived) {
          await post(trashPath, { session_id: sid, trashed: false });
          await post(archivePath, { session_id: sid, archived: true });
        } else {
          await post(archivePath, { session_id: sid, archived: false });
        }
      } catch (error) {
        process.stderr.write(`WARN could not restore ${sid}: ${error.message}\n`);
      }
    }
    await browser.close();
  }
})().catch(error => {
  process.stderr.write(`FAIL ${error.stack || error.message}\n`);
  process.exitCode = 1;
});
