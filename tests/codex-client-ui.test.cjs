const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const puppeteer = require('puppeteer');

const clientPath = path.resolve('static/codex-client.js');
const appSource = require('node:fs').readFileSync('static/app.js', 'utf8');
const indexSource = require('node:fs').readFileSync('static/index.html', 'utf8');
const clientCss = require('node:fs').readFileSync('static/codex-client.css', 'utf8');
let browser;

test.before(async () => { browser = await puppeteer.launch({ headless: true }); });
test.after(async () => { await browser?.close(); });

async function barePage() {
  const page = await browser.newPage();
  await page.setContent(`
    <style>:root{--bg:#111;--bg-secondary:#181818;--text:#eee;--text-muted:#999;--border:#333;--accent:#d97757}</style>
    <div class="conv-pane is-codex-session" data-pane-id="p1">
      <div class="conv-pane-header"><span class="conv-pane-actions"></span></div>
      <div class="conversations-view"></div>
      <div class="conv-input-bar"></div>
    </div>`);
  await page.evaluate(() => {
    window.CCCCodexClientContext = () => ({
      threadId: 'thread-1', repoPath: '/tmp/repo', paneId: 'p1',
      paneEl: document.querySelector('.conv-pane'), title: 'Fixture task',
    });
    window.CCCCodexMarkdown = text => '<p>' + String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;') + '</p>';
  });
  await page.addScriptTag({ path: clientPath });
  return page;
}

test('schema forms resolve refs, variants, arrays, maps, nullable and secret fields', async () => {
  const page = await barePage();
  try {
    const result = await page.evaluate(async () => {
      const schema = {
        type: 'object', required: ['name', 'auth', 'targets'],
        properties: {
          name: { type: 'string', title: 'Task name' },
          auth: { $ref: '#/$defs/Auth' },
          targets: { type: 'array', items: { type: 'string' } },
          labels: { type: 'object', additionalProperties: { type: 'string' } },
          note: { type: ['string', 'null'] },
          payload: { type: 'string', contentEncoding: 'base64', title: 'Attachment' },
        },
        $defs: {
          Auth: { oneOf: [
            { title: 'Token', type: 'object', required: ['kind', 'token'], properties: {
              kind: { const: 'token' }, token: { type: 'string', writeOnly: true },
            } },
            { title: 'Profile', type: 'object', required: ['kind', 'profile'], properties: {
              kind: { const: 'profile' }, profile: { type: 'string' },
            } },
          ] },
        },
      };
      const built = window.CCCCodexClient.__testing.createForm(schema, {
        name: 'Review', auth: { kind: 'token', token: 'secret' }, targets: ['src'],
        labels: { team: 'ui' }, note: null,
      }, { threadId: 'thread-1', repoPath: '/tmp/repo' });
      document.body.append(built.element);
      return {
        value: await built.read(),
        variants: built.element.querySelectorAll('[data-schema-variant]').length,
        arrays: built.element.querySelectorAll('[data-array-add]').length,
        maps: built.element.querySelectorAll('[data-map-add]').length,
        secretType: built.element.querySelector('input[name$="token"]').type,
        nullable: !!built.element.querySelector('[data-null-toggle]'),
        fileAdapter: !!built.element.querySelector('[data-file-adapter]'),
      };
    });
    assert.deepEqual(result.value, {
      name: 'Review', auth: { kind: 'token', token: 'secret' }, targets: ['src'],
      labels: { team: 'ui' }, note: null,
    });
    assert.equal(result.variants, 1);
    assert.equal(result.arrays, 1);
    assert.equal(result.maps, 1);
    assert.equal(result.secretType, 'password');
    assert.equal(result.nullable, true);
    assert.equal(result.fileAdapter, true);
  } finally { await page.close(); }
});

