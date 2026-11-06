const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
// CCC-144 Q577: a cold free-voice read starts on the built-in voice for its
// first clip while the free voice makes clip 1 at fg, then hands over.
function harness({continuous = false, pref = ''} = {}) {
  const context = vm.createContext({console, Blob, setTimeout, clearTimeout, performance});
  vm.runInContext(`
    let _ttsNeuralToken=0, _ttsUtterance, _ttsNeural, _ttsAudio, _ttsLastCharIndex=0, _ttsStartWhy='';
    let _ttsPaused=false, _ttsRate=1.25, _ttsActivePaneId='p1', _ttsDirectBtn=null, _ttsKeepVoice='';
    let _ttsNeuralCooldownUntil=0, _ttsBoundUtteranceText='';
    const fetches=[], spoken=[], logs=[]; let done=null;
    const localStorage={getItem:()=>${JSON.stringify(pref)}};
    const window={speechSynthesis:{speak:(u)=>{spoken.push(u.text);u.onstart?.();}}};
    function SpeechSynthesisUtterance(t){this.text=t;}
    const URL={createObjectURL:()=> 'clip',revokeObjectURL:()=>{}};
    const _ttsScheduleCacheDebug=()=>{}, _ttsSetPlay=()=>{}, _ttsSetStreaming=()=>{};
    const clearTtsHighlight=()=>{}, clearTtsCaption=()=>{}, _ttsCool=()=>{}, _ttsCoolMs=()=>0;
    const _ttsLog=(k)=>logs.push(k), _ttsShowVoice=()=>{}, _ttsPerfStart=()=>{}, _ttsPerfSilence=()=>{};
    const setTtsButtonsState=()=>{}, setTtsButtonsBusy=()=>{}, ttsButtons=()=>[], ttsButtonPaneId=()=>'p1';
    const highlightTtsWord=()=>{}, updateTtsCaption=()=>{}, showOpToast=()=>{}, stopTextToSpeech=()=>{};
    const ttsVoiceForPane=()=> 'Kokoro: af_nova';
    const _ttsBindChunkCompletion=(u,cb)=>{done=cb;};
    const fgGot={createUrl:()=> 'fg',voice:'Kokoro: af_nova',blob:{},done:Promise.resolve()};
    const _ttsFetchNeural=(text,voice,prio)=>{fetches.push([text,voice,prio]);return Promise.resolve(fgGot);};
    const _ttsContinuousOk=()=>${continuous}, _ttsCanStream=()=>false, _ttsEventLagMs=()=>0;
    const cancelAnimationFrame=()=>{}, requestAnimationFrame=()=>1;
    function Audio(){
      this.duration=Infinity;this.currentTime=0;this.paused=true;
      this.seekable={length:1,start:()=>0,end:()=>5};
      this.play=()=>{this.paused=false;this.onplay?.();return Promise.resolve();};
      this.pause=()=>{this.paused=true;};
      this.load=()=>{};
    }
    const _ttsHeadCache=new Map();
    function _ttsNeuralUsable(state){return state.neuralOk!==false;}
    let _ttsChunkState={chunks:[{text:'head',start:0},{text:'rest',start:5}],index:0,paneId:'p1',neuralOk:true,macBridge:true,perf:{}};
  `, context);
  for (const [from, to] of [
    ['  function _ttsSpeakChunkNeural(', '  // One-time iOS-Safari'],
    ['  // Built-in voice bridge for cold', '  // Speak a captured string directly'],
  ]) {
    const start = source.indexOf(from);
    assert.ok(start > 0, from);
    vm.runInContext(source.slice(start, source.indexOf(to, start)), context);
  }
  const run = js => vm.runInContext(js, context);
  const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
  return {run, flush};
}

test('cold read: built-in voice speaks clip 0 while clip 1 is fetched at fg', async () => {
  const h = harness();
  h.run('_ttsSpeakNextChunk()');
  await h.flush();
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(spoken)')), ['head']);
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(fetches)')), [['rest', 'Kokoro: af_nova', 'fg']]);
  assert.ok(h.run('logs.includes("BRIDGE")'));
  h.run('done(false)');
  await h.flush();
  assert.equal(h.run('_ttsAudio.src'), 'fg');
  assert.equal(h.run('fetches.length'), 1, 'clip 1 is not requested twice');
  assert.equal(h.run('spoken.length'), 1);
});

test('bridge is off for continuous (iPhone) reads and when opted out', () => {
  assert.equal(harness({continuous: true}).run("_ttsMacBridgeOk([{},{}])"), false);
  assert.equal(harness({pref: '0'}).run("_ttsMacBridgeOk([{},{}])"), false);
  assert.equal(harness().run("_ttsMacBridgeOk([{}])"), false);
  assert.equal(harness().run("_ttsMacBridgeOk([{},{}])"), true);
});

test('bridge makes clip 1 in the voice prepare-ahead chose for the message', async () => {
  const h = harness();
  h.run("_ttsHeadCache.set('head',{voice:'Kokoro: bf_alice',p:new Promise(()=>{})})");
  h.run('_ttsSpeakNextChunk()');
  await h.flush();
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(fetches)')), [['rest', 'Kokoro: bf_alice', 'fg']]);
});
