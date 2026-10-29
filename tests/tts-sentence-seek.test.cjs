const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
function harness() {
  const context = vm.createContext({console, Blob, setTimeout, clearTimeout});
  vm.runInContext(`
    let _ttsNeuralToken=0, _ttsUtterance, _ttsNeural, _ttsAudio, _ttsLastCharIndex=0;
    let _ttsPaused=false, _ttsRate=1.25, _ttsActivePaneId='p1', _ttsDirectBtn=null;
    let plays=0, fetches=0, bufferEnd=5;
    const window={}, URL={createObjectURL:()=> 'clip',revokeObjectURL:()=>{}};
    const _ttsScheduleCacheDebug=()=>{}, _ttsSetPlay=()=>{}, _ttsSetStreaming=()=>{};
    const clearTtsHighlight=()=>{}, clearTtsCaption=()=>{}, _ttsCool=()=>{}, _ttsCoolMs=()=>0;
    const _ttsLog=()=>{}, _ttsShowVoice=()=>{}, _ttsPerfStart=()=>{}, _ttsPerfSilence=()=>{};
    const setTtsButtonsState=()=>{}, setTtsButtonsBusy=()=>{}, ttsButtons=()=>[];
    const _ttsFetchNeural=()=>{fetches++; throw Error('Unnecessary synthesis');};
    const _ttsContinuousOk=()=>false;
    const cancelAnimationFrame=()=>{};
    function Audio(){
      this.duration=Infinity;this.currentTime=0;this.paused=true;
      this.seekable={length:1,start:()=>0,end:()=>bufferEnd};
      this.play=()=>{plays++;this.paused=false;this.onplay?.();return Promise.resolve();};
      this.pause=()=>{this.paused=true;this.onpause?.();};
      this.load=()=>{this.onloadedmetadata?.();};
    }
    const first='a'.repeat(400), second='b'.repeat(400);
    let finishFirst;
    const firstGot={createUrl:()=> 'live',voice:'Kokoro:test',blob:null,done:new Promise(r=>{finishFirst=r;})};
    const secondGot={createUrl:()=> 'ready',voice:'Kokoro:test',blob:{},done:Promise.resolve()};
    const _ttsHeadCache=new Map([[first,{p:Promise.resolve(firstGot),voice:firstGot.voice}],[second,{p:Promise.resolve(secondGot),voice:secondGot.voice}]]);
    let _ttsChunkState={chunks:[{text:first,start:0},{text:second,start:400}],index:0,paneId:'p1'};
    function _ttsSpeakNextChunk(){_ttsSpeakChunkNeural(_ttsChunkState,_ttsChunkState.chunks[_ttsChunkState.index]);}
    function _ttsEngineCancel(){_ttsNeuralToken++;_ttsNeural?.audio.pause();_ttsNeural=null;}
  `, context);
  let start=source.indexOf('  function _ttsSpeakChunkNeural(');
  vm.runInContext(source.slice(start,source.indexOf('  // One-time iOS-Safari',start)),context);
  start=source.indexOf('  function _ttsSeekNeuralTo(');
  vm.runInContext(source.slice(start,source.indexOf('  function _refreshTtsRateBtns(',start)),context);
  const run = js => vm.runInContext(js,context);
  return {run, async start(){run('_ttsSpeakNextChunk()');for(let i=0;i<10;i++)await Promise.resolve();}};
}

test('skipping beyond streamed bytes waits on the same request and resumes when buffered', async()=>{
  const h=harness();await h.start();
  assert.equal(h.run('_ttsSeekNeuralTo(90)'),true);
  assert.equal(h.run('_ttsAudio.paused'),true);
  assert.equal(h.run('fetches'),0);
  h.run('bufferEnd=10;_ttsAudio.onprogress()');
  assert.equal(h.run('_ttsAudio.currentTime'),6);
  assert.equal(h.run('_ttsAudio.paused'),false);
  assert.equal(h.run('fetches'),0);
});

test('paused sentence skipping seeks without resuming or requesting synthesis', async()=>{
  const h=harness();await h.start();
  h.run('_ttsPaused=true;_ttsAudio.pause();bufferEnd=15');
  const plays=h.run('plays');
  assert.equal(h.run('_ttsSeekNeuralTo(120)'),true);
  assert.equal(h.run('_ttsAudio.currentTime'),8);
  assert.equal(h.run('_ttsAudio.paused'),true);
  assert.equal(h.run('plays'),plays);
  assert.equal(h.run('fetches'),0);
});

test('skipping into a prepared next clip reuses its original split and honors its seek', async()=>{
  const h=harness();await h.start();
  h.run('_ttsAudio.duration=20;bufferEnd=20');
  assert.equal(h.run('_ttsSeekNeuralTo(430)'),true);
  for(let i=0;i<10;i++)await Promise.resolve();
  assert.equal(h.run('_ttsChunkState.index'),1);
  assert.equal(h.run('_ttsAudio.currentTime'),1.5);
  assert.equal(h.run('fetches'),0);
  h.run('finishFirst()');
  for(let i=0;i<10;i++)await Promise.resolve();
  assert.equal(h.run('_ttsAudio.src'),'ready');
  assert.equal(h.run('_ttsChunkState.index'),1);
});


test('skipping while the current request waits for headers reuses prepared next audio', async()=>{
  const h=harness();
  h.run("let resolveHead; _ttsHeadCache.get(first).p=new Promise(r=>{resolveHead=r;})");
  await h.start();
  h.run("bufferEnd=20");
  assert.equal(h.run('_ttsSeekNeuralTo(400)'),true);
  for(let i=0;i<10;i++)await Promise.resolve();
  assert.equal(h.run('_ttsAudio.src'),'ready');
  h.run('resolveHead(firstGot)');
  for(let i=0;i<10;i++)await Promise.resolve();
  assert.equal(h.run('_ttsAudio.src'),'ready');
  assert.equal(h.run('fetches'),0);
});
