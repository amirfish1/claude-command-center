"""Regression coverage for stream-json to durable transcript handoff."""

from pathlib import Path
import subprocess
import textwrap


APP_JS = Path(__file__).resolve().parents[1] / "static" / "app.js"
PROJECT_ROOT = APP_JS.parents[1]


def _render_conversation_events_source():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("function renderConversationEvents(")
    end = source.index("\n  // CCC-185:", start)
    return source[start:end]


def test_matching_stream_bubble_handoff_skips_live_word_reveal():
    """Already-streamed Claude text must not animate a second time."""
    body = _render_conversation_events_source()

    assert "let handedOffStreamingBubble = false;" in body
    assert "$view._streamedAssistantMessageIds.delete(ev.message_id)" in body
    assert "handedOffStreamingBubble = true;" in body
    bubble_lookup = body.index("const liveBubble = $view.querySelector")
    bubble_handoff = body.index("handedOffStreamingBubble = true;", bubble_lookup)
    bubble_removal = body.index("liveBubble.parentNode.removeChild(liveBubble)")
    assert bubble_lookup < bubble_handoff < bubble_removal
    assert (
        "if (ev.type === 'assistant' && !handedOffStreamingBubble) "
        "_convLiveRevealNewText(div, paneId, opts);"
    ) in body


def test_result_cleanup_cannot_forget_that_message_text_already_streamed():
    """A result may remove the bubble before the durable poll catches up."""
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("function ensureStreamingBubble(")
    end = source.index("\n  function handleSpawnEvents(", start)
    ensure_body = source[start:end]

    assert "$view._streamedAssistantMessageIds = new Set();" in ensure_body
    assert "$view._streamedAssistantMessageIds.add(msgId);" in ensure_body
    assert "MAX_STREAM_HANDOFF_MARKERS" in ensure_body
    assert "$view._streamedAssistantMessageIds.values().next().value" in ensure_body
    assert "clearStreamingBubble" not in ensure_body


def test_stream_handoff_markers_are_cleared_when_a_pane_changes_conversations():
    """The reused pane must not retain orphaned ids from an older session."""
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("async function selectConversation(")
    end = source.index("\n  function updateSplitInputBar(", start)
    select_body = source[start:end]

    clear_marker = "if ($view._streamedAssistantMessageIds) $view._streamedAssistantMessageIds.clear();"
    assert "if (previousConvId !== id)" in select_body
    assert clear_marker in select_body
    assert select_body.index(clear_marker) < select_body.index("$view.innerHTML =")


def test_ordinary_assistant_events_keep_live_word_reveal():
    """The fix stays local to matching stream-bubble handoffs."""
    body = _render_conversation_events_source()

    assert "if (ev.type === 'assistant' && ev.message_id)" in body
    assert "const liveBubble = $view.querySelector" in body
    assert "if (liveBubble)" in body
    assert "_convLiveRevealNewText(div, paneId, opts);" in body


def test_result_before_durable_handoff_in_real_browser():
    """Chromium executes the real renderers through the problematic ordering."""
    node_program = textwrap.dedent(
        r"""
        const fs = require('fs');
        const puppeteer = require('./require-puppeteer.js');
        const { findChromePath } = require('./puppeteer-browser-config.js');

        (async () => {
          const indexHtml = fs.readFileSync('static/index.html', 'utf8');
          const appSource = fs.readFileSync('static/app.js', 'utf8');
          const marker = '\n})();\n\n/* === Hero: Fleet Pulse === */';
          if (!appSource.includes(marker)) throw new Error('main IIFE marker missing');
          const exposed = appSource.replace(
            marker,
            '\n  window.__handoffTest = {'
              + 'renderConversationEvents, ensureStreamingBubble, clearStreamingBubble,'
              + 'activePaneId, getConvViewForPane, getConvView};'
              + marker
          );

          const browser = await puppeteer.launch({
            executablePath: findChromePath(),
            args: ['--no-sandbox'],
          });
          try {
            const page = await browser.newPage();
            await page.setRequestInterception(true);
            page.on('request', request => {
              const url = new URL(request.url());
              if (request.isNavigationRequest()) {
                request.respond({status: 200, contentType: 'text/html', body: indexHtml});
              } else if (url.pathname === '/static/app.js') {
                request.respond({status: 200, contentType: 'application/javascript', body: exposed});
              } else if (url.pathname.startsWith('/static/')) {
                request.respond({status: 404, body: ''});
              } else {
                request.respond({
                  status: 200,
                  contentType: 'application/json',
                  body: JSON.stringify([]),
                });
              }
            });
            await page.goto('http://ccc-handoff.test/', {waitUntil: 'load', timeout: 30000});
            await page.waitForFunction(() => !!window.__handoffTest, {timeout: 30000});

            const result = await page.evaluate(() => {
              const h = window.__handoffTest;
              const paneId = h.activePaneId();
              const view = h.getConvViewForPane(paneId) || h.getConvView();
              if (!view) throw new Error('conversation view missing');
              view.replaceChildren();

              const slot = h.ensureStreamingBubble('msg-result-first', paneId);
              if (!slot) throw new Error('stream bubble missing');
              const streamed = document.createElement('div');
              streamed.className = 'stream-block-text assistant-text';
              streamed.textContent = 'Already streamed answer';
              slot.appendChild(streamed);

              // Reproduce the race: result cleanup wins before JSONL polling.
              h.clearStreamingBubble();
              const bubbleClearedBeforeDurable = !view.querySelector(
                '.stream-bubble[data-msg-id="msg-result-first"]'
              );
              h.renderConversationEvents([{
                type: 'assistant',
                message_id: 'msg-result-first',
                line: 910001,
                ts: '2026-08-13T12:00:00Z',
                blocks: [{kind: 'text', text: 'Already streamed answer plus final tail'}],
              }], paneId, {});
              const handed = view.querySelector(
                '.event.assistant[data-msg-id="msg-result-first"]'
              );

              h.renderConversationEvents([{
                type: 'assistant',
                message_id: 'msg-ordinary',
                line: 910002,
                ts: '2026-08-13T12:00:01Z',
                blocks: [{kind: 'text', text: 'Ordinary durable reply'}],
              }], paneId, {});
              const ordinary = view.querySelector(
                '.event.assistant[data-msg-id="msg-ordinary"]'
              );

              return {
                bubbleClearedBeforeDurable,
                durablePresent: !!handed,
                durableText: handed?.querySelector('.assistant-text')?.textContent.trim() || '',
                durableWordWrappers: handed?.querySelectorAll('.conv-live-word').length ?? -1,
                markerConsumed: !view._streamedAssistantMessageIds?.has('msg-result-first'),
                ordinaryWordWrappers: ordinary?.querySelectorAll('.conv-live-word').length ?? -1,
              };
            });

            if (!result.bubbleClearedBeforeDurable) throw new Error('bubble was not cleared first');
            if (!result.durablePresent) throw new Error('durable row missing');
            if (result.durableText !== 'Already streamed answer plus final tail') {
              throw new Error(`wrong durable text: ${result.durableText}`);
            }
            if (result.durableWordWrappers !== 0) throw new Error('durable row replayed');
            if (!result.markerConsumed) throw new Error('handoff marker leaked');
            if (result.ordinaryWordWrappers < 1) throw new Error('ordinary reveal disabled');
          } finally {
            await browser.close();
          }
        })().catch(error => {
          console.error(error);
          process.exit(1);
        });
        """
    )
    subprocess.run(["node", "-e", node_program], cwd=PROJECT_ROOT, check=True)
