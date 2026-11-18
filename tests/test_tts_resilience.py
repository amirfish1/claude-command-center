from tests.dashboard_source import dashboard_source
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
        cls.source = dashboard_source(APP_JS)

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

    def test_browser_word_progress_uses_audio_time_and_whole_utf16_words(self):
        start = self.source.index("  function _ttsWordAtProgress(")
        helper = self.source[start:self.source.index("\n  }\n", start) + 5]
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
const view = {getBoundingClientRect:()=>rect(0,700), _convSpeakButton:{closest:()=>null,getBoundingClientRect:()=>circle}, querySelectorAll:()=>[old,current]};
""" + selector + """
const below = _visibleSpeakButton(view).id;
circle = rect(20,80);
const above = _visibleSpeakButton(view).id;
view._convSpeakButton.closest = () => ({});
circle = rect(-100, -40); // Docked controls must use the transcript center.
const docked = _visibleSpeakButton(view).id;
view._convSpeakButton.closest = () => null;
old.closest('').spoken = []; // Only the excluded status footer remains visible.
const footerExcluded = _visibleSpeakButton(view).id;
console.log(JSON.stringify({below,above,docked,footerExcluded}));
""")
        self.assertEqual(result, {"below":"current", "above":"older", "docked":"current", "footerExcluded":"current"})

if __name__ == "__main__":
    unittest.main()