test('pending request answers use exact registry response shapes', async () => {
  const page = await barePage();
  try {
    const shapes = await page.evaluate(() => {
      const shape = window.CCCCodexClient.__testing.shapePendingResponse;
      return {
        questions: shape({ method: 'item/tool/requestUserInput' }, { answers: { q1: ['One'] } }),
        cancelQuestions: shape({ method: 'item/tool/requestUserInput' }, { cancel: true }),
        approval: shape({ method: 'item/commandExecution/requestApproval' }, { decision: 'acceptForSession' }),
        legacy: shape({ method: 'execCommandApproval' }, { decision: 'acceptForSession' }),
        permissions: shape({ method: 'permissions/request', params: { permissions: { network: true, files: ['a'] } } }, { permissions: { network: true }, scope: 'session' }),
        deniedPermissions: shape({ method: 'permissions/request', params: { permissions: { network: true } } }, { decline: true }),
        elicitation: shape({ method: 'mcpServer/elicitation/request' }, { action: 'accept', content: { project: 'ccc' } }),
      };
    });
    assert.deepEqual(shapes.questions, { answers: { q1: { answers: ['One'] } } });
    assert.deepEqual(shapes.cancelQuestions, { answers: {} });
    assert.deepEqual(shapes.approval, { decision: 'acceptForSession' });
    assert.deepEqual(shapes.legacy, { decision: 'approved_for_session' });
    assert.deepEqual(shapes.permissions, { permissions: { network: true }, scope: 'session' });
    assert.deepEqual(shapes.deniedPermissions, { permissions: {}, scope: 'turn' });
    assert.deepEqual(shapes.elicitation, { action: 'accept', content: { project: 'ccc' } });
  } finally { await page.close(); }
});

test('thread items render safe markdown with distinct commentary and final phases', async () => {
  const page = await barePage();
  try {
    const result = await page.evaluate(() => {
      window.__xss = 0;
      window.CCCCodexMarkdown = value => String(value);
      const render = window.CCCCodexClient.__testing.renderItem;
      const commentary = render({ type: 'agentMessage', phase: 'commentary', text: 'Working <img src=x onerror="window.__xss=1"><svg><a xlink:href="javascript:window.__xss=3">bad</a></svg>' });
      const final = render({ type: 'agentMessage', phase: 'final_answer', text: '<a href="javascript:window.__xss=2">bad</a><button formaction="javascript:window.__xss=4">bad</button><strong>Done</strong>' });
      const secret = render({ type: 'unknown', accessToken: 'super-secret-fixture' });
      document.body.append(commentary, final, secret);
      return {
        commentary: commentary.className,
        final: final.className,
        images: document.querySelectorAll('.codex-client-item img').length,
        badLinks: document.querySelectorAll('.codex-client-item a[href^="javascript:"]').length,
        svg: document.querySelectorAll('.codex-client-item svg').length,
        formActions: document.querySelectorAll('.codex-client-item [formaction]').length,
        leakedSecret: document.body.textContent.includes('super-secret-fixture'),
        xss: window.__xss,
      };
    });
    assert.match(result.commentary, /is-commentary/);
    assert.match(result.final, /is-final-answer/);
    assert.equal(result.images, 0);
    assert.equal(result.badLinks, 0);
    assert.equal(result.svg, 0);
    assert.equal(result.formActions, 0);
    assert.equal(result.leakedSecret, false);
    assert.equal(result.xss, 0);
  } finally { await page.close(); }
});

test('history pagination never reuses the event sequence cursor', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      const calls = [];
      window.__calls = calls;
      window.fetch = async (url, options = {}) => {
        calls.push(String(url));
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, version: 1, fingerprint: 'fp', experimental_enabled: false, methods: [], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g1', cursor: 42, connected: true, thread: { id: 'thread-1', cwd: '/tmp/repo', turns: [] }, truncated: true, next_cursor: u.includes('cursor=history-opaque') ? null : 'history-opaque', requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g1', cursor: 43, connected: true, resync_required: false, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.waitForSelector('.codex-client-shell');
    await page.evaluate(() => window.CCCCodexClient.__testing.pollNow());
    await page.evaluate(() => window.CCCCodexClient.__testing.loadEarlier());
    const calls = await page.evaluate(() => window.__calls);
    assert.ok(calls.some(url => /events.*cursor=42/.test(url)), calls.join('\n'));
    assert.ok(calls.some(url => /history.*cursor=history-opaque/.test(url)), calls.join('\n'));
    assert.equal(calls.some(url => /history.*cursor=42/.test(url)), false);
  } finally { await page.close(); }
});

