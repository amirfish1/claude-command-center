// A conversation entry uses the same controllers and live cells as the dashboard.
(async function () {
  'use strict';
  const readyCallbacks = [];
  const conversation = window.CCCConversationHost.createHost({
    documentReady: callback => readyCallbacks.push(callback),
  });
  window.cccConversation = conversation;
  window.__cccIsImeKey = window.isImeKey = conversation.isImeKey;
  window.__cccBackgroundApiFetch = conversation.backgroundApiFetch;
  window.cccRenderMarkdown = conversation.renderMarkdown;
  window.cccFrontSessionId = () => conversation.currentSession?.id || '';
  window.cccOpenSession = id => conversation.selectConversation(id);
  window.CCCToolResultImagesCardHtml = conversation._toolResultImagesCardHtml;
  window.CCCKimiTidyToolGroups = conversation._kimiTidyToolGroups;
  window.openFolderViewer = conversation.openFolderViewer;
  if (document.readyState === 'loading') await new Promise(resolve => {
    document.addEventListener('DOMContentLoaded', resolve, { once: true });
  });
  conversation.bindConversationInteractions();
  conversation.bindConversationControls();
  conversation.bindConversationViews();
  for (const callback of readyCallbacks) await callback();
  conversation.wireEmbedHostMessages();
  await conversation.bootConversationPopoutDirect();
})().catch(error => {
  console.error('Conversation startup failed', error);
  const view = document.getElementById('conversationsView');
  if (view) {
    view.replaceChildren();
    const message = document.createElement('p');
    message.className = 'conv-load-error';
    message.textContent = 'Conversation could not start. Reload to try again.';
    view.appendChild(message);
  }
});
