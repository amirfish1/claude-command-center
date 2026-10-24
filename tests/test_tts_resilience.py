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
const document = { hidden: false };
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
