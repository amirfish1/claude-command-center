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
            { method: 'config/internalProbe', title: 'Internal probe', group: 'settings', read_only: true, available: true, internal: true },
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
        if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method: 'thread/archive', title: 'Archive task', read_only: false, experimental: true, params_type: 'null', params_schema: { type: 'object', additionalProperties: false }, description: 'Archives this task and closes the current workspace.' } }) };
        if (u.includes('/operation')) { writes++; return { ok: true, json: async () => ({ ok: true, generation: 'g2', result: {} }) }; }
        if (u.includes('/state')) return { ok: true, json: async () => ({ ok: true, generation: 'g2', cursor: 2, connected: true, thread: { id: 'thread-1', turns: [] }, requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g2', cursor: 2, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u + ' ' + (options.method || 'GET'));
      };
      await window.CCCCodexClient.open(window.CCCCodexClientContext());
      document.querySelector('[data-method="thread/archive"]').click();
      await new Promise(resolve => setTimeout(resolve, 0));
      const before = { writes, dialog: !!document.querySelector('.codex-client-dialog'), copy: document.querySelector('.codex-client-dialog')?.textContent || '' };
      document.querySelector('.codex-client-dialog .codex-client-button.is-primary').click();
      await new Promise(resolve => setTimeout(resolve, 20));
      return { before, afterWrites: writes };
    });
    assert.deepEqual({ writes: result.before.writes, dialog: result.before.dialog }, { writes: 0, dialog: true });
    assert.match(result.before.copy, /Archives this task and closes the current workspace/);
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

test('the visible action button validates and submits required native fields', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.__writes = [];
      window.__lifecycles = [];
      window.addEventListener('ccc:codex-lifecycle', event => window.__lifecycles.push(event.detail));
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'required', methods: [{ method: 'thread/name/set', title: 'Rename task', group: 'Conversations', read_only: false, available: true }], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method: 'thread/name/set', title: 'Rename task', read_only: false, params_type: 'object', params_schema: { type: 'object', required: ['threadId', 'name'], properties: { threadId: { type: 'string' }, name: { type: 'string', minLength: 1 } } } } }) };
        if (u.includes('/operation')) { const body = JSON.parse(options.body); window.__writes.push(body); return { ok: true, json: async () => ({ ok: true, generation: 'g', result: {} }) }; }
        if (u.includes('/state')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 2, connected: true, thread: { id: 'thread-1', turns: [] }, requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 2, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.click('[data-method="thread/name/set"]');
    await page.waitForSelector('.codex-client-dialog input[name="name"]');
    const attributes = await page.$eval('input[name="name"]', input => ({ required: input.required, aria: input.getAttribute('aria-required') }));
    assert.deepEqual(attributes, { required: true, aria: 'true' });
    await page.click('.codex-client-dialog .codex-client-button.is-primary');
    assert.equal(await page.evaluate(() => window.__writes.length), 0);
    assert.equal(await page.$eval('input[name="name"]', input => input.getAttribute('aria-invalid')), 'true');
    assert.match(await page.$eval('.codex-schema-errors', node => node.textContent), /required/i);
    await page.type('input[name="name"]', 'Renamed task');
    await page.click('.codex-client-dialog .codex-client-button.is-primary');
    await page.waitForFunction(() => window.__writes.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__writes[0].params), { threadId: 'thread-1', name: 'Renamed task' });
    assert.deepEqual(await page.evaluate(() => window.__lifecycles.map(event => event.method)), ['thread/name/set']);
    await page.evaluate(() => window.CCCCodexClient.handleEvents([{ seq: 3, method: 'thread/metadata/updated', params: { threadId: 'thread-1' } }]));
    await new Promise(resolve => setTimeout(resolve, 180));
    assert.equal(await page.evaluate(() => window.__writes.length), 1);
  } finally { await page.close(); }
});

test('a read action with optional parameters opens its form before running', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.__reads = [];
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'optional', methods: [{ method: 'thread/list', title: 'Find tasks', group: 'Conversations', read_only: true, available: true }], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method: 'thread/list', title: 'Find tasks', read_only: true, params_type: 'object', params_schema: { type: 'object', properties: { search: { type: 'string' } } } } }) };
        if (u.includes('/operation')) { const body = JSON.parse(options.body); window.__reads.push(body); return { ok: true, json: async () => ({ ok: true, generation: 'g', result: { data: [] } }) }; }
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.click('[data-method="thread/list"]');
    await page.waitForSelector('.codex-client-dialog input[name="search"]');
    assert.equal(await page.evaluate(() => window.__reads.length), 0);
    await page.type('input[name="search"]', 'workspace');
    await page.click('.codex-client-dialog .codex-client-button.is-primary');
    await page.waitForFunction(() => window.__reads.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__reads[0].params), { search: 'workspace' });
  } finally { await page.close(); }
});

