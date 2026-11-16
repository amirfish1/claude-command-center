// Serve shared conversation modules in browser fixtures that intercept assets.
const fs = require('node:fs');
const assets = new Map(['conversation-state.js', 'conversation-text.js', 'conversation-selection.js', 'conversation-events.js', 'conversation-composer.js', 'conversation-transport.js', 'conversation-content.js', 'conversation-panes.js'].map(name =>
  ['/static/' + name, fs.readFileSync(require.resolve('../static/' + name), 'utf8')]));
module.exports = function respondConversationAsset(request) {
  const body = assets.get(new URL(request.url()).pathname);
  if (body === undefined) return false;
  request.respond({ status: 200, contentType: 'application/javascript', body });
  return true;
};
