#!/usr/bin/env node
'use strict';
// A newline-delimited stdio MCP server. No model-controlled selectors, JS or URLs.
const fs = require('fs');
const path = require('path');
const readline = require('readline');
const {FAKE, allowedURL, risky, cardlike, fakeValue, requestAllowed, bootstrapAllowed} = require('./safety');

const objectSchema = (properties, required = []) => ({type: 'object', properties, required, additionalProperties: false});
const str = {type: 'string', maxLength: 2000};
const stepsSchema = {type: 'array', items: str, minItems: 1, maxItems: 30};
const narrative = {expected: str, actual: str};
const findingFields = {control: str, symptom: str, severity: {type: 'string', enum: ['blocked', 'confusing', 'slow', 'ugly']},
  steps: stepsSchema, ...narrative};
const TOOLS = [
  ['look', 'See current screenshot, visible text and numbered controls. Numbers expire after any action.', objectSchema({})],
  ['click', 'Click one numbered visible control; narrate expectation.', objectSchema({control: {type: 'integer', minimum: 1}, expected: str}, ['control', 'expected'])],
  ['type', 'Replace text in a visible field using an enumerated fake value. No arbitrary text or keys.', objectSchema({control: {type: 'integer', minimum: 1}, value: {type: 'string', enum: Object.keys(FAKE)}, expected: str}, ['control', 'value', 'expected'])],
  ['select', 'Choose a numbered visible option in a visible select (fake values only).', objectSchema({control: {type: 'integer', minimum: 1}, option: {type: 'integer', minimum: 1}, expected: str}, ['control', 'option', 'expected'])],
  ['scroll', 'Scroll one viewport up/down; never sends keyboard events.', objectSchema({direction: {type: 'string', enum: ['up', 'down']}, expected: str}, ['direction', 'expected'])],
  ['wait', 'Wait briefly for visible feedback, then look. Slow findings retain the screenshot from before this wait.', objectSchema({seconds: {type: 'integer', minimum: 1, enum: [1, 3, 7]}, expected: str}, ['seconds', 'expected'])],
  ['back', 'Go back within allowed hosts.', objectSchema({expected: str}, ['expected'])],
  ['restart', 'Return to the original URL for an explicit replay.', objectSchema({expected: str}, ['expected'])],
  ['record_finding', 'Save a finding and screenshot NOW, at the moment of the issue.', objectSchema(findingFields, Object.keys(findingFields))],
  ['replay', 'Record an explicit replay verdict for an earlier finding; provide actual steps performed.', objectSchema({key: str, verdict: {type: 'string', enum: ['present', 'absent', 'not checked']}, steps: stepsSchema, ...narrative}, ['key', 'verdict', 'steps', 'expected', 'actual'])],
  ['finish', 'Finish the session with the goal outcome and what worked.', objectSchema({success: {type: 'boolean'}, worked: str, actual: str}, ['success', 'worked', 'actual'])],
].map(([name, description, inputSchema]) => ({name, description, inputSchema}));

function validate(schema, args) {
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw Error('Arguments must be an object');
  for (const key of schema.required || []) if (!Object.hasOwn(args, key)) throw Error(`Missing ${key}`);
  for (const [key, value] of Object.entries(args)) {
    const rule = schema.properties[key];
    if (!rule) throw Error(`Unknown argument ${key}`);
    if (rule.type === 'integer' && (!Number.isInteger(value) || value < rule.minimum)) throw Error(`Invalid ${key}`);
    if (rule.type === 'string' && (typeof value !== 'string' || value.length > (rule.maxLength || 2000) || !value.trim())) throw Error(`Invalid ${key}`);
    if (rule.type === 'boolean' && typeof value !== 'boolean') throw Error(`Invalid ${key}`);
    if (rule.enum && !rule.enum.includes(value)) throw Error(`Invalid ${key}`);
    if (rule.type === 'array' && (!Array.isArray(value) || value.length < rule.minItems || value.length > rule.maxItems || value.some(v => typeof v !== 'string' || v.length > 2000))) throw Error(`Invalid ${key}`);
  }
}