test('required collections expose accessible errors until their minimum items exist', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.__writes = [];
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'collections', methods: [{ method: 'demo/write', title: 'Save choices', group: 'Settings', read_only: false, available: true }], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method: 'demo/write', title: 'Save choices', read_only: false, params_type: 'object', params_schema: { type: 'object', required: ['targets', 'options'], properties: { targets: { type: 'array', minItems: 1, items: { type: 'string' } }, options: { type: 'object', minProperties: 1, additionalProperties: { type: 'string' } } } } } }) };
        if (u.includes('/operation')) { window.__writes.push(JSON.parse(options.body)); return { ok: true, json: async () => ({ ok: true, generation: 'g', result: {} }) }; }
        if (u.includes('/state')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.click('[data-method="demo/write"]');
    await page.waitForSelector('.codex-client-dialog');
    await page.click('.codex-client-dialog .codex-client-button.is-primary');
    assert.equal(await page.evaluate(() => window.__writes.length), 0);
    assert.equal(await page.$eval('[data-schema-path="targets"]', node => node.getAttribute('aria-invalid')), 'true');
    assert.equal(await page.$eval('[data-schema-path="options"]', node => node.getAttribute('aria-invalid')), 'true');
    await page.click('[data-schema-path="targets"] [data-array-add]');
    await page.type('[data-schema-path="targets"] input[type="text"]', 'src');
    await page.click('[data-schema-path="options"] [data-map-add]');
    await page.type('[data-schema-path="options"] .codex-schema-map-row > input', 'mode');
    await page.type('[data-schema-path="options"] .codex-schema-map-row .codex-schema-field input', 'safe');
    await page.click('.codex-client-dialog .codex-client-button.is-primary');
    await page.waitForFunction(() => window.__writes.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__writes[0].params), { targets: ['src'], options: { mode: 'safe' } });
  } finally { await page.close(); }
});

test('schema true builds a structured arbitrary value from visible controls', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.__writes = [];
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'any', methods: [{ method: 'config/value/write', title: 'Write setting', group: 'Settings', read_only: false, available: true }], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method: 'config/value/write', title: 'Write setting', read_only: false, params_type: 'object', params_schema: { type: 'object', required: ['keyPath', 'mergeStrategy', 'value'], properties: { keyPath: { type: 'string' }, mergeStrategy: { type: 'string', enum: ['replace', 'upsert'] }, value: true } } } }) };
        if (u.includes('/operation')) { window.__writes.push(JSON.parse(options.body)); return { ok: true, json: async () => ({ ok: true, generation: 'g', result: {} }) }; }
        if (u.includes('/state')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.click('[data-surface="settings"]');
    await page.click('[data-method="config/value/write"]');
    await page.waitForSelector('[data-schema-path="value"] [data-any-value-type]');
    await page.type('input[name="keyPath"]', 'features.limit');
    await page.select('[data-schema-path="value"] [data-any-value-type]', 'object');
    await page.click('[data-schema-path="value"] [data-map-add]');
    await page.type('[data-schema-path="value"] .codex-schema-map-row > input', 'limit');
    await page.select('[data-schema-path="value"] .codex-schema-map-row [data-any-value-type]', 'number');
    await page.type('[data-schema-path="value"] .codex-schema-map-row input[type="number"]', '3');
    await page.click('.codex-client-dialog .codex-client-button.is-primary');
    await page.waitForFunction(() => window.__writes.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__writes[0].params), { keyPath: 'features.limit', mergeStrategy: 'replace', value: { limit: 3 } });
    assert.equal(await page.$('.codex-schema-file-text'), null);
  } finally { await page.close(); }
});

