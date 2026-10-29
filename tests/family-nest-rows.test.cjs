// CCC-228: a parent's lanes render as real indented rows under one fold
// toggle (Workers-cluster shape) instead of a "↳ N lanes" link list.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
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
    </div>`);
    const res = await page.evaluate(code => {
      (0, eval)(code);
      const list = document.getElementById('convList');
      _dedupeFamilyNestRows(list);
      return {
        ids: [...list.querySelectorAll('.conv-item[data-id]')].map(e => e.dataset.id),
        summary: list.querySelector('.conv-subagent-cluster-summary').textContent,
        wa: list.querySelector('.wa-lanes').firstChild.nodeValue,
      };
    }, fn('_dedupeFamilyNestRows'));
    assert.deepEqual(res.ids, ['p', 'a', 'b']);
    assert.equal(res.summary, '↳ 1 lane');
    assert.equal(res.wa, '↳ 1 ');
  } finally {
    await browser.close();
  }
});
