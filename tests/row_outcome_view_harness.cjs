// Loads the shipped rowOutcomeView (CCC-273) with its real helpers, so tests
// of the row renderer's outcome slice use the same rules as the live patcher.
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');

function fnSource(name) {
  const start = app.indexOf('  function ' + name + '(');
  if (start < 0) throw new Error('missing ' + name);
  const end = app.indexOf('\n  }\n', start);
  return app.slice(start, end + 4);
}

function loadRowOutcomeView(stubs = {}) {
  const src = ['_ultraFreshTime', 'sidebarSessionWorking', 'sessionReadyToClose',
    'sessionQuickAction', 'decisionQuickActions', 'sessionQuickActions', 'rowOutcomeView']
    .map(fnSource).join('\n') + '\nrowOutcomeView;';
  return vm.runInNewContext(src, Object.assign({
    _applyLiveOverlayToRow: row => row,
    sessionIsOptimisticallySending: () => false,
    preSendPreviewFor: () => null,
    rowHandoff: () => null,
    sessionAttentionStep: () => null,
  }, stubs));
}

module.exports = { loadRowOutcomeView };
