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
        chunker += "const _TTS_SECOND_CHARS = 160;\n" + function_block(self.source, "_ttsRestChunks", "_ttsSetPlay")
        result = self.run_node(chunker + """
const text = 'a'.repeat(80) + 'b'.repeat(160) + 'c'.repeat(400) + 'd'.repeat(400) + 'e'.repeat(400) + 'f'.repeat(400);
const _ttsHeadCache = new Map();
const _ttsChunkState = null;
const _ttsDebugJobs = [];
const _ttsCacheDebug = true;
const _ttsHeadChunk = s => _chunkTtsText(s, 80)[0];
_ttsHeadCache.set('a'.repeat(80), {debugReady:false});
_ttsHeadCache.set('b'.repeat(160), {debugReady:false});
for (const letter of 'cde') _ttsHeadCache.set(letter.repeat(400), {debugReady:false});
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
        self.assertAlmostEqual(result["sentOnly"]["sentFraction"], 1440 / 1840)
        self.assertTrue(result["sentOnly"]["pending"])
        self.assertAlmostEqual(result["partial"]["fraction"], 80 / 1840)
        self.assertTrue(result["partial"]["pending"])
        self.assertAlmostEqual(result["partial"]["sentFraction"], 1440 / 1840)
        self.assertAlmostEqual(result["capped"]["fraction"], 1440 / 1840)
        self.assertFalse(result["capped"]["pending"])
        self.assertAlmostEqual(result["capped"]["sentFraction"], 1440 / 1840)
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
        chunker += "const _TTS_SECOND_CHARS = 160;\n" + function_block(self.source, "_ttsRestChunks", "_ttsSetPlay")
        result = self.run_node(chunker + """
const text = 'a'.repeat(80) + 'b'.repeat(160);
const _ttsHeadCache = new Map([['a'.repeat(80), {debugReady:true}]]);
const _ttsChunkState = null;
const _ttsDebugJobs = [{text:'b'.repeat(160),status:'cancelled'}];
let _ttsCacheDebug = true;
const _ttsHeadChunk = s => _chunkTtsText(s, 80)[0];
""" + coverage + """
const cancelled = _ttsTextCacheCoverage(text);
_ttsDebugJobs.push({text:'b'.repeat(160),status:'queued'});
const requeued = _ttsTextCacheCoverage(text);
_ttsDebugJobs.push({text:'b'.repeat(160),status:'cancelled'});
_ttsHeadCache.set('b'.repeat(160), {debugReady:true});
const ready = _ttsTextCacheCoverage(text);
_ttsHeadCache.delete('b'.repeat(160));
_ttsCacheDebug = false;
const disabled = _ttsTextCacheCoverage(text);
console.log(JSON.stringify({cancelled,requeued,ready,disabled}));
""")
        self.assertAlmostEqual(result["cancelled"]["cancelledFraction"], 160 / 240)
        self.assertEqual(result["requeued"]["cancelledFraction"], 0)
        self.assertEqual(result["ready"]["fraction"], 1)
        self.assertEqual(result["ready"]["cancelledFraction"], 0)
        self.assertEqual(result["disabled"]["cancelledFraction"], 0)

    def test_second_clip_is_short_and_prepare_ahead_runs_during_another_read(self):
        # CCC-144: a cold read's second clip is ~160 chars so it is made while
        # the head plays; the focused pane's latest reply keeps preparing
        # (head + one clip) while ANOTHER message is read, never the one read.
        chunker = function_block(self.source, "_chunkTtsText", "_ttsBindChunkCompletion")
        rest = function_block(self.source, "_ttsRestChunks", "_ttsSetPlay")
        during = function_block(self.source, "_ttsReadingOther", "_ttsStartChain")
        result = self.run_node(chunker + "const _TTS_SECOND_CHARS = 160;\n" + rest + """
const _TTS_PREFETCH_CLIPS = 4, _TTS_PREFETCH_CLIPS_QUIET = 1;
let _ttsActive = false, _ttsPaused = false, _ttsChunkState = null, _ttsBoundUtteranceText = '';
""" + during + """
const words = Array.from({length: 200}, (_, i) => 'word' + i).join(' ');
const chunks = _ttsRestChunks('  ' + words, 80);
const idle = _ttsPrefetchClipCount('pane-1');
_ttsActive = true; _ttsChunkState = {}; _ttsBoundUtteranceText = 'Reading the older answer now.';
const reading = _ttsPrefetchClipCount('pane-1');
console.log(JSON.stringify({
  lens: chunks.map(c => c.text.length), start0: chunks[0].start,
  joined: chunks.map(c => c.text).join(' ') === words,
  idle, reading,
  other: _ttsReadingOther('The latest reply head'),
  same: _ttsReadingOther('the older answer'),
}));
""")
        self.assertLessEqual(result["lens"][0], 160)
        self.assertGreater(result["lens"][0], 100)
        self.assertTrue(all(n <= 400 for n in result["lens"][1:]))
        self.assertGreater(max(result["lens"][1:]), 300)
        self.assertEqual(result["start0"], 82)
        self.assertTrue(result["joined"])
        self.assertEqual(result["idle"], 4)
        self.assertEqual(result["reading"], 1)
        self.assertTrue(result["other"])
        self.assertFalse(result["same"])

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
const _ttsContinuousOk = () => false, _ttsCanStream = () => false, _ttsEventLagMs = () => 0;
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
let _ttsForceBridge = false;
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

    def test_offscreen_sessions_skip_prefetch_and_resume_when_visible_again(self):
        screen_helpers = self.source[
            self.source.index("  const _TTS_OFFSCREEN_BODY_CLASSES"):
            self.source.index("  let _ttsHoverBtn")
        ]
        viewport_scheduler = function_block(
            self.source, "scheduleTtsViewportPrefetch", "scheduleTtsPrefetch"
        )
        scheduler = function_block(
            self.source, "scheduleTtsPrefetch", "_ttsShowVoice"
        )
        result = self.run_node(
            """
let now = 0, nextTimer = 0;
let mobile = false, convId = 'conv-1', atBottom = true;
let viewRect = { left: 80, right: 1180, top: 40, bottom: 860, width: 1100, height: 820 };
let msgRect = { left: 100, right: 900, top: 500, bottom: 760, width: 800, height: 260 };
let displayStyle = 'block';
let hitExternalOverlay = false;
const innerWidth = 1200, innerHeight = 900;
const bodyClasses = new Set();
const timers = new Map(), requests = [];
let _ttsPrefetchTimer = null, _ttsPrefetchTarget = null;
const _ttsChainTokens = { p1: 1 };
const _ttsDebugJobs = [{ owner: 'p1', status: 'queued', text: 'queued clip' }];
const _ttsActive = false, _ttsPaused = false;
const isMobile = () => mobile;
const activePaneId = () => 'p1';
const paneByPaneId = () => ({ conversationId: convId });
const isConversationAtBottom = () => atBottom;
const getComputedStyle = () => ({ display: displayStyle, visibility: 'visible', opacity: '1' });
const _ttsMarkCache = () => {}, _ttsLog = () => {}, _ttsScheduleCacheDebug = () => {};
const _ttsSpeakButtonData = b => ({ text: b.text });
const _ttsPrefetchHead = (text, paneId, kind) => requests.push({ at: now, text, paneId, kind: kind || 'tail' });
const setTimeout = (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, at: now + delay }); return id; };
const clearTimeout = id => timers.delete(id);
function tick(delta) {
  const target = now + delta;
  for (;;) {
    const next = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
    if (!next || next[1].at > target) break;
    now = next[1].at;
    timers.delete(next[0]);
    next[1].fn();
  }
  now = target;
}
const msgEl = {
  nodeType: 1,
  closest: () => msgEl,
  getBoundingClientRect: () => msgRect,
};
const pane = {
  dataset: { paneId: 'p1' },
  contains: el => el === pane || el === view || el === msgEl,
};
const overlayModal = { contains: () => false };
const view = {
  isConnected: true,
  hidden: false,
  _convSpeakButton: { _speakTarget: null },
  closest: sel => sel === '.conv-pane' ? pane : null,
  contains: el => el === view || el === msgEl,
  getBoundingClientRect: () => viewRect,
};
pane.querySelector = sel => sel === '.conversations-view' ? view : null;
const getConvViewForPane = id => id === 'p1' ? view : null;
const lastMessageTtsData = () => ({
  text: 'Latest visible session reply.',
  mapping: [{ node: msgEl, start: 0, end: 29, nodeOffsetStart: 0 }],
});
const document = {
  hidden: false,
  body: {
    classList: {
      contains: c => bodyClasses.has(c),
    },
  },
  querySelectorAll: sel => sel === '.conv-pane' ? [pane] : sel === '.conversations-view' ? [view] : [],
  elementFromPoint: () => hitExternalOverlay ? overlayModal : msgEl,
};
"""
            + screen_helpers
            + viewport_scheduler
            + scheduler
            + """