test('the visible composer sends native expectedTurnId when steering', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.__writes = [];
      const thread = { id: 'thread-1', turns: [{ id: 'turn-active', status: 'inProgress', items: [] }] };
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'steer', methods: [{ method: 'turn/steer', title: 'Steer turn', group: 'Conversations', read_only: false, available: true }], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread, next_cursor: null, requests: [] }) };
        if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method: 'turn/steer', title: 'Steer turn', read_only: false, params_type: 'object', params_schema: { type: 'object', required: ['expectedTurnId', 'input', 'threadId'], properties: { expectedTurnId: { type: 'string' }, input: { type: 'array' }, threadId: { type: 'string' } } } } }) };
        if (u.includes('/operation')) { window.__writes.push(JSON.parse(options.body)); return { ok: true, json: async () => ({ ok: true, generation: 'g', result: {} }) }; }
        if (u.includes('/state')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 2, connected: true, thread, requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 2, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.waitForFunction(() => document.querySelector('[data-codex-send-label]').textContent === 'Steer');
    await page.type('[data-codex-composer] textarea', 'Use the smaller layout');
    await page.click('[data-codex-composer] button[type="submit"]');
    await page.waitForFunction(() => window.__writes.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__writes[0].params), {
      expectedTurnId: 'turn-active', input: [{ type: 'text', text: 'Use the smaller layout' }], threadId: 'thread-1',
    });
  } finally { await page.close(); }
});

test('approval cards show command, file, workspace, and permission context', async () => {
  const page = await barePage();
  try {
    const requests = [
      { key: 'command', generation: 'g', method: 'item/commandExecution/requestApproval', thread_id: 'thread-1', state: 'pending', params: {
        threadId: 'thread-1', command: ['npm', 'test'], cwd: '/tmp/repo', environmentId: 'local-env', kind: 'command',
        commandActions: [{ type: 'read', command: 'npm test' }],
        additionalPermissions: { network: { hosts: ['registry.npmjs.org'] } },
        networkApprovalContext: { host: 'api.example.com', protocol: 'https' },
        availableDecisions: ['decline'],
      } },
      { key: 'file', generation: 'g', method: 'item/fileChange/requestApproval', thread_id: 'thread-1', state: 'pending', params: {
        threadId: 'thread-1', cwd: '/tmp/repo', grantRoot: '/tmp/repo/src',
        fileChanges: { 'src/app.js': { type: 'update', unified_diff: '@@ -1 +1 @@' } },
        availableDecisions: ['accept', 'decline'],
      } },
    ];
    await page.evaluate(requests => {
      window.fetch = async url => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'approvals', methods: [], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests }) };
        throw new Error('unexpected ' + u);
      };
    }, requests);
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.waitForSelector('[data-request-key="command"]');
    const commandText = await page.$eval('[data-request-key="command"]', node => node.textContent);
    const fileText = await page.$eval('[data-request-key="file"]', node => node.textContent);
    assert.match(commandText, /npm test/);
    assert.match(commandText, /\/tmp\/repo/);
    assert.match(commandText, /registry\.npmjs\.org/);
    assert.match(commandText, /local-env/);
    assert.match(commandText, /api\.example\.com/);
    assert.match(fileText, /src\/app\.js/);
    assert.match(fileText, /@@ -1 \+1 @@/);
    assert.match(fileText, /\/tmp\/repo\/src/);
  } finally { await page.close(); }
});

test('a structured offered approval decision is submitted unchanged', async () => {
  const page = await barePage();
  try {
    const offered = { acceptWithExecpolicyAmendment: { execpolicy_amendment: ['npm', 'test'] } };
    await page.evaluate(offered => {
      window.__responses = [];
      const request = { key: 'structured', generation: 'g', method: 'item/commandExecution/requestApproval', thread_id: 'thread-1', state: 'pending', params: { threadId: 'thread-1', command: ['npm', 'test'], availableDecisions: [offered, 'decline'] } };
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'structured', methods: [], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [request] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [request] }) };
        if (u.includes('/respond')) { window.__responses.push(JSON.parse(options.body)); return { ok: true, json: async () => ({ ok: true }) }; }
        if (u.includes('/state')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 2, connected: true, thread: { id: 'thread-1', turns: [] }, requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    }, offered);
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.waitForSelector('[data-structured-decision]');
    assert.match(await page.$eval('[data-structured-decision]', node => node.textContent), /Accept With Execpolicy Amendment/i);
    await page.click('[data-structured-decision]');
    await page.waitForFunction(() => window.__responses.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__responses[0].result.decision), offered);
  } finally { await page.close(); }
});

