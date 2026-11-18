const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/conversation-chrome.js'), 'utf8');
const source = name => {
  const start = app.indexOf('  function ' + name + '(');
  assert.ok(start >= 0);
  return app.slice(start, app.indexOf('\n  }\n', start) + 4);
};

test('an empty orchestration map clears stale content without measuring layout', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setContent(`<div id="statusRailOrchestrationPane">
      <div id="orchPlaybooks"><span>Delegate</span></div><div id="orchEmpty"></div>
      <div id="orchMap"><svg id="orchMapEdges"><path d="M0,0 L1,1"/></svg>
      <div id="orchMapRoot"><button class="orch-node-root">Old session</button></div>
      <div id="orchMapLanes"><button class="orch-node" data-lane-id="old">Old lane</button></div>
      <div id="orchMapDone"></div></div></div>`);
    await page.addScriptTag({content:`
      const sharedState = {_orchLastKeys:new Set(),_orchFamilyTreeSid:'',_orchSim:null};
      const orchContext = () => ({convId:'',sid:'',rootSid:''});
      const orchPaintFamilyRows = () => {}, orchRefreshRegistry = () => {};
      const orchReplayClock = () => null, orchCollectLanes = () => [];
      window.geometryReads = 0;
      for (const element of document.querySelectorAll('#orchMap, #orchMap *')) {
        element.getBoundingClientRect = () => { geometryReads++; throw new Error('empty map measured'); };
        for (const key of ['clientWidth','scrollHeight']) {
          Object.defineProperty(element,key,{get(){geometryReads++;throw new Error('empty map sized');}});
        }
      }
      ${source('orchDrawEdges')}
      ${source('updateOrchestrationPane')}
      updateOrchestrationPane();
    `});
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.deepEqual(await page.evaluate(() => ({
      reads:geometryReads, root:document.getElementById('orchMapRoot').textContent,
      lanes:document.getElementById('orchMapLanes').textContent,
      edges:document.getElementById('orchMapEdges').innerHTML,
    })), {reads:0,root:'',lanes:'',edges:''});
    await page.setContent(`<div id="orchMap"><svg id="orchMapEdges"></svg>
      <button class="orch-node-root"></button><div id="orchMapLanes"><button class="orch-node-lane is-working"></button></div></div>`);
    const paths = await page.evaluate(() => {
      const map = document.getElementById('orchMap');
      Object.defineProperty(map,'clientWidth',{get:()=>460});
      Object.defineProperty(map,'scrollHeight',{get:()=>200});
      map.getBoundingClientRect=()=>({left:0,top:0});
      map.querySelector('.orch-node-root').getBoundingClientRect=()=>({left:10,top:10,width:200,height:40,bottom:50});
      map.querySelector('.orch-node-lane').getBoundingClientRect=()=>({left:40,top:100,width:200,height:40});
      orchDrawEdges();
      return {paths:[...map.querySelectorAll('path')].map(p=>[p.getAttribute('class'),p.getAttribute('d')]),
        viewBox:document.getElementById('orchMapEdges').getAttribute('viewBox')};
    });
    assert.deepEqual(paths, {paths:[
      ['orch-edge orch-edge-working','M110.0,50.0 C110.0,75.0 140.0,75.0 140.0,100.0'],
      ['orch-edge orch-edge-flow','M110.0,50.0 C110.0,75.0 140.0,75.0 140.0,100.0'],
    ],viewBox:'0 0 460 200'});
  } finally {await browser.close();}
});