const suppressed = {};
for (const cls of ['ccc-core-app-open', 'ccc-mobile-app-open', 'ccc-simple-home-open', 'ccc-simple-screen-open', 'mobile-ask-open']) {
  bodyClasses.add(cls);
  scheduleTtsPrefetch();
  tick(3000);
  suppressed[cls] = requests.length;
  bodyClasses.delete(cls);
}
// Mobile sidebar open (.main off-canvas without mobile-show-main)
mobile = true;
scheduleTtsPrefetch();
tick(3000);
suppressed.mobileSidebar = requests.length;
mobile = false;

// Translated off-canvas geometry
const savedViewRect = viewRect;
viewRect = { left: 1200, right: 2300, top: 40, bottom: 860, width: 1100, height: 820 };
scheduleTtsPrefetch();
tick(3000);
suppressed.offCanvasRect = requests.length;
viewRect = savedViewRect;

// Zero-sized / display:none pane
displayStyle = 'none';
scheduleTtsPrefetch();
tick(3000);
suppressed.displayNone = requests.length;
displayStyle = 'block';

// Empty pane with no selected conversation
convId = '';
scheduleTtsPrefetch();
tick(3000);
suppressed.emptyPane = requests.length;
convId = 'conv-1';

// Full-screen modal covering the session pane
hitExternalOverlay = true;
scheduleTtsPrefetch();
tick(3000);
suppressed.modalOccluded = requests.length;
hitExternalOverlay = false;

