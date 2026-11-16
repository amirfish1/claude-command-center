/* The standalone host composes the dashboard's conversation controllers. */
(function (root) {
  'use strict';
  function createHost({ documentReady } = {}) {
    const state = {};
    const adapters = { state, helpers: state, documentReady };
    for (const controller of [root.CCCConversationCore, root.CCCConversationPanes,
      root.CCCConversationControls, root.CCCConversationInteractions,
      root.CCCConversationServices, root.CCCConversationViews,
      root.CCCConversationChrome, root.CCCConversationPreferences,
      root.CCCConversationInput, root.CCCConversationBootstrap]) {
      Object.assign(state, controller.createController(adapters));
    }
    const text = root.CCCConversationText.createRenderer({
      cleanIssuePrompt: (...args) => state.cleanIssuePrompt(...args),
      sidebarRowDisplayTitle: (...args) => state.sidebarRowDisplayTitle(...args),
      shortSessionId: (...args) => state.shortSessionId(...args),
      sessionCompletionDetails: (...args) => state.sessionCompletionDetails(...args),
      activeSessionId: () => state.sessionIdByConv[state.currentConversation]
        || state.currentSession?.id || '',
      findSession: id => state.conversationsData.find(row => String(row.session_id || row.id || '') === id)
        || state.archiveData.find(row => String(row.session_id || row.id || '') === id),
    });
    Object.assign(state, text, root.CCCConversationContent.createHelpers(text),
      root.CCCConversationAnnotations.createController(adapters));

    // Navigation panels belong to the surrounding dashboard. Conversation
    // actions keep their state and goal chrome current and publish changes
    // for hosts without instantiating hidden sidebar/queue render trees.
    function navigationChanged(view) {
      document.dispatchEvent(new CustomEvent('ccc:conversation-navigation-changed', {
        detail: { view, conversationId: state.currentConversation },
      }));
    }
    Object.assign(state, {
      renderSidebar() {
        state.renderConversationGoalStrip(state.activePaneId(), state.currentConversationRow());
        navigationChanged('sessions');
      },
      renderFlowSidebar() { navigationChanged('flow'); },
      renderArchiveList() { navigationChanged('archive'); },
      _renderQueuePanel() { navigationChanged('queues'); },
      async refreshConversationList() {
        await state.refreshArchiveData({ force: true });
        if (state.popoutRepoPath()) await state.loadConversationList();
        state.renderSidebar();
      },
      openSettingsModal() {
        window.open('/?ccc_settings=appearance', '_blank', 'noopener');
      },
    });
    const initializers = root.CCCConversationDefaults.createInitializers(adapters);
    for (const [name, initialize] of Object.entries(initializers)) state[name] = initialize();
    for (const name of ['currentConversation', 'convLastLine', 'convEventSource',
      '_firstUserMsgRendered', '_pendingSends', '_currentToolGroup',
      '_currentToolCount', 'currentSession']) {
      root.CCCConversationState.bindLegacyProperty(state, state.splitState, name);
      root.CCCConversationState.bindLegacyProperty(root, state.splitState, name);
    }
    state.activeTab = 'sessions';
    state.kanbanView = false;
    Object.assign(state, root.CCCConversationTransport.createController(adapters));
    Object.assign(state, root.CCCConversationEvents.createRenderer({ ...adapters, text }));
    Object.assign(state, root.CCCConversationComposer.createComposer(adapters));
    Object.assign(state, root.CCCConversationSelection.createController({
      state, panes: state, transport: state, composer: state, navigation: state,
      chrome: state, replay: state, pkood: state,
    }));
    return state;
  }
  root.CCCConversationHost = Object.freeze({ createHost });
})(typeof window === 'undefined' ? globalThis : window);
