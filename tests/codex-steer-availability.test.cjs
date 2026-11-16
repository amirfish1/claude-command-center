const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = require('./legacy-dashboard-source.cjs');
const inputSource = fs.readFileSync('static/conversation-input.js', 'utf8');
const first = inputSource.indexOf('      if (activeSteerBtn) {');
const composer = inputSource.slice(first, inputSource.indexOf('      // Compact button', first))
  .replace(/\bstate\./g, '').replace(/\(0, helpers\.(\w+)\)/g, '$1');
const helperStart = app.indexOf('  function codexTurnSteerable(');
const helper = app.slice(helperStart, app.indexOf('\n  // "Could a steer/send', helperStart));

function state(overrides = {}) {
  return {
    isCodex: true, isKimi: false, isCursor: false, isHermes: false,
    isPkood: false, isGemini: false, isAntigravity: false, isDevinCli: false,
    canSend: true, hasSession: true, isNewSession: false, isBacklogIssue: false,
    currentSession: { source: 'codex' }, liveStatus: {},
    liveStatusMatchesOpenConv: () => true, devinSteerCapableNow: () => false,
    ...overrides,
  };
}

test('composer permits a Codex steer attempt before live status has arrived', () => {
  for (const liveStatus of [{}, { codexState: 'idle' }, { codexState: 'working', codexWriter: 'external' }]) {
    const button = { classList: { toggle(name, visible) { button.visible = visible; } } };
    vm.runInNewContext(helper + composer, state({ liveStatus, activeSteerBtn: button }));
    assert.equal(button.visible, true);
    assert.equal(button.disabled, false);
    assert.match(button.title, /send if no turn is running/);
  }
});

test('unsupported, read-only, and new conversations still cannot steer', () => {
  for (const overrides of [{ isCodex: false }, { canSend: false }, { hasSession: false }, { isNewSession: true }, { isBacklogIssue: true }]) {
    const button = { classList: { toggle() {} } };
    vm.runInNewContext(helper + composer, state({ ...overrides, activeSteerBtn: button }));
    assert.equal(button.disabled, true);
  }
});

test('past-message steer uses running status without rejecting writer attribution', () => {
  for (const writer of ['ccc', 'desktop', 'external', 'unknown', null]) {
    assert.equal(vm.runInNewContext(helper + '\ncodexTurnSteerable()', state({ liveStatus: { codexState: 'working', codexWriter: writer } })), true);
  }
  for (const overrides of [{ liveStatus: {} }, { liveStatusMatchesOpenConv: () => false }, { currentSession: { source: 'devin-cli' } }]) {
    assert.equal(vm.runInNewContext(helper + '\ncodexTurnSteerable()', state(overrides)), false);
  }
});

test('touch composer keeps Steer reachable through its shipped actions menu', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true });
    await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><div class="conv-pane is-webui-session" style="width:390px;height:844px"><div class="conv-input-bar visible"><button class="steer-btn" aria-label="Steer">Steer</button><div class="conv-actions-group"><button id="convActionsBtn">+</button><div id="convActionsMenu" style="display:none"><button class="conv-actions-item" data-act="steer">Steer</button></div></div></div></div>');
    await page.addStyleTag({ content: fs.readFileSync('static/app.css', 'utf8') });
    await page.addScriptTag({content: inputSource});
    await page.evaluate(({ composer }) => {
      Object.assign(window, {
        activeSteerBtn: document.querySelector('.steer-btn'),
        isCodex: true, isKimi: false, isCursor: false, isHermes: false,
        isPkood: false, isGemini: false, isAntigravity: false, isDevinCli: false,
        canSend: true, hasSession: true, isNewSession: false, isBacklogIssue: false,
        liveStatus: {}, devinSteerCapableNow: () => false,
      });
      (0, eval)(composer);
      window.$convSteerBtn = activeSteerBtn;
      CCCConversationInput.createController({state: window, helpers: {sendToTerminal: (pane, mode) => { window.clicked = {pane, mode}; }}}).initConversationActionsMenu();
    }, { composer });
    assert.equal(await page.evaluate(() => matchMedia('(pointer: coarse)').matches), true);
    await page.tap('#convActionsBtn');
    await page.tap('[data-act="steer"]');
    assert.deepEqual(await page.evaluate(() => window.clicked), {pane:'p1', mode:'steer'});
  } finally {
    await browser.close();
  }
});