for (const [method, returnedId] of [['thread/start', 'new-thread'], ['thread/fork', 'forked-thread']]) {
  test(method + ' activates the returned task and emits its lifecycle event', async () => {
    const page = await barePage();
    try {
      await page.evaluate(({ method, returnedId }) => {
        window.__lifecycles = [];
        window.__historyThreads = [];
        window.addEventListener('ccc:codex-lifecycle', event => window.__lifecycles.push(event.detail));
        window.fetch = async (url, options = {}) => {
          const u = String(url);
          if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'lifecycle', methods: [{ method, title: method === 'thread/start' ? 'New task' : 'Fork task', group: 'Conversations', read_only: false, available: true }], server_requests: [], notifications: [] }) };
          if (u.includes('/history')) { const id = new URL(u, 'http://fixture').searchParams.get('thread_id'); window.__historyThreads.push(id); return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id, cwd: '/tmp/repo', turns: [] }, next_cursor: null, requests: [] }) }; }
          if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method, title: method === 'thread/start' ? 'New task' : 'Fork task', read_only: false, params_type: 'object', params_schema: method === 'thread/start' ? { type: 'object', properties: {} } : { type: 'object', required: ['threadId'], properties: { threadId: { type: 'string' } } } } }) };
          if (u.includes('/operation')) return { ok: true, json: async () => ({ ok: true, generation: 'g', result: { thread: { id: returnedId, cwd: '/tmp/repo' } } }) };
          if (u.includes('/events')) { const id = new URL(u, 'http://fixture').searchParams.get('thread_id'); return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [], threadId: id }) }; }
          if (u.includes('/state')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, requests: [] }) };
          throw new Error('unexpected ' + u + ' ' + (options.method || 'GET'));
        };
      }, { method, returnedId });
      await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
      await page.click('[data-method="' + method + '"]');
      await page.waitForSelector('.codex-client-dialog');
      await page.click('.codex-client-dialog .codex-client-button.is-primary');
      await page.waitForFunction(returnedId => window.CCCCodexClient.__testing.state.context?.threadId === returnedId, {}, returnedId);
      assert.equal((await page.evaluate(() => window.__historyThreads)).at(-1), returnedId);
      assert.deepEqual(await page.evaluate(() => window.__lifecycles.map(event => [event.method, event.threadId])), [[method, returnedId]]);
    } finally { await page.close(); }
  });
}

for (const method of ['thread/archive', 'thread/delete']) {
  test(method + ' closes the current workspace and stops its poller', async () => {
    const page = await barePage();
    try {
      await page.evaluate(method => {
        window.__lifecycles = [];
        window.addEventListener('ccc:codex-lifecycle', event => window.__lifecycles.push(event.detail));
        window.fetch = async (url, options = {}) => {
          const u = String(url);
          if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'close', methods: [{ method, title: method === 'thread/archive' ? 'Archive task' : 'Delete task', group: 'Conversations', read_only: false, available: true }], server_requests: [], notifications: [] }) };
          if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
          if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method, title: 'Close task', read_only: false, params_type: 'object', params_schema: { type: 'object', required: ['threadId'], properties: { threadId: { type: 'string' } } } } }) };
          if (u.includes('/operation')) return { ok: true, json: async () => ({ ok: true, generation: 'g', result: {} }) };
          if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [] }) };
          if (u.includes('/state')) throw new Error('closed tasks must not reload state');
          throw new Error('unexpected ' + u + ' ' + (options.method || 'GET'));
        };
      }, method);
      await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
      await page.click('[data-method="' + method + '"]');
      await page.waitForSelector('.codex-client-dialog');
      await page.click('.codex-client-dialog .codex-client-button.is-primary');
      await page.waitForFunction(() => !document.querySelector('.codex-client-shell'));
      assert.deepEqual(await page.evaluate(() => ({ closed: window.CCCCodexClient.__testing.state.closed, timer: window.CCCCodexClient.__testing.state.pollTimer, methods: window.__lifecycles.map(event => event.method) })), { closed: true, timer: null, methods: [method] });
    } finally { await page.close(); }
  });
}

