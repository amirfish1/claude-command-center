#!/usr/bin/env node
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const app = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
const start = app.indexOf('  function startInlineRename(item) {');
const end = app.indexOf('\n  // Return the active conversation view element', start);
assert(start >= 0 && end > start, 'inline session rename implementation is present');

const rename = app.slice(start, end);
const responseRead = rename.indexOf('const data = await res.json();');
const release = rename.lastIndexOf('_renameInProgress = false;');
const render = rename.indexOf('renderSidebar(filterConversations($convSearch.value), { force: true });');

assert(responseRead >= 0, 'rename waits for the server response');
assert(release > responseRead,
  'sidebar redraws stay paused until the asynchronous rename response is handled');
assert(release < render,
  'rename releases the redraw guard before its forced post-save render');

console.log('inline rename keeps its redraw guard through async save');

// CCC-1173: the status-rail pencil rename must update the sidebar row
// immediately — the Archived/All list renders from archiveData, so the
// commit has to patch that pool and pin the rename override (a stale
// server snapshot must not snap the name back), and the repaint must be
// forced past the typing/hover pause guard.
const railStart = app.indexOf('  function startStatusRailTitleRename() {');
const railEnd = app.indexOf('\n  function updatePaneHeader(', railStart);
assert(railStart >= 0 && railEnd > railStart, 'status-rail rename implementation is present');
const rail = app.slice(railStart, railEnd);
assert(rail.includes('archiveData.find('),
  'rail rename patches archiveData so the sidebar row repaints immediately');
assert(rail.includes('_rememberRenameOverride('),
  'rail rename pins the override against stale server snapshots');
assert(rail.includes('renderSidebar(filterConversations($convSearch.value), { force: true })'),
  'rail rename forces the post-commit repaint past the pause guard');
