const fs = require('fs');
const os = require('os');
const path = require('path');

function executable(candidate) {
  if (!candidate) return false;
  try {
    fs.accessSync(candidate, fs.constants.X_OK);
    return true;
  } catch (_) {
    return false;
  }
}

function findCachedHeadlessShell() {
  const cacheRoot = path.join(os.homedir(), '.cache', 'puppeteer', 'chrome-headless-shell');
  let versions;
  try {
    versions = fs.readdirSync(cacheRoot, { withFileTypes: true })
      .filter(entry => entry.isDirectory())
      .map(entry => entry.name)
      .sort((left, right) => right.localeCompare(left, undefined, { numeric: true }));
  } catch (_) {
    return undefined;
  }

  for (const version of versions) {
    const versionRoot = path.join(cacheRoot, version);
    let packages;
    try {
      packages = fs.readdirSync(versionRoot, { withFileTypes: true })
        .filter(entry => entry.isDirectory())
        .map(entry => entry.name);
    } catch (_) {
      continue;
    }
    for (const packageName of packages) {
      const candidate = path.join(versionRoot, packageName, 'chrome-headless-shell');
      if (executable(candidate)) return candidate;
    }
  }
  return undefined;
}

function findChromePath() {
  if (process.env.SNAPSHOT_CHROME) return process.env.SNAPSHOT_CHROME;

  // Full Chrome builds have repeatedly hung or crashed during automated
  // capture on macOS ARM. Puppeteer's purpose-built cached shell is isolated
  // from the user's browser and has proven reliable for CCC verification.
  const headlessShell = findCachedHeadlessShell();
  if (headlessShell) return headlessShell;

  const installedBrowsers = [
    '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ];
  return installedBrowsers.find(executable);
}

module.exports = { findChromePath };