test('file-change items render filenames and colorized preformatted diffs', async () => {
  const page = await barePage();
  try {
    const result = await page.evaluate(() => {
      const item = window.CCCCodexClient.__testing.renderItem({ type: 'fileChange', id: 'change-1', changes: [
        { path: 'src/app.js', kind: {type:'update'}, diff: '@@ -1 +1 @@\n-old value\n+new value' },
      ] });
      document.body.append(item);
      return {
        filename: item.querySelector('.codex-client-file-change strong')?.textContent,
        kind: item.querySelector('.codex-client-badge')?.textContent,
        pre: item.querySelector('pre')?.textContent,
        additions: item.querySelectorAll('.codex-diff-add').length,
        deletions: item.querySelectorAll('.codex-diff-delete').length,
      };
    });
    assert.equal(result.filename, 'src/app.js');
    assert.equal(result.kind, 'Update');
    assert.match(result.pre, /@@ -1 \+1 @@/);
    assert.equal(result.additions, 1);
    assert.equal(result.deletions, 1);
  } finally { await page.close(); }
});

test('Conversation tools start collapsed and remain available from a compact toggle', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.fetch = async url => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'tools', methods: [{ method: 'thread/read', title: 'Read task', group: 'Conversations', read_only: true, available: true }], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.waitForSelector('[data-codex-tools-toggle]');
    assert.deepEqual(await page.evaluate(() => ({
      collapsed: document.querySelector('.codex-client-shell').classList.contains('is-tools-collapsed'),
      expanded: document.querySelector('[data-codex-tools-toggle]').getAttribute('aria-expanded'),
      label: document.querySelector('[data-codex-tools-toggle]').textContent,
    })), { collapsed: true, expanded: 'false', label: 'Tools (1)' });
    await page.click('[data-codex-tools-toggle]');
    assert.deepEqual(await page.evaluate(() => ({
      collapsed: document.querySelector('.codex-client-shell').classList.contains('is-tools-collapsed'),
      expanded: document.querySelector('[data-codex-tools-toggle]').getAttribute('aria-expanded'),
    })), { collapsed: false, expanded: 'true' });
  } finally { await page.close(); }
});

test('composer model and effort choices come from model/list and send with an image', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.__operations = [];
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'composer', methods: [
          { method: 'model/list', title: 'Models', group: 'Models', read_only: true, available: true },
          { method: 'turn/start', title: 'Send message', group: 'Conversations', read_only: false, available: true },
        ], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method: 'turn/start', title: 'Send message', read_only: false, params_type: 'object', params_schema: { type: 'object', required: ['threadId', 'input'], properties: { threadId: { type: 'string' }, input: { type: 'array' }, model: { type: 'string' }, effort: { type: 'string' } } } } }) };
        if (u.includes('/operation')) {
          const body = JSON.parse(options.body); window.__operations.push(body);
          if (body.method === 'model/list') return { ok: true, json: async () => ({ ok: true, generation: 'g', result: { data: [
            { id: 'model-a', displayName: 'Model A', supportedReasoningEfforts: [{ reasoningEffort: 'low' }], defaultReasoningEffort: 'low', inputModalities: ['text'] },
            { id: 'model-b', displayName: 'Model B', supportedReasoningEfforts: [{ reasoningEffort: 'medium' }, { reasoningEffort: 'high' }], defaultReasoningEffort: 'medium', inputModalities: ['text', 'image'] },
          ] } }) };
          return { ok: true, json: async () => ({ ok: true, generation: 'g', result: {} }) };
        }
        if (u.includes('/state')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 2, connected: true, thread: { id: 'thread-1', turns: [] }, requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 2, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.waitForSelector('[data-codex-model] option[value="model-b"]');
    await page.select('[data-codex-model]', 'model-b');
    await page.select('[data-codex-effort]', 'high');
    const file = await page.$('[data-codex-image-input]');
    await file.uploadFile(path.resolve('static/icon.svg'));
    await page.waitForSelector('[data-codex-attachment]:not([hidden])');
    await page.type('[data-codex-composer] textarea', 'Inspect this image');
    await page.click('[data-codex-composer] button[type="submit"]');
    await page.waitForFunction(() => window.__operations.some(body => body.method === 'turn/start'));
    const sent = await page.evaluate(() => window.__operations.find(body => body.method === 'turn/start').params);
    assert.equal(sent.model, 'model-b');
    assert.equal(sent.effort, 'high');
    assert.deepEqual(sent.input[0], { type: 'text', text: 'Inspect this image' });
    assert.equal(sent.input[1].type, 'image');
    assert.match(sent.input[1].url, /^data:image\/svg\+xml;base64,/);
  } finally { await page.close(); }
});