// Scrolled up into tool-only output with no visible speak button: must NOT fall through to bottom message
atBottom = false;
view._convSpeakButton._speakTarget = null;
scheduleTtsPrefetch();
tick(3000);
suppressed.scrolledUpNoTarget = requests.length;
atBottom = true;

// Bottom-pinned, but the last speakable message scrolled completely above view.top
const savedMsgRect = msgRect;
msgRect = { left: 100, right: 900, top: -400, bottom: -20, width: 800, height: 380 };
scheduleTtsPrefetch();
tick(3000);
suppressed.messageScrolledAboveViewport = requests.length;
msgRect = savedMsgRect;

// Overlay transition: on-screen -> Queues overlay cancels pending timer & queued job -> returning to Sessions resumes prefetch
_ttsSyncScreenVisibility(0);
scheduleTtsPrefetch();
tick(1000);
bodyClasses.add('ccc-core-app-open');
_ttsSyncScreenVisibility(0);
const jobAfterHide = _ttsDebugJobs[0].status;
const tokenAfterHide = _ttsChainTokens.p1;
tick(3000);
suppressed.cancelledDuringOverlay = requests.length;

const resumedAt = now;
bodyClasses.delete('ccc-core-app-open');
_ttsSyncScreenVisibility(0);
tick(2500);

console.log(JSON.stringify({ suppressed, jobAfterHide, tokenAfterHide, resumedAt, requests }));
"""
        )
        for reason, count in result["suppressed"].items():
            self.assertEqual(count, 0, f"expected zero prefetches for {reason}")
        self.assertEqual(result["jobAfterHide"], "cancelled")
        self.assertEqual(result["tokenAfterHide"], 2)
        self.assertEqual(
            result["requests"],
            [{"at": result["resumedAt"] + 2500, "text": "Latest visible session reply.", "paneId": "p1", "kind": "tail"}],
        )

    def test_prefetch_continuation_stops_when_session_leaves_screen(self):
        chunker = function_block(self.source, "_chunkTtsText", "_ttsBindChunkCompletion")
        rest_chunker = "const _TTS_SECOND_CHARS = 160;\n" + function_block(
            self.source, "_ttsRestChunks", "_ttsSetPlay"
        )
        chain_block = self.source[
            self.source.index("  function _ttsReadingOther("):
            self.source.index("  // A prefetch skipped while the tab or session pane was off screen")
        ]
        result = self.run_node(
            chunker
            + rest_chunker
            + """
const _TTS_PREFETCH_CLIPS = 4, _TTS_PREFETCH_CLIPS_QUIET = 1;
let onScreenState = true, _ttsActive = false, _ttsPaused = false;
let _ttsChunkState = null, _ttsBoundUtteranceText = '', _ttsHoverBtn = null;
const _ttsNeuralCooldownUntil = 0;
const _ttsChainTokens = {};
let _ttsChainQueue = Promise.resolve();
const _ttsHeadCache = new Map();
const _ttsDebugJobs = [];
const _ttsPrefetchTargets = new Map();
const retryTimers = [];
const fetched = [];
const document = { hidden: false };
const activePaneId = () => 'p1';
const _ttsIsPaneOnScreen = () => onScreenState;
const _ttsDebugJob = (text, status, owner) => {
  const j = { text, status, owner };
  _ttsDebugJobs.push(j);
  return j;
};
const _ttsScheduleCacheDebug = () => {}, _ttsWatchCacheEntry = () => {}, _ttsEvictCache = () => {};
const _ttsMarkCache = () => {}, _ttsLog = () => {}, scheduleTtsPrefetch = () => {};
const setTimeout = (fn, delay) => { retryTimers.push(delay); return retryTimers.length; };
const _ttsFetchNeural = (text) => {
  fetched.push(text);
  // Session goes off screen while the first continuation clip is in flight.
  onScreenState = false;
  return Promise.resolve({ voice: 'Kokoro:af_heart', done: Promise.resolve() });
};
"""
            + chain_block
            + """
(async () => {
  const words = Array.from({ length: 260 }, (_, i) => 'word' + i).join(' ');
  const head = { text: words.slice(0, 60), start: 0 };
  _ttsPrefetchTargets.set('p1', head.text);
  _ttsStartChain(words, head, { voice: 'Kokoro:af_heart', done: Promise.resolve() }, 'p1');
  await _ttsChainQueue;
  console.log(JSON.stringify({
    fetchedCount: fetched.length,
    statuses: _ttsDebugJobs.map(j => j.status),
    retryTimers,
  }));
})();
"""
        )
        self.assertEqual(result["fetchedCount"], 1)
        self.assertEqual(result["statuses"], ["ready", "cancelled", "cancelled", "cancelled"])
        self.assertEqual(result["retryTimers"], [])


if __name__ == "__main__":
    unittest.main()