test('a mutation is single-flight and close cleans up before reopening', async () => {
  const page = await barePage();
  try {
    const result = await page.evaluate(async () => {
      let resolveOperation;
      let mutations = 0;
      window.CCCCodexClient.__testing.state.generation = 'test-generation';
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/operation')) {
          mutations++;
          return new Promise(resolve => { resolveOperation = () => resolve({ ok: true, json: async () => ({ ok: true, result: {} }) }); });
        }
        throw new Error('unexpected ' + u);
      };
      const run = window.CCCCodexClient.__testing.runOperation;
      const first = run('turn/interrupt', { threadId: 'thread-1' }, { threadId: 'thread-1', repoPath: '/tmp/repo' });
      const second = run('turn/interrupt', { threadId: 'thread-1' }, { threadId: 'thread-1', repoPath: '/tmp/repo' });
      await Promise.resolve();
      resolveOperation();
      const values = await Promise.all([first, second]);
      document.body.insertAdjacentHTML('beforeend', '<div class="codex-client-shell"></div>');
      window.CCCCodexClient.close();
      return { mutations, duplicateSkipped: values[1]?.skipped === true, shells: document.querySelectorAll('.codex-client-shell').length };
    });
    assert.deepEqual(result, { mutations: 1, duplicateSkipped: true, shells: 0 });
  } finally { await page.close(); }
});

test('a write establishes and carries the current connection generation', async () => {
  const page = await barePage();
  try {
    const result = await page.evaluate(async () => {
      const bodies = [];
      window.CCCCodexClient.__testing.state.catalog = { methods: [
        { method: 'account/read', read_only: true },
        { method: 'thread/name/set', read_only: false },
      ] };
      window.CCCCodexClient.__testing.state.generation = null;
      window.fetch = async (_url, options = {}) => {
        const body = JSON.parse(options.body); bodies.push(body);
        if (body.method === 'account/read') return { ok: true, json: async () => ({ ok: true, generation: 'native-7', result: { account: null } }) };
        return { ok: true, json: async () => ({ ok: true, generation: 'native-8', result: { name: 'New name' } }) };
      };
      await window.CCCCodexClient.__testing.runOperation('thread/name/set', { threadId: 'thread-1', name: 'New name' }, { threadId: 'thread-1', repoPath: '/tmp/repo' });
      return { methods: bodies.map(body => body.method), writeGeneration: bodies[1].generation, current: window.CCCCodexClient.__testing.state.generation };
    });
    assert.deepEqual(result, { methods: ['account/read', 'thread/name/set'], writeGeneration: 'native-7', current: 'native-8' });
  } finally { await page.close(); }
});

test('a stale-generation rejection updates the receipt without retrying the write', async () => {
  const page = await barePage();
  try {
    const result = await page.evaluate(async () => {
      let writes = 0;
      window.CCCCodexClient.__testing.state.catalog = { methods: [{ method: 'thread/archive', read_only: false }] };
      window.CCCCodexClient.__testing.state.generation = 'old';
      window.fetch = async () => {
        writes++;
        return { ok: false, json: async () => ({ ok: false, generation: 'fresh', error: 'Stale connection generation' }) };
      };
      let message = '';
      try { await window.CCCCodexClient.__testing.runOperation('thread/archive', { threadId: 'thread-1' }, { threadId: 'thread-1', repoPath: '/tmp/repo' }); }
      catch (error) { message = error.message; }
      return { writes, current: window.CCCCodexClient.__testing.state.generation, message };
    });
    assert.equal(result.writes, 1);
    assert.equal(result.current, 'fresh');
    assert.match(result.message, /stale/i);
  } finally { await page.close(); }
});

test('the dashboard loads the workspace assets and exposes selected Codex context', () => {
  assert.match(indexSource, /\/static\/codex-client\.css/);
  assert.match(indexSource, /<script src="\/static\/codex-client\.js"><\/script>/);
  assert.match(appSource, /window\.C{4}odexClientContext\s*=/);
  assert.match(appSource, /_enginePaneEl\.classList\.toggle\('is-codex-session', data\.engine === 'codex'\)/);
});

