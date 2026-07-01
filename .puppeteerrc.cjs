// Chrome for Testing v149 has a macOS-ARM renderer crash on
// Page.captureScreenshot (OPS-4, recurred as OPS-60). Puppeteer reads this
// file for the default `executablePath` on every launch() call that doesn't
// pass one explicitly, so any script — including ones not written yet —
// gets a working Chrome without needing to know about the bug.
const fs = require('fs');

function findChromePath() {
  if (process.env.SNAPSHOT_CHROME) return process.env.SNAPSHOT_CHROME;
  const macs = [
    '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ];
  for (const p of macs) {
    try {
      fs.accessSync(p, fs.constants.X_OK);
      return p;
    } catch (_) {}
  }
  return undefined;
}

const executablePath = findChromePath();

module.exports = {
  ...(executablePath ? { executablePath } : {}),
};
