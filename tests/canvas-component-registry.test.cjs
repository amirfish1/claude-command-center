/* Pipeline Canvas component registry + template graph integrity.
 *
 * The library is data-driven (static/canvas-components.js): every
 * component must carry an anchor (the proven implementation), a category,
 * port rules that make sense, and an edge contract. Every template must
 * reference real components, lay out unique node keys, and draw only
 * port-legal edges.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../static/canvas-components.js');
const { TEMPLATES } = require('../static/canvas-templates.js');

const CATS = Object.keys(C.CATEGORIES);

test('registry has 24+ components across all five categories', () => {
  assert.ok(C.COMPONENT_REGISTRY.length >= 24,
    `expected 24+ components, got ${C.COMPONENT_REGISTRY.length}`);
  for (const cat of ['sources', 'workers', 'gates', 'sinks', 'utilities']) {
    const n = C.COMPONENT_REGISTRY.filter((c) => c.cat === cat).length;
    assert.ok(n >= 4, `category ${cat} has only ${n} components`);
    assert.ok(CATS.includes(cat));
  }
});

test('every component has id, name, category, letter, desc, anchor, pattern', () => {
  const ids = new Set();
  for (const c of C.COMPONENT_REGISTRY) {
    assert.ok(c.id && /^[a-z0-9-]+$/.test(c.id), `bad id: ${c.id}`);
    assert.ok(!ids.has(c.id), `duplicate id: ${c.id}`);
    ids.add(c.id);
    assert.ok(c.name, `${c.id}: missing name`);
    assert.ok(CATS.includes(c.cat), `${c.id}: bad category ${c.cat}`);
    assert.ok(c.letter, `${c.id}: missing letter`);
    assert.ok(c.desc && c.desc.length >= 10, `${c.id}: thin description`);
    assert.ok(c.anchor, `${c.id}: missing anchor (proven implementation)`);
    assert.ok(c.pattern && c.pattern.length >= 20, `${c.id}: thin pattern`);
  }
});

test('letters are unique within a category', () => {
  const seen = {};
  for (const c of C.COMPONENT_REGISTRY) {
    const key = `${c.cat}:${c.letter}`;
    assert.ok(!seen[key], `letter ${c.letter} repeats in ${c.cat} (${seen[key]} vs ${c.id})`);
    seen[key] = c.id;
  }
});

test('every component declares an edge contract direction', () => {
  for (const c of C.COMPONENT_REGISTRY) {
    const ports = C.portsFor(c);
    if (ports.out) assert.ok(c.files && c.files.what, `${c.id}: out port without files contract`);
    if (ports.inp) assert.ok(c.consumes || c.cat === 'sinks' || c.files,
      `${c.id}: in port without consumes/files contract`);
  }
});

test('port rules are categorical with per-component overrides respected', () => {
  assert.equal(C.portsFor(C.BY_ID['posthog-watcher']).out, true);
  assert.equal(C.portsFor(C.BY_ID['posthog-watcher']).inp, false);
  assert.equal(C.portsFor(C.BY_ID['github-issue']).out, false);
  assert.equal(C.portsFor(C.BY_ID['decision-inbox']).out, false);
  // Review gates that file findings/reopens must be able to emit.
  assert.equal(C.portsFor(C.BY_ID['senior-reviewer']).out, true);
  assert.equal(C.portsFor(C.BY_ID['closure-verifier']).out, true);
});

test('canConnect blocks sink->source and allows source->sink', () => {
  assert.equal(C.canConnect(C.BY_ID['github-issue'], C.BY_ID['posthog-watcher']), false);
  assert.equal(C.canConnect(C.BY_ID['posthog-watcher'], C.BY_ID['github-issue']), true);
  assert.equal(C.canConnect(C.BY_ID['scheduler'], C.BY_ID['executor-quick']), true);
  assert.equal(C.canConnect(C.BY_ID['executor-quick'], C.BY_ID['scheduler']), false);
  assert.equal(C.canConnect(null, C.BY_ID['planner']), false);
});

test('worker components carry a config sketch with engine and model', () => {
  for (const c of C.COMPONENT_REGISTRY.filter((x) => x.cat === 'workers')) {
    const keys = (c.config || []).map((f) => f.key);
    assert.ok(keys.includes('engine'), `${c.id}: config sketch missing engine`);
    assert.ok(keys.includes('model'), `${c.id}: config sketch missing model`);
  }
});

test('archetype maps reference real components and categories', () => {
  for (const [arch, compId] of Object.entries(C.ARCHETYPE_COMPONENT)) {
    assert.ok(C.BY_ID[compId], `archetype ${arch} maps to missing component ${compId}`);
    assert.ok(C.ARCHETYPE_CATEGORY[arch], `archetype ${arch} missing category`);
    assert.ok(C.ARCHETYPE_LETTER[arch], `archetype ${arch} missing letter`);
  }
});

test('there are 6+ templates and every graph validates', () => {
  assert.ok(TEMPLATES.length >= 6, `expected 6+ templates, got ${TEMPLATES.length}`);
  const ids = new Set();
  for (const t of TEMPLATES) {
    assert.ok(!ids.has(t.id), `duplicate template ${t.id}`);
    ids.add(t.id);
    assert.ok(t.name && t.desc && t.flow, `${t.id}: missing presentation fields`);
    const keys = new Set(t.nodes.map((n) => n.key));
    assert.equal(keys.size, t.nodes.length, `${t.id}: duplicate node keys`);
    for (const n of t.nodes) {
      assert.ok(C.BY_ID[n.comp], `${t.id}.${n.key}: unknown component ${n.comp}`);
      assert.ok(typeof n.x === 'number' && typeof n.y === 'number', `${t.id}.${n.key}: no layout`);
    }
    for (const e of t.edges) {
      assert.ok(keys.has(e.from), `${t.id}: edge from missing key ${e.from}`);
      assert.ok(keys.has(e.to), `${t.id}: edge to missing key ${e.to}`);
      assert.ok(e.label, `${t.id}: edge ${e.from}->${e.to} missing contract label`);
      const src = C.BY_ID[t.nodes.find((n) => n.key === e.from).comp];
      const tgt = C.BY_ID[t.nodes.find((n) => n.key === e.to).comp];
      assert.ok(C.canConnect(src, tgt),
        `${t.id}: port-illegal edge ${src.id} -> ${tgt.id}`);
    }
  }
});

test('every template forms a complete root-to-terminal path', () => {
  for (const t of TEMPLATES) {
    const byKey = Object.fromEntries(t.nodes.map((n) => [n.key, C.BY_ID[n.comp]]));
    const hasIn = new Set(t.edges.map((e) => e.to));
    const roots = t.nodes.filter((n) => !hasIn.has(n.key)).map((n) => n.key);
    const gates = t.nodes.filter((n) => !C.portsFor(byKey[n.key]).out).map((n) => n.key);
    assert.ok(roots.length >= 1, `${t.id}: no root node`);
    // (a graph may legitimately have no terminal node when it loops —
    // checked below via cycle detection)
    const adj = {};
    for (const e of t.edges) (adj[e.from] = adj[e.from] || []).push(e.to);
    const seen = new Set(roots);
    const stack = [...roots];
    while (stack.length) {
      const k = stack.pop();
      for (const nxt of adj[k] || []) if (!seen.has(nxt)) { seen.add(nxt); stack.push(nxt); }
    }
    // Every node hangs off a root — no orphans floating in the template.
    for (const n of t.nodes) assert.ok(seen.has(n.key), `${t.id}: ${n.key} unreachable from a root`);
    // Termination: either a gate ends the pipeline, or the graph loops on
    // purpose (self-healing). Detect a back-edge by DFS from each node.
    if (gates.length === 0) {
      let cyclic = false;
      for (const start of t.nodes.map((n) => n.key)) {
        const seen2 = new Set([start]);
        const st = [start];
        while (st.length && !cyclic) {
          const k = st.pop();
          for (const nxt of adj[k] || []) {
            if (nxt === start) { cyclic = true; break; }
            if (!seen2.has(nxt)) { seen2.add(nxt); st.push(nxt); }
          }
        }
        if (cyclic) break;
      }
      assert.ok(cyclic, `${t.id}: no terminal node and no intentional loop`);
    }
  }
});