test('normal Codex reasoning gets Markdown spacing without changing Kimi selectors', () => {
  assert.match(appSource, /_kimiThinkingHtml\(String\(b\.text\), _codexPane\)/);
  assert.match(appSource, /function _kimiThinkingHtml\(text, renderAsMarkdown\)/);
  assert.match(clientCss, /\.conv-pane\.is-codex-session:not\(\.codex-client-open\) \.kimi-thinking-full/);
  assert.doesNotMatch(clientCss, /\.conv-pane\.is-kimi-session/);
});

test('surface navigation categorizes every catalog action with plain labels', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.fetch = async url => {
        if (String(url).includes('/catalog')) return { ok: true, json: async () => ({
          ok: true, fingerprint: 'surface-fixture', experimental_enabled: false,
          methods: [
            { method: 'thread/fork', title: 'Fork task', group: 'threads', read_only: false, available: true },
            { method: 'fs/readFile', title: 'Read file', group: 'files', read_only: true, available: true },
            { method: 'account/read', title: 'Account', group: 'accounts', read_only: true, available: true },
            { method: 'plugin/install', title: 'Install plugin', group: 'plugins', read_only: false, available: false, unavailable_reason: 'Requires a newer Codex version' },
          ], server_requests: [], notifications: [],
        }) };
        if (String(url).includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (String(url).includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected');
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.click('[data-surface="workspace"]');
    const workspace = await page.evaluate(() => ({
      surface: document.querySelector('.codex-client-shell').dataset.surface,
      labels: Array.from(document.querySelectorAll('.codex-client-action strong')).map(node => node.textContent),
      text: document.querySelector('[data-codex-catalog]').textContent,
    }));
    assert.deepEqual(workspace.labels, ['Read file']);
    assert.equal(workspace.surface, 'workspace');
    assert.doesNotMatch(workspace.text, /fs\/readFile/);
    await page.click('[data-surface="settings"]');
    const settings = await page.evaluate(() => ({
      labels: Array.from(document.querySelectorAll('.codex-client-action strong')).map(node => node.textContent).sort(),
      text: document.querySelector('[data-codex-catalog]').textContent,
    }));
    assert.deepEqual(settings.labels, ['Account', 'Install plugin']);
    assert.match(settings.text, /Requires a newer Codex version/);
  } finally { await page.close(); }
});

test('free-form maps exclude named object fields from map rows', async () => {
  const page = await barePage();
  try {
    const result = await page.evaluate(async () => {
      const built = window.CCCCodexClient.__testing.createForm({
        type: 'object', properties: { mode: { type: 'string' } }, additionalProperties: true,
      }, { mode: 'safe', team: 'ui' }, {});
      document.body.append(built.element);
      return { value: await built.read(), rows: built.element.querySelectorAll('.codex-schema-map-row').length };
    });
    assert.deepEqual(result, { value: { mode: 'safe', team: 'ui' }, rows: 1 });
  } finally { await page.close(); }
});

test('a nullable union renders its single non-null branch as a structured form', async () => {
  const page = await barePage();
  try {
    const result = await page.evaluate(async () => {
      const built = window.CCCCodexClient.__testing.createForm({
        anyOf: [
          { type: 'object', required: ['label'], properties: { label: { type: 'string' } } },
          { type: 'null' },
        ],
      }, { label: 'Native' }, {});
      document.body.append(built.element);
      return { value: await built.read(), inputs: built.element.querySelectorAll('input[name="label"]').length };
    });
    assert.deepEqual(result, { value: { label: 'Native' }, inputs: 1 });
  } finally { await page.close(); }
});

test('a parameterless mutation waits for its confirmation form', async () => {
  const page = await barePage();
  try {
    const result = await page.evaluate(async () => {
      let writes = 0;
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'fp', experimental_enabled: true, methods: [{ method: 'thread/archive', title: 'Archive task', group: 'threads', read_only: false, experimental: true, available: true }], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method: 'thread/archive', title: 'Archive task', read_only: false, experimental: true, params_type: 'null', params_schema: { type: 'object', additionalProperties: false }, description: 'Internal RPC wire operation for archive_task_v2' } }) };
        if (u.includes('/operation')) { writes++; return { ok: true, json: async () => ({ ok: true, generation: 'g2', result: {} }) }; }
        if (u.includes('/state')) return { ok: true, json: async () => ({ ok: true, generation: 'g2', cursor: 2, connected: true, thread: { id: 'thread-1', turns: [] }, requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g2', cursor: 2, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u + ' ' + (options.method || 'GET'));
      };
      await window.CCCCodexClient.open(window.CCCCodexClientContext());
      document.querySelector('[data-method="thread/archive"]').click();
      await new Promise(resolve => setTimeout(resolve, 0));
      const before = { writes, dialog: !!document.querySelector('.codex-client-dialog'), copy: document.querySelector('.codex-client-dialog')?.textContent || '' };
      document.querySelector('.codex-client-dialog form').requestSubmit();
      await new Promise(resolve => setTimeout(resolve, 20));
      return { before, afterWrites: writes };
    });
    assert.deepEqual({ writes: result.before.writes, dialog: result.before.dialog }, { writes: 0, dialog: true });
    assert.doesNotMatch(result.before.copy, /Internal RPC wire|archive_task_v2/);
    assert.equal(result.afterWrites, 1);
  } finally { await page.close(); }
});

