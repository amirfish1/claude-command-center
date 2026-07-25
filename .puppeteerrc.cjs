// Keep every ad-hoc Puppeteer launch on the same verified browser selection.
// Explicit per-script executablePath values still win over this default.
const { findChromePath } = require('./puppeteer-browser-config.js');

const executablePath = findChromePath();

module.exports = {
  ...(executablePath ? { executablePath } : {}),
};
