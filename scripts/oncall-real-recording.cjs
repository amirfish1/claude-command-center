// Timed captures of actual browser pixels. No DOM replacement or generated UI.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const sensitive = require('./oncall-capture-safety.cjs');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const root = path.resolve(__dirname, '../.claude/screenshots/launch-captures');
function outputDir() {
  const dir = path.resolve(process.env.PROOF_CAPTURE_DIR || root);
  if (dir !== root && !dir.startsWith(root + path.sep)) throw Error('Capture must stay in launch-captures');
  fs.mkdirSync(dir, {recursive:true});
  return dir;
}
async function safeText(page, selector) {
  const text = await page.evaluate(sel => (sel ? document.querySelector(sel) : document.body)?.innerText || '', selector);
  // RFC-reserved examples are the only addresses permitted in a capture.
  if (sensitive(text.replace(/[\w.+-]+@example\.(?:test|com|org)\b/g, '[example address]'))) {
    throw Error('Private address or path visible; refusing real frame');
  }
}
// Use the Puppeteer page client that owns setViewport. A second CDP session
// captures at 1x or changes page metrics during navigation. This small adapter
// relies on Puppeteer 25 CdpPage._client(); the real browser check covers it.
const captureStates=new WeakMap();
async function capturePixels(page,selector=null,format='png') {
  let state=captureStates.get(page);
  if (!state) { state={tail:Promise.resolve()};captureStates.set(page,state); }
  const task=state.tail.then(async()=>{
    if (!state.cdp) state.cdp=page._client();
    const viewport=page.viewport();
    await safeText(page,selector);
    const params={format,captureBeyondViewport:false,fromSurface:true};
    if (format==='jpeg') params.quality=88;
    if (selector) {
      const target=await page.$(selector), box=target && await target.boundingBox();
      if (!box) throw Error('Real recording crop is not visible');
      params.clip={x:Math.max(0,box.x),y:Math.max(0,box.y),
        width:Math.min(box.width,viewport.width-Math.max(0,box.x)),
        height:Math.min(box.height,viewport.height-Math.max(0,box.y)),scale:1};
    }
    const result=await state.cdp.send('Page.captureScreenshot',params);
    await safeText(page,selector);
    return Buffer.from(result.data,'base64');
  });
  state.tail=task.catch(()=>{});
  return task;
}
class Recording {
  constructor(dir, name, page, selector=null) {
    if (!/^[a-z0-9-]+$/.test(name)) throw Error('Invalid recording name');
    this.dir=path.join(dir, name); fs.mkdirSync(this.dir, {recursive:false});
    this.page=page; this.selector=selector; this.frames=[]; this.events=[];
    this.started=performance.now(); this.utc=new Date().toISOString();
    this.stopped=false; this.failure=null; this.loop=this.capture();
  }
  event(kind, details={}) {
    this.events.push({kind, seconds:(performance.now()-this.started)/1000, utc:new Date().toISOString(), ...details});
  }
  async capture() {
    try {
      while (!this.stopped) {
        const before=performance.now();
        try {
          const file=`frame-${String(this.frames.length).padStart(7,'0')}.jpg`;
          const pixels=await capturePixels(this.page,this.selector,'jpeg');
          const seconds=(performance.now()-this.started)/1000, utc=new Date().toISOString();
          // Do not save a frame if navigation exposed sensitive text during capture.
          await safeText(this.page, this.selector);
          fs.writeFileSync(path.join(this.dir,file),pixels);
          this.frames.push({file, seconds, utc, capture_ms:performance.now()-before,
            sha256:crypto.createHash('sha256').update(pixels).digest('hex')});
        } catch(err) {
          // Real page navigation can destroy a JS execution context between
          // screenshots. Keep the true time gap; the editor rejects >2s gaps.
          if (!/Execution context was destroyed|Cannot find context with specified id|Node is detached/.test(String(err))) throw err;
        }
        // Serial screenshots: slow machines reduce the frame rate, never overlap browsers.
        await sleep(Math.max(0, 166-(performance.now()-before)));
      }
    } catch (err) { this.failure=String(err); this.stopped=true; }
  }
  async stop() {
    this.stopped=true; await this.loop;
    const data={schema:1, kind:'real-timed-browser-frames', run_id:process.env.PROOF_RUN_ID || 'standalone',
      model:process.env.PROOF_MODEL, started_utc:this.utc, ended_utc:new Date().toISOString(),
      viewport:this.page.viewport(), crop:this.selector, target_fps:6, failure:this.failure,
      frames:this.frames, events:this.events};
    fs.writeFileSync(path.join(this.dir,'recording.json'),JSON.stringify(data,null,2));
    if (this.failure) throw Error(this.failure);
    if (this.frames.length < 2) throw Error('Recording has fewer than two real frames');
    return data;
  }
}
module.exports={Recording, outputDir, safeText, sleep, capturePixels};