test('loading earlier history preserves the visible transcript position', async () => {
  const page = await barePage();
  try {
    const result = await page.evaluate(async () => {
      let historyCalls = 0;
      const turns = (prefix, count) => Array.from({ length: count }, (_, index) => ({ id: prefix + index, status: 'completed', items: [{ type: 'userMessage', text: prefix + index }] }));
      window.fetch = async url => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'fp', methods: [], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) {
          historyCalls++;
          return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 5, connected: true, thread: { id: 'thread-1', turns: historyCalls === 1 ? turns('new-', 30) : turns('old-', 10) }, next_cursor: historyCalls === 1 ? 'older' : null, requests: [] }) };
        }
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 5, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
      const style = document.createElement('style');
      style.textContent = '.codex-client-transcript{display:block;height:100px;overflow:auto}.codex-client-message{display:block;height:28px;margin:0;padding:0}';
      document.head.append(style);
      await window.CCCCodexClient.open(window.CCCCodexClientContext());
      const transcript = document.querySelector('.codex-client-transcript');
      transcript.scrollTop = 180;
      const before = { top: transcript.scrollTop, height: transcript.scrollHeight };
      await window.CCCCodexClient.__testing.loadEarlier();
      return { before, after: { top: transcript.scrollTop, height: transcript.scrollHeight } };
    });
    assert.equal(result.after.top - result.before.top, result.after.height - result.before.height);
    assert.ok(result.after.height > result.before.height);
  } finally { await page.close(); }
});

test('reopening starts cleanly on Conversation with one launcher and one shell', async () => {
  const page = await barePage();
  try {
    const result = await page.evaluate(async () => {
      window.fetch = async url => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'fp', methods: [], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
      await window.CCCCodexClient.open(window.CCCCodexClientContext());
      document.querySelector('[data-surface="settings"]').click();
      window.CCCCodexClient.close();
      await window.CCCCodexClient.open(window.CCCCodexClientContext());
      return {
        current: document.querySelector('.codex-client-tabs [aria-current="page"]')?.dataset.surface,
        surface: document.querySelector('.codex-client-shell')?.dataset.surface,
        conversationHidden: document.querySelector('[data-codex-conversation]')?.hidden,
        shells: document.querySelectorAll('.codex-client-shell').length,
        launchers: document.querySelectorAll('[data-codex-workspace-launch]').length,
      };
    });
    assert.deepEqual(result, { current: 'conversation', surface: 'conversation', conversationHidden: false, shells: 1, launchers: 1 });
  } finally { await page.close(); }
});
