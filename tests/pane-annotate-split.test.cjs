const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const app = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/app.css', 'utf8');
const html = fs.readFileSync('static/index.html', 'utf8');

// CCC-1156: the per-pane annotate icon appears in each pane header while
// split view has 2+ panes (hidden in single-pane), and the annotation
// anchor carries the CLICKED pane's session id — not the global
// currentSession.id.

test('pane annotate button is not debug-only and is gated to split mode', () => {
  const btnLine = html.match(/<button[^>]*data-role="pane-annotate"[^>]*>/);
  assert.ok(btnLine, 'pane-annotate button exists in the pane header');
  assert.ok(!/data-debug-hide/.test(btnLine[0]), 'button is not debug-gated');
  // The old blanket debug-hide CSS rule must no longer cover this control.
  const debugBlock = css.match(/html:not\(\.ccc-debug-mode\)[^}]+}/g) || [];
  assert.ok(
    debugBlock.every((rule) => !rule.includes('pane-annotate')),
    'no debug-mode rule hides [data-role="pane-annotate"]',
  );
  assert.match(
    css,
    /#convSplit\[data-orientation=""\] \[data-role="pane-annotate"\]\s*{[^}]*display:\s*none/,
    'annotate icon hidden while #convSplit has no orientation (single pane)',
  );
});

test('clicking the pane annotate icon starts annotation scoped to that pane', () => {
  const start = app.indexOf('[data-role="pane-annotate"], [data-role="pane-clear"]');
  assert.ok(start >= 0, 'pane action delegation found');
  const block = app.slice(start, start + 1200);
  assert.match(block, /paneId = \(pane && pane\.dataset\.paneId\)/, 'pane id resolved from the clicked pane');
  assert.match(block, /pane-annotate'\) \{ annStart\(paneId\)/, 'annStart receives the pane id');
});

test('annotation payload anchors to the pane session, not currentSession', () => {
  const start = app.indexOf('function _annPaneSessionId(paneId)');
  assert.ok(start >= 0, 'pane session resolver exists');
  const resolver = app.slice(start, start + 700);
  assert.match(resolver, /pane\.conversationId/, 'resolver reads the pane conversation');
  assert.match(resolver, /sessionIdByConv\[convId\]\)? \|\| convId/, 'resolver maps conversation to session id');

  const startState = app.indexOf('function annStart(paneId)');
  assert.ok(startState >= 0, 'annStart takes a pane id');
  const stateBlock = app.slice(startState, startState + 1400);
  assert.match(stateBlock, /paneId: paneId \|\| null/, 'annotation state keeps the pane id');
  assert.match(stateBlock, /paneSessionId: _annPaneSessionId\(paneId\)/, 'annotation state keeps the pane session id');

  const startPayload = app.indexOf('function annBuildPayload(note, screenshotB64)');
  assert.ok(startPayload >= 0, 'payload builder found');
  const payloadBlock = app.slice(startPayload, startPayload + 2000);
  assert.match(payloadBlock, /annotationState\.paneId/, 'payload honors pane scope');
  assert.match(payloadBlock, /annotationState\.paneSessionId/, 'payload uses the pane session id');
});
