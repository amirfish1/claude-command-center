const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const source = fs.readFileSync('static/app.js', 'utf8');
const extract = (name, next) => source.slice(source.indexOf('  function ' + name + '('), source.indexOf('  function ' + next + '('));
let browser;
before(async () => {browser = await puppeteer.launch({headless: true, executablePath: findChromePath(), args: ['--no-sandbox']});});
after(async () => {await browser?.close();});
async function cluster(html, id) {
  const page = await browser.newPage();
  try {
    await page.setContent('<div class="conversations-view">' + html + '</div>');
    await page.addScriptTag({content: extract('assistantTextElements', '_ttsSpeakButtonData')
      + extract('isPendingSendEchoElement', 'lastMessageTtsData')});
    return await page.evaluate(id => _ttsPlaybackElements(document.getElementById(id)).map(el => el.textContent), id);
  } finally {await page.close();}
}
test('Codex paragraphs continue across markers, tool groups, and result footers until the next user', async () => {
  const html = '<div class="event assistant kimi-marker"></div><div class="assistant-text">First paragraph.</div><div id="first" class="kimi-answer-meta">speaker</div>'
    + '<div class="kimi-tool-group"><div class="kimi-tool"><div class="assistant-text">Tool output must be skipped.</div></div></div>'
    + '<div class="event assistant kimi-marker"></div><div class="assistant-text">Second paragraph.</div><div id="second" class="kimi-answer-meta">speaker</div>'
    + '<div class="event result">Done footer.</div><div class="event user_text"><div class="user-msg">Next question.</div></div>'
    + '<div class="assistant-text">Next answer must not be read.</div><div class="kimi-answer-meta"></div>';
  assert.deepEqual(await cluster(html, 'first'), ['First paragraph.', 'Second paragraph.']);
  assert.deepEqual(await cluster(html, 'second'), ['Second paragraph.']);
});
test('Claude events include following live prose once and ignore queued echoes and harness notifications', async () => {
  assert.deepEqual(await cluster('<div id="first" class="event assistant"><div class="assistant-text">Beginning.</div></div>'
    + '<div class="event user_text pending">Queued input.</div><div class="event user_text task-notification-event">Tool notification.</div>'
    + '<div class="event assistant"><div class="assistant-text">Continuation.</div></div>'
    + '<div class="stream-bubble"><div class="stream-bubble-blocks"><div class="assistant-text">Live ending.</div></div></div>', 'first'),
    ['Beginning.', 'Continuation.', 'Live ending.']);
});
