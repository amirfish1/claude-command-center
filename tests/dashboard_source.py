"""Follow extracted dashboard declarations for legacy algorithm fixtures.

Demo copies and other assets are read verbatim. Runtime controller tests use
the real adapters; legacy algorithm fixtures ignore adapter qualification.
"""
from functools import lru_cache
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _dashboard_source():
    script = r"""
let source = require('./tests/legacy-dashboard-source.cjs');
const canonical = require('./tests/conversation-source.cjs');
// Include declarations embedded in factories that have no entry-point marker.
// Do not repeat declarations: some contracts count request/handler owners.
const included = new Set([...source.matchAll(/\bfunction (\w+)\(/g)].map(match => match[1]));
for (const match of canonical.source.matchAll(/^([ \t]*)(?:async )?function (\w+)\(/gm)) {
  const name = match[2];
  if (included.has(name)) continue;
  included.add(name);
  let declaration = canonical.functionSource(name, true)
    .replace(/\(0, (?:sharedState|hostServices|deferredServices|state|helpers)\.([\w$]+)\)/g, '$1')
    .replace(/\b(?:sharedState|hostServices|deferredServices|state|helpers)\.([\w$]+)/g, '$1');
  source += '\n' + declaration;
}
process.stdout.write(source);
"""
    return subprocess.run(
        ['node', '-e', script], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout


def dashboard_source(asset=None):
    asset = Path(asset) if asset is not None else ROOT / 'static/app.js'
    if asset.resolve() == (ROOT / 'static/app.js').resolve():
        return _dashboard_source()
    return asset.read_text(encoding='utf-8')
