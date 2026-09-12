// Engine glyphs in the conversation list go through one SVG sprite. Inlining
// the full logo path per row put 231 KB of `d="..."` attributes (24% of the
// list markup, 264 <path> nodes at 206 rows, measured 2026-09-12) into every
// innerHTML swap of #convList; the sprite is parsed once per document.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');

function extractFunction(name) {
  const start = app.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `function ${name} not found in app.js`);
  let cursor = app.indexOf('{', start);
  let depth = 0;
  for (; cursor < app.length; cursor++) {
    if (app[cursor] === '{') depth += 1;
    else if (app[cursor] === '}') {
      depth -= 1;
      if (depth === 0) return app.slice(start, cursor + 1);
    }
  }
  assert.fail(`unbalanced braces extracting ${name}`);
}

function extractLine(prefix) {
  const start = app.indexOf(prefix);
  assert.notEqual(start, -1, `${prefix} not found in app.js`);
  return app.slice(start, app.indexOf('\n', start) + 1);
}

function fakeDocument() {
  const byId = new Map();
  const body = {
    children: [],
    firstChild: null,
    insertBefore(node) { this.children.unshift(node); this.firstChild = node; if (node.id) byId.set(node.id, node); return node; },
  };
  return {
    body,
    getElementById: (id) => byId.get(id) || null,
    createElement: () => ({
      _html: '',
      set innerHTML(v) { this._html = v; },
      get innerHTML() { return this._html; },
      get firstChild() {
        const m = /id="([^"]+)"/.exec(this._html);
        return { id: m ? m[1] : '', outerHTML: this._html };
      },
    }),
  };
}

function load(document) {
  const sandbox = { document, console };
  const src = extractLine('  const _ENGINE_SVG_KEYS')
    + extractLine('  let _engineSvgSpriteReady')
    + extractLine('  const _ENGINE_SVG_SPRITE_ID')
    + extractFunction('_engineSvgSymbolId')
    + extractFunction('_engineSvgInline')
    + extractFunction('_ensureEngineSvgSprite')
    + extractFunction('getEngineSvg')
    + '\nthis.getEngineSvg = getEngineSvg; this._engineSvgInline = _engineSvgInline; this._ENGINE_SVG_KEYS = _ENGINE_SVG_KEYS;';
  vm.runInNewContext(src, sandbox);
  return sandbox;
}

test('rows reference the sprite instead of inlining the logo path', () => {
  const document = fakeDocument();
  const { getEngineSvg } = load(document);
  const html = getEngineSvg('codex');
  assert.match(html, /^<svg class="conv-session-svg" viewBox="0 0 24 24"><use href="#ccc-engine-codex"><\/use><\/svg>$/);
  assert.equal(html.includes('<path'), false);
});

test('the sprite is installed once with one symbol per engine plus the default', () => {
  const document = fakeDocument();
  const { getEngineSvg, _ENGINE_SVG_KEYS } = load(document);
  getEngineSvg('codex');
  getEngineSvg('claude');
  getEngineSvg('antigravity');
  assert.equal(document.body.children.length, 1, 'sprite installed exactly once');
  const sprite = document.getElementById('cccEngineSvgSprite').outerHTML;
  for (const key of _ENGINE_SVG_KEYS.concat(['default'])) {
    assert.ok(sprite.includes('<symbol id="ccc-engine-' + key + '"'), 'symbol for ' + key);
  }
  // The symbol carries the presentation attributes the inline markup had, so
  // currentColor / stroke styling still resolves inside the <use> instance.
  assert.match(sprite, /<symbol id="ccc-engine-codex" viewBox="0 0 24 24" fill="currentColor" fill-rule="evenodd"><path d="M9\.205/);
  assert.match(sprite, /<symbol id="ccc-engine-hermes" viewBox="0 0 24 24" fill="none" stroke="currentColor"/);
  assert.equal(sprite.includes('<svg class="conv-session-svg"'), false);
  assert.equal(sprite.includes('</svg></symbol>'), false);
});

test('an engine without its own glyph resolves to the default symbol', () => {
  const document = fakeDocument();
  const { getEngineSvg } = load(document);
  assert.ok(getEngineSvg('antigravity').includes('href="#ccc-engine-default"'));
  assert.ok(getEngineSvg('').includes('href="#ccc-engine-default"'));
  assert.ok(getEngineSvg(undefined).includes('href="#ccc-engine-default"'));
});

test('without a document body the inline markup is emitted as before', () => {
  const { getEngineSvg, _engineSvgInline } = load({ body: null, getElementById: () => null });
  assert.equal(getEngineSvg('codex'), _engineSvgInline('codex'));
  assert.ok(getEngineSvg('codex').includes('<path d="M9.205'));
});

test('the row icon builder still goes through getEngineSvg', () => {
  const builder = extractFunction('sessionEngineIconHtml');
  assert.ok(builder.includes('getEngineSvg(presentation.engine)'));
});
