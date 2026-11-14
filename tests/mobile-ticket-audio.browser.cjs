// Direct microphone capture and dragging, without sending real audio or tickets.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
(async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width:390,height:844,isMobile:true,hasTouch:true});
    await page.setRequestInterception(true);
    page.on('request', request => request.respond({status:200,contentType:'text/html',
      body:'<meta name="viewport" content="width=device-width,initial-scale=1"><body class="has-mobile-bottom-nav"><button id="sidebarNewTicketBtn">Ticket</button></body>'}));
    const code = fs.readFileSync('static/mobile-ticket-record.js','utf8');
    async function setup() {
      await page.goto('http://localhost/fixture');
      await page.evaluate(() => {
        window.uploads = []; window.stopped = 0; window.started = 0;
        window.fetch = async (url, options) => {
          if (options?.method === 'POST') {
            uploads.push({url, type:options.body.type, bytes:options.body.size});
            return {ok:true,json:async()=>({ok:true,text:'A locally transcribed ticket'})};
          }
          return {ok:true,json:async()=>({ok:true,available:true})};
        };
        navigator.mediaDevices.getUserMedia = async () => {
          if (window.deferPermission) await new Promise(resolve => {window.grantPermission=resolve;});
          return {getTracks:()=>[{stop:()=>window.stopped++}]};
        };
        window.MediaRecorder = class {
          static isTypeSupported(type) { return type === 'audio/mp4'; }
          constructor() { this.mimeType='audio/mp4'; this.state='inactive'; }
          start() { this.state='recording'; window.started++; }
          stop() {
            this.state='inactive';
            this.ondataavailable({data:new Blob(['fixture-audio'],{type:this.mimeType})});
            this.onstop();
          }
        };
        document.getElementById('sidebarNewTicketBtn').onclick=()=>{
          const input=document.createElement('textarea'); input.id='fqTicketNote'; document.body.append(input);
        };
        Element.prototype.setPointerCapture=()=>{};
      });
      await page.addScriptTag({content:code});
    }
    async function pointer(type,x=320,y=720) {
      await page.evaluate(({type,x,y})=>document.getElementById('mobileTicketRecord').dispatchEvent(
        new PointerEvent(type,{pointerId:1,button:0,clientX:x,clientY:y})),{type,x,y});
    }
    await setup();
    await pointer('pointerdown');
    await page.waitForFunction(()=>started===1);
    await pointer('pointerup');
    await page.waitForSelector('#fqTicketNote');
    assert.equal(await page.$eval('#fqTicketNote',e=>e.value),'A locally transcribed ticket');
    assert.deepEqual(await page.evaluate(()=>uploads),[{url:'/api/ticket-audio/transcribe',type:'audio/mp4',bytes:13}]);
    assert.equal(await page.evaluate(()=>stopped),1);

    await setup();
    await page.evaluate(()=>window.deferPermission=true);
    await pointer('pointerdown');
    await page.waitForFunction(()=>typeof grantPermission==='function');
    await pointer('pointerup');
    await page.evaluate(()=>grantPermission());
    await page.waitForFunction(()=>stopped===1);
    assert.equal(await page.evaluate(()=>started),0,'early release must not start capture');

    await setup();
    await pointer('pointerdown');
    await page.waitForFunction(()=>started===1);
    await pointer('pointermove',50,200);
    await pointer('pointerup',50,200);
    assert.equal(await page.evaluate(()=>uploads.length),0,'drag never uploads');
    assert.equal(await page.$('#fqTicketNote'),null);
    const position = await page.$eval('#mobileTicketRecord',e=>({x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y}));
    await setup();
    // Simulate navigation's late measurement after script startup.
    await page.evaluate(()=>document.documentElement.style.setProperty('--ccc-mobile-app-nav-height','55px'));
    await page.waitForFunction(()=>Math.abs(document.getElementById('mobileTicketRecord').getBoundingClientRect().y
      - JSON.parse(localStorage.getItem('ccc-mobile-ticket-record-position')).y * (innerHeight-55-80)) < 1);
    await page.evaluate(()=>document.documentElement.style.setProperty('--ccc-mobile-app-nav-height','70px'));
    await page.waitForFunction(()=>Math.abs(document.getElementById('mobileTicketRecord').getBoundingClientRect().y
      - JSON.parse(localStorage.getItem('ccc-mobile-ticket-record-position')).y * (innerHeight-70-80)) < 1);
    const restored = await page.$eval('#mobileTicketRecord',e=>({x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y}));
    assert.deepEqual(restored,position,'position persists after reload');
    await page.setViewport({width:320,height:480,isMobile:true,hasTouch:true});
    await page.waitForFunction(()=>{
      const r=document.getElementById('mobileTicketRecord').getBoundingClientRect();
      return r.left>=0 && r.right<=innerWidth && r.top>=0 && r.bottom<=innerHeight-70;
    });
    console.log('PASS: direct audio upload, draft, release during permission, drag cancellation, persistence and resize bounds');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