test('composer rejects an image above the bounded data-url limit', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.fetch = async url => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'image-limit', methods: [], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.evaluate(() => {
      const input = document.querySelector('[data-codex-image-input]');
      const transfer = new DataTransfer();
      transfer.items.add(new File([new Uint8Array(700 * 1024 + 1)], 'too-large.png', { type: 'image/png' }));
      input.files = transfer.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await page.waitForFunction(() => /smaller than 700 KB/i.test(document.querySelector('[data-codex-notices]').textContent));
    assert.equal(await page.$eval('[data-codex-attachment]', node => node.hidden), true);
    assert.equal(await page.evaluate(() => window.CCCCodexClient.__testing.state.composerAttachment), null);
  } finally { await page.close(); }
});

test('matching native changes debounce-refresh the displayed read result', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.__reads = 0;
      window.__accountEvents = [];
      window.addEventListener('ccc:codex-account-changed', event => window.__accountEvents.push(event.detail));
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'refresh', methods: [{ method: 'account/rateLimits/read', title: 'Usage limits', group: 'Accounts', read_only: true, available: true }], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method: 'account/rateLimits/read', title: 'Usage limits', read_only: true, params_type: 'null', params_schema: { type: 'object', properties: {} } } }) };
        if (u.includes('/operation')) { window.__reads++; return { ok: true, json: async () => ({ ok: true, generation: 'g', result: { revision: window.__reads } }) }; }
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u + ' ' + (options.method || 'GET'));
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.click('[data-surface="settings"]');
    await page.click('[data-method="account/rateLimits/read"]');
    await page.waitForFunction(() => window.__reads === 1);
    await page.evaluate(() => window.CCCCodexClient.handleEvents([
      { seq: 2, method: 'account/rateLimits/updated', params: {} },
      { seq: 3, method: 'account/rateLimits/updated', params: {} },
      { seq: 4, method: 'account/login/completed', params: { success: true } },
    ]));
    await page.waitForFunction(() => window.__reads === 2);
    assert.equal(await page.evaluate(() => window.__reads), 2);
    assert.match(await page.$eval('[data-codex-result]', node => node.textContent), /Revision\s*2/i);
    assert.equal(await page.evaluate(() => window.__accountEvents.length), 1);
    assert.match(await page.$eval('[data-codex-notices]', node => node.textContent), /signed in/i);
  } finally { await page.close(); }
});

test('a deleted tombstone closes the workspace and announces lifecycle', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.__deleted = false;
      window.__toasts = [];
      window.__lifecycles = [];
      window.showOpToast = message => window.__toasts.push(message);
      window.addEventListener('ccc:codex-lifecycle', event => window.__lifecycles.push(event.detail));
      window.fetch = async url => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'deleted', methods: [], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: window.__deleted ? 2 : 1, connected: true, events: window.__deleted ? [{ seq: 2, method: 'thread/deleted', params: { threadId: 'thread-1' } }] : [], requests: [] }) };
        if (u.includes('/state')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 2, connected: true, thread: { id: 'thread-1', deleted: true, turns: [] }, requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.evaluate(() => { window.__deleted = true; return window.CCCCodexClient.__testing.pollNow(); });
    await page.waitForFunction(() => !document.querySelector('.codex-client-shell'));
    assert.match((await page.evaluate(() => window.__toasts)).join(' '), /deleted/i);
    assert.deepEqual(await page.evaluate(() => window.__lifecycles.map(event => event.method)), ['thread/deleted']);
    assert.equal(await page.evaluate(() => window.CCCCodexClient.__testing.state.pollTimer), null);
  } finally { await page.close(); }
});

test('MCP elicitation Continue validates its generated form before responding', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.__responses = [];
      const request = { key: 'mcp-form', generation: 'g', method: 'mcpServer/elicitation/request', thread_id: 'thread-1', state: 'pending', params: {
        threadId: 'thread-1', mode: 'form', requestedSchema: { type: 'object', required: ['email'], properties: { email: { type: 'string', minLength: 3 } } },
      } };
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'mcp-form', methods: [], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [request] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [request] }) };
        if (u.includes('/respond')) { window.__responses.push(JSON.parse(options.body)); return { ok: true, json: async () => ({ ok: true }) }; }
        if (u.includes('/state')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 2, connected: true, thread: { id: 'thread-1', turns: [] }, requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.waitForSelector('[data-request-key="mcp-form"] input[name="email"]');
    await page.click('[data-request-key="mcp-form"] .codex-client-button.is-primary');
    assert.equal(await page.evaluate(() => window.__responses.length), 0);
    assert.equal(await page.$eval('[data-request-key="mcp-form"] input[name="email"]', input => input.getAttribute('aria-invalid')), 'true');
    assert.match(await page.$eval('[data-request-key="mcp-form"] .codex-schema-errors', node => node.textContent), /required/i);
    await page.type('[data-request-key="mcp-form"] input[name="email"]', 'me@example.com');
    await page.click('[data-request-key="mcp-form"] .codex-client-button.is-primary');
    await page.waitForFunction(() => window.__responses.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__responses[0].result), { action: 'accept', content: { email: 'me@example.com' } });
  } finally { await page.close(); }
});

