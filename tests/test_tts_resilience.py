import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "app.js"


def function_block(source, name, next_name):
    start = source.index(f"  function {name}(")
    end = source.index(f"  function {next_name}(", start)
    return source[start:end]


class TtsResilienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")

    def run_node(self, javascript):
        result = subprocess.run(
            ["node", "-e", javascript],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_sanitizer_strips_markup_and_angle_bracket_tokens(self):
        sanitizer = function_block(
            self.source, "_sanitizeTtsText", "_chunkTtsText"
        )
        result = self.run_node(
            sanitizer
            + """
const input = "**Run** `gh repo view <repo> --json visibility` "
  + "[docs](https://example.test/docs)";
console.log(JSON.stringify(_sanitizeTtsText(input)));
"""
        )
        self.assertEqual(
            result,
            "Run gh repo view --json visibility docs",
        )

    def test_chunker_bounds_utterances_without_losing_text(self):
        chunker = function_block(
            self.source, "_chunkTtsText", "_ttsBindChunkCompletion"
        )
        result = self.run_node(
            chunker
            + """
const input = "First sentence. Second sentence. Third sentence.";
const chunks = _chunkTtsText(input, 24);
console.log(JSON.stringify(chunks));
"""
        )
        self.assertGreater(len(result), 1)
        self.assertTrue(all(len(chunk["text"]) <= 24 for chunk in result))
        self.assertEqual(
            " ".join(chunk["text"] for chunk in result),
            "First sentence. Second sentence. Third sentence.",
        )

    def test_chunk_error_settles_as_skipped_once(self):
        binder = function_block(
            self.source, "_ttsBindChunkCompletion", "_ttsStartChunkedSpeech"
        )
        result = self.run_node(
            "const window = { addEventListener: () => {} };\n"
            + binder
            + """
const utterance = {};
const settled = [];
_ttsBindChunkCompletion(utterance, skipped => settled.push(skipped));
utterance.onerror({ error: "synthesis-failed" });
utterance.onend();
console.log(JSON.stringify(settled));
"""
        )
        self.assertEqual(result, [True])

    def test_live_prefetch_waits_for_changed_text_without_poll_starvation(self):
        scheduler = function_block(
            self.source, "scheduleTtsPrefetch", "_ttsShowVoice"
        )
        result = self.run_node(
            """
let now = 0, nextTimer = 0, text = 'A settled reply.';
let _ttsPrefetchTimer = null, _ttsPrefetchTarget = null;
const timers = new Map(), requests = [];
const activePaneId = () => 'p1';
const lastMessageTtsData = () => ({ text });
const document = { hidden: false, querySelectorAll: () => [] };
const _ttsActive = false, _ttsPaused = false;
const _ttsMarkCache = () => {}, _ttsLog = () => {};
const _ttsPrefetchHead = (text, paneId) => requests.push({ at: now, text, paneId });
const setTimeout = (fn, delay) => {
  const id = ++nextTimer;
  timers.set(id, { fn, at: now + delay });
  return id;
};
const clearTimeout = id => timers.delete(id);
function tick(to) {
  for (;;) {
    const next = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
    if (!next || next[1].at > to) break;
    now = next[1].at;
    timers.delete(next[0]);
    next[1].fn();
  }
  now = to;
}
"""
            + scheduler
            + """
scheduleTtsPrefetch();
tick(900); scheduleTtsPrefetch();
tick(1800); scheduleTtsPrefetch();
tick(2500);
const stable = requests.splice(0);
text = 'A reply still being written.';
scheduleTtsPrefetch();
tick(3400); text += ' Now complete.'; scheduleTtsPrefetch();
tick(4300); scheduleTtsPrefetch();
tick(5899);
const beforeQuiet = requests.length;
tick(5900);
console.log(JSON.stringify({ stable, beforeQuiet, changed: requests }));
"""
        )
        self.assertEqual(result["stable"], [
            {"at": 2500, "text": "A settled reply.", "paneId": "p1"}
        ])
        self.assertEqual(result["beforeQuiet"], 0)
        self.assertEqual(result["changed"], [
            {"at": 5900, "text": "A reply still being written. Now complete.", "paneId": "p1"}
        ])

    def test_viewport_prefetch_tracks_stable_target_and_waits_for_playback(self):
        scheduler = function_block(self.source, "scheduleTtsViewportPrefetch", "scheduleTtsPrefetch")
        result = self.run_node("""
let now = 0, nextTimer = 0, _ttsActive = false, _ttsPaused = false;
const timers = new Map(), requests = [];
let _ttsPrefetchTimer = null;
const document = { hidden: false };
const activePaneId = () => 'p1';
const isConversationAtBottom = v => v.bottom;
const _ttsSpeakButtonData = b => ({ text: b.text });
const _ttsLog = () => {};
const _ttsPrefetchHead = (text, pane, kind, btn) => requests.push({text, at: now, kind});
const setTimeout = (fn, delay) => { const id = ++nextTimer; timers.set(id, {fn, at: now + delay}); return id; };
const clearTimeout = id => timers.delete(id);
function tick(to) {
  for (;;) {
    const next = [...timers].sort((a,b) => a[1].at - b[1].at)[0];
    if (!next || next[1].at > to) break;
    now = next[1].at; timers.delete(next[0]); next[1].fn();
  }
  now = to;
}
const pane = { dataset: { paneId: 'p1' } };
const first = { isConnected: true, text: 'First older answer.' };
const second = { isConnected: true, text: 'Second older answer.' };
const view = { closest: () => pane, _convSpeakButton: { _speakTarget: first }, bottom: false };
""" + scheduler + """
scheduleTtsViewportPrefetch(view, first);
tick(100); scheduleTtsViewportPrefetch(view, first);
tick(200);
view._convSpeakButton._speakTarget = second;
_ttsActive = true;
scheduleTtsViewportPrefetch(view, second);
tick(400);
const whilePlaying = requests.length;
_ttsActive = false;
tick(1600);
view._convSpeakButton._speakTarget = first;
scheduleTtsViewportPrefetch(view, first);
tick(1650);
view.bottom = true;
scheduleTtsViewportPrefetch(view, first);
tick(2000);
console.log(JSON.stringify({ requests, whilePlaying }));
""")
        self.assertEqual(result["whilePlaying"], 1)
        self.assertEqual(result["requests"], [
            {"text": "First older answer.", "at": 200, "kind": "viewport"},
            {"text": "Second older answer.", "at": 1600, "kind": "viewport"},
        ])

    def test_cache_coverage_uses_actual_ready_characters_including_uncached_tail(self):
        coverage = function_block(self.source, "_ttsTextCacheCoverage", "_ttsRefreshCacheCoverage")
        chunker = function_block(self.source, "_chunkTtsText", "_ttsBindChunkCompletion")
        result = self.run_node(chunker + """
const text = 'a'.repeat(80) + 'b'.repeat(400) + 'c'.repeat(400) + 'd'.repeat(400) + 'e'.repeat(400) + 'f'.repeat(400);
const _ttsHeadCache = new Map();
const _ttsChunkState = null;
const _ttsDebugJobs = [];
const _ttsCacheDebug = true;
const _ttsHeadChunk = s => _chunkTtsText(s, 80)[0];
_ttsHeadCache.set('a'.repeat(80), {debugReady:false});
for (const letter of 'bcde') _ttsHeadCache.set(letter.repeat(400), {debugReady:false});
""" + coverage + """
const sentOnly = _ttsTextCacheCoverage(text);
_ttsHeadCache.get('a'.repeat(80)).debugReady = true;
const partial = _ttsTextCacheCoverage(text);
for (const entry of _ttsHeadCache.values()) entry.debugReady = true;
const capped = _ttsTextCacheCoverage(text);
_ttsHeadCache.set('f'.repeat(400), {debugReady:true});
const ready = _ttsTextCacheCoverage(text);
console.log(JSON.stringify({sentOnly, partial, capped, ready}));
""")
        self.assertEqual(result["sentOnly"]["fraction"], 0)
        self.assertAlmostEqual(result["sentOnly"]["sentFraction"], 1680 / 2080)
        self.assertTrue(result["sentOnly"]["pending"])
        self.assertAlmostEqual(result["partial"]["fraction"], 80 / 2080)
        self.assertTrue(result["partial"]["pending"])
        self.assertAlmostEqual(result["partial"]["sentFraction"], 1680 / 2080)
        self.assertAlmostEqual(result["capped"]["fraction"], 1680 / 2080)
        self.assertFalse(result["capped"]["pending"])
        self.assertAlmostEqual(result["capped"]["sentFraction"], 1680 / 2080)
        self.assertEqual(result["ready"], {"fraction": 1, "sentFraction": 1, "cancelledFraction": 0, "pending": False})

    def test_priority_change_cancels_waiting_work_but_keeps_sent_and_ready(self):
        select = function_block(self.source, "_ttsSelectPrefetchTarget", "_ttsRenderDebugQueue")
        result = self.run_node("""
const _ttsPrefetchTargets = new Map([['pane', 'old']]);
const _ttsChainTokens = {pane: 3};
const _ttsDebugJobs = [
  {owner:'pane', status:'queued'}, {owner:'pane', status:'processing'},
  {owner:'pane', status:'ready'}, {owner:'other', status:'queued'}
];
const _ttsScheduleCacheDebug = () => {};
""" + select + """
_ttsSelectPrefetchTarget('pane', 'new');
_ttsSelectPrefetchTarget('pane', 'new');
console.log(JSON.stringify({statuses:_ttsDebugJobs.map(j => j.status), token:_ttsChainTokens.pane}));
""")
        self.assertEqual(result, {"statuses": ["cancelled", "processing", "ready", "queued"], "token": 4})

    def test_superseded_head_completion_does_not_restart_old_chain(self):
        start = self.source.index("  function _ttsStartChain(")
        end = self.source.index("  const _TTS_PREFETCH_WIDTH", start)
        result = self.run_node("""
const _ttsPrefetchTargets = new Map([['pane', 'new head']]);
""" + self.source[start:end] + """
// No scheduler/cache stubs: reaching any scheduling code would fail.
_ttsStartChain('old head and more text', {text:'old head',start:0}, {voice:'voice'}, 'pane');
console.log(JSON.stringify({skipped:true}));
""")
        self.assertTrue(result["skipped"])

    def test_cancelled_coverage_yields_to_new_requests_and_ready_cache(self):
        coverage = function_block(self.source, "_ttsTextCacheCoverage", "_ttsRefreshCacheCoverage")
        chunker = function_block(self.source, "_chunkTtsText", "_ttsBindChunkCompletion")
        result = self.run_node(chunker + """
const text = 'a'.repeat(80) + 'b'.repeat(400);
const _ttsHeadCache = new Map([['a'.repeat(80), {debugReady:true}]]);
const _ttsChunkState = null;
const _ttsDebugJobs = [{text:'b'.repeat(400),status:'cancelled'}];
let _ttsCacheDebug = true;
const _ttsHeadChunk = s => _chunkTtsText(s, 80)[0];
""" + coverage + """
const cancelled = _ttsTextCacheCoverage(text);
_ttsDebugJobs.push({text:'b'.repeat(400),status:'queued'});
const requeued = _ttsTextCacheCoverage(text);
_ttsDebugJobs.push({text:'b'.repeat(400),status:'cancelled'});
_ttsHeadCache.set('b'.repeat(400), {debugReady:true});
const ready = _ttsTextCacheCoverage(text);
_ttsHeadCache.delete('b'.repeat(400));
_ttsCacheDebug = false;
const disabled = _ttsTextCacheCoverage(text);
console.log(JSON.stringify({cancelled,requeued,ready,disabled}));
""")
        self.assertAlmostEqual(result["cancelled"]["cancelledFraction"], 400 / 480)
        self.assertEqual(result["requeued"]["cancelledFraction"], 0)
        self.assertEqual(result["ready"]["fraction"], 1)
        self.assertEqual(result["ready"]["cancelledFraction"], 0)
        self.assertEqual(result["disabled"]["cancelledFraction"], 0)

    def test_neural_word_progress_uses_audio_time_and_whole_utf16_words(self):
        helper = function_block(self.source, "_ttsWordAtProgress", "_ttsSpeakChunkNeural")
        result = self.run_node(helper + """
const text = '  Hello,  🌍 world!';
console.log(JSON.stringify({
  first:_ttsWordAtProgress(text, 0, 10),
  whitespace:_ttsWordAtProgress(text, 4.5, 10),
  unicode:_ttsWordAtProgress(text, 6, 10),
  last:_ttsWordAtProgress(text, 10, 10),
  streaming:_ttsWordAtProgress(text, 0.9, Infinity),
  empty:_ttsWordAtProgress('   ', 2, 10)
}));
""")
        self.assertEqual(result["first"], {"start": 2, "length": 6})
        self.assertEqual(result["whitespace"], {"start": 2, "length": 6})
        self.assertEqual(result["unicode"], {"start": 10, "length": 2})
        self.assertEqual(result["last"], {"start": 13, "length": 6})
        self.assertEqual(result["streaming"], {"start": 13, "length": 6})
        self.assertIsNone(result["empty"])

    def test_prepared_clips_survive_playback_and_replay_without_fetching(self):
        start = self.source.index("  function _ttsSpeakChunkNeural(")
        end = self.source.index("  // One-time iOS-Safari", start)
        result = self.run_node("""
let _ttsNeuralToken = 0, _ttsUtterance, _ttsChunkState, _ttsNeural, _ttsAudio;
let _ttsRate = 1.25, _ttsActivePaneId = 'p1', _ttsLastCharIndex;
let fetches = 0, plays = 0, urls = 0, blockNext = false, _ttsPaused = false;
const _ttsDirectBtn = {};
const _setTtsDirectBtnState = (btn, state) => {btn.state = state;};
const showOpToast = () => {};
const revoked = [], audios = [], _ttsHeadCache = new Map();
const window = {};
const URL = {createObjectURL: () => 'play-' + (++urls), revokeObjectURL: u => revoked.push(u)};
const _ttsScheduleCacheDebug = () => {}, _ttsSetPlay = () => {}, _ttsSetStreaming = () => {};
const clearTtsHighlight = () => {}, clearTtsCaption = () => {};
const _ttsFetchNeural = () => { fetches++; throw Error('Unexpected cache miss'); };
const setTtsButtonsState = () => {}, setTtsButtonsBusy = () => {};
const ttsButtons = () => [], _ttsShowVoice = () => {}, _ttsLog = () => {};
const _ttsPerfStart = () => {}, _ttsPerfSilence = () => {};
function Audio(url) {
  this.url = url; this.addEventListener = () => {};
  this.play = () => {
    if (blockNext) { blockNext=false; return Promise.reject({name:'NotAllowedError'}); }
    plays++; return Promise.resolve();
  }; audios.push(this);
}
for (const text of ['Prepared head.', 'Prepared continuation.']) {
  const got = {url: 'cache-' + text, blob: {}, voice: 'Kokoro:test', done: Promise.resolve()};
  _ttsHeadCache.set(text, {p: Promise.resolve(got), got, voice: got.voice, debugReady: true, ready: true});
}
function _ttsSpeakNextChunk() {
  if (_ttsChunkState.index < _ttsChunkState.chunks.length)
    _ttsSpeakChunkNeural(_ttsChunkState, _ttsChunkState.chunks[_ttsChunkState.index]);
}
const drain = async () => { for(let i=0; i<8; i++) await Promise.resolve(); };
""" + self.source[start:end] + """
(async () => {
  for (let replay=0; replay<2; replay++) {
    _ttsChunkState = {chunks: [{text:'Prepared head.',start:0},{text:'Prepared continuation.',start:15}],index:0,paneId:'p1'};
    _ttsSpeakNextChunk(); await drain();
    audios[audios.length-1].onended(); await drain();
    audios[audios.length-1].onended(); await drain();
  }
  const completed = {fetches, plays, cacheSize: _ttsHeadCache.size, players: audios.length, revoked};
  blockNext = true;
  _ttsChunkState = {chunks:[{text:'Prepared head.',start:0},{text:'Prepared continuation.',start:15}],index:1,paneId:'p1'};
  _ttsSpeakNextChunk(); await drain();
  console.log(JSON.stringify({...completed, blocked:{paused:_ttsPaused,index:_ttsChunkState.index,button:_ttsDirectBtn.state,cacheSize:_ttsHeadCache.size,fetches}}));
})();
""")
        self.assertEqual(result["fetches"], 0)
        self.assertEqual(result["plays"], 4)
        self.assertEqual(result["cacheSize"], 2)
        self.assertEqual(result["players"], 1)
        self.assertEqual(result["revoked"], ["play-1", "play-2", "play-3", "play-4"])
        self.assertEqual(result["blocked"], {"paused":True,"index":1,"button":"paused","cacheSize":2,"fetches":0})

    def test_floating_speaker_targets_text_near_circle_instead_of_larger_footer(self):
        selector = function_block(self.source, "_visibleSpeakButton", "attachConversationEndAffordance")
        result = self.run_node("""
const rect = (top, bottom) => ({top, bottom, height: bottom-top, width:200});
let circle = rect(550, 610);
function answer(id, box, spoken) {
  const node = {id, elements:[{getBoundingClientRect: () => box}], spoken};
  return {id, closest: s => s === '.event.result' ? null : node};
}
const old = answer('older', rect(0, 500), [rect(20, 80)]);
const current = answer('current', rect(550, 630), [rect(550, 610)]);
const assistantTextElements = node => node.elements;
const buildTtsDataFromElements = elements => {
  const node = elements === old.closest('').elements ? old.closest('') : current.closest('');
  return {mapping: node.spoken.map(r => ({node:{nodeValue:'Spoken answer.', rect:r},nodeOffsetStart:0,start:0,end:14}))};
};
function Range() { this.setStart = node => {this.node=node}; this.setEnd=()=>{}; this.getClientRects=()=>[this.node.rect]; }
const view = {getBoundingClientRect:()=>rect(0,700), _convSpeakButton:{getBoundingClientRect:()=>circle}, querySelectorAll:()=>[old,current]};
""" + selector + """
const below = _visibleSpeakButton(view).id;
circle = rect(20,80);
const above = _visibleSpeakButton(view).id;
old.closest('').spoken = []; // Only the excluded status footer remains visible.
const footerExcluded = _visibleSpeakButton(view).id;
console.log(JSON.stringify({below,above,footerExcluded}));
""")
        self.assertEqual(result, {"below":"current", "above":"older", "footerExcluded":"current"})

    def test_failed_chunk_advances_queue_and_warns_non_blockingly(self):
        chunker = function_block(
            self.source, "_chunkTtsText", "_ttsBindChunkCompletion"
        )
        binder = function_block(
            self.source, "_ttsBindChunkCompletion", "_ttsStartChunkedSpeech"
        )
        start = self.source.index("  function _ttsStartChunkedSpeech(")
        end = self.source.index("  function speakTextDirect(", start)
        queue_source = self.source[start:end]
        hebrew_detector = self.source[
            self.source.index("  function _ttsIsHebrew("):
            self.source.index("  const _ttsHeadCache")
        ]
        result = self.run_node(
            """
const TTS_CHUNK_MAX_CHARS = 12;
let _ttsChunkState = null;
let _ttsUtterance = null;
let _ttsRate = 1;
let _ttsActivePaneId = null;
let _ttsLastCharIndex = 0;
let _ttsBoundUtteranceText = "";
const utterances = [];
const notices = [];
const activePaneId = () => "p1";
const setTtsButtonsState = () => {};
const setTtsButtonsBusy = () => {};
const ttsButtons = () => [];
const ttsButtonPaneId = () => "";
const highlightTtsWord = () => {};
const updateTtsCaption = () => {};
const stopTextToSpeech = () => {};
const showOpToast = (message, kind) => notices.push({ message, kind });
// Free (neural) voice stubs: held in cooldown so this exercises the Mac-voice
// fallback path that the chunk-skip behaviour belongs to.
let _ttsNeuralCooldownUntil = Infinity;
const _ttsNeuralCooldownWhy = "test";
const _ttsHeadChunk = () => null;
const _ttsHeadCache = new Map();
const _ttsCacheState = new Map();
const _perfEvent = () => {};
const _perfActiveElapsed = (a, b) => b - a;
const _TTS_HEAD_CHARS = 160;
let _ttsStartWhy = "";
let _ttsKeepVoice = null;
let _ttsDirectBtn = null;
const _ttsLog = () => {};
const _ttsShowVoice = () => {};
const _ttsNeuralUsable = () => false;
const _ttsSpeakChunkNeural = () => {};
const _ttsEnginePause = () => {};
const window = {
  addEventListener: () => {},
  speechSynthesis: {
    speak: utterance => utterances.push(utterance),
    pause: () => {},
  },
};
function SpeechSynthesisUtterance(text) { this.text = text; }
"""
            + hebrew_detector
            + chunker
            + binder
            + queue_source
            + """
_ttsBoundUtteranceText = "First chunk. Second chunk.";
_ttsStartChunkedSpeech(_ttsBoundUtteranceText, "p1", 0, false);
utterances[0].onerror({ error: "synthesis-failed" });
console.log(JSON.stringify({
  spoken: utterances.map(utterance => utterance.text),
  notices,
}));
"""
        )
        self.assertGreaterEqual(len(result["spoken"]), 2)
        self.assertEqual(len(result["notices"]), 1)
        self.assertEqual(result["notices"][0]["kind"], "info")
        self.assertIn("continuing", result["notices"][0]["message"].lower())


if __name__ == "__main__":
    unittest.main()
