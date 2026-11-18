// CCC-228: a parent's lanes render as real indented rows under one fold
// toggle (Workers-cluster shape) instead of a "↳ N lanes" link list.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = require('./legacy-dashboard-source.cjs');
function fn(name) {
  const start = app.indexOf('  function ' + name + '(');
  return app.slice(start, app.indexOf('\n  }', start) + 4);
}

test('unclustered parents nest their lanes through the cluster renderer', () => {
  assert.match(app, /return _renderSubagentCluster\(\{\s*familyNest: true,/);
  assert.match(app, /const orchChildBadgeHtml = opts\.familyNested \? '' :/);
  assert.match(app, /if \(el\.querySelector\('\.conv-subagent-cluster-toggle, \.wa-lanes'\)\)/);
});

test('a nested lane that also has its own row elsewhere is dropped from the nest and recounted', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent(`<div id="convList">
      <div class="conv-subagent-cluster is-family-nest is-expanded" data-subagent-parent-sid="p">
        <div class="conv-item" data-id="p"><button data-role="subagent-cluster-toggle" class="conv-subagent-cluster-toggle"><span class="conv-subagent-cluster-summary">↳ 2 lanes</span></button><button class="wa-lanes" data-role="subagent-cluster-toggle">↳ 2 <span class="conv-subagent-cluster-arrow">▾</span></button></div>
        <div class="conv-subagent-cluster-body">
          <div class="conv-item" data-id="a"></div>
          <div class="conv-item" data-id="b"></div>
        </div>
      </div>
      <div class="conv-item" data-id="b"></div>
      <div class="conv-subagent-cluster is-family-nest" id="deferred-nest">
        <button data-role="subagent-cluster-toggle"><span class="conv-subagent-cluster-summary">↳ 3 lanes</span></button>
        <button class="wa-lanes" data-role="subagent-cluster-toggle">↳ 3 <span>▸</span></button>
        <div class="conv-subagent-cluster-body" data-lanes-deferred="true" data-deferred-lane-ids='["a","b","c"]'></div>
      </div>
      <div class="conv-subagent-cluster"><div class="conv-subagent-cluster-body" data-lanes-deferred="true" data-deferred-lane-ids='["c"]'></div></div>
      <div class="conv-subagent-cluster is-family-nest" id="section-nest">
        <button data-role="subagent-cluster-toggle"><span class="conv-subagent-cluster-summary">↳ 2 lanes</span></button>
        <div class="conv-subagent-cluster-body" data-lanes-deferred="true" data-deferred-lane-ids='["archived","remaining"]'></div>
      </div>
      <div class="session-lifecycle-content" hidden data-deferred-row-ids='["archived"]'></div>
    </div>`);
    const res = await page.evaluate(code => {
      (0, eval)(code);
      const list = document.getElementById('convList');
      _dedupeFamilyNestRows(list);
      return {
        ids: [...list.querySelectorAll('.conv-item[data-id]')].map(e => e.dataset.id),
        summary: list.querySelector('.conv-subagent-cluster-summary').textContent,
        wa: list.querySelector('.wa-lanes').firstChild.nodeValue,
        deferredSummary: list.querySelector('#deferred-nest .conv-subagent-cluster-summary').textContent,
        deferredWa: list.querySelector('#deferred-nest .wa-lanes').firstChild.nodeValue,
        sectionSummary: list.querySelector('#section-nest .conv-subagent-cluster-summary').textContent,
      };
    }, fn('_dedupeFamilyNestRows'));
    assert.deepEqual(res.ids, ['p', 'a', 'b']);
    assert.equal(res.summary, '↳ 1 lane');
    assert.equal(res.wa, '↳ 1 ');
    assert.equal(res.deferredSummary, '↳ 1 lane');
    assert.equal(res.deferredWa, '↳ 1 ');
    assert.equal(res.sectionSummary, '↳ 1 lane', 'deferred sections still own their standalone identities');
  } finally {
    await browser.close();
  }
});


test('lanes start collapsed regardless of family size or attention; saved expansion wins', () => {
  const start = app.indexOf('    const _renderSubagentCluster = (cluster, opts = {}) => {');
  const end = app.indexOf('\n    };', start) + 7;
  const expanded = new Set();
  const collapsed = new Set();
  let built = 0;
  const render = require('node:vm').runInNewContext(app.slice(start, end) + '\n_renderSubagentCluster;', {
    _subagentClusterPresentation: cluster => ({ rootItem: cluster.rows[0], ...cluster }),
    _subagentRowId: card => card.id,
    _ultraFreshCurrent: card => card,
    _subagentExpandedParents: expanded,
    _subagentCollapsedParents: collapsed,
    _renderRow: () => { built++; return '<div class="conv-item"></div>'; },
    escapeAttr: String,
  });
  const cluster = (total, attention) => ({ total, attention, active: total,
    rows: Array.from({ length: total + 1 }, (_, i) => ({ card: { id: i ? 'child-' + i : 'parent' }, depth: i ? 1 : 0 })),
  });
  for (const total of [1, 2, 4]) {
    for (const attention of [0, 1]) {
      built = 0;
      const html = render(cluster(total, attention));
      assert.doesNotMatch(html, /is-expanded/);
      assert.match(html, /data-lanes-deferred="true"/);
      assert.equal(built, 1, 'only the visible parent needs row HTML');
    }
  }
  expanded.add('parent');
  built = 0;
  const html = render(cluster(4, 0));
  assert.match(html, /is-expanded/);
  assert.doesNotMatch(html, /data-lanes-deferred/);
  assert.equal(built, 5, 'a saved expansion builds every lane');
  collapsed.add('parent');
  assert.doesNotMatch(render(cluster(1, 1)), /is-expanded/);
  built = 0;
  const nested = render({ ...cluster(2, 0), familyNest: true });
  assert.match(nested, /data-deferred-lane-ids="/);
  assert.equal(built, 1, 'ad-hoc nests retain identities for cross-list deduplication');
});
