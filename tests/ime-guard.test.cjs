#!/usr/bin/env node
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

// 1. static/app.js
const appJs = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

// Helper definition
assert(appJs.includes('function isImeKey(ev) { return !!(ev && (ev.isComposing || ev.keyCode === 229)); }'), 'isImeKey helper defined');
assert(appJs.includes('window.__cccIsImeKey = isImeKey;'), '__cccIsImeKey exported');
assert(appJs.includes('window.isImeKey = isImeKey;'), 'window.isImeKey exported');

// Check key listeners in app.js
assert(appJs.includes('$convInput.addEventListener(\'keydown\', (e) => {\n      if (isImeKey(e)) return;'), '$convInput keydown guarded');
assert(appJs.includes('if ($fedSelfName) $fedSelfName.addEventListener(\'keydown\', (e) => {\n    if (isImeKey(e)) return;'), 'fedSelfName keydown guarded');
assert(appJs.includes('$chatFindInput.addEventListener(\'keydown\', e => {\n      if (isImeKey(e)) return;'), 'chatFindInput keydown guarded');
assert(appJs.includes('$cmdkInput.addEventListener(\'keydown\', (e) => {\n      if (isImeKey(e)) return;'), 'cmdkInput keydown guarded');
assert(appJs.includes('$convSearch.addEventListener(\'keydown\', (ev) => {\n    if (isImeKey(ev)) return;'), 'convSearch keydown guarded');
assert(appJs.includes('ta.addEventListener(\'keydown\', (ev) => {\n      if (isImeKey(ev)) return;'), 'annotate popover textarea guarded');

// 2. static/canvas.js
const canvasJs = fs.readFileSync(path.join(__dirname, '..', 'static', 'canvas.js'), 'utf8');
assert(canvasJs.includes('function isImeKey(e) { return !!(e && (e.isComposing || e.keyCode === 229)); }'), 'canvas isImeKey defined');
assert(canvasJs.includes('search.addEventListener("keydown", function (e) {\n      if (isImeKey(e)) return;'), 'canvas search input guarded');

// 3. static/bookmarklet.js
const bookmarkletJs = fs.readFileSync(path.join(__dirname, '..', 'static', 'bookmarklet.js'), 'utf8');
assert(bookmarkletJs.includes('if (e && (e.isComposing || e.keyCode === 229)) return;'), 'bookmarklet onKey guarded');

// 4. static/annotate-widget.js
const annotateWidgetJs = fs.readFileSync(path.join(__dirname, '..', 'static', 'annotate-widget.js'), 'utf8');
assert(annotateWidgetJs.includes('if (e && (e.isComposing || e.keyCode === 229)) return;'), 'annotate-widget onEsc guarded');

// 5. static/app-rail.js
const appRailJs = fs.readFileSync(path.join(__dirname, '..', 'static', 'app-rail.js'), 'utf8');
assert(appRailJs.includes('if (e && (e.isComposing || e.keyCode === 229)) return;'), 'app-rail onKey guarded');

console.log('All IME composition guards verified across frontend files.');
