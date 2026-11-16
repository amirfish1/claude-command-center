// Instantiate the production content helpers in small DOM and VM fixtures.
const fs = require('node:fs');
const textSource = fs.readFileSync(require.resolve('../static/conversation-text.js'), 'utf8');
const contentSource = fs.readFileSync(require.resolve('../static/conversation-content.js'), 'utf8');
const install = `
(() => {
  let content;
  const text = CCCConversationText.createRenderer({
    cleanIssuePrompt: (...args) => content.cleanIssuePrompt(...args),
    sidebarRowDisplayTitle: value => value,
    shortSessionId: value => value,
    sessionCompletionDetails: () => ({}),
    activeSessionId: () => '',
    findSession: () => null,
  });
  content = CCCConversationContent.createHelpers(text);
  Object.assign(globalThis, text, content);
})();`;
const source = textSource + '\n' + contentSource + '\n' + install;
module.exports = {
  source,
  async install(page) { await page.addScriptTag({ content: source }); },
  load(context) { require('node:vm').runInContext(source, context); return context; },
};
