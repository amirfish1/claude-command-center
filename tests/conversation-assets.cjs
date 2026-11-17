// Serve shared conversation modules in browser fixtures that intercept assets.
const fs = require('node:fs');
const assets = new Map(['conversation-issues.js', 'conversation-details.js', 'conversation-state.js', 'conversation-text.js', 'conversation-selection.js', 'conversation-events.js', 'conversation-composer.js', 'conversation-transport.js', 'conversation-content.js', 'conversation-panes.js', 'conversation-input.js', 'conversation-core.js', 'conversation-defaults.js', 'conversation-preferences.js', 'conversation-bootstrap.js', 'conversation-annotations.js', 'conversation-chrome.js', 'conversation-controls.js', 'conversation-interactions.js', 'conversation-services.js', 'conversation-views.js', 'conversation-host.js'].map(name =>
  ['/static/' + name, fs.readFileSync(require.resolve('../static/' + name), 'utf8')]));
module.exports = function respondConversationAsset(request) {
  const body = assets.get(new URL(request.url()).pathname);
  if (body === undefined) return false;
  request.respond({ status: 200, contentType: 'application/javascript', body });
  return true;
};