class BrowserSession {
  constructor(spec) {
    this.spec = spec;
    this.output = spec.output;
    this.result = {id: spec.person.id, status: 'running', findings: [], replays: [], trace: [], success: false};
    this.bootstrapping = false;
    this.authReady = !spec.person.auth;
    this.secrets = new Set();
    this.blockedRequests = [];
    this.controls = [];
    this.calls = 0;
    this.shots = 0;
    this.deadline = Date.now() + spec.timeout * 1000;
    this.save();
  }
  save() {
    fs.writeFileSync(path.join(this.output, 'result.json.tmp'), this.sanitize(JSON.stringify(this.result, null, 2)));
    fs.renameSync(path.join(this.output, 'result.json.tmp'), path.join(this.output, 'result.json'));
  }
  sanitize(text) {
    const cleanText = text => {
      for (const secret of [...this.secrets].sort((a, b) => b.length - a.length)) {
        for (const encoded of [encodeURIComponent(secret), secret])
          if (encoded) text = text.split(encoded).join('[REDACTED]');
      }
      return text;
    };
    const clean = value => {
      if (typeof value === 'string') {
        // Tool text may itself contain serialized observations. Preserve JSON keys.
        if (/^\s*[\[{]/.test(value)) {
          try { return JSON.stringify(clean(JSON.parse(value))); } catch {}
        }
        return cleanText(value);
      }
      if (Array.isArray(value)) return value.map(clean);
      if (value && typeof value === 'object') return Object.fromEntries(
        Object.entries(value).map(([key, val]) => [key, key === 'data' ? val : clean(val)]));
      return value;
    };
    try { return JSON.stringify(clean(JSON.parse(text))); } catch { return cleanText(text); }
  }

  trace(kind, detail) {
    if (this.bootstrapping) return;
    const entry = JSON.parse(this.sanitize(JSON.stringify({time: new Date().toISOString(), kind, ...detail})));
    this.result.trace.push(entry);
    fs.appendFileSync(path.join(this.output, 'trace.jsonl'), JSON.stringify(entry) + '\n');
    this.save();
  }
  async start() {
    try { await this.startBrowser(); } catch (error) {
      this.bootstrapping = false;
      this.authReady = false;
      this.result.status = 'failed';
      this.save();
      await this.close();
      throw Error(this.spec.person.auth ? 'Authentication bootstrap failed' : 'Browser startup failed');
    }
  }
  async startBrowser() {
    this.authentication = this.spec.person.auth ? this.spec.authentication?.[this.spec.person.auth] : null;
    if (this.spec.person.auth && !this.authentication) throw Error('Missing authentication');
    this.bootstrapping = !!this.authentication;
    this.startURL = new URL(this.spec.person.start_path || this.spec.url, this.spec.url).href;
    if (new URL(this.startURL).origin !== new URL(this.spec.url).origin) throw Error('Invalid start path');
    const puppeteer = require('../require-puppeteer');
    this.profile = fs.mkdtempSync(path.join(this.spec.profile_root || require('os').tmpdir(), 'ccc-uxlab-browser-'));
    this.browser = await puppeteer.launch({headless: true, userDataDir: this.profile,
      ...(this.spec.browser_executable ? {executablePath: this.spec.browser_executable} : {}),
      args: [...(this.spec.browser_sandbox === false ? ['--no-sandbox'] : []), '--disable-background-networking', '--disable-component-update', '--disable-sync',
        '--disable-extensions', '--disable-default-apps', '--no-first-run', '--no-default-browser-check', '--block-new-web-contents',
        '--disable-features=WebRtcHideLocalIpsWithMdns,ServiceWorker,WebTransport',
        '--force-webrtc-ip-handling-policy=disable_non_proxied_udp', '--disable-quic']});
    const pages = await this.browser.pages();
    this.page = pages[0] || await this.browser.newPage();
    this.browser.on('targetcreated', async target => {
      // Blocks popups before their page scripts can be used by the tester; opener is also disabled below.
      if (target.type() === 'page' && target !== this.page.target()) {
        const extra = await target.page().catch(() => null);
        if (extra) await extra.close().catch(() => {});
      }
    });
    const phone = this.spec.person.device === 'phone';
    this.viewport = {width: phone ? 390 : 1365, height: phone ? 844 : 900, isMobile: phone, hasTouch: phone, deviceScaleFactor: phone ? 3 : 1};
    await this.page.setViewport(this.viewport);
    if (phone) await this.page.setUserAgent('Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36');
    this.page.setDefaultTimeout(5000);
    this.page.setDefaultNavigationTimeout(15000);
    await this.page.setBypassServiceWorker(true);
    const cdp = await this.page.createCDPSession();
    await cdp.send('Browser.setDownloadBehavior', {behavior: 'deny'});
    await cdp.send('Network.enable');
    await cdp.send('Network.setBlockedURLs', {urls: ['ws://*', 'wss://*']});
    // This is a deny layer for every frame's request, including redirects.
    await this.page.setRequestInterception(true);
    this.page.on('request', request => {
      const data = {url: request.url(), method: request.method(), body: request.postData() || '', resource: request.resourceType()};
      // An auth endpoint is never a normal fake-data write, even after login.
      const authEndpoint = (this.authentication?.requests || []).some(rule => rule.url === data.url);
      if (this.bootstrapping && authEndpoint && data.body) this.rememberSecrets(data.body);
      const redirected = request.redirectChain().length > 0;
      const allowed = (!authEndpoint && requestAllowed(data, this.spec.safety)) ||
        (!redirected && bootstrapAllowed(data, this.spec.safety, this.authentication, this.bootstrapping));
      if (!allowed) {
        if (this.bootstrapping && authEndpoint) this.bootstrapDenied = true;
        const block = {method: request.method(), resource: data.resource, external: new URL(data.url).origin !== new URL(this.spec.url).origin};
        if (!this.bootstrapping) this.blockedRequests.push(block);
        this.trace('request_blocked', block);
      }
      (allowed ? request.continue() : request.abort('blockedbyclient')).catch(() => {});
    });
    this.page.on('response', response => {
      if (!this.bootstrapping) return;
      const cookies = response.headers()['set-cookie'] || '';
      for (const match of cookies.matchAll(/(?:^|[\n,])\s*[^=;,]+=([^;\n,]+)/g)) this.secrets.add(match[1]);
    });
    this.page.on('dialog', d => d.dismiss().catch(() => {}));
    // Runs in every new frame before app scripts. Browser flags and request
    // interception are independent layers; none make hostile JS safe.
    await this.page.evaluateOnNewDocument(safety => {
      const blocked = () => { throw new Error('Unavailable in UX lab'); };
      for (const key of ['open', 'WebSocket', 'RTCPeerConnection', 'webkitRTCPeerConnection', 'WebTransport', 'Worker', 'SharedWorker']) {
        try { Object.defineProperty(window, key, {value: blocked, configurable: false, writable: false}); } catch {}
      }
      // No connection is opened. Apps may install optional live-update streams
      // without treating an unavailable stream as a fatal rendering exception.
      class DisabledEventSource extends EventTarget {
        static CONNECTING = 0; static OPEN = 1; static CLOSED = 2;
        constructor() { super(); this.readyState = 2; queueMicrotask(() => {
          const event = new Event('error'); this.dispatchEvent(event); this.onerror?.(event);
        }); }
        close() { this.readyState = 2; }
      }
      try { Object.defineProperty(window, 'EventSource', {value: DisabledEventSource, configurable: false, writable: false}); } catch {}
      try { Object.defineProperty(navigator, 'sendBeacon', {value: () => false}); } catch {}
      // Preserve feature-detection shape without allowing a worker to register.
      // Setting this to undefined leaves `'serviceWorker' in navigator` true
      // and makes ordinary register(...).catch(...) code throw synchronously.
      try { Object.defineProperty(navigator, 'serviceWorker', {value: Object.freeze({
        register: () => Promise.reject(new Error('Unavailable in UX lab')),
        getRegistration: () => Promise.resolve(undefined), getRegistrations: () => Promise.resolve([]),
        addEventListener() {}, removeEventListener() {}, controller: null,
      })}); } catch {}
      const pattern = /\b(pay(?:ment)?|purchase|buy|checkout|subscribe|delete\s*(?:my\s*)?account|send|message|invite|real\s+people|transfer|donate|charge)\b/i;
      const safeURL = raw => { try { const u = new URL(raw, location.href); return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password && safety.hosts.includes(u.hostname.replace(/^\[|\]$/g, '').toLowerCase()); } catch { return false; } };
      const stop = event => {event.preventDefault(); event.stopImmediatePropagation();};
      document.addEventListener('click', event => {
        const el = event.target.closest('a,button,input,[role=button],[onclick]');
        if (!el) return;
        const form = el.form;
        const text = [el.innerText, el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('href'), el.getAttribute('formaction'), form?.innerText, form?.getAttribute('action')].join(' ');
        if ((!safety.allow_risky && pattern.test(text.replace(/[+_-]/g, ' '))) || (el.href && !safeURL(el.href)) || el.hasAttribute('download') || (el.target && el.target !== '_self')) stop(event);
      }, true);
      document.addEventListener('submit', event => {
        const form = event.target;
        if (!safeURL(form.action) || (!safety.allow_risky && pattern.test(form.innerText + ' ' + form.action))) stop(event);
      }, true);
      document.addEventListener('keydown', event => { if (event.key === 'Enter') stop(event); }, true);
    }, this.spec.safety);
    if (this.authentication) {
      const crypto = require('crypto');
      const helper = this.authentication;
      const stat = fs.lstatSync(helper.script);
      if (!stat.isFile() || stat.isSymbolicLink()) throw Error('Invalid helper');
      const source = fs.readFileSync(helper.script);
      if (crypto.createHash('sha256').update(source).digest('hex') !== helper.sha256) throw Error('Helper changed');
      // Trusted operator code, not a sandbox. No inherited require/process or
      // console output is provided; helper errors are never returned verbatim.
      const context = {module: {exports: {}}, console: {log() {}, error() {}, warn() {}, info() {}, debug() {}}};
      require('vm').runInNewContext(source.toString(), context, {timeout: 1000});
      if (typeof context.module.exports !== 'function') throw Error('Invalid helper');
      let timer;
      try {
        const ok = await Promise.race([
          context.module.exports({page: this.page, origin: new URL(this.spec.url).origin, person: this.spec.person, readFile: file => fs.readFileSync(file, 'utf8')}),
          new Promise((_, reject) => {timer = setTimeout(() => reject(Error('Timeout')), Math.min(30000, this.spec.timeout * 1000));}),
        ]);
        if (ok !== true || this.bootstrapDenied) throw Error('Login failed');
      } finally { clearTimeout(timer); this.bootstrapping = false; }
      // Destroy login DOM/history and revoke exceptions before any observation.
      await this.page.goto('about:blank');
    }
    await this.page.goto(this.startURL, {waitUntil: 'domcontentloaded'});
    this.authReady = true;
    await this.redactPage();
  }
  rememberSecrets(body) {
    const collect = (value, key = '') => {
      if (value && typeof value === 'object') Object.entries(value).forEach(([k, v]) => collect(v, k));
      else if (typeof value === 'string' && value && /password|passwd|email|token|secret|authorization|cookie/i.test(key)) this.secrets.add(value);
    };
    this.secrets.add(body);
    try { collect(JSON.parse(body)); } catch { for (const [key, value] of new URLSearchParams(body)) collect(value, key); }
  }
  async redactPage() {
    if (!this.secrets.size) return;
    const values = [...this.secrets];
    await this.page.evaluate(values => {
      const clean = text => values.reduce((out, value) => out.split(value).join('[REDACTED]'), text);
      const walker = document.createTreeWalker(document, NodeFilter.SHOW_TEXT);
      let node; while ((node = walker.nextNode())) node.textContent = clean(node.textContent);
      for (const el of document.querySelectorAll('*')) {
        for (const attr of [...el.attributes]) if (clean(attr.value) !== attr.value) el.setAttribute(attr.name, clean(attr.value));
        if ('value' in el && typeof el.value === 'string') el.value = clean(el.value);
      }
    }, values);
  }
  assertReady() {
    if (this.bootstrapping || !this.authReady) throw Error('Authentication is not ready');
  }
  async tap(x, y) {
    if (this.viewport.hasTouch) await this.page.touchscreen.tap(x, y);
    else await this.page.mouse.click(x, y);
  }
  async clearControls() {
    for (const c of this.controls) await c.handle.dispose().catch(() => {});
    this.controls = [];
  }
  async shot() {
    this.assertReady();
    await this.redactPage();
    const name = `shot-${++this.shots}.png`;
    await this.page.screenshot({path: path.join(this.output, name)});
    return name;
  }
  async look() {
    this.assertReady();
    await this.redactPage();
    await this.clearControls();
    if (!allowedURL(this.page.url(), this.spec.safety.hosts)) throw Error('Page left allowed hosts');
    // Only top-frame visible, hit-testable controls. Iframes are not exposed as controls.
    // Text extraction uses painted text nodes within the current viewport, not HTML/source.
    const text = await this.page.evaluate(() => {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const lines = []; let node; let inspected = 0; let nodes = 0;
      while ((node = walker.nextNode()) && inspected < 24000 && ++nodes < 3000) {
        const parent = node.parentElement;
        if (!parent || parent.closest('script,style,noscript,[hidden],[aria-hidden=true]')) continue;
        if (!parent.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
        const range = document.createRange(); range.selectNodeContents(node);
        if (![...range.getClientRects()].some(r => r.width && r.height && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth)) continue;
        let visible = '';
        // Test painted characters, not the full textContent of an overflow-clipped
        // node. This prevents the text channel from revealing clipped copy.
        for (let i = 0; i < Math.min(node.length, 4000) && inspected++ < 24000; ++i) {
          range.setStart(node, i); range.setEnd(node, i + 1);
          const r = range.getBoundingClientRect(); const x = r.left + r.width / 2, y = r.top + r.height / 2;
          if (r.width && r.height && x > 0 && y > 0 && x < innerWidth && y < innerHeight && parent.contains(document.elementFromPoint(x, y))) visible += node.textContent[i];
        }
        if (visible.trim()) lines.push(visible.trim());
      }
      return lines.join('\n').slice(0, 16000);
    });
    const handles = await this.page.$$('a,button,input:not([type=hidden]),textarea,select,[role=button],[role=link],[contenteditable=true]');
    for (const handle of handles) {
      const info = await handle.evaluate(el => {
        const r = el.getBoundingClientRect();
        if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) || el.closest('[aria-hidden=true]') || !r.width || !r.height) return null;
        const x = Math.max(0, r.left) + (Math.min(innerWidth, r.right) - Math.max(0, r.left)) / 2;
        const y = Math.max(0, r.top) + (Math.min(innerHeight, r.bottom) - Math.max(0, r.top)) / 2;
        if (x <= 0 || y <= 0 || x >= innerWidth || y >= innerHeight || !el.contains(document.elementFromPoint(x, y))) return null;
        const visibleLabel = el.labels ? [...el.labels].filter(l => l.checkVisibility()).map(l => l.innerText).join(' ') : '';
        return {tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '',
          label: (visibleLabel || el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || el.getAttribute('title') || (['button', 'submit', 'reset'].includes(el.type) ? el.value : '') || el.tagName).slice(0, 180),
          disabled: !!el.disabled, options: el.tagName === 'SELECT' ? [...el.options].map((o, i) => ({number: i + 1, label: o.label.slice(0, 120), disabled: o.disabled})) : undefined};
      }).catch(() => null);
      if (info && this.controls.length < 100) this.controls.push({handle, info}); else await handle.dispose();
    }
    const screenshot = await this.shot();
    this.lastLookScreenshot = screenshot;
    this.trace('look', {page: this.page.url(), text, controls: this.controls.map((c, i) => ({number: i + 1, ...c.info})), screenshot});
    return [{type: 'text', text: this.sanitize(JSON.stringify({page: this.page.url(), viewport: this.viewport, text,
      controls: this.controls.map((c, i) => ({number: i + 1, ...c.info})),
      remaining: Math.max(0, this.spec.steps - this.calls), remaining_seconds: Math.max(0, Math.floor((this.deadline - Date.now()) / 1000)), policy_blocks: this.blockedRequests.splice(0), fake_values: FAKE}))},
    {type: 'image', mimeType: 'image/png', data: fs.readFileSync(path.join(this.output, screenshot)).toString('base64')}];
  }
  async control(number) {
    const control = this.controls[number - 1];
    if (!control) throw Error('Unknown or expired control; call look');
    // Revalidate at action time: stale layout must not click another element.
    const data = await control.handle.evaluate(el => {
      const r = el.getBoundingClientRect();
      const x = Math.max(0, r.left) + (Math.min(innerWidth, r.right) - Math.max(0, r.left)) / 2;
      const y = Math.max(0, r.top) + (Math.min(innerHeight, r.bottom) - Math.max(0, r.top)) / 2;
      if (!el.isConnected || !el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) || !r.width || !r.height || x <= 0 || y <= 0 || x >= innerWidth || y >= innerHeight || !el.contains(document.elementFromPoint(x, y))) return null;
      const form = el.form;
      return {label: [el.innerText, el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('href'), el.getAttribute('formaction'), form?.innerText, form?.getAttribute('action')].join(' '),
        fieldLabel: [el.getAttribute('aria-label'), el.getAttribute('placeholder'), ...(el.labels ? [...el.labels].map(l => l.innerText) : [])].join(' '),
        type: el.type || '', name: el.name || '', autocomplete: el.autocomplete || '',
        href: el.href || '', target: el.target || '', download: el.hasAttribute('download'),
        formAction: form?.action || '', formMethod: form?.method || '',
        formValues: form ? [...form.elements].filter(f => !f.disabled && !['button', 'submit', 'reset'].includes(f.type)).map(f => ({
          type: f.type || '', name: f.name || '', autocomplete: f.autocomplete || '', label: f.getAttribute('aria-label') || '',
          value: f.type === 'checkbox' || f.type === 'radio' ? (f.checked ? f.value : '') : f.value || ''})) : [],
        disabled: !!el.disabled, readOnly: !!el.readOnly, x, y};
    });
    if (!data || data.disabled) throw Error('Control no longer visible/enabled; call look');
    if (data.download || (data.target && data.target !== '_self') ||
        (data.href && !allowedURL(data.href, this.spec.safety.hosts)) ||
        (data.formAction && !allowedURL(data.formAction, this.spec.safety.hosts))) throw Error('Unsafe navigation or download');
    if (!this.spec.safety.allow_risky && risky(data.label)) throw Error('Risky action blocked');
    return {control, data};
  }
  async call(name, args) {
    this.assertReady();
    const tool = TOOLS.find(t => t.name === name);
    if (!tool) throw Error('Unknown tool');
    validate(tool.inputSchema, args);
    if (this.result.status !== 'running') throw Error('Session already finished');
    ++this.calls;
    if ((this.calls > this.spec.steps || Date.now() > this.deadline) && name !== 'finish') throw Error('Budget exhausted; call finish with an honest incomplete outcome');
    this.trace('tool', {name, arguments: args});
    if (name === 'look') return this.look();
    if (name === 'record_finding' || name === 'replay') {
      if (name === 'replay' && !(this.spec.earlier || []).some(f => f.key === args.key)) throw Error('Unknown earlier finding');
      const evidence = {...args, page: this.page.url(), viewport: `${this.viewport.width}×${this.viewport.height}`, screenshot: name === 'record_finding' && args.severity === 'slow' && this.beforeWaitScreenshot ? this.beforeWaitScreenshot : await this.shot()};
      if (name === 'replay') {
        this.result.replays = this.result.replays.filter(r => r.key !== args.key);
        this.result.replays.push(evidence);
      } else this.result.findings.push(evidence);
      this.save();
      return [{type: 'text', text: 'Evidence saved at this moment.'}];
    }
    if (name === 'finish') {
      Object.assign(this.result, args, {status: 'finished'});
      this.save();
      await this.close();
      return [{type: 'text', text: 'Session saved and browser closed.'}];
    }
    if (name !== 'wait') this.beforeWaitScreenshot = null;
    if (name === 'click' || name === 'type' || name === 'select') {
      const {control, data} = await this.control(args.control);
      if (name === 'type') {
        if (!['input', 'textarea'].includes(control.info.tag)) throw Error('Not a supported text field');
        const value = fakeValue(args.value, {...data, label: data.fieldLabel});
        if (!['', 'text', 'search', 'email', 'password', 'tel', 'url', 'textarea'].includes(data.type)) throw Error('Not a supported text field');
        if (data.readOnly) throw Error('Field is read only');
        await this.tap(data.x, data.y);
        const modifier = process.platform === 'darwin' ? 'Meta' : 'Control';
        await this.page.keyboard.down(modifier);
        await this.page.keyboard.press('KeyA');
        await this.page.keyboard.up(modifier);
        await this.page.keyboard.press('Backspace');
        await this.page.keyboard.type(value);
      } else if (name === 'select') {
        if (control.info.tag !== 'select') throw Error('Not a select');
        const option = await control.handle.evaluate((el, number) => {
          const o = el.options[number - 1]; return o ? {value: o.value, label: o.label, disabled: o.disabled} : null;
        }, args.option);
        if (!option || option.disabled || (!this.spec.safety.allow_risky && risky(option.label)) || !Object.values(FAKE).includes(option.value)) throw Error('Select values must be enumerated fake data');
        await control.handle.select(option.value);
      } else {
        // A GET form can still carry prefilled personal data. Never allow a
        // click to submit unknown form values, even with --allow-risky.
        for (const field of data.formValues) {
          if (field.value && (!Object.values(FAKE).includes(field.value) && !['on', 'true', 'false'].includes(field.value))) throw Error('Form contains values outside the fake-data allowlist');
          if (cardlike(field.value) || /card|credit|debit|cc-|cvv|cvc|iban|routing|bank|social.security/i.test(`${field.name} ${field.autocomplete} ${field.label}`)) throw Error('Financial and identity forms are prohibited');
        }
        if (['file', 'password', 'hidden', 'submit', 'image'].includes(data.type)) {
          if (data.type !== 'submit') throw Error('Unsupported or unsafe click');
        }
        // Fixed visible coordinates, not a selector or JS supplied by the model.
        await this.tap(data.x, data.y);
      }
    } else if (name === 'wait') {
      this.beforeWaitScreenshot = this.lastLookScreenshot;
      await new Promise(resolve => setTimeout(resolve, args.seconds * 1000));
    } else if (name === 'scroll') {
      // Touch taps do not move the mouse cursor; a wheel at (0,0) can miss
      // an open modal's scroll container entirely.
      const x = this.viewport.width / 2;
      const from = this.viewport.height * (args.direction === 'down' ? .85 : .35);
      const to = this.viewport.height * (args.direction === 'down' ? .3 : .85);
      if (this.viewport.hasTouch) {
        const cdp = await this.page.createCDPSession();
        try {
          await cdp.send('Input.dispatchTouchEvent', {type:'touchStart', touchPoints:[{x,y:from}]});
          for (let i=1;i<=6;i++) await cdp.send('Input.dispatchTouchEvent', {type:'touchMove', touchPoints:[{x,y:from+(to-from)*i/6}]});
          await cdp.send('Input.dispatchTouchEvent', {type:'touchEnd', touchPoints:[]});
        } finally { await cdp.detach(); }
      } else {
        await this.page.mouse.move(x, this.viewport.height * .7);
        await this.page.mouse.wheel({deltaY: args.direction === 'down' ? this.viewport.height * .75 : -this.viewport.height * .75});
      }
    } else if (name === 'back') {
      if (this.authentication) throw Error('Back is unavailable in authenticated sessions; use restart');
      await this.page.goBack({waitUntil: 'domcontentloaded'});
    } else if (name === 'restart') {
      await this.page.goto(this.startURL, {waitUntil: 'domcontentloaded'});
    }
    await this.clearControls();
    // Short bounded settle, not a scripted test flow; next actions remain model choices.
    await new Promise(resolve => setTimeout(resolve, 150));
    return this.look();
  }
  async close() {
    if (this.browser) await this.browser.close().catch(() => {});
    if (this.profile) fs.rmSync(this.profile, {recursive: true, force: true});
    this.browser = null;
  }
}

