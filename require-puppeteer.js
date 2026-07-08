// Resolves 'puppeteer' even from a sibling git worktree (created via
// `git worktree add ../<repo>-wt-<name>`, this machine's git-hygiene
// convention for isolated work) — node_modules/package.json are gitignored
// here (local dev clutter, see .gitignore) and only exist in the main clone.
// Node's own upward node_modules search only finds them when a worktree is
// nested inside the main repo tree (e.g. .claude/worktrees/*); a sibling
// worktree outside that tree needs an explicit fallback (OPS-114).
const { execFileSync } = require('child_process');
const path = require('path');

function resolvePuppeteer() {
  try {
    return require('puppeteer');
  } catch (err) {
    if (err.code !== 'MODULE_NOT_FOUND') throw err;
  }
  const mainRepoRoot = execFileSync(
    'git',
    ['rev-parse', '--path-format=absolute', '--git-common-dir'],
    { cwd: __dirname, encoding: 'utf8' }
  ).trim().replace(/\/\.git$/, '');
  return require(path.join(mainRepoRoot, 'node_modules', 'puppeteer'));
}

module.exports = resolvePuppeteer();
