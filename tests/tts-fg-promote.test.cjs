const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
// CCC-144: a clip still being prepared at bg priority when the read needs it
// is asked for again at fg, so a cold click is not queued behind other prepares.
function harness(entry) {
  const context = vm.createContext({console, Blob, setTimeout, clearTimeout});
  vm.runInContext(`
    let _ttsNeuralToken=0, _ttsUtterance, _ttsNeural, _ttsAudio, _ttsLastCharIndex=0;
    let _ttsPaused=false, _ttsRate=1.25, _ttsActivePaneId='p1', _ttsDirectBtn=null;
    const fetches=[];
    const window={}, URL={createObjectURL:()=> 'clip',revokeObjectURL:()=>{}};
    const _ttsScheduleCacheDebug=()=>{}, _ttsSetPlay=()=>{}, _ttsSetStreaming=()=>{};
    const clearTtsHighlight=()=>{}, clearTtsCaption=()=>{}, _ttsCool=()=>{}, _ttsCoolMs=()=>0;
    const _ttsLog=()=>{}, _ttsShowVoice=()=>{}, _ttsPerfStart=()=>{}, _ttsPerfSilence=()=>{};
    const setTtsButtonsState=()=>{}, setTtsButtonsBusy=()=>{}, ttsButtons=()=>[];
    const fgGot={createUrl:()=> 'fg',voice:'Kokoro: af_nova',blob:{},done:Promise.resolve()};
    const _ttsFetchNeural=(text,voice,prio)=>{fetches.push([voice,prio]);return Promise.resolve(fgGot);};
    const _ttsContinuousOk=()=>false;
    const cancelAnimationFrame=()=>{};
    function Audio(){
      this.duration=Infinity;this.currentTime=0;this.paused=true;
      this.seekable={length:1,start:()=>0,end:()=>5};
      this.play=()=>{this.paused=false;this.onplay?.();return Promise.resolve();};
      this.pause=()=>{this.paused=true;};
      this.load=()=>{};
    }
    const first='a'.repeat(80);
    const bgGot={createUrl:()=> 'bg',voice:'Kokoro: af_nova',blob:null,done:new Promise(()=>{})};
    const _ttsHeadCache=new Map([[first,Object.assign({p:Promise.resolve(bgGot),voice:bgGot.voice},${JSON.stringify(entry)})]]);
    let _ttsChunkState={chunks:[{text:first,start:0}],index:0,paneId:'p1'};
    function _ttsSpeakNextChunk(){_ttsSpeakChunkNeural(_ttsChunkState,_ttsChunkState.chunks[_ttsChunkState.index]);}
  `, context);
  const start=source.indexOf('  function _ttsSpeakChunkNeural(');
  vm.runInContext(source.slice(start,source.indexOf('  // One-time iOS-Safari',start)),context);
  const run = js => vm.runInContext(js,context);
  return {run, async start(){run('_ttsSpeakNextChunk()');for(let i=0;i<10;i++)await Promise.resolve();}};
}

test('a bg-prepared clip still generating is re-requested at fg in the same voice', async()=>{
  const h=harness({bg:true,ready:false});await h.start();
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(fetches)')),[['Kokoro: af_nova','fg']]);
  assert.equal(h.run('_ttsAudio.src'),'fg');
});

test('a fully prepared clip plays from cache without a request', async()=>{
  const h=harness({bg:true,ready:true});await h.start();
  assert.equal(h.run('fetches.length'),0);
  assert.equal(h.run('_ttsAudio.src'),'bg');
});