async function protocolReply(message, session, start) {
  if (message.id === undefined) return undefined;
  let result;
  if (message.method === 'initialize') result = {protocolVersion: '2024-11-05', capabilities: {tools: {}}, serverInfo: {name: 'uxlab', version: '1.0.0'}};
  else if (message.method === 'ping') result = {};
  else if (message.method === 'tools/list') result = {tools: session.bootstrapping ? [] : TOOLS};
  else if (message.method === 'tools/call') {
    try {
      await start();
      result = {content: await session.call(message.params.name, message.params.arguments || {})};
    } catch (error) {
      session.trace('error', {message: session.spec?.person.auth ? 'Authenticated browser operation failed' : error.message});
      result = {isError: true, content: [{type: 'text', text: session.spec?.person.auth ? 'Authenticated browser operation failed' : error.message}]};
    }
  } else throw Error('Unsupported MCP method');
  return {jsonrpc: '2.0', id: message.id, result};
}

async function main() {
  if (process.argv[2] === '--connect') {
    const socket = require('net').createConnection(process.argv[3]);
    socket.on('error', () => { process.stderr.write('Browser connection failed\n'); process.exitCode = 1; process.stdin.destroy(); });
    process.stdin.pipe(socket); socket.pipe(process.stdout);
    socket.on('close', () => process.stdin.destroy());
    return;
  }
  const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  if (!allowedURL(spec.url, spec.safety.hosts)) throw Error('Initial host prohibited');
  const session = new BrowserSession(spec);
  let starting;
  let server, input = process.stdin, output = process.stdout;
  let lines, deadline;
  let shuttingDown = false;
  const shutdown = async () => {
    if (shuttingDown) return;
    shuttingDown = true;
    await session.close();
    clearTimeout(deadline);
    if (lines) lines.close();
    input.destroy();
    if (server) server.close();
    // Let the stdout pipe drain; process.exit() can truncate MCP responses.
    process.exitCode = 0;
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
  deadline = setTimeout(shutdown, spec.timeout * 1000 + 2000);
  deadline.unref();
  if (process.argv[3] === '--serve') {
    try {
      await session.start();
      starting = Promise.resolve();
      server = require('net').createServer();
      await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(process.argv[4], resolve);
      });
      fs.chmodSync(process.argv[4], 0o600);
      fs.writeFileSync(path.join(path.dirname(process.argv[4]), 'browser.ready'), 'ready', {mode: 0o600});
      input = output = await new Promise(resolve => server.once('connection', resolve));
      server.close();
    } catch {
      await shutdown();
      throw Error('Authentication bootstrap failed');
    }
  }
  lines = readline.createInterface({input, crlfDelay: Infinity});
  let chain = Promise.resolve();
  lines.on('line', line => {
    chain = chain.then(async () => {
      let message;
      try {
        message = JSON.parse(line);
        const reply = await protocolReply(message, session, () => (starting = starting || session.start()));
        if (reply) output.write(session.sanitize(JSON.stringify(reply)) + '\n');
      } catch (error) {
        output.write(JSON.stringify({jsonrpc: '2.0', id: message?.id ?? null, error: {code: -32600, message: session.spec?.person.auth ? 'Authenticated browser operation failed' : error.message}}) + '\n');
      }
    }).catch(() => process.stderr.write('Browser protocol failed\n'));
  });
  lines.on('close', () => chain.finally(shutdown));
}
if (require.main === module) main().catch(() => {process.stderr.write('Browser startup failed\n'); process.exitCode = 1;});
module.exports = {BrowserSession, TOOLS, validate, protocolReply};