test('a required array without minItems submits an empty list', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.__writes = [];
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'empty-array', methods: [{ method: 'skills/extraRoots/set', title: 'Set skill folders', group: 'Skills', read_only: false, available: true }], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method: 'skills/extraRoots/set', title: 'Set skill folders', read_only: false, params_type: 'object', params_schema: { type: 'object', required: ['extraRoots'], properties: { extraRoots: { type: 'array', items: { type: 'string' } } } } } }) };
        if (u.includes('/operation')) { window.__writes.push(JSON.parse(options.body)); return { ok: true, json: async () => ({ ok: true, generation: 'g', result: {} }) }; }
        if (u.includes('/state')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 2, connected: true, thread: { id: 'thread-1', turns: [] }, requests: [] }) };
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 2, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.click('[data-surface="settings"]');
    await page.click('[data-method="skills/extraRoots/set"]');
    await page.waitForSelector('[data-schema-path="extraRoots"]');
    await page.click('.codex-client-dialog .codex-client-button.is-primary');
    await page.waitForFunction(() => window.__writes.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__writes[0].params), { extraRoots: [] });
  } finally { await page.close(); }
});

test('a nullable ref preserves the referenced union variants and serialization', async () => {
  const page = await barePage();
  try {
    await page.evaluate(() => {
      window.__reads = [];
      window.fetch = async (url, options = {}) => {
        const u = String(url);
        if (u.includes('/catalog')) return { ok: true, json: async () => ({ ok: true, fingerprint: 'nested-union', methods: [{ method: 'thread/list', title: 'Find tasks', group: 'Conversations', read_only: true, available: true }], server_requests: [], notifications: [] }) };
        if (u.includes('/history')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, thread: { id: 'thread-1', turns: [] }, next_cursor: null, requests: [] }) };
        if (u.includes('/schema')) return { ok: true, json: async () => ({ ok: true, descriptor: { method: 'thread/list', title: 'Find tasks', read_only: true, params_type: 'object', params_schema: {
          type: 'object', properties: { cwd: { anyOf: [{ $ref: '#/definitions/ThreadListCwdFilter' }, { type: 'null' }] } },
          definitions: { ThreadListCwdFilter: { anyOf: [{ title: 'One folder', type: 'string' }, { title: 'Several folders', type: 'array', items: { type: 'string' } }] } },
        } } }) };
        if (u.includes('/operation')) { window.__reads.push(JSON.parse(options.body)); return { ok: true, json: async () => ({ ok: true, generation: 'g', result: { data: [] } }) }; }
        if (u.includes('/events')) return { ok: true, json: async () => ({ ok: true, generation: 'g', cursor: 1, connected: true, events: [], requests: [] }) };
        throw new Error('unexpected ' + u);
      };
    });
    await page.evaluate(() => window.CCCCodexClient.open(window.CCCCodexClientContext()));
    await page.click('[data-codex-tools-toggle]');
    await page.click('[data-method="thread/list"]');
    await page.waitForSelector('[data-schema-path="cwd"] [data-schema-variant]');
    assert.equal(await page.$$eval('[data-schema-path="cwd"] [data-schema-variant] > option', options => options.length), 2);
    await page.select('[data-schema-path="cwd"] [data-schema-variant]', '1');
    await page.click('[data-schema-path="cwd"] [data-array-add]');
    await page.type('[data-schema-path="cwd"] .codex-schema-array-row input', '/tmp/repo');
    await page.click('.codex-client-dialog .codex-client-button.is-primary');
    await page.waitForFunction(() => window.__reads.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__reads[0].params), { cwd: ['/tmp/repo'] });
  } finally { await page.close(); }
});
